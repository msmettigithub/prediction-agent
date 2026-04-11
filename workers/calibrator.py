import numpy as np
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def logit(p: float) -> float:
    p = np.clip(p, 1e-9, 1 - 1e-9)
    return float(np.log(p / (1 - p)))


def sigmoid(l: float) -> float:
    return float(1.0 / (1.0 + np.exp(-l)))


def count_agreeing_signals(
    p: float,
    base_rate: Optional[float] = None,
    trend: Optional[float] = None,
    sentiment: Optional[float] = None,
    momentum: Optional[float] = None,
) -> int:
    direction = p >= 0.5
    n_agreeing = 0
    for signal in [base_rate, trend, sentiment, momentum]:
        if signal is not None:
            if (signal >= 0.5) == direction:
                n_agreeing += 1
    return n_agreeing


def sharpen(
    p: float,
    base_rate: Optional[float] = None,
    trend: Optional[float] = None,
    sentiment: Optional[float] = None,
    momentum: Optional[float] = None,
) -> Optional[float]:
    p = float(np.clip(p, 1e-9, 1 - 1e-9))

    n_agreeing = count_agreeing_signals(p, base_rate, trend, sentiment, momentum)

    raw_alpha = 1.0 + 0.15 * (n_agreeing - 1)
    alpha = float(np.clip(raw_alpha, 1.0, 1.8))

    l = logit(p)
    l_new = l * alpha
    p_new = sigmoid(l_new)

    p_new = float(np.clip(p_new, 0.05, 0.95))

    if abs(p_new - 0.5) < 0.04:
        logger.debug(
            "Suppressing trade: p_new=%.4f too close to 0.5 (edge=%.4f < 0.04)",
            p_new,
            abs(p_new - 0.5),
        )
        return None

    logger.debug(
        "sharpen: p_in=%.4f n_agreeing=%d alpha=%.3f L=%.4f L_new=%.4f p_out=%.4f",
        p,
        n_agreeing,
        alpha,
        l,
        l_new,
        p_new,
    )

    return p_new


def calibrate(
    p: float,
    base_rate: Optional[float] = None,
    trend: Optional[float] = None,
    sentiment: Optional[float] = None,
    momentum: Optional[float] = None,
) -> Optional[float]:
    return sharpen(p, base_rate=base_rate, trend=trend, sentiment=sentiment, momentum=momentum)