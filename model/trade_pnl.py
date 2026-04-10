"""Trade P&L Forecasting — projects profit/loss at every stage.

ENTRY: "If I buy X contracts at Y price, and the contract resolves YES/NO,
       what's my profit after fees? What's my expected value?"
HOLD:  "Given current spot, what's my mark-to-market? Am I up or down vs entry?"
EXIT:  "If I sell now at Z price, what's my actual realized P&L after fees?"

Every number includes Kalshi fees. No hidden costs.
"""
from __future__ import annotations
from dataclasses import dataclass
from tools.market_data import binary_price
import math


def kalshi_fee_per_contract(price: float) -> float:
    """Kalshi taker fee per contract per side. Price in dollars (0-1)."""
    return min(0.07 * price * (1 - price), 0.035)


@dataclass
class EntryForecast:
    """What happens if we enter this trade?"""
    ticker: str
    side: str           # 'yes' or 'no'
    shares: int
    entry_price: float  # per contract
    total_cost: float   # shares * price
    entry_fee: float    # total entry fees

    # If we WIN (contract resolves in our favor)
    payout_if_win: float    # shares * $1
    exit_fee_if_win: float
    profit_if_win: float    # payout - cost - fees
    roi_if_win: float       # profit / cost

    # If we LOSE (contract resolves against us)
    loss_if_lose: float     # -cost - fees (total wipeout)

    # Expected value (probability-weighted)
    win_probability: float  # from vol model
    expected_pnl: float     # prob * win + (1-prob) * loss
    expected_roi: float     # expected_pnl / cost

    # Risk/reward
    risk_reward: float      # profit_if_win / abs(loss_if_lose)
    breakeven_prob: float   # probability needed to break even


@dataclass
class HoldForecast:
    """What's happening with this trade right now?"""
    ticker: str
    side: str
    shares: int
    entry_price: float
    current_fair: float     # vol-model fair value
    current_bid: float      # what we could sell for now

    unrealized_pnl: float   # (current_fair - entry) * shares (theoretical)
    realizable_pnl: float   # (current_bid - entry) * shares - exit_fee (actual if sold)
    pnl_pct: float          # realizable_pnl / cost

    # Updated win probability
    win_probability: float
    expected_pnl: float     # updated EV given current spot


@dataclass
class ExitForecast:
    """What happens if we sell now?"""
    ticker: str
    side: str
    shares: int
    entry_price: float
    exit_price: float       # what we'd sell at

    entry_cost: float
    entry_fee: float
    exit_proceeds: float
    exit_fee: float

    realized_pnl: float     # exit_proceeds - entry_cost - all_fees
    realized_roi: float
    is_profitable: bool


def forecast_entry(
    ticker: str, side: str, shares: int, price: float,
    win_probability: float,
) -> EntryForecast:
    """Forecast P&L before entering a trade."""
    cost = shares * price
    entry_fee = kalshi_fee_per_contract(price) * shares

    # Win: contract pays $1 per share
    payout = shares * 1.0
    exit_fee_win = kalshi_fee_per_contract(1.0 - price) * shares  # fee on exit side
    profit_win = payout - cost - entry_fee - exit_fee_win

    # Lose: contract pays $0, lose entire cost
    loss_lose = -cost - entry_fee

    # Expected value
    ev = win_probability * profit_win + (1 - win_probability) * loss_lose

    # Breakeven probability
    # prob * profit_win + (1-prob) * loss_lose = 0
    # prob = -loss_lose / (profit_win - loss_lose)
    total_range = profit_win - loss_lose
    breakeven = -loss_lose / total_range if total_range > 0 else 1.0

    return EntryForecast(
        ticker=ticker, side=side, shares=shares,
        entry_price=price, total_cost=round(cost, 4),
        entry_fee=round(entry_fee, 4),
        payout_if_win=round(payout, 4),
        exit_fee_if_win=round(exit_fee_win, 4),
        profit_if_win=round(profit_win, 4),
        roi_if_win=round(profit_win / cost, 4) if cost > 0 else 0,
        loss_if_lose=round(loss_lose, 4),
        win_probability=round(win_probability, 4),
        expected_pnl=round(ev, 4),
        expected_roi=round(ev / cost, 4) if cost > 0 else 0,
        risk_reward=round(profit_win / abs(loss_lose), 4) if loss_lose != 0 else 0,
        breakeven_prob=round(breakeven, 4),
    )


