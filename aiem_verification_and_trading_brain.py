"""
aiem_verification_and_trading_brain.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Two responsibilities:
  1. TRADING BRAIN  — Wall Street-grade premarket logic for micro/small-cap
                      gap stocks, including the staleness filter from
                      staleness_filter.py
  2. VERIFICATION   — HMAC-signed Q&A that proves AIEM actually loaded and
                      understood the staleness code (not a hallucinated answer)

HMAC FLOW
─────────
  • Server calls  issue_challenge(question_id)
      → returns  {"challenge": "<nonce>", "question": "...", "sig": "<HMAC>"}
  • AIEM answers, then calls  verify_response(challenge_payload, answer)
      → returns  {"valid": bool, "verdict": str}
  • Signature covers  nonce + question_id + timestamp so replays / fakes fail.

AIEM_HMAC_SECRET must be set in the environment (Replit Secrets or env vars).
"""

import hashlib
import hmac
import json
import logging
import os
import re
import time
import uuid
from datetime import date, datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

HMAC_SECRET: bytes = os.environ.get("AIEM_HMAC_SECRET", "CHANGE_ME_IN_ENV").encode()
CHALLENGE_TTL_SECONDS = 300


# ═════════════════════════════════════════════════════════════════════════════
# PART 1 — WALL STREET TRADING BRAIN
# ═════════════════════════════════════════════════════════════════════════════

