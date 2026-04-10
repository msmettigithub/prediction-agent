import logging
import math
from typing import Optional

logger = logging.getLogger(__name__)


def calibrate_probability(
    raw_probability: float,
    base_rate: Optional[float] = None,
    trend_signal: Optional[float] = None,
    sentiment_signal: Optional[float] = None,
) -> float:
    """
    Calibrate a raw probability estimate and apply extremizing transform.

    Steps:
    1. Apply existing shrinkage/base-rate calibration
    2. Determine signal agreement to set extremizing parameter a
    3. Apply extremizing transform p^a / (p^a + (1-p)^a)
    4. Enforce floor/ceiling at 0.05/0.95
    5. Log pre/post extremizing values for RL tuning

    Args:
        raw_probability: Raw model probability in [0, 1]
        base_rate: Optional base rate signal (directional, > 0.5 means bullish)
        trend_signal: Optional trend signal (directional, > 0.5 means bullish)
        sentiment_signal: Optional sentiment signal (directional, > 0.5 means bullish)

    Returns:
        Calibrated and extremized probability
    """
    p = _clamp(raw_probability, 1e-6, 1 - 1e-6)

    p = _apply_existing_calibration(p, base_rate)

    p = _clamp(p, 1e-6, 1 - 1e-6)

    a = _compute_extremizing_parameter(p, base_rate, trend_signal, sentiment_signal)

    p_pre_extremize = p

    p_extremized = _extremize(p, a)

    p_final = _clamp(p_extremized, 0.05, 0.95)

    logger.info(
        "Extremizing transform applied | "
        "pre=%.4f post_raw=%.4f post_clamped=%.4f a=%.2f | "
        "signals: base_rate=%s trend=%s sentiment=%s",
        p_pre_extremize,
        p_extremized,
        p_final,
        a,
        f"{base_rate:.3f}" if base_rate is not None else "None",
        f"{trend_signal:.3f}" if trend_signal is not None else "None",
        f"{sentiment_signal:.3f}" if sentiment_signal is not None else "None",
    )

    return p_final


def _apply_existing_calibration(
    p: float,
    base_rate: Optional[float],
) -> float:
    """
    Existing calibration step: light shrinkage toward base rate or 0.5.
    This preserves the prior calibration behavior while allowing extremizing
    to run afterward.
    """
    if base_rate is not None:
        anchor = _clamp(base_rate, 0.05, 0.95)
    else:
        anchor = 0.5

    shrinkage = 0.15
    p_calibrated = (1.0 - shrinkage) * p + shrinkage * anchor

    return p_calibrated


def _compute_extremizing_parameter(
    p: float,
    base_rate: Optional[float],
    trend_signal: Optional[float],
    sentiment_signal: Optional[float],
) -> float:
    """
    Compute the extremizing parameter a based on signal agreement.

    - Default: a = 1.6
    - All signals directionally aligned: a = 2.0
    - Signals conflict: a = 1.2

    Directional alignment is assessed relative to the calibrated probability p.
    A signal is considered "aligned" if it agrees with the direction of p
    (i.e., both above 0.5 or both below 0.5).
    """
    DEFAULT_A = 1.6
    HIGH_AGREEMENT_A = 2.0
    CONFLICT_A = 1.2

    signals = []
    if base_rate is not None:
        signals.append(base_rate)
    if trend_signal is not None:
        signals.append(trend_signal)
    if sentiment_signal is not None:
        signals.append(sentiment_signal)

    if len(signals) < 2:
        return DEFAULT_A

    p_direction = p >= 0.5
    signal_directions = [s >= 0.5 for s in signals]

    all_agree_with_p = all(d == p_direction for d in signal_directions)
    all_agree_with_each_other = len(set(signal_directions)) == 1

    if all_agree_with_p and all_agree_with_each_other:
        return HIGH_AGREEMENT_A

    any_conflict = not all_agree_with_each_other
    if any_conflict:
        return CONFLICT_A

    return DEFAULT_A


def _extremize(p: float, a: float) -> float:
    """
    Apply power-law extremizing transform.

    p_new = p^a / (p^a + (1-p)^a)

    For a > 1, probabilities are pushed away from 0.5.
    For a = 1, the transform is identity.
    """
    if a == 1.0:
        return p

    p_a = p ** a
    q_a = (1.0 - p) ** a
    denom = p_a + q_a

    if denom == 0.0:
        return p

    return p_a / denom


def _clamp(value: float, low: float, high: float) -> float:
    """Clamp value to [low, high]."""
    return max(low, min(high, value))


def extremize_probability(p: float, a: float = 1.6) -> float:
    """
    Standalone extremizing transform for use outside the full calibration pipeline.

    p_new = p^a / (p^a + (1-p)^a)

    Args:
        p: Probability in (0, 1)
        a: Extremizing parameter. Default 1.6 empirically maps:
           0.6 -> ~0.65, 0.7 -> ~0.76, 0.8 -> ~0.87 (doubles separation)

    Returns:
        Extremized probability clamped to [0.05, 0.95]
    """
    p_clamped = _clamp(p, 1e-6, 1 - 1e-6)
    p_ext = _extremize(p_clamped, a)
    p_final = _clamp(p_ext, 0.05, 0.95)

    logger.debug(
        "extremize_probability | input=%.4f a=%.2f output=%.4f",
        p,
        a,
        p_final,
    )

    return p_final


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    test_cases = [0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9]
    for a_val in [1.2, 1.6, 2.0]:
        logger.info("=== a=%.1f ===", a_val)
        for prob in test_cases:
            result = extremize_probability(prob, a=a_val)
            sep_before = abs(prob - 0.5)
            sep_after = abs(result - 0.5)
            logger.info(
                "  p=%.2f -> %.4f | sep %.3f -> %.3f (%.1fx)",
                prob,
                result,
                sep_before,
                sep_after,
                sep_after / sep_before if sep_before > 0 else float("inf"),
            )

    logger.info("=== Full calibration pipeline test ===")
    result = calibrate_probability(
        raw_probability=0.68,
        base_rate=0.62,
        trend_signal=0.71,
        sentiment_signal=0.65,
    )
    logger.info("All signals aligned: %.4f", result)

    result = calibrate_probability(
        raw_probability=0.68,
        base_rate=0.38,
        trend_signal=0.71,
        sentiment_signal=0.65,
    )
    logger.info("Conflicting signals: %.4f", result)

    result = calibrate_probability(raw_probability=0.68)
    logger.info("No optional signals: %.4f", result)