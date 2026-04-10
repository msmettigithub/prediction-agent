#!/usr/bin/env python3
"""Run all workers locally in a loop. Use this when Saturn jobs can't be provisioned.

Runs resolver every 15 min, calibrator every 30 min, scanner every 30 min.
"""
import time, subprocess, sys, os
from datetime import datetime, timezone

WORKERS = [
    {'name': 'reconciler', 'cmd': [sys.executable, 'workers/reconciler.py'], 'interval': 900},
    {'name': 'resolver', 'cmd': [sys.executable, 'workers/resolver.py'], 'interval': 900},
    {'name': 'calibrator', 'cmd': [sys.executable, 'workers/calibrator.py'], 'interval': 1800},
    {'name': 'scanner', 'cmd': [sys.executable, 'workers/scanner_worker.py'], 'interval': 1800},
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
                print(f"\n[{ts}] Running {w['name']}...")
                try:
                    r = subprocess.run(w['cmd'], cwd=cwd, timeout=300,
                                       capture_output=True, text=True)
                    print(r.stdout[-500:] if r.stdout else '')
                    if r.returncode != 0:
                        print(f"  ERROR: {r.stderr[-300:]}")
                except subprocess.TimeoutExpired:
                    print(f"  TIMEOUT: {w['name']}")
                except Exception as e:
                    print(f"  FAILED: {e}")
                last_run[w['name']] = now
        time.sleep(30)

if __name__ == '__main__':
    main()
