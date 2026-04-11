import math
import logging
from typing import Optional

logger = logging.getLogger(__name__)

SHARPENING_ENABLED = True

CATEGORY_BASE_RATES = {
    "policy_passage": 0.25,
    "incumbent_win": 0.65,
    "economic_recession": 0.20,
    "sports_championship": 0.15,
    "tech_product_launch": 0.70,
    "election_incumbent": 0.60,
    "legislation_pass": 0.30,
    "company_bankruptcy": 0.10,
    "default": 0.50,
}

TEMPERATURE = 0.6
BASE_RATE_BLEND_WEIGHT = 0.30
CONFIDENCE_GATE_THRESHOLD = 0.03


def _temperature_scale(p: float, temperature: float = TEMPERATURE) -> float:
    p = max(1e-9, min(1 - 1e-9, p))
    log_odds = math.log(p / (1.0 - p))
    sharpened_log_odds = log_odds / temperature
    sharpened_p = 1.0 / (1.0 + math.exp(-sharpened_log_odds))
    return max(1e-9, min(1 - 1e-9, sharpened_p))


def _base_rate_anchor(p: float, category: Optional[str], blend_weight: float = BASE_RATE_BLEND_WEIGHT) -> float:
    if category is None:
        base_rate = CATEGORY_BASE_RATES["default"]
    else:
        category_key = category.lower().strip()
        base_rate = CATEGORY_BASE_RATES.get(category_key, CATEGORY_BASE_RATES["default"])
    anchored_p = (1.0 - blend_weight) * p + blend_weight * base_rate
    return max(1e-9, min(1 - 1e-9, anchored_p))


def _passes_confidence_gate(raw_p: float, threshold: float = CONFIDENCE_GATE_THRESHOLD) -> bool:
    return abs(raw_p - 0.5) > threshold


def sharpen_probability(
    raw_p: float,
    category: Optional[str] = None,
    confidence_signals: Optional[dict] = None,
) -> float:
    if not SHARPENING_ENABLED:
        logger.debug("sharpening_disabled raw_p=%.4f returning_unchanged", raw_p)
        return raw_p

    raw_p = max(1e-9, min(1 - 1e-9, float(raw_p)))

    if not _passes_confidence_gate(raw_p):
        logger.debug(
            "confidence_gate_blocked raw_p=%.4f distance_from_half=%.4f threshold=%.4f",
            raw_p,
            abs(raw_p - 0.5),
            CONFIDENCE_GATE_THRESHOLD,
        )
        return raw_p

    after_temperature = _temperature_scale(raw_p, TEMPERATURE)
    after_anchoring = _base_rate_anchor(after_temperature, category, BASE_RATE_BLEND_WEIGHT)

    final_p = after_anchoring

    logger.info(
        "sharpening_pipeline "
        "raw_p=%.4f "
        "after_temperature_scale=%.4f "
        "after_base_rate_anchor=%.4f "
        "final_p=%.4f "
        "category=%s "
        "delta=%.4f",
        raw_p,
        after_temperature,
        after_anchoring,
        final_p,
        category,
        final_p - raw_p,
    )

    return final_p


def calibrate(raw_p: float, category: Optional[str] = None, confidence_signals: Optional[dict] = None) -> float:
    p = max(1e-9, min(1 - 1e-9, float(raw_p)))

    adjusted_p = _existing_calibration_adjustments(p)

    final_p = sharpen_probability(adjusted_p, category=category, confidence_signals=confidence_signals)

    logger.info(
        "calibration_complete raw_p=%.4f post_existing_adjustments=%.4f final_p=%.4f",
        raw_p,
        adjusted_p,
        final_p,
    )

    return final_p


def _existing_calibration_adjustments(p: float) -> float:
    return p


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    test_cases = [
        (0.52, "policy_passage"),
        (0.45, "incumbent_win"),
        (0.70, "election_incumbent"),
        (0.30, "legislation_pass"),
        (0.50, "default"),
        (0.501, None),
        (0.99, "tech_product_launch"),
        (0.01, "company_bankruptcy"),
    ]

    print("raw_p | category | final_p | delta")
    print("-" * 50)
    for raw_p, cat in test_cases:
        result = calibrate(raw_p, category=cat)
        print(f"{raw_p:.3f} | {str(cat):<22} | {result:.4f} | {result - raw_p:+.4f}")