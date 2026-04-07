"""Tests for the probability model in isolation."""

from __future__ import annotations

import math

import pytest

from database.models import Contract
from model.base_rates import get_base_rate, BASE_RATES
from model.probability_model import estimate_probability, Modifier, _compute_ci
from model.edge_calculator import compute_edge
from config import Config


# --- Base rate anchoring ---

class TestBaseRateAnchoring:
    def test_known_categories_have_base_rates(self):
        for cat in ("politics", "economics", "crypto", "sports", "science", "legal"):
            br = get_base_rate(cat)
            assert br.category == cat
            assert 0.0 < br.base_rate < 1.0

    def test_unknown_category_returns_default(self):
        br = get_base_rate("underwater_basket_weaving")
        assert br.category == "unknown"
        assert br.base_rate == 0.50

    def test_output_near_base_rate_with_no_modifiers(self):
        """With market price near base rate and no modifiers, output should stay close."""
        config = Config(mock_tools=True)
        for cat in ("politics", "economics", "crypto", "sports", "science", "legal"):
            br = get_base_rate(cat)
            contract = Contract(
                source="test", source_id="t", title="test",
                category=cat, yes_price=br.base_rate,
            )
            est = estimate_probability(contract, modifiers=[], config=config)
            # With market price == base rate, output should be very close to both
            assert abs(est.probability - br.base_rate) < 0.05


# --- Modifier application ---

class TestModifierApplication:
    def test_positive_modifier_increases_probability(self):
        config = Config(mock_tools=True)
        contract = Contract(
            source="test", source_id="t", title="test",
            category="economics", yes_price=0.50,
        )
        no_mod = estimate_probability(contract, modifiers=[], config=config)
        with_mod = estimate_probability(contract, modifiers=[
            Modifier(name="test", direction=1.0, weight=0.8, source="test_tool"),
        ], config=config)
        assert with_mod.probability > no_mod.probability

    def test_negative_modifier_decreases_probability(self):
        config = Config(mock_tools=True)
        contract = Contract(
            source="test", source_id="t", title="test",
            category="economics", yes_price=0.50,
        )
        no_mod = estimate_probability(contract, modifiers=[], config=config)
        with_mod = estimate_probability(contract, modifiers=[
            Modifier(name="test", direction=-1.0, weight=0.8, source="test_tool"),
        ], config=config)
        assert with_mod.probability < no_mod.probability

    def test_modifier_works_in_log_odds_not_linear(self):
        """A +0.12 modifier on a 0.5 base should NOT just add 0.12 linearly."""
        config = Config(mock_tools=True)
        contract = Contract(
            source="test", source_id="t", title="test",
            category="economics", yes_price=0.50,
        )
        mod = Modifier(name="test", direction=1.0, weight=0.5, source="test")
        est = estimate_probability(contract, modifiers=[mod], config=config)
        # In log-odds: 0.5 -> logit=0 -> add 0.25 -> sigmoid(0.25) ≈ 0.562
        # Should NOT be exactly 0.5 + 0.12 = 0.62
        assert est.probability != pytest.approx(0.62, abs=0.01)
        # Should be somewhere in sigmoid range
        assert 0.50 < est.probability < 0.70

    def test_multiple_opposing_modifiers_partially_cancel(self):
        config = Config(mock_tools=True)
        contract = Contract(
            source="test", source_id="t", title="test",
            category="economics", yes_price=0.50,
        )
        mods = [
            Modifier(name="bull", direction=1.0, weight=0.5, source="test"),
            Modifier(name="bear", direction=-1.0, weight=0.5, source="test"),
        ]
        est = estimate_probability(contract, modifiers=mods, config=config)
        # Opposing modifiers should leave probability near 0.50
        assert abs(est.probability - 0.50) < 0.05


# --- Probability caps ---

