import logging
import math
from typing import Optional

logger = logging.getLogger(__name__)

BASE_RATES = {
    "geopolitical": 0.15,
    "economic": 0.35,
    "tech": 0.60,
    "sports": 0.50,
    "political": 0.30,
    "scientific": 0.45,
    "social": 0.40,
    "default": 0.50,
}

DEFAULT_ALPHA = 1.5
DEFAULT_ANCHOR_WEIGHT = 0.25
CONFIDENCE_FLOOR = 0.05


def sharpen(p: float, alpha: float = DEFAULT_ALPHA) -> float:
    """
    Beta-calibration power sharpening transform.
    p_sharp = p^alpha / (p^alpha + (1-p)^alpha)
    Preserves 0.5 as fixed point, pushes values away from center.
    alpha > 1 increases separation; alpha=1 is identity.
    """
    if alpha <= 0:
        raise ValueError(f"alpha must be positive, got {alpha}")
    p = max(1e-9, min(1 - 1e-9, p))
    p_alpha = p ** alpha
    q_alpha = (1.0 - p) ** alpha
    denom = p_alpha + q_alpha
    if denom == 0:
        return p
    p_sharp = p_alpha / denom
    return float(p_sharp)


def anchor_to_base_rate(
    p: float,
    category: str = "default",
    anchor_weight: float = DEFAULT_ANCHOR_WEIGHT,
) -> float:
    """
    Blend model probability with category-specific historical base rate.
    p_anchored = (1 - anchor_weight) * p + anchor_weight * base_rate
    Pulls predictions away from 50/50 for categories with skewed priors.
    """
    category_key = category.lower().strip() if category else "default"
    base_rate = BASE_RATES.get(category_key, BASE_RATES["default"])
    p_anchored = (1.0 - anchor_weight) * p + anchor_weight * base_rate
    logger.debug(
        f"base_rate_anchor: category={category_key}, base_rate={base_rate:.3f}, "
        f"p_in={p:.4f}, p_anchored={p_anchored:.4f}, anchor_weight={anchor_weight}"
    )
    return float(p_anchored)


def confidence_floor_check(p: float, floor: float = CONFIDENCE_FLOOR) -> Optional[float]:
    """
    Returns None if |p - 0.5| < floor, signaling low-conviction skip.
    Otherwise returns p unchanged.
    """
    distance = abs(p - 0.5)
    if distance < floor:
        logger.info(
            f"confidence_floor: |{p:.4f} - 0.5| = {distance:.4f} < floor={floor:.4f}. "
            f"Skipping trade (low conviction)."
        )
        return None
    return p


def calibrate(
    p_raw: float,
    category: str = "default",
    alpha: float = DEFAULT_ALPHA,
    anchor_weight: float = DEFAULT_ANCHOR_WEIGHT,
    floor: float = CONFIDENCE_FLOOR,
) -> Optional[float]:
    """
    Full calibration pipeline:
      1. base_rate_anchor  — blend with category prior
      2. sharpen           — power-law variance expansion
      3. confidence_floor  — skip low-conviction predictions

    Returns calibrated probability, or None if below confidence floor.
    Logs pre/post probabilities and delta for RL tracking.
    """
    p_raw = float(p_raw)

    if not (0.0 <= p_raw <= 1.0):
        raise ValueError(f"p_raw must be in [0, 1], got {p_raw}")

    logger.info(f"calibrate_start: p_raw={p_raw:.4f}, category={category}")

    # Step 1: Base-rate anchoring
    p_anchored = anchor_to_base_rate(p_raw, category=category, anchor_weight=anchor_weight)
    logger.debug(f"post_anchor: p={p_anchored:.4f}")

    # Step 2: Sharpening
    p_sharp = sharpen(p_anchored, alpha=alpha)
    logger.debug(f"post_sharpen: p={p_sharp:.4f}")

    # Step 3: Confidence floor
    p_final = confidence_floor_check(p_sharp, floor=floor)

    if p_final is None:
        delta = p_sharp - p_raw
        logger.info(
            f"calibrate_result: p_raw={p_raw:.4f}, p_anchored={p_anchored:.4f}, "
            f"p_sharp={p_sharp:.4f}, p_final=None (skipped), "
            f"delta_total={delta:+.4f}, sep_raw={abs(p_raw - 0.5):.4f}, "
            f"sep_sharp={abs(p_sharp - 0.5):.4f}, "
            f"sep_improvement={abs(p_sharp - 0.5) - abs(p_raw - 0.5):+.4f}"
        )
        return None

    delta_total = p_final - p_raw
    sep_raw = abs(p_raw - 0.5)
    sep_final = abs(p_final - 0.5)
    sep_improvement = sep_final - sep_raw

    logger.info(
        f"calibrate_result: p_raw={p_raw:.4f}, p_anchored={p_anchored:.4f}, "
        f"p_sharp={p_sharp:.4f}, p_final={p_final:.4f}, "
        f"delta_total={delta_total:+.4f}, sep_raw={sep_raw:.4f}, "
        f"sep_final={sep_final:.4f}, sep_improvement={sep_improvement:+.4f}"
    )

    return float(p_final)


def batch_calibrate(
    predictions: list,
    category: str = "default",
    alpha: float = DEFAULT_ALPHA,
    anchor_weight: float = DEFAULT_ANCHOR_WEIGHT,
    floor: float = CONFIDENCE_FLOOR,
) -> list:
    """
    Calibrate a list of (id, p_raw) tuples.
    Returns list of (id, p_calibrated) where p_calibrated may be None.
    """
    results = []
    skipped = 0
    for item in predictions:
        if isinstance(item, (tuple, list)) and len(item) == 2:
            pred_id, p_raw = item
        else:
            pred_id, p_raw = None, item

        p_cal = calibrate(
            p_raw,
            category=category,
            alpha=alpha,
            anchor_weight=anchor_weight,
            floor=floor,
        )
        if p_cal is None:
            skipped += 1
        results.append((pred_id, p_cal))

    total = len(predictions)
    placed = total - skipped
    logger.info(
        f"batch_calibrate: total={total}, placed={placed}, skipped={skipped}, "
        f"deploy_rate={placed/total:.2f}" if total > 0 else "batch_calibrate: empty input"
    )
    return results