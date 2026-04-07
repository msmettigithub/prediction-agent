"""Safety guard for live trading.

Multi-layered checks that MUST all pass before any real order is placed.
Each check returns (ok: bool, reason: str). The check_all() function
short-circuits on the first failure.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from config import Config


@dataclass
class GuardResult:
    ok: bool
    reason: str
    deployed: float = 0.0
    remaining_cap: float = 0.0
    available_balance: float = 0.0


class LiveTradingGuard:
    """Hard safety controls for live trading.

    Every check is designed to fail-closed: if anything is uncertain,
    the trade is refused.
    """

    def __init__(self, config: Config, db, balance_provider=None):
        """
        Args:
            config: loaded Config (with live_trading_enabled, max_live_bankroll, max_single_bet)
            db: Database instance for reading total_live_deployed
            balance_provider: callable returning current Kalshi balance in dollars,
                or None to use the cap as a proxy (for testing/dry runs)
        """
        self.config = config
        self.db = db
        self.balance_provider = balance_provider

    # --- Individual checks ---

    def check_enabled(self) -> GuardResult:
        if not self.config.live_trading_enabled:
            return GuardResult(
                ok=False,
                reason="LIVE_TRADING_ENABLED is not true. Set in .env to enable live trading."
            )
        return GuardResult(ok=True, reason="enabled")

    def check_under_cap(self, proposed_cost: float = 0.0) -> GuardResult:
        deployed = self.db.total_live_deployed()
        remaining = self.config.max_live_bankroll - deployed
        if deployed + proposed_cost > self.config.max_live_bankroll:
            return GuardResult(
                ok=False,
                reason=(
                    f"Would exceed MAX_LIVE_BANKROLL: ${deployed:.2f} deployed + "
                    f"${proposed_cost:.2f} proposed > ${self.config.max_live_bankroll:.2f} cap"
                ),
                deployed=deployed,
                remaining_cap=remaining,
            )
        return GuardResult(
            ok=True, reason=f"under cap (${deployed:.2f}/${self.config.max_live_bankroll:.2f})",
            deployed=deployed, remaining_cap=remaining,
        )

    def check_single_bet_size(self, proposed_cost: float) -> GuardResult:
        if proposed_cost > self.config.max_single_bet:
            return GuardResult(
                ok=False,
                reason=(
                    f"Proposed cost ${proposed_cost:.2f} exceeds MAX_SINGLE_BET "
                    f"${self.config.max_single_bet:.2f}"
                ),
            )
        if proposed_cost <= 0:
            return GuardResult(
                ok=False,
                reason=f"Proposed cost ${proposed_cost:.2f} must be > 0",
            )
        return GuardResult(ok=True, reason=f"single bet OK (${proposed_cost:.2f})")

    def check_balance(self, proposed_cost: float = 0.0) -> GuardResult:
        if self.balance_provider is None:
            # Without a provider, fall back to assuming the cap IS the balance
            available = self.config.max_live_bankroll - self.db.total_live_deployed()
        else:
            try:
                available = float(self.balance_provider())
            except Exception as e:
                return GuardResult(
                    ok=False,
                    reason=f"Could not fetch Kalshi balance: {e}",
                )

        if available < proposed_cost:
            return GuardResult(
                ok=False,
                reason=f"Insufficient balance: ${available:.2f} available, ${proposed_cost:.2f} needed",
                available_balance=available,
            )
        return GuardResult(
            ok=True, reason=f"balance sufficient (${available:.2f})",
            available_balance=available,
        )

    # --- Combined check ---

    def check_all(self, proposed_cost: float = 0.0) -> GuardResult:
        """Run all checks in order. Returns first failure or final OK with metrics."""
        for check in [
            self.check_enabled,
            lambda: self.check_under_cap(proposed_cost),
            lambda: self.check_single_bet_size(proposed_cost) if proposed_cost > 0 else GuardResult(ok=True, reason="no cost specified"),
            lambda: self.check_balance(proposed_cost),
        ]:
            result = check()
            if not result.ok:
                return result

        # All checks passed — return enriched result with current metrics
        deployed = self.db.total_live_deployed()
        return GuardResult(
            ok=True,
            reason="all guards passed",
            deployed=deployed,
            remaining_cap=self.config.max_live_bankroll - deployed,
            available_balance=self.check_balance(proposed_cost).available_balance,
        )


def compute_shares(max_cost: float, price: float) -> int:
    """Compute number of shares purchasable at price for max_cost.
    Always rounds DOWN — never overspend.

    Args:
        max_cost: maximum dollar amount to spend
        price: per-share price (0-1, dollars)

    Returns:
        integer share count
    """
    if price <= 0 or max_cost <= 0:
        return 0
    return int(math.floor(max_cost / price))
