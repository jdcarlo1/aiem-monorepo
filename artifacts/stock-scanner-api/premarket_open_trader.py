"""
premarket_open_trader.py
====================================================================
Combines opening-snapshot pattern classification with TODAY's other
verified modules (synthesis confluence, earnings buffer, correlation
risk, regime, slippage) into ONE final decision: enter_now,
wait_until_945, or skip. If entering, writes a real PAPER pick to
ai_stock_picks.

This is the orchestration layer — the piece that combines separate
checks into one verdict instead of leaving you to read six numbers
and decide yourself.
====================================================================
"""

import datetime as dt
from typing import Dict, Any, List, Optional

import psycopg2
import decision_logger as dl
import opening_snapshot_tracker as ost
import pre_recommendation_synthesis as prs
import earnings_calendar as ec
import regime_detector as rd

BASE_PAPER_POSITION_USD = 1_000   # base paper-trade size in USD; scaled by regime multiplier
_CONFIDENCE_SCORE_MAP   = {"high": 0.85, "medium": 0.60, "low": 0.40}

_DECISION_TYPE_MAP = {
    "enter_now":      "trade",
    "wait_until_945": "no_trade",
    "skip":           "no_trade",
}


def classify_from_snapshots(snapshots: List[Dict[str, Any]],
                              premarket_gap_pct: float) -> Dict[str, Any]:
    """Same classification logic as before, fed by accumulated
    snapshots instead of a dedicated minute-bar feed."""
    if len(snapshots) < 3:
        return {"pattern": "insufficient_data", "confidence": "low",
                "recommendation": "wait_for_more_scans"}

    open_price  = snapshots[0]["price"]
    current_price = snapshots[-1]["price"]
    high_so_far = max(s["price"] for s in snapshots)
    low_so_far  = min(s["price"] for s in snapshots)

    move_from_open_pct      = ((current_price - open_price) / open_price) * 100 if open_price else 0
    pulled_back_pct         = ((high_so_far - low_so_far) / high_so_far) * 100 if high_so_far else 0
    recovered_from_low_pct  = ((current_price - low_so_far) / low_so_far) * 100 if low_so_far else 0

    is_gap_up = premarket_gap_pct > 0

    if is_gap_up:
        if current_price < open_price and move_from_open_pct < -1.0:
            pattern    = "fake_breakout"
            confidence = "medium" if move_from_open_pct < -2.0 else "low"
        elif pulled_back_pct > 1.5 and recovered_from_low_pct > 1.0 and current_price >= open_price * 0.99:
            pattern, confidence = "pullback_continuation", "medium"
        elif current_price >= open_price:
            pattern    = "genuine_continuation"
            confidence = "medium" if move_from_open_pct > 0.5 else "low"
        else:
            pattern, confidence = "ambiguous", "low"
    else:
        if current_price > open_price and move_from_open_pct > 1.0:
            pattern    = "fake_breakdown"
            confidence = "medium" if move_from_open_pct > 2.0 else "low"
        elif pulled_back_pct > 1.5 and current_price <= open_price * 1.01:
            pattern, confidence = "pullback_breakdown", "medium"
        elif current_price <= open_price:
            pattern    = "genuine_breakdown"
            confidence = "medium" if move_from_open_pct < -0.5 else "low"
        else:
            pattern, confidence = "ambiguous", "low"

    return {
        "pattern":                 pattern,
        "confidence":              confidence,
        "move_from_open_pct":      round(move_from_open_pct, 3),
        "pulled_back_pct":         round(pulled_back_pct, 3),
        "recovered_from_low_pct":  round(recovered_from_low_pct, 3),
        "n_snapshots":             len(snapshots),
    }


