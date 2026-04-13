import numpy as np
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def compute_confidence_score(
    signal_agreement: float,
    evidence_strength: float,
    base_rate_divergence: float,
    source_reliability_weights: Optional[list] = None,
    concordant_signals: int = 0,
    total_signals: int = 1,
) -> float:
    """
    Compute a confidence score from multiple signal quality metrics.

    Args:
        signal_agreement: Fraction of concordant signals (concordant/total)
        evidence_strength: Weighted evidence strength by source reliability
        base_rate_divergence: How far the prediction is from the base rate
        source_reliability_weights: Optional list of weights for sources
        concordant_signals: Number of signals agreeing with prediction direction
        total_signals: Total number of signals considered

    Returns:
        confidence_score in [0, 1]
    """
    if total_signals == 0:
        logger.warning("total_signals is 0, defaulting confidence_score to 0")
        return 0.0

    agreement_component = concordant_signals / max(total_signals, 1)

    strength_component = float(np.clip(evidence_strength, 0.0, 1.0))

    divergence_component = float(np.clip(abs(base_rate_divergence), 0.0, 1.0))

    confidence_score = (
        0.45 * agreement_component
        + 0.35 * strength_component
        + 0.20 * divergence_component
    )

    confidence_score = float(np.clip(confidence_score, 0.0, 1.0))

    logger.debug(
        "confidence_score components",
        extra={
            "agreement_component": agreement_component,
            "strength_component": strength_component,
            "divergence_component": divergence_component,
            "confidence_score": confidence_score,
            "concordant_signals": concordant_signals,
            "total_signals": total_signals,
        },
    )

    return confidence_score


def get_extremization_exponent(confidence_score: float) -> float:
    """
    Map a confidence score to an extremization exponent.

    Args:
        confidence_score: Value in [0, 1]

    Returns:
        Extremization exponent a >= 1.0
    """
    if confidence_score < 0.4:
        return 1.0
    elif confidence_score < 0.7:
        return 1.2
    else:
        return 1.5


def apply_extremization(p: float, a: float) -> float:
    """
    Apply the power-law extremization transform:
        p_ext = p^a / (p^a + (1-p)^a)

    When a=1.0, this is the identity transform.
    When a>1.0, probabilities are pushed further from 0.5.

    Args:
        p: Probability in (0, 1)
        a: Extremization exponent >= 1.0

    Returns:
        Extremized probability
    """
    p = float(np.clip(p, 1e-9, 1 - 1e-9))

    if abs(a - 1.0) < 1e-9:
        return p

    p_a = p ** a
    q_a = (1.0 - p) ** a
    denom = p_a + q_a

    if denom == 0:
        logger.warning("Extremization denominator is zero, returning p unchanged")
        return p

    return p_a / denom


def calibrate(
    raw_probability: float,
    concordant_signals: int = 0,
    total_signals: int = 1,
    evidence_strength: float = 0.5,
    base_rate: float = 0.5,
    source_reliability_weights: Optional[list] = None,
    sentiment_magnitude: float = 0.0,
    source_count: int = 1,
    agreement_ratio: float = 0.5,
    metadata: Optional[dict] = None,
) -> dict:
    """
    Full calibration pipeline with extremization stage.

    Steps:
    1. Clip raw probability to valid range
    2. Compute confidence score from signal metadata
    3. Select extremization exponent based on confidence
    4. Apply extremization transform
    5. Clamp final output to [0.05, 0.95]
    6. Log pre/post values for RL feedback

    Args:
        raw_probability: Initial calibrated probability from upstream model
        concordant_signals: Number of signals agreeing with prediction direction
        total_signals: Total signals considered
        evidence_strength: Weighted evidence strength by source reliability [0,1]
        base_rate: Prior/base rate for this market type
        source_reliability_weights: Optional reliability weights per source
        sentiment_magnitude: Absolute magnitude of sentiment signal [0,1]
        source_count: Number of distinct sources contributing
        agreement_ratio: Fraction of sources in agreement
        metadata: Optional dict for passing through additional signal metadata

    Returns:
        dict with keys:
            - probability: Final clamped extremized probability
            - pre_extremization_probability: Probability before extremization
            - confidence_score: Computed confidence score
            - extremization_exponent: Exponent a used
            - signal_metadata: Dict with source_count, agreement_ratio, sentiment_magnitude
            - concordant_signals: Passed through
            - total_signals: Passed through
    """
    p_raw = float(np.clip(raw_probability, 1e-9, 1 - 1e-9))

    base_rate_divergence = abs(p_raw - base_rate)

    confidence_score = compute_confidence_score(
        signal_agreement=agreement_ratio,
        evidence_strength=evidence_strength,
        base_rate_divergence=base_rate_divergence,
        source_reliability_weights=source_reliability_weights,
        concordant_signals=concordant_signals,
        total_signals=total_signals,
    )

    a = get_extremization_exponent(confidence_score)

    p_ext = apply_extremization(p_raw, a)

    p_final = float(np.clip(p_ext, 0.05, 0.95))

    signal_metadata = {
        "source_count": source_count,
        "agreement_ratio": agreement_ratio,
        "sentiment_magnitude": float(np.clip(abs(sentiment_magnitude), 0.0, 1.0)),
    }

    if metadata:
        signal_metadata.update(metadata)

    logger.info(
        "Calibrator extremization stage",
        extra={
            "pre_extremization_probability": p_raw,
            "post_extremization_probability": p_ext,
            "final_probability": p_final,
            "confidence_score": confidence_score,
            "extremization_exponent": a,
            "concordant_signals": concordant_signals,
            "total_signals": total_signals,
            "evidence_strength": evidence_strength,
            "base_rate_divergence": base_rate_divergence,
            "signal_metadata": signal_metadata,
            "rl_feedback": {
                "pre_ext": p_raw,
                "post_ext": p_final,
                "confidence": confidence_score,
                "exponent": a,
            },
        },
    )

    return {
        "probability": p_final,
        "pre_extremization_probability": p_raw,
        "confidence_score": confidence_score,
        "extremization_exponent": a,
        "signal_metadata": signal_metadata,
        "concordant_signals": concordant_signals,
        "total_signals": total_signals,
    }


def calibrate_simple(raw_probability: float) -> float:
    """
    Simplified calibration entry point for callers that don't provide signal metadata.
    Uses conservative defaults (no extremization applied).

    Args:
        raw_probability: Raw probability from upstream model

    Returns:
        Calibrated probability clamped to [0.05, 0.95]
    """
    result = calibrate(
        raw_probability=raw_probability,
        concordant_signals=0,
        total_signals=1,
        evidence_strength=0.3,
        base_rate=0.5,
        agreement_ratio=0.5,
        sentiment_magnitude=0.0,
        source_count=1,
    )
    return result["probability"]