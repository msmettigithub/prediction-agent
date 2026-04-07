"""Tests for KalshiTrader diagnostic surfacing and the kalshi-check doctor.

These tests verify that signer load failures are NOT silently swallowed and
that the second call returns the original error rather than a useless
'signer not available' message.
"""

from __future__ import annotations

import os
import sys
import tempfile

import pytest

cryptography = pytest.importorskip("cryptography")

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import Config
from live.kalshi_trader import KalshiTrader


@pytest.fixture
def real_keypair(tmp_path):
    """Write a real RSA key to disk and return the path."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    p = tmp_path / "key.pem"
    p.write_bytes(pem)
    return str(p)


# --- Diagnostic surfacing ---

class TestSignerErrorSurfacing:
    def test_first_call_raises_real_error_when_path_missing(self):
        config = Config(kalshi_api_key="x", kalshi_private_key_path="")
        trader = KalshiTrader(config)
        with pytest.raises(RuntimeError, match="KALSHI_PRIVATE_KEY_PATH not set"):
            trader._ensure_signer()

    def test_second_call_re_raises_original_error(self):
        """The bug fix: previously the second call gave 'signer not available'
        with no detail. It should now re-raise the original error message."""
        config = Config(kalshi_api_key="x", kalshi_private_key_path="")
        trader = KalshiTrader(config)
        # First call — should fail with the real error
        with pytest.raises(RuntimeError, match="KALSHI_PRIVATE_KEY_PATH"):
            trader._ensure_signer()
        # Second call — must NOT say "Kalshi signer not available"; must
        # surface the original cause
        with pytest.raises(RuntimeError, match="KALSHI_PRIVATE_KEY_PATH"):
            trader._ensure_signer()

    def test_nonexistent_file_raises_clear_error(self, tmp_path):
        config = Config(kalshi_api_key="x", kalshi_private_key_path=str(tmp_path / "nope.pem"))
        trader = KalshiTrader(config)
        with pytest.raises(RuntimeError, match="not found"):
            trader._ensure_signer()

    def test_malformed_key_raises_clear_error(self, tmp_path):
        bad = tmp_path / "bad.pem"
        bad.write_text("not a real key")
        config = Config(kalshi_api_key="x", kalshi_private_key_path=str(bad))
        trader = KalshiTrader(config)
        with pytest.raises(RuntimeError, match="Failed to load"):
            trader._ensure_signer()

    def test_valid_key_loads_successfully(self, real_keypair):
        config = Config(kalshi_api_key="test-uuid", kalshi_private_key_path=real_keypair)
        trader = KalshiTrader(config)
        signer = trader._ensure_signer()
        assert signer is not None
        # Second call should reuse the cached signer (no reload)
        assert trader._ensure_signer() is signer

    def test_tilde_path_is_expanded(self, real_keypair, monkeypatch):
        """Verify ~ in path gets expanded via os.path.expanduser."""
        # Move the real key to a path under HOME and use ~ in the config
        import shutil
        home = os.path.expanduser("~")
        target_dir = os.path.join(home, ".kalshi-test-temp")
        os.makedirs(target_dir, exist_ok=True)
        target = os.path.join(target_dir, "test_key.pem")
        try:
            shutil.copy(real_keypair, target)
            # Use ~ prefix
            config = Config(
                kalshi_api_key="test-uuid",
                kalshi_private_key_path="~/.kalshi-test-temp/test_key.pem",
            )
            trader = KalshiTrader(config)
            signer = trader._ensure_signer()
            assert signer is not None
        finally:
            if os.path.exists(target):
                os.unlink(target)
            if os.path.exists(target_dir):
                os.rmdir(target_dir)


# --- Doctor command behavior ---

class TestKalshiCheckDoctor:
    def test_doctor_runs_to_completion_on_missing_path(self, tmp_path, capsys):
        """Doctor should print all the diagnostic checks and not crash
        when the path is missing."""
        from main import cmd_kalshi_check
        # Use a temp DB
        from database.db import Database
        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        try:
            config = Config(kalshi_api_key="test", kalshi_private_key_path="")
            cmd_kalshi_check(db, config)
            output = capsys.readouterr().out
            assert "KALSHI AUTH CHECK" in output
            assert "KALSHI_PRIVATE_KEY_PATH not set" in output
        finally:
            db.close()

    def test_doctor_succeeds_through_signing_check_with_real_key(
        self, tmp_path, real_keypair, capsys, monkeypatch
    ):
        """With a real keypair (but no real Kalshi auth), the doctor should
        progress through all 4 local checks and only fail at the live API call."""
        from main import cmd_kalshi_check
        from database.db import Database

        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        try:
            config = Config(kalshi_api_key="test-uuid-1234", kalshi_private_key_path=real_keypair)
            # Patch the actual HTTP call so we don't hit real Kalshi
            from unittest.mock import patch, MagicMock
            with patch("live.kalshi_trader.requests.get") as mock_get:
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.json.return_value = {"balance": 1234}
                mock_resp.raise_for_status = MagicMock()
                mock_get.return_value = mock_resp

                cmd_kalshi_check(db, config)
            output = capsys.readouterr().out
            # All 5 checks should appear
            assert "1. Environment variables" in output
            assert "2. Private key file" in output
            assert "3. Key format" in output
            assert "4. Signature generation" in output
            assert "5. Live Kalshi API call" in output
            assert "ALL CHECKS PASSED" in output
            # Balance should show $12.34 (1234 cents)
            assert "$12.34" in output
        finally:
            db.close()

    def test_doctor_reports_401_diagnostically(self, tmp_path, real_keypair, capsys):
        """When Kalshi rejects the signature with 401, doctor should explain why."""
        from main import cmd_kalshi_check
        from database.db import Database
        from unittest.mock import patch
        import requests as _req

        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        try:
            config = Config(kalshi_api_key="test-uuid", kalshi_private_key_path=real_keypair)
            with patch("live.kalshi_trader.requests.get") as mock_get:
                err = _req.exceptions.HTTPError("401 Client Error: Unauthorized")
                mock_resp = mock_get.return_value
                mock_resp.status_code = 401
                mock_resp.raise_for_status.side_effect = err
                cmd_kalshi_check(db, config)
            output = capsys.readouterr().out
            assert "401" in output
            assert "Kalshi rejected the signature" in output
        finally:
            db.close()
