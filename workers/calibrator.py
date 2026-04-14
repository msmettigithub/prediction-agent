import logging
import math
from typing import Optional

logger = logging.getLogger(__name__)


def extremize(p: float, alpha: float, confidence_floor: float = 0.03) -> float:
    """
    Apply Karmarkar-type extremizing transform to a probability.

    p_ext = p^alpha / (p^alpha + (1-p)^alpha)

    Only applied when abs(p - 0.5) > confidence_floor to avoid amplifying
    noise on near-50/50 calls.
    """
    if abs(p - 0.5) <= confidence_floor:
        logger.debug(
            "extremize: skipping (|p - 0.5| = %.4f <= floor %.4f), p=%.4f",
            abs(p - 0.5),
            confidence_floor,
            p,
        )
        return p

    p_clamped = max(1e-9, min(1 - 1e-9, p))
    p_a = p_clamped ** alpha
    q_a = (1.0 - p_clamped) ** alpha
    p_ext = p_a / (p_a + q_a)

    logger.debug(
        "extremize: pre=%.4f alpha=%.3f post=%.4f", p, alpha, p_ext
    )
    return p_ext


def adaptive_alpha(backtest_accuracy: Optional[float], default_alpha: float = 1.5) -> float:
    """
    Select alpha based on backtest accuracy.

    When backtest_accuracy > 0.75:
        alpha = 1 + (accuracy - 0.5) * 2
        e.g. 0.804 -> alpha = 1 + 0.304 = 1.608 ~ 1.6

    Falls back to default_alpha when accuracy is unavailable or <= 0.75.
    """
    if backtest_accuracy is None:
        logger.debug("adaptive_alpha: no accuracy provided, using default %.3f", default_alpha)
        return default_alpha

    if backtest_accuracy > 0.75:
        alpha = 1.0 + (backtest_accuracy - 0.5) * 2.0
        logger.debug(
            "adaptive_alpha: accuracy=%.4f -> alpha=%.4f", backtest_accuracy, alpha
        )
        return alpha

    logger.debug(
        "adaptive_alpha: accuracy=%.4f <= 0.75, using default %.3f",
        backtest_accuracy,
        default_alpha,
    )
    return default_alpha


def calibrate(
    raw_p: float,
    backtest_accuracy: Optional[float] = None,
    confidence_floor: float = 0.03,
    output_min: float = 0.05,
    output_max: float = 0.95,
) -> float:
    """
    Full calibration pipeline.

    Steps:
      1. Validate / hard-clamp input.
      2. (Placeholder for any existing calibration steps, e.g. Platt scaling,
         isotonic regression, temperature scaling — insert those here.)
      3. Select alpha adaptively from backtest accuracy.
      4. Apply extremizing transform (skipped near 0.5 per confidence_floor).
      5. Clamp output to [output_min, output_max] to avoid overconfidence.
      6. Log pre/post values for RL feedback loop.

    Parameters
    ----------
    raw_p : float
        Model probability before calibration, in (0, 1).
    backtest_accuracy : float, optional
        Fraction of correctly called directions in recent backtest window.
        Used to tune alpha adaptively.
    confidence_floor : float
        Minimum |p - 0.5| required to apply extremizing.  Calls closer to
        50 % are returned unchanged (after earlier calibration steps).
    output_min, output_max : float
        Hard clamps applied to the final output.

    Returns
    -------
    float
        Calibrated probability in [output_min, output_max].
    """
    # ------------------------------------------------------------------
    # 1. Input validation
    # ------------------------------------------------------------------
    if not math.isfinite(raw_p):
        logger.warning("calibrate: non-finite raw_p=%.4f, clamping to 0.5", raw_p)
        raw_p = 0.5

    p = max(1e-9, min(1 - 1e-9, raw_p))

    # ------------------------------------------------------------------
    # 2. Existing calibration steps (temperature scaling, isotonic, etc.)
    #    Insert / call them here.  They should modify `p` in place.
    # ------------------------------------------------------------------
    p_after_existing_calibration = p  # replace with real pipeline result

    # ------------------------------------------------------------------
    # 3. Adaptive alpha selection
    # ------------------------------------------------------------------
    alpha = adaptive_alpha(backtest_accuracy, default_alpha=1.5)

    # ------------------------------------------------------------------
    # 4. Extremizing transform
    # ------------------------------------------------------------------
    pre_ext = p_after_existing_calibration
    p_ext = extremize(pre_ext, alpha=alpha, confidence_floor=confidence_floor)

    # ------------------------------------------------------------------
    # 5. Output clamp
    # ------------------------------------------------------------------
    p_final = max(output_min, min(output_max, p_ext))

    # ------------------------------------------------------------------
    # 6. Logging for RL feedback loop
    # ------------------------------------------------------------------
    logger.info(
        "calibrate | raw=%.4f pre_ext=%.4f alpha=%.3f post_ext=%.4f final=%.4f "
        "backtest_acc=%s confidence_floor=%.3f",
        raw_p,
        pre_ext,
        alpha,
        p_ext,
        p_final,
        f"{backtest_accuracy:.4f}" if backtest_accuracy is not None else "None",
        confidence_floor,
    )

    return p_final


# ---------------------------------------------------------------------------
# Convenience wrapper that makes the RL feedback loop explicit
# ---------------------------------------------------------------------------

class CalibratorState:
    """
    Stateful wrapper that accumulates backtest outcomes so alpha adapts
    automatically without the caller having to compute accuracy externally.

    Usage
    -----
    cal = CalibratorState()
    p = cal.calibrate(raw_p)
    # ... later, after outcome is known:
    cal.record_outcome(predicted_p=p, actual_direction=1)
    """

    def __init__(
        self,
        window: int = 200,
        confidence_floor: float = 0.03,
        output_min: float = 0.05,
        output_max: float = 0.95,
        default_alpha: float = 1.5,
    ):
        self.window = window
        self.confidence_floor = confidence_floor
        self.output_min = output_min
        self.output_max = output_max
        self.default_alpha = default_alpha

        self._outcomes: list[bool] = []  # True = correct direction call

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def calibrate(self, raw_p: float) -> float:
        accuracy = self._backtest_accuracy()
        return calibrate(
            raw_p=raw_p,
            backtest_accuracy=accuracy,
            confidence_floor=self.confidence_floor,
            output_min=self.output_min,
            output_max=self.output_max,
        )

    def record_outcome(self, predicted_p: float, actual_direction: int) -> None:
        """
        Record whether the prediction was directionally correct.

        Parameters
        ----------
        predicted_p : float
            The calibrated probability emitted by this calibrator.
        actual_direction : int
            1 if the event occurred, 0 if it did not.
        """
        predicted_direction = 1 if predicted_p >= 0.5 else 0
        correct = predicted_direction == actual_direction
        self._outcomes.append(correct)
        if len(self._outcomes) > self.window:
            self._outcomes.pop(0)

        logger.debug(
            "record_outcome: predicted_p=%.4f actual=%d correct=%s "
            "window_accuracy=%.4f",
            predicted_p,
            actual_direction,
            correct,
            self._backtest_accuracy() or float("nan"),
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _backtest_accuracy(self) -> Optional[float]:
        if not self._outcomes:
            return None
        return sum(self._outcomes) / len(self._outcomes)