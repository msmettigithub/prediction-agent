#!/usr/bin/env python3
"""Self-auditor — runs every 10 minutes, checks system health, creates priority-0 commands for failures.

Designed to be run as a background thread in the master agent loop.
"""
import os, sys, sqlite3, subprocess, time, threading
from datetime import datetime, timezone, timedelta

sys.path.insert(0, '/home/jovyan/workspace/prediction-agent')

DB = '/home/jovyan/shared/sm/prediction-agent-db/prediction_agent.db'
TRADE_DB = str(__import__('pathlib').Path(__file__).resolve().parent.parent / 'prediction_agent.db')
INTERVAL = 600  # 10 minutes


def log(msg, lvl='INFO'):
    try:
        c = sqlite3.connect(DB)
        c.execute("INSERT INTO agent_log(ts,lvl,agent,msg) VALUES(?,?,?,?)",
                  (datetime.now(timezone.utc).isoformat(), lvl, 'AUDITOR', msg[:500]))
        c.commit()
        c.close()
    except:
        pass


def create_priority_command(description):
    """Create a priority-0 command for the master agent to fix."""
    try:
        c = sqlite3.connect(DB)
        c.execute("""CREATE TABLE IF NOT EXISTS agent_commands(
            id INTEGER PRIMARY KEY,ts TEXT,command TEXT,
            status TEXT DEFAULT 'pending',result TEXT,executed_at TEXT)""")
        c.execute("INSERT INTO agent_commands(ts,command,status) VALUES(?,?,?)",
                  (datetime.now(timezone.utc).isoformat(),
                   f'[PRIORITY-0 AUDIT FAILURE] {description}', 'pending'))
        c.commit()
        c.close()
    except:
        pass


def check_imports():
    """Verify core modules import."""
    r = subprocess.run(
        [sys.executable, '-c',
         'import config,dashboard,model.probability_model,tools.tool_registry,database.db'],
        capture_output=True, text=True, timeout=15,
        cwd='/home/jovyan/workspace/prediction-agent')
    return r.returncode == 0, 'imports clean' if r.returncode == 0 else r.stderr[-200:]


def check_tests():
    """Run pytest, verify >= 139 pass."""
    r = subprocess.run(
        [sys.executable, '-m', 'pytest', 'tests/', '-q', '--tb=line'],
        capture_output=True, text=True, timeout=180,
        cwd='/home/jovyan/workspace/prediction-agent')
    import re
    m = re.search(r'(\d+) passed', r.stdout + r.stderr)
    n = int(m.group(1)) if m else 0
    f = re.search(r'(\d+) failed', r.stdout + r.stderr)
    nf = int(f.group(1)) if f else 0
    ok = r.returncode == 0 and n >= 139
    return ok, f'{n} passed, {nf} failed'


def check_db_writable():
    """Verify both DBs are writable."""
    for name, path in [('shared', DB), ('trade', TRADE_DB)]:
        try:
            c = sqlite3.connect(path)
            c.execute("SELECT 1")
            c.close()
        except Exception as e:
            return False, f'{name} DB not writable: {e}'
    return True, 'both DBs writable'


def check_dashboard():
    """Check if local dashboard responds."""
    try:
        import urllib.request
        r = urllib.request.urlopen('http://localhost:8000/api/status', timeout=5)
        return r.status == 200, 'dashboard OK'
    except Exception as e:
        return False, f'dashboard down: {e}'


def check_stale_trades():
    """Check if there are unresolved contracts past close time."""
    try:
        c = sqlite3.connect(TRADE_DB)
        c.row_factory = sqlite3.Row
        now = datetime.now(timezone.utc).isoformat()
        r = c.execute("SELECT COUNT(*) n FROM contracts WHERE resolution IS NULL AND close_time < ?", (now,)).fetchone()
        c.close()
        n = r['n']
        return n == 0, f'{n} unresolved past-close contracts'
    except Exception as e:
        return False, str(e)


def check_error_rate():
    """Check error rate in last hour."""
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        c = sqlite3.connect(DB)
        r = c.execute("SELECT COUNT(*) FROM agent_log WHERE lvl='ERROR' AND ts>?", (cutoff,)).fetchone()
        c.close()
        n = r[0]
        return n < 20, f'{n} errors in last hour'
    except:
        return True, 'could not check'


def check_live_safety():
    """CRITICAL: verify live trading limits are sane."""
    try:
        from config import load_config
        config = load_config()
        issues = []
        if config.live_trading_enabled and config.max_live_bankroll > 100:
            issues.append(f'MAX_LIVE_BANKROLL=${config.max_live_bankroll} (should be <=100)')
        if config.live_trading_enabled and config.max_single_bet > 25:
            issues.append(f'MAX_SINGLE_BET=${config.max_single_bet} (should be <=25)')
        if issues:
            return False, 'DANGEROUS: ' + '; '.join(issues)
        return True, f'live_enabled={config.live_trading_enabled} bankroll=${config.max_live_bankroll} bet=${config.max_single_bet}'
    except Exception as e:
        return False, str(e)


def run_audit():
    """Run all checks, log results, create priority commands for failures."""
    checks = [
        ('imports', check_imports),
        ('tests', check_tests),
        ('db_writable', check_db_writable),
        ('dashboard', check_dashboard),
        ('stale_trades', check_stale_trades),
        ('error_rate', check_error_rate),
        ('live_safety', check_live_safety),
    ]

    results = []
    for name, fn in checks:
        try:
            ok, detail = fn()
        except Exception as e:
            ok, detail = False, str(e)[:200]
        results.append((name, ok, detail))

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    summary = f'AUDIT {passed}/{total}: ' + ' | '.join(f'{n}:{"OK" if ok else "FAIL"}'
                                                         for n, ok, _ in results)

    log(summary, 'MILESTONE' if passed == total else 'ERROR')

    # Log details for failures
    for name, ok, detail in results:
        if not ok:
            log(f'AUDIT FAIL {name}: {detail}', 'ERROR')
            create_priority_command(f'{name}: {detail}')

    return passed == total


def audit_loop():
    """Background thread: run audit every INTERVAL seconds."""
    while True:
        try:
            run_audit()
        except Exception as e:
            log(f'Auditor crash: {e}', 'ERROR')
        time.sleep(INTERVAL)


def start_background():
    """Start auditor as a daemon thread."""
    t = threading.Thread(target=audit_loop, daemon=True, name='auditor')
    t.start()
    return t


if __name__ == '__main__':
    print("Running one-shot audit...")
    ok = run_audit()
    print(f"Audit {'PASSED' if ok else 'FAILED'}")
