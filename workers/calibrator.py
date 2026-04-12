import logging
import math

logger = logging.getLogger(__name__)


def extremize(p, a=1.5):
    """
    Apply extremizing transform: p_out = p^a / (p^a + (1-p)^a)

    Monotonic and symmetric around 0.5. Pushes probabilities away from 0.5,
    increasing separation while preserving rank ordering.

    Args:
        p: Input probability in [0, 1]
        a: Exponent parameter controlling strength of extremization (default 1.5)

    Returns:
        Transformed probability clipped to [0.05, 0.95]
    """
    FLOOR = 0.05
    CEILING = 0.95

    if not (0.0 <= p <= 1.0):
        logger.warning(f"extremize received out-of-range probability: {p}, clamping to [0,1]")
        p = max(0.0, min(1.0, p))

    if p <= 0.0:
        return FLOOR
    if p >= 1.0:
        return CEILING

    try:
        p_a = p ** a
        one_minus_p_a = (1.0 - p) ** a
        denom = p_a + one_minus_p_a
        if denom == 0.0:
            logger.warning(f"extremize denominator is zero for p={p}, a={a}, returning p unchanged")
            return p
        result = p_a / denom
    except (ValueError, ZeroDivisionError, OverflowError) as e:
        logger.warning(f"extremize computation error for p={p}, a={a}: {e}, returning p unchanged")
        return p

    result = max(FLOOR, min(CEILING, result))
    return result


def calibrate(raw_prob, signal_count=1, signal_agreement=1.0, extremize_exponent=1.5):
    """
    Calibrate a raw probability and apply extremizing transform.

    Args:
        raw_prob: Raw model probability in [0, 1]
        signal_count: Number of signals (unused currently, reserved for future weighting)
        signal_agreement: Float in [0, 1] representing fraction of signals in agreement
        extremize_exponent: Exponent for extremization transform (default 1.5)

    Returns:
        Final calibrated and extremized probability
    """
    if not (0.0 <= raw_prob <= 1.0):
        logger.warning(f"calibrate received out-of-range raw_prob: {raw_prob}, clamping")
        raw_prob = max(0.0, min(1.0, raw_prob))

    assert 0.0 <= raw_prob <= 1.0, f"Bad input to calibrate: {raw_prob}"

    pre_extremize = raw_prob

    a = extremize_exponent
    post_extremize = extremize(pre_extremize, a=a)

    logger.info(
        f"calibrate: raw={raw_prob:.4f} pre_extremize={pre_extremize:.4f} "
        f"post_extremize={post_extremize:.4f} "
        f"delta={post_extremize - pre_extremize:+.4f} "
        f"a={a:.2f} signal_count={signal_count} signal_agreement={signal_agreement:.3f}"
    )

    return post_extremize


def batch_calibrate(probabilities, extremize_exponent=1.5):
    """
    Calibrate a list of raw probabilities.

    Args:
        probabilities: List of raw probabilities
        extremize_exponent: Exponent for extremization

    Returns:
        List of calibrated probabilities
    """
    results = []
    for p in probabilities:
        results.append(calibrate(p, extremize_exponent=extremize_exponent))
    return results