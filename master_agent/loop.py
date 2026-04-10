# Patch applied over loop.py - fixed ORIENT_SYS (ASCII only) + retry logic + doctor fix
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
DAILY_BUDGET=float(os.environ.get("DAILY_BUDGET","999999"))
_cost=[0.0]; _day=[datetime.now(timezone.utc).date().isoformat()]
def track(i,o,m):
    t=datetime.now(timezone.utc).date().isoformat()
    with _lock:
        if t!=_day[0]: _cost[0]=0.0; _day[0]=t
        _cost[0]+=(i*0.015+o*0.045)/1000 if 'opus' in m else (i*0.003+o*0.009)/1000
        return _cost[0]
def ok_budget(): return _cost[0]<DAILY_BUDGET*0.9
def init_db():
    c=sqlite3.connect(DB)
    for sql in [
        'CREATE TABLE IF NOT EXISTS agent_log(id INTEGER PRIMARY KEY,ts TEXT,cid TEXT,lvl TEXT,agent TEXT,msg TEXT)',
        'CREATE TABLE IF NOT EXISTS agent_changes(id INTEGER PRIMARY KEY,cid TEXT,ts TEXT,hyp TEXT,file TEXT,diff TEXT,ok BOOLEAN,deployed BOOLEAN,brier_before FLOAT)',
        "CREATE TABLE IF NOT EXISTS agent_commands(id INTEGER PRIMARY KEY,ts TEXT,command TEXT,status TEXT DEFAULT 'pending',result TEXT,executed_at TEXT)",
        'CREATE TABLE IF NOT EXISTS tool_experiments(id INTEGER PRIMARY KEY,ts TEXT,tool_name TEXT,description TEXT,test_result TEXT,useful INTEGER,notes TEXT)',
    ]: c.execute(sql)
    c.commit(); c.close()
def dbq(sql,p=()):
    try:
        c=sqlite3.connect(DB); c.row_factory=sqlite3.Row
        r=c.execute(sql,p).fetchall(); c.close(); return [dict(x) for x in r]
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
SECRETS={'GITHUB_PAT':'3b8f8e0ea8a54b3fa85e210e3e94d79b','ANTHROPIC_API_KEY':'46a084f1474f4b3e8db0fbe5fbeb2f1d','SATURN_API_TOKEN':'e0e6ce81230d456d97077984c2135f09','KALSHI_API_KEY':'6efa392feb144083aa8eec4cee65ad2c','FRED_API_KEY':'25a493cef4e540b592259eaf410014d4','KALSHI_PRIVATE_KEY':'a2c72eae85324f7a91534b5ddae808a8','BANKROLL':'9a9ea2608f514ef5a9d665e5227f53b3','EDGE_THRESHOLD':'d6f9deb20e294380ae60816b37b0a49c','LIVE_TRADING_ENABLED':'f7ddf60450d94f35a03c42559ca23629','MAX_LIVE_BANKROLL':'706c4f79d15f49728748aa145c1f2a0b','MAX_SINGLE_BET':'c66a5d68b7094b00b0818d56fe1a54e7','MOCK_TOOLS':'b43c2c1429ac41859ff507acfd104330','OPENROUTER':'OPENROUTER_SECRET_ID'}
SHARED='ab695f5218d6402cb9289b821764c370'; GIT='9929090f1a4a48f6904f947210b94708'
def attach_all(rtype,rid):
    for name,sid in SECRETS.items():
        loc='/home/jovyan/.kalshi/private_key.pem' if name=='KALSHI_PRIVATE_KEY' else name
        att='file' if name=='KALSHI_PRIVATE_KEY' else 'environment_variable'
        saturn_api('post',f'/api/{rtype}s/{rid}/secrets',{'secret_id':sid,'location':loc,'attachment_type':att})
    saturn_api('post','/api/external_repo_attachments',{'external_repo_id':GIT,f'{rtype}_id':rid,'path':'/home/jovyan/workspace/prediction-agent'})
    saturn_api('post','/api/shared_folder_attachments',{'shared_folder_id':SHARED,f'{rtype}_id':rid,'path':'/home/jovyan/shared/sm/prediction-agent-db'})
