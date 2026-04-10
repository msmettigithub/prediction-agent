import math
import logging

logger = logging.getLogger(__name__)


def logit(p):
    p = max(1e-9, min(1 - 1e-9, p))
    return math.log(p / (1 - p))


def sigmoid(x):
    return 1.0 / (1.0 + math.exp(-x))


def sigmoid_stretch(p, k=1.8):
    raw_logit = logit(p)
    stretched_logit = k * raw_logit
    return sigmoid(stretched_logit)


def calibrate(raw_prob, signals=None):
    """
    Calibrate a raw probability using sigmoid stretch in logit space and
    a multi-signal agreement bonus.

    Parameters
    ----------
    raw_prob : float
        The raw probability output from the model, expected in (0, 1).
    signals : list of float, optional
        Independent signal probabilities from sources such as yfinance trend,
        vaderSentiment polarity, pytrends momentum, statsmodels forecast, and
        base rate prior.  Each value should be in (0, 1).  When 3 or more
        signals agree on direction (all above 0.5 or all below 0.5) a 3 pp
        bonus is applied in that direction.

    Returns
    -------
    float
        Calibrated probability clamped to [0.05, 0.95].
    """
    if signals is None:
        signals = []

    pre_calibration = raw_prob

    stretched = sigmoid_stretch(raw_prob, k=1.8)

    bullish_signals = [s for s in signals if s > 0.5]
    bearish_signals = [s for s in signals if s < 0.5]
    bullish_count = len(bullish_signals)
    bearish_count = len(bearish_signals)

    agreement_count = 0
    bonus = 0.0
    bonus_direction = "none"

    if bullish_count >= 3 and bullish_count > bearish_count:
        agreement_count = bullish_count
        bonus = 0.03
        bonus_direction = "bullish"
    elif bearish_count >= 3 and bearish_count > bullish_count:
        agreement_count = bearish_count
        bonus = -0.03
        bonus_direction = "bearish"

    calibrated = stretched + bonus

    calibrated = max(0.05, min(0.95, calibrated))

    logger.debug(
        "Calibration: pre=%.4f | sigmoid_stretch=%.4f | "
        "agreement_count=%d (%s) | bonus=%.3f | post=%.4f",
        pre_calibration,
        stretched,
        agreement_count,
        bonus_direction,
        bonus,
        calibrated,
    )

    return calibrated


def calibrate_batch(raw_probs, signals_list=None):
    """
    Calibrate a list of raw probabilities.

    Parameters
    ----------
    raw_probs : list of float
    signals_list : list of list of float, optional
        Parallel list of signal vectors.  If None, no signal bonus is applied.

    Returns
    -------
    list of float
    """
    if signals_list is None:
        signals_list = [[] for _ in raw_probs]

    results = []
    for prob, signals in zip(raw_probs, signals_list):
        results.append(calibrate(prob, signals))
    return results