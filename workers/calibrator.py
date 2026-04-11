import numpy as np
from typing import Optional


SIGNAL_WEIGHTS = {
    "yfinance": 0.30,
    "newsapi_vader": 0.25,
    "pytrends": 0.20,
    "statsmodels": 0.25,
}

PLATT_A = 1.3
PLATT_B = 0.0

MIN_EDGE_THRESHOLD = 0.05


def logit(p: float) -> float:
    p = np.clip(p, 1e-6, 1.0 - 1e-6)
    return float(np.log(p / (1.0 - p)))


def sigmoid(x: float) -> float:
    return float(1.0 / (1.0 + np.exp(-x)))


def platt_scale(p: float, a: float = PLATT_A, b: float = PLATT_B) -> float:
    lo = logit(p)
    return sigmoid(a * lo + b)


def concordance_multiplier(signals: list) -> float:
    if not signals:
        return 1.0

    above = sum(1 for s in signals if s > 0.5)
    below = sum(1 for s in signals if s < 0.5)
    n = len(signals)

    if n >= 3 and (above == n or below == n):
        return 1.5

    if above > 0 and below > 0:
        return 0.5

    return 1.0


def weighted_aggregate(signal_probs: dict) -> Optional[float]:
    total_weight = 0.0
    weighted_logit_sum = 0.0

    for source, prob in signal_probs.items():
        weight = SIGNAL_WEIGHTS.get(source, 0.0)
        if weight == 0.0 or prob is None:
            continue
        lo = logit(float(prob))
        weighted_logit_sum += weight * lo
        total_weight += weight

    if total_weight == 0.0:
        return None

    aggregated_logit = weighted_logit_sum / total_weight
    return sigmoid(aggregated_logit)


def calibrate(
    raw_prob: float,
    signal_probs: Optional[dict] = None,
    adjustment: float = 0.0,
    platt_a: float = PLATT_A,
    platt_b: float = PLATT_B,
) -> Optional[float]:
    p = float(np.clip(raw_prob, 1e-6, 1.0 - 1e-6))

    if signal_probs:
        aggregated = weighted_aggregate(signal_probs)
        if aggregated is not None:
            p = aggregated

    lo = logit(p)

    if signal_probs:
        signal_list = [v for v in signal_probs.values() if v is not None]
        mult = concordance_multiplier(signal_list)
    else:
        mult = 1.0

    lo += adjustment * mult

    p = sigmoid(lo)

    p = platt_scale(p, a=platt_a, b=platt_b)

    if abs(p - 0.5) < MIN_EDGE_THRESHOLD:
        return None

    return float(np.clip(p, 1e-6, 1.0 - 1e-6))


def should_skip_trade(calibrated_prob: Optional[float]) -> bool:
    if calibrated_prob is None:
        return True
    return abs(calibrated_prob - 0.5) < MIN_EDGE_THRESHOLD


def calibrate_batch(
    raw_probs: list,
    signal_probs_list: Optional[list] = None,
    adjustments: Optional[list] = None,
    platt_a: float = PLATT_A,
    platt_b: float = PLATT_B,
) -> list:
    results = []
    n = len(raw_probs)

    for i in range(n):
        rp = raw_probs[i]
        sp = signal_probs_list[i] if signal_probs_list and i < len(signal_probs_list) else None
        adj = adjustments[i] if adjustments and i < len(adjustments) else 0.0

        cal = calibrate(rp, signal_probs=sp, adjustment=adj, platt_a=platt_a, platt_b=platt_b)
        results.append(cal)

    return results


def extremize(p: float, alpha: float = 1.8) -> float:
    p = float(np.clip(p, 1e-6, 1.0 - 1e-6))
    p_alpha = p ** alpha
    denom = p_alpha + (1.0 - p) ** alpha
    if denom == 0.0:
        return p
    return float(p_alpha / denom)


def full_pipeline(
    raw_prob: float,
    signal_probs: Optional[dict] = None,
    adjustment: float = 0.0,
    use_extremize: bool = False,
    extremize_alpha: float = 1.8,
    platt_a: float = PLATT_A,
    platt_b: float = PLATT_B,
) -> Optional[float]:
    cal = calibrate(
        raw_prob,
        signal_probs=signal_probs,
        adjustment=adjustment,
        platt_a=platt_a,
        platt_b=platt_b,
    )

    if cal is None:
        return None

    if use_extremize:
        cal = extremize(cal, alpha=extremize_alpha)
        if abs(cal - 0.5) < MIN_EDGE_THRESHOLD:
            return None

    return cal