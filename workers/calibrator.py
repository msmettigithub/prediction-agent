workers/calibrator.py
import math
import logging
from typing import Optional

logger = logging.getLogger(__name__)

SIGNAL_RELIABILITY_WEIGHTS = {
    "base_rate": 1.2,
    "trend_momentum": 1.0,
    "sentiment": 0.8,
    "news_recency": 0.7,
}

DEFAULT_WEIGHT = 1.0

UNCERTAIN_BAND_LOW = 0.42
UNCERTAIN_BAND_HIGH = 0.58
ALPHA_UNCERTAIN = 1.8
ALPHA_CONFIDENT = 1.3
ALPHA_DEFAULT = 1.5


def extremize(p: float, alpha: float = ALPHA_DEFAULT) -> float:
    if p <= 0.0:
        return 0.0
    if p >= 1.0:
        return 1.0
    p_a = math.pow(p, alpha)
    one_minus_p_a = math.pow(1.0 - p, alpha)
    denom = p_a + one_minus_p_a
    if denom == 0.0:
        return p
    return p_a / denom


def _select_alpha(p: float) -> float:
    if UNCERTAIN_BAND_LOW <= p <= UNCERTAIN_BAND_HIGH:
        return ALPHA_UNCERTAIN
    return ALPHA_CONFIDENT


def aggregate_signals(
    signals: dict,
    backtest_mode: bool = False,
    backtest_log: Optional[list] = None,
) -> float:
    if not signals:
        logger.warning("aggregate_signals called with empty signals dict; returning 0.5")
        return 0.5

    total_weight = 0.0
    weighted_sum = 0.0

    for signal_name, signal_value in signals.items():
        weight = SIGNAL_RELIABILITY_WEIGHTS.get(signal_name, DEFAULT_WEIGHT)
        try:
            val = float(signal_value)
        except (TypeError, ValueError):
            logger.warning(
                "Signal '%s' has non-numeric value '%s'; skipping",
                signal_name,
                signal_value,
            )
            continue
        val = max(0.0, min(1.0, val))
        weighted_sum += weight * val
        total_weight += weight

    if total_weight == 0.0:
        logger.warning("Total signal weight is zero; returning 0.5")
        return 0.5

    pre_extremize_prob = weighted_sum / total_weight
    pre_extremize_prob = max(0.0, min(1.0, pre_extremize_prob))

    alpha = _select_alpha(pre_extremize_prob)
    post_extremize_prob = extremize(pre_extremize_prob, alpha=alpha)

    if backtest_mode:
        entry = {
            "pre_extremize": pre_extremize_prob,
            "post_extremize": post_extremize_prob,
            "alpha_used": alpha,
            "signals": dict(signals),
            "weights_used": {
                k: SIGNAL_RELIABILITY_WEIGHTS.get(k, DEFAULT_WEIGHT)
                for k in signals
            },
        }
        logger.debug("Backtest calibration entry: %s", entry)
        if backtest_log is not None:
            backtest_log.append(entry)

    logger.info(
        "Calibrator: pre_extremize=%.4f alpha=%.2f post_extremize=%.4f",
        pre_extremize_prob,
        alpha,
        post_extremize_prob,
    )

    return post_extremize_prob


def calibrate(
    signals: dict,
    backtest_mode: bool = False,
    backtest_log: Optional[list] = None,
) -> float:
    return aggregate_signals(
        signals=signals,
        backtest_mode=backtest_mode,
        backtest_log=backtest_log,
    )