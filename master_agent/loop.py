#!/usr/bin/env python3
"""Karpathy Kapital Master Agent — Full Autonomy Mode
- 4 parallel sub-agents from boot
- 2-minute base cycle (compresses toward 1-min floor)
- Full Saturn Cloud API: provision resources, resize hardware, create sub-agent deployments
- Self-upgrade: can change own instance size when throughput demands it
- Doctor agent heals all infrastructure every cycle
- Command queue: takes orders from dashboard/chat within 2 min
- Explorer agents: tests tools from curated list, no reinventing wheels
- Wiki: logs decisions, calibration, experiments to WIKI.md
- Budget: $50/day hard cap — only non-negotiable constraint
"""
import os,sys,time,subprocess,shutil,tempfile,uuid,json,traceback,re
import threading,sqlite3,requests
from queue import Queue
from datetime import datetime,timezone,timedelta
sys.path.insert(0,'/home/jovyan/workspace/prediction-agent')
import anthropic
from master_agent.observe import observe
from master_agent.confidence_gate import check_confidence
from master_agent.changelog import log,log_change
from master_agent import safeguards
try: from master_agent.doctor import check_and_heal
except ImportError: check_and_heal=lambda cid: []
try: from master_agent import wiki
except ImportError: wiki=None

DB='/home/jovyan/shared/sm/prediction-agent-db/prediction_agent.db'
PAT=os.environ.get('GITHUB_PAT','')
REPO=f'https://{PAT}@github.com/msmettigithub/prediction-agent.git'
SATURN=os.environ.get('SATURN_API_TOKEN','')
BASE='https://app.community.saturnenterprise.io/api'
SELF_ID='18efeea066bc4d828b984ec1d752d131'  # master-agent deployment ID
client=anthropic.Anthropic()
_lock=threading.Lock()
_git_lock=threading.Lock()

# ── Budget ───────────────────────────────────────────────
DAILY_BUDGET=50.0
_cost=[0.0]; _day=[datetime.now(timezone.utc).date().isoformat()]
def track(i,o,m):
    t=datetime.now(timezone.utc).date().isoformat()
    with _lock:
        if t!=_day[0]: _cost[0]=0.0; _day[0]=t
        _cost[0]+=(i*0.015+o*0.045)/1000 if 'opus' in m else (i*0.003+o*0.009)/1000
        return _cost[0]
def ok_budget(): return _cost[0]<DAILY_BUDGET*0.9

# ── DB ───────────────────────────────────────────────────
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

# ── RL stats ─────────────────────────────────────────────
def rl_stats():
    rows=dbq('SELECT ok,deployed FROM agent_changes ORDER BY ts DESC LIMIT 30')
    n=len(rows); dep=sum(1 for r in rows if r['deployed'])
    return {'n':n,'deployed':dep,'rate':round(dep/max(n,1),2),'n_total':len(dbq('SELECT id FROM agent_changes'))}

# ── Dynamic cycle speed ───────────────────────────────────
BASE_CYCLE=120; MIN_CYCLE=60  # 2-min base, 1-min floor
def cycle_speed(gate,rl):
    s=BASE_CYCLE
    if rl.get('rate',0)>0.7: s=int(s*0.7)
    if _cost[0]/DAILY_BUDGET>0.7: s=BASE_CYCLE*2
    return max(MIN_CYCLE,s)

# ── Saturn Cloud API ──────────────────────────────────────
def saturn(method,path,body=None):
    if not SATURN: return {}
    try:
        h={'Authorization':f'token {SATURN}','Content-Type':'application/json'}
        r=getattr(requests,method)(f'{BASE}{path}',json=body,headers=h,timeout=15)
        return r.json()
    except: return {}

