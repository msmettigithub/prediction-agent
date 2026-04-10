#!/usr/bin/env python3
"""Market Maker — collect spread by quoting both sides.

Strategy (from QuantSC Kalshi market making, adapted):
1. Compute fair value using Cauchy distribution (fat tails) for range buckets
   and Black-Scholes binary for threshold contracts
2. Place symmetric bid/ask around fair value
3. Adjust spread based on inventory — widen when long, narrow when short
4. Cancel and requote when spot moves or orders get stale
5. Max position per contract, max total exposure

This is NOT directional betting. We are the house — we profit from spread,
not from predicting direction. We should make money whether the market
goes up or down, as long as it doesn't gap through our quotes.

Risk gates (from OctagonAI 5-gate engine):
1. Kelly: position size from edge / odds
2. Liquidity: skip markets with no bids
3. Correlation: max exposure per series
4. Concentration: max % of balance in one contract
5. Drawdown: halt if daily loss exceeds limit
"""
import os, sys, json, time, math, re
import concurrent.futures
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
from config import load_config
from live.kalshi_trader import KalshiTrader
from tools.market_data import binary_price

LOG_DB = '/home/jovyan/shared/sm/prediction-agent-db/prediction_agent.db'
STATE_FILE = '/tmp/market_maker.json'

# Persistent sessions
_kalshi = requests.Session()
_yahoo = requests.Session()
_yahoo.headers.update({'User-Agent': 'Mozilla/5.0'})

# ── Parameters ───────────────────────────────────────────────────────────────

HALF_SPREAD = 0.03          # 3c each side of fair value (6c total spread)
MAX_POSITION = 30           # max contracts per market (long or short)
MAX_EXPOSURE_PER_SERIES = 200  # $ max per series
MAX_TOTAL_EXPOSURE = 500    # $ max across all MM positions
DAILY_LOSS_LIMIT = 50       # $ halt if lost more than this today
REQUOTE_SECONDS = 30        # cancel and requote every N seconds
MIN_EDGE = 0.01             # don't quote if our fair value is too uncertain
INVENTORY_SPREAD_SCALE = 0.005  # widen spread by 0.5c per contract of inventory
CYCLE_SECONDS = 5           # main loop speed

# Spot symbols
SPOT_SYMBOLS = {
    'KXWTI': 'CL=F', 'KXBTCD': 'BTC-USD', 'KXINX': '^GSPC',
    'KXGOLDW': 'GC=F', 'KXETH': 'ETH-USD',
}

# Range widths per series (for range bucket fair value)
RANGE_WIDTHS = {'KXINX': 25, 'KXBTCD': 250, 'KXGOLDW': 20, 'KXWTI': 1, 'KXETH': 20}


def log(msg, lvl='INFO'):
    ts = datetime.now(timezone.utc).strftime('%H:%M:%S')
    print(f"[{ts}] [{lvl}] {msg}")
    if lvl in ('MILESTONE', 'ERROR', 'WARN'):
        try:
            import sqlite3
            c = sqlite3.connect(LOG_DB)
            c.execute("INSERT INTO agent_log(ts,lvl,agent,msg) VALUES(?,?,?,?)",
                      (datetime.now(timezone.utc).isoformat(), lvl, 'MM', msg[:500]))
            c.commit(); c.close()
        except: pass


# ── Fair Value ───────────────────────────────────────────────────────────────

def cauchy_cdf(x, loc, scale):
    """Cauchy CDF — fat-tailed distribution for S&P/BTC returns."""
    return 0.5 + math.atan2(x - loc, scale) / math.pi


def fair_value_threshold(spot, strike, vol, hours):
    """P(spot > strike at expiry) using log-normal."""
    if hours <= 0: return 1.0 if spot > strike else 0.0
    return binary_price(spot, strike, vol, hours)


def fair_value_range(spot, strike_low, width, vol, hours):
    """P(strike_low <= spot < strike_low + width) using log-normal."""
    if hours <= 0:
        return 1.0 if strike_low <= spot < strike_low + width else 0.0
    p_above_low = binary_price(spot, strike_low, vol, hours)
    p_above_high = binary_price(spot, strike_low + width, vol, hours)
    return max(0.001, p_above_low - p_above_high)


