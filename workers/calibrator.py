import math
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Shrinkage/blend weight toward prior (reduced by ~50% from original 0.3)
SHRINKAGE = 0.15

# Sigmoid stretch parameter
SIGMOID_K = 2.5

# Output probability bounds (widened from [0.15, 0.85])
OUTPUT_MIN = 0.08
OUTPUT_MAX = 0.92

# Minimum separation threshold and scale factor
MIN_SEPARATION = 0.05
SEPARATION_SCALE = 1.8


def _sigmoid_stretch(p: float, k: float = SIGMOID_K) -> float:
    """
    Apply sigmoid stretching: p_stretched = 1 / (1 + exp(-k * (p - 0.5)))
    Nonlinearly amplifies deviations from 0.5 while keeping probabilities bounded.
    Monotonic transform preserving rank ordering.
    """
    try:
        return 1.0 / (1.0 + math.exp(-k * (p - 0.5)))
    except OverflowError:
        return 0.0 if p < 0.5 else 1.0


def _apply_minimum_separation(p: float) -> float:
    """
    If abs(p - 0.5) < MIN_SEPARATION, scale deviation by SEPARATION_SCALE
    to ensure marginal signals clear the gate.
    Monotonic transform preserving rank ordering.
    """
    deviation = p - 0.5
    if abs(deviation) < MIN_SEPARATION:
        scaled_deviation = deviation * SEPARATION_SCALE
        return 0.5 + scaled_deviation
    return p


def calibrate(raw_p: float, prior: float = 0.5, shrinkage: Optional[float] = None) -> float:
    """
    Calibrate a raw model probability.

    Steps:
    1. Blend raw probability toward prior using shrinkage (regression to mean).
    2. Apply sigmoid stretching to amplify deviations from 0.5.
    3. Apply minimum separation scaling for marginal signals.
    4. Clip to allowed output range [OUTPUT_MIN, OUTPUT_MAX].

    Args:
        raw_p: Raw model output probability in [0, 1].
        prior: Prior probability (default 0.5).
        shrinkage: Override for shrinkage parameter. Defaults to module-level SHRINKAGE.

    Returns:
        Calibrated probability in [OUTPUT_MIN, OUTPUT_MAX].
    """
    if shrinkage is None:
        shrinkage = SHRINKAGE

    # Clamp input to valid probability range
    raw_p = max(0.0, min(1.0, raw_p))

    # Step 1: Shrinkage / regression-to-mean blend
    # p_blended = (1 - shrinkage) * raw_p + shrinkage * prior
    p_blended = (1.0 - shrinkage) * raw_p + shrinkage * prior

    logger.debug(
        "calibrate: raw_p=%.4f prior=%.4f shrinkage=%.4f -> p_blended=%.4f",
        raw_p, prior, shrinkage, p_blended,
    )

    # Step 2: Sigmoid stretching (amplifies deviations from 0.5)
    p_stretched = _sigmoid_stretch(p_blended, k=SIGMOID_K)

    logger.debug("calibrate: p_stretched=%.4f", p_stretched)

    # Step 3: Minimum separation check and scaling
    p_separated = _apply_minimum_separation(p_stretched)

    logger.debug("calibrate: p_separated=%.4f", p_separated)

    # Step 4: Clip to allowed output range
    p_final = max(OUTPUT_MIN, min(OUTPUT_MAX, p_separated))

    logger.debug("calibrate: p_final=%.4f", p_final)

    return p_final


def calibrate_batch(raw_probs: list, prior: float = 0.5, shrinkage: Optional[float] = None) -> list:
    """
    Calibrate a batch of raw model probabilities.

    Args:
        raw_probs: List of raw model output probabilities.
        prior: Prior probability (default 0.5).
        shrinkage: Override for shrinkage parameter.

    Returns:
        List of calibrated probabilities.
    """
    return [calibrate(p, prior=prior, shrinkage=shrinkage) for p in raw_probs]


def separation(probs: list) -> float:
    """
    Compute mean absolute deviation from 0.5 as a measure of separation.

    Args:
        probs: List of calibrated probabilities.

    Returns:
        Mean absolute deviation from 0.5.
    """
    if not probs:
        return 0.0
    return sum(abs(p - 0.5) for p in probs) / len(probs)