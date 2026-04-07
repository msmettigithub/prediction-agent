#!/usr/bin/env python3
"""Karpathy Kapital Dashboard v3 — responsive mobile + desktop, chat tab, wiki integration"""
import os,sqlite3,json,re
from datetime import datetime,timezone,timedelta
from fastapi import FastAPI,Request
from fastapi.responses import HTMLResponse,JSONResponse
import uvicorn,anthropic

app=FastAPI()
DB='/home/jovyan/shared/sm/prediction-agent-db/prediction_agent.db'
client=anthropic.Anthropic()
WIKI_URL='https://raw.githubusercontent.com/msmettigithub/prediction-agent/main/WIKI.md'

def to_et(ts):
    if not ts: return ''
    try:
        dt=datetime.fromisoformat(str(ts).replace('Z','').split('.')[0])
        if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
        et=dt+timedelta(hours=-4 if 3<=dt.month<=10 else -5)
        return et.strftime('%m/%d/%Y %H:%M:%S')
    except: return str(ts)[:19]

def q(sql,p=()):
    if not os.path.exists(DB): return []
    try:
        c=sqlite3.connect(DB); c.row_factory=sqlite3.Row
        r=c.execute(sql,p).fetchall(); c.close(); return [dict(x) for x in r]
    except: return []

def w(sql,p=()):
    try:
        c=sqlite3.connect(DB)
        c.execute("""CREATE TABLE IF NOT EXISTS agent_commands(
            id INTEGER PRIMARY KEY,ts TEXT,command TEXT,
            status TEXT DEFAULT 'pending',result TEXT,executed_at TEXT)""")
        c.execute(sql,p); c.commit(); c.close(); return True
    except: return False

def get_data():
    logs=q('SELECT ts,lvl,agent,msg FROM agent_log ORDER BY ts DESC LIMIT 200')
    open_=q("SELECT COUNT(*) n FROM paper_trades WHERE status='active'")
    res=q("SELECT COUNT(*) n,COALESCE(SUM(pnl),0) pnl,AVG(CASE WHEN pnl>0 THEN 1.0 ELSE 0.0 END) wr FROM paper_trades WHERE status='resolved'")
    changes=q('SELECT ts,hyp,file,ok,deployed FROM agent_changes ORDER BY ts DESC LIMIT 30')
    cmds=q("SELECT id,ts,command,status,result FROM agent_commands ORDER BY ts DESC LIMIT 30")
    tools=q('SELECT ts,tool_name,useful,notes FROM tool_experiments ORDER BY ts DESC LIMIT 20') if q("SELECT name FROM sqlite_master WHERE type='table' AND name='tool_experiments'") else []
    return dict(logs=logs,open_trades=open_[0]['n'] if open_ else 0,
                res=res[0] if res else {'n':0,'pnl':0,'wr':0},
                changes=changes,cmds=cmds,tools=tools)

def get_context(d):
    logs=d['logs'][:15]; res=d['res']
    log_text='\n'.join(f"{to_et(l['ts'])} [{l['lvl']}] {l['msg'][:100]}" for l in logs)
    tools_text='\n'.join(f"{t['tool_name']}: {'useful' if t['useful'] else 'skip'} — {t.get('notes','')[:60]}" for t in d['tools'][:6])
    return f'STATE {to_et(datetime.now(timezone.utc).isoformat())} ET\nP&L:${float(res.get("pnl") or 0):.2f} resolved:{res.get("n",0)} wr:{int((res.get("wr") or 0)*100)}% open:{d["open_trades"]}\nRECENT LOGS:\n{log_text}\nTOOLS:\n{tools_text}'

@app.post('/command')
async def post_command(req:Request):
    try:
        b=await req.json(); cmd=b.get('command','').strip()
        if not cmd: return JSONResponse({'error':'empty'},400)
        now=datetime.now(timezone.utc).isoformat()
        ok=w('INSERT INTO agent_commands(ts,command,status) VALUES(?,?,?)',(now,cmd,'pending'))
        return JSONResponse({'ok':ok,'queued_at':to_et(now),'command':cmd})
    except Exception as e: return JSONResponse({'error':str(e)},500)

