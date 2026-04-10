"""Deep-dive agent — Claude Sonnet with tool_use for contract research.

Agentic loop: agent calls tools autonomously until done, max 8 iterations.
Output validated with pydantic before returning.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from config import Config
from database.db import Database
from database.models import Contract, ToolRun
from tools.tool_registry import ToolRegistry
from tools.prediction_history import PredictionHistoryTool
from tools.kelly_criterion import KellyCriterionTool
from agent.prompt_builder import build_system_prompt, get_tool_exclusions

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 8


class DeepDiveResult(BaseModel):
    contract_id: str
    contract_title: str
    model_probability: float = Field(ge=0.07, le=0.93)
    confidence: Literal["low", "medium", "high"]
    edge: float
    kelly_fraction: float
    recommended_action: Literal["PASS", "WATCH", "BET_YES", "BET_NO"]
    key_factors: list = Field(max_length=5)
    bull_case: str
    bear_case: str
    base_rate_used: float
    modifiers_applied: list
    tools_used: list
    tools_failed: list
    reasoning_trace: str
    generated_at: str

    @field_validator("model_probability")
    @classmethod
    def clamp_probability(cls, v):
        return max(0.07, min(0.93, v))


def run_deep_dive(
    contract: Contract,
    db: Database,
    config: Config,
    registry: ToolRegistry,
) -> DeepDiveResult:
    """Run the deep-dive agent on a contract. Returns validated DeepDiveResult."""

    tools_used = []
    tools_failed = []

    # Step 1: Always run prediction_history first (injected into prompt, not as tool call)
    history_data = None
    ph_tool = registry.get("prediction_history")
    if ph_tool is None:
        ph_tool = PredictionHistoryTool(mock_mode=config.mock_tools, db=db)
    else:
        ph_tool.db = db
    try:
        result = ph_tool.run(title=contract.title, category=contract.category)
        if result["success"]:
            history_data = result["data"]
            tools_used.append("prediction_history")
        else:
            tools_failed.append("prediction_history")
    except Exception as e:
        tools_failed.append("prediction_history")
        logger.warning(f"prediction_history failed: {e}")

    # Step 2: Compute scanner edge
    from model.probability_model import estimate_probability
    from model.edge_calculator import compute_edge
    estimate = estimate_probability(contract, config=config)
    edge_result = compute_edge(estimate, contract.yes_price, config)

    # Cross-market note
    cross_note = None
    if contract.cross_market_id:
        cross_note = f"Cross-market ID: {contract.cross_market_id}. Check both Kalshi and Polymarket prices."

    # Step 3: Build system prompt
    system_prompt = build_system_prompt(
        contract=contract,
        market_prob=contract.yes_price,
        scanner_edge=edge_result.edge,
        cross_market_note=cross_note,
        prediction_history_data=history_data,
    )

    # Step 4: Build tool definitions for Claude
    exclusions = get_tool_exclusions(contract.category)
    available_tools = []
    tool_map = {}
    for name, tool in registry.all().items():
        if name in exclusions or name == "prediction_history":
            continue
        schema = tool.get_schema()
        tool_def = {
            "name": name,
            "description": schema.get("description", ""),
            "input_schema": schema.get("parameters", {"type": "object", "properties": {}}),
        }
        available_tools.append(tool_def)
        tool_map[name] = tool

    # Step 5: Run agentic loop
    logger.info(f"Running deep dive (est. cost: ~$0.02-0.05). Contract: {contract.title[:60]}")
    try:
        import anthropic
    except ImportError:
        logger.warning("anthropic package not installed — using mock agent")
        return _mock_deep_dive(contract, config, tools_used, tools_failed, edge_result)

    if not config.anthropic_api_key:
        logger.info("No ANTHROPIC_API_KEY — using mock agent")
        return _mock_deep_dive(contract, config, tools_used, tools_failed, edge_result)

    client = anthropic.Anthropic(api_key=config.anthropic_api_key)
    messages = [{"role": "user", "content": f"Analyze this contract and provide your probability estimate.\n\nContract: {contract.title}"}]

    for iteration in range(MAX_TOOL_ITERATIONS):
        try:
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=4096,
                system=system_prompt,
                tools=available_tools,
                messages=messages,
            )
        except Exception as e:
            logger.error(f"Anthropic API error: {e}")
            return _mock_deep_dive(contract, config, tools_used, tools_failed, edge_result)

        # Process response
        assistant_content = response.content
        messages.append({"role": "assistant", "content": assistant_content})

        # Check if agent wants to use tools
        tool_uses = [block for block in assistant_content if block.type == "tool_use"]

        if not tool_uses:
            # Agent is done — extract result from text
            text_blocks = [block.text for block in assistant_content if block.type == "text"]
            full_text = "\n".join(text_blocks)
            return _parse_agent_output(full_text, contract, config, tools_used, tools_failed, edge_result, db)

        # Execute tool calls
        tool_results = []
        for tu in tool_uses:
            tool_name = tu.name
            tool_input = tu.input
            start = time.time()

            if tool_name in tool_map:
                try:
                    result = tool_map[tool_name].run(**tool_input)
                    latency = (time.time() - start) * 1000
                    if result["success"]:
                        tools_used.append(tool_name)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tu.id,
                            "content": json.dumps(result["data"])[:4000],
                        })
                    else:
                        tools_failed.append(tool_name)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tu.id,
                            "content": json.dumps({"status": "failed", "error": result.get("error", "unknown")}),
                            "is_error": True,
                        })
                    # Log tool run
                    db.insert_tool_run(ToolRun(
                        tool_name=tool_name,
                        contract_id=contract.id,
                        success=result["success"],
                        latency_ms=latency,
                        error_message=result.get("error", ""),
                    ))
                except Exception as e:
                    tools_failed.append(tool_name)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": json.dumps({"status": "failed", "error": str(e)}),
                        "is_error": True,
                    })
            else:
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": json.dumps({"status": "failed", "error": f"Unknown tool: {tool_name}"}),
                    "is_error": True,
                })

        messages.append({"role": "user", "content": tool_results})

    # Max iterations reached — force output
    messages.append({
        "role": "user",
        "content": "You've reached the maximum number of tool calls. Please provide your final probability estimate now as JSON.",
    })
    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            system=system_prompt,
            messages=messages,
        )
        text_blocks = [block.text for block in response.content if block.type == "text"]
        full_text = "\n".join(text_blocks)
        return _parse_agent_output(full_text, contract, config, tools_used, tools_failed, edge_result, db)
    except Exception as e:
        logger.error(f"Final Anthropic API call failed: {e}")
        return _mock_deep_dive(contract, config, tools_used, tools_failed, edge_result)


def _parse_agent_output(
    text: str,
    contract: Contract,
    config: Config,
    tools_used: list,
    tools_failed: list,
    edge_result,
    db: Database,
) -> DeepDiveResult:
    """Extract JSON from agent output, validate with pydantic, apply Kelly."""

    # Find JSON in the text
    json_str = _extract_json(text)
    if json_str is None:
        logger.warning("No JSON found in agent output — using fallback")
        return _mock_deep_dive(contract, config, tools_used, tools_failed, edge_result)

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        logger.warning("Invalid JSON in agent output — using fallback")
        return _mock_deep_dive(contract, config, tools_used, tools_failed, edge_result)

    # Apply Kelly criterion
    prob = max(0.07, min(0.93, float(data.get("probability", 0.5))))
    kelly_tool = KellyCriterionTool(mock_mode=config.mock_tools)
    kelly_result = kelly_tool.run(
        our_probability=prob,
        market_probability=contract.yes_price,
        bankroll=config.bankroll,
    )
    kelly_data = kelly_result.get("data", {}) if kelly_result.get("success") else {}

    edge = prob - contract.yes_price

    # Determine confidence — force "low" if <3 tools returned useful data
    unique_tools = list(set(tools_used))
    confidence = data.get("confidence", "medium")
    if len(unique_tools) < 3:
        confidence = "low"

    # Determine action
    abs_edge = abs(edge)
    if abs_edge < config.edge_threshold:
        action = "PASS"
    elif confidence == "low":
        action = "WATCH"
    elif edge > 0:
        action = "BET_YES"
    else:
        action = "BET_NO"

    now = datetime.utcnow().isoformat()

    result_dict = {
        "contract_id": str(contract.id or contract.source_id),
        "contract_title": contract.title,
        "model_probability": prob,
        "confidence": confidence,
        "edge": round(edge, 4),
        "kelly_fraction": kelly_data.get("capped_kelly", 0),
        "recommended_action": action,
        "key_factors": data.get("key_factors", [])[:5],
        "bull_case": data.get("bull_case", ""),
        "bear_case": data.get("bear_case", ""),
        "base_rate_used": float(data.get("base_rate_used", 0.5)),
        "modifiers_applied": data.get("modifiers_applied", []),
        "tools_used": unique_tools,
        "tools_failed": list(set(tools_failed)),
        "reasoning_trace": data.get("reasoning_trace", ""),
        "generated_at": now,
    }

    # Validate with pydantic
    try:
        validated = DeepDiveResult(**result_dict)
    except Exception as e:
        logger.warning(f"Pydantic validation failed: {e}. Attempting fix...")
        # Clamp probability
        result_dict["model_probability"] = max(0.07, min(0.93, result_dict["model_probability"]))
        if result_dict["confidence"] not in ("low", "medium", "high"):
            result_dict["confidence"] = "low"
        if result_dict["recommended_action"] not in ("PASS", "WATCH", "BET_YES", "BET_NO"):
            result_dict["recommended_action"] = "PASS"
        validated = DeepDiveResult(**result_dict)

    # Store to DB and link prediction via deep_dive_id FK
    try:
        dd_id = db.insert_deep_dive_result({
            "contract_id": contract.id,
            **validated.model_dump(),
        })
        # Also insert a prediction row with the deep_dive_id FK
        from database.models import Prediction
        pred = Prediction(
            contract_id=contract.id,
            model_prob=validated.model_probability,
            confidence=validated.confidence,
            edge=validated.edge,
            kelly_fraction=validated.kelly_fraction,
            recommendation=validated.recommended_action,
            key_factors=json.dumps(validated.key_factors),
            bull_case=validated.bull_case,
            bear_case=validated.bear_case,
            tools_used=json.dumps(validated.tools_used),
            tools_failed=json.dumps(validated.tools_failed),
        )
        pred_id = db.insert_prediction(pred)
        # Link the prediction to the deep dive result
        db.conn.execute(
            "UPDATE predictions SET deep_dive_id=? WHERE id=?",
            (dd_id, pred_id),
        )
        db.conn.commit()
    except Exception as e:
        logger.warning(f"Failed to store deep dive result: {e}")

    return validated


def _extract_json(text: str) -> str | None:
    """Extract JSON object from text, handling markdown code blocks."""
    import re
    # Try code block first
    match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if match:
        return match.group(1)
    # Try raw JSON
    match = re.search(r'\{[^{}]*"probability"[^{}]*\}', text, re.DOTALL)
    if match:
        return match.group(0)
    # Try finding any JSON object
    start = text.find('{')
    if start >= 0:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    return text[start:i+1]
    return None


def _mock_deep_dive(
    contract: Contract,
    config: Config,
    tools_used: list,
    tools_failed: list,
    edge_result,
) -> DeepDiveResult:
    """Generate a mock deep-dive result when no API key is available or for testing."""
    from model.base_rates import get_base_rate
    from model.data_modifiers import get_modifiers_for_contract
    from model.probability_model import estimate_probability
    import math

    source_id = getattr(contract, 'source_id', '') or ''
    base = get_base_rate(contract.category, source_id)
    market_prob = contract.yes_price

    # Get real data modifiers instead of a fake base rate blend
    data_mods = get_modifiers_for_contract(
        source_id=source_id,
        category=contract.category,
        market_price=market_prob,
        title=contract.title,
        close_time=contract.close_time,
    )

    # Run through the proper probability model
    estimate = estimate_probability(contract, modifiers=data_mods, config=config)
    blended = estimate.probability

    edge = blended - market_prob
    abs_edge = abs(edge)

    # Kelly
    kelly_tool = KellyCriterionTool(mock_mode=config.mock_tools)
    kelly_result = kelly_tool.run(
        our_probability=blended,
        market_probability=market_prob,
        bankroll=config.bankroll,
    )
    kelly_data = kelly_result.get("data", {}) if kelly_result.get("success") else {}

    unique_tools = list(set(tools_used))
    confidence = "low" if len(unique_tools) < 3 else "medium"

    if abs_edge < config.edge_threshold:
        action = "PASS"
    elif confidence == "low":
        action = "WATCH"
    elif edge > 0:
        action = "BET_YES"
    else:
        action = "BET_NO"

    # Generate realistic factors based on category
    factors_map = {
        "economics": ["Fed funds rate trajectory", "CPI trend", "Labor market conditions", "Market-implied probabilities", "Historical FOMC patterns"],
        "politics": ["Polling averages", "Historical precedent", "Expert consensus", "Incumbent advantage", "Media sentiment"],
        "crypto": ["BTC price momentum", "ETF inflow data", "On-chain metrics", "Regulatory environment", "Market sentiment"],
        "sports": ["Team form (last 5)", "Injury report", "Vegas line movement", "Home/away record", "Head-to-head history"],
        "science": ["Metaculus community prediction", "Expert panel consensus", "Historical resolution rate", "Technological readiness", "Funding trajectory"],
        "legal": ["Legal precedent", "Court composition", "Filing analysis", "Expert legal opinion", "Timeline constraints"],
    }
    factors = factors_map.get(contract.category, ["Base rate analysis", "Market price signal", "News sentiment"])[:5]

    modifiers = [
        {"name": m.name, "direction": "toward_yes" if m.direction > 0 else "toward_no" if m.direction < 0 else "neutral",
         "magnitude": "high" if m.weight > 0.5 else "medium" if m.weight > 0.2 else "low",
         "evidence": m.source}
        for m in data_mods
    ]
    if not modifiers:
        modifiers = [{"name": "market_price", "direction": "neutral", "magnitude": "high",
                       "evidence": f"No data modifiers — staying at market price {market_prob:.0%}"}]

    data_sources = [m.source.split(":")[0] for m in data_mods]

    return DeepDiveResult(
        contract_id=str(contract.id or contract.source_id),
        contract_title=contract.title,
        model_probability=blended,
        confidence=confidence,
        edge=round(edge, 4),
        kelly_fraction=kelly_data.get("capped_kelly", 0),
        recommended_action=action,
        key_factors=factors,
        bull_case=f"Data-driven estimate: {blended:.0%}. {len(data_mods)} real data modifiers applied from {', '.join(data_sources) or 'none'}.",
        bear_case=f"Uncertainty ±{base.uncertainty:.0%}. {len(data_mods)} data sources — {'sufficient' if len(data_mods) >= 2 else 'insufficient'} for high confidence.",
        base_rate_used=base.base_rate,
        modifiers_applied=modifiers,
        tools_used=unique_tools,
        tools_failed=list(set(tools_failed)),
        reasoning_trace=f"Market: {market_prob:.0%}. {len(data_mods)} data modifiers → model: {blended:.0%}. Edge: {edge:+.1%}. {'No real data — no edge.' if not data_mods else 'Data-backed edge.'}",
        generated_at=datetime.utcnow().isoformat(),
    )
