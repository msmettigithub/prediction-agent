"""Generate probability modifiers from REAL data sources.

This replaces the old approach of blending a flat base rate. Every modifier
here is backed by an actual data feed — FRED, Yahoo Finance, Kalshi strips,
BLS releases. If no data is available, no modifier is generated, and the
model stays at market price (correct behavior: no signal = no edge).

Each modifier has:
  - direction: positive = toward YES, negative = toward NO
  - weight: 0-1, how much to trust this signal
  - source: which data feed produced it
"""

from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timezone
from typing import Optional

from model.probability_model import Modifier

logger = logging.getLogger(__name__)


def get_modifiers_for_contract(
    source_id: str,
    category: str,
    market_price: float,
    title: str = "",
    close_time: Optional[datetime] = None,
) -> list[Modifier]:
    """Dispatch to the right modifier generator based on contract series.

    Returns an empty list if no real data is available — this is correct,
    because no data means no edge.
    """
    mods = []

    if source_id.startswith("KXINX"):
        mods = _inx_modifiers(source_id, market_price, title, close_time)
    elif source_id.startswith("KXCPI"):
        mods = _cpi_modifiers(source_id, market_price, title)
    elif source_id.startswith("KXGDP"):
        mods = _gdp_modifiers(source_id, market_price, title)
    elif source_id.startswith(("KXBTCD", "KXBTC", "KXETH")):
        mods = _crypto_modifiers(source_id, market_price, title, close_time)

    return mods


# ── S&P 500 (KXINX) ─────────────────────────────────────────────────────────

def _inx_modifiers(
    source_id: str, market_price: float, title: str,
    close_time: Optional[datetime],
) -> list[Modifier]:
    """Generate modifiers for S&P 500 contracts using spot + implied vol."""
    mods = []
    try:
        from tools.market_data import fetch_spot, binary_price

        spot_data = fetch_spot("^GSPC")
        spot = spot_data.price
        realized_vol = spot_data.realized_vol

        # Parse strike from source_id
        strike, is_range = _parse_inx_strike(source_id)
        if strike is None:
            return mods

        # Hours to close
        hours = _hours_to_close(close_time)
        if hours <= 0:
            return mods

        # Compute fair value from realized vol
        if is_range:
            # Range bucket: P(strike <= S < strike+25) ≈ P(S > strike) - P(S > strike+25)
            p_above_low = binary_price(spot, strike, realized_vol, hours)
            p_above_high = binary_price(spot, strike + 25, realized_vol, hours)
            fair = max(0.001, p_above_low - p_above_high)
        else:
            # Threshold: check if it's "above" or "below"
            if "below" in title.lower():
                fair = 1.0 - binary_price(spot, strike, realized_vol, hours)
            else:
                fair = binary_price(spot, strike, realized_vol, hours)

        # Edge = fair - market
        edge = fair - market_price
        if abs(edge) < 0.02:
            # Negligible edge — market agrees with our vol estimate
            return mods

        # Direction and weight based on edge magnitude
        direction = 1.0 if edge > 0 else -1.0
        # Weight scales with edge but caps at 0.8
        weight = min(0.8, abs(edge) * 3)

        mods.append(Modifier(
            name="spx_realized_vol",
            direction=direction,
            weight=weight,
            source=f"yahoo:^GSPC spot={spot:.0f} vol={realized_vol:.3f} fair={fair:.3f}",
        ))

        # Intraday momentum: if S&P is moving toward/away from strike
        change_pct = spot_data.change_pct
        if abs(change_pct) > 0.5:  # >0.5% move today
            if is_range:
                # Moving toward the range = bullish for this bucket
                mid = strike + 12.5
                moving_toward = (change_pct > 0 and spot < mid) or (change_pct < 0 and spot > mid)
                direction = 0.3 if moving_toward else -0.3
            else:
                direction = 0.3 if change_pct > 0 else -0.3
            mods.append(Modifier(
                name="spx_momentum",
                direction=direction,
                weight=min(0.4, abs(change_pct) / 3),
                source=f"yahoo:^GSPC change={change_pct:+.2f}%",
            ))

    except Exception as e:
        logger.debug(f"INX modifier failed: {e}")

    return mods


def _parse_inx_strike(source_id: str) -> tuple[Optional[float], bool]:
    """Parse strike price and type from KXINX source_id.

    KXINX-26APR10H1600-B6612 → (6612.0, True)   # range bucket
    KXINX-26APR10H1600-T6225 → (6225.0, False)   # threshold
    """
    m = re.search(r'-([BT])(\d+\.?\d*)', source_id)
    if not m:
        return None, False
    return float(m.group(2)), m.group(1) == 'B'


