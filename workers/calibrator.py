import logging
import numpy as np
from config import EXTREMIZATION_EXPONENT, EXTREMIZATION_MIN_THRESHOLD, CONFIDENCE_FLOOR

logger = logging.getLogger(__name__)


def extremize(p: float, alpha: float = None) -> float:
    """
    Apply Satopaa 2014 extremizing transform:
        p_ext = p^a / (p^a + (1-p)^a)

    This is monotonic and preserves ordering. With a > 1, probabilities
    are pushed away from 0.5, increasing separation on directional signals.
    """
    if alpha is None:
        alpha = EXTREMIZATION_EXPONENT

    p = float(np.clip(p, 1e-9, 1 - 1e-9))
    pa = p ** alpha
    one_minus_pa = (1.0 - p) ** alpha
    p_ext = pa / (pa + one_minus_pa)
    return float(p_ext)


def apply_confidence_floor(p: float, floor: float = None) -> float:
    """
    Clamp probability to [floor, 1-floor] to preserve hedging.
    No probability is pushed to an extreme that eliminates uncertainty entirely.
    """
    if floor is None:
        floor = CONFIDENCE_FLOOR
    return float(np.clip(p, floor, 1.0 - floor))


def calibrate(
    raw_prob: float,
    alpha: float = None,
    min_threshold: float = None,
    confidence_floor: float = None,
) -> float:
    """
    Full calibration pipeline with optional extremization.

    Steps:
      1. Clamp raw input to a safe range.
      2. Apply any existing weak calibration logic (identity for now,
         extensible).
      3. If the signal is directional enough (|p - 0.5| > min_threshold),
         apply extremization with exponent alpha.
      4. Clamp result to [confidence_floor, 1 - confidence_floor].
      5. Log pre- and post-extremization values for monitoring.

    Parameters
    ----------
    raw_prob : float
        Raw model probability in [0, 1].
    alpha : float, optional
        Extremization exponent. Defaults to EXTREMIZATION_EXPONENT from config.
    min_threshold : float, optional
        Minimum |p - 0.5| required to trigger extremization.
        Defaults to EXTREMIZATION_MIN_THRESHOLD from config.
    confidence_floor : float, optional
        Floor/ceiling for final clamped probability.
        Defaults to CONFIDENCE_FLOOR from config.

    Returns
    -------
    float
        Calibrated (and possibly extremized) probability.
    """
    if alpha is None:
        alpha = EXTREMIZATION_EXPONENT
    if min_threshold is None:
        min_threshold = EXTREMIZATION_MIN_THRESHOLD
    if confidence_floor is None:
        confidence_floor = CONFIDENCE_FLOOR

    # Step 1: safe clamp
    p = float(np.clip(raw_prob, 1e-9, 1 - 1e-9))

    # Step 2: existing calibration logic placeholder
    # Replace or extend this block with isotonic regression, Platt scaling, etc.
    p_calibrated = p  # currently identity; hook for future calibration

    # Step 3: extremization (only when signal is meaningfully directional)
    deviation = abs(p_calibrated - 0.5)
    if deviation > min_threshold:
        p_pre = p_calibrated
        p_post = extremize(p_calibrated, alpha=alpha)
        logger.info(
            "[CALIBRATOR] extremize applied: "
            "p_pre=%.6f  p_post=%.6f  alpha=%.4f  deviation=%.6f",
            p_pre,
            p_post,
            alpha,
            deviation,
        )
        p_calibrated = p_post
    else:
        logger.debug(
            "[CALIBRATOR] extremize skipped (|p-0.5|=%.6f <= threshold=%.6f): p=%.6f",
            deviation,
            min_threshold,
            p_calibrated,
        )

    # Step 4: confidence floor
    p_final = apply_confidence_floor(p_calibrated, floor=confidence_floor)

    if p_final != p_calibrated:
        logger.debug(
            "[CALIBRATOR] confidence_floor applied: p_before_floor=%.6f  p_final=%.6f  floor=%.4f",
            p_calibrated,
            p_final,
            confidence_floor,
        )

    return p_final


def calibrate_batch(
    raw_probs: list,
    alpha: float = None,
    min_threshold: float = None,
    confidence_floor: float = None,
) -> list:
    """
    Convenience wrapper to calibrate a list of raw probabilities.

    Parameters
    ----------
    raw_probs : list of float
        Raw model probabilities.
    alpha : float, optional
        Extremization exponent.
    min_threshold : float, optional
        Minimum deviation from 0.5 to trigger extremization.
    confidence_floor : float, optional
        Probability floor/ceiling after extremization.

    Returns
    -------
    list of float
        Calibrated probabilities in the same order.
    """
    return [
        calibrate(
            p,
            alpha=alpha,
            min_threshold=min_threshold,
            confidence_floor=confidence_floor,
        )
        for p in raw_probs
    ]