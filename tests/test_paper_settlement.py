"""Tests for paper trade settlement flow.

Covers:
- Binary payoff math (_compute_paper_pnl) for YES/NO wins and losses
- update_contract_resolution DB helper
- fetch_single_market behavior in mock mode
- End-to-end: insert open paper trade → settle via resolution update → verify PnL
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

# Ensure project root on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ["MOCK_TOOLS"] = "true"

from config import Config
from database.db import Database
from database.models import Contract
from main import _compute_paper_pnl
from tools.kalshi import KalshiTool


# --- Helper fixtures ---

@pytest.fixture
def tmpdb():
    """Fresh Database in a temp file."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    db = Database(path)
    yield db
    db.close()
    os.unlink(path)


@pytest.fixture
def contract_factory(tmpdb):
    """Create a contract in the DB and return it with its row id."""
    counter = {"n": 0}

    def _make(yes_price: float = 0.50, resolved: bool = False, resolution: bool | None = None):
        counter["n"] += 1
        c = Contract(
            source="test",
            source_id=f"TEST-TICKER-{counter['n']:03d}",
            title=f"Test contract {counter['n']}",
            category="economics",
            yes_price=yes_price,
            volume_24h=10000,
            open_time=datetime(2026, 1, 1),
            close_time=datetime(2026, 12, 31),
            resolved=resolved,
            resolution=resolution,
            resolved_at=datetime(2026, 6, 1) if resolved else None,
        )
        c.id = tmpdb.upsert_contract(c)
        return c

    return _make


# --- _compute_paper_pnl ---

class TestComputePaperPnl:
    def test_yes_bet_yes_resolution_wins(self):
        # YES at $0.40, bet $40 → 100 contracts, win $60 if YES
        won, pnl = _compute_paper_pnl(side="YES", entry_price=0.40, bet_amount=40.0, resolution_yes=True)
        assert won is True
        assert pnl == 60.0

    def test_yes_bet_no_resolution_loses(self):
        won, pnl = _compute_paper_pnl(side="YES", entry_price=0.40, bet_amount=40.0, resolution_yes=False)
        assert won is False
        assert pnl == -40.0

    def test_no_bet_no_resolution_wins(self):
        # NO bet at YES price 0.60 → NO price 0.40, bet $40 → 100 contracts,
        # win collects YES side of the payoff → 100 * 0.60 = $60
        won, pnl = _compute_paper_pnl(side="NO", entry_price=0.60, bet_amount=40.0, resolution_yes=False)
        assert won is True
        assert pnl == 60.0

    def test_no_bet_yes_resolution_loses(self):
        won, pnl = _compute_paper_pnl(side="NO", entry_price=0.60, bet_amount=40.0, resolution_yes=True)
        assert won is False
        assert pnl == -40.0

    def test_yes_at_50_yields_equal_upside(self):
        # Buy YES at 0.50 for $50 → 100 contracts → win $50 if YES
        won, pnl = _compute_paper_pnl(side="YES", entry_price=0.50, bet_amount=50.0, resolution_yes=True)
        assert won is True
        assert pnl == 50.0

    def test_no_at_50_yields_equal_upside(self):
        won, pnl = _compute_paper_pnl(side="NO", entry_price=0.50, bet_amount=50.0, resolution_yes=False)
        assert won is True
        assert pnl == 50.0

    def test_yes_at_extreme_low_price_big_upside(self):
        # $1 at price 0.05 → 20 contracts → $19 profit if YES (huge payoff)
        won, pnl = _compute_paper_pnl(side="YES", entry_price=0.05, bet_amount=1.0, resolution_yes=True)
        assert won is True
        assert pnl == 19.0

    def test_yes_at_price_zero_doesnt_divide(self):
        """Edge case: entry price 0 should not crash."""
        won, pnl = _compute_paper_pnl(side="YES", entry_price=0.0, bet_amount=10.0, resolution_yes=True)
        assert pnl == -10.0  # falls through to loss branch

    def test_no_at_price_one_doesnt_divide(self):
        """Edge case: NO bet at YES price 1.0 means no_price=0, should not crash."""
        won, pnl = _compute_paper_pnl(side="NO", entry_price=1.0, bet_amount=10.0, resolution_yes=False)
        assert pnl == -10.0  # falls through to loss branch


# --- update_contract_resolution ---

class TestUpdateContractResolution:
    def test_marks_unresolved_contract_as_resolved(self, tmpdb, contract_factory):
        c = contract_factory(resolved=False)
        ok = tmpdb.update_contract_resolution(c.id, resolution=True)
        assert ok is True
        reloaded = tmpdb.get_contract(c.id)
        assert reloaded.resolved is True
        assert reloaded.resolution is True
        assert reloaded.resolved_at is not None

    def test_does_not_overwrite_already_resolved(self, tmpdb, contract_factory):
        c = contract_factory(resolved=True, resolution=False)
        # Try to flip it to True — should NOT update
        ok = tmpdb.update_contract_resolution(c.id, resolution=True)
        assert ok is False
        reloaded = tmpdb.get_contract(c.id)
        assert reloaded.resolution is False  # unchanged

    def test_returns_false_for_nonexistent_contract(self, tmpdb):
        ok = tmpdb.update_contract_resolution(contract_id=99999, resolution=True)
        assert ok is False

    def test_resolution_false_is_stored(self, tmpdb, contract_factory):
        c = contract_factory(resolved=False)
        tmpdb.update_contract_resolution(c.id, resolution=False)
        reloaded = tmpdb.get_contract(c.id)
        assert reloaded.resolved is True
        assert reloaded.resolution is False


# --- fetch_single_market (mock mode) ---