# ── Saturn provisioning — master agent creates its own resources ─
IMG='9879fc989f054272903cd4afd5e520bd'
SECRETS={'GITHUB_PAT':'3b8f8e0ea8a54b3fa85e210e3e94d79b',
         'ANTHROPIC_API_KEY':'46a084f1474f4b3e8db0fbe5fbeb2f1d',
         'SATURN_API_TOKEN':'e0e6ce81230d456d97077984c2135f09',
         'KALSHI_API_KEY':'6efa392feb144083aa8eec4cee65ad2c',
         'FRED_API_KEY':'25a493cef4e540b592259eaf410014d4',
         'KALSHI_PRIVATE_KEY':'a2c72eae85324f7a91534b5ddae808a8',
         'BANKROLL':'9a9ea2608f514ef5a9d665e5227f53b3',
         'EDGE_THRESHOLD':'d6f9deb20e294380ae60816b37b0a49c',
         'LIVE_TRADING_ENABLED':'f7ddf60450d94f35a03c42559ca23629',
         'MAX_LIVE_BANKROLL':'706c4f79d15f49728748aa145c1f2a0b',
         'MAX_SINGLE_BET':'c66a5d68b7094b00b0818d56fe1a54e7',
         'MOCK_TOOLS':'b43c2c1429ac41859ff507acfd104330'}
SHARED_FOLDER='ab695f5218d6402cb9289b821764c370'
GIT_REPO='9929090f1a4a48f6904f947210b94708'

def attach_secrets(resource_type,rid,secret_names=None):
    """Attach standard secrets to a new resource."""
    names=secret_names or list(SECRETS.keys())
    results=[]
    for name,sid in SECRETS.items():
        if name not in names: continue
        att_type='file' if name=='KALSHI_PRIVATE_KEY' else 'environment_variable'
        loc='/home/jovyan/.kalshi/private_key.pem' if name=='KALSHI_PRIVATE_KEY' else name
        r=saturn('post',f'/api/{resource_type}s/{rid}/secrets',
            {'secret_id':sid,'location':loc,'attachment_type':att_type})
        results.append({'name':name,'ok':bool(r.get('id'))})
    return results

def provision_job(name,command,schedule=None,packages='anthropic requests',cid=None):
    """Create a new Saturn Cloud job and attach all secrets."""
    body={'name':name,'command':command,'image_tag_id':IMG,'instance_size':'medium',
          'working_dir':'/home/jovyan/workspace/prediction-agent',
          'extra_packages':{'pip':packages,'as_requirements_txt':False,'use_mamba':False},
          'start_script':'mkdir -p ~/.kalshi && git config --global user.email kk@saturncloud.io && git config --global user.name "KK Agent"'}
    if schedule: body['cron_schedule_options']={'schedule':schedule}
    r=saturn('post','/api/jobs',body)
    if not r.get('id'): log(DB,f'Failed to create job {name}: {r}','ERROR',cid=cid); return None
    jid=r['id']
    attach_secrets('job',jid)
    saturn('post',f'/api/external_repo_attachments',{'external_repo_id':GIT_REPO,'job_id':jid,'path':'/home/jovyan/workspace/prediction-agent'})
    saturn('post',f'/api/shared_folder_attachments',{'shared_folder_id':SHARED_FOLDER,'job_id':jid,'path':'/home/jovyan/shared/sm/prediction-agent-db'})
    saturn('post',f'/api/jobs/{jid}/start',{})
    log(DB,f'Provisioned job {name} [{jid}]','MILESTONE',cid=cid)
    return jid

