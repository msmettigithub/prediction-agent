#!/usr/bin/env python3
"""Karpathy Kapital Dashboard v5 — Real-time countdowns, ET timestamps, live activity."""
import os,sqlite3,json
from datetime import datetime,timezone,timedelta
from fastapi import FastAPI,Request
from fastapi.responses import HTMLResponse,JSONResponse
import uvicorn

app=FastAPI()
DB='/home/jovyan/shared/sm/prediction-agent-db/prediction_agent.db'

def to_et(ts):
    if not ts: return ''
    try:
        dt=datetime.fromisoformat(str(ts).replace('Z','').split('.')[0])
        if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
        off=-4 if 3<=dt.month<=10 else -5
        return (dt+timedelta(hours=off)).strftime('%m/%d/%Y %H:%M:%S')
    except: return str(ts)[:19]

def utc_iso(ts):
    """Return clean UTC ISO for JS consumption"""
    if not ts: return ''
    try:
        dt=datetime.fromisoformat(str(ts).replace('Z','').split('.')[0])
        if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
        return dt.strftime('%Y-%m-%dT%H:%M:%SZ')
    except: return ''

def age_secs(ts):
    """Seconds since timestamp"""
    if not ts: return 999999
    try:
        dt=datetime.fromisoformat(str(ts).replace('Z','').split('.')[0])
        if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
        return int((datetime.now(timezone.utc)-dt).total_seconds())
    except: return 999999

def q(sql,p=()):
    if not os.path.exists(DB): return []
    try:
        c=sqlite3.connect(DB); c.row_factory=sqlite3.Row
        rows=c.execute(sql,p).fetchall(); c.close()
        return [dict(r) for r in rows]
    except: return []

def ins(sql,p=()):
    try:
        c=sqlite3.connect(DB)
        c.execute("CREATE TABLE IF NOT EXISTS agent_commands(id INTEGER PRIMARY KEY,ts TEXT,command TEXT,status TEXT DEFAULT 'pending',result TEXT,executed_at TEXT)")
        c.execute(sql,p); c.commit(); c.close(); return True
    except: return False

def infer_next_cycle(logs):
    """Parse 'Next:Xs' from recent logs to infer cycle speed."""
    for l in logs[:20]:
        m=__import__('re').search(r'[Nn]ext[:\s]+?(\d+)s',l.get('msg',''))
        if m: return int(m.group(1))
    return 300  # default 5min

def get_data():
    logs=q('SELECT ts,lvl,agent,msg FROM agent_log ORDER BY ts DESC LIMIT 200')
    open_t=q("SELECT COUNT(*) n FROM paper_trades WHERE status='active'")
    res=q("SELECT COUNT(*) n,COALESCE(SUM(pnl),0) pnl,AVG(CASE WHEN pnl>0 THEN 1.0 ELSE 0.0 END) wr FROM paper_trades WHERE status='resolved'")
    changes=q('SELECT ts,hyp,file,ok,deployed FROM agent_changes ORDER BY ts DESC LIMIT 30')
    cmds=q("SELECT ts,command,status,result FROM agent_commands ORDER BY ts DESC LIMIT 15")
    tools=q('SELECT ts,tool_name,useful,notes FROM tool_experiments ORDER BY ts DESC LIMIT 20') if q("SELECT name FROM sqlite_master WHERE type='table' AND name='tool_experiments'") else []
    return dict(logs=logs,open=open_t[0]['n'] if open_t else 0,
                res=res[0] if res else {'n':0,'pnl':0,'wr':0},
                changes=changes,cmds=cmds,tools=tools)

@app.post('/command')
async def post_command(req:Request):
    try:
        body=await req.json(); cmd=body.get('command','').strip()
        if not cmd: return JSONResponse({'error':'empty'},400)
        now=datetime.now(timezone.utc).isoformat()
        ok=ins('INSERT INTO agent_commands(ts,command,status) VALUES(?,?,?)',(now,cmd,'pending'))
        return JSONResponse({'ok':ok,'queued_at':to_et(now)})
    except Exception as e: return JSONResponse({'error':str(e)},500)

@app.get('/api/pulse')
def api_pulse():
    logs=q('SELECT ts,lvl,msg FROM agent_log ORDER BY ts DESC LIMIT 1')
    last=logs[0] if logs else {}
    a=age_secs(last.get('ts',''))
    return {'last_ts':utc_iso(last.get('ts','')),
            'last_msg':last.get('msg','')[:80],'age_secs':a}

