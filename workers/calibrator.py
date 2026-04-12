import logging
import math
from typing import Optional

logger = logging.getLogger(__name__)

SHARPEN_ENABLED = True

def _get_adaptive_alpha(backtest_accuracy: Optional[float]) -> float:
    if backtest_accuracy is None:
        return 1.8
    if backtest_accuracy > 0.85:
        return 2.2
    elif backtest_accuracy > 0.75:
        return 1.8
    else:
        return 1.3

def sharpen_probability(p: float, alpha: float) -> float:
    p = max(1e-9, min(1 - 1e-9, p))
    p_alpha = p ** alpha
    one_minus_alpha = (1.0 - p) ** alpha
    denom = p_alpha + one_minus_alpha
    if denom == 0:
        return p
    return p_alpha / denom

def apply_sharpening(p: float, backtest_accuracy: Optional[float] = None) -> float:
    if not SHARPEN_ENABLED:
        return p

    alpha = _get_adaptive_alpha(backtest_accuracy)
    p_pre = p

    p_sharp = sharpen_probability(p, alpha)
    p_clamped = max(0.05, min(0.95, p_sharp))

    delta = p_clamped - p_pre
    logger.info(
        f"[Sharpening] pre={p_pre:.4f} post={p_clamped:.4f} delta={delta:+.4f} "
        f"alpha={alpha} backtest_acc={backtest_accuracy}"
    )

    return p_clamped

def calibrate(
    raw_probability: float,
    backtest_accuracy: Optional[float] = None,
    **kwargs
) -> float:
    p = raw_probability
    p = max(0.0, min(1.0, p))

    logger.debug(f"[Calibrator] raw input probability: {p:.4f}")

    p = apply_sharpening(p, backtest_accuracy=backtest_accuracy)

    logger.debug(f"[Calibrator] final calibrated probability: {p:.4f}")
    return p

def calibrate_batch(
    probabilities: list,
    backtest_accuracy: Optional[float] = None,
    **kwargs
) -> list:
    return [calibrate(p, backtest_accuracy=backtest_accuracy, **kwargs) for p in probabilities]