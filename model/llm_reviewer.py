"""LLM Reviewer — multi-model sanity check layer.

Before any trade executes, an LLM reviews:
1. Does the data support this trade?
2. Is the P&L forecast reasonable?
3. Are there risks the model missed?

Periodically reviews:
4. Code health (import errors, test failures)
5. Strategy performance (are we actually making money?)
6. Data feed quality (stale data, anomalies)

Uses available LLM APIs. Currently: Claude (Anthropic).
Architecture supports adding: GPT (OpenAI), Grok (xAI), Gemini (Google).
"""
import os
import json
import time
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class ReviewResult:
    approved: bool
    confidence: float   # 0-1
    reasoning: str
    risks: list         # identified risks
    suggestion: str     # what to do differently
    model: str          # which LLM reviewed
    latency_ms: int


def _get_anthropic_client():
    try:
        import anthropic
        key = os.environ.get('ANTHROPIC_API_KEY') or os.environ.get('ANTHROPIC') or ''
        if not key:
            return None
        return anthropic.Anthropic(api_key=key)
    except:
        return None


def _call_claude(system: str, prompt: str, max_tokens: int = 500) -> str:
    """Call Claude Sonnet for review. Returns raw text."""
    client = _get_anthropic_client()
    if not client:
        return ''
    try:
        r = client.messages.create(
            model='claude-sonnet-4-6', max_tokens=max_tokens,
            system=system,
            messages=[{'role': 'user', 'content': prompt}])
        return r.content[0].text
    except Exception as e:
        logger.warning(f"Claude review failed: {e}")
        return ''


def review_trade(
    ticker: str, side: str, shares: int, price: float,
    spot_price: float, strike: float, hours_to_expiry: float,
    edge: float, win_probability: float, expected_pnl: float,
    intel_direction: float, intel_signals: list,
    data_modifiers: list = None,
) -> ReviewResult:
    """Ask an LLM to sanity-check a trade before execution."""
    t0 = time.time()

    system = """You are a risk manager for a Kalshi prediction market trading fund.
Review this proposed trade and respond with ONLY valid JSON:
{"approved": true/false, "confidence": 0.0-1.0, "reasoning": "one sentence",
 "risks": ["risk1", "risk2"], "suggestion": "what to do differently"}
Be skeptical. Flag anything suspicious. Real money is at stake."""

    prompt = f"""PROPOSED TRADE:
{side.upper()} {ticker} — {shares} shares @ ${price:.2f}
Strike: ${strike:,.2f} | Spot: ${spot_price:,.2f} | Distance: {abs(spot_price-strike)/spot_price*100:.1f}%
Hours to expiry: {hours_to_expiry:.1f}
Edge (vol model): {edge*100:+.1f}pp
Win probability: {win_probability:.0%}
Expected P&L: ${expected_pnl:+.2f}

MARKET INTEL:
Direction: {intel_direction:+.2f} ({'bullish' if intel_direction > 0.2 else 'bearish' if intel_direction < -0.2 else 'neutral'})
Signals: {', '.join(str(s) for s in (intel_signals or []))}

DATA MODIFIERS: {json.dumps(data_modifiers or [], default=str)[:200]}

Should we execute this trade?"""

    text = _call_claude(system, prompt)
    if not text:
        return ReviewResult(approved=True, confidence=0.0, reasoning='LLM unavailable, defaulting to approve',
                           risks=['no review performed'], suggestion='', model='none',
                           latency_ms=int((time.time()-t0)*1000))

    try:
        # Parse JSON from response
        import re
        match = re.search(r'\{[^}]+\}', text, re.DOTALL)
        if match:
            d = json.loads(match.group())
        else:
            d = json.loads(text)
        return ReviewResult(
            approved=d.get('approved', True),
            confidence=float(d.get('confidence', 0.5)),
            reasoning=d.get('reasoning', ''),
            risks=d.get('risks', []),
            suggestion=d.get('suggestion', ''),
            model='claude-sonnet-4-6',
            latency_ms=int((time.time()-t0)*1000),
        )
    except:
        return ReviewResult(approved=True, confidence=0.3, reasoning=text[:200],
                           risks=['could not parse LLM response'], suggestion='',
                           model='claude-sonnet-4-6', latency_ms=int((time.time()-t0)*1000))


