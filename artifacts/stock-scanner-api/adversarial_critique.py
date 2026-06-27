"""
adversarial_critique.py
-------------------------
After AIEM finds a result it likes, runs a structured adversarial pass
that actively tries to break the finding — hunting for look-ahead bias,
survivorship bias, small-sample flukes, and confounds.

Uses Anthropic's API for the LLM-assisted critique. Falls back to
rule-based checks if no API key is available.
"""

import os
import json
import math
from typing import Dict, Any, List, Optional

try:
    import anthropic
    _HAS_ANTHROPIC = True
except ImportError:
    _HAS_ANTHROPIC = False


def check_sample_size(n_trades: int, min_n: int = 30) -> Optional[str]:
    if n_trades < min_n:
        return (
            f"Sample size is only {n_trades} trades (below the {min_n}-trade floor). "
            f"Win rates on small samples swing wildly — a 75% WR on 12 trades means "
            f"almost nothing statistically."
        )
    return None


def check_implied_significance(win_rate: float, n_trades: int, baseline: float = 0.5) -> Optional[str]:
    if n_trades == 0:
        return "No trades to evaluate."
    se = math.sqrt(baseline * (1 - baseline) / n_trades)
    z  = (win_rate - baseline) / se if se > 0 else 0
    if abs(z) < 1.96:
        return (
            f"Win rate of {win_rate:.1%} on {n_trades} trades is NOT statistically "
            f"distinguishable from a 50% baseline (z={z:.2f}). This could easily be noise."
        )
    return None


def check_lookahead_risk(parameters: Dict[str, Any]) -> Optional[str]:
    suspicious_terms = ["same_day_close", "eod_label", "future_", "next_day_actual"]
    text = json.dumps(parameters).lower()
    hits = [t for t in suspicious_terms if t in text]
    if hits:
        return (
            f"Parameters reference fields that sound like they could leak future "
            f"information at decision time: {hits}. Verify these were genuinely "
            f"available at the moment the signal would have fired."
        )
    return None


def check_survivorship_bias(universe_description: str) -> Optional[str]:
    flags = ["current s&p", "today's universe", "current holdings", "active tickers only"]
    text  = universe_description.lower()
    hits  = [f for f in flags if f in text]
    if hits:
        return (
            "Universe description suggests backtest may only include tickers that "
            "survived to today, excluding delisted/failed companies. "
            "This inflates win rates — confirm a point-in-time universe was used."
        )
    return None


def run_rule_based_critique(
    n_trades: int,
    win_rate: float,
    parameters: Dict[str, Any],
    universe_description: str,
) -> List[str]:
    checks = [
        check_sample_size(n_trades),
        check_implied_significance(win_rate, n_trades),
        check_lookahead_risk(parameters),
        check_survivorship_bias(universe_description),
    ]
    return [c for c in checks if c]


CRITIC_SYSTEM_PROMPT = """You are an adversarial quantitative researcher whose
sole job is to find reasons a proposed trading signal's backtest result is
WRONG, OVERFIT, or MISLEADING. You are not trying to be balanced — you are
trying to break this finding. Look specifically for:

1. Look-ahead bias: could any input have used information not actually
   available at the time the signal would have fired?
2. Survivorship bias: does the universe only include tickers that exist today?
3. Multiple-comparisons: how many variations were likely tried before landing
   on this one, and does the reported significance account for that?
4. Regime dependency: is this result concentrated in a narrow time window
   corresponding to one market regime rather than a durable, repeatable edge?
5. Confounds: is there a simpler, boring explanation (general market beta,
   sector momentum, earnings season clustering) that explains the result as
   well as the proposed signal does?

Respond ONLY in JSON with this exact structure:
{
  "verdict": "likely_real" | "likely_overfit" | "inconclusive",
  "top_concerns": ["concern 1", "concern 2"],
  "what_would_change_your_mind": "specific test or data that would resolve the biggest concern"
}"""


def run_llm_critique(
    hypothesis_name: str,
    parameters: Dict[str, Any],
    n_trades: int,
    win_rate: float,
    test_window: str,
    universe_description: str,
) -> Dict[str, Any]:
    if not _HAS_ANTHROPIC or not os.environ.get("ANTHROPIC_API_KEY"):
        return {
            "verdict": "inconclusive",
            "top_concerns": ["LLM critique unavailable (no API key/library) — rule-based checks only."],
            "what_would_change_your_mind": "Set ANTHROPIC_API_KEY to enable full adversarial critique.",
        }

    try:
        client = anthropic.Anthropic()
        user_payload = {
            "hypothesis_name":      hypothesis_name,
            "parameters":           parameters,
            "n_trades":             n_trades,
            "win_rate":             win_rate,
            "test_window":          test_window,
            "universe_description": universe_description,
        }
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1000,
            system=CRITIC_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": json.dumps(user_payload, indent=2)}],
        )
        text = "".join(block.text for block in response.content if hasattr(block, "text")).strip()
        if text.startswith("```"):
            text = text.strip("`")
            text = text.split("\n", 1)[-1] if text.lower().startswith("json") else text
        return json.loads(text)
    except Exception as e:
        return {
            "verdict": "inconclusive",
            "top_concerns": [f"LLM critique failed: {e}"],
            "what_would_change_your_mind": "Fix API connectivity and re-run.",
        }


def adversarial_review(
    hypothesis_name: str,
    parameters: Dict[str, Any],
    n_trades: int,
    win_rate: float,
    test_window: str,
    universe_description: str,
) -> Dict[str, Any]:
    rule_flags   = run_rule_based_critique(n_trades, win_rate, parameters, universe_description)
    llm_result   = run_llm_critique(
        hypothesis_name, parameters, n_trades, win_rate, test_window, universe_description
    )
    overall_verdict = llm_result.get("verdict", "inconclusive")
    if rule_flags and overall_verdict == "likely_real":
        overall_verdict = "inconclusive"

    return {
        "hypothesis_name":   hypothesis_name,
        "rule_based_flags":  rule_flags,
        "llm_critique":      llm_result,
        "overall_verdict":   overall_verdict,
        "recommendation": (
            "Do not promote to shadow trading until concerns are addressed."
            if overall_verdict != "likely_real"
            else "Eligible for shadow trading window (still requires human sign-off)."
        ),
    }
