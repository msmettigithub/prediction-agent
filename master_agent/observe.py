import sqlite3,os,subprocess,tempfile,shutil
from datetime import datetime,timezone

PAT=os.environ.get('GITHUB_PAT','')
REPO=f'https://{PAT}@github.com/msmettigithub/prediction-agent.git'

def get_repo_files():
    """Get list of actual Python files in the repo that are safe to modify."""
    d=tempfile.mkdtemp()
    try:
        subprocess.run(['git','clone','--depth=1',REPO,d],capture_output=True,timeout=60)
        files=[]
        safe_dirs=['model','scanner','tools','database']
        for sd in safe_dirs:
            dp=os.path.join(d,sd)
            if os.path.isdir(dp):
                for f in os.listdir(dp):
                    if f.endswith('.py') and not f.startswith('__'):
                        files.append(f'{sd}/{f}')
        return files
    except: return []
    finally: shutil.rmtree(d,ignore_errors=True)

def observe(db):
    o={'ts':datetime.now(timezone.utc).isoformat(),
       'calib':{'acc':0,'brier':1.0,'sep':0},
       'trades':{'open':0,'resolved':0,'pnl':0},
       'errors':[],
       'repo_files':[]}
    # Get actual repo files so orient agent doesn't hallucinate filenames
    o['repo_files']=get_repo_files()
    if not os.path.exists(db): o['errors'].append('db missing'); return o
    try:
        c=sqlite3.connect(db); c.row_factory=sqlite3.Row; cur=c.cursor()
        cur.execute("SELECT COUNT(*) n FROM paper_trades WHERE status='open'")
        o['trades']['open']=cur.fetchone()['n']
        cur.execute("SELECT COUNT(*) n FROM paper_trades WHERE status IN ('won','lost')")
        o['trades']['resolved']=cur.fetchone()['n']
        cur.execute("SELECT COALESCE(SUM(pnl),0) t FROM paper_trades WHERE status IN ('won','lost')")
        o['trades']['pnl']=round(cur.fetchone()['t'],2)
        c.close()
    except Exception as e: o['errors'].append(str(e))
    return o