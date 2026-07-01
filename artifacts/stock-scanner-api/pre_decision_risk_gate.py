"""
pre_decision_risk_gate.py
----------------------------
The final checkpoint before any recommendation reaches your weekly email.
Pulls together everything else in this package into one synthesis step,
and — critically — forces a devil's advocate pass that argues AGAINST the
pick before it's allowed through.

What it checks, in order:

  1. Does market_regime_overlay say sit out or reduce? If so, this alone
     can downgrade or block a pick regardless of how good the underlying
     signal looks — a great signal in a bad market regime is still a bad trade.
  2. Does kill_switch show the agent is currently halted? If halted, NO
     recommendation goes out at all, full stop.
  3. Does adversarial_critique's rule-based checks flag anything about the
     specific hypothesis behind this pick (sample size, look-ahead risk)?
  4. A devil's advocate LLM pass: given the pick and its reasoning, argue
     specifically why this could be wrong — not a generic disclaimer, a
     pick-specific case against it.
  5. Cross-signal agreement check: if multiple INDEPENDENT signal layers
     point the same direction, that's stronger evidence than one signal
     firing alone.

Output is always APPROVED, APPROVED_WITH_CAUTION, or BLOCKED — never a
silent pass-through. Every gate decision is logged.

REQUIRES: AIEM_DATABASE_URL. ANTHROPIC_API_KEY for devil's advocate pass
(falls back to rule-based-only if unavailable).
"""

import os
import json
import datetime as dt
from typing import Dict, Any, List, Optional

import psycopg2
import psycopg2.extras

import decision_logger    as dl
import kill_switch        as ks
import market_regime_overlay as mro
import adversarial_critique  as ac
import position_reconciler as pr
import daily_loss_limit    as dll
import order_dedup         as od

try:
    import anthropic
    _HAS_ANTHROPIC = True
except ImportError:
    _HAS_ANTHROPIC = False


DDL = """
CREATE TABLE IF NOT EXISTS risk_gate_decisions (
    id SERIAL PRIMARY KEY,
    ticker TEXT NOT NULL,
    signal_name TEXT NOT NULL,
    gate_decision TEXT NOT NULL CHECK (gate_decision IN ('APPROVED', 'APPROVED_WITH_CAUTION', 'BLOCKED')),
    reasons JSONB NOT NULL,
    devils_advocate_argument TEXT,
    cross_signal_agreement_count INT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def _connect():
    url = os.environ.get("AIEM_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("Neither AIEM_DATABASE_URL nor DATABASE_URL is set.")
    return psycopg2.connect(url)


def init_schema():
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(DDL)
        conn.commit()
    print("[pre_decision_risk_gate] schema ready")


DEVILS_ADVOCATE_SYSTEM_PROMPT = """You are a skeptical risk manager reviewing
a SPECIFIC trade recommendation before it's sent to a retail trader. Your
only job is to argue why THIS SPECIFIC pick could go wrong — not generic
market disclaimers. Be concrete: name the specific scenario, catalyst, or
weakness in the reasoning that could make this pick lose money.

