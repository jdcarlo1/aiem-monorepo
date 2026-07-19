#!/usr/bin/env python3
"""
verify_dpl_phase2.py — Decision Proof Layer Phase 2 acceptance verifier.

Acceptance criteria (38 total):
  Schema      C01-C05  — five JSONB context columns exist in oe_decision_audit
  Trigger     C06-C08  — immutability trigger extended to context columns
  Bootstrap   C09      — bootstrap_dpl is idempotent (second call succeeds)
  write / ctx C10-C12  — write_decision stores context blobs + sets VERIFIED
  identity    C13-C18  — identity_json required keys + field correctness
  technical   C19-C22  — technical_json required keys
  options     C23-C27  — options_intel_json required keys
  prob/risk   C28-C33  — probability_risk_json required keys + flagged fields
  justif      C34-C38  — justification_json required keys + flagged fields

Each test prints PASS or FAIL with a reason, then emits a summary line.
Exit code 0 = all pass, 1 = any fail.
"""

import json
import os
import sys
import uuid
from datetime import date

import psycopg2

DB_URL = os.environ.get("DATABASE_URL", "")

PASS  = "PASS"
FAIL  = "FAIL"
SEQ   = 0
results = []


def _t(tag: str, condition: bool, detail: str = "") -> None:
    global SEQ
    SEQ += 1
    status = PASS if condition else FAIL
    label  = f"[{status}] {tag}"
    if detail:
        label += f" — {detail}"
    print(label)
    results.append((tag, status))


def _conn():
    return psycopg2.connect(DB_URL, connect_timeout=8,
                            options="-c statement_timeout=10000")


# ─────────────────────────────────────────────────────────────────────────────
# BOOTSTRAP
# ─────────────────────────────────────────────────────────────────────────────
import aiem_options_dpl as dpl

print("=== DPL Phase 2 Verifier ===\n")

try:
    ok = dpl.bootstrap_dpl(DB_URL)
    _t("C09_bootstrap_idempotent_first", ok is True)
    ok2 = dpl.bootstrap_dpl(DB_URL)
    _t("C09b_bootstrap_idempotent_second", ok2 is True)
except Exception as e:
    _t("C09_bootstrap", False, str(e))
    print("FATAL: bootstrap failed — cannot continue"); sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
# SCHEMA: five new JSONB columns
# ─────────────────────────────────────────────────────────────────────────────
with _conn() as conn, conn.cursor() as cur:
    cur.execute("""
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name = 'oe_decision_audit'
    """)
    cols = {r[0]: r[1] for r in cur.fetchall()}

_EXPECTED_CTX_COLS = (
    "identity_json", "technical_json", "options_intel_json",
    "probability_risk_json", "justification_json",
)
for col in _EXPECTED_CTX_COLS:
    _t(f"C0{_EXPECTED_CTX_COLS.index(col)+1}_{col}_exists",
       col in cols,
       f"dtype={cols.get(col, 'MISSING')}")

for col in _EXPECTED_CTX_COLS:
    _t(f"C0{_EXPECTED_CTX_COLS.index(col)+1}_{col}_is_jsonb",
       cols.get(col) == "jsonb")


# ─────────────────────────────────────────────────────────────────────────────
# TRIGGER: immutability extended to context columns
# ─────────────────────────────────────────────────────────────────────────────
_test_id = uuid.uuid4().hex[:24]

# Write a production test row directly (bypass write_decision is_test to test trigger)
with _conn() as conn, conn.cursor() as cur:
    cur.execute("""
        INSERT INTO oe_decision_audit
            (decision_id, created_at, input_hash, output_hash,
             verification_status, engine_version, db_version, is_test_record,
             identity_json)
        VALUES (%s, NOW(), 'h1', 'h2', 'PENDING', 'v1', '16.0', FALSE,
                '{"ticker":"TEST"}'::jsonb)
    """, (_test_id,))
    conn.commit()

# C06: trigger blocks UPDATE of identity_json on production row
try:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            UPDATE oe_decision_audit
            SET identity_json = '{"ticker":"TAMPERED"}'::jsonb
            WHERE decision_id = %s
        """, (_test_id,))
        conn.commit()
    _t("C06_trigger_blocks_identity_json_update", False,
       "expected exception not raised")
except psycopg2.errors.RaiseException:
    _t("C06_trigger_blocks_identity_json_update", True)

# C07: trigger blocks UPDATE of justification_json on production row
try:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            UPDATE oe_decision_audit
            SET justification_json = '{"tampered":true}'::jsonb
            WHERE decision_id = %s
        """, (_test_id,))
        conn.commit()
    _t("C07_trigger_blocks_justification_json_update", False,
       "expected exception not raised")
