import logging
import math

logger = logging.getLogger(__name__)

# Configurable sharpness parameter for power-law sharpening
# k=1.0 is identity, k>1 pushes probabilities away from 0.5
# Future RL iterations can tune this value
CALIBRATION_SHARPNESS = 1.8

# Minimum distance from center threshold to avoid low-conviction noise
NO_TRADE_ZONE_THRESHOLD = 0.04

# Clamp bounds to avoid overconfidence
OUTPUT_CLAMP_MIN = 0.05
OUTPUT_CLAMP_MAX = 0.95


def sharpen_probability(p: float, k: float = CALIBRATION_SHARPNESS) -> float:
    """
    Apply power-law sharpening transformation to a probability.
    
    p_sharp = p^k / (p^k + (1-p)^k)
    
    This is monotonic and preserves rank ordering while increasing separation.
    k=1.0 is identity, k>1 pushes probabilities away from 0.5.
    
    Examples with k=1.8:
        p=0.60 -> ~0.65
        p=0.40 -> ~0.35
        p=0.70 -> ~0.77
        p=0.30 -> ~0.23
    """
    if k == 1.0:
        return p
    
    # Guard against edge cases
    if p <= 0.0:
        return 0.0
    if p >= 1.0:
        return 1.0
    
    p_k = p ** k
    q_k = (1.0 - p) ** k
    denominator = p_k + q_k
    
    if denominator == 0.0:
        return 0.5
    
    return p_k / denominator


def apply_no_trade_zone(p: float, threshold: float = NO_TRADE_ZONE_THRESHOLD) -> float:
    """
    Snap probabilities close to 0.5 to exactly 0.5 to avoid low-conviction noise.
    
    If abs(p - 0.5) < threshold, return 0.5 exactly.
    This creates a no-trade zone for weak signals.
    """
    if abs(p - 0.5) < threshold:
        return 0.5
    return p


def clamp_probability(p: float, min_val: float = OUTPUT_CLAMP_MIN, max_val: float = OUTPUT_CLAMP_MAX) -> float:
    """
    Clamp probability to [min_val, max_val] to avoid overconfidence.
    """
    return max(min_val, min(max_val, p))


def calibrate(raw_p: float, k: float = CALIBRATION_SHARPNESS) -> float:
    """
    Full calibration pipeline:
    1. Apply power-law sharpening
    2. Apply no-trade zone snap
    3. Clamp to safe output range
    
    Logs both raw and sharpened probabilities for backtest analysis.
    
    Args:
        raw_p: Raw probability from model, expected in [0, 1]
        k: Sharpening exponent (default CALIBRATION_SHARPNESS)
    
    Returns:
        Calibrated probability in [OUTPUT_CLAMP_MIN, OUTPUT_CLAMP_MAX]
        or exactly 0.5 if in no-trade zone
    """
    # Step 1: Apply power-law sharpening
    p_sharp = sharpen_probability(raw_p, k=k)
    
    # Step 2: Apply no-trade zone
    p_no_trade = apply_no_trade_zone(p_sharp)
    
    # Step 3: Clamp final output
    p_final = clamp_probability(p_no_trade)
    
    # Log both raw and sharpened for backtest analysis
    logger.debug(
        "Calibration pipeline: raw_p=%.4f | sharpened=%.4f | no_trade_zone=%.4f | final=%.4f | k=%.2f",
        raw_p,
        p_sharp,
        p_no_trade,
        p_final,
        k,
    )
    
    if p_no_trade == 0.5:
        logger.debug(
            "No-trade zone triggered: raw_p=%.4f sharpened=%.4f within %.3f of 0.5",
            raw_p,
            p_sharp,
            NO_TRADE_ZONE_THRESHOLD,
        )
    
    separation_raw = abs(raw_p - 0.5)
    separation_final = abs(p_final - 0.5)
    if separation_raw > 0:
        amplification = separation_final / separation_raw
        logger.debug(
            "Separation amplification: raw=%.4f final=%.4f ratio=%.2fx",
            separation_raw,
            separation_final,
            amplification,
        )
    
    return p_final


def calibrate_batch(probabilities: list, k: float = CALIBRATION_SHARPNESS) -> list:
    """
    Apply calibration to a batch of probabilities.
    
    Args:
        probabilities: List of raw probabilities
        k: Sharpening exponent
    
    Returns:
        List of calibrated probabilities
    """
    results = []
    for raw_p in probabilities:
        results.append(calibrate(raw_p, k=k))
    
    # Log batch statistics for analysis
    if probabilities:
        raw_separations = [abs(p - 0.5) for p in probabilities]
        final_separations = [abs(p - 0.5) for p in results]
        no_trade_count = sum(1 for p in results if p == 0.5)
        
        avg_raw_sep = sum(raw_separations) / len(raw_separations)
        avg_final_sep = sum(final_separations) / len(final_separations)
        
        logger.info(
            "Batch calibration: n=%d | avg_raw_separation=%.4f | avg_final_separation=%.4f | no_trade_count=%d | k=%.2f",
            len(probabilities),
            avg_raw_sep,
            avg_final_sep,
            no_trade_count,
            k,
        )
    
    return results