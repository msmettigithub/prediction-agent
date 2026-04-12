workers/calibrator.py

import math
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def compute_signal_strength(
    raw_p: float,
    base_rate: Optional[float] = None,
    trend_direction: Optional[float] = None,
    sentiment_direction: Optional[float] = None,
    market_movement: Optional[float] = None,
) -> float:
    """
    Count how many signals agree with the raw probability direction (above/below 0.5).
    Returns a value in [0, 1] representing fraction of available signals that corroborate.
    """
    direction = 1 if raw_p >= 0.5 else -1
    corroborating = 0
    total = 0

    if base_rate is not None:
        total += 1
        base_direction = 1 if base_rate >= 0.5 else -1
        if base_direction == direction:
            corroborating += 1

    if trend_direction is not None:
        total += 1
        trend_dir = 1 if trend_direction >= 0 else -1
        if trend_dir == direction:
            corroborating += 1

    if sentiment_direction is not None:
        total += 1
        sent_dir = 1 if sentiment_direction >= 0 else -1
        if sent_dir == direction:
            corroborating += 1

    if market_movement is not None:
        total += 1
        mkt_dir = 1 if market_movement >= 0 else -1
        if mkt_dir == direction:
            corroborating += 1

    if total == 0:
        return 0.5

    return corroborating / total


def calibrate(
    raw_p: float,
    base_rate: Optional[float] = None,
    trend_direction: Optional[float] = None,
    sentiment_direction: Optional[float] = None,
    market_movement: Optional[float] = None,
    shrinkage_base: float = 0.3,
    floor: float = 0.08,
    ceiling: float = 0.92,
    contract_id: Optional[str] = None,
) -> float:
    """
    Calibrate a raw probability with signal-strength-dependent shrinkage.

    Formula:
        calibrated_p = 0.5 + (raw_p - 0.5) * (1 - shrinkage_base * (1 - signal_strength))

    When all signals agree (signal_strength=1.0), shrinkage factor becomes 0 and raw_p stands.
    When signals are mixed (signal_strength=0.0), shrinkage factor is shrinkage_base (~0.3).

    Output is clamped to [floor, ceiling] = [0.08, 0.92].
    """
    raw_p = max(0.0, min(1.0, raw_p))

    signal_strength = compute_signal_strength(
        raw_p=raw_p,
        base_rate=base_rate,
        trend_direction=trend_direction,
        sentiment_direction=sentiment_direction,
        market_movement=market_movement,
    )

    effective_shrinkage = shrinkage_base * (1.0 - signal_strength)

    calibrated_p = 0.5 + (raw_p - 0.5) * (1.0 - effective_shrinkage)

    calibrated_p = max(floor, min(ceiling, calibrated_p))

    label = contract_id if contract_id else "unknown"
    logger.info(
        "calibrator | contract=%s raw_p=%.4f signal_strength=%.4f "
        "shrinkage_base=%.3f effective_shrinkage=%.4f calibrated_p=%.4f",
        label,
        raw_p,
        signal_strength,
        shrinkage_base,
        effective_shrinkage,
        calibrated_p,
    )

    return calibrated_p


def extremize(p: float, k: float = 1.7) -> float:
    """
    Logit-space sharpening transform. k=1.7 maps:
      0.60 -> ~0.67, 0.65 -> ~0.74, 0.70 -> ~0.81
    Use after calibrate() if additional separation is desired.
    """
    p = max(0.01, min(0.99, p))
    logit = math.log(p / (1.0 - p))
    sharpened_logit = logit * k
    return 1.0 / (1.0 + math.exp(-sharpened_logit))


def calibrate_and_extremize(
    raw_p: float,
    base_rate: Optional[float] = None,
    trend_direction: Optional[float] = None,
    sentiment_direction: Optional[float] = None,
    market_movement: Optional[float] = None,
    shrinkage_base: float = 0.3,
    floor: float = 0.08,
    ceiling: float = 0.92,
    extremize_k: float = 1.7,
    contract_id: Optional[str] = None,
) -> float:
    """
    Full pipeline: calibrate with signal-strength shrinkage, then extremize in logit space.
    """
    cal_p = calibrate(
        raw_p=raw_p,
        base_rate=base_rate,
        trend_direction=trend_direction,
        sentiment_direction=sentiment_direction,
        market_movement=market_movement,
        shrinkage_base=shrinkage_base,
        floor=floor,
        ceiling=ceiling,
        contract_id=contract_id,
    )

    ext_p = extremize(cal_p, k=extremize_k)

    ext_p = max(floor, min(ceiling, ext_p))

    logger.info(
        "calibrator | contract=%s post_extremize_p=%.4f (k=%.2f)",
        contract_id if contract_id else "unknown",
        ext_p,
        extremize_k,
    )

    return ext_p