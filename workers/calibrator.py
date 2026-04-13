workers/calibrator.py
import math
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Default configuration
DEFAULT_CONFIG = {
    "sharpening_k": 1.5,
    "confidence_floor": 0.03,
    "base_rate_model_weight": 0.70,
    "base_rate_category_weight": 0.30,
}

# Category-specific base rates for anchoring
CATEGORY_BASE_RATES = {
    "incumbent": 0.65,
    "status_quo": 0.65,
    "reelection": 0.62,
    "policy_continuation": 0.63,
    "regulatory_approval": 0.60,
    "legislative_passage": 0.40,
    "challenger": 0.35,
    "disruption": 0.38,
    "default": 0.55,
}


def get_base_rate(category: Optional[str]) -> float:
    if category is None:
        return CATEGORY_BASE_RATES["default"]
    normalized = category.lower().strip()
    for key in CATEGORY_BASE_RATES:
        if key in normalized:
            return CATEGORY_BASE_RATES[key]
    return CATEGORY_BASE_RATES["default"]


def anchor_to_base_rate(
    model_prob: float,
    category: Optional[str] = None,
    model_weight: float = DEFAULT_CONFIG["base_rate_model_weight"],
    base_rate_weight: float = DEFAULT_CONFIG["base_rate_category_weight"],
) -> float:
    base_rate = get_base_rate(category)
    anchored = model_weight * model_prob + base_rate_weight * base_rate
    anchored = max(1e-6, min(1 - 1e-6, anchored))
    logger.debug(
        f"Base rate anchor: model_prob={model_prob:.4f}, base_rate={base_rate:.4f}, "
        f"anchored={anchored:.4f}, category={category}"
    )
    return anchored


def sharpen_probability(p: float, k: float = DEFAULT_CONFIG["sharpening_k"]) -> float:
    p = max(1e-6, min(1 - 1e-6, p))
    odds_ratio = (1.0 - p) / p
    sharpened = 1.0 / (1.0 + (odds_ratio ** k))
    sharpened = max(1e-6, min(1 - 1e-6, sharpened))
    logger.debug(f"Sharpening: p_in={p:.4f}, k={k:.2f}, p_out={sharpened:.4f}")
    return sharpened


def check_confidence_floor(
    raw_model_prob: float,
    floor: float = DEFAULT_CONFIG["confidence_floor"],
) -> bool:
    signal_strength = abs(raw_model_prob - 0.5)
    passes = signal_strength >= floor
    logger.debug(
        f"Confidence floor check: raw_prob={raw_model_prob:.4f}, "
        f"signal_strength={signal_strength:.4f}, floor={floor:.4f}, passes={passes}"
    )
    return passes


def calibrate(
    raw_model_prob: float,
    category: Optional[str] = None,
    config: Optional[dict] = None,
) -> Optional[float]:
    if config is None:
        config = DEFAULT_CONFIG

    sharpening_k = config.get("sharpening_k", DEFAULT_CONFIG["sharpening_k"])
    confidence_floor = config.get("confidence_floor", DEFAULT_CONFIG["confidence_floor"])
    model_weight = config.get("base_rate_model_weight", DEFAULT_CONFIG["base_rate_model_weight"])
    base_rate_weight = config.get("base_rate_category_weight", DEFAULT_CONFIG["base_rate_category_weight"])

    raw_model_prob = max(1e-6, min(1 - 1e-6, float(raw_model_prob)))

    logger.debug(f"Calibration pipeline start: raw_prob={raw_model_prob:.4f}, category={category}")

    # Step 1: Base rate anchoring
    anchored_prob = anchor_to_base_rate(
        model_prob=raw_model_prob,
        category=category,
        model_weight=model_weight,
        base_rate_weight=base_rate_weight,
    )

    # Step 2: Sigmoid sharpening
    sharpened_prob = sharpen_probability(p=anchored_prob, k=sharpening_k)

    # Step 3: Confidence floor check (based on raw model signal strength)
    if not check_confidence_floor(raw_model_prob=raw_model_prob, floor=confidence_floor):
        logger.info(
            f"Signal below confidence floor ({abs(raw_model_prob - 0.5):.4f} < {confidence_floor}). "
            f"Returning None (no-trade signal)."
        )
        return None

    logger.info(
        f"Calibration complete: raw={raw_model_prob:.4f} -> anchored={anchored_prob:.4f} "
        f"-> sharpened={sharpened_prob:.4f} (category={category})"
    )

    return sharpened_prob


def batch_calibrate(
    probabilities: list,
    categories: Optional[list] = None,
    config: Optional[dict] = None,
) -> list:
    if categories is None:
        categories = [None] * len(probabilities)

    if len(categories) != len(probabilities):
        raise ValueError(
            f"Length mismatch: probabilities={len(probabilities)}, categories={len(categories)}"
        )

    results = []
    for prob, cat in zip(probabilities, categories):
        calibrated = calibrate(raw_model_prob=prob, category=cat, config=config)
        results.append(calibrated)

    traded = [r for r in results if r is not None]
    skipped = len(results) - len(traded)
    logger.info(
        f"Batch calibration: {len(probabilities)} inputs, {len(traded)} tradeable, "
        f"{skipped} skipped (below confidence floor)"
    )

    return results


def compute_separation(calibrated_probs: list) -> float:
    tradeable = [p for p in calibrated_probs if p is not None]
    if not tradeable:
        return 0.0
    yes_trades = [p for p in tradeable if p > 0.5]
    no_trades = [p for p in tradeable if p <= 0.5]
    if not yes_trades or not no_trades:
        return 0.0
    mean_yes = sum(yes_trades) / len(yes_trades)
    mean_no = sum(no_trades) / len(no_trades)
    separation = mean_yes - mean_no
    logger.info(
        f"Separation: {separation:.4f} (mean_yes={mean_yes:.4f}, mean_no={mean_no:.4f}, "
        f"n_yes={len(yes_trades)}, n_no={len(no_trades)})"
    )
    return separation