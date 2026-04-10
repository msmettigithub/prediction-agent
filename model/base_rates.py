"""Per-category and per-series base rates.

Each base rate represents: given no other information, what is the prior
probability that a YES contract in this category/series resolves YES?

IMPORTANT: For range-bucket contracts (S&P landing in a 25-point band,
CPI printing in a specific range), the base rate should reflect the
number of buckets, NOT a generic "economics" rate. A 25-point S&P range
bucket among 40+ buckets has a ~2.5% base rate, not 60%.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class BaseRate:
    category: str
    base_rate: float       # prior probability before any contract-specific info
    uncertainty: float     # half-width of 80% CI around base_rate
    source_type: str       # "empirical" or "prior"
    source_note: str       # citation or reasoning


# ── Series-specific base rates ──
# These override the category-level rate when we can identify the contract series.

# S&P 500 intraday range buckets (KXINX): ~40 buckets of 25 points each.
# Uniform prior = 1/40 = 2.5%. In practice distribution is peaked around
# current level, but at listing time we don't know where that is.
SERIES_INX_RANGE = BaseRate(
    category="economics",
    base_rate=0.025,
    uncertainty=0.02,
    source_type="prior",
    source_note="S&P 500 intraday 25pt range bucket. ~40 buckets → uniform prior ~2.5%. "
                "Actual distribution is peaked but we don't encode current level here.",
)

# S&P 500 threshold contracts (above/below X): depends on distance from current.
# At listing, market price is the best prior. Use 0.50 (maximum uncertainty).
SERIES_INX_THRESHOLD = BaseRate(
    category="economics",
    base_rate=0.50,
    uncertainty=0.20,
    source_type="prior",
    source_note="S&P 500 above/below threshold. Distance from current level drives this, "
                "which changes by the minute. Market price is the only reasonable prior.",
)

# CPI monthly change thresholds (KXCPI): "Will CPI rise more than X%?"
# Historical MoM CPI: mean ~0.2-0.3%, std ~0.2%. Distribution is roughly normal.
# Threshold contracts at different strikes have very different base rates.
SERIES_CPI = BaseRate(
    category="economics",
    base_rate=0.50,
    uncertainty=0.15,
    source_type="empirical",
    source_note="CPI MoM threshold. Historical mean ~0.25%, std ~0.2%. Base rate varies by "
                "strike — use market price as prior, only deviate with real CPI data signal.",
)

# GDP quarterly growth thresholds (KXGDP): "Will real GDP increase by more than X%?"
# Historical quarterly GDP: mean ~2.0% annualized, std ~2.0%.
SERIES_GDP = BaseRate(
    category="economics",
    base_rate=0.50,
    uncertainty=0.15,
    source_type="empirical",
    source_note="GDP quarterly threshold. Historical mean ~2% annualized, high variance. "
                "Use market price as prior, only deviate with real GDP nowcast signal.",
)

# BTC/ETH daily range (KXBTCD, KXETH)
SERIES_CRYPTO_RANGE = BaseRate(
    category="crypto",
    base_rate=0.025,
    uncertainty=0.02,
    source_type="prior",
    source_note="Crypto daily range bucket. Similar to S&P range — many buckets, "
                "uniform prior across them.",
)

# Sports (KXNHL etc.)
SERIES_SPORTS = BaseRate(
    category="sports",
    base_rate=0.50,
    uncertainty=0.15,
    source_type="prior",
    source_note="Sports outcome. Market price is the best available prior.",
)


# ── Category-level fallbacks ──

POLITICS = BaseRate(
    category="politics",
    base_rate=0.50,
    uncertainty=0.15,
    source_type="prior",
    source_note="Political prediction market. Market price is the best prior.",
)

ECONOMICS = BaseRate(
    category="economics",
    base_rate=0.50,
    uncertainty=0.15,
    source_type="prior",
    source_note="Generic economics contract. Market price is the best prior. "
                "Series-specific rates should be used when possible.",
)

CRYPTO = BaseRate(
    category="crypto",
    base_rate=0.50,
    uncertainty=0.20,
    source_type="prior",
    source_note="Crypto contract. High uncertainty, market price is the best prior.",
)

SPORTS = BaseRate(
    category="sports",
    base_rate=0.50,
    uncertainty=0.15,
    source_type="prior",
    source_note="Sports contract. Market price is the best prior.",
)

SCIENCE = BaseRate(
    category="science",
    base_rate=0.50,
    uncertainty=0.15,
    source_type="prior",
    source_note="Science/tech contract. Market price is the best prior.",
)

LEGAL = BaseRate(
    category="legal",
    base_rate=0.50,
    uncertainty=0.18,
    source_type="prior",
    source_note="Legal/regulatory contract. High uncertainty.",
)

BASE_RATES: dict[str, BaseRate] = {
    "politics": POLITICS,
    "economics": ECONOMICS,
    "crypto": CRYPTO,
    "sports": SPORTS,
    "science": SCIENCE,
    "legal": LEGAL,
}

DEFAULT_BASE_RATE = BaseRate(
    category="unknown",
    base_rate=0.50,
    uncertainty=0.20,
    source_type="prior",
    source_note="Default uninformative prior for unknown category.",
)

# Series prefix → specific base rate
_SERIES_MAP: dict[str, BaseRate] = {
    "KXCPI": SERIES_CPI,
    "KXGDP": SERIES_GDP,
    "KXNHL": SERIES_SPORTS,
}


def _classify_inx(source_id: str) -> BaseRate:
    """Classify KXINX contracts as range-bucket vs threshold."""
    if not source_id:
        return SERIES_INX_THRESHOLD
    # Range buckets have B (between) in the suffix: KXINX-...-B6612
    if "-B" in source_id:
        return SERIES_INX_RANGE
    # Threshold contracts have T (above/below): KXINX-...-T6225
    return SERIES_INX_THRESHOLD


def get_base_rate(category: str, source_id: str = "") -> BaseRate:
    """Get the most specific base rate available for this contract.

    Checks series prefix first, falls back to category.
    """
    if source_id:
        # Check KXINX specially (range vs threshold)
        if source_id.startswith("KXINX"):
            return _classify_inx(source_id)
        # Check crypto range buckets
        if source_id.startswith(("KXBTCD", "KXETH")):
            # Range buckets have -B in suffix
            if "-B" in source_id:
                return SERIES_CRYPTO_RANGE
        # Check other known series
        for prefix, rate in _SERIES_MAP.items():
            if source_id.startswith(prefix):
                return rate
    return BASE_RATES.get(category.lower(), DEFAULT_BASE_RATE)
