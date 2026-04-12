import math
import logging
from workers.config import CALIBRATION_SHARPNESS

logger = logging.getLogger(__name__)


def _logit(p: float) -> float:
    return math.log(p / (1.0 - p))


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def calibrate(raw_prob: float, k: float = None) -> float:
    """
    Calibrate a raw probability using a logit-space sharpening transform.

    Steps:
      1. Clamp raw_prob to (0.001, 0.999) for numerical safety.
      2. Compute logit = ln(p / (1 - p)).
      3. Multiply logit by sharpening factor k (default CALIBRATION_SHARPNESS).
      4. Convert back via sigmoid: p_sharp = 1 / (1 + exp(-k * logit)).
      5. Clamp result to [0.03, 0.97] to avoid extreme values.

    Args:
        raw_prob: Raw model probability in (0, 1).
        k: Sharpening factor. Values > 1 push probabilities away from 0.5.
           Defaults to CALIBRATION_SHARPNESS from config.

    Returns:
        Sharpened and clamped calibrated probability.
    """
    if k is None:
        k = CALIBRATION_SHARPNESS

    # Step 1: clamp for numerical safety before logit
    p = max(0.001, min(0.999, raw_prob))

    # Step 2: logit transform
    logit_p = _logit(p)

    # Step 3: apply sharpening in logit space
    sharpened_logit = k * logit_p

    # Step 4: convert back to probability space
    p_sharp = _sigmoid(sharpened_logit)

    # Step 5: clamp to [0.03, 0.97]
    p_sharp = max(0.03, min(0.97, p_sharp))

    # Step 6: log both raw and sharpened for monitoring
    logger.debug(
        "calibrate: raw_prob=%.6f logit=%.6f k=%.4f sharpened_logit=%.6f p_sharp=%.6f",
        raw_prob,
        logit_p,
        k,
        sharpened_logit,
        p_sharp,
    )

    return float(p_sharp)


def batch_calibrate(probs: list, k: float = None) -> list:
    """
    Calibrate a list of raw probabilities.

    Args:
        probs: List of raw probabilities in (0, 1).
        k: Sharpening factor passed to calibrate(). Defaults to CALIBRATION_SHARPNESS.

    Returns:
        List of calibrated probabilities.
    """
    if k is None:
        k = CALIBRATION_SHARPNESS

    results = []
    for raw_prob in probs:
        results.append(calibrate(raw_prob, k=k))

    logger.info(
        "batch_calibrate: processed %d probabilities with k=%.4f | "
        "raw_mean=%.4f sharp_mean=%.4f | "
        "raw_separation=%.4f sharp_separation=%.4f",
        len(probs),
        k,
        sum(probs) / len(probs) if probs else 0.0,
        sum(results) / len(results) if results else 0.0,
        sum(abs(p - 0.5) for p in probs) / len(probs) if probs else 0.0,
        sum(abs(p - 0.5) for p in results) / len(results) if results else 0.0,
    )

    return results


# ---------------------------------------------------------------------------
# Sanity checks (executed at import time in development; skipped silently in
# production if assertions are disabled via -O flag).
# ---------------------------------------------------------------------------

def _run_sanity_checks() -> None:
    _eps = 1e-5

    # 0.5 must be a fixed point for any k
    mid = calibrate(0.5, k=1.5)
    assert abs(mid - 0.5) < _eps, f"0.5 must map to 0.5, got {mid}"

    # Probabilities above 0.5 must be pushed higher
    assert calibrate(0.7, k=1.5) > 0.7, "p=0.7 must be pushed up"
    assert calibrate(0.65, k=1.5) > 0.65, "p=0.65 must be pushed up"

    # Probabilities below 0.5 must be pushed lower
    assert calibrate(0.3, k=1.5) < 0.3, "p=0.3 must be pushed down"
    assert calibrate(0.35, k=1.5) < 0.35, "p=0.35 must be pushed down"

    # Output must stay within clamped range
    assert 0.03 <= calibrate(0.999, k=1.5) <= 0.97, "must stay in [0.03, 0.97]"
    assert 0.03 <= calibrate(0.001, k=1.5) <= 0.97, "must stay in [0.03, 0.97]"

    # k=1 should be a near-identity (modulo clamping at tails)
    for p_test in [0.2, 0.4, 0.5, 0.6, 0.8]:
        result = calibrate(p_test, k=1.0)
        assert abs(result - max(0.03, min(0.97, p_test))) < _eps, (
            f"k=1 identity failed at p={p_test}: got {result}"
        )

    logger.debug("calibrator sanity checks passed")


_run_sanity_checks()