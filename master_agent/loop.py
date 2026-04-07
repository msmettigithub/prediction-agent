#!/usr/bin/env python3
'Karpathy Kapital Master Agent — Full Autonomy + File Tree Fix'
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
try: from master_agent.doctor import check_and_heal
except ImportError: check_and_heal=lambda cid:[]
try: from master_agent import wiki
except ImportError: wiki=None

DB='/home/jovyan/shared/sm/prediction-agent-db/prediction_agent.db'
PAT=os.environ.get('GITHUB_PAT','')
REPO=f'https://{PAT}@github.com/msmettigithub/prediction-agent.git'
SATURN=os.environ.get('SATURN_API_TOKEN','')
BASE='https://app.community.saturnenterprise.io/api'
SELF_ID='18efeea066bc4d828b984ec1d752d131'
client=anthropic.Anthropic()
_lock=threading.Lock()
_git_lock=threading.Lock()
DAILY_BUDGET=50.0
_cost=[0.0]; _day=[datetime.now(timezone.utc).date().isoformat()]
def track(i,o,m):
    t=datetime.now(timezone.utc).date().isoformat()
    with _lock:
        if t!=_day[0]: _cost[0]=0.0; _day[0]=t
        _cost[0]+=(i*0.015+o*0.045)/1000 if 'opus' in m else (i*0.003+o*0.009)/1000; return _cost[0]
def ok_budget(): return _cost[0]<DAILY_BUDGET*0.9
def init_db():
    c=sqlite3.connect(DB)
    for sql in ['CREATE TABLE IF NOT EXISTS agent_log(id INTEGER PRIMARY KEY,ts TEXT,cid TEXT,lvl TEXT,agent TEXT,msg TEXT)',
        'CREATE TABLE IF NOT EXISTS agent_changes(id INTEGER PRIMARY KEY,cid TEXT,ts TEXT,hyp TEXT,file TEXT,diff TEXT,ok BOOLEAN,deployed BOOLEAN,brier_before FLOAT)',
        "CREATE TABLE IF NOT EXISTS agent_commands(id INTEGER PRIMARY KEY,ts TEXT,command TEXT,status TEXT DEFAULT 'pending',result TEXT,executed_at TEXT)",
        'CREATE TABLE IF NOT EXISTS tool_experiments(id INTEGER PRIMARY KEY,ts TEXT,tool_name TEXT,description TEXT,test_result TEXT,useful INTEGER,notes TEXT)']: c.execute(sql)
    c.commit(); c.close()
def dbq(sql,p=()):
    try:
        c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; r=c.execute(sql,p).fetchall(); c.close(); return [dict(x) for x in r]
    except: return []
def dbw(sql,p=()):
    try: c=sqlite3.connect(DB); c.execute(sql,p); c.commit(); c.close(); return True
    except: return False
def rl_stats():
    rows=dbq('SELECT ok,deployed FROM agent_changes ORDER BY ts DESC LIMIT 30')
    n=len(rows); dep=sum(1 for r in rows if r['deployed'])
    return {'n':n,'deployed':dep,'rate':round(dep/max(n,1),2),'total':len(dbq('SELECT id FROM agent_changes'))}
BASE_CYCLE=120; MIN_CYCLE=60
def cycle_speed(rl):
    s=BASE_CYCLE
    if rl.get('rate',0)>0.7: s=int(s*0.7)
    if _cost[0]/DAILY_BUDGET>0.7: s=BASE_CYCLE*2
    return max(MIN_CYCLE,s)
def saturn_api(method,path,body=None):
    if not SATURN: return {}
    try:
        h={'Authorization':f'token {SATURN}','Content-Type':'application/json'}
        return getattr(requests,method)(f'{BASE}{path}',json=body,headers=h,timeout=15).json()
    except: return {}