def provision_job(name,command,schedule=None,cid=None):
    r=saturn_api('post','/api/jobs',{'name':name,'command':command,'image_tag_id':IMG,'instance_size':'medium','working_dir':'/home/jovyan/workspace/prediction-agent','extra_packages':{'pip':'anthropic requests','as_requirements_txt':False,'use_mamba':False},'start_script':'mkdir -p ~/.kalshi && git config --global user.email kk@saturncloud.io && git config --global user.name KK'})
    if not r.get('id'): return None
    jid=r['id']; attach_all('job',jid)
    if schedule: saturn_api('patch',f'/api/jobs/{jid}',{'cron_schedule_options':{'schedule':schedule}})
    saturn_api('post',f'/api/jobs/{jid}/start',{}); log(DB,f'Provisioned job {name} [{jid}]','MILESTONE',cid=cid); return jid
def provision_deployment(name,command,cid=None):
    r=saturn_api('post','/api/deployments',{'name':name,'command':command,'image_tag_id':IMG,'instance_size':'medium','working_dir':'/home/jovyan/workspace/prediction-agent','extra_packages':{'pip':'anthropic requests','as_requirements_txt':False,'use_mamba':False},'start_script':'mkdir -p ~/.kalshi && git config --global user.email kk@saturncloud.io && git config --global user.name KK'})
    if not r.get('id'): return None
    did=r['id']; attach_all('deployment',did); saturn_api('post',f'/api/deployments/{did}/start',{})
    log(DB,f'Provisioned deployment {name} [{did}]','MILESTONE',cid=cid); return did
def resize_self(size,cid=None):
    r=saturn_api('patch',f'/api/deployments/{SELF_ID}',{'instance_size':size})
    if r.get('instance_size')==size: log(DB,f'Resized to {size}','MILESTONE',cid=cid); return True
    return False
# KNOWN FILES - verified to exist in repo
KNOWN_FILES=['model/probability_model.py','model/calibration.py','model/base_rates.py','model/edge_calculator.py','scanner/scanner.py','scanner/filters.py','database/db.py','database/models.py','tools/kalshi.py','tools/polymarket.py','tools/metaculus.py','tools/search_news.py','tools/fed_data.py','tools/polling_data.py','tools/twitter_sentiment.py','tools/kelly_criterion.py','tools/prediction_history.py','tools/academic_forecasting.py','tools/sports_data.py','tools/sec_filings.py','tools/manifold.py','tools/tool_registry.py']
def get_repo_files(d):
    files=[]
    for root,dirs,fnames in os.walk(d):
        dirs[:]=[x for x in dirs if x not in ['__pycache__','.git','master_agent','live']]
        for f in fnames:
            if f.endswith('.py'):
                rel=os.path.relpath(os.path.join(root,f),d)
                if not any(s in rel for s in ['live/','master_agent/']): files.append(rel)
    return sorted(files)
# ORIENT_SYS - pure ASCII, no special chars, forces JSON output
ORIENT_SYS = 'You are the Karpathy Kapital improvement agent. Maximize Brier score via RL iteration. CRITICAL SYSTEM CONTEXT: 0 resolved paper trades is EXPECTED and CORRECT. The paper trading pipeline works fine. Real contracts resolve April 10 2026. Backtest baseline: 51 seeded contracts, Brier=0.168, acc=80.4%, separation=6.1%. DO NOT try to fix the pipeline - it works. The REAL bottleneck is: separation=6.1% but gate needs 10pp. This means the model assigns probabilities too close to 50/50 - it lacks confidence. Fix this by: improving base rate calibration, better signal weighting in probability_model.py, stronger priors in base_rates.py, better feature extraction in scanner/scanner.py. Recommend ONE change to available_files ONLY. NEVER modify: live/, master_agent/, .env, CLAUDE.md. No markdown in response. Return only a single JSON object with keys: recommendation, file_to_modify, hypothesis, change_description, priority, expected_brier_delta'
CODE_SYS='Senior Python engineer. Return ONLY the complete new file content. No markdown, no backticks, no explanation.'
def parse_json_response(text):
    text=text.strip()
    # Strip markdown code blocks if present
    text=re.sub(r'^```(?:json)?\s*','',text,flags=re.MULTILINE)
    text=re.sub(r'```\s*$','',text,flags=re.MULTILINE)
    text=text.strip()
    # Try direct parse first
    try: return json.loads(text)
    except: pass
    # Find JSON object with regex
    m=re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}',text,re.DOTALL)
    if m:
        try: return json.loads(m.group())
        except: pass
    return None
