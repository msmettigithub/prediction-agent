import math
from typing import Optional, Dict
from config import EXTREMIZE_ALPHA

BASE_RATES: Dict[str, float] = {
    "geopolitical_stability": 0.70,
    "tech_earnings_beat": 0.65,
    "policy_continuation": 0.60,
}

DEFAULT_BASE_RATE = 0.55
BASE_RATE_MIXING_WEIGHT = 0.30
MODEL_SIGNAL_WEIGHT = 0.70
CONFIDENCE_THRESHOLD_LOW = 0.40
CONFIDENCE_THRESHOLD_HIGH = 0.60


def extremize(p: float, alpha: float = EXTREMIZE_ALPHA) -> float:
    eps = 1e-9
    p = max(eps, min(1.0 - eps, p))
    p_alpha = p ** alpha
    one_minus_p_alpha = (1.0 - p) ** alpha
    return p_alpha / (p_alpha + one_minus_p_alpha)


def anchor_to_base_rate(p: float, category: Optional[str] = None) -> float:
    base_rate = BASE_RATES.get(category, DEFAULT_BASE_RATE) if category else DEFAULT_BASE_RATE
    anchored = MODEL_SIGNAL_WEIGHT * p + BASE_RATE_MIXING_WEIGHT * base_rate
    return anchored


def platt_scale(p: float, a: float = 1.0, b: float = 0.0) -> float:
    eps = 1e-9
    p = max(eps, min(1.0 - eps, p))
    logit_p = math.log(p / (1.0 - p))
    scaled_logit = a * logit_p + b
    return 1.0 / (1.0 + math.exp(-scaled_logit))


def calibrate(
    raw_prob: float,
    category: Optional[str] = None,
    platt_a: float = 1.0,
    platt_b: float = 0.0,
    alpha: Optional[float] = None,
) -> float:
    if not (0.0 <= raw_prob <= 1.0):
        raise ValueError(f"raw_prob must be in [0, 1], got {raw_prob}")

    p = platt_scale(raw_prob, a=platt_a, b=platt_b)

    p = anchor_to_base_rate(p, category=category)

    if not (CONFIDENCE_THRESHOLD_LOW <= p <= CONFIDENCE_THRESHOLD_HIGH):
        effective_alpha = alpha if alpha is not None else EXTREMIZE_ALPHA
        p = extremize(p, alpha=effective_alpha)

    p = max(1e-9, min(1.0 - 1e-9, p))
    return p


def batch_calibrate(
    raw_probs: list,
    categories: Optional[list] = None,
    platt_a: float = 1.0,
    platt_b: float = 0.0,
    alpha: Optional[float] = None,
) -> list:
    if categories is None:
        categories = [None] * len(raw_probs)

    if len(categories) != len(raw_probs):
        raise ValueError("raw_probs and categories must have the same length")

    return [
        calibrate(p, category=cat, platt_a=platt_a, platt_b=platt_b, alpha=alpha)
        for p, cat in zip(raw_probs, categories)
    ]