@app.post('/chat')
async def chat(req:Request):
    try:
        b=await req.json(); msg=b.get('message','').strip(); history=b.get('history',[])
        if not msg: return JSONResponse({'error':'empty'},400)
        d=get_data(); ctx=get_context(d)
        sys_p=f'You are the AI interface for Karpathy Kapital, an autonomous prediction market fund.\nFull system context:\n{ctx}\nQueue commands with [COMMAND: text]. Be direct and data-driven.'
        r=client.messages.create(model='claude-sonnet-4-6',max_tokens=1000,system=sys_p,
            messages=[*history,{'role':'user','content':msg}])
        resp=r.content[0].text
        cmds=re.findall(r'\[COMMAND: ([^\]]+)\]',resp)
        for cmd in cmds:
            w('INSERT INTO agent_commands(ts,command,status) VALUES(?,?,?)',
              (datetime.now(timezone.utc).isoformat(),cmd,'pending'))
        return JSONResponse({'response':resp,'commands_queued':cmds})
    except Exception as e: return JSONResponse({'error':str(e)},500)

@app.get('/api/status')
def api_status():
    d=get_data(); last=d['logs'][0] if d['logs'] else {}
    return {'last_action':last.get('msg',''),'last_ts':to_et(last.get('ts','')),'pnl':float(d['res'].get('pnl') or 0)}

