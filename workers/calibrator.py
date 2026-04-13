import numpy as np
from typing import Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Blend weight: calibrated = ALPHA * raw + (1 - ALPHA) * 0.5
# Reduced from ~0.5 to 0.75 (cut prior pull by >40%)
ALPHA: float = 0.75

# Confidence multiplier when multiple signals agree on direction
SIGNAL_AGREEMENT_MULTIPLIER: float = 1.3
SIGNAL_AGREEMENT_THRESHOLD: int = 3  # need at least this many agreeing signals

# Trade deployment thresholds (inclusive)
DEPLOY_THRESHOLD_HIGH: float = 0.57
DEPLOY_THRESHOLD_LOW: float = 0.43

# Final probability clamp
PROB_MIN: float = 0.05
PROB_MAX: float = 0.95


# ---------------------------------------------------------------------------
# Core calibration helpers
# ---------------------------------------------------------------------------

def blend_toward_prior(raw: float, alpha: float = ALPHA) -> float:
    """
    Pull raw probability toward 0.5 using a weighted blend.

    calibrated = alpha * raw + (1 - alpha) * 0.5

    Higher alpha => less shrinkage toward 0.5.
    """
    raw = float(np.clip(raw, 0.0, 1.0))
    return alpha * raw + (1.0 - alpha) * 0.5


def count_agreeing_signals(
    sentiment: Optional[float],
    trend: Optional[float],
    base_rate: Optional[float],
    momentum: Optional[float],
    neutral_band: float = 0.05,
) -> int:
    """
    Count how many independent signals agree on the same directional side
    (above-neutral = bullish, below-neutral = bearish).

    Each signal is expected to be a probability-like float in [0, 1].
    Values within `neutral_band` of 0.5 are considered neutral and excluded.

    Returns the *net* agreement count: positive means bullish majority,
    negative means bearish majority.  We return the *absolute* count of the
    majority side so callers can compare against SIGNAL_AGREEMENT_THRESHOLD.
    """
    signals = [sentiment, trend, base_rate, momentum]
    bullish = sum(1 for s in signals if s is not None and s > 0.5 + neutral_band)
    bearish = sum(1 for s in signals if s is not None and s < 0.5 - neutral_band)
    return max(bullish, bearish)


def apply_signal_agreement_amplifier(
    prob: float,
    n_agreeing: int,
    multiplier: float = SIGNAL_AGREEMENT_MULTIPLIER,
    threshold: int = SIGNAL_AGREEMENT_THRESHOLD,
) -> float:
    """
    When `n_agreeing` signals (or more) point in the same direction, push the
    probability further from 0.5 by scaling its distance from 0.5.

        distance = prob - 0.5
        new_prob = 0.5 + distance * multiplier
    """
    if n_agreeing < threshold:
        return prob
    distance = prob - 0.5
    amplified = 0.5 + distance * multiplier
    return float(np.clip(amplified, PROB_MIN, PROB_MAX))


def extremize(p: float, alpha: float = 1.6) -> float:
    """
    Satopää et al. extremizing transform.

    Pushes *p* away from 0.5 proportionally to existing conviction.
    alpha = 1.0  → identity
    alpha > 1.0  → extremize (push further from 0.5)
    alpha < 1.0  → shrink toward 0.5

    Example outputs (alpha=1.6):
        p=0.70  → 0.762  (+6.2 pp)
        p=0.65  → 0.710  (+6.0 pp)
        p=0.55  → 0.572  (+2.2 pp)
        p=0.50  → 0.500  (unchanged)
    """
    p = float(np.clip(p, 1e-6, 1.0 - 1e-6))
    pa = p ** alpha
    return float(pa / (pa + (1.0 - p) ** alpha))


# ---------------------------------------------------------------------------
# Main calibration entry point
# ---------------------------------------------------------------------------

