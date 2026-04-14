import logging
import math

logger = logging.getLogger(__name__)

EXTREMIZE_FACTOR = 1.5
MIN_CONFIDENCE_TO_EXTREMIZE = 0.03


def extremize(p: float, alpha: float = EXTREMIZE_FACTOR) -> float:
    p = max(0.001, min(0.999, p))
    p_a = p ** alpha
    one_minus_p_a = (1.0 - p) ** alpha
    return p_a / (p_a + one_minus_p_a)


def maybe_extremize(p: float, alpha: float = EXTREMIZE_FACTOR) -> float:
    if abs(p - 0.5) <= MIN_CONFIDENCE_TO_EXTREMIZE:
        logger.debug(f"EXTREMIZE_SKIPPED: p={p:.4f} too close to 0.5 (threshold={MIN_CONFIDENCE_TO_EXTREMIZE})")
        return p
    p_ext = extremize(p, alpha)
    logger.info(f"EXTREMIZE_APPLIED: pre={p:.4f} post={p_ext:.4f} alpha={alpha} delta={p_ext - p:+.4f}")
    return p_ext


def calibrate(p: float) -> float:
    calibrated = _apply_existing_calibration(p)
    final = maybe_extremize(calibrated)
    logger.info(f"CALIBRATE_COMPLETE: raw={p:.4f} calibrated={calibrated:.4f} final={final:.4f}")
    return final


def _apply_existing_calibration(p: float) -> float:
    p = max(0.001, min(0.999, p))
    return p