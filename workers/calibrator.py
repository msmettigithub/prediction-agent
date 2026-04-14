import logging
import numpy as np

logger = logging.getLogger(__name__)


def extremize_probability(p: float, alpha: float = 1.5) -> float:
    """
    Extremization transform: p_ext = p^alpha / (p^alpha + (1-p)^alpha)

    For alpha > 1, pushes probabilities away from 0.5.
    For alpha = 1, identity transform.
    For alpha < 1, shrinks toward 0.5.

    Examples with alpha=1.5:
      p=0.65 -> ~0.72
      p=0.35 -> ~0.28
      p=0.50 -> 0.50 (fixed point)
    """
    p = float(np.clip(p, 1e-9, 1 - 1e-9))
    p_alpha = p ** alpha
    q_alpha = (1.0 - p) ** alpha
    p_ext = p_alpha / (p_alpha + q_alpha)
    return float(p_ext)


def calibrate(
    raw_probability: float,
    base_rate: float = 0.5,
    base_rate_weight: float = 0.15,
    extremize_alpha: float = 1.5,
    extremize_min_confidence: float = 0.03,
    output_clamp_low: float = 0.05,
    output_clamp_high: float = 0.95,
) -> float:
    """
    Full calibration pipeline:

    1. Clamp raw input to avoid degenerate values.
    2. Bayesian shrinkage toward base rate.
    3. Extremization step (if |p - 0.5| > extremize_min_confidence).
    4. Clamp output to [output_clamp_low, output_clamp_high].

    Args:
        raw_probability: Model's raw probability estimate in [0, 1].
        base_rate: Historical base rate for this question type.
        base_rate_weight: Weight applied to base rate in Bayesian blend (0 = no shrinkage).
        extremize_alpha: Exponent for the extremization transform (>1 pushes away from 0.5).
        extremize_min_confidence: Minimum |p - 0.5| required to apply extremization.
                                  Prevents amplifying noise on truly uncertain questions.
        output_clamp_low: Lower bound for final output probability.
        output_clamp_high: Upper bound for final output probability.

    Returns:
        Calibrated probability in [output_clamp_low, output_clamp_high].
    """
    # --- Stage 0: Sanitize input ---
    p = float(np.clip(raw_probability, 1e-6, 1 - 1e-6))
    logger.debug("calibrate: raw_probability=%.6f (clamped=%.6f)", raw_probability, p)

    # --- Stage 1: Bayesian shrinkage toward base rate ---
    base_rate = float(np.clip(base_rate, 1e-6, 1 - 1e-6))
    p_shrunk = (1.0 - base_rate_weight) * p + base_rate_weight * base_rate
    logger.debug(
        "calibrate: after shrinkage p=%.6f (base_rate=%.4f, weight=%.4f)",
        p_shrunk,
        base_rate,
        base_rate_weight,
    )

    # --- Stage 2: Extremization ---
    p_before_ext = p_shrunk
    deviation = abs(p_before_ext - 0.5)

    if deviation > extremize_min_confidence:
        p_ext = extremize_probability(p_before_ext, alpha=extremize_alpha)
        logger.info(
            "calibrate: extremization applied (|p-0.5|=%.4f > threshold=%.4f): "
            "%.6f -> %.6f (alpha=%.3f)",
            deviation,
            extremize_min_confidence,
            p_before_ext,
            p_ext,
            extremize_alpha,
        )
    else:
        p_ext = p_before_ext
        logger.info(
            "calibrate: extremization skipped (|p-0.5|=%.4f <= threshold=%.4f): p=%.6f",
            deviation,
            extremize_min_confidence,
            p_before_ext,
        )

    # --- Stage 3: Clamp output ---
    p_final = float(np.clip(p_ext, output_clamp_low, output_clamp_high))
    logger.info(
        "calibrate: final probability=%.6f (pre-clamp=%.6f, clamp=[%.2f, %.2f])",
        p_final,
        p_ext,
        output_clamp_low,
        output_clamp_high,
    )

    return p_final


def batch_calibrate(
    probabilities: list,
    base_rate: float = 0.5,
    base_rate_weight: float = 0.15,
    extremize_alpha: float = 1.5,
    extremize_min_confidence: float = 0.03,
    output_clamp_low: float = 0.05,
    output_clamp_high: float = 0.95,
) -> list:
    """
    Apply calibrate() to a list of raw probabilities.

    Returns a list of calibrated probabilities in the same order.
    """
    results = []
    for i, p in enumerate(probabilities):
        try:
            cal = calibrate(
                raw_probability=p,
                base_rate=base_rate,
                base_rate_weight=base_rate_weight,
                extremize_alpha=extremize_alpha,
                extremize_min_confidence=extremize_min_confidence,
                output_clamp_low=output_clamp_low,
                output_clamp_high=output_clamp_high,
            )
        except Exception as exc:
            logger.error("batch_calibrate: error at index %d (p=%s): %s", i, p, exc)
            cal = 0.5
        results.append(cal)

    raw_arr = np.array(probabilities, dtype=float)
    cal_arr = np.array(results, dtype=float)
    logger.info(
        "batch_calibrate: n=%d  raw mean=%.4f std=%.4f  "
        "calibrated mean=%.4f std=%.4f  mean_shift=%.4f",
        len(probabilities),
        float(np.nanmean(raw_arr)),
        float(np.nanstd(raw_arr)),
        float(np.nanmean(cal_arr)),
        float(np.nanstd(cal_arr)),
        float(np.nanmean(np.abs(cal_arr - 0.5)) - np.nanmean(np.abs(raw_arr - 0.5))),
    )

    return results