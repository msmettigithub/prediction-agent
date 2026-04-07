#!/usr/bin/env python3
"""
Karpathy Kapital Master Agent v3
Philosophy: Voracious learner. RL-driven. Aggressive iteration toward vanishing returns.

Key principles:
- The prompt IS the model. Eval harness IS the loss function.
- Every resolved trade is a labeled training example. Mine it ruthlessly.
- Parallel sub-agents run simultaneously for maximum learning velocity.
- Dynamic cycle speed: start 5min, compress to 1min as confidence grows.
- RL reward = Brier improvement. Penalize failed tests. Reward fast deployment.
- $50/day hard cap. Self-monitor and throttle if approaching limit.
- Build Saturn Cloud resources autonomously when throughput demands it.
"""
import os,sys,time,subprocess,shutil,tempfile,uuid,json,traceback,re
import threading,concurrent.futures,math,requests
from queue import Queue, Empty
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
SATURN_BASE='https://app.community.saturnenterprise.io/api'
client=anthropic.Anthropic()

# ============================================================
# BUDGET MANAGEMENT — $50/day hard cap
# ============================================================
DAILY_BUDGET=50.0
_cost=[0.0]; _day=[datetime.now(timezone.utc).date().isoformat()]
_lock=threading.Lock()

def track(inp,out,model):
    today=datetime.now(timezone.utc).date().isoformat()
    with _lock:
        if today!=_day[0]: _cost[0]=0.0; _day[0]=today
        rate=0.015 if 'opus' in model else 0.003
        _cost[0]+=inp*rate/1000+out*rate*3/1000
        return _cost[0]

def budget_ok(): return _cost[0]<DAILY_BUDGET*0.9  # 90% threshold for safety
def budget_pct(): return _cost[0]/DAILY_BUDGET

# ============================================================
# RL REWARD TRACKING — empirical signal performance
# ============================================================
import sqlite3

def rl_stats():
    try:
        c=sqlite3.connect(DB); c.row_factory=sqlite3.Row
        rows=c.execute('SELECT ok,deployed,hyp,file FROM agent_changes ORDER BY ts DESC LIMIT 30').fetchall()
        c.close()
        n=len(rows); dep=sum(1 for r in rows if r['deployed']); ok=sum(1 for r in rows if r['ok'])
        return {'n_attempts':n,'n_deployed':dep,'n_passed':ok,
                'deploy_rate':round(dep/max(n,1),2),'pass_rate':round(ok/max(n,1),2)}
    except: return {'n_attempts':0,'n_deployed':0,'deploy_rate':0,'pass_rate':0}

# ============================================================
# DYNAMIC CYCLE SPEED — compress as learning velocity grows
# ============================================================
BASE_CYCLE_SEC=300   # 5 minutes base
MIN_CYCLE_SEC=60     # 1 minute floor (vanishing returns threshold)

def get_cycle_speed(gate,rl):
    """RL-style adaptive cycle speed.
    Faster when: calibration close to passing, high deploy rate, budget available.
    Slower when: budget tight, many failures, gate far from passing."""
    blocking=len(gate.get('blocking',[]))
    deploy_rate=rl.get('deploy_rate',0)
    budget_used=budget_pct()
    # Start at 5min, compress based on performance
    speed=BASE_CYCLE_SEC
    if blocking<=2: speed=int(speed*0.6)   # Close to passing — go faster
    if deploy_rate>0.7: speed=int(speed*0.7)  # High success rate — go faster
    if budget_used>0.7: speed=int(speed*2)  # Budget tight — go slower
    return max(MIN_CYCLE_SEC, speed)

# ============================================================
# PARALLEL SUB-AGENTS — multiple simultaneous improvements
# ============================================================

ORIENT_SYS="""You are a sub-agent of Karpathy Kapital's autonomous trading fund council.
Karpathy framework: prompt=model, eval=loss, resolved_trades=training_data.
RL principle: each code change is an action. Brier improvement = reward. Failed tests = penalty.

Mandate: recommend ONE specific testable change to improve Brier score or separation.
Only touch: model/, scanner/, tools/, database/
NEVER touch: live/, master_agent/, .env, CLAUDE.md

Be concrete. Be bold. Think like Karpathy — what would move the loss function most?
Consider: base rates, calibration curve, signal attribution, momentum features, cross-market arb.

Respond in raw JSON only:
{recommendation,file_to_modify,hypothesis,change_description,priority,expected_brier_delta}"""

CODE_SYS='Implement the change. Return ONLY complete new Python file. No markdown, no backticks.'

def orient_agent(obs,gate,rl,agent_id):
    if not budget_ok(): return None
    ctx=json.dumps({'gate':gate['all_passed'],'blocking':gate['blocking'],
        'metrics':gate['metrics'],'trades':obs['trades'],'rl':rl,
        'budget_pct':round(budget_pct()*100),'agent_id':agent_id})
    r=client.messages.create(model='claude-opus-4-6',max_tokens=600,
        system=ORIENT_SYS,messages=[{'role':'user','content':ctx}])
    track(r.usage.input_tokens,r.usage.output_tokens,'opus')
    t=re.sub(r'^\s*`{3}(?:json)?\s*','',r.content[0].text.strip())
    t=re.sub(r'\s*`{3}\s*$','',t)
    return json.loads(t.strip())

