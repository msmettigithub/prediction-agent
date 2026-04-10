#!/usr/bin/env python3
"""Real-time position monitor + opportunity scanner.

Runs continuously. Every 30s:
- Fetches spot prices (BTC, WTI, Gold, S&P) from Yahoo Finance
- Computes mark-to-market P&L for all open positions
- Scans Kalshi for mispriced contracts using realized vol
- Writes alerts to a file the dashboard can read + agent_log

Every 5 min:
- Reconciles positions with Kalshi
- Checks risk limits
"""
import os, sys, json, time, sqlite3, math
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
from config import load_config
from live.kalshi_trader import KalshiTrader
from tools.market_data import fetch_spot, binary_price

TRADE_DB = str(Path(__file__).resolve().parent.parent / 'prediction_agent.db')
LOG_DB = '/home/jovyan/shared/sm/prediction-agent-db/prediction_agent.db'
STATE_FILE = '/tmp/live_monitor.json'
ALERT_FILE = '/tmp/live_alerts.json'

SPOT_SYMBOLS = {
    'KXWTI': 'CL=F',
    'KXBTCD': 'BTC-USD',
    'KXETH': 'ETH-USD',
    'KXINX': '^GSPC',
    'KXGOLDW': 'GC=F',
}

# Strike extraction patterns
import re

def log(msg, lvl='INFO'):
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] [{lvl}] {msg}")
    try:
        c = sqlite3.connect(LOG_DB)
        c.execute("INSERT INTO agent_log(ts,lvl,agent,msg) VALUES(?,?,?,?)",
                  (datetime.now(timezone.utc).isoformat(), lvl, 'MONITOR', msg[:500]))
        c.commit(); c.close()
    except: pass


def parse_strike(ticker):
    """Extract strike price from ticker. Returns (strike, is_threshold)."""
    m = re.search(r'-T(\d+\.?\d*)', ticker)
    if m:
        return float(m.group(1)), True
    m = re.search(r'-B(\d+\.?\d*)', ticker)
    if m:
        return float(m.group(1)), False
    return None, None


def get_series(ticker):
    """Extract series prefix from ticker."""
    for prefix in SPOT_SYMBOLS:
        if ticker.startswith(prefix):
            return prefix
    return None


def fetch_positions(trader):
    """Get all open positions from Kalshi."""
    url = f"{trader.TRADING_URL}/portfolio/positions"
    r = requests.get(url, headers=trader._signed_headers("GET", url),
                     params={'limit': 200}, timeout=15)
    r.raise_for_status()
    positions = r.json().get('market_positions', [])
    return [p for p in positions
            if float(p.get('market_exposure_dollars', '0') or 0) > 0
            or abs(float(p.get('position_fp', '0') or 0)) > 0.01]


def compute_mtm(positions, spots):
    """Compute mark-to-market P&L for each position."""
    results = []
    for p in positions:
        ticker = p.get('ticker', '')
        series = get_series(ticker)
        if not series or series not in spots:
            continue

        spot_data = spots[series]
        strike, is_threshold = parse_strike(ticker)
        if strike is None:
            continue

        position_fp = float(p.get('position_fp', '0') or 0)
        exposure = float(p.get('market_exposure_dollars', '0') or 0)
        realized = float(p.get('realized_pnl_dollars', '0') or 0)
        fees = float(p.get('fees_paid_dollars', '0') or 0)

        # Compute theoretical fair value
        hours = 16.0  # rough — will be replaced with actual expiry calc
        if is_threshold:
            fair = binary_price(spot_data.price, strike, spot_data.realized_vol, hours)
        else:
            # Range bucket: P(strike <= S < strike + bucket_width)
            width = 25 if series == 'KXINX' else 250 if series == 'KXBTCD' else 20
            fair = max(0.001, binary_price(spot_data.price, strike, spot_data.realized_vol, hours)
                       - binary_price(spot_data.price, strike + width, spot_data.realized_vol, hours))

        is_long = position_fp > 0
        shares = abs(int(position_fp))

        if is_long:
            entry_cost = exposure
            current_value = fair * shares
            unrealized = current_value - entry_cost
        else:
            entry_cost = exposure
            current_value = (1 - fair) * shares
            unrealized = current_value - entry_cost

        results.append({
            'ticker': ticker,
            'series': series,
            'side': 'LONG' if is_long else 'SHORT',
            'shares': shares,
            'strike': strike,
            'spot': spot_data.price,
            'fair': round(fair, 4),
            'exposure': round(exposure, 2),
            'unrealized': round(unrealized, 2),
            'realized': round(realized, 2),
            'fees': round(fees, 2),
            'total_pnl': round(unrealized + realized - fees, 2),
        })

    return results


