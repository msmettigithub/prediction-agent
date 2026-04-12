import numpy as np
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def extremize(p: float, alpha: float) -> float:
    p = np.clip(p, 1e-9, 1 - 1e-9)
    pa = p ** alpha
    one_minus_pa = (1.0 - p) ** alpha
    return pa / (pa + one_minus_pa)


def count_concordant_signals(
    base_rate_prior: Optional[float],
    trend_signal: Optional[float],
    sentiment_signal: Optional[float],
    market_momentum: Optional[float],
    threshold: float = 0.5,
) -> int:
    signals = [base_rate_prior, trend_signal, sentiment_signal, market_momentum]
    valid = [s for s in signals if s is not None]
    if len(valid) == 0:
        return 0
    above = sum(1 for s in valid if s > threshold)
    below = sum(1 for s in valid if s < threshold)
    return max(above, below)


def calibrate(
    raw_probability: float,
    base_rate_prior: Optional[float] = None,
    trend_signal: Optional[float] = None,
    sentiment_signal: Optional[float] = None,
    market_momentum: Optional[float] = None,
    alpha_3: float = 1.5,
    alpha_4: float = 2.0,
    floor: float = 0.08,
    ceiling: float = 0.92,
) -> float:
    p = float(np.clip(raw_probability, 1e-9, 1 - 1e-9))

    p_base_calibrated = _apply_base_calibration(p)

    n_concordant = count_concordant_signals(
        base_rate_prior=base_rate_prior,
        trend_signal=trend_signal,
        sentiment_signal=sentiment_signal,
        market_momentum=market_momentum,
    )

    pre_extremize = p_base_calibrated

    if n_concordant >= 4:
        alpha = alpha_4
        p_extremized = extremize(p_base_calibrated, alpha)
    elif n_concordant >= 3:
        alpha = alpha_3
        p_extremized = extremize(p_base_calibrated, alpha)
    else:
        alpha = 1.0
        p_extremized = p_base_calibrated

    p_final = float(np.clip(p_extremized, floor, ceiling))

    logger.info(
        "calibration",
        extra={
            "raw_probability": raw_probability,
            "base_calibrated": pre_extremize,
            "post_extremized": p_extremized,
            "final_clamped": p_final,
            "n_concordant_signals": n_concordant,
            "alpha_used": alpha,
            "floor": floor,
            "ceiling": ceiling,
        },
    )

    logger.debug(
        "calibration_detail | raw=%.4f base=%.4f pre_extremize=%.4f "
        "post_extremize=%.4f final=%.4f n_concordant=%d alpha=%.2f",
        raw_probability,
        p_base_calibrated,
        pre_extremize,
        p_extremized,
        p_final,
        n_concordant,
        alpha,
    )

    return p_final


def _apply_base_calibration(p: float) -> float:
    p = float(np.clip(p, 1e-9, 1 - 1e-9))

    if p < 0.1:
        shrink_factor = 0.85
    elif p < 0.2:
        shrink_factor = 0.90
    elif p > 0.9:
        shrink_factor = 0.85
    elif p > 0.8:
        shrink_factor = 0.90
    else:
        shrink_factor = 0.95

    p_shrunk = 0.5 + (p - 0.5) * shrink_factor
    return float(np.clip(p_shrunk, 1e-9, 1 - 1e-9))


def batch_calibrate(
    raw_probabilities: list,
    base_rate_priors: Optional[list] = None,
    trend_signals: Optional[list] = None,
    sentiment_signals: Optional[list] = None,
    market_momenta: Optional[list] = None,
    alpha_3: float = 1.5,
    alpha_4: float = 2.0,
    floor: float = 0.08,
    ceiling: float = 0.92,
) -> list:
    n = len(raw_probabilities)

    def _get(lst, i):
        if lst is None or i >= len(lst):
            return None
        return lst[i]

    results = []
    for i in range(n):
        result = calibrate(
            raw_probability=raw_probabilities[i],
            base_rate_prior=_get(base_rate_priors, i),
            trend_signal=_get(trend_signals, i),
            sentiment_signal=_get(sentiment_signals, i),
            market_momentum=_get(market_momenta, i),
            alpha_3=alpha_3,
            alpha_4=alpha_4,
            floor=floor,
            ceiling=ceiling,
        )
        results.append(result)

    return results