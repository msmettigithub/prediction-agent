"""Filter pipeline for market contracts."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from dataclasses import dataclass

from database.models import Contract
from config import Config
from tools.kalshi import SERIES_TO_CATEGORY, derive_category

logger = logging.getLogger(__name__)

SUPPORTED_CATEGORIES = {"politics", "economics", "crypto", "sports", "science", "legal"}


@dataclass
class FilterResult:
    contract: Contract
    passed: bool
    reasons: list  # reasons for failing, empty if passed


def apply_filters(contract: Contract, config: Config) -> FilterResult:
    """Run all filters on a contract. Returns FilterResult with pass/fail and reasons."""
    reasons = []

    # Volume filter
    if contract.volume_24h < config.min_volume_24h:
        reasons.append(f"volume {contract.volume_24h:.0f} < {config.min_volume_24h:.0f}")

    # Close time filter: must be within max_days_to_close
    if contract.close_time:
        days_to_close = (contract.close_time - datetime.utcnow()).days
        if days_to_close > config.max_days_to_close:
            reasons.append(f"closes in {days_to_close}d > {config.max_days_to_close}d")
        if days_to_close < 0:
            reasons.append("already closed")
    else:
        reasons.append("no close_time")

    # Probability range filter: skip extremes
    if contract.yes_price < config.prob_floor:
        reasons.append(f"prob {contract.yes_price:.2f} < {config.prob_floor:.2f}")
    if contract.yes_price > config.prob_ceiling:
        reasons.append(f"prob {contract.yes_price:.2f} > {config.prob_ceiling:.2f}")

    # Category filter — attempt derivation from source_id if category missing/unknown
    category = contract.category
    if not category or category in ("", "unknown", "exotic"):
        derived = derive_category(contract.source_id)
        if derived not in ("unknown", "exotic"):
            contract.category = derived
            category = derived
        else:
            reasons.append(f"unsupported category: {category or 'empty'}")
            return FilterResult(contract=contract, passed=False, reasons=reasons)

    if category.lower() not in SUPPORTED_CATEGORIES:
        reasons.append(f"unsupported category: {category}")

    return FilterResult(contract=contract, passed=len(reasons) == 0, reasons=reasons)


def filter_contracts(contracts: list[Contract], config: Config) -> list[Contract]:
    """Apply all filters and return only passing contracts."""
    results = [apply_filters(c, config) for c in contracts]
    return [r.contract for r in results if r.passed]
