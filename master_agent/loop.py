#!/usr/bin/env python3
import os,sys,time,subprocess,shutil,tempfile,uuid,json,traceback,re
import threading,sqlite3,requests
from queue import Queue
from datetime import datetime,timezone
sys.path.insert(0,'/home/jovyan/workspace/prediction-agent')
import anthropic
from master_agent.observe import observe
from master_agent.confidence_gate import check_confidence
from master_agent.changelog import log,log_change
from master_agent import safeguards
DB='/home/jovyan/shared/sm/prediction-agent-db/prediction_agent.db'
PAT=os.environ.get('GITHUB_PAT','')
REPO=f'https://{PAT}@github.com/msmettigithub/prediction-agent.git'
SATURN=os.environ.get('SATURN_API_TOKEN','')
client=anthropic.Anthropic()
_lock=threading.Lock(); _git_lock=threading.Lock()
DAILY_BUDGET=50.0; _cost=[0.0]; _day=[datetime.now(timezone.utc).date().isoformat()]
def track(i,o,m):
    with _lock:
        t=datetime.now(timezone.utc).date().isoformat()
        if t!=_day[0]: _cost[0]=0.0; _day[0]=t
        _cost[0]+=(i+o*3)*(0.015 if 'opus' in m else 0.003)/1000
        return _cost[0]
def ok_budget(): return _cost[0]<DAILY_BUDGET*0.9
def dbq(sql,p=()):
    try:
        c=sqlite3.connect(DB); c.row_factory=sqlite3.Row
        r=c.execute(sql,p).fetchall(); c.close(); return [dict(x) for x in r]
    except: return []
def dbw(sql,p=()):
    try: c=sqlite3.connect(DB); c.execute(sql,p); c.commit(); c.close(); return True
    except: return False
def init_db():
    c=sqlite3.connect(DB)
    for s in [
        'CREATE TABLE IF NOT EXISTS agent_log(id INTEGER PRIMARY KEY,ts TEXT,cid TEXT,lvl TEXT,agent TEXT,msg TEXT)',
        'CREATE TABLE IF NOT EXISTS agent_changes(id INTEGER PRIMARY KEY,cid TEXT,ts TEXT,hyp TEXT,file TEXT,diff TEXT,ok BOOLEAN,deployed BOOLEAN,brier_before FLOAT)',
        "CREATE TABLE IF NOT EXISTS agent_commands(id INTEGER PRIMARY KEY,ts TEXT,command TEXT,status TEXT DEFAULT 'pending',result TEXT,executed_at TEXT)",
        'CREATE TABLE IF NOT EXISTS tool_experiments(id INTEGER PRIMARY KEY,ts TEXT,tool_name TEXT,description TEXT,test_result TEXT,useful INTEGER,notes TEXT)',
    ]: c.execute(s)
    c.commit(); c.close()
def rl_stats():
    rows=dbq('SELECT ok,deployed FROM agent_changes ORDER BY ts DESC LIMIT 30')
    n=len(rows); dep=sum(1 for r in rows if r['deployed'])
    return {'n':n,'deployed':dep,'rate':round(dep/max(n,1),2)}
BASE_CYCLE=300; MIN_CYCLE=60
def cycle_spd(gate,rl):
    s=BASE_CYCLE
    if len(gate.get('blocking',[]))<=2: s=int(s*0.6)
    if rl.get('rate',0)>0.7: s=int(s*0.7)
    if _cost[0]/DAILY_BUDGET>0.7: s=int(s*2)
    return max(MIN_CYCLE,s)
