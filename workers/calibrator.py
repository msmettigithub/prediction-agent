import logging
import numpy as np

try:
    from config import EXTREMIZE_ALPHA
except (ImportError, AttributeError):
    EXTREMIZE_ALPHA = 1.8

logger = logging.getLogger(__name__)

BASE_RATE = 0.50
BASE_RATE_BLEND = 0.15
PROB_FLOOR = 0.05
PROB_CEIL = 0.95


def extremize(p: float, alpha: float = None) -> float:
    """
    Symmetric logit extremization transform.

    p_ext = p**alpha / (p**alpha + (1-p)**alpha)

    For alpha > 1, probabilities are pushed away from 0.5 toward 0 and 1.
    For alpha = 1, the function is the identity.

    Args:
        p: Input probability in [0, 1]
        alpha: Extremization exponent. Values in [1.5, 2.5] are typical.
               Defaults to EXTREMIZE_ALPHA from config (or 1.8).

    Returns:
        Extremized probability, clamped to [PROB_FLOOR, PROB_CEIL].
    """
    if alpha is None:
        alpha = EXTREMIZE_ALPHA

    p = float(np.clip(p, 1e-9, 1.0 - 1e-9))

    p_alpha = p ** alpha
    q_alpha = (1.0 - p) ** alpha
    denom = p_alpha + q_alpha

    if denom == 0.0:
        logger.warning("extremize: zero denominator for p=%.6f alpha=%.3f; returning 0.5", p, alpha)
        return 0.5

    p_ext = p_alpha / denom
    p_clamped = float(np.clip(p_ext, PROB_FLOOR, PROB_CEIL))
    return p_clamped


def blend_base_rate(p: float, base_rate: float = BASE_RATE, blend_weight: float = BASE_RATE_BLEND) -> float:
    """
    Blend a probability toward the base rate (shrinkage / regularization).

    p_blended = (1 - blend_weight) * p + blend_weight * base_rate

    Args:
        p: Raw probability estimate.
        base_rate: Prior base rate (default 0.50 for symmetric markets).
        blend_weight: Weight on the base rate in [0, 1].

    Returns:
        Blended probability.
    """
    p = float(np.clip(p, 0.0, 1.0))
    return (1.0 - blend_weight) * p + blend_weight * base_rate


def calibrate(raw_prob: float, alpha: float = None, base_rate: float = BASE_RATE,
              blend_weight: float = BASE_RATE_BLEND, market_id: str = None) -> float:
    """
    Full calibration pipeline:
        1. Clamp raw input to valid probability range.
        2. Blend toward base rate (reduces overconfidence from sparse data).
        3. Extremize via symmetric logit transform (amplifies directional signal).
        4. Clamp final output to [PROB_FLOOR, PROB_CEIL].

    Args:
        raw_prob: Raw model probability in [0, 1].
        alpha: Extremization exponent. Defaults to EXTREMIZE_ALPHA config value.
        base_rate: Prior base rate for blending. Default 0.50.
        blend_weight: Weight on base rate during blending. Default BASE_RATE_BLEND.
        market_id: Optional identifier for logging context.

    Returns:
        Calibrated probability in [PROB_FLOOR, PROB_CEIL].
    """
    if alpha is None:
        alpha = EXTREMIZE_ALPHA

    context = f"market={market_id}" if market_id else "no-market-id"

    # Step 1: Clamp raw input
    raw_prob = float(np.clip(raw_prob, 0.0, 1.0))
    logger.debug("[%s] raw_prob=%.6f", context, raw_prob)

    # Step 2: Blend toward base rate
    blended = blend_base_rate(raw_prob, base_rate=base_rate, blend_weight=blend_weight)
    logger.debug("[%s] after_base_rate_blend=%.6f (base_rate=%.3f, blend_weight=%.3f)",
                 context, blended, base_rate, blend_weight)

    # Step 3: Extremize
    pre_ext = blended
    post_ext = extremize(blended, alpha=alpha)

    distance_before = abs(pre_ext - 0.5)
    distance_after = abs(post_ext - 0.5)

    logger.info(
        "[%s] extremization: pre=%.6f (dist_from_0.5=%.4f) -> post=%.6f (dist_from_0.5=%.4f) "
        "alpha=%.3f delta_dist=%.4f",
        context, pre_ext, distance_before, post_ext, distance_after, alpha,
        distance_after - distance_before
    )

    # Step 4: Final clamp (already applied inside extremize, but be explicit)
    final = float(np.clip(post_ext, PROB_FLOOR, PROB_CEIL))

    if final != post_ext:
        logger.debug("[%s] final clamp applied: %.6f -> %.6f", context, post_ext, final)

    logger.debug("[%s] final_calibrated_prob=%.6f", context, final)
    return final


def calibrate_batch(raw_probs, alpha: float = None, base_rate: float = BASE_RATE,
                    blend_weight: float = BASE_RATE_BLEND, market_ids=None):
    """
    Calibrate a batch of raw probabilities.

    Args:
        raw_probs: Iterable of raw probabilities.
        alpha: Extremization exponent. Defaults to EXTREMIZE_ALPHA.
        base_rate: Prior base rate for blending.
        blend_weight: Weight on base rate during blending.
        market_ids: Optional iterable of market identifiers for logging.

    Returns:
        List of calibrated probabilities.
    """
    if alpha is None:
        alpha = EXTREMIZE_ALPHA

    probs = list(raw_probs)
    ids = list(market_ids) if market_ids is not None else [None] * len(probs)

    if len(ids) != len(probs):
        logger.warning(
            "calibrate_batch: market_ids length %d != probs length %d; ignoring ids",
            len(ids), len(probs)
        )
        ids = [None] * len(probs)

    results = [
        calibrate(p, alpha=alpha, base_rate=base_rate, blend_weight=blend_weight, market_id=mid)
        for p, mid in zip(probs, ids)
    ]

    if results:
        arr = np.array(results)
        logger.info(
            "calibrate_batch: n=%d alpha=%.3f mean=%.4f std=%.4f "
            "mean_dist_from_0.5=%.4f min=%.4f max=%.4f",
            len(results), alpha, arr.mean(), arr.std(),
            np.mean(np.abs(arr - 0.5)), arr.min(), arr.max()
        )

    return results