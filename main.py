#!/usr/bin/env python3
"""CLI entrypoint for the prediction market research agent.

Subcommands:
  scan              — Run scanner, print table of top edges sorted by |edge|
  research <id>     — Run deep-dive agent on specific contract
  backtest          — Run backtester, print calibration report
  health            — Run health_check on all tools, show latency table
  seed              — Seed DB with resolved contracts (mock data for testing)
  monitor [mins]    — Run scanner on loop (default 15 min), alert on new edges
  paper             — Paper trading: scan, open trades on edges, settle resolved
  live              — LIVE trading: scan, propose orders with confirmation
  live --resolve    — Resolve open live trades against Kalshi positions
  live --status     — Show all live trades and portfolio scorecard
  kalshi-check      — Doctor: verify Kalshi API key + private key auth flow
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from config import load_config
from database.db import Database

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _compute_paper_pnl(side: str, entry_price: float, bet_amount: float, resolution_yes: bool) -> tuple[bool, float]:
    """Binary payoff PnL for a paper trade.

    Returns (won: bool, pnl: float).

    For a YES bet at entry_price p:
      contracts_bought = bet_amount / p
      if YES: payoff = contracts_bought * $1 → profit = contracts_bought * (1 - p)
      if NO:  payoff = 0                    → profit = -bet_amount

    For a NO bet at YES price p (NO price is 1-p):
      contracts_bought = bet_amount / (1 - p)
      if NO:  payoff = contracts_bought * $1 → profit = contracts_bought * p
      if YES: payoff = 0                    → profit = -bet_amount
    """
    if side == "YES":
        won = resolution_yes
        if won and entry_price > 0:
            contracts_bought = bet_amount / entry_price
            pnl = contracts_bought * (1.0 - entry_price)
        else:
            pnl = -bet_amount
    else:  # NO
        won = not resolution_yes
        no_price = 1.0 - entry_price
        if won and no_price > 0:
            contracts_bought = bet_amount / no_price
            pnl = contracts_bought * entry_price
        else:
            pnl = -bet_amount
    return won, round(pnl, 2)


def cmd_scan(db, config):
    from scanner.scanner import Scanner
    from backtest.report import get_calibration_warning
    from model.probability_model import estimate_probability
    from model.edge_calculator import compute_edge

    # Calibration gate: warn if backtest hasn't passed
    warning = get_calibration_warning(config, db)
    if warning:
        print()
        print("!" * 60)
        print(warning)
        print("!" * 60)
        print()

    scanner = Scanner(db, config)
    contracts = scanner.run_once()

    if not contracts:
        print("No contracts passed filters.")
        return

    # Compute edge for each contract using base-rate blend (no tool data available in scan)
    rows = []
    for c in contracts:
        est = estimate_probability(c, modifiers=[], config=config, backtest_mode=True)
        edge_result = compute_edge(est, c.yes_price, config)
        if edge_result.abs_edge >= config.edge_threshold or edge_result.is_high_priority:
            rows.append((c, est, edge_result))

    rows.sort(key=lambda x: x[2].abs_edge, reverse=True)

    if not rows:
        print(f"No contracts with |edge| >= {config.edge_threshold:.0%}.")
        return

    # Print table
    print()
    print(f"{'ID':>4}  {'Title':<45}  {'Mkt':>5}  {'Model':>5}  {'Edge':>6}  {'Action':<8}  {'Kelly':>6}  {'Bet':>7}")
    print(f"{'─' * 100}")
    for c, est, er in rows:
        title = c.title[:43] + ".." if len(c.title) > 45 else c.title
        priority = " [HIGH]" if er.is_high_priority else ""
        print(
            f"{c.id:>4}  {title:<45}  {c.yes_price:>5.0%}  {est.probability:>5.0%}  "
            f"{er.edge:>+5.0%}  {er.recommendation:<8}  {er.kelly_fraction:>5.1%}  ${er.bet_amount:>6.2f}{priority}"
        )
    print()


def cmd_research(db, config, contract_id_str):
    from tools.tool_registry import ToolRegistry
    from agent.deep_dive import run_deep_dive

    # Try to fetch contract from DB
    try:
        contract_id = int(contract_id_str)
        contract = db.get_contract(contract_id)
    except ValueError:
        contract = db.get_contract_by_source("kalshi", contract_id_str)
        if contract is None:
            contract = db.get_contract_by_source("polymarket", contract_id_str)

    if contract is None:
        # Try to scan and find it
        from scanner.scanner import Scanner
        scanner = Scanner(db, config)
        scanner.run_once()
        try:
            contract = db.get_contract(int(contract_id_str))
        except (ValueError, TypeError):
            contract = db.get_contract_by_source("kalshi", contract_id_str)
        if contract is None:
            print(f"Contract '{contract_id_str}' not found. Run 'scan' first or use a valid ID.")
            return

    registry = ToolRegistry()
    registry.discover(mock_mode=config.mock_tools)

    print(f"\nResearching: {contract.title}")
    print(f"Market: {contract.source} | Price: {contract.yes_price:.0%} | Category: {contract.category}")
    print("─" * 60)
    print("Running deep-dive agent...")
    print()

    result = run_deep_dive(contract, db, config, registry)

    # === Signal Summary ===
    print("=" * 60)
    print("  SIGNAL SUMMARY")
    print("=" * 60)
    print()
    print(f"  {'Metric':<25} {'Value':>15}")
    print(f"  {'─' * 42}")
    print(f"  {'Probability':<25} {result.model_probability:>14.1%}")
    print(f"  {'Market Price':<25} {contract.yes_price:>14.1%}")
    print(f"  {'Edge':<25} {result.edge:>+14.1%}")
    print(f"  {'Confidence':<25} {result.confidence:>15}")
    print(f"  {'Action':<25} {result.recommended_action:>15}")
    print(f"  {'Kelly Fraction':<25} {result.kelly_fraction:>14.1%}")
    print(f"  {'Bet Amount':<25} {'$' + f'{result.kelly_fraction * config.bankroll:.2f}':>14}")
    print()

    # Cross-market divergence flag
    if contract.cross_market_id:
        print("  *** CROSS-MARKET DIVERGENCE DETECTED ***")
        print(f"  Cross-market ID: {contract.cross_market_id}")
        print()

    # === Research Detail ===
    print("=" * 60)
    print("  RESEARCH DETAIL")
    print("=" * 60)
    print()

    print("  Key Factors:")
    for i, f in enumerate(result.key_factors, 1):
        print(f"    {i}. {f}")
    print()

    print(f"  Bull Case: {result.bull_case}")
    print(f"  Bear Case: {result.bear_case}")
    print()

    print(f"  Base Rate Used: {result.base_rate_used:.0%}")
    print()

    if result.modifiers_applied:
        print("  Modifiers Applied:")
        print(f"  {'Name':<25} {'Direction':<15} {'Magnitude':<10} {'Evidence'}")
        print(f"  {'─' * 80}")
        for m in result.modifiers_applied:
            if isinstance(m, dict):
                print(f"  {m.get('name', ''):<25} {m.get('direction', ''):<15} {m.get('magnitude', ''):<10} {m.get('evidence', '')[:40]}")
        print()

    print(f"  Tools Used:   {', '.join(result.tools_used) if result.tools_used else 'none'}")
    print(f"  Tools Failed: {', '.join(result.tools_failed) if result.tools_failed else 'none'}")
    print()

    if result.reasoning_trace:
        print("  Reasoning Trace:")
        # Wrap long text
        trace = result.reasoning_trace
        while trace:
            print(f"    {trace[:76]}")
            trace = trace[76:]
        print()


def cmd_backtest(db, config, diagnostic=False):
    from scanner.scanner import Scanner
    from backtest.backtest import Backtester, print_diagnostic
    from backtest.report import print_report

    # Seed resolved contracts if none exist
    resolved = db.get_resolved_contracts()
    if not resolved:
        print("No resolved contracts found. Seeding from Kalshi (mock mode)...")
        scanner = Scanner(db, config)
        scanner.seed_resolved()

    backtester = Backtester(db, config)

    if diagnostic:
        # Run diagnostic first — may reveal the backtest is meaningless
        resolutions = db.get_all_resolutions()
        if not resolutions:
            resolutions = backtester.run()
        diag = backtester.run_diagnostic()
        print_diagnostic(diag)
        if resolutions:
            print_report(resolutions, config)
        return

    resolutions = backtester.run()
    if not resolutions:
        print("No resolutions generated. Check that resolved contracts have outcome data.")
        return

    print_report(resolutions, config)


def cmd_health(db, config):
    from tools.tool_registry import ToolRegistry

    registry = ToolRegistry()
    registry.discover(mock_mode=config.mock_tools)

    results = registry.health_check_all()

    # Sort: failed tools first, then by name
    results.sort(key=lambda r: (r["healthy"], r["tool"]))

    # Get last successful run times from DB
    last_runs = {}
    try:
        for r in results:
            row = db.conn.execute(
                "SELECT created_at FROM tool_runs WHERE tool_name=? AND success=1 ORDER BY created_at DESC LIMIT 1",
                (r["tool"],),
            ).fetchone()
            last_runs[r["tool"]] = row["created_at"] if row else "never"
    except Exception:
        pass

    print()
    print(f"  {'Tool':<25} {'Status':<8} {'Latency':>8}  {'Last Success':<20}  {'Error'}")
    print(f"  {'─' * 80}")
    for r in results:
        status = "OK" if r["healthy"] else "FAIL"
        latency = f"{r['latency_ms']:.0f}ms" if r["latency_ms"] else "—"
        error = r.get("error", "") or ""
        last = last_runs.get(r["tool"], "—")
        if last and last != "never" and last != "—":
            try:
                dt = datetime.fromisoformat(last)
                last = dt.strftime("%Y-%m-%d %H:%M")
            except Exception:
                pass
        print(f"  {r['tool']:<25} {status:<8} {latency:>8}  {str(last):<20}  {error}")

    total = len(results)
    healthy = sum(1 for r in results if r["healthy"])
    print()
    print(f"  {healthy}/{total} tools healthy")
    print()


def cmd_seed(db, config):
    from scanner.scanner import Scanner
    from backtest.backtest import Backtester
    from backtest.report import print_report

    # Clear previous seeded resolved contracts and their resolutions
    db.conn.execute("DELETE FROM resolutions")
    db.conn.execute("DELETE FROM contracts WHERE resolved=1")
    db.conn.commit()
    print("Cleared previous seeded data.")

    scanner = Scanner(db, config)
    result = scanner.seed_resolved()

    total = result["total"]
    by_cat = result["by_category"]
    skipped = result.get("skipped", {})

    print(f"\nSeeded {total} resolved contracts:")
    for cat, count in sorted(by_cat.items(), key=lambda x: -x[1]):
        print(f"  {cat:<15} {count:>4}")

    if skipped:
        skip_total = sum(skipped.values())
        if skip_total:
            print(f"\nSkipped {skip_total} contracts:")
            for reason, count in skipped.items():
                if count:
                    print(f"  {reason:<30} {count:>4}")

    if total == 0:
        print("\nNo contracts seeded. Check API connectivity.")
        return

    # Automatically run backtest after seeding
    print("\nRunning backtest on seeded data...")
    backtester = Backtester(db, config)
    resolutions = backtester.run()
    if resolutions:
        print_report(resolutions, config)
    else:
        print("No resolutions generated from seeded data.")


def cmd_monitor(db, config, interval_mins=15):
    from scanner.scanner import Scanner
    from backtest.report import get_calibration_warning
    from model.probability_model import estimate_probability
    from model.edge_calculator import compute_edge

    print(f"Monitor mode: scanning every {interval_mins} minutes. Press Ctrl+C to stop.")

    warning = get_calibration_warning(config, db)
    if warning:
        print()
        print("!" * 60)
        print(warning)
        print("!" * 60)

    scanner = Scanner(db, config)
    scan_count = 0

    try:
        while True:
            scan_count += 1
            now = datetime.utcnow().strftime("%H:%M:%S")
            print(f"\n[{now}] Scan #{scan_count}...")

            contracts = scanner.run_once()

            # Only consider contracts never alerted before (persisted in DB)
            candidates = db.get_unalerted_edge_candidates()
            candidate_ids = {c.id for c in candidates}
            new_alerts = []

            for c in contracts:
                if c.id not in candidate_ids:
                    continue
                est = estimate_probability(c, modifiers=[], config=config)
                edge_result = compute_edge(est, c.yes_price, config)

                if edge_result.abs_edge >= config.edge_threshold or edge_result.is_high_priority:
                    new_alerts.append((c, est, edge_result))
                    db.set_alerted(c.id)

            if new_alerts:
                print(f"\n  *** {len(new_alerts)} NEW ALERT(S) ***")
                for c, est, er in new_alerts:
                    priority = " [HIGH PRIORITY]" if er.is_high_priority else ""
                    print(f"  {c.title[:50]}")
                    print(f"    Market: {c.yes_price:.0%}  Model: {est.probability:.0%}  Edge: {er.edge:+.1%}  Action: {er.recommendation}{priority}")
            else:
                print(f"  No new alerts ({len(contracts)} contracts scanned)")

            time.sleep(interval_mins * 60)

    except KeyboardInterrupt:
        print(f"\nMonitor stopped after {scan_count} scans.")


def cmd_kalshi_check(db, config):
    """Doctor command — verify Kalshi API key + private key + auth flow.

    Runs a series of checks with verbose output so the user can diagnose
    why live trading isn't authenticating without running the full flow.
    """
    import os as _os
    from live.kalshi_signer import KalshiSigner
    from live.kalshi_trader import KalshiTrader

    print()
    print("=" * 60)
    print("  KALSHI AUTH CHECK")
    print("=" * 60)

    # Check 1: env vars
    print("\n1. Environment variables")
    api_key = config.kalshi_api_key
    key_path = config.kalshi_private_key_path
    if not api_key:
        print("   [FAIL] KALSHI_API_KEY not set")
    else:
        masked = api_key[:8] + "..." + api_key[-4:] if len(api_key) > 12 else "***"
        print(f"   [ OK ] KALSHI_API_KEY: {masked} (len={len(api_key)})")

    if not key_path:
        print("   [FAIL] KALSHI_PRIVATE_KEY_PATH not set")
        print("\n   Fix: add this to .env:")
        print("     KALSHI_PRIVATE_KEY_PATH=/Users/you/.kalshi/private_key.pem")
        return

    expanded = _os.path.expanduser(key_path)
    print(f"   [ OK ] KALSHI_PRIVATE_KEY_PATH: {key_path}")
    if expanded != key_path:
        print(f"          expanded → {expanded}")

    # Check 2: file exists and readable
    print("\n2. Private key file")
    if not _os.path.exists(expanded):
        print(f"   [FAIL] File does not exist: {expanded}")
        print(f"          Absolute: {_os.path.abspath(expanded)}")
        return
    print(f"   [ OK ] File exists")

    if not _os.access(expanded, _os.R_OK):
        print(f"   [FAIL] File not readable (permissions issue)")
        try:
            mode = oct(_os.stat(expanded).st_mode)[-3:]
            print(f"          Current mode: {mode}")
        except Exception:
            pass
        print(f"          Fix: chmod 600 {expanded}")
        return
    print(f"   [ OK ] File readable")

    try:
        size = _os.path.getsize(expanded)
        print(f"   [INFO] File size: {size} bytes")
    except Exception:
        pass

    # Check 3: load as RSA private key
    print("\n3. Key format")
    try:
        signer = KalshiSigner(private_key_path=expanded, api_key=api_key)
        print("   [ OK ] Loaded as RSA private key")
    except Exception as e:
        print(f"   [FAIL] {type(e).__name__}: {e}")
        # Show first few bytes to help diagnose
        try:
            head = open(expanded, "rb").read(40)
            print(f"          First 40 bytes: {head!r}")
            if not head.startswith(b"-----BEGIN"):
                print("          File doesn't start with '-----BEGIN' — not a PEM file")
        except Exception:
            pass
        return

    # Check 4: produce a signed request header (no network call)
    print("\n4. Signature generation")
    try:
        headers = signer.headers_for("GET", "/trade-api/v2/portfolio/balance")
        for k in ("KALSHI-ACCESS-KEY", "KALSHI-ACCESS-TIMESTAMP", "KALSHI-ACCESS-SIGNATURE"):
            v = headers.get(k, "")
            display = (v[:10] + "..." + v[-4:]) if len(v) > 16 else v
            print(f"   [ OK ] {k}: {display}")
    except Exception as e:
        print(f"   [FAIL] Signing error: {type(e).__name__}: {e}")
        return

    # Check 5: actual HTTP call to /portfolio/balance
    print("\n5. Live Kalshi API call (/portfolio/balance)")
    try:
        trader = KalshiTrader(config, signer=signer)
        balance = trader.get_balance()
        print(f"   [ OK ] Balance: ${balance:.2f}")
        print("\n   ALL CHECKS PASSED. Live trading auth is working.")
    except Exception as e:
        msg = str(e)
        print(f"   [FAIL] {type(e).__name__}: {msg[:200]}")
        if "401" in msg:
            print("          401 = Kalshi rejected the signature.")
            print("          Possible causes:")
            print("          - API key UUID doesn't match the private key you downloaded")
            print("          - Private key was regenerated but .env has the old one")
            print("          - Key doesn't have trading scope")
            print("          - Clock drift > 30 seconds")
        elif "404" in msg:
            print("          404 = Endpoint not found. Kalshi may have moved it.")
        print()


def cmd_live(db, config, mode="session"):
    """Live trading command. mode in {'session', 'resolve', 'status'}."""
    from live.live_trader import LiveTrader

    trader = LiveTrader(db, config)

    if mode == "status":
        trader.print_status()
        return

    if mode == "resolve":
        # Resolve mode requires LIVE_TRADING_ENABLED for consistency
        if not config.live_trading_enabled:
            print("\nERROR: LIVE_TRADING_ENABLED is not true. Cannot access live positions.")
            sys.exit(1)
        result = trader.resolve_open_trades()
        print(f"\nResolved {result['settled']} trades. P&L: ${result['total_pnl']:+.2f}")
        trader.print_status()
        return

    # Default: full session
    trader.run_session()


def cmd_calibrate(db, config):
    """Show paper trading calibration report."""
    from model.calibration import paper_trade_calibration, print_paper_calibration

    trades = db.get_all_paper_trades()
    if not trades:
        print("\n  No paper trades in history. Run 'python main.py paper' first.")
        return

    # Build contract lookup
    contracts_by_id = {}
    for t in trades:
        c = db.get_contract(t["contract_id"])
        if c:
            contracts_by_id[t["contract_id"]] = c

    cal = paper_trade_calibration(trades, contracts_by_id)
    print_paper_calibration(cal)


def cmd_paper(db, config, edge_threshold_override=None, auto_mode=False):
    """Paper trading: scan for edges, open paper trades, settle resolved ones."""
    from scanner.scanner import Scanner
    from backtest.report import get_calibration_warning
    from model.probability_model import estimate_probability
    from model.edge_calculator import compute_edge

    if edge_threshold_override is not None:
        object.__setattr__(config, "edge_threshold", edge_threshold_override)

    # Calibration gate (suppress in auto mode for cron quietness)
    if not auto_mode:
        warning = get_calibration_warning(config, db)
        if warning:
            print()
            print("!" * 60)
            print(warning)
            print("!" * 60)
            print()

    # --- Phase 1a: Poll Kalshi for current status of open trades ---
    # Scanner only fetches status=open, so contracts that have since settled would
    # otherwise never appear in our DB as resolved. Hit the public /markets/{ticker}
    # endpoint directly for each open trade so we can detect settlement.
    from tools.kalshi import KalshiTool
    kalshi = KalshiTool(mock_mode=config.mock_tools)
    open_trades = db.get_open_paper_trades()
    newly_resolved = 0
    for trade in open_trades:
        contract = db.get_contract(trade["contract_id"])
        if not contract or contract.resolved:
            continue
        market = kalshi.fetch_single_market(contract.source_id)
        if market and market.get("resolved") and market.get("resolution") is not None:
            if db.update_contract_resolution(contract.id, bool(market["resolution"])):
                newly_resolved += 1
    if newly_resolved:
        print(f"  Detected {newly_resolved} newly-settled contract(s) from Kalshi.")

    # --- Phase 1b: Settle any open trades whose contracts are now resolved ---
    settled_count = 0
    settled_pnl = 0.0
    for trade in db.get_open_paper_trades():
        contract = db.get_contract(trade["contract_id"])
        if not (contract and contract.resolved and contract.resolution is not None):
            continue
        won, pnl = _compute_paper_pnl(
            side=trade["side"],
            entry_price=trade["entry_price"],
            bet_amount=trade["bet_amount"],
            resolution_yes=bool(contract.resolution),
        )
        exit_price = 1.0 if contract.resolution else 0.0
        db.close_paper_trade(trade["id"], won, exit_price, pnl)
        settled_count += 1
        settled_pnl += pnl

    if settled_count:
        print(f"\n  Settled {settled_count} paper trade(s): P&L = ${settled_pnl:+.2f}")

    # --- Phase 2: Scan for new edges and open paper trades ---
    scanner = Scanner(db, config)
    contracts = scanner.run_once()

    # Build cross-market price map for divergence signal
    cross_prices = {}
    for c in contracts:
        if c.cross_market_id:
            if c.cross_market_id not in cross_prices:
                cross_prices[c.cross_market_id] = []
            cross_prices[c.cross_market_id].append(c)

    new_trades = 0
    for c in contracts:
        if db.has_open_paper_trade(c.id):
            continue

        # Use backtest_mode (base rate blend) when no tool modifiers available
        est = estimate_probability(c, modifiers=[], config=config, backtest_mode=True)

        # Cross-market divergence boost: if two markets disagree, use midpoint
        cross_market_div = None
        if c.cross_market_id and c.cross_market_id in cross_prices:
            pair = cross_prices[c.cross_market_id]
            if len(pair) >= 2:
                prices = [p.yes_price for p in pair]
                divergence = abs(max(prices) - min(prices))
                if divergence > config.cross_market_divergence_pp:
                    midpoint = sum(prices) / len(prices)
                    cross_market_div = divergence
                    # Override model probability with cross-market midpoint
                    from model.probability_model import ProbabilityEstimate
                    est = ProbabilityEstimate(
                        probability=max(config.model_prob_floor, min(config.model_prob_ceiling, midpoint)),
                        confidence_interval=(min(prices), max(prices)),
                        confidence="medium",
                        base_rate=est.base_rate,
                        raw_probability=midpoint,
                    )

        edge_result = compute_edge(est, c.yes_price, config, cross_market_divergence=cross_market_div)

        if edge_result.recommendation in ("BET_YES", "BET_NO"):
            side = "YES" if edge_result.recommendation == "BET_YES" else "NO"
            signal = " [CROSS-MKT]" if cross_market_div else ""
            trade = {
                "contract_id": c.id,
                "side": side,
                "entry_price": c.yes_price,
                "model_prob": est.probability,
                "kelly_fraction": edge_result.kelly_fraction,
                "bet_amount": edge_result.bet_amount,
            }
            db.insert_paper_trade(trade)
            new_trades += 1
            print(f"  NEW TRADE: {side} on '{c.title[:45]}' @ {c.yes_price:.0%}  "
                  f"model={est.probability:.0%}  edge={edge_result.edge:+.1%}  bet=${edge_result.bet_amount:.2f}{signal}")
        elif edge_result.recommendation == "WATCH":
            signal = " [CROSS-MKT]" if cross_market_div else ""
            print(f"  WATCH: '{c.title[:50]}' edge={edge_result.edge:+.1%}{signal}")

    if new_trades == 0:
        print(f"\n  No new paper trades (scanned {len(contracts)} contracts, threshold={config.edge_threshold:.0%})")

    # --- Phase 3: Portfolio summary ---
    all_trades = db.get_all_paper_trades()
    if not all_trades:
        print("\n  No paper trades in history.")
        return

    open_t = [t for t in all_trades if t["status"] == "open"]
    won_t = [t for t in all_trades if t["status"] == "won"]
    lost_t = [t for t in all_trades if t["status"] == "lost"]
    total_pnl = sum(t.get("pnl", 0) or 0 for t in all_trades)
    total_bet = sum(t["bet_amount"] for t in all_trades)
    win_rate = len(won_t) / (len(won_t) + len(lost_t)) if (won_t or lost_t) else 0

    print()
    print("=" * 60)
    print("  PAPER TRADING PORTFOLIO")
    print("=" * 60)
    print(f"  Open trades:    {len(open_t)}")
    print(f"  Won:            {len(won_t)}")
    print(f"  Lost:           {len(lost_t)}")
    print(f"  Win rate:       {win_rate:.0%}" if (won_t or lost_t) else "  Win rate:       —")
    print(f"  Total wagered:  ${total_bet:.2f}")
    print(f"  Total P&L:      ${total_pnl:+.2f}")
    print(f"  ROI:            {total_pnl/total_bet:+.1%}" if total_bet > 0 else "  ROI:            —")
    print()

    if open_t:
        print(f"  {'Side':<5} {'Title':<40} {'Entry':>6} {'Model':>6} {'Bet':>8} {'Opened'}")
        print(f"  {'─' * 80}")
        for t in open_t:
            contract = db.get_contract(t["contract_id"])
            title = (contract.title[:38] + "..") if contract and len(contract.title) > 40 else (contract.title if contract else "?")
            print(f"  {t['side']:<5} {title:<40} {t['entry_price']:>5.0%} {t['model_prob']:>5.0%} ${t['bet_amount']:>7.2f} {t['opened_at'][:10]}")
        print()


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1]
    config = load_config()

    # Startup validation: check required keys for commands that need them
    commands_needing_api = {"scan", "seed", "paper", "monitor", "research"}
    if command in commands_needing_api and not config.mock_tools:
        missing = []
        if not config.anthropic_api_key and command == "research":
            missing.append("ANTHROPIC_API_KEY")
        if not config.kalshi_api_key:
            # Kalshi public endpoint works without key, but warn
            pass
        if missing:
            for key in missing:
                print(f"ERROR: {key} not set. Add it to .env file.", file=sys.stderr)
            sys.exit(1)

    # Auto-enable mock mode when no API keys are set,
    # UNLESS the user explicitly set MOCK_TOOLS=false
    explicit_mock = os.environ.get("MOCK_TOOLS", "").lower()
    if explicit_mock != "false" and not any([config.kalshi_api_key, config.polymarket_api_key]):
        object.__setattr__(config, "mock_tools", True)

    db = Database(config.db_path)

    try:
        if command == "scan":
            cmd_scan(db, config)
        elif command == "backtest":
            diagnostic = "--diagnostic" in sys.argv
            cmd_backtest(db, config, diagnostic=diagnostic)
        elif command == "health":
            cmd_health(db, config)
        elif command == "seed":
            cmd_seed(db, config)
        elif command == "research":
            if len(sys.argv) < 3:
                print("Usage: python main.py research <contract_id>")
                sys.exit(1)
            cmd_research(db, config, sys.argv[2])
        elif command == "monitor":
            interval = int(sys.argv[2]) if len(sys.argv) > 2 else 15
            cmd_monitor(db, config, interval)
        elif command == "calibrate":
            cmd_calibrate(db, config)
        elif command == "live":
            mode = "session"
            if "--resolve" in sys.argv:
                mode = "resolve"
            elif "--status" in sys.argv:
                mode = "status"
            cmd_live(db, config, mode=mode)
        elif command == "kalshi-check":
            cmd_kalshi_check(db, config)
        elif command == "paper":
            et_override = None
            for arg in sys.argv[2:]:
                if arg.startswith("--edge-threshold"):
                    if "=" in arg:
                        et_override = float(arg.split("=")[1])
                    else:
                        idx = sys.argv.index(arg)
                        if idx + 1 < len(sys.argv):
                            et_override = float(sys.argv[idx + 1])
            auto_mode = "--auto" in sys.argv
            cmd_paper(db, config, edge_threshold_override=et_override, auto_mode=auto_mode)
        else:
            print(f"Unknown command: {command}")
            print(__doc__)
            sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