IMG='9879fc989f054272903cd4afd5e520bd'
SECRETS={'GITHUB_PAT':'3b8f8e0ea8a54b3fa85e210e3e94d79b','ANTHROPIC_API_KEY':'46a084f1474f4b3e8db0fbe5fbeb2f1d','SATURN_API_TOKEN':'e0e6ce81230d456d97077984c2135f09','KALSHI_API_KEY':'6efa392feb144083aa8eec4cee65ad2c','FRED_API_KEY':'25a493cef4e540b592259eaf410014d4','KALSHI_PRIVATE_KEY':'a2c72eae85324f7a91534b5ddae808a8','BANKROLL':'9a9ea2608f514ef5a9d665e5227f53b3','EDGE_THRESHOLD':'d6f9deb20e294380ae60816b37b0a49c','LIVE_TRADING_ENABLED':'f7ddf60450d94f35a03c42559ca23629','MAX_LIVE_BANKROLL':'706c4f79d15f49728748aa145c1f2a0b','MAX_SINGLE_BET':'c66a5d68b7094b00b0818d56fe1a54e7','MOCK_TOOLS':'b43c2c1429ac41859ff507acfd104330'}
SHARED='ab695f5218d6402cb9289b821764c370'; GIT='9929090f1a4a48f6904f947210b94708'
def attach_all(rtype,rid):
    for name,sid in SECRETS.items():
        loc='/home/jovyan/.kalshi/private_key.pem' if name=='KALSHI_PRIVATE_KEY' else name
        att='file' if name=='KALSHI_PRIVATE_KEY' else 'environment_variable'
        saturn_api('post',f'/api/{rtype}s/{rid}/secrets',{'secret_id':sid,'location':loc,'attachment_type':att})
    saturn_api('post','/api/external_repo_attachments',{'external_repo_id':GIT,f'{rtype}_id':rid,'path':'/home/jovyan/workspace/prediction-agent'})
    saturn_api('post','/api/shared_folder_attachments',{'shared_folder_id':SHARED,f'{rtype}_id':rid,'path':'/home/jovyan/shared/sm/prediction-agent-db'})
def provision_job(name,command,schedule=None,cid=None):
    r=saturn_api('post','/api/jobs',{'name':name,'command':command,'image_tag_id':IMG,'instance_size':'medium','working_dir':'/home/jovyan/workspace/prediction-agent','extra_packages':{'pip':'anthropic requests','as_requirements_txt':False,'use_mamba':False},'start_script':'mkdir -p ~/.kalshi && git config --global user.email kk@saturncloud.io && git config --global user.name "KK Agent"'})
    if not r.get('id'): return None
    jid=r['id']; attach_all('job',jid)
    if schedule: saturn_api('patch',f'/api/jobs/{jid}',{'cron_schedule_options':{'schedule':schedule}})
    saturn_api('post',f'/api/jobs/{jid}/start',{}); log(DB,f'Provisioned job {name} [{jid}]','MILESTONE',cid=cid); return jid
def provision_deployment(name,command,cid=None):
    r=saturn_api('post','/api/deployments',{'name':name,'command':command,'image_tag_id':IMG,'instance_size':'medium','working_dir':'/home/jovyan/workspace/prediction-agent','extra_packages':{'pip':'anthropic requests','as_requirements_txt':False,'use_mamba':False},'start_script':'mkdir -p ~/.kalshi && git config --global user.email kk@saturncloud.io && git config --global user.name "KK Agent"'})
    if not r.get('id'): return None
    did=r['id']; attach_all('deployment',did); saturn_api('post',f'/api/deployments/{did}/start',{})
    log(DB,f'Provisioned deployment {name} [{did}]','MILESTONE',cid=cid); return did
def resize_self(size,cid=None):
    r=saturn_api('patch',f'/api/deployments/{SELF_ID}',{'instance_size':size})
    if r.get('instance_size')==size: log(DB,f'Resized to {size} (next restart)','MILESTONE',cid=cid); return True
    return False
