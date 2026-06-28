"""
bull_bear_debate.py
---------------------------
Bull/bear synthesis debate for a specific ticker/setup.

WHY THIS EXISTS
----------------
adversarial_critique.py already does cross-model adversarial review on
BACKTEST RESULTS (is this hypothesis's win rate real or overfit?). This
module does something related but distinct: for a SPECIFIC live setup
AIEM is considering right now, it builds the strongest possible bull case
and the strongest possible bear case as two separate, independently-
generated arguments — then synthesizes them into a single verdict.

This matters because a single model (even a good one) tends to anchor on
whichever direction its first read leans toward, then under-weight
disconfirming evidence afterward. Forcing two FULL, independently-built
cases — not just "any concerns?" — surfaces things a single pass would
gloss over. Same cross-model principle as adversarial_critique.py: the
bull and bear arguments are built by DIFFERENT model families, since two
prompts to the same model checking each other still share that model's
blind spots.

This is NOT a replacement for market_regime_overlay.py (overall market
risk-on/risk-off) or a bull/bear MARKET trend classifier (multi-week/month
SPY trend state) — those are about the broader market. This module is
about whether THIS specific ticker/setup has a real edge right now, argued
from both sides.

REQUIRES: same AI_INTEGRATIONS env vars already used elsewhere
(AI_INTEGRATIONS_OPENAI_API_KEY/BASE_URL, ANTHROPIC_API_KEY), AIEM_DATABASE_URL.

INTEGRATION
-----------
Call run_bull_bear_debate(ticker, signal_context) before finalizing a high-
conviction signal. Pass the synthesis result into input_state_snapshot
when you call decision_logger.log_decision() for the actual trade/no_trade
call, so the full debate is preserved alongside the decision it informed —
don't log it as its own decision_type, since decision_logger's schema only
allows ('trade','no_trade','hold','exit').

Remember the wiring lesson if this becomes an agent-callable tool: add it
to both the schema AND the dispatch dict of whichever loop should use it.
"""

import os
import re
import json
import datetime as dt
from typing import Dict, Any, Optional

import psycopg2
import psycopg2.extras


def _connect():
    url = os.environ.get("AIEM_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("No database URL found (set AIEM_DATABASE_URL or DATABASE_URL).")
    return psycopg2.connect(url)


DDL = """
CREATE TABLE IF NOT EXISTS bull_bear_debates (
    id SERIAL PRIMARY KEY,
    debate_time TIMESTAMPTZ NOT NULL DEFAULT now(),
    ticker TEXT NOT NULL,
    signal_context JSONB NOT NULL,
    bull_argument TEXT,
    bear_argument TEXT,
    synthesis JSONB,
    verdict TEXT
);
CREATE INDEX IF NOT EXISTS idx_bull_bear_debates_ticker ON bull_bear_debates(ticker);
"""


def init_schema():
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(DDL)
        conn.commit()
    print("[bull_bear_debate] schema ready")


BULL_SYSTEM_PROMPT = """You are an aggressive, conviction-driven bull case
researcher. Given the signal data for a ticker, build the STRONGEST possible
case for why this setup is bullish and worth taking. Use only the data
provided — do not invent facts. Be specific: cite the actual numbers given.
Do not hedge or present both sides; that is not your job here. Respond ONLY
in JSON:
{"thesis": "2-4 sentences, specific to the numbers provided",
 "strongest_point": "the single most compelling piece of evidence",
 "catalyst": "what would need to happen for this to play out"}"""

BEAR_SYSTEM_PROMPT = """You are a skeptical short-seller researcher whose
job is to find every reason this setup could fail or is being
misread. Given the same signal data, build the STRONGEST possible case
against taking this trade. Use only the data provided — do not invent
facts. Be specific: cite the actual numbers given, and look especially for
overfit-looking patterns, thin/illiquid conditions, or signals that could
be coincidental. Do not hedge; that is not your job here. Respond ONLY in
JSON:
{"thesis": "2-4 sentences, specific to the numbers provided",
 "strongest_point": "the single most damaging piece of counter-evidence",
 "risk": "what would need to happen for this to fail"}"""

SYNTHESIS_SYSTEM_PROMPT = """You are a neutral synthesis judge reviewing a
bull case and a bear case for the same trading setup, built independently
by two different researchers. Your job is NOT to split the difference —
weigh the actual strength of evidence on each side and reach a real
conclusion. If one side is clearly stronger, say so. If both sides raise
genuinely unresolved concerns, say that too — "CONFLICTED" is a legitimate
and useful verdict, not a cop-out. Respond ONLY in JSON:
{"verdict": "BULLISH_LEAN" | "BEARISH_LEAN" | "CONFLICTED" | "NO_EDGE",
 "conviction_adjustment": <float from -1.0 to 1.0, where positive favors
   the bull case and negative favors the bear case, scaled by how decisive
   the evidence actually was>,
 "key_disagreement": "what the bull and bear case actually disagree about",
 "what_would_resolve_it": "the single piece of additional data or event
   that would most clearly settle this"}"""


def _strip_json_fences(text: str) -> str:
    text = re.sub(r'^```(?:json)?\s*', '', text.strip())
    text = re.sub(r'\s*```$', '', text)
    return text.strip()


def _call_openai(system_prompt: str, payload: Dict[str, Any], model: str = "gpt-5.4") -> Dict[str, Any]:
    from openai import OpenAI
    client = OpenAI(
        api_key=os.environ.get("AI_INTEGRATIONS_OPENAI_API_KEY", ""),
        base_url=os.environ.get("AI_INTEGRATIONS_OPENAI_BASE_URL", "https://ai-integrations.replit.com/openai"),
    )
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(payload, indent=2, default=str)},
        ],
        max_completion_tokens=600,
    )
    raw = _strip_json_fences(resp.choices[0].message.content or "{}")
    return json.loads(raw)


