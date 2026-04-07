"""Builds system prompt dynamically for the deep-dive agent.

Injects: contract metadata, base rates, scanner edge signal,
cross-market divergence, modifier checklist, and output schema.
"""

from __future__ import annotations

from database.models import Contract
from model.base_rates import get_base_rate

# Never call twitter/social sentiment for these categories — irrelevant and noisy
TWITTER_EXCLUDED_CATEGORIES = {"economics", "legal"}


def build_system_prompt(
    contract: Contract,
    market_prob: float,
    scanner_edge: float | None = None,
    cross_market_note: str | None = None,
    prediction_history_data: dict | None = None,
) -> str:
    base = get_base_rate(contract.category)

    history_section = ""
    if prediction_history_data:
        cat_hist = prediction_history_data.get("category_history")
        if cat_hist:
            history_section = f"""
## Prediction History (this category)
- Category: {cat_hist.get('category', 'unknown')}
- Historical contracts: {cat_hist.get('total_contracts', 0)}
- Model accuracy on resolved: {cat_hist.get('model_accuracy', 0):.1%}
- Average Brier score: {cat_hist.get('avg_brier', 0):.4f}
- Historical YES resolution rate: {cat_hist.get('base_rate_yes', 0):.1%}
"""
        similar = prediction_history_data.get("similar_contracts", [])
        if similar:
            history_section += "\n## Similar Past Contracts\n"
            for s in similar[:3]:
                history_section += (
                    f"- \"{s.get('title', '')}\" (similarity: {s.get('similarity', 0):.0%}) — "
                    f"model: {s.get('model_prob', 'N/A')}, resolved: {'YES' if s.get('resolved_yes') else 'NO'}, "
                    f"correct: {'YES' if s.get('correct') else 'NO'}\n"
                )
        exact = prediction_history_data.get("exact_match")
        if exact:
            history_section += f"\nEXACT MATCH FOUND: \"{exact.get('title', '')}\" resolved {'YES' if exact.get('resolution') else 'NO'}.\n"

    edge_section = ""
    if scanner_edge is not None:
        direction = "YES is underpriced" if scanner_edge > 0 else "NO is underpriced"
        edge_section = f"""
## Scanner Edge Signal
- Edge: {scanner_edge:+.1%} ({direction})
- This contract was flagged because |edge| > threshold.
"""

    cross_market_section = ""
    if cross_market_note:
        cross_market_section = f"""
## Cross-Market Divergence (HIGH PRIORITY)
{cross_market_note}
Markets disagreeing is itself information — investigate why.
"""

    twitter_note = ""
    if contract.category.lower() in TWITTER_EXCLUDED_CATEGORIES:
        twitter_note = "\nDo NOT use the twitter_sentiment tool — it is irrelevant and noisy for this category."

    return f"""You are a superforecaster research agent analyzing a prediction market contract.

## Contract
- Title: {contract.title}
- Category: {contract.category}
- Market probability (YES): {market_prob:.1%}
- Volume 24h: ${contract.volume_24h:,.0f}
- Close time: {contract.close_time.isoformat() if contract.close_time else 'unknown'}
- Source: {contract.source} ({contract.source_id})

## Category Base Rate
- Category: {base.category}
- Base rate: {base.base_rate:.0%}
- Uncertainty: ±{base.uncertainty:.0%}
- Source type: {base.source_type}
- Note: {base.source_note}
{history_section}{edge_section}{cross_market_section}
## Your Process

Work through this like a superforecaster:

1. **Start with the base rate** ({base.base_rate:.0%} for {base.category}).
2. **Gather evidence** using the available tools. Call the most relevant tools for this category.
3. **Apply modifiers one at a time**. For each piece of evidence, state:
   - What the evidence is
   - Which direction it pushes (toward YES or NO)
   - How much weight to give it (low/medium/high)
4. **Check for inside vs outside view conflicts**. If your adjusted estimate differs significantly from the market, explain why you think the market is wrong.
5. **State your final probability** with a confidence level.

## Modifier Checklist (confirm or deny each that applies)
- [ ] Base rate anchor: starting from {base.base_rate:.0%}
- [ ] Market price signal: current market says {market_prob:.1%}
- [ ] Recent news: any developments that shift probability?
- [ ] Expert consensus: what do domain experts say?
- [ ] Historical precedent: has this type of event happened before?
- [ ] Time to resolution: how does proximity to close date affect certainty?
- [ ] Cross-market data: do other prediction markets agree?
- [ ] Quantitative indicators: any hard data (polls, economic data, odds)?
{twitter_note}
## Constraints
- Probability must be between 7% and 93% (caps enforced)
- If fewer than 3 tools returned useful data, set confidence to "low"
- Do not use kelly_criterion tool — it is applied automatically after your estimate
- Show your reasoning BEFORE your final estimate

## Output

After your research, output ONLY valid JSON matching this exact schema. No prose outside the JSON.

```json
{{
  "probability": <float 0.07-0.93>,
  "confidence": "<low|medium|high>",
  "key_factors": ["<factor1>", "<factor2>", ...],
  "bull_case": "<1-2 sentences>",
  "bear_case": "<1-2 sentences>",
  "base_rate_used": <float>,
  "modifiers_applied": [
    {{"name": "<modifier>", "direction": "<toward_yes|toward_no>", "magnitude": "<low|medium|high>", "evidence": "<brief>"}}
  ],
  "reasoning_trace": "<your step-by-step reasoning>"
}}
```"""


def get_tool_exclusions(category: str) -> list[str]:
    """Return list of tool names to exclude for this category."""
    exclusions = ["kelly_criterion"]  # always excluded from agent tools (applied after)
    if category.lower() in TWITTER_EXCLUDED_CATEGORIES:
        exclusions.append("twitter_sentiment")
    return exclusions
