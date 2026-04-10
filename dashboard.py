#!/usr/bin/env python3
"""Karpathy Kapital Dashboard v3 — responsive mobile + desktop, chat tab, wiki integration"""
import os,sqlite3,json,re
from datetime import datetime,timezone,timedelta
from fastapi import FastAPI,Request
from fastapi.responses import HTMLResponse,JSONResponse
import uvicorn,anthropic

app=FastAPI()
DB='/home/jovyan/shared/sm/prediction-agent-db/prediction_agent.db'
TRADE_DB=str(__import__('pathlib').Path(__file__).parent/'prediction_agent.db')
_api_key=os.environ.get('ANTHROPIC_API_KEY') or os.environ.get('ANTHROPIC') or ''
client=anthropic.Anthropic(api_key=_api_key) if _api_key else None
WIKI_URL='https://raw.githubusercontent.com/msmettigithub/prediction-agent/main/WIKI.md'

def to_et(ts):
    if not ts: return ''
    try:
        dt=datetime.fromisoformat(str(ts).replace('Z','').split('.')[0])
        if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
        et=dt+timedelta(hours=-4 if 3<=dt.month<=10 else -5)
        return et.strftime('%m/%d/%Y %H:%M:%S')
    except: return str(ts)[:19]

def q(sql,p=(),db=None):
    d=db or DB
    if not os.path.exists(d): return []
    try:
        c=sqlite3.connect(d); c.row_factory=sqlite3.Row
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
    open_=q("SELECT COUNT(*) n FROM paper_trades WHERE status='open'",db=TRADE_DB)
    res=q("SELECT COUNT(*) n,COALESCE(SUM(pnl),0) pnl,AVG(CASE WHEN pnl>0 THEN 1.0 ELSE 0.0 END) wr FROM paper_trades WHERE status IN ('won','lost')",db=TRADE_DB)
    positions=q("""SELECT pt.id,pt.side,pt.entry_price,pt.model_prob,pt.bet_amount,pt.status,pt.pnl,pt.opened_at,pt.closed_at,
        c.title,c.source_id,c.yes_price,c.close_time FROM paper_trades pt JOIN contracts c ON pt.contract_id=c.id
        ORDER BY CASE pt.status WHEN 'open' THEN 0 ELSE 1 END, pt.opened_at DESC""",db=TRADE_DB)
    changes=q('SELECT ts,hyp,file,ok,deployed FROM agent_changes ORDER BY ts DESC LIMIT 30')
    cmds=q("SELECT id,ts,command,status,result FROM agent_commands ORDER BY ts DESC LIMIT 30")
    tools=q('SELECT ts,tool_name,useful,notes FROM tool_experiments ORDER BY ts DESC LIMIT 20') if q("SELECT name FROM sqlite_master WHERE type='table' AND name='tool_experiments'") else []
    wti=q("""SELECT id,ticker,strike,side,entry_price,shares,cost,model_prob,edge_at_entry,status,exit_price,pnl,opened_at,closed_at
        FROM wti_paper_trades ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END, opened_at DESC""",db=TRADE_DB) if q("SELECT name FROM sqlite_master WHERE type='table' AND name='wti_paper_trades'",db=TRADE_DB) else []
    return dict(logs=logs,open_trades=open_[0]['n'] if open_ else 0,
                res=res[0] if res else {'n':0,'pnl':0,'wr':0},
                positions=positions,wti=wti,changes=changes,cmds=cmds,tools=tools)

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
        if not client: return JSONResponse({'error':'ANTHROPIC_API_KEY not set'},500)
        b=await req.json(); msg=b.get('message','').strip(); history=b.get('history',[])
        if not msg: return JSONResponse({'error':'empty'},400)
        d=get_data(); ctx=get_context(d)
        # Add live data feeds to context
        import subprocess
        try:
            feed_line=subprocess.run(['tail','-1','/tmp/smart_trader.log'],capture_output=True,text=True,timeout=2).stdout.strip()
        except: feed_line=''
        # Add portfolio balance + live positions
        try:
            from config import load_config
            from live.kalshi_trader import KalshiTrader
            import requests as _rq
            config=load_config()
            trader=KalshiTrader(config)
            balance=trader.get_balance()
            bal_str=f'Balance: ${balance:,.2f}'
            # Fetch live positions
            _purl=f'{trader.TRADING_URL}/portfolio/positions'
            _pr=_rq.get(_purl,headers=trader._signed_headers('GET',_purl),params={'limit':200},timeout=10)
            _positions=_pr.json().get('market_positions',[]) if _pr.status_code==200 else []
            _open=[p for p in _positions if float(p.get('market_exposure_dollars','0') or 0)>0]
            _total_exp=sum(float(p.get('market_exposure_dollars','0') or 0) for p in _open)
            _total_pnl=sum(float(p.get('realized_pnl_dollars','0') or 0) for p in _positions)
            pos_str=f' | {len(_open)} live positions | exposure ${_total_exp:.0f} | realized P&L ${_total_pnl:+.0f}'
            # Top 5 positions by exposure for context
            _open.sort(key=lambda p:-float(p.get('market_exposure_dollars','0') or 0))
            top_pos='\n'.join(f"  {p.get('ticker','')} pos={float(p.get('position_fp','0')):+.0f} exp=${float(p.get('market_exposure_dollars','0')):.0f} pnl=${float(p.get('realized_pnl_dollars','0')):+.2f}" for p in _open[:8])
            bal_str+=pos_str+'\nTOP POSITIONS:\n'+top_pos
        except: bal_str=''
        live_ctx=f'\nLIVE FEEDS: {feed_line}\n{bal_str}'
        # Get scan candidates for context
        scan_ctx=''
        cands=q("SELECT source_id,title,market_price,model_prob,edge,confidence,recommendation FROM scan_candidates WHERE ABS(edge)>0.05 ORDER BY ts DESC LIMIT 5",db=TRADE_DB)
        if cands:
            scan_ctx='\nSCAN CANDIDATES (data-backed edges):\n'+'\n'.join(f"  {c['source_id']}: {c['title'][:60]} mkt={c['market_price']:.0%} model={c['model_prob']:.0%} edge={c['edge']:+.1%} {c['recommendation']}" for c in cands)
        sys_p=f'''You are the AI trading interface for Karpathy Kapital, an autonomous prediction market fund trading REAL MONEY on Kalshi.
Full system context:
{ctx}{live_ctx}{scan_ctx}

ACTIONS — these execute immediately when you output them:
1. [TRADE: ticker=KXCPI-26APR-T0.8 side=YES amount=25 force=true] — LIVE trade on Kalshi (real money, default)
2. [TRADE: ticker=... side=... amount=... mode=paper force=true] — paper trade only
3. [SEARCH: keyword] — search Kalshi for markets matching keyword
4. [SCAN] — show data-backed opportunities from scanner
5. [COMMAND: text] — ONLY for master agent ops. NEVER for trades or searches.

RULES:
- ALL trades are LIVE by default. Real money on Kalshi. The user knows this.
- When the user tells you to trade, output [TRADE: ... force=true]. Execute immediately. No essays.
- When the user asks to find/search markets, output [SEARCH: keyword].
- Output exactly ONE tag per action. Never duplicate.
- Keep responses to 2-3 lines. Show: ticker, side, price, amount. That is it.'''
        r=client.messages.create(model='claude-sonnet-4-6',max_tokens=1000,system=sys_p,
            messages=[*history,{'role':'user','content':msg}])
        resp=r.content[0].text
        cmds=re.findall(r'\[COMMAND: ([^\]]+)\]',resp)
        for cmd in cmds:
            w('INSERT INTO agent_commands(ts,command,status) VALUES(?,?,?)',
              (datetime.now(timezone.utc).isoformat(),cmd,'pending'))
        # Execute trades
        trades_executed=[]
        trade_tags=re.findall(r'\[TRADE: ([^\]]+)\]',resp)
        for tag in trade_tags:
            parts=dict(re.findall(r'(\w+)=(\S+)',tag))
            if parts.get('ticker'):
                import httpx
                try:
                    trade_req={'ticker':parts['ticker'],'side':parts.get('side','YES'),
                        'amount':float(parts.get('amount',0)),'force':parts.get('force','false').lower()=='true',
                        'mode':parts.get('mode','live')}
                    # Call our own trade endpoint
                    async with httpx.AsyncClient() as hc:
                        tr=await hc.post('http://localhost:8000/api/trade',json=trade_req,timeout=30)
                        result=tr.json()
                        trades_executed.append(result)
                except Exception as te: trades_executed.append({'error':str(te),'ticker':parts.get('ticker')})
        # Execute searches
        search_results=[]
        search_tags=re.findall(r'\[SEARCH: ([^\]]+)\]',resp)
        for keyword in search_tags:
            try:
                import requests as _req
                kw=keyword.strip(); kw_lower=kw.lower()
                headers={'Accept':'application/json'}
                # Search via events endpoint (has titles, subtitles, categories)
                cursor=None
                for _ in range(8):
                    params={'limit':200,'status':'open'}
                    if cursor: params['cursor']=cursor
                    r=_req.get('https://api.elections.kalshi.com/trade-api/v2/events',headers=headers,params=params,timeout=15)
                    if r.status_code!=200: break
                    data=r.json(); events=data.get('events',[])
                    for e in events:
                        haystack=((e.get('title','')or'')+'|'+(e.get('sub_title','')or'')+'|'+(e.get('event_ticker','')or'')+'|'+(e.get('category','')or'')).lower()
                        if kw_lower in haystack:
                            # Fetch markets for this event
                            try:
                                r2=_req.get(f'https://api.elections.kalshi.com/trade-api/v2/events/{e["event_ticker"]}',headers=headers,timeout=10)
                                if r2.status_code==200:
                                    for m in r2.json().get('markets',[]):
                                        yp=m.get('yes_ask',0) or 0; yp=yp/100 if yp>1 else yp
                                        search_results.append({'ticker':m.get('ticker',''),'title':(e.get('title','')+(': '+(m.get('title','')or'')[:50]) if m.get('title') else '')[:100],'yes_price':yp,'volume_24h':m.get('volume_24h',0) or 0,'close_time':m.get('close_date','')or m.get('expiration_time','')or''})
                            except: pass
                    cursor=data.get('cursor')
                    if not cursor or not events or len(search_results)>=15: break
                # Also search local DB
                local=q("SELECT source_id,title,yes_price,volume_24h,close_time FROM contracts WHERE title LIKE ? AND resolution IS NULL ORDER BY close_time LIMIT 10",('%'+kw+'%',),db=TRADE_DB)
                for m in local:
                    if not any(s['ticker']==m['source_id'] for s in search_results):
                        search_results.append({'ticker':m['source_id'],'title':m['title'],'yes_price':m['yes_price'],'volume_24h':m['volume_24h'] or 0,'close_time':m['close_time'] or ''})
            except Exception as se:
                search_results.append({'ticker':'ERROR','title':str(se)[:100],'yes_price':0,'volume_24h':0,'close_time':''})
        # Execute scans
        scan_results=[]
        if '[SCAN]' in resp:
            scan_results=q("SELECT source_id,title,market_price,model_prob,edge,confidence,recommendation FROM scan_candidates WHERE ABS(edge)>0.03 ORDER BY ts DESC, ABS(edge) DESC LIMIT 10",db=TRADE_DB)
        return JSONResponse({'response':resp,'commands_queued':cmds,'trades':trades_executed,'search':search_results,'scan':scan_results})
    except Exception as e: return JSONResponse({'error':str(e)},500)

