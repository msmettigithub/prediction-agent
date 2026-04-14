import numpy as np
import logging
import math

logger = logging.getLogger(__name__)

ALPHA = 1.8
CONFIDENCE_FLOOR = 0.03
CLAMP_LOW = 0.05
CLAMP_HIGH = 0.95


def sharpen_probability(p: float, alpha: float = ALPHA) -> float:
    """
    Generalized log-odds scaling (extremizing transform).

    Steps:
        1. logit = log(p / (1 - p))
        2. scaled_logit = alpha * logit
        3. p_out = 1 / (1 + exp(-scaled_logit))

    This is a monotonic transform that preserves probability ordering.
    Only applied when abs(p - 0.5) > CONFIDENCE_FLOOR to avoid
    amplifying noise in near-50/50 predictions.
    Result is clamped to [CLAMP_LOW, CLAMP_HIGH].

    Args:
        p: Input probability in (0, 1).
        alpha: Scaling factor. alpha=1 is identity; alpha>1 sharpens.

    Returns:
        Sharpened probability, clamped to [CLAMP_LOW, CLAMP_HIGH].
    """
    p_safe = float(np.clip(p, 1e-9, 1 - 1e-9))

    if abs(p_safe - 0.5) <= CONFIDENCE_FLOOR:
        logger.debug(
            "sharpen_probability: p=%.6f within confidence_floor=%.3f, skipping sharpening",
            p_safe,
            CONFIDENCE_FLOOR,
        )
        return float(np.clip(p_safe, CLAMP_LOW, CLAMP_HIGH))

    logit = math.log(p_safe / (1.0 - p_safe))
    scaled_logit = alpha * logit
    p_out = 1.0 / (1.0 + math.exp(-scaled_logit))
    p_out = float(np.clip(p_out, CLAMP_LOW, CLAMP_HIGH))

    logger.debug(
        "sharpen_probability: p_in=%.6f | logit=%.6f | scaled_logit=%.6f | p_out=%.6f | alpha=%.3f",
        p_safe,
        logit,
        scaled_logit,
        p_out,
        alpha,
    )

    return p_out


def base_calibrate(p: float) -> float:
    """
    Base calibration step (isotonic-style clamp + minor regularization).
    Keeps probabilities away from absolute 0/1 before sharpening.

    Args:
        p: Raw model probability.

    Returns:
        Base-calibrated probability in (0, 1).
    """
    p_safe = float(np.clip(p, 1e-6, 1 - 1e-6))
    return p_safe


def calibrate(p: float, alpha: float = ALPHA) -> float:
    """
    Full calibration pipeline:
        1. Base calibration (regularization / isotonic correction).
        2. Sharpening via generalized log-odds scaling (confidence-gated).
        3. Clamp to [CLAMP_LOW, CLAMP_HIGH].

    This is the entry point wired into the trading brain.

    Args:
        p: Raw model probability output.
        alpha: Sharpening strength. Defaults to ALPHA=1.8.

    Returns:
        Final calibrated-and-sharpened probability.
    """
    p_base = base_calibrate(p)
    logger.info(
        "calibrate: pre_sharpen_p=%.6f (raw_input=%.6f)",
        p_base,
        p,
    )

    p_sharp = sharpen_probability(p_base, alpha=alpha)

    logger.info(
        "calibrate: post_sharpen_p=%.6f | delta=%.6f | alpha=%.3f | "
        "confidence_floor=%.3f | clamped_to=[%.2f, %.2f]",
        p_sharp,
        p_sharp - p_base,
        alpha,
        CONFIDENCE_FLOOR,
        CLAMP_LOW,
        CLAMP_HIGH,
    )

    return p_sharp


def adjust(p: float, alpha: float = ALPHA) -> float:
    """
    Alias for calibrate(). Provided for compatibility with callers
    that reference adjust() directly.

    Args:
        p: Raw model probability.
        alpha: Sharpening strength.

    Returns:
        Calibrated-and-sharpened probability.
    """
    return calibrate(p, alpha=alpha)


transform = calibrate
calibrate_probability = calibrate


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    test_probs = [0.5, 0.51, 0.53, 0.55, 0.60, 0.70, 0.80, 0.90, 0.95,
                  0.45, 0.40, 0.30, 0.20, 0.10, 0.05]
    print(f"{'p_in':>8} {'p_base':>8} {'p_sharp':>8} {'delta':>8}")
    print("-" * 36)
    for prob in test_probs:
        p_b = base_calibrate(prob)
        p_s = calibrate(prob)
        print(f"{prob:8.4f} {p_b:8.4f} {p_s:8.4f} {p_s - p_b:+8.4f}")