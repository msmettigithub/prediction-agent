"""Backtester: runs model against historical resolved contracts.

No-lookahead rule: only uses data available at contract open_time.
The backtester treats each contract as if we're making a prediction
at the time it was first listed, using only the market price and
category base rate as inputs (no tool data in backtest — tools
would need historical API access which isn't available).
"""

from __future__ import annotations

import logging
import statistics
from datetime import datetime, timedelta

from config import Config
from database.db import Database
from database.models import Contract, Resolution
from model.probability_model import estimate_probability, Modifier, ProbabilityEstimate
from model.edge_calculator import compute_edge

logger = logging.getLogger(__name__)


class Backtester:
    def __init__(self, db: Database, config: Config):
        self.db = db
        self.config = config

    def run(self) -> list[Resolution]:
        """Run model against all resolved contracts, record resolutions."""
        resolved = self.db.get_resolved_contracts()
        if not resolved:
            logger.warning("No resolved contracts in database. Seed data first.")
            return []

        resolutions = []
        for contract in resolved:
            resolution = self._evaluate_contract(contract)
            if resolution:
                self.db.insert_resolution(resolution)
                resolutions.append(resolution)

        logger.info(f"Backtest complete: {len(resolutions)} contracts evaluated")
        return resolutions

    def _evaluate_contract(self, contract: Contract) -> Resolution | None:
        """Evaluate model prediction against actual resolution.

        No lookahead: we use only the market price at listing time (yes_price)
        and category base rate. In production, tools would add modifiers, but
        for backtest we can only use info available at open_time.
        """
        if contract.resolution is None:
            return None

        estimate = estimate_probability(contract, modifiers=[], config=self.config, backtest_mode=True)

        model_prob = estimate.probability
        market_prob = contract.yes_price
        outcome = 1.0 if contract.resolution else 0.0

        # Brier component: (predicted - outcome)^2
        brier = (model_prob - outcome) ** 2

        # Directional accuracy: did we lean the right way?
        if contract.resolution:
            correct = model_prob > 0.5
        else:
            correct = model_prob < 0.5

        return Resolution(
            contract_id=contract.id,
            model_prob=model_prob,
            market_prob=market_prob,
            resolved_yes=contract.resolution,
            brier_component=brier,
            correct_direction=correct,
        )

    def run_diagnostic(self) -> dict:
        """Run a full diagnostic of the seeded data and model behavior.

        Returns a dict with all diagnostic data for printing.
        Flags lookahead bias, skewed distributions, and model-vs-market echo.
        """
        resolved = self.db.get_resolved_contracts()
        if not resolved:
            return {"error": "No resolved contracts in database."}

        resolutions = self.db.get_all_resolutions()
        # Build resolution lookup by contract_id
        res_by_cid = {}
        for r in resolutions:
            res_by_cid[r.contract_id] = r

        # 1. Price distribution
        price_buckets = {}
        for i in range(10):
            price_buckets[i] = {"count": 0, "yes_count": 0}

        for c in resolved:
            bucket = min(int(c.yes_price * 10), 9)
            price_buckets[bucket]["count"] += 1
            if c.resolution:
                price_buckets[bucket]["yes_count"] += 1

        # 2. Model vs market by bucket
        model_vs_market = {}
        for i in range(10):
            model_vs_market[i] = {"market_probs": [], "model_probs": [], "outcomes": []}

        for c in resolved:
            bucket = min(int(c.yes_price * 10), 9)
            r = res_by_cid.get(c.id)
            if r:
                model_vs_market[bucket]["market_probs"].append(r.market_prob)
                model_vs_market[bucket]["model_probs"].append(r.model_prob)
                model_vs_market[bucket]["outcomes"].append(1.0 if r.resolved_yes else 0.0)

        # 3. Price statistics
        prices = [c.yes_price for c in resolved]
        price_stats = {
            "min": min(prices),
            "max": max(prices),
            "mean": sum(prices) / len(prices),
            "median": statistics.median(prices),
        }

        # 4. Lookahead check — flag contracts where open_time to resolved_at < 24h
        lookahead_flags = []
        for c in resolved:
            flag = False
            gap_hours = None
            if c.open_time and c.resolved_at:
                gap = c.resolved_at - c.open_time
                gap_hours = gap.total_seconds() / 3600
                if gap_hours < 24:
                    flag = True
            lookahead_flags.append({
                "contract_id": c.id,
                "ticker": c.source_id,
                "gap_hours": gap_hours,
                "potential_lookahead": flag,
            })

        lookahead_count = sum(1 for f in lookahead_flags if f["potential_lookahead"])
        extreme_count = price_buckets[0]["count"] + price_buckets[9]["count"]

        return {
            "n_contracts": len(resolved),
            "price_buckets": price_buckets,
            "model_vs_market": model_vs_market,
            "price_stats": price_stats,
            "lookahead_count": lookahead_count,
            "lookahead_pct": lookahead_count / len(resolved) if resolved else 0,
            "extreme_count": extreme_count,
            "extreme_pct": extreme_count / len(resolved) if resolved else 0,
            "data_quality_warning": extreme_count / len(resolved) > 0.30 or lookahead_count / len(resolved) > 0.20,
        }


