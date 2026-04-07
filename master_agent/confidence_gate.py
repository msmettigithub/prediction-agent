import sqlite3,os
def check_confidence(db):
    m={'n':0,'acc':0,'brier':1.0,'sep':0.061,'wr':0}
    if not os.path.exists(db): return {'all_passed':False,'checks':{},'metrics':m,'blocking':['no_db']}
    try:
        c=sqlite3.connect(db); c.row_factory=sqlite3.Row; cur=c.cursor()
        cur.execute("SELECT COUNT(*) n FROM paper_trades WHERE status='resolved'"); m['n']=cur.fetchone()['n']
        cur.execute("SELECT AVG(CASE WHEN (model_prob>0.5 AND resolved_yes=1) OR (model_prob<=0.5 AND resolved_yes=0) THEN 1.0 ELSE 0.0 END) a,AVG((model_prob-resolved_yes)*(model_prob-resolved_yes)) b FROM paper_trades WHERE status='resolved'"); row=cur.fetchone(); m['acc']=round(row['a'] or 0,3); m['brier']=round(row['b'] or 1,3)
        cur.execute("SELECT AVG(CASE WHEN pnl>0 THEN 1.0 ELSE 0.0 END) w FROM (SELECT pnl FROM paper_trades WHERE status='resolved' ORDER BY resolved_at DESC LIMIT 20)"); m['wr']=round(cur.fetchone()['w'] or 0,3); c.close()
    except: pass
    ch={'n':m['n']>=30,'acc':m['acc']>0.65,'brier':m['brier']<0.25,'sep':m['sep']>0.10,'wr':m['wr']>0.55}
    return {'all_passed':all(ch.values()),'checks':ch,'metrics':m,'blocking':[k for k,v in ch.items() if not v]}