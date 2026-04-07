"""Polling data — RealClearPolitics averages + Wikipedia historical reference."""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from tools.base_tool import BaseTool, ToolFetchError

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "polling_data.json"


class PollingDataTool(BaseTool):
    RCP_URL = "https://www.realclearpolitics.com/epolls/json"
    WIKI_API = "https://en.wikipedia.org/w/api.php"

    @property
    def name(self) -> str:
        return "polling_data"

    def __init__(self, mock_mode: bool = False):
        self.mock_mode = mock_mode
        self._last_request = 0.0
        self._min_interval = 1.0

    def get_schema(self) -> dict:
        return {
            "name": "polling_data",
            "description": "Fetch polling averages for political contracts. Uses RealClearPolitics for US races and Wikipedia for historical reference.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Political race or event to search for"},
                    "include_historical": {"type": "boolean", "default": True},
                },
                "required": ["query"],
            },
        }

    def _rate_limit(self):
        elapsed = time.time() - self._last_request
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request = time.time()

    def _fetch(self, query: str = "", include_historical: bool = True, **kwargs) -> Any:
        if self.mock_mode:
            return json.loads(FIXTURE_PATH.read_text())

        result = {"polls": [], "historical_reference": None}

        # Try RCP
        self._rate_limit()
        for attempt in range(2):
            try:
                resp = requests.get(
                    f"https://www.realclearpolitics.com/epolls/json/polls.json",
                    timeout=10,
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                if resp.status_code >= 500 and attempt == 0:
                    time.sleep(2)
                    continue
                if resp.status_code == 200:
                    result["polls"] = resp.json()
                break
            except requests.RequestException:
                if attempt == 0:
                    time.sleep(2)
                    continue

        # Wikipedia historical reference
        if include_historical and query:
            self._rate_limit()
            try:
                params = {
                    "action": "query",
                    "list": "search",
                    "srsearch": query,
                    "format": "json",
                    "srlimit": 5,
                }
                resp = requests.get(self.WIKI_API, params=params, headers=self._HEADERS, timeout=10)
                if resp.status_code == 200:
                    search_results = resp.json().get("query", {}).get("search", [])
                    result["historical_reference"] = {
                        "query": query,
                        "wiki_results": [
                            {"title": r["title"], "snippet": r.get("snippet", "")}
                            for r in search_results[:3]
                        ],
                    }
            except requests.RequestException:
                pass

        return result

    def _parse(self, raw_data: Any) -> dict:
        polls = []
        for p in raw_data.get("polls", []):
            if isinstance(p, dict):
                candidates = []
                for c in p.get("candidates", []):
                    candidates.append({
                        "name": c.get("name", ""),
                        "poll_average": c.get("poll_average", 0),
                        "trend_7d": c.get("trend_7d", 0),
                    })
                polls.append({
                    "race": p.get("race", ""),
                    "candidates": candidates,
                    "lead_margin": p.get("lead_margin", 0),
                    "sample_size_weighted": p.get("sample_size_weighted", 0),
                    "last_updated": p.get("last_updated", ""),
                })

        hist = raw_data.get("historical_reference")

        return {
            "polls": polls,
            "historical_reference": hist,
            "source": "rcp_wikipedia",
            "fetched_at": datetime.utcnow().isoformat(),
            "confidence": "high" if polls else ("medium" if hist else "low"),
        }

    _HEADERS = {"User-Agent": "PredictionAgent/1.0 (research@example.com)"}

    def health_check(self) -> dict:
        if self.mock_mode:
            return {"healthy": True, "latency_ms": 1.0, "error": None}
        start = time.time()
        try:
            resp = requests.get(
                self.WIKI_API,
                params={"action": "query", "meta": "siteinfo", "format": "json"},
                headers=self._HEADERS,
                timeout=5,
            )
            latency = (time.time() - start) * 1000
            return {"healthy": resp.status_code == 200, "latency_ms": latency, "error": None}
        except Exception as e:
            return {"healthy": False, "latency_ms": 0, "error": str(e)}