# ── CPI (KXCPI) ─────────────────────────────────────────────────────────────

def _cpi_modifiers(
    source_id: str, market_price: float, title: str,
) -> list[Modifier]:
    """Generate modifiers for CPI contracts using FRED data + consensus."""
    mods = []
    try:
        from tools.fed_data import FedDataTool
        from tools.econ_calendar import get_next_release

        # Get current CPI trend from FRED
        tool = FedDataTool(mock_mode=False)
        result = tool.run(series=["CPIAUCSL"], include_fedwatch=False)
        if result.get("success") and result.get("data", {}).get("series", {}).get("CPIAUCSL"):
            cpi_data = result["data"]["series"]["CPIAUCSL"]
            trend = cpi_data.get("trend_direction", "unknown")

            # Parse threshold from source_id: KXCPI-26APR-T0.8 → 0.8%
            threshold = _parse_threshold(source_id)
            if threshold is None:
                return mods

            # CPI trend signal
            # Historical MoM: mean ~0.25%, std ~0.20%
            # If trend is rising, higher thresholds more likely
            if trend == "rising":
                # Rising CPI → higher probability of exceeding threshold
                # But effect depends on how far threshold is from mean
                if threshold <= 0.3:
                    # Threshold near/below mean — rising trend makes this very likely
                    mods.append(Modifier(
                        name="cpi_trend_rising",
                        direction=0.4,
                        weight=0.5,
                        source=f"fred:CPIAUCSL trend=rising thresh={threshold}%",
                    ))
                elif threshold <= 0.6:
                    # Moderate threshold — rising helps
                    mods.append(Modifier(
                        name="cpi_trend_rising",
                        direction=0.3,
                        weight=0.4,
                        source=f"fred:CPIAUCSL trend=rising thresh={threshold}%",
                    ))
                else:
                    # High threshold (>0.6%) — even rising trend doesn't help much
                    mods.append(Modifier(
                        name="cpi_trend_rising",
                        direction=0.1,
                        weight=0.2,
                        source=f"fred:CPIAUCSL trend=rising thresh={threshold}%",
                    ))
            elif trend == "falling":
                if threshold >= 0.4:
                    mods.append(Modifier(
                        name="cpi_trend_falling",
                        direction=-0.4,
                        weight=0.5,
                        source=f"fred:CPIAUCSL trend=falling thresh={threshold}%",
                    ))
                else:
                    mods.append(Modifier(
                        name="cpi_trend_falling",
                        direction=-0.2,
                        weight=0.3,
                        source=f"fred:CPIAUCSL trend=falling thresh={threshold}%",
                    ))

        # Check consensus estimate from econ calendar
        next_rel = get_next_release()
        if next_rel and "CPI" in (next_rel.get("name", "") or ""):
            consensus = next_rel.get("consensus_estimate")
            if consensus is not None and threshold is not None:
                # Consensus is the % change estimate
                # If threshold < consensus, market should price YES high
                # If threshold > consensus, market should price YES low
                gap = consensus - threshold
                if abs(gap) > 0.05:  # meaningful gap
                    direction = 0.5 if gap > 0 else -0.5
                    mods.append(Modifier(
                        name="cpi_consensus",
                        direction=direction,
                        weight=min(0.6, abs(gap) * 2),
                        source=f"econ_cal:CPI consensus={consensus}% thresh={threshold}%",
                    ))

    except Exception as e:
        logger.debug(f"CPI modifier failed: {e}")

    return mods


# ── GDP (KXGDP) ─────────────────────────────────────────────────────────────

