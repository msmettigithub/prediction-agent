import numpy as np
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


CATEGORY_BASE_RATES: Dict[str, float] = {
    "default": 0.50,
    "earnings_beat": 0.52,
    "earnings_miss": 0.48,
    "momentum": 0.53,
    "reversal": 0.47,
    "breakout": 0.51,
    "breakdown": 0.49,
    "macro_positive": 0.51,
    "macro_negative": 0.49,
}


def _compute_alpha(signal_count: int, signal_consistent: bool) -> float:
    if signal_count < 2:
        return 1.0
    if signal_count >= 5 and signal_consistent:
        return 2.0
    if signal_count >= 3:
        return 1.5
    return 1.2


def extremize(p: float, alpha: float) -> float:
    p = float(np.clip(p, 1e-6, 1.0 - 1e-6))
    numerator = p ** alpha
    denominator = numerator + (1.0 - p) ** alpha
    return float(numerator / denominator)


def anchor_to_base_rate(
    p_cal: float,
    p_base: float,
    w_base: float,
    w_model: float,
) -> float:
    total = w_base + w_model
    if total <= 0:
        return p_cal
    return float((w_base * p_base + w_model * p_cal) / total)


def calibrate(
    p_raw: float,
    signal_count: int = 0,
    signal_consistent: bool = False,
    category: Optional[str] = None,
    recency_weight: float = 1.0,
    metadata: Optional[Dict[str, Any]] = None,
) -> float:
    p_raw = float(np.clip(p_raw, 1e-6, 1.0 - 1e-6))

    alpha = _compute_alpha(signal_count, signal_consistent)

    p_cal = extremize(p_raw, alpha)

    cat_key = category if category in CATEGORY_BASE_RATES else "default"
    p_base = CATEGORY_BASE_RATES[cat_key]

    w_base = 1.0
    w_model = float(np.clip(signal_count, 1, 10)) * recency_weight

    p_final = anchor_to_base_rate(p_cal, p_base, w_base, w_model)
    p_final = float(np.clip(p_final, 1e-6, 1.0 - 1e-6))

    logger.info(
        "calibration | pre=%.4f | extremized=%.4f | final=%.4f | "
        "alpha=%.2f | signals=%d | consistent=%s | category=%s | "
        "w_base=%.2f | w_model=%.2f",
        p_raw,
        p_cal,
        p_final,
        alpha,
        signal_count,
        signal_consistent,
        cat_key,
        w_base,
        w_model,
    )

    return p_final


def calibrate_batch(
    records: list,
) -> list:
    results = []
    for rec in records:
        p_raw = rec.get("probability", 0.5)
        signal_count = rec.get("signal_count", 0)
        signal_consistent = rec.get("signal_consistent", False)
        category = rec.get("category", None)
        recency_weight = rec.get("recency_weight", 1.0)
        metadata = rec.get("metadata", None)

        p_final = calibrate(
            p_raw=p_raw,
            signal_count=signal_count,
            signal_consistent=signal_consistent,
            category=category,
            recency_weight=recency_weight,
            metadata=metadata,
        )
        results.append({**rec, "probability_calibrated": p_final})

    return results