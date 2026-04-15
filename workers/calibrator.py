import math
import logging
from typing import Optional

logger = logging.getLogger(__name__)

EXTREMIZE_D = 1.5


class Calibrator:
    def __init__(self, d: Optional[float] = None):
        self.d = d if d is not None else EXTREMIZE_D

    def calibrate(self, p: float) -> float:
        p = self._base_calibration(p)
        before = p
        p = self.extremize(p, d=self.d)
        logger.info(f"Extremize transform: before={before:.4f}, after={p:.4f}, d={self.d}")
        return p

    def _base_calibration(self, p: float) -> float:
        p = max(1e-9, min(1 - 1e-9, p))
        return p

    def extremize(self, p: float, d: float = 1.5) -> float:
        p = max(1e-9, min(1 - 1e-9, p))

        if abs(p - 0.5) <= 0.03:
            logger.debug(f"Extremize skipped (near toss-up): p={p:.4f}, |p-0.5|={abs(p-0.5):.4f}")
            return p

        try:
            logit = math.log(p / (1.0 - p))
            scaled_logit = d * logit
            p_ext = 1.0 / (1.0 + math.exp(-scaled_logit))
        except (ValueError, OverflowError) as e:
            logger.warning(f"Extremize math error for p={p:.4f}, d={d}: {e}")
            return p

        p_ext = max(0.05, min(0.95, p_ext))

        logger.debug(
            f"Extremize: p={p:.4f}, logit={logit:.4f}, "
            f"scaled_logit={scaled_logit:.4f}, p_ext={p_ext:.4f}"
        )

        return p_ext