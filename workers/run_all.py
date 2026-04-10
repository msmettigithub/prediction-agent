#!/usr/bin/env python3
"""Run all workers locally in a tight loop.

Intervals are minimized:
- reconciler: 60s (sync positions with Kalshi)
- fill_tracker: 30s (catch fills, cancel stale orders)
- resolver: 120s (resolve expired contracts)
- calibrator: 300s (compute model accuracy — heavier, less frequent)
- scanner: 120s (scan for new opportunities)

The live_monitor runs separately as its own process (3s cycle).
"""
import time, subprocess, sys, os
from datetime import datetime, timezone

WORKERS = [
    # Trading brain runs separately as its own process (2s cycles, observe/decide/act)
    # Start it with: python -u workers/trading_brain.py
    # These are support workers:
    {'name': 'fill_tracker', 'cmd': [sys.executable, 'workers/fill_tracker.py'], 'interval': 30},
    {'name': 'reconciler', 'cmd': [sys.executable, 'workers/reconciler.py'], 'interval': 60},
    {'name': 'resolver', 'cmd': [sys.executable, 'workers/resolver.py'], 'interval': 120},
    {'name': 'calibrator', 'cmd': [sys.executable, 'workers/calibrator.py'], 'interval': 300},
]

def main():
    cwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    last_run = {w['name']: 0 for w in WORKERS}
    print(f"[run_all] Starting worker loop from {cwd}")

    while True:
        now = time.time()
        for w in WORKERS:
            if now - last_run[w['name']] >= w['interval']:
                ts = datetime.now(timezone.utc).strftime('%H:%M:%S')
                try:
                    r = subprocess.run(w['cmd'], cwd=cwd, timeout=120,
                                       capture_output=True, text=True)
                    # Print last line only for brevity
                    lines = [l for l in (r.stdout or '').strip().split('\n') if l.strip()]
                    summary = lines[-1] if lines else ''
                    print(f"[{ts}] {w['name']:15} {summary[-120:]}")
                    if r.returncode != 0:
                        err_lines = [l for l in (r.stderr or '').strip().split('\n') if l.strip()]
                        print(f"  ERR: {err_lines[-1][:120] if err_lines else 'unknown'}")
                except subprocess.TimeoutExpired:
                    print(f"[{ts}] {w['name']:15} TIMEOUT")
                except Exception as e:
                    print(f"[{ts}] {w['name']:15} FAIL: {e}")
                last_run[w['name']] = now
        time.sleep(5)  # check every 5s

if __name__ == '__main__':
    main()
