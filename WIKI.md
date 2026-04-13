# Karpathy Kapital - System Wiki
*Persistent memory for Claude and the master agent. Updated automatically.*

## SESSION BOOTSTRAP — READ THIS FIRST

You are resuming work on **Karpathy Kapital**, an autonomous Kalshi prediction market trading system. This is REAL MONEY. Here's what you need to know immediately:

### Critical Context
1. **Model was broken, now fixed (2026-04-10):** The old probability model used a universal 0.60 base rate for all economics contracts, generating FAKE 20pp+ edges. Backtested against 51 resolved contracts it lost $183. The fix: `model/data_modifiers.py` generates modifiers from real data only (Yahoo Finance vol, FRED CPI/GDP trends, econ calendar consensus). No data = no edge = no trade.
2. **20 open paper trades exist** that were entered on the broken model. They will resolve on their own — do NOT enter new trades using flat base rates.
3. **Workers pipeline** (`workers/`): resolver (fetches Kalshi resolutions), calibrator (computes Brier/accuracy), scanner (finds data-backed opportunities). May need restarting: `python workers/run_all.py &`

### First Steps for Any New Session
```bash
# 1. Check system health
curl -s http://localhost:8000/api/health | python -m json.tool

# 2. If dashboard is down
pip install fastapi uvicorn 2>/dev/null; python dashboard.py &

# 3. If workers aren't running
pgrep -f run_all || python workers/run_all.py &

# 4. Run resolver to catch up on contract resolutions
python workers/resolver.py

# 5. Run tests
python -m pytest tests/ -q  # must be >= 139

# 6. Check what's open
sqlite3 prediction_agent.db "SELECT c.source_id, pt.side, pt.entry_price, pt.model_prob, pt.status FROM paper_trades pt JOIN contracts c ON pt.contract_id=c.id ORDER BY pt.status, c.close_time"
```

### Rules — NEVER break these
- `pytest` minimum: **139 tests**
- NEVER modify: `live/`, `master_agent/safeguards.py`, `.env`
- NEVER auto-enable live trading — human flips that switch
- NEVER generate edges from flat base rates — every edge must trace to a real data source
- ALWAYS audit model output before discussing trade thesis

## Quick Reference
- Dashboard: https://pd-sm-kk-dashboard-9ad2711e1bd44434a4f324220fb537e9.community.saturnenterprise.io
- GitHub: https://github.com/msmettigithub/prediction-agent
- DB Path: /home/jovyan/shared/sm/prediction-agent-db/prediction_agent.db
- Saturn Org: test-org (user: sm)

## Saturn Cloud Resource IDs
| Resource | ID | Type |
|---|---|---|
| master-agent | 18efeea066bc4d828b984ec1d752d131 | Deployment |
| kk-dashboard | 9ad2711e1bd44434a4f324220fb537e9 | Deployment |
| prediction-agent-auto | 6f9f09390bb3454faf88ed00419edf92 | Job (*/15 * * * *) |
| prediction-agent-db | ab695f5218d6402cb9289b821764c370 | Shared Folder |
| prediction-agent | 9929090f1a4a48f6904f947210b94708 | External Git Repo |

## Secret IDs
| Secret | ID | Env Var |
|---|---|---|
| anthropic | 46a084f1474f4b3e8db0fbe5fbeb2f1d | ANTHROPIC_API_KEY |
| kalshi_public_api_key | 6efa392feb144083aa8eec4cee65ad2c | KALSHI_API_KEY |
| fred | 25a493cef4e540b592259eaf410014d4 | FRED_API_KEY |
| GITHUB_PAT | 3b8f8e0ea8a54b3fa85e210e3e94d79b | GITHUB_PAT |
| SATURN_API_TOKEN | e0e6ce81230d456d97077984c2135f09 | SATURN_API_TOKEN |
| BANKROLL | 9a9ea2608f514ef5a9d665e5227f53b3 | BANKROLL |
| EDGE_THRESHOLD | d6f9deb20e294380ae60816b37b0a49c | EDGE_THRESHOLD |
| LIVE_TRADING_ENABLED | f7ddf60450d94f35a03c42559ca23629 | LIVE_TRADING_ENABLED |
| MAX_LIVE_BANKROLL | 706c4f79d15f49728748aa145c1f2a0b | MAX_LIVE_BANKROLL |
| MAX_SINGLE_BET | c66a5d68b7094b00b0818d56fe1a54e7 | MAX_SINGLE_BET |
| MOCK_TOOLS | b43c2c1429ac41859ff507acfd104330 | MOCK_TOOLS |
| KALSHI_PRIVATE_KEY | a2c72eae85324f7a91534b5ddae808a8 | file: /home/jovyan/.kalshi/private_key.pem |

