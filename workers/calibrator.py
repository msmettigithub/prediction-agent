import numpy as np
import logging
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from config import CALIBRATION_STRETCH_FACTOR, CALIBRATION_ACCURACY_THRESHOLD
except ImportError:
    CALIBRATION_STRETCH_FACTOR = 1.35
    CALIBRATION_ACCURACY_THRESHOLD = 0.70


def sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -500, 500)))


def confidence_stretch(
    p: float,
    stretch_factor: float = CALIBRATION_STRETCH_FACTOR,
) -> float:
    p_clipped = float(np.clip(p, 0.001, 0.999))
    logit = np.log(p_clipped / (1.0 - p_clipped))
    stretched_logit = logit * stretch_factor
    p_new = float(sigmoid(np.array([stretched_logit]))[0])
    return p_new


def calibrate_probability(
    raw_prob: float,
    base_model_accuracy: Optional[float] = None,
    stretch_factor: float = CALIBRATION_STRETCH_FACTOR,
    accuracy_threshold: float = CALIBRATION_ACCURACY_THRESHOLD,
) -> float:
    if not (0.0 <= raw_prob <= 1.0):
        logger.warning(f"Raw probability {raw_prob} out of [0,1], clamping.")
        raw_prob = float(np.clip(raw_prob, 0.0, 1.0))

    p = float(np.clip(raw_prob, 0.001, 0.999))

    p = _apply_isotonic_adjustment(p)
    p = _apply_temperature_scaling(p)

    pre_stretch = p
    logger.debug(f"Pre-stretch probability: {pre_stretch:.6f}")

    should_stretch = True
    if base_model_accuracy is not None:
        if base_model_accuracy <= accuracy_threshold:
            should_stretch = False
            logger.info(
                f"Skipping confidence stretch: base_model_accuracy={base_model_accuracy:.4f} "
                f"<= threshold={accuracy_threshold:.4f}"
            )
        else:
            logger.info(
                f"Applying confidence stretch: base_model_accuracy={base_model_accuracy:.4f} "
                f"> threshold={accuracy_threshold:.4f}"
            )

    if should_stretch:
        p = confidence_stretch(p, stretch_factor=stretch_factor)
        post_stretch = p
        logger.info(
            f"Confidence stretch applied: pre={pre_stretch:.6f} -> post={post_stretch:.6f} "
            f"(stretch_factor={stretch_factor}, delta={post_stretch - pre_stretch:+.6f})"
        )
        separation_pre = abs(pre_stretch - 0.5)
        separation_post = abs(post_stretch - 0.5)
        logger.info(
            f"Separation from 0.5: pre={separation_pre:.6f} -> post={separation_post:.6f} "
            f"(gain={separation_post - separation_pre:+.6f})"
        )
    else:
        logger.debug(f"Confidence stretch skipped, probability unchanged: {p:.6f}")

    p_final = float(np.clip(p, 0.05, 0.95))

    if abs(p_final - p) > 1e-9:
        logger.debug(f"Final clamp applied: {p:.6f} -> {p_final:.6f}")

    logger.debug(
        f"calibrate_probability complete: raw={raw_prob:.6f} -> final={p_final:.6f}"
    )

    return p_final


def calibrate_batch(
    raw_probs: list,
    base_model_accuracy: Optional[float] = None,
    stretch_factor: float = CALIBRATION_STRETCH_FACTOR,
    accuracy_threshold: float = CALIBRATION_ACCURACY_THRESHOLD,
) -> list:
    if not raw_probs:
        return []

    calibrated = [
        calibrate_probability(
            p,
            base_model_accuracy=base_model_accuracy,
            stretch_factor=stretch_factor,
            accuracy_threshold=accuracy_threshold,
        )
        for p in raw_probs
    ]

    separations = [abs(p - 0.5) for p in calibrated]
    raw_separations = [abs(p - 0.5) for p in raw_probs]
    logger.info(
        f"Batch calibration summary: n={len(calibrated)}, "
        f"mean_separation_raw={np.mean(raw_separations):.6f}, "
        f"mean_separation_calibrated={np.mean(separations):.6f}, "
        f"mean_calibrated_prob={np.mean(calibrated):.6f}, "
        f"std_calibrated_prob={np.std(calibrated):.6f}"
    )

    return calibrated


def _apply_isotonic_adjustment(p: float) -> float:
    return p


def _apply_temperature_scaling(p: float, temperature: float = 1.0) -> float:
    if abs(temperature - 1.0) < 1e-9:
        return p
    p_clipped = float(np.clip(p, 0.001, 0.999))
    logit = np.log(p_clipped / (1.0 - p_clipped))
    scaled_logit = logit / temperature
    return float(sigmoid(np.array([scaled_logit]))[0])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    test_cases = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90,
                  0.45, 0.40, 0.35, 0.30, 0.25, 0.20]

    print(f"{'Raw':>8} {'Stretched':>12} {'Delta':>10} {'Sep_raw':>10} {'Sep_new':>10}")
    print("-" * 55)
    for raw in test_cases:
        stretched = confidence_stretch(raw, stretch_factor=CALIBRATION_STRETCH_FACTOR)
        delta = stretched - raw
        sep_raw = abs(raw - 0.5)
        sep_new = abs(stretched - 0.5)
        print(f"{raw:>8.4f} {stretched:>12.6f} {delta:>+10.6f} {sep_raw:>10.6f} {sep_new:>10.6f}")

    print()
    print("Full calibration pipeline test (accuracy=0.804):")
    for raw in [0.55, 0.60, 0.65, 0.70, 0.80]:
        final = calibrate_probability(raw, base_model_accuracy=0.804)
        print(f"  raw={raw:.2f} -> final={final:.6f}")

    print()
    print("Full calibration pipeline test (accuracy=0.65 - below threshold, no stretch):")
    for raw in [0.55, 0.60, 0.65, 0.70, 0.80]:
        final = calibrate_probability(raw, base_model_accuracy=0.65)
        print(f"  raw={raw:.2f} -> final={final:.6f}")