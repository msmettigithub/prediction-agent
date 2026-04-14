import numpy as np
from config import SHARPENING_GAMMA, SHARPENING_MIN_CONFIDENCE

def sharpen_probability(p: float) -> float:
    """
    Apply post-calibration sharpening to increase separation while preserving rank order.
    
    Uses a power transform in centered space:
        centered = 2*p - 1
        sharpened = sign(centered) * |centered|^GAMMA
        p_final = 0.5 + sharpened / 2
    
    Only applied when |p - 0.5| > SHARPENING_MIN_CONFIDENCE to avoid
    amplifying noise on truly uncertain predictions.
    
    Args:
        p: Raw calibrated probability in [0, 1]
    
    Returns:
        Sharpened probability clamped to [0.05, 0.95]
    """
    deviation = abs(p - 0.5)
    
    if deviation <= SHARPENING_MIN_CONFIDENCE:
        return float(np.clip(p, 0.05, 0.95))
    
    centered = 2.0 * p - 1.0
    sign = np.sign(centered)
    magnitude = abs(centered)
    sharpened_mag = magnitude ** SHARPENING_GAMMA
    p_final = 0.5 + sign * sharpened_mag / 2.0
    
    return float(np.clip(p_final, 0.05, 0.95))


def calibrate(raw_scores, calibration_model=None):
    """
    Calibrate raw model scores to probabilities, then apply sharpening.
    
    Args:
        raw_scores: Raw model output scores (numpy array or list)
        calibration_model: Optional fitted calibration model (e.g., isotonic regression)
    
    Returns:
        Numpy array of calibrated and sharpened probabilities
    """
    raw_scores = np.asarray(raw_scores, dtype=float)
    
    if calibration_model is not None:
        calibrated = calibration_model.predict(raw_scores)
    else:
        calibrated = np.clip(raw_scores, 0.0, 1.0)
    
    sharpened = np.array([sharpen_probability(p) for p in calibrated])
    
    return sharpened


def calibrate_single(raw_score: float, calibration_model=None) -> float:
    """
    Calibrate a single raw score to a sharpened probability.
    
    Args:
        raw_score: Single raw model output score
        calibration_model: Optional fitted calibration model
    
    Returns:
        Calibrated and sharpened probability in [0.05, 0.95]
    """
    result = calibrate(np.array([raw_score]), calibration_model=calibration_model)
    return float(result[0])