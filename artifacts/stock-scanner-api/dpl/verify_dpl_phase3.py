#!/usr/bin/env python3
"""
verify_dpl_phase3.py — DPL Phase 3 (Reproducibility Replay) Verifier
12 checks: C01-C12

  C01  Imports: ReplayInputsMissingError, bootstrap_dpl_phase3,
                capture_replay_inputs, replay_decision importable
  C02  Table oe_decision_replay_inputs exists after bootstrap
  C03  All 14 required columns present
  C04  bootstrap_dpl_phase3() idempotent (double-call, no error)
  C05  MISSING-EVIDENCE CHECK: replay_decision(unknown_id) raises
         ReplayInputsMissingError — not a silent fallback
  C06  capture_replay_inputs() writes a row for a real decision_id FK
  C07  replay_decision() returns dict with all required keys
  C08  Replayed call_score matches stored within 0.05 [REPLAY MATCH]
  C09  Replayed put_score matches stored within 0.05  [REPLAY MATCH]
  C10  Replayed direction matches stored_direction    [REPLAY MATCH]
  C11  Row B (mutated theta) replays and full_match=True for its own stored values
  C12  MUTATION CHECK: score_A != score_B (altered input changes output)

Run via:
  cd artifacts/stock-scanner-api && tools/verified_run.sh verify_dpl_phase3.py

Exit 0 all pass, 1 any fail.
"""

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import psycopg2
_DB_URL = os.environ.get("DATABASE_URL", "")


def _conn():
    return psycopg2.connect(_DB_URL, connect_timeout=8,
                            options="-c statement_timeout=15000")


_PASS = []
_FAIL = []


def chk(label, cond, detail=""):
    if cond:
        print(f"PASS {label}")
        _PASS.append(label)
    else:
        msg = f"FAIL {label}" + (f" -- {detail}" if detail else "")
        print(msg)
        _FAIL.append(label)
    return cond


# ─────────────────────────────────────────────────────────────────────────────
# C01: Imports
# ─────────────────────────────────────────────────────────────────────────────

try:
    from aiem_options_dpl import (
        bootstrap_dpl,
        bootstrap_dpl_phase3,
        capture_replay_inputs,
        replay_decision,
        write_decision,
        ReplayInputsMissingError,
    )
    from aiem_options_pipeline import compute_req6_score
    chk("C01_imports_ok", True)
except Exception as _e:
    chk("C01_imports_ok", False, str(_e))
    print("FATAL: cannot import required modules — aborting")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
# C02: Table exists
# ─────────────────────────────────────────────────────────────────────────────

try:
    bootstrap_dpl_phase3()
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_name = 'oe_decision_replay_inputs'
            )
        """)
        chk("C02_table_exists", cur.fetchone()[0])
except Exception as _e:
    chk("C02_table_exists", False, str(_e))


# ─────────────────────────────────────────────────────────────────────────────
# C03: All 14 required columns present
# ─────────────────────────────────────────────────────────────────────────────

_REQUIRED_COLS = {
    "decision_id", "alert_id", "replay_schema_version",
    "contract_data_call", "contract_data_put", "stock_data_replay",
    "iv_rank", "verify_result_replay", "config_versions",
    "data_source_timestamps", "stored_call_score", "stored_put_score",
    "stored_direction", "created_at",
}
try:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'oe_decision_replay_inputs'
        """)
        _found_cols = {r[0] for r in cur.fetchall()}
    _missing = _REQUIRED_COLS - _found_cols
    chk("C03_columns_present", not _missing,
        f"missing: {_missing}" if _missing else "")
except Exception as _e:
    chk("C03_columns_present", False, str(_e))


# ─────────────────────────────────────────────────────────────────────────────
# C04: bootstrap_dpl_phase3() idempotent
# ─────────────────────────────────────────────────────────────────────────────

try:
    bootstrap_dpl_phase3()
    bootstrap_dpl_phase3()
    chk("C04_bootstrap_idempotent", True)
except Exception as _e:
    chk("C04_bootstrap_idempotent", False, str(_e))


