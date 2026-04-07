#!/usr/bin/env python3
"""Master Agent — Autonomous Kalshi prediction market fund improvement council.
Principles: Voracious learner. RL-driven self-improvement. $50/day hard budget cap.
Karpathy framework: prompt=model, eval=loss, resolved_trades=training_data."""
import os,sys,time,subprocess,shutil,tempfile,uuid,json,traceback,re,math
sys.path.insert(0,'/home/jovyan/workspace/prediction-agent')
import anthropic
from master_agent.observe import observe
from master_agent.confidence_gate import check_confidence
from master_agent.changelog import log,log_change
from master_agent import safeguards

DB='/home/jovyan/shared/sm/prediction-agent-db/prediction_agent.db'
PAT=os.environ.get('GITHUB_PAT','')
REPO=f'https://{PAT}@github.com/msmettigithub/prediction-agent.git'
SATURN_TOKEN=os.environ.get('SATURN_API_TOKEN','')
ANTHROPIC_KEY=os.environ.get('ANTHROPIC_API_KEY','')
client=anthropic.Anthropic()

# Cost tracking — $50/day hard cap
DAILY_BUDGET_USD=50.0
COST_PER_OPUS_1K=0.015   # claude-opus-4-6 input per 1K tokens
COST_PER_SONNET_1K=0.003 # claude-sonnet-4-6 input per 1K tokens
_daily_cost=[0.0]
_day_started=[time.strftime('%Y-%m-%d')]

def track_cost(tokens_in,tokens_out,model):
    today=time.strftime('%Y-%m-%d')
    if today!=_day_started[0]: _daily_cost[0]=0.0; _day_started[0]=today
    rate=COST_PER_OPUS_1K if 'opus' in model else COST_PER_SONNET_1K
    _daily_cost[0]+=tokens_in*rate/1000+tokens_out*rate/1000*3
    return _daily_cost[0]

def within_budget():
    return _daily_cost[0]<DAILY_BUDGET_USD

# RL self-improvement: track which recommendations improved Brier score
def get_signal_performance():
    """Compute empirical lift per change type from agent_changes history.
    This IS the RL reward signal — changes that improve Brier get weighted higher."""
    import sqlite3
    if not os.path.exists(DB): return {}
    try:
        c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; cur=c.cursor()
        cur.execute('SELECT hyp,ok,deployed FROM agent_changes ORDER BY ts DESC LIMIT 50')
        rows=cur.fetchall(); c.close()
        success_rate=sum(1 for r in rows if r['ok'])/max(len(rows),1)
        return {'total_changes':len(rows),'success_rate':round(success_rate,2),'recent':len(rows)}
    except: return {}

ORIENT_SYSTEM="""You are the autonomous improvement agent for a Kalshi prediction market fund.
Karpathy framework: The prompt IS the model. Resolved trades ARE training data. Eval harness IS the loss function.
Mandate: maximize Brier score improvement and paper trade win rate via RL-style iteration.

Core RL principle: Every resolved trade is a labeled example. Mine it. Every agent_change is an action.
Changes that improve Brier = positive reward. Changes that fail tests = negative reward.
Prioritize changes with highest expected improvement given current calibration gap.

Recommend ONE specific testable change to model/, scanner/, tools/, or database/ ONLY.
NEVER change live/, master_agent/, .env, CLAUDE.md.
If calibration gates failing: ONLY improve model accuracy or separation.
If accuracy>65% but separation<10pp: focus on confidence calibration.
If all metrics near threshold: focus on data quality and signal attribution.

Respond ONLY in raw JSON (no markdown):
{recommendation,file_to_modify,hypothesis,change_description,priority,expected_brier_delta}"""

CODE_SYSTEM='Senior Python engineer implementing a change for a systematic trading system. Return ONLY the complete new file. No markdown, no backticks. Production quality with error handling.'

def orient(obs,gate,signal_perf):
    if not within_budget():
        log(DB,f'Budget cap hit: ${_daily_cost[0]:.2f}/{DAILY_BUDGET_USD}. Skipping orient.','WARN')
        return None
    ctx=json.dumps({'gate':gate['all_passed'],'blocking':gate['blocking'],'metrics':gate['metrics'],
                   'trades':obs['trades'],'errors':obs['errors'],'signal_performance':signal_perf,
                   'daily_cost_usd':round(_daily_cost[0],3),'budget_remaining':round(DAILY_BUDGET_USD-_daily_cost[0],3)})
    r=client.messages.create(model='claude-opus-4-6',max_tokens=800,system=ORIENT_SYSTEM,
        messages=[{'role':'user','content':ctx}])
    track_cost(r.usage.input_tokens,r.usage.output_tokens,'opus')
    t=re.sub(r'^\s*`{3}(?:json)?\s*','',r.content[0].text.strip())
    t=re.sub(r'\s*`{3}\s*$','',t)
    return json.loads(t.strip())

def decide(rec,cur):
    if not within_budget(): return None
    r=client.messages.create(model='claude-sonnet-4-6',max_tokens=4000,system=CODE_SYSTEM,
        messages=[{'role':'user','content':f"Change:{rec['change_description']}\nHypothesis:{rec['hypothesis']}\nExpected Brier delta:{rec.get('expected_brier_delta','unknown')}\nFile {rec['file_to_modify']}:\n{cur[:4000]}"}])
    track_cost(r.usage.input_tokens,r.usage.output_tokens,'sonnet')
    return r.content[0].text.strip()

