"""Combines base rate + modifiers → calibrated probability with confidence interval.

The model works in 3 steps:
1. Start from market price (the strongest available prior)
2. Apply modifier signals from real data (each shifts probability in log-odds space)
3. Clamp to [7%, 93%] floor/ceiling

CRITICAL: Without real data modifiers, the model should NOT deviate from market
price. Phantom edges from blending a flat base rate into market price are fake.
Only real signals (FRED data, BLS releases, implied vol, consensus estimates)
should create edge.
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

    Both backtest and live mode start from market price. The only thing
    that should move the estimate away from market is real signal —
    either from tool-derived modifiers (live) or static contract metadata
    (backtest, but conservatively).

    Range-bucket contracts (S&P 25pt bands) get a much lower base rate
    to prevent the old bug of inflating 5c contracts to 27c.
    """
    if config is None:
        from config import load_config
        config = load_config()
    if modifiers is None:
        modifiers = []

    source_id = getattr(contract, 'source_id', '') or ''
    base = get_base_rate(contract.category, source_id)

    market_prob = contract.yes_price
    if market_prob <= 0:
        market_prob = 0.01
    if market_prob >= 1:
        market_prob = 0.99

    # Always start from market price — it's the strongest prior we have
    log_odds = math.log(market_prob / (1 - market_prob))

    if backtest_mode:
        # Apply conservative static modifiers from contract metadata.
        # These should be SMALL adjustments, not a 40/60 blend with a
        # generic base rate.
        static_mods = _derive_static_modifiers(contract, base)
        for mod in static_mods:
            shift = mod.direction * mod.weight * 0.15  # reduced from 0.3
            log_odds += shift

    # Apply explicit modifiers (from real data tools) in log-odds space
    for mod in modifiers:
        shift = mod.direction * mod.weight * 0.5  # 0.5 log-odds max per modifier
        log_odds += shift

    # Convert back to probability
    raw_prob = 1.0 / (1.0 + math.exp(-log_odds))

    # Clamp to floor/ceiling
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
    These should be SMALL adjustments — not enough to generate tradeable edge
    on their own."""
    mods = []

    # Volume signal: thin markets may have more mispricing
    if contract.volume_24h > 50000:
        mods.append(Modifier(name="high_volume", direction=0.0, weight=0.0, source="static"))
    elif contract.volume_24h < 1000:
        # Thin market — slightly wider CI but no directional signal
        mods.append(Modifier(name="low_volume", direction=0.0, weight=0.0, source="static"))

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
