import math
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Sharpening config — exposed here so RL iterations can tune directly
SHARPEN_CONFIG = {
    "alpha_default": 1.4,
    "alpha_agree": 1.6,
    "alpha_disagree": 1.1,
    "agree_threshold": 2,      # number of agreeing signals required for high alpha
    "prob_floor": 0.05,
    "prob_ceil": 0.95,
}


def _logit(p: float) -> float:
    """Convert probability to logit (log-odds) space."""
    p = max(1e-9, min(1 - 1e-9, p))
    return math.log(p / (1.0 - p))


def _sigmoid(x: float) -> float:
    """Convert log-odds back to probability via sigmoid."""
    return 1.0 / (1.0 + math.exp(-x))


def sharpen(
    p: float,
    alpha: float = SHARPEN_CONFIG["alpha_default"],
    label: str = "",
) -> float:
    """
    Logit-space sharpening:
        p_sharp = sigmoid(alpha * logit(p))

    alpha > 1.0 pushes probabilities away from 0.5 (sharpens).
    alpha < 1.0 shrinks toward 0.5 (softens).
    """
    logit_p = _logit(p)
    logit_sharp = alpha * logit_p
    p_sharp = _sigmoid(logit_sharp)

    logger.info(
        "sharpen | label=%s alpha=%.3f p_pre=%.5f p_post=%.5f delta=%+.5f",
        label or "?",
        alpha,
        p,
        p_sharp,
        p_sharp - p,
    )
    return p_sharp


def _resolve_alpha(
    trend_signal: Optional[float],
    sentiment_signal: Optional[float],
    base_rate_signal: Optional[float],
    p_base: float,
) -> float:
    """
    Confidence-weighted alpha selection.

    Counts how many independent signals agree with the direction implied by
    p_base (> 0.5 → bullish, < 0.5 → bearish).  When >= agree_threshold
    signals concur, use alpha_agree; when they actively conflict, use
    alpha_disagree; otherwise fall back to alpha_default.
    """
    bullish = p_base >= 0.5

    agree = 0
    disagree = 0

    for sig in (trend_signal, sentiment_signal, base_rate_signal):
        if sig is None:
            continue
        # Each signal is a probability in [0,1]; > 0.5 means bullish lean
        if (sig >= 0.5) == bullish:
            agree += 1
        else:
            disagree += 1

    cfg = SHARPEN_CONFIG
    if agree >= cfg["agree_threshold"] and disagree == 0:
        alpha = cfg["alpha_agree"]
        reason = "full-agreement"
    elif disagree >= 2:
        alpha = cfg["alpha_disagree"]
        reason = "strong-disagreement"
    elif disagree >= 1:
        alpha = cfg["alpha_default"]
        reason = "partial-disagreement"
    else:
        alpha = cfg["alpha_default"]
        reason = "default"

    logger.info(
        "resolve_alpha | agree=%d disagree=%d alpha=%.3f reason=%s",
        agree,
        disagree,
        alpha,
        reason,
    )
    return alpha


def calibrate(
    raw_p: float,
    trend_signal: Optional[float] = None,
    sentiment_signal: Optional[float] = None,
    base_rate_signal: Optional[float] = None,
    label: str = "",
) -> float:
    """
    Full calibration pipeline:

        raw_p
          → base_calibration  (existing shrinkage / isotonic / Platt logic)
          → logit sharpening  (alpha chosen by signal agreement)
          → clamp to [floor, ceil]
          → return

    Parameters
    ----------
    raw_p : float
        Probability emitted by the upstream model before calibration.
    trend_signal : float | None
        Probability-like score from the trend detector (0–1).
    sentiment_signal : float | None
        Probability-like score from the sentiment module (0–1).
    base_rate_signal : float | None
        Historical base-rate probability for this setup class (0–1).
    label : str
        Human-readable tag for log lines (e.g. ticker + timestamp).

    Returns
    -------
    float
        Calibrated, sharpened, clamped probability in [floor, ceil].
    """
    cfg = SHARPEN_CONFIG

    # ------------------------------------------------------------------
    # Step 1 – base calibration (temperature / Platt / isotonic).
    # Replace the body of this block with the real implementation;
    # the identity pass-through keeps existing behaviour if nothing else
    # has been wired in yet.
    # ------------------------------------------------------------------
    p_base = _base_calibrate(raw_p)
    logger.info(
        "calibrate | label=%s raw_p=%.5f p_base=%.5f",
        label or "?",
        raw_p,
        p_base,
    )

    # ------------------------------------------------------------------
    # Step 2 – choose alpha from signal agreement
    # ------------------------------------------------------------------
    alpha = _resolve_alpha(
        trend_signal,
        sentiment_signal,
        base_rate_signal,
        p_base,
    )

    # ------------------------------------------------------------------
    # Step 3 – logit-space sharpening
    # ------------------------------------------------------------------
    p_sharp = sharpen(p_base, alpha=alpha, label=label)

    # ------------------------------------------------------------------
    # Step 4 – clamp to avoid degenerate extremes
    # ------------------------------------------------------------------
    p_final = max(cfg["prob_floor"], min(cfg["prob_ceil"], p_sharp))

    if p_final != p_sharp:
        logger.info(
            "calibrate | label=%s clamped %.5f → %.5f",
            label or "?",
            p_sharp,
            p_final,
        )

    logger.info(
        "calibrate | label=%s FINAL p=%.5f  (raw=%.5f base=%.5f sharp=%.5f alpha=%.3f)",
        label or "?",
        p_final,
        raw_p,
        p_base,
        p_sharp,
        alpha,
    )

    return p_final


# ----------------------------------------------------------------------
# Base calibration stub
# ----------------------------------------------------------------------
# Replace this function with the real isotonic / Platt / temperature
# scaling implementation.  It lives here so the rest of the module can
# import cleanly even before the full model artefacts are wired in.
# ----------------------------------------------------------------------

def _base_calibrate(p: float) -> float:
    """
    Placeholder base calibration.

    Current behaviour: identity (no shrinkage), so the sharpening layer
    above will be the only transform applied.  Swap in your Platt / isotonic
    regressor here; the interface is simply float → float.
    """
    return float(p)