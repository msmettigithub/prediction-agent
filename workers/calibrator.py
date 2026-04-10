import numpy as np
import logging
from typing import Optional

logger = logging.getLogger(__name__)

EXTREMIZE_K = 1.75
CONFIDENCE_FLOOR = 0.03


def extremize(p: float, k: float = EXTREMIZE_K) -> float:
    """
    Apply extremization transform: p_ext = p^k / (p^k + (1-p)^k)

    Mathematically guaranteed to:
    - Preserve probability ordering (monotonic transform)
    - Push values symmetrically away from 0.5
    - Map [0,1] -> [0,1] with fixed points at 0, 0.5, 1

    Args:
        p: Input probability in [0, 1]
        k: Extremization exponent. k > 1 pushes away from 0.5,
           k = 1 is identity, k < 1 shrinks toward 0.5

    Returns:
        Extremized probability in [0, 1]
    """
    if not 0.0 <= p <= 1.0:
        raise ValueError(f"Probability must be in [0, 1], got {p}")
    if k <= 0:
        raise ValueError(f"Extremization exponent k must be positive, got {k}")

    if p == 0.0:
        return 0.0
    if p == 1.0:
        return 1.0

    p_k = p ** k
    one_minus_p_k = (1.0 - p) ** k
    denom = p_k + one_minus_p_k

    if denom == 0.0:
        return p

    return p_k / denom


def calibrate(
    raw_prob: float,
    k: float = EXTREMIZE_K,
    confidence_floor: float = CONFIDENCE_FLOOR,
    context: Optional[dict] = None,
) -> float:
    """
    Full calibration pipeline. Applies extremization as the final step
    after all other calibration adjustments.

    The confidence_floor check prevents amplifying noise on genuinely
    uncertain predictions: if |p - 0.5| <= confidence_floor the raw
    probability is returned unchanged.

    Args:
        raw_prob: Raw model probability output in [0, 1]
        k: Extremization exponent (tunable via RL). Defaults to EXTREMIZE_K.
        confidence_floor: Minimum |p - 0.5| required to trigger extremization.
                          Predictions within this band of 0.5 are left alone.
        context: Optional dict of extra metadata (market id, feature values,
                 etc.) included in log records for future RL feedback loops.

    Returns:
        Calibrated and (conditionally) extremized probability in [0, 1]
    """
    if not 0.0 <= raw_prob <= 1.0:
        raise ValueError(f"raw_prob must be in [0, 1], got {raw_prob}")

    # ------------------------------------------------------------------
    # Step 1: placeholder for upstream calibration steps
    # (isotonic regression, Platt scaling, temperature scaling, etc.)
    # Those transforms run first; their output feeds into extremization.
    # ------------------------------------------------------------------
    pre_extremize_prob = raw_prob

    # ------------------------------------------------------------------
    # Step 2: Confidence floor check
    # ------------------------------------------------------------------
    distance_from_half = abs(pre_extremize_prob - 0.5)
    should_extremize = distance_from_half > confidence_floor

    if not should_extremize:
        logger.debug(
            "extremization_skipped",
            extra={
                "pre_extremize_prob": pre_extremize_prob,
                "distance_from_half": distance_from_half,
                "confidence_floor": confidence_floor,
                "k": k,
                "context": context or {},
            },
        )
        return pre_extremize_prob

    # ------------------------------------------------------------------
    # Step 3: Apply extremization transform
    # ------------------------------------------------------------------
    post_extremize_prob = extremize(pre_extremize_prob, k=k)

    # ------------------------------------------------------------------
    # Step 4: Log pre/post values to enable RL feedback loop optimisation
    # of k. Each record contains everything needed to compute a reward
    # signal (Brier improvement, separation delta, etc.) once the market
    # resolves.
    # ------------------------------------------------------------------
    logger.info(
        "extremization_applied",
        extra={
            "pre_extremize_prob": pre_extremize_prob,
            "post_extremize_prob": post_extremize_prob,
            "delta": post_extremize_prob - pre_extremize_prob,
            "distance_from_half_pre": distance_from_half,
            "distance_from_half_post": abs(post_extremize_prob - 0.5),
            "k": k,
            "confidence_floor": confidence_floor,
            "context": context or {},
        },
    )

    return post_extremize_prob


def batch_calibrate(
    raw_probs: list,
    k: float = EXTREMIZE_K,
    confidence_floor: float = CONFIDENCE_FLOOR,
    contexts: Optional[list] = None,
) -> list:
    """
    Vectorised wrapper around calibrate() for processing a batch of
    predictions in one call.

    Args:
        raw_probs: List of raw model probabilities, each in [0, 1]
        k: Extremization exponent applied uniformly to the batch.
        confidence_floor: Minimum |p - 0.5| to trigger extremization.
        contexts: Optional list of context dicts aligned with raw_probs.

    Returns:
        List of calibrated probabilities with the same length as raw_probs.
    """
    if contexts is None:
        contexts = [None] * len(raw_probs)

    if len(contexts) != len(raw_probs):
        raise ValueError(
            f"contexts length {len(contexts)} must match raw_probs length {len(raw_probs)}"
        )

    return [
        calibrate(p, k=k, confidence_floor=confidence_floor, context=ctx)
        for p, ctx in zip(raw_probs, contexts)
    ]


def separation_stats(probs: list) -> dict:
    """
    Compute separation diagnostics on a list of calibrated probabilities.
    Useful for monitoring whether extremization is achieving the target
    mean |p - 0.5| > 0.10.

    Args:
        probs: List of calibrated probabilities in [0, 1]

    Returns:
        Dict with mean_separation, median_separation, fraction_above_floor,
        and fraction_above_10pct.
    """
    if not probs:
        return {}

    arr = np.array(probs, dtype=float)
    distances = np.abs(arr - 0.5)

    stats = {
        "mean_separation": float(np.mean(distances)),
        "median_separation": float(np.median(distances)),
        "std_separation": float(np.std(distances)),
        "fraction_above_floor": float(np.mean(distances > CONFIDENCE_FLOOR)),
        "fraction_above_10pct": float(np.mean(distances > 0.10)),
        "n": len(probs),
    }

    logger.info("separation_stats", extra=stats)
    return stats