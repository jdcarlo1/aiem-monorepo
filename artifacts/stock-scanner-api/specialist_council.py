"""
specialist_council.py
---------------------------
Multi-agent negotiation system: takes opinions from several differently-
specialized sources for the SAME ticker/setup and negotiates them into one
final call — distinct from bull_bear_debate.py (which argues exactly 2
sides for one setup) and from market_regime_overlay.py (which is about the
broad market, not a specific ticker).

WHY THIS EXISTS
----------------
You already have several independent "specialist" sources of opinion on
any given setup: GARCH volatility regime, macro cross-asset context, social
sentiment, the bull/bear debate's synthesis, and your kill-switch/risk-gate
checks. Right now each one is a separate vote feeding loosely into a
conviction score. This module formalizes that into an actual NEGOTIATION:
each specialist's vote gets weighted by how reliable that *category* of
specialist has historically been (reusing the same Thompson-sampling
category track record from active_hypothesis_selection.py — so a
specialist whose past calls were usually right gets more say, while one
with a thin or poor track record gets less, automatically, without you
hand-tuning weights).

When specialists are in clear agreement, this just returns a weighted
verdict. When they're SPLIT, `negotiate()` escalates to `_llm_coordinate()`
— despite the name, this is a DETERMINISTIC confidence-weighted-mean
tie-breaker (no external AI/LLM call, zero added cost or latency; see its
own docstring below). This corrects an earlier version of this comment
that described it as an actual LLM call — it never was.

INTEGRATION (ILLUSTRATIVE ONLY — NOT THE LIVE WIRING)
------------------------------------------------------
The 4-specialist example below (garch_volatility/macro_rates/
bull_bear_debate/social_sentiment) was never implemented as shown; it is
kept here only to illustrate the SpecialistOpinion/negotiate() call shape.
Grep-verified (2026-07-10, Diagram-2 remediation): none of these 4 names
are ever constructed anywhere in the live codebase.
The other 3 illustrative specialists already run live in this system, but
as their OWN independent parallel score-adjustment paths, not as
SpecialistOpinion votes into this module: GARCH volatility regime is
consumed by market_regime_overlay.py (via volatility_clustering.py); the
bull/bear debate is main.py's own aiem_registry.py stage 15/19 source
(bull_bear_debate.run_bull_bear_debate / persist_debate); social sentiment
is main.py's own standalone StockTwits multiplier immediately above the
council call site. This is the intended, approved parallel-paths design
(per aiem_registry.py's stage map) — folding them into this module's vote
would double-count them, since each already applies its own live score
adjustment elsewhere.

CANONICAL COUNCIL FACTORY (Diagram-2 C23 remediation, 2026-07-10)
------------------------------------------------------------------
Audit finding: two independent call sites in main.py each inline-
constructed their OWN ad-hoc SpecialistOpinion list (never the same 2
names at both sites) and only shared compute_weighted_verdict() — this
violated the remediation directive's Section 3 Rule B ("no call site may
independently construct a partial council"). Fixed by adding
`run_council()` below as the ONE canonical construction entry point.
Both main.py call sites now call `run_council(context, ticker, inputs)`
instead of building `SpecialistOpinion` objects themselves:
  - main.py ~L41453 (paper-trade candidate scoring) calls
    run_council("candidate_entry", ...) → seats {signal_engine, fred_macro}.
  - main.py ~L43770 (4PM mark-to-market exit judgment) calls
    run_council("mtm_exit", ...) → seats {technicals, fred_macro}.
Reconciled council size is 3 total registered specialists across the
system (signal_engine, technicals, fred_macro), NOT 9 — the architecture
audit's "9" claim was never real (see COUNCIL_REGISTRY below); each
context seats 2 of the 3 by design (technicals doesn't exist pre-entry,
signal_engine's role is entry-only). This is a genuinely smaller VERIFIED
council per Section 3.C, not a count chosen to satisfy the audit — GARCH/
bull-bear/social-sentiment are deliberately excluded from voting (see
above) to avoid double-counting real signals that already vote elsewhere.
Vote math is unchanged from the pre-refactor inline code (verbatim
formulas moved into `_build_opinion()`); this was a wiring fix, not a
scoring change. Every run_council() call persists one evidence row to
`aiem_specialist_council_runs` (see `init_schema()`/`persist_council_run()`
below) recording registered/instantiated/invoked member counts and any
explicit abstentions, so a missing member can never silently look like a
full council (Section 3, "prevent missing-member silent success").

from specialist_council import SpecialistOpinion, negotiate
opinions = [
    SpecialistOpinion("garch_volatility", vote=garch_result["vote"],
                       confidence=0.7, reasoning=garch_result["reason"],
                       category="volatility"),
    SpecialistOpinion("macro_rates", vote=macro_votes[0]["vote"],
                       confidence=0.6, reasoning=macro_votes[0]["reason"],
                       category="macro"),
    SpecialistOpinion("bull_bear_debate", vote=debate_synthesis["conviction_adjustment"],
                       confidence=0.8, reasoning=debate_synthesis["key_disagreement"],
                       category="setup_specific"),
    SpecialistOpinion("social_sentiment", vote=sentiment_result["vote"],
                       confidence=0.4, reasoning=sentiment_result["reason"],
                       category="sentiment"),
]
final = negotiate(ticker, opinions)

Remember the wiring lesson if exposed as an agent-callable tool: schema +
dispatch dict, both, or it's unreachable.
"""

