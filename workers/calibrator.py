workers/calibrator.py
import logging
import numpy as np
from typing import Union, List, Optional

logger = logging.getLogger(__name__)


def sharpen(
    p: float,
    n_agreeing_signals: int,
    clip_low: float = 0.05,
    clip_high: float = 0.95,
) -> float:
    """
    Apply symmetric power-law sharpening to a raw probability.

    The sharpening exponent k is computed as:
        k = 1.0 + 0.3 * (n_agreeing_signals - 1), capped at 2.5

    The transform is:
        p_out = p^k / (p^k + (1-p)^k)

    This is monotonic and symmetric around 0.5, so it:
      - Preserves calibration rank-ordering
      - Never flips a prediction (p > 0.5 stays > 0.5)
      - Only amplifies existing confidence proportionally to signal agreement

    Args:
        p: Raw probability in (0, 1).
        n_agreeing_signals: Number of independent signals agreeing on direction
            (i.e., all above 0.5 or all below 0.5).
        clip_low: Lower safety clip bound. Defaults to 0.05.
        clip_high: Upper safety clip bound. Defaults to 0.95.

    Returns:
        Sharpened probability clipped to [clip_low, clip_high].

    Examples:
        With k=1.6 (3 agreeing signals):
            p=0.60 -> ~0.64
            p=0.70 -> ~0.77
            p=0.55 -> ~0.57
            p=0.50 ->  0.50  (unchanged by symmetry)
    """
    p_in = float(p)

    # Compute sharpening exponent, capped at 2.5
    k = 1.0 + 0.3 * (n_agreeing_signals - 1)
    k = min(k, 2.5)

    # Guard against degenerate inputs before raising to a power
    p_clipped = float(np.clip(p_in, 1e-9, 1.0 - 1e-9))

    pk = p_clipped ** k
    one_minus_pk = (1.0 - p_clipped) ** k
    denom = pk + one_minus_pk

    if denom == 0.0:
        # Should never happen given the clip above, but be safe
        p_sharp = p_clipped
    else:
        p_sharp = pk / denom

    # Safety clip
    p_out = float(np.clip(p_sharp, clip_low, clip_high))

    logger.debug(
        "sharpen: p_in=%.4f n_agreeing=%d k=%.2f p_sharp=%.4f p_out=%.4f",
        p_in,
        n_agreeing_signals,
        k,
        p_sharp,
        p_out,
    )

    return p_out


def count_agreeing_signals(signals: List[Optional[float]]) -> int:
    """
    Count the number of signals that agree on direction (all above or all below 0.5).

    Signals that are exactly 0.5 or None are treated as neutral and excluded
    from the agreement count.

    Args:
        signals: List of probability-like signal values in [0, 1] or None.

    Returns:
        Number of non-neutral signals that share the majority direction.
        Returns 1 if there is no agreement (or no valid signals), so that
        the sharpening exponent defaults to k=1.0 (identity transform).
    """
    valid = [s for s in signals if s is not None and s != 0.5]

    if not valid:
        return 1

    n_above = sum(1 for s in valid if s > 0.5)
    n_below = sum(1 for s in valid if s < 0.5)

    # Majority direction count
    agreeing = max(n_above, n_below)

    # If perfectly split, no agreement — default to 1
    if n_above == n_below:
        return 1

    return agreeing


def calibrate(
    raw_prob: float,
    signals: Optional[List[Optional[float]]] = None,
) -> float:
    """
    Calibrate a raw probability by applying signal-agreement-based sharpening.

    Steps:
        1. Count the number of independent signals agreeing on direction.
        2. Compute sharpening exponent k = 1.0 + 0.3 * (n_agreeing - 1), cap at 2.5.
        3. Apply symmetric sharpening: p_out = p^k / (p^k + (1-p)^k).
        4. Clip final output to [0.05, 0.95].
        5. Log pre/post sharpening values for monitoring.

    Args:
        raw_prob: Raw probability estimate in [0, 1].
        signals: Optional list of signal values (base rate, trend, sentiment,
            momentum, etc.) each in [0, 1] or None for missing signals.
            If None or empty, defaults to no sharpening (k=1.0).

    Returns:
        Calibrated and sharpened probability in [0.05, 0.95].
    """
    if signals is None:
        signals = []

    n_agreeing = count_agreeing_signals(signals)

    p_out = sharpen(raw_prob, n_agreeing_signals=n_agreeing)

    logger.info(
        "CALIBRATE_SHARPENING: raw=%.4f n_signals=%d n_agreeing=%d sharpened=%.4f",
        raw_prob,
        len([s for s in signals if s is not None]),
        n_agreeing,
        p_out,
    )

    return p_out