ORIENT_SYS='You are Karpathy Kapital sub-agent. Recommend ONE testable change to model/,scanner/,tools/,database/ ONLY. Never touch live/,master_agent/,.env,CLAUDE.md. Bold, concrete, Brier-improving. Consider tool_experiments results. Raw JSON:{recommendation,file_to_modify,hypothesis,change_description,priority,expected_brier_delta}'
CODE_SYS='Python engineer. Return ONLY complete new file. No markdown.'
COMMAND_SYS='Parse user order into JSON action. Actions: explore_tool{action,tool_name,description}, set_agents{action,n}, run_eval{action}, report{action}, ignore{action,reason}. Raw JSON only.'
EXPLORER_SYS='Evaluate Python library for prediction market trading signals. Test if useful for: event outcomes, real-time data, cross-market arb. JSON:{useful:bool,notes:str,signals_available:[str]}'
TOOLS=[('pytrends','Google Trends momentum'),('yfinance','Market correlations'),('newsapi','News headlines'),('vaderSentiment','Social sentiment'),('statsmodels','Calibration/logistic'),('scipy','Signal processing'),('pandas-ta','Price momentum'),('textblob','Quick NLP'),('alpha_vantage','Economic indicators'),('gnews','Free news API')]
def orient(obs,gate,rl,aid):
    if not ok_budget(): return None
    tools=json.dumps([{'t':r['tool_name'],'u':bool(r['useful']),'n':r['notes'][:60]} for r in dbq('SELECT * FROM tool_experiments LIMIT 6')])
    ctx=json.dumps({'gate':gate['all_passed'],'blocks':gate['blocking'],'metrics':gate['metrics'],'trades':obs['trades'],'rl':rl,'agent':aid,'budget_pct':round(_cost[0]/DAILY_BUDGET*100),'tools':tools})
    r=client.messages.create(model='claude-opus-4-6',max_tokens=600,system=ORIENT_SYS,messages=[{'role':'user','content':ctx}])
    track(r.usage.input_tokens,r.usage.output_tokens,'opus')
    t=re.sub(r'^\s*`{3}(?:json)?\s*','',r.content[0].text.strip())
    return json.loads(re.sub(r'\s*`{3}\s*$','',t).strip())
def decide(rec):
    if not ok_budget(): return None
    r=client.messages.create(model='claude-sonnet-4-6',max_tokens=4000,system=CODE_SYS,messages=[{'role':'user','content':f"Change:{rec['change_description']}\nHyp:{rec['hypothesis']}\nFile:{rec['file_to_modify']}"}])
    track(r.usage.input_tokens,r.usage.output_tokens,'sonnet'); return r.content[0].text.strip()
def act(rec,new,cid):
    with _git_lock:
        d=tempfile.mkdtemp()
        try:
            if subprocess.run(['git','clone','--depth=1',REPO,d],capture_output=True,timeout=120).returncode!=0:
                log(DB,'Clone fail','ERROR',cid=cid); return False
            subprocess.run(['git','config','user.email','kk@saturncloud.io'],cwd=d)
            subprocess.run(['git','config','user.name','KK Agent'],cwd=d)
            ok,reason=safeguards.can_act(rec['file_to_modify'])
            if not ok: log(DB,f'Blocked:{reason}','WARN',cid=cid); return False
            fp=os.path.join(d,rec['file_to_modify'])
            if not os.path.exists(fp): return False
            open(fp,'w').write(new)
            r=subprocess.run([sys.executable,'-m','pytest','tests/','-q','--tb=short'],cwd=d,capture_output=True,text=True,timeout=300)
            m=re.search(r'(\d+) passed',r.stdout+r.stderr); n=int(m.group(1)) if m else 0
            if r.returncode!=0 or n<safeguards.MIN_TEST_COUNT:
                log(DB,f'Tests failed({n})','ERROR',cid=cid)
                log_change(DB,cid,rec['hypothesis'],rec['file_to_modify'],'',False,False); return False
            subprocess.run(['git','add','-A'],cwd=d)
            subprocess.run(['git','commit','-m',f"kk[{cid}]:{rec['recommendation']}|delta:{rec.get('expected_brier_delta','?')}"],cwd=d)
            if subprocess.run(['git','push'],cwd=d,capture_output=True,timeout=60).returncode!=0:
                log(DB,'Push fail','ERROR',cid=cid); return False
            log_change(DB,cid,rec['hypothesis'],rec['file_to_modify'],'',True,True)
            log(DB,f"DEPLOYED:{rec['file_to_modify']}|delta:{rec.get('expected_brier_delta')}|cost:${_cost[0]:.3f}",'MILESTONE',cid=cid)
            return True
        finally: shutil.rmtree(d,ignore_errors=True)
