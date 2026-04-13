import math
import logging

logger = logging.getLogger(__name__)

CALIBRATION_SHARPENING_K = 1.5

CATEGORY_BASE_RATES = {}


def _logit(p: float) -> float:
    p = max(1e-9, min(1 - 1e-9, p))
    return math.log(p / (1.0 - p))


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def sharpen_probability(p: float, k: float = CALIBRATION_SHARPENING_K) -> float:
    if abs(p - 0.5) < 0.03:
        return p
    p_clamped = max(1e-9, min(1 - 1e-9, p))
    logit_val = _logit(p_clamped)
    logit_sharp = k * logit_val
    p_sharp = _sigmoid(logit_sharp)
    return max(0.05, min(0.95, p_sharp))


def blend_with_base_rate(p: float, category: str, model_weight: float = 0.7) -> float:
    base_rate = CATEGORY_BASE_RATES.get(category)
    if base_rate is None:
        return p
    base_rate = max(0.05, min(0.95, float(base_rate)))
    blended = model_weight * p + (1.0 - model_weight) * base_rate
    logger.debug(
        "Base rate blend: category=%s p=%.4f base_rate=%.4f blended=%.4f",
        category,
        p,
        base_rate,
        blended,
    )
    return blended


def calibrate(raw_probability: float, category: str = None) -> float:
    p = max(0.0, min(1.0, float(raw_probability)))

    p = _apply_existing_calibration(p)

    p = sharpen_probability(p, k=CALIBRATION_SHARPENING_K)

    if category is not None:
        p = blend_with_base_rate(p, category)

    p = max(0.05, min(0.95, p))

    logger.debug(
        "calibrate: raw=%.4f -> final=%.4f (category=%s)",
        raw_probability,
        p,
        category,
    )
    return p


def _apply_existing_calibration(p: float) -> float:
    return p


def set_category_base_rate(category: str, base_rate: float) -> None:
    CATEGORY_BASE_RATES[category] = max(0.0, min(1.0, float(base_rate)))


def load_category_base_rates(rates_dict: dict) -> None:
    for category, rate in rates_dict.items():
        set_category_base_rate(category, rate)