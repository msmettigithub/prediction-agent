import math
import logging
from typing import Optional

logger = logging.getLogger(__name__)

SHARPEN_FACTOR = 1.6

BASE_RATE_BLEND_WEIGHT = 0.3

def sharpen_probability(p: float, sharpen_factor: float = SHARPEN_FACTOR) -> float:
    p = max(1e-6, min(1 - 1e-6, p))
    logit = math.log(p / (1.0 - p))
    scaled_logit = logit * sharpen_factor
    p_sharp = 1.0 / (1.0 + math.exp(-scaled_logit))
    p_sharp = max(0.05, min(0.95, p_sharp))
    return p_sharp


def blend_with_base_rate(p: float, base_rate: float, base_rate_weight: float = BASE_RATE_BLEND_WEIGHT) -> float:
    blended = (1.0 - base_rate_weight) * p + base_rate_weight * base_rate
    return max(0.05, min(0.95, blended))


def calibrate(p: float, category: Optional[str] = None, base_rates: Optional[dict] = None, sharpen_factor: float = SHARPEN_FACTOR) -> float:
    p = max(1e-9, min(1 - 1e-9, p))

    pre_sharpen = p
    logger.debug(f"calibrate: pre_sharpen={pre_sharpen:.6f}, category={category}")

    p_sharp = sharpen_probability(p, sharpen_factor=sharpen_factor)

    if base_rates is not None and category is not None and category in base_rates:
        base_rate = base_rates[category]
        base_rate = max(0.05, min(0.95, float(base_rate)))
        p_final = blend_with_base_rate(p_sharp, base_rate)
        logger.debug(
            f"calibrate: post_sharpen={p_sharp:.6f}, base_rate={base_rate:.6f}, "
            f"blended={p_final:.6f}, category={category}"
        )
    else:
        p_final = p_sharp
        logger.debug(
            f"calibrate: post_sharpen={p_sharp:.6f} (no base_rate blend), "
            f"category={category}"
        )

    logger.info(
        f"SHARPEN_LOG pre={pre_sharpen:.6f} post={p_final:.6f} "
        f"delta={p_final - pre_sharpen:+.6f} factor={sharpen_factor} category={category}"
    )

    return p_final


def calibrate_batch(probabilities: list, categories: Optional[list] = None, base_rates: Optional[dict] = None, sharpen_factor: float = SHARPEN_FACTOR) -> list:
    if categories is None:
        categories = [None] * len(probabilities)
    results = []
    for p, cat in zip(probabilities, categories):
        results.append(calibrate(p, category=cat, base_rates=base_rates, sharpen_factor=sharpen_factor))
    return results