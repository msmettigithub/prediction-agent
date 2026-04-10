#!/usr/bin/env python3
"""Trading Brain — unified observe/decide/act loop.

Every ~2s:
1. OBSERVE: fetch spots, positions, Kalshi orderbook
2. DECIDE: compute fair values, find edges, check existing positions
3. ACT: enter new positions, exit losing ones, scalp mispriced contracts

This replaces the separate monitor + auto_trader. One brain, one loop.
"""
import os, sys, json, time, sqlite3, math, re
import concurrent.futures
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
from config import load_config
from live.kalshi_trader import KalshiTrader
from live.guard import compute_shares
from tools.market_data import binary_price
from database.db import Database

TRADE_DB = str(Path(__file__).resolve().parent.parent / 'prediction_agent.db')
LOG_DB = '/home/jovyan/shared/sm/prediction-agent-db/prediction_agent.db'
STATE_FILE = '/tmp/live_monitor.json'

# Sessions
_yahoo = requests.Session()
_yahoo.headers.update({'User-Agent': 'Mozilla/5.0'})
_kalshi = requests.Session()
_cgecko = requests.Session()

# Strategy params
MAX_YES_PRICE = 0.22      # entry: buy NO when YES below this
MIN_EDGE = 0.05           # minimum vol-model edge to enter
MAX_BET = 15.0            # per trade
MIN_BALANCE = 100.0       # stop trading below this
MAX_TRADES_PER_CYCLE = 3  # don't flood
STALE_ORDER_SECONDS = 180 # cancel unfilled orders after 3 min
EXIT_EDGE = -0.03         # exit if our position has negative edge (market moved against us)
MACRO_SERIES = {'KXCPI', 'KXGDP', 'KXUNRATE'}  # need FRED data

SPOT_SYMBOLS = {
    'KXWTI': 'CL=F', 'KXBTCD': 'BTC-USD', 'KXETH': 'ETH-USD',
    'KXINX': '^GSPC', 'KXGOLDW': 'GC=F',
}


def log(msg, lvl='INFO'):
    ts = datetime.now(timezone.utc).strftime('%H:%M:%S.%f')[:12]
    print(f"[{ts}] [{lvl}] {msg}")
    if lvl in ('MILESTONE', 'ERROR', 'WARN'):
        try:
            c = sqlite3.connect(LOG_DB)
            c.execute("INSERT INTO agent_log(ts,lvl,agent,msg) VALUES(?,?,?,?)",
                      (datetime.now(timezone.utc).isoformat(), lvl, 'BRAIN', msg[:500]))
            c.commit(); c.close()
        except: pass


def kalshi_fee(price):
    return min(0.07 * price * (1 - price), 0.035) * 2


def parse_strike(ticker):
    m = re.search(r'-T(\d+\.?\d*)', ticker)
    if m: return float(m.group(1)), True
    m = re.search(r'-B(\d+\.?\d*)', ticker)
    if m: return float(m.group(1)), False
    return None, None


def get_series(ticker):
    for prefix in SPOT_SYMBOLS:
        if ticker.startswith(prefix):
            return prefix
    return None


# ── OBSERVE ──────────────────────────────────────────────────────────────────

