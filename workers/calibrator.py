import logging
import math
from typing import Optional

logger = logging.getLogger(__name__)

CATEGORY_BASE_RATES = {
    "sports": 0.52,
    "politics": 0.48,
    "economics": 0.50,
    "technology": 0.51,
    "entertainment": 0.50,
    "science": 0.49,
    "default": 0.50,
}

BASE_RATE_WEIGHT = 0.3
MODEL_WEIGHT = 0.7
DEFAULT_K = 1.8
FENCE_SITTING_K = 2.2
FENCE_SITTING_LOW = 0.47
FENCE_SITTING_HIGH = 0.53
CLAMP_LOW = 0.03
CLAMP_HIGH = 0.97


def extremize(p: float, k: float = DEFAULT_K) -> float:
    p = clamp(p, 0.001, 0.999)
    odds = p / (1.0 - p)
    odds_k = odds ** k
    p_ext = odds_k / (1.0 + odds_k)
    return clamp(p_ext, CLAMP_LOW, CLAMP_HIGH)


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def anchor_to_base_rate(p_model: float, category: Optional[str] = None) -> float:
    base_rate = CATEGORY_BASE_RATES.get(category or "default", CATEGORY_BASE_RATES["default"])
    return MODEL_WEIGHT * p_model + BASE_RATE_WEIGHT * base_rate


def calibrate(p_raw: float, category: Optional[str] = None) -> float:
    logger.info("CALIBRATOR_VERSION=v3_extremize_k1.8_base_rate_anchor")

    p_raw = clamp(p_raw, CLAMP_LOW, CLAMP_HIGH)

    logger.info("calibration_input", extra={"p_raw": p_raw, "category": category})

    p_anchored = anchor_to_base_rate(p_raw, category)
    logger.info("calibration_anchored", extra={"p_anchored": p_anchored, "category": category})

    is_fence_sitting = FENCE_SITTING_LOW <= p_raw <= FENCE_SITTING_HIGH
    k = FENCE_SITTING_K if is_fence_sitting else DEFAULT_K

    p_final = extremize(p_anchored, k=k)
    p_final = clamp(p_final, CLAMP_LOW, CLAMP_HIGH)

    logger.info(
        "calibration_output",
        extra={
            "p_raw": p_raw,
            "p_anchored": p_anchored,
            "p_final": p_final,
            "k_used": k,
            "is_fence_sitting": is_fence_sitting,
            "category": category,
            "delta": p_final - p_raw,
        },
    )

    return p_final


def calibrate_batch(probabilities: list, categories: Optional[list] = None) -> list:
    if categories is None:
        categories = [None] * len(probabilities)
    results = []
    for p, cat in zip(probabilities, categories):
        results.append(calibrate(p, category=cat))
    return results