"""Tests for live trading guard and Kalshi order placement.

CRITICAL: All Kalshi API calls are mocked. No real orders are ever placed.
Every test verifies a safety guard or output format.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from config import Config
from live.guard import LiveTradingGuard, GuardResult, compute_shares
from live.kalshi_trader import KalshiTrader


def _make_db(deployed: float = 0.0) -> MagicMock:
    """Create a mock DB that reports a fixed total_live_deployed value."""
    db = MagicMock()
    db.total_live_deployed.return_value = deployed
    return db


def _enabled_config(**overrides) -> Config:
    base = dict(
        live_trading_enabled=True,
        max_live_bankroll=50.00,
        max_single_bet=10.00,
        kalshi_api_key="test-key-uuid-12345",
    )
    base.update(overrides)
    return Config(**base)


def _disabled_config() -> Config:
    return Config(live_trading_enabled=False, max_live_bankroll=50.00, max_single_bet=10.00)


def _mock_signer():
    """Return a MagicMock that produces fake signed headers."""
    signer = MagicMock()
    signer.headers_for.return_value = {
        "KALSHI-ACCESS-KEY": "test-uuid",
        "KALSHI-ACCESS-TIMESTAMP": "1700000000000",
        "KALSHI-ACCESS-SIGNATURE": "fake-signature-base64",
    }
    signer.api_key = "test-uuid"
    return signer


# --- Guard: enabled flag ---

class TestGuardEnabled:
    def test_refuses_when_disabled(self):
        guard = LiveTradingGuard(_disabled_config(), _make_db())
        result = guard.check_enabled()
        assert result.ok is False
        assert "LIVE_TRADING_ENABLED" in result.reason

    def test_refuses_check_all_when_disabled(self):
        guard = LiveTradingGuard(_disabled_config(), _make_db())
        result = guard.check_all(proposed_cost=5.00)
        assert result.ok is False
        assert "LIVE_TRADING_ENABLED" in result.reason

    def test_accepts_when_enabled(self):
        guard = LiveTradingGuard(_enabled_config(), _make_db(),
                                 balance_provider=lambda: 100.00)
        result = guard.check_enabled()
        assert result.ok is True


# --- Guard: bankroll cap ---

class TestGuardBankrollCap:
    def test_refuses_when_proposed_exceeds_remaining_cap(self):
        # $40 already deployed, $50 cap, propose $15 more → $55 > $50
        guard = LiveTradingGuard(_enabled_config(), _make_db(deployed=40.00),
                                 balance_provider=lambda: 100.00)
        result = guard.check_under_cap(proposed_cost=15.00)
        assert result.ok is False
        assert "MAX_LIVE_BANKROLL" in result.reason
        assert result.deployed == 40.00

    def test_accepts_when_proposed_under_cap(self):
        guard = LiveTradingGuard(_enabled_config(), _make_db(deployed=20.00),
                                 balance_provider=lambda: 100.00)
        result = guard.check_under_cap(proposed_cost=10.00)
        assert result.ok is True
        assert result.deployed == 20.00
        assert result.remaining_cap == 30.00

    def test_check_all_blocks_when_cap_exceeded(self):
        guard = LiveTradingGuard(_enabled_config(), _make_db(deployed=45.00),
                                 balance_provider=lambda: 100.00)
        result = guard.check_all(proposed_cost=10.00)
        assert result.ok is False
        assert "MAX_LIVE_BANKROLL" in result.reason


# --- Guard: single bet size ---

class TestGuardSingleBetSize:
    def test_refuses_when_single_bet_too_large(self):
        guard = LiveTradingGuard(_enabled_config(), _make_db(),
                                 balance_provider=lambda: 100.00)
        # $10 cap, propose $15
        result = guard.check_single_bet_size(proposed_cost=15.00)
        assert result.ok is False
        assert "MAX_SINGLE_BET" in result.reason

    def test_accepts_at_exactly_max_single_bet(self):
        guard = LiveTradingGuard(_enabled_config(), _make_db(),
                                 balance_provider=lambda: 100.00)
        result = guard.check_single_bet_size(proposed_cost=10.00)
        assert result.ok is True

    def test_refuses_zero_or_negative_cost(self):
        guard = LiveTradingGuard(_enabled_config(), _make_db(),
                                 balance_provider=lambda: 100.00)
        result = guard.check_single_bet_size(proposed_cost=0.00)
        assert result.ok is False
        result2 = guard.check_single_bet_size(proposed_cost=-5.00)
        assert result2.ok is False

    def test_check_all_blocks_when_single_bet_exceeded(self):
        guard = LiveTradingGuard(_enabled_config(), _make_db(deployed=0),
                                 balance_provider=lambda: 100.00)
        result = guard.check_all(proposed_cost=15.00)
        assert result.ok is False
        assert "MAX_SINGLE_BET" in result.reason


# --- Guard: balance ---

class TestGuardBalance:
    def test_refuses_when_balance_insufficient(self):
        # Mock balance below proposed cost
        guard = LiveTradingGuard(_enabled_config(), _make_db(),
                                 balance_provider=lambda: 5.00)
        result = guard.check_balance(proposed_cost=10.00)
        assert result.ok is False
        assert "Insufficient balance" in result.reason
        assert result.available_balance == 5.00

    def test_accepts_when_balance_sufficient(self):
        guard = LiveTradingGuard(_enabled_config(), _make_db(),
                                 balance_provider=lambda: 100.00)
        result = guard.check_balance(proposed_cost=10.00)
        assert result.ok is True
        assert result.available_balance == 100.00

    def test_check_all_blocks_when_balance_low(self):
        guard = LiveTradingGuard(_enabled_config(), _make_db(),
                                 balance_provider=lambda: 3.00)
        result = guard.check_all(proposed_cost=8.00)
        assert result.ok is False
        assert "Insufficient balance" in result.reason

    def test_balance_provider_exception_fails_safe(self):
        def broken_provider():
            raise RuntimeError("Kalshi API down")
        guard = LiveTradingGuard(_enabled_config(), _make_db(),
                                 balance_provider=broken_provider)
        result = guard.check_balance(proposed_cost=5.00)
        assert result.ok is False
        assert "Could not fetch" in result.reason


# --- Guard: full check_all happy path ---

class TestGuardCheckAllHappyPath:
    def test_all_pass_with_sensible_values(self):
        guard = LiveTradingGuard(
            _enabled_config(),
            _make_db(deployed=15.00),
            balance_provider=lambda: 50.00,
        )
        result = guard.check_all(proposed_cost=8.00)
        assert result.ok is True
        assert result.deployed == 15.00
        assert result.remaining_cap == 35.00
        assert result.available_balance == 50.00


# --- Shares calculation ---

class TestComputeShares:
    def test_floor_division(self):
        # $9.85 / $0.34 = 28.97... → 28 (always rounds down)
        assert compute_shares(max_cost=9.85, price=0.34) == 28
        # $10.20 / $0.34 = 30.0 (but float-imprecise, so floor to 29 is acceptable)
        result = compute_shares(max_cost=10.20, price=0.34)
        assert result in (29, 30)

    def test_exact_division(self):
        assert compute_shares(max_cost=10.00, price=0.50) == 20

    def test_rounds_down(self):
        # $10 / $0.33 = 30.30 → 30
        assert compute_shares(max_cost=10.00, price=0.33) == 30

    def test_zero_price_returns_zero(self):
        assert compute_shares(max_cost=10.00, price=0.0) == 0

    def test_zero_cost_returns_zero(self):
        assert compute_shares(max_cost=0.0, price=0.50) == 0

    def test_never_overspends(self):
        """Verify shares * price <= max_cost for any input."""
        for max_cost in [1.00, 5.00, 9.99, 10.00]:
            for price in [0.01, 0.17, 0.33, 0.50, 0.77, 0.99]:
                shares = compute_shares(max_cost, price)
                actual_cost = shares * price
                assert actual_cost <= max_cost, \
                    f"Overspend: cost={max_cost} price={price} shares={shares}"


# --- Kalshi order payload format ---

class TestKalshiOrderPayload:
    def test_yes_order_payload_uses_cents(self):
        config = _enabled_config()
        trader = KalshiTrader(config, signer=_mock_signer())

        with patch("live.kalshi_trader.requests.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {"order": {"order_id": "test-123"}}
            mock_post.return_value.raise_for_status = MagicMock()

            trader.place_order(ticker="KXFEDRATE-26MAY-T5.00", side="yes",
                               shares=29, price_cents=34)

            call_args = mock_post.call_args
            payload = call_args.kwargs["json"]
            assert payload["ticker"] == "KXFEDRATE-26MAY-T5.00"
            assert payload["side"] == "yes"
            assert payload["action"] == "buy"
            assert payload["type"] == "limit"
            assert payload["count"] == 29
            assert payload["yes_price"] == 34  # cents, not 0.34
            assert "no_price" not in payload

    def test_no_order_payload_uses_no_price_field(self):
        config = _enabled_config()
        trader = KalshiTrader(config, signer=_mock_signer())

        with patch("live.kalshi_trader.requests.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {"order": {"order_id": "test-456"}}
            mock_post.return_value.raise_for_status = MagicMock()

            trader.place_order(ticker="KXBTC-26APR-T100K", side="no",
                               shares=10, price_cents=66)

            payload = mock_post.call_args.kwargs["json"]
            assert payload["side"] == "no"
            assert payload["no_price"] == 66
            assert "yes_price" not in payload

    def test_invalid_shares_rejected(self):
        config = _enabled_config()
        trader = KalshiTrader(config)
        with pytest.raises(RuntimeError, match="Invalid share count"):
            trader.place_order(ticker="X", side="yes", shares=0, price_cents=50)

    def test_invalid_price_rejected(self):
        config = _enabled_config()
        trader = KalshiTrader(config)
        with pytest.raises(RuntimeError, match="Invalid price"):
            trader.place_order(ticker="X", side="yes", shares=10, price_cents=0)
        with pytest.raises(RuntimeError, match="Invalid price"):
            trader.place_order(ticker="X", side="yes", shares=10, price_cents=100)

    def test_invalid_side_rejected(self):
        config = _enabled_config()
        trader = KalshiTrader(config)
        with pytest.raises(RuntimeError, match="Invalid side"):
            trader.place_order(ticker="X", side="maybe", shares=10, price_cents=50)

    def test_no_orders_placed_without_signing_credentials(self):
        """Without KALSHI_PRIVATE_KEY_PATH, the trader fails before any HTTP call."""
        config = Config(live_trading_enabled=True, max_live_bankroll=50, max_single_bet=10,
                        kalshi_api_key="x", kalshi_private_key_path="")
        trader = KalshiTrader(config)
        with pytest.raises(RuntimeError, match="KALSHI_PRIVATE_KEY_PATH"):
            trader.place_order(ticker="X", side="yes", shares=10, price_cents=50)
