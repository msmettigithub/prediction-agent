"""Drawdown circuit breaker — portfolio-level risk checks before any trade.

Checks daily loss, total exposure, and concentration limits.
Returns (ok, reason) tuples; first failure blocks the trade.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

import requests

from config import Config

logger = logging.getLogger(__name__)

# Defaults
MAX_DAILY_LOSS = 100.0       # $100/day
MAX_EXPOSURE_PCT = 0.80      # 80% of balance
MAX_CONCENTRATION_PCT = 0.40 # 40% in any single series


class RiskManager:
    def __init__(self, config: Config, trader, db):
        self.config = config
        self.trader = trader
        self.db = db

    def _fetch_positions(self) -> list[dict]:
        """Fetch all open positions from Kalshi."""
        url = f"{self.trader.TRADING_URL}/portfolio/positions"
        try:
            resp = requests.get(url, headers=self.trader._signed_headers('GET', url),
                                params={'limit': 200}, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            return data.get('market_positions', []) or data.get('positions', [])
        except Exception as e:
            logger.warning(f"Failed to fetch positions: {e}")
            return []

    def _fetch_settlements_today(self) -> float:
        """Fetch today's realized P&L from Kalshi settlements."""
        url = f"{self.trader.TRADING_URL}/portfolio/settlements"
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        try:
            resp = requests.get(url, headers=self.trader._signed_headers('GET', url),
                                params={'limit': 200}, timeout=10)
            resp.raise_for_status()
            settlements = resp.json().get('settlements', [])
            total_pnl = 0.0
            for s in settlements:
                settled_str = s.get('settled_time', '') or s.get('created_time', '')
                try:
                    settled = datetime.fromisoformat(settled_str.replace('Z', '+00:00'))
                except (ValueError, AttributeError):
                    continue
                if settled >= today:
                    # revenue - cost = pnl (values in cents)
                    revenue = float(s.get('revenue', 0)) / 100.0
                    cost = float(s.get('cost', 0)) / 100.0
                    total_pnl += (revenue - cost)
            return total_pnl
        except Exception as e:
            logger.warning(f"Failed to fetch settlements: {e}")
            # Also check local DB as fallback
            return self._local_daily_pnl()

    def _local_daily_pnl(self) -> float:
        """Fallback: sum pnl from live_trades closed today."""
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        try:
            row = self.db.conn.execute(
                "SELECT COALESCE(SUM(pnl), 0) AS total FROM live_trades WHERE closed_at >= ?",
                (today,)
            ).fetchone()
            return float(row['total']) if row else 0.0
        except Exception:
            return 0.0

    def check_daily_loss(self) -> tuple[bool, str]:
        """Check if daily realized losses exceed limit."""
        daily_pnl = self._fetch_settlements_today()
        limit = MAX_DAILY_LOSS
        if daily_pnl < -limit:
            return False, f"Daily loss ${daily_pnl:+.2f} exceeds -${limit:.0f} limit"
        return True, f"daily_pnl=${daily_pnl:+.2f}"

    def check_portfolio_exposure(self) -> tuple[bool, str]:
        """Check if total exposure exceeds limit."""
        try:
            balance = self.trader.get_balance()
        except Exception as e:
            return False, f"Cannot fetch balance: {e}"

        if balance <= 0:
            return False, "Zero or negative balance"

        positions = self._fetch_positions()
        total_exposure = 0.0
        for p in positions:
            # market_exposure is in cents
            exposure = abs(float(p.get('market_exposure', 0))) / 100.0
            total_exposure += exposure

        max_allowed = balance * MAX_EXPOSURE_PCT
        if total_exposure > max_allowed:
            return False, (f"Exposure ${total_exposure:.2f} exceeds "
                           f"{MAX_EXPOSURE_PCT:.0%} of balance (${max_allowed:.2f})")
        return True, f"exposure=${total_exposure:.2f}/{max_allowed:.2f}"

    def check_concentration(self) -> tuple[bool, str]:
        """Check if any single series has >40% of total exposure."""
        positions = self._fetch_positions()
        if not positions:
            return True, "no positions"

        series_exposure: dict[str, float] = {}
        total_exposure = 0.0
        for p in positions:
            exposure = abs(float(p.get('market_exposure', 0))) / 100.0
            total_exposure += exposure
            ticker = p.get('ticker', '')
            # Extract series prefix: KXWTI-26APR-T62.5 -> KXWTI
            parts = ticker.split('-')
            series = parts[0] if parts else ticker
            series_exposure[series] = series_exposure.get(series, 0.0) + exposure

        if total_exposure <= 0:
            return True, "no exposure"

        for series, exp in series_exposure.items():
            pct = exp / total_exposure
            if pct > MAX_CONCENTRATION_PCT:
                return False, (f"Series {series} is {pct:.0%} of exposure "
                               f"(${exp:.2f}/${total_exposure:.2f}), limit {MAX_CONCENTRATION_PCT:.0%}")
        return True, "concentration OK"

    def check_all(self) -> tuple[bool, str]:
        """Run all risk checks. Returns (ok, first_failure_reason)."""
        for check in [self.check_daily_loss, self.check_portfolio_exposure, self.check_concentration]:
            ok, reason = check()
            if not ok:
                return False, reason
        return True, "all risk checks passed"