def provision_deployment(name,command,packages='anthropic requests',cid=None):
    """Create a new always-on Saturn Cloud deployment."""
    body={'name':name,'command':command,'image_tag_id':IMG,'instance_size':'medium',
          'working_dir':'/home/jovyan/workspace/prediction-agent',
          'extra_packages':{'pip':packages,'as_requirements_txt':False,'use_mamba':False},
          'start_script':'mkdir -p ~/.kalshi && git config --global user.email kk@saturncloud.io && git config --global user.name "KK Agent"'}
    r=saturn('post','/api/deployments',body)
    if not r.get('id'): log(DB,f'Failed to create deployment {name}: {r}','ERROR',cid=cid); return None
    did=r['id']
    attach_secrets('deployment',did)
    saturn('post','/api/external_repo_attachments',{'external_repo_id':GIT_REPO,'deployment_id':did,'path':'/home/jovyan/workspace/prediction-agent'})
    saturn('post','/api/shared_folder_attachments',{'shared_folder_id':SHARED_FOLDER,'deployment_id':did,'path':'/home/jovyan/shared/sm/prediction-agent-db'})
    saturn('post',f'/api/deployments/{did}/start',{})
    log(DB,f'Provisioned deployment {name} [{did}]','MILESTONE',cid=cid)
    return did

def resize_self(new_size,cid=None):
    """Self-upgrade: change own instance size. Takes effect on next restart."""
    r=saturn('patch',f'/api/deployments/{SELF_ID}',{'instance_size':new_size})
    if r.get('instance_size')==new_size:
        log(DB,f'Resized self to {new_size} — will apply on next restart','MILESTONE',cid=cid)
        if wiki: wiki.log_decision(f'Resized master-agent to {new_size}','throughput optimization',cid)
        return True
    return False

# ── Command Queue ─────────────────────────────────────────
COMMAND_SYS="""Command interpreter for Karpathy Kapital master agent.
Parse the order and return a JSON action plan.
Actions: explore_tool, set_agents, run_eval, improve_file, report,
         heal_all, provision_job, provision_deployment, resize_self,
         upgrade_hardware, ignore.
provision_job: {action, name, command, schedule?}
provision_deployment: {action, name, command}
resize_self: {action, size} — valid: medium, large, xlarge
upgrade_hardware: alias for resize_self
Raw JSON only."""

N_AGENTS=4  # Start at full speed

def parse_command(cmd):
    try:
        r=client.messages.create(model='claude-sonnet-4-6',max_tokens=300,system=COMMAND_SYS,
            messages=[{'role':'user','content':cmd}])
        track(r.usage.input_tokens,r.usage.output_tokens,'sonnet')
        t=re.sub(r'^\s*`{3}(?:json)?\s*','',r.content[0].text.strip())
        return json.loads(re.sub(r'\s*`{3}\s*$','',t).strip())
    except: return {'action':'ignore'}

def execute_command(row,obs,gate,cid):
    global N_AGENTS
    plan=parse_command(row['command']); action=plan.get('action','ignore'); result='ok'
    if action=='explore_tool':
        threading.Thread(target=run_explorer,args=(plan.get('tool_name','?'),plan.get('description',''),cid),daemon=True).start()
        result=f'Explorer launched: {plan.get("tool_name")}'
    elif action=='set_agents':
        N_AGENTS=max(1,min(8,int(plan.get('n',4))))
        result=f'N_AGENTS={N_AGENTS}'; log(DB,f'N_AGENTS→{N_AGENTS}','MILESTONE',cid=cid)
    elif action=='heal_all':
        healed=check_and_heal(cid); result=f'Healed:{healed}'
    elif action=='report':
        rl=rl_stats()
        result=f'gate:{gate["all_passed"]} resolved:{obs["trades"]["resolved"]} pnl:${obs["trades"]["pnl"]} rl:{rl["rate"]} cost:${_cost[0]:.2f}'
        log(DB,f'REPORT: {result}','MILESTONE',cid=cid)
    elif action in ('resize_self','upgrade_hardware'):
        size=plan.get('size','large')
        result=f'Resizing to {size}: {resize_self(size,cid)}'
    elif action=='provision_job':
        jid=provision_job(plan.get('name','kk-job'),plan.get('command','echo done'),plan.get('schedule'),cid=cid)
        result=f'Created job {jid}'
    elif action=='provision_deployment':
        did=provision_deployment(plan.get('name','kk-worker'),plan.get('command','python -m master_agent.loop'),cid=cid)
        result=f'Created deployment {did}'
    elif action=='ignore': result='not understood'
    dbw('UPDATE agent_commands SET status=?,result=?,executed_at=? WHERE id=?',('done',result[:200],datetime.now(timezone.utc).isoformat(),row['id']))

