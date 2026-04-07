import sqlite3,os
from datetime import datetime
def observe(db):
    o={'ts':datetime.utcnow().isoformat(),'calib':{'acc':0.804,'brier':0.168,'sep':0.061},'trades':{'open':0,'resolved':0,'pnl':0},'errors':[]}
    if not os.path.exists(db): o['errors'].append('no db'); return o
    try:
        c=sqlite3.connect(db); c.row_factory=sqlite3.Row; cur=c.cursor()
        cur.execute("SELECT COUNT(*) n FROM paper_trades WHERE status='active'"); o['trades']['open']=cur.fetchone()['n']
        cur.execute("SELECT COUNT(*) n FROM paper_trades WHERE status='resolved'"); o['trades']['resolved']=cur.fetchone()['n']
        cur.execute("SELECT COALESCE(SUM(pnl),0) t FROM paper_trades WHERE status='resolved'"); o['trades']['pnl']=round(cur.fetchone()['t'],2)
        c.close()
    except Exception as e: o['errors'].append(str(e))
    return o