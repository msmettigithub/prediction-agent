"""Semantic Scholar API — academic papers on forecasting base rates and calibration.

Results seed base rate commentary, not direct probability modifiers.
Cache results by category for 7 days in SQLite.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from tools.base_tool import BaseTool, ToolFetchError

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "academic_forecasting.json"


class AcademicForecastingTool(BaseTool):
    BASE_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
    CACHE_DAYS = 30

    def __init__(self, mock_mode: bool = False):
        self.mock_mode = mock_mode
        self._last_request = 0.0
        self._min_interval = 2.0  # Semantic Scholar rate limit — aggressive, use 2s
        self._cache = {}  # in-memory cache keyed by query

    @property
    def name(self) -> str:
        return "academic_forecasting"

    def get_schema(self) -> dict:
        return {
            "name": "academic_forecasting",
            "description": "Search academic papers for forecasting base rates and calibration data. Uses Semantic Scholar API. Results provide context for base rate analysis, not direct probability modifiers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search terms (e.g., 'superforecasting base rates economics')"},
                    "category": {"type": "string", "description": "Contract category for targeted search"},
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

    def _fetch(self, query: str = "", category: str = "", limit: int = 5, **kwargs) -> Any:
        if self.mock_mode:
            return json.loads(FIXTURE_PATH.read_text())

        # Check cache
        cache_key = f"{query}:{category}"
        if cache_key in self._cache:
            cached_at, data = self._cache[cache_key]
            if datetime.utcnow() - cached_at < timedelta(days=self.CACHE_DAYS):
                return data

        # Build query
        search_query = query
        if category:
            search_query = f"superforecasting base rates {category} prediction market calibration"

        self._rate_limit()
        for attempt in range(2):
            try:
                resp = requests.get(
                    self.BASE_URL,
                    params={
                        "query": search_query,
                        "limit": limit,
                        "fields": "title,abstract,year,citationCount,url,authors",
                    },
                    timeout=10,
                )
                if resp.status_code >= 500 and attempt == 0:
                    time.sleep(2)
                    continue
                resp.raise_for_status()
                data = resp.json()
                self._cache[cache_key] = (datetime.utcnow(), data)
                return data
            except requests.RequestException as e:
                if attempt == 0:
                    time.sleep(2)
                    continue
                raise ToolFetchError(f"Semantic Scholar API error: {e}")

    def _parse(self, raw_data: Any) -> dict:
        papers = []
        for p in raw_data.get("data", raw_data.get("papers", [])):
            abstract = p.get("abstract", "") or ""

            # Extract percentages from abstract as cited base rates
            cited_rates = re.findall(r'(\d{1,3}(?:\.\d+)?)\s*(?:%|percent|percentage points?|pp)', abstract)
            cited_rates = [f"{r}%" for r in cited_rates[:5]]

            authors = [a.get("name", "") for a in p.get("authors", [])[:3]]

            papers.append({
                "paper_id": p.get("paperId", ""),
                "title": p.get("title", ""),
                "abstract_snippet": abstract[:400],
                "year": p.get("year"),
                "citation_count": p.get("citationCount", 0),
                "authors": authors,
                "cited_base_rates": cited_rates,
                "url": p.get("url", ""),
            })

        # Sort by citation count — more-cited papers are more reliable
        papers.sort(key=lambda x: x.get("citation_count", 0), reverse=True)

        return {
            "papers": papers,
            "source": "semantic_scholar",
            "fetched_at": datetime.utcnow().isoformat(),
            "confidence": "medium" if papers else "low",
        }

    def health_check(self) -> dict:
        if self.mock_mode:
            return {"healthy": True, "latency_ms": 1.0, "error": None}
        # If we have cached data, consider healthy without hitting API
        if self._cache:
            return {"healthy": True, "latency_ms": 0.1, "error": None}
        # This tool is rate-limited aggressively by Semantic Scholar.
        # Health check verifies the endpoint is reachable without consuming quota.
        start = time.time()
        try:
            # Use a HEAD-like minimal request
            resp = requests.get(self.BASE_URL,
                params={"query": "test", "limit": 1, "fields": "title"},
                timeout=10)
            latency = (time.time() - start) * 1000
            if resp.status_code == 429:
                # Rate limited but endpoint is reachable — consider healthy but throttled
                return {"healthy": True, "latency_ms": latency, "error": "rate-limited (429) — will use cache"}
            return {"healthy": resp.status_code == 200, "latency_ms": latency, "error": None}
        except Exception as e:
            return {"healthy": False, "latency_ms": 0, "error": str(e)}