@app.post('/api/trade')
async def api_trade(req:Request):
    """Execute a trade from the chat interface.
    Fetches contract from Kalshi, runs data modifiers, computes edge, places trade.
    Supports paper trades always. Live trades only if LIVE_TRADING_ENABLED=true.
    """
    try:
        b=await req.json()
        ticker=b.get('ticker','').strip()
        side=b.get('side','YES').upper()
        amount=float(b.get('amount',0))
        force=b.get('force',False)  # skip edge check (manual override)
        mode=b.get('mode','live')  # 'live' (default) or 'paper'
        if not ticker: return JSONResponse({'error':'ticker required'},400)
        if side not in ('YES','NO'): return JSONResponse({'error':'side must be YES or NO'},400)

        from tools.kalshi import KalshiTool
        from model.probability_model import estimate_probability
        from model.edge_calculator import compute_edge
        from model.data_modifiers import get_modifiers_for_contract
        from database.models import Contract
        from database.db import Database
        from config import load_config

        config=load_config()
        kalshi=KalshiTool(mock_mode=False)

        # Fetch contract from Kalshi
        market=kalshi.fetch_single_market(ticker)
        if not market: return JSONResponse({'error':f'Contract {ticker} not found on Kalshi'},404)
        if market.get('resolved'): return JSONResponse({'error':f'Contract {ticker} already resolved'},400)

        mp=market.get('yes_price',0)
        title=market.get('title','')
        category=market.get('category','economics')
        close_time_str=market.get('close_time','')
        close_time=None
        if close_time_str:
            try: close_time=datetime.fromisoformat(close_time_str.replace('Z','+00:00'))
            except: pass

        # Run data modifiers
        mods=get_modifiers_for_contract(source_id=ticker,category=category,market_price=mp,title=title,close_time=close_time)

        # Build contract object
        contract=Contract(id=0,title=title,source='kalshi',source_id=ticker,category=category,
            yes_price=mp,close_time=close_time,open_time=datetime.now(timezone.utc),
            volume_24h=market.get('volume_24h',0))

        # Estimate probability
        estimate=estimate_probability(contract,modifiers=mods,config=config)
        edge_result=compute_edge(estimate,mp,config)

        mod_info=[{'name':m.name,'direction':m.direction,'weight':m.weight,'source':m.source} for m in mods]
        analysis={'ticker':ticker,'title':title,'market_price':mp,'model_prob':round(estimate.probability,4),
            'edge':round(edge_result.edge,4),'confidence':estimate.confidence,
            'recommendation':edge_result.recommendation,'kelly_fraction':round(edge_result.kelly_fraction,4),
            'modifiers':mod_info,'n_modifiers':len(mods)}

        # Determine if we should trade
        if not force and edge_result.recommendation in ('PASS','WATCH'):
            analysis['action']='DECLINED'
            analysis['reason']=f'Model says {edge_result.recommendation} (edge={edge_result.edge:+.1%}, confidence={estimate.confidence}). Use force=true to override.'
            return JSONResponse(analysis)

        # Calculate bet amount
        if amount>0:
            bet_amount=min(amount,config.bankroll*config.kelly_max_bet_pct)
        else:
            bet_amount=edge_result.bet_amount
        if bet_amount<=0:
            analysis['action']='DECLINED'
            analysis['reason']='Kelly sizing says $0 (edge too small)'
            return JSONResponse(analysis)

        entry_price=mp if side=='YES' else (1-mp)

        # Place trade
        db=Database(TRADE_DB)
        contract_id=db.upsert_contract(contract)

        if mode=='live' and config.live_trading_enabled:
            from live.guard import LiveTradingGuard, compute_shares
            from live.kalshi_trader import KalshiTrader
            trader=KalshiTrader(config)
            guard=LiveTradingGuard(config,db,balance_provider=trader.get_balance)
            price_dollars=entry_price  # 0-1 float
            shares=compute_shares(bet_amount,price_dollars)
            if shares<=0:
                analysis['action']='DECLINED'
                analysis['reason']=f'0 shares at ${price_dollars:.2f}/share for ${bet_amount:.2f}'
                db.close()
                return JSONResponse(analysis)
            cost=round(shares*price_dollars,2)
            # Risk check BEFORE guard
            from model.risk import RiskManager
            risk_mgr=RiskManager(config,trader,db)
            risk_ok,risk_reason=risk_mgr.check_all()
            if not risk_ok:
                analysis['action']='RISK_BLOCKED'
                analysis['reason']=f'Risk: {risk_reason}'
                db.close()
                return JSONResponse(analysis)
            guard_result=guard.check_all(proposed_cost=cost)
            if not guard_result.ok:
                analysis['action']='BLOCKED'
                analysis['reason']=f'Guard: {guard_result.reason}'
                db.close()
                return JSONResponse(analysis)
            price_cents=max(1,min(99,int(round(price_dollars*100))))
            try:
                # Call Kalshi API directly (kalshi_trader.place_order has a client_order_id bug)
                import requests as _rq2
                _ourl=f'{trader.TRADING_URL}/portfolio/orders'
                _opayload={'ticker':ticker,'side':side.lower(),'action':'buy','type':'limit','count':shares,
                    'yes_price':price_cents if side=='YES' else None,'no_price':price_cents if side=='NO' else None}
                _opayload={k:v for k,v in _opayload.items() if v is not None}
                _oresp=_rq2.post(_ourl,headers=trader._signed_headers('POST',_ourl),json=_opayload,timeout=15)
                _oresp.raise_for_status()
                order=_oresp.json()
                order_id=order.get('order',{}).get('order_id','') if isinstance(order.get('order'),dict) else ''
                db.insert_live_trade({'contract_id':contract_id,'kalshi_order_id':order_id,
                    'kalshi_ticker':ticker,'side':side,'entry_price':price_dollars,'shares':shares,
                    'cost':cost,'max_payout':shares,'model_prob':estimate.probability,'edge_at_entry':edge_result.edge})
                analysis['action']='LIVE_TRADE'
                analysis['shares']=shares
                analysis['cost']=cost
                analysis['order_id']=order_id
                analysis['price_cents']=price_cents
            except Exception as oe:
                analysis['action']='LIVE_FAILED'
                analysis['reason']=str(oe)[:300]
        else:
            # Paper trade
            db.insert_paper_trade({'contract_id':contract_id,'side':side,'entry_price':entry_price,
                'model_prob':estimate.probability,'kelly_fraction':edge_result.kelly_fraction,
                'bet_amount':bet_amount})
            analysis['action']='PAPER_TRADE'
            analysis['bet_amount']=round(bet_amount,2)

        db.close()
        return JSONResponse(analysis)
    except Exception as e:
        import traceback; traceback.print_exc()
        return JSONResponse({'error':str(e)},500)

