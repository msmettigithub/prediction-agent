import math
import logging
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from config import EXTREMIZE_K, EXTREMIZE_ENABLED, PROB_FLOOR, PROB_CEILING
except ImportError:
    EXTREMIZE_K = 1.5
    EXTREMIZE_ENABLED = True
    PROB_FLOOR = 0.05
    PROB_CEILING = 0.95


class Calibrator:
    def __init__(
        self,
        extremize_k: Optional[float] = None,
        extremize_enabled: Optional[bool] = None,
        prob_floor: Optional[float] = None,
        prob_ceiling: Optional[float] = None,
    ):
        self.extremize_k = extremize_k if extremize_k is not None else EXTREMIZE_K
        self.extremize_enabled = extremize_enabled if extremize_enabled is not None else EXTREMIZE_ENABLED
        self.prob_floor = prob_floor if prob_floor is not None else PROB_FLOOR
        self.prob_ceiling = prob_ceiling if prob_ceiling is not None else PROB_CEILING

        logger.info(
            "Calibrator initialized: extremize_enabled=%s, extremize_k=%.3f, "
            "prob_floor=%.3f, prob_ceiling=%.3f",
            self.extremize_enabled,
            self.extremize_k,
            self.prob_floor,
            self.prob_ceiling,
        )

    def extremize(self, p: float, k: Optional[float] = None) -> float:
        """
        Apply power-law extremization transform to a calibrated probability.

        Formula: p_ext = p^k / (p^k + (1-p)^k)

        Properties:
        - Idempotent at p=0.5: extremize(0.5) == 0.5 for all k
        - Monotonically increasing: preserves rank order of predictions
        - For k > 1: pushes probabilities away from 0.5 (extremizes)
        - For k = 1: identity transform
        - For k < 1: shrinks probabilities toward 0.5

        Args:
            p: Calibrated probability in [0, 1]
            k: Exponent parameter. Defaults to self.extremize_k.

        Returns:
            Extremized probability in [0, 1]
        """
        if k is None:
            k = self.extremize_k

        if p <= 0.0:
            return 0.0
        if p >= 1.0:
            return 1.0

        p_k = p ** k
        one_minus_p_k = (1.0 - p) ** k
        denom = p_k + one_minus_p_k

        if denom == 0.0:
            logger.warning("extremize: zero denominator for p=%.6f, k=%.3f, returning 0.5", p, k)
            return 0.5

        return p_k / denom

    def clamp(self, p: float) -> float:
        """
        Apply floor/ceiling clamp to probability to avoid overconfident predictions.

        Clamps to [prob_floor, prob_ceiling] (default [0.05, 0.95]) to protect
        Brier score from catastrophic losses on near-certain predictions.

        Args:
            p: Probability value

        Returns:
            Clamped probability
        """
        return max(self.prob_floor, min(self.prob_ceiling, p))

    def calibrate(self, raw_prob: float) -> float:
        """
        Apply full calibration pipeline to a raw model probability.

        Pipeline:
        1. Basic validity clamp to [0, 1]
        2. Any existing calibration steps (isotonic regression, Platt scaling, etc.)
        3. Extremization transform (if enabled)
        4. Floor/ceiling clamp at [prob_floor, prob_ceiling]

        Args:
            raw_prob: Raw probability from model output

        Returns:
            Calibrated and optionally extremized probability
        """
        p = float(raw_prob)

        # Step 1: validity clamp
        p = max(0.0, min(1.0, p))

        # Step 2: existing calibration steps (override in subclass or extend here)
        p = self._apply_base_calibration(p)

        # Step 3: extremization
        if self.extremize_enabled:
            p_before = p
            p = self.extremize(p)
            logger.debug(
                "extremize: p=%.4f -> %.4f (k=%.3f)",
                p_before,
                p,
                self.extremize_k,
            )

        # Step 4: floor/ceiling clamp
        p = self.clamp(p)

        return p

    def _apply_base_calibration(self, p: float) -> float:
        """
        Apply base calibration (e.g., isotonic regression, Platt scaling).

        Override this method in subclasses to add model-specific calibration.
        Default implementation is identity (pass-through).

        Args:
            p: Probability after validity clamp

        Returns:
            Calibrated probability
        """
        return p

    def batch_calibrate(self, raw_probs: list) -> list:
        """
        Apply calibration pipeline to a list of raw probabilities.

        Args:
            raw_probs: List of raw probability floats

        Returns:
            List of calibrated probabilities
        """
        return [self.calibrate(p) for p in raw_probs]

    def separation_stats(self, probs: list) -> dict:
        """
        Compute separation statistics for a list of probabilities.

        Useful for diagnostics and verifying that extremization is working.

        Args:
            probs: List of probability values

        Returns:
            Dict with mean_separation, min_p, max_p, count
        """
        if not probs:
            return {"mean_separation": 0.0, "min_p": 0.5, "max_p": 0.5, "count": 0}

        separations = [abs(p - 0.5) for p in probs]
        return {
            "mean_separation": sum(separations) / len(separations),
            "min_p": min(probs),
            "max_p": max(probs),
            "count": len(probs),
        }


def make_calibrator(
    extremize_k: float = EXTREMIZE_K,
    extremize_enabled: bool = EXTREMIZE_ENABLED,
    prob_floor: float = PROB_FLOOR,
    prob_ceiling: float = PROB_CEILING,
) -> Calibrator:
    """
    Factory function to create a Calibrator instance with config-driven defaults.

    Args:
        extremize_k: Power exponent for extremization (default from config)
        extremize_enabled: Whether to apply extremization (default from config)
        prob_floor: Minimum probability floor (default from config)
        prob_ceiling: Maximum probability ceiling (default from config)

    Returns:
        Configured Calibrator instance
    """
    return Calibrator(
        extremize_k=extremize_k,
        extremize_enabled=extremize_enabled,
        prob_floor=prob_floor,
        prob_ceiling=prob_ceiling,
    )