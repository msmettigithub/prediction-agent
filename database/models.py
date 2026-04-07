"""Data models for the prediction agent database."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Contract:
    id: Optional[int] = None
    source: str = ""            # "kalshi" or "polymarket"
    source_id: str = ""         # ticker or market ID from source
    title: str = ""
    category: str = ""
    yes_price: float = 0.0      # market probability (0-1)
    volume_24h: float = 0.0
    open_time: Optional[datetime] = None
    close_time: Optional[datetime] = None
    resolved: bool = False
    resolution: Optional[bool] = None  # True=YES, False=NO, None=unresolved
    resolved_at: Optional[datetime] = None
    cross_market_id: Optional[str] = None  # links matched contracts across markets
    alerted_at: Optional[datetime] = None  # when this contract was first alerted to the user
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass
class Prediction:
    id: Optional[int] = None
    contract_id: int = 0
    model_prob: float = 0.0
    confidence: str = "low"     # low, medium, high
    edge: float = 0.0           # model_prob - market_prob
    kelly_fraction: float = 0.0
    recommendation: str = "PASS"  # PASS, WATCH, BET_YES, BET_NO
    key_factors: str = "[]"     # JSON array of strings
    bull_case: str = ""
    bear_case: str = ""
    tools_used: str = "[]"      # JSON array of tool names
    tools_failed: str = "[]"    # JSON array of failed tool names
    created_at: Optional[datetime] = None


@dataclass
class Resolution:
    id: Optional[int] = None
    contract_id: int = 0
    prediction_id: Optional[int] = None
    model_prob: float = 0.0
    market_prob: float = 0.0
    resolved_yes: bool = False
    brier_component: float = 0.0  # (prob - outcome)^2
    correct_direction: bool = False
    created_at: Optional[datetime] = None


@dataclass
class ToolRun:
    id: Optional[int] = None
    tool_name: str = ""
    contract_id: Optional[int] = None
    success: bool = False
    latency_ms: float = 0.0
    error_message: str = ""
    created_at: Optional[datetime] = None
