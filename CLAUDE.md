# Prediction Agent Standing Orders
## Mandate: Maximize Kalshi returns. Every decision: edge, sizing, win rate. REAL MONEY. Balance ~$3,400.
## Rules: pytest min 139. NEVER modify live/, master_agent/safeguards.py, .env. Human flips live switch only.
## Resume: Read WIKI.md first — `python -c "from master_agent.wiki import read_wiki; print(read_wiki()[:5000])"` — it has bootstrap prompt + session log.
## Architecture: model/ workers/ tools/ live/ database/ master_agent/ agent/ tests/
## Key files: workers/trading_brain.py (main loop), model/market_intel.py (data), model/trade_pnl.py (P&L), model/llm_reviewer.py (Claude review)
## Startup: `pgrep -f trading_brain || BRAIN_TRADE_ENABLED=true python -u workers/trading_brain.py &` then `pgrep -f run_all || python -u workers/run_all.py &` then `pgrep -f dashboard || python -u dashboard.py &`
## Critical lessons: NEVER sell binary contracts at a loss (hold to settlement). NEVER trust vol model edges >20pp on multi-day contracts. ALWAYS check entry_price vs sell_price before any exit. ALL trades go through LLM review.