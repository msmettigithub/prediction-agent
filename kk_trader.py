import os, time, threading, subprocess, json
from datetime import datetime
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

app = FastAPI()
state = {
    "cycles": 0, "last_run_ms": 0, "mode": "paper",
    "started": datetime.utcnow().isoformat(), "killed": False,
    "last_output": "", "errors": 0
}

def run_loop():
    while not state["killed"]:
        t0 = time.time()
        try:
            mode = state["mode"]
            result = subprocess.run(
                ["python", "main.py", mode, "--auto"],
                capture_output=True, text=True, timeout=25,
                cwd="/home/jovyan/workspace/prediction-agent"
            )
            state["last_output"] = (result.stdout + result.stderr)[-2000:]
            state["cycles"] += 1
            state["last_run_ms"] = int((time.time() - t0) * 1000)
            # Kill switch
            max_loss = float(os.environ.get("MAX_LIVE_BANKROLL", "50"))
            if state["mode"] == "live":
                try:
                    pnl_line = [l for l in state["last_output"].split("\n") if "pnl" in l.lower()]
                    if pnl_line:
                        pnl = float(pnl_line[-1].split(":")[-1].strip().replace(",",""))
                        if pnl < -max_loss:
                            state["killed"] = True
                            state["mode"] = "paper"
                except: pass
        except Exception as e:
            state["errors"] += 1
            state["last_output"] = str(e)
        time.sleep(30)

threading.Thread(target=run_loop, daemon=True).start()

@app.get("/health")
def health():
    return {
        "status": "killed" if state["killed"] else "ok",
        "cycles": state["cycles"],
        "mode": state["mode"],
        "last_run_ms": state["last_run_ms"],
        "errors": state["errors"],
        "uptime_s": int((datetime.utcnow() - datetime.fromisoformat(state["started"])).total_seconds())
    }

@app.get("/api/state")
def api_state():
    try:
        import sqlite3
        db = "/home/jovyan/shared/sm/prediction-agent-db/prediction_agent.db"
        con = sqlite3.connect(db)
        cur = con.cursor()
        cur.execute("SELECT COUNT(*) FROM trades WHERE resolved=1")
        resolved = cur.fetchone()[0]
        cur.execute("SELECT COALESCE(SUM(pnl),0) FROM trades")
        pnl = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM trades WHERE resolved=0")
        open_trades = cur.fetchone()[0]
        con.close()
    except Exception as e:
        resolved, pnl, open_trades = 0, 0, 0
    return JSONResponse({
        "cycles": state["cycles"],
        "mode": state["mode"],
        "resolved": resolved,
        "pnl": round(pnl, 2),
        "open_trades": open_trades,
        "last_run_ms": state["last_run_ms"],
        "errors": state["errors"],
        "killed": state["killed"],
        "gate_open": resolved >= 30,
        "last_output_tail": state["last_output"][-500:]
    })

@app.post("/set_mode")
def set_mode(body: dict):
    live_enabled = os.environ.get("LIVE_TRADING_ENABLED", "false").lower() == "true"
    if body.get("mode") == "live" and not live_enabled:
        return JSONResponse({"error": "LIVE_TRADING_ENABLED is false"}, status_code=403)
    state["mode"] = body.get("mode", "paper")
    state["killed"] = False
    return {"mode": state["mode"]}

@app.post("/kill")
def kill():
    state["killed"] = True
    return {"status": "killed"}

