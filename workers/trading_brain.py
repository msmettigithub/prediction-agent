import logging
import math
from model.base_rates import BaseRates

logger = logging.getLogger(__name__)

STRETCH_EXPONENT = 0.65
MIN_SEPARATION = 0.10
CONFIDENCE_DEAD_ZONE_LOW = 0.42
CONFIDENCE_DEAD_ZONE_HIGH = 0.58
CONSENSUS_BOOST = 0.05


def stretch_probability(p: float) -> float:
    deviation = p - 0.5
    if deviation == 0:
        return 0.5
    sign = 1 if deviation > 0 else -1
    stretched = 0.5 + sign * (abs(2 * deviation) ** STRETCH_EXPONENT) * 0.5
    return max(0.01, min(0.99, stretched))


def count_signal_agreement(base_rate_signal: float, trend_signal: float,
                           sentiment_signal: float, market_price_signal: float,
                           direction: str) -> int:
    signals = [base_rate_signal, trend_signal, sentiment_signal, market_price_signal]
    if direction == "yes":
        return sum(1 for s in signals if s is not None and s > 0.5)
    else:
        return sum(1 for s in signals if s is not None and s < 0.5)


def calibrate_probability(raw_prob: float, base_rate_prior: float,
                          trend_signal: float = None,
                          sentiment_signal: float = None,
                          market_price_signal: float = None,
                          market_metadata: dict = None) -> dict:
    market_metadata = market_metadata or {}

    # Stage 1: Bayesian anchor using base rate prior
    if base_rate_prior is not None and 0.0 < base_rate_prior < 1.0:
        prior_weight = 0.3
        evidence_weight = 0.7
        bayesian_updated = prior_weight * base_rate_prior + evidence_weight * raw_prob
    else:
        bayesian_updated = raw_prob

    # Stage 2: Confidence stretching
    stretched = stretch_probability(bayesian_updated)

    # Stage 3: Signal agreement weighting
    direction = "yes" if stretched > 0.5 else "no"
    base_rate_as_signal = base_rate_prior if base_rate_prior is not None else 0.5
    trend_as_signal = trend_signal if trend_signal is not None else 0.5
    sentiment_as_signal = sentiment_signal if sentiment_signal is not None else 0.5
    market_price_as_signal = market_price_signal if market_price_signal is not None else 0.5

    agreements = count_signal_agreement(
        base_rate_as_signal,
        trend_as_signal,
        sentiment_as_signal,
        market_price_as_signal,
        direction
    )

    consensus_applied = False
    if agreements >= 3:
        if direction == "yes":
            stretched = min(0.99, stretched + CONSENSUS_BOOST)
        else:
            stretched = max(0.01, stretched - CONSENSUS_BOOST)
        consensus_applied = True

    # Stage 4: Dead zone handling - push to boundary or reject
    trade_rejected = False
    final_prob = stretched

    if CONFIDENCE_DEAD_ZONE_LOW <= final_prob <= CONFIDENCE_DEAD_ZONE_HIGH:
        mid = 0.5
        dist_to_low = abs(final_prob - CONFIDENCE_DEAD_ZONE_LOW)
        dist_to_high = abs(final_prob - CONFIDENCE_DEAD_ZONE_HIGH)

        # Prefer rejecting over forcing a marginal trade
        trade_rejected = True
        final_prob = stretched  # keep for logging but mark as rejected

    # Stage 5: Gate check - minimum separation of 0.10
    separation = abs(final_prob - 0.5)
    gate_passed = (not trade_rejected) and (separation >= MIN_SEPARATION)

    result = {
        "raw_prob": raw_prob,
        "bayesian_updated": bayesian_updated,
        "stretched_prob": stretched,
        "final_prob": final_prob,
        "signal_agreements": agreements,
        "consensus_applied": consensus_applied,
        "trade_rejected": trade_rejected,
        "gate_passed": gate_passed,
        "separation": separation,
        "direction": direction,
    }

    logger.info(
        "CALIBRATION | market=%s | raw=%.4f | bayesian=%.4f | stretched=%.4f | "
        "agreements=%d | consensus=%s | rejected=%s | gate=%s | separation=%.4f",
        market_metadata.get("title", "unknown"),
        raw_prob,
        bayesian_updated,
        stretched,
        agreements,
        consensus_applied,
        trade_rejected,
        gate_passed,
        separation,
    )

    return result


class TradingBrain:
    def __init__(self):
        self.base_rates = BaseRates()

    def evaluate_trade(self, market_data: dict, raw_prob: float,
                       trend_signal: float = None,
                       sentiment_signal: float = None,
                       market_price_signal: float = None) -> dict:
        category = self.base_rates.classify_market(market_data)
        base_rate_prior = self.base_rates.get_prior(market_data)

        logger.info(
            "TRADE_EVAL START | market=%s | category=%s | base_rate_prior=%.4f | raw_prob=%.4f",
            market_data.get("title", "unknown"),
            category,
            base_rate_prior if base_rate_prior is not None else -1.0,
            raw_prob,
        )

        calibration = calibrate_probability(
            raw_prob=raw_prob,
            base_rate_prior=base_rate_prior,
            trend_signal=trend_signal,
            sentiment_signal=sentiment_signal,
            market_price_signal=market_price_signal,
            market_metadata=market_data,
        )

        recommendation = {
            "should_trade": calibration["gate_passed"],
            "probability": calibration["final_prob"],
            "direction": calibration["direction"],
            "calibration_details": calibration,
            "category": category,
            "base_rate_prior": base_rate_prior,
        }

        logger.info(
            "TRADE_EVAL END | market=%s | should_trade=%s | final_prob=%.4f | direction=%s",
            market_data.get("title", "unknown"),
            recommendation["should_trade"],
            recommendation["probability"],
            recommendation["direction"],
        )

        return recommendation

    def batch_evaluate(self, candidates: list) -> list:
        results = []
        for candidate in candidates:
            market_data = candidate.get("market_data", {})
            raw_prob = candidate.get("raw_prob", 0.5)
            trend_signal = candidate.get("trend_signal")
            sentiment_signal = candidate.get("sentiment_signal")
            market_price_signal = candidate.get("market_price_signal")

            evaluation = self.evaluate_trade(
                market_data=market_data,
                raw_prob=raw_prob,
                trend_signal=trend_signal,
                sentiment_signal=sentiment_signal,
                market_price_signal=market_price_signal,
            )

            results.append({
                "candidate": candidate,
                "evaluation": evaluation,
            })

        tradeable = [r for r in results if r["evaluation"]["should_trade"]]
        rejected = [r for r in results if not r["evaluation"]["should_trade"]]

        logger.info(
            "BATCH_EVAL | total=%d | tradeable=%d | rejected=%d",
            len(results),
            len(tradeable),
            len(rejected),
        )

        return results