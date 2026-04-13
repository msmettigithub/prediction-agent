import logging
import math
from typing import Optional

logger = logging.getLogger(__name__)


DEFAULT_EXTREMIZE_EXPONENT = 1.8
MAX_EXTREMIZE_EXPONENT = 2.5
DEFAULT_CONFIDENCE_FLOOR = 0.15


def extremize(p: float, a: float = DEFAULT_EXTREMIZE_EXPONENT) -> float:
    p = max(1e-9, min(1.0 - 1e-9, float(p)))
    numerator = p ** a
    denominator = numerator + (1.0 - p) ** a
    if denominator == 0.0:
        return p
    return numerator / denominator


def apply_confidence_floor(p: float, floor: float = DEFAULT_CONFIDENCE_FLOOR) -> float:
    low = 0.5 - floor
    high = 0.5 + floor
    if low < p < high:
        if p < 0.5:
            return low
        else:
            return high
    return p


def compute_signal_agreement(signals: list) -> float:
    if not signals or len(signals) < 2:
        return 0.0
    directions = []
    for s in signals:
        if s > 0.5:
            directions.append(1)
        elif s < 0.5:
            directions.append(-1)
        else:
            directions.append(0)
    non_neutral = [d for d in directions if d != 0]
    if not non_neutral:
        return 0.0
    agreement = abs(sum(non_neutral)) / len(non_neutral)
    return agreement


def compute_dynamic_exponent(
    base_exponent: float,
    signals: Optional[list],
    max_exponent: float = MAX_EXTREMIZE_EXPONENT,
) -> float:
    if not signals or len(signals) < 2:
        return base_exponent
    agreement = compute_signal_agreement(signals)
    exponent_range = max_exponent - base_exponent
    dynamic_exponent = base_exponent + agreement * exponent_range
    return min(dynamic_exponent, max_exponent)


def calibrate(
    raw_probability: float,
    signals: Optional[list] = None,
    base_exponent: float = DEFAULT_EXTREMIZE_EXPONENT,
    confidence_floor: float = DEFAULT_CONFIDENCE_FLOOR,
    max_exponent: float = MAX_EXTREMIZE_EXPONENT,
) -> float:
    pre_extremize = max(1e-9, min(1.0 - 1e-9, float(raw_probability)))

    a = compute_dynamic_exponent(base_exponent, signals, max_exponent)

    if signals and len(signals) >= 2:
        logger.debug(
            "Signal agreement computed",
            extra={
                "signals": signals,
                "signal_count": len(signals),
                "agreement": compute_signal_agreement(signals),
                "dynamic_exponent": a,
            },
        )

    post_extremize = extremize(pre_extremize, a)
    final = apply_confidence_floor(post_extremize, confidence_floor)

    logger.info(
        "Calibration extremization applied",
        extra={
            "pre_extremize": pre_extremize,
            "post_extremize": post_extremize,
            "final_probability": final,
            "exponent_used": a,
            "confidence_floor": confidence_floor,
            "floor_nudged": final != post_extremize,
        },
    )

    return final


def run(
    raw_probability: float,
    signals: Optional[list] = None,
    exponent: float = DEFAULT_EXTREMIZE_EXPONENT,
    confidence_floor: float = DEFAULT_CONFIDENCE_FLOOR,
) -> dict:
    final = calibrate(
        raw_probability=raw_probability,
        signals=signals,
        base_exponent=exponent,
        confidence_floor=confidence_floor,
    )

    result = {
        "raw_probability": raw_probability,
        "calibrated_probability": final,
        "exponent": exponent,
        "confidence_floor": confidence_floor,
        "signals_used": signals if signals else [],
    }

    logger.info("Calibrator worker run complete", extra=result)
    return result