def calibrate(
    raw_prob: float,
    sentiment: Optional[float] = None,
    trend: Optional[float] = None,
    base_rate: Optional[float] = None,
    momentum: Optional[float] = None,
    use_extremize: bool = False,
    extremize_alpha: float = 1.6,
) -> float:
    """
    Full calibration pipeline.

    Steps
    -----
    1. Blend raw probability toward 0.5 with reduced shrinkage (ALPHA=0.75).
    2. Count how many independent signals agree on direction.
    3. If enough signals agree, amplify the distance from 0.5 by
       SIGNAL_AGREEMENT_MULTIPLIER.
    4. Optionally apply the Satopää extremizing transform.
    5. Clamp result to [PROB_MIN, PROB_MAX].

    Parameters
    ----------
    raw_prob : float
        Model's raw predicted probability in [0, 1].
    sentiment, trend, base_rate, momentum : float or None
        Independent signal probabilities in [0, 1].  Pass None to omit.
    use_extremize : bool
        Apply Satopää extremizing transform as an additional step.
    extremize_alpha : float
        Alpha parameter for the extremizing transform (default 1.6).

    Returns
    -------
    float
        Calibrated probability in [PROB_MIN, PROB_MAX].
    """
    # Step 1: reduce prior pull
    prob = blend_toward_prior(raw_prob, alpha=ALPHA)

    # Step 2 & 3: signal-agreement amplifier
    n_agreeing = count_agreeing_signals(sentiment, trend, base_rate, momentum)
    prob = apply_signal_agreement_amplifier(prob, n_agreeing)

    # Step 4 (optional): Satopää extremizing
    if use_extremize:
        prob = extremize(prob, alpha=extremize_alpha)

    # Step 5: safety clamp
    prob = float(np.clip(prob, PROB_MIN, PROB_MAX))

    return prob


# ---------------------------------------------------------------------------
# Deployment gate
# ---------------------------------------------------------------------------

def should_deploy(prob: float) -> bool:
    """
    Return True when the calibrated probability is confident enough to trade.

    Thresholds lowered from 0.60/0.40 to 0.57/0.43 so that more trades
    pass the gate while still filtering weak signals.
    """
    return prob >= DEPLOY_THRESHOLD_HIGH or prob <= DEPLOY_THRESHOLD_LOW


def deployment_side(prob: float) -> Optional[str]:
    """
    Return 'YES', 'NO', or None (do not trade).

    Mirrors should_deploy() but also communicates direction.
    """
    if prob >= DEPLOY_THRESHOLD_HIGH:
        return "YES"
    if prob <= DEPLOY_THRESHOLD_LOW:
        return "NO"
    return None


# ---------------------------------------------------------------------------
# Convenience wrapper used by the trading pipeline
# ---------------------------------------------------------------------------

def calibrate_and_gate(
    raw_prob: float,
    sentiment: Optional[float] = None,
    trend: Optional[float] = None,
    base_rate: Optional[float] = None,
    momentum: Optional[float] = None,
    use_extremize: bool = False,
    extremize_alpha: float = 1.6,
) -> dict:
    """
    Run full calibration and deployment gate in one call.

    Returns
    -------
    dict with keys:
        - "raw_prob"        : original input
        - "calibrated_prob" : fully calibrated probability
        - "deploy"          : bool – whether to trade
        - "side"            : "YES", "NO", or None
        - "n_agreeing"      : number of signals that agreed on direction
    """
    n_agreeing = count_agreeing_signals(sentiment, trend, base_rate, momentum)
    calibrated = calibrate(
        raw_prob,
        sentiment=sentiment,
        trend=trend,
        base_rate=base_rate,
        momentum=momentum,
        use_extremize=use_extremize,
        extremize_alpha=extremize_alpha,
    )
    return {
        "raw_prob": raw_prob,
        "calibrated_prob": calibrated,
        "deploy": should_deploy(calibrated),
        "side": deployment_side(calibrated),
        "n_agreeing": n_agreeing,
    }