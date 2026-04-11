import logging
import math
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_EXTREMIZE_EXPONENT = 1.5
PROBABILITY_FLOOR = 0.05
PROBABILITY_CEILING = 0.95


def extremize(p: float, d: float = DEFAULT_EXTREMIZE_EXPONENT) -> float:
    p_clamped = max(1e-9, min(1 - 1e-9, p))
    pd = p_clamped ** d
    one_minus_pd = (1.0 - p_clamped) ** d
    denom = pd + one_minus_pd
    if denom == 0.0:
        return p_clamped
    return pd / denom


def clamp(p: float, floor: float = PROBABILITY_FLOOR, ceiling: float = PROBABILITY_CEILING) -> float:
    return max(floor, min(ceiling, p))


def get_exponent_from_config(config: Optional[dict]) -> float:
    if config is None:
        return DEFAULT_EXTREMIZE_EXPONENT
    try:
        val = config.get("extremize_exponent", DEFAULT_EXTREMIZE_EXPONENT)
        return float(val)
    except (TypeError, ValueError):
        logger.warning(
            "Invalid extremize_exponent in config, using default %.2f",
            DEFAULT_EXTREMIZE_EXPONENT,
        )
        return DEFAULT_EXTREMIZE_EXPONENT


def calibrate(p: float, config: Optional[dict] = None) -> float:
    logger.debug("calibrate input: p=%.6f", p)

    p = max(0.0, min(1.0, p))

    adjusted = _apply_existing_calibration(p, config)
    logger.debug("post-existing-calibration: p=%.6f", adjusted)

    d = get_exponent_from_config(config)

    pre_ext = adjusted
    post_ext = extremize(adjusted, d=d)
    logger.info(
        "extremize: pre=%.6f post=%.6f exponent=%.3f",
        pre_ext,
        post_ext,
        d,
    )

    final = clamp(post_ext, floor=PROBABILITY_FLOOR, ceiling=PROBABILITY_CEILING)
    if final != post_ext:
        logger.info(
            "clamp applied: pre_clamp=%.6f final=%.6f",
            post_ext,
            final,
        )

    logger.debug("calibrate output: p=%.6f", final)
    return final


def _apply_existing_calibration(p: float, config: Optional[dict]) -> float:
    if config is None:
        return p

    bias = config.get("bias_correction", 0.0)
    try:
        bias = float(bias)
    except (TypeError, ValueError):
        bias = 0.0

    if bias != 0.0:
        logit = _logit(p)
        logit_adjusted = logit + bias
        p = _sigmoid(logit_adjusted)
        logger.debug("bias_correction=%.4f applied, new p=%.6f", bias, p)

    alpha = config.get("sharpen_alpha", None)
    if alpha is not None:
        try:
            alpha = float(alpha)
            if alpha != 1.0:
                logit = _logit(p)
                logit_sharpened = logit * alpha
                p = _sigmoid(logit_sharpened)
                logger.debug(
                    "sharpen_alpha=%.4f applied, new p=%.6f", alpha, p
                )
        except (TypeError, ValueError):
            pass

    return p


def _logit(p: float) -> float:
    p = max(1e-12, min(1 - 1e-12, p))
    return math.log(p / (1.0 - p))


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    exp_x = math.exp(x)
    return exp_x / (1.0 + exp_x)


def should_trade(p: float, min_delta: float = 0.06) -> bool:
    return abs(p - 0.5) > min_delta


def mean_separation(yes_probs: list, no_probs: list) -> Optional[float]:
    if not yes_probs or not no_probs:
        logger.warning(
            "mean_separation called with empty list: yes=%d no=%d",
            len(yes_probs),
            len(no_probs),
        )
        return None
    mean_yes = sum(yes_probs) / len(yes_probs)
    mean_no = sum(no_probs) / len(no_probs)
    sep = mean_yes - mean_no
    logger.info(
        "mean_separation: mean_yes=%.4f mean_no=%.4f separation=%.4f",
        mean_yes,
        mean_no,
        sep,
    )
    return sep