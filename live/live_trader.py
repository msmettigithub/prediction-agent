"""Live trading orchestrator.

Wires together: scanner → guard → user confirmation → Kalshi order → DB record.

Every step is gated. The user must explicitly type 'confirm' for each
trade. There is no batch-confirmation mode.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from typing import Optional

from config import Config
from database.db import Database
from database.models import Contract
from live.guard import LiveTradingGuard, compute_shares
from live.kalshi_trader import KalshiTrader
from model.probability_model import estimate_probability
from model.edge_calculator import compute_edge
from scanner.scanner import Scanner

logger = logging.getLogger(__name__)


class LiveTrader:
    def __init__(self, db: Database, config: Config, kalshi_trader: Optional[KalshiTrader] = None):
        self.db = db
        self.config = config
        self.kalshi = kalshi_trader or KalshiTrader(config)
        self.guard = LiveTradingGuard(
            config=config,
            db=db,
            balance_provider=self._safe_balance_provider,
        )

    def _safe_balance_provider(self) -> float:
        """Wrapper that catches errors and falls back to cap-based estimate.

        Distinguishes between signer-loading errors (which should be shown
        prominently so the user can fix their .env) and transient API errors
        (which we soft-fail through so the session can still run proposals).
        """
        try:
            return self.kalshi.get_balance()
        except RuntimeError as e:
            # Signer failures are config errors — log loudly at ERROR level
            # so the user sees the real cause.
            logger.error(f"Kalshi signer/config error: {e}")
            logger.error("Falling back to cap-based balance estimate.")
            return self.config.max_live_bankroll - self.db.total_live_deployed()
        except Exception as e:
            # Transient HTTP/network errors — warning is enough
            logger.warning(f"Balance fetch failed (network), using cap as proxy: {e}")
            return self.config.max_live_bankroll - self.db.total_live_deployed()

    # --- Main flow ---

    def run_session(self) -> dict:
        """Run a live trading session: scan, propose, confirm, place.

        Returns: {orders_placed: int, total_deployed: float, remaining_cap: float}
        """
        # Step 1: enabled check
        result = self.guard.check_enabled()
        if not result.ok:
            print(f"\nERROR: {result.reason}")
            print("Exiting without any action.\n")
            sys.exit(1)

        # Step 2: pre-flight pass (no specific cost — just verify cap and balance)
        preflight = self.guard.check_all(proposed_cost=0.0)
        if not preflight.ok:
            print(f"\nERROR: pre-flight check failed: {preflight.reason}")
            sys.exit(1)

        print()
        print("=" * 60)
        print("  LIVE TRADING SESSION")
        print("=" * 60)
        print(f"  Cap:        ${self.config.max_live_bankroll:.2f}")
        print(f"  Deployed:   ${preflight.deployed:.2f}")
        print(f"  Remaining:  ${preflight.remaining_cap:.2f}")
        print(f"  Balance:    ${preflight.available_balance:.2f}")
        print(f"  Max bet:    ${self.config.max_single_bet:.2f}")
        print()

        # Step 3: scan for edges
        print("Scanning markets for edges...")
        scanner = Scanner(self.db, self.config)
        contracts = scanner.run_once()

        # Step 4: compute edges and rank
        candidates = []
        for c in contracts:
            if self.db.has_open_live_trade(c.id):
                continue
            est = estimate_probability(c, modifiers=[], config=self.config, backtest_mode=True)
            er = compute_edge(est, c.yes_price, self.config)
            if er.recommendation in ("BET_YES", "BET_NO"):
                candidates.append((c, est, er))

        candidates.sort(key=lambda x: x[2].abs_edge, reverse=True)
        print(f"Found {len(candidates)} candidates with edge >= {self.config.edge_threshold:.0%}\n")

        # Step 5: propose each, await confirmation
        orders_placed = 0
        for contract, estimate, edge_result in candidates:
            # Re-check cap before proposing — may have changed after prior orders
            cap_check = self.guard.check_under_cap(0.0)
            if not cap_check.ok:
                print(f"\n  Cap reached: {cap_check.reason}")
                break

            remaining = cap_check.remaining_cap
            single_cap = min(self.config.max_single_bet, remaining)
            target_cost = edge_result.kelly_fraction * single_cap
            target_cost = min(target_cost, single_cap)

            shares = compute_shares(target_cost, contract.yes_price)
            if shares == 0:
                continue
            actual_cost = round(shares * contract.yes_price, 2)

            # Check this specific cost against guard
            guard_result = self.guard.check_all(proposed_cost=actual_cost)
            if not guard_result.ok:
                print(f"  SKIP: {contract.title[:50]} — {guard_result.reason}")
                continue

            # Print proposal
            side = "BET_YES" if edge_result.edge > 0 else "BET_NO"
            entry_price = contract.yes_price if edge_result.edge > 0 else (1.0 - contract.yes_price)
            max_payout = round(shares * 1.0, 2)
            net_profit = round(max_payout - actual_cost, 2)

            print()
            print("  LIVE TRADE PROPOSAL")
            print("  " + "─" * 50)
            print(f"  Contract : {contract.title[:60]}")
            print(f"  Ticker   : {contract.source_id}")
            print(f"  Action   : {side}")
            print(f"  Market   : {contract.yes_price:.0%}  |  Model: {estimate.probability:.0%}  |  Edge: {edge_result.edge:+.0%}")
            print(f"  Shares   : {shares} @ ${entry_price:.2f} = ${actual_cost:.2f} cost")
            print(f"  Max win  : ${max_payout:.2f}  |  Net profit if correct: ${net_profit:.2f}")
            print(f"  Deployed : ${guard_result.deployed + actual_cost:.2f} / ${self.config.max_live_bankroll:.2f} cap")
            print()

            # User confirmation
            try:
                response = input("  Type 'confirm' to place order, anything else to skip: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\n  Session cancelled by user.")
                break

            if response != "confirm":
                print("  SKIPPED")
                continue

            # Place the order
            try:
                ticker = contract.source_id
                price_cents = int(round(entry_price * 100))
                order_side = "yes" if side == "BET_YES" else "no"

                order_response = self.kalshi.place_order(
                    ticker=ticker,
                    side=order_side,
                    shares=shares,
                    price_cents=price_cents,
                )
                order_id = order_response.get("order", {}).get("order_id") or order_response.get("order_id")

                # Record in DB
                self.db.insert_live_trade({
                    "contract_id": contract.id,
                    "kalshi_order_id": order_id,
                    "kalshi_ticker": ticker,
                    "side": "YES" if side == "BET_YES" else "NO",
                    "entry_price": entry_price,
                    "shares": shares,
                    "cost": actual_cost,
                    "max_payout": max_payout,
                    "model_prob": estimate.probability,
                    "edge_at_entry": edge_result.edge,
                })
                orders_placed += 1
                print(f"  ✓ ORDER PLACED  id={order_id}")
            except Exception as e:
                print(f"  ✗ ORDER FAILED: {e}")

        # Step 6: summary
        final_deployed = self.db.total_live_deployed()
        print()
        print("=" * 60)
        print("  LIVE SESSION COMPLETE")
        print("=" * 60)
        print(f"  Orders placed : {orders_placed}")
        print(f"  Total deployed: ${final_deployed:.2f} / ${self.config.max_live_bankroll:.2f}")
        print(f"  Remaining cap : ${self.config.max_live_bankroll - final_deployed:.2f}")
        print()

        return {
            "orders_placed": orders_placed,
            "total_deployed": final_deployed,
            "remaining_cap": self.config.max_live_bankroll - final_deployed,
        }

    # --- Resolution ---

    def resolve_open_trades(self) -> dict:
        """Check status of open live trades and settle resolved ones.

        Also updates the contracts table so paper/backtest views see the
        same resolution — a single source of truth.
        """
        open_trades = self.db.get_open_live_trades()
        settled = 0
        total_pnl = 0.0

        for t in open_trades:
            ticker = t["kalshi_ticker"]
            market = self.kalshi.get_market_status(ticker)
            if not market:
                continue

            # Check if settled
            status = market.get("status", "")
            if status not in ("settled", "finalized"):
                continue

            result = market.get("result", "").lower()
            if result not in ("yes", "no"):
                continue  # Malformed response — skip rather than guess

            resolution_yes = (result == "yes")
            won = (t["side"] == "YES" and resolution_yes) or (t["side"] == "NO" and not resolution_yes)
            exit_price = 1.0 if won else 0.0

            if won:
                pnl = round(t["max_payout"] - t["cost"], 2)
            else:
                pnl = round(-t["cost"], 2)

            # Update both the live_trade and the underlying contract record
            self.db.close_live_trade(t["id"], won, exit_price, pnl)
            self.db.update_contract_resolution(t["contract_id"], resolution_yes)
            settled += 1
            total_pnl += pnl
            print(f"  SETTLED: {ticker} ({t['side']}) — {'WON' if won else 'LOST'} ${pnl:+.2f}")

        return {"settled": settled, "total_pnl": total_pnl}

    # --- Status report ---

    def print_status(self):
        """Print live trading scorecard (open + resolved)."""
        all_trades = self.db.get_all_live_trades()

        print()
        print("=" * 60)
        print("  LIVE TRADING SCORECARD")
        print("=" * 60)

        if not all_trades:
            print("\n  No live trades recorded.")
            print(f"  Cap: ${self.config.max_live_bankroll:.2f}  |  Max single bet: ${self.config.max_single_bet:.2f}")
            print()
            return

        open_t = [t for t in all_trades if t["status"] == "open"]
        won_t = [t for t in all_trades if t["status"] == "won"]
        lost_t = [t for t in all_trades if t["status"] == "lost"]
        deployed = self.db.total_live_deployed()
        total_pnl = sum(t.get("pnl", 0) or 0 for t in all_trades)
        total_cost = sum(t["cost"] for t in all_trades)
        win_rate = len(won_t) / (len(won_t) + len(lost_t)) if (won_t or lost_t) else 0

        print(f"  Total trades:    {len(all_trades)}")
        print(f"  Open:            {len(open_t)}")
        print(f"  Won:             {len(won_t)}")
        print(f"  Lost:            {len(lost_t)}")
        print(f"  Win rate:        {win_rate:.0%}" if (won_t or lost_t) else "  Win rate:        —")
        print(f"  Total deployed:  ${deployed:.2f} / ${self.config.max_live_bankroll:.2f} cap")
        print(f"  Total cost:      ${total_cost:.2f}")
        print(f"  Total P&L:       ${total_pnl:+.2f}")
        if total_cost > 0:
            print(f"  ROI:             {total_pnl/total_cost:+.1%}")
        print()

        if open_t:
            print(f"  {'Ticker':<35} {'Side':<5} {'Shares':>7} {'Cost':>8} {'Edge':>7}")
            print(f"  {'─' * 70}")
            for t in open_t:
                tk = (t["kalshi_ticker"][:33] + "..") if len(t["kalshi_ticker"]) > 35 else t["kalshi_ticker"]
                print(f"  {tk:<35} {t['side']:<5} {t['shares']:>7} ${t['cost']:>6.2f} {t['edge_at_entry']:>+6.1%}")
            print()