CSS='''
:root{--bg:#080810;--bg2:#0d0d1a;--bg3:#0a0a12;--bd:#1a1a2e;--text:#ccc;--dim:#555;--accent:#7c3aed;}
*{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{background:var(--bg);color:var(--text);font-family:-apple-system,'SF Pro Display','Segoe UI',monospace;min-height:100vh;}
/* ── Header ── */
.hdr{background:var(--bg);border-bottom:1px solid var(--bd);padding:12px 16px;position:sticky;top:0;z-index:100;display:flex;justify-content:space-between;align-items:center;}
.hdr-title{font-size:18px;font-weight:700;color:#fff;letter-spacing:2px;}
.hdr-sub{font-size:10px;color:#333;letter-spacing:1px;margin-top:2px;}
.hdr-time{font-size:11px;color:var(--dim);text-align:right;}
/* ── Mobile nav (bottom) ── */
.tab-bar{position:fixed;bottom:0;left:0;right:0;background:var(--bg);border-top:1px solid var(--bd);display:flex;z-index:200;padding-bottom:env(safe-area-inset-bottom);}
.tab{flex:1;padding:10px 4px 8px;text-align:center;cursor:pointer;border:none;background:none;color:var(--dim);font-size:10px;letter-spacing:1px;text-transform:uppercase;transition:color .2s;}
.tab.active{color:var(--accent);}
.tab-icon{font-size:20px;display:block;margin-bottom:2px;}
.pane{display:none;padding:12px 12px 80px;}
.pane.active{display:block;}
/* ── Desktop nav (top, horizontal) ── */
@media(min-width:768px){
  body{padding-bottom:0;}
  .tab-bar{position:static;border-top:none;border-bottom:1px solid var(--bd);padding:0 24px 0;background:var(--bg2);}
  .tab{padding:14px 20px 12px;flex:none;font-size:11px;border-bottom:2px solid transparent;}
  .tab.active{color:var(--accent);border-bottom-color:var(--accent);}
  .tab-icon{display:inline;font-size:14px;margin-right:6px;margin-bottom:0;}
  .pane{padding:24px 24px 24px;}
  .pane.active{display:grid;}
  #pane-dash.active{grid-template-columns:1fr 1fr;gap:0 24px;}
  #pane-chat.active{display:flex;flex-direction:column;}
  #pane-cmds.active, #pane-tools.active{grid-template-columns:1fr 1fr;gap:0 24px;}
}
/* ── Cards ── */
.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:14px;}
@media(min-width:768px){.grid{grid-template-columns:repeat(5,1fr);}}
.card{background:var(--bg2);border:1px solid var(--bd);border-radius:8px;padding:14px 12px;}
.card .v{font-size:24px;font-weight:700;color:#fff;}
.card .l{font-size:10px;color:var(--dim);letter-spacing:1px;text-transform:uppercase;margin-top:4px;}
/* ── Section titles ── */
.sec-title{font-size:10px;letter-spacing:2px;text-transform:uppercase;color:var(--dim);margin:16px 0 8px;padding-bottom:5px;border-bottom:1px solid var(--bd);}
.sec{background:var(--bg3);border:1px solid var(--bd);border-radius:8px;overflow:hidden;margin-bottom:12px;}
/* ── Log rows ── */
.log-row{padding:8px 10px;border-bottom:1px solid #0f0f1a;}
.log-ts{font-size:10px;color:var(--dim);display:block;margin-bottom:2px;font-family:monospace;}
.log-lvl{font-size:10px;font-weight:700;margin-right:6px;}
.log-msg{font-size:13px;color:#ddd;line-height:1.4;}
.change-row{padding:10px;border-bottom:1px solid #0f0f1a;}
/* ── Scrollable ── */
.scroll-box{max-height:400px;overflow-y:auto;-webkit-overflow-scrolling:touch;}
.scroll-tall{max-height:60vh;overflow-y:auto;-webkit-overflow-scrolling:touch;}
@media(min-width:768px){.scroll-tall{max-height:75vh;}}
/* ── Pulse ── */
.pulse-bar{background:var(--bg2);border-radius:10px;padding:14px;margin-bottom:14px;}
.pulse{display:inline-block;width:10px;height:10px;border-radius:50%;box-shadow:0 0 10px currentColor;animation:p 1.5s infinite;margin-right:8px;}
@keyframes p{0%,100%{opacity:1}50%{opacity:.3}}
/* ── Chat ── */
.chat-wrap{display:flex;flex-direction:column;height:calc(100vh - 140px);}
@media(min-width:768px){.chat-wrap{height:calc(100vh - 180px);}}
.chat-msgs{flex:1;overflow-y:auto;-webkit-overflow-scrolling:touch;padding:8px 0;}
.msg{margin:8px 0;max-width:85%;}
.msg.user{margin-left:auto;text-align:right;}
.msg-bubble{padding:10px 14px;border-radius:16px;font-size:14px;line-height:1.5;white-space:pre-wrap;}
.msg.user .msg-bubble{background:var(--accent);color:#fff;border-bottom-right-radius:4px;}
.msg.ai .msg-bubble{background:var(--bg2);color:#ddd;border-bottom-left-radius:4px;border:1px solid var(--bd);}
.msg-time{font-size:10px;color:var(--dim);margin-top:3px;padding:0 4px;}
.chat-input-wrap{background:var(--bg);border-top:1px solid var(--bd);padding:10px 12px;padding-bottom:calc(10px + env(safe-area-inset-bottom));}
@media(min-width:768px){.chat-input-wrap{padding-bottom:16px;}}
.chat-row{display:flex;gap:8px;align-items:flex-end;}
textarea{background:var(--bg2);border:1px solid var(--bd);color:#ddd;padding:10px 14px;border-radius:20px;font-family:-apple-system,sans-serif;font-size:15px;resize:none;flex:1;max-height:120px;outline:none;-webkit-appearance:none;}
textarea::placeholder{color:var(--dim);}
.send-btn{background:var(--accent);border:none;width:40px;height:40px;border-radius:50%;cursor:pointer;font-size:18px;flex-shrink:0;color:#fff;}
/* ── Forms ── */
.cmd-input{background:var(--bg2);border:1px solid var(--bd);color:#ddd;padding:12px;border-radius:8px;font-size:14px;width:100%;outline:none;margin-bottom:8px;}
.btn{background:var(--accent);color:#fff;border:none;padding:12px 20px;border-radius:8px;cursor:pointer;font-size:14px;font-weight:600;width:100%;}
@media(min-width:768px){.btn{width:auto;padding:10px 24px;}}
/* ── Desktop two-column layout helpers ── */
@media(min-width:768px){
  .col-left{grid-column:1;}
  .col-right{grid-column:2;}
  .col-full{grid-column:1/-1;}
}
'''