def print_diagnostic(diag: dict):
    """Print the diagnostic report."""
    if "error" in diag:
        print(f"\n  {diag['error']}")
        return

    n = diag["n_contracts"]
    print()
    print("=" * 65)
    print("  BACKTEST DIAGNOSTIC — DATA QUALITY AUDIT")
    print("=" * 65)

    # Data quality warning
    if diag["data_quality_warning"]:
        print()
        print("  ╔═══════════════════════════════════════════════════════════╗")
        print("  ║  WARNING: BACKTEST ACCURACY IS NOT MEANINGFUL            ║")
        print("  ║  Seeded data is skewed toward already-decided contracts. ║")
        print("  ║  Seed mid-range contracts (20-80%) to get real signal.   ║")
        print("  ╚═══════════════════════════════════════════════════════════╝")

    # 1. Price distribution
    print()
    print(f"  1. SEEDED PRICE DISTRIBUTION ({n} contracts)")
    print(f"  {'Bucket':>10}  {'Count':>6}  {'Resolved YES':>13}  {'YES Rate':>9}")
    print(f"  {'─' * 46}")
    buckets = diag["price_buckets"]
    for i in range(10):
        b = buckets[i]
        count = b["count"]
        yes = b["yes_count"]
        rate = f"{yes/count:.0%}" if count > 0 else "—"
        marker = " ◀ EXTREME" if (i == 0 or i == 9) and count > n * 0.15 else ""
        print(f"  {i*10:>3}-{(i+1)*10:>3}%   {count:>5}  {yes:>12}  {rate:>9}{marker}")

    extreme = diag["extreme_count"]
    print(f"\n  Extreme buckets (0-10% + 90-100%): {extreme}/{n} ({diag['extreme_pct']:.0%})")

    # 2. Model vs market
    print()
    print("  2. MODEL vs MARKET by BUCKET")
    print(f"  {'Bucket':>10}  {'Mkt Mean':>9}  {'Model Mean':>11}  {'Actual':>7}  {'Mdl-Mkt':>8}  {'N':>4}")
    print(f"  {'─' * 58}")
    mvm = diag["model_vs_market"]
    for i in range(10):
        d = mvm[i]
        if d["market_probs"]:
            mkt = sum(d["market_probs"]) / len(d["market_probs"])
            mdl = sum(d["model_probs"]) / len(d["model_probs"])
            act = sum(d["outcomes"]) / len(d["outcomes"])
            diff = mdl - mkt
            echo = " (echo)" if abs(diff) < 0.02 else ""
            print(f"  {i*10:>3}-{(i+1)*10:>3}%   {mkt:>8.1%}  {mdl:>10.1%}  {act:>6.0%}  {diff:>+7.1%}  {len(d['market_probs']):>4}{echo}")

    # 3. Price stats
    ps = diag["price_stats"]
    print()
    print("  3. SEEDED PRICE STATISTICS")
    print(f"     Min: {ps['min']:.3f}   Max: {ps['max']:.3f}   Mean: {ps['mean']:.3f}   Median: {ps['median']:.3f}")
    if ps["median"] < 0.10:
        print("     ⚠ Median below 10% — dataset heavily skewed toward NO outcomes")
    elif ps["median"] > 0.90:
        print("     ⚠ Median above 90% — dataset heavily skewed toward YES outcomes")

    # 4. Lookahead
    la = diag["lookahead_count"]
    print()
    print("  4. POTENTIAL LOOKAHEAD (open-to-resolution < 24 hours)")
    print(f"     Flagged: {la}/{n} ({diag['lookahead_pct']:.0%})")
    if diag["lookahead_pct"] > 0.20:
        print("     ⚠ >20% of contracts resolved within 24h of opening.")
        print("       These were near-certain by the time they were seeded.")

    print()
