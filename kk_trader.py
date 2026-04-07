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