## Saturn API Patterns
- Base: https://app.community.saturnenterprise.io/api
- Auth: Authorization: token {SATURN_API_TOKEN}
- Start deployment: POST /api/deployments/{id}/start
- Stop deployment: POST /api/deployments/{id}/stop
- Update packages: PATCH /api/deployments/{id} body: {extra_packages:{pip:'...'}}
- Attach secret: POST /api/deployments/{id}/secrets {secret_id, location, attachment_type}
- Attach git repo: POST /api/external_repo_attachments {external_repo_id, deployment_id, path}
- Attach shared folder: POST /api/shared_folder_attachments {shared_folder_id, deployment_id, path}
- Get logs: GET /api/deployments/{id}/logs?page_size=100
- Available instance sizes: medium, large, xlarge (small NOT available)
- Base image ID: 9879fc989f054272903cd4afd5e520bd

## Bootstrap Method (Claude pushing code to Saturn)
Claude cannot use bash_tool (network disabled). Cannot paste API tokens in chat.
Method: JavaScript fetch() in browser tab on Saturn Cloud domain uses session cookies.

For GitHub file pushes: Create Saturn Cloud Job with GITHUB_PAT secret.
Push job command: python3 -c "import base64;exec(base64.b64decode('B64').decode())"
Inner script calls GitHub Contents API: PUT https://api.github.com/repos/{owner}/{repo}/contents/{path}
Size limit: command field ~20KB b64 OK, start_script keep under 500 chars.

## Architecture
prediction-agent/
  main.py           - CLI: scan, paper, backtest, health, calibrate, live
  CLAUDE.md         - Standing orders (NEVER modify)
  WIKI.md           - This file
  model/            - Probability model, Kelly sizing
  scanner/          - Kalshi + Polymarket scanner
  tools/            - 13 data source plugins
  live/             - PROTECTED: Kalshi trader + guard
  database/         - SQLite WAL
  tests/            - 139 tests (never drop below)
  master_agent/
    loop.py         - OODA loop v5: self-healing, parallel, command queue
    doctor.py       - Monitors resources, diagnoses crashes, fixes and revives
    observe.py      - DB reader
    confidence_gate.py - 6-check gate
    changelog.py    - agent_log + agent_changes tables
    safeguards.py   - Protected files, rate limits
    wiki.py         - Read/write this wiki
  dashboard.py      - FastAPI: mobile+desktop, chat tab, command queue

## Master Agent v5 Behavior
- OODA cycle: 5min base, compresses to 1min as performance improves
- 2 parallel sub-agents (scales to 4 at >80% deploy rate)
- Doctor runs every cycle: monitors master-agent + kk-dashboard + paper-auto job
- Command queue: agent_commands table, processes within 5min
- Explorer agents: tests tool from curated list every 3rd cycle
- Budget: $50/day hard cap, 90% threshold triggers throttle
- Rate limit: 1 code push per hour

## Karpathy Framework
- Prompt = model. Eval harness = loss function. Resolved trades = training data.
- RL: each code change is action, Brier improvement is reward, failed tests = penalty
- Voracious learner: test everything, document in tool_experiments table

## Confidence Gate (6 checks - ALL must pass for live trading)
1. n_resolved >= 30
2. accuracy > 65%
3. Brier score < 0.25
4. separation > 10pp  <-- CURRENT BLOCKER (at 6.1%, need 10%)
5. paper_win_rate_rolling_20 > 55%
6. positive_ev_all_categories
Current: 139 tests, 51 seeded contracts, Brier 0.168, acc 80.4%, sep 6.1%
First real data: April 10 2026 (S&P contracts resolve)

