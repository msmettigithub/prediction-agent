# Design Decisions

## Architecture

1. **SQLite over Postgres**: Single-file DB for portability. No external dependencies for dev/testing. WAL mode for concurrent reads during scanner + backtest.

2. **Sync over async**: Using `requests` + synchronous SQLite. The bottleneck is API rate limits, not I/O concurrency. Keeps code simple and debuggable. Can migrate to async later if scanner polling needs parallelism.

3. **Tool auto-discovery**: `tool_registry.py` uses `importlib` to scan the `tools/` directory. Any file that isn't `base_tool.py` or `tool_registry.py` and contains a class inheriting `BaseTool` gets registered. No manual registration needed.

4. **Quarter-Kelly with 5% cap**: Full Kelly is optimal for log-wealth but assumes perfect probability estimates. We don't have perfect estimates. Quarter-Kelly reduces variance by ~75% while capturing ~50% of the growth rate. The 5% hard cap prevents catastrophic loss from a single miscalibrated bet — even if Kelly says 20%, we never risk more than 5% of bankroll on one contract.

## Probability Model

5. **Base rate categories**: Starting with 6 categories:
   - Politics/Elections (base rate from historical polling accuracy)
   - Economics/Fed (base rate from Fed funds futures accuracy)
   - Crypto (high uncertainty prior, wide confidence intervals)
   - Sports (base rate from closing line value studies)
   - Science/Tech (base rate from Metaculus community calibration)
   - Legal/Regulatory (base rate from historical precedent frequency)

6. **Probability caps at 7%/93%**: Never predict anything with >93% or <7% confidence. This matches superforecaster calibration — even "certain" events fail ~5-7% of the time. Enforced at the model output layer, not in individual modifiers, so modifiers can express extreme views without clipping.

7. **Confidence intervals**: Using bootstrap-style CI from modifier disagreement. If 3 modifiers say 60% and one says 30%, the CI is wide. This feeds into Kelly sizing — wider CI → smaller bet.

## Backtesting

8. **No lookahead enforcement**: Backtest filters all data by `contract.open_time`. Tools return data with timestamps; anything after open_time is discarded. This is checked programmatically, not by convention.

9. **Minimum 30 resolved contracts for calibration**: Below this, Brier scores and reliability diagrams are statistically meaningless. The system warns but doesn't prevent backtesting with fewer — it just won't report calibration metrics.

10. **Calibration thresholds**: >65% directional accuracy, Brier <0.25, separation >10pp. These are intentionally modest — we're targeting "better than naive" not "perfect."

## Data Sources

11. **Free tiers only**: All tool implementations use free API tiers. Rate limiting is built into each tool. Mock mode (`MOCK_TOOLS=true`) returns fixture data for testing without hitting APIs.

12. **Cross-market divergence**: When Kalshi and Polymarket disagree by >10pp on the same contract (matched by title similarity using difflib), the contract is flagged HIGH PRIORITY. Market disagreement is a strong signal of either mispricing or information asymmetry.

## CLI

13. **Click for CLI**: Using `click` library for subcommand routing. Clean `--help` output, typed arguments.

14. **Rich for output**: Using `rich` library for tables, colored output, progress bars. Falls back to plain text if not installed.

## API Authentication Audit (Phase C)

15. **Kalshi v2 Auth**: Kalshi's v2 API uses two authentication methods:
    - **API Key + Secret**: Pass as `Authorization: Bearer <api_key>` header. The key is a UUID-format string. For trading endpoints, they use HMAC signing with the secret.
    - **JWT tokens**: Some endpoints accept JWT. Tokens expire and need refresh.
    - **Our implementation**: Uses `Authorization: Bearer` header with the API key. For read-only market data (which is all we do), this is sufficient. If Kalshi returns 401, check key format first via `check_auth()`.
    - **Confirmed**: Key goes in `Authorization` header, NOT in query params.

16. **Polymarket Auth**: Two distinct APIs:
    - **Gamma API** (`gamma-api.polymarket.com`): Public, no key needed for read-only market data. This is what we use for scanning.
    - **CLOB API** (`clob.polymarket.com`): Requires API key for order placement. Read-only queries (orderbook, markets) may work without auth.
    - **Our implementation**: Uses Gamma API for market data reads. No auth required. POLYMARKET_API_KEY is reserved for future CLOB integration.

17. **FRED Auth**: FRED API uses a simple query parameter approach.
    - Key goes in `?api_key=<key>` as a URL parameter, NOT in any header.
    - Keys are 32-character lowercase hex strings.
    - Free tier: 120 requests/minute.
    - **Confirmed**: Our implementation correctly passes key as `api_key` param.

18. **Tavily Auth**: Tavily Search API uses JSON body auth, not headers.
    - Key is passed as `"api_key": "<key>"` inside the POST JSON body.
    - Key format: `tvly-*` prefixed string.
    - NOT passed as `Authorization: Bearer` or any header.
    - **Confirmed**: Our implementation correctly passes key in JSON body.

19. **Brave Search Auth**: Uses a custom header.
    - Key goes in `X-Subscription-Token: <key>` header.
    - NOT `Authorization: Bearer`.
    - Key format: `BSA*` prefixed string.
    - **Confirmed**: Our implementation correctly uses `X-Subscription-Token`.

## Rate Limiting

20. **Token bucket rate limiter**: Default 10 requests/minute per tool. Configurable per-tool via `RATE_LIMIT_<TOOLNAME>=N` env var (e.g., `RATE_LIMIT_KALSHI=5`). On hit: sleep + warning log, never raise. Individual tools also have their own built-in `_rate_limit()` method for API-specific intervals — the token bucket is an additional safety layer.
