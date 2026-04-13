import math
import logging
from typing import Optional

logger = logging.getLogger(__name__)

ACCURACY_TO_ALPHA = {
    "high": 0.6,
    "medium": 0.75,
    "low": 0.9,
}

CLAMP_MIN = 0.05
CLAMP_MAX = 0.95


def get_alpha_from_accuracy(backtest_accuracy: float) -> float:
    if backtest_accuracy >= 0.80:
        return ACCURACY_TO_ALPHA["high"]
    elif backtest_accuracy >= 0.70:
        return ACCURACY_TO_ALPHA["medium"]
    else:
        return ACCURACY_TO_ALPHA["low"]


def stretch_probability(p: float, alpha: float) -> float:
    centered = p - 0.5
    if centered == 0.0:
        return 0.5
    sign = 1.0 if centered > 0 else -1.0
    abs_double = abs(2.0 * centered)
    stretched = 0.5 + sign * (abs_double ** alpha) / 2.0
    return stretched


def clamp_probability(p: float) -> float:
    return max(CLAMP_MIN, min(CLAMP_MAX, p))


def calibrate(
    raw_probability: float,
    corroborating_signals: int = 0,
    backtest_accuracy: float = 0.804,
    market_price: Optional[float] = None,
    question_id: Optional[str] = None,
) -> float:
    p = float(raw_probability)
    p = clamp_probability(p)

    # Existing calibration step: shrink toward market price if available
    if market_price is not None:
        blend_weight = 0.7
        p = blend_weight * p + (1.0 - blend_weight) * market_price

    pre_stretch = p

    # Gate: only stretch when evidence is sufficient
    if corroborating_signals >= 3:
        alpha = get_alpha_from_accuracy(backtest_accuracy)
        p_stretched = stretch_probability(p, alpha)
        p_stretched = clamp_probability(p_stretched)

        logger.info(
            "confidence_stretch",
            extra={
                "question_id": question_id,
                "pre_stretch": pre_stretch,
                "post_stretch": p_stretched,
                "alpha": alpha,
                "backtest_accuracy": backtest_accuracy,
                "corroborating_signals": corroborating_signals,
                "stretch_delta": p_stretched - pre_stretch,
            },
        )

        return p_stretched
    else:
        logger.info(
            "confidence_stretch_skipped",
            extra={
                "question_id": question_id,
                "pre_stretch": pre_stretch,
                "post_stretch": pre_stretch,
                "corroborating_signals": corroborating_signals,
                "reason": "insufficient_evidence",
            },
        )

        return pre_stretch


def calibrate_batch(
    predictions: list,
    backtest_accuracy: float = 0.804,
) -> list:
    results = []
    for pred in predictions:
        if isinstance(pred, dict):
            raw_p = pred.get("probability", 0.5)
            signals = pred.get("corroborating_signals", 0)
            market_p = pred.get("market_price", None)
            qid = pred.get("question_id", None)
        else:
            raw_p = float(pred)
            signals = 0
            market_p = None
            qid = None

        calibrated = calibrate(
            raw_probability=raw_p,
            corroborating_signals=signals,
            backtest_accuracy=backtest_accuracy,
            market_price=market_p,
            question_id=qid,
        )
        results.append(calibrated)

    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    test_cases = [
        {"probability": 0.62, "corroborating_signals": 5, "question_id": "test_1"},
        {"probability": 0.58, "corroborating_signals": 2, "question_id": "test_2"},
        {"probability": 0.75, "corroborating_signals": 4, "market_price": 0.65, "question_id": "test_3"},
        {"probability": 0.45, "corroborating_signals": 3, "question_id": "test_4"},
    ]

    for tc in test_cases:
        result = calibrate(
            raw_probability=tc["probability"],
            corroborating_signals=tc.get("corroborating_signals", 0),
            backtest_accuracy=0.804,
            market_price=tc.get("market_price"),
            question_id=tc.get("question_id"),
        )
        print(f"Input: {tc['probability']:.3f} signals={tc.get('corroborating_signals',0)} -> Output: {result:.4f}")