def _gdp_modifiers(
    source_id: str, market_price: float, title: str,
) -> list[Modifier]:
    """Generate modifiers for GDP contracts using FRED data + consensus."""
    mods = []
    try:
        from tools.fed_data import FedDataTool
        from tools.econ_calendar import get_next_release

        threshold = _parse_threshold(source_id)
        if threshold is None:
            return mods

        # Get GDP trend from FRED
        tool = FedDataTool(mock_mode=False)
        result = tool.run(series=["GDP"], include_fedwatch=False)
        if result.get("success") and result.get("data", {}).get("series", {}).get("GDP"):
            gdp_data = result["data"]["series"]["GDP"]
            trend = gdp_data.get("trend_direction", "unknown")

            if trend == "rising":
                direction = 0.3 if threshold <= 2.5 else 0.1
                mods.append(Modifier(
                    name="gdp_trend",
                    direction=direction,
                    weight=0.3,
                    source=f"fred:GDP trend={trend} thresh={threshold}%",
                ))
            elif trend == "falling":
                direction = -0.3 if threshold >= 2.0 else -0.1
                mods.append(Modifier(
                    name="gdp_trend",
                    direction=direction,
                    weight=0.3,
                    source=f"fred:GDP trend={trend} thresh={threshold}%",
                ))

        # Check consensus
        next_rel = get_next_release()
        if next_rel and "GDP" in (next_rel.get("name", "") or ""):
            consensus = next_rel.get("consensus_estimate")
            if consensus is not None:
                gap = consensus - threshold
                if abs(gap) > 0.2:
                    direction = 0.5 if gap > 0 else -0.5
                    mods.append(Modifier(
                        name="gdp_consensus",
                        direction=direction,
                        weight=min(0.6, abs(gap) / 2),
                        source=f"econ_cal:GDP consensus={consensus}% thresh={threshold}%",
                    ))

    except Exception as e:
        logger.debug(f"GDP modifier failed: {e}")

    return mods


# ── Crypto (KXBTCD, KXETH) ──────────────────────────────────────────────────

def _crypto_modifiers(
    source_id: str, market_price: float, title: str,
    close_time: Optional[datetime],
) -> list[Modifier]:
    """Generate modifiers for crypto contracts using spot price + vol."""
    mods = []
    try:
        from tools.market_data import fetch_spot, binary_price

        # Determine which symbol
        if "ETH" in source_id:
            symbol = "ETH-USD"
        else:
            symbol = "BTC-USD"

        spot_data = fetch_spot(symbol)
        spot = spot_data.price
        realized_vol = spot_data.realized_vol

        strike, is_range = _parse_crypto_strike(source_id)
        if strike is None:
            return mods

        hours = _hours_to_close(close_time)
        if hours <= 0:
            return mods

        if is_range:
            # Range width varies — parse from source_id or use default
            range_width = _get_crypto_range_width(source_id, strike)
            p_above_low = binary_price(spot, strike, realized_vol, hours)
            p_above_high = binary_price(spot, strike + range_width, realized_vol, hours)
            fair = max(0.001, p_above_low - p_above_high)
        else:
            fair = binary_price(spot, strike, realized_vol, hours)

        edge = fair - market_price
        if abs(edge) < 0.02:
            return mods

        direction = 1.0 if edge > 0 else -1.0
        weight = min(0.8, abs(edge) * 3)

        mods.append(Modifier(
            name=f"{'eth' if 'ETH' in source_id else 'btc'}_realized_vol",
            direction=direction,
            weight=weight,
            source=f"yahoo:{symbol} spot={spot:.0f} vol={realized_vol:.3f} fair={fair:.3f}",
        ))

    except Exception as e:
        logger.debug(f"Crypto modifier failed: {e}")

    return mods


# ── Helpers ──────────────────────────────────────────────────────────────────

def _parse_threshold(source_id: str) -> Optional[float]:
    """Parse threshold from source_id like KXCPI-26APR-T0.8 → 0.8"""
    m = re.search(r'-T(\d+\.?\d*)', source_id)
    if m:
        return float(m.group(1))
    return None


def _parse_crypto_strike(source_id: str) -> tuple[Optional[float], bool]:
    """Parse strike from crypto source_id.

    KXBTCD-26APR10-B71000 → (71000.0, True)
    KXETH-26APR0611-B2150 → (2150.0, True)
    """
    m = re.search(r'-([BT])(\d+\.?\d*)', source_id)
    if not m:
        return None, False
    return float(m.group(2)), m.group(1) == 'B'


def _get_crypto_range_width(source_id: str, strike: float) -> float:
    """Estimate range width for crypto buckets."""
    if "ETH" in source_id:
        return 20  # $20 ETH buckets
    if strike > 10000:
        return 250  # $250 BTC buckets (typical for Kalshi)
    return 100


def _hours_to_close(close_time: Optional[datetime]) -> float:
    """Compute hours until contract close."""
    if close_time is None:
        return 24.0  # default
    now = datetime.now(timezone.utc)
    if close_time.tzinfo is None:
        close_time = close_time.replace(tzinfo=timezone.utc)
    delta = (close_time - now).total_seconds() / 3600
    return max(0, delta)