import os
import json
import re
from dataclasses import dataclass
from typing import List, Dict, Any, Optional

from active_hypothesis_selection import thompson_sample_category_value


@dataclass
class SpecialistOpinion:
    specialist_name: str
    vote: float
    confidence: float
    reasoning: str
    category: str


def _strip_json_fences(text: str) -> str:
    text = re.sub(r'^```(?:json)?\s*', '', text.strip())
    text = re.sub(r'\s*```$', '', text)
    return text.strip()


def compute_weighted_verdict(opinions: List[SpecialistOpinion]) -> Dict[str, Any]:
    """
    Weight = confidence * thompson_sampled_category_reliability.
    Returns a weighted average vote plus the variance across opinions —
    high variance is the signal that this needs the deterministic
    tie-breaker in _llm_coordinate() (NOT an actual LLM call, despite the
    name) rather than just trusting the weighted average.
    """
    if not opinions:
        return {"weighted_vote": 0.0, "variance": 0.0, "weights": {}}

    weights = {}
    weighted_sum = 0.0
    weight_total = 0.0

    for op in opinions:
        category_reliability = thompson_sample_category_value(op.category)
        weight = op.confidence * category_reliability
        weights[op.specialist_name] = round(weight, 4)
        weighted_sum += weight * op.vote
        weight_total += weight

    weighted_vote = weighted_sum / weight_total if weight_total > 0 else 0.0

    votes = [op.vote for op in opinions]
    mean_vote = sum(votes) / len(votes)
    variance = sum((v - mean_vote) ** 2 for v in votes) / len(votes)

    return {"weighted_vote": round(weighted_vote, 4), "variance": round(variance, 4), "weights": weights}


# ── Canonical council factory (Diagram-2 C23 remediation) ──────────────────
# One registry, one construction path — see module docstring "CANONICAL
# COUNCIL FACTORY" section for the audit context/why.

COUNCIL_REGISTRY: Dict[str, tuple] = {
    "candidate_entry": ("signal_engine", "fred_macro"),
    "mtm_exit": ("technicals", "fred_macro"),
}


def init_schema():
    """
    Create aiem_specialist_council_runs if it does not exist. One row per
    run_council() call — the evidence record required by remediation
    Section 3.D (registered/instantiated/invoked member counts, per-member
    vote/confidence, abstentions, aggregation result).
    """
    try:
        url = os.environ.get("DATABASE_URL", "")
        if not url:
            return
        import psycopg2
        with psycopg2.connect(url) as c, c.cursor() as cu:
            cu.execute("""
                CREATE TABLE IF NOT EXISTS aiem_specialist_council_runs (
                    id                  BIGSERIAL PRIMARY KEY,
                    run_time            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    context             TEXT NOT NULL,
                    ticker              TEXT NOT NULL,
                    trace_id            TEXT,
                    registered_members  JSONB NOT NULL,
                    invoked_members     JSONB NOT NULL,
                    abstained_members   JSONB NOT NULL,
                    abstentions         JSONB NOT NULL,
                    opinions            JSONB NOT NULL,
                    weighted_vote       DOUBLE PRECISION,
                    variance            DOUBLE PRECISION,
                    weights             JSONB
                )
            """)
            cu.execute("CREATE INDEX IF NOT EXISTS idx_council_runs_ticker ON aiem_specialist_council_runs (ticker)")
            cu.execute("CREATE INDEX IF NOT EXISTS idx_council_runs_context ON aiem_specialist_council_runs (context)")
            cu.execute("CREATE INDEX IF NOT EXISTS idx_council_runs_trace_id ON aiem_specialist_council_runs (trace_id)")
            c.commit()
    except Exception:
        pass


