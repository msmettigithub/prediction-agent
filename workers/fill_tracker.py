#!/usr/bin/env python3
"""Fill Tracker Worker — monitors resting Kalshi orders, cancels stale ones, backfills fills.

Runs as a Saturn Cloud job on cron (every 5 min).
- Fetches all orders from Kalshi portfolio
- Cancels resting orders older than 5 minutes
- Backfills filled orders missing from local live_trades table
- Logs stats to agent_log
"""
import os, sys, sqlite3, time
from datetime import datetime, timezone, timedelta

sys.path.insert(0, '/home/jovyan/workspace/prediction-agent')

from config import load_config
from live.kalshi_trader import KalshiTrader

DB_PATH = os.environ.get('TRADE_DB', str(__import__('pathlib').Path(__file__).resolve().parent.parent / 'prediction_agent.db'))
LOG_DB = '/home/jovyan/shared/sm/prediction-agent-db/prediction_agent.db'

STALE_MINUTES = 5


def log(msg, lvl='INFO'):
    print(f"[{lvl}] {msg}")
    try:
        c = sqlite3.connect(LOG_DB)
        c.execute("INSERT INTO agent_log(ts,lvl,agent,msg) VALUES(?,?,?,?)",
                  (datetime.now(timezone.utc).isoformat(), lvl, 'FILL_TRACKER', msg[:500]))
        c.commit()
        c.close()
    except Exception as e:
        print(f"Log write failed: {e}")


def fetch_orders(trader):
    """Fetch all orders from Kalshi portfolio."""
    url = f"{trader.TRADING_URL}/portfolio/orders?limit=200"
    import requests
    resp = requests.get(url, headers=trader._signed_headers('GET', url), timeout=15)
    resp.raise_for_status()
    return resp.json().get('orders', [])


def cancel_order(trader, order_id):
    """Cancel a single resting order."""
    url = f"{trader.TRADING_URL}/portfolio/orders/{order_id}"
    import requests
    resp = requests.delete(url, headers=trader._signed_headers('DELETE', url), timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_known_order_ids(db_path):
    """Get all kalshi_order_ids already in live_trades."""
    c = sqlite3.connect(db_path)
    rows = c.execute("SELECT kalshi_order_id FROM live_trades WHERE kalshi_order_id IS NOT NULL").fetchall()
    c.close()
    return {r[0] for r in rows}


def backfill_filled_order(db_path, order):
    """Insert a filled order into live_trades if not already present."""
    ticker = order.get('ticker', '')
    side = order.get('side', '').upper()
    yes_price = order.get('yes_price', 0)
    no_price = order.get('no_price', 0)

    if side == 'YES':
        entry_price = float(yes_price) / 100.0 if yes_price else 0.0
    else:
        entry_price = float(no_price) / 100.0 if no_price else 0.0

    shares = int(order.get('count', 0))
    cost = round(shares * entry_price, 2)
    order_id = order.get('order_id', '')

    now = datetime.now(timezone.utc).isoformat()
    created = order.get('created_time', now)

    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row

    # Find or create contract
    row = c.execute("SELECT id FROM contracts WHERE source='kalshi' AND source_id=?", (ticker,)).fetchone()
    if row:
        contract_id = row['id']
    else:
        c.execute("""INSERT INTO contracts(source, source_id, title, category, yes_price,
                     volume_24h, resolved, created_at, updated_at)
                     VALUES('kalshi',?,?,'',%s,0,0,?,?)""" % entry_price,
                  (ticker, ticker, now, now))
        contract_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]

    c.execute("""INSERT INTO live_trades(contract_id, kalshi_order_id, kalshi_ticker, side,
                 entry_price, shares, cost, max_payout, model_prob, edge_at_entry,
                 status, opened_at)
                 VALUES(?,?,?,?,?,?,?,?,0.5,0.0,'open',?)""",
              (contract_id, order_id, ticker, side, entry_price, shares, cost, shares, created))
    c.commit()
    c.close()


def main():
    log("=== FILL_TRACKER STARTING ===", 'MILESTONE')

    config = load_config()
    trader = KalshiTrader(config)

    try:
        orders = fetch_orders(trader)
    except Exception as e:
        log(f"Failed to fetch orders: {e}", 'ERROR')
        return

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=STALE_MINUTES)
    known_ids = get_known_order_ids(DB_PATH)

    stats = {'resting': 0, 'filled': 0, 'cancelled': 0, 'stale_cancelled': 0, 'backfilled': 0}

    for order in orders:
        status = order.get('status', '')
        order_id = order.get('order_id', '')

        if status == 'resting':
            stats['resting'] += 1
            # Parse creation time
            created_str = order.get('created_time', '')
            try:
                created = datetime.fromisoformat(created_str.replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                created = now  # can't parse = treat as fresh

            if created < cutoff:
                try:
                    cancel_order(trader, order_id)
                    stats['stale_cancelled'] += 1
                    log(f"Cancelled stale order {order_id} ({order.get('ticker','')})")
                except Exception as e:
                    log(f"Failed to cancel {order_id}: {e}", 'ERROR')
                time.sleep(0.2)

        elif status == 'canceled':
            stats['cancelled'] += 1

        elif status == 'executed':
            stats['filled'] += 1
            if order_id and order_id not in known_ids:
                try:
                    backfill_filled_order(DB_PATH, order)
                    stats['backfilled'] += 1
                    log(f"Backfilled fill {order_id} ({order.get('ticker','')})")
                except Exception as e:
                    log(f"Backfill error {order_id}: {e}", 'ERROR')

    summary = (f"resting={stats['resting']} filled={stats['filled']} "
               f"cancelled={stats['cancelled']} stale_cancelled={stats['stale_cancelled']} "
               f"backfilled={stats['backfilled']}")
    log(f"=== FILL_TRACKER DONE: {summary} ===", 'MILESTONE')


if __name__ == '__main__':
    main()