def _call_claude(system_prompt: str, payload: Dict[str, Any], model: str = "claude-sonnet-4-5") -> Dict[str, Any]:
    import anthropic
    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=model,
        max_tokens=600,
        system=system_prompt,
        messages=[{"role": "user", "content": json.dumps(payload, indent=2, default=str)}],
    )
    text = "".join(block.text for block in resp.content if hasattr(block, "text")).strip()
    return json.loads(_strip_json_fences(text))


def build_bull_case(ticker: str, signal_context: Dict[str, Any]) -> Dict[str, Any]:
    """Built via gpt-5.4 — your existing reasoning-tier model for AIEM's
    other agent loops, used here as one of the two independent voices."""
    try:
        payload = {"ticker": ticker, **signal_context}
        return _call_openai(BULL_SYSTEM_PROMPT, payload)
    except Exception as e:
        return {"thesis": f"[bull case generation failed: {e}]", "strongest_point": None, "catalyst": None}


def build_bear_case(ticker: str, signal_context: Dict[str, Any]) -> Dict[str, Any]:
    """Built via Claude Sonnet — deliberately a DIFFERENT model family than
    the bull case, same principle as adversarial_critique.py: two prompts
    to the same model checking each other still share that model's blind
    spots, so the opposing case needs genuinely different model weights."""
    try:
        payload = {"ticker": ticker, **signal_context}
        return _call_claude(BEAR_SYSTEM_PROMPT, payload)
    except Exception as e:
        return {"thesis": f"[bear case generation failed: {e}]", "strongest_point": None, "risk": None}


def synthesize_debate(ticker: str, bull_case: Dict[str, Any], bear_case: Dict[str, Any],
                       signal_context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Synthesis runs on whichever model wasn't used for either side, to avoid
    the judge sharing blind spots with either advocate. Since bull=gpt-5.4
    and bear=claude-sonnet-4-5 above, synthesis uses gpt-4o-mini (cheap,
    you already use it elsewhere for lighter tasks, and it's distinct
    enough in this role — it's just weighing two already-built arguments,
    not generating novel analysis from raw data).
    """
    try:
        payload = {
            "ticker": ticker,
            "bull_case": bull_case,
            "bear_case": bear_case,
            "underlying_signal_context": signal_context,
        }
        from openai import OpenAI
        client = OpenAI(
            api_key=os.environ.get("AI_INTEGRATIONS_OPENAI_API_KEY", ""),
            base_url=os.environ.get("AI_INTEGRATIONS_OPENAI_BASE_URL", "https://ai-integrations.replit.com/openai"),
        )
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYNTHESIS_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, indent=2, default=str)},
            ],
            max_completion_tokens=400,
        )
        raw = _strip_json_fences(resp.choices[0].message.content or "{}")
        return json.loads(raw)
    except Exception as e:
        return {
            "verdict": "CONFLICTED",
            "conviction_adjustment": 0.0,
            "key_disagreement": f"synthesis failed: {e}",
            "what_would_resolve_it": "retry synthesis once API issue is resolved",
        }


def run_bull_bear_debate(ticker: str, signal_context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Full pipeline: build bull case (gpt-5.4) + bear case (Claude Sonnet)
    independently, synthesize (gpt-4o-mini), store the full transcript for
    audit, and return the result. signal_context should be whatever
    quantitative data is driving this candidate signal (the same kind of
    dict you'd pass to log_decision's input_state_snapshot) — both
    advocates see the exact same data, so any disagreement comes from
    interpretation, not from one side having more information.
    """
    bull_case = build_bull_case(ticker, signal_context)
    bear_case = build_bear_case(ticker, signal_context)
    synthesis = synthesize_debate(ticker, bull_case, bear_case, signal_context)

    record = {
        "ticker": ticker,
        "signal_context": signal_context,
        "bull_argument": json.dumps(bull_case, default=str),
        "bear_argument": json.dumps(bear_case, default=str),
        "synthesis": synthesis,
        "verdict": synthesis.get("verdict", "CONFLICTED"),
    }

    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(DDL)
                cur.execute("""
                    INSERT INTO bull_bear_debates
                        (ticker, signal_context, bull_argument, bear_argument, synthesis, verdict)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (
                    record["ticker"], json.dumps(signal_context, default=str),
                    record["bull_argument"], record["bear_argument"],
                    json.dumps(synthesis, default=str), record["verdict"],
                ))
            conn.commit()
    except Exception as e:
        print(f"[bull_bear_debate] failed to store debate record: {e}")

    return {
        "ticker": ticker,
        "bull_case": bull_case,
        "bear_case": bear_case,
        "synthesis": synthesis,
    }


def get_debate_history(ticker: str, limit: int = 20) -> list:
    """Pull past debates for a given ticker — useful for checking whether
    AIEM has flip-flopped on the same name, or whether a verdict pattern is
    emerging across multiple sessions."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT debate_time, verdict, synthesis
                FROM bull_bear_debates
                WHERE ticker = %s
                ORDER BY debate_time DESC
                LIMIT %s
            """, (ticker, limit))
            return [dict(r) for r in cur.fetchall()]