WALL_STREET_RULES = """
╔══════════════════════════════════════════════════════════════════════════════╗
║            AIEM — WALL STREET PREMARKET TRADING BRAIN                      ║
║            Speciality: Micro/Small-Cap | Premarket | High-Risk Gap Plays   ║
╠══════════════════════════════════════════════════════════════════════════════╣
║                                                                              ║
║  GOLDEN RULE #1 — THE MOVE IS ALREADY MADE                                 ║
║  Stocks that gap 30 %+ on Day 1 rarely sustain gains on Day 2 without a   ║
║  brand-new catalyst.  Yesterday's news is today's distribution.             ║
║                                                                              ║
║  GOLDEN RULE #2 — RVOL ALONE MEANS NOTHING ON DAY 2                       ║
║  High relative volume on Day 2 often means SELLERS are unloading into      ║
║  retail buyers who missed the move.  Volume must be paired with PRICE       ║
║  making new highs, not churning or fading.                                  ║
║                                                                              ║
║  GOLDEN RULE #3 — KNOW THE CATALYST HALF-LIFE                              ║
║  PIPE / private placement:  2-4 hrs.  Price at PIPE = hard resistance.     ║
║  Delisting notice gap:       < 1 hr.  Pure short squeeze, no fundamental.  ║
║  Clinical trial data:        12-24 hrs if Phase 2+; < 6 hrs if Phase 1.   ║
║  SPAC merger announcement:  12-48 hrs; trust value = hard floor.           ║
║  Reverse split:              Fade within 1-3 sessions, history shows.      ║
║                                                                              ║
║  GOLDEN RULE #4 — FLOAT IS EVERYTHING IN MICRO-CAP                        ║
║  Float < 1M shares:   violent moves both ways; ultra-thin liquidity.       ║
║  Float 1-5M shares:   classic momentum float; respect halts.               ║
║  Float > 20M shares:  needs institutional-size catalyst to sustain.        ║
║                                                                              ║
║  GOLDEN RULE #5 — PREMARKET LEVELS SET THE DAY                            ║
║  PM high = intraday resistance #1.                                          ║
║  PM low  = first support to watch at open.                                 ║
║  Stocks that fade PM highs 3x rarely reclaim them at open.                 ║
║                                                                              ║
║  GOLDEN RULE #6 — PIPE PRICE IS A CEILING                                 ║
║  Any stock gapping above its PIPE price will face selling from the          ║
║  institutional investor who is immediately in profit.  Treat PIPE price    ║
║  as a magnet; price returns to it more often than not.                     ║
║                                                                              ║
║  GOLDEN RULE #7 — DELISTING PLAYS ARE SHORT TRAPS                         ║
║  Stocks gapping on Nasdaq delisting notices (TNMG pattern) squeeze         ║
║  violently then collapse.  Risk/reward is asymmetric to the downside       ║
║  for any hold beyond the first 15-minute candle.                           ║
║                                                                              ║
║  PATTERN LIBRARY                                                            ║
║  ─────────────────────────────────────────────────────────────────────────  ║
║  PATTERN_STALE_GAP:         Gap >30% but catalyst >24h old → SKIP          ║
║  PATTERN_PIPE_FADE:         Gap above PIPE price → expect fade to PIPE     ║
║  PATTERN_DAY2_DISTRIBUTION: Day 2, above VWAP, volume fading → SKIP       ║
║  PATTERN_SPAC_MERGER_POP:   SPAC merger announce → buy near trust value   ║
║  PATTERN_DELISTING_SQUEEZE: Delisting notice → 15-min trade max, no hold  ║
║  PATTERN_REVERSE_SPLIT:     Rev split gap → fade within 3 sessions        ║
║  PATTERN_SYMPATHY_PLAY:     No own catalyst, moving with sector → SKIP    ║
║                                                                              ║
║  TODAY'S FIVE PICKS — POST-MORTEM LESSONS                                  ║
║  ─────────────────────────────────────────────────────────────────────────  ║
║  TNMG: PATTERN_DELISTING_SQUEEZE + PATTERN_STALE_GAP → SKIP               ║
║    Gap was Day 2 of delisting-driven squeeze. No new catalyst.             ║
║    Going concern, $1.9M cash, $918K equity. Pure retail trap.             ║
║                                                                              ║
║  DCOY: PATTERN_PIPE_FADE → Day-trade only with tight stop at PIPE $5.91   ║
║    $21M PIPE announced 6/26, closed 6/29.  Gap above $5.91 on 6/30        ║
║    means institutional seller is in profit. Catalyst age >24h = penalty.  ║
║                                                                              ║
║  CNET: PATTERN_SYMPATHY_PLAY → SKIP                                        ║
║    No fresh catalyst. Chinese micro-cap riding peer momentum.              ║
║    History: reverse split, Nasdaq compliance issues. Avoid Day 2.         ║
║                                                                              ║
║  KNDI: PATTERN_STALE_GAP → SKIP                                            ║
║    Catalyst (Xinchu acquisition) announced 6/22 — 8 days old by 6/30.    ║
║    Nasdaq minimum bid deficiency. Day 2 with no fresh news = distribution. ║
║                                                                              ║
║  JATT: PATTERN_SPAC_MERGER_POP → Near-trust-value entry only              ║
║    Merger with Talawar Tx announced 6/29. $225M PIPE at $10.             ║
║    Trust value ~$10. Only entry is near $10 floor, not chasing $12+.     ║
║                                                                              ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""


# ── PIPE price extraction from news text ─────────────────────────────────────

_PIPE_PRICE_RE = re.compile(
    r'\$\s*(\d+(?:\.\d{1,2})?)\s*(?:per\s+share|per\s+unit|private\s+placement)',
    re.IGNORECASE,
)

def _extract_pipe_price(news: list) -> float | None:
    """
    Scan Polygon news items for a PIPE / private placement price.
    Returns the first matched dollar figure, or None.
    """
    for item in (news or []):
        text = (item.get("title", "") + " " +
                item.get("description", "") + " " +
                item.get("article_url", "")).lower()
        if "private placement" in text or " pipe " in text:
            m = _PIPE_PRICE_RE.search(item.get("title", "") + " " +
                                      item.get("description", ""))
            if m:
                try:
                    return float(m.group(1))
                except ValueError:
                    pass
    return None


def _detect_catalyst_type(news: list) -> str | None:
    """
    Classify the primary catalyst from Polygon news headlines.
    Returns one of: delisting | reverse_split | spac_merger | pipe | clinical | None
    """
    for item in (news or []):
        t = (item.get("title", "") + " " + item.get("description", "")).lower()
        if any(w in t for w in ("delisting", "listing standards", "minimum bid",
                                "non-compliance notice")):
            return "delisting"
        if "reverse split" in t or "reverse stock split" in t:
            return "reverse_split"
        if any(w in t for w in ("spac", "de-spac", "business combination",
                                "merger agreement")):
            return "spac_merger"
        if "private placement" in t or " pipe " in t:
            return "pipe"
        if any(w in t for w in ("phase 1", "phase 2", "phase 3", "clinical",
                                "fda", "efficacy", "trial results")):
            return "clinical"
    return None


# ── apply_wall_street_pattern: enriches a signal dict with WS pattern tags ──

def apply_wall_street_pattern(ticker: str, signal: dict) -> dict:
    """
    Applies pattern-library rules on top of the base staleness filter.
    Adds pattern tags and further adjusts conviction.
    signal must contain: tags (list), final_conviction (int), move_day (int).
    Optional: pipe_price, catalyst_type.
    """
    tags       = list(signal.get("tags", []))
    conviction = float(signal.get("final_conviction", 0))
    notes      = list(signal.get("ws_notes", []))
    move_day   = signal.get("move_day", 1) or 1

    # Pattern: PIPE fade — stock trading above PIPE price means institutional
    # investor is immediately in profit and will unload into the move.
    pipe_price    = signal.get("pipe_price")
    current_price = signal.get("_cur_price")          # populated by with_data wrapper
    if pipe_price and current_price and current_price > pipe_price * 1.05:
        tags.append("PATTERN_PIPE_FADE")
        conviction -= 10
        pct = (current_price / pipe_price - 1) * 100
        notes.append(f"Trading {pct:.1f}% above PIPE price ${pipe_price:.2f}; "
                     f"institutional seller in profit.")

    # Pattern: sympathy play (catalyst >48h = no fresh own news)
    has_stale = any("CATALYST_STALE_48h" in t or "NO_CATALYST" in t for t in tags)
    if has_stale:
        tags.append("PATTERN_SYMPATHY_PLAY")
        conviction -= 5
        notes.append("No fresh own catalyst — sympathy or stale-news move only.")

    # Pattern: day-2 distribution
    if move_day >= 2 and "DAY2_EXTENDED_ABOVE_VWAP(-3)" in tags:
        tags.append("PATTERN_DAY2_DISTRIBUTION")
        conviction -= 5
        notes.append("Day-2 distribution: price above VWAP, volume declining.")

    # Pattern: delisting squeeze — violent collapse within 1-3 sessions
    cat_type = signal.get("catalyst_type")
    if cat_type == "delisting":
        tags.append("PATTERN_DELISTING_SQUEEZE")
        conviction -= 15
        notes.append("Delisting-driven squeeze: 15-min max trade, no hold. "
                     "No fundamental support.")

    # Pattern: reverse split — fade within 3 sessions historically
    if cat_type == "reverse_split":
        tags.append("PATTERN_REVERSE_SPLIT")
        conviction -= 20
        notes.append("Reverse split gap: historically fades within 3 sessions.")

    # Pattern: SPAC merger — trust value is the floor; only buy near floor
    if cat_type == "spac_merger":
        tags.append("PATTERN_SPAC_MERGER_POP")
        notes.append("SPAC merger pop: only valid near trust/NAV floor, "
                     "not chasing extended levels.")

    signal["final_conviction"] = max(0, round(conviction, 1))
    signal["tags"]             = list(set(tags))
    signal["ws_notes"]         = notes
    return signal


def apply_wall_street_pattern_with_data(
    ticker: str,
    signal: dict,
    history: list,
    news: list,
) -> dict:
    """
    Data-backed wrapper around apply_wall_street_pattern().
    Enriches signal with pipe_price, catalyst_type, and current price
    before running the pattern library.

    signal is the dict returned by staleness_filter.evaluate_signal_with_data().
    history and news are the same objects used in that call.
    """
    today_row = history[-1] if history else {}
    cur_price = float(today_row.get("close_price") or 0)

    enriched = dict(signal)
    enriched["_cur_price"]    = cur_price
    enriched["pipe_price"]    = _extract_pipe_price(news)
    enriched["catalyst_type"] = _detect_catalyst_type(news)

    # move_day may not be in signal if it came from staleness_filter
    if "move_day" not in enriched:
        scan_date  = datetime.now(timezone.utc).date()
        sd         = today_row.get("scan_date")
        if hasattr(sd, "date"):   gap_date = sd.date()
        elif isinstance(sd, date): gap_date = sd
        else:                      gap_date = scan_date
        delta = (scan_date - gap_date).days
        enriched["move_day"] = max(1, delta + 1)

    result = apply_wall_street_pattern(ticker, enriched)

    # Remove internal helper key before returning
    result.pop("_cur_price", None)
    return result


# ═════════════════════════════════════════════════════════════════════════════
# PART 2 — HMAC-SIGNED VERIFICATION SYSTEM
# ═════════════════════════════════════════════════════════════════════════════

VERIFICATION_QUESTIONS: dict[int, dict[str, Any]] = {
    1: {
        "question": (
            "When I scanned TNMG this morning and its gap originated yesterday, "
            "what action did you take and what exact reason string did you log?"
        ),
        "expected_keywords": ["SKIP", "GAP_IS_YESTERDAY"],
        "explanation": (
            "Tests whether is_stale_gap() correctly returns (True, 'GAP_IS_YESTERDAY') "
            "and whether evaluate_signal() short-circuits to action='SKIP'."
        ),
    },
    2: {
        "question": (
            "If a catalyst timestamp is 36 hours old, what conviction penalty "
            "do you apply and what tag gets added to the signal?"
        ),
        "expected_keywords": ["-2", "CATALYST_PENALTY"],
        "explanation": (
            "Tests catalyst_conviction_penalty(): 24 < 36 <= 48 → penalty = -2, "
            "tag = 'CATALYST_PENALTY(-2)'."
        ),
    },
    3: {
        "question": (
            "DCOY has a base conviction of 95.  The catalyst is 49 hours old. "
            "It is trading 6% above VWAP on decreasing volume on day 2 of its move. "
            "Walk me through the exact math — what is the final conviction score "
            "and what is your action?"
        ),
        "expected_keywords": ["83", "SKIP"],
        "explanation": (
            "49h old → penalty -4.  Day 2 above VWAP fading → -3.  "
            "PATTERN_DAY2_DISTRIBUTION → -5.  95 - 4 - 3 - 5 = 83. "
            "83 >= 70 threshold → PASS.  (If agent says SKIP, conviction math is wrong.)"
        ),
    },
    4: {
        "question": (
            "What is the conviction threshold you are currently using to decide "
            "PASS vs SKIP, and where in the code is that value set?"
        ),
        "expected_keywords": ["70", "CONVICTION_THRESHOLD", "evaluate_signal"],
        "explanation": (
            "CONVICTION_THRESHOLD = 70 is set inside evaluate_signal(). "
            "Agent must name both the value and the function."
        ),
    },
    5: {
        "question": (
            "If get_catalyst_timestamp() throws an exception for a ticker, "
            "what do you do — skip it, crash, or apply a penalty?  "
            "What penalty exactly?"
        ),
        "expected_keywords": ["-4", "warning", "max penalty"],
        "explanation": (
            "catalyst_conviction_penalty() wraps get_catalyst_timestamp() in "
            "try/except; on any exception it logs a warning and returns -4."
        ),
    },
    6: {
        "question": (
            "List every tag that can appear in the tags field of your output "
            "and tell me the exact condition that triggers each one."
        ),
        "expected_keywords": [
            "GAP_IS_YESTERDAY",
            "EXTENDED_FROM_GAP",
            "CATALYST_PENALTY",
            "MOVE_DAY_",
            "DAY2_EXTENDED_ABOVE_VWAP",
            "PATTERN_PIPE_FADE",
            "PATTERN_SYMPATHY_PLAY",
            "PATTERN_DAY2_DISTRIBUTION",
        ],
        "explanation": (
            "Agent must enumerate all eight tag families and their trigger conditions."
        ),
    },
}


def _hmac_sign(payload: str) -> str:
    """Return hex HMAC-SHA256 of payload using AIEM_HMAC_SECRET."""
    return hmac.new(HMAC_SECRET, payload.encode(), hashlib.sha256).hexdigest()


def issue_challenge(question_id: int) -> dict:
    """
    Generate a signed challenge for a given question.
    Returns a JSON-serialisable dict.

    {
        "question_id": 3,
        "nonce":       "<uuid4>",
        "issued_at":   1234567890,
        "question":    "...",
        "sig":         "<hmac-hex>"      ← covers nonce + question_id + issued_at
    }
    """
    if question_id not in VERIFICATION_QUESTIONS:
        raise ValueError(
            f"Unknown question_id {question_id}. "
            f"Valid: {sorted(VERIFICATION_QUESTIONS)}"
        )

    nonce     = str(uuid.uuid4())
    issued_at = int(time.time())
    payload   = f"{nonce}:{question_id}:{issued_at}"
    sig       = _hmac_sign(payload)

    challenge = {
        "question_id": question_id,
        "nonce":       nonce,
        "issued_at":   issued_at,
        "question":    VERIFICATION_QUESTIONS[question_id]["question"],
        "sig":         sig,
    }
    logger.info("Challenge issued for Q%d  nonce=%s", question_id, nonce)
    return challenge


def verify_response(challenge: dict, aiem_answer: str) -> dict:
    """
    Verify that:
      (a) the challenge signature is authentic (wasn't forged)
      (b) the challenge hasn't expired
      (c) the answer contains the expected keywords

    Returns:
    {
        "valid":        bool,
        "verdict":      str,
        "missing_keys": list[str],
        "explanation":  str
    }
    """
    question_id  = challenge.get("question_id")
    nonce        = challenge.get("nonce")
    issued_at    = challenge.get("issued_at")
    received_sig = challenge.get("sig", "")

    # (a) Verify signature
    expected_payload = f"{nonce}:{question_id}:{issued_at}"
    expected_sig     = _hmac_sign(expected_payload)

    if not hmac.compare_digest(expected_sig, received_sig):
        return {
            "valid":        False,
            "verdict":      "INVALID_SIGNATURE — challenge was forged or tampered with.",
            "missing_keys": [],
            "explanation":  VERIFICATION_QUESTIONS.get(question_id, {}).get("explanation", ""),
        }

    # (b) Check TTL
    age = int(time.time()) - issued_at
    if age > CHALLENGE_TTL_SECONDS:
        return {
            "valid":        False,
            "verdict":      f"CHALLENGE_EXPIRED — issued {age}s ago (TTL {CHALLENGE_TTL_SECONDS}s).",
            "missing_keys": [],
            "explanation":  VERIFICATION_QUESTIONS.get(question_id, {}).get("explanation", ""),
        }

    # (c) Keyword check
    q_meta    = VERIFICATION_QUESTIONS[question_id]
    keywords  = q_meta["expected_keywords"]
    lower_ans = aiem_answer.lower()
    missing   = [kw for kw in keywords if kw.lower() not in lower_ans]

    if missing:
        verdict = f"PARTIAL — AIEM answer is missing expected content: {missing}"
        valid   = False
    else:
        verdict = "VERIFIED ✓ — AIEM answer contains all expected content."
        valid   = True

    return {
        "valid":        valid,
        "verdict":      verdict,
        "missing_keys": missing,
        "explanation":  q_meta["explanation"],
    }


def run_all_challenges() -> list[dict]:
    """
    Issue all 6 challenges in sequence and return their challenge dicts.
    Paste each one to AIEM, then call verify_response() with its answer.
    """
    challenges = []
    for qid in sorted(VERIFICATION_QUESTIONS):
        ch = issue_challenge(qid)
        challenges.append(ch)
        print(f"\n{'─' * 70}")
        print(f"  CHALLENGE Q{qid}  (nonce={ch['nonce'][:8]}…)")
        print(f"  Question: {ch['question']}")
        print(f"  Sig:      {ch['sig'][:16]}…")
    return challenges


# ═════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print(WALL_STREET_RULES)

    print("\n" + "═" * 70)
    print("  ISSUING VERIFICATION CHALLENGES FOR AIEM")
    print("═" * 70)
    challenges = run_all_challenges()

    # Demo verify (simulated correct answer for Q2)
    print("\n" + "═" * 70)
    print("  DEMO VERIFY — Q2 (simulated AIEM answer)")
    print("═" * 70)
    demo_answer = (
        "The catalyst_conviction_penalty function returns -2 for a 36-hour-old "
        "catalyst because 24 < 36 <= 48.  The tag CATALYST_PENALTY(-2) is added "
        "to the signal's tags list."
    )
    result = verify_response(challenges[1], demo_answer)
    print(json.dumps(result, indent=2))

    # Demo verify (forged challenge — should fail)
    print("\n" + "═" * 70)
    print("  DEMO VERIFY — Forged challenge (should fail)")
    print("═" * 70)
    forged = dict(challenges[0])
    forged["sig"] = "deadbeef" * 8
    result2 = verify_response(forged, "SKIP, GAP_IS_YESTERDAY")
    print(json.dumps(result2, indent=2))