Respond ONLY in JSON:
{
  "strongest_objection": "the single most concrete reason this could be wrong",
  "what_would_invalidate_this": "a specific event/data point that would prove the pick wrong",
  "severity": "low" | "moderate" | "high"
}
"""


def devils_advocate_pass(ticker: str, signal_name: str,
                          reasoning: str, conviction_score: float) -> Dict[str, Any]:
    if not _HAS_ANTHROPIC or not os.environ.get("ANTHROPIC_API_KEY"):
        return {
            "strongest_objection": "Devil's advocate LLM pass unavailable (no API key) — rule-based checks only ran.",
            "what_would_invalidate_this": "N/A",
            "severity": "moderate",
        }

    try:
        client  = anthropic.Anthropic()
        payload = {"ticker": ticker, "signal_name": signal_name,
                   "reasoning": reasoning, "conviction_score": conviction_score}

        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=400,
            system=DEVILS_ADVOCATE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": json.dumps(payload)}],
        )
        text = "".join(b.text for b in response.content if hasattr(b, "text")).strip()
    except Exception as e:
        # Any failure calling the Anthropic API (timeout, rate limit, auth
        # error, network error, SDK error, malformed response, etc.) must
        # NOT propagate unguarded and must NOT be silently treated as "no
        # objection found." Fail toward more caution, not less: this returns
        # the most severe category this function supports ("high"), with an
        # explicit error flag so callers can distinguish "the LLM raised a
        # real objection" from "we don't actually know because the call
        # failed." Do not downgrade this to "moderate" or swallow it silently.
        return {
            "strongest_objection": (
                f"Devil's advocate LLM pass FAILED with an error "
                f"({type(e).__name__}: {e}) — no evaluation was performed. "
                f"Treat this pick as unreviewed, not as having passed a "
                f"devil's advocate check."
            ),
            "what_would_invalidate_this": "N/A — API call errored before any evaluation ran",
            "severity": "high",
            "error": True,
        }

    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text.split("\n", 1)[-1]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"strongest_objection": text,
                "what_would_invalidate_this": "unparsed", "severity": "moderate"}


def cross_signal_agreement(ticker: str, signal_votes: Dict[str, str]) -> Dict[str, Any]:
    """signal_votes: {"dark_pool": "bullish", "gamma": "bullish", "sweep": "neutral", ...}
    Counts how many INDEPENDENT signals agree — a single-signal pick is
    meaningfully weaker evidence than three independent signals agreeing."""
    bullish = [n for n, v in signal_votes.items() if v == "bullish"]
    bearish = [n for n, v in signal_votes.items() if v == "bearish"]

    direction       = "bullish" if len(bullish) >= len(bearish) else "bearish"
    agreeing        = bullish if direction == "bullish" else bearish
    agreement_count = len(agreeing)

    return {
        "agreement_count":   agreement_count,
        "agreeing_signals":  agreeing,
        "direction":         direction,
        "single_signal_only": agreement_count <= 1,
    }


def run_risk_gate(
    ticker: str,
    signal_name: str,
    reasoning: str,
    conviction_score: float,
    n_trades_in_backtest: int,
    win_rate_in_backtest: float,
    hypothesis_parameters: Dict[str, Any],
    universe_description: str,
    signal_votes: Optional[Dict[str, str]]       = None,
    market_regime_result: Optional[Dict[str, Any]] = None,
    decision_id: Optional[int]                   = None,
) -> Dict[str, Any]:
    """The actual gate. Call on EVERY candidate recommendation before it's
    allowed into the weekly email. Returns APPROVED / APPROVED_WITH_CAUTION /
    BLOCKED — never a silent pass-through."""
    reasons          = []
    blocking_reasons = []

    _db_url = os.environ.get("DATABASE_URL", "")

    if not _db_url:
        # Fail CLOSED, not open: without DATABASE_URL we cannot verify position
        # reconciliation, the daily loss limit, or order dedup — all three are
        # safety-critical, so an unreachable DB blocks trading instead of
        # silently skipping these checks.
        blocking_reasons.append(
            "DATABASE_URL is not set — position reconciliation, daily loss limit, "
            "and order dedup checks cannot run. Failing closed: trading blocked "
            "until DB configuration is fixed."
        )
    else:
        # 0a. Position reconciliation — unresolved broker/DB mismatch blocks all new orders
        if pr.has_unresolved_mismatch(_db_url):
            blocking_reasons.append(
                "Unresolved position mismatch between broker and DB — trading blocked until resolved."
            )

        # 0b. Daily loss limit circuit breaker — pure math, no broker dependency
        _dll_result = dll.check_daily_loss_limit(_db_url)
        if _dll_result["halt_trading"]:
            blocking_reasons.append(
                f"Daily loss limit breached: {_dll_result['loss_pct']}% "
                f"(limit: -{_dll_result['loss_limit_pct']}%). Trading halted for today."
            )

        # 0c. Order dedup — if this decision_id already placed an order, block the duplicate
        if decision_id is not None and not od.should_place_order(_db_url, decision_id):
            blocking_reasons.append(
                f"Decision ID {decision_id} already has an order in order_execution_log — duplicate blocked."
            )

    # 1. Kill switch — hard block, no exceptions
    halt_reason = ks._is_currently_halted()
    if halt_reason:
        blocking_reasons.append(f"Kill switch is currently halted: {halt_reason}")

    # 2. Market regime
    if market_regime_result is None:
        reasons.append("No market regime check provided — proceeding without this input, treat with extra caution.")
    elif market_regime_result.get("recommendation") == "sit_out":
        blocking_reasons.append(
            f"Market regime overlay recommends sitting out: {market_regime_result.get('plain_language_summary')}"
        )
    elif market_regime_result.get("recommendation") == "reduce_exposure":
        reasons.append(
            f"Market regime overlay recommends reduced exposure: {market_regime_result.get('plain_language_summary')}"
        )

    # 3. Rule-based adversarial critique on the hypothesis
    rule_flags = ac.run_rule_based_critique(
        n_trades_in_backtest, win_rate_in_backtest,
        hypothesis_parameters, universe_description,
    )
    if rule_flags:
        reasons.extend(rule_flags)

    # 4. Devil's advocate pass
    devils_advocate = devils_advocate_pass(ticker, signal_name, reasoning, conviction_score)
    if devils_advocate.get("severity") == "high":
        reasons.append(f"Devil's advocate (high severity): {devils_advocate.get('strongest_objection')}")
    elif devils_advocate.get("severity") == "moderate":
        reasons.append(f"Devil's advocate (moderate): {devils_advocate.get('strongest_objection')}")

    # 5. Cross-signal agreement
    agreement = None
    if signal_votes:
        agreement = cross_signal_agreement(ticker, signal_votes)
        if agreement["single_signal_only"]:
            reasons.append(
                f"Only {agreement['agreement_count']} signal(s) agree on direction "
                f"— single-signal pick, lower-confidence by nature."
            )

    # Decision
    if blocking_reasons:
        decision = "BLOCKED"
    elif len(reasons) >= 3 or devils_advocate.get("severity") == "high":
        decision = "APPROVED_WITH_CAUTION"
    else:
        decision = "APPROVED"

    all_reasons = blocking_reasons + reasons

    result = {
        "ticker":                 ticker,
        "signal_name":            signal_name,
        "gate_decision":          decision,
        "reasons":                all_reasons,
        "devils_advocate":        devils_advocate,
        "cross_signal_agreement": agreement,
    }

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO risk_gate_decisions
                    (ticker, signal_name, gate_decision, reasons,
                     devils_advocate_argument, cross_signal_agreement_count)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    ticker, signal_name, decision, json.dumps(all_reasons),
                    devils_advocate.get("strongest_objection"),
                    agreement["agreement_count"] if agreement else None,
                ),
            )
        conn.commit()

    dl.log_decision(
        signal_name=signal_name,
        decision_type="no_trade" if decision == "BLOCKED" else "trade",
        reasoning=(
            f"Risk gate: {decision}. "
            f"Reasons: {'; '.join(all_reasons) if all_reasons else 'none — clean pass'}"
        ),
        confidence=conviction_score,
        input_state_snapshot={**result, "ticker": ticker},
    )

    return result


def get_recent_gate_decisions(limit: int = 50) -> List[Dict[str, Any]]:
    """Pull recent gate history. If BLOCKED/APPROVED_WITH_CAUTION almost
    never appears, the gate isn't doing real work — thresholds may need
    tightening."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM risk_gate_decisions ORDER BY created_at DESC LIMIT %s",
                (limit,),
            )
            return [dict(r) for r in cur.fetchall()]


if __name__ == "__main__":
    init_schema()
    print("pre_decision_risk_gate schema ready.")
    print("Call run_risk_gate() on EVERY candidate pick before it goes into the weekly email.")
