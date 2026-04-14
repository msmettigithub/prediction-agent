import logging
import math

logger = logging.getLogger(__name__)

BASE_RATES = {
    "politics": 0.52,
    "economics": 0.48,
    "tech": 0.55,
    "sports": 0.50,
    "science": 0.50,
    "finance": 0.48,
    "health": 0.51,
    "environment": 0.49,
    "entertainment": 0.50,
    "military": 0.51,
}

STRONG_CATEGORIES = {"politics", "economics", "tech"}
N_PRIOR_STRONG = 20
N_PRIOR_WEAK = 10


def sharpen(p: float, k: float = 1.5) -> float:
    p = float(p)
    p = max(1e-9, min(1 - 1e-9, p))
    pk = p ** k
    qk = (1.0 - p) ** k
    denom = pk + qk
    if denom == 0 or math.isnan(denom) or math.isinf(denom):
        return p
    result = pk / denom
    if math.isnan(result) or math.isinf(result):
        return p
    return float(result)


def get_base_rate(category: str) -> float:
    if category is None:
        return 0.5
    return BASE_RATES.get(category.lower().strip(), 0.5)


def get_n_prior(category: str) -> int:
    if category is None:
        return N_PRIOR_WEAK
    if category.lower().strip() in STRONG_CATEGORIES:
        return N_PRIOR_STRONG
    return N_PRIOR_WEAK


def bayesian_update(p_sharp: float, category: str) -> float:
    base_rate = get_base_rate(category)
    n_prior = get_n_prior(category)
    n_evidence = 1
    p_final = (n_prior * base_rate + n_evidence * p_sharp) / (n_prior + n_evidence)
    return float(p_final)


def count_feature_agreement(signals: list) -> int:
    if not signals:
        return 0
    positive = sum(1 for s in signals if s > 0.5)
    negative = sum(1 for s in signals if s <= 0.5)
    return max(positive, negative)


def calibrate(
    raw_prob: float,
    category: str = None,
    signals: list = None,
    k: float = 1.5,
) -> float:
    if signals is None:
        signals = []

    raw_prob = float(raw_prob)
    if math.isnan(raw_prob) or math.isinf(raw_prob):
        logger.warning("calibrate received invalid raw_prob=%s, defaulting to 0.5", raw_prob)
        raw_prob = 0.5

    raw_prob = max(0.0, min(1.0, raw_prob))

    agreement_score = count_feature_agreement(signals)
    n_signals = len(signals)
    sharpening_eligible = agreement_score >= 3 if n_signals > 0 else False

    logger.info(
        "pre_sharpen prob=%.4f category=%s signals=%d agreement=%d eligible=%s",
        raw_prob,
        category,
        n_signals,
        agreement_score,
        sharpening_eligible,
    )

    if sharpening_eligible:
        p_sharp = sharpen(raw_prob, k=k)
    else:
        p_sharp = raw_prob

    logger.info(
        "post_sharpen prob=%.4f (delta=%.4f) sharpening_applied=%s",
        p_sharp,
        p_sharp - raw_prob,
        sharpening_eligible,
    )

    p_final = bayesian_update(p_sharp, category)

    p_final = max(0.05, min(0.95, p_final))

    logger.info(
        "final_calibrated prob=%.4f category=%s base_rate=%.4f n_prior=%d",
        p_final,
        category,
        get_base_rate(category),
        get_n_prior(category),
    )

    return p_final


def calibrate_batch(
    predictions: list,
    category: str = None,
    signals: list = None,
    k: float = 1.5,
) -> list:
    return [calibrate(p, category=category, signals=signals, k=k) for p in predictions]