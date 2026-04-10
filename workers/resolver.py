#!/usr/bin/env python3
"""Contract Resolution Worker — fetches outcomes from Kalshi for expired contracts.

Runs as a Saturn Cloud job on a cron schedule (every 15 min).
Resolves paper_trades and wti_paper_trades based on Kalshi settlement data.
Writes to shared prediction_agent.db and logs to shared agent_log.
"""
import os, sys, sqlite3, time
from datetime import datetime, timezone

sys.path.insert(0, '/home/jovyan/workspace/prediction-agent')

from tools.kalshi import KalshiTool

DB_PATH = os.environ.get('TRADE_DB', str(__import__('pathlib').Path(__file__).resolve().parent.parent / 'prediction_agent.db'))
LOG_DB = '/home/jovyan/shared/sm/prediction-agent-db/prediction_agent.db'


def log(msg, lvl='INFO'):
    print(f"[{lvl}] {msg}")
    try:
        c = sqlite3.connect(LOG_DB)
        c.execute("INSERT INTO agent_log(ts,lvl,agent,msg) VALUES(?,?,?,?)",
                  (datetime.now(timezone.utc).isoformat(), lvl, 'RESOLVER', msg[:500]))
        c.commit()
        c.close()
    except Exception as e:
        print(f"Log write failed: {e}")


def get_unresolved_contracts(db_path):
    """Get contracts that are past close_time but have no resolution."""
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    now = datetime.now(timezone.utc).isoformat()
    rows = c.execute("""
        SELECT id, source_id, title, close_time FROM contracts
        WHERE resolution IS NULL AND close_time < ?
        ORDER BY close_time
    """, (now,)).fetchall()
    c.close()
    return [dict(r) for r in rows]


def resolve_contracts(db_path):
    """Fetch resolutions from Kalshi and update local DB."""
    unresolved = get_unresolved_contracts(db_path)
    if not unresolved:
        log("No unresolved contracts")
        return 0

    log(f"Found {len(unresolved)} unresolved contracts")

    kalshi = KalshiTool(mock_mode=False)
    resolved_count = 0

    for contract in unresolved:
        source_id = contract['source_id']
        try:
            market = kalshi.fetch_single_market(source_id)
            if not market:
                log(f"  {source_id}: not found on Kalshi", 'WARN')
                continue

            if not market.get('resolved'):
                # Not yet settled on Kalshi
                continue

            resolution = 1 if market.get('resolution') else 0

            # Update contract
            c = sqlite3.connect(db_path)
            c.execute("UPDATE contracts SET resolution = ? WHERE id = ?",
                      (resolution, contract['id']))

            # Update paper_trades
            trades = c.execute("""
                SELECT id, side, entry_price, bet_amount FROM paper_trades
                WHERE contract_id = ? AND status = 'open'
            """, (contract['id'],)).fetchall()

            for t in trades:
                tid, side, entry, bet = t
                if side == 'YES':
                    pnl = (resolution - entry) * bet / entry if entry > 0 else 0
                    status = 'won' if resolution == 1 else 'lost'
                else:
                    pnl = ((1 - resolution) - (1 - entry)) * bet / (1 - entry) if entry < 1 else 0
                    status = 'won' if resolution == 0 else 'lost'

                c.execute("""
                    UPDATE paper_trades SET status = ?, pnl = ?, closed_at = ?
                    WHERE id = ?
                """, (status, round(pnl, 4), datetime.now(timezone.utc).isoformat(), tid))

                log(f"  {source_id}: {side} → {status} pnl=${pnl:+.4f}")

            c.commit()
            c.close()
            resolved_count += 1
            log(f"  {source_id}: resolved={'YES' if resolution else 'NO'}")

            time.sleep(0.3)  # rate limit

        except Exception as e:
            log(f"  {source_id}: error {e}", 'ERROR')

    return resolved_count


def resolve_wti_trades(db_path):
    """Resolve WTI paper trades similarly."""
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    rows = c.execute("""
        SELECT id, ticker, side, entry_price, shares, cost FROM wti_paper_trades
        WHERE status = 'open'
    """).fetchall()
    c.close()

    if not rows:
        return 0

    kalshi = KalshiTool(mock_mode=False)
    resolved = 0

    for row in rows:
        row = dict(row)
        try:
            market = kalshi.fetch_single_market(row['ticker'])
            if not market or not market.get('resolved'):
                continue

            resolution = 1 if market.get('resolution') else 0
            entry = row['entry_price']
            shares = row['shares']
            side = row['side']

            if side == 'yes':
                pnl = (resolution - entry) * shares
                status = 'won' if resolution == 1 else 'lost'
            else:
                pnl = ((1 - resolution) - (1 - entry)) * shares
                status = 'won' if resolution == 0 else 'lost'

            c = sqlite3.connect(db_path)
            c.execute("""
                UPDATE wti_paper_trades SET status = ?, pnl = ?, exit_price = ?,
                closed_at = ? WHERE id = ?
            """, (status, round(pnl, 4), float(resolution),
                  datetime.now(timezone.utc).isoformat(), row['id']))
            c.commit()
            c.close()

            log(f"  WTI {row['ticker']}: {side} → {status} pnl=${pnl:+.4f}")
            resolved += 1
            time.sleep(0.3)

        except Exception as e:
            log(f"  WTI {row['ticker']}: error {e}", 'ERROR')

    return resolved


def main():
    log("=== RESOLVER STARTING ===", 'MILESTONE')
    n1 = resolve_contracts(DB_PATH)
    try: n2 = resolve_wti_trades(DB_PATH)
    except Exception: n2 = 0  # wti_paper_trades table may not exist
    log(f"=== RESOLVER DONE: {n1} contracts, {n2} WTI resolved ===", 'MILESTONE')


if __name__ == '__main__':
    main()
