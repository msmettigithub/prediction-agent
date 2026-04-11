import numpy as np
import logging
from config import SHARPENING_ALPHA

logger = logging.getLogger(__name__)

BOUNDS_MIN = 0.05
BOUNDS_MAX = 0.95
MIN_CONFIRMING_SIGNALS = 2


def sharpen(p: float, alpha: float = None) -> float:
    if alpha is None:
        alpha = SHARPENING_ALPHA
    sign = 1.0 if p >= 0.5 else -1.0
    deviation = abs(2 * (p - 0.5))
    sharpened = 0.5 + sign * (deviation ** alpha) / 2
    return float(np.clip(sharpened, BOUNDS_MIN, BOUNDS_MAX))


def count_confirming_signals(signals: dict) -> int:
    directions = []
    if "base_rate" in signals:
        br = signals["base_rate"]
        if br is not None:
            directions.append(1 if br > 0.5 else -1)
    if "trend" in signals:
        tr = signals["trend"]
        if tr is not None:
            directions.append(1 if tr > 0 else -1)
    if "sentiment" in signals:
        se = signals["sentiment"]
        if se is not None:
            directions.append(1 if se > 0 else -1)
    if not directions:
        return 0
    dominant = 1 if sum(directions) > 0 else -1
    confirming = sum(1 for d in directions if d == dominant)
    return confirming


def calibrate(p_raw: float, signals: dict = None, alpha: float = None) -> float:
    if alpha is None:
        alpha = SHARPENING_ALPHA

    p_clamped = float(np.clip(p_raw, BOUNDS_MIN, BOUNDS_MAX))

    if signals is None:
        signals = {}

    confirming = count_confirming_signals(signals)

    if confirming >= MIN_CONFIRMING_SIGNALS:
        p_sharp = sharpen(p_clamped, alpha=alpha)
        logger.info(
            "sharpening applied | pre=%.4f post=%.4f alpha=%.3f confirming_signals=%d signals=%s",
            p_clamped,
            p_sharp,
            alpha,
            confirming,
            signals,
        )
        return p_sharp
    else:
        p_out = float(np.clip(p_clamped, BOUNDS_MIN, BOUNDS_MAX))
        logger.info(
            "sharpening skipped | pre=%.4f post=%.4f confirming_signals=%d signals=%s",
            p_clamped,
            p_out,
            confirming,
            signals,
        )
        return p_out


def extremize(p: float, alpha: float = 1.8) -> float:
    p = float(np.clip(p, 0.01, 0.99))
    p_a = p ** alpha
    result = p_a / (p_a + (1 - p) ** alpha)
    return float(np.clip(result, BOUNDS_MIN, BOUNDS_MAX))


def should_trade(p: float, min_edge: float = 0.08) -> bool:
    return abs(p - 0.5) > min_edge