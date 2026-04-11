import math
import logging
from typing import Optional

logger = logging.getLogger(__name__)

CALIBRATION_STRETCH_FACTOR = 1.5
CALIBRATION_STRETCH_FACTOR_AGREEMENT = 1.7
CALIBRATION_CLAMP_MIN = 0.05
CALIBRATION_CLAMP_MAX = 0.95


def logit(p: float) -> float:
    p = max(1e-9, min(1 - 1e-9, p))
    return math.log(p / (1.0 - p))


def sigmoid(l: float) -> float:
    return 1.0 / (1.0 + math.exp(-l))


def stretch_probability(p: float, k: float = CALIBRATION_STRETCH_FACTOR) -> float:
    l = logit(p)
    l_new = k * l
    p_new = sigmoid(l_new)
    return max(CALIBRATION_CLAMP_MIN, min(CALIBRATION_CLAMP_MAX, p_new))


def signals_agree(sentiment: Optional[float], trend: Optional[float], base_rate: Optional[float], threshold: float = 0.5) -> bool:
    directions = []
    if sentiment is not None:
        directions.append(1 if sentiment > threshold else -1)
    if trend is not None:
        directions.append(1 if trend > threshold else -1)
    if base_rate is not None:
        directions.append(1 if base_rate > threshold else -1)
    if len(directions) < 2:
        return False
    return len(set(directions)) == 1


class Calibrator:
    def __init__(
        self,
        stretch_factor: float = CALIBRATION_STRETCH_FACTOR,
        stretch_factor_agreement: float = CALIBRATION_STRETCH_FACTOR_AGREEMENT,
        clamp_min: float = CALIBRATION_CLAMP_MIN,
        clamp_max: float = CALIBRATION_CLAMP_MAX,
    ):
        self.stretch_factor = stretch_factor
        self.stretch_factor_agreement = stretch_factor_agreement
        self.clamp_min = clamp_min
        self.clamp_max = clamp_max

    def _base_calibrate(self, p: float) -> float:
        return p

    def calibrate(
        self,
        p: float,
        sentiment: Optional[float] = None,
        trend: Optional[float] = None,
        base_rate: Optional[float] = None,
    ) -> float:
        p_base = self._base_calibrate(p)

        agreement = signals_agree(sentiment, trend, base_rate)
        k = self.stretch_factor_agreement if agreement else self.stretch_factor

        if agreement:
            logger.debug(
                "Multiple signals agree in direction; applying higher stretch factor k=%.2f", k
            )
        else:
            logger.debug("Applying standard stretch factor k=%.2f", k)

        p_stretched = stretch_probability(p_base, k=k)
        logger.debug(
            "Calibration: raw=%.4f base=%.4f k=%.2f stretched=%.4f",
            p, p_base, k, p_stretched,
        )
        return p_stretched

    def adjust(
        self,
        p: float,
        sentiment: Optional[float] = None,
        trend: Optional[float] = None,
        base_rate: Optional[float] = None,
    ) -> float:
        return self.calibrate(p, sentiment=sentiment, trend=trend, base_rate=base_rate)


_default_calibrator = Calibrator()


def calibrate(
    p: float,
    sentiment: Optional[float] = None,
    trend: Optional[float] = None,
    base_rate: Optional[float] = None,
    calibrator: Optional[Calibrator] = None,
) -> float:
    cal = calibrator if calibrator is not None else _default_calibrator
    return cal.calibrate(p, sentiment=sentiment, trend=trend, base_rate=base_rate)