# ── Verified file list (prevents recommend-nonexistent-file errors)
KNOWN_FILES=["model/probability_model.py","model/calibration.py","model/base_rates.py","model/edge_calculator.py","scanner/scanner.py","scanner/filters.py","database/db.py","database/models.py","tools/kalshi.py","tools/polymarket.py","tools/metaculus.py","tools/search_news.py","tools/fed_data.py","tools/polling_data.py","tools/twitter_sentiment.py","tools/kelly_criterion.py","tools/prediction_history.py","tools/academic_forecasting.py","tools/sports_data.py","tools/sec_filings.py","tools/manifold.py","tools/tool_registry.py"]
def get_repo_files(d):
    files=[]
    for root,dirs,fnames in os.walk(d):
        dirs[:]=[x for x in dirs if x not in ['__pycache__','.git','master_agent','live']]
        for f in fnames:
            if f.endswith('.py'):
                rel=os.path.relpath(os.path.join(root,f),d)
                if not any(s in rel for s in ['live/','master_agent/']): files.append(rel)
    return sorted(files)
# ── Command queue
COMMAND_SYS='Parse order for Karpathy Kapital master agent. Actions: explore_tool, set_agents, report, heal_all, provision_job{name,command,schedule?}, provision_deployment{name,command}, resize_self{size}, ignore. Raw JSON only.'
N_AGENTS=4
def parse_cmd(cmd):
    try:
        r=client.messages.create(model='claude-sonnet-4-6',max_tokens=200,system=COMMAND_SYS,messages=[{'role':'user','content':cmd}])
        track(r.usage.input_tokens,r.usage.output_tokens,'sonnet')
        t=re.sub(r'^\s*`{3}(?:json)?\s*','',r.content[0].text.strip())
        return json.loads(re.sub(r'\s*`{3}\s*$','',t).strip())
    except: return {'action':'ignore'}
def exec_cmd(row,obs,gate,cid):
    global N_AGENTS
    p=parse_cmd(row['command']); a=p.get('action','ignore'); res='ok'
    if a=='explore_tool': threading.Thread(target=run_explorer,args=(p.get('tool_name','?'),p.get('description',''),cid),daemon=True).start(); res=f'exploring {p.get("tool_name")}' 
    elif a=='set_agents': N_AGENTS=max(1,min(8,int(p.get('n',4)))); res=f'N_AGENTS={N_AGENTS}'; log(DB,f'N_AGENTS set to {N_AGENTS}','MILESTONE',cid=cid)
    elif a=='heal_all': res=str(check_and_heal(cid))
    elif a=='report': res=f'gate:{gate["all_passed"]} resolved:{obs["trades"]["resolved"]} pnl:${obs["trades"]["pnl"]} rl:{rl_stats()["rate"]} cost:${_cost[0]:.2f}'; log(DB,f'REPORT:{res}','MILESTONE',cid=cid)
    elif a=='resize_self': res=str(resize_self(p.get('size','large'),cid))
    elif a=='provision_job': res=str(provision_job(p.get('name','kk-job'),p.get('command','echo done'),p.get('schedule'),cid))
    elif a=='provision_deployment': res=str(provision_deployment(p.get('name','kk-worker'),p.get('command','python -m master_agent.loop'),cid))
    dbw('UPDATE agent_commands SET status=?,result=?,executed_at=? WHERE id=?',('done',res[:200],datetime.now(timezone.utc).isoformat(),row['id']))
def proc_cmds(obs,gate,cid):
    for cmd in dbq("SELECT * FROM agent_commands WHERE status='pending' ORDER BY ts LIMIT 5"):
        try: exec_cmd(cmd,obs,gate,cid)
        except Exception as e: dbw('UPDATE agent_commands SET status=?,result=? WHERE id=?',('error',str(e),cmd['id']))
