"""Configuration loaded from environment / .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env", override=True)


@dataclass(frozen=True)
class Config:
    anthropic_api_key: str = ""
    kalshi_api_key: str = ""
    kalshi_private_key_path: str = ""
    polymarket_api_key: str = ""
    fred_api_key: str = ""
    brave_search_api_key: str = ""
    tavily_api_key: str = ""
    odds_api_key: str = ""

    bankroll: float = 1000.0
    edge_threshold: float = 0.08  # 8pp minimum edge to surface
    mock_tools: bool = False

    # Live trading safety controls
    live_trading_enabled: bool = False
    max_live_bankroll: float = 50.00  # absolute cap on total deployed capital
    max_single_bet: float = 10.00     # absolute cap on any single order

    # Scanner defaults
    min_volume_24h: float = 1000.0
    max_days_to_close: int = 90
    prob_floor: float = 0.05
    prob_ceiling: float = 0.95

    # Model constraints
    model_prob_floor: float = 0.07
    model_prob_ceiling: float = 0.93

    # Kelly constraints
    kelly_fraction: float = 0.25  # quarter-Kelly
    kelly_max_bet_pct: float = 0.05  # hard cap 5% of bankroll

    # Cross-market divergence threshold
    cross_market_divergence_pp: float = 0.10  # 10pp

    # Calibration thresholds
    min_resolved_for_calibration: int = 30
    accuracy_threshold: float = 0.65
    brier_threshold: float = 0.25
    separation_threshold: float = 0.10  # 10pp

    db_path: str = ""

    def __post_init__(self):
        if not self.db_path:
            object.__setattr__(self, "db_path", str(Path(__file__).parent / "prediction_agent.db"))


def load_config() -> Config:
    """Load config from environment variables."""
    return Config(
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        kalshi_api_key=os.getenv("KALSHI_API_KEY", ""),
        kalshi_private_key_path=os.getenv("KALSHI_PRIVATE_KEY_PATH", ""),
        polymarket_api_key=os.getenv("POLYMARKET_API_KEY", ""),
        fred_api_key=os.getenv("FRED_API_KEY", ""),
        brave_search_api_key=os.getenv("BRAVE_SEARCH_API_KEY", ""),
        tavily_api_key=os.getenv("TAVILY_API_KEY", ""),
        odds_api_key=os.getenv("ODDS_API_KEY", ""),
        bankroll=float(os.getenv("BANKROLL", "1000")),
        edge_threshold=float(os.getenv("EDGE_THRESHOLD", "0.08")),
        mock_tools=os.getenv("MOCK_TOOLS", "false").lower() == "true",
        live_trading_enabled=os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "true",
        max_live_bankroll=float(os.getenv("MAX_LIVE_BANKROLL", "50.00")),
        max_single_bet=float(os.getenv("MAX_SINGLE_BET", "10.00")),
    )
