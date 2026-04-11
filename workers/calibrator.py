import math
import logging
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from config import CALIBRATION_STRETCH_FACTOR
except ImportError:
    CALIBRATION_STRETCH_FACTOR = 1.75


def _logit(p: float) -> float:
    return math.log(p / (1.0 - p))


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def logit_stretch(p: float, k: float = CALIBRATION_STRETCH_FACTOR) -> float:
    """
    Logit-space scaling transform.
    Converts p to logit space, scales by k, converts back.
    Preserves rank-ordering (monotonic) while increasing separation from 0.5.
    Bypasses already-confident signals (p > 0.85 or p < 0.15).
    Clamps output to [0.05, 0.95].
    """
    if p >= 0.85 or p <= 0.15:
        return float(max(0.05, min(0.95, p)))

    logit = _logit(p)
    scaled_logit = k * logit
    calibrated = _sigmoid(scaled_logit)
    return float(max(0.05, min(0.95, calibrated)))


def blend_with_base_rate(p: float, base_rate: float, weight: float = 0.15) -> float:
    """Blend prediction with category base rate."""
    return (1.0 - weight) * p + weight * base_rate


def calibrate(
    p_raw: float,
    base_rate: Optional[float] = None,
    base_rate_weight: float = 0.15,
    stretch_factor: float = CALIBRATION_STRETCH_FACTOR,
) -> float:
    """
    Primary calibration pipeline:
    1. Clamp input to a safe range.
    2. Optionally blend with base rate.
    3. Apply logit-space stretch transform.
    4. Final clamp to [0.05, 0.95].

    Args:
        p_raw: Raw probability in [0, 1].
        base_rate: Optional category base rate for anchoring.
        base_rate_weight: Weight given to base rate in blend (default 0.15).
        stretch_factor: Logit scaling factor k (default CALIBRATION_STRETCH_FACTOR).

    Returns:
        Calibrated probability in [0.05, 0.95].
    """
    p = float(max(1e-6, min(1.0 - 1e-6, p_raw)))

    if base_rate is not None:
        base_rate_clamped = float(max(0.01, min(0.99, base_rate)))
        p = blend_with_base_rate(p, base_rate_clamped, weight=base_rate_weight)
        logger.debug(
            "After base-rate blend (base_rate=%.3f, weight=%.2f): p=%.4f",
            base_rate_clamped,
            base_rate_weight,
            p,
        )

    p_stretched = logit_stretch(p, k=stretch_factor)
    logger.debug(
        "After logit stretch (k=%.2f): %.4f -> %.4f",
        stretch_factor,
        p,
        p_stretched,
    )

    return p_stretched


def adjust(
    p_raw: float,
    base_rate: Optional[float] = None,
    base_rate_weight: float = 0.15,
    stretch_factor: float = CALIBRATION_STRETCH_FACTOR,
) -> float:
    """Alias for calibrate() for backward compatibility."""
    return calibrate(
        p_raw,
        base_rate=base_rate,
        base_rate_weight=base_rate_weight,
        stretch_factor=stretch_factor,
    )