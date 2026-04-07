"""Backtest report: prints accuracy, Brier, calibration, separation stats."""

from __future__ import annotations

from model.calibration import CalibrationReport, compute_calibration
from database.models import Resolution
from config import Config


def print_report(resolutions: list[Resolution], config: Config):
    """Print formatted backtest report with PASS/FAIL indicators."""
    report = compute_calibration(
        resolutions,
        min_resolved=config.min_resolved_for_calibration,
        accuracy_threshold=config.accuracy_threshold,
        brier_threshold=config.brier_threshold,
        separation_threshold=config.separation_threshold,
    )

    print()
    print("=" * 60)
    print("  BACKTEST REPORT")
    print("=" * 60)
    print()
    print(f"  Resolved contracts:  {report.n_resolved}")
    print(f"  Sufficient data:     {'YES' if report.sufficient_data else 'NO (need >= ' + str(config.min_resolved_for_calibration) + ')'}")
    print()

    if not report.sufficient_data:
        print("  WARNING: Insufficient data for reliable calibration metrics.")
        print(f"  Need at least {config.min_resolved_for_calibration} resolved contracts.")
        print(f"  Current: {report.n_resolved}")
        print()

    # Metrics table
    acc_status = _status(report.accuracy_pass)
    bri_status = _status(report.brier_pass)
    sep_status = _status(report.separation_pass)

    print(f"  Metric                Value      Threshold    Status")
    print(f"  {'─' * 52}")
    print(f"  Directional Accuracy  {report.directional_accuracy:>6.1%}     >= {config.accuracy_threshold:.0%}        {acc_status}")
    print(f"  Brier Score           {report.brier_score:>6.4f}     <= {config.brier_threshold:.4f}    {bri_status}")
    print(f"  Separation            {report.separation:>+6.1%}     >= {config.separation_threshold:+.0%}       {sep_status}")
    print()

    # Calibration table (reliability diagram data)
    if report.buckets:
        print("  Calibration Table (Reliability Diagram)")
        print(f"  {'Bucket':>12}  {'Predicted':>10}  {'Actual':>8}  {'Count':>6}  {'Gap':>8}")
        print(f"  {'─' * 50}")
        for b in report.buckets:
            if b.count > 0:
                gap = b.mean_predicted - b.mean_actual
                print(f"  {b.bucket_low:.0%}-{b.bucket_high:.0%}       {b.mean_predicted:>8.1%}    {b.mean_actual:>6.1%}  {b.count:>6}  {gap:>+7.1%}")
        print()

    # Overall verdict
    if report.overall_pass:
        print("  ╔══════════════════════════════════════════╗")
        print("  ║  OVERALL: PASS — Model meets thresholds  ║")
        print("  ╚══════════════════════════════════════════╝")
    else:
        print("  ╔══════════════════════════════════════════════╗")
        print("  ║  OVERALL: FAIL — Model does not meet all    ║")
        print("  ║  thresholds. DO NOT rely on edge signals.   ║")
        print("  ╚══════════════════════════════════════════════╝")

    print()
    return report


def get_calibration_warning(config: Config, db) -> str | None:
    """Returns a warning string if backtest hasn't been run or failed thresholds.
    Used by scanner to print calibration gate banner."""
    resolutions = db.get_all_resolutions()
    if not resolutions:
        return (
            "WARNING: No backtest has been run. Model predictions are UNCALIBRATED.\n"
            "Run `python main.py backtest` before trusting edge signals."
        )

    report = compute_calibration(
        resolutions,
        min_resolved=config.min_resolved_for_calibration,
        accuracy_threshold=config.accuracy_threshold,
        brier_threshold=config.brier_threshold,
        separation_threshold=config.separation_threshold,
    )

    if not report.sufficient_data:
        return (
            f"WARNING: Only {report.n_resolved} resolved contracts (need {config.min_resolved_for_calibration}).\n"
            "Calibration metrics are unreliable. Proceed with caution."
        )

    if not report.overall_pass:
        failures = []
        if not report.accuracy_pass:
            failures.append(f"accuracy {report.directional_accuracy:.1%} < {config.accuracy_threshold:.0%}")
        if not report.brier_pass:
            failures.append(f"Brier {report.brier_score:.4f} > {config.brier_threshold}")
        if not report.separation_pass:
            failures.append(f"separation {report.separation:+.1%} < {config.separation_threshold:+.0%}")
        return (
            f"WARNING: Backtest FAILED thresholds: {', '.join(failures)}.\n"
            "Edge signals may not be reliable."
        )

    return None


def _status(passed: bool) -> str:
    return "PASS" if passed else "FAIL"
