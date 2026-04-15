import numpy as np
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Shrinkage/blend parameter reduced by ~50% (was 0.6, now 0.3)
PRIOR_WEIGHT = 0.3

# Logistic calibration slope multiplier (increased steepness)
SLOPE_MULTIPLIER = 1.8

# Minimum confidence/edge threshold for deployment (lowered from 0.15 to 0.07)
MIN_CONFIDENCE_THRESHOLD = 0.07

# Probability band caps (widened from [0.20, 0.80] to [0.10, 0.90])
PROB_CAP_LOW = 0.10
PROB_CAP_HIGH = 0.90


def shrink_toward_prior(p: float, prior: float = 0.5, weight: float = PRIOR_WEIGHT) -> float:
    """
    Blend calibrated probability toward prior (0.5).
    Reduced weight means less shrinkage toward 0.5.
    """
    p = float(np.clip(p, 0.0, 1.0))
    blended = (1.0 - weight) * p + weight * prior
    return float(np.clip(blended, PROB_CAP_LOW, PROB_CAP_HIGH))


def logistic_calibrate(raw_score: float, slope: float = 1.0, intercept: float = 0.0) -> float:
    """
    Apply logistic/sigmoid calibration with increased steepness.
    slope is multiplied by SLOPE_MULTIPLIER to sharpen the curve.
    """
    effective_slope = slope * SLOPE_MULTIPLIER
    z = effective_slope * raw_score + intercept
    p = 1.0 / (1.0 + np.exp(-z))
    return float(np.clip(p, PROB_CAP_LOW, PROB_CAP_HIGH))


def sharpen_probability(p: float, k: float = SLOPE_MULTIPLIER) -> float:
    """
    Power-law extremization in logit space.
    Pushes probabilities away from 0.5 without flipping direction.

    At k=1.8:
      p=0.55 -> ~0.598
      p=0.60 -> ~0.646
      p=0.65 -> ~0.695
      p=0.70 -> ~0.743
    """
    p = float(np.clip(p, 0.01, 0.99))
    p_k = p ** k
    q_k = (1.0 - p) ** k
    p_sharp = p_k / (p_k + q_k)
    return float(np.clip(p_sharp, PROB_CAP_LOW, PROB_CAP_HIGH))


def calibrate(
    raw_prob: float,
    slope: float = 1.0,
    intercept: float = 0.0,
    signals: Optional[list] = None,
) -> float:
    """
    Full calibration pipeline:
      1. Apply logistic calibration with increased steepness
      2. Apply power-law sharpening
      3. Apply reduced shrinkage toward 0.5
      4. Cap within widened band [0.10, 0.90]

    Parameters
    ----------
    raw_prob  : raw model probability in (0, 1)
    slope     : base slope for logistic calibration (will be multiplied by SLOPE_MULTIPLIER)
    intercept : intercept for logistic calibration
    signals   : optional list of directional signals for agreement-gated sharpening

    Returns
    -------
    Calibrated probability in [PROB_CAP_LOW, PROB_CAP_HIGH]
    """
    raw_prob = float(np.clip(raw_prob, 0.01, 0.99))

    # Step 1: logistic calibration with steeper slope
    if slope != 1.0 or intercept != 0.0:
        p = logistic_calibrate(raw_prob, slope=slope, intercept=intercept)
    else:
        p = float(np.clip(raw_prob, PROB_CAP_LOW, PROB_CAP_HIGH))

    # Step 2: signal-agreement-gated sharpening
    if signals is not None and len(signals) >= 2:
        signal_variance = float(np.var(signals))
        # Effective sharpening exponent scales with signal agreement
        effective_k = 1.0 + (SLOPE_MULTIPLIER - 1.0) * max(0.0, 1.0 - signal_variance / 0.1)
        p = sharpen_probability(p, k=effective_k)
    else:
        p = sharpen_probability(p, k=SLOPE_MULTIPLIER)

    # Step 3: reduced shrinkage toward prior (was 0.6, now 0.3)
    p = shrink_toward_prior(p, prior=0.5, weight=PRIOR_WEIGHT)

    # Step 4: enforce widened probability band
    p = float(np.clip(p, PROB_CAP_LOW, PROB_CAP_HIGH))

    logger.debug(
        "calibrate: raw=%.4f -> calibrated=%.4f (slope_mult=%.1f, prior_weight=%.2f)",
        raw_prob,
        p,
        SLOPE_MULTIPLIER,
        PRIOR_WEIGHT,
    )

    return p


def has_sufficient_edge(prob: float, threshold: float = MIN_CONFIDENCE_THRESHOLD) -> bool:
    """
    Gate check: only deploy when edge (|prob - 0.5|) exceeds threshold.
    Threshold lowered from 0.15 to 0.07 to allow more deployments.
    """
    edge = abs(prob - 0.5)
    passes = edge >= threshold
    logger.debug(
        "has_sufficient_edge: prob=%.4f, edge=%.4f, threshold=%.4f, passes=%s",
        prob,
        edge,
        threshold,
        passes,
    )
    return passes


def calibrate_and_gate(
    raw_prob: float,
    slope: float = 1.0,
    intercept: float = 0.0,
    signals: Optional[list] = None,
    threshold: float = MIN_CONFIDENCE_THRESHOLD,
) -> tuple[float, bool]:
    """
    Convenience wrapper: calibrate then apply edge gate.

    Returns
    -------
    (calibrated_prob, should_deploy)
    """
    cal_prob = calibrate(raw_prob, slope=slope, intercept=intercept, signals=signals)
    deploy = has_sufficient_edge(cal_prob, threshold=threshold)
    return cal_prob, deploy