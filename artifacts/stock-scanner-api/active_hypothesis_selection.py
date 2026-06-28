"""
active_hypothesis_selection.py
---------------------------
Active/Bayesian hypothesis prioritization layer for AIEM.

WHY THIS EXISTS
----------------
hypothesis_registry.py forces every hypothesis to be pre-registered before
testing (good — prevents p-hacking), but nothing currently decides WHICH
hypothesis AIEM should spend its limited iteration budget registering and
testing next. Right now that order is whatever the LLM happens to think of
first in a given session.

This module adds a priority layer in front of registration: given a list of
candidate hypotheses AIEM is considering, rank them by expected information
value BEFORE spending iteration budget (and a hypothesis_registry slot,
which counts toward your Bonferroni correction denominator) testing them.

Two real signals combine to produce that ranking:

1. NOVELTY — how different is this candidate from hypotheses you've already
   tested? Re-testing near-duplicates wastes budget and inflates your
   multiple-comparisons count for no new information. Measured via embedding
   similarity against your already-LOCKED hypotheses (reuses the same
   text-embedding-3-small approach as search_past_findings, just scoped to
   hypothesis descriptions instead of weekly research summaries).

2. CATEGORY TRACK RECORD — among hypotheses similar to this one (same broad
   category — e.g. "dark_pool", "gamma", "options_flow"), what fraction
   historically came back likely_real vs likely_overfit/inconclusive from
   adversarial_review? Modeled as a Beta distribution per category and
   sampled via Thompson sampling — this naturally balances two things: favor
   categories with a strong track record (exploit), but don't starve
   under-tested categories of a chance to prove out (explore), because a
   category with few past tests has a wide, uncertain Beta distribution that
   will occasionally sample high.

The combined score = thompson_sampled_category_value * novelty. Cheap to
compute, no new infrastructure beyond one new small table for caching
hypothesis-description embeddings (same pattern as aiem_finding_embeddings).

INTEGRATION
-----------
This is meant to run BEFORE _aiem_tool_register_hypotheses is called, not
as a replacement for it. Feed it whatever candidate hypotheses the agent is
currently considering registering this session, get back a ranked list, and
only register/test the top N given the session's iteration budget.

If exposed as its own tool to the agent (e.g. "rank_hypothesis_candidates"),
remember the wiring lesson from the tool-map audit: add it to BOTH the
relevant _AIEM_AGENT_TOOLS-style schema AND the dispatch dict in whichever
agent loop(s) should be able to call it, or it'll be invisible/unreachable
like the 52 tools found earlier.
"""

import os
import json
import random
import datetime as dt
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

import psycopg2
import psycopg2.extras