def process_commands(obs,gate,cid):
    for cmd in dbq("SELECT * FROM agent_commands WHERE status='pending' ORDER BY ts LIMIT 5"):
        try: execute_command(cmd,obs,gate,cid)
        except Exception as e: dbw('UPDATE agent_commands SET status=?,result=? WHERE id=?',('error',str(e),cmd['id']))

# ── Tool Explorer ─────────────────────────────────────────
TOOLS=[('pytrends','Google Trends for political/economic momentum'),
       ('yfinance','Yahoo Finance market correlations'),
       ('newsapi','Real-time news headlines'),
       ('statsmodels','Calibration curves, logistic regression'),
       ('vaderSentiment','Social media sentiment'),
       ('textblob','Quick NLP sentiment'),
       ('pandas-ta','Technical analysis on market probs'),
       ('alpha_vantage','Economic indicators'),
       ('scipy','Signal processing, time series'),
       ('beautifulsoup4','Web scraping: RCP polls, odds aggregators'),
       ('gnews','Alternative news API'),
       ('polymarket-py','Polymarket direct integration')]

EXPLORER_SYS='Evaluate this Python library for Kalshi prediction market trading. Test if useful for event outcome signals. JSON: {useful:bool,notes:str,signals_available:[str]}'

def run_explorer(tool,desc,cid):
    if dbq('SELECT id FROM tool_experiments WHERE tool_name=?',(tool,)): return
    log(DB,f'EXPLORING: {tool}','MILESTONE',cid=cid)
    try:
        r=client.messages.create(model='claude-sonnet-4-6',max_tokens=400,system=EXPLORER_SYS,
            messages=[{'role':'user','content':f'Tool:{tool} Desc:{desc}'}])
        track(r.usage.input_tokens,r.usage.output_tokens,'sonnet')
        t=re.sub(r'^\s*`{3}(?:json)?\s*','',r.content[0].text.strip())
        res=json.loads(re.sub(r'\s*`{3}\s*$','',t).strip())
        useful=1 if res.get('useful') else 0
        notes=f"{res.get('notes','')} | Signals: {', '.join(res.get('signals_available',[])[:5])}"
        dbw('INSERT INTO tool_experiments(ts,tool_name,description,test_result,useful,notes) VALUES(?,?,?,?,?,?)',
            (datetime.now(timezone.utc).isoformat(),tool,desc,json.dumps(res),useful,notes[:400]))
        log(DB,f'EXPLORER: {tool} → {"USEFUL" if useful else "SKIP"}','MILESTONE',cid=cid)
        if wiki: wiki.log_tool_experiment(tool,bool(useful),notes[:100],cid)
    except Exception as e: log(DB,f'Explorer error {tool}:{e}','ERROR',cid=cid)

def maybe_explore(cycle,cid):
    if cycle%3!=0: return
    tested=set(r['tool_name'] for r in dbq('SELECT tool_name FROM tool_experiments'))
    untested=[(n,d) for n,d in TOOLS if n not in tested]
    if untested: threading.Thread(target=run_explorer,args=(*untested[0],cid),daemon=True).start()

# ── Improvement Sub-Agents ────────────────────────────────
ORIENT_SYS="""Karpathy Kapital autonomous improvement agent.
Mandate: maximize Brier score via RL-style iteration.
Prompt=model. Eval=loss. Resolved trades=training data.
ONE specific testable change to model/, scanner/, tools/, database/ ONLY.
NEVER: live/, master_agent/, .env, CLAUDE.md.
Consult tool_experiments — use proven libraries, skip untested ones.
Bold, concrete, traceable. Raw JSON: {recommendation,file_to_modify,hypothesis,change_description,priority,expected_brier_delta}"""
CODE_SYS='Senior Python engineer. Complete new file only. No markdown, no backticks.'

