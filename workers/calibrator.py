import logging
import math
from typing import Optional

logger = logging.getLogger(__name__)

CATEGORY_BASE_RATES = {
    "geopolitical": 0.35,
    "economic": 0.50,
    "tech": 0.55,
    "default": 0.50,
}

MODEL_WEIGHT = 0.75
BASE_RATE_WEIGHT = 0.25
EXTREMIZE_ALPHA = 1.5
EXTREMIZE_THRESHOLD = 0.08
CLIP_LOW = 0.03
CLIP_HIGH = 0.97


def extremize(p: float, alpha: float = EXTREMIZE_ALPHA) -> float:
    p = max(min(p, 0.999), 0.001)
    p_extreme = 0.5 + alpha * (p - 0.5)
    p_extreme = max(CLIP_LOW, min(CLIP_HIGH, p_extreme))
    return p_extreme


def apply_base_rate_anchoring(p: float, category: Optional[str] = None) -> float:
    base_rate = CATEGORY_BASE_RATES.get(category or "default", CATEGORY_BASE_RATES["default"])
    anchored = MODEL_WEIGHT * p + BASE_RATE_WEIGHT * base_rate
    return anchored


def confidence_gated_extremize(p: float, alpha: float = EXTREMIZE_ALPHA) -> float:
    if abs(p - 0.5) > EXTREMIZE_THRESHOLD:
        return extremize(p, alpha=alpha)
    return p


def calibrate(p_raw: float, category: Optional[str] = None) -> float:
    logger.info(
        "calibration_start",
        extra={
            "event": "calibration_start",
            "p_raw": p_raw,
            "category": category,
        },
    )

    try:
        p = float(p_raw)
        if not math.isfinite(p):
            logger.warning("Non-finite probability received, defaulting to 0.5", extra={"p_raw": p_raw})
            p = 0.5
        p = max(0.0, min(1.0, p))

        p_anchored = apply_base_rate_anchoring(p, category=category)

        logger.debug(
            "calibration_after_anchoring",
            extra={
                "event": "calibration_after_anchoring",
                "p_raw": p_raw,
                "p_anchored": p_anchored,
                "category": category,
            },
        )

        p_final = confidence_gated_extremize(p_anchored, alpha=EXTREMIZE_ALPHA)

        logger.info(
            "calibration_complete",
            extra={
                "event": "calibration_complete",
                "p_raw": p_raw,
                "p_anchored": p_anchored,
                "p_final": p_final,
                "category": category,
                "extremized": abs(p_anchored - 0.5) > EXTREMIZE_THRESHOLD,
                "separation_contribution": abs(p_final - 0.5),
            },
        )

        return p_final

    except Exception as exc:
        logger.error(
            "calibration_error",
            extra={
                "event": "calibration_error",
                "p_raw": p_raw,
                "error": str(exc),
            },
        )
        return float(p_raw) if math.isfinite(float(p_raw)) else 0.5


def batch_calibrate(probabilities: list, categories: Optional[list] = None) -> list:
    if categories is None:
        categories = [None] * len(probabilities)
    if len(categories) != len(probabilities):
        logger.warning(
            "batch_calibrate_length_mismatch",
            extra={
                "event": "batch_calibrate_length_mismatch",
                "n_probs": len(probabilities),
                "n_categories": len(categories),
            },
        )
        categories = (categories + [None] * len(probabilities))[: len(probabilities)]

    results = []
    for p, cat in zip(probabilities, categories):
        results.append(calibrate(p, category=cat))

    if results:
        separations = [abs(p - 0.5) for p in results]
        avg_separation = sum(separations) / len(separations)
        logger.info(
            "batch_calibration_summary",
            extra={
                "event": "batch_calibration_summary",
                "n": len(results),
                "avg_separation": avg_separation,
                "min_separation": min(separations),
                "max_separation": max(separations),
                "above_gate_count": sum(1 for s in separations if s > 0.10),
            },
        )

    return results