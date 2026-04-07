"""
Karpathy Kapital Wiki — Persistent memory system.
Both Claude (in chat) and master agent read/write this.
Solves context compaction by keeping truth in GitHub, not just in-context.
"""
import os,subprocess,shutil,tempfile,json,re
from datetime import datetime,timezone

PAT=os.environ.get('GITHUB_PAT','')
REPO=f'https://{PAT}@github.com/msmettigithub/prediction-agent.git'
WIKI_PATH='WIKI.md'

def read_wiki():
    """Read current WIKI.md content."""
    d=tempfile.mkdtemp()
    try:
        subprocess.run(['git','clone','--depth=1',REPO,d],capture_output=True,timeout=60)
        fp=os.path.join(d,WIKI_PATH)
        return open(fp).read() if os.path.exists(fp) else ''
    finally: shutil.rmtree(d,ignore_errors=True)

def append_section(section_header,content,cid=None):
    """Append content under a section in WIKI.md. Creates section if missing."""
    d=tempfile.mkdtemp()
    try:
        subprocess.run(['git','clone','--depth=1',REPO,d],capture_output=True,timeout=120)
        subprocess.run(['git','config','user.email','kk-wiki@saturncloud.io'],cwd=d)
        subprocess.run(['git','config','user.name','KK Wiki'],cwd=d)
        fp=os.path.join(d,WIKI_PATH)
        text=open(fp).read() if os.path.exists(fp) else ''
        ts=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')
        entry=f'\n- {ts}: {content}'
        if section_header in text:
            # Insert after the section header
            text=text.replace(section_header,section_header+entry,1)
        else:
            text+=f'\n\n{section_header}{entry}\n'
        open(fp,'w').write(text)
        subprocess.run(['git','add','-A'],cwd=d)
        subprocess.run(['git','commit','-m',f'wiki[{cid or "auto"}]:{section_header[:30]}'],cwd=d)
        r=subprocess.run(['git','push'],cwd=d,capture_output=True,text=True,timeout=60)
        return r.returncode==0
    finally: shutil.rmtree(d,ignore_errors=True)

def log_decision(decision,rationale,cid=None):
    return append_section('## Decision Log',f'{decision} | rationale: {rationale}',cid)

def log_calibration(metrics,cid=None):
    m=json.dumps(metrics)
    return append_section('## Calibration History',f'metrics: {m}',cid)

def log_tool_experiment(tool,useful,notes,cid=None):
    verdict='USEFUL' if useful else 'SKIP'
    return append_section('## Tool Experiments',f'{tool}: {verdict} — {notes[:100]}',cid)

def update_state(state_dict,cid=None):
    """Update the Current State section with latest metrics."""
    d=tempfile.mkdtemp()
    try:
        subprocess.run(['git','clone','--depth=1',REPO,d],capture_output=True,timeout=120)
        subprocess.run(['git','config','user.email','kk-wiki@saturncloud.io'],cwd=d)
        subprocess.run(['git','config','user.name','KK Wiki'],cwd=d)
        fp=os.path.join(d,WIKI_PATH)
        text=open(fp).read() if os.path.exists(fp) else ''
        ts=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
        new_state=f'\n## Current Live State (auto-updated)\n'
        new_state+=f'Last update: {ts}\n'
        for k,v in state_dict.items():
            new_state+=f'- {k}: {v}\n'
        # Replace existing Current Live State section or append
        if '## Current Live State' in text:
            text=re.sub(r'## Current Live State.*?(?=\n##|$)',new_state.strip(),text,flags=re.DOTALL)
        else:
            text+=new_state
        open(fp,'w').write(text)
        subprocess.run(['git','add','-A'],cwd=d)
        subprocess.run(['git','commit','-m',f'wiki-state[{cid or "auto"}]:update'],cwd=d)
        r=subprocess.run(['git','push'],cwd=d,capture_output=True,text=True,timeout=60)
        return r.returncode==0
    finally: shutil.rmtree(d,ignore_errors=True)