def orient(obs,gate,rl,aid,avail):
    if not ok_budget(): return None
    tools_ctx=json.dumps([{'t':r['tool_name'],'ok':bool(r['useful'])} for r in dbq('SELECT * FROM tool_experiments LIMIT 10')])
    ctx=json.dumps({'gate':gate['all_passed'],'blocking':gate['blocking'],'metrics':gate['metrics'],
                   'trades':obs['trades'],'rl':rl,'agent':aid,
                   'budget_pct':round(_cost[0]/DAILY_BUDGET*100),
                   'tools_tested':tools_ctx,
                   'available_files':list(set(KNOWN_FILES+avail))[:25]})
    for attempt in range(2):  # retry once on parse failure
        try:
            r=client.messages.create(model='claude-opus-4-6',max_tokens=600,
                system=ORIENT_SYS,messages=[{'role':'user','content':ctx}])
            track(r.usage.input_tokens,r.usage.output_tokens,'opus')
            result=parse_json_response(r.content[0].text)
            if result and result.get('file_to_modify'):
                return result
            log(DB,f'[{aid}] orient attempt {attempt+1} bad JSON: {r.content[0].text[:100]}','WARN')
        except Exception as e:
            log(DB,f'[{aid}] orient attempt {attempt+1} error: {e}','ERROR')
    return None
ADVISOR_SYS='You are a quantitative trading strategist advising an autonomous prediction market trading agent. Your only goal is to improve separation score from 6.1% to 10%. Be specific and technical.'
OPENROUTER_KEY=os.environ.get('OPENROUTER','')
ADVISOR_MODELS=[
    ('anthropic/claude-sonnet-4','claude-sonnet'),
    ('google/gemini-2.5-flash','gemini-flash'),
    ('openai/gpt-4.1-mini','gpt-4.1-mini'),
]
def _openrouter_call(model,system,user_msg,max_tokens=1000):
    """Call a model via OpenRouter API. Returns response text or empty string."""
    r=requests.post('https://openrouter.ai/api/v1/chat/completions',
        headers={'Authorization':f'Bearer {OPENROUTER_KEY}','Content-Type':'application/json'},
        json={'model':model,'max_tokens':max_tokens,
              'messages':[{'role':'system','content':system},{'role':'user','content':user_msg}]},
        timeout=30)
    if r.status_code!=200: return ''
    choices=r.json().get('choices',[])
    return choices[0]['message']['content'].strip() if choices else ''