## Safety Rules
NEVER modify: live/kalshi_trader.py, live/guard.py, master_agent/safeguards.py, .env, CLAUDE.md
NEVER auto-enable live trading
ALWAYS run pytest before push - 139 tests minimum
$50/day budget hard cap

## Orders Channel
Dashboard Chat tab -> claude-sonnet-4-6 with full context -> auto-queues [COMMAND: ...] to DB
Dashboard Commands tab -> direct insert to agent_commands table
This chat -> Claude creates Saturn job to inject into agent_commands DB

## Tool Experiments
- 2026-04-10 00:09: ?: SKIP — No library name, description, or code was provided to evaluate. Cannot assess ut
- 2026-04-07 17:16: polymarket-py: SKIP — polymarket-py is a Python library for Polymarket, not Kalshi. These are two sepa
- 2026-04-07 17:07: gnews: USEFUL — GNews provides alternative news feeds that can supply real-time and recent news 
- 2026-04-07 16:55: beautifulsoup4: USEFUL — BeautifulSoup4 can scrape RealClearPolitics poll aggregates to extract polling a
- 2026-04-07 09:36: scipy: USEFUL — SciPy provides signal processing and statistical tools that can be applied to hi
- 2026-04-07 09:26: alpha_vantage: SKIP — Alpha Vantage provides traditional economic indicators (GDP, CPI, unemployment, 
- 2026-04-07 09:17: pandas-ta: USEFUL — pandas-ta provides technical analysis indicators (RSI, MACD, Bollinger Bands, et
- 2026-04-07 09:08: textblob: SKIP — TextBlob provides basic rule-based sentiment polarity and subjectivity scores de
- 2026-04-07 08:59: vaderSentiment: USEFUL — VADER sentiment analysis can provide supplementary signal for Kalshi prediction 
- 2026-04-07 08:50: statsmodels: USEFUL — Statsmodels is moderately useful for Kalshi prediction market trading. Logistic 
- 2026-04-07 08:40: newsapi: USEFUL — NewsAPI provides real-time headlines that can serve as leading indicators for Ka
- 2026-04-07 08:31: yfinance: USEFUL — yfinance provides historical and real-time equity, ETF, commodity, and macro ass
- 2026-04-07 08:15: pytrends: USEFUL — Pytrends provides Google Trends data that can serve as a leading indicator for K
(Updated by explorer agents - see tool_experiments DB table)

## Decision Log
- 2026-04-10 21:13: v6: ASCII fix + JSON retry logic | rationale: UTF-8 encoding bug caused all orient calls to fail
- 2026-04-10 19:28: v6: ASCII fix + JSON retry logic | rationale: UTF-8 encoding bug caused all orient calls to fail
- 2026-04-10 19:07: v6: ASCII fix + JSON retry logic | rationale: UTF-8 encoding bug caused all orient calls to fail
- 2026-04-10 03:19: CRITICAL FIX: Replaced phantom edge model with data-driven modifiers | rationale: Old model used universal 0.60 base rate for all economics, creating fake 20pp edges. Backtested: -$183 on 51 resolved. New model starts from market price, only deviates with real data (Yahoo vol, FRED, consensus). Built workers/resolver.py, calibrator.py, scanner_worker.py. Added /api/health endpoint with 8 automated checks. Dashboard now has Health tab, command queue in chat, positions tab.
- 2026-04-08 22:38: v6: ASCII fix + JSON retry logic | rationale: UTF-8 encoding bug caused all orient calls to fail
- 2026-04-08 22:23: v6: ASCII fix + JSON retry logic | rationale: UTF-8 encoding bug caused all orient calls to fail
- 2026-04-08 21:23: v6: ASCII fix + JSON retry logic | rationale: UTF-8 encoding bug caused all orient calls to fail
- 2026-04-08 21:09: v6: ASCII fix + JSON retry logic | rationale: UTF-8 encoding bug caused all orient calls to fail
- 2026-04-08 21:01: v6: ASCII fix + JSON retry logic | rationale: UTF-8 encoding bug caused all orient calls to fail
- 2026-04-07 17:00: v6: ASCII fix + JSON retry logic | rationale: UTF-8 encoding bug caused all orient calls to fail
- 2026-04-07 16:56: v6: ASCII fix + JSON retry logic | rationale: UTF-8 encoding bug caused all orient calls to fail
- 2026-04-07 08:25: v6: ASCII fix + JSON retry logic | rationale: UTF-8 encoding bug caused all orient calls to fail
- 2026-04-07 08:10: Full autonomy fixed — file tree embedded | rationale: prevent nonexistent file errors
- 2026-04-07 07:55: Full autonomy mode activated | rationale: user instruction: go full speed
2026-04-07: Bootstrapped master_agent/ via GitHub API (no local dev needed)
2026-04-07: Added doctor.py - self-healing infrastructure
2026-04-07: Added wiki.py - RAG/memory system to survive context compaction
2026-04-07: Dashboard responsive for mobile (iPhone 16) + desktop
2026-04-07: Budget cap $50/day, parallel sub-agents 2->4 based on RL performance

## Calibration History
(Updated by master agent after each calibration run)
2026-04-07: Initial state - 51 seeded backtest contracts, Brier 0.168

## Current Live State (auto-updated)
Last update: 2026-04-13 00:06 UTC
- gate: False
- resolved: 0
- pnl: 0
- rl_rate: 0.1
- cost: $42.78
## Session Log
- 2026-04-10 17:35: SESSION 2026-04-10: Major architecture rebuild.
BALANCE: $3,378 (started $1,710, dipped to $1,362, recovered via settlements).
NET REALIZED: -$85 (down from -$275 after WTI/BTC settlements paid out).

WHAT WAS BUILT:
- model/data_modifiers.py: real data → probability modifiers (Yahoo vol, BLS CPI/GDP, Deribit IV)
- model/market_intel.py: aggregates 6 feeds into per-asset direction+conviction+vol regime
- model/trade_pnl.py: P&L forecast at entry/hold/exit (cost, fees, EV, breakeven, risk/reward)
- model/risk.py: drawdown circuit breaker ($50/day loss, 80% exposure, 40% concentration)
- model/llm_reviewer.py: Claude reviews every trade, generates ideas, audits portfolio+code
- workers/trading_brain.py: unified observe/decide/act loop (2s cycles, 18K+ cycles today)
- workers/market_maker.py: quotes both sides near-the-money (disabled — no fills)
- workers/reconciler.py: syncs Kalshi positions to local DB
- workers/fill_tracker.py: catches fills, cancels stale orders
- workers/auto_trader.py: NO-on-cheap-YES strategy (97% backtest win rate)
- workers/live_monitor.py: replaced by trading_brain.py
- master_agent/auditor.py: 8 checks, auto-restarts brain+dashboard
- Dashboard: Live tab (intel+ideas+positions), Arch tab (live architecture), chat trades, search

CRITICAL FIXES:
- Old probability model used fake 0.60 base rate → replaced with market price + real data
- Brain sold winning BTC/Gold positions → exit logic rebuilt (only take-profit, no stop-loss on binaries)
- CPI/GDP had no data → now using BLS API (no key needed)
- Backtest proved: buying cheap YES loses money, selling cheap YES (buying NO) = 97% win rate

WHAT'S RUNNING:
- trading_brain.py: 2s cycles, monitors all positions, generates ideas via Claude every 5min
- run_all.py: fill_tracker(30s), reconciler(60s), resolver(120s), calibrator(300s)
- dashboard.py: port 8000
- Brain trading ENABLED at $7 max bet, every trade goes through LLM review

KNOWN ISSUES:
- Vol model miscalibrated for multi-day contracts (Claude correctly rejects these)
- Market maker had zero fills — disabled
- OpenRouter API key not found in environment — only Claude available for LLM review
- .env has LIVE_TRADING_ENABLED=true with $1000/$200 limits (should be lower)
