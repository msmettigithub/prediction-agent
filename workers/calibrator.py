import logging
import math
from typing import Optional

logger = logging.getLogger(__name__)


def extremize(p: float, alpha: float) -> float:
    """
    Power-law extremizer: p_extreme = p^alpha / (p^alpha + (1-p)^alpha)
    alpha > 1 pushes probabilities away from 0.5.
    alpha = 1.0 is identity transform.
    """
    if alpha == 1.0:
        return p
    p_clamped = max(1e-9, min(1 - 1e-9, p))
    p_a = p_clamped ** alpha
    q_a = (1.0 - p_clamped) ** alpha
    return p_a / (p_a + q_a)


def compute_signal_agreement(signals: dict) -> int:
    """
    Given a dict of named signal values (probabilities or directional floats),
    count how many independent signals agree in direction relative to 0.5.
    Returns the count of signals pointing in the majority direction.
    """
    if not signals:
        return 0

    above = sum(1 for v in signals.values() if v is not None and v > 0.5)
    below = sum(1 for v in signals.values() if v is not None and v < 0.5)
    agreeing = max(above, below)
    return agreeing


def get_alpha_for_agreement(agreeing_count: int) -> float:
    """
    Map signal agreement count to extremizing alpha.
    3+ signals agree -> alpha=1.8 (strong push)
    2 signals agree  -> alpha=1.3 (moderate push)
    <2 signals agree -> alpha=1.0 (no change)
    """
    if agreeing_count >= 3:
        return 1.8
    elif agreeing_count == 2:
        return 1.3
    else:
        return 1.0


FLOOR = 0.08
CEILING = 0.92


def recalibrate(
    p_raw: float,
    base_rate: Optional[float] = None,
    trend: Optional[float] = None,
    sentiment: Optional[float] = None,
    market_momentum: Optional[float] = None,
    question_id: Optional[str] = None,
) -> float:
    """
    Full recalibration pipeline with extremizing step.

    Steps:
    1. Collect available signals.
    2. Compute signal agreement count.
    3. Select alpha based on agreement tier.
    4. Apply power-law extremizer.
    5. Clamp to [FLOOR, CEILING].
    6. Log pre/post probabilities and confidence tier for RL tracking.

    Returns the recalibrated probability.
    """
    signals = {
        "base_rate": base_rate,
        "trend": trend,
        "sentiment": sentiment,
        "market_momentum": market_momentum,
    }

    available_signals = {k: v for k, v in signals.items() if v is not None}

    agreeing_count = compute_signal_agreement(available_signals)
    alpha = get_alpha_for_agreement(agreeing_count)

    if agreeing_count >= 3:
        confidence_tier = "HIGH"
    elif agreeing_count == 2:
        confidence_tier = "MEDIUM"
    else:
        confidence_tier = "LOW"

    p_extremized = extremize(p_raw, alpha)

    p_final = max(FLOOR, min(CEILING, p_extremized))

    logger.info(
        "recalibrate | question_id=%s | p_raw=%.4f | p_extremized=%.4f | "
        "p_final=%.4f | alpha=%.2f | agreeing_signals=%d | confidence_tier=%s | "
        "signals=%s",
        question_id or "unknown",
        p_raw,
        p_extremized,
        p_final,
        alpha,
        agreeing_count,
        confidence_tier,
        {k: round(v, 4) for k, v in available_signals.items()},
    )

    return p_final


def calibrate_probability(
    p_raw: float,
    signals: Optional[dict] = None,
    question_id: Optional[str] = None,
) -> float:
    """
    Convenience wrapper that accepts a signals dict with keys:
    base_rate, trend, sentiment, market_momentum.
    Falls back gracefully if signals is None or missing keys.
    """
    if signals is None:
        signals = {}

    return recalibrate(
        p_raw=p_raw,
        base_rate=signals.get("base_rate"),
        trend=signals.get("trend"),
        sentiment=signals.get("sentiment"),
        market_momentum=signals.get("market_momentum"),
        question_id=question_id,
    )