@app.get('/api/scan')
def api_scan():
    """Return recent scan candidates with real data-backed edges."""
    cands=q("SELECT * FROM scan_candidates ORDER BY ts DESC, ABS(edge) DESC LIMIT 20",db=TRADE_DB)
    return {'candidates':cands}

@app.get('/api/status')
def api_status():
    d=get_data(); last=d['logs'][0] if d['logs'] else {}
    return {'last_action':last.get('msg',''),'last_ts':to_et(last.get('ts','')),'pnl':float(d['res'].get('pnl') or 0)}

@app.get('/api/commands')
def api_commands():
    cmds=q("SELECT id,ts,command,status,result,executed_at FROM agent_commands ORDER BY ts DESC LIMIT 20")
    return {'commands':cmds}

@app.get('/api/data_feeds')
def api_data_feeds():
    """Return real-time data feed status — reads from smart_trader log."""
    import subprocess
    try:
        result=subprocess.run(['tail','-1','/tmp/smart_trader.log'],capture_output=True,text=True,timeout=2)
        line=result.stdout.strip()
        # Parse: STATUS | BTC=$71,720 WTI=$98.30 SPX=$6,854 GOLD=$4,780 | DVOL=47.5 ATM_IV=42.6 | ...
        feeds={}
        if 'STATUS' in line:
            parts=line.split('|')
            for part in parts:
                for item in part.strip().split():
                    if '=' in item:
                        k,v=item.split('=',1)
                        feeds[k.strip()]=v.strip().replace('$','').replace(',','')
        # Also read model_adjustments.json if it exists
        adjustments={}
        adj_path=os.path.join(os.path.dirname(__file__),'model_adjustments.json')
        if os.path.exists(adj_path):
            with open(adj_path) as f: adjustments=json.load(f)
        # Read model accuracy
        accuracy=q("SELECT * FROM model_accuracy ORDER BY ts DESC LIMIT 1",db=TRADE_DB)
        return {'feeds':feeds,'adjustments':adjustments,'accuracy':accuracy[0] if accuracy else {},'raw_line':line}
    except: return {'feeds':{},'adjustments':{},'accuracy':{},'raw_line':''}

@app.get('/api/live_events')
def api_live_events():
    """Return recent trader events — thinking, scanning, trading, data ingestion."""
    events=q("SELECT ts,event_type,message,data FROM trader_events ORDER BY id DESC LIMIT 50",db=TRADE_DB)
    return {'events':events}

@app.get('/api/architecture')
def api_architecture():
    """Generate live architecture diagram from running system state."""
    import subprocess
    # Check what's running
    procs=subprocess.run(['ps','aux'],capture_output=True,text=True).stdout
    brain_on='trading_brain' in procs
    mm_on='market_maker' in procs
    workers_on='run_all' in procs
    dash_on=True  # we're serving this, so yes
    # Read brain state
    brain_state={}
    try:
        with open('/tmp/live_monitor.json') as f: brain_state=json.load(f)
    except: pass
    llm=brain_state.get('llm_activity',{})
    intel=brain_state.get('intel',{})
    cycle=brain_state.get('cycle',0)
    opps=brain_state.get('opportunities',0)
    ideas=brain_state.get('llm_ideas',[])
    hr=brain_state.get('hold_reports',[])
    exits=brain_state.get('exit_candidates',[])
    return {
        'processes':{
            'Trading Brain':{'status':'RUNNING' if brain_on else 'DOWN','cycle':cycle,'detail':f'{opps} opportunities, 2s cycles'},
            'Market Maker':{'status':'RUNNING' if mm_on else 'OFF','detail':'quotes both sides near-the-money'},
            'Workers':{'status':'RUNNING' if workers_on else 'DOWN','detail':'fill_tracker(30s) reconciler(60s) resolver(120s) calibrator(300s)'},
            'Dashboard':{'status':'RUNNING','detail':'port 8000, chat+trade+search+monitor'},
            'Master Agent':{'status':'RUNNING (Saturn)','detail':'OODA loop, RL code changes, wiki updates'},
        },
        'pipeline':[
            {'stage':'Data Feeds','sources':['Yahoo Finance (spot+vol)','CoinGecko (BTC/ETH)','Deribit (IV+funding+basis)','Fear & Greed','BLS (CPI+GDP)','Kalshi Orderbook'],'status':'6/8 active','detail':'FRED+Brave keys missing'},
            {'stage':'Market Intel','output':'direction + conviction per asset','assets':{k:{'dir':v.get('dir',0),'conv':v.get('conv',0),'vol':v.get('vol','')} for k,v in intel.items()}},
            {'stage':'Vol Model','method':'Black-Scholes binary pricing with realized vol','output':'fair value per contract'},
            {'stage':'Edge Detection','method':'fair value vs market price, both YES and NO sides','threshold':'5pp minimum'},
            {'stage':'Intel Gate','method':'blocks trades against data conviction (>0.3)'},
            {'stage':'P&L Forecast','method':'entry cost, win/lose amounts, expected value, breakeven prob, risk/reward','gate':'must be positive EV after fees'},
            {'stage':'LLM Trade Review','model':llm.get('Trade Reviewer',{}).get('model','claude-sonnet-4-6'),'status':llm.get('Trade Reviewer',{}).get('status','idle'),'detail':llm.get('Trade Reviewer',{}).get('summary','')},
            {'stage':'Risk Manager','checks':['daily loss < $50','exposure < 80% balance','concentration < 40% per series']},
            {'stage':'Execute on Kalshi','method':'limit order via REST API'},
        ],
        'llm_roles':{
            'Idea Generator':{'model':'claude-sonnet-4-6','frequency':'every 5 min','purpose':'proposes 1-3 new trade ideas from portfolio+intel+markets','last':llm.get('Idea Generator',{}).get('summary','')},
            'Trade Reviewer':{'model':'claude-sonnet-4-6','frequency':'every trade','purpose':'sanity-checks data, logic, risks before execution. Can reject.','last':llm.get('Trade Reviewer',{}).get('summary','')},
            'Portfolio Strategist':{'model':'claude-sonnet-4-6','frequency':'every 15 min','purpose':'strategic advice on positions, risk, opportunities','last':llm.get('Portfolio Strategist',{}).get('summary','')},
            'Code Auditor':{'model':'claude-sonnet-4-6','frequency':'every 1 hour','purpose':'reviews error logs, identifies recurring issues, suggests fixes','last':llm.get('Code Auditor',{}).get('summary','')},
            'Dashboard Chat':{'model':'claude-sonnet-4-6','frequency':'on demand','purpose':'human interface — search markets, execute trades, ask questions'},
        },
        'safety':{
            'tests':'139 passing (pytest)',
            'auditor':'8 checks every 10 min, auto-restarts brain+dashboard if down',
            'protected_files':['live/kalshi_trader.py','live/guard.py','master_agent/safeguards.py','.env'],
            'exit_logic':'only take-profit exits (profitable AND edge flipped). No stop-loss on binary contracts.',
            'bet_size':'$7 max per trade (learning mode)',
        },
        'databases':{
            'shared':{'path':'/home/jovyan/shared/sm/prediction-agent-db/prediction_agent.db','tables':'agent_log, agent_changes, agent_commands, tool_experiments'},
            'local':{'path':'prediction_agent.db','tables':'contracts, paper_trades, live_trades, predictions, resolutions, deep_dive_results, scalper_trades, wti_paper_trades, portfolio_snapshots, scan_candidates, model_accuracy'},
        },
        'current_state':{
            'balance':brain_state.get('balance',0),
            'positions':len(hr),
            'exposure':brain_state.get('portfolio',{}).get('total_exposure',0),
            'realized_pnl':brain_state.get('portfolio',{}).get('realized_pnl',0),
            'ideas_pending':len(ideas),
            'exit_signals':len(exits),
        },
    }

@app.get('/api/monitor')
def api_monitor():
    """Return live monitor state — spots, positions, alerts."""
    try:
        with open('/tmp/live_monitor.json') as f: return json.load(f)
    except: return {'error':'Monitor not running. Start: python workers/live_monitor.py &'}