def decide_agent(rec):
    if not budget_ok(): return None
    r=client.messages.create(model='claude-sonnet-4-6',max_tokens=4000,system=CODE_SYS,
        messages=[{'role':'user','content':f"Change:{rec['change_description']}\nHypothesis:{rec['hypothesis']}\nFile:{rec['file_to_modify']}"}])
    track(r.usage.input_tokens,r.usage.output_tokens,'sonnet')
    return r.content[0].text.strip()

# ============================================================
# GIT ACT — test and push
# ============================================================
_git_lock=threading.Lock()  # One push at a time

def act(rec,new,cid):
    with _git_lock:  # Serialize git operations
        d=tempfile.mkdtemp()
        try:
            r=subprocess.run(['git','clone','--depth=1',REPO,d],capture_output=True,text=True,timeout=120)
            if r.returncode!=0: log(DB,f'Clone fail:{r.stderr[:200]}','ERROR',cid=cid); return False
            subprocess.run(['git','config','user.email','kk-agent@saturncloud.io'],cwd=d)
            subprocess.run(['git','config','user.name','KK Master Agent'],cwd=d)
            ok,reason=safeguards.can_act(rec['file_to_modify'])
            if not ok: log(DB,f'Blocked:{reason}','WARN',cid=cid); return False
            fp=os.path.join(d,rec['file_to_modify'])
            if not os.path.exists(fp): log(DB,f'Not found:{fp}','ERROR',cid=cid); return False
            open(fp,'w').write(new)
            r=subprocess.run([sys.executable,'-m','pytest','tests/','-q','--tb=short'],
                cwd=d,capture_output=True,text=True,timeout=300)
            out=r.stdout+r.stderr
            m=re.search(r'(\d+) passed',out); n=int(m.group(1)) if m else 0
            if r.returncode!=0 or n<safeguards.MIN_TEST_COUNT:
                log(DB,f'Tests failed({n}):{out[-300:]}','ERROR',cid=cid)
                log_change(DB,cid,rec['hypothesis'],rec['file_to_modify'],'',False,False); return False
            subprocess.run(['git','add','-A'],cwd=d)
            subprocess.run(['git','commit','-m',
                f"kk-agent[{cid}]:{rec['recommendation']}|delta:{rec.get('expected_brier_delta','')}|cost:${_cost[0]:.3f}"],cwd=d)
            r=subprocess.run(['git','push'],cwd=d,capture_output=True,text=True,timeout=60)
            if r.returncode!=0: log(DB,f'Push fail:{r.stderr[:200]}','ERROR',cid=cid); return False
            log_change(DB,cid,rec['hypothesis'],rec['file_to_modify'],'',True,True)
            log(DB,f"DEPLOYED:{rec['file_to_modify']}|delta:{rec.get('expected_brier_delta')}|cost:${_cost[0]:.3f}",'MILESTONE',cid=cid)
            return True
        finally: shutil.rmtree(d,ignore_errors=True)

# ============================================================
# SATURN CLOUD ORCHESTRATION — self-scale when needed
# ============================================================

def saturn_get(path):
    if not SATURN: return {}
    try:
        r=requests.get(f'{SATURN_BASE}{path}',
            headers={'Authorization':f'token {SATURN}'},timeout=10)
        return r.json()
    except: return {}

def saturn_post(path,body={}):
    if not SATURN: return {}
    try:
        r=requests.post(f'{SATURN_BASE}{path}',json=body,
            headers={'Authorization':f'token {SATURN}'},timeout=10)
        return r.json()
    except: return {}

def check_saturn_resources():
    """Monitor Saturn Cloud resource health and costs.
    Self-reports status to agent_log for dashboard visibility."""
    jobs=saturn_get('/api/jobs?page_size=10')
    deps=saturn_get('/api/deployments?page_size=10')
    paper_job=next((j for j in (jobs.get('jobs') or []) if 'prediction' in j.get('name','')),None)
    dashboard=next((d for d in (deps.get('deployments') or []) if 'dashboard' in d.get('name','')),None)
    ma=next((d for d in (deps.get('deployments') or []) if d.get('name')=='master-agent'),None)
    return {
        'paper_job':paper_job.get('status') if paper_job else 'missing',
        'dashboard':dashboard.get('status') if dashboard else 'missing',
        'master_agent':ma.get('status') if ma else 'missing'
    }

# ============================================================
# PARALLEL OODA — multiple agents, shared commit queue
# ============================================================

