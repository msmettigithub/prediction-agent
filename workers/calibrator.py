import math
import logging
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from config import EXTREMIZE_K, EXTREMIZE_ENABLED, EXTREMIZE_MIN_CONFIDENCE_DELTA
except ImportError:
    EXTREMIZE_K = 1.5
    EXTREMIZE_ENABLED = True
    EXTREMIZE_MIN_CONFIDENCE_DELTA = 0.03


def extremize(p: float, k: Optional[float] = None) -> float:
    """
    Apply log-odds sharpening (extremization) to a calibrated probability.

    Steps:
    1. Convert p to log-odds: logit = ln(p / (1-p))
    2. Scale: scaled_logit = k * logit
    3. Convert back: p_sharp = 1 / (1 + exp(-scaled_logit))
    4. Clamp to [0.05, 0.95]

    Only applies when |p - 0.5| > EXTREMIZE_MIN_CONFIDENCE_DELTA to avoid
    amplifying noise on genuinely uncertain questions.

    Args:
        p: Calibrated probability in (0, 1)
        k: Sharpening factor. k > 1 pushes probabilities toward extremes.
           Defaults to EXTREMIZE_K from config (recommended range: 1.2-2.0
           per superforecasting literature).

    Returns:
        Sharpened probability clamped to [0.05, 0.95]
    """
    if k is None:
        k = EXTREMIZE_K

    p = float(p)

    # Safety clamp before logit to avoid log(0) or log(inf)
    p = max(1e-9, min(1 - 1e-9, p))

    # Only extremize when model has sufficient confidence signal
    confidence_delta = abs(p - 0.5)
    if confidence_delta <= EXTREMIZE_MIN_CONFIDENCE_DELTA:
        logger.debug(
            "Skipping extremization: |p - 0.5| = %.4f <= threshold %.4f",
            confidence_delta,
            EXTREMIZE_MIN_CONFIDENCE_DELTA,
        )
        # Still clamp to [0.05, 0.95] for consistency
        return max(0.05, min(0.95, p))

    # Step 1: log-odds transform
    logit = math.log(p / (1.0 - p))

    # Step 2: apply sharpening factor
    scaled_logit = k * logit

    # Step 3: sigmoid back to probability
    try:
        p_sharp = 1.0 / (1.0 + math.exp(-scaled_logit))
    except OverflowError:
        # scaled_logit extremely negative => probability near 0
        p_sharp = 0.0

    # Step 4: clamp to [0.05, 0.95]
    p_sharp = max(0.05, min(0.95, p_sharp))

    logger.debug(
        "Extremize: p=%.4f -> logit=%.4f -> scaled_logit=%.4f (k=%.2f) -> p_sharp=%.4f",
        p,
        logit,
        scaled_logit,
        k,
        p_sharp,
    )

    return p_sharp


def calibrate(raw_probability: float, market_context: Optional[dict] = None) -> float:
    """
    Full calibration pipeline for a raw model probability output.

    Pipeline:
    1. Input validation and clamping
    2. (Existing calibration logic preserved here — add isotonic regression,
       Platt scaling, or beta calibration upstream of this function as needed)
    3. Extremization post-processing step (config-toggled)

    Args:
        raw_probability: Raw model output probability in [0, 1]
        market_context: Optional dict with additional context (unused currently,
                        reserved for future concordance-based signal fusion)

    Returns:
        Calibrated (and optionally extremized) probability in [0.05, 0.95]
    """
    p = float(raw_probability)

    # Input validation
    if not math.isfinite(p):
        logger.warning("Non-finite probability received: %s, defaulting to 0.5", p)
        p = 0.5

    # Clamp raw input to valid probability range
    p = max(0.0, min(1.0, p))

    # ------------------------------------------------------------------
    # Existing calibration logic (preserved)
    # Insert isotonic regression / Platt scaling / beta calibration here.
    # This function currently acts as a passthrough for the base probability
    # and applies extremization as a post-processing layer below.
    # ------------------------------------------------------------------
    calibrated_p = p

    # ------------------------------------------------------------------
    # Extremization post-processing
    # Toggle via EXTREMIZE_ENABLED in config.py
    # Adjust sharpening strength via EXTREMIZE_K in config.py
    # ------------------------------------------------------------------
    if EXTREMIZE_ENABLED:
        logger.debug(
            "Applying extremization with k=%.2f, min_delta=%.3f",
            EXTREMIZE_K,
            EXTREMIZE_MIN_CONFIDENCE_DELTA,
        )
        final_p = extremize(calibrated_p, k=EXTREMIZE_K)
    else:
        logger.debug("Extremization disabled via config, returning calibrated_p=%.4f", calibrated_p)
        final_p = max(0.05, min(0.95, calibrated_p))

    logger.info(
        "Calibration complete: raw=%.4f -> calibrated=%.4f -> final=%.4f (extremize=%s, k=%.2f)",
        raw_probability,
        calibrated_p,
        final_p,
        EXTREMIZE_ENABLED,
        EXTREMIZE_K,
    )

    return final_p


def calibrate_batch(probabilities: list, market_contexts: Optional[list] = None) -> list:
    """
    Apply calibration pipeline to a batch of probabilities.

    Args:
        probabilities: List of raw model output probabilities
        market_contexts: Optional list of market context dicts, aligned with probabilities

    Returns:
        List of calibrated probabilities
    """
    if market_contexts is None:
        market_contexts = [None] * len(probabilities)

    if len(market_contexts) != len(probabilities):
        logger.warning(
            "market_contexts length %d != probabilities length %d, ignoring contexts",
            len(market_contexts),
            len(probabilities),
        )
        market_contexts = [None] * len(probabilities)

    results = []
    for i, (p, ctx) in enumerate(zip(probabilities, market_contexts)):
        try:
            calibrated = calibrate(p, market_context=ctx)
            results.append(calibrated)
        except Exception as exc:
            logger.error(
                "Calibration failed for probability[%d]=%.4f: %s, using passthrough",
                i,
                float(p) if p is not None else float("nan"),
                exc,
            )
            # Safe fallback: clamp raw value, no extremization
            try:
                fallback = max(0.05, min(0.95, float(p)))
            except (TypeError, ValueError):
                fallback = 0.5
            results.append(fallback)

    return results