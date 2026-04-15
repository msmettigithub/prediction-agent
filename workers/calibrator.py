import logging
import math

logger = logging.getLogger(__name__)


def extremize(p: float, a: float) -> float:
    p = max(1e-9, min(1 - 1e-9, p))
    pa = p ** a
    one_minus_pa = (1.0 - p) ** a
    return pa / (pa + one_minus_pa)


def calibrate(
    raw_prob: float,
    base_rate: float = None,
    sentiment_strength: float = None,
    sentiment_direction: float = None,
) -> float:
    pre_extremize = float(max(1e-9, min(1 - 1e-9, raw_prob)))

    # Determine direction implied by raw probability (above or below 0.5)
    raw_direction = 1 if pre_extremize >= 0.5 else -1

    # Confidence-gated amplifier: check if we are in the uncertain band
    in_uncertain_band = 0.42 <= pre_extremize <= 0.58

    if in_uncertain_band:
        signals_agree = 0
        signals_conflict = 0

        # Check base rate prior signal
        if base_rate is not None:
            base_direction = 1 if base_rate >= 0.5 else -1
            if base_direction == raw_direction:
                signals_agree += 1
            else:
                signals_conflict += 1

        # Check news sentiment signal
        if sentiment_strength is not None and sentiment_direction is not None:
            # sentiment_strength: magnitude [0, 1]; sentiment_direction: +1 or -1
            if sentiment_strength >= 0.4:
                sent_dir = 1 if sentiment_direction > 0 else -1
                if sent_dir == raw_direction:
                    signals_agree += 1
                else:
                    signals_conflict += 1

        if signals_agree > 0 and signals_conflict == 0 and signals_agree >= 1:
            # Multiple (or at least one strong) signals agree — boost
            a = 2.5
            amplifier_reason = "signals_agree"
        elif signals_conflict > 0 and signals_agree == 0:
            # Signals conflict — no change
            a = 1.0
            amplifier_reason = "signals_conflict"
        else:
            # Mixed or no signals — apply default mild extremizing
            a = 2.0
            amplifier_reason = "default_uncertain_band"
    else:
        # Outside uncertain band — apply standard extremizing
        a = 2.0
        amplifier_reason = "outside_uncertain_band"

    post_extremize = extremize(pre_extremize, a)

    # Clamp to avoid overconfidence
    final_prob = float(max(0.05, min(0.95, post_extremize)))

    logger.info(
        "CALIBRATION_STEP "
        "pre_extremize=%.6f "
        "a=%.2f "
        "post_extremize=%.6f "
        "final_prob=%.6f "
        "in_uncertain_band=%s "
        "amplifier_reason=%s "
        "base_rate=%s "
        "sentiment_strength=%s "
        "sentiment_direction=%s",
        pre_extremize,
        a,
        post_extremize,
        final_prob,
        in_uncertain_band,
        amplifier_reason,
        base_rate,
        sentiment_strength,
        sentiment_direction,
    )

    return final_prob