def fetch_spots():
    """Parallel spot fetch. ~150ms."""
    spots = {}
    from tools.market_data import SpotData
    def _yahoo_fetch(symbol):
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
                vol = math.sqrt(sum((r-mean_r)**2 for r in rets)/len(rets) * len(rets))
        return SpotData(price=meta["regularMarketPrice"], prev_close=meta["previousClose"],
            change_pct=(meta["regularMarketPrice"]-meta["previousClose"])/meta["previousClose"]*100,
            high=max(closes) if closes else meta["regularMarketPrice"],
            low=min(closes) if closes else meta["regularMarketPrice"],
            realized_vol=max(0.005, vol), source="yahoo", timestamp=time.time())

    def _crypto_fetch():
        r = _cgecko.get('https://api.coingecko.com/api/v3/simple/price',
            params={'ids':'bitcoin,ethereum','vs_currencies':'usd','include_24hr_change':'true'}, timeout=5)
        r.raise_for_status(); d = r.json(); out = {}
        for coin, prefix in [('bitcoin','KXBTCD'), ('ethereum','KXETH')]:
            if coin in d:
                out[prefix] = SpotData(price=d[coin]['usd'], prev_close=d[coin]['usd'],
                    change_pct=d[coin].get('usd_24h_change',0) or 0,
                    high=d[coin]['usd'], low=d[coin]['usd'],
                    realized_vol=0.015 if coin=='bitcoin' else 0.020,
                    source='coingecko', timestamp=time.time())
        return out

    non_crypto = [(s, sym) for s, sym in SPOT_SYMBOLS.items() if s not in ('KXBTCD','KXETH')]
    with concurrent.futures.ThreadPoolExecutor(4) as ex:
        crypto_f = ex.submit(_crypto_fetch)
        yahoo_fs = {ex.submit(_yahoo_fetch, sym): s for s, sym in non_crypto}
        try: spots.update(crypto_f.result())
        except: pass
        for f in concurrent.futures.as_completed(yahoo_fs):
            try: spots[yahoo_fs[f]] = f.result()
            except: pass
    return spots


def fetch_positions(trader):
    url = f"{trader.TRADING_URL}/portfolio/positions"
    r = _kalshi.get(url, headers=trader._signed_headers("GET", url), params={'limit':200}, timeout=15)
    r.raise_for_status()
    return [p for p in r.json().get('market_positions', [])
            if float(p.get('market_exposure_dollars','0') or 0) > 0
            or abs(float(p.get('position_fp','0') or 0)) > 0.01]


def fetch_markets(event_tickers):
    """Fetch Kalshi markets for given event tickers. Returns {ticker: market_dict}."""
    markets = {}
    for et in event_tickers:
        try:
            r = _kalshi.get(f'https://api.elections.kalshi.com/trade-api/v2/events/{et}',
                           headers={'Accept':'application/json'}, timeout=10)
            if r.status_code != 200: continue
            for m in r.json().get('markets', []):
                markets[m.get('ticker', '')] = m
        except: pass
    return markets


def fetch_orders(trader):
    url = f"{trader.TRADING_URL}/portfolio/orders"
    r = _kalshi.get(url, headers=trader._signed_headers("GET", url), params={'limit':200}, timeout=10)
    r.raise_for_status()
    return r.json().get('orders', [])


# ── DECIDE ───────────────────────────────────────────────────────────────────

def compute_fair(spot, strike, vol, hours, is_threshold, series):
    """Compute fair value for a contract."""
    if hours <= 0: hours = 0.1
    if is_threshold:
        return binary_price(spot, strike, vol, hours)
    else:
        width = {'KXINX': 25, 'KXBTCD': 250, 'KXGOLDW': 20, 'KXWTI': 1, 'KXETH': 20}.get(series, 25)
        return max(0.001, binary_price(spot, strike, vol, hours)
                   - binary_price(spot, strike + width, vol, hours))