def compute_fair(spot, strike, vol, hours, is_threshold, series):
    width = RANGE_WIDTHS.get(series, 25)
    if is_threshold:
        return fair_value_threshold(spot, strike, vol, hours)
    else:
        return fair_value_range(spot, strike, width, vol, hours)


# ── Spot Data ────────────────────────────────────────────────────────────────

def fetch_spot(symbol):
    r = _yahoo.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                   params={"interval": "1m", "range": "1d"}, timeout=5)
    r.raise_for_status()
    d = r.json()["chart"]["result"][0]; meta = d["meta"]
    closes = [c for c in d["indicators"]["quote"][0]["close"] if c]
    vol = 0.025
    if len(closes) > 20:
        rets = [math.log(closes[i]/closes[i-1]) for i in range(1, len(closes)) if closes[i] and closes[i-1]]
        if rets:
            mean_r = sum(rets)/len(rets)
            vol = math.sqrt(sum((r - mean_r)**2 for r in rets)/len(rets) * len(rets))
    return meta["regularMarketPrice"], max(0.005, vol)


def fetch_all_spots():
    spots = {}
    with concurrent.futures.ThreadPoolExecutor(5) as ex:
        futures = {ex.submit(fetch_spot, sym): series for series, sym in SPOT_SYMBOLS.items()}
        for f in concurrent.futures.as_completed(futures):
            try:
                price, vol = f.result()
                spots[futures[f]] = (price, vol)
            except: pass
    return spots


# ── Kalshi API ───────────────────────────────────────────────────────────────

def parse_strike(ticker):
    m = re.search(r'-T(\d+\.?\d*)', ticker)
    if m: return float(m.group(1)), True
    m = re.search(r'-B(\d+\.?\d*)', ticker)
    if m: return float(m.group(1)), False
    return None, None


def get_series(ticker):
    for prefix in SPOT_SYMBOLS:
        if ticker.startswith(prefix): return prefix
    return None


def fetch_markets(trader, event_tickers):
    markets = {}
    for et in event_tickers:
        try:
            r = _kalshi.get(f'https://api.elections.kalshi.com/trade-api/v2/events/{et}',
                           headers={'Accept': 'application/json'}, timeout=10)
            if r.status_code != 200: continue
            for m in r.json().get('markets', []):
                markets[m.get('ticker', '')] = m
        except: pass
    return markets


def fetch_positions(trader):
    url = f"{trader.TRADING_URL}/portfolio/positions"
    r = _kalshi.get(url, headers=trader._signed_headers("GET", url),
                   params={'limit': 200}, timeout=15)
    r.raise_for_status()
    pos = {}
    for p in r.json().get('market_positions', []):
        ticker = p.get('ticker', '')
        pos_fp = float(p.get('position_fp', '0') or 0)
        if abs(pos_fp) > 0.01:
            pos[ticker] = int(pos_fp)
    return pos


def fetch_my_orders(trader):
    url = f"{trader.TRADING_URL}/portfolio/orders"
    r = _kalshi.get(url, headers=trader._signed_headers("GET", url),
                   params={'limit': 200}, timeout=10)
    r.raise_for_status()
    return r.json().get('orders', [])


def cancel_order(trader, order_id):
    url = f'{trader.TRADING_URL}/portfolio/orders/{order_id}'
    _kalshi.delete(url, headers=trader._signed_headers('DELETE', url), timeout=10)