# ─────────────────────────────────────────────────────────────────────────────
# C05: MISSING-EVIDENCE CHECK — loud fail, no fallback
# ─────────────────────────────────────────────────────────────────────────────

try:
    _fake_id = "p3v_miss_" + uuid.uuid4().hex[:8]
    _raised = False
    _err_msg = ""
    try:
        replay_decision(_fake_id)
    except ReplayInputsMissingError as _rme:
        _raised = True
        _err_msg = str(_rme)
    except Exception as _other:
        _err_msg = f"wrong exception type: {type(_other).__name__}: {_other}"
    chk("C05_missing_evidence_loud_fail", _raised,
        _err_msg if not _raised else "")
    if _raised:
        print(f"  [C05 detail] ReplayInputsMissingError raised as required: "
              f"{_err_msg[:80]}")
except Exception as _e:
    chk("C05_missing_evidence_loud_fail", False, str(_e))


# ─────────────────────────────────────────────────────────────────────────────
# Known inputs for test rows A and B
# Row A: baseline
# Row B: same as A except call_data["theta"] = 1.50  (extreme → D8 drops to 0)
# ─────────────────────────────────────────────────────────────────────────────

_STOCK_DATA = {
    "stock_direction": "STRONG_BULL",
    "market_regime":   "TRENDING",
    "vwap_position":   "ABOVE_VWAP",
    "close_strength":  0.80,
    "iv_crush_risk":   "",
    "pc_skew_tag":     "CALL_SKEW",
    "sector_strength": 0.70,
    "market_breadth":  0.60,
}

_CALL_DATA_A = {
    "probability_estimate": 0.45,
    "expected_return":       1.50,
    "premium_at_risk":       100.0,
    "profit_target":         1.20,
    "slippage_pct":          0.05,
    "theta":                 0.03,    # <-- mutated to 1.50 in row B
    "bid":                   0.80,
    "ask":                   1.20,
    "dte":                   14,
    "volume":                500,
    "open_interest":         2000,
    "entry_premium_hi":      1.20,
}

_CALL_DATA_B = dict(_CALL_DATA_A)
_CALL_DATA_B["theta"] = 1.50          # extreme theta collapses D8 to 0

_PUT_DATA = {
    "probability_estimate": 0.40,
    "expected_return":       1.20,
    "premium_at_risk":       90.0,
    "profit_target":         1.00,
    "slippage_pct":          0.06,
    "theta":                 0.025,
    "bid":                   0.70,
    "ask":                   1.10,
    "dte":                   14,
    "volume":                300,
    "open_interest":         1500,
    "entry_premium_hi":      1.10,
}

_VERIFY_RESULT = {
    "call_eligible": True, "put_eligible": True,
    "ready_for_decision": True, "gate_failures": [],
}
_IV_RANK = 0.35    # 0-1 float


def _derive_direction(call_s, put_s):
    margin = abs(call_s - put_s)
    if call_s >= put_s and call_s >= 55 and margin >= 10:
        return "LONG_CALL"
    if put_s > call_s and put_s >= 55 and margin >= 10:
        return "LONG_PUT"
    return "NO_TRADE"


# Pre-compute expected scores (same function as replay uses)
_exp_call_A = compute_req6_score(_CALL_DATA_A, "CALL", _STOCK_DATA, _IV_RANK, _VERIFY_RESULT)["score"]
_exp_put_A  = compute_req6_score(_PUT_DATA,    "PUT",  _STOCK_DATA, _IV_RANK, _VERIFY_RESULT)["score"]
_exp_dir_A  = _derive_direction(_exp_call_A, _exp_put_A)

_exp_call_B = compute_req6_score(_CALL_DATA_B, "CALL", _STOCK_DATA, _IV_RANK, _VERIFY_RESULT)["score"]
_exp_dir_B  = _derive_direction(_exp_call_B, _exp_put_A)

