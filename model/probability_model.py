"""Combines base rate + modifiers → calibrated probability with confidence interval.

The model works in 3 steps:
1. Start with the category base rate
2. Apply modifier signals (each shifts the probability toward or away from YES)
3. Clamp to [7%, 93%] floor/ceiling — even "certain" events fail ~5-7% of the time
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from config import Config
from database.models import Contract
from model.base_rates import get_base_rate


@dataclass
class Modifier:
    """A single signal that adjusts the probability."""
    name: str
    direction: float    # positive = toward YES, negative = toward NO
    weight: float       # 0-1, how much to trust this signal
    source: str         # which tool produced this


@dataclass
class ProbabilityEstimate:
    probability: float
    confidence_interval: tuple[float, float]
    confidence: str     # "low", "medium", "high"
    base_rate: float
    modifiers_applied: list[Modifier] = field(default_factory=list)
    raw_probability: float = 0.0  # before clamping


def estimate_probability(
    contract: Contract,
    modifiers: list[Modifier] | None = None,
    config: Config | None = None,
    backtest_mode: bool = False,
) -> ProbabilityEstimate:
    """Produce a calibrated probability estimate for a contract.

    Uses log-odds space for combining signals to avoid probability compression
    at extremes (0.9 + small signal should still move meaningfully).

    In backtest_mode: uses a weighted blend of base rate + market price with
    static modifiers (close time, volume) to produce an independent signal.
    In live mode: starts from market price and applies tool-derived modifiers.
    """
    if config is None:
        from config import load_config
        config = load_config()
    if modifiers is None:
        modifiers = []

    base = get_base_rate(contract.category)

    market_prob = contract.yes_price
    if market_prob <= 0:
        market_prob = 0.01
    if market_prob >= 1:
        market_prob = 0.99

    if backtest_mode:
        # Backtest mode: blend base rate (independent) with market price (informative)
        # Weight: 40% base rate, 60% market — gives the model genuine separation
        # from pure market echo while still using market as a strong prior
        blend = 0.40 * base.base_rate + 0.60 * market_prob
        log_odds = math.log(blend / (1 - blend))

        # Apply static modifiers derivable from contract metadata
        static_mods = _derive_static_modifiers(contract, base)
        for mod in static_mods:
            shift = mod.direction * mod.weight * 0.3
            log_odds += shift
    else:
        # Live mode: start from market price, apply tool-derived modifiers
        log_odds = math.log(market_prob / (1 - market_prob))

    # Apply explicit modifiers in log-odds space
    for mod in modifiers:
        shift = mod.direction * mod.weight * 0.5  # 0.5 log-odds max per modifier
        log_odds += shift

    # Convert back to probability
    raw_prob = 1.0 / (1.0 + math.exp(-log_odds))

    # Clamp to floor/ceiling — enforced here, not in modifiers
    clamped_prob = max(config.model_prob_floor, min(config.model_prob_ceiling, raw_prob))

    # Confidence interval from base rate uncertainty + modifier disagreement
    ci_half = _compute_ci(base.uncertainty, modifiers)
    ci_low = max(config.model_prob_floor, clamped_prob - ci_half)
    ci_high = min(config.model_prob_ceiling, clamped_prob + ci_half)

    # Confidence level from CI width
    ci_width = ci_high - ci_low
    if ci_width < 0.15:
        confidence = "high"
    elif ci_width < 0.30:
        confidence = "medium"
    else:
        confidence = "low"

    return ProbabilityEstimate(
        probability=clamped_prob,
        confidence_interval=(ci_low, ci_high),
        confidence=confidence,
        base_rate=base.base_rate,
        modifiers_applied=modifiers,
        raw_probability=raw_prob,
    )


def _derive_static_modifiers(contract: Contract, base) -> list[Modifier]:
    """Derive modifiers from contract metadata only (no external data).
    Used in backtest mode to give the model signal beyond base rate echo."""
    mods = []
    from datetime import datetime

    # Time to close: closer contracts have more certainty
    if contract.close_time and contract.open_time:
        days_to_close = (contract.close_time - contract.open_time).days
        if days_to_close <= 7:
            # Very short horizon — market price is very informative, pull toward market
            mods.append(Modifier(name="short_horizon", direction=0, weight=0.0, source="static"))
        elif days_to_close <= 30:
            # Medium horizon — slight regression toward base rate
            mods.append(Modifier(name="medium_horizon", direction=-0.2, weight=0.3, source="static"))
        else:
            # Long horizon — more uncertainty, stronger regression to base rate
            mods.append(Modifier(name="long_horizon", direction=-0.3, weight=0.4, source="static"))

    # Volume signal: high volume = more informed market
    if contract.volume_24h > 50000:
        # Very liquid — trust market more (smaller base rate adjustment)
        mods.append(Modifier(name="high_volume", direction=0.1, weight=0.2, source="static"))
    elif contract.volume_24h < 1000:
        # Thin market — more room for mispricing, lean toward base rate
        mods.append(Modifier(name="low_volume", direction=-0.1, weight=0.3, source="static"))

    # Price extremity: extreme prices are less reliable than mid-range
    if contract.yes_price < 0.20 or contract.yes_price > 0.80:
        # Extreme prices tend to overshoot — slight regression toward 50%
        direction = 0.3 if contract.yes_price < 0.50 else -0.3
        mods.append(Modifier(name="extreme_price_regression", direction=direction, weight=0.2, source="static"))

    return mods


def _compute_ci(base_uncertainty: float, modifiers: list[Modifier]) -> float:
    """Compute confidence interval half-width.

    Starts with category uncertainty, narrows with agreeing modifiers,
    widens with disagreeing modifiers.
    """
    if not modifiers:
        return base_uncertainty

    # If modifiers agree in direction, CI narrows; if they disagree, CI widens
    directions = [m.direction for m in modifiers if m.weight > 0]
    if not directions:
        return base_uncertainty

    mean_direction = sum(directions) / len(directions)
    variance = sum((d - mean_direction) ** 2 for d in directions) / len(directions)

    # More modifiers = more info = narrower CI (diminishing returns)
    info_factor = 1.0 / (1.0 + 0.2 * len(modifiers))

    # High variance among modifiers = wider CI
    disagreement_factor = 1.0 + variance

    return base_uncertainty * info_factor * disagreement_factor
