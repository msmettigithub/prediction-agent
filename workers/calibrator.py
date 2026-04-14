import math
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def temperature_sharpen(p: float, T: float = 0.6) -> float:
    """
    Apply temperature scaling in logit space to sharpen probabilities.
    
    Maps p through: p_sharp = sigmoid(logit(p) / T)
    
    T < 1.0 sharpens (pushes away from 0.5)
    T = 1.0 is identity
    T > 1.0 softens (pushes toward 0.5)
    
    Examples with T=0.6:
        0.55 -> ~0.62
        0.60 -> ~0.71
        0.45 -> ~0.38
        0.40 -> ~0.29
    """
    p = float(p)
    
    # Clamp to avoid log(0)
    p = max(1e-9, min(1 - 1e-9, p))
    
    if abs(T) < 1e-9:
        # Degenerate: return hard decision
        return 1.0 if p > 0.5 else 0.0
    
    logit_p = math.log(p / (1.0 - p))
    scaled_logit = logit_p / T
    p_sharp = 1.0 / (1.0 + math.exp(-scaled_logit))
    
    return p_sharp


def select_temperature(signal_agreement_count: int) -> float:
    """
    Choose sharpening temperature based on how many signals agree on direction.
    
    More agreement = more aggressive sharpening (lower T).
    
    Args:
        signal_agreement_count: Number of signals agreeing on direction (1, 2, 3+)
    
    Returns:
        Temperature T to use in temperature_sharpen
    """
    if signal_agreement_count >= 3:
        return 0.5   # Aggressive sharpening
    elif signal_agreement_count == 2:
        return 0.7   # Moderate sharpening
    else:
        return 0.9   # Conservative sharpening


def anchor_to_base_rate(
    p_sharpened: float,
    base_rate: Optional[float],
    base_rate_weight: float = 0.3
) -> float:
    """
    Blend sharpened probability with category base rate.
    
    Strong base rates (e.g. >70% incumbent re-election) provide
    additional separation by anchoring predictions toward known priors.
    
    Args:
        p_sharpened: The temperature-sharpened probability
        base_rate: Category base rate (e.g. 0.72 for incumbents).
                   If None, no anchoring is applied.
        base_rate_weight: Weight given to base rate (0.3 means 30% base rate,
                          70% sharpened signal)
    
    Returns:
        Blended probability
    """
    if base_rate is None:
        return p_sharpened
    
    base_rate = float(base_rate)
    base_rate = max(0.0, min(1.0, base_rate))
    base_rate_weight = max(0.0, min(1.0, base_rate_weight))
    
    blended = (1.0 - base_rate_weight) * p_sharpened + base_rate_weight * base_rate
    return blended


def apply_guardrails(p: float, lo: float = 0.08, hi: float = 0.92) -> float:
    """
    Clamp probability to [lo, hi] to prevent overconfidence.
    
    Args:
        p: Probability to clamp
        lo: Lower bound (default 0.08)
        hi: Upper bound (default 0.92)
    
    Returns:
        Clamped probability
    """
    return max(lo, min(hi, p))


def sharpen_calibrated_probability(
    p: float,
    signal_agreement_count: int,
    base_rate: Optional[float] = None,
    base_rate_weight: float = 0.3,
    guardrail_lo: float = 0.08,
    guardrail_hi: float = 0.92,
    label: str = ""
) -> float:
    """
    Full post-calibration sharpening pipeline.
    
    Steps:
    1. Require minimum 2 signals before any sharpening
    2. Select temperature based on signal agreement count
    3. Apply temperature scaling in logit space
    4. Anchor to base rate if provided
    5. Apply guardrails [0.08, 0.92]
    6. Log pre/post probabilities
    
    Args:
        p: Input calibrated probability
        signal_agreement_count: Number of signals agreeing on predicted direction
        base_rate: Optional category base rate for anchoring
        base_rate_weight: Weight for base rate in blending (default 0.3)
        guardrail_lo: Lower probability clamp (default 0.08)
        guardrail_hi: Upper probability clamp (default 0.92)
        label: Optional label for logging context
    
    Returns:
        Sharpened, anchored, and guardrailed probability
    """
    p_input = float(p)
    context = f"[{label}] " if label else ""
    
    logger.info(f"{context}sharpen_calibrated_probability called: "
                f"p={p_input:.4f}, signals={signal_agreement_count}, "
                f"base_rate={base_rate}")
    
    # Guardrail: require minimum 2 signals before sharpening
    if signal_agreement_count < 2:
        logger.info(
            f"{context}PRE-SHARPEN p={p_input:.4f} | "
            f"Skipping sharpening (only {signal_agreement_count} signal(s), need >= 2) | "
            f"POST-SHARPEN p={p_input:.4f} (unchanged)"
        )
        return apply_guardrails(p_input, guardrail_lo, guardrail_hi)
    
    # Step 1: Log pre-sharpen
    logger.info(f"{context}PRE-SHARPEN p={p_input:.4f} (signals={signal_agreement_count})")
    
    # Step 2: Select temperature
    T = select_temperature(signal_agreement_count)
    logger.debug(f"{context}Selected temperature T={T} for {signal_agreement_count} agreeing signals")
    
    # Step 3: Temperature sharpen
    p_sharpened = temperature_sharpen(p_input, T=T)
    logger.debug(f"{context}After temperature_sharpen(T={T}): {p_input:.4f} -> {p_sharpened:.4f}")
    
    # Step 4: Base rate anchoring
    p_anchored = anchor_to_base_rate(p_sharpened, base_rate, base_rate_weight)
    if base_rate is not None:
        logger.debug(
            f"{context}After base_rate anchoring (rate={base_rate:.3f}, weight={base_rate_weight}): "
            f"{p_sharpened:.4f} -> {p_anchored:.4f}"
        )
    else:
        p_anchored = p_sharpened
    
    # Step 5: Apply guardrails
    p_final = apply_guardrails(p_anchored, guardrail_lo, guardrail_hi)
    if p_final != p_anchored:
        logger.warning(
            f"{context}Guardrail clamp applied: {p_anchored:.4f} -> {p_final:.4f} "
            f"(bounds=[{guardrail_lo}, {guardrail_hi}])"
        )
    
    # Step 6: Log post-sharpen
    delta = p_final - p_input
    logger.info(
        f"{context}POST-SHARPEN p={p_final:.4f} | "
        f"delta={delta:+.4f} | "
        f"T={T} | signals={signal_agreement_count} | "
        f"base_rate={base_rate}"
    )
    
    return p_final


def calibrate(
    raw_probability: float,
    signal_agreement_count: int = 1,
    base_rate: Optional[float] = None,
    base_rate_weight: float = 0.3,
    label: str = ""
) -> float:
    """
    Main calibration entry point with post-calibration sharpening pipeline.
    
    Applies:
    - Temperature-scaled logit sharpening based on signal agreement
    - Base rate anchoring (if base_rate provided)
    - Guardrails to prevent overconfidence
    
    Args:
        raw_probability: Raw model probability output
        signal_agreement_count: Number of signals agreeing on direction
        base_rate: Optional category base rate
        base_rate_weight: Weight for base rate blending
        label: Optional context label for logging
    
    Returns:
        Calibrated and sharpened probability in [0.08, 0.92]
    """
    return sharpen_calibrated_probability(
        p=raw_probability,
        signal_agreement_count=signal_agreement_count,
        base_rate=base_rate,
        base_rate_weight=base_rate_weight,
        label=label
    )