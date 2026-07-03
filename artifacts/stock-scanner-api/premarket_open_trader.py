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

SAFETY GATES (Group 1, wired 2026-07-03)
-----------------------------------------
Four hard-stop checks run before any paper pick is ever written:

  1. position_reconciler  — unresolved broker/DB mismatch → skip
  2. daily_loss_limit     — today's loss exceeds threshold → skip
                            IMPORTANT: fails CLOSED when
                            ACCOUNT_VALUE_BASELINE env var is not set.
                            Set it (e.g. ACCOUNT_VALUE_BASELINE=50000)
                            or every trade is blocked by design.
  3. portfolio_correlation_risk — too many correlated positions → skip
  4. order_dedup          — same decision_id attempted twice → skip

Hard gates:  ANY single hit forces decision="skip", no pick written.
Soft gates:  2+ soft hits force skip; 1 soft hit → wait_until_945.
====================================================================
"""

import datetime as dt
import os
from typing import Dict, Any, List, Optional

import psycopg2
import decision_logger as dl
import opening_snapshot_tracker as ost
import pre_recommendation_synthesis as prs
import earnings_calendar as ec
import regime_detector as rd
import position_reconciler as pr
import daily_loss_limit as dll
import order_dedup as od
import portfolio_correlation_risk as pcr

BASE_PAPER_POSITION_USD = 1_000
_CONFIDENCE_SCORE_MAP   = {"high": 0.85, "medium": 0.60, "low": 0.40}

_DECISION_TYPE_MAP = {
    "enter_now":      "trade",
    "wait_until_945": "no_trade",
    "skip":           "no_trade",
}


def init_schema(db_url: str = None) -> None:
    """
    Creates the tables required by the safety gate modules.
    Call once at startup (wired into _DEFERRED_INITS in main.py).
    Idempotent — safe to call on every boot.
    """
    if db_url is None:
        db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("[premarket_open_trader] init_schema: DATABASE_URL not set, skipping")
        return

    statements = [
        """
        CREATE TABLE IF NOT EXISTS order_execution_log (
            id              SERIAL PRIMARY KEY,
            decision_id     INTEGER NOT NULL,
            broker_order_id TEXT,
            ticker          TEXT,
            side            TEXT,
            qty             DOUBLE PRECISION,
            status          TEXT,
            submitted_at    TIMESTAMPTZ NOT NULL,
            filled_at       TIMESTAMPTZ,
            fill_price      DOUBLE PRECISION,
            UNIQUE (decision_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS daily_loss_breach_log (
            id              SERIAL PRIMARY KEY,
            checked_at      TIMESTAMPTZ NOT NULL,
            account_value   DOUBLE PRECISION,
            realized_pnl    DOUBLE PRECISION,
            loss_pct        DOUBLE PRECISION,
            loss_limit_pct  DOUBLE PRECISION,
            resolved        BOOLEAN DEFAULT FALSE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS reconciliation_log (
            id              SERIAL PRIMARY KEY,
            checked_at      TIMESTAMPTZ NOT NULL,
            only_in_broker  TEXT,
            only_in_db      TEXT,
            mismatch_found  BOOLEAN,
            resolved        BOOLEAN DEFAULT FALSE
        )
        """,
    ]

    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor() as cur:
            for sql in statements:
                cur.execute(sql)
        conn.commit()
        print("[premarket_open_trader] init_schema: safety gate tables verified/created")
    except Exception as exc:
        print(f"[premarket_open_trader] init_schema error: {exc}")
    finally:
        conn.close()


def classify_from_snapshots(snapshots: List[Dict[str, Any]],
                              premarket_gap_pct: float) -> Dict[str, Any]:
    """Same classification logic as before, fed by accumulated
    snapshots instead of a dedicated minute-bar feed."""
    if len(snapshots) < 3:
        return {"pattern": "insufficient_data", "confidence": "low",
                "recommendation": "wait_for_more_scans"}

    open_price    = snapshots[0]["price"]
    current_price = snapshots[-1]["price"]
    high_so_far   = max(s["price"] for s in snapshots)
    low_so_far    = min(s["price"] for s in snapshots)

    move_from_open_pct     = ((current_price - open_price) / open_price) * 100 if open_price else 0
    pulled_back_pct        = ((high_so_far - low_so_far) / high_so_far) * 100 if high_so_far else 0
    recovered_from_low_pct = ((current_price - low_so_far) / low_so_far) * 100 if low_so_far else 0

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
        "pattern":                pattern,
        "confidence":             confidence,
        "move_from_open_pct":     round(move_from_open_pct, 3),
        "pulled_back_pct":        round(pulled_back_pct, 3),
        "recovered_from_low_pct": round(recovered_from_low_pct, 3),
        "n_snapshots":            len(snapshots),
    }


def evaluate_ticker(db_url: str, ticker: str, premarket_gap_pct: float) -> Dict[str, Any]:
    """
    THE FULL COMBINED DECISION. Pulls every relevant check and
    produces ONE verdict, with the reasoning trail showing which
    factors drove it.

    Gate order:
      Hard gates run first (any one → skip, no pick written):
        1. earnings risk
        2. unresolved position mismatch
        3. daily loss limit breached
        4. portfolio concentration risk

      Soft gates run next (2+ → skip, 1 → wait_until_945):
        5. opening pattern is a fake move
        6. opening behavior still ambiguous
        7. weak signal confluence
        8. defensive market regime
    """
    snapshots       = ost.get_todays_snapshots(db_url, ticker)
    opening_pattern = classify_from_snapshots(snapshots, premarket_gap_pct)
    synthesis       = prs.synthesize_and_log(db_url, ticker)
    earnings        = ec.should_avoid_entry(db_url, ticker, buffer_days=2)
    regime          = rd.get_current_regime(db_url, "SPY")

    hard_blockers: List[str] = []
    soft_blockers: List[str] = []

    # ── HARD GATE 1: earnings risk ────────────────────────────────────
    if earnings.get("avoid"):
        hard_blockers.append(f"earnings risk: {earnings['reason']}")

    # ── HARD GATE 2: unresolved broker/DB position mismatch ──────────
    try:
        if pr.has_unresolved_mismatch(db_url):
            hard_blockers.append("unresolved position mismatch between broker and DB")
    except Exception as _exc:
        hard_blockers.append(f"position reconciler check failed (fail closed): {_exc}")

    # ── HARD GATE 3: daily loss limit ────────────────────────────────
    try:
        dll_result = dll.check_daily_loss_limit(db_url)
        if dll_result["halt_trading"]:
            loss_pct   = dll_result.get("loss_pct")
            limit_pct  = dll_result.get("loss_limit_pct")
            reason     = dll_result.get("reason") or ""
            if loss_pct is not None:
                hard_blockers.append(
                    f"daily loss limit breached: {loss_pct}% (limit -{limit_pct}%)"
                )
            else:
                hard_blockers.append(
                    f"daily loss limit check failed (fail closed): {reason[:120]}"
                )
    except Exception as _exc:
        hard_blockers.append(f"daily loss limit check raised exception (fail closed): {_exc}")

    # ── HARD GATE 4: portfolio concentration risk ─────────────────────
    try:
        correlation_result = pcr.check_current_portfolio_risk(db_url)
        if correlation_result["concentration_risk_flag"]:
            hard_blockers.append(
                f"portfolio concentration risk: {correlation_result['warnings']}"
            )
    except Exception as _exc:
        hard_blockers.append(f"portfolio correlation check failed (fail closed): {_exc}")

    # ── SOFT GATES ────────────────────────────────────────────────────
    if opening_pattern["pattern"] in ("fake_breakout", "fake_breakdown"):
        soft_blockers.append(
            f"opening pattern looks like a fake move ({opening_pattern['pattern']})"
        )
    if opening_pattern["confidence"] == "low" or \
       opening_pattern["pattern"] in ("ambiguous", "insufficient_data"):
        soft_blockers.append("opening behavior still ambiguous, not enough signal yet")
    if synthesis["confluence_count"] < 2:
        soft_blockers.append(f"weak signal confluence ({synthesis['confluence_count']}/4)")
    if regime.get("regime") in ("high_vol_downtrend",) or \
       (regime.get("confidence") == "low" and regime.get("total_score", 0) < 0):
        soft_blockers.append("defensive market regime")

    # ── DECISION ──────────────────────────────────────────────────────
    if hard_blockers:
        decision = "skip"
    elif len(soft_blockers) >= 2:
        decision = "skip"
    elif soft_blockers:
        decision = "wait_until_945"
    elif opening_pattern["pattern"] in ("genuine_continuation", "genuine_breakdown") \
         and opening_pattern["confidence"] == "medium":
        decision = "enter_now"
    else:
        decision = "wait_until_945"

    all_blockers = hard_blockers + soft_blockers

    result = {
        "ticker":               ticker,
        "decision":             decision,
        "opening_pattern":      opening_pattern,
        "synthesis_confluence": synthesis["confluence_count"],
        "earnings_check":       earnings,
        "regime":               regime.get("regime"),
        "hard_blockers":        hard_blockers,
        "soft_blockers":        soft_blockers,
        "blockers":             all_blockers,
        "checked_at":           dt.datetime.utcnow().isoformat(),
    }

    reasoning = (
        f"PREMARKET/OPEN DECISION for {ticker}: {decision.upper()}. "
        f"Opening pattern: {opening_pattern['pattern']} "
        f"(confidence {opening_pattern['confidence']}, "
        f"{opening_pattern.get('n_snapshots', 0)} scans observed). "
        f"Synthesis confluence: {synthesis['confluence_count']}/4. "
        f"Hard blockers: {hard_blockers if hard_blockers else 'none'}. "
        f"Soft blockers: {soft_blockers if soft_blockers else 'none'}."
    )

    decision_id: Optional[int] = None
    try:
        decision_id = dl.log_decision(
            signal_name="premarket_open_trader",
            decision_type=_DECISION_TYPE_MAP.get(decision, "no_trade"),
            ticker=ticker,
            reasoning=reasoning,
        )
    except Exception as _e:
        print(f"[premarket_open_trader] log_decision failed: {_e}")

    if decision == "enter_now":
        if decision_id is None:
            print(
                f"[premarket_open_trader] WARNING: decision_id is None for {ticker} "
                f"(log_decision failed) — skipping write to preserve dedup guarantee"
            )
        elif od.should_place_order(db_url, decision_id):
            write_paper_pick(db_url, ticker, opening_pattern, synthesis, regime=regime)
            od.mark_order_placed(
                db_url, decision_id,
                broker_order_id=f"paper-{decision_id}",
                ticker=ticker, side="long", qty=1, status="filled",
            )
        else:
            print(
                f"[premarket_open_trader] duplicate blocked: "
                f"decision_id={decision_id} already in order_execution_log"
            )

    result["decision_id"] = decision_id
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
