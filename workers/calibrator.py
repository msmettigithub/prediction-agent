import logging
import math
from typing import Optional
import numpy as np

logger = logging.getLogger(__name__)


def compute_confidence_score(
    agreeing_signals: int,
    total_signals: int,
    signal_strength_variance: float,
    historical_reliability: float,
) -> float:
    agreement_ratio = agreeing_signals / max(total_signals, 1)
    agreement_score = max(0.0, (agreement_ratio - 0.5) * 2.0)

    variance_score = max(0.0, 1.0 - signal_strength_variance)

    reliability_score = max(0.0, min(1.0, historical_reliability))

    confidence = (
        0.4 * agreement_score
        + 0.3 * variance_score
        + 0.3 * reliability_score
    )

    return float(np.clip(confidence, 0.0, 1.0))


def extremize(p: float, alpha: float) -> float:
    p_safe = float(np.clip(p, 1e-9, 1.0 - 1e-9))
    pa = p_safe ** alpha
    one_minus_pa = (1.0 - p_safe) ** alpha
    return pa / (pa + one_minus_pa)


def calibrate(
    base_probability: float,
    agreeing_signals: int = 0,
    total_signals: int = 1,
    signal_strength_variance: float = 0.5,
    historical_reliability: float = 0.5,
    tool_name: Optional[str] = None,
) -> float:
    p = float(np.clip(base_probability, 1e-9, 1.0 - 1e-9))

    confidence = compute_confidence_score(
        agreeing_signals=agreeing_signals,
        total_signals=total_signals,
        signal_strength_variance=signal_strength_variance,
        historical_reliability=historical_reliability,
    )

    alpha = 1.0 + 1.5 * confidence

    p_ext = extremize(p, alpha)

    p_final = float(np.clip(p_ext, 0.05, 0.95))

    logger.info(
        "calibration_extremization",
        extra={
            "tool": tool_name or "unknown",
            "p_pre_extremization": round(p, 6),
            "p_post_extremization": round(p_ext, 6),
            "p_final": round(p_final, 6),
            "confidence_score": round(confidence, 6),
            "alpha": round(alpha, 6),
            "agreeing_signals": agreeing_signals,
            "total_signals": total_signals,
            "signal_strength_variance": round(signal_strength_variance, 6),
            "historical_reliability": round(historical_reliability, 6),
            "rl_feedback": {
                "pre": round(p, 6),
                "post": round(p_final, 6),
                "confidence": round(confidence, 6),
                "alpha": round(alpha, 6),
                "separation_delta": round(abs(p_final - 0.5) - abs(p - 0.5), 6),
            },
        },
    )

    return p_final


def calibrate_multi_tool(
    base_probability: float,
    tool_signals: dict,
    historical_reliability: float = 0.5,
    tool_name: Optional[str] = None,
) -> float:
    tool_names = [
        "pytrends",
        "yfinance",
        "newsapi",
        "vaderSentiment",
        "statsmodels",
    ]

    threshold = 0.5
    direction = base_probability >= threshold

    agreeing = 0
    total = 0
    strengths = []

    for tool in tool_names:
        if tool in tool_signals:
            signal_value = tool_signals[tool]
            if signal_value is not None:
                total += 1
                signal_direction = signal_value >= threshold
                if signal_direction == direction:
                    agreeing += 1
                strengths.append(float(signal_value))

    if len(strengths) >= 2:
        variance = float(np.var(strengths))
        max_possible_variance = 0.25
        normalized_variance = min(variance / max_possible_variance, 1.0)
    else:
        normalized_variance = 0.5

    return calibrate(
        base_probability=base_probability,
        agreeing_signals=agreeing,
        total_signals=max(total, 1),
        signal_strength_variance=normalized_variance,
        historical_reliability=historical_reliability,
        tool_name=tool_name,
    )


def batch_calibrate(predictions: list, historical_reliability: float = 0.5) -> list:
    results = []
    for pred in predictions:
        if isinstance(pred, dict):
            p = pred.get("probability", 0.5)
            tool_signals = pred.get("tool_signals", {})
            tool_name = pred.get("tool_name")

            if tool_signals:
                p_cal = calibrate_multi_tool(
                    base_probability=p,
                    tool_signals=tool_signals,
                    historical_reliability=historical_reliability,
                    tool_name=tool_name,
                )
            else:
                p_cal = calibrate(
                    base_probability=p,
                    historical_reliability=historical_reliability,
                    tool_name=tool_name,
                )

            results.append({**pred, "calibrated_probability": p_cal})
        elif isinstance(pred, (float, int)):
            p_cal = calibrate(
                base_probability=float(pred),
                historical_reliability=historical_reliability,
            )
            results.append(p_cal)
        else:
            results.append(pred)

    return results