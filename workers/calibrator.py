import logging
import math
from typing import Any

logger = logging.getLogger(__name__)


def calibrate_probability(
    raw_probability: float,
    category: str = "",
    domain: str = "",
    num_corroborating_signals: int = 0,
    historical_rates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Three-stage calibration pipeline:
      Stage 1 - Base Rate Anchor
      Stage 2 - Signal-Weighted Power Stretch
      Stage 3 - Extremity Guard

    Returns a dict with 'probability' and 'calibration_metadata'.
    """
    if historical_rates is None:
        historical_rates = {}

    p = float(raw_probability)
    p = max(0.001, min(0.999, p))

    # ------------------------------------------------------------------ #
    # Stage 1 – Base Rate Anchor                                          #
    # ------------------------------------------------------------------ #
    base_rate: float | None = None
    base_rate_sample_size: int = 0

    lookup_keys = []
    if category and domain:
        lookup_keys.append(f"{category}:{domain}")
    if category:
        lookup_keys.append(category)
    if domain:
        lookup_keys.append(domain)

    for key in lookup_keys:
        entry = historical_rates.get(key)
        if entry and isinstance(entry, dict):
            br = entry.get("resolution_rate")
            ss = entry.get("sample_size", 0)
            if br is not None and isinstance(br, (int, float)):
                base_rate = float(br)
                base_rate_sample_size = int(ss)
                break

    p_after_anchor = p
    anchor_weight: float = 0.0

    if base_rate is not None:
        same_direction = (p > 0.5 and base_rate > 0.5) or (p < 0.5 and base_rate < 0.5)
        if same_direction:
            # Weight grows with sample size, capped so it never dominates entirely
            # weight = 0 at n=0, asymptotes toward 0.35 at large n
            anchor_weight = 0.35 * (1.0 - math.exp(-base_rate_sample_size / 200.0))
            p_after_anchor = (1.0 - anchor_weight) * p + anchor_weight * base_rate
            logger.debug(
                "Stage 1 anchor: base_rate=%.4f sample_size=%d weight=%.4f "
                "p %.4f -> %.4f",
                base_rate,
                base_rate_sample_size,
                anchor_weight,
                p,
                p_after_anchor,
            )

    # ------------------------------------------------------------------ #
    # Stage 2 – Signal-Weighted Power Stretch                             #
    # ------------------------------------------------------------------ #
    # a = 1.0 + 0.15 * num_corroborating_signals, capped at 2.5
    signals_capped = min(num_corroborating_signals, 10)  # 10 signals -> a=2.5
    a = 1.0 + 0.15 * signals_capped
    a = min(a, 2.5)

    p_in = p_after_anchor
    if p_in <= 0.0 or p_in >= 1.0:
        p_stretched = p_in
    else:
        pa = p_in ** a
        q_a = (1.0 - p_in) ** a
        denominator = pa + q_a
        if denominator == 0.0:
            p_stretched = p_in
        else:
            p_stretched = pa / denominator

    logger.debug(
        "Stage 2 stretch: signals=%d a=%.4f p %.4f -> %.4f",
        num_corroborating_signals,
        a,
        p_in,
        p_stretched,
    )

    # ------------------------------------------------------------------ #
    # Stage 3 – Extremity Guard                                           #
    # ------------------------------------------------------------------ #
    p_final = max(0.05, min(0.95, p_stretched))

    logger.debug(
        "Stage 3 guard: p %.4f -> %.4f (clamped=%s)",
        p_stretched,
        p_final,
        p_final != p_stretched,
    )

    calibration_metadata: dict[str, Any] = {
        "raw_probability": raw_probability,
        "p_after_anchor": round(p_after_anchor, 6),
        "p_after_stretch": round(p_stretched, 6),
        "p_final": round(p_final, 6),
        "stretch_factor_a": round(a, 4),
        "num_corroborating_signals": num_corroborating_signals,
        "base_rate_used": base_rate,
        "base_rate_sample_size": base_rate_sample_size,
        "anchor_weight": round(anchor_weight, 6),
        "category": category,
        "domain": domain,
        "clamped": p_final != p_stretched,
    }

    return {
        "probability": round(p_final, 6),
        "calibration_metadata": calibration_metadata,
    }


def calibrate(
    raw_probability: float,
    context: dict[str, Any] | None = None,
) -> float:
    """
    Thin convenience wrapper that accepts an optional context dict and returns
    only the calibrated probability float.  Existing call-sites that pass a
    single probability value continue to work without modification.
    """
    if context is None:
        context = {}

    result = calibrate_probability(
        raw_probability=raw_probability,
        category=context.get("category", ""),
        domain=context.get("domain", ""),
        num_corroborating_signals=int(context.get("num_corroborating_signals", 0)),
        historical_rates=context.get("historical_rates", {}),
    )
    return result["probability"]