def orient(obs,gate,rl,aid):
    if not ok_budget(): return None
    tools=json.dumps([{'t':r['tool_name'],'ok':bool(r['useful'])} for r in dbq('SELECT * FROM tool_experiments LIMIT 10')])
    ctx=json.dumps({'gate':gate['all_passed'],'blocking':gate['blocking'],
                   'metrics':gate['metrics'],'trades':obs['trades'],'rl':rl,
                   'agent':aid,'budget_pct':round(_cost[0]/DAILY_BUDGET*100),'tools':tools})
    r=client.messages.create(model='claude-opus-4-6',max_tokens=600,system=ORIENT_SYS,messages=[{'role':'user','content':ctx}])
    track(r.usage.input_tokens,r.usage.output_tokens,'opus')
    t=re.sub(r'^\s*`{3}(?:json)?\s*','',r.content[0].text.strip())
    return json.loads(re.sub(r'\s*`{3}\s*$','',t).strip())

def decide(rec):
    if not ok_budget(): return None
    r=client.messages.create(model='claude-sonnet-4-6',max_tokens=4000,system=CODE_SYS,
        messages=[{'role':'user','content':f"Change:{rec['change_description']}\nHyp:{rec['hypothesis']}\nFile:{rec['file_to_modify']}"}])
    track(r.usage.input_tokens,r.usage.output_tokens,'sonnet')
    return r.content[0].text.strip()

def act(rec,new,cid):
    with _git_lock:
        d=tempfile.mkdtemp()
        try:
            if subprocess.run(['git','clone','--depth=1',REPO,d],capture_output=True,text=True,timeout=120).returncode!=0: return False
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
            subprocess.run(['git','commit','-m',f"kk[{cid}]:{rec['recommendation']}"],cwd=d)
            if subprocess.run(['git','push'],cwd=d,capture_output=True,timeout=60).returncode!=0: return False
            log_change(DB,cid,rec['hypothesis'],rec['file_to_modify'],'',True,True)
            log(DB,f"DEPLOYED:{rec['file_to_modify']}|delta:{rec.get('expected_brier_delta')}|cost:${_cost[0]:.3f}",'MILESTONE',cid=cid)
            return True
        finally: shutil.rmtree(d,ignore_errors=True)

def sub_agent(aid,obs,gate,rl,q):
    cid=f'{aid[:3]}-{str(uuid.uuid4())[:4]}'
    try:
        rec=orient(obs,gate,rl,aid)
        if not rec: return
        log(DB,f'[{cid}] {aid}:{rec["recommendation"]}',cid=cid)
        d=tempfile.mkdtemp()
        try:
            subprocess.run(['git','clone','--depth=1',REPO,d],capture_output=True,timeout=60)
            fp=os.path.join(d,rec['file_to_modify'])
            cur=open(fp).read() if os.path.exists(fp) else ''
        finally: shutil.rmtree(d,ignore_errors=True)
        new=decide(rec)
        if new: q.put((rec,new,cid,rec.get('priority','medium')))
    except Exception as e: log(DB,f'[{cid}] sub-agent error:{e}','ERROR',cid=cid)

