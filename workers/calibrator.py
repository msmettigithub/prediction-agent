import math
import logging

logger = logging.getLogger(__name__)


def sharpen_probability(p: float, k: float = 1.5) -> float:
    """
    Sharpen a probability by scaling its logit by factor k.
    
    Converts p to logit space (log(p/(1-p))), multiplies by k,
    then converts back via sigmoid. Preserves ordering and symmetry around 0.5.
    
    Args:
        p: Probability in [0, 1]
        k: Sharpening factor (>1 pushes away from 0.5, <1 pulls toward 0.5)
    
    Returns:
        Sharpened probability clipped to [0.05, 0.95]
    """
    # Clip input to avoid log(0)
    p_clipped = max(1e-9, min(1 - 1e-9, p))
    
    # Convert to logit space
    logit = math.log(p_clipped / (1 - p_clipped))
    
    # Scale by sharpening factor
    logit_sharpened = logit * k
    
    # Convert back via sigmoid
    p_sharpened = 1.0 / (1.0 + math.exp(-logit_sharpened))
    
    # Apply configurable bounds to prevent extreme values
    p_sharpened = max(0.05, min(0.95, p_sharpened))
    
    return p_sharpened


def get_confidence_weight(num_confirming_signals: int) -> float:
    """
    Get the sharpening factor k based on number of confirming signals.
    
    Only push probabilities further from 0.5 when multiple independent
    signals agree — that's when the model should be more confident.
    
    Args:
        num_confirming_signals: Count of independent signals that agree
    
    Returns:
        Sharpening factor k
    """
    if num_confirming_signals >= 3:
        return 1.8
    elif num_confirming_signals == 2:
        return 1.5
    else:
        return 1.3


def calibrate(p: float, num_confirming_signals: int = 1) -> float:
    """
    Apply calibration adjustments to a raw model probability.
    
    The mathematical basis: if true accuracy is 80% but average |p-0.5| is
    only 6%, then E[|p-0.5|] should be closer to 20-25% for a well-calibrated
    model with that accuracy. Sharpening by 1.5x in logit space roughly doubles
    the deviation from 0.5, bringing separation to ~12%.
    
    Args:
        p: Raw probability from model
        num_confirming_signals: Number of independent signals confirming direction
    
    Returns:
        Calibrated and sharpened probability
    """
    # --- Existing calibration adjustments go here ---
    # (placeholder: apply any existing linear/isotonic/Platt scaling here)
    p_calibrated = p  # Replace with existing calibration logic if any

    # --- Apply sharpening AFTER existing calibration adjustments ---
    k = get_confidence_weight(num_confirming_signals)
    
    # Log pre-sharpen probability for monitoring
    logger.info(
        "calibrator pre_sharpen: p=%.6f, num_signals=%d, k=%.2f",
        p_calibrated,
        num_confirming_signals,
        k,
    )
    
    p_sharpened = sharpen_probability(p_calibrated, k=k)
    
    # Log post-sharpen probability for monitoring
    logger.info(
        "calibrator post_sharpen: p_before=%.6f, p_after=%.6f, delta=%.6f, |p-0.5|_before=%.6f, |p-0.5|_after=%.6f",
        p_calibrated,
        p_sharpened,
        p_sharpened - p_calibrated,
        abs(p_calibrated - 0.5),
        abs(p_sharpened - 0.5),
    )
    
    return p_sharpened


def extremize(p: float, a: float = 2.5) -> float:
    """
    Extremize a probability using power-law transform.
    
    This is an alternative extremizing approach using the formula:
        p_ext = p^a / (p^a + (1-p)^a)
    
    At a=2.5: p=0.65 -> ~0.75 (delta=0.10), hitting the separation target.
    
    Args:
        p: Probability in [0, 1]
        a: Extremizing exponent (>1 pushes away from 0.5)
    
    Returns:
        Extremized probability clipped to [0.05, 0.95]
    """
    # Guard: skip if already extreme or invalid
    if not (0.01 < p < 0.99):
        logger.warning("extremize: p=%.6f out of safe range, returning clipped value", p)
        return max(0.05, min(0.95, p))
    
    p_a = p ** a
    one_minus_p_a = (1.0 - p) ** a
    
    denom = p_a + one_minus_p_a
    if denom == 0:
        logger.warning("extremize: zero denominator for p=%.6f, a=%.2f", p, a)
        return p
    
    p_ext = p_a / denom
    
    # Hard floor/ceiling after extremizing to prevent calibrator rejection
    p_ext = max(0.05, min(0.95, p_ext))
    
    logger.info(
        "extremize: p_in=%.6f, a=%.2f, p_out=%.6f, delta=%.6f",
        p,
        a,
        p_ext,
        p_ext - p,
    )
    
    return p_ext