"""SEC EDGAR filings — 8-K, SC 13D/G, DEFM14A for corporate/legal contracts."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from tools.base_tool import BaseTool, ToolFetchError

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sec_filings.json"


class SecFilingsTool(BaseTool):
    EDGAR_URL = "https://efts.sec.gov/LATEST/search-index"
    EDGAR_FULL_TEXT = "https://efts.sec.gov/LATEST/search-index"

    def __init__(self, mock_mode: bool = False):
        self.mock_mode = mock_mode
        self._last_request = 0.0
        self._min_interval = 0.5  # SEC rate limit: 10 req/sec with User-Agent

    @property
    def name(self) -> str:
        return "sec_filings"

    def get_schema(self) -> dict:
        return {
            "name": "sec_filings",
            "description": "Search SEC EDGAR for recent filings (8-K material events, SC 13D/G activist stakes, DEFM14A merger proxies). Focus on corporate/legal contracts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Company name or event to search"},
                    "filing_types": {
                        "type": "array",
                        "items": {"type": "string"},
                        "default": ["8-K", "SC 13D", "SC 13G", "DEFM14A"],
                    },
                    "days_back": {"type": "integer", "default": 90},
                },
                "required": ["query"],
            },
        }

    def _rate_limit(self):
        elapsed = time.time() - self._last_request
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request = time.time()

    def _fetch(self, query: str = "", filing_types: list = None, days_back: int = 90, **kwargs) -> Any:
        if self.mock_mode:
            return json.loads(FIXTURE_PATH.read_text())

        if filing_types is None:
            filing_types = ["8-K", "SC 13D", "SC 13G", "DEFM14A"]

        start_date = (datetime.utcnow() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        self._rate_limit()

        # Use EDGAR full-text search
        params = {
            "q": query,
            "dateRange": "custom",
            "startdt": start_date,
            "forms": ",".join(filing_types),
        }
        headers = {"User-Agent": "PredictionAgent research@example.com"}

        for attempt in range(2):
            try:
                resp = requests.get(
                    "https://efts.sec.gov/LATEST/search-index",
                    params=params,
                    headers=headers,
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
                raise ToolFetchError(f"EDGAR API error: {e}")

    def _parse(self, raw_data: Any) -> dict:
        filings = []
        hits = raw_data.get("filings", raw_data.get("hits", {}).get("hits", []))
        if isinstance(hits, list):
            for f in hits[:10]:
                src = f.get("_source", f) if isinstance(f, dict) else f
                filings.append({
                    "filing_type": src.get("filing_type", src.get("form_type", "")),
                    "company": src.get("company", src.get("entity_name", "")),
                    "date_filed": src.get("date_filed", src.get("file_date", "")),
                    "description": src.get("description", ""),
                    "url": src.get("url", src.get("file_url", "")),
                    "key_excerpt": src.get("key_excerpt", "")[:500],
                })

        return {
            "filings": filings,
            "total_results": raw_data.get("total_results", len(filings)),
            "source": "sec_edgar",
            "fetched_at": datetime.utcnow().isoformat(),
            "confidence": "high" if filings else "low",
        }

    def health_check(self) -> dict:
        if self.mock_mode:
            return {"healthy": True, "latency_ms": 1.0, "error": None}
        start = time.time()
        try:
            resp = requests.get(
                "https://efts.sec.gov/LATEST/search-index",
                params={"q": "test", "forms": "8-K"},
                headers={"User-Agent": "PredictionAgent research@example.com"},
                timeout=5,
            )
            latency = (time.time() - start) * 1000
            return {"healthy": resp.status_code == 200, "latency_ms": latency, "error": None}
        except Exception as e:
            return {"healthy": False, "latency_ms": 0, "error": str(e)}
