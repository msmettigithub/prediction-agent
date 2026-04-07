#!/usr/bin/env python3
import os,sys,time,subprocess,shutil,tempfile,uuid,json,traceback,re
sys.path.insert(0,'/home/jovyan/workspace/prediction-agent')
import anthropic
from master_agent.observe import observe
from master_agent.confidence_gate import check_confidence
from master_agent.changelog import log,log_change
from master_agent import safeguards
DB='/home/jovyan/shared/sm/prediction-agent-db/prediction_agent.db'
PAT=os.environ.get('GITHUB_PAT','')
REPO=f'https://{PAT}@github.com/msmettigithub/prediction-agent.git'
client=anthropic.Anthropic()
SYS='You are the autonomous improvement agent for a Kalshi prediction market fund. Maximize Brier score and win rate. Recommend ONE testable change to model/, scanner/, tools/, or database/ ONLY. NEVER change live/, master_agent/, .env, CLAUDE.md. Raw JSON only: {recommendation,file_to_modify,hypothesis,change_description,priority}'
def orient(obs,gate):
    ctx=json.dumps({'gate':gate['all_passed'],'blocking':gate['blocking'],'metrics':gate['metrics'],'trades':obs['trades'],'errors':obs['errors']})
    r=client.messages.create(model='claude-opus-4-6',max_tokens=800,system=SYS,messages=[{'role':'user','content':ctx}])
    t=r.content[0].text.strip()
    t=re.sub(r'^\s*```(?:json)?\s*','',t); t=re.sub(r'\s*```\s*$','',t)
    return json.loads(t.strip())
def decide(rec,cur):
    r=client.messages.create(model='claude-sonnet-4-6',max_tokens=4000,system='Return ONLY the complete new Python file. No markdown.',messages=[{'role':'user','content':f"Change:{rec['change_description']}\nHypothesis:{rec['hypothesis']}\nFile {rec['file_to_modify']}:\n{cur[:4000]}"}])
    return r.content[0].text.strip()
def act(rec,new,cid):
    d=tempfile.mkdtemp()
    try:
        if subprocess.run(['git','clone','--depth=1',REPO,d],capture_output=True,timeout=120).returncode!=0: return False
        subprocess.run(['git','config','user.email','master-agent@saturncloud.io'],cwd=d)
        subprocess.run(['git','config','user.name','Master Agent'],cwd=d)
        ok,reason=safeguards.can_act(rec['file_to_modify'])
        if not ok: log(DB,f'Blocked:{reason}','WARN',cid=cid); return False
        fp=os.path.join(d,rec['file_to_modify'])
        if not os.path.exists(fp): return False
        open(fp,'w').write(new)
        r=subprocess.run([sys.executable,'-m','pytest','tests/','-q','--tb=short'],cwd=d,capture_output=True,text=True,timeout=300)
        m=re.search(r'(\d+) passed',r.stdout+r.stderr); n=int(m.group(1)) if m else 0
        if r.returncode!=0 or n<139: log(DB,f'Tests failed({n})','ERROR',cid=cid); return False
        subprocess.run(['git','add','-A'],cwd=d); subprocess.run(['git','commit','-m',f"agent:{rec['recommendation']}"],cwd=d)
        if subprocess.run(['git','push'],cwd=d,capture_output=True,timeout=60).returncode!=0: return False
        log_change(DB,cid,rec['hypothesis'],rec['file_to_modify'],'',True,True)
        log(DB,f"Deployed:{rec['file_to_modify']}",'MILESTONE',cid=cid); return True
    finally: shutil.rmtree(d,ignore_errors=True)
def main():
    log(DB,'MASTER AGENT COUNCIL ONLINE','MILESTONE')
    last=0
    while True:
        cid=str(uuid.uuid4())[:8]
        try:
            obs=observe(DB); gate=check_confidence(DB)
            log(DB,f"[{cid}] gate:{gate['all_passed']} resolved:{obs['trades']['resolved']} pnl:${obs['trades']['pnl']}",'MILESTONE' if gate['all_passed'] else 'INFO',cid=cid)
            if gate['all_passed']: log(DB,'CONFIDENCE GATE PASSED-AWAITING HUMAN','MILESTONE',cid=cid)
            if time.time()-last<3600: time.sleep(min(1800,int(3600-(time.time()-last)))); continue
            rec=orient(obs,gate); log(DB,f"[{cid}]{rec['recommendation']}->{rec['file_to_modify']}",cid=cid)
            d2=tempfile.mkdtemp()
            try:
                subprocess.run(['git','clone','--depth=1',REPO,d2],capture_output=True,timeout=60)
                fp=os.path.join(d2,rec['file_to_modify']); cur=open(fp).read() if os.path.exists(fp) else ''
            finally: shutil.rmtree(d2,ignore_errors=True)
            if act(rec,decide(rec,cur),cid): last=time.time()
        except Exception as e: log(DB,f'[{cid}]ERROR:{e}\n{traceback.format_exc()}','ERROR',cid=cid)
        time.sleep(1800)
if __name__=='__main__': main()