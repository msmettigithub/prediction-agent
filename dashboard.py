#!/usr/bin/env python3
"""Karpathy Kapital — Mobile-First Command Center v2
Tabs: Dashboard | Chat | Commands | Tools
ET timestamps, touch-friendly, iPhone 16 Safari optimized."""
import os,sqlite3,json,re
from datetime import datetime,timezone,timedelta
from fastapi import FastAPI,Request
from fastapi.responses import HTMLResponse,JSONResponse
import uvicorn,anthropic

app=FastAPI()
DB='/home/jovyan/shared/sm/prediction-agent-db/prediction_agent.db'
client=anthropic.Anthropic()

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
        for tbl in [
            'CREATE TABLE IF NOT EXISTS agent_commands(id INTEGER PRIMARY KEY,ts TEXT,command TEXT,status TEXT DEFAULT chr(39)pending chr(39),result TEXT,executed_at TEXT)',
            'CREATE TABLE IF NOT EXISTS tool_experiments(id INTEGER PRIMARY KEY,ts TEXT,tool_name TEXT,description TEXT,test_result TEXT,useful INTEGER,notes TEXT)',
        ]: c.execute(tbl.replace('chr(39)','"').replace('pending ','pending').replace(" '",'"'))
        c.execute(sql,p); c.commit(); c.close(); return True
    except Exception as e: print('DB write error:',e); return False

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

def get_context_summary(d):
    logs=d['logs'][:15]
    res=d['res']
    recent_log_text='\n'.join(f"{to_et(l['ts'])} [{l['lvl']}] {l['msg'][:100]}" for l in logs)
    tools_text='\n'.join(f"{t['tool_name']}: {'useful' if t['useful'] else 'not useful'} — {t.get('notes','')[:80]}" for t in d['tools'][:8])
    return f"""SYSTEM STATE as of {to_et(datetime.now(timezone.utc).isoformat())} ET

PAPER TRADES: {res.get('n',0)} resolved | ${float(res.get('pnl') or 0):.2f} P&L | {int((res.get('wr') or 0)*100)}% win rate | {d['open_trades']} open

RECENT ACTIVITY (last 15 log entries):
{recent_log_text}

TOOL EXPERIMENTS COMPLETED:
{tools_text or 'None yet'}

RECENT CODE CHANGES: {len(d['changes'])} total, {sum(1 for c in d['changes'] if c.get('deployed'))} deployed
"""

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
        b=await req.json()
        msg=b.get('message','').strip()
        history=b.get('history',[])
        if not msg: return JSONResponse({'error':'empty'},400)
        d=get_data()
        ctx=get_context_summary(d)
        sys_prompt=f"""You are the AI interface for Karpathy Kapital, an autonomous prediction market trading fund.
You have direct access to the master agent's command queue and full system visibility.

The master agent runs on Saturn Cloud, makes autonomous code improvements every 5-30 min,
tracks calibration toward live Kalshi trading, and manages a portfolio of paper trades.

Current system context:
{ctx}

You can queue commands to the master agent by including [COMMAND: <instruction>] in your response.
The master agent will execute these within the next cycle (within 5 minutes).

Examples of commands you can queue:
- [COMMAND: explore pytrends library for Google Trends signals]
- [COMMAND: increase parallel sub-agents to 4]
- [COMMAND: focus next improvement on model/probability_model.py]
- [COMMAND: generate status report]

Be direct, data-driven, and helpful. This is a Bloomberg terminal, not a chatbot.
"""
        messages=[*history,{'role':'user','content':msg}]
        r=client.messages.create(model='claude-sonnet-4-6',max_tokens=1000,
            system=sys_prompt,messages=messages)
        resp=r.content[0].text
        # Auto-queue any commands the AI decided to issue
        commands=re.findall(r'\[COMMAND: ([^\]]+)\]',resp)
        queued=[]
        for cmd in commands:
            now=datetime.now(timezone.utc).isoformat()
            if w('INSERT INTO agent_commands(ts,command,status) VALUES(?,?,?)',(now,cmd,'pending')):
                queued.append(cmd)
        return JSONResponse({'response':resp,'commands_queued':queued,
            'tokens':r.usage.input_tokens+r.usage.output_tokens})
    except Exception as e: return JSONResponse({'error':str(e)},500)

@app.get('/api/status')
def api_status():
    d=get_data(); logs=d['logs']
    last=logs[0] if logs else {}
    return {'last_action':last.get('msg',''),'last_ts':to_et(last.get('ts','')),'pnl':float(d['res'].get('pnl') or 0),'resolved':d['res'].get('n',0)}