print(f"  [scores] call_A={_exp_call_A}  put_A={_exp_put_A}  dir_A={_exp_dir_A}")
print(f"  [scores] call_B={_exp_call_B}  put_A={_exp_put_A}  dir_B={_exp_dir_B}")
print(f"  [mutation delta] call_A - call_B = {round(_exp_call_A - _exp_call_B, 2)}")


# ─────────────────────────────────────────────────────────────────────────────
# C06: capture_replay_inputs() writes row for a real FK decision_id
# ─────────────────────────────────────────────────────────────────────────────

_decision_id_A = None
_replay_A = None

try:
    # Create parent row in oe_decision_audit (FK target)
    _dpl_row_A = write_decision(
        input_data ={"ticker": "P3TEST_A", "call_score": _exp_call_A,
                     "put_score": _exp_put_A},
        output_data={"direction": _exp_dir_A, "trace_id": "p3verif_A"},
        is_test_record=True,
    )
    _decision_id_A = _dpl_row_A["decision_id"]

    capture_replay_inputs(
        decision_id   = _decision_id_A,
        direction     = _exp_dir_A,
        call_score    = _exp_call_A,
        put_score     = _exp_put_A,
        call_data     = _CALL_DATA_A,
        put_data      = _PUT_DATA,
        stock_data    = _STOCK_DATA,
        verify_result = _VERIFY_RESULT,
        iv_rank       = _IV_RANK,
        alert_id      = None,
    )

    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT decision_id FROM oe_decision_replay_inputs "
            "WHERE decision_id = %s",
            (_decision_id_A,)
        )
        _row_back = cur.fetchone()
    chk("C06_capture_writes_row", _row_back is not None,
        f"row not found for {_decision_id_A}")
except Exception as _e:
    chk("C06_capture_writes_row", False, str(_e))


# ─────────────────────────────────────────────────────────────────────────────
# C07: replay_decision() returns dict with all required keys
# ─────────────────────────────────────────────────────────────────────────────

_REQUIRED_REPLAY_KEYS = {
    "decision_id", "call_score_replayed", "put_score_replayed",
    "call_score_stored", "put_score_stored",
    "direction_replayed", "direction_stored",
    "call_match", "put_match", "direction_match", "full_match",
    "call_scoring", "put_scoring",
}

try:
    if _decision_id_A:
        _replay_A = replay_decision(_decision_id_A)
    _missing_keys = _REQUIRED_REPLAY_KEYS - set(_replay_A or {})
    chk("C07_replay_returns_structure",
        _replay_A is not None and not _missing_keys,
        f"missing keys: {_missing_keys}" if _missing_keys else "")
    if _replay_A:
        print(f"  [C07 detail] call_replayed={_replay_A['call_score_replayed']}  "
              f"call_stored={_replay_A['call_score_stored']}  "
              f"put_replayed={_replay_A['put_score_replayed']}  "
              f"put_stored={_replay_A['put_score_stored']}  "
              f"dir_replayed={_replay_A['direction_replayed']}  "
              f"dir_stored={_replay_A['direction_stored']}")
except Exception as _e:
    chk("C07_replay_returns_structure", False, str(_e))


# ─────────────────────────────────────────────────────────────────────────────
# C08: call_score replayed matches stored within 0.05
# ─────────────────────────────────────────────────────────────────────────────

try:
    if _replay_A:
        _dc = abs(_replay_A["call_score_replayed"] - _replay_A["call_score_stored"])
        chk("C08_call_score_replay_match", _dc < 0.05,
            f"diff={_dc:.4f}  "
            f"replayed={_replay_A['call_score_replayed']}  "
            f"stored={_replay_A['call_score_stored']}")
    else:
        chk("C08_call_score_replay_match", False, "no replay result from C07")
except Exception as _e:
    chk("C08_call_score_replay_match", False, str(_e))


# ─────────────────────────────────────────────────────────────────────────────
# C09: put_score replayed matches stored within 0.05
# ─────────────────────────────────────────────────────────────────────────────

try:
    if _replay_A:
        _dp = abs(_replay_A["put_score_replayed"] - _replay_A["put_score_stored"])
        chk("C09_put_score_replay_match", _dp < 0.05,
            f"diff={_dp:.4f}  "
            f"replayed={_replay_A['put_score_replayed']}  "
            f"stored={_replay_A['put_score_stored']}")
    else:
        chk("C09_put_score_replay_match", False, "no replay result from C07")