def review_portfolio(positions: list, balance: float, total_exposure: float,
                     realized_pnl: float, intel: dict) -> str:
    """Periodic portfolio review — ask LLM for strategic advice."""
    system = """You are a portfolio strategist for a Kalshi prediction market fund.
Review the current portfolio and give actionable advice in 3-5 bullet points.
Be specific: which positions to worry about, what to do, what opportunities to look for.
Keep it under 200 words. Real money."""

    pos_summary = '\n'.join(
        f"  {p.get('side','?')} {p.get('ticker','?')} {p.get('shares',0)}sh "
        f"entry={p.get('entry',0):.2f} fair={p.get('fair',0):.2f} ev=${p.get('ev',0):+.2f}"
        for p in (positions or [])[:15]
    )

    intel_summary = '\n'.join(
        f"  {name}: dir={v.get('dir',0):+.2f} conv={v.get('conv',0):.0%} vol={v.get('vol','?')}"
        for name, v in (intel or {}).items()
    )

    prompt = f"""PORTFOLIO:
Balance: ${balance:,.2f}
Exposure: ${total_exposure:,.2f}
Realized P&L: ${realized_pnl:+,.2f}

POSITIONS:
{pos_summary}

MARKET INTEL:
{intel_summary}

What should we do?"""

    return _call_claude(system, prompt, max_tokens=300)


def generate_trade_ideas(
    balance: float, total_exposure: float, realized_pnl: float,
    positions: list, intel: dict, available_markets: list,
    recent_settlements: list = None,
) -> list[dict]:
    """Ask LLM to propose NEW trade ideas based on the full picture.

    Returns list of: {ticker, side, reason, conviction, size_suggestion}
    These get fed back into the top of the stack: edge check → P&L forecast → review → execute.
    """
    system = """You are a quantitative trader for a Kalshi prediction market fund.
Given the portfolio, market intel, and available contracts, propose 1-3 specific trades.

For each trade, respond with ONLY a JSON array:
[{"ticker": "EXACT_TICKER", "side": "yes" or "no", "reason": "1 sentence why",
  "conviction": 0.0-1.0, "size_pct": 0.01-0.05}]

Rules:
- Only propose trades on tickers from the AVAILABLE MARKETS list
- Consider correlation with existing positions (don't double up on same risk)
- Prefer high risk/reward (cheap contracts with asymmetric payoff)
- Factor in market intel direction and conviction
- Consider time to expiry — shorter = more certain, longer = more uncertain
- Real money. Be specific. No generic advice."""

    pos_text = '\n'.join(
        f"  {p.get('side','?')} {p.get('ticker','?')} {p.get('shares',0)}sh "
        f"entry={p.get('entry',0):.2f} ev=${p.get('ev',0):+.2f}"
        for p in (positions or [])[:10]
    )

    intel_text = '\n'.join(
        f"  {name}: {v.get('dir',0):+.2f} ({'bull' if v.get('dir',0)>0.2 else 'bear' if v.get('dir',0)<-0.2 else 'flat'}) "
        f"conv={v.get('conv',0):.0%} vol={v.get('vol','?')}"
        for name, v in (intel or {}).items()
    )

    mkt_text = '\n'.join(
        f"  {m.get('ticker','')} yes={m.get('yes_ask',0):.0%} no={m.get('no_ask',0):.0%} "
        f"hours={m.get('hours',0):.0f} strike={m.get('strike',0)}"
        for m in (available_markets or [])[:30]
    )

    settlements_text = ''
    if recent_settlements:
        settlements_text = '\nRECENT SETTLEMENTS (learn from these):\n' + '\n'.join(
            f"  {s}" for s in recent_settlements[:10])

    prompt = f"""PORTFOLIO: balance=${balance:,.0f} exposure=${total_exposure:,.0f} realized_pnl=${realized_pnl:+,.0f}

CURRENT POSITIONS:
{pos_text}

MARKET INTEL:
{intel_text}

AVAILABLE MARKETS (can trade these):
{mkt_text}
{settlements_text}

Propose 1-3 trades. JSON array only."""

    text = _call_claude(system, prompt, max_tokens=400)
    if not text:
        return []

    try:
        import re
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if match:
            ideas = json.loads(match.group())
            # Validate
            valid = []
            valid_tickers = {m.get('ticker', '') for m in (available_markets or [])}
            for idea in ideas:
                if idea.get('ticker') in valid_tickers and idea.get('side') in ('yes', 'no'):
                    valid.append(idea)
            return valid
        return []
    except:
        return []


def review_code_health() -> str:
    """Ask LLM to review recent errors and suggest fixes."""
    import sqlite3
    try:
        c = sqlite3.connect('/home/jovyan/shared/sm/prediction-agent-db/prediction_agent.db')
        errors = c.execute(
            "SELECT ts, msg FROM agent_log WHERE lvl='ERROR' ORDER BY ts DESC LIMIT 10"
        ).fetchall()
        c.close()
        error_text = '\n'.join(f"[{ts}] {msg}" for ts, msg in errors)
    except:
        error_text = 'Could not read error log'

    system = """You are a senior engineer reviewing a trading system's error logs.
Identify: (1) recurring issues, (2) critical failures, (3) suggested fixes.
Keep it under 150 words. Be specific about file names and error types."""

    return _call_claude(system, f"Recent errors:\n{error_text}", max_tokens=200)
