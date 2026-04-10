#!/usr/bin/env python3
"""One-shot diagnostic: check if OpenRouter is working and dump advisor logs."""
import os, sys, sqlite3, json, subprocess
from datetime import datetime, timezone

DB = '/home/jovyan/shared/sm/prediction-agent-db/prediction_agent.db'
REPO_DIR = '/home/jovyan/workspace/prediction-agent'
REPORT = os.path.join(REPO_DIR, 'OPENROUTER_STATUS.md')

lines = [f"# OpenRouter Status Check\n", f"**Run at:** {datetime.now(timezone.utc).isoformat()}\n"]

# Check env
key = os.environ.get('OPENROUTER', '')
lines.append(f"**OPENROUTER env var:** {'SET (len={})'.format(len(key)) if key else 'NOT SET'}\n")

# Check DB for advisor logs
if os.path.exists(DB):
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    rows = c.execute("SELECT ts, agent, substr(msg,1,120) as preview FROM agent_log WHERE agent LIKE 'advisor%' OR msg LIKE '%OPENROUTER%' ORDER BY ts DESC LIMIT 15").fetchall()
    lines.append(f"\n## Agent Log (advisor entries): {len(rows)} found\n")
    for r in rows:
        lines.append(f"- `{r['ts']}` **{r['agent']}**: {r['preview']}\n")
    if not rows:
        lines.append("- No advisor entries found in agent_log\n")
    c.close()
else:
    lines.append(f"\n**DB not found at {DB}**\n")

# Write report
with open(REPORT, 'w') as f:
    f.writelines(lines)

# Git push
os.chdir(REPO_DIR)
subprocess.run(['git', 'add', 'OPENROUTER_STATUS.md'])
subprocess.run(['git', 'commit', '-m', 'diagnostic: OpenRouter status check'])
subprocess.run(['git', 'push'], capture_output=True, timeout=30)
print("Report written and pushed")

# Also callable as module
if __name__ == '__main__':
    main()

def main():
    import os, sqlite3
    DB = '/home/jovyan/shared/sm/prediction-agent-db/prediction_agent.db'
    key = os.environ.get('OPENROUTER', '')
    print(f"OPENROUTER key: {'SET (len={len(key)})' if key else 'NOT SET'}")
    if os.path.exists(DB):
        c = sqlite3.connect(DB)
        c.row_factory = sqlite3.Row
        for r in c.execute("SELECT ts, agent, substr(msg,1,120) FROM agent_log WHERE agent LIKE 'advisor%' OR msg LIKE '%OPENROUTER%' ORDER BY ts DESC LIMIT 10").fetchall():
            print(f"  [{r[0]}] {r[1]}: {r[2]}")
    else:
        print(f"DB not found at {DB}")
