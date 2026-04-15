import math
import logging
from typing import Optional

logger = logging.getLogger(__name__)

CALIBRATION_STRETCH_K = 1.8


def calibration_stretch(p: float, k: float = CALIBRATION_STRETCH_K) -> float:
    """
    Apply logit-stretch calibration to a probability.

    Steps:
    1. Convert raw probability p to log-odds: logit = ln(p/(1-p))
    2. Apply stretch: stretched_logit = k * logit
    3. Convert back: calibrated_p = 1/(1+exp(-stretched_logit))
    4. Clamp output to [0.05, 0.95]

    This is a monotonic transform preserving rank ordering while amplifying
    distance from 0.5, increasing separation between YES/NO signals.
    """
    if p <= 0.0 or p >= 1.0:
        return max(0.05, min(0.95, p))

    try:
        logit = math.log(p / (1.0 - p))
        stretched_logit = k * logit
        calibrated_p = 1.0 / (1.0 + math.exp(-stretched_logit))
        calibrated_p = max(0.05, min(0.95, calibrated_p))
        return calibrated_p
    except (ValueError, ZeroDivisionError, OverflowError) as e:
        logger.warning(f"calibration_stretch error for p={p}, k={k}: {e}")
        return max(0.05, min(0.95, p))


class Calibrator:
    """
    Calibrator worker that applies probability stretching to improve
    separation between YES-leaning and NO-leaning predictions.
    """

    def __init__(self, stretch_k: Optional[float] = None):
        self.stretch_k = stretch_k if stretch_k is not None else CALIBRATION_STRETCH_K
        logger.info(f"Calibrator initialized with stretch_k={self.stretch_k}")

    def calibrate(self, p: float) -> float:
        """
        Main calibration entry point. Applies logit-stretch transform so all
        probabilities flowing to the trading brain are stretched away from 0.5.
        """
        original_p = p
        calibrated = calibration_stretch(p, k=self.stretch_k)
        logger.debug(
            f"Calibrate: {original_p:.4f} -> {calibrated:.4f} (k={self.stretch_k})"
        )
        return calibrated

    def adjust(self, p: float) -> float:
        """
        Alias for calibrate(), for compatibility with callers using adjust().
        """
        return self.calibrate(p)

    def calibrate_batch(self, probabilities: list) -> list:
        """
        Apply calibration to a list of probabilities.
        """
        return [self.calibrate(p) for p in probabilities]

    def set_stretch_k(self, k: float) -> None:
        """
        Update the stretch factor k at runtime.
        """
        logger.info(f"Calibrator stretch_k updated: {self.stretch_k} -> {k}")
        self.stretch_k = k