@app.get('/',response_class=HTMLResponse)
def home():
    d=get_data()
    logs=d['logs']; res=d['res']
    last=logs[0] if logs else {}
    last_utc=utc_iso(last.get('ts',''))
    last_et=to_et(last.get('ts',''))
    last_msg=last.get('msg','No activity yet')[:120]
    cycle_sec=infer_next_cycle(logs)
    now_et=to_et(datetime.now(timezone.utc).isoformat())
    pnl_col='#00ff88' if float(res.get('pnl') or 0)>=0 else '#ff4444'
    
    def lvl_col(lvl): return {'MILESTONE':'#00ff88','ERROR':'#ff4444','WARN':'#ffaa00'}.get(lvl,'#999')
    
    def log_row(l):
        utc=utc_iso(l.get('ts',''))
        et=to_et(l.get('ts',''))
        c=lvl_col(l['lvl'])
        msg=l.get('msg','')[:150]
        a=age_secs(l.get('ts',''))
        ago=f'{a//60}m {a%60}s' if a<3600 else f'{a//3600}h {(a%3600)//60}m'
        age_c='#00ff88' if a<300 else ('#ffaa00' if a<1800 else '#ff4444')
        return (f'<tr data-utc="{utc}">'
                f'<td style="white-space:nowrap;padding:5px 8px">'
                f'<div style="color:#555;font-size:11px">{et} ET</div>'
                f'<div class="age" style="color:{age_c};font-size:12px;font-weight:bold">{ago} ago</div>'
                f'</td>'
                f'<td style="color:{c};font-weight:bold;font-size:11px;padding:5px 4px;white-space:nowrap">{l["lvl"]}</td>'
                f'<td style="color:#ddd;font-size:13px;padding:5px 8px">{msg}</td>'
                f'</tr>')
    
    def change_row(c):
        col='#00ff88' if c.get('deployed') else ('#ffaa00' if c.get('ok') else '#ff4444')
        st='DEPLOYED' if c.get('deployed') else ('PASSED' if c.get('ok') else 'FAILED')
        et=to_et(c.get('ts',''))
        a=age_secs(c.get('ts',''))
        ago=f'{a//60}m {a%60}s' if a<3600 else f'{a//3600}h'
        return (f'<tr><td style="white-space:nowrap;padding:4px 8px">'
                f'<div style="color:#555;font-size:11px">{et} ET</div>'
                f'<div style="color:#777;font-size:11px">{ago} ago</div></td>'
                f'<td style="color:{col};font-size:11px;font-weight:bold;padding:4px">{st}</td>'
                f'<td style="color:#777;font-size:11px;padding:4px 8px">{(c.get("file") or "")}</td>'
                f'<td style="color:#ddd;font-size:12px;padding:4px 8px">{(c.get("hyp") or "")[:90]}</td></tr>')
    
    def cmd_row(c):
        col={'pending':'#ffaa00','done':'#00ff88','error':'#ff4444'}.get(c.get('status',''),'#aaa')
        return (f'<tr><td style="color:#555;font-size:11px;white-space:nowrap;padding:4px 8px">{to_et(c.get("ts",""))} ET</td>'
                f'<td style="color:{col};font-size:11px;font-weight:bold;padding:4px">{c.get("status","").upper()}</td>'
                f'<td style="color:#ddd;font-size:12px;padding:4px 8px">{(c.get("command") or "")[:100]}</td>'
                f'<td style="color:#666;font-size:11px;padding:4px 8px">{(c.get("result") or "")[:60]}</td></tr>')
    
    milestones=[l for l in logs if l['lvl']=='MILESTONE'][:6]
    errors=[l for l in logs if l['lvl']=='ERROR'][:4]
    recent=logs[:50]
    
    html=f"""<!DOCTYPE html>
<html><head><title>⚡ Karpathy Kapital</title>
<meta http-equiv="refresh" content="30">
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#080810;color:#ccc;font-family:'SF Mono','Fira Code',monospace;padding:20px}}
h2{{color:#444;font-size:10px;letter-spacing:3px;text-transform:uppercase;margin:16px 0 8px;border-bottom:1px solid #151522;padding-bottom:5px}}
.hdr{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:18px;border-bottom:1px solid #151522;padding-bottom:12px}}
.grid{{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-bottom:16px}}
.card{{background:#0d0d1a;border:1px solid #1a1a2e;border-radius:6px;padding:14px}}
.card .v{{font-size:24px;font-weight:bold;color:#fff}}
.card .l{{font-size:10px;color:#444;letter-spacing:2px;text-transform:uppercase;margin-top:3px}}
.sec{{background:#0a0a12;border:1px solid #151522;border-radius:6px;padding:14px;margin-bottom:12px}}
table{{width:100%;border-collapse:collapse}}
tr:hover{{background:#0f0f18}}
td{{vertical-align:top}}
textarea{{background:#0d0d1a;border:1px solid #2a2a3a;color:#ddd;padding:10px;width:100%;border-radius:4px;font-family:inherit;font-size:13px;resize:vertical}}
button{{background:#7c3aed;color:#fff;border:none;padding:10px 20px;border-radius:4px;cursor:pointer;font-family:inherit;font-size:13px;margin-top:8px}}
button:hover{{background:#9d4edd}}
.pulse{{display:inline-block;width:12px;height:12px;border-radius:50%;animation:p 1.5s infinite;margin-right:8px;flex-shrink:0}}
@keyframes p{{0%,100%{{opacity:1}}50%{{opacity:0.3}}}}
.countdown-bar{{background:#0d0d1a;border:1px solid #1a1a2e;border-radius:8px;padding:18px 20px;margin-bottom:16px}}
.big-timer{{font-size:42px;font-weight:bold;font-variant-numeric:tabular-nums;letter-spacing:2px}}
.next-timer{{font-size:28px;font-weight:bold;font-variant-numeric:tabular-nums}}
.timer-label{{font-size:10px;color:#444;letter-spacing:3px;text-transform:uppercase;margin-bottom:4px}}
.timers{{display:grid;grid-template-columns:1fr 1fr;gap:20px}}
.progress-track{{background:#151522;border-radius:4px;height:6px;margin-top:10px;overflow:hidden}}
.progress-fill{{height:6px;border-radius:4px;transition:width 1s linear}}
</style></head><body>

<div class='hdr'>
  <div>
    <div style='font-size:20px;color:#fff;letter-spacing:3px;font-weight:bold'>⚡ KARPATHY KAPITAL</div>
    <div style='color:#333;font-size:10px;margin-top:2px;letter-spacing:2px'>AUTONOMOUS PREDICTION MARKET FUND — COMMAND CENTER</div>
  </div>
  <div style='text-align:right'>
    <div style='color:#333;font-size:10px;letter-spacing:2px'>DASHBOARD</div>
    <div style='color:#fff;font-size:13px;margin-top:2px'>{now_et} ET</div>
    <div style='color:#222;font-size:10px;margin-top:2px'>↻ page refresh 30s | timers live</div>
  </div>
</div>

<!-- MAIN COUNTDOWN BANNER -->
<div class='countdown-bar'>
  <div class='timers'>
    <div>
      <div class='timer-label'>⏱ time since last action</div>
      <div style='display:flex;align-items:center'>
        <span class='pulse' id='pulse' style='background:#00ff88;box-shadow:0 0 10px #00ff88'></span>
        <span class='big-timer' id='since'>--:--</span>
      </div>
      <div style='color:#555;font-size:11px;margin-top:4px' id='lastMsg'>{last_msg}</div>
      <div style='color:#333;font-size:10px;margin-top:2px'>{last_et} ET</div>
    </div>
    <div>
      <div class='timer-label'>⚡ next anticipated action in</div>
      <div class='next-timer' id='nextin' style='color:#7c3aed'>--:--</div>
      <div class='progress-track'><div class='progress-fill' id='progbar' style='background:#7c3aed;width:0%'></div></div>
      <div style='color:#555;font-size:11px;margin-top:6px' id='nextAction'>OBSERVE → ORIENT → DECIDE → ACT</div>
      <div style='color:#333;font-size:10px;margin-top:2px'>Cycle: <span id='cycleSpeed'>{cycle_sec}s</span></div>
    </div>
  </div>
</div>

<div class='grid'>
  <div class='card' style='border-color:{pnl_col}33'><div class='v' style='color:{pnl_col}'>${{float(res.get('pnl') or 0):.2f}}</div><div class='l'>Paper P&L</div></div>
  <div class='card'><div class='v'>{res.get('n',0)}</div><div class='l'>Resolved Trades</div></div>
  <div class='card'><div class='v'>{d['open']}</div><div class='l'>Open Positions</div></div>
  <div class='card' style='border-color:#{'00ff88' if (res.get('wr') or 0)>0.55 else '1a1a2e'}33'><div class='v'>{int((res.get('wr') or 0)*100)}%</div><div class='l'>Win Rate</div></div>
  <div class='card'><div class='v'>{len(d['changes'])}</div><div class='l'>RL Actions Taken</div></div>
</div>

<h2>Last 50 Actions — Live Timing</h2>
<div class='sec' style='max-height:700px;overflow-y:auto'>
  <table>
    <tr><th style='color:#333;font-size:9px;text-align:left;padding:4px 8px'>TIMESTAMP (ET) / AGE</th><th style='color:#333;font-size:9px;text-align:left;padding:4px'>LEVEL</th><th style='color:#333;font-size:9px;text-align:left;padding:4px 8px'>ACTION</th></tr>
    {''.join(log_row(l) for l in recent) or '<tr><td colspan="3" style="color:#333;padding:10px">No activity yet</td></tr>'}
  </table>
</div>

<h2>Milestones</h2>
<div class='sec'>
  <table>{''.join(log_row(l) for l in milestones) or '<tr><td colspan="3" style="color:#333;padding:8px">None yet</td></tr>'}</table>
</div>

<h2>Errors</h2>
<div class='sec'>
  <table>{''.join(log_row(l) for l in errors) or '<tr><td colspan="3" style="color:#00ff8855;padding:8px">✓ No errors — clean</td></tr>'}</table>
</div>

<h2>RL Code Changes</h2>
<div class='sec' style='max-height:280px;overflow-y:auto'>
  <table>
    <tr><th style='color:#333;font-size:9px;text-align:left;padding:4px 8px'>WHEN</th><th style='color:#333;font-size:9px;text-align:left;padding:4px'>STATUS</th><th style='color:#333;font-size:9px;text-align:left;padding:4px 8px'>FILE</th><th style='color:#333;font-size:9px;text-align:left;padding:4px 8px'>HYPOTHESIS</th></tr>
    {''.join(change_row(c) for c in d['changes']) or '<tr><td colspan="4" style="color:#333;padding:8px">No changes yet</td></tr>'}
  </table>
</div>

<h2>Tool Experiments</h2>
<div class='sec'>
  {''.join(f"<div style='padding:7px 0;border-bottom:1px solid #111'><span style='color:#7c3aed;font-size:13px;font-weight:bold'>{t.get('tool_name','')}</span><span style='color:{chr(35)+chr(48)+chr(48)+chr(102)+chr(102)+chr(56)+chr(56) if t.get(chr(117)+chr(115)+chr(101)+chr(102)+chr(117)+chr(108)) else chr(35)+chr(102)+chr(102)+chr(52)+chr(52)+chr(52)+chr(52)};font-size:11px;font-weight:bold;margin-left:10px'>{chr(85)+chr(83)+chr(69)+chr(70)+chr(85)+chr(76) if t.get(chr(117)+chr(115)+chr(101)+chr(102)+chr(117)+chr(108)) else chr(78)+chr(79)+chr(84)+chr(32)+chr(85)+chr(83)+chr(69)+chr(70)+chr(85)+chr(76)}</span><span style='color:#333;font-size:10px;margin-left:8px'>{to_et(t.get(chr(116)+chr(115),chr(32)))} ET</span><div style='color:#888;font-size:11px;margin-top:2px'>{(t.get(chr(110)+chr(111)+chr(116)+chr(101)+chr(115)) or chr(32))[:120]}</div></div>" for t in d['tools']) or "<div style='color:#333;padding:8px'>Explorer agents will populate this — first experiment launches in cycle 3</div>"}
</div>

<h2>Command Queue — Send Orders to Master Agent</h2>
<div class='sec'>
  <div style='margin-bottom:14px'>
    <textarea id='cmd' rows='2' placeholder='Examples: explore pytrends, increase n_agents to 4, run eval harness, focus on model/probability_model.py'></textarea>
    <button onclick='sendCmd()'>⚡ Send Order</button>
    <span id='cmdStatus' style='color:#555;font-size:11px;margin-left:12px'></span>
  </div>
  <table>
    <tr><th style='color:#333;font-size:9px;text-align:left;padding:4px 8px'>TIME (ET)</th><th style='color:#333;font-size:9px;text-align:left;padding:4px'>STATUS</th><th style='color:#333;font-size:9px;text-align:left;padding:4px 8px'>COMMAND</th><th style='color:#333;font-size:9px;text-align:left;padding:4px 8px'>RESULT</th></tr>
    {''.join(cmd_row(c) for c in d['cmds']) or '<tr><td colspan="4" style="color:#333;padding:8px">No commands yet</td></tr>'}
  </table>
</div>

<script>
const LAST_UTC='{last_utc}';
const CYCLE_SEC={cycle_sec};

function fmt(secs){{
  if(secs<0) return '00:00';
  const h=Math.floor(secs/3600);
  const m=Math.floor((secs%3600)/60);
  const s=secs%60;
  if(h>0) return h+'h '+String(m).padStart(2,'0')+'m '+String(s).padStart(2,'0')+'s';
  return String(m).padStart(2,'0')+'m '+String(s).padStart(2,'0')+'s';
}}

function getPhase(since){{
  const pos=since%CYCLE_SEC;
  if(pos<30) return 'OBSERVE — reading DB metrics';
  if(pos<60) return 'PROCESS COMMANDS — checking order queue';
  if(pos<90) return 'EXPLORE — testing new tools';
  if(pos<180) return 'ORIENT — Opus reasoning about best change';
  if(pos<270) return 'DECIDE — Sonnet writing implementation';
  return 'ACT — running tests, pushing to GitHub';
}}

function tick(){{
  if(!LAST_UTC) return;
  const now=Date.now();
  const last=new Date(LAST_UTC).getTime();
  const since=Math.floor((now-last)/1000);
  const nextIn=Math.max(0,CYCLE_SEC-since);
  const pct=Math.min(100,Math.floor((since/CYCLE_SEC)*100));
  // Since timer
  const sinceEl=document.getElementById('since');
  const pulseEl=document.getElementById('pulse');
  if(sinceEl){{
    sinceEl.textContent=fmt(since);
    let col;
    if(since<300){{ col='#00ff88'; }}
    else if(since<1800){{ col='#ffaa00'; }}
    else{{ col='#ff4444'; }}
    sinceEl.style.color=col;
    if(pulseEl) pulseEl.style.background=col;
  }}
  // Next timer
  const nextEl=document.getElementById('nextin');
  const progEl=document.getElementById('progbar');
  const phaseEl=document.getElementById('nextAction');
  if(nextEl){{ nextEl.textContent=nextIn>0?fmt(nextIn):'ACTION IMMINENT'; nextEl.style.color=nextIn<30?'#00ff88':'#7c3aed'; }}
  if(progEl) progEl.style.width=pct+'%';
  if(phaseEl) phaseEl.textContent=getPhase(since);
  // Update age in each log row
  document.querySelectorAll('tr[data-utc]').forEach(row=>{{
    const utc=row.getAttribute('data-utc');
    if(!utc) return;
    const rowAge=Math.floor((now-new Date(utc).getTime())/1000);
    const ageDivs=row.querySelectorAll('.age');
    if(ageDivs.length>0){{
      const ag=rowAge<3600?fmt(rowAge):Math.floor(rowAge/3600)+'h '+Math.floor((rowAge%3600)/60)+'m';
      ageDivs[0].textContent=ag+' ago';
      let ac;
      if(rowAge<300){{ ac='#00ff88'; }}
      else if(rowAge<1800){{ ac='#ffaa00'; }}
      else{{ ac='#ff4444'; }}
      ageDivs[0].style.color=ac;
    }}
  }});
}}

setInterval(tick,1000); tick();

async function sendCmd(){{
  const cmd=document.getElementById('cmd').value.trim();
  const st=document.getElementById('cmdStatus');
  if(!cmd) return;
  st.textContent='Sending...'; st.style.color='#ffaa00';
  try{{
    const r=await fetch('/command',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{command:cmd}})}});
    const j=await r.json();
    if(j.ok){{ st.textContent='✓ Queued '+j.queued_at+' ET'; st.style.color='#00ff88'; document.getElementById('cmd').value=''; }}
    else{{ st.textContent='Error: '+j.error; st.style.color='#ff4444'; }}
  }}catch(e){{ st.textContent='Error: '+e; st.style.color='#ff4444'; }}
}}
</script>
</body></html>"""
    return html

if __name__=='__main__':
    uvicorn.run(app,host='0.0.0.0',port=8000,reload=False)