def _build_opinion(name: str, inputs: Dict[str, Any]):
    """
    Build one SpecialistOpinion from raw upstream inputs. Vote formulas are
    VERBATIM copies of the pre-refactor inline construction at each call
    site — this is a wiring fix (Rule B canonical-factory requirement), not
    a scoring change. Returns (opinion_or_None, abstention_reason_or_None).
    A member abstains (never fabricates a value) when its required upstream
    input is genuinely absent.
    """
    if name == "signal_engine":
        d = inputs.get("signal_engine") or {}
        if d.get("score") is None or not d.get("trade_type"):
            return None, "ABSTAINED_INSUFFICIENT_DATA: missing candidate score/trade_type"
        return SpecialistOpinion(
            specialist_name="signal_engine",
            vote=min(1.0, d["score"] / 20.0),
            confidence=0.80,
            reasoning=f"{d.get('source', '')}: {d.get('detail', '')}",
            category="options" if d.get("trade_type") == "CALL_OPTION" else "momentum",
        ), None

    if name == "fred_macro":
        mb = inputs.get("macro_bias")
        if mb is None:
            return None, "ABSTAINED_INSUFFICIENT_DATA: macro_bias unavailable"
        return SpecialistOpinion(
            specialist_name="fred_macro",
            vote=float(mb) * 0.5,
            confidence=0.65,
            reasoning="FRED yield curve + credit spread macro state",
            category="macro",
        ), None

    if name == "technicals":
        d = inputs.get("technicals")
        if not d:
            return None, "ABSTAINED_INSUFFICIENT_DATA: no technicals snapshot available"
        rsi_v = d.get("rsi")
        return SpecialistOpinion(
            specialist_name="technicals",
            vote=(-1.0 if (rsi_v and rsi_v > 70) else 1.0 if (rsi_v and rsi_v < 30) else 0.0),
            confidence=0.70,
            reasoning=f"RSI {rsi_v}, CMF {d.get('cmf')}, overall {d.get('overall')}",
            category="momentum",
        ), None

    return None, f"ABSTAINED_UNKNOWN_SPECIALIST: '{name}' has no _build_opinion mapping"


