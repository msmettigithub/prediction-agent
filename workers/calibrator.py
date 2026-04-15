import math
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def extremize(p: float, k: float = 0.6) -> float:
    """
    Extremization transform: pushes probabilities away from 0.5.
    p_extreme = 0.5 + sign(p - 0.5) * |2*(p-0.5)|^k / 2
    k < 1 makes the transform push outward (extremize).
    k = 1 is identity. k > 1 would shrink toward 0.5.
    """
    p = float(p)
    delta = p - 0.5
    if delta == 0.0:
        return 0.5
    stretched = math.copysign(abs(2 * delta) ** k / 2, delta)
    return 0.5 + stretched


def select_k(agreeing_signal_count: int) -> float:
    """
    Select extremization strength based on number of agreeing signals.
    More agreeing signals -> more confident -> stronger extremization (lower k).
    """
    if agreeing_signal_count >= 3:
        return 0.5   # strong extremization
    elif agreeing_signal_count == 2:
        return 0.65  # moderate extremization
    else:
        return 0.85  # mild extremization


def calibrate(
    raw_prob: float,
    agreeing_signal_count: int = 1,
    k: Optional[float] = None,
    context: Optional[str] = None,
) -> float:
    """
    Calibrate a raw probability using extremization.

    Args:
        raw_prob: The aggregated probability before extremization. Should be in [0, 1].
        agreeing_signal_count: Number of signals agreeing on direction.
        k: Override for the extremization exponent. If None, selected from signal count.
        context: Optional label for logging (e.g. market id or symbol).

    Returns:
        Calibrated probability clamped to [0.05, 0.95].
    """
    raw_prob = float(raw_prob)

    # Clamp input to valid range before processing
    raw_prob_clamped = max(0.0, min(1.0, raw_prob))
    if raw_prob_clamped != raw_prob:
        logger.warning(
            "calibrate: raw_prob %.6f out of [0,1], clamped to %.6f",
            raw_prob,
            raw_prob_clamped,
        )
    raw_prob = raw_prob_clamped

    # Select k
    if k is None:
        k = select_k(agreeing_signal_count)

    pre_extremization = raw_prob

    # Apply extremization
    post_extremization = extremize(raw_prob, k=k)

    # Clamp output to safety range
    post_extremization_clamped = max(0.05, min(0.95, post_extremization))

    label = f"[{context}] " if context else ""
    logger.info(
        "%scalibrate: agreeing_signals=%d k=%.3f "
        "pre_extremization=%.6f post_extremization=%.6f clamped=%.6f",
        label,
        agreeing_signal_count,
        k,
        pre_extremization,
        post_extremization,
        post_extremization_clamped,
    )

    # RL feedback hook: emit structured log for downstream consumption
    logger.debug(
        "RL_FEEDBACK context=%s pre=%.6f post=%.6f k=%.3f signals=%d",
        context or "unknown",
        pre_extremization,
        post_extremization_clamped,
        k,
        agreeing_signal_count,
    )

    return post_extremization_clamped


def aggregate_and_calibrate(
    signals: list,
    context: Optional[str] = None,
) -> float:
    """
    Aggregate a list of probability signals (floats in [0,1]) by averaging,
    count how many agree on direction relative to 0.5, then apply extremization.

    Args:
        signals: List of float probabilities from individual models/features.
        context: Optional label for logging.

    Returns:
        Calibrated and extremized probability in [0.05, 0.95].
    """
    if not signals:
        logger.warning("aggregate_and_calibrate: empty signals list, returning 0.5")
        return 0.5

    signals_clamped = [max(0.0, min(1.0, float(s))) for s in signals]
    aggregated = sum(signals_clamped) / len(signals_clamped)

    # Count agreeing signals: signals on same side of 0.5 as aggregated mean
    direction_positive = aggregated >= 0.5
    if direction_positive:
        agreeing = sum(1 for s in signals_clamped if s >= 0.5)
    else:
        agreeing = sum(1 for s in signals_clamped if s < 0.5)

    label = f"[{context}] " if context else ""
    logger.info(
        "%saggregate_and_calibrate: n_signals=%d aggregated=%.6f "
        "direction_positive=%s agreeing=%d",
        label,
        len(signals_clamped),
        aggregated,
        direction_positive,
        agreeing,
    )

    return calibrate(
        raw_prob=aggregated,
        agreeing_signal_count=agreeing,
        context=context,
    )