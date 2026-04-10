#!/usr/bin/env python3
"""Calibration Worker — computes live model accuracy from resolved trades.

Runs after resolver. Updates model_accuracy table and logs calibration
reports. Detects model drift and flags regressions.
"""
import os, sys, sqlite3, json
from datetime import datetime, timezone

sys.path.insert(0, '/home/jovyan/workspace/prediction-agent')

from model.calibration import compute_calibration, paper_trade_calibration
from database.models import Resolution

DB_PATH = os.environ.get('TRADE_DB', str(__import__('pathlib').Path(__file__).resolve().parent.parent / 'prediction_agent.db'))
LOG_DB = '/home/jovyan/shared/sm/prediction-agent-db/prediction_agent.db'


def log(msg, lvl='INFO'):
    print(f"[{lvl}] {msg}")
    try:
        c = sqlite3.connect(LOG_DB)
        c.execute("INSERT INTO agent_log(ts,lvl,agent,msg) VALUES(?,?,?,?)",
                  (datetime.now(timezone.utc).isoformat(), lvl, 'CALIBRATOR', msg[:500]))
        c.commit()
        c.close()
    except:
        pass


def ensure_table(db_path):
    c = sqlite3.connect(db_path)
    c.execute("""CREATE TABLE IF NOT EXISTS model_accuracy(
        id INTEGER PRIMARY KEY, ts TEXT, n_resolved INTEGER,
        accuracy REAL, brier REAL, separation REAL, win_rate REAL,
        pnl REAL, roi REAL, details TEXT)""")
    c.commit()
    c.close()


def build_resolutions(db_path):
    """Build Resolution objects from resolved contracts + paper trades."""
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row

    rows = c.execute("""
        SELECT c.source_id, c.resolution, c.yes_price as market_prob,
               pt.model_prob, pt.side, pt.status, pt.pnl, pt.entry_price
        FROM paper_trades pt
        JOIN contracts c ON pt.contract_id = c.id
        WHERE pt.status IN ('won', 'lost') AND c.resolution IS NOT NULL
    """).fetchall()
    c.close()

    resolutions = []
    for r in rows:
        resolved_yes = bool(r['resolution'])
        model_prob = r['model_prob']
        correct = (model_prob > 0.5 and resolved_yes) or (model_prob <= 0.5 and not resolved_yes)
        brier = (model_prob - (1.0 if resolved_yes else 0.0)) ** 2

        resolutions.append(Resolution(
            contract_id=r['source_id'],
            resolved_yes=resolved_yes,
            model_prob=model_prob,
            market_prob=r['market_prob'],
            correct_direction=correct,
            brier_component=brier,
        ))

    return resolutions


def get_trade_dicts(db_path):
    """Get paper trades as dicts for paper_trade_calibration."""
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    rows = c.execute("SELECT * FROM paper_trades").fetchall()
    c.close()
    return [dict(r) for r in rows]


def main():
    log("=== CALIBRATOR STARTING ===", 'MILESTONE')
    ensure_table(DB_PATH)

    resolutions = build_resolutions(DB_PATH)
    log(f"Found {len(resolutions)} resolved trades for calibration")

    if len(resolutions) < 5:
        log("Insufficient resolved trades for calibration (<5)", 'WARN')
        return

    # Compute calibration
    report = compute_calibration(
        resolutions,
        min_resolved=10,  # lower threshold for early stage
        accuracy_threshold=0.55,  # realistic target
        brier_threshold=0.25,
        separation_threshold=0.05,  # lower early bar
    )

    log(f"Calibration: n={report.n_resolved} acc={report.directional_accuracy:.3f} "
        f"brier={report.brier_score:.4f} sep={report.separation:.4f}")
    log(f"Pass: acc={report.accuracy_pass} brier={report.brier_pass} "
        f"sep={report.separation_pass} overall={report.overall_pass}")

    # Compute paper trade P&L calibration
    trades = get_trade_dicts(DB_PATH)
    resolved = [t for t in trades if t['status'] in ('won', 'lost')]
    won = sum(1 for t in resolved if t['status'] == 'won')
    total_pnl = sum(t.get('pnl', 0) or 0 for t in resolved)
    total_wagered = sum(t.get('bet_amount', 0) or 0 for t in trades)
    win_rate = won / len(resolved) if resolved else 0
    roi = total_pnl / total_wagered if total_wagered else 0

    log(f"P&L: ${total_pnl:+.2f} win_rate={win_rate:.0%} roi={roi:+.1%}", 'MILESTONE')

    # Drift detection
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    prev = c.execute("SELECT brier, accuracy FROM model_accuracy ORDER BY ts DESC LIMIT 1").fetchone()
    c.close()

    if prev:
        brier_delta = report.brier_score - prev['brier']
        acc_delta = report.directional_accuracy - prev['accuracy']
        if brier_delta > 0.05:
            log(f"DRIFT ALERT: Brier worsened by {brier_delta:+.4f}", 'ERROR')
        if acc_delta < -0.05:
            log(f"DRIFT ALERT: Accuracy dropped by {acc_delta:+.3f}", 'ERROR')

    # Store
    details = json.dumps({
        'buckets': [{'low': b.bucket_low, 'high': b.bucket_high,
                     'predicted': b.mean_predicted, 'actual': b.mean_actual,
                     'count': b.count} for b in report.buckets],
        'pass': {'acc': report.accuracy_pass, 'brier': report.brier_pass,
                 'sep': report.separation_pass, 'overall': report.overall_pass},
    })

    c = sqlite3.connect(DB_PATH)
    c.execute("""INSERT INTO model_accuracy(ts, n_resolved, accuracy, brier,
        separation, win_rate, pnl, roi, details) VALUES(?,?,?,?,?,?,?,?,?)""",
              (datetime.now(timezone.utc).isoformat(), report.n_resolved,
               report.directional_accuracy, report.brier_score,
               report.separation, win_rate, total_pnl, roi, details))
    c.commit()
    c.close()

    log("=== CALIBRATOR DONE ===", 'MILESTONE')


if __name__ == '__main__':
    main()
