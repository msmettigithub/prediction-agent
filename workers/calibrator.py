import math
import logging
from typing import Optional, Sequence

logger = logging.getLogger(__name__)

CALIBRATOR_STRETCH_ENABLED: bool = True
CALIBRATOR_STRETCH_FACTOR: float = 1.5

_FLOOR: float = 0.05
_CEILING: float = 0.95


def _to_logit(p: float) -> float:
    p = max(1e-9, min(1 - 1e-9, p))
    return math.log(p / (1.0 - p))


def _from_logit(logit: float) -> float:
    return 1.0 / (1.0 + math.exp(-logit))


def _concordance_weight(signals: Sequence[float]) -> float:
    if not signals:
        return 1.0
    above = sum(1 for s in signals if s > 0.5)
    below = sum(1 for s in signals if s < 0.5)
    total = above + below
    if total == 0:
        return 1.0
    majority = max(above, below)
    agreement_ratio = majority / total
    if agreement_ratio >= 1.0:
        return 1.6
    elif agreement_ratio >= 0.67:
        return 1.3
    else:
        return 1.0


def stretch_probability(
    p: float,
    signals: Optional[Sequence[float]] = None,
    stretch_factor: Optional[float] = None,
    enabled: Optional[bool] = None,
) -> float:
    if enabled is None:
        enabled = CALIBRATOR_STRETCH_ENABLED
    if not enabled:
        return p

    if stretch_factor is None:
        stretch_factor = CALIBRATOR_STRETCH_FACTOR

    if signals is None:
        signals = [p]

    concordance = _concordance_weight(signals)
    logit = _to_logit(p)
    stretched_logit = logit * stretch_factor * concordance
    new_p = _from_logit(stretched_logit)
    new_p = max(_FLOOR, min(_CEILING, new_p))
    return new_p


def calibrate(
    raw_probability: float,
    signals: Optional[Sequence[float]] = None,
    stretch_factor: Optional[float] = None,
    enabled: Optional[bool] = None,
) -> float:
    pre_stretch_p = float(raw_probability)
    pre_stretch_p = max(1e-9, min(1 - 1e-9, pre_stretch_p))

    if signals is None:
        signals = [pre_stretch_p]

    concordance = _concordance_weight(signals)
    pre_logit = _to_logit(pre_stretch_p)

    logger.debug(
        "pre_stretch probability=%.6f logit=%.6f concordance_weight=%.2f signal_count=%d",
        pre_stretch_p,
        pre_logit,
        concordance,
        len(signals),
    )

    post_stretch_p = stretch_probability(
        pre_stretch_p,
        signals=signals,
        stretch_factor=stretch_factor,
        enabled=enabled,
    )
    post_logit = _to_logit(post_stretch_p)
    separation_delta = abs(post_stretch_p - 0.5) - abs(pre_stretch_p - 0.5)

    logger.info(
        "calibrator_stretch: pre_p=%.6f post_p=%.6f pre_logit=%.6f post_logit=%.6f "
        "separation_delta=%.6f concordance=%.2f stretch_factor=%.4f enabled=%s",
        pre_stretch_p,
        post_stretch_p,
        pre_logit,
        post_logit,
        separation_delta,
        concordance,
        stretch_factor if stretch_factor is not None else CALIBRATOR_STRETCH_FACTOR,
        enabled if enabled is not None else CALIBRATOR_STRETCH_ENABLED,
    )

    return post_stretch_p


def batch_calibrate(
    raw_probabilities: Sequence[float],
    signals_per_prediction: Optional[Sequence[Optional[Sequence[float]]]] = None,
    stretch_factor: Optional[float] = None,
    enabled: Optional[bool] = None,
) -> list:
    if signals_per_prediction is None:
        signals_per_prediction = [None] * len(raw_probabilities)

    results = []
    pre_separations = []
    post_separations = []

    for i, p in enumerate(raw_probabilities):
        sigs = signals_per_prediction[i] if i < len(signals_per_prediction) else None
        pre_sep = abs(p - 0.5)
        post_p = calibrate(p, signals=sigs, stretch_factor=stretch_factor, enabled=enabled)
        post_sep = abs(post_p - 0.5)
        pre_separations.append(pre_sep)
        post_separations.append(post_sep)
        results.append(post_p)

    if pre_separations:
        mean_pre = sum(pre_separations) / len(pre_separations)
        mean_post = sum(post_separations) / len(post_separations)
        logger.info(
            "batch_calibrate_metrics: n=%d mean_pre_separation=%.6f mean_post_separation=%.6f "
            "delta=%.6f pct_increase=%.2f%%",
            len(results),
            mean_pre,
            mean_post,
            mean_post - mean_pre,
            100.0 * (mean_post - mean_pre) / max(mean_pre, 1e-9),
        )

    return results