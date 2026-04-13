import logging
import math
import os

logger = logging.getLogger(__name__)


def temperature_sharpen(p: float, T: float = 0.6) -> float:
    p = max(1e-9, min(1 - 1e-9, p))
    p_T = p ** T
    q_T = (1.0 - p) ** T
    return p_T / (p_T + q_T)


def adjust_probability(
    p: float,
    sentiment_signal: float | None = None,
    trend_signal: float | None = None,
    base_rate_signal: float | None = None,
    T: float = 0.6,
    agreement_boost: float = 0.05,
    floor: float = 0.05,
    ceiling: float = 0.95,
) -> float:
    p = max(1e-9, min(1 - 1e-9, float(p)))
    pre_sharp = p

    direction = 1 if p >= 0.5 else -1

    signals = []
    if sentiment_signal is not None:
        signals.append(sentiment_signal)
    if trend_signal is not None:
        signals.append(trend_signal)
    if base_rate_signal is not None:
        signals.append(base_rate_signal)

    agreeing = sum(1 for s in signals if (s >= 0.5) == (p >= 0.5))

    p_sharp = temperature_sharpen(p, T=T)

    if agreeing >= 3:
        p_sharp = p_sharp + direction * agreement_boost
        logger.info(
            "signal_agreement_boost applied: %d signals agree, boost=%.4f",
            agreeing,
            agreement_boost,
        )

    p_sharp = max(floor, min(ceiling, p_sharp))

    logger.info(
        "calibrator: pre_sharpening=%.6f post_sharpening=%.6f "
        "(T=%.2f, agreeing_signals=%d/%d, floor=%.2f, ceiling=%.2f)",
        pre_sharp,
        p_sharp,
        T,
        agreeing,
        len(signals),
        floor,
        ceiling,
    )

    return p_sharp


def calibrate(
    p: float,
    sentiment_signal: float | None = None,
    trend_signal: float | None = None,
    base_rate_signal: float | None = None,
) -> float:
    return adjust_probability(
        p=p,
        sentiment_signal=sentiment_signal,
        trend_signal=trend_signal,
        base_rate_signal=base_rate_signal,
    )