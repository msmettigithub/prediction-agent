import sqlite3
from datetime import datetime
def _t(c):
    c.execute("CREATE TABLE IF NOT EXISTS agent_log(id INTEGER PRIMARY KEY,ts TEXT,cid TEXT,lvl TEXT,agent TEXT,msg TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS agent_changes(id INTEGER PRIMARY KEY,cid TEXT,ts TEXT,hyp TEXT,file TEXT,diff TEXT,ok BOOLEAN,deployed BOOLEAN)")
    c.commit()
def log(db,msg,lvl='INFO',agent='sys',cid=None):
    print(f'[{lvl}] {msg}',flush=True)
    try:
        c=sqlite3.connect(db); _t(c); c.execute('INSERT INTO agent_log(ts,cid,lvl,agent,msg) VALUES(?,?,?,?,?)',(datetime.utcnow().isoformat(),cid,lvl,agent,msg)); c.commit(); c.close()
    except: pass
def log_change(db,cid,hyp,f,diff,ok,dep):
    try:
        c=sqlite3.connect(db); _t(c); c.execute('INSERT INTO agent_changes VALUES(NULL,?,?,?,?,?,?,?)',(cid,datetime.utcnow().isoformat(),hyp,f,diff[:1000],ok,dep)); c.commit(); c.close()
    except: pass