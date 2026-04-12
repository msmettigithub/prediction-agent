import math
from typing import Optional


def _logit(p: float) -> float:
    p = max(1e-9, min(1 - 1e-9, p))
    return math.log(p / (1.0 - p))


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def calibrate(
    raw_p: float,
    signal_agreement_count: Optional[int] = None,
) -> float:
    """
    Calibrate a raw probability toward a well-separated output.

    Parameters
    ----------
    raw_p : float
        Raw probability in (0, 1).
    signal_agreement_count : int or None
        Number of independent signals (base_rate, trend, sentiment, volume)
        that agree on the directional outcome.  When None or < 2 the original
        conservative path is used.

    Returns
    -------
    float
        Calibrated probability, always in [0.08, 0.92].
    """
    raw_p = _clamp(float(raw_p), 1e-6, 1.0 - 1e-6)

    # ------------------------------------------------------------------
    # Path A – confidence expansion via logit-space stretch
    # Requires at least 2 independent signals agreeing on direction.
    # ------------------------------------------------------------------
    if signal_agreement_count is not None and signal_agreement_count >= 2:
        logit_raw = _logit(raw_p)

        # expansion factor: 1 + 0.15 * clamp(signal_count - 1, 0, 3)
        extra_signals = _clamp(signal_agreement_count - 1, 0, 3)
        expansion = 1.0 + 0.15 * extra_signals

        logit_adjusted = logit_raw * expansion
        p_calibrated = _sigmoid(logit_adjusted)

        # Asymmetric clipping: avoid catastrophic Brier penalties on surprises
        return _clamp(p_calibrated, 0.08, 0.92)

    # ------------------------------------------------------------------
    # Path B – original conservative shrinkage (fallback)
    # Pulls probabilities modestly toward 0.5 to avoid over-confidence
    # when signal evidence is weak or absent.
    # ------------------------------------------------------------------
    shrinkage = 0.15  # blend 15 % toward 0.5
    p_conservative = raw_p * (1.0 - shrinkage) + 0.5 * shrinkage
    return _clamp(p_conservative, 0.08, 0.92)


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------

def count_signal_agreement(
    base_rate: Optional[float] = None,
    trend: Optional[float] = None,
    sentiment: Optional[float] = None,
    volume: Optional[float] = None,
    threshold: float = 0.5,
) -> int:
    """
    Count how many of the supplied signals agree that the outcome probability
    is above *threshold* (i.e. lean YES) or all agree it is below (lean NO).

    Each signal should be a probability in [0, 1] or None if unavailable.

    Returns the count of signals that agree on the *dominant* direction.
    """
    signals = [s for s in (base_rate, trend, sentiment, volume) if s is not None]
    if not signals:
        return 0

    yes_votes = sum(1 for s in signals if s >= threshold)
    no_votes = len(signals) - yes_votes

    # Agreement count = size of the majority bloc
    return max(yes_votes, no_votes)


def calibrate_with_signals(
    raw_p: float,
    base_rate: Optional[float] = None,
    trend: Optional[float] = None,
    sentiment: Optional[float] = None,
    volume: Optional[float] = None,
    threshold: float = 0.5,
) -> float:
    """
    Full pipeline: count signal agreement then calibrate.

    Parameters mirror calibrate() and count_signal_agreement().
    """
    count = count_signal_agreement(
        base_rate=base_rate,
        trend=trend,
        sentiment=sentiment,
        volume=volume,
        threshold=threshold,
    )
    return calibrate(raw_p, signal_agreement_count=count)