class TestFetchSingleMarket:
    def test_mock_mode_returns_resolved_contract(self):
        tool = KalshiTool(mock_mode=True)
        # Use a ticker present in MOCK_RESOLVED_MARKETS
        result = tool.fetch_single_market("FED-RATE-25DEC-5.25")
        assert result is not None
        assert result["ticker"] == "FED-RATE-25DEC-5.25"
        assert result.get("resolved") is True
        assert result.get("resolution") is True

    def test_mock_mode_returns_none_for_unknown_ticker(self):
        tool = KalshiTool(mock_mode=True)
        result = tool.fetch_single_market("DOES-NOT-EXIST-TICKER")
        assert result is None

    def test_mock_mode_returns_open_contract(self):
        tool = KalshiTool(mock_mode=True)
        result = tool.fetch_single_market("FED-RATE-26JUN-5.00")
        assert result is not None
        assert result["ticker"] == "FED-RATE-26JUN-5.00"
        # Open contracts in mock don't have "resolved" flag set
        assert not result.get("resolved", False)

    def test_live_mode_404_returns_none(self):
        """When Kalshi returns 404, fetch_single_market should return None, not raise."""
        tool = KalshiTool(mock_mode=False)
        with patch("tools.kalshi.requests.get") as mock_get:
            mock_get.return_value.status_code = 404
            result = tool.fetch_single_market("NONEXISTENT")
            assert result is None

    def test_live_mode_parses_settled_response(self):
        """Simulate Kalshi returning a settled market; verify it parses correctly."""
        tool = KalshiTool(mock_mode=False)
        with patch("tools.kalshi.requests.get") as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.raise_for_status = MagicMock()
            mock_get.return_value.json.return_value = {
                "market": {
                    "ticker": "KXCPI-26MAR-T0.4",
                    "title": "Will CPI rise more than 0.4% in March 2026?",
                    "status": "finalized",
                    "result": "yes",
                    "last_price_dollars": "0.9800",
                    "volume_fp": "25000.00",
                    "volume_24h_fp": "0.00",
                    "close_time": "2026-04-10T20:30:00Z",
                    "open_time": "2026-03-01T00:00:00Z",
                    "event_ticker": "KXCPI-26MAR",
                }
            }
            result = tool.fetch_single_market("KXCPI-26MAR-T0.4")
            assert result is not None
            assert result["resolved"] is True
            assert result["resolution"] is True  # "yes" → True
            assert result["ticker"] == "KXCPI-26MAR-T0.4"


# --- End-to-end settlement flow ---

class TestSettlementFlow:
    def test_open_trade_settles_when_contract_resolves(self, tmpdb, contract_factory):
        """The main loop cmd_paper does:
        1. Poll Kalshi (mocked here)
        2. update_contract_resolution
        3. Iterate open trades → compute PnL → close_paper_trade
        """
        # Set up: contract at 30¢, paper trade YES for $30 (100 contracts)
        c = contract_factory(yes_price=0.30, resolved=False)
        trade_id = tmpdb.insert_paper_trade({
            "contract_id": c.id,
            "side": "YES",
            "entry_price": 0.30,
            "model_prob": 0.50,
            "kelly_fraction": 0.03,
            "bet_amount": 30.0,
        })

        # Simulate Kalshi reporting the contract as settled YES
        updated = tmpdb.update_contract_resolution(c.id, resolution=True)
        assert updated is True

        # Reload contract and simulate the settlement loop
        contract = tmpdb.get_contract(c.id)
        assert contract.resolved
        won, pnl = _compute_paper_pnl(
            side="YES",
            entry_price=0.30,
            bet_amount=30.0,
            resolution_yes=bool(contract.resolution),
        )
        tmpdb.close_paper_trade(trade_id, won, 1.0, pnl)

        # Expected: $30 at 0.30 → 100 contracts → $70 profit
        trades = tmpdb.get_all_paper_trades()
        settled = [t for t in trades if t["status"] != "open"]
        assert len(settled) == 1
        assert settled[0]["status"] == "won"
        assert settled[0]["pnl"] == 70.0

    def test_no_trade_settles_when_contract_resolves_no(self, tmpdb, contract_factory):
        c = contract_factory(yes_price=0.70, resolved=False)
        trade_id = tmpdb.insert_paper_trade({
            "contract_id": c.id,
            "side": "NO",
            "entry_price": 0.70,
            "model_prob": 0.50,
            "kelly_fraction": 0.03,
            "bet_amount": 30.0,
        })

        tmpdb.update_contract_resolution(c.id, resolution=False)
        contract = tmpdb.get_contract(c.id)
        won, pnl = _compute_paper_pnl("NO", 0.70, 30.0, bool(contract.resolution))
        tmpdb.close_paper_trade(trade_id, won, 0.0, pnl)

        settled = [t for t in tmpdb.get_all_paper_trades() if t["status"] != "open"]
        # $30 at no_price 0.30 → 100 contracts → collect $70 on YES-side payoff
        assert settled[0]["status"] == "won"
        assert settled[0]["pnl"] == 70.0

    def test_open_trade_stays_open_when_contract_not_resolved(self, tmpdb, contract_factory):
        c = contract_factory(yes_price=0.40, resolved=False)
        tmpdb.insert_paper_trade({
            "contract_id": c.id,
            "side": "YES",
            "entry_price": 0.40,
            "model_prob": 0.50,
            "kelly_fraction": 0.03,
            "bet_amount": 20.0,
        })
        # Don't update resolution — trade should remain open
        open_trades = tmpdb.get_open_paper_trades()
        assert len(open_trades) == 1
        assert open_trades[0]["status"] == "open"
