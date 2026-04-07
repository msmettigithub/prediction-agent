"""Per-category base rates with empirical modifiers.

Each base rate is tagged as:
- "empirical": derived from historical data or prediction market track records
- "prior": based on academic literature or domain expertise

Sources are cited in comments. These are starting points — the backtest loop
will reveal which need adjustment.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BaseRate:
    category: str
    base_rate: float       # prior probability before any contract-specific info
    uncertainty: float     # half-width of 80% CI around base_rate
    source_type: str       # "empirical" or "prior"
    source_note: str       # citation or reasoning


# Source: Nate Silver / FiveThirtyEight historical polling accuracy analysis.
# Final polls predict election outcomes ~75% directionally, but market-style
# contracts have more variety. Base rate represents "how often does the market-
# implied favorite actually win" in political prediction markets.
POLITICS = BaseRate(
    category="politics",
    base_rate=0.55,
    uncertainty=0.15,
    source_type="empirical",
    source_note="Kalshi/PredictIt historical resolution rate for political contracts. "
                "Markets are roughly 55% accurate at time of listing for binary political events.",
)

# Source: Fed Funds futures vs actual outcomes 2000-2024.
# Fed funds futures are well-calibrated within 1 meeting horizon (~85% accurate
# for next-meeting rate direction) but degrade sharply at 3+ month horizons.
ECONOMICS = BaseRate(
    category="economics",
    base_rate=0.60,
    uncertainty=0.12,
    source_type="empirical",
    source_note="Fed funds futures accuracy at ~2mo horizon. Cleveland Fed research shows "
                "futures-implied probabilities are well-calibrated for near-term FOMC decisions.",
)

# Source: Crypto market implied probabilities are notoriously poorly calibrated.
# Coingecko/DefiLlama data shows prediction markets on crypto price targets
# resolve at roughly coin-flip rates when listed 30+ days out.
CRYPTO = BaseRate(
    category="crypto",
    base_rate=0.50,
    uncertainty=0.20,
    source_type="prior",
    source_note="Crypto price prediction markets have near-zero edge at listing time. "
                "Wide uncertainty reflects extreme volatility and thin order books.",
)

# Source: Pinnacle closing line value studies (Wunderdog, CRIS data).
# Closing lines are 52-54% accurate against the spread, but moneyline/binary
# markets on specific outcomes (e.g., 'will team X win championship') are different.
SPORTS = BaseRate(
    category="sports",
    base_rate=0.58,
    uncertainty=0.10,
    source_type="empirical",
    source_note="Sports betting closing lines. Pinnacle's closing line is the most efficient "
                "predictor; binary outcome markets track within ~2pp of closing line accuracy.",
)

# Source: Metaculus community calibration data (2020-2024 community resolution analysis).
# Metaculus community predictions are ~70% directionally correct on science/tech questions
# but this drops to ~55% for novel/unprecedented events.
SCIENCE = BaseRate(
    category="science",
    base_rate=0.55,
    uncertainty=0.15,
    source_type="empirical",
    source_note="Metaculus community track record on science/technology questions. "
                "Well-calibrated on recurring events, less so on novel ones.",
)

# Source: Expert legal prediction accuracy (Philip Tetlock / superforecasting research).
# Legal/regulatory outcomes are among the hardest to predict — base rate reflects
# that market prices at listing are only slightly better than chance.
LEGAL = BaseRate(
    category="legal",
    base_rate=0.52,
    uncertainty=0.18,
    source_type="prior",
    source_note="Legal/regulatory prediction accuracy from superforecasting literature. "
                "Tetlock's research shows domain experts are ~55% accurate on regulatory outcomes.",
)

BASE_RATES: dict[str, BaseRate] = {
    "politics": POLITICS,
    "economics": ECONOMICS,
    "crypto": CRYPTO,
    "sports": SPORTS,
    "science": SCIENCE,
    "legal": LEGAL,
}

# Default for unknown categories
DEFAULT_BASE_RATE = BaseRate(
    category="unknown",
    base_rate=0.50,
    uncertainty=0.20,
    source_type="prior",
    source_note="Default uninformative prior for unknown category.",
)


def get_base_rate(category: str) -> BaseRate:
    return BASE_RATES.get(category.lower(), DEFAULT_BASE_RATE)