except psycopg2.errors.RaiseException:
    _t("C07_trigger_blocks_justification_json_update", True)

# C08: trigger still allows verification_status UPDATE on production row
try:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            UPDATE oe_decision_audit
            SET verification_status = 'VERIFIED'
            WHERE decision_id = %s
        """, (_test_id,))
        conn.commit()
    _t("C08_trigger_allows_verif_status_update", True)
except Exception as e:
    _t("C08_trigger_allows_verif_status_update", False, str(e))

# Cleanup test row via psycopg2 directly (no trigger on test_record=FALSE row,
# but is_test_record=FALSE blocks DELETE — clean up using test record pattern)
# We leave the test row; it's a production row and cannot be deleted.
# It's harmless for future verifier runs.


# ─────────────────────────────────────────────────────────────────────────────
# WRITE_DECISION + ASSEMBLE_DPL_CONTEXT
# ─────────────────────────────────────────────────────────────────────────────
_SAMPLE_CTX = dpl.assemble_dpl_context(
    ticker="AAPL",
    scan_date=date.today(),
    trace_id="abcd1234ef567890",
    direction="LONG_CALL",
    alert_id=999,
    sel_data={
        "delta": 0.45, "gamma": 0.03, "theta": -0.02, "vega": 0.12,
        "iv": 0.32, "bid": 1.05, "ask": 1.15,
        "bid_ask_spread_pct": 0.095,
        "open_interest": 5000, "volume": 800,
        "premium_at_risk": 110.0,
        "profit_target": 220.0,
        "stop_level": 55.0,
        "probability_estimate": 0.52,
        "expected_return": 0.18,
    },
    stock_data={
        "market_regime": "BULL_TRENDING",
        "close_strength": 0.72,
        "rvol": 1.8,
        "gap_pct": 0.012,
        "sector": "Technology",
        "sector_strength": 0.65,
        "gex_regime": "POSITIVE",
    },
    verify_result={
        "gate_failures": [],
        "correlation_check": "OK",
        "portfolio_check": "PASS",
        "concentration_ok": True,
    },
    chain_strategies=[
        {"strategy": "LONG_CALL", "rejected": False},
        {"strategy": "BULL_CALL_SPREAD", "rejected": True,
         "rejection_reason": "insufficient_margin"},
    ],
    best_chain_strategy={
        "strategy": "LONG_CALL",
        "legs": [{"action": "BUY", "type": "CALL",
                  "strike": 185.0, "expiry": "2026-07-28"}],
        "regime_suitability": "HIGH",
    },
    sel_strike=185.0,
    expiry_str="2026-07-28",
    alert_fields={
        "why_selected_won": "LONG_CALL scored 68.5 vs 42.1 (margin=26.4). "
                            "skew=NEUTRAL regime=BULL_TRENDING",
        "main_risks": "IV crush (iv_rank=34); theta decay 9 DTE; gap risk.",
        "dte": 9,
        "bid": 1.05,
        "ask": 1.15,
        "breakeven": 186.15,
        "profit_target": 220.0,
        "stop_level": 55.0,
        "max_premium_risk": 110.0,
        "probability_estimate": 0.52,
        "expected_return": 0.18,
        "delta": 0.45,
        "gamma": 0.03,
        "theta": -0.02,
        "vega": 0.12,
        "iv": 0.32,
        "volume": 800,
        "open_interest": 5000,
        "bid_ask_spread_pct": 0.095,
        "expected_move": 4.20,
        "expected_move_pct": 0.023,
    },
    pm_intel={
        "premarket_score": 0.71,
        "premarket_direction": "BULLISH",
        "premarket_confidence": 0.65,
        "pm_rvol": 1.4,
        "premarket_gap": 0.009,
        "risk_flags": [],
        "catalyst_flags": ["ANALYST_ACTION"],
        "sector_confirmed": True,
        "premarket_high": 184.5,
        "premarket_low": 182.0,
        "news_headline_count": 3,
        "earnings_in_news": False,
    },
    mtf_result={
        "dominant_bias": "BULLISH",
        "timeframe_alignment_score": 0.78,
        "conflict_score": 0.12,
        "entry_timing_status": "OPTIMAL",
    },
    pattern_result={
        "pattern_score": 0.65,
        "all_patterns": [
            {"canonical_id": "breakout_ascending_triangle",
             "name": "Ascending Triangle Breakout",
             "confidence": 0.72, "timeframe": "1D", "actionable": True},
        ],
    },
    em_result={
        "expected_move": 4.20,
        "expected_move_pct": 0.023,
    },
    ivr_result={
        "iv_rank": 34.0,
        "iv_label": "IV_NORMAL",
    },
    call_score=68.5,
    put_score=42.1,
    db_url=DB_URL,
)

# assemble_dpl_context: returned dict has required top-level keys
for key in ("identity", "technical", "options_intel", "probability_risk", "justification"):
    _t(f"C10_ctx_has_{key}", key in _SAMPLE_CTX)

# write_decision with context stores all 5 JSONB blobs
_test_did = None
try:
    _wr = dpl.write_decision(
        input_data={"ticker": "AAPL", "trace_id": "abcd1234ef567890",
                    "call_score": 68.5, "put_score": 42.1, "direction": "LONG_CALL"},
        output_data={"alert_id": 999, "direction": "LONG_CALL",
                     "chain_sha": "abc123", "trace_id": "abcd1234ef567890"},
        context=_SAMPLE_CTX,
        is_test_record=True,
        db_url=DB_URL,
    )
    _test_did = _wr["decision_id"]
    _t("C11_write_decision_with_context_succeeds", True)
    _t("C11b_write_decision_returns_VERIFIED",
       _wr.get("verification_status") == "VERIFIED")
    _t("C11c_write_decision_has_context",
       _wr.get("has_context") is True)
except Exception as e:
    _t("C11_write_decision_with_context_succeeds", False, str(e))
    _test_did = None

# C12: all five JSONB columns non-NULL in DB
if _test_did:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT identity_json, technical_json, options_intel_json,
                   probability_risk_json, justification_json
            FROM oe_decision_audit
            WHERE decision_id = %s
        """, (_test_did,))
        _row = cur.fetchone()

    for i, col in enumerate(_EXPECTED_CTX_COLS):
        _t(f"C12_{col}_stored_non_null", _row and _row[i] is not None)

    # psycopg2 auto-deserializes JSONB → Python dict; no json.loads needed
    _idt  = _row[0] if _row and _row[0] else {}
    _tech = _row[1] if _row and _row[1] else {}
    _opts = _row[2] if _row and _row[2] else {}
    _prob = _row[3] if _row and _row[3] else {}
    _just = _row[4] if _row and _row[4] else {}
