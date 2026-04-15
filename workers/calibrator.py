import numpy as np
import logging
from typing import Optional

logger = logging.getLogger(__name__)

BASE_RATES = {
    "macro/fed": 0.70,
    "earnings": 0.65,
    "geopolitical": 0.45,
    "crypto": 0.52,
    "tech": 0.62,
    "default": 0.55,
}

BASE_RATE_WEIGHT = 0.3


def extremize(p: float, alpha: float = 1.5) -> float:
    p = float(np.clip(p, 1e-9, 1 - 1e-9))
    p_a = p ** alpha
    one_minus_p_a = (1.0 - p) ** alpha
    denom = p_a + one_minus_p_a
    if denom == 0:
        return p
    return float(p_a / denom)


def signal_concordance_score(
    yfinance_trend: Optional[float] = None,
    pytrends_momentum: Optional[float] = None,
    vader_sentiment: Optional[float] = None,
) -> int:
    signals = []
    if yfinance_trend is not None:
        signals.append(1 if yfinance_trend > 0 else -1)
    if pytrends_momentum is not None:
        signals.append(1 if pytrends_momentum > 0 else -1)
    if vader_sentiment is not None:
        signals.append(1 if vader_sentiment > 0 else -1)

    if not signals:
        return 0

    positive = sum(1 for s in signals if s > 0)
    negative = sum(1 for s in signals if s < 0)
    dominant = max(positive, negative)
    return dominant


def blend_with_base_rate(p: float, category: str = "default") -> float:
    base_rate = BASE_RATES.get(category, BASE_RATES["default"])
    blended = (1.0 - BASE_RATE_WEIGHT) * p + BASE_RATE_WEIGHT * base_rate
    return float(np.clip(blended, 0.0, 1.0))


def clamp(p: float, floor: float = 0.08, ceiling: float = 0.92) -> float:
    return float(np.clip(p, floor, ceiling))


def calibrate(
    raw_prob: float,
    category: str = "default",
    yfinance_trend: Optional[float] = None,
    pytrends_momentum: Optional[float] = None,
    vader_sentiment: Optional[float] = None,
) -> float:
    try:
        p = float(np.clip(raw_prob, 1e-9, 1 - 1e-9))

        p = blend_with_base_rate(p, category)

        concordance = signal_concordance_score(
            yfinance_trend=yfinance_trend,
            pytrends_momentum=pytrends_momentum,
            vader_sentiment=vader_sentiment,
        )

        alpha = 1.5
        if concordance >= 3:
            alpha = 2.0

        logger.debug(
            f"calibrate: raw={raw_prob:.4f} blended={p:.4f} "
            f"concordance={concordance} alpha={alpha:.1f} category={category}"
        )

        p = extremize(p, alpha=alpha)

        p = clamp(p)

        logger.debug(f"calibrate: final={p:.4f}")
        return p

    except Exception as exc:
        logger.error(f"calibrate() failed: {exc}", exc_info=True)
        return float(np.clip(raw_prob, 0.08, 0.92))