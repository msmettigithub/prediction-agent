import logging
import math
from typing import Optional

logger = logging.getLogger(__name__)


def extremize(p: float, a: float = 1.5) -> float:
    pa = p ** a
    one_minus_pa = (1.0 - p) ** a
    denom = pa + one_minus_pa
    if denom == 0.0:
        return p
    return pa / denom


def base_rate_anchor(p_model: float, base_rate: float, base_rate_weight: float = 0.30) -> float:
    return (1.0 - base_rate_weight) * p_model + base_rate_weight * base_rate


def calibrate(
    p_raw: float,
    category: Optional[str] = None,
    base_rates: Optional[dict] = None,
    exponent: float = 1.5,
    confidence_threshold: float = 0.08,
    clamp_low: float = 0.05,
    clamp_high: float = 0.95,
) -> float:
    logger.info(f"CALIBRATE_START: p_raw={p_raw:.4f}, category={category}")

    p = float(p_raw)
    p = max(0.0, min(1.0, p))

    # Step 1: base_rate_anchor
    if base_rates is not None and category is not None:
        base_rate = base_rates.get(category)
        if base_rate is not None:
            p_before_anchor = p
            p = base_rate_anchor(p, base_rate, base_rate_weight=0.30)
            logger.info(
                f"CALIBRATE_BASE_RATE_ANCHOR: category={category}, "
                f"base_rate={base_rate:.4f}, "
                f"p_before={p_before_anchor:.4f}, p_after={p:.4f}"
            )
        else:
            logger.info(f"CALIBRATE_BASE_RATE_ANCHOR: no base rate found for category={category}, skipping")
    else:
        logger.info("CALIBRATE_BASE_RATE_ANCHOR: skipped (no base_rates or category provided)")

    p_after_anchor = p

    # Step 2: confidence_gate then extremize
    signal_strength = abs(p - 0.5)
    if signal_strength > confidence_threshold:
        p_before_extremize = p
        p = extremize(p, a=exponent)
        logger.info(
            f"CALIBRATE_EXTREMIZE: signal_strength={signal_strength:.4f} > threshold={confidence_threshold}, "
            f"exponent={exponent}, p_before={p_before_extremize:.4f}, p_after={p:.4f}"
        )
    else:
        logger.info(
            f"CALIBRATE_EXTREMIZE: skipped (signal_strength={signal_strength:.4f} <= threshold={confidence_threshold})"
        )

    p_after_extremize = p

    # Step 3: clamp
    p_clamped = max(clamp_low, min(clamp_high, p))
    if p_clamped != p_after_extremize:
        logger.info(
            f"CALIBRATE_CLAMP: p_before={p_after_extremize:.4f}, "
            f"p_after={p_clamped:.4f}, bounds=[{clamp_low}, {clamp_high}]"
        )

    p = p_clamped

    logger.info(
        f"CALIBRATE_END: p_raw={p_raw:.4f}, p_after_anchor={p_after_anchor:.4f}, "
        f"p_after_extremize={p_after_extremize:.4f}, p_final={p:.4f} "
        f"(delta={p - p_raw:+.4f})"
    )

    return p