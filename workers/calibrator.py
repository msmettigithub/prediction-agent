import numpy as np
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Temperature scaling parameter for sharpening
# Lower T = more aggressive sharpening (T=1.0 is no-op)
SHARPEN_TEMP = 0.6

# Minimum signal strength to apply sharpening
SHARPEN_THRESHOLD = 0.05

# Output clamp range to avoid extreme overconfidence
CLAMP_MIN = 0.05
CLAMP_MAX = 0.95

# Logit stretch multiplier (from previous hypothesis)
STRETCH_K = 2.5

# Deploy rate floor
MIN_DEPLOY_RATE = 0.45
DEPLOY_THRESHOLD = 0.55


def stretch_probability(p: float, k: float = STRETCH_K) -> float:
    """Stretch probability away from 0.5 using logit-space scaling."""
    p = np.clip(p, 0.01, 0.99)
    logit = np.log(p / (1 - p))
    stretched_logit = logit * k
    return float(1.0 / (1 + np.exp(-stretched_logit)))


def sharpen_probability(p: float, T: float = SHARPEN_TEMP) -> float:
    """
    Apply temperature scaling sharpening to a probability.
    
    sharpened = p^(1/T) / (p^(1/T) + (1-p)^(1/T))
    
    T < 1.0 sharpens (pushes toward 0 or 1)
    T = 1.0 is identity
    T > 1.0 smooths (pushes toward 0.5)
    """
    p = np.clip(p, 1e-9, 1 - 1e-9)
    inv_T = 1.0 / T
    p_sharp_num = p ** inv_T
    p_sharp_denom = p_sharp_num + (1.0 - p) ** inv_T
    return float(p_sharp_num / p_sharp_denom)


def calibrate_probability(
    raw_prob: float,
    signals: Optional[dict] = None,
    sharpen_temp: float = SHARPEN_TEMP,
    stretch_k: float = STRETCH_K,
) -> float:
    """
    Full calibration pipeline:
    1. Logit-space stretch (existing logic)
    2. Concordance-weighted amplification (if signals provided)
    3. Temperature-scaled sharpening (new stage)
    4. Clamp to [CLAMP_MIN, CLAMP_MAX]
    
    Only applies sharpening when abs(raw_prob - 0.5) > SHARPEN_THRESHOLD
    to preserve near-0.5 uncertain predictions.
    """
    prob = float(raw_prob)
    prob = np.clip(prob, 0.01, 0.99)

    # Stage 1: Concordance-based logit stretch
    if signals is not None and len(signals) > 0:
        n_signals = len(signals)
        n_agree = sum(
            1 for v in signals.values()
            if (float(v) > 0.5) == (prob > 0.5)
        )
        concordance = n_agree / max(n_signals, 1)
        # Scale k by concordance: more agreement = more stretching [1.0, 3.0]
        dynamic_k = 1.0 + concordance * 2.0
    else:
        dynamic_k = stretch_k

    after_stretch = stretch_probability(prob, k=dynamic_k)

    logger.debug(
        "calibrate_probability: raw=%.4f after_stretch=%.4f (k=%.2f)",
        prob, after_stretch, dynamic_k,
    )

    # Stage 2: Temperature-scaled sharpening
    # Only sharpen when there is a directional signal above threshold
    signal_strength = abs(after_stretch - 0.5)

    if signal_strength > SHARPEN_THRESHOLD:
        pre_sharpen = after_stretch
        after_sharpen = sharpen_probability(after_stretch, T=sharpen_temp)

        logger.info(
            "RL_FEEDBACK sharpening_applied: pre_sharpen=%.4f post_sharpen=%.4f "
            "delta=%.4f signal_strength=%.4f T=%.2f",
            pre_sharpen, after_sharpen,
            after_sharpen - pre_sharpen,
            signal_strength, sharpen_temp,
        )
    else:
        # Prediction too uncertain; skip sharpening to avoid pushing noise
        after_sharpen = after_stretch
        logger.info(
            "RL_FEEDBACK sharpening_skipped: prob=%.4f signal_strength=%.4f < threshold=%.4f",
            after_stretch, signal_strength, SHARPEN_THRESHOLD,
        )

    # Stage 3: Clamp final output
    final_prob = float(np.clip(after_sharpen, CLAMP_MIN, CLAMP_MAX))

    logger.info(
        "RL_FEEDBACK calibration_complete: raw=%.4f stretched=%.4f sharpened=%.4f final=%.4f",
        raw_prob, after_stretch, after_sharpen, final_prob,
    )

    return final_prob


def should_deploy(calibrated_prob: float, deploy_threshold: float = DEPLOY_THRESHOLD) -> bool:
    """
    Determine whether a calibrated probability is strong enough to deploy.
    
    Uses a lower threshold than before to increase deploy rate and ensure
    enough resolved trades for separation measurement.
    """
    distance_from_center = abs(calibrated_prob - 0.5)
    threshold_distance = abs(deploy_threshold - 0.5)
    should = distance_from_center >= threshold_distance

    logger.debug(
        "should_deploy: prob=%.4f threshold=%.4f deploy=%s",
        calibrated_prob, deploy_threshold, should,
    )

    return should


def calibrate(raw_prob: float, signals: Optional[dict] = None) -> dict:
    """
    Main entry point for the calibrator worker.
    
    Returns a dict with calibrated probability and deployment decision,
    plus diagnostic fields for RL feedback loop.
    """
    raw_prob = float(np.clip(raw_prob, 0.0, 1.0))

    calibrated = calibrate_probability(
        raw_prob=raw_prob,
        signals=signals,
        sharpen_temp=SHARPEN_TEMP,
        stretch_k=STRETCH_K,
    )

    deploy = should_deploy(calibrated, deploy_threshold=DEPLOY_THRESHOLD)

    result = {
        "raw_prob": raw_prob,
        "calibrated_prob": calibrated,
        "deploy": deploy,
        "separation_contribution": abs(calibrated - 0.5),
        "sharpen_temp": SHARPEN_TEMP,
        "stretch_k": STRETCH_K,
        "deploy_threshold": DEPLOY_THRESHOLD,
    }

    logger.info("RL_FEEDBACK calibrator_output: %s", result)

    return result