def find_entries(markets, spots, existing_tickers, balance, intel=None):
    """Find ALL mispriced contracts — both YES and NO side.
    Uses market intel to adjust edge thresholds and skip trades that go against conviction.
    Returns entries (actionable trades) and opportunities (all edges for logging)."""
    if intel is None: intel = {}
    entries = []
    opportunities = []

    for ticker, m in markets.items():
        series = get_series(ticker)
        if not series or series not in spots: continue
        if series in MACRO_SERIES: continue

        yes_ask = float(m.get('yes_ask_dollars', '0') or 0)
        no_ask = float(m.get('no_ask_dollars', '0') or 0)
        if yes_ask <= 0.01 and no_ask <= 0.01: continue

        strike, is_threshold = parse_strike(ticker)
        if strike is None: continue

        spot = spots[series]
        exp_str = m.get('expiration_time') or m.get('close_time') or ''
        hours = 16.0
        if exp_str:
            try:
                exp = datetime.fromisoformat(exp_str.replace('Z', '+00:00'))
                hours = max(0.1, (exp - datetime.now(timezone.utc)).total_seconds() / 3600)
            except: pass
        if hours < 0.5: continue

        fair_yes = compute_fair(spot.price, strike, spot.realized_vol, hours, is_threshold, series)

        # Check BOTH sides for edge
        edge_buy_yes = fair_yes - yes_ask if yes_ask > 0.01 else 0
        edge_buy_no = (1 - fair_yes) - no_ask if no_ask > 0.01 else 0

        best_side = 'yes' if edge_buy_yes > edge_buy_no else 'no'
        best_edge = max(edge_buy_yes, edge_buy_no)
        best_price = yes_ask if best_side == 'yes' else no_ask

        if best_edge > 0.03:  # log anything with >3pp edge
            # Check intel — does the data agree with this trade?
            view = intel.get(series)
            intel_agrees = True
            intel_note = ''
            if view:
                # If we want to buy YES (bullish), intel should not be bearish
                # If we want to buy NO (bearish), intel should not be bullish
                if best_side == 'yes' and view.is_bearish and view.conviction > 0.3:
                    intel_agrees = False
                    intel_note = f'intel={view.direction:+.2f} BEARISH blocks YES'
                elif best_side == 'no' and view.is_bullish and view.conviction > 0.3:
                    intel_agrees = False
                    intel_note = f'intel={view.direction:+.2f} BULLISH blocks NO'
                elif view.conviction > 0.2:
                    intel_note = f'intel={view.direction:+.2f} agrees'

            opp = {
                'ticker': ticker, 'side': best_side, 'edge': round(best_edge, 4),
                'fair_yes': round(fair_yes, 4), 'yes_ask': yes_ask, 'no_ask': no_ask,
                'spot': spot.price, 'strike': strike, 'hours': round(hours, 1),
                'series': series, 'held': ticker in existing_tickers,
                'intel': intel_note, 'intel_agrees': intel_agrees,
            }
            opportunities.append(opp)

            # Only actionable if edge > threshold, not already held, affordable, AND intel agrees
            if best_edge >= MIN_EDGE and ticker not in existing_tickers and best_price > 0.01 and intel_agrees:
                shares = compute_shares(MAX_BET, best_price)
                if shares >= 1:
                    cost = shares * best_price
                    if cost <= balance * 0.05:
                        # P&L FORECAST before entry
                        from model.trade_pnl import forecast_entry
                        win_prob = fair_yes if best_side == 'yes' else (1 - fair_yes)
                        fc = forecast_entry(ticker, best_side, shares, best_price, win_prob)
                        # Only enter if expected value is positive after fees
                        if fc.expected_pnl > 0:
                            entries.append({
                                **opp, 'shares': shares, 'price': best_price,
                                'cost': cost, 'expected_profit': fc.expected_pnl,
                                'forecast': {
                                    'profit_if_win': fc.profit_if_win,
                                    'loss_if_lose': fc.loss_if_lose,
                                    'roi_if_win': fc.roi_if_win,
                                    'expected_roi': fc.expected_roi,
                                    'breakeven_prob': fc.breakeven_prob,
                                    'risk_reward': fc.risk_reward,
                                },
                            })

    opportunities.sort(key=lambda x: -x['edge'])
    entries.sort(key=lambda x: -x['edge'])
    return entries[:MAX_TRADES_PER_CYCLE], opportunities


def find_exits(positions, spots, markets):
    """Find positions we should exit — spot moved against us."""
    exits = []
    for p in positions:
        ticker = p.get('ticker', '')
        series = get_series(ticker)
        if not series or series not in spots: continue

        pos_fp = float(p.get('position_fp', '0') or 0)
        exposure = float(p.get('market_exposure_dollars', '0') or 0)
        if abs(pos_fp) < 0.01: continue

        strike, is_threshold = parse_strike(ticker)
        if strike is None: continue

        spot = spots[series]
        m = markets.get(ticker)
        if not m: continue

        exp_str = m.get('expiration_time') or m.get('close_time') or ''
        hours = 16.0
        if exp_str:
            try:
                exp = datetime.fromisoformat(exp_str.replace('Z', '+00:00'))
                hours = max(0.1, (exp - datetime.now(timezone.utc)).total_seconds() / 3600)
            except: pass

        fair_yes = compute_fair(spot.price, strike, spot.realized_vol, hours, is_threshold, series)

        is_long_no = pos_fp < 0  # negative position_fp = short YES = long NO
        if is_long_no:
            fair_no = 1 - fair_yes
            no_bid = float(m.get('no_bid_dollars', '0') or 0)
            # We hold NO. If fair_no dropped below what we can sell for, edge is gone.
            if no_bid > 0.01 and fair_no < no_bid + EXIT_EDGE:
                exits.append({
                    'ticker': ticker, 'side': 'no', 'action': 'sell',
                    'shares': int(abs(pos_fp)), 'price': no_bid,
                    'reason': f'fair_no={fair_no:.0%} < bid={no_bid:.0%}',
                    'fair_yes': fair_yes, 'spot': spot.price,
                })

    return exits


