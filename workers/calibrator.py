import numpy as np
import logging
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)

CATEGORY_BASE_RATES: Dict[str, float] = {
    "geopolitical": 0.3,
    "tech": 0.55,
    "market": 0.55,
    "policy": 0.4,
    "default": 0.5,
}


def temperature_sharpen(p: float, T: float) -> float:
    p = float(np.clip(p, 1e-6, 1 - 1e-6))
    p_t = p ** (1.0 / T)
    q_t = (1.0 - p) ** (1.0 / T)
    denom = p_t + q_t
    if denom == 0:
        return p
    return p_t / denom


def log_odds_sharpen(p: float, stretch: float) -> float:
    p = float(np.clip(p, 0.02, 0.98))
    log_odds = np.log(p / (1.0 - p))
    sharpened = log_odds * stretch
    return float(1.0 / (1.0 + np.exp(-sharpened)))


def get_base_rate(category: Optional[str]) -> float:
    if category is None:
        return CATEGORY_BASE_RATES["default"]
    key = category.lower().strip()
    for k, v in CATEGORY_BASE_RATES.items():
        if k in key:
            return v
    return CATEGORY_BASE_RATES["default"]


def assess_signal_agreement(
    signals: Optional[Dict[str, Optional[float]]],
    p_raw: float,
) -> str:
    if not signals:
        return "neutral"

    direction = "up" if p_raw >= 0.5 else "down"
    threshold = 0.5

    agreements = 0
    conflicts = 0
    valid = 0

    for name, val in signals.items():
        if val is None:
            continue
        valid += 1
        sig_direction = "up" if val >= threshold else "down"
        if sig_direction == direction:
            agreements += 1
        else:
            conflicts += 1

    if valid == 0:
        return "neutral"

    if agreements >= 2 and conflicts == 0:
        return "strong_agree"
    if agreements > conflicts:
        return "agree"
    if conflicts > agreements:
        return "conflict"
    return "neutral"


def calibrate(
    p_raw: float,
    category: Optional[str] = None,
    signals: Optional[Dict[str, Optional[float]]] = None,
    n_concordant_signals: Optional[int] = None,
) -> float:
    p_raw = float(np.clip(p_raw, 1e-6, 1 - 1e-6))

    # Stage 1: Base sharpening with T=0.6
    # Maps: 0.55->0.59, 0.60->0.67, 0.65->0.73, 0.70->0.79
    p_sharp = temperature_sharpen(p_raw, T=0.6)

    # Stage 2: Base-rate anchor
    # Blend toward category base rate when raw prob is within 10pp of 0.5
    base_rate = get_base_rate(category)
    if abs(p_raw - 0.5) <= 0.10:
        p_sharp = 0.80 * p_sharp + 0.20 * base_rate

    # Stage 3: Confidence gate based on signal agreement
    agreement = "neutral"
    if signals is not None:
        agreement = assess_signal_agreement(signals, p_raw)
    elif n_concordant_signals is not None:
        if n_concordant_signals >= 3:
            agreement = "strong_agree"
        elif n_concordant_signals == 2:
            agreement = "agree"
        elif n_concordant_signals <= 0:
            agreement = "conflict"

    if agreement == "strong_agree":
        # Strong agreement: apply additional sharpening T=0.45
        p_final = temperature_sharpen(p_sharp, T=0.45)
    elif agreement == "agree":
        # Moderate agreement: stick with T=0.6 result (already applied)
        p_final = p_sharp
    elif agreement == "conflict":
        # Conflicting signals: revert to mild sharpening T=0.8
        p_final = temperature_sharpen(p_raw, T=0.8)
        if abs(p_raw - 0.5) <= 0.10:
            p_final = 0.80 * p_final + 0.20 * base_rate
    else:
        p_final = p_sharp

    # Stage 4: Safety clamp
    p_final = float(np.clip(p_final, 0.05, 0.95))

    logger.info(
        f"CALIBRATOR_ACTIVE p_in={p_raw:.4f} p_sharp={p_sharp:.4f} "
        f"p_out={p_final:.4f} category={category} agreement={agreement} "
        f"base_rate={base_rate:.3f}"
    )

    return p_final


def calibrate_batch(
    probabilities: List[float],
    categories: Optional[List[Optional[str]]] = None,
    signals_list: Optional[List[Optional[Dict[str, Optional[float]]]]] = None,
) -> List[float]:
    n = len(probabilities)
    cats = categories if categories is not None else [None] * n
    sigs = signals_list if signals_list is not None else [None] * n

    results = []
    for p, cat, sig in zip(probabilities, cats, sigs):
        results.append(calibrate(p_raw=p, category=cat, signals=sig))
    return results


def diagnose_distribution(probabilities: List[float]) -> Dict[str, float]:
    arr = np.array(probabilities, dtype=float)
    return {
        "mean": float(np.mean(arr)),
        "std": float(np.std(arr)),
        "min": float(np.min(arr)),
        "max": float(np.max(arr)),
        "pct_near_50": float(np.mean(np.abs(arr - 0.5) < 0.1)),
        "pct_extreme": float(np.mean((arr < 0.2) | (arr > 0.8))),
    }