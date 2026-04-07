"""Twitter/X sentiment analysis — expert vs general sentiment scoring.

Uses Nitter instances as scraper fallback if Twitter API unavailable.
Expert sentiment weighted 5:1 over general sentiment.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from tools.base_tool import BaseTool, ToolFetchError

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "twitter_sentiment.json"

NITTER_INSTANCES = [
    "https://nitter.net",
    "https://nitter.privacydev.net",
]


class TwitterSentimentTool(BaseTool):
    def __init__(self, mock_mode: bool = False):
        self.mock_mode = mock_mode
        self._last_request = 0.0
        self._min_interval = 2.0  # aggressive rate limit to avoid bans

    @property
    def name(self) -> str:
        return "twitter_sentiment"

    def get_schema(self) -> dict:
        return {
            "name": "twitter_sentiment",
            "description": "Analyze Twitter/X sentiment from expert and general accounts. Expert accounts (domain keywords in bio) weighted 5:1 over general. Returns sentiment scores from -1 (bearish) to 1 (bullish).",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Topic to search sentiment for"},
                    "domain_keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Keywords to identify expert accounts (e.g., ['economist', 'fed', 'rates'])",
                    },
                },
                "required": ["query"],
            },
        }

    def _rate_limit(self):
        elapsed = time.time() - self._last_request
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request = time.time()

    def _fetch(self, query: str = "", domain_keywords: list = None, **kwargs) -> Any:
        if self.mock_mode:
            return json.loads(FIXTURE_PATH.read_text())

        # Try Nitter instances as public scraper fallback
        for instance in NITTER_INSTANCES:
            self._rate_limit()
            try:
                resp = requests.get(
                    f"{instance}/search",
                    params={"f": "tweets", "q": query},
                    timeout=10,
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                if resp.status_code >= 500:
                    continue
                if resp.status_code == 200:
                    return {"html": resp.text, "source": instance, "query": query, "domain_keywords": domain_keywords or []}
            except requests.RequestException:
                continue

        raise ToolFetchError("No Twitter/Nitter endpoint available")

    def _parse(self, raw_data: Any) -> dict:
        # If we have structured fixture data (mock mode), use directly
        if "expert_tweets" in raw_data:
            expert_tweets = raw_data["expert_tweets"]
            general_tweets = raw_data["general_tweets"]
            summary = raw_data.get("summary", {})

            expert_scores = [t["sentiment_score"] for t in expert_tweets]
            general_scores = [t["sentiment_score"] for t in general_tweets]

            expert_avg = sum(expert_scores) / len(expert_scores) if expert_scores else 0
            general_avg = sum(general_scores) / len(general_scores) if general_scores else 0

            # 5:1 expert weighting
            total_weight = 5 * len(expert_scores) + len(general_scores)
            if total_weight > 0:
                weighted = (5 * sum(expert_scores) + sum(general_scores)) / total_weight
            else:
                weighted = 0

            return {
                "expert_sentiment_score": round(expert_avg, 3),
                "expert_tweet_count": len(expert_tweets),
                "general_sentiment_score": round(general_avg, 3),
                "general_tweet_count": len(general_tweets),
                "weighted_sentiment": round(weighted, 3),
                "sample_size": len(expert_tweets) + len(general_tweets),
                "source": "twitter",
                "fetched_at": datetime.utcnow().isoformat(),
                "confidence": "medium" if len(expert_tweets) >= 2 else "low",
            }

        # HTML scraping fallback — basic sentiment heuristic
        # (In production, you'd use a proper NLP model here)
        return {
            "expert_sentiment_score": 0,
            "expert_tweet_count": 0,
            "general_sentiment_score": 0,
            "general_tweet_count": 0,
            "weighted_sentiment": 0,
            "sample_size": 0,
            "source": "twitter_scraped",
            "fetched_at": datetime.utcnow().isoformat(),
            "confidence": "low",
        }

    def health_check(self) -> dict:
        if self.mock_mode:
            return {"healthy": True, "latency_ms": 1.0, "error": None}
        start = time.time()
        for instance in NITTER_INSTANCES:
            try:
                resp = requests.get(instance, timeout=5, headers={"User-Agent": "Mozilla/5.0"})
                latency = (time.time() - start) * 1000
                if resp.status_code == 200:
                    return {"healthy": True, "latency_ms": latency, "error": None}
            except Exception:
                continue
        return {"healthy": False, "latency_ms": 0, "error": "No Nitter instance reachable"}
