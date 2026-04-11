import numpy as np
import logging

log = logging.getLogger(__name__)


def concordance_score(base_rate, sentiment, momentum, news_volume):
    """
    Count how many independent signal sources agree on direction.
    Direction is defined as >0.5 (bullish/yes) or <0.5 (bearish/no).
    Returns integer 0-4.
    """
    signals = []

    if base_rate is not None and base_rate != 0.5:
        signals.append(1 if base_rate > 0.5 else 0)

    if sentiment is not None and sentiment != 0.5:
        signals.append(1 if sentiment > 0.5 else 0)

    if momentum is not None and momentum != 0.5:
        signals.append(1 if momentum > 0.5 else 0)

    if news_volume is not None and news_volume != 0.5:
        signals.append(1 if news_volume > 0.5 else 0)

    if len(signals) == 0:
        return 0

    yes_count = sum(signals)
    no_count = len(signals) - yes_count
    agreement = max(yes_count, no_count)

    return agreement


def expansion_factor_from_concordance(concordance, data_points=None):
    """
    Return log-odds expansion factor based on concordance score.
    Optionally modulates by data_points decay parameter.
    """
    base_factors = {
        4: 1.5,
        3: 1.3,
        2: 1.0,
        1: 0.85,
        0: 0.85,
    }

    factor = base_factors.get(concordance, 0.85)

    if data_points is not None and data_points > 0:
        decay = 1.0 + 0.1 * np.log1p(data_points / 10.0)
        factor = factor * min(decay, 1.4)

    return factor


def log_odds_expand(p, expansion_factor):
    """
    Convert probability to log-odds, scale by expansion_factor, convert back.
    Clamps input to avoid numerical issues at 0 or 1.
    """
    p_safe = np.clip(p, 1e-6, 1.0 - 1e-6)
    log_odds = np.log(p_safe / (1.0 - p_safe))
    expanded_log_odds = log_odds * expansion_factor
    expanded_p = 1.0 / (1.0 + np.exp(-expanded_log_odds))
    return expanded_p


def calibrate(
    p_raw,
    base_rate=None,
    sentiment=None,
    momentum=None,
    news_volume=None,
    data_points=None,
    alpha=1.6,
):
    """
    Calibrate a raw probability estimate with concordance-weighted log-odds expansion.

    Steps:
    1. Guard input: ensure p_raw is a valid probability (apply sigmoid if logit-like).
    2. Apply baseline extremizing calibration (existing logic preserved).
    3. Compute concordance score from available signal sources.
    4. Apply log-odds expansion weighted by concordance.
    5. Clamp final output to [0.05, 0.95].

    Parameters
    ----------
    p_raw : float
        Raw model probability output.
    base_rate : float or None
        Historical base rate for this event type, in [0, 1].
    sentiment : float or None
        Sentiment signal mapped to [0, 1].
    momentum : float or None
        Trend/momentum signal mapped to [0, 1].
    news_volume : float or None
        News volume signal mapped to [0, 1].
    data_points : int or None
        Number of data points available; higher values allow stronger expansion.
    alpha : float
        Baseline extremizing exponent (existing calibration logic).

    Returns
    -------
    float
        Calibrated probability in [0.05, 0.95].
    """
    if p_raw is None:
        log.warning("calibrate() received None for p_raw, defaulting to 0.5")
        p_raw = 0.5

    p_raw = float(p_raw)

    if not (0.0 <= p_raw <= 1.0):
        log.debug(
            f"p_raw={p_raw:.4f} outside [0,1], applying sigmoid (treating as logit)"
        )
        p_raw = 1.0 / (1.0 + np.exp(-p_raw))

    p_clipped = np.clip(p_raw, 1e-6, 1.0 - 1e-6)

    p_calibrated = p_clipped**alpha / (
        p_clipped**alpha + (1.0 - p_clipped) ** alpha
    )

    log.debug(
        f"Baseline calibration: p_raw={p_raw:.4f} -> p_calibrated={p_calibrated:.4f} (alpha={alpha})"
    )

    concordance = concordance_score(base_rate, sentiment, momentum, news_volume)

    factor = expansion_factor_from_concordance(concordance, data_points=data_points)

    log.debug(
        f"Concordance={concordance}, expansion_factor={factor:.3f} "
        f"(base_rate={base_rate}, sentiment={sentiment}, momentum={momentum}, news_volume={news_volume})"
    )

    if factor != 1.0:
        p_expanded = log_odds_expand(p_calibrated, factor)
        log.debug(
            f"Log-odds expansion: p_calibrated={p_calibrated:.4f} -> p_expanded={p_expanded:.4f}"
        )
    else:
        p_expanded = p_calibrated
        log.debug("No expansion applied (concordance=2 or factor=1.0)")

    p_final = float(np.clip(p_expanded, 0.05, 0.95))

    log.info(
        f"calibrate() summary: p_raw={p_raw:.4f}, p_calibrated={p_calibrated:.4f}, "
        f"concordance={concordance}, factor={factor:.3f}, p_final={p_final:.4f}"
    )

    return p_final


def calibrate_batch(records, alpha=1.6):
    """
    Calibrate a batch of probability records.

    Parameters
    ----------
    records : list of dict
        Each dict should contain:
          - 'p_raw': float (required)
          - 'base_rate': float or None
          - 'sentiment': float or None
          - 'momentum': float or None
          - 'news_volume': float or None
          - 'data_points': int or None

    Returns
    -------
    list of float
        Calibrated probabilities in [0.05, 0.95].
    """
    results = []
    for i, record in enumerate(records):
        try:
            p = calibrate(
                p_raw=record.get("p_raw"),
                base_rate=record.get("base_rate"),
                sentiment=record.get("sentiment"),
                momentum=record.get("momentum"),
                news_volume=record.get("news_volume"),
                data_points=record.get("data_points"),
                alpha=alpha,
            )
        except Exception as e:
            log.error(f"calibrate_batch: error on record {i}: {e}, defaulting to 0.5")
            p = 0.5
        results.append(p)

    if results:
        arr = np.array(results)
        log.info(
            f"calibrate_batch(): n={len(results)}, "
            f"mean={arr.mean():.4f}, std={arr.std():.4f}, "
            f"min={arr.min():.4f}, max={arr.max():.4f}"
        )

    return results