# ── Tool explorer
TOOLS=[('pytrends','Google Trends political/econ momentum'),('yfinance','Market correlations'),('newsapi','Real-time news'),('statsmodels','Calibration, logistic regression'),('vaderSentiment','Social media sentiment'),('textblob','Quick NLP'),('pandas-ta','Technical analysis on probs'),('alpha_vantage','Economic indicators'),('scipy','Signal processing'),('beautifulsoup4','Web scraping RCP polls'),('gnews','Alternative news'),('polymarket-py','Polymarket direct')]
EXPL_SYS='Evaluate this Python library for Kalshi prediction market trading. JSON:{useful:bool,notes:str,signals_available:[str]}'
def run_explorer(tool,desc,cid):
    if dbq('SELECT id FROM tool_experiments WHERE tool_name=?',(tool,)): return
    log(DB,f'EXPLORING:{tool}','MILESTONE',cid=cid)
    try:
        r=client.messages.create(model='claude-sonnet-4-6',max_tokens=400,system=EXPL_SYS,messages=[{'role':'user','content':f'Tool:{tool} Desc:{desc}'}])
        track(r.usage.input_tokens,r.usage.output_tokens,'sonnet')
        t=re.sub(r'^\s*`{3}(?:json)?\s*','',r.content[0].text.strip())
        res=json.loads(re.sub(r'\s*`{3}\s*$','',t).strip())
        useful=1 if res.get('useful') else 0
        notes=f"{res.get('notes','')} | {', '.join(res.get('signals_available',[])[:4])}"
        dbw('INSERT INTO tool_experiments(ts,tool_name,description,test_result,useful,notes) VALUES(?,?,?,?,?,?)',(datetime.now(timezone.utc).isoformat(),tool,desc,json.dumps(res),useful,notes[:400]))
        log(DB,f'EXPLORER:{tool} {"USEFUL" if useful else "SKIP"} {notes[:80]}','MILESTONE',cid=cid)
        if wiki: wiki.log_tool_experiment(tool,bool(useful),notes[:80],cid)
    except Exception as e: log(DB,f'Explorer error {tool}:{e}','ERROR',cid=cid)
def maybe_explore(cycle,cid):
    if cycle%3!=0: return
    tested=set(r['tool_name'] for r in dbq('SELECT tool_name FROM tool_experiments'))
    untested=[(n,d) for n,d in TOOLS if n not in tested]
    if untested: threading.Thread(target=run_explorer,args=(*untested[0],cid),daemon=True).start()
