import math
import logging
from typing import Optional

logger = logging.getLogger(__name__)

CALIBRATION_SHARPNESS = 1.75


def _sharpen(p: float, k: float = CALIBRATION_SHARPNESS) -> float:
    """
    Apply logit-space sharpening to push probabilities away from 0.5.

    Steps:
      1. Clip p to avoid log(0) or log(inf)
      2. Convert to logit space: logit = log(p / (1 - p))
      3. Stretch: stretched_logit = k * logit
      4. Convert back: p_sharp = 1 / (1 + exp(-stretched_logit))
      5. Clamp to [0.04, 0.96] to avoid extreme probabilities

    Monotonic transform: preserves rank ordering / accuracy.
    """
    p = float(p)
    p = max(1e-9, min(1.0 - 1e-9, p))

    logit = math.log(p / (1.0 - p))
    stretched_logit = k * logit
    p_sharp = 1.0 / (1.0 + math.exp(-stretched_logit))

    p_sharp = max(0.04, min(0.96, p_sharp))
    return p_sharp


def calibrate(
    raw_p: float,
    sharpness: Optional[float] = None,
) -> float:
    """
    Full calibration pipeline:
      1. Validate / clip raw probability
      2. (Placeholder for any isotonic / Platt scaling already in use)
      3. Apply logit-space sharpening
      4. Return final calibrated probability

    Args:
        raw_p:     Raw model output probability in [0, 1].
        sharpness: Override for CALIBRATION_SHARPNESS (k factor).
                   Pass None to use the module-level default.

    Returns:
        Calibrated, sharpened probability in [0.04, 0.96].
    """
    k = sharpness if sharpness is not None else CALIBRATION_SHARPNESS

    # --- Step 1: basic validation ---
    raw_p = float(raw_p)
    if not math.isfinite(raw_p):
        logger.warning(
            "calibrate() received non-finite raw_p=%s; defaulting to 0.5", raw_p
        )
        raw_p = 0.5

    raw_p = max(1e-9, min(1.0 - 1e-9, raw_p))

    # --- Step 2: existing calibration (identity pass-through placeholder) ---
    # Insert any Platt / isotonic scaling here if/when available.
    calibrated_p = raw_p

    # --- Step 3: logit-space sharpening ---
    p_sharp = _sharpen(calibrated_p, k=k)

    logger.info(
        "CALIBRATE: raw=%.6f -> calibrated=%.6f -> sharp=%.6f (k=%.3f)",
        raw_p,
        calibrated_p,
        p_sharp,
        k,
    )

    return p_sharp


def calibrate_batch(
    raw_probs: list,
    sharpness: Optional[float] = None,
) -> list:
    """
    Convenience wrapper: calibrate a list of raw probabilities.

    Args:
        raw_probs: Iterable of raw model probabilities.
        sharpness: Optional k override passed through to calibrate().

    Returns:
        List of calibrated, sharpened probabilities.
    """
    return [calibrate(p, sharpness=sharpness) for p in raw_probs]