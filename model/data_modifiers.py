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
        # Weight scales with edge — vol-derived fair value is the strongest signal
        weight = min(0.9, abs(edge) * 3)

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

def _fetch_bls_cpi() -> dict:
    """Fetch CPI data directly from BLS (no API key needed)."""
    try:
        import requests
        r = requests.get('https://api.bls.gov/publicAPI/v2/timeseries/data/CUUR0000SA0',
            json={'startyear': '2025', 'endyear': '2026'}, timeout=10)
        if r.status_code != 200:
            return {}
        data = r.json().get('Results', {}).get('series', [{}])[0].get('data', [])
        # Filter out missing values
        valid = [d for d in data if d.get('value') and d['value'] != '-']
        if len(valid) < 3:
            return {}
        values = [float(d['value']) for d in valid[:6]]
        latest_mom = (values[0] - values[1]) / values[1] * 100
        prev_mom = (values[1] - values[2]) / values[2] * 100
        trend = 'rising' if latest_mom > prev_mom else 'falling' if latest_mom < prev_mom else 'flat'
        return {'latest_mom': latest_mom, 'prev_mom': prev_mom, 'trend': trend,
                'latest_value': values[0], 'period': valid[0].get('year', '') + '-' + valid[0].get('period', '')}
    except:
        return {}


def _cpi_modifiers(
    source_id: str, market_price: float, title: str,
) -> list[Modifier]:
    """Generate modifiers for CPI contracts using BLS data + consensus."""
    mods = []
    try:
        from tools.econ_calendar import get_next_release

        # Get current CPI trend from BLS (no API key needed)
        cpi_bls = _fetch_bls_cpi()
        if cpi_bls:
            trend = cpi_bls['trend']
            latest_mom = cpi_bls['latest_mom']

            threshold = _parse_threshold(source_id)
            if threshold is None:
                return mods

            # Use actual CPI MoM data to estimate probability
            # Latest MoM = 0.47%, previous = 0.37%
            # If threshold < latest_mom, YES is likely. If threshold > latest_mom, NO is likely.
            if trend == "rising":
                if threshold <= latest_mom * 0.8:
                    mods.append(Modifier(name="cpi_trend_rising", direction=0.5, weight=0.6,
                        source=f"bls:CPI mom={latest_mom:.2f}% trend=rising thresh={threshold}%"))
                elif threshold <= latest_mom * 1.2:
                    mods.append(Modifier(name="cpi_trend_rising", direction=0.2, weight=0.4,
                        source=f"bls:CPI mom={latest_mom:.2f}% trend=rising thresh={threshold}%"))
                else:
                    mods.append(Modifier(name="cpi_above_trend", direction=-0.3, weight=0.4,
                        source=f"bls:CPI mom={latest_mom:.2f}% thresh={threshold}% above recent"))
            elif trend == "falling":
                if threshold >= latest_mom:
                    mods.append(Modifier(name="cpi_trend_falling", direction=-0.4, weight=0.5,
                        source=f"bls:CPI mom={latest_mom:.2f}% trend=falling thresh={threshold}%"))
                else:
                    mods.append(Modifier(name="cpi_trend_falling", direction=-0.1, weight=0.3,
                        source=f"bls:CPI mom={latest_mom:.2f}% trend=falling thresh={threshold}%"))
            else:
                # Flat — compare threshold to recent MoM
                gap = latest_mom - threshold
                if abs(gap) > 0.1:
                    direction = 0.3 if gap > 0 else -0.3
                    mods.append(Modifier(name="cpi_vs_recent", direction=direction, weight=0.3,
                        source=f"bls:CPI mom={latest_mom:.2f}% vs thresh={threshold}%"))

        # Use recent CPI MoM as the best consensus proxy
        # (the econ_calendar consensus field has the index level, not the change)
        if latest_mom > 0:
            gap = latest_mom - threshold
            if abs(gap) > 0.05:
                direction = 0.4 if gap > 0 else -0.4
                mods.append(Modifier(
                    name="cpi_recent_vs_threshold",
                    direction=direction,
                    weight=min(0.5, abs(gap) * 2),
                    source=f"bls:recent_mom={latest_mom:.2f}% vs thresh={threshold}%",
                ))

    except Exception as e:
        logger.debug(f"CPI modifier failed: {e}")

    return mods


# ── GDP (KXGDP) ─────────────────────────────────────────────────────────────

def _fetch_bls_productivity() -> dict:
    """Fetch productivity/GDP data from BLS (no API key needed)."""
    try:
        import requests
        r = requests.get('https://api.bls.gov/publicAPI/v2/timeseries/data/PRS85006092',
            json={'startyear': '2025', 'endyear': '2026'}, timeout=10)
        if r.status_code != 200:
            return {}
        data = r.json().get('Results', {}).get('series', [{}])[0].get('data', [])
        valid = [d for d in data if d.get('value') and d['value'] != '-']
        if not valid:
            return {}
        latest = float(valid[0]['value'])
        prev = float(valid[1]['value']) if len(valid) > 1 else latest
        trend = 'rising' if latest > prev else 'falling' if latest < prev else 'flat'
        return {'latest': latest, 'prev': prev, 'trend': trend,
                'period': valid[0].get('year', '') + '-' + valid[0].get('period', '')}
    except:
        return {}


def _gdp_modifiers(
    source_id: str, market_price: float, title: str,
) -> list[Modifier]:
    """Generate modifiers for GDP contracts using BLS productivity data."""
    mods = []
    try:
        threshold = _parse_threshold(source_id)
        if threshold is None:
            return mods

        # Get productivity trend from BLS (proxy for GDP growth)
        prod = _fetch_bls_productivity()
        if prod:
            latest = prod['latest']
            trend = prod['trend']

            # Productivity data: latest Q4 2025 = 1.8%, prior Q3 = 5.2%
            # Falling productivity suggests weaker GDP
            if trend == 'falling':
                if threshold >= latest:
                    mods.append(Modifier(name="gdp_productivity_falling", direction=-0.4, weight=0.5,
                        source=f"bls:productivity={latest}% falling, thresh={threshold}%"))
                else:
                    mods.append(Modifier(name="gdp_productivity_falling", direction=-0.1, weight=0.3,
                        source=f"bls:productivity={latest}% falling, thresh={threshold}%"))
            elif trend == 'rising':
                if threshold <= latest:
                    mods.append(Modifier(name="gdp_productivity_rising", direction=0.3, weight=0.4,
                        source=f"bls:productivity={latest}% rising, thresh={threshold}%"))
                else:
                    mods.append(Modifier(name="gdp_productivity_rising", direction=0.1, weight=0.2,
                        source=f"bls:productivity={latest}% rising, thresh={threshold}%"))

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
        # Vol-derived fair value is the strongest signal — allow higher weight
        weight = min(0.9, abs(edge) * 3)

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