@app.get("/", response_class=HTMLResponse)
def dashboard():
    return """<!DOCTYPE html>
<html><head><title>Karpathy Kapital</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{font-family:monospace;background:#0a0a0a;color:#00ff88;padding:20px;margin:0}
h1{color:#ffd700;font-size:1.4em}
.card{background:#111;border:1px solid #333;border-radius:8px;padding:16px;margin:12px 0}
.metric{display:inline-block;margin:8px 16px 8px 0}
.val{font-size:1.6em;color:#ffd700}
.lbl{font-size:0.75em;color:#888}
.ok{color:#00ff88}.warn{color:#ffd700}.bad{color:#ff4444}
pre{background:#000;padding:12px;border-radius:4px;overflow-x:auto;font-size:0.8em;color:#aaa}
button{background:#ffd700;color:#000;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;font-weight:bold;margin:4px}
button.red{background:#ff4444;color:#fff}
</style></head>
<body>
<h1>⚡ Karpathy Kapital</h1>
<div class="card" id="status">Loading...</div>
<div class="card">
<button onclick="fetch('/kill',{method:'POST'}).then(()=>load())">🛑 KILL</button>
<button onclick="fetch('/set_mode',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode:'paper'})}).then(()=>load())">📄 Paper</button>
</div>
<div class="card"><pre id="log">...</pre></div>
<script>
function load(){
  fetch('/api/state').then(r=>r.json()).then(d=>{
    document.getElementById('status').innerHTML=`
      <div class="metric"><div class="val">${d.cycles}</div><div class="lbl">CYCLES</div></div>
      <div class="metric"><div class="val ${d.pnl>=0?'ok':'bad'}">$${d.pnl}</div><div class="lbl">P&L</div></div>
      <div class="metric"><div class="val">${d.resolved}</div><div class="lbl">RESOLVED</div></div>
      <div class="metric"><div class="val">${d.open_trades}</div><div class="lbl">OPEN</div></div>
      <div class="metric"><div class="val ${d.mode=='live'?'bad':'ok'}">${d.mode.toUpperCase()}</div><div class="lbl">MODE</div></div>
      <div class="metric"><div class="val">${d.last_run_ms}ms</div><div class="lbl">LAST RUN</div></div>
      <div class="metric"><div class="val ${d.gate_open?'ok':'warn'}">${d.gate_open?'OPEN':'LOCKED'}</div><div class="lbl">GATE</div></div>
    `;
    document.getElementById('log').textContent=d.last_output_tail;
  });
}
load(); setInterval(load, 10000);
</script>
</body></html>"""
import urllib.request as _u2,json as _j2,os as _o2
_SAT="https://app.community.saturnenterprise.io/api"
_MA="18efeea066bc4d828b984ec1d752d131"
def _sg(p):
    t=_o2.environ.get("SATURN_API_TOKEN","")
    r=_u2.Request(_SAT+p,headers={"Authorization":"token "+t})
    with _u2.urlopen(r) as x: return _j2.loads(x.read())
def _sp(p,b=None,m="PATCH"):
    t=_o2.environ.get("SATURN_API_TOKEN","")
    d=_j2.dumps(b or {}).encode()
    r=_u2.Request(_SAT+p,data=d,headers={"Authorization":"token "+t,"Content-Type":"application/json"},method=m)
    with _u2.urlopen(r) as x: return _j2.loads(x.read())
@app.get("/api/controls")
def get_controls():
    dep=_sg("/deployments/"+_MA)
    env=dep.get("environment_variables",{})
    return {"status":dep.get("status","?"),"daily_budget":env.get("DAILY_BUDGET","999999"),"cycle_seconds":env.get("CYCLE_SECONDS","90"),"agent_paused":env.get("AGENT_PAUSED","false")}
@app.post("/api/controls")
def set_controls(daily_budget:str=None,cycle_seconds:str=None,agent_paused:str=None):
    dep=_sg("/deployments/"+_MA)
    env=dep.get("environment_variables",{})
    if daily_budget is not None: env["DAILY_BUDGET"]=daily_budget
    if cycle_seconds is not None: env["CYCLE_SECONDS"]=cycle_seconds
    if agent_paused is not None: env["AGENT_PAUSED"]=agent_paused
    _sp("/deployments/"+_MA,{"environment_variables":env})
    return {"ok":True,**env}