@app.get('/api/pnl_timeline')
def api_pnl_timeline():
    """Return cumulative P&L timeline from scalper trades."""
    trades=q("""SELECT ts,ticker,side,entry_price,exit_price,count,cost,pnl,status,edge,staleness_ms
        FROM scalper_trades ORDER BY ts""",db=TRADE_DB)
    # Build cumulative P&L series
    cumulative=0; timeline=[]; fees_total=0
    for t in trades:
        pnl=t.get('pnl') or 0
        cost=t.get('cost') or 0
        entry=t.get('entry_price') or 0
        count=t.get('count') or 0
        fee=0.07*entry*(1-entry)*count if entry else 0
        if t.get('status')!='open':
            cumulative+=pnl
            fees_total+=fee*2  # entry+exit
        timeline.append({'ts':t['ts'],'ticker':t['ticker'],'side':t['side'],
            'pnl':round(pnl,4),'cumulative':round(cumulative,2),
            'cost':round(cost,2),'status':t['status'],
            'edge':t.get('edge',0),'stale_ms':t.get('staleness_ms',0)})
    return {'timeline':timeline,'total_pnl':round(cumulative,2),
            'total_fees':round(fees_total,2),'total_trades':len(trades),
            'open':sum(1 for t in trades if t.get('status')=='open'),
            'closed':sum(1 for t in trades if t.get('status')!='open')}

@app.get('/api/health')
def api_health():
    """Run health checks across all systems. Returns list of checks with status."""
    import subprocess,time
    checks=[]
    def chk(name,fn):
        t0=time.time()
        try:
            ok,detail=fn()
            checks.append({'name':name,'ok':ok,'detail':detail,'ms':int((time.time()-t0)*1000)})
        except Exception as e:
            checks.append({'name':name,'ok':False,'detail':str(e)[:200],'ms':int((time.time()-t0)*1000)})
    # 1. Database connectivity
    def ck_db():
        r=q('SELECT COUNT(*) n FROM agent_log')
        return (bool(r),f'{r[0]["n"]} log rows' if r else 'no rows')
    chk('Main DB',ck_db)
    def ck_trade_db():
        r=q("SELECT COUNT(*) n FROM paper_trades",db=TRADE_DB)
        return (bool(r),f'{r[0]["n"]} trades' if r else 'no rows')
    chk('Trade DB',ck_trade_db)
    # 2. Recent errors in agent_log
    def ck_errors():
        cutoff=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()
        errs=q("SELECT COUNT(*) n FROM agent_log WHERE lvl='ERROR' AND ts>?",(cutoff,))
        n=errs[0]['n'] if errs else 0
        return (n<5,f'{n} errors in last hour')
    chk('Error rate',ck_errors)
    # 3. Tests
    def ck_tests():
        r=subprocess.run(['python','-m','pytest','tests/','-q','--tb=line'],capture_output=True,text=True,timeout=120,cwd='/home/jovyan/workspace/prediction-agent')
        m=re.search(r'(\d+) passed',r.stdout+r.stderr)
        n=int(m.group(1)) if m else 0
        failed=re.search(r'(\d+) failed',r.stdout+r.stderr)
        f=int(failed.group(1)) if failed else 0
        return (r.returncode==0 and n>=139,f'{n} passed, {f} failed')
    chk('Tests',ck_tests)
    # 4. Module imports
    def ck_imports():
        mods=['dashboard','config','model.probability_model','tools.tool_registry','database.db','master_agent.observe','master_agent.confidence_gate']
        bad=[]
        for mod in mods:
            r=subprocess.run(['python','-c',f'import {mod}'],capture_output=True,text=True,timeout=10,cwd='/home/jovyan/workspace/prediction-agent')
            if r.returncode!=0: bad.append(mod)
        return (not bad,f'{len(mods)-len(bad)}/{len(mods)} ok' + (f' FAIL: {",".join(bad)}' if bad else ''))
    chk('Imports',ck_imports)
    # 5. Pending commands stuck
    def ck_stuck():
        cutoff=(datetime.now(timezone.utc)-timedelta(hours=2)).isoformat()
        stuck=q("SELECT COUNT(*) n FROM agent_commands WHERE status='pending' AND ts<?",(cutoff,))
        n=stuck[0]['n'] if stuck else 0
        return (n==0,f'{n} commands pending >2h')
    chk('Stuck commands',ck_stuck)
    # 6. Disk / DB size
    def ck_disk():
        sz1=os.path.getsize(DB) if os.path.exists(DB) else 0
        sz2=os.path.getsize(TRADE_DB) if os.path.exists(TRADE_DB) else 0
        return (sz1<500_000_000 and sz2<500_000_000,f'main={sz1//1024}KB trade={sz2//1024}KB')
    chk('DB size',ck_disk)
    # 7. Dashboard endpoints
    def ck_endpoints():
        import urllib.request
        bad=[]
        for ep in ['/api/status','/api/commands','/api/data_feeds']:
            try:
                r=urllib.request.urlopen(f'http://localhost:8000{ep}',timeout=3)
                if r.status!=200: bad.append(ep)
            except: bad.append(ep)
        return (not bad,f'{3-len(bad)}/3 endpoints ok' + (f' FAIL: {",".join(bad)}' if bad else ''))
    chk('Endpoints',ck_endpoints)
    total=len(checks); ok=sum(1 for c in checks if c['ok'])
    return {'checks':checks,'summary':f'{ok}/{total} passing','healthy':ok==total,
            'ts':datetime.now(timezone.utc).isoformat()}

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
  #pane-pos.active{grid-template-columns:1fr 1fr;gap:0 24px;}
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
    def posr(p):
        st=p.get('status','open'); sc_={'open':'#ffaa00','won':'#00ff88','lost':'#ff4444'}.get(st,'#666')
        sl_={'open':'OPEN','won':'WON','lost':'LOST'}.get(st,st.upper())
        edge=p.get('model_prob',0)-p.get('entry_price',0)
        pnl_s=f"${p.get('pnl',0) or 0:+.2f}" if st!='open' else '—'
        pnl_c='#00ff88' if (p.get('pnl') or 0)>0 else '#ff4444' if (p.get('pnl') or 0)<0 else '#888'
        title=(p.get('title','') or '')[:65]
        return f'<div class="change-row"><div style="display:flex;justify-content:space-between;align-items:center"><span style="color:{sc_};font-size:11px;font-weight:700">{sl_} · {p.get("side","")}</span><span style="color:{pnl_c};font-size:13px;font-weight:700">{pnl_s}</span></div><div style="color:#ddd;font-size:13px;margin-top:3px">{title}</div><div style="color:#888;font-size:11px;margin-top:2px">entry={p.get("entry_price",0):.0%} model={p.get("model_prob",0):.0%} edge={edge:+.1%} bet=${p.get("bet_amount",0):.2f}</div><div style="color:var(--dim);font-size:10px;margin-top:2px">{p.get("source_id","")} · opened {to_et(p.get("opened_at",""))}</div></div>'
    open_pos=[p for p in d['positions'] if p.get('status')=='open']
    closed_pos=[p for p in d['positions'] if p.get('status')!='open']
    pos_open_h=''.join(posr(p) for p in open_pos) or '<div style="color:#333;padding:12px">No open positions</div>'
    pos_closed_h=''.join(posr(p) for p in closed_pos) or '<div style="color:#333;padding:12px">No settled trades yet</div>'
    def wtir(w):
        st=w.get('status','open'); sc_={'open':'#ffaa00','won':'#00ff88','lost':'#ff4444'}.get(st,'#666')
        sl_={'open':'OPEN','won':'WON','lost':'LOST'}.get(st,st.upper())
        pnl_v=w.get('pnl') or 0; pnl_s=f"${pnl_v:+.2f}" if st!='open' else f"${w.get('cost',0):.2f} at risk"
        pnl_c='#00ff88' if pnl_v>0 else '#ff4444' if pnl_v<0 else '#888'
        edge=w.get('edge_at_entry',0)
        return f'<div class="change-row"><div style="display:flex;justify-content:space-between;align-items:center"><span style="color:{sc_};font-size:11px;font-weight:700">{sl_} · {w.get("side","").upper()}</span><span style="color:{pnl_c};font-size:13px;font-weight:700">{pnl_s}</span></div><div style="color:#ddd;font-size:13px;margin-top:3px">WTI &gt; ${w.get("strike",0):.2f}</div><div style="color:#888;font-size:11px;margin-top:2px">{w.get("ticker","")} · entry={w.get("entry_price",0):.0%} model={w.get("model_prob",0):.0%} edge={edge:+.1%} x{w.get("shares",0)}</div><div style="color:var(--dim);font-size:10px;margin-top:2px">opened {to_et(w.get("opened_at",""))}</div></div>'
    wti_open=[w for w in d['wti'] if w.get('status')=='open']
    wti_closed=[w for w in d['wti'] if w.get('status')!='open']
    wti_open_h=''.join(wtir(w) for w in wti_open) or '<div style="color:#333;padding:12px">No WTI positions</div>'
    wti_closed_h=''.join(wtir(w) for w in wti_closed) or '<div style="color:#333;padding:12px">No settled WTI trades</div>'
    wti_exposure=sum(w.get('cost',0) for w in wti_open)
    wti_pnl=sum(w.get('pnl',0) or 0 for w in wti_closed)
    # Leaderboard: combine all settled positions, rank by P&L
    all_settled=[]
    for p in closed_pos:
        pv=p.get('pnl') or 0; title=(p.get('title','') or '')[:55]
        all_settled.append({'pnl':pv,'title':title,'side':p.get('side',''),'entry':p.get('entry_price',0),'bet':p.get('bet_amount',0),'source':p.get('source_id','')})
    for w in wti_closed:
        pv=w.get('pnl') or 0
        all_settled.append({'pnl':pv,'title':f"WTI >{w.get('strike',0):.2f}",'side':w.get('side',''),'entry':w.get('entry_price',0),'bet':w.get('cost',0),'source':w.get('ticker','')})
    winners=sorted([s for s in all_settled if s['pnl']>0],key=lambda x:-x['pnl'])[:8]
    losers=sorted([s for s in all_settled if s['pnl']<0],key=lambda x:x['pnl'])[:8]
    def ldr(s,color):
        return f'<div class="change-row"><div style="display:flex;justify-content:space-between"><span style="color:#ddd;font-size:12px">{s["title"]}</span><span style="color:{color};font-size:13px;font-weight:700">${s["pnl"]:+.2f}</span></div><div style="color:#888;font-size:10px">{s["side"]} entry={s["entry"]:.0%} bet=${s["bet"]:.0f} · {s["source"]}</div></div>'
    winners_h=''.join(ldr(s,'#00ff88') for s in winners) or '<div style="color:#333;padding:12px">No winners yet</div>'
    losers_h=''.join(ldr(s,'#ff4444') for s in losers) or '<div style="color:#333;padding:12px">No losers yet</div>'
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
  <button class='tab' id='tab-pos' onclick='sw("pos")'><span class='tab-icon'>💰</span>Positions</button>
  <button class='tab' id='tab-chat' onclick='sw("chat")'><span class='tab-icon'>💬</span>Chat</button>
  <button class='tab' id='tab-cmds' onclick='sw("cmds")'><span class='tab-icon'>⚡</span>Commands</button>
  <button class='tab' id='tab-live' onclick='sw("live")'><span class='tab-icon'>🧠</span>Live</button>
  <button class='tab' id='tab-pnl' onclick='sw("pnl")'><span class='tab-icon'>📈</span>P&L</button>
  <button class='tab' id='tab-tools' onclick='sw("tools")'><span class='tab-icon'>🔬</span>Tools</button>
  <button class='tab' id='tab-health' onclick='sw("health")'><span class='tab-icon'>🩺</span>Health</button>
  <button class='tab' id='tab-arch' onclick='sw("arch")'><span class='tab-icon'>🏗️</span>Arch</button>
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
<!-- POSITIONS TAB -->
<div id='pane-pos' class='pane'>
  <div class='col-full'>
    <div class='grid'>
      <div class='card'><div class='v'>{len(open_pos)+len(wti_open)}</div><div class='l'>Open</div></div>
      <div class='card'><div class='v'>{len(closed_pos)+len(wti_closed)}</div><div class='l'>Settled</div></div>
      <div class='card' style='border-color:{pc}33'><div class='v' style='color:{pc}'>${pnl+wti_pnl:.2f}</div><div class='l'>Total P&amp;L</div></div>
      <div class='card'><div class='v'>${sum(p.get("bet_amount",0) for p in open_pos)+wti_exposure:.0f}</div><div class='l'>At Risk</div></div>
    </div>
    <div style='background:var(--bg2);border:1px solid #ff880044;border-radius:8px;padding:12px;margin-bottom:14px'>
      <div style='color:#ff8800;font-size:12px;font-weight:700;margin-bottom:4px'>WTI CRUDE OIL DAY TRADING</div>
      <div style='color:#888;font-size:12px'>KXWTI series · {len(wti_open)} open / {len(wti_closed)} settled · exposure ${wti_exposure:.2f} · P&amp;L ${wti_pnl:+.2f}</div>
    </div>
  </div>
  <div class='col-left'>
    <div class='sec-title'>WTI Open ({len(wti_open)})</div>
    <div class='sec scroll-box'>{wti_open_h}</div>
    <div class='sec-title'>Other Open ({len(open_pos)})</div>
    <div class='sec scroll-tall'>{pos_open_h}</div>
  </div>
  <div class='col-right'>
    <div class='sec-title'>WTI Settled ({len(wti_closed)})</div>
    <div class='sec scroll-box'>{wti_closed_h}</div>
    <div class='sec-title'>Other Settled ({len(closed_pos)})</div>
    <div class='sec scroll-tall'>{pos_closed_h}</div>
  </div>
  <div class='col-full'>
    <div style='display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:8px'>
      <div>
        <div class='sec-title' style='color:#00ff88'>Top Winners</div>
        <div class='sec'>{winners_h}</div>
      </div>
      <div>
        <div class='sec-title' style='color:#ff4444'>Top Losers</div>
        <div class='sec'>{losers_h}</div>
      </div>
    </div>
  </div>
