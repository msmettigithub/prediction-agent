import numpy as np
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def extremize(p: float, a: float) -> float:
    p = float(np.clip(p, 1e-9, 1 - 1e-9))
    pa = p ** a
    return pa / (pa + (1.0 - p) ** a)


def choose_exponent(signal_count: int, agreement_fraction: float) -> float:
    if signal_count >= 3 and agreement_fraction >= 0.75:
        a = 1.5 + 0.5 * min((agreement_fraction - 0.75) / 0.25, 1.0)
    elif signal_count >= 3 and agreement_fraction >= 0.6:
        a = 1.5
    else:
        a = 1.0
    return round(a, 3)


def confidence_multiplier(source_hits: int, max_sources: int = 6) -> float:
    ratio = min(source_hits, max_sources) / max_sources
    return 0.8 + 0.4 * ratio


def calibrate(
    raw_probability: float,
    signals: Optional[list] = None,
    source_flags: Optional[dict] = None,
) -> dict:
    if signals is None:
        signals = []
    if source_flags is None:
        source_flags = {}

    p = float(np.clip(raw_probability, 1e-9, 1 - 1e-9))

    n = len(signals)
    if n > 0:
        bullish = sum(1 for s in signals if s > 0)
        bearish = sum(1 for s in signals if s < 0)
        dominant = max(bullish, bearish)
        agreement_fraction = dominant / n
    else:
        agreement_fraction = 0.5

    a = choose_exponent(n, agreement_fraction)

    available_sources = [
        "yfinance",
        "newsapi",
        "pytrends",
        "vaderSentiment",
        "statsmodels",
        "pandas-ta",
    ]
    source_hits = sum(1 for src in available_sources if source_flags.get(src, False))
    cm = confidence_multiplier(source_hits, max_sources=len(available_sources))

    p_pre = p
    p_ext = extremize(p, a)

    logit_pre = float(np.log(p_pre / (1.0 - p_pre)))
    logit_ext = float(np.log(max(p_ext, 1e-9) / max(1.0 - p_ext, 1e-9)))
    logit_final = logit_ext * cm
    p_scaled = float(1.0 / (1.0 + np.exp(-logit_final)))

    p_final = float(np.clip(p_scaled, 0.05, 0.95))

    logger.info(
        "calibrator | pre=%.4f ext=%.4f final=%.4f | a=%.3f cm=%.3f "
        "signals=%d agree=%.2f sources=%d",
        p_pre,
        p_ext,
        p_final,
        a,
        cm,
        n,
        agreement_fraction,
        source_hits,
    )

    return {
        "probability": p_final,
        "pre_extremize": p_pre,
        "post_extremize": p_ext,
        "exponent": a,
        "confidence_multiplier": cm,
        "signal_count": n,
        "agreement_fraction": agreement_fraction,
        "source_hits": source_hits,
        "rl_feedback": {
            "pre": p_pre,
            "post": p_final,
            "a": a,
            "cm": cm,
            "n_signals": n,
            "agreement": agreement_fraction,
        },
    }


def calibrate_batch(
    raw_probabilities: list,
    signals_list: Optional[list] = None,
    source_flags_list: Optional[list] = None,
) -> list:
    n = len(raw_probabilities)
    if signals_list is None:
        signals_list = [None] * n
    if source_flags_list is None:
        source_flags_list = [None] * n
    return [
        calibrate(raw_probabilities[i], signals_list[i], source_flags_list[i])
        for i in range(n)
    ]