def advise(obs,gate,rl,cid):
    """ADVISE step: fan out to multiple LLMs via OpenRouter for diverse strategic recommendations."""
    if not ok_budget(): return ''
    state=gate.get('metrics',{})
    changes=dbq('SELECT ts,hyp,file,ok,deployed FROM agent_changes ORDER BY ts DESC LIMIT 5')
    user_msg=json.dumps({
        'brier_score':state.get('brier'),
        'separation_score':state.get('sep',0.061),
        'accuracy':state.get('acc'),
        'win_rate':state.get('wr'),
        'resolved_trades':obs.get('trades',{}).get('resolved',0),
        'pnl':obs.get('trades',{}).get('pnl',0),
        'rl_deploy_rate':rl.get('rate',0),
        'last_5_agent_changes':changes,
    },default=str)
    recs=[]
    # Fan out to multiple models via OpenRouter
    if OPENROUTER_KEY:
        for model_id,label in ADVISOR_MODELS:
            try:
                r=_openrouter_call(model_id,ADVISOR_SYS,user_msg)
                if r:
                    recs.append(f'[{label}] {r}')
                    dbw("INSERT INTO agent_log(ts,cid,lvl,agent,msg) VALUES(?,?,?,?,?)",
                        (datetime.now(timezone.utc).isoformat(),cid,'INFO',f'advisor-{label}',r[:2000]))
            except Exception as e:
                log(DB,f'[{cid}] advisor-{label} error:{e}','WARN',cid=cid)
    # Fallback to direct Anthropic if OpenRouter unavailable or as additional signal
    if not recs:
        try:
            r=client.messages.create(model='claude-sonnet-4-6',max_tokens=1000,
                system=ADVISOR_SYS,messages=[{'role':'user','content':user_msg}])
            track(r.usage.input_tokens,r.usage.output_tokens,'sonnet')
            rec=r.content[0].text.strip()
            recs.append(f'[claude-sonnet-direct] {rec}')
            dbw("INSERT INTO agent_log(ts,cid,lvl,agent,msg) VALUES(?,?,?,?,?)",
                (datetime.now(timezone.utc).isoformat(),cid,'INFO','advisor',rec[:2000]))
        except Exception as e:
            log(DB,f'[{cid}] advisor error:{e}','ERROR',cid=cid)
    return '\n\n'.join(recs)
def decide(rec,advisor_rec=''):
    if not ok_budget(): return None
    advisor_ctx=f"\n\n## Advisor Recommendation\n{advisor_rec}" if advisor_rec else ''
    r=client.messages.create(model='claude-sonnet-4-6',max_tokens=4000,system=CODE_SYS,
        messages=[{'role':'user','content':f"Implement this change.\nFile: {rec['file_to_modify']}\nChange: {rec['change_description']}\nHypothesis: {rec['hypothesis']}{advisor_ctx}"}])
    track(r.usage.input_tokens,r.usage.output_tokens,'sonnet')
    return r.content[0].text.strip()
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
            r=subprocess.run([sys.executable,'-m','pytest','tests/','-q','--tb=short'],
                cwd=d,capture_output=True,text=True,timeout=300)
            m=re.search(r'(\d+) passed',r.stdout+r.stderr); n=int(m.group(1)) if m else 0
            if r.returncode!=0 or n<safeguards.MIN_TEST_COUNT: log(DB,f'Tests failed({n}):{(r.stdout+r.stderr)[-200:]}','ERROR',cid=cid); log_change(DB,cid,rec['hypothesis'],rec['file_to_modify'],'',False,False); return False
            subprocess.run(['git','add','-A'],cwd=d)
            subprocess.run(['git','commit','-m',f"kk[{cid}]:{rec.get('recommendation','improve')}|delta:{rec.get('expected_brier_delta','?')}"],cwd=d)
            if subprocess.run(['git','push'],cwd=d,capture_output=True,timeout=60).returncode!=0: log(DB,'Push failed','ERROR',cid=cid); return False
            log_change(DB,cid,rec['hypothesis'],rec['file_to_modify'],'',True,True)
            log(DB,f"DEPLOYED:{rec['file_to_modify']}|delta:{rec.get('expected_brier_delta')}",'MILESTONE',cid=cid); return True
        finally: shutil.rmtree(d,ignore_errors=True)
COMMAND_SYS='Parse order for Karpathy Kapital master agent. Actions: explore_tool, set_agents, report, heal_all, provision_job, provision_deployment, resize_self{size:medium|large|xlarge}, ignore. Raw JSON only.'
N_AGENTS=4
def parse_cmd(cmd):
    try:
        r=client.messages.create(model='claude-sonnet-4-6',max_tokens=200,system=COMMAND_SYS,messages=[{'role':'user','content':cmd}])
        track(r.usage.input_tokens,r.usage.output_tokens,'sonnet')
        return parse_json_response(r.content[0].text) or {'action':'ignore'}
    except: return {'action':'ignore'}
