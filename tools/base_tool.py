"""Abstract base class for all data source tools."""

from __future__ import annotations

import logging
import os
import time
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


class TokenBucketRateLimiter:
    """Simple token bucket rate limiter.

    Default: 10 requests/minute per tool.
    Configurable per-tool via RATE_LIMIT_<TOOLNAME>=N env var.
    On rate limit hit: sleep until bucket refills, log a warning.
    """

    def __init__(self, tool_name: str, default_rpm: int = 10):
        env_key = f"RATE_LIMIT_{tool_name.upper()}"
        self.rpm = int(os.environ.get(env_key, str(default_rpm)))
        self.interval = 60.0 / self.rpm if self.rpm > 0 else 0
        self.tokens = float(self.rpm)
        self.max_tokens = float(self.rpm)
        self.last_refill = time.monotonic()

    def acquire(self):
        """Block until a token is available."""
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.max_tokens, self.tokens + elapsed * (self.rpm / 60.0))
        self.last_refill = now

        if self.tokens < 1.0:
            wait_time = (1.0 - self.tokens) / (self.rpm / 60.0)
            logger.warning(f"Rate limit hit, sleeping {wait_time:.1f}s")
            time.sleep(wait_time)
            self.tokens = 1.0
            self.last_refill = time.monotonic()

        self.tokens -= 1.0


class BaseTool(ABC):
    """Every tool must implement these 4 methods plus an explicit `name` property."""

    @abstractmethod
    def get_schema(self) -> dict:
        """Return JSON schema describing this tool's input parameters and output format.
        Used by the deep-dive agent to know what tools are available and how to call them."""
        ...

    @abstractmethod
    def _fetch(self, **kwargs) -> Any:
        """Fetch raw data from the external source.
        Must handle rate limiting, retries, and timeouts internally.
        Should raise ToolFetchError on failure."""
        ...

    @abstractmethod
    def _parse(self, raw_data: Any) -> dict:
        """Parse raw API response into a standardized dict.
        The dict schema should match what get_schema() describes for output."""
        ...

    @abstractmethod
    def health_check(self) -> dict:
        """Ping the API and return {healthy: bool, latency_ms: float, error: str|None}.
        Should complete in <5 seconds."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Tool name. MUST match the filename stem (e.g. fed_data.py -> 'fed_data').
        Every subclass must override this explicitly."""
        ...

    def run(self, **kwargs) -> dict:
        """Public entry point: fetch + parse with retry on transient errors.
        Retries once on ConnectionError, Timeout, and 5xx responses."""
        import requests as _req
        retryable = (_req.exceptions.ConnectionError, _req.exceptions.Timeout,
                     _req.exceptions.ChunkedEncodingError)
        for attempt in range(2):
            try:
                raw = self._fetch(**kwargs)
                parsed = self._parse(raw)
                return {"success": True, "data": parsed}
            except retryable as e:
                if attempt == 0:
                    logger.warning(f"{self.name}: transient error, retrying: {e}")
                    time.sleep(2)
                    continue
                return {"success": False, "error": str(e), "tool": self.name}
            except Exception as e:
                return {"success": False, "error": str(e), "tool": self.name}
        return {"success": False, "error": "max retries exceeded", "tool": self.name}

    def validate_response(self, raw: dict, required_fields: list[str]) -> dict:
        """Check required fields exist in a raw API response.

        Logs a warning for each missing field.
        Raises ToolParseError if >50% of expected fields are missing.
        Returns the raw dict unchanged if validation passes.
        """
        if not isinstance(raw, dict):
            raise ToolParseError(
                f"{self.name}: expected dict, got {type(raw).__name__}",
                raw_response=raw,
            )
        missing = [f for f in required_fields if f not in raw]
        for f in missing:
            logger.warning(f"{self.name}: response missing field '{f}'")

        if len(missing) > len(required_fields) / 2:
            raise ToolParseError(
                f"{self.name}: >50% of expected fields missing ({len(missing)}/{len(required_fields)}): {missing}",
                raw_response=raw,
            )
        return raw


class ToolFetchError(Exception):
    """Raised when a tool fails to fetch data."""
    pass


class ToolParseError(Exception):
    """Raised when a tool fails to parse a response (schema drift)."""
    def __init__(self, message: str, raw_response: Any = None):
        super().__init__(message)
        self.raw_response = raw_response


class ToolNameMismatchError(Exception):
    """Raised when a tool's name property doesn't match its filename."""
    pass
