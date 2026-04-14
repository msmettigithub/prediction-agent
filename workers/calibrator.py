import math
import logging

logger = logging.getLogger(__name__)

EXTREMIZE_POWER = 0.6
EXTREMIZE_ENABLED = True
EXTREMIZE_MIN_THRESHOLD = 0.03
FINAL_PROB_MIN = 0.05
FINAL_PROB_MAX = 0.95


def extremize_probability(p: float, k: float = EXTREMIZE_POWER) -> float:
    """
    Apply power-law extremization transform.
    p_ext = 0.5 + sign(p - 0.5) * (|2*(p-0.5)|^k) / 2

    Pushes confident predictions further from 0.5 to increase separation
    while preserving calibration direction. Only applied when |p - 0.5|
    exceeds EXTREMIZE_MIN_THRESHOLD to avoid amplifying noise on near-toss-up
    predictions.

    Args:
        p: Input probability in [0, 1]
        k: Power exponent, k < 1 extremizes (pushes toward extremes),
           k > 1 moderates (pulls toward 0.5)

    Returns:
        Extremized probability clamped to [0, 1]
    """
    p = float(p)
    p = max(0.0, min(1.0, p))

    deviation = p - 0.5

    if abs(deviation) <= EXTREMIZE_MIN_THRESHOLD:
        logger.debug(
            "Skipping extremization for p=%.4f (|deviation|=%.4f <= threshold=%.4f)",
            p, abs(deviation), EXTREMIZE_MIN_THRESHOLD
        )
        return p

    sign = 1.0 if deviation > 0 else -1.0
    abs_scaled = abs(2.0 * deviation)

    try:
        extremized_magnitude = math.pow(abs_scaled, k)
    except (ValueError, OverflowError) as e:
        logger.warning(
            "extremize_probability math error for p=%.4f, k=%.4f: %s. Returning original.",
            p, k, e
        )
        return p

    p_ext = 0.5 + sign * extremized_magnitude / 2.0

    p_ext = max(0.0, min(1.0, p_ext))

    logger.debug(
        "extremize_probability: p=%.4f -> p_ext=%.4f (k=%.2f, deviation=%.4f)",
        p, p_ext, k, deviation
    )

    return p_ext


def clamp_probability(p: float,
                      low: float = FINAL_PROB_MIN,
                      high: float = FINAL_PROB_MAX) -> float:
    """
    Clamp probability to [low, high] to prevent overconfidence.

    Args:
        p: Input probability
        low: Minimum allowed probability
        high: Maximum allowed probability

    Returns:
        Clamped probability
    """
    clamped = max(low, min(high, p))
    if clamped != p:
        logger.debug(
            "clamp_probability: clamped %.4f to %.4f (bounds=[%.2f, %.2f])",
            p, clamped, low, high
        )
    return clamped


def calibrate_probability(raw_prob: float,
                          extremize_enabled: bool = EXTREMIZE_ENABLED,
                          extremize_power: float = EXTREMIZE_POWER) -> float:
    """
    Full calibration pipeline for a raw model probability.

    Pipeline steps:
      1. Input validation and clamping to valid [0, 1] range
      2. Base calibration (placeholder for Platt scaling / isotonic regression
         or any learned calibration transform applied upstream)
      3. Extremization via power-law transform (conditional on flag and threshold)
      4. Final guardrail clamp to [FINAL_PROB_MIN, FINAL_PROB_MAX]

    Args:
        raw_prob: Raw probability from the model, expected in [0, 1]
        extremize_enabled: Whether to apply extremization step
        extremize_power: Exponent k for the extremization transform

    Returns:
        Calibrated probability in [FINAL_PROB_MIN, FINAL_PROB_MAX]
    """
    if not isinstance(raw_prob, (int, float)):
        logger.error(
            "calibrate_probability received non-numeric input: %r. Returning 0.5.",
            raw_prob
        )
        return 0.5

    p = float(raw_prob)

    if math.isnan(p) or math.isinf(p):
        logger.error(
            "calibrate_probability received nan/inf: %r. Returning 0.5.",
            raw_prob
        )
        return 0.5

    p = max(0.0, min(1.0, p))

    logger.debug("calibrate_probability: raw_prob=%.4f after initial clamp=%.4f", raw_prob, p)

    base_calibrated = p

    if extremize_enabled:
        extremized = extremize_probability(base_calibrated, k=extremize_power)
        logger.debug(
            "calibrate_probability: base_calibrated=%.4f -> extremized=%.4f",
            base_calibrated, extremized
        )
    else:
        extremized = base_calibrated
        logger.debug(
            "calibrate_probability: extremization disabled, keeping %.4f",
            base_calibrated
        )

    final = clamp_probability(extremized, low=FINAL_PROB_MIN, high=FINAL_PROB_MAX)

    logger.info(
        "calibrate_probability: %.4f -> base=%.4f -> extremized=%.4f -> final=%.4f "
        "(extremize_enabled=%s, k=%.2f)",
        raw_prob, base_calibrated, extremized, final,
        extremize_enabled, extremize_power
    )

    return final


def batch_calibrate(raw_probs: list,
                    extremize_enabled: bool = EXTREMIZE_ENABLED,
                    extremize_power: float = EXTREMIZE_POWER) -> list:
    """
    Apply calibration pipeline to a list of raw probabilities.

    Args:
        raw_probs: List of raw model probabilities
        extremize_enabled: Whether to apply extremization
        extremize_power: Exponent k for extremization

    Returns:
        List of calibrated probabilities
    """
    return [
        calibrate_probability(p, extremize_enabled=extremize_enabled,
                              extremize_power=extremize_power)
        for p in raw_probs
    ]