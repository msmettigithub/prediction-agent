import math
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _logit(p: float) -> float:
    p = max(1e-9, min(1 - 1e-9, p))
    return math.log(p / (1 - p))


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    else:
        exp_x = math.exp(x)
        return exp_x / (1.0 + exp_x)


class Calibrator:
    """
    Converts raw model probabilities into calibrated, deployment-ready probabilities.

    Calibration pipeline:
      1. Platt scaling (linear transform in logit space)
      2. Isotonic regression lookup (optional, via table)
      3. sharpen_probability() — confidence-aware logit scaling
      4. Clamp to [0.07, 0.93]
    """

    SHARPEN_CLAMP_LOW = 0.07
    SHARPEN_CLAMP_HIGH = 0.93
    SHARPEN_SCALE_BASE = 1.0
    SHARPEN_SCALE_PER_SIGNAL = 0.1
    SHARPEN_SCALE_CAP = 1.5

    def __init__(
        self,
        platt_a: float = -1.0,
        platt_b: float = 0.0,
        isotonic_table: Optional[dict] = None,
        sharpening_enabled: bool = True,
    ):
        """
        Parameters
        ----------
        platt_a : float
            Slope for Platt scaling in logit space.
        platt_b : float
            Intercept for Platt scaling in logit space.
        isotonic_table : dict or None
            Optional lookup table {bucket_low: calibrated_prob, ...}.
        sharpening_enabled : bool
            Master switch.  Set False in backtests to compare sharpened vs
            unsharpened without changing any other logic.
        """
        self.platt_a = platt_a
        self.platt_b = platt_b
        self.isotonic_table = isotonic_table or {}
        self.sharpening_enabled = sharpening_enabled

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def calibrate(
        self,
        raw_prob: float,
        base_rate: Optional[float] = None,
        trend: Optional[float] = None,
        sentiment: Optional[float] = None,
        market_data: Optional[float] = None,
        bypass_sharpening: bool = False,
    ) -> float:
        """
        Full calibration pipeline.

        Parameters
        ----------
        raw_prob : float
            Raw model output in [0, 1].
        base_rate : float or None
            Base-rate signal (probability in [0, 1]) if available.
        trend : float or None
            Trend signal (probability in [0, 1]) if available.
        sentiment : float or None
            Sentiment signal (probability in [0, 1]) if available.
        market_data : float or None
            Market-data signal (probability in [0, 1]) if available.
        bypass_sharpening : bool
            When True, skip the sharpening step regardless of the
            instance-level ``sharpening_enabled`` flag.  Useful for
            per-call backtest comparisons.

        Returns
        -------
        float
            Calibrated probability in [0.07, 0.93].
        """
        if not (0.0 <= raw_prob <= 1.0):
            logger.warning("raw_prob %s outside [0,1]; clamping.", raw_prob)
            raw_prob = max(0.0, min(1.0, raw_prob))

        # Step 1 — Platt scaling
        prob = self._platt_scale(raw_prob)

        # Step 2 — Isotonic regression lookup (if table provided)
        prob = self._isotonic_lookup(prob)

        # Step 3 — Confidence-aware sharpening
        if self.sharpening_enabled and not bypass_sharpening:
            prob = self.sharpen_probability(
                prob,
                base_rate=base_rate,
                trend=trend,
                sentiment=sentiment,
                market_data=market_data,
            )
        else:
            # Still apply the hard clamp even when sharpening is bypassed
            prob = max(self.SHARPEN_CLAMP_LOW, min(self.SHARPEN_CLAMP_HIGH, prob))

        logger.debug(
            "calibrate: raw=%.4f -> final=%.4f (sharpen=%s)",
            raw_prob,
            prob,
            self.sharpening_enabled and not bypass_sharpening,
        )
        return prob

    def sharpen_probability(
        self,
        calibrated_prob: float,
        base_rate: Optional[float] = None,
        trend: Optional[float] = None,
        sentiment: Optional[float] = None,
        market_data: Optional[float] = None,
    ) -> float:
        """
        Confidence-aware sharpening in logit space.

        Algorithm
        ---------
        1. Convert *calibrated_prob* to logit space:
               logit = ln(p / (1 - p))
        2. Count how many of the (up to 4) independent signals agree with
           the direction implied by *calibrated_prob* (i.e., the same side
           of 0.5):
               n_agreeing = number of signals also > 0.5 if prob > 0.5,
                            or also < 0.5 if prob < 0.5.
           Signals that are None (unavailable) are skipped.
        3. Compute a scaling factor:
               scale = min(1.0 + 0.1 * n_agreeing, 1.5)
        4. Multiply the logit by *scale*:
               sharpened_logit = scale * logit
        5. Convert back via sigmoid.
        6. Clamp to [0.07, 0.93].

        Parameters
        ----------
        calibrated_prob : float
            Already-calibrated probability in [0, 1].
        base_rate, trend, sentiment, market_data : float or None
            Independent signal probabilities.  Pass None if a signal is
            not available for this prediction.

        Returns
        -------
        float
            Sharpened probability in [0.07, 0.93].
        """
        calibrated_prob = max(1e-9, min(1 - 1e-9, calibrated_prob))

        # --- Step 1: logit ---
        logit_val = _logit(calibrated_prob)

        # --- Step 2: count agreeing signals ---
        direction_above = calibrated_prob > 0.5
        signals = {
            "base_rate": base_rate,
            "trend": trend,
            "sentiment": sentiment,
            "market_data": market_data,
        }
        n_agreeing = 0
        for name, sig in signals.items():
            if sig is None:
                continue
            sig_above = sig > 0.5
            if sig_above == direction_above:
                n_agreeing += 1
            logger.debug(
                "sharpen signal '%s'=%.4f agrees=%s",
                name,
                sig,
                sig_above == direction_above,
            )

        # --- Step 3: scaling factor ---
        scale = min(
            self.SHARPEN_SCALE_BASE + self.SHARPEN_SCALE_PER_SIGNAL * n_agreeing,
            self.SHARPEN_SCALE_CAP,
        )

        # --- Step 4: scale the logit ---
        sharpened_logit = scale * logit_val

        # --- Step 5: back to probability ---
        sharpened_prob = _sigmoid(sharpened_logit)

        # --- Step 6: clamp ---
        sharpened_prob = max(
            self.SHARPEN_CLAMP_LOW, min(self.SHARPEN_CLAMP_HIGH, sharpened_prob)
        )

        logger.debug(
            "sharpen: p_in=%.4f logit=%.4f n_agree=%d scale=%.2f "
            "sharpened_logit=%.4f p_out=%.4f",
            calibrated_prob,
            logit_val,
            n_agreeing,
            scale,
            sharpened_logit,
            sharpened_prob,
        )
        return sharpened_prob

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _platt_scale(self, prob: float) -> float:
        """Apply Platt scaling: sigmoid(a * logit(p) + b)."""
        logit_val = _logit(prob)
        scaled = self.platt_a * logit_val + self.platt_b
        result = _sigmoid(scaled)
        logger.debug("platt_scale: %.4f -> %.4f", prob, result)
        return result

    def _isotonic_lookup(self, prob: float) -> float:
        """
        Optional piecewise-constant isotonic correction.

        The table maps lower-bucket-boundary (float) to corrected probability.
        If no table is configured the input is returned unchanged.
        """
        if not self.isotonic_table:
            return prob

        # Find the largest bucket key <= prob
        sorted_keys = sorted(self.isotonic_table.keys())
        corrected = prob
        for key in sorted_keys:
            if prob >= key:
                corrected = self.isotonic_table[key]
            else:
                break

        logger.debug("isotonic_lookup: %.4f -> %.4f", prob, corrected)
        return corrected