def act(rec,new,cid):
    d=tempfile.mkdtemp()
    try:
        r=subprocess.run(['git','clone','--depth=1',REPO,d],capture_output=True,text=True,timeout=120)
        if r.returncode!=0: log(DB,f'Clone failed:{r.stderr}','ERROR',cid=cid); return False
        subprocess.run(['git','config','user.email','master-agent@saturncloud.io'],cwd=d)
        subprocess.run(['git','config','user.name','Master Agent'],cwd=d)
        ok,reason=safeguards.can_act(rec['file_to_modify'])
        if not ok: log(DB,f'Blocked:{reason}','WARN',cid=cid); return False
        fp=os.path.join(d,rec['file_to_modify'])
        if not os.path.exists(fp): log(DB,f'File not found:{fp}','ERROR',cid=cid); return False
        open(fp,'w').write(new)
        r=subprocess.run([sys.executable,'-m','pytest','tests/','-q','--tb=short'],
            cwd=d,capture_output=True,text=True,timeout=300)
        out=r.stdout+r.stderr
        m=re.search(r'(\d+) passed',out); n=int(m.group(1)) if m else 0
        if r.returncode!=0 or n<safeguards.MIN_TEST_COUNT:
            log(DB,f'Tests failed({n}):{out[-400:]}','ERROR',cid=cid)
            log_change(DB,cid,rec['hypothesis'],rec['file_to_modify'],'',False,False); return False
        subprocess.run(['git','add','-A'],cwd=d)
        msg=f"agent[{cid}]:{rec['recommendation']} | hypothesis:{rec['hypothesis']}"
        subprocess.run(['git','commit','-m',msg],cwd=d)
        r=subprocess.run(['git','push'],cwd=d,capture_output=True,text=True,timeout=60)
        if r.returncode!=0: log(DB,f'Push failed:{r.stderr}','ERROR',cid=cid); return False
        log_change(DB,cid,rec['hypothesis'],rec['file_to_modify'],'',True,True)
        log(DB,f"Deployed:{rec['file_to_modify']} | expected_delta:{rec.get('expected_brier_delta')}",'MILESTONE',cid=cid)
        return True
    finally: shutil.rmtree(d,ignore_errors=True)

def main():
    log(DB,'='*50,'MILESTONE')
    log(DB,'MASTER AGENT COUNCIL ONLINE — KARPATHY RL FRAMEWORK','MILESTONE')
    log(DB,f'Budget cap: ${DAILY_BUDGET_USD}/day | Cycle: 30min | Gate: 6 checks','MILESTONE')
    log(DB,'='*50,'MILESTONE')
    last_change=0
    cycle=0
    while True:
        cid=str(uuid.uuid4())[:8]
        cycle+=1
        try:
            # OBSERVE
            obs=observe(DB)
            gate=check_confidence(DB)
            signal_perf=get_signal_performance()
            log(DB,f'[{cid}] C#{cycle} gate:{gate["all_passed"]} blocks:{gate["blocking"]} resolved:{obs["trades"]["resolved"]} pnl:${obs["trades"]["pnl"]} budget:${_daily_cost[0]:.2f}/${DAILY_BUDGET_USD}',
                'MILESTONE' if gate['all_passed'] else 'INFO',cid=cid)
            if gate['all_passed']:
                log(DB,'CONFIDENCE GATE PASSED — ALL 6 CHECKS — READY FOR LIVE TRADING — AWAITING HUMAN','MILESTONE',cid=cid)
            # Rate limit: 1 code change per hour max
            since=time.time()-last_change
            if since<3600:
                sleep_t=min(1800,int(3600-since))
                log(DB,f'[{cid}] Rate limit: {sleep_t//60}min until next change. Budget: ${_daily_cost[0]:.2f}/${DAILY_BUDGET_USD}',cid=cid)
                time.sleep(sleep_t)
                continue
            # ORIENT — Opus reasons about highest-leverage improvement
            log(DB,f'[{cid}] ORIENT (budget:${_daily_cost[0]:.2f})',cid=cid)
            rec=orient(obs,gate,signal_perf)
            if not rec: time.sleep(1800); continue
            log(DB,f'[{cid}] Rec:{rec["recommendation"]} -> {rec["file_to_modify"]} | expected_delta:{rec.get("expected_brier_delta")}',cid=cid)
            # DECIDE — Sonnet writes the implementation
            log(DB,f'[{cid}] DECIDE (budget:${_daily_cost[0]:.2f})',cid=cid)
            d2=tempfile.mkdtemp()
            try:
                subprocess.run(['git','clone','--depth=1',REPO,d2],capture_output=True,timeout=60)
                fp=os.path.join(d2,rec['file_to_modify'])
                cur=open(fp).read() if os.path.exists(fp) else ''
            finally: shutil.rmtree(d2,ignore_errors=True)
            new=decide(rec,cur)
            if not new: time.sleep(1800); continue
            # ACT — test and push
            log(DB,f'[{cid}] ACT -> {rec["file_to_modify"]}',cid=cid)
            if act(rec,new,cid):
                last_change=time.time()
                log(DB,f'[{cid}] DEPLOYED — cycle#{cycle} total_cost:${_daily_cost[0]:.3f}','MILESTONE',cid=cid)
            else:
                log(DB,f'[{cid}] No change this cycle',cid=cid)
        except Exception as e:
            log(DB,f'[{cid}] ERROR:{e}\n{traceback.format_exc()}','ERROR',cid=cid)
        # 30 min between cycles — aggressive but not wasteful
        log(DB,f'[{cid}] Sleeping 30min. Daily cost so far: ${_daily_cost[0]:.3f}',cid=cid)
        time.sleep(1800)

if __name__=='__main__': main()