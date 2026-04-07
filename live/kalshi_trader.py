"""Kalshi order placement client.

Wraps the Kalshi v2 trade API for placing market orders. Handles:
- Auth via RSA-PSS signed headers (KALSHI-ACCESS-KEY/TIMESTAMP/SIGNATURE)
- Order payload formatting (cents, not dollars)
- Balance fetching
- Position lookup for resolution

NEVER places orders unless explicitly called. The CLI flow ensures
user confirmation before any call to place_order().
"""

from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import urlparse

import requests

from config import Config
from live.kalshi_signer import KalshiSigner

logger = logging.getLogger(__name__)


class KalshiTrader:
    """Live order placement on Kalshi. Use only after guard.check_all() passes."""

    TRADING_URL = "https://api.elections.kalshi.com/trade-api/v2"

    def __init__(self, config: Config, signer: Optional[KalshiSigner] = None):
        self.config = config
        self.api_key = config.kalshi_api_key
        # Allow signer injection for tests; otherwise lazy-load on first auth call
        self._signer = signer
        self._signer_error: Optional[Exception] = None

    def _ensure_signer(self) -> KalshiSigner:
        """Lazy-load the signer. If loading failed previously, re-raise the
        original error so the caller always sees the root cause — not a
        generic 'not available' fallback.
        """
        if self._signer is not None:
            return self._signer
        if self._signer_error is not None:
            # Re-raise the cached original error so the real cause is visible
            raise RuntimeError(
                f"Kalshi signer failed to load: {self._signer_error}"
            ) from self._signer_error

        # First load attempt — show diagnostics
        import os as _os
        raw_path = self.config.kalshi_private_key_path
        expanded = _os.path.expanduser(raw_path) if raw_path else ""
        logger.info(f"Loading Kalshi signer: path={raw_path!r} expanded={expanded!r}")
        if raw_path:
            exists = _os.path.exists(expanded)
            readable = _os.access(expanded, _os.R_OK) if exists else False
            logger.info(f"Kalshi key file: exists={exists} readable={readable}")

        try:
            self._signer = KalshiSigner(
                private_key_path=raw_path,
                api_key=self.api_key,
            )
            return self._signer
        except Exception as e:
            self._signer_error = e
            logger.error(f"KalshiSigner construction failed: {type(e).__name__}: {e}")
            raise

    def _signed_headers(self, method: str, full_url: str) -> dict:
        """Return KALSHI-* headers for the given request."""
        signer = self._ensure_signer()
        # Sign only the path portion of the URL (no host, no query string)
        path = urlparse(full_url).path
        headers = signer.headers_for(method, path)
        headers["Content-Type"] = "application/json"
        return headers

    def get_balance(self) -> float:
        """Fetch current portfolio balance in dollars (converted from cents)."""
        url = f"{self.TRADING_URL}/portfolio/balance"
        try:
            resp = requests.get(
                url,
                headers=self._signed_headers("GET", url),
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            # Kalshi returns balance in cents
            return float(data.get("balance", 0)) / 100.0
        except Exception as e:
            logger.error(f"Failed to fetch Kalshi balance: {e}")
            raise

    def place_order(self, ticker: str, side: str, shares: int, price_cents: int) -> dict:
        """Place a limit order on Kalshi.

        Args:
            ticker: Kalshi market ticker (e.g., 'KXFEDRATE-26MAY-T5.00')
            side: 'yes' or 'no' (lowercase per Kalshi spec)
            shares: number of contracts
            price_cents: limit price in cents (1-99)

        Returns:
            dict with order details (including order_id)

        Raises:
            RuntimeError on failure
        """
        if shares <= 0:
            raise RuntimeError(f"Invalid share count: {shares}")
        if not (0 < price_cents < 100):
            raise RuntimeError(f"Invalid price: {price_cents} cents (must be 1-99)")
        if side not in ("yes", "no"):
            raise RuntimeError(f"Invalid side: {side} (must be 'yes' or 'no')")

        # Kalshi v2 order payload — prices in cents, not dollars
        payload = {
            "ticker": ticker,
            "side": side,
            "action": "buy",
            "type": "limit",
            "count": shares,
            "yes_price": price_cents if side == "yes" else None,
            "no_price": price_cents if side == "no" else None,
            "client_order_id": f"pa-{ticker}-{shares}-{price_cents}",
        }
        payload = {k: v for k, v in payload.items() if v is not None}

        logger.info(f"PLACING LIVE ORDER: {payload}")

        url = f"{self.TRADING_URL}/portfolio/orders"
        try:
            resp = requests.post(
                url,
                headers=self._signed_headers("POST", url),
                json=payload,
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            status_code = getattr(getattr(e, "response", None), "status_code", "?")
            body = getattr(getattr(e, "response", None), "text", "")[:500]
            raise RuntimeError(
                f"Kalshi order placement failed (HTTP {status_code}): {e}\nBody: {body}"
            )

    def get_position(self, ticker: str) -> Optional[dict]:
        """Fetch current position on a ticker. Returns None if no position."""
        url = f"{self.TRADING_URL}/portfolio/positions"
        try:
            resp = requests.get(
                url,
                headers=self._signed_headers("GET", url),
                params={"ticker": ticker},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            positions = data.get("market_positions", []) or data.get("positions", [])
            for p in positions:
                if p.get("ticker") == ticker:
                    return p
            return None
        except Exception as e:
            logger.warning(f"Failed to fetch position for {ticker}: {e}")
            return None

    def get_market_status(self, ticker: str) -> Optional[dict]:
        """Fetch a single market's current state, including resolution if settled.
        Markets endpoint is public — no auth needed."""
        try:
            resp = requests.get(
                f"{self.TRADING_URL}/markets/{ticker}",
                timeout=10,
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json().get("market", {})
        except Exception as e:
            logger.warning(f"Failed to fetch market {ticker}: {e}")
            return None