# ── Improvement agents
ORIENT_SYS="You are Karpathy Kapital autonomous improvement agent.\nFramework: prompt=model, eval=loss, resolved_trades=training_data. Maximize Brier score via RL.\n\nMODIFIABLE FILES (ONLY use these exact paths — no others exist):\n  model/probability_model.py  - main probability estimator and modifiers\n  model/calibration.py        - calibration curves, Platt scaling\n  model/base_rates.py         - historical base rates by category\n  model/edge_calculator.py    - edge detection, Kelly fraction\n  scanner/scanner.py          - market scanning, contract scoring\n  scanner/filters.py          - market filters, liquidity thresholds\n  database/db.py              - SQLite queries and storage\n  database/models.py          - data models and schema\n  tools/search_news.py        - news signal extraction\n  tools/fed_data.py           - FRED economic data\n  tools/polling_data.py       - polling aggregation\n  tools/twitter_sentiment.py  - social sentiment\n  tools/kalshi.py             - Kalshi API client\n  tools/polymarket.py         - Polymarket integration\n  tools/tool_registry.py      - tool routing\n\nNEVER modify: live/, master_agent/, .env, CLAUDE.md\nConsult tool_experiments for proven libraries. Be bold. Target biggest Brier delta.\nRaw JSON ONLY: {recommendation,file_to_modify,hypothesis,change_description,priority,expected_brier_delta}"
CODE_SYS='Senior Python engineer. Complete new file only. No markdown, no backticks.'
def orient(obs,gate,rl,aid,avail):
    if not ok_budget(): return None
    tools_ctx=json.dumps([{'t':r['tool_name'],'ok':bool(r['useful'])} for r in dbq('SELECT * FROM tool_experiments LIMIT 10')])
    ctx=json.dumps({'gate':gate['all_passed'],'blocking':gate['blocking'],'metrics':gate['metrics'],'trades':obs['trades'],'rl':rl,'agent':aid,'budget_pct':round(_cost[0]/DAILY_BUDGET*100),'tools':tools_ctx,'available_files':list(set(KNOWN_FILES+avail))[:30]})
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
            if subprocess.run(['git','clone','--depth=1',REPO,d],capture_output=True,text=True,timeout=120).returncode!=0: log(DB,'Clone failed','ERROR',cid=cid); return False
            subprocess.run(['git','config','user.email','kk@saturncloud.io'],cwd=d)
            subprocess.run(['git','config','user.name','KK Agent'],cwd=d)
            ok,reason=safeguards.can_act(rec['file_to_modify'])
            if not ok: log(DB,f'Blocked:{reason}','WARN',cid=cid); return False
            fp=os.path.join(d,rec['file_to_modify'])
            if not os.path.exists(fp): log(DB,f'File not found:{rec["file_to_modify"]}','WARN',cid=cid); return False
            open(fp,'w').write(new)
            r=subprocess.run([sys.executable,'-m','pytest','tests/','-q','--tb=short'],cwd=d,capture_output=True,text=True,timeout=300)
            m=re.search(r'(\d+) passed',r.stdout+r.stderr); n=int(m.group(1)) if m else 0
            if r.returncode!=0 or n<safeguards.MIN_TEST_COUNT: log(DB,f'Tests failed({n})','ERROR',cid=cid); log_change(DB,cid,rec['hypothesis'],rec['file_to_modify'],'',False,False); return False
            subprocess.run(['git','add','-A'],cwd=d)
            subprocess.run(['git','commit','-m',f"kk[{cid}]:{rec['recommendation']}|delta:{rec.get('expected_brier_delta','?')}"],cwd=d)
            if subprocess.run(['git','push'],cwd=d,capture_output=True,timeout=60).returncode!=0: log(DB,'Push failed','ERROR',cid=cid); return False
            log_change(DB,cid,rec['hypothesis'],rec['file_to_modify'],'',True,True)
            log(DB,f"DEPLOYED:{rec['file_to_modify']}|delta:{rec.get('expected_brier_delta')}|cost:${_cost[0]:.3f}",'MILESTONE',cid=cid); return True
        finally: shutil.rmtree(d,ignore_errors=True)
def sub_agent(aid,obs,gate,rl,q):
    cid=f'{aid[:3]}-{str(uuid.uuid4())[:4]}'
    try:
        # Discover real files from repo before orient
        d=tempfile.mkdtemp()
        try: subprocess.run(['git','clone','--depth=1',REPO,d],capture_output=True,timeout=60); avail=get_repo_files(d)
        finally: shutil.rmtree(d,ignore_errors=True)
        rec=orient(obs,gate,rl,aid,avail)
        if not rec: return
        # Validate file exists — never waste a decide() on phantom files
        all_known=set(KNOWN_FILES+avail)
        if rec.get('file_to_modify') not in all_known:
            log(DB,f'[{cid}] SKIP: {rec.get("file_to_modify")} not in repo',cid=cid); return
        log(DB,f'[{cid}] {aid}:{rec["recommendation"]}',cid=cid)
        d2=tempfile.mkdtemp()
        try: subprocess.run(['git','clone','--depth=1',REPO,d2],capture_output=True,timeout=60); fp=os.path.join(d2,rec['file_to_modify']); cur=open(fp).read() if os.path.exists(fp) else ''
        finally: shutil.rmtree(d2,ignore_errors=True)
        new=decide(rec)
        if new: q.put((rec,new,cid,rec.get('priority','medium')))
    except Exception as e: log(DB,f'[{cid}] error:{e}','ERROR',cid=cid)
