import logging
import math
from typing import Optional

logger = logging.getLogger(__name__)

# Category-specific base rates for Bayesian anchoring
CATEGORY_BASE_RATES = {
    "politics": 0.45,
    "economics": 0.50,
    "sports": 0.52,
    "technology": 0.55,
    "science": 0.50,
    "entertainment": 0.48,
    "weather": 0.50,
    "default": 0.50,
}

# Default configuration
DEFAULT_EXPONENT = 1.8
MAX_EXPONENT = 2.5
PRIOR_WEIGHT = 0.3
FLOOR = 0.08
CEILING = 0.92


def extremize(p: float, alpha: float = DEFAULT_EXPONENT) -> float:
    """
    Maps probabilities away from 0.5 using the formula:
    p_ext = p^a / (p^a + (1-p)^a)

    For alpha=1.8:
      p=0.6 -> ~0.64
      p=0.7 -> ~0.75
      p=0.8 -> ~0.86

    For alpha=1.0, the function is identity (no change).
    """
    if p <= 0.0:
        return 0.0
    if p >= 1.0:
        return 1.0

    pa = p ** alpha
    one_minus_pa = (1.0 - p) ** alpha

    denom = pa + one_minus_pa
    if denom == 0.0:
        return p

    return pa / denom


def anchor_to_base_rate(
    raw_prob: float,
    category: str = "default",
    prior_weight: float = PRIOR_WEIGHT,
) -> float:
    """
    Blends raw probability with category-specific base rate using
    a Bayesian-style update:
      anchored = prior_weight * base_rate + (1 - prior_weight) * raw_prob

    This prevents the model from drifting too far from known base rates
    while still allowing the signal to dominate.
    """
    base_rate = CATEGORY_BASE_RATES.get(category, CATEGORY_BASE_RATES["default"])
    anchored = prior_weight * base_rate + (1.0 - prior_weight) * raw_prob
    logger.debug(
        "Base rate anchoring: raw=%.4f category=%s base_rate=%.4f "
        "prior_weight=%.2f anchored=%.4f",
        raw_prob,
        category,
        base_rate,
        prior_weight,
        anchored,
    )
    return anchored


def compute_exponent(num_agreeing_signals: int, total_signals: int) -> float:
    """
    Scales the extremization exponent based on signal agreement.

    When all signals agree, uses MAX_EXPONENT.
    When no signals agree, uses DEFAULT_EXPONENT.
    Scales linearly between them based on agreement fraction.

    num_agreeing_signals: number of signals pointing in the same direction
    total_signals: total number of independent signals evaluated
    """
    if total_signals <= 0:
        return DEFAULT_EXPONENT

    agreement_fraction = num_agreeing_signals / total_signals
    exponent = DEFAULT_EXPONENT + (MAX_EXPONENT - DEFAULT_EXPONENT) * agreement_fraction

    logger.debug(
        "Signal confidence scaling: %d/%d signals agree -> exponent=%.3f",
        num_agreeing_signals,
        total_signals,
        exponent,
    )
    return exponent


def clamp(p: float, floor: float = FLOOR, ceiling: float = CEILING) -> float:
    """
    Applies floor/ceiling clamp to avoid overconfidence on edge cases.
    """
    return max(floor, min(ceiling, p))