def _connect():
    url = os.environ.get("AIEM_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("No database URL found (set AIEM_DATABASE_URL or DATABASE_URL).")
    return psycopg2.connect(url)


EMBEDDING_DDL = """
CREATE TABLE IF NOT EXISTS hypothesis_candidate_embeddings (
    hypothesis_hash TEXT PRIMARY KEY,
    description TEXT,
    embedding JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
"""


@dataclass
class HypothesisCandidate:
    name: str
    description: str
    category: str
    parameters: Dict[str, Any]
    estimated_n_trades: int = 30
    estimated_universe_pct: float = 0.1
    novelty_score: Optional[float] = field(default=None, init=False)
    category_value: Optional[float] = field(default=None, init=False)
    combined_score: Optional[float] = field(default=None, init=False)


def categorize(name: str) -> str:
    name_lower = name.lower()
    known_categories = [
        "dark_pool", "gamma", "charm", "squeeze", "sweep", "float",
        "options", "sector", "vwap", "breakout", "gap", "intraday",
        "momentum", "volume", "regime", "divergence", "accumulation",
    ]
    for cat in known_categories:
        if cat in name_lower:
            return cat
    return "uncategorized"


def _embed(text: str) -> List[float]:
    from openai import OpenAI
    client = OpenAI(
        base_url=os.environ.get("AI_INTEGRATIONS_OPENAI_BASE_URL", "https://ai-integrations.replit.com/openai"),
        api_key=os.environ.get("AI_INTEGRATIONS_OPENAI_API_KEY", ""),
    )
    resp = client.embeddings.create(model="text-embedding-3-small", input=str(text)[:2000])
    return resp.data[0].embedding


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    import numpy as np
    a_arr, b_arr = np.array(a), np.array(b)
    denom = (np.linalg.norm(a_arr) * np.linalg.norm(b_arr))
    if denom == 0:
        return 0.0
    return float(np.dot(a_arr, b_arr) / denom)


def _get_locked_hypothesis_embeddings() -> List[Dict[str, Any]]:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(EMBEDDING_DDL)
        conn.commit()

        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT hr.hypothesis_hash, hr.description,
                       hce.embedding
                FROM hypothesis_registry hr
                LEFT JOIN hypothesis_candidate_embeddings hce
                    ON hce.hypothesis_hash = hr.hypothesis_hash
                WHERE hr.locked = TRUE
            """)
            rows = cur.fetchall()

        results = []
        for row in rows:
            if row["embedding"]:
                results.append({"hash": row["hypothesis_hash"], "embedding": row["embedding"]})
                continue
            try:
                emb = _embed(row["description"] or "")
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO hypothesis_candidate_embeddings
                            (hypothesis_hash, description, embedding)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (hypothesis_hash) DO UPDATE
                            SET embedding = EXCLUDED.embedding
                    """, (row["hypothesis_hash"], row["description"], json.dumps(emb)))
                conn.commit()
                results.append({"hash": row["hypothesis_hash"], "embedding": emb})
            except Exception as e:
                print(f"[active_hypothesis_selection] embedding failed for "
                      f"{row['hypothesis_hash'][:8]}: {e}")
        return results


def compute_novelty(candidate: HypothesisCandidate) -> float:
    try:
        candidate_emb = _embed(candidate.description)
    except Exception as e:
        print(f"[active_hypothesis_selection] could not embed candidate "
              f"'{candidate.name}': {e} — defaulting novelty to 0.5 (neutral)")
        return 0.5

    locked = _get_locked_hypothesis_embeddings()
    if not locked:
        return 1.0

    max_sim = max(_cosine_similarity(candidate_emb, row["embedding"]) for row in locked)
    return max(0.0, 1.0 - max_sim)


def get_category_track_record(category: str) -> Dict[str, int]:
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT name, result
                FROM hypothesis_registry
                WHERE locked = TRUE AND result IS NOT NULL
            """)
            rows = cur.fetchall()

    successes, failures = 0, 0
    for row in rows:
        if categorize(row["name"]) != category:
            continue
        result = row["result"] if isinstance(row["result"], dict) else {}
        verdict = result.get("overall_verdict", "inconclusive")
        if verdict == "likely_real":
            successes += 1
        else:
            failures += 1

    return {"successes": successes, "failures": failures}


def thompson_sample_category_value(category: str) -> float:
    record = get_category_track_record(category)
    alpha = record["successes"] + 1
    beta = record["failures"] + 1
    return random.betavariate(alpha, beta)


def rank_candidates(candidates: List[HypothesisCandidate]) -> List[HypothesisCandidate]:
    for c in candidates:
        c.category = c.category or categorize(c.name)
        c.novelty_score = compute_novelty(c)
        c.category_value = thompson_sample_category_value(c.category)
        c.combined_score = (
            c.category_value * c.novelty_score * max(c.estimated_universe_pct, 0.01)
        )
    return sorted(candidates, key=lambda c: c.combined_score, reverse=True)


def select_top_n(candidates: List[HypothesisCandidate], n: int) -> List[HypothesisCandidate]:
    ranked = rank_candidates(candidates)
    return ranked[:n]


def explain_ranking(ranked_candidates: List[HypothesisCandidate]) -> str:
    lines = []
    for i, c in enumerate(ranked_candidates, 1):
        lines.append(
            f"{i}. {c.name} [{c.category}] — score={c.combined_score:.3f} "
            f"(novelty={c.novelty_score:.2f}, category_value={c.category_value:.2f}, "
            f"universe_impact={c.estimated_universe_pct:.2f})"
        )
    return "\n".join(lines)
