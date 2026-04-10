import sqlite3,os
def check_confidence(db):
    m={'n':0,'acc':0,'brier':1.0,'sep':0,'wr':0}
    if not os.path.exists(db): return {'all_passed':False,'checks':{},'metrics':m,'blocking':['no_db']}
    try:
        c=sqlite3.connect(db); c.row_factory=sqlite3.Row; cur=c.cursor()
        cur.execute("SELECT COUNT(*) n FROM paper_trades WHERE status IN ('won','lost')"); m['n']=cur.fetchone()['n']
        cur.execute("""SELECT AVG(CASE WHEN (pt.model_prob>0.5 AND co.resolution=1) OR (pt.model_prob<=0.5 AND co.resolution=0) THEN 1.0 ELSE 0.0 END) a,
            AVG((pt.model_prob-co.resolution)*(pt.model_prob-co.resolution)) b
            FROM paper_trades pt JOIN contracts co ON pt.contract_id=co.id
            WHERE pt.status IN ('won','lost')"""); row=cur.fetchone(); m['acc']=round(row['a'] or 0,3); m['brier']=round(row['b'] or 1,3)
        # Compute separation: mean |prob-0.5| when correct minus when incorrect
        cur.execute("""SELECT
            AVG(CASE WHEN (pt.model_prob>0.5 AND co.resolution=1) OR (pt.model_prob<=0.5 AND co.resolution=0)
                THEN ABS(pt.model_prob-0.5) END) sep_correct,
            AVG(CASE WHEN NOT((pt.model_prob>0.5 AND co.resolution=1) OR (pt.model_prob<=0.5 AND co.resolution=0))
                THEN ABS(pt.model_prob-0.5) END) sep_incorrect
            FROM paper_trades pt JOIN contracts co ON pt.contract_id=co.id
            WHERE pt.status IN ('won','lost')""")
        srow=cur.fetchone(); sc=srow['sep_correct'] or 0; si=srow['sep_incorrect'] or 0; m['sep']=round(sc-si,4)
        cur.execute("SELECT AVG(CASE WHEN pnl>0 THEN 1.0 ELSE 0.0 END) w FROM (SELECT pnl FROM paper_trades WHERE status IN ('won','lost') ORDER BY closed_at DESC LIMIT 20)"); m['wr']=round(cur.fetchone()['w'] or 0,3); c.close()
    except: pass
    ch={'n':m['n']>=30,'acc':m['acc']>0.65,'brier':m['brier']<0.25,'sep':m['sep']>0.10,'wr':m['wr']>0.55}
    return {'all_passed':all(ch.values()),'checks':ch,'metrics':m,'blocking':[k for k,v in ch.items() if not v]}