</div>
<!-- CHAT TAB -->
<div id='pane-chat' class='pane'>
  <div class='chat-wrap'>
    <div id='cmd-queue' style='background:#0d0d1a;border:1px solid #7c3aed44;border-radius:8px;padding:6px 10px;margin-bottom:6px;display:none;max-height:80px;overflow-y:auto'>
      <div style='display:flex;justify-content:space-between;align-items:center'>
        <span style='color:var(--accent);font-size:10px;font-weight:700'>QUEUE</span>
        <span id='cmd-count' style='color:#888;font-size:9px'></span>
      </div>
      <div id='cmd-list' style='font-size:11px'></div>
    </div>
    <div id='msgs' class='chat-msgs'><div style='color:var(--dim);text-align:center;padding:40px 20px;font-size:14px'>Chat with your master agent.<br><br>Full system context injected. Ask anything.<br>Commands persist — queue work and walk away.</div></div>
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
<!-- LIVE BRAIN TAB -->
<div id='pane-live' class='pane'>
  <div class='col-full'>
    <div style='background:var(--bg2);border:1px solid #7c3aed44;border-radius:8px;padding:12px;margin-bottom:14px'>
      <div style='color:var(--accent);font-size:12px;font-weight:700;margin-bottom:4px'>REAL-TIME TRADING BRAIN</div>
      <div style='color:#888;font-size:12px'>Live data ingestion, opportunity analysis, confidence scoring, and trade execution. Auto-refreshes every 5s.</div>
      <div id='live-feeds-bar' style='display:flex;gap:8px;flex-wrap:wrap;margin-top:8px'></div>
    </div>
    <div class='sec scroll-tall' id='live-events' style='font-family:monospace;font-size:12px'></div>
  </div>
</div>
<!-- P&L TAB -->
<div id='pane-pnl' class='pane'>
  <div class='col-full'>
    <div id='pnl-summary' style='display:flex;gap:10px;margin-bottom:14px;flex-wrap:wrap'></div>
    <div class='sec' style='padding:14px;min-height:300px'>
      <canvas id='pnl-chart' style='width:100%;height:300px'></canvas>
    </div>
    <div class='sec-title'>Trade Log</div>
    <div class='sec scroll-tall' id='pnl-trades'></div>
  </div>
</div>
<!-- ARCHITECTURE TAB -->
<div id='pane-arch' class='pane'>
  <div class='col-full'>
    <div style='background:var(--bg2);border:1px solid #7c3aed44;border-radius:8px;padding:14px;margin-bottom:14px'>
      <div style='color:var(--accent);font-size:13px;font-weight:700;margin-bottom:8px'>KARPATHY KAPITAL — SYSTEM ARCHITECTURE</div>
      <div style='color:#888;font-size:11px'>Auto-generated from running system. Updates on tab switch.</div>
    </div>
    <div id='arch-content' class='sec' style='padding:14px;font-family:monospace;font-size:12px;line-height:1.8;color:#ccc'></div>
  </div>
