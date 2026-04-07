"""Tests for the backtester and calibration metrics."""

from __future__ import annotations

from datetime import datetime

import pytest

from config import Config
from database.db import Database
from database.models import Contract, Resolution
from model.calibration import compute_calibration, CalibrationReport
from backtest.backtest import Backtester
from backtest.report import get_calibration_warning


def _seed_contracts(db: Database, n: int, yes_rate: float = 0.6) -> list[Contract]:
    """Seed n resolved contracts with controlled outcomes."""
    contracts = []
    for i in range(n):
        resolved_yes = i < int(n * yes_rate)
        c = Contract(
            source="test",
            source_id=f"TEST-{i:03d}",
            title=f"Test contract {i}",
            category="economics",
            yes_price=0.55 + (0.02 * (i % 5)),  # prices between 0.55-0.63
            volume_24h=10000,
            open_time=datetime(2025, 1, 1),
            close_time=datetime(2025, 6, 30),
            resolved=True,
            resolution=resolved_yes,
            resolved_at=datetime(2025, 7, 1),
        )
        c_id = db.upsert_contract(c)
        c.id = c_id
        contracts.append(c)
    return contracts


# --- Brier score ---

class TestBrierScore:
    def test_perfect_predictions_brier_zero(self):
        """If model_prob matches outcome exactly, Brier should be 0."""
        resolutions = [
            Resolution(model_prob=1.0, market_prob=0.5, resolved_yes=True,
                       brier_component=0.0, correct_direction=True),
            Resolution(model_prob=0.0, market_prob=0.5, resolved_yes=False,
                       brier_component=0.0, correct_direction=True),
        ]
        report = compute_calibration(resolutions, min_resolved=0)
        assert report.brier_score == pytest.approx(0.0, abs=0.001)

    def test_worst_predictions_brier_one(self):
        """If model_prob is completely wrong, Brier should be 1.0."""
        resolutions = [
            Resolution(model_prob=0.0, market_prob=0.5, resolved_yes=True,
                       brier_component=1.0, correct_direction=False),
            Resolution(model_prob=1.0, market_prob=0.5, resolved_yes=False,
                       brier_component=1.0, correct_direction=False),
        ]
        report = compute_calibration(resolutions, min_resolved=0)
        assert report.brier_score == pytest.approx(1.0, abs=0.001)

    def test_hand_computed_brier(self):
        """Verify against hand-computed values for 5 specific contracts."""
        # Contract 1: predicted 0.7, outcome YES -> (0.7-1)^2 = 0.09
        # Contract 2: predicted 0.3, outcome NO  -> (0.3-0)^2 = 0.09
        # Contract 3: predicted 0.8, outcome YES -> (0.8-1)^2 = 0.04
        # Contract 4: predicted 0.6, outcome NO  -> (0.6-0)^2 = 0.36
        # Contract 5: predicted 0.5, outcome YES -> (0.5-1)^2 = 0.25
        # Mean = (0.09 + 0.09 + 0.04 + 0.36 + 0.25) / 5 = 0.166
        resolutions = [
            Resolution(model_prob=0.7, market_prob=0.5, resolved_yes=True,
                       brier_component=0.09, correct_direction=True),
            Resolution(model_prob=0.3, market_prob=0.5, resolved_yes=False,
                       brier_component=0.09, correct_direction=True),
            Resolution(model_prob=0.8, market_prob=0.5, resolved_yes=True,
                       brier_component=0.04, correct_direction=True),
            Resolution(model_prob=0.6, market_prob=0.5, resolved_yes=False,
                       brier_component=0.36, correct_direction=False),
            Resolution(model_prob=0.5, market_prob=0.5, resolved_yes=True,
                       brier_component=0.25, correct_direction=False),
        ]
        report = compute_calibration(resolutions, min_resolved=0)
        assert report.brier_score == pytest.approx(0.166, abs=0.001)


# --- Separation metric ---

class TestSeparation:
    def test_known_separation(self):
        """Winners at prob=0.75 and losers at prob=0.45 →
        confidence_when_correct = |0.75-0.5| = 0.25
        confidence_when_incorrect = |0.45-0.5| = 0.05
        separation = 0.25 - 0.05 = 0.20
        """
        resolutions = []
        # 5 correct predictions at 0.75 (all YES)
        for _ in range(5):
            resolutions.append(Resolution(
                model_prob=0.75, market_prob=0.5, resolved_yes=True,
                brier_component=(0.75-1)**2, correct_direction=True,
            ))
        # 5 incorrect predictions at 0.45 (predicted NO-leaning, but resolved YES)
        for _ in range(5):
            resolutions.append(Resolution(
                model_prob=0.45, market_prob=0.5, resolved_yes=True,
                brier_component=(0.45-1)**2, correct_direction=False,
            ))
        report = compute_calibration(resolutions, min_resolved=0)
        assert report.separation == pytest.approx(0.20, abs=0.01)


