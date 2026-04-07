"""Kalshi REST API v2 — fetch open and resolved markets."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

import requests

from config import load_config
from tools.base_tool import BaseTool, ToolFetchError

logger = logging.getLogger(__name__)

# --- Series ticker → category mapping ---
# Only fetch from known series to avoid exotic multivariate parlays (KXMVE*).

KNOWN_SERIES = [
    "KXFEDRATE", "KXCPI", "KXGDP", "KXUNRATE",  # economics
    "KXSPX", "KXNASDAQCOMP", "KXINX",             # index/economics
    "KXBTC", "KXBTCD", "KXBTCW", "KXETH",         # crypto
    "KXELECTION", "KXSENATE", "KXHOUSE", "KXPOTUS",# politics
    "KXNHL", "KXNBA", "KXNFL", "KXMLB",           # sports
    "KXNHLTOTAL", "KXNBAML", "KXNHLML",           # sports (totals/ML)
    "KXNFLML", "KXMLBML",                          # sports (ML)
]

SERIES_TO_CATEGORY = {
    "KXFEDRATE": "economics",
    "KXCPI": "economics",
    "KXGDP": "economics",
    "KXUNRATE": "economics",
    "KXSPX": "economics",
    "KXNASDAQCOMP": "economics",
    "KXINX": "economics",
    "KXBTC": "crypto",
    "KXBTCD": "crypto",
    "KXBTCW": "crypto",
    "KXETH": "crypto",
    "KXELECTION": "politics",
    "KXSENATE": "politics",
    "KXHOUSE": "politics",
    "KXPOTUS": "politics",
    "KXNHL": "sports",
    "KXNBA": "sports",
    "KXNFL": "sports",
    "KXMLB": "sports",
    "KXNHLTOTAL": "sports",
    "KXNBAML": "sports",
    "KXNHLML": "sports",
    "KXNFLML": "sports",
    "KXMLBML": "sports",
}


def derive_category(ticker: str, event_ticker: str = "", series_ticker: str = "") -> str:
    """Derive category from ticker/event_ticker/series_ticker using SERIES_TO_CATEGORY.
    Returns 'unknown' if no match found."""
    # Try series_ticker first (most reliable)
    for source in [series_ticker, event_ticker, ticker]:
        if not source:
            continue
        for prefix, cat in SERIES_TO_CATEGORY.items():
            if source.upper().startswith(prefix):
                return cat
    # Check if it's a multivariate exotic — never assign a real category
    if ticker.startswith("KXMVE"):
        return "exotic"
    return "unknown"


# Mock data for testing without API access
MOCK_OPEN_MARKETS = [
    {
        "ticker": "FED-RATE-26JUN-5.00",
        "title": "Will the Fed cut rates by June 2026?",
        "yes_price": 0.62,
        "volume_24h": 45000,
        "close_time": "2026-06-30T20:00:00Z",
        "category": "economics",
        "open_time": "2026-01-15T12:00:00Z",
    },
    {
        "ticker": "SCOTUS-TERM-26",
        "title": "Will a Supreme Court justice retire by end of 2026 term?",
        "yes_price": 0.28,
        "volume_24h": 12000,
        "close_time": "2026-10-01T00:00:00Z",
        "category": "politics",
        "open_time": "2026-01-01T00:00:00Z",
    },
    {
        "ticker": "BTC-100K-26APR",
        "title": "Will Bitcoin exceed $100K by April 30, 2026?",
        "yes_price": 0.45,
        "volume_24h": 89000,
        "close_time": "2026-04-30T23:59:59Z",
        "category": "crypto",
        "open_time": "2026-02-01T00:00:00Z",
    },
    {
        "ticker": "RAIN-NYC-26APR07",
        "title": "Will it rain in NYC on April 7, 2026?",
        "yes_price": 0.50,
        "volume_24h": 500,
        "close_time": "2026-04-07T23:59:59Z",
        "category": "weather",
        "open_time": "2026-04-01T00:00:00Z",
    },
]

MOCK_RESOLVED_MARKETS = [
    {"ticker": "FED-RATE-25DEC-5.25", "title": "Will the Fed hold rates through Dec 2025?",
     "yes_price": 0.70, "volume_24h": 30000, "close_time": "2025-12-31T20:00:00Z",
     "category": "economics", "open_time": "2025-06-01T00:00:00Z",
     "resolved": True, "resolution": True, "resolved_at": "2025-12-31T20:00:00Z"},
    {"ticker": "INAUG-CROWD-25", "title": "Will inauguration crowd exceed 1M?",
     "yes_price": 0.35, "volume_24h": 15000, "close_time": "2025-01-20T18:00:00Z",
     "category": "politics", "open_time": "2024-11-15T00:00:00Z",
     "resolved": True, "resolution": False, "resolved_at": "2025-01-21T00:00:00Z"},
    {"ticker": "BTC-50K-25MAR", "title": "Will Bitcoin exceed $50K by March 2025?",
     "yes_price": 0.80, "volume_24h": 120000, "close_time": "2025-03-31T23:59:59Z",
     "category": "crypto", "open_time": "2024-12-01T00:00:00Z",
     "resolved": True, "resolution": True, "resolved_at": "2025-02-15T00:00:00Z"},
    {"ticker": "TRUMP-PARDON-25Q1", "title": "Will Trump issue >10 pardons in Q1 2025?",
     "yes_price": 0.55, "volume_24h": 25000, "close_time": "2025-03-31T23:59:59Z",
     "category": "politics", "open_time": "2025-01-20T00:00:00Z",
     "resolved": True, "resolution": True, "resolved_at": "2025-03-31T23:59:59Z"},
    {"ticker": "SPX-5500-25Q1", "title": "Will S&P 500 close above 5500 by March 2025?",
     "yes_price": 0.60, "volume_24h": 50000, "close_time": "2025-03-31T23:59:59Z",
     "category": "economics", "open_time": "2025-01-02T00:00:00Z",
     "resolved": True, "resolution": True, "resolved_at": "2025-03-28T16:00:00Z"},
]


class KalshiTool(BaseTool):
    # trading-api requires auth; elections endpoint allows public reads
    # Kalshi consolidated their API endpoints — both reads and trading now go through
    # api.elections.kalshi.com.
    TRADING_URL = "https://api.elections.kalshi.com/trade-api/v2"
    PUBLIC_URL = "https://api.elections.kalshi.com/trade-api/v2"

    @property
    def name(self) -> str:
        return "kalshi"

    def __init__(self, mock_mode: bool = False):
        self.mock_mode = mock_mode
        self.config = load_config()
        self._last_request = 0.0
        self._min_interval = 1.0  # rate limit: 1 req/sec
        self.base_url = self.TRADING_URL if self.config.kalshi_api_key else self.PUBLIC_URL

    def get_schema(self) -> dict:
        return {
            "name": "kalshi",
            "description": "Fetch prediction market data from Kalshi. Can retrieve open or resolved markets.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["open", "resolved"], "default": "open"},
                    "series_ticker": {"type": "string", "description": "Filter by series ticker"},
                    "limit": {"type": "integer", "default": 100},
                },
            },
        }

    def _rate_limit(self):
        elapsed = time.time() - self._last_request
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request = time.time()

    # "settled" is the Kalshi API v2 query param for resolved/finalized markets
    _STATUS_MAP = {
        "open": "open",
        "resolved": "settled",
        "finalized": "settled",
        "settled": "settled",
        "closed": "closed",
    }

    def _fetch(self, status: str = "open", series_ticker: str = None,
               category: str = None, limit: int = 100,
               cursor: str = None, max_pages: int = 1) -> Any:
        """Fetch markets from Kalshi.

        When max_pages > 1, follows the Kalshi cursor-based pagination
        until either max_pages requests have been made OR the cursor
        is empty (no more results). Returns a flat list of market dicts.
        """
        if self.mock_mode:
            if status in ("resolved", "finalized", "settled"):
                return MOCK_RESOLVED_MARKETS
            return MOCK_OPEN_MARKETS

        api_status = self._STATUS_MAP.get(status, status)
        headers = {}
        if self.config.kalshi_api_key:
            headers["Authorization"] = f"Bearer {self.config.kalshi_api_key}"

        all_markets = []
        next_cursor = cursor
        for page in range(max_pages):
            self._rate_limit()
            params = {"limit": limit, "status": api_status}
            if series_ticker:
                params["series_ticker"] = series_ticker
            if next_cursor:
                params["cursor"] = next_cursor

            page_markets, next_cursor = self._fetch_page(params, headers)
            all_markets.extend(page_markets)
            if not next_cursor or not page_markets:
                break

        return all_markets

    def _fetch_page(self, params: dict, headers: dict) -> tuple:
        """Single page fetch. Returns (markets, next_cursor)."""
        for url in [self.base_url, self.PUBLIC_URL]:
            resp = None
            try:
                resp = requests.get(f"{url}/markets", params=params, headers=headers, timeout=10)
                if resp.status_code == 401 and url == self.TRADING_URL:
                    continue
                resp.raise_for_status()
                data = resp.json()
                return data.get("markets", []), data.get("cursor", "")
            except requests.RequestException as e:
                if url == self.base_url and url != self.PUBLIC_URL:
                    continue
                status_code = resp.status_code if resp is not None else "?"
                raise ToolFetchError(f"Kalshi API error (HTTP {status_code}): {e}")
        raise ToolFetchError("Kalshi API: all endpoints failed")

    def fetch_single_market(self, ticker: str) -> dict | None:
        """Fetch a single market by ticker from the public /markets/{ticker} endpoint.

        Returns parsed market dict (same schema as _parse) or None if not found.
        Used by paper/live settlement loops to check if a contract has resolved
        without needing to pull the full open/settled listings.
        """
        if self.mock_mode:
            # Return mock resolved contract matching the ticker if present
            for m in MOCK_RESOLVED_MARKETS + MOCK_OPEN_MARKETS:
                if m.get("ticker") == ticker:
                    parsed = self._parse([m])
                    return parsed["markets"][0] if parsed["markets"] else None
            return None

        self._rate_limit()
        for url in [self.base_url, self.PUBLIC_URL]:
            try:
                resp = requests.get(f"{url}/markets/{ticker}", timeout=10)
                if resp.status_code == 404:
                    return None
                if resp.status_code == 401 and url == self.TRADING_URL:
                    continue
                resp.raise_for_status()
                data = resp.json()
                market = data.get("market") or {}
                if not market:
                    return None
                parsed = self._parse([market])
                return parsed["markets"][0] if parsed["markets"] else None
            except requests.RequestException as e:
                if url == self.base_url and url != self.PUBLIC_URL:
                    continue
                logger.warning(f"fetch_single_market({ticker}) failed: {e}")
                return None
        return None

    def fetch_known_series(self, status: str = "settled", limit_per_series: int = 200,
                           price_min: float = 0.10, price_max: float = 0.90,
                           min_lifetime_hours: float = 24,
                           max_pages_per_series: int = 10) -> dict:
        """Fetch markets from all known series tickers.

        Uses Kalshi cursor pagination — pulls up to max_pages_per_series
        pages of limit_per_series markets each (default: 5 pages × 200 =
        1000 markets per series). This dramatically increases the size
        of the historical seed for backtesting.

        Filters:
        - Price in [price_min, price_max] range only (genuine uncertainty)
        - Lifetime > min_lifetime_hours (meaningful price discovery)
        - Skips exotics (KXMVE*) and zero-volume contracts
        - Deduplicates by ticker

        Returns {markets: [], skipped: {reason: count}}.
        """
        all_markets = []
        seen_tickers = set()
        skipped = {
            "exotic": 0, "zero_volume_and_price": 0, "duplicate": 0,
            "out_of_price_range": 0, "too_short_lived": 0,
        }

        for series in KNOWN_SERIES:
            try:
                raw = self._fetch(status=status, series_ticker=series,
                                  limit=limit_per_series, max_pages=max_pages_per_series)
                parsed = self._parse(raw)
                for m in parsed["markets"]:
                    ticker = m["ticker"]

                    if ticker.startswith("KXMVE"):
                        skipped["exotic"] += 1
                        continue

                    if m["volume_24h"] == 0.0:
                        skipped["zero_volume_and_price"] += 1
                        continue

                    # Price range filter — only contracts with genuine uncertainty
                    if not (price_min <= m["yes_price"] <= price_max):
                        skipped["out_of_price_range"] += 1
                        continue

                    # Lifetime filter — skip contracts that resolved too quickly
                    open_t = m.get("open_time", "")
                    close_t = m.get("resolved_at", m.get("close_time", ""))
                    if open_t and close_t:
                        try:
                            from datetime import datetime as _dt
                            ot = _dt.fromisoformat(str(open_t).replace("Z", "+00:00")).replace(tzinfo=None)
                            ct = _dt.fromisoformat(str(close_t).replace("Z", "+00:00")).replace(tzinfo=None)
                            lifetime_hours = (ct - ot).total_seconds() / 3600
                            if lifetime_hours < min_lifetime_hours:
                                skipped["too_short_lived"] += 1
                                continue
                        except (ValueError, TypeError):
                            pass  # can't parse dates — keep the contract

                    if ticker in seen_tickers:
                        skipped["duplicate"] += 1
                        continue
                    seen_tickers.add(ticker)

                    all_markets.append(m)
            except ToolFetchError as e:
                logger.warning(f"Failed to fetch {series}: {e}")
                continue

        return {"markets": all_markets, "skipped": skipped}

    def _parse(self, raw_data: Any) -> dict:
        markets = []
        for m in raw_data:
            if isinstance(m, dict):
                self.validate_response(m, ["ticker", "title"])

            ticker = m.get("ticker", "")
            event_ticker = m.get("event_ticker", "")
            series_ticker_val = m.get("series_ticker", "")

            # --- Price extraction ---
            yes_price = m.get("yes_price")
            if yes_price is not None:
                # Mock format — already 0-1 float
                yes_price = float(yes_price)
                if yes_price > 1.0:
                    yes_price = yes_price / 100.0
            else:
                # Live API — dollar-string fields
                last = m.get("last_price_dollars") or m.get("last_price")
                bid = m.get("yes_bid_dollars") or m.get("yes_bid")
                ask = m.get("yes_ask_dollars") or m.get("yes_ask")
                if last and str(last).replace(".", "").replace("-", "").isdigit():
                    yes_price = float(last)
                elif bid or ask:
                    b = float(bid) if bid else 0
                    a = float(ask) if ask else 0
                    yes_price = (b + a) / 2.0 if (b or a) else 0
                else:
                    yes_price = 0.0
                if yes_price > 1.0:
                    yes_price = yes_price / 100.0

            # --- Volume ---
            # Prefer total volume (volume_fp) over 24h volume for settled markets
            # because 24h volume is 0 after settlement
            vol = m.get("volume_24h")
            if vol is None:
                vfp = float(m.get("volume_fp", 0) or 0)
                v24 = float(m.get("volume_24h_fp", 0) or 0)
                vol = max(vfp, v24) if (vfp or v24) else (m.get("volume") or 0)
            volume = float(vol)

            # --- Category ---
            category = m.get("category", "")
            if not category or category == "":
                category = derive_category(ticker, event_ticker, series_ticker_val)
            if category == "unknown":
                logger.warning(f"Unknown category for ticker {ticker} (event={event_ticker})")

            market = {
                "ticker": ticker,
                "title": m.get("title", ""),
                "yes_price": yes_price,
                "volume_24h": volume,
                "close_time": m.get("close_time", m.get("expected_expiration_time", "")),
                "category": category,
                "open_time": m.get("open_time", m.get("created_time", "")),
            }

            # --- Resolution detection ---
            is_resolved = m.get("resolved", False)
            api_status = m.get("status", "")
            if api_status in ("finalized", "settled"):
                is_resolved = True

            if is_resolved:
                market["resolved"] = True
                result_val = m.get("result", m.get("resolution"))
                if isinstance(result_val, str):
                    market["resolution"] = result_val.lower() == "yes"
                elif isinstance(result_val, bool):
                    market["resolution"] = result_val
                else:
                    market["resolution"] = None
                market["resolved_at"] = m.get("resolved_at",
                    m.get("settlement_ts",
                    m.get("expiration_time", m.get("close_time", ""))))

            markets.append(market)
        return {"markets": markets, "source": "kalshi", "fetched_at": datetime.utcnow().isoformat(), "confidence": "high" if markets else "low"}

    def check_auth(self) -> bool:
        key = self.config.kalshi_api_key
        if not key:
            raise ToolFetchError("Kalshi API key not configured. Set KALSHI_API_KEY in .env")
        if len(key) < 10:
            raise ToolFetchError(f"Kalshi API key appears malformed (len={len(key)}).")
        return True

    def health_check(self) -> dict:
        if self.mock_mode:
            return {"healthy": True, "latency_ms": 1.0, "error": None}
        start = time.time()
        try:
            headers = {}
            if self.config.kalshi_api_key:
                headers["Authorization"] = f"Bearer {self.config.kalshi_api_key}"
            for url in [self.base_url, self.PUBLIC_URL]:
                resp = requests.get(
                    f"{url}/markets", params={"limit": 1, "status": "open"},
                    headers=headers, timeout=5,
                )
                if resp.status_code == 200:
                    latency = (time.time() - start) * 1000
                    return {"healthy": True, "latency_ms": latency, "error": None}
            latency = (time.time() - start) * 1000
            return {"healthy": False, "latency_ms": latency, "error": f"HTTP {resp.status_code}"}
        except Exception as e:
            return {"healthy": False, "latency_ms": 0, "error": str(e)}
