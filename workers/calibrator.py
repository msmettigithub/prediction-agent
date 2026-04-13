import logging
import math

logger = logging.getLogger(__name__)


def count_signals(signals: dict) -> int:
    count = 0
    if signals.get("base_rate") is not None:
        count += 1
    if signals.get("trend") is not None:
        count += 1
    if signals.get("sentiment") is not None:
        count += 1
    if signals.get("market_data") is not None:
        count += 1
    return count


def extremize(p: float, a: float) -> float:
    if abs(p - 0.5) <= 0.03:
        return p
    pa = p ** a
    one_minus_pa = (1.0 - p) ** a
    denom = pa + one_minus_pa
    if denom == 0:
        return p
    return pa / denom


def clamp(p: float, floor: float = 0.08, ceiling: float = 0.92) -> float:
    return max(floor, min(ceiling, p))


def calibrate(raw_prob: float, signals: dict = None) -> float:
    if signals is None:
        signals = {}

    signal_count = count_signals(signals)
    a = 1.0 + 0.3 * min(signal_count, 5)

    p_raw = float(raw_prob)
    p_extremized = extremize(p_raw, a)
    p_final = clamp(p_extremized)

    logger.info(
        "calibrate: signal_count=%d extremizing_factor=%.3f "
        "p_raw=%.4f p_extremized=%.4f p_final=%.4f "
        "deviation_from_half=%.4f threshold_active=%s",
        signal_count,
        a,
        p_raw,
        p_extremized,
        p_final,
        abs(p_raw - 0.5),
        abs(p_raw - 0.5) > 0.03,
    )

    return p_final


def calibrate_with_history(raw_prob: float, signals: dict = None, question_id: str = None) -> dict:
    if signals is None:
        signals = {}

    signal_count = count_signals(signals)
    a = 1.0 + 0.3 * min(signal_count, 5)

    p_raw = float(raw_prob)
    deviation = abs(p_raw - 0.5)
    threshold_active = deviation > 0.03

    p_extremized = extremize(p_raw, a)
    p_final = clamp(p_extremized)

    result = {
        "probability": p_final,
        "p_raw": p_raw,
        "p_extremized": p_extremized,
        "p_final": p_final,
        "extremizing_factor": a,
        "signal_count": signal_count,
        "deviation_from_half": deviation,
        "threshold_active": threshold_active,
        "question_id": question_id,
    }

    logger.info(
        "calibrate_with_history: question_id=%s signal_count=%d "
        "extremizing_factor=%.3f p_raw=%.4f p_extremized=%.4f "
        "p_final=%.4f rl_feedback=%s",
        question_id,
        signal_count,
        a,
        p_raw,
        p_extremized,
        p_final,
        result,
    )

    return result