def run_explorer(tool,desc,cid):
    if dbq('SELECT id FROM tool_experiments WHERE tool_name=?',(tool,)): return
    log(DB,f'EXPLORING:{tool}','MILESTONE',cid=cid)
    try:
        r=client.messages.create(model='claude-sonnet-4-6',max_tokens=600,system=EXPLORER_SYS,
            messages=[{'role':'user','content':f'Tool:{tool} Desc:{desc} Context:Kalshi prediction market trading'}])
        track(r.usage.input_tokens,r.usage.output_tokens,'sonnet')
        t=re.sub(r'^\s*`{3}(?:json)?\s*','',r.content[0].text.strip())
        res=json.loads(re.sub(r'\s*`{3}\s*$','',t).strip())
        u=1 if res.get('useful') else 0
        notes=res.get('notes','')+' | Signals:'+','.join(res.get('signals_available',[])[:4])
        dbw('INSERT INTO tool_experiments(ts,tool_name,description,test_result,useful,notes) VALUES(?,?,?,?,?,?)',
            (datetime.now(timezone.utc).isoformat(),tool,desc,json.dumps(res),u,notes[:500]))
        log(DB,f'EXPLORER:{tool} {"USEFUL" if u else "NOT USEFUL"} — {res.get("notes","")[:80]}','MILESTONE',cid=cid)
    except Exception as e: log(DB,f'Explorer err {tool}:{e}','ERROR',cid=cid)
def maybe_explore(cycle,cid):
    if cycle%3!=0: return
    tested=set(r['tool_name'] for r in dbq('SELECT tool_name FROM tool_experiments'))
    untested=[(n,d) for n,d in TOOLS if n not in tested]
    if untested: threading.Thread(target=run_explorer,args=(untested[0][0],untested[0][1],cid),daemon=True).start()
def parse_cmd(text):
    try:
        r=client.messages.create(model='claude-sonnet-4-6',max_tokens=200,system=COMMAND_SYS,messages=[{'role':'user','content':text}])
        track(r.usage.input_tokens,r.usage.output_tokens,'sonnet')
        t=re.sub(r'^\s*`{3}(?:json)?\s*','',r.content[0].text.strip())
        return json.loads(re.sub(r'\s*`{3}\s*$','',t).strip())
    except: return {'action':'ignore','reason':'parse fail'}
N_AGENTS=2
def process_cmds(obs,gate,cid):
    global N_AGENTS
    for cmd in dbq("SELECT * FROM agent_commands WHERE status='pending' ORDER BY ts LIMIT 3"):
        try:
            log(DB,f'ORDER RECEIVED:{cmd["command"]}','MILESTONE',cid=cid)
            p=parse_cmd(cmd['command']); a=p.get('action','ignore'); result='ok'
            if a=='explore_tool': threading.Thread(target=run_explorer,args=(p.get('tool_name','?'),p.get('description',''),cid),daemon=True).start(); result=f'Exploring {p.get("tool_name")}'
            elif a=='set_agents': N_AGENTS=max(1,min(4,int(p.get('n',2)))); result=f'N_AGENTS={N_AGENTS}'; log(DB,f'AGENTS SCALED TO {N_AGENTS}','MILESTONE',cid=cid)
            elif a=='report':
                rl=rl_stats(); result=f'Gate:{gate["all_passed"]} resolved:{obs["trades"]["resolved"]} pnl:${obs["trades"]["pnl"]} rl:{rl["rate"]} cost:${_cost[0]:.2f}'
                log(DB,f'REPORT:{result}','MILESTONE',cid=cid)
            else: result=f'Not understood:{p.get("reason")}'; log(DB,f'Order not understood:{cmd["command"]}','WARN',cid=cid)
            dbw('UPDATE agent_commands SET status=?,result=?,executed_at=? WHERE id=?',('done',result,datetime.now(timezone.utc).isoformat(),cmd['id']))
        except Exception as e: dbw('UPDATE agent_commands SET status=?,result=? WHERE id=?',('error',str(e),cmd['id']))