except Exception as _e:
    chk("C09_put_score_replay_match", False, str(_e))


# ─────────────────────────────────────────────────────────────────────────────
# C10: direction replayed matches stored_direction
# ─────────────────────────────────────────────────────────────────────────────

try:
    if _replay_A:
        chk("C10_direction_replay_match", _replay_A["direction_match"],
            f"replayed={_replay_A['direction_replayed']}  "
            f"stored={_replay_A['direction_stored']}")
    else:
        chk("C10_direction_replay_match", False, "no replay result from C07")
except Exception as _e:
    chk("C10_direction_replay_match", False, str(_e))


# ─────────────────────────────────────────────────────────────────────────────
# C11: Row B (mutated theta=1.50) captures and replays with full_match=True
# ─────────────────────────────────────────────────────────────────────────────

_decision_id_B = None
_replay_B = None

try:
    _dpl_row_B = write_decision(
        input_data ={"ticker": "P3TEST_B", "call_score": _exp_call_B,
                     "put_score": _exp_put_A},
        output_data={"direction": _exp_dir_B, "trace_id": "p3verif_B"},
        is_test_record=True,
    )
    _decision_id_B = _dpl_row_B["decision_id"]

    capture_replay_inputs(
        decision_id   = _decision_id_B,
        direction     = _exp_dir_B,
        call_score    = _exp_call_B,
        put_score     = _exp_put_A,
        call_data     = _CALL_DATA_B,    # mutated theta
        put_data      = _PUT_DATA,
        stock_data    = _STOCK_DATA,
        verify_result = _VERIFY_RESULT,
        iv_rank       = _IV_RANK,
        alert_id      = None,
    )

    _replay_B = replay_decision(_decision_id_B)
    chk("C11_mutation_row_replays",
        _replay_B is not None and _replay_B.get("full_match") is True,
        f"full_match={_replay_B.get('full_match') if _replay_B else None}  "
        f"call_r={_replay_B.get('call_score_replayed')}  "
        f"call_s={_replay_B.get('call_score_stored')}" if _replay_B else "no result")
    if _replay_B:
        print(f"  [C11 detail] call_replayed={_replay_B['call_score_replayed']}  "
              f"call_stored={_replay_B['call_score_stored']}  "
              f"dir_replayed={_replay_B['direction_replayed']}  "
              f"theta_mutated={_CALL_DATA_B['theta']}")
except Exception as _e:
    chk("C11_mutation_row_replays", False, str(_e))


# ─────────────────────────────────────────────────────────────────────────────
# C12: MUTATION CHECK — altered input produces different call_score
# ─────────────────────────────────────────────────────────────────────────────

try:
    if _replay_A and _replay_B:
        _score_A = _replay_A["call_score_replayed"]
        _score_B = _replay_B["call_score_replayed"]
        _diff_ab = abs(_score_A - _score_B)
        chk("C12_mutation_changes_output", _diff_ab >= 1.0,
            f"score_A={_score_A}  score_B={_score_B}  diff={_diff_ab:.2f}  "
            f"theta: {_CALL_DATA_A['theta']} -> {_CALL_DATA_B['theta']}")
        print(f"  [C12 detail] score_A={_score_A}  score_B={_score_B}  "
              f"diff={_diff_ab:.2f}  "
              f"theta_A={_CALL_DATA_A['theta']}  theta_B={_CALL_DATA_B['theta']}")
    else:
        chk("C12_mutation_changes_output", False,
            "one or both replay results missing (C07/C11 failed)")
except Exception as _e:
    chk("C12_mutation_changes_output", False, str(_e))


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────

print(f"\nSUMMARY: {len(_PASS)} PASS  {len(_FAIL)} FAIL")
if _FAIL:
    print(f"FAILED: {', '.join(_FAIL)}")

sys.exit(0 if not _FAIL else 1)
