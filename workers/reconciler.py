#!/usr/bin/env python3
"""Position Reconciler — syncs Kalshi positions with local live_trades table.

Runs periodically to backfill missing positions and settle closed ones.
"""
import os, sys, sqlite3, json, time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
from config import load_config
from live.kalshi_trader import KalshiTrader
from database.db import Database

TRADE_DB = os.environ.get('TRADE_DB', str(Path(__file__).resolve().parent.parent / 'prediction_agent.db'))
LOG_DB = '/home/jovyan/shared/sm/prediction-agent-db/prediction_agent.db'

AGENT = 'RECONCILER'


def log(msg, lvl='INFO'):
    print(f"[{lvl}] {msg}")
    try:
        c = sqlite3.connect(LOG_DB)
        c.execute("INSERT INTO agent_log(ts,lvl,agent,msg) VALUES(?,?,?,?)",
                  (datetime.now(timezone.utc).isoformat(), lvl, AGENT, msg[:500]))
        c.commit()
        c.close()
    except Exception as e:
        print(f"Log write failed: {e}")


def ensure_snapshot_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            total_balance REAL NOT NULL,
            total_exposure REAL NOT NULL,
            total_pnl REAL NOT NULL,
            n_open INTEGER NOT NULL,
            n_settled INTEGER NOT NULL,
            snapshot_json TEXT NOT NULL
        )
    """)
    conn.commit()


def fetch_all_positions(trader):
    """GET /portfolio/positions — returns list of market_positions."""
    url = f"{trader.TRADING_URL}/portfolio/positions"
    resp = requests.get(url, headers=trader._signed_headers("GET", url), timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return data.get("market_positions", []) or data.get("positions", [])


def fetch_all_orders(trader):
    """GET /portfolio/orders — returns list of orders (paginated, fetch up to 200)."""
    url = f"{trader.TRADING_URL}/portfolio/orders"
    resp = requests.get(url, headers=trader._signed_headers("GET", url),
                        params={"limit": 200}, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return data.get("orders", [])


def build_order_lookup(orders):
    """Map ticker -> list of fill orders for backfill context."""
    lookup = {}
    for o in orders:
        ticker = o.get("ticker", "")
        lookup.setdefault(ticker, []).append(o)
    return lookup


def get_or_create_contract(db, ticker):
    """Ensure a contracts row exists for this ticker. Returns contract_id."""
    existing = db.conn.execute(
        "SELECT id FROM contracts WHERE source='kalshi' AND source_id=?", (ticker,)
    ).fetchone()
    if existing:
        return existing["id"]

    now = datetime.now(timezone.utc).isoformat()
    cursor = db.conn.execute(
        """INSERT INTO contracts (source, source_id, title, category, yes_price,
           volume_24h, created_at, updated_at)
           VALUES ('kalshi', ?, ?, '', 0.0, 0.0, ?, ?)""",
        (ticker, f"[reconciled] {ticker}", now, now)
    )
    db.conn.commit()
    return cursor.lastrowid


def reconcile():
    config = load_config()
    trader = KalshiTrader(config)
    db = Database(TRADE_DB)
    ensure_snapshot_table(db.conn)

    log("=== RECONCILER STARTING ===", 'MILESTONE')

    # 1. Fetch positions and orders from Kalshi
    try:
        positions = fetch_all_positions(trader)
        log(f"Fetched {len(positions)} positions from Kalshi")
    except Exception as e:
        log(f"Failed to fetch positions: {e}", 'ERROR')
        db.conn.close()
        return

    try:
        orders = fetch_all_orders(trader)
        order_lookup = build_order_lookup(orders)
        log(f"Fetched {len(orders)} orders from Kalshi")
    except Exception as e:
        log(f"Failed to fetch orders (continuing without): {e}", 'WARN')
        order_lookup = {}

    # 2. Get balance
    try:
        balance = trader.get_balance()
    except Exception:
        balance = 0.0

    # 3. Index local open trades by ticker
    local_trades = db.get_open_live_trades()
    local_by_ticker = {}
    for t in local_trades:
        local_by_ticker[t["kalshi_ticker"]] = t

    # 4. Build set of tickers with exposure from Kalshi
    active_tickers = set()
    backfilled = 0
    updated = 0
    total_exposure = 0.0
    total_pnl = 0.0

    for pos in positions:
        ticker = pos.get("ticker", "")
        exposure = float(pos.get("market_exposure_dollars", 0) or 0)
        realized_pnl = float(pos.get("realized_pnl_dollars", 0) or 0)
        total_traded = float(pos.get("total_traded_dollars", 0) or 0)
        fees = float(pos.get("fees_paid_dollars", 0) or 0)
        position_fp = float(pos.get("position_fp", 0) or 0)
        resting = int(pos.get("resting_orders_count", 0) or 0)

        if exposure <= 0 and abs(position_fp) < 0.01:
            continue  # no active position

        active_tickers.add(ticker)
        total_exposure += exposure
        total_pnl += realized_pnl

        if ticker in local_by_ticker:
            # Position exists locally — update exposure/pnl
            lt = local_by_ticker[ticker]
            db.conn.execute(
                "UPDATE live_trades SET cost=?, pnl=? WHERE id=?",
                (exposure, realized_pnl - fees, lt["id"])
            )
            updated += 1
        else:
            # Missing locally — backfill
            side = "yes" if position_fp > 0 else "no"
            shares = int(abs(position_fp))

            # Try to get entry price from orders
            entry_price = 0.50  # default fallback
            ticker_orders = order_lookup.get(ticker, [])
            fills = [o for o in ticker_orders if o.get("status") == "executed"]
            if fills:
                total_fill_cost = sum(float(o.get("taker_fill_cost_dollars", 0) or 0) + float(o.get("maker_fill_cost_dollars", 0) or 0) for o in fills)
                total_fill_shares = sum(float(o.get("fill_count_fp", 0) or 0) for o in fills)
                if total_fill_shares > 0:
                    entry_price = total_fill_cost / total_fill_shares

            contract_id = get_or_create_contract(db, ticker)

            trade = {
                "contract_id": contract_id,
                "kalshi_order_id": fills[0].get("order_id") if fills else None,
                "kalshi_ticker": ticker,
                "side": side,
                "entry_price": entry_price,
                "shares": shares,
                "cost": exposure,
                "max_payout": shares * 1.0,  # $1 per share max
                "model_prob": 0.50,  # unknown — reconciled position
                "edge_at_entry": 0.0,  # unknown — reconciled position
            }
            db.insert_live_trade(trade)
            backfilled += 1
            log(f"  BACKFILL: {ticker} {side} {shares}sh exposure=${exposure:.2f}")

    db.conn.commit()

    # 5. Settle local trades whose Kalshi position is gone
    settled = 0
    for ticker, lt in local_by_ticker.items():
        if ticker not in active_tickers:
            # Position closed on Kalshi — check market resolution
            market = trader.get_market_status(ticker)
            if market and market.get("status") == "settled":
                result = market.get("result", "")
                won = (result == "yes" and lt["side"] == "yes") or \
                      (result == "no" and lt["side"] == "no")
                exit_price = 1.0 if won else 0.0
                pnl = (exit_price - lt["entry_price"]) * lt["shares"] if lt["side"] == "yes" \
                    else ((1 - exit_price) - (1 - lt["entry_price"])) * lt["shares"]
                db.close_live_trade(lt["id"], won, exit_price, round(pnl, 4))
                settled += 1
                log(f"  SETTLED: {ticker} → {'WON' if won else 'LOST'} pnl=${pnl:+.4f}")
            else:
                # Position gone but market not settled — might have been sold
                db.close_live_trade(lt["id"], False, 0.0, 0.0)
                settled += 1
                log(f"  CLOSED: {ticker} (no Kalshi exposure, marking closed)")
            time.sleep(0.3)

    # 6. Snapshot
    n_open = db.conn.execute("SELECT COUNT(*) n FROM live_trades WHERE status='open'").fetchone()["n"]
    n_settled_total = db.conn.execute("SELECT COUNT(*) n FROM live_trades WHERE status!='open'").fetchone()["n"]

    snapshot = {
        "positions": [{
            "ticker": p.get("ticker"),
            "exposure": float(p.get("market_exposure_dollars", 0) or 0),
            "position": float(p.get("position_fp", 0) or 0),
            "pnl": float(p.get("realized_pnl_dollars", 0) or 0),
        } for p in positions if float(p.get("market_exposure_dollars", 0) or 0) > 0 or abs(float(p.get("position_fp", 0) or 0)) > 0.01],
        "balance": balance,
        "backfilled": backfilled,
        "updated": updated,
        "settled": settled,
    }

    now = datetime.now(timezone.utc).isoformat()
    db.conn.execute(
        """INSERT INTO portfolio_snapshots (ts, total_balance, total_exposure, total_pnl,
           n_open, n_settled, snapshot_json) VALUES (?,?,?,?,?,?,?)""",
        (now, balance, total_exposure, total_pnl, n_open, n_settled_total, json.dumps(snapshot))
    )
    db.conn.commit()

    summary = (f"balance=${balance:.2f} exposure=${total_exposure:.2f} pnl=${total_pnl:+.2f} | "
               f"open={n_open} settled={n_settled_total} | "
               f"backfilled={backfilled} updated={updated} closed={settled}")
    log(f"=== RECONCILER DONE: {summary} ===", 'MILESTONE')
    db.conn.close()
    return summary


def main():
    reconcile()


if __name__ == '__main__':
    main()