@app.get('/',response_class=HTMLResponse)
def home():
    d=get_data(); logs=d['logs']; res=d['res']
    now_et=to_et(datetime.now(timezone.utc).isoformat())
    last=logs[0] if logs else {}
    last_ts=to_et(last.get('ts',''))
    last_msg=last.get('msg','No activity yet')[:120]
    sc='#ff4444'; sl='STALE'
    if last.get('ts'):
        try:
            dt=datetime.fromisoformat(last['ts'].replace('Z',''))
            if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
            age=int((datetime.now(timezone.utc)-dt).total_seconds())
            if age<1800: sc='#00ff88'; sl=f'{age//60}m {age%60}s ago'
            elif age<7200: sc='#ffaa00'; sl=f'{age//3600}h {(age%3600)//60}m ago'
            else: sl=f'{age//3600}h ago — CHECK SYSTEM'
        except: pass
    pnl=float(res.get('pnl') or 0); pc='#00ff88' if pnl>=0 else '#ff4444'
    def lc(lvl): return {'MILESTONE':'#00ff88','ERROR':'#ff4444','WARN':'#ffaa00'}.get(lvl,'#aaaaaa')
    def lr(l,big=False):
        c=lc(l['lvl']); ts=to_et(l.get('ts','')); msg=l.get('msg','')[:140]
        s='14px' if big else '12px'
        return f'<div class="log-row"><span class="log-ts">{ts} ET</span><span class="log-lvl" style="color:{c}">{l["lvl"]}</span><span class="log-msg" style="font-size:{s}">{msg}</span></div>'
    def cr(c):
        col='#00ff88' if c.get('deployed') else ('#ffaa00' if c.get('ok') else '#ff4444')
        st='✅ DEPLOYED' if c.get('deployed') else ('🟡 PASSED' if c.get('ok') else '❌ FAILED')
        return f'<div class="change-row"><div style="display:flex;justify-content:space-between"><span style="color:{col};font-size:12px;font-weight:700">{st}</span><span style="color:var(--dim);font-size:10px">{to_et(c["ts"])} ET</span></div><div style="color:#888;font-size:11px">{c.get("file","")}</div><div style="color:#ccc;font-size:13px;margin-top:3px">{c.get("hyp","")[:80]}</div></div>'
    def cmdr(c):
        col={'pending':'#ffaa00','done':'#00ff88','error':'#ff4444'}.get(c.get('status',''),'#666')
        return f'<div class="change-row"><div style="display:flex;justify-content:space-between"><span style="color:{col};font-size:11px;font-weight:700">{c.get("status","").upper()}</span><span style="color:var(--dim);font-size:10px">{to_et(c["ts"])} ET</span></div><div style="color:#ddd;font-size:13px;margin-top:3px">{c.get("command","")[:100]}</div></div>'
    def toolr(t):
        col='#00ff88' if t.get('useful') else '#ff4444'
        return f'<div class="change-row"><div style="display:flex;justify-content:space-between"><span style="color:{col};font-size:12px;font-weight:700">{"✅" if t.get("useful") else "❌"} {t.get("tool_name","")}</span><span style="color:var(--dim);font-size:10px">{to_et(t["ts"])} ET</span></div><div style="color:#888;font-size:12px;margin-top:3px">{t.get("notes","")[:120]}</div></div>'
    all_logs=''.join(lr(l,big=True) for l in logs[:60]) or '<div style="color:#333;padding:12px">No activity yet</div>'
    ms=''.join(lr(l) for l in [x for x in logs if x['lvl']=='MILESTONE'][:10]) or '<div style="color:#333;padding:12px">None yet</div>'
    ers=''.join(lr(l) for l in [x for x in logs if x['lvl']=='ERROR'][:8]) or '<div style="color:#00ff8844;padding:12px">✓ System clean</div>'
    chs=''.join(cr(c) for c in d['changes']) or '<div style="color:#333;padding:12px">No changes yet</div>'
    cmds_h=''.join(cmdr(c) for c in d['cmds']) or '<div style="color:#333;padding:12px">No commands yet</div>'
    tools_h=''.join(toolr(t) for t in d['tools']) or '<div style="color:#333;padding:12px">Explorer agents will populate this</div>'
    return f"""<!DOCTYPE html>
<html lang='en'><head>
<meta charset='UTF-8'>
<meta name='viewport' content='width=device-width,initial-scale=1,maximum-scale=1'>
<meta name='apple-mobile-web-app-capable' content='yes'>
<title>⚡ Karpathy Kapital</title>
<style>{CSS}</style></head><body>
<div class='hdr'>
  <div><div class='hdr-title'>⚡ Karpathy Kapital</div><div class='hdr-sub'>AUTONOMOUS PREDICTION MARKET FUND</div></div>
  <div class='hdr-time'>{now_et}<br><span style='font-size:9px'>ET • ↻30s</span></div>
</div>
<div class='tab-bar'>
  <button class='tab active' id='tab-dash' onclick='sw("dash")'><span class='tab-icon'>📊</span>Dashboard</button>
  <button class='tab' id='tab-chat' onclick='sw("chat")'><span class='tab-icon'>💬</span>Chat</button>
  <button class='tab' id='tab-cmds' onclick='sw("cmds")'><span class='tab-icon'>⚡</span>Commands</button>
  <button class='tab' id='tab-tools' onclick='sw("tools")'><span class='tab-icon'>🔬</span>Tools</button>
</div>
<!-- DASHBOARD TAB -->
<div id='pane-dash' class='pane active'>
  <div class='col-full'>
    <div class='pulse-bar' style='border:1px solid {sc}44'>
      <div style='display:flex;align-items:center;margin-bottom:8px'>
        <span class='pulse' style='color:{sc};background:{sc}'></span>
        <span style='color:{sc};font-weight:700;font-size:13px;letter-spacing:1px'>{sl}</span>
      </div>
      <div style='color:#fff;font-size:14px;line-height:1.4'>{last_msg}</div>
      <div style='color:var(--dim);font-size:11px;margin-top:5px'>{last_ts} ET</div>
    </div>
    <div class='grid'>
      <div class='card' style='border-color:{pc}33'><div class='v' style='color:{pc}'>${pnl:.2f}</div><div class='l'>Paper P&amp;L</div></div>
      <div class='card'><div class='v'>{res.get('n',0)}</div><div class='l'>Resolved</div></div>
      <div class='card'><div class='v'>{d['open_trades']}</div><div class='l'>Open</div></div>
      <div class='card'><div class='v'>{int((res.get('wr') or 0)*100)}%</div><div class='l'>Win Rate</div></div>
      <div class='card'><div class='v'>{len(d['changes'])}</div><div class='l'>RL Actions</div></div>
    </div>
  </div>
  <div class='col-left'>
    <div class='sec-title'>Last 60 Actions</div>
    <div class='sec scroll-tall'>{all_logs}</div>
  </div>
  <div class='col-right'>
    <div class='sec-title'>Milestones</div>
    <div class='sec scroll-box'>{ms}</div>
    <div class='sec-title'>Errors</div>
    <div class='sec'>{ers}</div>
  </div>
</div>
<!-- CHAT TAB -->
<div id='pane-chat' class='pane'>
  <div class='chat-wrap'>
    <div id='msgs' class='chat-msgs'><div style='color:var(--dim);text-align:center;padding:40px 20px;font-size:14px'>Chat with your master agent.<br><br>Full system context injected. Ask anything.<br>I queue commands automatically.</div></div>
    <div class='chat-input-wrap'>
      <div class='chat-row'>
        <textarea id='ci' rows='1' placeholder='Ask the master agent...' oninput='ar(this)'></textarea>
        <button class='send-btn' onclick='sc()'>↑</button>
      </div>
    </div>
  </div>
</div>
<!-- COMMANDS TAB -->
<div id='pane-cmds' class='pane'>
  <div class='col-left'>
    <div class='sec-title'>Send Order to Master Agent</div>
    <div class='sec' style='padding:12px'>
      <input class='cmd-input' id='cmi' placeholder='e.g. explore pytrends, increase agents to 4, run eval'>
      <button class='btn' onclick='sendCmd()'>⚡ Queue Command</button>
      <div id='cms' style='color:var(--dim);font-size:12px;margin-top:8px'></div>
    </div>
    <div class='sec-title'>Command History</div>
    <div class='sec scroll-box'>{cmds_h}</div>
  </div>
  <div class='col-right'>
    <div class='sec-title'>RL Code Changes</div>
    <div class='sec scroll-tall'>{chs}</div>
  </div>
</div>
<!-- TOOLS TAB -->
<div id='pane-tools' class='pane'>
  <div class='col-full'>
    <div style='background:var(--bg2);border:1px solid #7c3aed44;border-radius:8px;padding:12px;margin-bottom:14px'>
      <div style='color:var(--accent);font-size:12px;font-weight:700;margin-bottom:4px'>EXPLORER AGENTS + WIKI</div>
      <div style='color:#888;font-size:13px;line-height:1.5'>Every 3rd cycle, a sub-agent tests a new library for Kalshi signals. Results stored here and in <a href='https://github.com/msmettigithub/prediction-agent/blob/main/WIKI.md' target='_blank' style='color:var(--accent)'>WIKI.md</a> for persistent memory across sessions.</div>
    </div>
  </div>
  <div class='col-left'>
    <div class='sec-title'>Tool Experiments ({len(d['tools'])} tested)</div>
    <div class='sec scroll-tall'>{tools_h}</div>
  </div>
  <div class='col-right'>
    <div class='sec-title'>Wiki Reference</div>
    <div class='sec' style='padding:12px'>
      <div style='color:#888;font-size:13px;line-height:1.6'>The <a href='https://github.com/msmettigithub/prediction-agent/blob/main/WIKI.md' target='_blank' style='color:var(--accent)'>WIKI.md</a> in GitHub is the persistent memory for this system. Both Claude (in chat) and the master agent update it after key events. Read it at the start of any new session to restore full context.</div>
      <div style='margin-top:12px;color:#555;font-size:11px'>Updated by: master agent (calibration, decisions, experiments) + Claude (architecture, resource IDs, API patterns)</div>
    </div>
  </div>
</div>
<script>
function sw(id){{document.querySelectorAll('.pane,.tab').forEach(e=>e.classList.remove('active'));document.getElementById('pane-'+id).classList.add('active');document.getElementById('tab-'+id).classList.add('active');if(id==='chat')document.getElementById('ci').focus();}}
let H=[];
function ar(el){{el.style.height='auto';el.style.height=Math.min(el.scrollHeight,120)+'px';}}
function am(role,text){{const m=document.getElementById('msgs');const t=new Date().toLocaleTimeString('en-US',{{hour:'2-digit',minute:'2-digit',hour12:true}});m.innerHTML+=`<div class='msg ${{role}}'><div class='msg-bubble'>${{text}}</div><div class='msg-time'>${{t}}</div></div>`;m.scrollTop=m.scrollHeight;}}
async function sc(){{const i=document.getElementById('ci');const msg=i.value.trim();if(!msg)return;am('user',msg);i.value='';i.style.height='auto';H.push({{role:'user',content:msg}});am('ai','...');try{{const r=await fetch('/chat',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{message:msg,history:H.slice(0,-1)}})}});const j=await r.json();const bs=document.getElementById('msgs').querySelectorAll('.msg.ai');bs[bs.length-1].querySelector('.msg-bubble').textContent=j.response||'Error';H.push({{role:'assistant',content:j.response||''}});if(j.commands_queued?.length)am('ai','Queued: '+j.commands_queued.join(', '));}}catch(e){{am('ai','Error: '+e);}}}}
document.getElementById('ci').addEventListener('keydown',e=>{{if(e.key==='Enter'&&!e.shiftKey){{e.preventDefault();sc();}}}});
async function sendCmd(){{const i=document.getElementById('cmi');const cmd=i.value.trim();const s=document.getElementById('cms');if(!cmd)return;s.textContent='Sending...';s.style.color='#ffaa00';try{{const r=await fetch('/command',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{command:cmd}})}});const j=await r.json();if(j.ok){{s.textContent='Queued at '+j.queued_at+' ET';s.style.color='#00ff88';i.value='';}}else s.textContent='Error: '+j.error;}}catch(e){{s.textContent='Error: '+e;s.style.color='#ff4444';}}}}
setInterval(()=>{{if(document.getElementById('pane-dash').classList.contains('active'))location.reload();}},30000);
</script></body></html>"""

if __name__=='__main__':
    uvicorn.run(app,host='0.0.0.0',port=8000,reload=False)