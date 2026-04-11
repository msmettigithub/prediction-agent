import numpy as np
import logging
from typing import Optional

logger = logging.getLogger(__name__)

EXTREMIZE_ENABLED = True
EXTREMIZE_ALPHA = 1.5
CONFIDENCE_FLOOR = 0.55


def extremize(p: float, alpha: float = EXTREMIZE_ALPHA) -> float:
    p = float(np.clip(p, 1e-6, 1 - 1e-6))
    pa = p ** alpha
    one_minus_pa = (1.0 - p) ** alpha
    return pa / (pa + one_minus_pa)


def apply_confidence_floor(p: float, floor: float = CONFIDENCE_FLOOR) -> float:
    if p > 0.5 and p < floor:
        return floor
    elif p < 0.5 and p > (1.0 - floor):
        return 1.0 - floor
    return p


def calibrate(raw_prob: float,
              extremize_enabled: Optional[bool] = None,
              alpha: float = EXTREMIZE_ALPHA,
              confidence_floor: float = CONFIDENCE_FLOOR) -> float:
    if extremize_enabled is None:
        extremize_enabled = EXTREMIZE_ENABLED

    p = float(np.clip(raw_prob, 1e-6, 1 - 1e-6))

    p = apply_confidence_floor(p, confidence_floor)

    pre_extremize = p

    if extremize_enabled:
        p = extremize(p, alpha=alpha)
        logger.info(
            "calibrator: pre_extremize=%.4f post_extremize=%.4f alpha=%.2f confidence_floor=%.2f",
            pre_extremize, p, alpha, confidence_floor
        )
    else:
        logger.info(
            "calibrator: extremization disabled, prob=%.4f (confidence_floor=%.2f applied)",
            p, confidence_floor
        )

    return float(np.clip(p, 1e-6, 1 - 1e-6))


class Calibrator:
    def __init__(self,
                 extremize_enabled: bool = EXTREMIZE_ENABLED,
                 alpha: float = EXTREMIZE_ALPHA,
                 confidence_floor: float = CONFIDENCE_FLOOR):
        self.extremize_enabled = extremize_enabled
        self.alpha = alpha
        self.confidence_floor = confidence_floor

    def calibrate(self, raw_prob: float) -> float:
        return calibrate(
            raw_prob,
            extremize_enabled=self.extremize_enabled,
            alpha=self.alpha,
            confidence_floor=self.confidence_floor
        )

    def calibrate_batch(self, raw_probs) -> np.ndarray:
        arr = np.array(raw_probs, dtype=float)
        return np.array([self.calibrate(p) for p in arr])