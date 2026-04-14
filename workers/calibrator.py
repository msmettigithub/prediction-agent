import logging
import math
import os
from typing import Optional

logger = logging.getLogger(__name__)

CALIBRATION_TEMPERATURE = float(os.environ.get("CALIBRATION_TEMPERATURE", "0.6"))


def sharpen(p: float, temperature: float = CALIBRATION_TEMPERATURE) -> float:
    """
    Apply temperature-scaled sharpening to a probability.
    
    Uses the transform: p_sharp = p^(1/T) / (p^(1/T) + (1-p)^(1/T))
    
    This is equivalent to exponentiating log-odds by 1/T:
        logit(p_sharp) = logit(p) / T
    
    T < 1.0 sharpens (pushes away from 0.5).
    T = 1.0 is identity.
    T = 0.6 maps p=0.60 -> ~0.66, p=0.70 -> ~0.78.
    
    Args:
        p: Raw probability in (0, 1).
        temperature: Sharpening temperature. Default from CALIBRATION_TEMPERATURE env var.
    
    Returns:
        Sharpened probability in (0, 1).
    """
    if p <= 0.0 or p >= 1.0:
        return p
    if temperature <= 0.0:
        raise ValueError(f"Temperature must be positive, got {temperature}")
    
    exponent = 1.0 / temperature
    p_exp = p ** exponent
    q_exp = (1.0 - p) ** exponent
    denom = p_exp + q_exp
    if denom == 0.0:
        return p
    return p_exp / denom


def clamp(p: float, low: float = 0.05, high: float = 0.95) -> float:
    """Clamp probability to [low, high] to prevent overconfidence."""
    return max(low, min(high, p))


def calibrate(p_raw: float, temperature: Optional[float] = None) -> float:
    """
    Main calibration method. Applies temperature-scaled sharpening to a raw probability.

    Steps:
      1. Receive raw probability p_raw.
      2. Only apply sharpening when abs(p_raw - 0.5) > 0.03 (minimal signal threshold).
      3. Apply the sharpening transform with the configured temperature.
      4. Clamp the result to [0.05, 0.95].
      5. Log pre- and post-sharpening values for monitoring.

    Args:
        p_raw: Raw probability estimate in [0, 1].
        temperature: Override the default CALIBRATION_TEMPERATURE if provided.

    Returns:
        Calibrated probability clamped to [0.05, 0.95].
    """
    T = temperature if temperature is not None else CALIBRATION_TEMPERATURE

    logger.info(
        "calibrate: p_raw=%.6f temperature=%.4f",
        p_raw,
        T,
    )

    # Guard: if raw prob is essentially at 50/50 (noise band), skip sharpening
    if abs(p_raw - 0.5) <= 0.03:
        p_out = clamp(p_raw)
        logger.info(
            "calibrate: near-50/50 signal (|p-0.5|=%.4f <= 0.03), skipping sharpening. "
            "p_pre_sharp=%.6f p_post_sharp=%.6f (no change)",
            abs(p_raw - 0.5),
            p_raw,
            p_out,
        )
        return p_out

    # Apply sharpening
    p_sharp = sharpen(p_raw, temperature=T)

    # Clamp to prevent overconfidence
    p_out = clamp(p_sharp)

    logger.info(
        "calibrate: p_pre_sharp=%.6f p_post_sharp=%.6f p_clamped=%.6f "
        "(delta=%.6f, T=%.4f)",
        p_raw,
        p_sharp,
        p_out,
        p_sharp - p_raw,
        T,
    )

    return p_out


class Calibrator:
    """
    Calibrator wraps the calibrate() function with optional instance-level
    temperature configuration and maintains per-instance logging context.
    """

    def __init__(self, temperature: Optional[float] = None):
        """
        Args:
            temperature: Sharpening temperature to use. Defaults to the
                         CALIBRATION_TEMPERATURE environment variable (0.6 default).
        """
        self.temperature = temperature if temperature is not None else CALIBRATION_TEMPERATURE
        logger.info("Calibrator initialized with temperature=%.4f", self.temperature)

    def calibrate(self, p_raw: float) -> float:
        """
        Calibrate a raw probability.

        Args:
            p_raw: Raw probability in [0, 1].

        Returns:
            Calibrated, clamped probability in [0.05, 0.95].
        """
        return calibrate(p_raw, temperature=self.temperature)

    def calibrate_batch(self, probabilities: list) -> list:
        """
        Calibrate a list of raw probabilities.

        Args:
            probabilities: List of raw probabilities in [0, 1].

        Returns:
            List of calibrated probabilities.
        """
        results = []
        for p in probabilities:
            results.append(self.calibrate(p))
        
        if probabilities:
            import statistics
            raw_mean = statistics.mean(probabilities)
            cal_mean = statistics.mean(results)
            raw_stdev = statistics.stdev(probabilities) if len(probabilities) > 1 else 0.0
            cal_stdev = statistics.stdev(results) if len(results) > 1 else 0.0
            logger.info(
                "calibrate_batch: n=%d raw_mean=%.4f cal_mean=%.4f "
                "raw_stdev=%.4f cal_stdev=%.4f spread_increase=%.4f",
                len(probabilities),
                raw_mean,
                cal_mean,
                raw_stdev,
                cal_stdev,
                cal_stdev - raw_stdev,
            )
        
        return results