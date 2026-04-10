#!/usr/bin/env python3
"""Market Scanner Worker — scans Kalshi for new opportunities with real data modifiers.

Runs as a Saturn Cloud job on cron (every 30 min).
Fetches open markets, runs data_modifiers pipeline, stores candidates
with real edges in a candidates table for the master agent to review.
"""
import os, sys, sqlite3, json, time
from datetime import datetime, timezone

sys.path.insert(0, '/home/jovyan/workspace/prediction-agent')

from tools.kalshi import KalshiTool
from model.probability_model import estimate_probability
from model.edge_calculator import compute_edge
from model.data_modifiers import get_modifiers_for_contract
from database.models import Contract
from config import load_config

DB_PATH = os.environ.get('TRADE_DB', str(__import__('pathlib').Path(__file__).resolve().parent.parent / 'prediction_agent.db'))
LOG_DB = '/home/jovyan/shared/sm/prediction-agent-db/prediction_agent.db'


def log(msg, lvl='INFO'):
    print(f"[{lvl}] {msg}")
    try:
        c = sqlite3.connect(LOG_DB)
        c.execute("INSERT INTO agent_log(ts,lvl,agent,msg) VALUES(?,?,?,?)",
                  (datetime.now(timezone.utc).isoformat(), lvl, 'SCANNER', msg[:500]))
        c.commit()
        c.close()
    except:
        pass


def ensure_table(db_path):
    c = sqlite3.connect(db_path)
    c.execute("""CREATE TABLE IF NOT EXISTS scan_candidates(
        id INTEGER PRIMARY KEY, ts TEXT, source_id TEXT, title TEXT,
        category TEXT, market_price REAL, model_prob REAL, edge REAL,
        confidence TEXT, recommendation TEXT, modifiers TEXT,
        kelly_fraction REAL, bet_amount REAL,
        UNIQUE(source_id, ts))""")
    c.commit()
    c.close()


def scan_series(kalshi, series, config):
    """Scan a single series for opportunities."""
    candidates = []

    try:
        result = kalshi.run(status="open", series_ticker=series, limit=100)
        if not result.get('success'):
            return candidates

        markets = result.get('data', {}).get('markets', [])
        if not markets:
            return candidates

        for m in markets:
            mp = m.get('yes_price', 0)
            if mp < config.prob_floor or mp > config.prob_ceiling:
                continue

            source_id = m.get('ticker', '')
            title = m.get('title', '')
            category = m.get('category', 'economics')
            close_time_str = m.get('close_time', '')

            # Parse close_time
            close_time = None
            if close_time_str:
                try:
                    close_time = datetime.fromisoformat(close_time_str.replace('Z', '+00:00'))
                except:
                    pass

            # Get real data modifiers
            mods = get_modifiers_for_contract(
                source_id=source_id,
                category=category,
                market_price=mp,
                title=title,
                close_time=close_time,
            )

            if not mods:
                continue  # No data = no edge = skip

            # Build contract object
            contract = Contract(
                id=0, title=title, source='kalshi', source_id=source_id,
                category=category, yes_price=mp,
                close_time=close_time,
                open_time=datetime.now(timezone.utc),
                volume_24h=m.get('volume_24h', 0),
            )

            # Estimate probability with real modifiers
            estimate = estimate_probability(contract, modifiers=mods, config=config)
            edge_result = compute_edge(estimate, mp, config)

            if edge_result.recommendation in ('BET_YES', 'BET_NO'):
                mod_info = [{'name': mod.name, 'dir': mod.direction, 'w': mod.weight,
                             'src': mod.source} for mod in mods]
                candidates.append({
                    'source_id': source_id,
                    'title': title,
                    'category': category,
                    'market_price': mp,
                    'model_prob': estimate.probability,
                    'edge': edge_result.edge,
                    'confidence': estimate.confidence,
                    'recommendation': edge_result.recommendation,
                    'modifiers': json.dumps(mod_info),
                    'kelly_fraction': edge_result.kelly_fraction,
                    'bet_amount': edge_result.bet_amount,
                })

    except Exception as e:
        log(f"Error scanning {series}: {e}", 'ERROR')

    return candidates


def main():
    log("=== SCANNER STARTING ===", 'MILESTONE')
    ensure_table(DB_PATH)
    config = load_config()
    kalshi = KalshiTool(mock_mode=False)

    # Scan key series
    series_list = ['KXINX', 'KXCPI', 'KXGDP', 'KXBTCD', 'KXETH', 'KXWTI',
                   'KXFEDRATE', 'KXUNRATE']

    all_candidates = []
    for series in series_list:
        cands = scan_series(kalshi, series, config)
        if cands:
            log(f"  {series}: {len(cands)} candidates with real edge")
        all_candidates.extend(cands)
        time.sleep(0.5)  # rate limit

    # Store candidates
    now = datetime.now(timezone.utc).isoformat()
    c = sqlite3.connect(DB_PATH)
    for cand in all_candidates:
        try:
            c.execute("""INSERT OR REPLACE INTO scan_candidates(
                ts, source_id, title, category, market_price, model_prob,
                edge, confidence, recommendation, modifiers, kelly_fraction, bet_amount
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (
                now, cand['source_id'], cand['title'], cand['category'],
                cand['market_price'], cand['model_prob'], cand['edge'],
                cand['confidence'], cand['recommendation'], cand['modifiers'],
                cand['kelly_fraction'], cand['bet_amount'],
            ))
        except Exception as e:
            log(f"Store error {cand['source_id']}: {e}", 'ERROR')
    c.commit()
    c.close()

    log(f"=== SCANNER DONE: {len(all_candidates)} data-backed candidates from {len(series_list)} series ===", 'MILESTONE')


if __name__ == '__main__':
    main()