</div>
<!-- HEALTH TAB -->
<div id='pane-health' class='pane'>
  <div class='col-full'>
    <div style='background:var(--bg2);border:1px solid #7c3aed44;border-radius:8px;padding:12px;margin-bottom:14px'>
      <div style='display:flex;justify-content:space-between;align-items:center'>
        <div>
          <div style='color:var(--accent);font-size:12px;font-weight:700;margin-bottom:4px'>SYSTEM HEALTH MONITOR</div>
          <div style='color:#888;font-size:12px'>Tests, imports, DBs, endpoints, error rates. Auto-checks every 60s.</div>
        </div>
        <button class='btn' style='padding:6px 14px;font-size:11px' onclick='runHealth()'>Run Now</button>
      </div>
      <div id='health-summary' style='margin-top:8px;font-size:14px;font-weight:700;color:#888'>Not checked yet</div>
      <div id='health-ts' style='color:#444;font-size:10px;margin-top:2px'></div>
    </div>
    <div id='health-checks' class='sec scroll-tall' style='font-size:13px'></div>
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
let H=[];
function ar(el){{el.style.height='auto';el.style.height=Math.min(el.scrollHeight,120)+'px';}}
function am(role,text){{const m=document.getElementById('msgs');const t=new Date().toLocaleTimeString('en-US',{{hour:'2-digit',minute:'2-digit',hour12:true}});m.innerHTML+=`<div class='msg ${{role}}'><div class='msg-bubble'>${{text}}</div><div class='msg-time'>${{t}}</div></div>`;m.scrollTop=m.scrollHeight;}}
async function sc(){{const i=document.getElementById('ci');const msg=i.value.trim();if(!msg)return;am('user',msg);i.value='';i.style.height='auto';H.push({{role:'user',content:msg}});am('ai','...');try{{const r=await _f('/chat',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{message:msg,history:H.slice(0,-1)}})}});const j=await r.json();const bs=document.getElementById('msgs').querySelectorAll('.msg.ai');bs[bs.length-1].querySelector('.msg-bubble').innerHTML=(j.response||'Error').replace(/\\n/g,'<br>');H.push({{role:'assistant',content:j.response||''}});if(j.commands_queued?.length)am('ai','⚡ Queued: '+j.commands_queued.join(', '));if(j.trades?.length){{j.trades.forEach(t=>{{const col=t.action?.includes('TRADE')?'#00ff88':t.action==='DECLINED'?'#ffaa00':'#ff4444';am('ai',`<span style="color:${{col}};font-weight:700">${{t.action}}</span> ${{t.ticker}} ${{t.side||''}} mkt=${{(t.market_price*100).toFixed(0)}}c model=${{(t.model_prob*100).toFixed(0)}}c edge=${{(t.edge*100).toFixed(1)}}pp ${{t.bet_amount?'$'+t.bet_amount:''}} ${{t.reason||''}}`)}})}}if(j.search?.length){{let s='<b>MARKETS FOUND:</b><br>'+j.search.map(m=>`<span style="color:var(--accent)">${{m.ticker}}</span> ${{(m.yes_price*100).toFixed(0)}}c — ${{m.title.slice(0,70)}}`).join('<br>');am('ai',s)}}if(j.scan?.length){{let s='<b>SCAN:</b><br>'+j.scan.map(c=>`${{c.source_id}} ${{c.recommendation}} mkt=${{(c.market_price*100).toFixed(0)}}c edge=${{(c.edge*100).toFixed(1)}}pp`).join('<br>');am('ai',s)}}}}catch(e){{am('ai','Error: '+e);}}}}
document.getElementById('ci').addEventListener('keydown',e=>{{if(e.key==='Enter'&&!e.shiftKey){{e.preventDefault();sc();}}}});
async function sendCmd(){{const i=document.getElementById('cmi');const cmd=i.value.trim();const s=document.getElementById('cms');if(!cmd)return;s.textContent='Sending...';s.style.color='#ffaa00';try{{const r=await _f('/command',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{command:cmd}})}});const j=await r.json();if(j.ok){{s.textContent='Queued at '+j.queued_at+' ET';s.style.color='#00ff88';i.value='';}}else s.textContent='Error: '+j.error;}}catch(e){{s.textContent='Error: '+e;s.style.color='#ff4444';}}}}
setInterval(()=>{{if(document.getElementById('pane-dash').classList.contains('active'))location.reload();}},10000);
// Live Monitor Feed
let liveTimer=null;
function loadLive(){{
  if(liveTimer)return;
  function refresh(){{
    _f('/api/monitor').then(r=>r.json()).then(d=>{{
      if(d.error){{document.getElementById('live-events').innerHTML=`<div style="color:#ff4444;padding:20px">${{d.error}}</div>`;return;}}
      const bar=document.getElementById('live-feeds-bar');
      const el=document.getElementById('live-events');
      // Spot prices bar
      if(d.spots){{
        bar.innerHTML=Object.entries(d.spots).map(([k,v])=>{{
          const col=v.change>0?'#00ff88':v.change<0?'#ff4444':'#888';
          return `<span style="background:#0a0a12;border:1px solid ${{col}}44;color:${{col}};padding:2px 6px;border-radius:4px;font-size:10px">${{k}}=${{Number(v.price).toLocaleString(undefined,{{maximumFractionDigits:2}})}} ${{v.change>0?'+':''}}${{v.change.toFixed(1)}}%</span>`;
        }}).join('');
      }}
      // Portfolio summary
      const p=d.portfolio||{{}};
      const netpnl=p.net_pnl||p.realized_pnl||0;
      const pc=netpnl>=0?'#00ff88':'#ff4444';
      let html=`<div style="padding:10px;border-bottom:1px solid #1a1a2e;display:flex;gap:16px;flex-wrap:wrap;font-size:13px">
        <span style="color:#fff;font-weight:700">BAL $${{Number(d.balance||0).toLocaleString()}}</span>
        <span>EXP $${{(p.total_exposure||0).toFixed(0)}}</span>
        <span style="color:${{pc}};font-weight:700">P&L $${{netpnl>=0?'+':''}}$${{netpnl.toFixed(0)}}</span>
        <span style="color:#888">${{p.n_positions||0}} pos</span>
        <span style="color:#888">${{d.opportunities||0}} edges</span>
      </div>`;
      // Positions by series
      if(p.by_series){{
        html+=Object.entries(p.by_series).map(([s,v])=>{{
          const vunr=v.unrealized||0; const uc=vunr>=0?'#00ff88':'#888';
          const vexp=v.exposure||v.exp||0; const vn=v.count||v.n||0;
          return `<div style="padding:4px 10px;display:flex;justify-content:space-between;border-bottom:1px solid #0f0f1a"><span style="color:var(--accent);font-size:12px;font-weight:600">${{s}}</span><span style="font-size:12px"><span style="color:#888">exp=$${{vexp.toFixed(0)}}</span> <span style="color:${{uc}};font-weight:600">${{vn}}pos</span></span></div>`;
        }}).join('');
      }}
      // LLM Activity
      const llmActs=d.llm_activity||{{}};
      if(Object.keys(llmActs).length){{
        html+=`<div style="padding:8px 10px;color:#e879f9;font-size:11px;font-weight:700;border-bottom:1px solid #1a1a2e">LLM MODELS ACTIVE</div>`;
        html+=Object.entries(llmActs).map(([role,info])=>{{
          const sc=info.status==='ok'?'#00ff88':info.status==='rejected'?'#ff4444':'#888';
          return `<div style="padding:4px 10px;border-bottom:1px solid #0f0f1a;font-size:11px"><span style="color:#e879f9;font-weight:600">${{info.model||'?'}}</span> <span style="color:#888">→</span> <span style="color:#ddd">${{role}}</span> <span style="color:${{sc}};font-weight:600">${{info.status||'idle'}}</span> <span style="color:#555">${{info.latency||''}} ${{info.last||''}}</span><div style="color:#aaa;font-size:10px;margin-top:1px;padding-left:4px">${{info.summary||''}}</div></div>`;
        }}).join('');
      }}
      // LLM Ideas
      const ideas=d.llm_ideas||[];
      if(ideas.length){{
        html+=`<div style="padding:8px 10px;color:#7c3aed;font-size:11px;font-weight:700;border-bottom:1px solid #1a1a2e">AI TRADE IDEAS</div>`;
        html+=ideas.map(i=>{{
          const sc=i.side==='yes'?'#00ff88':'#ff4444';
          return `<div style="padding:5px 10px;border-bottom:1px solid #0f0f1a;font-size:12px;background:#0d0d1a"><span style="color:${{sc}};font-weight:700">${{i.side.toUpperCase()}}</span> <span style="color:#ddd">${{i.ticker}}</span> <span style="color:#7c3aed">conv=${{Math.round((i.conviction||0)*100)}}%</span> <span style="color:#888">size=${{Math.round((i.size_pct||0)*100)}}%</span><div style="color:#aaa;font-size:11px;margin-top:2px;padding-left:4px">${{i.reason||''}}</div></div>`;
        }}).join('');
      }}
      // Exit candidates
      const exits=d.exit_candidates||[];
      if(exits.length){{
        html+=`<div style="padding:8px 10px;color:#ff4444;font-size:11px;font-weight:700;border-bottom:1px solid #1a1a2e">EXIT SIGNALS</div>`;
        html+=exits.map(e=>{{
          return `<div style="padding:4px 10px;border-bottom:1px solid #0f0f1a;font-size:12px"><span style="color:#ff4444;font-weight:600">${{e.reason}}</span> <span style="color:#ddd">${{e.ticker}}</span> <span style="color:${{e.profitable?'#00ff88':'#ff4444'}}">pnl=$${{e.pnl>=0?'+':''}}${{e.pnl.toFixed(2)}} ${{e.profitable?'PROFIT':'LOSS'}}</span></div>`;
        }}).join('');
      }}
      // Market intel detail
      const intelData=d.intel||{{}};
      if(Object.keys(intelData).length){{
        html+=`<div style="padding:8px 10px;color:#00aaff;font-size:11px;font-weight:700;border-bottom:1px solid #1a1a2e">MARKET INTEL</div>`;
        html+=Object.entries(intelData).map(([name,v])=>{{
          const dc=v.dir>0.2?'#00ff88':v.dir<-0.2?'#ff4444':'#888';
          const dir=v.dir>0.2?'BULL':v.dir<-0.2?'BEAR':'FLAT';
          const sigs=(v.signals||[]).map(s=>s.name+'('+s.dir.toFixed(1)+')').join(' ');
          return `<div style="padding:3px 10px;border-bottom:1px solid #0a0a12;font-size:11px"><span style="color:${{dc}};font-weight:700">${{name}} ${{dir}}</span> <span style="color:#888">conv=${{Math.round(v.conv*100)}}% vol=${{v.vol}}</span> <span style="color:#555">${{sigs}}</span></div>`;
        }}).join('');
      }}
      // Alerts
      if(d.alerts&&d.alerts.length){{
        html+=`<div style="padding:8px 10px;color:#ffaa00;font-size:11px;font-weight:700;border-bottom:1px solid #1a1a2e">EDGES (${{d.alerts.length}})</div>`;
        html+=d.alerts.map(a=>{{
          return `<div style="padding:4px 10px;border-bottom:1px solid #0f0f1a;font-size:12px"><span style="color:#ffaa00;font-weight:600">${{a.side}}</span> <span style="color:#ddd">${{a.ticker}}</span> <span style="color:#00ff88">edge=${{(a.edge*100).toFixed(1)}}pp</span> <span style="color:#888">fair=${{(a.fair*100).toFixed(0)}}c mkt=${{(a.market*100).toFixed(0)}}c spot=$${{Number(a.spot).toLocaleString()}}</span></div>`;
        }}).join('');
      }}
      // Top positions
      // Hold reports — evaluated positions with P&L
      const hr=d.hold_reports||[];
      if(hr.length){{
        html+=`<div style="padding:8px 10px;color:#888;font-size:11px;font-weight:700;border-bottom:1px solid #1a1a2e">POSITIONS (${{hr.length}} evaluated)</div>`;
        html+=hr.map(h=>{{
          const rc=h.realizable>=0?'#00ff88':'#ff4444';
          const wp=Math.round((h.win_prob||0)*100);
          return `<div style="padding:3px 10px;border-bottom:1px solid #0a0a12;font-size:11px"><span style="color:${{h.side==='yes'?'#00ff88':'#ff4444'}};font-weight:600">${{h.side.toUpperCase()}}</span> <span style="color:#ccc">${{h.ticker}}</span> <span style="color:#888">${{h.shares}}sh entry=${{h.entry.toFixed(2)}}</span> <span style="color:${{rc}};font-weight:600">$${{h.realizable>=0?'+':''}}${{h.realizable.toFixed(2)}}</span> <span style="color:#555">win=${{wp}}% ev=$${{h.ev.toFixed(2)}}</span></div>`;
        }}).join('');
      }} else if(d.positions&&d.positions.length){{
        html+=`<div style="padding:8px 10px;color:#888;font-size:11px;font-weight:700;border-bottom:1px solid #1a1a2e">POSITIONS (${{d.positions.length}})</div>`;
        html+=d.positions.slice(0,15).map(p=>{{
          return `<div style="padding:3px 10px;border-bottom:1px solid #0a0a12;font-size:11px"><span style="color:${{p.side==='LONG'?'#00ff88':'#ff4444'}};font-weight:600">${{p.side}}</span> <span style="color:#ccc">${{p.ticker}}</span> <span style="color:#888">${{p.shares}}sh exp=$${{p.exposure}}</span></div>`;
        }}).join('');
      }}
      el.innerHTML=html||'<div style="color:#333;padding:20px">Waiting for monitor data...</div>';
    }}).catch(e=>{{document.getElementById('live-events').innerHTML=`<div style="color:#ff4444;padding:20px">Monitor error: ${{e}}</div>`;}});
    // Keep old feed badges update as fallback
    _f('/api/data_feeds').then(r=>r.json()).then(d=>{{
      if(!d.feeds||!Object.keys(d.feeds).length)return;
      const bar=document.getElementById('live-feeds-bar');
      if(bar.children.length>0)return; // monitor already populated
      const badges=Object.entries(d.feeds).map(([k,v])=>{{
        const col=v&&v!=='N/A'&&v!=='0'?'#00ff88':'#333';
        return `<span style="background:#0a0a12;border:1px solid ${{col}}44;color:${{col}};padding:2px 6px;border-radius:4px;font-size:10px">${{k}}=${{v||'?'}}</span>`;
      }}).join('');
      bar.innerHTML=badges;
    }}).catch(()=>{{}});
  }}
  refresh();
  liveTimer=setInterval(refresh,5000);
}}
// P&L Timeline
let pnlLoaded=false;
function loadPnL(){{if(pnlLoaded)return;pnlLoaded=true;
_f('/api/pnl_timeline').then(r=>r.json()).then(d=>{{
  // Summary cards
  const s=document.getElementById('pnl-summary');
  const pc=d.total_pnl>=0?'#00ff88':'#ff4444';
  s.innerHTML=`
    <div class='card'><div class='v' style='color:${{pc}}'>${{d.total_pnl>=0?'+':''}}$${{d.total_pnl.toFixed(2)}}</div><div class='l'>Realized P&amp;L</div></div>
    <div class='card'><div class='v'>${{d.total_trades}}</div><div class='l'>Total Trades</div></div>
    <div class='card'><div class='v'>${{d.closed}}</div><div class='l'>Closed</div></div>
    <div class='card'><div class='v'>${{d.open}}</div><div class='l'>Open</div></div>
    <div class='card'><div class='v'>$${{d.total_fees.toFixed(2)}}</div><div class='l'>Fees Paid</div></div>`;
  // Chart
  const canvas=document.getElementById('pnl-chart');
  const ctx=canvas.getContext('2d');
  canvas.width=canvas.offsetWidth*2;canvas.height=600;
  const tl=d.timeline.filter(t=>t.status!=='open');
  if(tl.length<2){{ctx.fillStyle='#333';ctx.font='14px monospace';ctx.fillText('Waiting for closed trades...',20,150);return;}}
  const vals=tl.map(t=>t.cumulative);
  const mn=Math.min(0,...vals);const mx=Math.max(0,...vals);
  const rng=Math.max(mx-mn,0.01);
  const w=canvas.width;const h=canvas.height;
  const pad={{l:60,r:20,t:20,b:40}};
  const pw=w-pad.l-pad.r;const ph=h-pad.t-pad.b;
  // Grid
  ctx.strokeStyle='#1a1a2e';ctx.lineWidth=1;
  const nGrid=5;
  for(let i=0;i<=nGrid;i++){{const y=pad.t+ph*i/nGrid;ctx.beginPath();ctx.moveTo(pad.l,y);ctx.lineTo(w-pad.r,y);ctx.stroke();
    const v=mx-rng*i/nGrid;ctx.fillStyle='#555';ctx.font='11px monospace';ctx.textAlign='right';ctx.fillText('$'+v.toFixed(2),pad.l-8,y+4);}}
  // Zero line
  const zeroY=pad.t+ph*(mx/(rng||1));
  ctx.strokeStyle='#333';ctx.lineWidth=2;ctx.setLineDash([4,4]);ctx.beginPath();ctx.moveTo(pad.l,zeroY);ctx.lineTo(w-pad.r,zeroY);ctx.stroke();ctx.setLineDash([]);
  // P&L line
  ctx.beginPath();ctx.lineWidth=2;
  for(let i=0;i<tl.length;i++){{
    const x=pad.l+pw*i/(tl.length-1);
    const y=pad.t+ph*(mx-tl[i].cumulative)/rng;
    if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y);
  }}
  const lastVal=vals[vals.length-1];
  ctx.strokeStyle=lastVal>=0?'#00ff88':'#ff4444';ctx.stroke();
  // Fill under curve
  ctx.lineTo(pad.l+pw,zeroY);ctx.lineTo(pad.l,zeroY);ctx.closePath();
  ctx.fillStyle=lastVal>=0?'rgba(0,255,136,0.08)':'rgba(255,68,68,0.08)';ctx.fill();
  // Dots for each trade
  for(let i=0;i<tl.length;i++){{
    const x=pad.l+pw*i/(tl.length-1);
    const y=pad.t+ph*(mx-tl[i].cumulative)/rng;
    ctx.beginPath();ctx.arc(x,y,3,0,Math.PI*2);
    ctx.fillStyle=tl[i].pnl>=0?'#00ff88':'#ff4444';ctx.fill();
  }}
  // Time labels
  ctx.fillStyle='#555';ctx.font='10px monospace';ctx.textAlign='center';
  const step=Math.max(1,Math.floor(tl.length/6));
  for(let i=0;i<tl.length;i+=step){{
    const x=pad.l+pw*i/(tl.length-1);
    const ts=tl[i].ts||'';const short=ts.slice(11,19);
    ctx.fillText(short,x,h-pad.b+16);
  }}
  // Trade log
  const logEl=document.getElementById('pnl-trades');
  logEl.innerHTML=d.timeline.slice().reverse().map(t=>{{
    const col=t.status==='open'?'#ffaa00':(t.pnl>=0?'#00ff88':'#ff4444');
    const pnlStr=t.status==='open'?'OPEN':'$'+(t.pnl>=0?'+':'')+t.pnl.toFixed(4);
    return `<div class='change-row'><div style='display:flex;justify-content:space-between'><span style='color:${{col}};font-size:11px;font-weight:700'>${{t.side.toUpperCase()}} ${{t.status.toUpperCase()}}</span><span style='color:${{col}};font-size:13px;font-weight:700'>${{pnlStr}}</span></div><div style='color:#ddd;font-size:12px;margin-top:2px'>${{t.ticker}}</div><div style='color:#888;font-size:11px'>edge=${{(t.edge*100).toFixed(1)}}c stale=${{t.stale_ms}}ms cost=$${{t.cost.toFixed(2)}} cum=$${{t.cumulative.toFixed(2)}}</div><div style='color:var(--dim);font-size:10px'>${{t.ts}}</div></div>`;
  }}).join('');
}}).catch(e=>console.error('PnL load error:',e));}}
// Load PnL when tab selected
const origSw=sw;
// Detect base path for proxy compatibility
const _bp=(()=>{{const p=window.location.pathname;const i=p.indexOf('/proxy/');if(i>=0){{const end=p.indexOf('/',i+7);return end>=0?p.slice(0,end):p;}}return '';}})();
function _f(path,opts){{return fetch(_bp+path,opts);}}
// Health monitor
let healthTimer=null;
function runHealth(){{
  document.getElementById('health-summary').textContent='Running checks...';
  document.getElementById('health-summary').style.color='#ffaa00';
  _f('/api/health').then(r=>r.json()).then(d=>{{
    const s=document.getElementById('health-summary');
    s.textContent=d.healthy?'ALL SYSTEMS GO — '+d.summary:'ISSUES DETECTED — '+d.summary;
    s.style.color=d.healthy?'#00ff88':'#ff4444';
    document.getElementById('health-ts').textContent='Last check: '+new Date().toLocaleTimeString();
    const el=document.getElementById('health-checks');
    el.innerHTML=d.checks.map(c=>{{
      const icon=c.ok?'✅':'❌';
      const col=c.ok?'#00ff88':'#ff4444';
      return `<div class='change-row'><div style='display:flex;justify-content:space-between;align-items:center'><span style='font-size:14px'>${{icon}} <span style='color:${{col}};font-size:13px;font-weight:600'>${{c.name}}</span></span><span style='color:#444;font-size:10px'>${{c.ms}}ms</span></div><div style='color:#888;font-size:12px;margin-top:3px'>${{c.detail}}</div></div>`;
    }}).join('');
  }}).catch(e=>{{
    document.getElementById('health-summary').textContent='Check failed: '+e;
    document.getElementById('health-summary').style.color='#ff4444';
  }});
}}
function startHealthTimer(){{if(!healthTimer)healthTimer=setInterval(runHealth,30000);}}
// Command queue
let cmdTimer=null;
async function queueCmd(cmd){{
  if(!cmd)return;
  try{{const r=await _f('/command',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{command:cmd}})}});
    const j=await r.json();if(j.ok){{am('ai','⚡ Queued: '+cmd);refreshCmds();}}
  }}catch(e){{am('ai','Queue error: '+e);}}
}}
function refreshCmds(){{
  _f('/api/commands').then(r=>r.json()).then(d=>{{
    const q=document.getElementById('cmd-queue');const list=document.getElementById('cmd-list');const cnt=document.getElementById('cmd-count');
    if(!d.commands||!d.commands.length){{q.style.display='none';return;}}
    q.style.display='block';
    const pending=d.commands.filter(c=>c.status==='pending');
    cnt.textContent=pending.length+' pending / '+d.commands.length+' total';
    list.innerHTML=d.commands.slice(0,3).map(c=>{{
      const sc={{'pending':'#ffaa00','done':'#00ff88','error':'#ff4444'}}[c.status]||'#888';
      const icon={{'pending':'⏳','done':'✅','error':'❌'}}[c.status]||'📋';
      const ts=c.ts?c.ts.slice(11,19):'';
      const res=c.result?` → ${{c.result.slice(0,60)}}`:'';
      return `<div style="padding:4px 0;border-bottom:1px solid #111"><span style="font-size:12px">${{icon}}</span> <span style="color:${{sc}};font-size:11px;font-weight:600">${{c.status.toUpperCase()}}</span> <span style="color:#ccc;font-size:12px">${{c.command.slice(0,80)}}</span><span style="color:#555;font-size:11px">${{res}}</span> <span style="color:#444;font-size:10px">${{ts}}</span></div>`;
    }}).join('');
  }}).catch(()=>{{}});
}}
function loadArch(){{
  _f('/api/architecture').then(r=>r.json()).then(d=>{{
    const el=document.getElementById('arch-content');
    let h='';
    // Processes
    h+=`<div style="color:var(--accent);font-weight:700;margin-bottom:8px">PROCESSES</div>`;
    Object.entries(d.processes||{{}}).forEach(([name,p])=>{{
      const sc=p.status.includes('RUNNING')?'#00ff88':p.status==='OFF'?'#555':'#ff4444';
      h+=`<div style="margin-bottom:4px"><span style="color:${{sc}};font-weight:600">${{p.status}}</span> ${{name}} <span style="color:#555">— ${{p.detail||''}}</span></div>`;
    }});
    // Pipeline
    h+=`<div style="color:var(--accent);font-weight:700;margin:16px 0 8px">TRADE PIPELINE</div>`;
    (d.pipeline||[]).forEach((s,i)=>{{
      h+=`<div style="margin-bottom:6px;padding-left:${{i*8}}px"><span style="color:#ffaa00">${{i+1}}.</span> <span style="color:#ddd;font-weight:600">${{s.stage}}</span>`;
      if(s.method) h+=` <span style="color:#888">— ${{s.method}}</span>`;
      if(s.gate) h+=` <span style="color:#ff4444">GATE: ${{s.gate}}</span>`;
      if(s.status) h+=` <span style="color:#00ff88">[${{s.status}}]</span>`;
      if(s.sources) h+=`<div style="color:#555;padding-left:16px">${{s.sources.join(' · ')}}</div>`;
      if(s.detail) h+=` <span style="color:#555">${{s.detail}}</span>`;
      h+=`</div>`;
    }});
    // LLM Roles
    h+=`<div style="color:#e879f9;font-weight:700;margin:16px 0 8px">LLM MODELS</div>`;
    Object.entries(d.llm_roles||{{}}).forEach(([role,info])=>{{
      h+=`<div style="margin-bottom:6px"><span style="color:#e879f9;font-weight:600">${{role}}</span> <span style="color:#888">(${{info.model}}, ${{info.frequency}})</span><div style="color:#aaa;padding-left:16px">${{info.purpose}}</div>`;
      if(info.last) h+=`<div style="color:#555;padding-left:16px;font-size:11px">Last: ${{info.last.slice(0,100)}}</div>`;
      h+=`</div>`;
    }});
    // Safety
    h+=`<div style="color:#ff4444;font-weight:700;margin:16px 0 8px">SAFETY</div>`;
    const sf=d.safety||{{}};
    Object.entries(sf).forEach(([k,v])=>{{
      const val=Array.isArray(v)?v.join(', '):v;
      h+=`<div style="margin-bottom:2px"><span style="color:#888">${{k}}:</span> <span style="color:#ccc">${{val}}</span></div>`;
    }});
    // Current state
    h+=`<div style="color:#00ff88;font-weight:700;margin:16px 0 8px">LIVE STATE</div>`;
    const cs=d.current_state||{{}};
    h+=`<div>Balance: $${{Number(cs.balance||0).toLocaleString()}} | Exposure: $${{Number(cs.exposure||0).toFixed(0)}} | P&L: $${{Number(cs.realized_pnl||0).toFixed(0)}} | Positions: ${{cs.positions||0}} | Ideas: ${{cs.ideas_pending||0}} | Exits: ${{cs.exit_signals||0}}</div>`;
    el.innerHTML=h;
  }}).catch(e=>{{document.getElementById('arch-content').innerHTML=`<div style="color:#ff4444">Error: ${{e}}</div>`;}});
}}
function sw(id){{document.querySelectorAll('.pane,.tab').forEach(e=>e.classList.remove('active'));document.getElementById('pane-'+id).classList.add('active');document.getElementById('tab-'+id).classList.add('active');if(id==='chat'){{document.getElementById('ci').focus();refreshCmds();if(!cmdTimer)cmdTimer=setInterval(refreshCmds,5000);}}if(id==='pnl')loadPnL();if(id==='live')loadLive();if(id==='health'){{runHealth();startHealthTimer();}}if(id==='arch')loadArch();}}
</script></body></html>"""

if __name__=='__main__':
    uvicorn.run(app,host='0.0.0.0',port=8000,reload=False)