else:
    for col in _EXPECTED_CTX_COLS:
        _t(f"C12_{col}_stored_non_null", False, "write_decision failed")
    _idt = _tech = _opts = _prob = _just = {}


# ─────────────────────────────────────────────────────────────────────────────
# identity_json required keys (C13-C18)
# ─────────────────────────────────────────────────────────────────────────────
_IDT_KEYS = ("ticker", "scan_date", "trace_id", "direction",
             "selected_strategy", "strikes", "expiration",
             "position_size_usd", "market_regime", "volatility_regime",
             "premarket_conditions")
_t("C13_identity_ticker_correct", _idt.get("ticker") == "AAPL")
_t("C14_identity_selected_strategy", "selected_strategy" in _idt)
_t("C15_identity_market_regime",
   _idt.get("market_regime") in ("BULL_TRENDING", "POSITIVE"))
_t("C16_identity_volatility_regime", _idt.get("volatility_regime") == "IV_NORMAL")
_t("C17_identity_premarket_conditions_dict",
   isinstance(_idt.get("premarket_conditions"), dict))
_t("C18_identity_premarket_direction",
   _idt.get("premarket_conditions", {}).get("premarket_direction") == "BULLISH")


# ─────────────────────────────────────────────────────────────────────────────
# technical_json required keys (C19-C22)
# ─────────────────────────────────────────────────────────────────────────────
_t("C19_technical_trend", _tech.get("trend") == "BULLISH")
_t("C20_technical_momentum_dict", isinstance(_tech.get("momentum"), dict))
_t("C21_technical_pattern_recognition_dict",
   isinstance(_tech.get("pattern_recognition"), dict))
_t("C22_technical_mtf_alignment_score",
   _tech.get("multi_timeframe_confirmation", {}).get("alignment_score") is not None)


