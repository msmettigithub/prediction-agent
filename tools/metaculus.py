"""Metaculus API — community prediction data for calibration signals."""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from tools.base_tool import BaseTool, ToolFetchError


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "metaculus.json"


class MetaculusTool(BaseTool):
    BASE_URL = "https://www.metaculus.com/api2/questions/"

    @property
    def name(self) -> str:
        return "metaculus"

    def __init__(self, mock_mode: bool = False):
        self.mock_mode = mock_mode
        self._last_request = 0.0
        self._min_interval = 2.0
        import os
        self._token = os.environ.get("METACULUS_API_TOKEN", "")

    def get_schema(self) -> dict:
        return {
            "name": "metaculus",
            "description": "Search Metaculus for community predictions on questions similar to a contract. Returns community median, forecaster count, and prediction history.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query (usually contract title)"},
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
        params = {"search": query, "limit": limit, "status": "open"}
        headers = {"User-Agent": "PredictionAgent/1.0"}
        if self._token:
            headers["Authorization"] = f"Token {self._token}"

        for attempt in range(2):
            try:
                resp = requests.get(self.BASE_URL, params=params, headers=headers, timeout=10)
                if resp.status_code == 403:
                    raise ToolFetchError("Metaculus API requires auth. Set METACULUS_API_TOKEN in .env")
                if resp.status_code >= 500 and attempt == 0:
                    time.sleep(2)
                    continue
                resp.raise_for_status()
                return resp.json()
            except ToolFetchError:
                raise
            except requests.RequestException as e:
                if attempt == 0:
                    time.sleep(2)
                    continue
                raise ToolFetchError(f"Metaculus API error: {e}")

    def _parse(self, raw_data: Any) -> dict:
        results = raw_data.get("results", raw_data) if isinstance(raw_data, dict) else raw_data
        parsed = []
        for q in (results if isinstance(results, list) else [results]):
            if isinstance(q, dict):
                self.validate_response(q, ["title", "community_prediction", "number_of_forecasters"])
            cp = q.get("community_prediction", {})
            full = cp.get("full", {})
            median = full.get("q2", None)

            history = []
            for h in cp.get("history", []):
                history.append({"timestamp": h.get("t"), "median": h.get("x1")})

            num_forecasters = q.get("number_of_forecasters", 0)
            if num_forecasters > 100:
                confidence = "high"
            elif num_forecasters >= 20:
                confidence = "medium"
            else:
                confidence = "low"

            parsed.append({
                "id": q.get("id"),
                "title": q.get("title", ""),
                "community_prediction": median,
                "resolution_criteria": q.get("resolution_criteria", ""),
                "close_time": q.get("close_time", ""),
                "num_forecasters": num_forecasters,
                "history": history[-10:],  # last 10 data points
                "confidence": confidence,
                "source": "metaculus",
                "fetched_at": datetime.utcnow().isoformat(),
            })
        return {"questions": parsed, "source": "metaculus", "fetched_at": datetime.utcnow().isoformat(), "confidence": parsed[0]["confidence"] if parsed else "low"}

    def health_check(self) -> dict:
        if self.mock_mode:
            return {"healthy": True, "latency_ms": 1.0, "error": None}
        if not self._token:
            return {"healthy": False, "latency_ms": 0, "error": "METACULUS_API_TOKEN not set (API requires auth)"}
        start = time.time()
        try:
            headers = {"Authorization": f"Token {self._token}", "User-Agent": "PredictionAgent/1.0"}
            resp = requests.get(self.BASE_URL, params={"limit": 1}, headers=headers, timeout=5)
            latency = (time.time() - start) * 1000
            return {"healthy": resp.status_code == 200, "latency_ms": latency,
                    "error": None if resp.status_code == 200 else f"HTTP {resp.status_code}"}
        except Exception as e:
            return {"healthy": False, "latency_ms": 0, "error": str(e)}
