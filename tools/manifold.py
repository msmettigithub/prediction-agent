"""Manifold Markets API — secondary prediction market calibration signal."""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from tools.base_tool import BaseTool, ToolFetchError

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "manifold.json"


class ManifoldTool(BaseTool):
    BASE_URL = "https://api.manifold.markets/v0"

    @property
    def name(self) -> str:
        return "manifold"

    def __init__(self, mock_mode: bool = False):
        self.mock_mode = mock_mode
        self._last_request = 0.0
        self._min_interval = 0.5

    def get_schema(self) -> dict:
        return {
            "name": "manifold",
            "description": "Search Manifold Markets for prediction market data. Secondary signal alongside Kalshi/Polymarket.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "limit": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
        }

    def _rate_limit(self):
        elapsed = time.time() - self._last_request
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request = time.time()

    def _fetch(self, query: str = "", limit: int = 5, **kwargs) -> Any:
        if self.mock_mode:
            return json.loads(FIXTURE_PATH.read_text())

        self._rate_limit()
        for attempt in range(2):
            try:
                resp = requests.get(
                    f"{self.BASE_URL}/search-markets",
                    params={"term": query, "limit": limit},
                    timeout=10,
                )
                if resp.status_code >= 500 and attempt == 0:
                    time.sleep(2)
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as e:
                if attempt == 0:
                    time.sleep(2)
                    continue
                raise ToolFetchError(f"Manifold API error: {e}")

    def _parse(self, raw_data: Any) -> dict:
        markets = []
        items = raw_data if isinstance(raw_data, list) else raw_data.get("markets", [])
        for m in items:
            close_ts = m.get("closeTime")
            close_time = datetime.fromtimestamp(close_ts / 1000).isoformat() if close_ts else None

            markets.append({
                "id": m.get("id", ""),
                "question": m.get("question", ""),
                "probability": m.get("probability", 0),
                "volume": m.get("volume", 0),
                "num_traders": m.get("totalTraders", m.get("uniqueBettorCount", 0)),
                "close_time": close_time,
                "is_resolved": m.get("isResolved", False),
                "resolution": m.get("resolution"),
                "url": m.get("url", ""),
            })

        return {
            "markets": markets,
            "source": "manifold",
            "fetched_at": datetime.utcnow().isoformat(),
            "confidence": "medium" if markets else "low",
        }

    def health_check(self) -> dict:
        if self.mock_mode:
            return {"healthy": True, "latency_ms": 1.0, "error": None}
        start = time.time()
        try:
            resp = requests.get(f"{self.BASE_URL}/search-markets", params={"term": "test", "limit": 1}, timeout=5)
            latency = (time.time() - start) * 1000
            return {"healthy": resp.status_code == 200, "latency_ms": latency, "error": None}
        except Exception as e:
            return {"healthy": False, "latency_ms": 0, "error": str(e)}
