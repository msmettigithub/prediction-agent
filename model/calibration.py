"""Calibration metrics: Brier score, reliability diagrams, separation.

All metrics gate on minimum 30 resolved contracts before reporting,
per DECISIONS.md. Below this threshold, the statistics are unreliable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from database.models import Resolution


@dataclass
class CalibrationBucket:
    """One decile bucket for reliability diagram."""
    bucket_low: float
    bucket_high: float
    mean_predicted: float
    mean_actual: float
    count: int


@dataclass
class CalibrationReport:
    n_resolved: int
    sufficient_data: bool         # True if n_resolved >= threshold
    directional_accuracy: float   # fraction of correct direction calls
    brier_score: float            # mean (predicted - outcome)^2
    separation: float             # mean_prob_correct - mean_prob_incorrect (pp)
    buckets: list[CalibrationBucket] = field(default_factory=list)

    # Pass/fail thresholds
    accuracy_pass: bool = False
    brier_pass: bool = False
    separation_pass: bool = False
    overall_pass: bool = False


def compute_calibration(
    resolutions: list[Resolution],
    min_resolved: int = 30,
    accuracy_threshold: float = 0.65,
    brier_threshold: float = 0.25,
    separation_threshold: float = 0.10,
) -> CalibrationReport:
    """Compute all calibration metrics from resolution records."""
    n = len(resolutions)
    if n == 0:
        return CalibrationReport(
            n_resolved=0, sufficient_data=False,
            directional_accuracy=0, brier_score=1.0, separation=0,
        )

    # Directional accuracy: did we predict >0.5 when outcome was YES, <0.5 when NO?
    correct = sum(1 for r in resolutions if r.correct_direction)
    accuracy = correct / n

    # Brier score: mean of (predicted - outcome)^2
    brier = sum(r.brier_component for r in resolutions) / n

    # Separation: mean predicted prob for correct predictions - mean for incorrect
    correct_probs = [r.model_prob for r in resolutions if r.correct_direction]
    incorrect_probs = [r.model_prob for r in resolutions if not r.correct_direction]

    mean_correct = _mean(correct_probs) if correct_probs else 0.5
    mean_incorrect = _mean(incorrect_probs) if incorrect_probs else 0.5

    # Separation is the gap between confidence-when-right and confidence-when-wrong.
    # For correct YES predictions, high prob is good. For correct NO, low prob is good.
    # We use |prob - 0.5| as confidence measure for both directions.
    confidence_when_correct = _mean([abs(r.model_prob - 0.5) for r in resolutions if r.correct_direction]) if correct_probs else 0
    confidence_when_incorrect = _mean([abs(r.model_prob - 0.5) for r in resolutions if not r.correct_direction]) if incorrect_probs else 0
    separation = confidence_when_correct - confidence_when_incorrect

    # Reliability diagram: decile buckets
    buckets = _compute_buckets(resolutions)

    sufficient = n >= min_resolved
    accuracy_pass = accuracy >= accuracy_threshold
    brier_pass = brier <= brier_threshold
    separation_pass = separation >= separation_threshold

    return CalibrationReport(
        n_resolved=n,
        sufficient_data=sufficient,
        directional_accuracy=accuracy,
        brier_score=brier,
        separation=separation,
        buckets=buckets,
        accuracy_pass=accuracy_pass,
        brier_pass=brier_pass,
        separation_pass=separation_pass,
        overall_pass=accuracy_pass and brier_pass and separation_pass and sufficient,
    )


def _compute_buckets(resolutions: list[Resolution], n_buckets: int = 10) -> list[CalibrationBucket]:
    """Group predictions into decile buckets by predicted probability."""
    buckets = []
    bucket_width = 1.0 / n_buckets

    for i in range(n_buckets):
        low = i * bucket_width
        high = (i + 1) * bucket_width
        in_bucket = [r for r in resolutions if low <= r.model_prob < high or (i == n_buckets - 1 and r.model_prob == high)]

        if in_bucket:
            mean_pred = _mean([r.model_prob for r in in_bucket])
            mean_actual = _mean([1.0 if r.resolved_yes else 0.0 for r in in_bucket])
        else:
            mean_pred = (low + high) / 2
            mean_actual = 0.0

        buckets.append(CalibrationBucket(
            bucket_low=low, bucket_high=high,
            mean_predicted=mean_pred, mean_actual=mean_actual,
            count=len(in_bucket),
        ))

    return buckets


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


@dataclass
class PaperTradeCalibration:
    total_trades: int
    resolved_trades: int
    open_trades: int
    win_rate: float
    total_pnl: float
    total_wagered: float
    roi: float
    by_category: dict  # category -> {won, lost, win_rate}
    by_edge_bucket: dict  # bucket -> {won, lost, win_rate, mean_edge}
    mean_edge_winners: float
    mean_edge_losers: float


def paper_trade_calibration(trades: list[dict], contracts_by_id: dict) -> PaperTradeCalibration:
    """Compute paper trade calibration from resolved trades."""
    resolved = [t for t in trades if t["status"] in ("won", "lost")]
    open_t = [t for t in trades if t["status"] == "open"]

    if not trades:
        return PaperTradeCalibration(
            total_trades=0, resolved_trades=0, open_trades=0,
            win_rate=0, total_pnl=0, total_wagered=0, roi=0,
            by_category={}, by_edge_bucket={},
            mean_edge_winners=0, mean_edge_losers=0,
        )

    won = [t for t in resolved if t["status"] == "won"]
    lost = [t for t in resolved if t["status"] == "lost"]
    win_rate = len(won) / len(resolved) if resolved else 0
    total_pnl = sum(t.get("pnl", 0) or 0 for t in resolved)
    total_wagered = sum(t["bet_amount"] for t in trades)

    # By category
    by_category = {}
    for t in resolved:
        contract = contracts_by_id.get(t["contract_id"])
        cat = contract.category if contract else "unknown"
        if cat not in by_category:
            by_category[cat] = {"won": 0, "lost": 0}
        if t["status"] == "won":
            by_category[cat]["won"] += 1
        else:
            by_category[cat]["lost"] += 1
    for cat in by_category:
        d = by_category[cat]
        total = d["won"] + d["lost"]
        d["win_rate"] = d["won"] / total if total else 0

    # By edge bucket
    by_edge = {"<10pp": {"won": 0, "lost": 0, "edges": []},
               "10-20pp": {"won": 0, "lost": 0, "edges": []},
               ">20pp": {"won": 0, "lost": 0, "edges": []}}
    for t in resolved:
        edge = abs(t["model_prob"] - t["entry_price"])
        if edge < 0.10:
            bucket = "<10pp"
        elif edge < 0.20:
            bucket = "10-20pp"
        else:
            bucket = ">20pp"
        by_edge[bucket][t["status"]] += 1
        by_edge[bucket]["edges"].append(edge)

    for bucket in by_edge:
        d = by_edge[bucket]
        total = d["won"] + d["lost"]
        d["win_rate"] = d["won"] / total if total else 0
        d["mean_edge"] = _mean(d["edges"])

    # Mean edge for winners vs losers
    winner_edges = [abs(t["model_prob"] - t["entry_price"]) for t in won]
    loser_edges = [abs(t["model_prob"] - t["entry_price"]) for t in lost]

    return PaperTradeCalibration(
        total_trades=len(trades),
        resolved_trades=len(resolved),
        open_trades=len(open_t),
        win_rate=win_rate,
        total_pnl=total_pnl,
        total_wagered=total_wagered,
        roi=total_pnl / total_wagered if total_wagered else 0,
        by_category=by_category,
        by_edge_bucket=by_edge,
        mean_edge_winners=_mean(winner_edges),
        mean_edge_losers=_mean(loser_edges),
    )


def print_paper_calibration(cal: PaperTradeCalibration):
    """Print paper trading calibration report."""
    print()
    print("=" * 60)
    print("  PAPER TRADING CALIBRATION")
    print("=" * 60)

    if cal.resolved_trades < 5:
        print(f"\n  Insufficient resolved paper trades ({cal.resolved_trades}, need 5+)")
        print(f"  Open trades: {cal.open_trades}")
        print(f"  Total wagered: ${cal.total_wagered:.2f}")
        print()
        return

    print(f"\n  Total: {cal.total_trades}  Resolved: {cal.resolved_trades}  Open: {cal.open_trades}")
    print(f"  Win rate: {cal.win_rate:.0%}  P&L: ${cal.total_pnl:+.2f}  ROI: {cal.roi:+.1%}")
    print()

    # By category
    if cal.by_category:
        print(f"  {'Category':<15} {'Won':>5} {'Lost':>5} {'Win Rate':>9}")
        print(f"  {'─' * 38}")
        for cat, d in sorted(cal.by_category.items()):
            print(f"  {cat:<15} {d['won']:>5} {d['lost']:>5} {d['win_rate']:>8.0%}")
        print()

    # By edge bucket
    print(f"  {'Edge Bucket':<12} {'Won':>5} {'Lost':>5} {'Win Rate':>9} {'Mean Edge':>10}")
    print(f"  {'─' * 46}")
    for bucket in ["<10pp", "10-20pp", ">20pp"]:
        d = cal.by_edge_bucket[bucket]
        total = d["won"] + d["lost"]
        if total > 0:
            print(f"  {bucket:<12} {d['won']:>5} {d['lost']:>5} {d['win_rate']:>8.0%} {d['mean_edge']:>+9.1%}")
    print()

    print(f"  Mean edge (winners): {cal.mean_edge_winners:+.1%}")
    print(f"  Mean edge (losers):  {cal.mean_edge_losers:+.1%}")
    print()