def sub_agent(aid,obs,gate,rl,q):
    cid=f'{aid[:3]}-{str(uuid.uuid4())[:4]}'
    try:
        rec=orient(obs,gate,rl,aid)
        if not rec: return
        log(DB,f'[{cid}] {aid}:{rec["recommendation"]}->{ rec["file_to_modify"]}',cid=cid)
        d=tempfile.mkdtemp()
        try:
            subprocess.run(['git','clone','--depth=1',REPO,d],capture_output=True,timeout=60)
            fp=os.path.join(d,rec['file_to_modify']); cur=open(fp).read() if os.path.exists(fp) else ''
        finally: shutil.rmtree(d,ignore_errors=True)
        new=decide(rec)
        if new: q.put((rec,new,cid,rec.get('priority','medium')))
    except Exception as e: log(DB,f'[{cid}] sub-agent:{e}','ERROR',cid=cid)
def main():
    global N_AGENTS
    init_db()
    log(DB,'='*55,'MILESTONE')
    log(DB,'KARPATHY KAPITAL v4 — AUTONOMOUS+ORDERS+EXPLORER ONLINE','MILESTONE')
    log(DB,f'Budget:${DAILY_BUDGET}/day Cycle:{BASE_CYCLE}s→{MIN_CYCLE}s Agents:{N_AGENTS}','MILESTONE')
    log(DB,'='*55,'MILESTONE')
    last_change=0; cycle=0
    while True:
        cycle+=1; cid=str(uuid.uuid4())[:8]
        try:
            obs=observe(DB); gate=check_confidence(DB); rl=rl_stats()
            speed=cycle_spd(gate,rl)
            log(DB,f'[{cid}] C#{cycle} gate:{gate["all_passed"]} blocks:{gate["blocking"]} resolved:{obs["trades"]["resolved"]} pnl:${obs["trades"]["pnl"]} rl:{rl["rate"]} cost:${_cost[0]:.2f}/{DAILY_BUDGET} agents:{N_AGENTS} next:{speed}s',
                'MILESTONE' if gate['all_passed'] else 'INFO',cid=cid)
            if gate['all_passed']: log(DB,'CONFIDENCE GATE PASSED — ALL GREEN — AWAITING HUMAN FOR LIVE','MILESTONE',cid=cid)
            process_cmds(obs,gate,cid)
            maybe_explore(cycle,cid)
            if not ok_budget(): log(DB,f'Budget cap ${_cost[0]:.2f}/{DAILY_BUDGET} — sleeping 1hr','WARN',cid=cid); time.sleep(3600); continue
            since=time.time()-last_change
            if since<3600: wait=min(speed,int(3600-since)); log(DB,f'[{cid}] Rate limit {wait}s',cid=cid); time.sleep(wait); continue
            log(DB,f'[{cid}] Launching {N_AGENTS} parallel agents',cid=cid)
            rq=Queue()
            ts=[threading.Thread(target=sub_agent,args=(f'agent-{i}',obs,gate,rl,rq)) for i in range(N_AGENTS)]
            for t in ts: t.start()
            for t in ts: t.join(timeout=180)
            results=[]
            while not rq.empty(): results.append(rq.get())
            if results:
                pmap={'critical':0,'high':1,'medium':2,'low':3}
                results.sort(key=lambda x:pmap.get(x[0].get('priority','medium'),2))
                rec,new,bcid,_=results[0]
                log(DB,f'[{cid}] Best of {len(results)}:{rec["recommendation"]}',cid=cid)
                if act(rec,new,bcid):
                    last_change=time.time()
                    if rl.get('rate',0)>0.8 and N_AGENTS<4:
                        N_AGENTS=min(4,N_AGENTS+1)
                        log(DB,f'SCALING TO {N_AGENTS} AGENTS (rate={rl["rate"]})','MILESTONE',cid=cid)
            else: log(DB,f'[{cid}] No recs from {N_AGENTS} agents',cid=cid)
        except Exception as e: log(DB,f'[{cid}] ERROR:{e}\n{traceback.format_exc()}','ERROR',cid=cid)
        log(DB,f'[{cid}] Cycle done. Next:{speed}s cost:${_cost[0]:.3f}',cid=cid)
        time.sleep(speed)
if __name__=='__main__': main()