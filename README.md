# Prediction Market Trading Research Agent

A Python-based prediction market research agent that scans Kalshi and Polymarket for mispriced contracts, estimates probabilities using base rate models and research tools, and manages a paper trading portfolio to validate edge before risking real capital.

## Setup

```bash
git clone <repo>
cd prediction_agent
pip install -r requirements.txt
cp .env.example .env
# Fill in API keys in .env (ANTHROPIC_API_KEY required for deep-dive research)
```

## Commands

| Command | Description |
|---------|-------------|
| `python main.py seed` | Seed DB with resolved Kalshi contracts for backtesting |
| `python main.py backtest` | Run backtester and print calibration report |
| `python main.py backtest --diagnostic` | Audit seeded data for lookahead bias and distribution skew |
| `python main.py scan` | Scan markets, print table of top edges |
| `python main.py research <id>` | Deep-dive research on a specific contract using Claude Sonnet |
| `python main.py paper` | Paper trading: settle resolved trades, scan for new edges, record bets |
| `python main.py paper --edge-threshold=0.05` | Paper trade with custom edge threshold |
| `python main.py paper --auto` | Auto mode for cron (quiet output) |
| `python main.py calibrate` | Show paper trading calibration report |
| `python main.py health` | Health check all 13 research tools |
| `python main.py monitor [mins]` | Continuous scanner loop with alerts |

## Architecture

The system is organized into 7 modules: **scanner** (market ingestion + filtering), **model** (base rate anchoring + probability estimation + Kelly sizing), **backtest** (historical validation with lookahead-bias diagnostics), **agent** (Claude Sonnet deep-dive with autonomous tool calling), **tools** (13 pluggable data sources with mock mode), **database** (SQLite with WAL + migrations), and **CLI** (all commands via main.py). The probability model works in log-odds space, blending category base rates with market prices and applying modifiers from research tools. Position sizing uses quarter-Kelly with a 5% bankroll hard cap.

## Automated Paper Trading (Cron)

```bash
# Run every 4 hours
crontab -e
# Add this line:
0 */4 * * * /bin/bash "/path/to/prediction_agent/scripts/cron_paper.sh"
```

Logs are written to `logs/paper_trading.log` with automatic rotation at 10MB.

## Environment Variables

See `.env.example` for the full list. Required for live operation:
- `ANTHROPIC_API_KEY` — for deep-dive research agent
- `KALSHI_API_KEY` — for authenticated Kalshi access (public endpoint works without)

Optional (enhance research quality):
- `FRED_API_KEY`, `TAVILY_API_KEY`, `BRAVE_SEARCH_API_KEY`, `ODDS_API_KEY`, `METACULUS_API_TOKEN`