def run_sub_agent(agent_id,obs,gate,rl,result_queue):
    """Each sub-agent independently orients and decides.
    Results go into queue. Main thread serializes the git push."""
    cid=f'{agent_id[:4]}-{str(uuid.uuid4())[:4]}'
    try:
        rec=orient_agent(obs,gate,rl,agent_id)
        if not rec: return
        log(DB,f'[{cid}] Sub-agent rec:{rec["recommendation"]}->{ rec["file_to_modify"]}',cid=cid)
        # Read current file
        d=tempfile.mkdtemp()
        try:
            subprocess.run(['git','clone','--depth=1',REPO,d],capture_output=True,timeout=60)
            fp=os.path.join(d,rec['file_to_modify'])
            cur=open(fp).read() if os.path.exists(fp) else ''
        finally: shutil.rmtree(d,ignore_errors=True)
        new=decide_agent(rec)
        if new: result_queue.put((rec,new,cid,rec.get('priority','medium')))
    except Exception as e:
        log(DB,f'[{cid}] Sub-agent error:{e}','ERROR',cid=cid)

# ============================================================
# MAIN LOOP
# ============================================================

def main():
    log(DB,'='*60,'MILESTONE')
    log(DB,'KARPATHY KAPITAL — MASTER AGENT v3 ONLINE','MILESTONE')
    log(DB,'RL framework | Parallel sub-agents | Dynamic cycle speed','MILESTONE')
    log(DB,f'Budget: ${DAILY_BUDGET}/day | Base cycle: {BASE_CYCLE_SEC}s | Min cycle: {MIN_CYCLE_SEC}s','MILESTONE')
    log(DB,'='*60,'MILESTONE')
    last_change=0
    cycle=0
    N_AGENTS=2  # Start with 2 parallel agents, can scale up

    while True:
        cycle+=1
        cid=str(uuid.uuid4())[:8]
        try:
            # OBSERVE
            obs=observe(DB)
            gate=check_confidence(DB)
            rl=rl_stats()
            resources=check_saturn_resources()
            cycle_speed=get_cycle_speed(gate,rl)

            log(DB,(
                f'[{cid}] C#{cycle} | gate:{gate["all_passed"]} blocks:{gate["blocking"]} | '
                f'resolved:{obs["trades"]["resolved"]} pnl:${obs["trades"]["pnl"]} | '
                f'rl_rate:{rl["deploy_rate"]} | cost:${_cost[0]:.2f}/{DAILY_BUDGET} | '
                f'next_cycle:{cycle_speed}s | resources:{resources}'
            ),'MILESTONE' if gate['all_passed'] else 'INFO',cid=cid)

            if gate['all_passed']:
                log(DB,'CONFIDENCE GATE PASSED — ALL 6 CHECKS GREEN — AWAITING HUMAN FOR LIVE TRADING','MILESTONE',cid=cid)

            # Budget check
            if not budget_ok():
                log(DB,f'Budget cap approached (${_cost[0]:.2f}/${DAILY_BUDGET}). Sleeping 1hr.','WARN',cid=cid)
                time.sleep(3600); continue

            # Rate limit: 1 push per hour max
            since=time.time()-last_change
            if since<3600:
                wait=min(cycle_speed,int(3600-since))
                log(DB,f'[{cid}] Waiting {wait}s (rate limit). Cost:${_cost[0]:.3f}',cid=cid)
                time.sleep(wait); continue

            # PARALLEL ORIENT+DECIDE — N agents simultaneously
            log(DB,f'[{cid}] Launching {N_AGENTS} parallel sub-agents',cid=cid)
            result_queue=Queue()
            agent_ids=[f'agent-{i}' for i in range(N_AGENTS)]
            threads=[threading.Thread(target=run_sub_agent,args=(aid,obs,gate,rl,result_queue)) for aid in agent_ids]
            for t in threads: t.start()
            for t in threads: t.join(timeout=180)  # Max 3min for parallel phase

            # ACT — take best result (highest priority) and push
            results=[]
            while not result_queue.empty():
                results.append(result_queue.get())
            if not results:
                log(DB,f'[{cid}] No valid recommendations from {N_AGENTS} agents',cid=cid)
            else:
                # Sort by priority: critical > high > medium
                pmap={'critical':0,'high':1,'medium':2,'low':3}
                results.sort(key=lambda x:pmap.get(x[0].get('priority','medium'),2))
                best_rec,best_new,best_cid,_=results[0]
                log(DB,f'[{cid}] Acting on best of {len(results)} recommendations: {best_rec["recommendation"]}',cid=cid)
                if act(best_rec,best_new,best_cid):
                    last_change=time.time()
                    # Scale up agents if consistently succeeding
                    if rl.get('deploy_rate',0)>0.8 and N_AGENTS<4:
                        N_AGENTS=min(4,N_AGENTS+1)
                        log(DB,f'Scaling up to {N_AGENTS} parallel agents (deploy_rate={rl["deploy_rate"]})','MILESTONE',cid=cid)

        except Exception as e:
            log(DB,f'[{cid}] MAIN ERROR:{e}\n{traceback.format_exc()}','ERROR',cid=cid)

        log(DB,f'[{cid}] Cycle {cycle} done. Next in {cycle_speed}s. Cost:${_cost[0]:.3f}',cid=cid)
        time.sleep(cycle_speed)

if __name__=='__main__': main()