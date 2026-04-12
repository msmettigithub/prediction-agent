import numpy as np


def extremize(p: float, alpha: float = 1.75) -> float:
    """
    Apply extremizing transform from Satopää et al. (2014).
    
    Pushes probabilities away from 0.5, amplifying the signal in
    already-directional predictions without changing rank ordering.
    
    p_ext = p^alpha / (p^alpha + (1-p)^alpha)
    
    Args:
        p: probability in [0, 1]
        alpha: exponent controlling extremizing strength (default 1.75)
               alpha=1.0 → identity, alpha>1.0 → push away from 0.5
    
    Returns:
        Extremized probability, clamped to [0.05, 0.95]
    """
    p = float(np.clip(p, 1e-9, 1 - 1e-9))
    
    p_alpha = p ** alpha
    q_alpha = (1.0 - p) ** alpha
    
    p_ext = p_alpha / (p_alpha + q_alpha)
    
    return float(np.clip(p_ext, 0.05, 0.95))


def calibrate(p: float, signal_count: int = 0, signal_agreement: float = 0.0, alpha: float = 1.75) -> float:
    """
    Calibrate raw probability output and apply extremizing transform.
    
    Pipeline:
      1. Clamp input to valid range
      2. Apply logit-space sharpening based on signal quality
      3. Apply extremizing transform (only when |p - 0.5| >= 0.02)
      4. Clamp output to [0.05, 0.95]
    
    Args:
        p: raw probability from model
        signal_count: number of signals contributing to prediction
        signal_agreement: fraction of signals agreeing on direction [0, 1]
        alpha: extremizing exponent, default 1.75
               (tuned to push separation from ~6% to ~12%)
    
    Returns:
        Calibrated and extremized probability in [0.05, 0.95]
    """
    p = float(np.clip(p, 1e-9, 1 - 1e-9))

    if abs(p - 0.5) < 0.05:
        return 0.5

    logit_p = np.log(p / (1.0 - p))

    if signal_count >= 3 and signal_agreement >= 0.67:
        scale = 1.3
    elif signal_count >= 2 and signal_agreement >= 0.5:
        scale = 1.15
    else:
        scale = 1.0

    logit_calibrated = logit_p * scale
    p_calibrated = 1.0 / (1.0 + np.exp(-logit_calibrated))
    p_calibrated = float(np.clip(p_calibrated, 1e-9, 1 - 1e-9))

    deviation = abs(p_calibrated - 0.5)
    if deviation >= 0.02:
        p_calibrated = extremize(p_calibrated, alpha=alpha)
    
    return float(np.clip(p_calibrated, 0.05, 0.95))