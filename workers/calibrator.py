import logging
import math
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from config import EXTREMIZATION_ALPHA
except ImportError:
    EXTREMIZATION_ALPHA = 1.5

CATEGORY_BASE_RATES = {
    "geopolitical-status-quo": 0.7,
    "financial-mean-reversion": 0.6,
    "technology-adoption": 0.55,
    "election-incumbent": 0.6,
}

DEFAULT_BASE_RATE = 0.5
BASE_RATE_WEIGHT = 0.3
MODEL_WEIGHT = 0.7
CONFIDENCE_GATE_THRESHOLD = 0.05
ALPHA_MIN = 1.2
ALPHA_MAX = 2.0


def _validate_alpha(a: float) -> float:
    if not (ALPHA_MIN <= a <= ALPHA_MAX):
        logger.warning(
            "Extremization alpha %.3f out of valid range [%.1f, %.1f], clamping.",
            a,
            ALPHA_MIN,
            ALPHA_MAX,
        )
        return max(ALPHA_MIN, min(ALPHA_MAX, a))
    return a


def _validate_probability(p: float, name: str = "probability") -> float:
    if not (0.0 <= p <= 1.0):
        logger.warning(
            "Invalid %s value %.4f, clamping to [0, 1].", name, p
        )
        return max(0.0, min(1.0, p))
    return p


def get_base_rate(category: Optional[str]) -> float:
    if category is None:
        return DEFAULT_BASE_RATE
    rate = CATEGORY_BASE_RATES.get(category, DEFAULT_BASE_RATE)
    if category not in CATEGORY_BASE_RATES:
        logger.debug(
            "Category '%s' not found in base rate table, using default %.2f.",
            category,
            DEFAULT_BASE_RATE,
        )
    return rate


def anchor_to_base_rate(p_model: float, category: Optional[str]) -> float:
    p_model = _validate_probability(p_model, "model probability")
    base_rate = get_base_rate(category)
    p_anchored = BASE_RATE_WEIGHT * base_rate + MODEL_WEIGHT * p_model
    p_anchored = _validate_probability(p_anchored, "anchored probability")
    logger.debug(
        "Base rate anchoring: category=%s base_rate=%.4f p_model=%.4f p_anchored=%.4f",
        category,
        base_rate,
        p_model,
        p_anchored,
    )
    return p_anchored


def extremize(p: float, a: float) -> float:
    p = _validate_probability(p, "pre-extremization probability")
    a = _validate_alpha(a)
    p_clipped = max(1e-9, min(1.0 - 1e-9, p))
    p_a = math.pow(p_clipped, a)
    one_minus_p_a = math.pow(1.0 - p_clipped, a)
    denominator = p_a + one_minus_p_a
    if denominator == 0.0:
        logger.warning("Extremization denominator is zero for p=%.6f, a=%.3f, returning 0.5.", p, a)
        return 0.5
    p_ext = p_a / denominator
    return _validate_probability(p_ext, "post-extremization probability")


def calibrate(
    p_model: float,
    category: Optional[str] = None,
    a: Optional[float] = None,
    contract_id: Optional[str] = None,
) -> float:
    if a is None:
        a = EXTREMIZATION_ALPHA

    a = _validate_alpha(a)
    p_model = _validate_probability(p_model, "raw model probability")

    p_anchored = anchor_to_base_rate(p_model, category)

    distance_from_half = abs(p_anchored - 0.5)
    if distance_from_half <= CONFIDENCE_GATE_THRESHOLD:
        logger.info(
            "Calibration [contract=%s category=%s]: "
            "anchored probability %.4f is within %.2f of 0.5 (distance=%.4f), "
            "skipping extremization to avoid amplifying noise. "
            "p_raw=%.4f p_anchored=%.4f p_final=%.4f",
            contract_id,
            category,
            p_anchored,
            CONFIDENCE_GATE_THRESHOLD,
            distance_from_half,
            p_model,
            p_anchored,
            p_anchored,
        )
        return p_anchored

    p_extremized = extremize(p_anchored, a)

    logger.info(
        "Calibration [contract=%s category=%s]: "
        "p_raw=%.4f p_anchored=%.4f p_extremized=%.4f "
        "alpha=%.3f distance_from_half=%.4f extremization_applied=True",
        contract_id,
        category,
        p_model,
        p_anchored,
        p_extremized,
        a,
        distance_from_half,
    )

    return p_extremized


def calibrate_batch(
    predictions: list,
    a: Optional[float] = None,
) -> list:
    if a is None:
        a = EXTREMIZATION_ALPHA

    results = []
    for item in predictions:
        p_model = item.get("p_model")
        category = item.get("category", None)
        contract_id = item.get("contract_id", None)

        if p_model is None:
            logger.warning(
                "Missing p_model in batch item contract_id=%s, skipping.", contract_id
            )
            results.append({**item, "p_calibrated": None, "error": "missing p_model"})
            continue

        p_calibrated = calibrate(
            p_model=p_model,
            category=category,
            a=a,
            contract_id=contract_id,
        )
        results.append({**item, "p_calibrated": p_calibrated})

    return results