"""Tests for the deep-dive agent: pydantic validation, mock output, tool failure handling."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from agent.deep_dive import (
    DeepDiveResult,
    _extract_json,
    _mock_deep_dive,
    _parse_agent_output,
)
from config import Config
from database.models import Contract
from model.edge_calculator import EdgeResult
from tools.base_tool import ToolFetchError


# --- Pydantic validation ---

class TestPydanticValidation:
    def _valid_result(self, **overrides):
        base = dict(
            contract_id="1",
            contract_title="Test",
            model_probability=0.62,
            confidence="medium",
            edge=0.12,
            kelly_fraction=0.05,
            recommended_action="BET_YES",
            key_factors=["factor1", "factor2"],
            bull_case="bull case text",
            bear_case="bear case text",
            base_rate_used=0.55,
            modifiers_applied=[],
            tools_used=["metaculus"],
            tools_failed=[],
            reasoning_trace="reasoning here",
            generated_at="2026-04-02T00:00:00",
        )
        base.update(overrides)
        return base

    def test_valid_result_passes(self):
        result = DeepDiveResult(**self._valid_result())
        assert result.model_probability == 0.62

    def test_probability_clamped_to_floor(self):
        result = DeepDiveResult(**self._valid_result(model_probability=0.07))
        assert result.model_probability >= 0.07

    def test_probability_clamped_to_ceiling(self):
        result = DeepDiveResult(**self._valid_result(model_probability=0.93))
        assert result.model_probability <= 0.93

    def test_probability_below_floor_rejected(self):
        with pytest.raises(Exception):
            DeepDiveResult(**self._valid_result(model_probability=0.03))

    def test_probability_above_ceiling_rejected(self):
        with pytest.raises(Exception):
            DeepDiveResult(**self._valid_result(model_probability=0.97))

    def test_invalid_confidence_rejected(self):
        with pytest.raises(Exception):
            DeepDiveResult(**self._valid_result(confidence="very_high"))

    def test_invalid_action_rejected(self):
        with pytest.raises(Exception):
            DeepDiveResult(**self._valid_result(recommended_action="YOLO"))

    def test_max_5_key_factors(self):
        result = DeepDiveResult(**self._valid_result(
            key_factors=["a", "b", "c", "d", "e"]
        ))
        assert len(result.key_factors) == 5

    def test_over_5_key_factors_rejected(self):
        with pytest.raises(Exception):
            DeepDiveResult(**self._valid_result(
                key_factors=["a", "b", "c", "d", "e", "f"]
            ))


# --- JSON extraction ---

class TestJsonExtraction:
    def test_extract_from_code_block(self):
        text = 'Some text\n```json\n{"probability": 0.6}\n```\nMore text'
        result = _extract_json(text)
        assert result is not None
        parsed = json.loads(result)
        assert parsed["probability"] == 0.6

    def test_extract_raw_json(self):
        text = 'Here is my answer: {"probability": 0.55, "confidence": "medium"}'
        result = _extract_json(text)
        assert result is not None

    def test_extract_nested_json(self):
        text = '{"probability": 0.5, "modifiers": [{"name": "test"}]}'
        result = _extract_json(text)
        assert result is not None
        parsed = json.loads(result)
        assert parsed["probability"] == 0.5

    def test_no_json_returns_none(self):
        text = "There is no JSON here, just plain text."
        result = _extract_json(text)
        assert result is None


# --- Mock deep dive ---

class TestMockDeepDive:
    def _make_contract(self, category="economics", yes_price=0.62):
        return Contract(
            id=1, source="kalshi", source_id="TEST-1",
            title="Test contract", category=category,
            yes_price=yes_price, volume_24h=45000,
            open_time=datetime(2026, 1, 1),
            close_time=datetime(2026, 6, 30),
        )

    def _make_edge_result(self):
        return EdgeResult(
            edge=0.05, abs_edge=0.05, kelly_fraction=0.01,
            bet_amount=10, recommendation="PASS", is_high_priority=False,
        )

    def test_mock_returns_valid_result(self):
        config = Config(mock_tools=True)
        contract = self._make_contract()
        result = _mock_deep_dive(contract, config, ["prediction_history"], [], self._make_edge_result())
        assert isinstance(result, DeepDiveResult)
        assert 0.07 <= result.model_probability <= 0.93

    def test_low_tool_count_forces_low_confidence(self):
        """If <3 tools returned useful data, confidence must be 'low'."""
        config = Config(mock_tools=True)
        contract = self._make_contract()
        result = _mock_deep_dive(contract, config, ["one_tool"], [], self._make_edge_result())
        assert result.confidence == "low"

    def test_tools_failed_populates_correctly(self):
        config = Config(mock_tools=True)
        contract = self._make_contract()
        failed = ["search_news", "metaculus"]
        result = _mock_deep_dive(contract, config, ["prediction_history"], failed, self._make_edge_result())
        assert "search_news" in result.tools_failed
        assert "metaculus" in result.tools_failed

    def test_different_categories_produce_different_factors(self):
        config = Config(mock_tools=True)
        econ = _mock_deep_dive(
            self._make_contract("economics"), config,
            ["prediction_history"], [], self._make_edge_result(),
        )
        crypto = _mock_deep_dive(
            self._make_contract("crypto", 0.45), config,
            ["prediction_history"], [], self._make_edge_result(),
        )
        assert econ.key_factors != crypto.key_factors


# --- Integration: parse_agent_output ---

class TestParseAgentOutput:
    def test_valid_json_produces_result(self, db, config):
        contract = Contract(
            id=None, source="test", source_id="PARSE-TEST",
            title="Parse test", category="economics",
            yes_price=0.55, volume_24h=10000,
        )
        contract.id = db.upsert_contract(contract)

        edge_result = EdgeResult(
            edge=0.05, abs_edge=0.05, kelly_fraction=0.01,
            bet_amount=10, recommendation="PASS", is_high_priority=False,
        )

        agent_text = json.dumps({
            "probability": 0.65,
            "confidence": "medium",
            "key_factors": ["Fed guidance", "CPI trend"],
            "bull_case": "Inflation cooling",
            "bear_case": "Labor market still tight",
            "base_rate_used": 0.60,
            "modifiers_applied": [
                {"name": "cpi_trend", "direction": "toward_yes", "magnitude": "medium", "evidence": "CPI declining"}
            ],
            "reasoning_trace": "Starting from base rate..."
        })

        result = _parse_agent_output(
            agent_text, contract, config,
            ["prediction_history", "fed_data", "search_news"], [],
            edge_result, db,
        )
        assert isinstance(result, DeepDiveResult)
        assert result.model_probability == pytest.approx(0.65, abs=0.01)
        assert result.confidence == "medium"

    def test_malformed_json_falls_back_to_mock(self, db, config):
        contract = Contract(
            id=None, source="test", source_id="BADJSON-TEST",
            title="Bad JSON test", category="economics",
            yes_price=0.55, volume_24h=10000,
        )
        contract.id = db.upsert_contract(contract)

        edge_result = EdgeResult(
            edge=0.05, abs_edge=0.05, kelly_fraction=0.01,
            bet_amount=10, recommendation="PASS", is_high_priority=False,
        )

        result = _parse_agent_output(
            "This is not JSON at all",
            contract, config, [], [], edge_result, db,
        )
        # Should fall back to mock rather than crash
        assert isinstance(result, DeepDiveResult)
