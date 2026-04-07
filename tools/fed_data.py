"""FRED API + CME FedWatch — economic data for rate/macro contracts."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from config import load_config
from tools.base_tool import BaseTool, ToolFetchError

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "fed_data.json"

# The 8 most relevant FRED series for prediction market contracts
SERIES_IDS = ["FEDFUNDS", "CPIAUCSL", "UNRATE", "GDP", "T10YIE", "DFF", "SOFR", "PCE"]


class FedDataTool(BaseTool):
    FRED_URL = "https://api.stlouisfed.org/fred/series/observations"

    @property
    def name(self) -> str:
        return "fed_data"

    def __init__(self, mock_mode: bool = False):
        self.mock_mode = mock_mode
        self.config = load_config()
        self._last_request = 0.0
        self._min_interval = 0.5

    def get_schema(self) -> dict:
        return {
            "name": "fed_data",
            "description": "Fetch Federal Reserve economic data (FRED) and CME FedWatch implied probabilities. Covers: FEDFUNDS, CPI, unemployment, GDP, breakeven inflation, daily fed funds, SOFR, PCE.",
            "parameters": {
                "type": "object",
                "properties": {
                    "series": {
                        "type": "array",
                        "items": {"type": "string", "enum": SERIES_IDS},
                        "description": "FRED series to fetch. Defaults to all 8.",
                    },
                    "include_fedwatch": {"type": "boolean", "default": True},
                },
            },
        }

    def _rate_limit(self):
        elapsed = time.time() - self._last_request
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request = time.time()

    def _fetch(self, series: list = None, include_fedwatch: bool = True, **kwargs) -> Any:
        if self.mock_mode:
            return json.loads(FIXTURE_PATH.read_text())

        if series is None:
            series = SERIES_IDS

        api_key = self.config.fred_api_key
        if not api_key:
            raise ToolFetchError("FRED_API_KEY not configured")

        result = {"fred_series": {}, "fedwatch": None}

        for sid in series:
            self._rate_limit()
            params = {
                "series_id": sid,
                "api_key": api_key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": 2,
            }
            for attempt in range(2):
                try:
                    resp = requests.get(self.FRED_URL, params=params, timeout=10)
                    if resp.status_code >= 500 and attempt == 0:
                        time.sleep(2)
                        continue
                    resp.raise_for_status()
                    obs = resp.json().get("observations", [])
                    if obs:
                        latest = obs[0]
                        prev = obs[1] if len(obs) > 1 else obs[0]
                        result["fred_series"][sid] = {
                            "value": float(latest["value"]) if latest["value"] != "." else None,
                            "date": latest["date"],
                            "prev_value": float(prev["value"]) if prev["value"] != "." else None,
                        }
                    break
                except requests.RequestException as e:
                    if attempt == 0:
                        time.sleep(2)
                        continue
                    raise ToolFetchError(f"FRED API error for {sid}: {e}")

        if include_fedwatch:
            result["fedwatch"] = self._fetch_fedwatch()

        return result

    def _fetch_fedwatch(self) -> dict:
        """Attempt to get CME FedWatch data. Falls back to None on failure."""
        try:
            self._rate_limit()
            resp = requests.get(
                "https://www.cmegroup.com/services/fed-funds-probabilities/",
                timeout=10,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            if resp.status_code == 200:
                data = resp.json()
                return {"raw": data, "source": "cme_fedwatch"}
        except Exception:
            pass
        return None

    def check_auth(self) -> bool:
        """Validate FRED API key format. FRED keys are 32-character hex strings.
        Key goes in ?api_key= query parameter, NOT in headers."""
        key = self.config.fred_api_key
        if not key:
            raise ToolFetchError("FRED API key not configured. Set FRED_API_KEY in .env")
        if len(key) != 32:
            raise ToolFetchError(
                f"FRED API key appears malformed (len={len(key)}, expected 32). "
                "Get a free key at https://fred.stlouisfed.org/docs/api/api_key.html"
            )
        return True

    def _parse(self, raw_data: Any) -> dict:
        if isinstance(raw_data, dict):
            self.validate_response(raw_data, ["fred_series"])
        series = {}
        for sid, vals in raw_data.get("fred_series", {}).items():
            value = vals.get("value")
            prev = vals.get("prev_value")
            if value is not None and prev is not None:
                trend = "rising" if value > prev else "falling" if value < prev else "flat"
            else:
                trend = "unknown"
            series[sid] = {
                "current_value": value,
                "prev_value": prev,
                "date": vals.get("date", ""),
                "trend_direction": trend,
            }

        fedwatch = raw_data.get("fedwatch")
        fw_parsed = None
        if fedwatch and isinstance(fedwatch, dict):
            probs = fedwatch.get("probabilities", {})
            fw_parsed = {
                "next_meeting": fedwatch.get("next_meeting", ""),
                "hold_prob": probs.get("hold"),
                "cut_25bp_prob": probs.get("cut_25bp"),
                "cut_50bp_prob": probs.get("cut_50bp"),
                "hike_25bp_prob": probs.get("hike_25bp"),
                "implied_rate_eoy": fedwatch.get("implied_rate_eoy"),
            }

        return {
            "series": series,
            "fedwatch": fw_parsed,
            "source": "fred",
            "fetched_at": datetime.utcnow().isoformat(),
            "confidence": "high" if len(series) >= 4 else "medium",
        }

    def health_check(self) -> dict:
        if self.mock_mode:
            return {"healthy": True, "latency_ms": 1.0, "error": None}
        start = time.time()
        try:
            api_key = self.config.fred_api_key
            if not api_key:
                return {"healthy": False, "latency_ms": 0, "error": "FRED_API_KEY not set"}
            resp = requests.get(
                self.FRED_URL,
                params={"series_id": "DFF", "api_key": api_key, "file_type": "json", "limit": 1},
                timeout=5,
            )
            latency = (time.time() - start) * 1000
            return {"healthy": resp.status_code == 200, "latency_ms": latency, "error": None}
        except Exception as e:
            return {"healthy": False, "latency_ms": 0, "error": str(e)}
