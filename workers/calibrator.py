import logging
import math
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from config import EXTREMIZE_EXPONENT, BASE_RATE_BLEND_WEIGHT
except ImportError:
    EXTREMIZE_EXPONENT = 1.5
    BASE_RATE_BLEND_WEIGHT = 0.2

CATEGORY_BASE_RATES: dict[str, float] = {
    "crypto": 0.52,
    "sports": 0.50,
    "politics": 0.48,
    "finance": 0.51,
    "science": 0.50,
    "weather": 0.53,
    "entertainment": 0.50,
    "technology": 0.51,
    "default": 0.50,
}

CONFIDENCE_FLOOR = 0.03


def extremize(p: float, a: Optional[float] = None) -> float:
    if a is None:
        a = EXTREMIZE_EXPONENT
    if p <= 0.0:
        return 0.0
    if p >= 1.0:
        return 1.0
    p_a = p ** a
    complement_a = (1.0 - p) ** a
    denom = p_a + complement_a
    if denom == 0.0:
        return p
    return p_a / denom


def get_base_rate(category: Optional[str]) -> float:
    if category is None:
        return CATEGORY_BASE_RATES["default"]
    return CATEGORY_BASE_RATES.get(category.lower(), CATEGORY_BASE_RATES["default"])


def apply_base_rate_anchoring(
    model_prob: float,
    category: Optional[str] = None,
    blend_weight: Optional[float] = None,
) -> float:
    if blend_weight is None:
        blend_weight = BASE_RATE_BLEND_WEIGHT
    base_rate = get_base_rate(category)
    blended = blend_weight * base_rate + (1.0 - blend_weight) * model_prob
    logger.debug(
        f"base_rate_anchoring: model_prob={model_prob:.4f} base_rate={base_rate:.4f} "
        f"blend_weight={blend_weight:.2f} blended={blended:.4f} category={category}"
    )
    return blended


def calibrate(
    p_raw: float,
    category: Optional[str] = None,
    signal_count: Optional[int] = None,
    exponent: Optional[float] = None,
) -> Optional[float]:
    p_raw = float(p_raw)
    p_raw = max(1e-9, min(1.0 - 1e-9, p_raw))

    logger.info(f"calibrate_input: p_raw={p_raw:.4f} category={category} signal_count={signal_count}")

    p_anchored = apply_base_rate_anchoring(p_raw, category=category)

    a = exponent if exponent is not None else EXTREMIZE_EXPONENT
    if signal_count is not None:
        a = a + 0.1 * min(max(signal_count - 1, 0), 4)

    p_ext = extremize(p_anchored, a=a)

    logger.info(
        f"calibrate_transform: p_raw={p_raw:.4f} p_anchored={p_anchored:.4f} "
        f"p_ext={p_ext:.4f} exponent={a:.3f} category={category}"
    )

    distance_from_half = abs(p_ext - 0.5)
    if distance_from_half < CONFIDENCE_FLOOR:
        logger.info(
            f"calibrate_skip: p_ext={p_ext:.4f} distance_from_half={distance_from_half:.4f} "
            f"below floor={CONFIDENCE_FLOOR} returning None"
        )
        return None

    logger.info(
        f"calibrate_output: p_ext={p_ext:.4f} distance_from_half={distance_from_half:.4f}"
    )
    return p_ext


def batch_calibrate(
    predictions: list[dict],
    exponent: Optional[float] = None,
) -> list[dict]:
    results = []
    for pred in predictions:
        p_raw = pred.get("probability", pred.get("p_raw", 0.5))
        category = pred.get("category", None)
        signal_count = pred.get("signal_count", None)

        p_cal = calibrate(
            p_raw=p_raw,
            category=category,
            signal_count=signal_count,
            exponent=exponent,
        )

        result = dict(pred)
        result["p_calibrated"] = p_cal
        result["skipped"] = p_cal is None
        results.append(result)

    deployed = sum(1 for r in results if not r["skipped"])
    total = len(results)
    logger.info(
        f"batch_calibrate_summary: total={total} deployed={deployed} skipped={total - deployed} "
        f"deploy_rate={deployed / total:.3f}" if total > 0 else "batch_calibrate_summary: total=0"
    )
    return results