class TestProbabilityCaps:
    def test_floor_enforced(self):
        """Even with extremely bearish modifiers, probability never goes below 0.07."""
        config = Config(mock_tools=True)
        contract = Contract(
            source="test", source_id="t", title="test",
            category="economics", yes_price=0.05,
        )
        mods = [Modifier(name=f"bear{i}", direction=-1.0, weight=1.0, source="test") for i in range(5)]
        est = estimate_probability(contract, modifiers=mods, config=config)
        assert est.probability >= 0.07

    def test_ceiling_enforced(self):
        """Even with extremely bullish modifiers, probability never exceeds 0.93."""
        config = Config(mock_tools=True)
        contract = Contract(
            source="test", source_id="t", title="test",
            category="economics", yes_price=0.95,
        )
        mods = [Modifier(name=f"bull{i}", direction=1.0, weight=1.0, source="test") for i in range(5)]
        est = estimate_probability(contract, modifiers=mods, config=config)
        assert est.probability <= 0.93

    def test_custom_caps_respected(self):
        config = Config(mock_tools=True, model_prob_floor=0.10, model_prob_ceiling=0.90)
        contract = Contract(
            source="test", source_id="t", title="test",
            category="economics", yes_price=0.02,
        )
        est = estimate_probability(contract, modifiers=[], config=config)
        assert est.probability >= 0.10


# --- CI width ---

class TestConfidenceInterval:
    def test_more_disagreement_wider_ci(self):
        """Modifiers that disagree should produce a wider CI than modifiers that agree."""
        agreeing = [
            Modifier(name="a", direction=1.0, weight=0.5, source="t"),
            Modifier(name="b", direction=1.0, weight=0.5, source="t"),
            Modifier(name="c", direction=1.0, weight=0.5, source="t"),
        ]
        disagreeing = [
            Modifier(name="a", direction=1.0, weight=0.5, source="t"),
            Modifier(name="b", direction=-1.0, weight=0.5, source="t"),
            Modifier(name="c", direction=1.0, weight=0.5, source="t"),
        ]
        ci_agree = _compute_ci(0.15, agreeing)
        ci_disagree = _compute_ci(0.15, disagreeing)
        assert ci_disagree > ci_agree

    def test_more_modifiers_narrower_base_ci(self):
        """More modifiers (even without direction data) narrows the base CI."""
        few = [Modifier(name="a", direction=1.0, weight=0.5, source="t")]
        many = [Modifier(name=f"m{i}", direction=1.0, weight=0.5, source="t") for i in range(5)]
        ci_few = _compute_ci(0.15, few)
        ci_many = _compute_ci(0.15, many)
        assert ci_many < ci_few

    def test_no_modifiers_returns_base_uncertainty(self):
        ci = _compute_ci(0.15, [])
        assert ci == 0.15


# --- Determinism ---

class TestDeterminism:
    def test_same_inputs_same_output(self):
        config = Config(mock_tools=True)
        contract = Contract(
            source="test", source_id="t", title="test",
            category="economics", yes_price=0.65,
        )
        mods = [Modifier(name="test", direction=0.5, weight=0.3, source="test")]
        results = [estimate_probability(contract, modifiers=mods, config=config) for _ in range(10)]
        probs = [r.probability for r in results]
        assert all(p == probs[0] for p in probs), "Probability model is non-deterministic"


# --- Edge calculator ---

class TestEdgeCalculator:
    def test_positive_edge_recommends_bet_yes(self):
        config = Config(mock_tools=True, edge_threshold=0.08)
        contract = Contract(
            source="test", source_id="t", title="test",
            category="economics", yes_price=0.40,
        )
        mods = [Modifier(name="bull", direction=1.0, weight=1.0, source="t")]
        est = estimate_probability(contract, modifiers=mods, config=config)
        er = compute_edge(est, 0.40, config)
        if er.abs_edge >= 0.08 and est.confidence != "low":
            assert er.recommendation == "BET_YES"

    def test_kelly_capped_at_5_percent(self):
        config = Config(mock_tools=True)
        contract = Contract(
            source="test", source_id="t", title="test",
            category="economics", yes_price=0.20,
        )
        # Huge edge to force large Kelly
        mods = [Modifier(name=f"bull{i}", direction=1.0, weight=1.0, source="t") for i in range(3)]
        est = estimate_probability(contract, modifiers=mods, config=config)
        er = compute_edge(est, 0.20, config)
        assert er.kelly_fraction <= 0.05

    def test_below_threshold_is_pass(self):
        config = Config(mock_tools=True, edge_threshold=0.08)
        contract = Contract(
            source="test", source_id="t", title="test",
            category="economics", yes_price=0.50,
        )
        est = estimate_probability(contract, modifiers=[], config=config)
        er = compute_edge(est, 0.50, config)
        # With no modifiers at market price, edge should be tiny -> PASS
        assert er.recommendation == "PASS"
