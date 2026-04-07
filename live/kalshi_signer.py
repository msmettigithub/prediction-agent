"""Kalshi v2 RSA-PSS request signing.

Kalshi authenticates trading endpoints with three headers:
  KALSHI-ACCESS-KEY        — your API key UUID
  KALSHI-ACCESS-TIMESTAMP  — milliseconds since Unix epoch
  KALSHI-ACCESS-SIGNATURE  — base64(RSA-PSS-sign(timestamp_ms + METHOD + path))

The signing message is the concatenation of:
  - timestamp_ms (string of integer milliseconds)
  - HTTP method (uppercase, e.g. "GET", "POST")
  - request path (path only, no host, no query string)

Signature uses RSA-PSS with SHA-256 and MGF1 with maximum salt length.

Setup:
1. Generate an RSA keypair in the Kalshi web dashboard
2. Download the private key as a .pem file
3. Set KALSHI_PRIVATE_KEY_PATH=/path/to/key.pem in .env
4. Set KALSHI_API_KEY to the access key UUID shown in the dashboard

This module never logs or transmits the private key. Only the resulting
signature header is sent.
"""

from __future__ import annotations

import base64
import logging
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class KalshiSigner:
    """Loads an RSA private key and produces signed request headers.

    The key is loaded once at construction and held in memory. If the
    `cryptography` package isn't installed or the key file is missing,
    construction raises a clear error rather than failing at request time.
    """

    def __init__(self, private_key_path: str, api_key: str):
        if not private_key_path:
            raise RuntimeError(
                "KALSHI_PRIVATE_KEY_PATH not set. "
                "Generate an RSA keypair in your Kalshi dashboard and set the path in .env."
            )
        if not api_key:
            raise RuntimeError("KALSHI_API_KEY not set.")

        try:
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import padding, rsa
        except ImportError as e:
            raise RuntimeError(
                "cryptography package not installed. "
                "Run: pip install cryptography"
            ) from e

        self._hashes = hashes
        self._padding = padding
        self._rsa = rsa

        key_path = Path(private_key_path).expanduser()
        if not key_path.exists():
            raise RuntimeError(
                f"Kalshi private key file not found: {key_path}. "
                "Download it from your Kalshi dashboard."
            )

        try:
            key_bytes = key_path.read_bytes()
            self._private_key = serialization.load_pem_private_key(
                key_bytes, password=None,
            )
        except Exception as e:
            raise RuntimeError(
                f"Failed to load Kalshi private key from {key_path}: {e}. "
                "Ensure the file is an unencrypted PEM-format RSA private key."
            ) from e

        if not isinstance(self._private_key, rsa.RSAPrivateKey):
            raise RuntimeError(
                f"Key at {key_path} is not an RSA private key. "
                "Kalshi requires RSA keys."
            )

        self.api_key = api_key

    def sign_message(self, message: str) -> str:
        """Sign a string with RSA-PSS / SHA-256, return base64 string."""
        signature = self._private_key.sign(
            message.encode("utf-8"),
            self._padding.PSS(
                mgf=self._padding.MGF1(self._hashes.SHA256()),
                salt_length=self._padding.PSS.DIGEST_LENGTH,
            ),
            self._hashes.SHA256(),
        )
        return base64.b64encode(signature).decode("ascii")

    def headers_for(self, method: str, path: str) -> dict:
        """Produce KALSHI-* auth headers for a given request.

        Args:
            method: HTTP method (e.g. 'GET', 'POST')
            path:   request path including any version prefix
                    (e.g. '/trade-api/v2/portfolio/balance')
                    Do NOT include host or query string in the signed path.

        Returns:
            dict of headers ready to merge into a requests call
        """
        timestamp_ms = str(int(time.time() * 1000))
        method_upper = method.upper()
        message = timestamp_ms + method_upper + path
        signature = self.sign_message(message)
        return {
            "KALSHI-ACCESS-KEY": self.api_key,
            "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
            "KALSHI-ACCESS-SIGNATURE": signature,
            "accept": "application/json",
        }


def try_load_signer(private_key_path: str, api_key: str) -> Optional[KalshiSigner]:
    """Load a signer if possible, return None on any failure (for graceful degradation)."""
    try:
        return KalshiSigner(private_key_path, api_key)
    except Exception as e:
        logger.warning(f"Kalshi signer not available: {e}")
        return None
