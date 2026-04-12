import logging
import math
import numpy as np
from typing import Optional

logger = logging.getLogger(__name__)


def evidence_strength(
    base_rate: Optional[float] = None,
    trend_momentum: Optional[float] = None,
    sentiment_polarity: Optional[float] = None,
    volume_signal: Optional[float] = None,
) -> float:
    """
    Count corroborating signals and return a score in [0, 1].

    Parameters
    ----------
    base_rate : float or None
        Historical base rate probability (0-1). Divergence from 0.5 is a signal.
    trend_momentum : float or None
        Trend momentum value in [-1, 1]. Non-zero values are directional signals.
    sentiment_polarity : float or None
        Sentiment polarity in [-1, 1]. Non-zero values are directional signals.
    volume_signal : float or None
        Volume signal in [-1, 1]. Non-zero values indicate volume confirmation.

    Returns
    -------
    float
        Score in [0, 1] representing fraction of active signals that corroborate.
    """
    scores = []

    if base_rate is not None:
        divergence = abs(base_rate - 0.5) * 2.0
        scores.append(min(1.0, divergence))

    if trend_momentum is not None:
        scores.append(min(1.0, abs(float(trend_momentum))))

    if sentiment_polarity is not None:
        scores.append(min(1.0, abs(float(sentiment_polarity))))

    if volume_signal is not None:
        scores.append(min(1.0, abs(float(volume_signal))))

    if not scores:
        return 0.5

    return float(np.mean(scores))


def _power_transform(p: float, gamma: float) -> float:
    """
    Apply power transform: p_cal = 0.5 + sign(p - 0.5) * |2p - 1|^gamma * 0.5

    gamma < 1 stretches probabilities away from 0.5 (amplifies).
    gamma > 1 shrinks probabilities toward 0.5 (dampens).
    """
    p = float(p)
    sign = 1.0 if p >= 0.5 else -1.0
    inner = abs(2.0 * p - 1.0)
    transformed = 0.5 + sign * (inner ** gamma) * 0.5
    return transformed


def calibrate(
    p: float,
    base_rate: Optional[float] = None,
    trend_momentum: Optional[float] = None,
    sentiment_polarity: Optional[float] = None,
    volume_signal: Optional[float] = None,
    gamma_strong: float = 0.7,
    gamma_weak: float = 1.3,
    floor: float = 0.05,
    ceiling: float = 0.95,
    market_id: Optional[str] = None,
) -> float:
    """
    Calibrate a raw probability using evidence-weighted power transform.

    When evidence_strength > 0.6, gamma < 1 is applied to stretch probabilities
    away from 0.5 (signal convergence warrants more confidence).

    When evidence_strength < 0.4, gamma > 1 is applied to shrink probabilities
    toward 0.5 (weak evidence warrants less confidence).

    Between 0.4 and 0.6, linear interpolation between the two gammas is used.

    A floor/ceiling clamp at 0.05/0.95 prevents overconfidence.

    Parameters
    ----------
    p : float
        Raw probability in [0, 1].
    base_rate : float or None
        Historical base rate probability for evidence scoring.
    trend_momentum : float or None
        Trend momentum signal for evidence scoring.
    sentiment_polarity : float or None
        Sentiment polarity signal for evidence scoring.
    volume_signal : float or None
        Volume signal for evidence scoring.
    gamma_strong : float
        Power transform gamma for strong evidence (< 1 stretches away from 0.5).
    gamma_weak : float
        Power transform gamma for weak evidence (> 1 shrinks toward 0.5).
    floor : float
        Minimum output probability (prevents overconfidence toward 0).
    ceiling : float
        Maximum output probability (prevents overconfidence toward 1).
    market_id : str or None
        Optional market identifier for logging.

    Returns
    -------
    float
        Calibrated probability clamped to [floor, ceiling].
    """
    p = float(p)
    p_pre = max(0.0, min(1.0, p))

    ev_strength = evidence_strength(
        base_rate=base_rate,
        trend_momentum=trend_momentum,
        sentiment_polarity=sentiment_polarity,
        volume_signal=volume_signal,
    )

    if ev_strength > 0.6:
        gamma = gamma_strong
    elif ev_strength < 0.4:
        gamma = gamma_weak
    else:
        t = (ev_strength - 0.4) / 0.2
        gamma = gamma_weak + t * (gamma_strong - gamma_weak)

    p_transformed = _power_transform(p_pre, gamma)

    p_cal = max(floor, min(ceiling, p_transformed))

    log_context = f"market={market_id} " if market_id else ""
    logger.info(
        "%sevidence_strength=%.4f gamma=%.4f p_raw=%.4f p_pre_clamp=%.4f p_cal=%.4f",
        log_context,
        ev_strength,
        gamma,
        p_pre,
        p_transformed,
        p_cal,
    )

    return p_cal


def calibrate_batch(
    probabilities,
    base_rate: Optional[float] = None,
    trend_momentum: Optional[float] = None,
    sentiment_polarity: Optional[float] = None,
    volume_signal: Optional[float] = None,
    gamma_strong: float = 0.7,
    gamma_weak: float = 1.3,
    floor: float = 0.05,
    ceiling: float = 0.95,
    market_id: Optional[str] = None,
):
    """
    Calibrate a list or array of raw probabilities.

    Parameters
    ----------
    probabilities : iterable of float
        Raw probabilities in [0, 1].
    base_rate, trend_momentum, sentiment_polarity, volume_signal : float or None
        Shared signals used for evidence scoring across all probabilities.
    gamma_strong : float
        Power transform gamma for strong evidence.
    gamma_weak : float
        Power transform gamma for weak evidence.
    floor : float
        Minimum output probability.
    ceiling : float
        Maximum output probability.
    market_id : str or None
        Optional market identifier for logging.

    Returns
    -------
    list of float
        Calibrated probabilities.
    """
    return [
        calibrate(
            p=p,
            base_rate=base_rate,
            trend_momentum=trend_momentum,
            sentiment_polarity=sentiment_polarity,
            volume_signal=volume_signal,
            gamma_strong=gamma_strong,
            gamma_weak=gamma_weak,
            floor=floor,
            ceiling=ceiling,
            market_id=market_id,
        )
        for p in probabilities
    ]