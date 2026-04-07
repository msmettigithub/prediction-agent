"""Kelly criterion calculator — pure math, no HTTP calls.

Always called last in the deep-dive pipeline after the agent's probability estimate.
Computes optimal position sizing with quarter-Kelly and bankroll cap.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from tools.base_tool import BaseTool

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "kelly_criterion.json"


class KellyCriterionTool(BaseTool):
    def __init__(self, mock_mode: bool = False):
        self.mock_mode = mock_mode

    @property
    def name(self) -> str:
        return "kelly_criterion"

    def get_schema(self) -> dict:
        return {
            "name": "kelly_criterion",
            "description": "Calculate optimal bet size using Kelly criterion. Inputs: model probability, market probability, bankroll, existing exposure. Returns: edge, raw Kelly, quarter-Kelly (capped), bet amount, break-even probability.",
            "parameters": {
                "type": "object",
                "properties": {
                    "our_probability": {"type": "number", "description": "Model's estimated probability (0-1)"},
                    "market_probability": {"type": "number", "description": "Market price / implied probability (0-1)"},
                    "bankroll": {"type": "number", "description": "Total bankroll"},
                    "existing_exposure": {"type": "number", "default": 0, "description": "Sum of open positions"},
                },
                "required": ["our_probability", "market_probability", "bankroll"],
            },
        }

    def _fetch(self, our_probability: float = 0.5, market_probability: float = 0.5,
               bankroll: float = 1000, existing_exposure: float = 0, **kwargs) -> Any:
        if self.mock_mode:
            return json.loads(FIXTURE_PATH.read_text())

        return {
            "our_probability": our_probability,
            "market_probability": market_probability,
            "bankroll": bankroll,
            "existing_exposure": existing_exposure,
        }

    def _parse(self, raw_data: Any) -> dict:
        p = raw_data["our_probability"]
        m = raw_data["market_probability"]
        bankroll = raw_data["bankroll"]
        exposure = raw_data.get("existing_exposure", 0)

        edge = p - m

        # Kelly fraction: f* = edge / odds
        # For YES bet: odds = (1/m - 1), f* = (p*(1/m - 1) - (1-p)) / (1/m - 1)
        # For NO bet: flip p and m
        if edge > 0:
            # Bet YES
            odds = (1.0 / m) - 1.0 if m > 0 else 0
            raw_kelly = (p * odds - (1 - p)) / odds if odds > 0 else 0
        elif edge < 0:
            # Bet NO
            nm = 1.0 - m
            np_ = 1.0 - p
            odds = (1.0 / nm) - 1.0 if nm > 0 else 0
            raw_kelly = (np_ * odds - p) / odds if odds > 0 else 0
        else:
            raw_kelly = 0

        raw_kelly = max(0, raw_kelly)

        # Quarter-Kelly
        quarter_kelly = raw_kelly * 0.25

        # Hard cap at 5% of bankroll
        capped_kelly = min(quarter_kelly, 0.05)

        # Adjust for existing exposure
        available_bankroll = max(0, bankroll - exposure)
        bet_amount = capped_kelly * bankroll
        adjusted_bet = min(bet_amount, available_bankroll * 0.05)  # never exceed 5% of available

        # Break-even probability: minimum accuracy to profit given vig
        # At market price m, you need to be right (m/(1-vig)) fraction of the time
        # Assuming ~2% vig on prediction markets
        vig = 0.02
        break_even = m + vig / 2 if edge > 0 else (1 - m) + vig / 2

        # Expected value per dollar
        if edge > 0:
            ev_per_dollar = p * (1.0 / m - 1) - (1 - p)
        elif edge < 0:
            ev_per_dollar = (1 - p) * (1.0 / (1 - m) - 1) - p
        else:
            ev_per_dollar = 0

        return {
            "our_probability": p,
            "market_probability": m,
            "edge": round(edge, 4),
            "raw_kelly": round(raw_kelly, 4),
            "quarter_kelly": round(quarter_kelly, 4),
            "capped_kelly": round(capped_kelly, 4),
            "bet_amount": round(bet_amount, 2),
            "bankroll": bankroll,
            "existing_exposure": exposure,
            "adjusted_bet": round(adjusted_bet, 2),
            "break_even_probability": round(break_even, 4),
            "expected_value_per_dollar": round(ev_per_dollar, 4),
            "source": "kelly_calculator",
            "fetched_at": datetime.utcnow().isoformat(),
            "confidence": "high",
        }

    def health_check(self) -> dict:
        # Pure calculation — always healthy
        return {"healthy": True, "latency_ms": 0.1, "error": None}