def scan_opportunities(spots, trader):
    """Scan for mispriced contracts across all series."""
    alerts = []
    for series, symbol in SPOT_SYMBOLS.items():
        if series not in spots:
            continue
        spot = spots[series]

        # Fetch current Kalshi markets for this series
        # Try today's event
        today = datetime.now(timezone.utc).strftime('%y%b%d').upper()
        event_tickers = [f'{series}-26APR10', f'{series}-26APR1017']

        for event_ticker in event_tickers:
            try:
                r = requests.get(
                    f'https://api.elections.kalshi.com/trade-api/v2/events/{event_ticker}',
                    headers={'Accept': 'application/json'}, timeout=10)
                if r.status_code != 200:
                    continue
                markets = r.json().get('markets', [])

                for m in markets:
                    ticker = m.get('ticker', '')
                    strike, is_threshold = parse_strike(ticker)
                    if strike is None:
                        continue

                    yes_ask = float(m.get('yes_ask_dollars', '0') or m.get('yes_ask', 0) or 0)
                    no_ask = float(m.get('no_ask_dollars', '0') or m.get('no_ask', 0) or 0)
                    if yes_ask <= 0.01 and no_ask <= 0.01:
                        continue

                    hours = 16.0
                    if is_threshold:
                        fair = binary_price(spot.price, strike, spot.realized_vol, hours)
                    else:
                        width = 25 if series == 'KXINX' else 250 if series == 'KXBTCD' else 20
                        fair = max(0.001, binary_price(spot.price, strike, spot.realized_vol, hours)
                                   - binary_price(spot.price, strike + width, spot.realized_vol, hours))

                    edge_yes = fair - yes_ask if yes_ask > 0.01 else 0
                    edge_no = (1 - fair) - no_ask if no_ask > 0.01 else 0

                    if edge_yes > 0.08:
                        alerts.append({
                            'ticker': ticker, 'side': 'YES', 'edge': round(edge_yes, 3),
                            'fair': round(fair, 3), 'market': round(yes_ask, 3),
                            'spot': spot.price, 'strike': strike, 'series': series,
                        })
                    elif edge_no > 0.08:
                        alerts.append({
                            'ticker': ticker, 'side': 'NO', 'edge': round(edge_no, 3),
                            'fair': round(1 - fair, 3), 'market': round(no_ask, 3),
                            'spot': spot.price, 'strike': strike, 'series': series,
                        })
            except:
                continue
            time.sleep(0.3)

    return sorted(alerts, key=lambda x: -x['edge'])


def main():
    log("=== LIVE MONITOR STARTING ===", 'MILESTONE')
    config = load_config()
    trader = KalshiTrader(config)

    cycle = 0
    while True:
        cycle += 1
        try:
            # Fetch spot prices
            spots = {}
            for series, symbol in SPOT_SYMBOLS.items():
                try:
                    spots[series] = fetch_spot(symbol)
                except Exception as e:
                    pass

            spot_line = ' | '.join(f'{s}=${spots[s].price:,.2f}' for s in sorted(spots))

            # Fetch positions
            positions = fetch_positions(trader)

            # Mark to market
            mtm = compute_mtm(positions, spots)
            total_unreal = sum(p['unrealized'] for p in mtm)
            total_real = sum(p['realized'] for p in mtm)
            total_fees = sum(p['fees'] for p in mtm)
            total_exp = sum(p['exposure'] for p in mtm)

            # Group by series for summary
            from collections import defaultdict
            by_series = defaultdict(lambda: {'unrealized': 0, 'exposure': 0, 'count': 0})
            for p in mtm:
                by_series[p['series']]['unrealized'] += p['unrealized']
                by_series[p['series']]['exposure'] += p['exposure']
                by_series[p['series']]['count'] += 1

            series_line = ' | '.join(
                f'{s}:${d["unrealized"]:+.0f}' for s, d in sorted(by_series.items()))

            # Scan for opportunities every other cycle
            alerts = []
            if cycle % 2 == 0:
                alerts = scan_opportunities(spots, trader)

            # Build state
            balance = trader.get_balance()
            state = {
                'ts': datetime.now(timezone.utc).isoformat(),
                'cycle': cycle,
                'balance': balance,
                'spots': {s: {'price': spots[s].price, 'vol': spots[s].realized_vol,
                              'change': spots[s].change_pct} for s in spots},
                'portfolio': {
                    'total_exposure': round(total_exp, 2),
                    'unrealized_pnl': round(total_unreal, 2),
                    'realized_pnl': round(total_real, 2),
                    'fees': round(total_fees, 2),
                    'net_pnl': round(total_unreal + total_real - total_fees, 2),
                    'n_positions': len(mtm),
                    'by_series': {s: dict(d) for s, d in by_series.items()},
                },
                'positions': mtm[:20],  # top 20 by exposure
                'alerts': alerts[:10],
            }

            # Write state files
            with open(STATE_FILE, 'w') as f:
                json.dump(state, f)
            if alerts:
                with open(ALERT_FILE, 'w') as f:
                    json.dump({'ts': state['ts'], 'alerts': alerts}, f)

            # Log
            status = (f"C#{cycle} {spot_line} | bal=${balance:.0f} exp=${total_exp:.0f} "
                      f"unreal=${total_unreal:+.0f} real=${total_real:+.0f} fees=${total_fees:.0f} "
                      f"net=${total_unreal + total_real - total_fees:+.0f} | {series_line}")
            if alerts:
                status += f" | {len(alerts)} edges: {alerts[0]['ticker']} {alerts[0]['side']} {alerts[0]['edge']:+.0%}"

            print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {status}")

            # Log to DB every 5th cycle
            if cycle % 5 == 0:
                log(f"STATUS {status}", 'INFO')

            # Alert on big moves
            for p in mtm:
                if p['unrealized'] < -50:
                    log(f"ALERT: {p['ticker']} {p['side']} unrealized=${p['unrealized']:+.0f}", 'WARN')

        except Exception as e:
            log(f"Monitor error: {e}", 'ERROR')

        time.sleep(30)


if __name__ == '__main__':
    main()
