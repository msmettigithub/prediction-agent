"""News search — Tavily API (primary) with Brave Search fallback."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from config import load_config
from tools.base_tool import BaseTool, ToolFetchError

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "search_news.json"


class SearchNewsTool(BaseTool):
    TAVILY_URL = "https://api.tavily.com/search"
    BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, mock_mode: bool = False):
        self.mock_mode = mock_mode
        self.config = load_config()
        self._last_request = 0.0
        self._min_interval = 1.0

    @property
    def name(self) -> str:
        return "search_news"

    def get_schema(self) -> dict:
        return {
            "name": "search_news",
            "description": "Search recent news articles relevant to a contract. Uses Tavily (primary) or Brave Search (fallback). Returns top 5 results weighted by recency.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query (contract title or keywords)"},
                    "max_results": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
        }

    def _rate_limit(self):
        elapsed = time.time() - self._last_request
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request = time.time()

    def _fetch(self, query: str = "", max_results: int = 5, **kwargs) -> Any:
        if self.mock_mode:
            return json.loads(FIXTURE_PATH.read_text())

        # Try Tavily first
        if self.config.tavily_api_key:
            self._rate_limit()
            for attempt in range(2):
                try:
                    resp = requests.post(
                        self.TAVILY_URL,
                        json={
                            "api_key": self.config.tavily_api_key,
                            "query": query,
                            "max_results": max_results,
                            "search_depth": "advanced",
                            "include_answer": False,
                        },
                        timeout=10,
                    )
                    if resp.status_code >= 500 and attempt == 0:
                        time.sleep(2)
                        continue
                    resp.raise_for_status()
                    data = resp.json()
                    return {"results": data.get("results", []), "query": query, "search_engine": "tavily"}
                except requests.RequestException:
                    if attempt == 0:
                        time.sleep(2)
                        continue

        # Fallback to Brave
        if self.config.brave_search_api_key:
            self._rate_limit()
            for attempt in range(2):
                try:
                    resp = requests.get(
                        self.BRAVE_URL,
                        params={"q": query, "count": max_results},
                        headers={"X-Subscription-Token": self.config.brave_search_api_key},
                        timeout=10,
                    )
                    if resp.status_code >= 500 and attempt == 0:
                        time.sleep(2)
                        continue
                    resp.raise_for_status()
                    data = resp.json()
                    results = []
                    for r in data.get("web", {}).get("results", []):
                        results.append({
                            "title": r.get("title", ""),
                            "snippet": r.get("description", ""),
                            "url": r.get("url", ""),
                            "published_date": r.get("age", ""),
                        })
                    return {"results": results, "query": query, "search_engine": "brave"}
                except requests.RequestException:
                    if attempt == 0:
                        time.sleep(2)
                        continue

        raise ToolFetchError("No search API key configured (TAVILY_API_KEY or BRAVE_SEARCH_API_KEY)")

    def check_auth(self) -> bool:
        """Validate search API key format.
        Tavily: key is passed in JSON body as 'api_key', not in headers.
        Brave: key goes in X-Subscription-Token header."""
        if self.config.tavily_api_key:
            key = self.config.tavily_api_key
            if len(key) < 10:
                raise ToolFetchError(
                    f"Tavily API key appears malformed (len={len(key)}). "
                    "Expected a tvly-* prefixed key."
                )
            return True
        if self.config.brave_search_api_key:
            key = self.config.brave_search_api_key
            if len(key) < 10:
                raise ToolFetchError(
                    f"Brave Search API key appears malformed (len={len(key)}). "
                    "Expected a BSA* prefixed key."
                )
            return True
        raise ToolFetchError("No search API key configured (TAVILY_API_KEY or BRAVE_SEARCH_API_KEY)")

    def _parse(self, raw_data: Any) -> dict:
        cutoff = datetime.utcnow() - timedelta(days=30)
        results = []
        for r in raw_data.get("results", [])[:5]:
            pub_date = r.get("published_date", r.get("publishedDate", ""))
            relevance = float(r.get("relevance_score", r.get("score", 0.5)))

            # Halve relevance for articles >30 days old
            if pub_date:
                try:
                    parsed = datetime.fromisoformat(pub_date.replace("Z", "+00:00")).replace(tzinfo=None)
                    if parsed < cutoff:
                        relevance *= 0.5
                except (ValueError, AttributeError):
                    pass

            results.append({
                "title": r.get("title", ""),
                "snippet": r.get("snippet", r.get("content", ""))[:300],
                "url": r.get("url", ""),
                "published_date": pub_date,
                "relevance_score": relevance,
                "source": r.get("source", ""),
            })

        results.sort(key=lambda x: x["relevance_score"], reverse=True)
        return {
            "results": results,
            "query": raw_data.get("query", ""),
            "search_engine": raw_data.get("search_engine", "unknown"),
            "source": "search_news",
            "fetched_at": datetime.utcnow().isoformat(),
            "confidence": "high" if len(results) >= 3 else ("medium" if results else "low"),
        }

    def health_check(self) -> dict:
        if self.mock_mode:
            return {"healthy": True, "latency_ms": 1.0, "error": None}
        start = time.time()
        try:
            if self.config.tavily_api_key:
                resp = requests.post(
                    self.TAVILY_URL,
                    json={"api_key": self.config.tavily_api_key, "query": "test", "max_results": 1},
                    timeout=5,
                )
                latency = (time.time() - start) * 1000
                return {"healthy": resp.status_code == 200, "latency_ms": latency, "error": None}
            elif self.config.brave_search_api_key:
                resp = requests.get(
                    self.BRAVE_URL,
                    params={"q": "test", "count": 1},
                    headers={"X-Subscription-Token": self.config.brave_search_api_key},
                    timeout=5,
                )
                latency = (time.time() - start) * 1000
                return {"healthy": resp.status_code == 200, "latency_ms": latency, "error": None}
            return {"healthy": False, "latency_ms": 0, "error": "No search API key configured"}
        except Exception as e:
            return {"healthy": False, "latency_ms": 0, "error": str(e)}