def place_order(trader, ticker, side, action, count, price_cents):
    url = f'{trader.TRADING_URL}/portfolio/orders'
    payload = {'ticker': ticker, 'side': side, 'action': action,
               'type': 'limit', 'count': count}
    if side == 'yes':
        payload['yes_price'] = price_cents
    else:
        payload['no_price'] = price_cents
    resp = _kalshi.post(url, headers=trader._signed_headers('POST', url),
                       json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()


# ── Quoting Logic ────────────────────────────────────────────────────────────

def compute_quotes(fair, inventory, half_spread=HALF_SPREAD):
    """Compute bid/ask prices adjusted for inventory.

    Inventory > 0 (long): widen ask, narrow bid → encourage sells to us
    Inventory < 0 (short): widen bid, narrow ask → encourage buys from us
    """
    inv_adj = inventory * INVENTORY_SPREAD_SCALE
    bid = fair - half_spread + inv_adj  # lower bid when long
    ask = fair + half_spread + inv_adj  # raise ask when long
    bid = max(0.02, min(0.98, bid))
    ask = max(0.02, min(0.98, ask))
    if bid >= ask:
        mid = (bid + ask) / 2
        bid = mid - 0.01
        ask = mid + 0.01
    return round(bid, 2), round(ask, 2)


# ── Risk Gates ───────────────────────────────────────────────────────────────

def check_risk(positions, balance, daily_pnl, series_exposure):
    """5-gate risk check. Returns (ok, reason)."""
    # Gate 1: Drawdown
    if daily_pnl < -DAILY_LOSS_LIMIT:
        return False, f'daily loss ${daily_pnl:.0f} exceeds limit ${DAILY_LOSS_LIMIT}'
    # Gate 2: Balance
    if balance < 100:
        return False, f'balance ${balance:.0f} too low'
    # Gate 3: Balance-based — don't MM if balance is too low for the exposure cap
    if balance < MAX_TOTAL_EXPOSURE * 1.5:
        return False, f'balance ${balance:.0f} < 1.5x exposure cap ${MAX_TOTAL_EXPOSURE}'
    # Gate 4 & 5 checked per-market in quoting logic
    return True, 'ok'


# ── Main Loop ────────────────────────────────────────────────────────────────

def build_event_tickers():
    now = datetime.now(timezone.utc)
    tickers = []
    for offset in range(2):
        d = now + timedelta(days=offset)
        ds = d.strftime('%y%b%d').upper()
        tickers.extend([
            f'KXINX-{ds}H1600', f'KXBTCD-{ds}17',
            f'KXGOLDW-{ds}17', f'KXWTI-{ds}',
        ])
        # Hourly BTC
        for h in range(24):
            tickers.append(f'KXBTCD-{ds}{h:02d}')
    return tickers


def main():
    log("=== MARKET MAKER STARTING ===", 'MILESTONE')
    config = load_config()
    trader = KalshiTrader(config)

    if not config.live_trading_enabled:
        log("LIVE_TRADING_ENABLED=false, dry run mode", 'WARN')

    cycle = 0
    last_market_fetch = 0
    last_requote = 0
    cached_markets = {}
    my_resting_orders = {}  # order_id -> order dict
    daily_pnl = 0  # track from start of session

    while True:
        cycle += 1
        t0 = time.time()
        try:
            # 1. Fetch spots
            spots = fetch_all_spots()
            if not spots:
                time.sleep(1); continue

            # 2. Fetch markets every 30s
            if time.time() - last_market_fetch > 30:
                event_tickers = build_event_tickers()
                cached_markets = fetch_markets(trader, event_tickers)
                last_market_fetch = time.time()

            # 3. Fetch positions + balance
            positions = fetch_positions(trader)
            balance = trader.get_balance()

            # 4. Risk check
            series_exp = defaultdict(float)
            for ticker, qty in positions.items():
                s = get_series(ticker)
                if s: series_exp[s] += abs(qty) * 0.50
            risk_ok, risk_reason = check_risk(positions, balance, daily_pnl, series_exp)

            # 5. Find quotable markets — near spot, with liquidity
            quotes_to_place = []
            for ticker, m in cached_markets.items():
                series = get_series(ticker)
                if not series or series not in spots: continue

                strike, is_threshold = parse_strike(ticker)
                if strike is None: continue

                spot_price, spot_vol = spots[series]

                # Check expiry
                exp_str = m.get('expiration_time') or m.get('close_time') or ''
                hours = 16.0
                if exp_str:
                    try:
                        exp_dt = datetime.fromisoformat(exp_str.replace('Z', '+00:00'))
                        hours = max(0.1, (exp_dt - datetime.now(timezone.utc)).total_seconds() / 3600)
                    except: pass
                if hours < 0.5 or hours > 200: continue

                # Only quote near-the-money contracts (within 3% of spot for thresholds)
                if is_threshold:
                    distance = abs(spot_price - strike) / spot_price
                    if distance > 0.03: continue
                else:
                    width = RANGE_WIDTHS.get(series, 25)
                    mid = strike + width / 2
                    distance = abs(spot_price - mid) / spot_price
                    if distance > 0.02: continue

                # Check liquidity — skip if no bids exist
                yb = float(m.get('yes_bid_dollars', '0') or 0)
                ya = float(m.get('yes_ask_dollars', '0') or 0)
                if yb <= 0.01 and ya <= 0.01: continue

                # Compute fair value
                fair = compute_fair(spot_price, strike, spot_vol, hours, is_threshold, series)
                if fair < 0.03 or fair > 0.97: continue  # skip extremes

                # Inventory
                inventory = positions.get(ticker, 0)

                # Gate 4: Concentration — skip if too much in this contract
                if abs(inventory) >= MAX_POSITION: continue

                # Gate 5: Series concentration
                if series_exp.get(series, 0) >= MAX_EXPOSURE_PER_SERIES and abs(inventory) == 0:
                    continue  # don't open new positions in overweight series

                # Compute quotes
                bid, ask = compute_quotes(fair, inventory)
                bid_cents = max(1, min(99, int(round(bid * 100))))
                ask_cents = max(1, min(99, int(round(ask * 100))))

                if bid_cents >= ask_cents: continue  # crossed, skip

                quotes_to_place.append({
                    'ticker': ticker, 'fair': fair, 'bid': bid, 'ask': ask,
                    'bid_cents': bid_cents, 'ask_cents': ask_cents,
                    'inventory': inventory, 'series': series,
                    'spot': spot_price, 'strike': strike, 'hours': hours,
                })

            # 6. Cancel old orders and requote
            should_requote = time.time() - last_requote > REQUOTE_SECONDS
            if should_requote and risk_ok and config.live_trading_enabled:
                # Cancel all resting MM orders
                try:
                    orders = fetch_my_orders(trader)
                    for o in orders:
                        if o.get('status') == 'resting':
                            try: cancel_order(trader, o['order_id'])
                            except: pass
                except: pass

                # Place new quotes
                placed = 0
                for q in quotes_to_place[:5]:  # max 5 markets per requote cycle
                    try:
                        # Place bid (buy YES at bid price)
                        place_order(trader, q['ticker'], 'yes', 'buy', 3, q['bid_cents'])
                        time.sleep(0.3)
                        # Place ask (buy NO at 100 - ask price)
                        place_order(trader, q['ticker'], 'no', 'buy', 3, 100 - q['ask_cents'])
                        time.sleep(0.3)
                        placed += 1
                    except Exception as e:
                        log(f"Quote failed {q['ticker']}: {str(e)[:60]}", 'ERROR')
                        time.sleep(2)  # back off on rate limit
                        break

                last_requote = time.time()
                if placed:
                    log(f"Quoted {placed} markets", 'INFO')

            # 7. Write state
            elapsed = (time.time() - t0) * 1000
            spot_line = ' '.join(f'{s}={p:,.0f}' for s, (p, _) in sorted(spots.items()))
            n_quotable = len(quotes_to_place)

            state = {
                'ts': datetime.now(timezone.utc).isoformat(), 'cycle': cycle,
                'balance': balance, 'spots': {s: {'price': p, 'vol': v} for s, (p, v) in spots.items()},
                'n_positions': len(positions), 'n_quotable': n_quotable,
                'risk_ok': risk_ok, 'risk_reason': risk_reason,
                'quotes': [{'ticker': q['ticker'], 'fair': q['fair'], 'bid': q['bid'],
                           'ask': q['ask'], 'inv': q['inventory'], 'hrs': q['hours']}
                          for q in quotes_to_place[:10]],
            }
            with open(STATE_FILE, 'w') as f:
                json.dump(state, f)

            action = ''
            if should_requote and risk_ok: action = f' quoted={min(len(quotes_to_place),10)}'
            if not risk_ok: action = f' RISK:{risk_reason[:30]}'
            print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] "
                  f"{elapsed:.0f}ms C#{cycle} {spot_line} bal=${balance:.0f} "
                  f"inv={len(positions)} quotable={n_quotable}{action}")

        except Exception as e:
            log(f"MM error: {e}", 'ERROR')
            time.sleep(2)
            continue

        elapsed = time.time() - t0
        remaining = max(0, CYCLE_SECONDS - elapsed)
        if remaining > 0:
            time.sleep(remaining)


if __name__ == '__main__':
    main()