def cancel_stale_orders(orders, trader):
    """Cancel orders resting longer than STALE_ORDER_SECONDS."""
    now = datetime.now(timezone.utc)
    cancelled = 0
    for o in orders:
        if o.get('status') != 'resting': continue
        created = o.get('created_time', '')
        if not created: continue
        try:
            created_dt = datetime.fromisoformat(created.replace('Z', '+00:00'))
            age = (now - created_dt).total_seconds()
            if age > STALE_ORDER_SECONDS:
                oid = o.get('order_id', '')
                url = f'{trader.TRADING_URL}/portfolio/orders/{oid}'
                _kalshi.delete(url, headers=trader._signed_headers('DELETE', url), timeout=10)
                cancelled += 1
        except: pass
    return cancelled


# ── ACT ──────────────────────────────────────────────────────────────────────

def place_order(trader, ticker, side, shares, price_cents):
    """Place order on Kalshi. Returns order dict or None."""
    url = f'{trader.TRADING_URL}/portfolio/orders'
    payload = {'ticker': ticker, 'side': side, 'action': 'buy', 'type': 'limit',
               'count': shares}
    if side == 'yes':
        payload['yes_price'] = price_cents
    else:
        payload['no_price'] = price_cents
    resp = _kalshi.post(url, headers=trader._signed_headers('POST', url),
                       json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()


# ── MAIN LOOP ────────────────────────────────────────────────────────────────

def build_event_tickers():
    now = datetime.now(timezone.utc)
    tickers = []
    for offset in range(5):  # today + 4 days ahead
        d = now + timedelta(days=offset)
        ds = d.strftime('%y%b%d').upper()
        tickers.extend([
            f'KXINX-{ds}H1600', f'KXBTCD-{ds}17',
            f'KXGOLDW-{ds}17', f'KXWTI-{ds}',
        ])
    # Monthly macro (even without FRED, log them as opportunities)
    for mo in ['26APR', '26MAY', '26JUN']:
        tickers.append(f'KXCPI-{mo}')
    tickers.extend(['KXGDP-26APR30', 'KXGDP-26JUL30'])
    return tickers


def main():
    log("=== TRADING BRAIN STARTING ===", 'MILESTONE')
    config = load_config()
    trader = KalshiTrader(config)

    if not config.live_trading_enabled:
        log("LIVE_TRADING_ENABLED=false, running in monitor-only mode", 'WARN')

    cycle = 0
    last_market_fetch = 0
    last_order_check = 0
    last_balance_check = 0
    last_db_log = 0
    balance = 0
    cached_markets = {}
    existing_tickers = set()
    total_entered = 0
    total_exited = 0

    while True:
        cycle += 1
        t0 = time.time()
        try:
            # ── OBSERVE ──
            with concurrent.futures.ThreadPoolExecutor(3) as ex:
                spots_f = ex.submit(fetch_spots)
                pos_f = ex.submit(fetch_positions, trader)
                # Balance every 30s
                bal_f = None
                if time.time() - last_balance_check > 30:
                    bal_f = ex.submit(trader.get_balance)
                spots = spots_f.result()
                positions = pos_f.result()
                if bal_f:
                    try: balance = bal_f.result(); last_balance_check = time.time()
                    except: pass

            # Refresh Kalshi markets every 10s
            if time.time() - last_market_fetch > 10:
                event_tickers = build_event_tickers()
                cached_markets = fetch_markets(event_tickers)
                last_market_fetch = time.time()

            # Track what we hold
            existing_tickers = {p.get('ticker','') for p in positions}

            # ── MARKET INTEL ──
            try:
                from model.market_intel import get_market_intel, format_intel
                intel = get_market_intel(hours_ahead=16.0)
                # Log intel every 60s
                if time.time() - last_db_log > 55:
                    log(f"INTEL: {format_intel(intel)}")
                # Map series to intel
                series_intel = {}
                for name, view in intel.items():
                    if name == 'WTI': series_intel['KXWTI'] = view
                    elif name == 'BTC': series_intel['KXBTCD'] = view
                    elif name == 'GOLD': series_intel['KXGOLDW'] = view
                    elif name == 'SPX': series_intel['KXINX'] = view
                    elif name == 'ETH': series_intel['KXETH'] = view
            except Exception as ie:
                intel = {}; series_intel = {}

            # ── DECIDE ──
            # MTM
            total_exp = sum(float(p.get('market_exposure_dollars','0') or 0) for p in positions)
            total_pnl = sum(float(p.get('realized_pnl_dollars','0') or 0) for p in positions)
            by_series = defaultdict(lambda: {'exp': 0, 'n': 0})
            for p in positions:
                s = get_series(p.get('ticker',''))
                if s:
                    by_series[s]['exp'] += float(p.get('market_exposure_dollars','0') or 0)
                    by_series[s]['n'] += 1

            # Find entries + all opportunities (with intel)
            entries = []
            opportunities = []
            if balance > 0:
                entries, opportunities = find_entries(cached_markets, spots, existing_tickers, balance, series_intel)

            # Find exits
            exits = find_exits(positions, spots, cached_markets)

            # Cancel stale orders every 30s
            cancelled = 0
            if time.time() - last_order_check > 30:
                try:
                    orders = fetch_orders(trader)
                    cancelled = cancel_stale_orders(orders, trader)
                    last_order_check = time.time()
                except: pass

            # ── ACT ──
            # Execute entries — only when we've validated the strategy works
            # Currently: monitor-only mode. Log opportunities but don't trade.
            # Enable by setting BRAIN_TRADE_ENABLED=true in environment.
            brain_enabled = os.environ.get('BRAIN_TRADE_ENABLED', 'false').lower() == 'true'
            if entries and brain_enabled:
                for e in entries:
                    price_cents = max(1, min(99, int(round(e['price'] * 100))))
                    try:
                        order = place_order(trader, e['ticker'], e['side'], e['shares'], price_cents)
                        oid = order.get('order',{}).get('order_id','')[:12] if isinstance(order.get('order'), dict) else ''
                        fc = e.get('forecast', {})
                        log(f"ENTER: BUY {e['side'].upper()} {e['ticker']} {e['shares']}sh @{price_cents}c "
                                f"edge={e['edge']:+.0%} EV=${e.get('expected_profit',0):+.2f} "
                                f"win=${fc.get('profit_if_win',0):+.2f} lose=${fc.get('loss_if_lose',0):.2f} "
                                f"r:r={fc.get('risk_reward',0):.1f} breakeven={fc.get('breakeven_prob',0):.0%} "
                                f"| {oid}", 'MILESTONE')
                        total_entered += 1
                        existing_tickers.add(e['ticker'])
                    except Exception as ex:
                        log(f"ENTRY FAILED: {e['ticker']} — {str(ex)[:80]}", 'ERROR')
            elif entries:
                log(f"{len(entries)} entry signals (monitor-only, set BRAIN_TRADE_ENABLED=true to trade)", 'INFO')

            # ── POSITION EVALUATION — forecast every open position ──
            from model.trade_pnl import forecast_hold, forecast_exit
            hold_reports = []
            exit_candidates = []
            for p in positions:
                ticker = p.get('ticker', '')
                series = get_series(ticker)
                if not series or series not in spots: continue

                pos_fp = float(p.get('position_fp', '0') or 0)
                if abs(pos_fp) < 0.01: continue
                exp = float(p.get('market_exposure_dollars', '0') or 0)
                shares = int(abs(pos_fp))
                side = 'yes' if pos_fp > 0 else 'no'

                # Get entry price from local DB
                entry_price = exp / shares if shares > 0 else 0.50

                # Current fair value
                strike, is_threshold = parse_strike(ticker)
                if strike is None: continue
                spot = spots[series]
                exp_str = cached_markets.get(ticker, {}).get('expiration_time') or cached_markets.get(ticker, {}).get('close_time') or ''
                hours = 16.0
                if exp_str:
                    try:
                        exp_dt = datetime.fromisoformat(exp_str.replace('Z', '+00:00'))
                        hours = max(0.1, (exp_dt - datetime.now(timezone.utc)).total_seconds() / 3600)
                    except: pass

                fair_yes = compute_fair(spot.price, strike, spot.realized_vol, hours, is_threshold, series)
                current_fair = fair_yes if side == 'yes' else 1 - fair_yes
                win_prob = fair_yes if side == 'yes' else 1 - fair_yes

                # Current bid from market
                m = cached_markets.get(ticker, {})
                if side == 'yes':
                    current_bid = float(m.get('yes_bid_dollars', '0') or 0)
                else:
                    current_bid = float(m.get('no_bid_dollars', '0') or 0)

                hf = forecast_hold(ticker, side, shares, entry_price,
                                   current_fair, current_bid, win_prob)
                hold_reports.append(hf)

                # Should we exit? Check with forecast_exit
                if current_bid > 0.01:
                    ef = forecast_exit(ticker, side, shares, entry_price, current_bid)
                    # EXIT RULES (learned the hard way):
                    # 1. TAKE PROFIT: only if profitable AND fair value dropped below 90% of entry
                    #    (meaning we got lucky — edge flipped, lock in gains)
                    # 2. NO STOP LOSS on binary contracts — they resolve at $1 or $0.
                    #    Selling at a loss is almost always wrong. The only time to stop out
                    #    is if win_prob < 2% (virtually certain to lose) AND loss is > $20.
                    #    Otherwise HOLD — the asymmetry (pay 10c, win $1) is the whole point.
                    if ef.is_profitable and current_fair < entry_price * 0.90:
                        exit_candidates.append(('TAKE_PROFIT', ef, hf))

            # Log position summary every 60s
            if time.time() - last_db_log > 55 and hold_reports:
                winners = sum(1 for h in hold_reports if h.realizable_pnl > 0)
                losers = sum(1 for h in hold_reports if h.realizable_pnl < 0)
                total_unreal = sum(h.realizable_pnl for h in hold_reports)
                log(f"POSITIONS: {len(hold_reports)} tracked, {winners}W/{losers}L, "
                    f"realizable=${total_unreal:+.2f}")

            # Execute exits only with P&L check
            for reason, ef, hf in exit_candidates[:1]:  # max 1 exit per cycle
                if not config.live_trading_enabled: continue
                if not ef.is_profitable and reason != 'STOP_LOSS': continue
                price_cents = max(1, min(99, int(round(ef.exit_price * 100))))
                try:
                    url = f'{trader.TRADING_URL}/portfolio/orders'
                    payload = {'ticker': ef.ticker, 'side': ef.side, 'action': 'sell',
                               'type': 'limit', 'count': ef.shares}
                    if ef.side == 'yes':
                        payload['yes_price'] = price_cents
                    else:
                        payload['no_price'] = price_cents
                    resp = _kalshi.post(url, headers=trader._signed_headers('POST', url),
                                      json=payload, timeout=15)
                    resp.raise_for_status()
                    oid = resp.json().get('order',{}).get('order_id','')[:12] if isinstance(resp.json().get('order'), dict) else ''
                    log(f"EXIT {reason}: SELL {ef.side.upper()} {ef.ticker} {ef.shares}sh @{price_cents}c "
                        f"pnl=${ef.realized_pnl:+.2f} ({ef.realized_roi:+.0%}) "
                        f"entry={ef.entry_price:.2f} exit={ef.exit_price:.2f} | {oid}", 'MILESTONE')
                    total_exited += 1
                except Exception as ex:
                    log(f"EXIT FAILED: {ef.ticker} — {str(ex)[:80]}", 'ERROR')

            # ── WRITE STATE ──
            net = total_pnl  # simplified — full MTM is in the old monitor
            state = {
                'ts': datetime.now(timezone.utc).isoformat(),
                'cycle': cycle,
                'balance': balance,
                'spots': {s: {'price': spots[s].price, 'vol': spots[s].realized_vol,
                              'change': spots[s].change_pct} for s in spots},
                'portfolio': {
                    'total_exposure': round(total_exp, 2),
                    'realized_pnl': round(total_pnl, 2),
                    'n_positions': len(positions),
                    'by_series': {s: dict(d) for s, d in by_series.items()},
                },
                'entries_available': len(entries),
                'opportunities': len(opportunities),
                'exit_signals': len(exits),
                'total_entered': total_entered,
                'total_exited': total_exited,
                'intel': {name: {'dir': v.direction, 'conv': v.conviction, 'vol': v.vol_regime,
                                 'range': [v.expected_low, v.expected_high],
                                 'signals': [{'name': s.name, 'dir': s.direction, 'src': s.source} for s in v.signals]}
                         for name, v in (intel.items() if intel else {})},
                'alerts': [{'ticker': o['ticker'], 'side': o['side'].upper(), 'edge': o['edge'],
                           'fair': o['fair_yes'] if o['side']=='yes' else 1-o['fair_yes'],
                           'market': o['yes_ask'] if o['side']=='yes' else o['no_ask'],
                           'spot': o['spot'], 'strike': o['strike'], 'series': o['series'],
                           'hours': o['hours'], 'held': o.get('held', False),
                           'intel': o.get('intel', ''), 'intel_ok': o.get('intel_agrees', True)}
                          for o in opportunities[:15]],
                'hold_reports': [{
                    'ticker': h.ticker, 'side': h.side, 'shares': h.shares,
                    'entry': h.entry_price, 'fair': h.current_fair, 'bid': h.current_bid,
                    'unrealized': h.unrealized_pnl, 'realizable': h.realizable_pnl,
                    'pnl_pct': h.pnl_pct, 'win_prob': h.win_probability, 'ev': h.expected_pnl,
                } for h in sorted(hold_reports, key=lambda h: h.realizable_pnl)[:20]],
                'exit_candidates': [{'reason': r, 'ticker': ef.ticker, 'pnl': ef.realized_pnl,
                                     'profitable': ef.is_profitable} for r, ef, _ in exit_candidates],
                'positions': [{
                    'ticker': p.get('ticker',''),
                    'series': get_series(p.get('ticker','')) or '',
                    'side': 'LONG' if float(p.get('position_fp','0') or 0) > 0 else 'SHORT',
                    'shares': int(abs(float(p.get('position_fp','0') or 0))),
                    'exposure': round(float(p.get('market_exposure_dollars','0') or 0), 2),
                    'realized': round(float(p.get('realized_pnl_dollars','0') or 0), 2),
                } for p in sorted(positions, key=lambda p: -float(p.get('market_exposure_dollars','0') or 0))[:20]],
            }
            with open(STATE_FILE, 'w') as f:
                json.dump(state, f)

            # Print status
            elapsed = (time.time() - t0) * 1000
            spot_line = ' '.join(f'{s}={spots[s].price:,.0f}' for s in sorted(spots))
            action_line = ''
            if opportunities: action_line += f' edges={len(opportunities)}'
            if entries: action_line += f' +{len(entries)}trades'
            if exits: action_line += f' !{len(exits)}exits'
            if cancelled: action_line += f' x{cancelled}stale'
            print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S.%f')[:12]}] "
                  f"{elapsed:.0f}ms C#{cycle} {spot_line} bal=${balance:.0f} "
                  f"exp=${total_exp:.0f} pnl=${total_pnl:+.0f} "
                  f"pos={len(positions)}{action_line}")

            # DB log every 60s
            if time.time() - last_db_log > 60:
                series_line = ' '.join(f'{s}:${d["exp"]:.0f}({d["n"]})' for s, d in sorted(by_series.items()))
                log(f"C#{cycle} bal=${balance:.0f} exp=${total_exp:.0f} pnl=${total_pnl:+.0f} "
                    f"entered={total_entered} | {series_line}")
                last_db_log = time.time()

        except Exception as e:
            log(f"Brain error: {e}", 'ERROR')
            time.sleep(1)
            continue

        # Target 2s cycles
        elapsed = time.time() - t0
        remaining = max(0, 2.0 - elapsed)
        if remaining > 0:
            time.sleep(remaining)


if __name__ == '__main__':
    main()