@app.post("/api/master-agent/start")
def ma_start(): _sp("/deployments/"+_MA+"/start",method="POST"); return {"ok":True}
@app.post("/api/master-agent/stop")
def ma_stop(): _sp("/deployments/"+_MA+"/stop",method="POST"); return {"ok":True}
from fastapi.responses import HTMLResponse as _HRx,Response as _Rx
@app.get("/controls",response_class=_HRx)
def controls_ui(): return _HRx(base64.b64decode("PCFET0NUWVBFIGh0bWw+PGh0bWw+PGhlYWQ+PHRpdGxlPktLIENvbnRyb2xzPC90aXRsZT4KPHN0eWxlPip7Ym94LXNpemluZzpib3JkZXItYm94fWJvZHl7Zm9udC1mYW1pbHk6bW9ub3NwYWNlO2JhY2tncm91bmQ6IzBhMGEwYTtjb2xvcjojZTBlMGUwO21heC13aWR0aDo2MDBweDttYXJnaW46NDBweCBhdXRvO3BhZGRpbmc6MjBweH0KaDF7Y29sb3I6I2YwYjQyOTtib3JkZXItYm90dG9tOjFweCBzb2xpZCAjMzMzO3BhZGRpbmctYm90dG9tOjEwcHh9Ci5jYXJke2JhY2tncm91bmQ6IzExMTtib3JkZXI6MXB4IHNvbGlkICMyMjI7Ym9yZGVyLXJhZGl1czo4cHg7cGFkZGluZzoxNnB4O21hcmdpbjoxMHB4IDB9Ci5sYmx7Y29sb3I6IzU1NTtmb250LXNpemU6MTFweDt0ZXh0LXRyYW5zZm9ybTp1cHBlcmNhc2U7bGV0dGVyLXNwYWNpbmc6MXB4O21hcmdpbi1ib3R0b206NnB4fQppbnB1dHtiYWNrZ3JvdW5kOiMwMDA7Y29sb3I6IzBmMDtib3JkZXI6MXB4IHNvbGlkICMzMzM7cGFkZGluZzo2cHggMTBweDtib3JkZXItcmFkaXVzOjRweDt3aWR0aDoxMzBweDtmb250LWZhbWlseTptb25vc3BhY2V9CmJ1dHRvbntwYWRkaW5nOjhweCAxNHB4O2JvcmRlci1yYWRpdXM6NHB4O2JvcmRlcjpub25lO2N1cnNvcjpwb2ludGVyO2ZvbnQtZmFtaWx5Om1vbm9zcGFjZTtmb250LXNpemU6MTNweDttYXJnaW46M3B4fQouZ3tiYWNrZ3JvdW5kOiMxYTVjMWE7Y29sb3I6IzVmNX0ucntiYWNrZ3JvdW5kOiM1YzFhMWE7Y29sb3I6I2Y1NX0ueXtiYWNrZ3JvdW5kOiM1YzRhMGE7Y29sb3I6I2ZjNX0uYntiYWNrZ3JvdW5kOiMwYTJhNWM7Y29sb3I6IzVhZn0KI21zZ3ttYXJnaW4tdG9wOjEwcHg7Zm9udC1zaXplOjEycHg7Y29sb3I6IzY2Nn0KPC9zdHlsZT48L2hlYWQ+PGJvZHk+CjxoMT4mIzk4ODk7IEtLIENvbnRyb2wgUGFuZWw8L2gxPgo8ZGl2IGNsYXNzPSJjYXJkIj48ZGl2IGNsYXNzPSJsYmwiPlN0YXR1czwvZGl2PjxkaXYgaWQ9InN0IiBzdHlsZT0iZm9udC1zaXplOjE4cHg7Zm9udC13ZWlnaHQ6Ym9sZCI+Li4uPC9kaXY+PGRpdiBpZD0ic20iIHN0eWxlPSJmb250LXNpemU6MTJweDtjb2xvcjojNTU1O21hcmdpbi10b3A6NHB4Ij48L2Rpdj48L2Rpdj4KPGRpdiBjbGFzcz0iY2FyZCI+PGRpdiBjbGFzcz0ibGJsIj5EYWlseSBCdWRnZXQgKFVTRCAtIDk5OTk5OT11bmxpbWl0ZWQpPC9kaXY+PGlucHV0IGlkPSJidiIgdHlwZT0ibnVtYmVyIiBzdGVwPSIxMCIvPjxidXR0b24gY2xhc3M9InkiIG9uY2xpY2s9InNjKCdkYWlseV9idWRnZXQnLGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdidicpLnZhbHVlKSI+U2V0PC9idXR0b24+PC9kaXY+CjxkaXYgY2xhc3M9ImNhcmQiPjxkaXYgY2xhc3M9ImxibCI+Q3ljbGUgU3BlZWQgKHNlY29uZHMpPC9kaXY+PGlucHV0IGlkPSJjdiIgdHlwZT0ibnVtYmVyIiBzdGVwPSIxMCIvPjxidXR0b24gY2xhc3M9ImIiIG9uY2xpY2s9InNjKCdjeWNsZV9zZWNvbmRzJyxkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnY3YnKS52YWx1ZSkiPlNldDwvYnV0dG9uPjwvZGl2Pgo8ZGl2IGNsYXNzPSJjYXJkIj48ZGl2IGNsYXNzPSJsYmwiPkFnZW50IENvbnRyb2w8L2Rpdj4KPGJ1dHRvbiBjbGFzcz0iciIgb25jbGljaz0ic2MoJ2FnZW50X3BhdXNlZCcsJ3RydWUnKSI+UGF1c2U8L2J1dHRvbj4KPGJ1dHRvbiBjbGFzcz0iZyIgb25jbGljaz0ic2MoJ2FnZW50X3BhdXNlZCcsJ2ZhbHNlJykiPlJlc3VtZTwvYnV0dG9uPgo8YnV0dG9uIGNsYXNzPSJyIiBvbmNsaWNrPSJtYSgnc3RvcCcpIj5TdG9wPC9idXR0b24+CjxidXR0b24gY2xhc3M9ImciIG9uY2xpY2s9Im1hKCdzdGFydCcpIj5TdGFydDwvYnV0dG9uPjwvZGl2Pgo8ZGl2IGlkPSJtc2ciPjwvZGl2Pgo8c2NyaXB0Pgphc3luYyBmdW5jdGlvbiBsb2FkKCl7Y29uc3Qgcj1hd2FpdCBmZXRjaCgnL2FwaS9jb250cm9scycpLGQ9YXdhaXQgci5qc29uKCk7CmRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdzdCcpLmlubmVySFRNTD0nPHNwYW4gc3R5bGU9ImNvbG9yOicrKGQuc3RhdHVzPT09J3J1bm5pbmcnPycjNWY1JzonI2Y1NScpKyciPicrZC5zdGF0dXMudG9VcHBlckNhc2UoKSsnPC9zcGFuPic7CmRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdzbScpLnRleHRDb250ZW50PSdCdWRnZXQ6ICQnK2QuZGFpbHlfYnVkZ2V0KycgfCBDeWNsZTogJytkLmN5Y2xlX3NlY29uZHMrJ3MgfCBQYXVzZWQ6ICcrZC5hZ2VudF9wYXVzZWQ7CmRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdidicpLnZhbHVlPWQuZGFpbHlfYnVkZ2V0O2RvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdjdicpLnZhbHVlPWQuY3ljbGVfc2Vjb25kczt9CmFzeW5jIGZ1bmN0aW9uIHNjKGssdil7ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ21zZycpLnRleHRDb250ZW50PSdVcGRhdGluZy4uLic7CmNvbnN0IHI9YXdhaXQgZmV0Y2goJy9hcGkvY29udHJvbHM/JytrKyc9JytlbmNvZGVVUklDb21wb25lbnQodikse21ldGhvZDonUE9TVCd9KTsKZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ21zZycpLnRleHRDb250ZW50PUpTT04uc3RyaW5naWZ5KGF3YWl0IHIuanNvbigpKTtsb2FkKCk7fQphc3luYyBmdW5jdGlvbiBtYShhKXtkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnbXNnJykudGV4dENvbnRlbnQ9J1NlbmRpbmcgJythKycuLi4nOwphd2FpdCBmZXRjaCgnL2FwaS9tYXN0ZXItYWdlbnQvJythLHttZXRob2Q6J1BPU1QnfSk7c2V0VGltZW91dChsb2FkLDIwMDApO30KbG9hZCgpO3NldEludGVydmFsKGxvYWQsMTAwMDApOwo8L3NjcmlwdD48L2JvZHk+PC9odG1sPg==").decode())
