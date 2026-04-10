# Prediction Agent Standing Orders
## Mandate: Maximize Kalshi returns. Every decision: edge, sizing, win rate. REAL MONEY.
## Rules: pytest min 139. NEVER modify live/, master_agent/safeguards.py, .env. Human flips live switch only.
## Resume: Read WIKI.md first — `python -c "from master_agent.wiki import read_wiki; print(read_wiki()[:3000])"` — it has a bootstrap prompt with critical context and first steps.
## Model: Edges MUST come from real data (Yahoo vol, FRED, consensus). NEVER from flat base rates. See model/data_modifiers.py.
## Workers: workers/run_all.py runs resolver, calibrator, scanner. Check: `pgrep -f run_all`
## Arch: model/ scanner/ tools/ live/ database/ master_agent/ agent/ workers/ tests/