def evaluate_ticker(db_url: str, ticker: str, premarket_gap_pct: float) -> Dict[str, Any]:
    """
    THE FULL COMBINED DECISION. Pulls every relevant check and
    produces ONE verdict, with the reasoning trail showing which
    factors drove it.
    """
    snapshots       = ost.get_todays_snapshots(db_url, ticker)
    opening_pattern = classify_from_snapshots(snapshots, premarket_gap_pct)
    synthesis       = prs.synthesize_and_log(db_url, ticker)
    earnings        = ec.should_avoid_entry(db_url, ticker, buffer_days=2)
    regime          = rd.get_current_regime(db_url, "SPY")

    blockers = []
    if earnings.get("avoid"):
        blockers.append(f"earnings risk: {earnings['reason']}")
    if opening_pattern["pattern"] in ("fake_breakout", "fake_breakdown"):
        blockers.append(f"opening pattern looks like a fake move ({opening_pattern['pattern']})")
    if opening_pattern["confidence"] == "low" or opening_pattern["pattern"] in ("ambiguous", "insufficient_data"):
        blockers.append("opening behavior still ambiguous, not enough signal yet")
    if synthesis["confluence_count"] < 2:
        blockers.append(f"weak signal confluence ({synthesis['confluence_count']}/4)")
    if regime.get("regime") in ("high_vol_downtrend",) or \
       (regime.get("confidence") == "low" and regime.get("total_score", 0) < 0):
        blockers.append("defensive market regime")

    if blockers:
        decision = "skip" if len(blockers) >= 2 else "wait_until_945"
    elif opening_pattern["pattern"] in ("genuine_continuation", "genuine_breakdown") \
         and opening_pattern["confidence"] == "medium":
        decision = "enter_now"
    else:
        decision = "wait_until_945"

    result = {
        "ticker":               ticker,
        "decision":             decision,
        "opening_pattern":      opening_pattern,
        "synthesis_confluence": synthesis["confluence_count"],
        "earnings_check":       earnings,
        "regime":               regime.get("regime"),
        "blockers":             blockers,
        "checked_at":           dt.datetime.utcnow().isoformat(),
    }

    reasoning = (
        f"PREMARKET/OPEN DECISION for {ticker}: {decision.upper()}. "
        f"Opening pattern: {opening_pattern['pattern']} "
        f"(confidence {opening_pattern['confidence']}, "
        f"{opening_pattern.get('n_snapshots', 0)} scans observed). "
        f"Synthesis confluence: {synthesis['confluence_count']}/4. "
        f"Blockers: {blockers if blockers else 'none'}."
    )

    try:
        dl.log_decision(
            signal_name="premarket_open_trader",
            decision_type=_DECISION_TYPE_MAP.get(decision, "no_trade"),
            ticker=ticker,
            reasoning=reasoning,
        )
    except Exception as _e:
        print(f"[premarket_open_trader] log_decision failed: {_e}")

    if decision == "enter_now":
        write_paper_pick(db_url, ticker, opening_pattern, synthesis, regime=regime)

    return result


def write_paper_pick(db_url: str, ticker: str, opening_pattern: Dict[str, Any],
                      synthesis: Dict[str, Any],
                      regime: Dict[str, Any] = None) -> None:
    """Writes a paper-trade row to ai_stock_picks.
    Applies regime multipliers so paper position size and confidence score
    reflect the current market environment, not just the per-ticker signal.
    """
    import json as _json
    multipliers  = (regime or {}).get("multipliers") or {}
    pos_mult     = float(multipliers.get("position_size_multiplier", 1.0))
    conf_mult    = float(multipliers.get("confidence_multiplier",    1.0))
    base_conf    = _CONFIDENCE_SCORE_MAP.get(opening_pattern["confidence"], 0.5)
    adj_conf     = round(min(1.0, base_conf * conf_mult), 4)
    pos_size_usd = round(BASE_PAPER_POSITION_USD * pos_mult, 2)
    regime_rec   = (regime or {}).get("recommendation", "unknown")

    note = (
        f"PAPER ENTRY (premarket_open_trader): "
        f"pattern={opening_pattern['pattern']}, "
        f"confluence={synthesis['confluence_count']}/4, "
        f"regime={regime_rec}, pos_size=${pos_size_usd:.0f}"
    )
    signals_json = {
        "position_size_usd":        pos_size_usd,
        "position_size_multiplier": pos_mult,
        "confidence_multiplier":    conf_mult,
        "regime_recommendation":    regime_rec,
        "base_confidence":          base_conf,
        "adjusted_confidence":      adj_conf,
    }
    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO ai_stock_picks
                    (ticker, status, pick_date, entry_note, confidence, score, signals)
                VALUES (%s, 'open', CURRENT_DATE, %s, %s, %s, %s)
                ON CONFLICT (pick_date, ticker) DO NOTHING
            """, (ticker, note, opening_pattern["confidence"],
                  adj_conf, _json.dumps(signals_json)))
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    import os
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        print(evaluate_ticker(db_url, "AAPL", premarket_gap_pct=4.5))
    else:
        print("Set DATABASE_URL to test.")
