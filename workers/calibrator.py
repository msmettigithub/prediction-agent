import numpy as np
import logging
from typing import Optional

logger = logging.getLogger(__name__)

EXTREMIZING_K = 1.5

CATEGORY_BASE_RATES = {
    "geopolitical": 0.35,
    "economic_indicator": 0.55,
    "tech": 0.45,
    "sports": 0.40,
    "default": 0.50,
}

BASE_RATE_WEIGHT = 0.15
BASE_RATE_RAW_WEIGHT = 1.0 - BASE_RATE_WEIGHT

LOW_CONVICTION_THRESHOLD = 0.03


def get_base_rate(category: Optional[str]) -> float:
    if category is None:
        return CATEGORY_BASE_RATES["default"]
    return CATEGORY_BASE_RATES.get(category.lower(), CATEGORY_BASE_RATES["default"])


def apply_base_rate_anchoring(p_raw: float, category: Optional[str]) -> float:
    base_rate = get_base_rate(category)
    p_blended = BASE_RATE_RAW_WEIGHT * p_raw + BASE_RATE_WEIGHT * base_rate
    logger.debug(
        "base_rate_anchoring: p_raw=%.4f category=%s base_rate=%.4f p_blended=%.4f",
        p_raw,
        category,
        base_rate,
        p_blended,
    )
    return p_blended


def apply_extremizing(p_calibrated: float, k: float = EXTREMIZING_K) -> float:
    raw_shift = k * (p_calibrated - 0.5)
    clamped_shift = max(-0.45, min(0.45, raw_shift))
    p_extremized = 0.5 + clamped_shift
    logger.debug(
        "extremizing: p_calibrated=%.4f k=%.2f raw_shift=%.4f clamped_shift=%.4f p_extremized=%.4f",
        p_calibrated,
        k,
        raw_shift,
        clamped_shift,
        p_extremized,
    )
    return p_extremized


def calibrate(
    p_raw: float,
    category: Optional[str] = None,
    k: float = EXTREMIZING_K,
) -> Optional[float]:
    p_raw = float(p_raw)
    p_raw = float(np.clip(p_raw, 1e-6, 1.0 - 1e-6))

    logger.info("calibrate: p_raw=%.4f category=%s", p_raw, category)

    if abs(p_raw - 0.5) < LOW_CONVICTION_THRESHOLD:
        logger.info(
            "calibrate: low-conviction signal (|%.4f - 0.5| = %.4f < %.2f), skipping trade",
            p_raw,
            abs(p_raw - 0.5),
            LOW_CONVICTION_THRESHOLD,
        )
        return None

    p_blended = apply_base_rate_anchoring(p_raw, category)

    p_blended = float(np.clip(p_blended, 1e-6, 1.0 - 1e-6))

    p_extremized = apply_extremizing(p_blended, k=k)

    p_extremized = float(np.clip(p_extremized, 0.05, 0.95))

    logger.info(
        "calibrate: p_raw=%.4f -> p_blended=%.4f -> p_extremized=%.4f (category=%s, k=%.2f)",
        p_raw,
        p_blended,
        p_extremized,
        category,
        k,
    )

    return p_extremized


def calibrate_batch(
    probs: list,
    categories: Optional[list] = None,
    k: float = EXTREMIZING_K,
) -> list:
    results = []
    for i, p in enumerate(probs):
        category = None
        if categories is not None and i < len(categories):
            category = categories[i]
        try:
            result = calibrate(p, category=category, k=k)
            results.append(result)
        except Exception as exc:
            logger.warning(
                "calibrate_batch: error on index %d p=%s: %s, falling back to None",
                i,
                p,
                exc,
            )
            results.append(None)
    return results