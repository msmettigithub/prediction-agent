"""Computes edge (model - market), Kelly sizing, and position recommendation."""

from __future__ import annotations

from dataclasses import dataclass

from config import Config
from model.probability_model import ProbabilityEstimate


@dataclass
class EdgeResult:
    edge: float                 # model_prob - market_prob (positive = model thinks YES is underpriced)
    abs_edge: float             # |edge|
    kelly_fraction: float       # quarter-Kelly bet size as fraction of bankroll
    bet_amount: float           # kelly_fraction * bankroll, capped
    recommendation: str         # PASS, WATCH, BET_YES, BET_NO
    is_high_priority: bool      # cross-market divergence flag


def compute_edge(
    estimate: ProbabilityEstimate,
    market_prob: float,
    config: Config,
    cross_market_divergence: float | None = None,
) -> EdgeResult:
    """Compute edge and Kelly-optimal bet size.

    Quarter-Kelly with 5% bankroll hard cap:
    - Full Kelly maximizes log-wealth growth but assumes perfect probability estimates.
      We don't have perfect estimates.
    - Quarter-Kelly reduces bet-size variance by ~75% while still capturing ~50%
      of the theoretical growth rate (Thorp, 2006).
    - The 5% hard cap is a circuit breaker: even if Kelly says "bet 40% of bankroll",
      a single miscalibrated probability shouldn't risk ruin. At 5% max, you survive
      20 consecutive total losses before bankruptcy — enough runway to detect and
      fix calibration errors.
    """
    model_prob = estimate.probability
    edge = model_prob - market_prob
    abs_edge = abs(edge)

    # Kelly criterion: f* = (bp - q) / b
    # For binary markets at price p: b = (1/market_prob - 1), p = model_prob, q = 1 - model_prob
    # Simplifies to: f* = (model_prob - market_prob) / (1 - market_prob) for YES bets
    #                f* = (market_prob - model_prob) / market_prob for NO bets
    if edge > 0:
        # Model thinks YES is underpriced
        odds = (1.0 / market_prob) - 1.0 if market_prob > 0 else 0
        kelly_full = (model_prob * odds - (1 - model_prob)) / odds if odds > 0 else 0
    elif edge < 0:
        # Model thinks NO is underpriced
        no_market = 1.0 - market_prob
        odds = (1.0 / no_market) - 1.0 if no_market > 0 else 0
        no_model = 1.0 - model_prob
        kelly_full = (no_model * odds - model_prob) / odds if odds > 0 else 0
    else:
        kelly_full = 0

    # Quarter-Kelly
    kelly_quarter = max(0, kelly_full * config.kelly_fraction)

    # Hard cap at 5% of bankroll
    kelly_capped = min(kelly_quarter, config.kelly_max_bet_pct)

    bet_amount = kelly_capped * config.bankroll

    # Recommendation logic
    is_high_priority = False
    if cross_market_divergence is not None and cross_market_divergence > config.cross_market_divergence_pp:
        is_high_priority = True

    if abs_edge < config.edge_threshold and not is_high_priority:
        recommendation = "PASS"
    elif abs_edge < config.edge_threshold and is_high_priority:
        recommendation = "WATCH"
    elif edge > 0:
        recommendation = "BET_YES"
    else:
        recommendation = "BET_NO"

    # Downgrade to WATCH if confidence is low
    if estimate.confidence == "low" and recommendation in ("BET_YES", "BET_NO"):
        recommendation = "WATCH"

    return EdgeResult(
        edge=edge,
        abs_edge=abs_edge,
        kelly_fraction=kelly_capped,
        bet_amount=bet_amount,
        recommendation=recommendation,
        is_high_priority=is_high_priority,
    )
