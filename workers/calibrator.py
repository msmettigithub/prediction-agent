import math
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Tunable via RL iterations
EXTREMIZE_FACTOR = 1.6

# Base rates by category (historical resolution rates for YES)
BASE_RATES = {
    "binary_by_date": 0.35,
    "election": 0.50,
    "sports": 0.50,
    "economic_indicator": 0.45,
    "default": 0.40,
}

# Blend weights for base-rate anchoring
BASE_RATE_WEIGHT = 0.30
MODEL_WEIGHT = 0.70

# Safety clamp bounds
CLAMP_LOW = 0.05
CLAMP_HIGH = 0.95


def detect_category(contract: Optional[dict]) -> str:
    """
    Detect contract category from metadata for base-rate anchoring.
    Returns a key into BASE_RATES.
    """
    if not contract:
        return "default"

    title = (contract.get("title") or "").lower()
    question = (contract.get("question") or "").lower()
    category = (contract.get("category") or "").lower()
    text = title + " " + question + " " + category

    if any(kw in text for kw in ["election", "president", "senator", "governor", "vote", "poll"]):
        return "election"
    if any(kw in text for kw in ["win", "championship", "match", "game", "score", "nfl", "nba", "mlb", "nhl", "soccer"]):
        return "sports"
    if any(kw in text for kw in ["gdp", "inflation", "unemployment", "rate", "fed", "cpi", "interest"]):
        return "economic_indicator"
    if any(kw in text for kw in ["will", "by", "before", "happen", "occur", "complete", "launch", "release", "announce"]):
        return "binary_by_date"

    return "default"


def anchor_to_base_rate(p: float, base_rate: float) -> float:
    """
    Blend model probability with historical base rate.
    p_anchored = MODEL_WEIGHT * p + BASE_RATE_WEIGHT * base_rate
    """
    anchored = MODEL_WEIGHT * p + BASE_RATE_WEIGHT * base_rate
    return anchored


def extremize(p: float, k: float = EXTREMIZE_FACTOR) -> float:
    """
    Apply log-odds extremizing transform.

    Steps:
      1. Convert p to log-odds: lo = log(p / (1-p))
      2. Scale by factor k:     lo_ext = k * lo
      3. Convert back:          p_ext = 1 / (1 + exp(-lo_ext))
      4. Clamp to [CLAMP_LOW, CLAMP_HIGH]

    With k=1.6:
      p=0.58 -> ~0.64
      p=0.42 -> ~0.36
    """
    # Guard against degenerate inputs
    p = float(p)
    p = max(1e-9, min(1.0 - 1e-9, p))

    lo = math.log(p / (1.0 - p))
    lo_ext = k * lo

    # Guard against overflow in exp
    if lo_ext > 700:
        p_ext = 1.0
    elif lo_ext < -700:
        p_ext = 0.0
    else:
        p_ext = 1.0 / (1.0 + math.exp(-lo_ext))

    if not math.isfinite(p_ext):
        logger.warning("extremize produced non-finite value for p=%.6f k=%.3f, returning original", p, k)
        return max(CLAMP_LOW, min(CLAMP_HIGH, p))

    p_clamped = max(CLAMP_LOW, min(CLAMP_HIGH, p_ext))
    return p_clamped


def calibrate(
    raw_prob: float,
    contract: Optional[dict] = None,
    existing_calibrated: Optional[float] = None,
    extremize_factor: float = EXTREMIZE_FACTOR,
) -> dict:
    """
    Full calibration pipeline:

    1. Start from existing_calibrated if available, else raw_prob.
    2. Detect contract category and look up historical base rate.
    3. Anchor to base rate (70% model, 30% base rate).
    4. Apply log-odds extremizing with factor k.
    5. Clamp to [CLAMP_LOW, CLAMP_HIGH].

    Returns a dict with full audit trail for monitoring.
    """
    # Step 0: Choose starting probability
    start_prob = existing_calibrated if existing_calibrated is not None else raw_prob
    start_prob = float(start_prob)

    if not math.isfinite(start_prob):
        logger.error("calibrate received non-finite start_prob=%.6f, falling back to 0.5", start_prob)
        start_prob = 0.5

    start_prob = max(1e-9, min(1.0 - 1e-9, start_prob))

    # Step 1: Detect category
    category = detect_category(contract)
    base_rate = BASE_RATES.get(category, BASE_RATES["default"])

    # Step 2: Base-rate anchoring (before extremizing)
    anchored_prob = anchor_to_base_rate(start_prob, base_rate)

    # Step 3: Extremizing
    extremized_prob = extremize(anchored_prob, k=extremize_factor)

    # Step 4: Logging
    logger.info(
        "calibrate | raw=%.4f | existing_cal=%.4f | category=%s | base_rate=%.3f | "
        "anchored=%.4f | extremized=%.4f | k=%.3f",
        raw_prob,
        existing_calibrated if existing_calibrated is not None else float("nan"),
        category,
        base_rate,
        anchored_prob,
        extremized_prob,
        extremize_factor,
    )

    return {
        "raw_prob": raw_prob,
        "existing_calibrated": existing_calibrated,
        "category": category,
        "base_rate": base_rate,
        "anchored_prob": anchored_prob,
        "extremized_prob": extremized_prob,
        "final_prob": extremized_prob,
        "extremize_factor": extremize_factor,
    }


def calibrate_simple(
    raw_prob: float,
    contract: Optional[dict] = None,
    existing_calibrated: Optional[float] = None,
    extremize_factor: float = EXTREMIZE_FACTOR,
) -> float:
    """
    Convenience wrapper returning only the final calibrated probability.
    """
    result = calibrate(
        raw_prob=raw_prob,
        contract=contract,
        existing_calibrated=existing_calibrated,
        extremize_factor=extremize_factor,
    )
    return result["final_prob"]