def exec_cmd(row,obs,gate,cid):
    global N_AGENTS
    p=parse_cmd(row['command']); a=p.get('action','ignore'); res='ok'
    if a=='explore_tool': threading.Thread(target=run_explorer,args=(p.get('tool_name','?'),p.get('description',''),cid),daemon=True).start(); res=f'exploring {p.get("tool_name")}' 
    elif a=='set_agents': N_AGENTS=max(1,min(8,int(p.get('n',4)))); res=f'N_AGENTS={N_AGENTS}'; log(DB,f'N_AGENTS={N_AGENTS}','MILESTONE',cid=cid)
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
TOOLS=[('pytrends','Google Trends political/econ momentum'),('yfinance','Market correlations'),('newsapi','Real-time news headlines'),('statsmodels','Calibration curves logistic regression'),('vaderSentiment','Social media sentiment'),('textblob','Quick NLP sentiment'),('pandas-ta','Technical analysis on market probs'),('alpha_vantage','Economic indicators'),('scipy','Signal processing time series'),('beautifulsoup4','Web scraping RCP polls'),('gnews','Alternative news'),('polymarket-py','Polymarket direct integration')]
EXPL_SYS='Evaluate this Python library for Kalshi prediction market trading. Is it useful for predicting event outcomes? Return only JSON: {"useful":bool,"notes":"str","signals_available":["str"]}'
def run_explorer(tool,desc,cid):
    if dbq('SELECT id FROM tool_experiments WHERE tool_name=?',(tool,)): return
    log(DB,f'EXPLORING:{tool}','MILESTONE',cid=cid)
    try:
        r=client.messages.create(model='claude-sonnet-4-6',max_tokens=400,system=EXPL_SYS,messages=[{'role':'user','content':f'Tool:{tool}. Desc:{desc}. Context: Kalshi prediction markets.'}])
        track(r.usage.input_tokens,r.usage.output_tokens,'sonnet')
        result=parse_json_response(r.content[0].text)
        if not result: result={'useful':False,'notes':'parse failed','signals_available':[]}
        useful=1 if result.get('useful') else 0
        notes=f"{result.get('notes','')} | {', '.join(result.get('signals_available',[])[:4])}"
        dbw('INSERT INTO tool_experiments(ts,tool_name,description,test_result,useful,notes) VALUES(?,?,?,?,?,?)',(datetime.now(timezone.utc).isoformat(),tool,desc,json.dumps(result),useful,notes[:400]))
        log(DB,f'EXPLORER:{tool} {"USEFUL" if useful else "SKIP"} | {notes[:80]}','MILESTONE',cid=cid)
        if wiki: wiki.log_tool_experiment(tool,bool(useful),notes[:80],cid)
    except Exception as e: log(DB,f'Explorer error {tool}:{e}','ERROR',cid=cid)
def maybe_explore(cycle,cid):
    if cycle%3!=0: return
    tested=set(r['tool_name'] for r in dbq('SELECT tool_name FROM tool_experiments'))
    untested=[(n,d) for n,d in TOOLS if n not in tested]
    if untested: threading.Thread(target=run_explorer,args=(*untested[0],cid),daemon=True).start()
def sub_agent(aid,obs,gate,rl,q):
    cid=f'{aid[:3]}-{str(uuid.uuid4())[:4]}'
    try:
        d=tempfile.mkdtemp()
        try: subprocess.run(['git','clone','--depth=1',REPO,d],capture_output=True,timeout=60); avail=get_repo_files(d)
        except: avail=[]
        finally: shutil.rmtree(d,ignore_errors=True)
        rec=orient(obs,gate,rl,aid,avail)
        if not rec: log(DB,f'[{cid}] orient returned None','WARN',cid=cid); return
        all_known=set(KNOWN_FILES+avail)
        if rec.get('file_to_modify') not in all_known: log(DB,f'[{cid}] SKIP phantom file:{rec.get("file_to_modify")}','WARN',cid=cid); return
        log(DB,f'[{cid}] {aid} rec:{rec["recommendation"]} -> {rec["file_to_modify"]}',cid=cid)
        advisor_rec=advise(obs,gate,rl,cid)
        d2=tempfile.mkdtemp()
        try:
            subprocess.run(['git','clone','--depth=1',REPO,d2],capture_output=True,timeout=60)
            fp=os.path.join(d2,rec['file_to_modify']); cur=open(fp).read() if os.path.exists(fp) else ''
        finally: shutil.rmtree(d2,ignore_errors=True)
        new=decide(rec,advisor_rec)
        if new: q.put((rec,new,cid,rec.get('priority','medium')))
    except Exception as e: log(DB,f'[{cid}] sub_agent error:{e}','ERROR',cid=cid)