def calibrate(
    raw_prob: float,
    category: str = "default",
    num_agreeing_signals: int = 0,
    total_signals: int = 0,
    prior_weight: float = PRIOR_WEIGHT,
    base_exponent: float = DEFAULT_EXPONENT,
) -> float:
    """
    Full calibration pipeline:

    1. Anchor to base rate (Bayesian blend with category prior)
    2. Compute exponent based on signal confidence
    3. Apply extremization
    4. Clamp to floor/ceiling

    Example:
      raw_prob=0.62, category with base_rate=0.70, 3/3 signals agree
      -> anchored = 0.3*0.70 + 0.7*0.62 = 0.644
      -> exponent = 2.5 (all signals agree)
      -> extremized = 0.644^2.5 / (0.644^2.5 + 0.356^2.5) ≈ 0.78
      -> clamped = 0.78 (within bounds)

    Logs pre- and post-calibration values for RL tuning.
    """
    logger.info(
        "PRE_CALIBRATION: raw_prob=%.4f category=%s "
        "agreeing_signals=%d total_signals=%d",
        raw_prob,
        category,
        num_agreeing_signals,
        total_signals,
    )

    # Step 1: Base-rate anchoring
    anchored = anchor_to_base_rate(raw_prob, category, prior_weight)

    # Step 2: Compute signal-confidence-scaled exponent
    alpha = compute_exponent(num_agreeing_signals, total_signals)

    # Step 3: Extremization
    extremized = extremize(anchored, alpha)

    # Step 4: Clamp
    result = clamp(extremized)

    logger.info(
        "POST_CALIBRATION: raw=%.4f anchored=%.4f alpha=%.3f "
        "extremized=%.4f clamped=%.4f shift=%.4f",
        raw_prob,
        anchored,
        alpha,
        extremized,
        result,
        result - raw_prob,
    )

    return result


def calibrate_batch(predictions: list[dict]) -> list[dict]:
    """
    Calibrate a batch of predictions for distribution logging.

    Each prediction dict should have:
      - 'prob': float, raw probability
      - 'category': str (optional)
      - 'num_agreeing_signals': int (optional)
      - 'total_signals': int (optional)

    Returns list of dicts with added 'calibrated_prob' field.

    Logs distribution statistics before and after calibration
    for RL tuning iterations.
    """
    if not predictions:
        return predictions

    raw_probs = [p.get("prob", 0.5) for p in predictions]
    results = []

    for pred in predictions:
        raw_prob = pred.get("prob", 0.5)
        category = pred.get("category", "default")
        num_agreeing = pred.get("num_agreeing_signals", 0)
        total_sigs = pred.get("total_signals", 0)

        calibrated = calibrate(
            raw_prob=raw_prob,
            category=category,
            num_agreeing_signals=num_agreeing,
            total_signals=total_sigs,
        )

        result = dict(pred)
        result["calibrated_prob"] = calibrated
        results.append(result)

    calibrated_probs = [r["calibrated_prob"] for r in results]

    # Log distribution statistics for RL tuning
    if raw_probs:
        raw_mean = sum(raw_probs) / len(raw_probs)
        raw_spread = max(raw_probs) - min(raw_probs)
        cal_mean = sum(calibrated_probs) / len(calibrated_probs)
        cal_spread = max(calibrated_probs) - min(calibrated_probs)

        # Compute variance as proxy for separation
        raw_var = sum((p - raw_mean) ** 2 for p in raw_probs) / len(raw_probs)
        cal_var = sum((p - cal_mean) ** 2 for p in calibrated_probs) / len(calibrated_probs)

        logger.info(
            "CALIBRATION_DISTRIBUTION: n=%d "
            "raw_mean=%.4f raw_spread=%.4f raw_variance=%.4f | "
            "cal_mean=%.4f cal_spread=%.4f cal_variance=%.4f | "
            "variance_increase=%.4f",
            len(predictions),
            raw_mean,
            raw_spread,
            raw_var,
            cal_mean,
            cal_spread,
            cal_var,
            cal_var - raw_var,
        )

    return results


def log_calibration_params() -> None:
    """
    Log current calibration hyperparameters for RL tracking.
    Useful for correlating parameter settings with performance outcomes.
    """
    logger.info(
        "CALIBRATION_PARAMS: default_exponent=%.2f max_exponent=%.2f "
        "prior_weight=%.2f floor=%.2f ceiling=%.2f",
        DEFAULT_EXPONENT,
        MAX_EXPONENT,
        PRIOR_WEIGHT,
        FLOOR,
        CEILING,
    )


# Log params at module load time so every run captures the active config
log_calibration_params()