"""Tests for Kalshi RSA-PSS request signing.

Generates an ephemeral RSA keypair per test — no real Kalshi credentials used.
Verifies signature format, header structure, and round-trip verification.
"""

from __future__ import annotations

import base64
import os
import tempfile
import time

import pytest

# Skip the entire module if cryptography is not installed
cryptography = pytest.importorskip("cryptography")

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from live.kalshi_signer import KalshiSigner, try_load_signer


@pytest.fixture
def test_keypair(tmp_path):
    """Generate an ephemeral RSA keypair, write private key to a temp file."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()

    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    key_path = tmp_path / "test_key.pem"
    key_path.write_bytes(pem)

    return {"path": str(key_path), "private": private_key, "public": public_key}


# --- Construction & error handling ---

class TestSignerConstruction:
    def test_loads_valid_key(self, test_keypair):
        signer = KalshiSigner(
            private_key_path=test_keypair["path"],
            api_key="test-key-uuid",
        )
        assert signer.api_key == "test-key-uuid"

    def test_missing_path_raises(self):
        with pytest.raises(RuntimeError, match="KALSHI_PRIVATE_KEY_PATH not set"):
            KalshiSigner(private_key_path="", api_key="x")

    def test_missing_api_key_raises(self, test_keypair):
        with pytest.raises(RuntimeError, match="KALSHI_API_KEY not set"):
            KalshiSigner(private_key_path=test_keypair["path"], api_key="")

    def test_nonexistent_file_raises(self, tmp_path):
        with pytest.raises(RuntimeError, match="not found"):
            KalshiSigner(
                private_key_path=str(tmp_path / "nope.pem"),
                api_key="test",
            )

    def test_malformed_key_raises(self, tmp_path):
        bad_path = tmp_path / "bad.pem"
        bad_path.write_text("not a real PEM file")
        with pytest.raises(RuntimeError, match="Failed to load"):
            KalshiSigner(private_key_path=str(bad_path), api_key="test")

    def test_try_load_returns_none_on_failure(self):
        result = try_load_signer(private_key_path="", api_key="x")
        assert result is None


# --- Signature production ---

class TestSignature:
    def test_signs_simple_message(self, test_keypair):
        signer = KalshiSigner(test_keypair["path"], "k")
        sig = signer.sign_message("hello")
        # Should be valid base64
        decoded = base64.b64decode(sig)
        assert len(decoded) == 256  # 2048-bit RSA → 256 byte signature

    def test_signature_verifies_against_public_key(self, test_keypair):
        """The signature must be verifiable with the matching public key."""
        signer = KalshiSigner(test_keypair["path"], "k")
        message = "1700000000000GET/trade-api/v2/portfolio/balance"
        sig = signer.sign_message(message)
        sig_bytes = base64.b64decode(sig)

        # Verify with public key — should not raise
        test_keypair["public"].verify(
            sig_bytes,
            message.encode("utf-8"),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )

    def test_different_messages_produce_different_signatures(self, test_keypair):
        signer = KalshiSigner(test_keypair["path"], "k")
        sig_a = signer.sign_message("message-a")
        sig_b = signer.sign_message("message-b")
        assert sig_a != sig_b

    def test_signature_changes_each_call_due_to_pss_salt(self, test_keypair):
        """RSA-PSS uses random salt, so the same message signs differently each call."""
        signer = KalshiSigner(test_keypair["path"], "k")
        sig1 = signer.sign_message("same")
        sig2 = signer.sign_message("same")
        # Both should still verify, but they should not be byte-identical
        assert sig1 != sig2


# --- Header production ---

class TestHeaders:
    def test_headers_have_required_keys(self, test_keypair):
        signer = KalshiSigner(test_keypair["path"], "uuid-1234")
        headers = signer.headers_for("GET", "/trade-api/v2/portfolio/balance")
        assert "KALSHI-ACCESS-KEY" in headers
        assert "KALSHI-ACCESS-TIMESTAMP" in headers
        assert "KALSHI-ACCESS-SIGNATURE" in headers
        assert headers["KALSHI-ACCESS-KEY"] == "uuid-1234"

    def test_timestamp_is_milliseconds(self, test_keypair):
        signer = KalshiSigner(test_keypair["path"], "k")
        headers = signer.headers_for("GET", "/path")
        ts = int(headers["KALSHI-ACCESS-TIMESTAMP"])
        # Should be roughly current time in ms (within 5 seconds)
        now_ms = int(time.time() * 1000)
        assert abs(now_ms - ts) < 5000
        # Should be 13 digits (millisecond epoch)
        assert len(str(ts)) == 13

    def test_signature_signs_timestamp_method_path(self, test_keypair):
        """Verify the signed message format: timestamp + METHOD + path."""
        signer = KalshiSigner(test_keypair["path"], "k")
        headers = signer.headers_for("POST", "/trade-api/v2/portfolio/orders")

        ts = headers["KALSHI-ACCESS-TIMESTAMP"]
        expected_message = ts + "POST" + "/trade-api/v2/portfolio/orders"

        sig_bytes = base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"])
        # Should verify with the public key
        test_keypair["public"].verify(
            sig_bytes,
            expected_message.encode("utf-8"),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )

    def test_method_is_uppercased(self, test_keypair):
        signer = KalshiSigner(test_keypair["path"], "k")
        headers_lower = signer.headers_for("get", "/path")
        headers_upper = signer.headers_for("GET", "/path")

        ts_lower = headers_lower["KALSHI-ACCESS-TIMESTAMP"]
        ts_upper = headers_upper["KALSHI-ACCESS-TIMESTAMP"]

        # Both should produce valid signatures over the same uppercased message
        sig_lower = base64.b64decode(headers_lower["KALSHI-ACCESS-SIGNATURE"])
        sig_upper = base64.b64decode(headers_upper["KALSHI-ACCESS-SIGNATURE"])

        # Verify lower-case method produced uppercase signature message
        test_keypair["public"].verify(
            sig_lower,
            (ts_lower + "GET" + "/path").encode("utf-8"),
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )


# --- Integration with KalshiTrader ---

class TestTraderIntegration:
    def test_trader_uses_signer_headers(self, test_keypair):
        """Verify KalshiTrader sends KALSHI-* headers, not Bearer."""
        from unittest.mock import patch, MagicMock
        from config import Config
        from live.kalshi_trader import KalshiTrader

        config = Config(
            kalshi_api_key="test-uuid",
            kalshi_private_key_path=test_keypair["path"],
        )
        signer = KalshiSigner(test_keypair["path"], "test-uuid")
        trader = KalshiTrader(config, signer=signer)

        with patch("live.kalshi_trader.requests.get") as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = {"balance": 5000}
            mock_get.return_value.raise_for_status = MagicMock()

            balance = trader.get_balance()

            assert balance == 50.0  # 5000 cents → $50
            sent_headers = mock_get.call_args.kwargs["headers"]
            assert "KALSHI-ACCESS-KEY" in sent_headers
            assert "KALSHI-ACCESS-SIGNATURE" in sent_headers
            assert "KALSHI-ACCESS-TIMESTAMP" in sent_headers
            assert "Authorization" not in sent_headers  # No more Bearer

    def test_trader_signs_only_path_not_full_url(self, test_keypair):
        """The signature should sign only the path component, not host/scheme."""
        from unittest.mock import patch, MagicMock
        from urllib.parse import urlparse
        from config import Config
        from live.kalshi_trader import KalshiTrader

        config = Config(
            kalshi_api_key="test-uuid",
            kalshi_private_key_path=test_keypair["path"],
        )
        signer = KalshiSigner(test_keypair["path"], "test-uuid")
        trader = KalshiTrader(config, signer=signer)

        with patch("live.kalshi_trader.requests.get") as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = {"balance": 0}
            mock_get.return_value.raise_for_status = MagicMock()

            trader.get_balance()

            sent_headers = mock_get.call_args.kwargs["headers"]
            ts = sent_headers["KALSHI-ACCESS-TIMESTAMP"]
            sig = base64.b64decode(sent_headers["KALSHI-ACCESS-SIGNATURE"])

            # The signed message should be: timestamp + GET + path-only
            expected_path = "/trade-api/v2/portfolio/balance"
            expected_message = ts + "GET" + expected_path

            test_keypair["public"].verify(
                sig,
                expected_message.encode("utf-8"),
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.DIGEST_LENGTH,
                ),
                hashes.SHA256(),
            )

    def test_trader_raises_clear_error_when_no_key_configured(self):
        """Without KALSHI_PRIVATE_KEY_PATH, the trader fails on first auth call."""
        from config import Config
        from live.kalshi_trader import KalshiTrader

        config = Config(kalshi_api_key="x", kalshi_private_key_path="")
        trader = KalshiTrader(config)
        with pytest.raises(RuntimeError, match="KALSHI_PRIVATE_KEY_PATH not set"):
            trader.get_balance()