def main():
    global N_AGENTS
    init_db()
    log(DB,'='*55,'MILESTONE')
    log(DB,'KARPATHY KAPITAL — FULL AUTONOMY + FILE FIX','MILESTONE')
    log(DB,f'22 verified files | 4 agents | 2-min cycles | doctor | wiki','MILESTONE')
    log(DB,'='*55,'MILESTONE')
    if wiki: wiki.log_decision('Full autonomy fixed — file tree embedded','prevent nonexistent file errors')
    last_change=0; cycle=0; last_wiki=0
    while True:
        cycle+=1; cid=str(uuid.uuid4())[:8]
        try:
            obs=observe(DB); gate=check_confidence(DB); rl=rl_stats(); speed=cycle_speed(rl)
            log(DB,f'[{cid}] C#{cycle} gate:{gate["all_passed"]} blocks:{gate["blocking"]} resolved:{obs["trades"]["resolved"]} pnl:${obs["trades"]["pnl"]} rl:{rl["rate"]}({rl["total"]}) cost:${_cost[0]:.2f}/{DAILY_BUDGET} agents:{N_AGENTS} next:{speed}s','MILESTONE' if gate['all_passed'] else 'INFO',cid=cid)
            if gate['all_passed']: log(DB,'CONFIDENCE GATE PASSED — AWAITING HUMAN FOR LIVE TRADING','MILESTONE',cid=cid)
            try: healed=check_and_heal(cid);
            except Exception as e: healed=[]; log(DB,f'Doctor error:{e}','ERROR',cid=cid)
            if healed: log(DB,f'Doctor healed:{healed}','MILESTONE',cid=cid)
            proc_cmds(obs,gate,cid)
            maybe_explore(cycle,cid)
            if wiki and cycle-last_wiki>=30:
                try: wiki.update_state({'gate':gate['all_passed'],'resolved':obs['trades']['resolved'],'pnl':obs['trades']['pnl'],'rl_rate':rl['rate'],'cost':f'${_cost[0]:.2f}'},cid); last_wiki=cycle
                except: pass
            if not ok_budget(): log(DB,f'Budget cap — sleeping 1hr','WARN',cid=cid); time.sleep(3600); continue
            since=time.time()-last_change
            if since<3600: wait=min(speed,int(3600-since)); log(DB,f'[{cid}] Rate limit {wait}s',cid=cid); time.sleep(wait); continue
            log(DB,f'[{cid}] Launching {N_AGENTS} sub-agents',cid=cid)
            rq=Queue()
            threads=[threading.Thread(target=sub_agent,args=(f'agent-{i}',obs,gate,rl,rq),daemon=True) for i in range(N_AGENTS)]
            for t in threads: t.start()
            for t in threads: t.join(timeout=180)
            results=[]
            while not rq.empty(): results.append(rq.get())
            if results:
                pmap={'critical':0,'high':1,'medium':2,'low':3}
                results.sort(key=lambda x:pmap.get(x[0].get('priority','medium'),2))
                rec,new,bcid,_=results[0]
                log(DB,f'[{cid}] Best of {len(results)}: {rec["recommendation"]}→{rec["file_to_modify"]}',cid=cid)
                if act(rec,new,bcid):
                    last_change=time.time(); log(DB,f'[{cid}] DEPLOYED C#{cycle}','MILESTONE',cid=cid)
                    if rl.get('rate',0)>0.8 and N_AGENTS<8: N_AGENTS=min(8,N_AGENTS+1); log(DB,f'Scaled to {N_AGENTS} agents','MILESTONE',cid=cid)
            else: log(DB,f'[{cid}] No deployable changes this cycle',cid=cid)
        except Exception as e: log(DB,f'[{cid}] MAIN ERROR:{e}\n{traceback.format_exc()}','ERROR',cid=cid)
        log(DB,f'[{cid}] Sleeping {speed}s cost:${_cost[0]:.3f}',cid=cid)
        time.sleep(speed)
if __name__=='__main__': main()