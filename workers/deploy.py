#!/usr/bin/env python3
"""Deploy workers to Saturn Cloud.

Usage: python workers/deploy.py [--dry-run]

Provisions:
  1. kk-resolver    — cron job, every 15 min, resolves expired contracts
  2. kk-calibrator  — cron job, every 30 min (offset), computes model accuracy
  3. kk-scanner     — cron job, every 30 min, scans for data-backed opportunities
  4. kk-dashboard   — deployment (always-on), serves the dashboard
"""
import os, sys, json

sys.path.insert(0, '/home/jovyan/workspace/prediction-agent')

# Reuse the Saturn API helpers from the master agent loop
from master_agent.loop import (
    saturn_api, attach_all, provision_job, provision_deployment,
    IMG, SECRETS, SHARED, GIT, DB, log
)


WORKERS = [
    {
        'type': 'job',
        'name': 'kk-resolver',
        'command': 'cd /home/jovyan/workspace/prediction-agent && python workers/resolver.py',
        'schedule': '*/15 * * * *',
        'description': 'Resolve expired contracts from Kalshi every 15 min',
    },
    {
        'type': 'job',
        'name': 'kk-calibrator',
        'command': 'cd /home/jovyan/workspace/prediction-agent && python workers/calibrator.py',
        'schedule': '7,37 * * * *',  # offset from resolver
        'description': 'Compute model calibration every 30 min',
    },
    {
        'type': 'job',
        'name': 'kk-scanner',
        'command': 'cd /home/jovyan/workspace/prediction-agent && python workers/scanner_worker.py',
        'schedule': '*/30 * * * *',
        'description': 'Scan Kalshi for data-backed opportunities every 30 min',
    },
    {
        'type': 'deployment',
        'name': 'kk-dashboard',
        'command': 'cd /home/jovyan/workspace/prediction-agent && pip install fastapi uvicorn anthropic && python dashboard.py',
        'description': 'Always-on dashboard',
    },
]


def list_existing():
    """List existing Saturn jobs and deployments."""
    jobs = saturn_api('get', '/api/jobs') or []
    deps = saturn_api('get', '/api/deployments') or []
    if isinstance(jobs, dict):
        jobs = jobs.get('jobs', [])
    if isinstance(deps, dict):
        deps = deps.get('deployments', [])
    existing = {}
    for j in jobs:
        existing[j.get('name', '')] = {'id': j['id'], 'type': 'job', 'status': j.get('status', '')}
    for d in deps:
        existing[d.get('name', '')] = {'id': d['id'], 'type': 'deployment', 'status': d.get('status', '')}
    return existing


def deploy(dry_run=False):
    existing = list_existing()
    print(f"\n=== SATURN CLOUD WORKER DEPLOYMENT ===")
    print(f"Existing resources: {len(existing)}")
    for name, info in existing.items():
        print(f"  {info['type']:12} {name:20} {info['status']:12} [{info['id'][:8]}]")
    print()

    for w in WORKERS:
        name = w['name']
        if name in existing:
            print(f"  SKIP  {name} — already exists [{existing[name]['id'][:8]}]")
            continue

        if dry_run:
            print(f"  WOULD {w['type']:12} {name:20} schedule={w.get('schedule', 'always-on')}")
            continue

        print(f"  DEPLOY {w['type']:12} {name:20} ...", end=' ', flush=True)
        if w['type'] == 'job':
            result = provision_job(name, w['command'], w.get('schedule'))
            print(f"{'OK' if result else 'FAILED'} [{result}]")
        else:
            result = provision_deployment(name, w['command'])
            print(f"{'OK' if result else 'FAILED'} [{result}]")

    print(f"\n=== DEPLOYMENT COMPLETE ===\n")


if __name__ == '__main__':
    dry_run = '--dry-run' in sys.argv
    if dry_run:
        print("DRY RUN — no resources will be created\n")
    deploy(dry_run=dry_run)
