"""Polymarket Gamma API + CLOB API — fetch markets and match to Kalshi contracts."""

from __future__ import annotations

import logging
import time
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)
from typing import Any

import requests

from config import load_config
from tools.base_tool import BaseTool, ToolFetchError

MOCK_MARKETS = [
    {
        "condition_id": "pm-fed-rate-jun26",
        "question": "Will the Federal Reserve cut interest rates by June 2026?",
        "outcome_prices": [0.58, 0.42],
        "volume": 350000,
        "end_date": "2026-06-30T20:00:00Z",
        "category": "economics",
        "active": True,
    },
    {
        "condition_id": "pm-btc-100k-apr26",
        "question": "Bitcoin above $100,000 on April 30?",
        "outcome_prices": [0.48, 0.52],
        "volume": 520000,
        "end_date": "2026-04-30T23:59:59Z",
        "category": "crypto",
        "active": True,
    },
    {
        "condition_id": "pm-scotus-retire-26",
        "question": "Supreme Court justice retirement in 2026?",
        "outcome_prices": [0.22, 0.78],
        "volume": 85000,
        "end_date": "2026-10-01T00:00:00Z",
        "category": "politics",
        "active": True,
    },
]


class PolymarketTool(BaseTool):
    GAMMA_URL = "https://gamma-api.polymarket.com"
    CLOB_URL = "https://clob.polymarket.com"

    @property
    def name(self) -> str:
        return "polymarket"

    def __init__(self, mock_mode: bool = False):
        self.mock_mode = mock_mode
        self.config = load_config()
        self._last_request = 0.0
        self._min_interval = 0.5

    def get_schema(self) -> dict:
        return {
            "name": "polymarket",
            "description": "Fetch prediction market data from Polymarket. Can also match against Kalshi contracts by title similarity.",
            "parameters": {
                "type": "object",
                "properties": {
                    "active": {"type": "boolean", "default": True},
                    "limit": {"type": "integer", "default": 100},
                    "match_title": {"type": "string", "description": "Title to fuzzy-match against"},
                },
            },
            "output": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "condition_id": {"type": "string"},
                        "question": {"type": "string"},
                        "yes_price": {"type": "number"},
                        "volume": {"type": "number"},
                        "end_date": {"type": "string"},
                    },
                },
            },
        }

    def _rate_limit(self):
        elapsed = time.time() - self._last_request
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request = time.time()

    def _fetch(self, active: bool = True, limit: int = 100, match_title: str = None) -> Any:
        if self.mock_mode:
            markets = MOCK_MARKETS
            if match_title:
                markets = [m for m in markets if self._title_similarity(match_title, m["question"]) > 0.4]
            return markets

        self._rate_limit()
        params = {"limit": limit, "active": str(active).lower()}
        try:
            resp = requests.get(f"{self.GAMMA_URL}/markets", params=params, timeout=10)
            resp.raise_for_status()
            markets = resp.json()
            if match_title:
                markets = [m for m in markets if self._title_similarity(match_title, m.get("question", "")) > 0.4]
            return markets
        except requests.RequestException as e:
            raise ToolFetchError(f"Polymarket API error: {e}")

    def check_auth(self) -> bool:
        """Validate Polymarket API key format.
        Polymarket Gamma API is public (no key needed for reads).
        CLOB API requires an API key for order placement, not for reads."""
        # Gamma API is public — no key required for market data reads.
        # CLOB API key is optional for read-only usage.
        return True

    def _parse(self, raw_data: Any) -> dict:
        markets = []
        for m in raw_data:
            if isinstance(m, dict):
                # Live API uses camelCase "outcomePrices"; mock uses snake_case
                has_prices = "outcome_prices" in m or "outcomePrices" in m
                if not has_prices:
                    logger.warning(f"polymarket: market missing price data: {m.get('question', '?')[:40]}")
            prices = m.get("outcome_prices", m.get("outcomePrices", [0, 0]))
            if isinstance(prices, list) and len(prices) >= 1:
                yes_price = float(prices[0])
            else:
                yes_price = 0.0
            markets.append({
                "condition_id": m.get("condition_id", m.get("conditionId", "")),
                "question": m.get("question", ""),
                "yes_price": yes_price,
                "volume": float(m.get("volume", 0)),
                "end_date": m.get("end_date", m.get("endDate", "")),
                "category": m.get("category", ""),
            })
        from datetime import datetime as dt
        return {"markets": markets, "source": "polymarket", "fetched_at": dt.utcnow().isoformat(), "confidence": "high" if markets else "low"}

    def health_check(self) -> dict:
        if self.mock_mode:
            return {"healthy": True, "latency_ms": 1.0, "error": None}
        start = time.time()
        try:
            resp = requests.get(f"{self.GAMMA_URL}/markets", params={"limit": 1}, timeout=5)
            latency = (time.time() - start) * 1000
            return {"healthy": resp.status_code == 200, "latency_ms": latency, "error": None}
        except Exception as e:
            return {"healthy": False, "latency_ms": 0, "error": str(e)}

    @staticmethod
    def _title_similarity(a: str, b: str) -> float:
        return SequenceMatcher(None, a.lower(), b.lower()).ratio()
