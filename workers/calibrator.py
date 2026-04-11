import logging
import numpy as np
from typing import Optional

logger = logging.getLogger(__name__)


def extremize(p: float, a: float = 1.75) -> float:
    pa = p ** a
    one_minus_pa = (1.0 - p) ** a
    denom = pa + one_minus_pa
    if denom == 0:
        return p
    return pa / denom


class Calibrator:
    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        self.extremize_exponent = self.config.get("extremize_exponent", 1.75)
        self.extremize_min_signals = self.config.get("extremize_min_signals", 2)
        self.confidence_floor_low = self.config.get("confidence_floor_low", 0.45)
        self.confidence_floor_high = self.config.get("confidence_floor_high", 0.55)
        self.extremize_cap_low = self.config.get("extremize_cap_low", 0.05)
        self.extremize_cap_high = self.config.get("extremize_cap_high", 0.95)

    def calibrate(self, probability: float, signal_count: int = 0, **kwargs) -> float:
        p = float(probability)
        p = max(0.0, min(1.0, p))

        pre_extremized = p

        if signal_count >= self.extremize_min_signals:
            if self.confidence_floor_low <= p <= self.confidence_floor_high:
                logger.info(
                    "calibrate: p=%.4f is within confidence floor [%.2f, %.2f], "
                    "skipping extremizing (signal_count=%d)",
                    p,
                    self.confidence_floor_low,
                    self.confidence_floor_high,
                    signal_count,
                )
            else:
                p_ext = extremize(p, a=self.extremize_exponent)
                p_ext = max(self.extremize_cap_low, min(self.extremize_cap_high, p_ext))

                logger.info(
                    "calibrate: extremizing applied (signal_count=%d, a=%.2f): "
                    "p_before=%.4f -> p_after=%.4f",
                    signal_count,
                    self.extremize_exponent,
                    pre_extremized,
                    p_ext,
                )

                p = p_ext
        else:
            logger.info(
                "calibrate: skipping extremizing (signal_count=%d < min=%d), p=%.4f",
                signal_count,
                self.extremize_min_signals,
                p,
            )

        return p

    def calibrate_batch(self, probabilities, signal_counts=None, **kwargs):
        if signal_counts is None:
            signal_counts = [0] * len(probabilities)

        results = []
        for prob, sc in zip(probabilities, signal_counts):
            results.append(self.calibrate(prob, signal_count=sc, **kwargs))
        return results