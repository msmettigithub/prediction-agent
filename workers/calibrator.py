import logging
import math
from typing import Optional

logger = logging.getLogger(__name__)


def _get_alpha(num_agreeing_signals: int) -> float:
    if num_agreeing_signals >= 6:
        return 1.8
    elif num_agreeing_signals >= 4:
        return 1.5
    elif num_agreeing_signals >= 2:
        return 1.3
    else:
        return 1.0


def sharpen_probability(p: float, num_agreeing_signals: int = 0) -> float:
    alpha = _get_alpha(num_agreeing_signals)
    p_clipped = max(1e-9, min(1 - 1e-9, p))

    p_alpha = p_clipped ** alpha
    one_minus_p_alpha = (1.0 - p_clipped) ** alpha
    denom = p_alpha + one_minus_p_alpha

    if denom == 0.0:
        p_sharp = p_clipped
    else:
        p_sharp = p_alpha / denom

    p_sharp = max(0.05, min(0.95, p_sharp))

    logger.info(
        "sharpen_probability: pre_sharp=%.6f post_sharp=%.6f alpha=%.2f "
        "num_agreeing_signals=%d",
        p,
        p_sharp,
        alpha,
        num_agreeing_signals,
    )

    return p_sharp


class Calibrator:
    def __init__(self):
        self._isotonic_model = None
        self._platt_slope: Optional[float] = None
        self._platt_intercept: Optional[float] = None

    def _isotonic_adjust(self, p: float) -> float:
        if self._isotonic_model is None:
            return p
        try:
            import numpy as np
            adjusted = self._isotonic_model.predict([[p]])[0]
            return float(np.clip(adjusted, 1e-9, 1 - 1e-9))
        except Exception as exc:
            logger.warning("isotonic_adjust failed: %s", exc)
            return p

    def _platt_adjust(self, p: float) -> float:
        if self._platt_slope is None or self._platt_intercept is None:
            return p
        try:
            logit = math.log(p / (1.0 - p))
            scaled = self._platt_slope * logit + self._platt_intercept
            return 1.0 / (1.0 + math.exp(-scaled))
        except (ValueError, ZeroDivisionError, OverflowError) as exc:
            logger.warning("platt_adjust failed: %s", exc)
            return p

    def calibrate(self, p: float, num_agreeing_signals: int = 0) -> float:
        p = max(1e-9, min(1 - 1e-9, p))

        p = self._isotonic_adjust(p)
        p = self._platt_adjust(p)

        p = max(1e-9, min(1 - 1e-9, p))

        logger.debug("calibrate: post_base_calibration=%.6f", p)

        p = sharpen_probability(p, num_agreeing_signals=num_agreeing_signals)

        return p

    def adjust(self, p: float, num_agreeing_signals: int = 0) -> float:
        return self.calibrate(p, num_agreeing_signals=num_agreeing_signals)

    def fit_platt(self, slope: float, intercept: float) -> None:
        self._platt_slope = slope
        self._platt_intercept = intercept
        logger.info("fit_platt: slope=%.4f intercept=%.4f", slope, intercept)

    def fit_isotonic(self, model) -> None:
        self._isotonic_model = model
        logger.info("fit_isotonic: model fitted")