# ── Main Loop ────────────────────────────────────────────
def main():
    global N_AGENTS
    init_db()
    log(DB,'='*55,'MILESTONE')
    log(DB,'KARPATHY KAPITAL — FULL AUTONOMY MODE','MILESTONE')
    log(DB,f'4 agents | 2-min cycles | full Saturn provisioning | doctor heals every cycle','MILESTONE')
    log(DB,f'Budget: ${DAILY_BUDGET}/day | Rate: 1 push/hr | Self-healing: ON | Wiki: ON','MILESTONE')
    log(DB,'='*55,'MILESTONE')
    if wiki: wiki.log_decision('Full autonomy mode activated','user instruction: go full speed',None)
    last_change=0; cycle=0; last_wiki_update=0
    while True:
        cycle+=1; cid=str(uuid.uuid4())[:8]
        try:
            obs=observe(DB); gate=check_confidence(DB); rl=rl_stats()
            speed=cycle_speed(gate,rl)
            log(DB,
                f'[{cid}] C#{cycle} gate:{gate["all_passed"]} blocks:{gate["blocking"]} '
                f'resolved:{obs["trades"]["resolved"]} pnl:${obs["trades"]["pnl"]} '
                f'rl:{rl["rate"]}({rl["n_total"]} total) cost:${_cost[0]:.2f}/{DAILY_BUDGET} '
                f'agents:{N_AGENTS} speed:{speed}s',
                'MILESTONE' if gate['all_passed'] else 'INFO',cid=cid)
            if gate['all_passed']:
                log(DB,'CONFIDENCE GATE PASSED — ALL 6 CHECKS — READY FOR LIVE TRADING — AWAITING HUMAN','MILESTONE',cid=cid)
            # Doctor — heal all watched resources every cycle
            try:
                healed=check_and_heal(cid)
                if healed: log(DB,f'Doctor healed:{healed}','MILESTONE',cid=cid)
            except Exception as e: log(DB,f'Doctor error:{e}','ERROR',cid=cid)
            # Process orders from chat/dashboard
            process_commands(obs,gate,cid)
            # Tool explorer every 3rd cycle
            maybe_explore(cycle,cid)
            # Update wiki every 30 cycles (~1hr)
            if wiki and cycle-last_wiki_update>=30:
                try:
                    wiki.update_state({'gate':gate['all_passed'],'resolved':obs['trades']['resolved'],
                                       'pnl':obs['trades']['pnl'],'rl_rate':rl['rate'],'cycle':cycle,
                                       'daily_cost':f"${_cost[0]:.2f}"},cid)
                    last_wiki_update=cycle
                except: pass
            # Budget check
            if not ok_budget():
                log(DB,f'Budget ${_cost[0]:.2f}/{DAILY_BUDGET} — sleeping 1hr','WARN',cid=cid)
                time.sleep(3600); continue
            # Rate limit: 1 code push per hour (quality over quantity)
            since=time.time()-last_change
            if since<3600:
                wait=min(speed,int(3600-since))
                log(DB,f'[{cid}] Rate limit {wait}s remaining',cid=cid)
                time.sleep(wait); continue
            # Launch N parallel sub-agents simultaneously
            log(DB,f'[{cid}] Launching {N_AGENTS} parallel sub-agents',cid=cid)
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
                log(DB,f'[{cid}] Acting on best of {len(results)}: {rec["recommendation"]}',cid=cid)
                if act(rec,new,bcid):
                    last_change=time.time()
                    log(DB,f'[{cid}] CHANGE DEPLOYED — cycle#{cycle}','MILESTONE',cid=cid)
                    # Auto-scale: increase agents if consistently succeeding
                    if rl.get('rate',0)>0.8 and N_AGENTS<8:
                        N_AGENTS=min(8,N_AGENTS+1)
                        log(DB,f'Auto-scaled to {N_AGENTS} agents (deploy_rate={rl["rate"]})','MILESTONE',cid=cid)
            else:
                log(DB,f'[{cid}] No deployable changes from {N_AGENTS} agents this cycle',cid=cid)
        except Exception as e:
            log(DB,f'[{cid}] MAIN ERROR:{e}\n{traceback.format_exc()}','ERROR',cid=cid)
        log(DB,f'[{cid}] Sleeping {speed}s | cost:${_cost[0]:.3f}/{DAILY_BUDGET}',cid=cid)
        time.sleep(speed)

if __name__=='__main__': main()