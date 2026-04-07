"""Market scanner — polls Kalshi + Polymarket, applies filters, stores to DB."""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime
from difflib import SequenceMatcher

from config import Config
from database.db import Database
from database.models import Contract
from scanner.filters import filter_contracts
from tools.kalshi import KalshiTool, KNOWN_SERIES, derive_category
from tools.polymarket import PolymarketTool

logger = logging.getLogger(__name__)


class Scanner:
    def __init__(self, db: Database, config: Config):
        self.db = db
        self.config = config
        self.kalshi = KalshiTool(mock_mode=config.mock_tools)
        self.polymarket = PolymarketTool(mock_mode=config.mock_tools)

    def run_once(self) -> list[Contract]:
        """Single scan pass: fetch markets from known series, filter, store, detect cross-market divergence."""
        contracts = []

        # Fetch from Kalshi — only known series to avoid exotic parlays
        kalshi_count_raw = 0
        if self.config.mock_tools:
            kalshi_result = self.kalshi.run(status="open")
            if kalshi_result["success"]:
                for m in kalshi_result["data"]["markets"]:
                    kalshi_count_raw += 1
                    c = self._market_to_contract(m, "kalshi")
                    contracts.append(c)
            else:
                logger.warning(f"Kalshi fetch failed: {kalshi_result.get('error')}")
        else:
            for series in KNOWN_SERIES:
                try:
                    raw = self.kalshi._fetch(status="open", series_ticker=series, limit=50)
                    parsed = self.kalshi._parse(raw)
                    for m in parsed["markets"]:
                        kalshi_count_raw += 1
                        if m["category"] == "exotic":
                            continue
                        c = self._market_to_contract(m, "kalshi")
                        contracts.append(c)
                except Exception as e:
                    logger.warning(f"Kalshi {series} fetch failed: {e}")

        logger.info(f"Kalshi: {kalshi_count_raw} raw markets fetched, {sum(1 for c in contracts if c.source == 'kalshi')} kept")

        # Fetch from Polymarket
        poly_contracts = []
        poly_result = self.polymarket.run()
        if poly_result["success"]:
            poly_raw = len(poly_result["data"]["markets"])
            for m in poly_result["data"]["markets"]:
                c = Contract(
                    source="polymarket",
                    source_id=m["condition_id"],
                    title=m["question"],
                    category=m.get("category", ""),
                    yes_price=m["yes_price"],
                    volume_24h=m.get("volume", 0),
                    close_time=_parse_dt(m.get("end_date")),
                )
                poly_contracts.append(c)
                contracts.append(c)
            logger.info(f"Polymarket: {poly_raw} markets fetched")
        else:
            logger.warning(f"Polymarket fetch failed: {poly_result.get('error')} — continuing with Kalshi only")

        # Apply filters
        filtered = filter_contracts(contracts, self.config)

        # Store to DB
        stored = []
        for c in filtered:
            c_id = self.db.upsert_contract(c)
            c.id = c_id
            stored.append(c)

        # Cross-market matching
        self._detect_cross_market_divergence(stored)

        logger.info(f"Scanner: {len(contracts)} total, {len(filtered)} passed filters, {len(stored)} stored")
        return stored

    def seed_resolved(self) -> dict:
        """Fetch resolved markets from known series for backtest seeding.
        Returns {total: int, by_category: Counter, skipped: dict}."""
        if self.config.mock_tools:
            # Mock path — use the simple fetch
            result = self.kalshi.run(status="resolved")
            if not result["success"]:
                logger.warning(f"Failed to fetch resolved markets: {result.get('error')}")
                return {"total": 0, "by_category": Counter(), "skipped": {}}
            count = 0
            by_category = Counter()
            for m in result["data"]["markets"]:
                c = self._market_to_contract(m, "kalshi")
                c.resolved = True
                c.resolution = m.get("resolution")
                c.resolved_at = _parse_dt(m.get("resolved_at"))
                self.db.upsert_contract(c)
                count += 1
                by_category[c.category] += 1
            return {"total": count, "by_category": by_category, "skipped": {}}

        # Live path — fetch per known series, filter quality
        data = self.kalshi.fetch_known_series(status="settled", limit_per_series=100)
        by_category = Counter()
        count = 0
        for m in data["markets"]:
            c = self._market_to_contract(m, "kalshi")
            c.resolved = True
            c.resolution = m.get("resolution")
            c.resolved_at = _parse_dt(m.get("resolved_at"))
            self.db.upsert_contract(c)
            count += 1
            by_category[c.category] += 1

        return {"total": count, "by_category": by_category, "skipped": data["skipped"]}

    def _market_to_contract(self, m: dict, source: str) -> Contract:
        """Convert a parsed market dict to a Contract."""
        return Contract(
            source=source,
            source_id=m.get("ticker", m.get("condition_id", "")),
            title=m.get("title", m.get("question", "")),
            category=m.get("category", ""),
            yes_price=m.get("yes_price", 0),
            volume_24h=m.get("volume_24h", m.get("volume", 0)),
            open_time=_parse_dt(m.get("open_time")),
            close_time=_parse_dt(m.get("close_time")),
        )

    def _detect_cross_market_divergence(self, contracts: list[Contract]):
        """Match Kalshi and Polymarket contracts by title similarity.
        Flag pairs with >10pp price divergence as HIGH PRIORITY."""
        kalshi_contracts = [c for c in contracts if c.source == "kalshi"]
        poly_contracts = [c for c in contracts if c.source == "polymarket"]

        for kc in kalshi_contracts:
            best_match = None
            best_ratio = 0.0
            for pc in poly_contracts:
                ratio = SequenceMatcher(None, kc.title.lower(), pc.title.lower()).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_match = pc

            if best_match and best_ratio > 0.5:
                divergence = abs(kc.yes_price - best_match.yes_price)
                cross_id = f"{kc.source_id}:{best_match.source_id}"

                kc.cross_market_id = cross_id
                self.db.upsert_contract(kc)
                best_match.cross_market_id = cross_id
                self.db.upsert_contract(best_match)

                if divergence > self.config.cross_market_divergence_pp:
                    logger.warning(
                        f"HIGH PRIORITY: Cross-market divergence {divergence:.1%} "
                        f"on '{kc.title}' — Kalshi {kc.yes_price:.0%} vs Poly {best_match.yes_price:.0%}"
                    )


def _parse_dt(s) -> datetime | None:
    if not s:
        return None
    if isinstance(s, datetime):
        return s
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, AttributeError):
        return None
