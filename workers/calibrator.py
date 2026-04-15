import numpy as np
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def count_concordant_signals(signals: dict) -> int:
    direction_votes = []

    signal_keys = [
        "yfinance_prob",
        "newsapi_prob",
        "pytrends_prob",
        "vader_prob",
        "statsmodels_prob",
        "pandas_ta_prob",
    ]

    for key in signal_keys:
        if key in signals:
            val = signals[key]
            if val is not None and not (isinstance(val, float) and np.isnan(val)):
                try:
                    direction_votes.append(1 if float(val) > 0.5 else 0)
                except (TypeError, ValueError):
                    pass

    if len(direction_votes) == 0:
        return 0

    up_count = sum(direction_votes)
    down_count = len(direction_votes) - up_count
    concordance = max(up_count, down_count)
    return concordance


def apply_concordance_stretch(p: float, concordance: int, total_signals: int) -> float:
    p = float(p)
    sign = 1.0 if p >= 0.5 else -1.0
    deviation = abs(p - 0.5)

    if total_signals == 0:
        exponent = 1.2
    elif concordance >= 4:
        exponent = 0.7
    elif concordance == 3:
        exponent = 0.85
    else:
        exponent = 1.2

    stretched_deviation = deviation ** exponent
    p_final = 0.5 + sign * stretched_deviation
    p_final = float(np.clip(p_final, 0.05, 0.95))

    logger.debug(
        f"Concordance stretch: p={p:.4f}, concordance={concordance}/{total_signals}, "
        f"exponent={exponent}, p_final={p_final:.4f}"
    )

    return p_final


def calibrate(raw_prob: float, signals: Optional[dict] = None) -> float:
    p = float(np.clip(raw_prob, 1e-6, 1.0 - 1e-6))

    if signals is not None:
        concordance = count_concordant_signals(signals)
        total_signals = sum(
            1
            for key in [
                "yfinance_prob",
                "newsapi_prob",
                "pytrends_prob",
                "vader_prob",
                "statsmodels_prob",
                "pandas_ta_prob",
            ]
            if key in signals
            and signals[key] is not None
            and not (isinstance(signals[key], float) and np.isnan(signals[key]))
        )
        p = apply_concordance_stretch(p, concordance, total_signals)
    else:
        p = float(np.clip(p, 0.05, 0.95))

    return p


def calibrate_batch(raw_probs: list, signals_list: Optional[list] = None) -> list:
    if signals_list is None:
        signals_list = [None] * len(raw_probs)

    if len(signals_list) != len(raw_probs):
        logger.warning(
            f"signals_list length {len(signals_list)} != raw_probs length {len(raw_probs)}, "
            f"padding with None"
        )
        signals_list = list(signals_list) + [None] * (len(raw_probs) - len(signals_list))

    return [calibrate(p, s) for p, s in zip(raw_probs, signals_list)]


def get_concordance_info(signals: dict) -> dict:
    signal_keys = [
        "yfinance_prob",
        "newsapi_prob",
        "pytrends_prob",
        "vader_prob",
        "statsmodels_prob",
        "pandas_ta_prob",
    ]

    available = []
    for key in signal_keys:
        if key in signals:
            val = signals[key]
            if val is not None and not (isinstance(val, float) and np.isnan(val)):
                try:
                    available.append((key, float(val)))
                except (TypeError, ValueError):
                    pass

    if len(available) == 0:
        return {
            "concordance": 0,
            "total_signals": 0,
            "up_votes": 0,
            "down_votes": 0,
            "exponent": 1.2,
            "signal_details": [],
        }

    direction_votes = [1 if val > 0.5 else 0 for _, val in available]
    up_count = sum(direction_votes)
    down_count = len(direction_votes) - up_count
    concordance = max(up_count, down_count)
    total_signals = len(available)

    if concordance >= 4:
        exponent = 0.7
    elif concordance == 3:
        exponent = 0.85
    else:
        exponent = 1.2

    return {
        "concordance": concordance,
        "total_signals": total_signals,
        "up_votes": up_count,
        "down_votes": down_count,
        "exponent": exponent,
        "signal_details": [(key, val) for key, val in available],
    }