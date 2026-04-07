"""Prediction history — local DB queries only, no external API calls.

Returns historical contracts in same category, model accuracy, and fuzzy
title matching for exact-contract repeats.
"""

from __future__ import annotations

import json
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Optional

from tools.base_tool import BaseTool

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "prediction_history.json"


class PredictionHistoryTool(BaseTool):
    def __init__(self, mock_mode: bool = False, db=None):
        self.mock_mode = mock_mode
        self.db = db

    @property
    def name(self) -> str:
        return "prediction_history"

    def get_schema(self) -> dict:
        return {
            "name": "prediction_history",
            "description": "Query local database for historical contracts in same category, model accuracy on similar questions, and check if this exact contract has been asked before (fuzzy match threshold=0.85).",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Contract title to search for"},
                    "category": {"type": "string", "description": "Contract category"},
                },
                "required": ["category"],
            },
        }

    def _fetch(self, title: str = "", category: str = "", **kwargs) -> Any:
        if self.mock_mode:
            return json.loads(FIXTURE_PATH.read_text())

        if self.db is None:
            return {"category_history": None, "similar_contracts": [], "exact_match": None}

        resolved = self.db.get_resolved_contracts()
        resolutions = self.db.get_all_resolutions()

        # Filter by category
        cat_contracts = [c for c in resolved if c.category.lower() == category.lower()]
        cat_resolutions = [r for r in resolutions if any(
            c.id == r.contract_id and c.category.lower() == category.lower() for c in resolved
        )]

        # Category history stats
        n_total = len(cat_contracts)
        n_correct = sum(1 for r in cat_resolutions if r.correct_direction)
        accuracy = n_correct / len(cat_resolutions) if cat_resolutions else 0
        avg_brier = sum(r.brier_component for r in cat_resolutions) / len(cat_resolutions) if cat_resolutions else 0
        yes_rate = sum(1 for c in cat_contracts if c.resolution) / n_total if n_total else 0

        # Find similar contracts by title
        similar = []
        exact_match = None
        if title:
            for c in resolved:
                ratio = SequenceMatcher(None, title.lower(), c.title.lower()).ratio()
                if ratio >= 0.85:
                    exact_match = {"id": c.id, "title": c.title, "similarity": ratio,
                                   "resolution": c.resolution}
                elif ratio >= 0.5:
                    # Find matching resolution
                    matching_res = [r for r in resolutions if r.contract_id == c.id]
                    res_info = matching_res[0] if matching_res else None
                    similar.append({
                        "id": c.id, "title": c.title, "similarity": ratio,
                        "model_prob": res_info.model_prob if res_info else None,
                        "market_prob": c.yes_price,
                        "resolved_yes": c.resolution,
                        "correct": res_info.correct_direction if res_info else None,
                    })

        similar.sort(key=lambda x: x["similarity"], reverse=True)

        return {
            "category_history": {
                "category": category,
                "total_contracts": n_total,
                "resolved_contracts": len(cat_resolutions),
                "model_accuracy": accuracy,
                "avg_brier": avg_brier,
                "base_rate_yes": yes_rate,
            },
            "similar_contracts": similar[:5],
            "exact_match": exact_match,
        }

    def _parse(self, raw_data: Any) -> dict:
        return {
            "category_history": raw_data.get("category_history"),
            "similar_contracts": raw_data.get("similar_contracts", []),
            "exact_match": raw_data.get("exact_match"),
            "source": "local_db",
            "fetched_at": datetime.utcnow().isoformat(),
            "confidence": "high" if raw_data.get("category_history", {}).get("resolved_contracts", 0) >= 10 else "medium",
        }

    def health_check(self) -> dict:
        # Local DB — always healthy in mock mode, check DB otherwise
        if self.mock_mode:
            return {"healthy": True, "latency_ms": 0.1, "error": None}
        if self.db is None:
            return {"healthy": False, "latency_ms": 0, "error": "No database connection"}
        return {"healthy": True, "latency_ms": 0.1, "error": None}
