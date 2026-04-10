"""Market Intelligence — aggregates all available data into a conviction score.

Pulls from: Yahoo Finance (spot, vol, intraday trend), Deribit (IV, funding, basis),
CoinGecko (BTC/ETH), Fear & Greed index, Kalshi orderbook depth.

Outputs a MarketView per asset with:
- direction: bullish/bearish/neutral
- conviction: 0-1 (how confident)
- vol_regime: low/normal/high
- expected_range: (low, high) for next N hours
- signals: list of named signals that contributed
"""
import math
import time
import requests
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_session = requests.Session()


@dataclass
class Signal:
    name: str
    direction: float   # -1 to +1 (bearish to bullish)
    weight: float      # 0-1
    source: str


@dataclass
class MarketView:
    asset: str
    spot: float
    direction: float        # -1 to +1
    conviction: float       # 0-1
    vol_regime: str         # low/normal/high
    realized_vol: float
    implied_vol: float
    expected_low: float
    expected_high: float
    signals: list = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    @property
    def is_bullish(self): return self.direction > 0.2
    @property
    def is_bearish(self): return self.direction < -0.2
    @property
    def is_neutral(self): return abs(self.direction) <= 0.2


def get_market_intel(hours_ahead=16.0) -> dict[str, MarketView]:
    """Fetch all available data and build MarketView for each asset."""
    views = {}

    # 1. Spot prices + realized vol from Yahoo
    from tools.market_data import fetch_spot
    yahoo_assets = {'WTI': 'CL=F', 'BTC': 'BTC-USD', 'GOLD': 'GC=F', 'SPX': '^GSPC', 'ETH': 'ETH-USD'}
    spots = {}
    for name, sym in yahoo_assets.items():
        try:
            s = fetch_spot(sym)
            spots[name] = s
        except:
            pass

    # 2. Deribit — BTC implied vol, funding, basis
    deribit = _fetch_deribit()

    # 3. Fear & Greed
    fear_greed = _fetch_fear_greed()

    # 4. Build views
    for name, spot_data in spots.items():
        signals = []
        iv = 0

        # Intraday momentum
        if abs(spot_data.change_pct) > 0.3:
            dir_val = 1.0 if spot_data.change_pct > 0 else -1.0
            weight = min(0.6, abs(spot_data.change_pct) / 3)
            signals.append(Signal('intraday_momentum', dir_val, weight,
                                  f'change={spot_data.change_pct:+.2f}%'))

        # Intraday range position — where is spot relative to today's range?
        if spot_data.high > spot_data.low:
            range_pos = (spot_data.price - spot_data.low) / (spot_data.high - spot_data.low)
            # Near high = bullish momentum, near low = bearish
            dir_val = (range_pos - 0.5) * 2  # -1 at low, +1 at high
            signals.append(Signal('range_position', dir_val, 0.3,
                                  f'at {range_pos:.0%} of day range'))

        # BTC-specific signals
        if name == 'BTC' and deribit:
            iv = deribit.get('avg_iv', 0) / 100  # convert from % to decimal

            # Funding rate — positive = longs paying, bearish pressure
            funding = deribit.get('funding_8h', 0)
            if funding and abs(funding) > 1e-5:
                dir_val = -1.0 if funding > 0 else 1.0  # positive funding = bearish
                signals.append(Signal('perp_funding', dir_val, 0.4,
                                      f'funding_8h={funding:.6f}'))

            # Basis — perp premium = bullish, discount = bearish
            basis = deribit.get('basis_pct', 0)
            if abs(basis) > 0.01:
                dir_val = 1.0 if basis > 0 else -1.0
                signals.append(Signal('perp_basis', dir_val, 0.3,
                                      f'basis={basis:+.3f}%'))

            # Fear & Greed
            if fear_greed is not None:
                # 0-25 = extreme fear (contrarian bullish but short-term bearish)
                # 75-100 = extreme greed (contrarian bearish)
                if fear_greed < 25:
                    signals.append(Signal('fear_greed', -0.3, 0.3,
                                          f'F&G={fear_greed} extreme fear'))
                elif fear_greed > 75:
                    signals.append(Signal('fear_greed', 0.3, 0.3,
                                          f'F&G={fear_greed} extreme greed'))

        # Vol regime
        rv = spot_data.realized_vol
        use_vol = iv if iv > 0 else rv
        if use_vol > 0.03:
            vol_regime = 'high'
        elif use_vol > 0.015:
            vol_regime = 'normal'
        else:
            vol_regime = 'low'

        # Compute expected range
        sigma = spot_data.price * use_vol * math.sqrt(hours_ahead / 24)
        expected_low = spot_data.price - 2 * sigma
        expected_high = spot_data.price + 2 * sigma

        # Aggregate direction
        if signals:
            total_weight = sum(s.weight for s in signals)
            direction = sum(s.direction * s.weight for s in signals) / total_weight if total_weight > 0 else 0
            conviction = min(1.0, total_weight / 2)  # more signals = more conviction
        else:
            direction = 0
            conviction = 0

        views[name] = MarketView(
            asset=name, spot=spot_data.price,
            direction=round(direction, 3), conviction=round(conviction, 3),
            vol_regime=vol_regime, realized_vol=rv, implied_vol=iv,
            expected_low=round(expected_low, 2), expected_high=round(expected_high, 2),
            signals=signals,
        )

    return views