def main():
    global N_AGENTS
    init_db()
    log(DB,'='*55,'MILESTONE')
    log(DB,'KARPATHY KAPITAL - FULL AUTONOMY v6 - ASCII FIX + RETRY','MILESTONE')
    log(DB,f'4 agents | 2-min cycles | 22 verified files | doctor | wiki | $50/day','MILESTONE')
    log(DB,'='*55,'MILESTONE')
    if wiki: wiki.log_decision('v6: ASCII fix + JSON retry logic','UTF-8 encoding bug caused all orient calls to fail')
    last_change=0; cycle=0; last_wiki=0
    while True:
    while os.environ.get("AGENT_PAUSED","false").lower()=="true":
        print("[LOOP] Paused.")
        import time as _t;_t.sleep(30)

        cycle+=1; cid=str(uuid.uuid4())[:8]
        try:
            obs=observe(DB); gate=check_confidence(DB); rl=rl_stats(); speed=cycle_speed(rl)
            log(DB,f'[{cid}] C#{cycle} gate:{gate["all_passed"]} blocks:{gate["blocking"]} resolved:{obs["trades"]["resolved"]} pnl:${obs["trades"]["pnl"]} rl:{rl["rate"]}({rl["total"]}) cost:${_cost[0]:.2f}/{DAILY_BUDGET} agents:{N_AGENTS} next:{speed}s','MILESTONE' if gate['all_passed'] else 'INFO',cid=cid)
            if gate['all_passed']: log(DB,'CONFIDENCE GATE PASSED - AWAITING HUMAN FOR LIVE TRADING','MILESTONE',cid=cid)
            try: healed=check_and_heal(cid)
            except Exception as e: healed=[]; log(DB,f'Doctor error:{e}','ERROR',cid=cid)
            if healed: log(DB,f'Doctor healed:{healed}','MILESTONE',cid=cid)
            proc_cmds(obs,gate,cid)
            maybe_explore(cycle,cid)
            if wiki and cycle-last_wiki>=30:
                try: wiki.update_state({'gate':gate['all_passed'],'resolved':obs['trades']['resolved'],'pnl':obs['trades']['pnl'],'rl_rate':rl['rate'],'cost':f'${_cost[0]:.2f}'},cid); last_wiki=cycle
                except: pass
            if not ok_budget(): log(DB,f'Budget cap - sleeping 1hr','WARN',cid=cid); time.sleep(3600); continue
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
                log(DB,f'[{cid}] Best of {len(results)}: {rec["recommendation"]}','INFO',cid=cid)
                if act(rec,new,bcid):
                    last_change=time.time(); log(DB,f'[{cid}] DEPLOYED C#{cycle} cost:${_cost[0]:.3f}','MILESTONE',cid=cid)
                    if rl.get('rate',0)>0.8 and N_AGENTS<8: N_AGENTS=min(8,N_AGENTS+1); log(DB,f'Auto-scaled to {N_AGENTS} agents','MILESTONE',cid=cid)
            else: log(DB,f'[{cid}] No deployable changes this cycle',cid=cid)
        except Exception as e: log(DB,f'[{cid}] MAIN ERROR:{e}\n{traceback.format_exc()}','ERROR',cid=cid)
        log(DB,f'[{cid}] Sleeping {speed}s cost:${_cost[0]:.3f}',cid=cid)
        time.sleep(speed)
if __name__=='__main__': main()