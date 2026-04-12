workers/calibrator.py
import logging
import math

logger = logging.getLogger(__name__)

DEFAULT_ALPHA = 1.8
MAX_ALPHA = 2.5
MIDDLE_BAND_LOW = 0.42
MIDDLE_BAND_HIGH = 0.58
MIN_SIGNALS_FOR_BAND_AVOIDANCE = 2


def extremize(p: float, alpha: float = DEFAULT_ALPHA) -> float:
    p = max(0.001, min(0.999, p))
    pa = p ** alpha
    one_minus_pa = (1.0 - p) ** alpha
    result = pa / (pa + one_minus_pa)
    logger.info(
        f"CALIBRATOR extremize: raw={p:.4f} -> extremized={result:.4f} (alpha={alpha:.3f})"
    )
    return result


def confidence_weight(num_signals: int, base_alpha: float = DEFAULT_ALPHA) -> float:
    if num_signals <= 0:
        return base_alpha
    signal_bonus = math.log1p(num_signals) * 0.3
    weighted = base_alpha + signal_bonus
    capped = min(weighted, MAX_ALPHA)
    logger.info(
        f"CALIBRATOR confidence_weight: signals={num_signals} base_alpha={base_alpha:.3f} -> alpha={capped:.3f}"
    )
    return capped


def apply_floor_ceiling(
    p_extremized: float,
    p_raw: float,
    num_signals: int,
) -> float:
    in_middle_band = MIDDLE_BAND_LOW <= p_extremized <= MIDDLE_BAND_HIGH
    if in_middle_band and num_signals >= MIN_SIGNALS_FOR_BAND_AVOIDANCE:
        if p_raw >= 0.5:
            adjusted = MIDDLE_BAND_HIGH + 0.001
        else:
            adjusted = MIDDLE_BAND_LOW - 0.001
        adjusted = max(0.001, min(0.999, adjusted))
        logger.info(
            f"CALIBRATOR floor_ceiling: pushed {p_extremized:.4f} -> {adjusted:.4f} "
            f"(signals={num_signals}, raw={p_raw:.4f})"
        )
        return adjusted
    return p_extremized


def calibrate(
    probability_dict: dict,
    num_signals: int = 0,
    backtest_alpha: float = None,
) -> dict:
    result = dict(probability_dict)

    raw_prob = result.get("probability", result.get("p", result.get("raw_probability")))
    if raw_prob is None:
        logger.warning("CALIBRATOR: no probability key found in dict, returning unchanged")
        return result

    raw_prob = float(raw_prob)
    raw_prob = max(0.001, min(0.999, raw_prob))

    if backtest_alpha is not None:
        alpha = float(backtest_alpha)
        logger.info(f"CALIBRATOR: using backtest_alpha={alpha:.3f}")
    else:
        alpha = confidence_weight(num_signals, DEFAULT_ALPHA)

    p_extremized = extremize(raw_prob, alpha)
    p_final = apply_floor_ceiling(p_extremized, raw_prob, num_signals)

    result["probability"] = p_final
    result["p"] = p_final
    result["raw_probability"] = raw_prob
    result["extremized_probability"] = p_extremized
    result["calibration_alpha"] = alpha
    result["calibration_num_signals"] = num_signals
    result["separation"] = abs(p_final - 0.5)

    logger.info(
        f"CALIBRATOR final: raw={raw_prob:.4f} extremized={p_extremized:.4f} "
        f"final={p_final:.4f} separation={result['separation']:.4f} alpha={alpha:.3f}"
    )

    return result


def calibrate_scalar(
    p: float,
    num_signals: int = 0,
    backtest_alpha: float = None,
) -> float:
    result = calibrate(
        {"probability": p},
        num_signals=num_signals,
        backtest_alpha=backtest_alpha,
    )
    return result["probability"]


def calibrate_batch(
    probs: list,
    num_signals: int = 0,
    backtest_alpha: float = None,
) -> list:
    return [
        calibrate_scalar(p, num_signals=num_signals, backtest_alpha=backtest_alpha)
        for p in probs
    ]