import math
import logging
from config import CALIBRATION_EXTREMIZE_FACTOR

logger = logging.getLogger(__name__)

EXTREMIZE_MIN_CONFIDENCE = 0.03


def extremize(p: float, k: float) -> float:
    """
    Transform probability via log-odds scaling.
    1. Convert p to log-odds: logit = ln(p/(1-p))
    2. Multiply logit by extremization factor k > 1
    3. Convert back: p_new = 1/(1+exp(-k*logit))
    4. Clamp to [0.05, 0.95] for safety
    Only applied when |p - 0.5| > EXTREMIZE_MIN_CONFIDENCE.
    """
    p_clamped = max(1e-9, min(1 - 1e-9, p))
    confidence = abs(p_clamped - 0.5)

    if confidence <= EXTREMIZE_MIN_CONFIDENCE:
        logger.debug(
            f"EXTREMIZE_SKIP: p={p_clamped:.4f} confidence={confidence:.4f} "
            f"below threshold={EXTREMIZE_MIN_CONFIDENCE}"
        )
        return p_clamped

    logit = math.log(p_clamped / (1.0 - p_clamped))
    scaled_logit = k * logit
    p_new = 1.0 / (1.0 + math.exp(-scaled_logit))
    p_new = max(0.05, min(0.95, p_new))

    logger.debug(
        f"EXTREMIZE: p={p_clamped:.4f} logit={logit:.4f} "
        f"k={k} scaled_logit={scaled_logit:.4f} p_new={p_new:.4f}"
    )
    return p_new


def calibrate(p_raw: float) -> float:
    """
    Main calibration entry point.
    Applies extremization transform to push probabilities away from 0.5,
    exploiting the model's strong directional accuracy (80.4%) by increasing
    separation without degrading directional signal.
    """
    logger.critical(f"CALIBRATOR_HIT raw={p_raw:.4f}")

    k = CALIBRATION_EXTREMIZE_FACTOR

    if k <= 0:
        logger.warning(
            f"CALIBRATION_EXTREMIZE_FACTOR={k} is invalid (must be > 0), "
            f"skipping extremization"
        )
        return p_raw

    p_calibrated = extremize(p_raw, k)

    logger.critical(
        f"CALIBRATOR_RESULT raw={p_raw:.4f} calibrated={p_calibrated:.4f} "
        f"delta={p_calibrated - p_raw:+.4f} k={k}"
    )

    return p_calibrated


def calibrate_batch(probabilities: list) -> list:
    """
    Apply calibration to a list of raw probabilities.
    Returns list of calibrated probabilities in the same order.
    """
    return [calibrate(p) for p in probabilities]