def forecast_hold(
    ticker: str, side: str, shares: int, entry_price: float,
    current_fair: float, current_bid: float,
    win_probability: float,
) -> HoldForecast:
    """Forecast P&L while holding a position."""
    cost = shares * entry_price
    entry_fee = kalshi_fee_per_contract(entry_price) * shares

    # Theoretical unrealized (based on fair value)
    if side == 'yes':
        unrealized = (current_fair - entry_price) * shares
    else:
        unrealized = ((1 - current_fair) - (1 - entry_price)) * shares

    # Actual realizable if sold now
    exit_fee = kalshi_fee_per_contract(current_bid) * shares
    if side == 'yes':
        realizable = (current_bid - entry_price) * shares - entry_fee - exit_fee
    else:
        realizable = (current_bid - (1 - entry_price)) * shares - entry_fee - exit_fee

    pnl_pct = realizable / cost if cost > 0 else 0

    # Updated EV
    profit_win = shares - cost - entry_fee - kalshi_fee_per_contract(1.0 - entry_price) * shares
    loss_lose = -cost - entry_fee
    ev = win_probability * profit_win + (1 - win_probability) * loss_lose

    return HoldForecast(
        ticker=ticker, side=side, shares=shares,
        entry_price=entry_price,
        current_fair=round(current_fair, 4),
        current_bid=round(current_bid, 4),
        unrealized_pnl=round(unrealized, 4),
        realizable_pnl=round(realizable, 4),
        pnl_pct=round(pnl_pct, 4),
        win_probability=round(win_probability, 4),
        expected_pnl=round(ev, 4),
    )


def forecast_exit(
    ticker: str, side: str, shares: int,
    entry_price: float, exit_price: float,
) -> ExitForecast:
    """Forecast P&L if we exit at a specific price. THIS IS THE CHECK
    THAT MUST HAPPEN BEFORE EVERY SELL ORDER."""
    entry_cost = shares * entry_price
    entry_fee = kalshi_fee_per_contract(entry_price) * shares

    exit_proceeds = shares * exit_price
    exit_fee = kalshi_fee_per_contract(exit_price) * shares

    realized = exit_proceeds - entry_cost - entry_fee - exit_fee
    roi = realized / entry_cost if entry_cost > 0 else 0

    return ExitForecast(
        ticker=ticker, side=side, shares=shares,
        entry_price=entry_price, exit_price=exit_price,
        entry_cost=round(entry_cost, 4),
        entry_fee=round(entry_fee, 4),
        exit_proceeds=round(exit_proceeds, 4),
        exit_fee=round(exit_fee, 4),
        realized_pnl=round(realized, 4),
        realized_roi=round(roi, 4),
        is_profitable=realized > 0,
    )


def format_entry_forecast(f: EntryForecast) -> str:
    return (f"{f.side.upper()} {f.ticker} {f.shares}sh @${f.entry_price:.2f} "
            f"cost=${f.total_cost:.2f}+${f.entry_fee:.2f}fee | "
            f"WIN: +${f.profit_if_win:.2f} ({f.roi_if_win:+.0%}) | "
            f"LOSE: ${f.loss_if_lose:.2f} | "
            f"EV: ${f.expected_pnl:+.2f} ({f.expected_roi:+.0%}) "
            f"win_prob={f.win_probability:.0%} breakeven={f.breakeven_prob:.0%} "
            f"r:r={f.risk_reward:.1f}")
