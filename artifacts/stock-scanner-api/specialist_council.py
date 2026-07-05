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
verdict — no need to spend an LLM call negotiating something that isn't
actually contested. When they're SPLIT, it escalates to an LLM coordinator
that reads every specialist's actual reasoning (not just their vote number)
and produces a final call with an explanation of what tipped it — this is
the "negotiation" part, reserved for when there's something to negotiate.

INTEGRATION
-----------
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
    high variance is the signal that this needs LLM-level negotiation
    rather than just trusting the weighted average.
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
    Main entry point. Computes the weighted verdict first (cheap, no LLM
    call). If disagreement (variance) is below the threshold, returns that
    directly — the specialists agree, nothing to negotiate. If variance
    exceeds the threshold, escalates to the LLM coordinator and returns
    its negotiated call instead.

    disagreement_threshold: variance of raw votes (-1 to 1 scale) above
    which negotiation kicks in. 0.35 means specialists are meaningfully
    split, not just noisily disagreeing by a hair — tune this based on how
    often you want the LLM coordinator actually invoked.
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