def persist_council_run(context: str, ticker: str, registered: tuple,
                         opinions: List[SpecialistOpinion], abstentions: List[Dict[str, Any]],
                         result: Dict[str, Any], trace_id: Optional[str] = None) -> Optional[int]:
    """
    Persist one run_council() evidence row. Never raises — callers must not
    have the live scoring path interrupted by a persistence issue.
    """
    try:
        url = os.environ.get("DATABASE_URL", "")
        if not url:
            return None
        import psycopg2
        with psycopg2.connect(url) as c, c.cursor() as cu:
            cu.execute("""
                INSERT INTO aiem_specialist_council_runs
                    (context, ticker, trace_id, registered_members, invoked_members,
                     abstained_members, abstentions, opinions, weighted_vote, variance, weights)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                context,
                ticker,
                trace_id,
                json.dumps(list(registered)),
                json.dumps([o.specialist_name for o in opinions]),
                json.dumps([a["specialist_name"] for a in abstentions]),
                json.dumps(abstentions),
                json.dumps([{"specialist_name": o.specialist_name, "vote": o.vote,
                              "confidence": o.confidence, "reasoning": o.reasoning,
                              "category": o.category} for o in opinions]),
                result.get("weighted_vote", 0.0),
                result.get("variance", 0.0),
                json.dumps(result.get("weights", {})),
            ))
            row = cu.fetchone()
            c.commit()
            return row[0] if row else None
    except Exception as _exc:
        print(f"[specialist_council.persist_council_run] {type(_exc).__name__}: {_exc}")
        return None


def run_council(context: str, ticker: str, inputs: Dict[str, Any],
                 trace_id: Optional[str] = None) -> Dict[str, Any]:
    """
    THE canonical Specialist Council construction entry point (Diagram-2
    C23 remediation). Every live call site must call this instead of
    constructing SpecialistOpinion objects itself (Section 3 Rule B).

    context: key into COUNCIL_REGISTRY — determines which registered
    members are seated for this call site. Context-varying membership from
    this ONE factory is compliant with Rule B (Rule B forbids call sites
    independently constructing councils; it does not mandate identical
    membership everywhere).
    inputs: raw upstream values keyed by specialist name (see
    _build_opinion for the expected shape per name), plus "macro_bias".
    trace_id: optional Diagram-2 root_trace_id, when the caller has one in
    scope (nullable — some call sites run before a trace_id is minted).

    Returns registered/invoked/abstained member lists, the weighted verdict,
    and the persisted evidence row ID — never fabricates a value for a
    missing member.
    """
    members = COUNCIL_REGISTRY.get(context, ())
    opinions: List[SpecialistOpinion] = []
    abstentions: List[Dict[str, Any]] = []

    for name in members:
        op, abstain_reason = _build_opinion(name, inputs)
        if op is not None:
            opinions.append(op)
        else:
            abstentions.append({"specialist_name": name, "reason": abstain_reason})

    result = compute_weighted_verdict(opinions)
    council_run_id = persist_council_run(context, ticker, members, opinions, abstentions, result, trace_id)

    return {
        "context": context,
        "council_run_id": council_run_id,
        "registered_members": list(members),
        "invoked_members": [o.specialist_name for o in opinions],
        "abstained_members": [a["specialist_name"] for a in abstentions],
        "abstentions": abstentions,
        "weighted_vote": result.get("weighted_vote", 0.0),
        "variance": result.get("variance", 0.0),
        "weights": result.get("weights", {}),
        "opinions": opinions,
    }


def _llm_coordinate(ticker: str, opinions: List[SpecialistOpinion],
                    context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Deterministic tie-breaker when specialist opinions diverge.
    Uses weighted mean of votes — no external AI calls, zero cost.
    """
    if not opinions:
        return {"weighted_vote": 0.0, "verdict": "NEUTRAL", "method": "deterministic"}
    
    total_weight = sum(o.confidence for o in opinions)
    if total_weight == 0:
        return {"weighted_vote": 0.0, "verdict": "NEUTRAL", "method": "deterministic"}
    
    weighted_vote = sum(o.vote * o.confidence for o in opinions) / total_weight
    
    # Determine verdict from weighted mean
    if weighted_vote >= 0.4:
        verdict = "BUY"
    elif weighted_vote >= 0.1:
        verdict = "LEAN_BUY"
    elif weighted_vote <= -0.4:
        verdict = "SELL"
    elif weighted_vote <= -0.1:
        verdict = "LEAN_SELL"
    else:
        verdict = "NEUTRAL"
    
    rationale = " | ".join(
        f"{o.specialist_name}({'+' if o.vote > 0 else ''}{o.vote:.1f}): {o.reasoning or ''}"
        for o in opinions[:3]
    )
    return {
        "weighted_vote": round(weighted_vote, 4),
        "verdict": verdict,
        "rationale": rationale,
        "n_specialists": len(opinions),
        "method": "deterministic_weighted_mean",
    }

def negotiate(ticker: str, opinions: List[SpecialistOpinion],
               disagreement_threshold: float = 0.35) -> Dict[str, Any]:
    """
    Main entry point. Computes the weighted verdict first (cheap, no
    external calls). If disagreement (variance) is below the threshold,
    returns that directly — the specialists agree, nothing to negotiate.
    If variance exceeds the threshold, escalates to `_llm_coordinate()`
    (a deterministic confidence-weighted-mean tie-breaker — NOT an actual
    LLM call, despite the name) and returns its result instead.

    disagreement_threshold: variance of raw votes (-1 to 1 scale) above
    which negotiation kicks in. 0.35 means specialists are meaningfully
    split, not just noisily disagreeing by a hair — tune this based on how
    often you want the tie-breaker actually invoked.
    """
    weighted_result = compute_weighted_verdict(opinions)

    if weighted_result["variance"] <= disagreement_threshold:
        return {
            "ticker": ticker,
            "negotiated": False,
            "final_vote": weighted_result["weighted_vote"],
            "reason": "specialists agreed (variance below threshold) — weighted average used directly",
            "specialist_weights": weighted_result["weights"],
            "disagreement_variance": weighted_result["variance"],
        }

    coordination = _llm_coordinate(ticker, opinions, weighted_result)
    return {
        "ticker": ticker,
        "negotiated": True,
        "final_vote": coordination.get("final_vote", weighted_result["weighted_vote"]),
        "confidence": coordination.get("confidence"),
        "deciding_factor": coordination.get("deciding_factor"),
        "unresolved_risk": coordination.get("unresolved_risk"),
        "raw_weighted_average": weighted_result["weighted_vote"],
        "specialist_weights": weighted_result["weights"],
        "disagreement_variance": weighted_result["variance"],
    }