def _fetch_deribit() -> dict:
    """Fetch Deribit BTC data: index, perp funding/basis, IV."""
    try:
        # Index price
        r = _session.get('https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd', timeout=5)
        idx = r.json().get('result', {}).get('index_price', 0)

        # Perp
        r2 = _session.get('https://www.deribit.com/api/v2/public/get_book_summary_by_instrument?instrument_name=BTC-PERPETUAL', timeout=5)
        perp = r2.json().get('result', [{}])[0] if r2.json().get('result') else {}
        funding = perp.get('funding_8h', 0)
        mark = perp.get('mark_price', 0)
        basis_pct = (mark - idx) / idx * 100 if idx else 0

        # IV from options (top 20 by volume)
        r3 = _session.get('https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency=BTC&kind=option', timeout=5)
        opts = r3.json().get('result', [])
        ivs = sorted([(o.get('mark_iv', 0), o.get('volume', 0)) for o in opts if o.get('mark_iv', 0) > 0],
                     key=lambda x: -x[1])[:20]
        avg_iv = sum(iv for iv, _ in ivs) / len(ivs) if ivs else 0

        return {'index': idx, 'mark': mark, 'funding_8h': funding,
                'basis_pct': basis_pct, 'avg_iv': avg_iv}
    except:
        return {}


def _fetch_fear_greed() -> int | None:
    """Fetch crypto Fear & Greed Index (0-100)."""
    try:
        r = _session.get('https://api.alternative.me/fng/?limit=1', timeout=5)
        return int(r.json().get('data', [{}])[0].get('value', 50))
    except:
        return None


def _fetch_kalshi_orderbook(trader, ticker) -> dict:
    """Fetch Kalshi orderbook for a specific market."""
    try:
        r = _session.get(f'{trader.TRADING_URL}/markets/{ticker}/orderbook',
                        headers={'Accept': 'application/json'}, timeout=5)
        if r.status_code == 200:
            return r.json().get('orderbook_fp', r.json().get('orderbook', {}))
    except:
        pass
    return {}


def format_intel(views: dict[str, MarketView]) -> str:
    """Format market intel for logging/display."""
    lines = []
    for name, v in sorted(views.items()):
        dir_str = 'BULL' if v.is_bullish else 'BEAR' if v.is_bearish else 'FLAT'
        sig_str = ' '.join(f'{s.name}({s.direction:+.1f})' for s in v.signals)
        lines.append(f'{name}: ${v.spot:,.0f} {dir_str} conv={v.conviction:.0%} '
                     f'vol={v.vol_regime} range=${v.expected_low:,.0f}-${v.expected_high:,.0f} '
                     f'| {sig_str}')
    return '\n'.join(lines)
