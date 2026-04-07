"""Karpathy Kapital Doctor Agent — self-healing infrastructure.
Monitors all Saturn Cloud resources. Detects crashes. Diagnoses with Claude. Fixes. Revives.
No human needed. Runs every OODA cycle.
"""
import os,json,re,time,subprocess,tempfile,shutil,requests,anthropic
from datetime import datetime,timezone

SATURN=os.environ.get('SATURN_API_TOKEN','')
BASE='https://app.community.saturnenterprise.io/api'
PAT=os.environ.get('GITHUB_PAT','')
REPO=f'https://{PAT}@github.com/msmettigithub/prediction-agent.git'
DB='/home/jovyan/shared/sm/prediction-agent-db/prediction_agent.db'
client=anthropic.Anthropic()

WATCHED={
    'deployments':{
        'master-agent':'18efeea066bc4d828b984ec1d752d131',
        'kk-dashboard':'9ad2711e1bd44434a4f324220fb537e9',
    },
    'jobs':{
        'prediction-agent-auto':'6f9f09390bb3454faf88ed00419edf92',
    }
}

def log(msg,lvl='INFO',cid=None):
    import sqlite3; print(f'[DOCTOR][{lvl}] {msg}',flush=True)
    try:
        c=sqlite3.connect(DB)
        c.execute('CREATE TABLE IF NOT EXISTS agent_log(id INTEGER PRIMARY KEY,ts TEXT,cid TEXT,lvl TEXT,agent TEXT,msg TEXT)')
        c.execute('INSERT INTO agent_log(ts,cid,lvl,agent,msg) VALUES(?,?,?,?,?)',
            (datetime.now(timezone.utc).isoformat(),cid,lvl,'DOCTOR',msg[:500]))
        c.commit(); c.close()
    except: pass

def api(method,path,body=None):
    if not SATURN: return {}
    try:
        h={'Authorization':f'token {SATURN}','Content-Type':'application/json'}
        r=getattr(requests,method)(f'{BASE}{path}',json=body,headers=h,timeout=15)
        return r.json()
    except: return {}

def get_logs(rtype,rid,n=60):
    d=api('get',f'/api/{rtype}s/{rid}/logs?page_size={n}')
    return '\n'.join(l.get('content','') for l in (d.get('logs') or [])[-n:])

DIAGNOSE_SYS="""You are the doctor for Karpathy Kapital's Saturn Cloud infrastructure.
A resource has crashed. Diagnose from logs and prescribe the minimal fix.
Possible fixes:
- restart: just restart (for transient errors)
- add_package: {fix, package} — add missing pip package
- fix_file: {fix, file, issue} — file has a bug, needs code fix
- fix_command: {fix, command} — wrong startup command
Respond ONLY in raw JSON: {diagnosis, fix, details, confidence}"""

def diagnose(name,rtype,rid,logs,cid):
    try:
        r=client.messages.create(model='claude-sonnet-4-6',max_tokens=400,system=DIAGNOSE_SYS,
            messages=[{'role':'user','content':f'Resource:{name}({rtype})\nLogs:\n{logs[-2500:]}'}])
        t=re.sub(r'^\s*`{3}(?:json)?\s*','',r.content[0].text.strip())
        result=json.loads(re.sub(r'\s*`{3}\s*$','',t).strip())
        log(f'Diagnosed {name}: {result["diagnosis"]} fix={result["fix"]} conf={result["confidence"]}','MILESTONE',cid)
        return result
    except Exception as e:
        log(f'Diagnose error: {e}','ERROR',cid)
        return {'fix':'restart','diagnosis':'unknown','confidence':0.5,'details':{}}

FIX_SYS='Senior Python engineer. Fix the described bug. Return ONLY the complete corrected file. No markdown.'

def fix_file_via_git(filepath,issue,cid):
    d=tempfile.mkdtemp()
    try:
        if subprocess.run(['git','clone','--depth=1',REPO,d],capture_output=True,timeout=120).returncode!=0: return False
        subprocess.run(['git','config','user.email','kk-doctor@saturncloud.io'],cwd=d)
        subprocess.run(['git','config','user.name','KK Doctor'],cwd=d)
        fp=os.path.join(d,filepath)
        if not os.path.exists(fp): return False
        current=open(fp).read()
        r=client.messages.create(model='claude-sonnet-4-6',max_tokens=4000,system=FIX_SYS,
            messages=[{'role':'user','content':f'File:{filepath}\nIssue:{issue}\nContent:\n{current[:4000]}'}])
        open(fp,'w').write(r.content[0].text.strip())
        subprocess.run(['git','add','-A'],cwd=d)
        subprocess.run(['git','commit','-m',f'doctor-fix:{filepath}:{issue[:50]}'],cwd=d)
        return subprocess.run(['git','push'],cwd=d,capture_output=True,timeout=60).returncode==0
    finally: shutil.rmtree(d,ignore_errors=True)

def apply_fix(name,rtype,rid,diag,cid):
    fix=diag.get('fix','restart'); det=diag.get('details',{})
    log(f'Applying fix to {name}: {fix}','MILESTONE',cid)
    if fix=='add_package':
        pkg=det.get('package','')
        if pkg:
            cur=api('get',f'/api/{rtype}s/{rid}')
            pkgs=cur.get('extra_packages',{}).get('pip','')
            if pkg not in pkgs:
                api('patch',f'/api/{rtype}s/{rid}',{'extra_packages':{'pip':f'{pkgs} {pkg}'.strip(),'as_requirements_txt':False,'use_mamba':False}})
                log(f'Added package {pkg} to {name}','MILESTONE',cid)
    elif fix=='fix_command':
        cmd=det.get('command','')
        if cmd: api('patch',f'/api/{rtype}s/{rid}',{'command':cmd})
    elif fix=='fix_file':
        fp=det.get('file',''); issue=det.get('issue','')
        if fp: fix_file_via_git(fp,issue,cid)
    # Always restart
    time.sleep(2)
    api('post',f'/api/{rtype}s/{rid}/stop')
    time.sleep(4)
    result=api('post',f'/api/{rtype}s/{rid}/start')
    log(f'Restarted {name}: {result.get("status","?")}','MILESTONE',cid)

def check_and_heal(cid):
    """Main health check. Called from master agent every OODA cycle."""
    healed=[]
    for name,rid in WATCHED['deployments'].items():
        # Skip healing self (master-agent) to avoid restart loops
        if name=='master-agent': continue
        d=api('get',f'/api/deployments/{rid}')
        status=d.get('status','unknown'); active=d.get('active_count',0)
        if status=='error' or (status=='running' and active==0):
            log(f'HEALTH ALERT: {name} status={status} active={active}','MILESTONE',cid)
            logs=get_logs('deployment',rid,80)
            diag=diagnose(name,'deployment',rid,logs,cid)
            apply_fix(name,'deployment',rid,diag,cid)
            healed.append(name)
        else:
            log(f'HEALTH OK: {name} [{status}] active:{active}',cid=cid)
    for name,jid in WATCHED['jobs'].items():
        d=api('get',f'/api/jobs/{jid}')
        if d.get('status')=='error':
            log(f'HEALTH ALERT: job {name} in error','MILESTONE',cid)
            api('post',f'/api/jobs/{jid}/stop')
            time.sleep(2)
            api('post',f'/api/jobs/{jid}/start')
            healed.append(name)
    return healed