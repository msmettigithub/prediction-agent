#!/usr/bin/env python3
"""Karpathy Kapital — Master Agent Dashboard"""
import os, sqlite3, json
from datetime import datetime, timezone
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import uvicorn

app = FastAPI()
DB = '/home/jovyan/shared/sm/prediction-agent-db/prediction_agent.db'

def q(sql, params=()):
    if not os.path.exists(DB): return []
    try:
        c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
        rows = c.execute(sql, params).fetchall()
        c.close(); return [dict(r) for r in rows]
    except: return []

def get_data():
    logs = q('SELECT ts, lvl, agent, msg FROM agent_log ORDER BY ts DESC LIMIT 100')
    trades = q("SELECT COUNT(*) n FROM paper_trades WHERE status='active'")
    resolved = q("SELECT COUNT(*) n, COALESCE(SUM(pnl),0) pnl, AVG(CASE WHEN pnl>0 THEN 1.0 ELSE 0.0 END) wr FROM paper_trades WHERE status='resolved'")
    changes = q('SELECT ts, hyp, file, ok, deployed FROM agent_changes ORDER BY ts DESC LIMIT 20')
    return {'logs': logs, 'open_trades': trades[0]['n'] if trades else 0,
            'resolved': resolved[0] if resolved else {'n':0,'pnl':0,'wr':0},
            'changes': changes}

