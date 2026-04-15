import logging
import math
import os
from typing import Optional

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

logger.info("CALIBRATOR MODULE LOADED v3.0 - workers/calibrator.py")

CATEGORY_BASE_RATES = {
    "financial_exceed": 0.65,
    "financial_below": 0.35,
    "sports_favorite": 0.60,
    "election_incumbent": 0.55,
    "default": 0.50,
}

BASE_RATE_WEIGHT = 0.25
DEFAULT_GAMMA = 0.7
CONCORDANCE_GAMMA = 0.55
CONCORDANCE_THRESHOLD = 3
PROB_MIN = 0.05
PROB_MAX = 0.95


def confidence_stretch(p: float, gamma: float = DEFAULT_GAMMA) -> float:
    p = max(PROB_MIN, min(PROB_MAX, p))
    centered = p - 0.5
    sign = 1.0 if centered >= 0 else -1.0
    abs_centered = abs(centered)
    stretched = sign * (abs(2 * centered) ** gamma) / 2.0
    p_adjusted = 0.5 + stretched
    p_adjusted = max(PROB_MIN, min(PROB_MAX, p_adjusted))
    return p_adjusted


def count_signal_concordance(
    raw_prob: float,
    yfinance_momentum: Optional[float] = None,
    pytrends_interest: Optional[float] = None,
    newsapi_sentiment: Optional[float] = None,
    statsmodels_trend: Optional[float] = None,
) -> int:
    direction = 1 if raw_prob >= 0.5 else -1
    agreeing = 0

    if yfinance_momentum is not None:
        signal_dir = 1 if yfinance_momentum >= 0 else -1
        if signal_dir == direction:
            agreeing += 1

    if pytrends_interest is not None:
        signal_dir = 1 if pytrends_interest >= 0.5 else -1
        if signal_dir == direction:
            agreeing += 1

    if newsapi_sentiment is not None:
        signal_dir = 1 if newsapi_sentiment >= 0.0 else -1
        if signal_dir == direction:
            agreeing += 1

    if statsmodels_trend is not None:
        signal_dir = 1 if statsmodels_trend >= 0.0 else -1
        if signal_dir == direction:
            agreeing += 1

    return agreeing


def get_base_rate(category: str) -> float:
    for key in CATEGORY_BASE_RATES:
        if key in category.lower():
            return CATEGORY_BASE_RATES[key]
    return CATEGORY_BASE_RATES["default"]


def blend_with_base_rate(p: float, category: str, weight: float = BASE_RATE_WEIGHT) -> float:
    base_rate = get_base_rate(category)
    blended = (1.0 - weight) * p + weight * base_rate
    return blended


def calibrate(
    raw_prob: float,
    contract_id: str = "unknown",
    category: str = "default",
    yfinance_momentum: Optional[float] = None,
    pytrends_interest: Optional[float] = None,
    newsapi_sentiment: Optional[float] = None,
    statsmodels_trend: Optional[float] = None,
) -> float:
    logger.info(
        "PRE_CALIBRATION contract_id=%s category=%s raw_prob=%.4f "
        "yfinance_momentum=%s pytrends_interest=%s newsapi_sentiment=%s statsmodels_trend=%s",
        contract_id,
        category,
        raw_prob,
        yfinance_momentum,
        pytrends_interest,
        newsapi_sentiment,
        statsmodels_trend,
    )

    p = max(PROB_MIN, min(PROB_MAX, float(raw_prob)))

    p_blended = blend_with_base_rate(p, category, weight=BASE_RATE_WEIGHT)
    logger.info(
        "AFTER_BASE_RATE_BLEND contract_id=%s p_before=%.4f p_after=%.4f base_rate=%.4f weight=%.2f",
        contract_id,
        p,
        p_blended,
        get_base_rate(category),
        BASE_RATE_WEIGHT,
    )

    concordance_count = count_signal_concordance(
        raw_prob=p_blended,
        yfinance_momentum=yfinance_momentum,
        pytrends_interest=pytrends_interest,
        newsapi_sentiment=newsapi_sentiment,
        statsmodels_trend=statsmodels_trend,
    )

    gamma = CONCORDANCE_GAMMA if concordance_count >= CONCORDANCE_THRESHOLD else DEFAULT_GAMMA

    logger.info(
        "SIGNAL_CONCORDANCE contract_id=%s concordance_count=%d threshold=%d gamma_selected=%.2f",
        contract_id,
        concordance_count,
        CONCORDANCE_THRESHOLD,
        gamma,
    )

    p_stretched = confidence_stretch(p_blended, gamma=gamma)

    p_final = max(PROB_MIN, min(PROB_MAX, p_stretched))

    logger.info(
        "POST_CALIBRATION contract_id=%s raw_prob=%.4f p_blended=%.4f p_stretched=%.4f "
        "p_final=%.4f gamma=%.2f concordance=%d category=%s",
        contract_id,
        raw_prob,
        p_blended,
        p_stretched,
        p_final,
        gamma,
        concordance_count,
        category,
    )

    return p_final


def batch_calibrate(contracts: list) -> list:
    results = []
    for contract in contracts:
        contract_id = contract.get("id", "unknown")
        raw_prob = contract.get("probability", 0.5)
        category = contract.get("category", "default")
        yfinance_momentum = contract.get("yfinance_momentum")
        pytrends_interest = contract.get("pytrends_interest")
        newsapi_sentiment = contract.get("newsapi_sentiment")
        statsmodels_trend = contract.get("statsmodels_trend")

        calibrated_prob = calibrate(
            raw_prob=raw_prob,
            contract_id=contract_id,
            category=category,
            yfinance_momentum=yfinance_momentum,
            pytrends_interest=pytrends_interest,
            newsapi_sentiment=newsapi_sentiment,
            statsmodels_trend=statsmodels_trend,
        )

        result = dict(contract)
        result["calibrated_probability"] = calibrated_prob
        result["raw_probability"] = raw_prob
        results.append(result)

    logger.info("BATCH_CALIBRATION_COMPLETE total_contracts=%d", len(results))
    return results