@app.get('/',response_class=HTMLResponse)
def home():
    d=get_data()
    logs=d['logs']; res=d['res']
    now_et=to_et(datetime.now(timezone.utc).isoformat())
    last=logs[0] if logs else {}
    last_ts=to_et(last.get('ts',''))
    last_msg=last.get('msg','No activity yet')[:120]
    stale_color='#ff4444'; stale_label='STALE'
    if last.get('ts'):
        try:
            dt=datetime.fromisoformat(last['ts'].replace('Z',''))
            if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
            age=int((datetime.now(timezone.utc)-dt).total_seconds())
            if age<1800: stale_color='#00ff88'; stale_label=f'{age//60}m {age%60}s ago'
            elif age<7200: stale_color='#ffaa00'; stale_label=f'{age//3600}h ago'
            else: stale_label=f'{age//3600}h ago — CHECK'
        except: pass
    pnl=float(res.get('pnl') or 0)
    pnl_col='#00ff88' if pnl>=0 else '#ff4444'
    def lvl_col(lvl): return {'MILESTONE':'#00ff88','ERROR':'#ff4444','WARN':'#ffaa00'}.get(lvl,'#aaaaaa')
    def log_row(l):
        ts=to_et(l.get('ts','')); c=lvl_col(l['lvl']); msg=l.get('msg','')[:130]
        return f'<div class="log-row"><span class="log-ts">{ts} ET</span><span class="log-lvl" style="color:{c}">{l["lvl"]}</span><span class="log-msg">{msg}</span></div>'
    def change_row(c):
        col='#00ff88' if c.get('deployed') else ('#ffaa00' if c.get('ok') else '#ff4444')
        st='✅ DEPLOYED' if c.get('deployed') else ('🟡 PASSED' if c.get('ok') else '❌ FAILED')
        ts=to_et(c['ts']); f=c.get('file',''); h=c.get('hyp','')[:80]
        return f'<div class="change-row"><div style="display:flex;justify-content:space-between;margin-bottom:3px"><span style="color:{col};font-size:12px;font-weight:bold">{st}</span><span class="log-ts">{ts} ET</span></div><div style="color:#888;font-size:11px">{f}</div><div style="color:#ccc;font-size:13px;margin-top:3px">{h}</div></div>'
    def cmd_row(c):
        col={'pending':'#ffaa00','done':'#00ff88','error':'#ff4444'}.get(c.get('status',''),'#666')
        ts=to_et(c['ts']); cmd=c.get('command','')[:100]; res=c.get('result','')[:80]
        return f'<div class="change-row"><div style="display:flex;justify-content:space-between"><span style="color:{col};font-size:11px;font-weight:bold">{c.get("status","").upper()}</span><span class="log-ts">{ts} ET</span></div><div style="color:#ddd;font-size:13px;margin-top:3px">{cmd}</div>{"<div style=color:#555;font-size:11px;margin-top:2px>"+res+"</div>" if res else ""}</div>'
    def tool_row(t):
        col='#00ff88' if t.get('useful') else '#ff4444'
        v='✅ USEFUL' if t.get('useful') else '❌ SKIP'
        return f'<div class="change-row"><div style="display:flex;justify-content:space-between"><span style="color:{col};font-size:12px;font-weight:bold">{v} — {t.get("tool_name","")}</span><span class="log-ts">{to_et(t.get("ts",""))} ET</span></div><div style="color:#888;font-size:12px;margin-top:3px">{t.get("notes","")[:120]}</div></div>'
    all_logs_html=''.join(log_row(l) for l in logs[:60]) or '<div style="color:#333;padding:12px">No activity yet</div>'
    milestones_html=''.join(log_row(l) for l in [x for x in logs if x['lvl']=='MILESTONE'][:10]) or '<div style="color:#333;padding:12px">No milestones yet</div>'
    errors_html=''.join(log_row(l) for l in [x for x in logs if x['lvl']=='ERROR'][:8]) or '<div style="color:#00ff8844;padding:12px">✓ No errors — system clean</div>'
    changes_html=''.join(change_row(c) for c in d['changes']) or '<div style="color:#333;padding:12px">No changes yet</div>'
    cmds_html=''.join(cmd_row(c) for c in d['cmds']) or '<div style="color:#333;padding:12px">No commands yet</div>'
    tools_html=''.join(tool_row(t) for t in d['tools']) or '<div style="color:#333;padding:12px">Explorer agents will populate this</div>'
    return f"""<!DOCTYPE html>
<html lang='en'><head>
<meta charset='UTF-8'>
<meta name='viewport' content='width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no'>
<meta name='apple-mobile-web-app-capable' content='yes'>
<meta name='apple-mobile-web-app-status-bar-style' content='black-translucent'>
<title>⚡ KK</title>
<style>
:root{{--bg:#080810;--bg2:#0d0d1a;--bg3:#0a0a12;--border:#1a1a2e;--text:#ccc;--dim:#555;}}
*{{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}}
body{{background:var(--bg);color:var(--text);font-family:-apple-system,'SF Pro Display','Segoe UI',monospace;
  min-height:100vh;padding-bottom:70px}}
.hdr{{background:var(--bg);border-bottom:1px solid var(--border);padding:12px 16px;position:sticky;top:0;z-index:100;
  display:flex;justify-content:space-between;align-items:center}}
.hdr-title{{font-size:18px;font-weight:700;color:#fff;letter-spacing:2px}}
.hdr-time{{font-size:11px;color:var(--dim);text-align:right}}
.tab-bar{{position:fixed;bottom:0;left:0;right:0;background:var(--bg);border-top:1px solid var(--border);
  display:flex;z-index:200;padding-bottom:env(safe-area-inset-bottom)}}
.tab{{flex:1;padding:10px 4px 8px;text-align:center;cursor:pointer;border:none;background:none;color:var(--dim);font-size:10px;letter-spacing:1px;text-transform:uppercase}}
.tab.active{{color:#7c3aed}}
.tab-icon{{font-size:20px;display:block;margin-bottom:2px}}
.pane{{display:none;padding:12px 12px 0}}
.pane.active{{display:block}}
.pulse-bar{{background:var(--bg2);border:1px solid {stale_color}44;border-radius:10px;padding:14px;margin-bottom:14px}}
.pulse{{display:inline-block;width:10px;height:10px;border-radius:50%;background:{stale_color};
  box-shadow:0 0 10px {stale_color};animation:p 1.5s infinite;margin-right:8px;flex-shrink:0}}
@keyframes p{{0%,100%{{opacity:1}}50%{{opacity:.3}}}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:14px}}
@media(min-width:600px){{.grid{{grid-template-columns:repeat(4,1fr)}}}}
.card{{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:14px 12px}}
.card .v{{font-size:24px;font-weight:700;color:#fff}}
.card .l{{font-size:10px;color:var(--dim);letter-spacing:1px;text-transform:uppercase;margin-top:4px}}
.sec-title{{font-size:10px;letter-spacing:2px;text-transform:uppercase;color:var(--dim);
  margin:16px 0 8px;padding-bottom:5px;border-bottom:1px solid var(--border)}}
.sec{{background:var(--bg3);border:1px solid var(--border);border-radius:8px;overflow:hidden;margin-bottom:12px}}
.log-row{{padding:8px 10px;border-bottom:1px solid #0f0f1a;}}
.log-ts{{font-size:10px;color:var(--dim);display:block;margin-bottom:2px;font-family:monospace}}
.log-lvl{{font-size:10px;font-weight:700;margin-right:6px}}
.log-msg{{font-size:13px;color:#ddd;line-height:1.4}}
.change-row{{padding:10px;border-bottom:1px solid #0f0f1a}}
.scroll-box{{max-height:400px;overflow-y:auto;-webkit-overflow-scrolling:touch}}
.scroll-box-tall{{max-height:65vh;overflow-y:auto;-webkit-overflow-scrolling:touch}}
/* Chat styles */
.chat-wrap{{display:flex;flex-direction:column;height:calc(100vh - 130px)}}
.chat-msgs{{flex:1;overflow-y:auto;-webkit-overflow-scrolling:touch;padding:8px 0}}
.msg{{margin:8px 0;max-width:88%}}
.msg.user{{margin-left:auto;text-align:right}}
.msg-bubble{{padding:10px 14px;border-radius:16px;font-size:14px;line-height:1.5}}
.msg.user .msg-bubble{{background:#7c3aed;color:#fff;border-bottom-right-radius:4px}}
.msg.ai .msg-bubble{{background:var(--bg2);color:#ddd;border-bottom-left-radius:4px;border:1px solid var(--border)}}
.msg-time{{font-size:10px;color:var(--dim);margin-top:3px;padding:0 4px}}
.chat-input-wrap{{background:var(--bg);border-top:1px solid var(--border);padding:10px 12px;
  padding-bottom:calc(10px + env(safe-area-inset-bottom))}}
.chat-input-row{{display:flex;gap:8px;align-items:flex-end}}
textarea{{background:var(--bg2);border:1px solid var(--border);color:#ddd;padding:10px 14px;
  border-radius:20px;font-family:-apple-system,sans-serif;font-size:15px;
  resize:none;flex:1;max-height:120px;outline:none;-webkit-appearance:none}}
textarea::placeholder{{color:var(--dim)}}
.send-btn{{background:#7c3aed;border:none;width:40px;height:40px;border-radius:50%;
  cursor:pointer;font-size:18px;flex-shrink:0;display:flex;align-items:center;justify-content:center}}
.cmd-input{{background:var(--bg2);border:1px solid var(--border);color:#ddd;padding:12px;
  border-radius:8px;font-size:14px;width:100%;outline:none;margin-bottom:8px;-webkit-appearance:none}}
.btn{{background:#7c3aed;color:#fff;border:none;padding:12px 20px;border-radius:8px;
  cursor:pointer;font-size:14px;width:100%;font-weight:600;margin-top:4px}}
.tag-useful{{background:#00ff8820;color:#00ff88;border:1px solid #00ff8840;font-size:10px;padding:2px 6px;border-radius:4px;margin-left:6px}}
.tag-skip{{background:#ff444420;color:#ff4444;border:1px solid #ff444440;font-size:10px;padding:2px 6px;border-radius:4px;margin-left:6px}}
</style></head><body>

<div class='hdr'>
  <div><div class='hdr-title'>⚡ KK</div><div style='font-size:10px;color:#333;letter-spacing:1px'>KARPATHY KAPITAL</div></div>
  <div class='hdr-time'>{now_et}<br>ET</div>
</div>

<!-- TAB PANES -->
<div id='pane-dash' class='pane active'>
  <div class='pulse-bar'>
    <div style='display:flex;align-items:center;margin-bottom:8px'>
      <span class='pulse'></span>
      <span style='color:{stale_color};font-weight:700;font-size:13px;letter-spacing:1px'>{stale_label}</span>
    </div>
    <div style='color:#fff;font-size:14px;line-height:1.4'>{last_msg}</div>
    <div style='color:var(--dim);font-size:11px;margin-top:5px'>{last_ts} ET</div>
  </div>
  <div class='grid'>
    <div class='card' style='border-color:{pnl_col}33'><div class='v' style='color:{pnl_col}'>${pnl:.2f}</div><div class='l'>Paper P&amp;L</div></div>
    <div class='card'><div class='v'>{res.get('n',0)}</div><div class='l'>Resolved</div></div>
    <div class='card'><div class='v'>{d['open_trades']}</div><div class='l'>Open</div></div>
    <div class='card'><div class='v'>{int((res.get('wr') or 0)*100)}%</div><div class='l'>Win Rate</div></div>
  </div>
  <div class='sec-title'>Last 60 Actions</div>
  <div class='sec scroll-box-tall'>{all_logs_html}</div>
  <div class='sec-title'>Milestones</div>
  <div class='sec scroll-box'>{milestones_html}</div>
  <div class='sec-title'>Errors</div>
  <div class='sec'>{errors_html}</div>
</div>

<div id='pane-chat' class='pane'>
  <div class='chat-wrap'>
    <div id='chat-msgs' class='chat-msgs'><div style='color:var(--dim);text-align:center;padding:40px 20px;font-size:14px'>Chat with your master agent.<br><br>Ask anything — system state, strategy, what to do next.<br>I can also queue commands directly.</div></div>
    <div class='chat-input-wrap'>
      <div class='chat-input-row'>
        <textarea id='chat-input' rows='1' placeholder='Ask the master agent...' oninput='autoResize(this)'></textarea>
        <button class='send-btn' onclick='sendChat()'>↑</button>
      </div>
    </div>
  </div>
</div>

<div id='pane-cmds' class='pane'>
  <div class='sec-title'>Send Order to Master Agent</div>
  <div class='sec' style='padding:12px'>
    <input class='cmd-input' id='cmd-input' placeholder='e.g. explore pytrends, increase agents to 4, run eval now' />
    <button class='btn' onclick='sendCmd()'>⚡ Queue Command</button>
    <div id='cmd-status' style='color:var(--dim);font-size:12px;margin-top:8px'></div>
  </div>
  <div class='sec-title'>Command History</div>
  <div class='sec scroll-box'>{cmds_html}</div>
  <div class='sec-title'>RL Code Changes</div>
  <div class='sec scroll-box'>{changes_html}</div>
</div>

<div id='pane-tools' class='pane'>
  <div style='background:var(--bg2);border:1px solid #7c3aed44;border-radius:8px;padding:12px;margin-bottom:14px'>
    <div style='font-size:12px;color:#7c3aed;font-weight:700;margin-bottom:4px'>EXPLORER AGENTS</div>
    <div style='font-size:13px;color:#888;line-height:1.5'>Every 3rd cycle (~15min), a sub-agent tests a new library and reports if it's useful for prediction market signals. No reinventing wheels.</div>
  </div>
  <div class='sec-title'>Tool Experiments ({len(d['tools'])} tested)</div>
  <div class='sec scroll-box-tall'>{tools_html}</div>
</div>

<!-- TAB BAR -->
<div class='tab-bar'>
  <button class='tab active' id='tab-dash' onclick='switchTab("dash")'>
    <span class='tab-icon'>📊</span>Dashboard</button>
  <button class='tab' id='tab-chat' onclick='switchTab("chat")'>
    <span class='tab-icon'>💬</span>Chat</button>
  <button class='tab' id='tab-cmds' onclick='switchTab("cmds")'>
    <span class='tab-icon'>⚡</span>Commands</button>
  <button class='tab' id='tab-tools' onclick='switchTab("tools")'>
    <span class='tab-icon'>🔬</span>Tools</button>
</div>

<script>
// ── Tab switching
function switchTab(id){{
  document.querySelectorAll('.pane').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.getElementById('pane-'+id).classList.add('active');
  document.getElementById('tab-'+id).classList.add('active');
  if(id==='chat'){{document.getElementById('chat-input').focus();}}
}}
// ── Chat
let chatHistory=[];
function autoResize(el){{el.style.height='auto';el.style.height=Math.min(el.scrollHeight,120)+'px';}}
function addMsg(role,text){{
  const msgs=document.getElementById('chat-msgs');
  const now=new Date().toLocaleTimeString('en-US',{{hour:'2-digit',minute:'2-digit',hour12:true}});
  msgs.innerHTML+=`<div class='msg ${{role}}'><div class='msg-bubble'>${{text}}</div><div class='msg-time'>${{now}}</div></div>`;
  msgs.scrollTop=msgs.scrollHeight;
}}
async function sendChat(){{
  const inp=document.getElementById('chat-input');
  const msg=inp.value.trim(); if(!msg)return;
  addMsg('user',msg); inp.value=''; inp.style.height='auto';
  chatHistory.push({{role:'user',content:msg}});
  addMsg('ai','<i style="color:#555">Thinking...</i>');
  try{{
    const r=await fetch('/chat',{{method:'POST',headers:{{'Content-Type':'application/json'}},
      body:JSON.stringify({{message:msg,history:chatHistory.slice(0,-1)}})}});
    const j=await r.json();
    const msgs=document.getElementById('chat-msgs');
    const bubbles=msgs.querySelectorAll('.msg.ai');
    const last=bubbles[bubbles.length-1];
    if(last)last.querySelector('.msg-bubble').innerHTML=j.response?.replace(/\n/g,'<br>')||'Error: '+j.error;
    chatHistory.push({{role:'assistant',content:j.response||''}});
    if(j.commands_queued?.length){{
      addMsg('ai',`✅ Queued ${{j.commands_queued.length}} command(s) to master agent:\n${{j.commands_queued.join('\n')}}`);
    }}
  }}catch(e){{addMsg('ai','Connection error: '+e);}}
}}
document.getElementById('chat-input').addEventListener('keydown',function(e){{
  if(e.key==='Enter'&&!e.shiftKey){{e.preventDefault();sendChat();}}
}});
// ── Command queue
async function sendCmd(){{
  const inp=document.getElementById('cmd-input');
  const cmd=inp.value.trim(); if(!cmd)return;
  const st=document.getElementById('cmd-status');
  st.textContent='Sending...';st.style.color='#ffaa00';
  try{{
    const r=await fetch('/command',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{command:cmd}})}});
    const j=await r.json();
    if(j.ok){{st.textContent='✓ Queued at '+j.queued_at+' ET';st.style.color='#00ff88';inp.value='';}}
    else st.textContent='Error: '+j.error;
  }}catch(e){{st.textContent='Error: '+e;st.style.color='#ff4444';}}
}}
// Auto-refresh every 30s (dashboard tab only)
setInterval(()=>{{
  if(document.getElementById('pane-dash').classList.contains('active'))location.reload();
}},30000);
</script>
</body></html>"""

if __name__=='__main__':
    uvicorn.run(app,host='0.0.0.0',port=8000,reload=False)