# --- Calibration gate ---

class TestCalibrationGate:
    def test_below_30_insufficient(self):
        resolutions = [
            Resolution(model_prob=0.6, market_prob=0.5, resolved_yes=True,
                       brier_component=0.16, correct_direction=True)
            for _ in range(20)
        ]
        report = compute_calibration(resolutions, min_resolved=30)
        assert report.sufficient_data is False

    def test_at_30_sufficient(self):
        resolutions = [
            Resolution(model_prob=0.6, market_prob=0.5, resolved_yes=True,
                       brier_component=0.16, correct_direction=True)
            for _ in range(30)
        ]
        report = compute_calibration(resolutions, min_resolved=30)
        assert report.sufficient_data is True

    def test_overall_pass_requires_all_thresholds(self):
        # Good accuracy and Brier, but bad separation
        resolutions = [
            Resolution(model_prob=0.51, market_prob=0.5, resolved_yes=True,
                       brier_component=0.24, correct_direction=True)
            for _ in range(30)
        ]
        report = compute_calibration(
            resolutions, min_resolved=30,
            accuracy_threshold=0.5,  # easy to pass
            brier_threshold=0.25,     # easy to pass
            separation_threshold=0.50, # hard to pass
        )
        assert report.overall_pass is False


# --- Calibration warning in scanner ---

class TestCalibrationWarning:
    def test_no_resolutions_warns(self, db, config):
        warning = get_calibration_warning(config, db)
        assert warning is not None
        assert "No backtest" in warning

    def test_insufficient_data_warns(self, db, config):
        # Need a valid contract for FK
        from database.models import Contract
        c = Contract(source="test", source_id="WARN-TEST", title="t", category="economics",
                     yes_price=0.5, volume_24h=100)
        c_id = db.upsert_contract(c)
        # Insert a few resolutions
        for i in range(5):
            db.insert_resolution(Resolution(
                contract_id=c_id, model_prob=0.6, market_prob=0.5,
                resolved_yes=True, brier_component=0.16, correct_direction=True,
            ))
        warning = get_calibration_warning(config, db)
        assert warning is not None
        assert "Only 5" in warning


# --- Backtester integration ---

class TestBacktester:
    def test_backtester_produces_resolutions(self, db, config):
        _seed_contracts(db, 10)
        bt = Backtester(db, config)
        resolutions = bt.run()
        assert len(resolutions) == 10

    def test_backtester_correct_direction(self, db, config):
        """Contracts with yes_price > 0.5 and resolution=True should be correct."""
        c = Contract(
            source="test", source_id="DIR-TEST",
            title="Direction test", category="economics",
            yes_price=0.80, volume_24h=10000,
            open_time=datetime(2025, 1, 1),
            close_time=datetime(2025, 6, 30),
            resolved=True, resolution=True,
            resolved_at=datetime(2025, 7, 1),
        )
        db.upsert_contract(c)
        bt = Backtester(db, config)
        resolutions = bt.run()
        assert len(resolutions) == 1
        assert resolutions[0].correct_direction is True

    def test_no_lookahead_uses_market_price_at_open(self, db, config):
        """The backtester uses yes_price (the listing price) as the market prior.
        It should NOT use any information that arrived after open_time."""
        c = Contract(
            source="test", source_id="LOOKAHEAD-TEST",
            title="Lookahead test", category="economics",
            yes_price=0.30,  # listing price is LOW
            volume_24h=10000,
            open_time=datetime(2025, 1, 1),
            close_time=datetime(2025, 6, 30),
            resolved=True, resolution=True,  # but it resolved YES
            resolved_at=datetime(2025, 7, 1),
        )
        db.upsert_contract(c)
        bt = Backtester(db, config)
        resolutions = bt.run()
        assert len(resolutions) == 1
        # Model should be near the listing price of 0.30, not near 1.0
        # (which would imply lookahead to the resolution)
        assert resolutions[0].model_prob < 0.50
