workers/calibrator.py

import math
import logging
from typing import Optional

logger = logging.getLogger(__name__)

CATEGORY_K = {
    "geopolitics": 1.4,
    "policy": 1.4,
    "markets": 1.8,
    "sports": 1.8,
}
DEFAULT_K = 1.6
DEFAULT_CONFIDENCE_FLOOR = 0.12


def stretch_probability(p: float, k: float = DEFAULT_K) -> float:
    """
    Power-law logit transform: p_out = p^k / (p^k + (1-p)^k)

    Pushes probabilities away from 0.5 toward the extremes.
    k=1.0 is identity. k>1 increases separation. k<1 compresses.
    """
    if p <= 0.0:
        return 0.0
    if p >= 1.0:
        return 1.0
    pk = p ** k
    qk = (1.0 - p) ** k
    denom = pk + qk
    if denom == 0.0:
        return p
    return pk / denom


def apply_confidence_floor(
    p: float,
    floor: float = DEFAULT_CONFIDENCE_FLOOR,
    signal_count: int = 0,
) -> float:
    """
    If the prediction is closer to 0.5 than `floor` and we have enough
    signals, push it to exactly 0.5 +/- floor (toward the nearer extreme).
    This prevents weak-signal predictions from cluttering the 0.45-0.55 band.
    """
    if signal_count < 2:
        return p
    distance = abs(p - 0.5)
    if distance < floor:
        if p >= 0.5:
            return 0.5 + floor
        else:
            return 0.5 - floor
    return p


class Calibrator:
    """
    Wraps an existing calibration pipeline and adds a confidence-amplification
    step as the final transform.

    The _calibrated flag makes the sharpening step idempotent: calling
    calibrate() on an already-calibrated value will not double-stretch it.
    """

    def __init__(
        self,
        k: Optional[float] = None,
        confidence_floor: float = DEFAULT_CONFIDENCE_FLOOR,
        category: Optional[str] = None,
    ):
        self.confidence_floor = confidence_floor
        self.category = category
        if k is not None:
            self.k = k
        elif category is not None:
            self.k = CATEGORY_K.get(category.lower(), DEFAULT_K)
        else:
            self.k = DEFAULT_K

        self._calibrated_values: dict = {}

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def calibrate(
        self,
        p: float,
        signal_count: int = 0,
        prediction_id: Optional[str] = None,
        category: Optional[str] = None,
    ) -> float:
        """
        Full calibration pipeline:
          1. Clamp input to (0, 1)
          2. (Placeholder) apply any existing base calibration logic here
          3. Apply confidence floor
          4. Apply power-law logit stretch
          5. Log pre/post for RL feedback
          6. Mark as calibrated (idempotency guard)

        Returns the calibrated probability.
        """
        # Idempotency guard
        if prediction_id is not None and self._calibrated_values.get(prediction_id):
            logger.debug(
                "calibrate() called again on already-calibrated prediction_id=%s; "
                "returning cached value.",
                prediction_id,
            )
            return self._calibrated_values[prediction_id]

        p_raw = float(p)
        p_clamped = max(1e-6, min(1.0 - 1e-6, p_raw))

        # ----------------------------------------------------------------
        # Step 1: existing base calibration (identity here — extend as
        #         needed without touching the sharpening logic below)
        # ----------------------------------------------------------------
        p_base = self._base_calibration(p_clamped)

        # ----------------------------------------------------------------
        # Step 2: confidence floor
        # ----------------------------------------------------------------
        p_floored = apply_confidence_floor(
            p_base,
            floor=self.confidence_floor,
            signal_count=signal_count,
        )

        # ----------------------------------------------------------------
        # Step 3: power-law logit stretch (final step)
        # ----------------------------------------------------------------
        effective_k = self._resolve_k(category)
        p_out = stretch_probability(p_floored, k=effective_k)

        # ----------------------------------------------------------------
        # Step 4: log for RL feedback loop
        # ----------------------------------------------------------------
        logger.info(
            "calibration | id=%s category=%s k=%.2f signal_count=%d "
            "p_raw=%.4f p_base=%.4f p_floored=%.4f p_out=%.4f",
            prediction_id,
            category or self.category,
            effective_k,
            signal_count,
            p_raw,
            p_base,
            p_floored,
            p_out,
        )

        # ----------------------------------------------------------------
        # Step 5: cache for idempotency
        # ----------------------------------------------------------------
        if prediction_id is not None:
            self._calibrated_values[prediction_id] = p_out

        return p_out

    # alias so callers using .adjust() still work
    def adjust(
        self,
        p: float,
        signal_count: int = 0,
        prediction_id: Optional[str] = None,
        category: Optional[str] = None,
    ) -> float:
        return self.calibrate(
            p,
            signal_count=signal_count,
            prediction_id=prediction_id,
            category=category,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _base_calibration(self, p: float) -> float:
        """
        Placeholder for any pre-existing calibration logic (isotonic
        regression lookup, Platt scaling, etc.).  Currently identity.
        Extend here without touching the sharpening pipeline.
        """
        return p

    def _resolve_k(self, category: Optional[str]) -> float:
        if category is not None:
            return CATEGORY_K.get(category.lower(), self.k)
        return self.k

    def clear_cache(self) -> None:
        """Flush the idempotency cache (e.g. between batches)."""
        self._calibrated_values.clear()


# ---------------------------------------------------------------------------
# Module-level convenience functions for callers that don't instantiate
# the class directly
# ---------------------------------------------------------------------------

def calibrate_probability(
    p: float,
    k: float = DEFAULT_K,
    confidence_floor: float = DEFAULT_CONFIDENCE_FLOOR,
    signal_count: int = 0,
    prediction_id: Optional[str] = None,
    category: Optional[str] = None,
) -> float:
    """
    Stateless convenience wrapper around the full calibration pipeline.
    No idempotency caching (caller is responsible if needed).
    """
    p_clamped = max(1e-6, min(1.0 - 1e-6, float(p)))
    effective_k = CATEGORY_K.get((category or "").lower(), k) if category else k
    p_floored = apply_confidence_floor(p_clamped, floor=confidence_floor, signal_count=signal_count)
    p_out = stretch_probability(p_floored, k=effective_k)
    logger.info(
        "calibrate_probability | category=%s k=%.2f signal_count=%d "
        "p_in=%.4f p_floored=%.4f p_out=%.4f",
        category,
        effective_k,
        signal_count,
        p_clamped,
        p_floored,
        p_out,
    )
    return p_out