@app.get('/', response_class=HTMLResponse)
def dashboard():
    d = get_data()
    logs = d['logs']
    last_ts = logs[0]['ts'] if logs else None
    last_action = logs[0]['msg'][:80] if logs else 'No activity yet'
    last_lvl = logs[0]['lvl'] if logs else 'INFO'
    milestones = [l for l in logs if l['lvl'] == 'MILESTONE'][:5]
    errors = [l for l in logs if l['lvl'] == 'ERROR'][:5]
    info_logs = logs[:50]
    changes = d['changes']
    res = d['resolved']
    now = datetime.now(timezone.utc).isoformat()[:19] + 'Z'
    
    # Time since last action
    time_status = '#ff4444'
    time_label = 'STALE'
    if last_ts:
        try:
            from datetime import timedelta
            last_dt = datetime.fromisoformat(last_ts.replace('Z',''))
            if last_dt.tzinfo is None: last_dt = last_dt.replace(tzinfo=timezone.utc)
            diff = (datetime.now(timezone.utc) - last_dt).total_seconds()
            if diff < 1800: time_status = '#00ff88'; time_label = f'{int(diff//60)}m ago'
            elif diff < 7200: time_status = '#ffaa00'; time_label = f'{int(diff//3600)}h ago'
            else: time_status = '#ff4444'; time_label = f'{int(diff//3600)}h ago — CHECK'
        except: pass
    
    def log_row(l):
        color = {'MILESTONE':'#00ff88','ERROR':'#ff4444','WARN':'#ffaa00'}.get(l['lvl'],'#aaaaaa')
        ts = l['ts'][:19] if l['ts'] else ''
        return f'<tr><td style="color:#666;font-size:11px;white-space:nowrap">{ts}</td><td style="color:{color};font-weight:bold;font-size:11px">{l["lvl"]}</td><td style="color:#ddd;font-size:12px">{l["msg"][:120]}</td></tr>'
    
    def change_row(c):
        ok_col = '#00ff88' if c.get('deployed') else ('#ffaa00' if c.get('ok') else '#ff4444')
        status = 'DEPLOYED' if c.get('deployed') else ('TESTED' if c.get('ok') else 'FAILED')
        ts = c['ts'][:19] if c['ts'] else ''
        return f'<tr><td style="color:#666;font-size:11px">{ts}</td><td style="color:{ok_col};font-size:11px;font-weight:bold">{status}</td><td style="color:#aaa;font-size:11px">{(c.get("file") or "")}</td><td style="color:#ddd;font-size:12px">{(c.get("hyp") or "")[:80]}</td></tr>'
    
    html = f'''<!DOCTYPE html>
<html><head><title>Karpathy Kapital — Command Center</title>
<meta http-equiv="refresh" content="30">
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ background:#0a0a0f; color:#ddd; font-family:"SF Mono","Fira Code",monospace; padding:24px; }}
h1 {{ color:#fff; font-size:28px; letter-spacing:4px; text-transform:uppercase; }}
h2 {{ color:#888; font-size:13px; letter-spacing:3px; text-transform:uppercase; margin:20px 0 10px; border-bottom:1px solid #222; padding-bottom:6px; }}
.header {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:24px; border-bottom:1px solid #1a1a2e; padding-bottom:16px; }}
.pulse {{ display:inline-block; width:10px; height:10px; border-radius:50%; background:{time_status}; box-shadow:0 0 10px {time_status}; animation:pulse 2s infinite; margin-right:8px; }}
@keyframes pulse {{ 0%,100%{{opacity:1}} 50%{{opacity:0.4}} }}
.grid {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin-bottom:20px; }}
.card {{ background:#0f0f1a; border:1px solid #1a1a2e; border-radius:8px; padding:16px; }}
.card .val {{ font-size:28px; font-weight:bold; color:#fff; }}
.card .lbl {{ font-size:11px; color:#555; letter-spacing:2px; text-transform:uppercase; margin-top:4px; }}
.card.good {{ border-color:#00ff8833; }}
.card.warn {{ border-color:#ffaa0033; }}
.card.bad {{ border-color:#ff444433; }}
table {{ width:100%; border-collapse:collapse; }}
tr:hover {{ background:#0f0f1a; }}
td {{ padding:6px 8px; border-bottom:1px solid #111; vertical-align:top; }}
.section {{ background:#0a0a12; border:1px solid #1a1a2e; border-radius:8px; padding:16px; margin-bottom:16px; }}
.last-action {{ background:#0f0f1a; border:1px solid {time_status}44; border-radius:8px; padding:16px; margin-bottom:20px; }}
.countdown {{ color:#555; font-size:11px; }}
</style></head><body>
<div class="header">
  <div><h1>⚡ Karpathy Kapital</h1><div style="color:#555;font-size:12px;margin-top:4px">Autonomous Prediction Market Fund — Command Center</div></div>
  <div style="text-align:right"><div style="color:#555;font-size:11px">SYSTEM TIME</div><div style="color:#fff;font-size:14px">{now}</div><div class="countdown" style="margin-top:4px">↻ auto-refresh 30s</div></div>
</div>

<div class="last-action">
  <div style="display:flex;align-items:center;margin-bottom:8px">
    <span class="pulse"></span>
    <span style="color:{time_status};font-weight:bold;font-size:13px;letter-spacing:2px">LAST ACTION — {time_label}</span>
  </div>
  <div style="color:#fff;font-size:14px">{last_action}</div>
  <div style="color:#555;font-size:11px;margin-top:4px">{last_ts}</div>
</div>

<div class="grid">
  <div class="card {'good' if float(res.get('pnl',0) or 0) >= 0 else 'bad'}"><div class="val">${float(res.get('pnl',0) or 0):.2f}</div><div class="lbl">Paper P&amp;L</div></div>
  <div class="card"><div class="val">{res.get('n',0)}</div><div class="lbl">Resolved Trades</div></div>
  <div class="card"><div class="val">{d['open_trades']}</div><div class="lbl">Open Positions</div></div>
  <div class="card {'good' if (res.get('wr') or 0) > 0.55 else 'warn'}"><div class="val">{int((res.get('wr') or 0)*100)}%</div><div class="lbl">Win Rate (last 20)</div></div>
</div>

<h2>Milestones</h2>
<div class="section">
  <table>{''.join(log_row(l) for l in milestones) or '<tr><td style="color:#444">No milestones yet</td></tr>'}</table>
</div>

<h2>Recent Errors</h2>
<div class="section">
  <table>{''.join(log_row(l) for l in errors) or '<tr><td style="color:#444">No errors — system clean ✓</td></tr>'}</table>
</div>

<h2>Code Changes (RL Actions)</h2>
<div class="section">
  <table><tr><th style="color:#555;font-size:10px;text-align:left;padding:4px 8px">TIME</th><th style="color:#555;font-size:10px;text-align:left;padding:4px 8px">STATUS</th><th style="color:#555;font-size:10px;text-align:left;padding:4px 8px">FILE</th><th style="color:#555;font-size:10px;text-align:left;padding:4px 8px">HYPOTHESIS</th></tr>
  {''.join(change_row(c) for c in changes) or '<tr><td colspan="4" style="color:#444">No changes yet</td></tr>'}
  </table>
</div>

<h2>Full Activity Log</h2>
<div class="section" style="max-height:400px;overflow-y:auto">
  <table>{''.join(log_row(l) for l in info_logs)}</table>
</div>

</body></html>'''
    return html

if __name__ == '__main__':
    uvicorn.run(app, host='0.0.0.0', port=8000)