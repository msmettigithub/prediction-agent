import numpy as np
import logging
from typing import Union

logger = logging.getLogger(__name__)

CALIBRATION_TEMPERATURE = 0.6

try:
    from config import settings
    CALIBRATION_TEMPERATURE = getattr(settings, 'CALIBRATION_TEMPERATURE', 0.6)
except Exception:
    pass

PROB_CLAMP_MIN = 0.03
PROB_CLAMP_MAX = 0.97


def sharpen_probability(p: Union[float, np.ndarray], temperature: float = None) -> Union[float, np.ndarray]:
    if temperature is None:
        temperature = CALIBRATION_TEMPERATURE

    if temperature <= 0:
        raise ValueError(f"Temperature must be positive, got {temperature}")

    scalar_input = np.isscalar(p)
    p = np.atleast_1d(np.array(p, dtype=float))

    p_clamped = np.clip(p, PROB_CLAMP_MIN, PROB_CLAMP_MAX)

    logit = np.log(p_clamped / (1.0 - p_clamped))

    sharpened_logit = logit / temperature

    sharpened = 1.0 / (1.0 + np.exp(-sharpened_logit))

    sharpened = np.clip(sharpened, PROB_CLAMP_MIN, PROB_CLAMP_MAX)

    if scalar_input:
        return float(sharpened[0])
    return sharpened


def calibrate_probabilities(probs: Union[float, np.ndarray], temperature: float = None, label: str = "") -> Union[float, np.ndarray]:
    if temperature is None:
        temperature = CALIBRATION_TEMPERATURE

    scalar_input = np.isscalar(probs)
    probs_arr = np.atleast_1d(np.array(probs, dtype=float))

    before_mean = float(np.mean(probs_arr))
    before_std = float(np.std(probs_arr))
    before_min = float(np.min(probs_arr))
    before_max = float(np.max(probs_arr))

    sharpened = sharpen_probability(probs_arr, temperature=temperature)

    after_mean = float(np.mean(sharpened))
    after_std = float(np.std(sharpened))
    after_min = float(np.min(sharpened))
    after_max = float(np.max(sharpened))

    tag = f"[{label}] " if label else ""
    logger.info(
        f"{tag}Calibration sharpening (T={temperature}): "
        f"BEFORE mean={before_mean:.4f} std={before_std:.4f} min={before_min:.4f} max={before_max:.4f} | "
        f"AFTER  mean={after_mean:.4f} std={after_std:.4f} min={after_min:.4f} max={after_max:.4f} | "
        f"separation_delta={abs(after_mean - 0.5) - abs(before_mean - 0.5):+.4f}"
    )

    if scalar_input:
        return float(sharpened[0])
    return sharpened


class Calibrator:
    def __init__(self, temperature: float = None):
        self.temperature = temperature if temperature is not None else CALIBRATION_TEMPERATURE
        logger.info(f"Calibrator initialized with temperature={self.temperature}")

    def calibrate(self, probs: Union[float, np.ndarray], label: str = "") -> Union[float, np.ndarray]:
        try:
            return calibrate_probabilities(probs, temperature=self.temperature, label=label)
        except Exception as exc:
            logger.error(f"Calibration failed ({exc}), returning original probabilities")
            return probs

    def set_temperature(self, temperature: float):
        logger.info(f"Calibrator temperature updated: {self.temperature} -> {temperature}")
        self.temperature = temperature


_default_calibrator = Calibrator()


def apply_calibration(probs: Union[float, np.ndarray], label: str = "") -> Union[float, np.ndarray]:
    return _default_calibrator.calibrate(probs, label=label)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    test_probs = np.array([0.42, 0.45, 0.50, 0.55, 0.58, 0.60, 0.70, 0.30])
    print("Input probabilities:", test_probs)
    sharpened = apply_calibration(test_probs, label="selftest")
    print("Sharpened probabilities:", sharpened)
    for p_in, p_out in zip(test_probs, sharpened):
        print(f"  {p_in:.3f} -> {p_out:.3f}  (delta={p_out - p_in:+.3f})")