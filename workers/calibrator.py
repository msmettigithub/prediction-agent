import math
import logging
from typing import Optional

logger = logging.getLogger(__name__)

CATEGORY_BASE_RATES = {
    "geopolitical": 0.30,
    "tech_earnings": 0.65,
    "macro_economic": 0.45,
    "sports": 0.50,
    "political_election": 0.50,
    "default": 0.50,
}

ALPHA = 0.6
BASE_RATE_WEIGHT = 0.25
FLOOR = 0.08
CEILING = 0.92
MIN_CONFIDENCE_DELTA = 0.03


def sharpen(p: float, alpha: float = ALPHA) -> float:
    """
    Confidence-stretching sharpening function.
    p_sharp = 0.5 + sign(p-0.5) * |2*(p-0.5)|^alpha / 2
    alpha < 1 stretches probabilities away from 0.5.
    """
    p = max(0.0, min(1.0, p))
    delta = p - 0.5
    if delta == 0.0:
        return 0.5
    sign = 1.0 if delta > 0 else -1.0
    stretched = sign * (abs(2.0 * delta) ** alpha) / 2.0
    return 0.5 + stretched


def anchor_to_base_rate(p: float, category: Optional[str], weight: float = BASE_RATE_WEIGHT) -> float:
    """
    Blend model probability with category-specific base rate.
    p_anchored = (1 - weight) * p + weight * base_rate
    """
    key = (category or "default").lower()
    base_rate = CATEGORY_BASE_RATES.get(key, CATEGORY_BASE_RATES["default"])
    return (1.0 - weight) * p + weight * base_rate


def calibrate(p: float, category: Optional[str] = None) -> Optional[float]:
    """
    Full calibration pipeline:
    1. Sharpen probability away from 0.5 (power-law stretch)
    2. Anchor to category base rate
    3. Clip to floor/ceiling
    4. Return None (skip trade) if |p - 0.5| < MIN_CONFIDENCE_DELTA

    Returns calibrated probability, or None if trade should be skipped.
    """
    pre_calibration = p

    sharpened = sharpen(p, alpha=ALPHA)

    anchored = anchor_to_base_rate(sharpened, category, weight=BASE_RATE_WEIGHT)

    clipped = max(FLOOR, min(CEILING, anchored))

    confidence_delta = abs(clipped - 0.5)
    if confidence_delta < MIN_CONFIDENCE_DELTA:
        logger.info(
            "calibration_skip",
            extra={
                "pre_calibration_p": round(pre_calibration, 6),
                "post_sharpening_p": round(sharpened, 6),
                "post_anchoring_p": round(anchored, 6),
                "post_clipping_p": round(clipped, 6),
                "confidence_delta": round(confidence_delta, 6),
                "category": category,
                "action": "skip_noise_trade",
            },
        )
        return None

    logger.info(
        "calibration_applied",
        extra={
            "pre_calibration_p": round(pre_calibration, 6),
            "post_sharpening_p": round(sharpened, 6),
            "post_anchoring_p": round(anchored, 6),
            "post_clipping_p": round(clipped, 6),
            "confidence_delta": round(confidence_delta, 6),
            "category": category,
            "alpha": ALPHA,
            "base_rate_weight": BASE_RATE_WEIGHT,
        },
    )

    return clipped


def calibrate_batch(predictions: list[dict]) -> list[dict]:
    """
    Calibrate a batch of predictions.
    Each prediction dict must have 'probability' key.
    Optional 'category' key for base-rate anchoring.

    Returns list with calibrated predictions; skipped trades have 'skip': True.
    """
    results = []
    for pred in predictions:
        raw_p = pred.get("probability", 0.5)
        category = pred.get("category", None)

        calibrated_p = calibrate(raw_p, category=category)

        result = dict(pred)
        result["raw_probability"] = raw_p

        if calibrated_p is None:
            result["probability"] = None
            result["skip"] = True
            result["skip_reason"] = "insufficient_confidence_post_calibration"
        else:
            result["probability"] = calibrated_p
            result["skip"] = False

        results.append(result)

    skipped = sum(1 for r in results if r.get("skip", False))
    logger.info(
        "calibration_batch_complete",
        extra={
            "total": len(predictions),
            "skipped": skipped,
            "passed": len(predictions) - skipped,
        },
    )

    return results