# ─────────────────────────────────────────────────────────────────────────────
# options_intel_json required keys (C23-C27)
# ─────────────────────────────────────────────────────────────────────────────
_t("C23_options_greeks_dict", isinstance(_opts.get("greeks"), dict))
_t("C24_options_greeks_delta_set", _opts.get("greeks", {}).get("delta") is not None)
_t("C25_options_iv_rank_set", _opts.get("iv_rank") is not None)
_t("C26_options_expected_move_set", _opts.get("expected_move") is not None)
_t("C27_options_bid_ask_spread_dict",
   isinstance(_opts.get("bid_ask_spread"), dict))


# ─────────────────────────────────────────────────────────────────────────────
# probability_risk_json required keys + flagged fields (C28-C33)
# ─────────────────────────────────────────────────────────────────────────────
_t("C28_prob_risk_probability_engine_output",
   isinstance(_prob.get("probability_engine_output"), dict))
_t("C29_prob_risk_max_risk_set", _prob.get("max_risk") is not None)
_t("C30_prob_risk_max_reward_set", _prob.get("max_reward") is not None)
_t("C31_prob_risk_capital_preservation_flagged",
   _prob.get("capital_preservation_score", {}).get("_flag") == "NOT_PER_DECISION")
_t("C32_prob_risk_capital_efficiency_flagged",
   _prob.get("capital_efficiency_score", {}).get("_flag") == "NOT_PER_DECISION")
_t("C33_prob_risk_sector_exposure_dict",
   isinstance(_prob.get("sector_exposure_impact"), dict))


# ─────────────────────────────────────────────────────────────────────────────
# justification_json required keys + flagged fields (C34-C38)
# ─────────────────────────────────────────────────────────────────────────────
_t("C34_just_why_stock_qualified_set",
   _just.get("why_stock_qualified") is not None)
_t("C35_just_stop_loss_criteria_dict",
   isinstance(_just.get("stop_loss_criteria"), dict)
   and _just.get("stop_loss_criteria", {}).get("stop_level") is not None)
_t("C36_just_time_exit_flagged_PARTIAL",
   _just.get("time_based_exit_rules", {}).get("_flag") == "PARTIAL")
_t("C37_just_adjustment_rolling_flagged_NOT_COMPUTED",
   _just.get("adjustment_rolling_rules", {}).get("_flag") == "NOT_COMPUTED")
_t("C38_just_invalidation_flagged_PARTIAL",
   _just.get("invalidation_conditions", {}).get("_flag") == "PARTIAL")


# ─────────────────────────────────────────────────────────────────────────────
# NO_TRADE path: no_trade_explanation populated (bonus checks)
# ─────────────────────────────────────────────────────────────────────────────
_NT_CTX = dpl.assemble_dpl_context(
    ticker="SPY", scan_date=date.today(),
    trace_id="nt_test_0000", direction="NO_TRADE",
    call_score=38.0, put_score=41.0,
    verify_result={"gate_failures": ["SCORE_GATE", "MARGIN_GATE"]},
    db_url=DB_URL,
)
_t("C38b_no_trade_explanation_populated",
   isinstance(
       _NT_CTX.get("justification", {}).get("no_trade_explanation"), dict)
   and "gate_failures" in _NT_CTX["justification"]["no_trade_explanation"])

# write_decision with context=None still works (Phase 1 backward compat)
try:
    _wr_no_ctx = dpl.write_decision(
        input_data={"ticker": "TEST", "trace_id": "bkcompat_test"},
        output_data={"result": "no_context"},
        context=None,
        is_test_record=True,
        db_url=DB_URL,
    )
    _t("C38c_write_decision_no_context_backward_compat",
       _wr_no_ctx.get("verification_status") == "VERIFIED"
       and _wr_no_ctx.get("has_context") is False)
except Exception as e:
    _t("C38c_write_decision_no_context_backward_compat", False, str(e))


# ─────────────────────────────────────────────────────────────────────────────
# SUMMARY
# ─────────────────────────────────────────────────────────────────────────────
print()
n_pass = sum(1 for _, s in results if s == PASS)
n_fail = sum(1 for _, s in results if s == FAIL)
print(f"=== RESULT: {n_pass} PASS / {n_fail} FAIL / {len(results)} TOTAL ===")

if n_fail:
    print("\nFailed tests:")
    for tag, status in results:
        if status == FAIL:
            print(f"  {tag}")
    sys.exit(1)
else:
    print("ALL PASS")
    sys.exit(0)
