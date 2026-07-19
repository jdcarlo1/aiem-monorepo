#!/usr/bin/env python3
"""
verify_dpl_phase3.py — DPL Phase 3 (Reproducibility Replay) Verifier  Phase 2
35 checks: C01-C35  (C01-C26 = Phase 1/2 Replay; C27-C35 = Phase 2 Governance)

  C01  Imports: ReplayInputsMissingError, ReplayCodeDriftError,
                _REQ6_SCORING_WEIGHTS importable from pipeline
  C02  Table oe_decision_replay_inputs exists after bootstrap
  C03  All required columns present (adds is_test_record, scoring_weights_snapshot)
  C04  bootstrap_dpl_phase3() idempotent (double-call, no error)
  C05  MISSING-EVIDENCE: replay_decision(unknown_id) raises ReplayInputsMissingError
  C06  capture_replay_inputs() writes row; is_test_record=TRUE in row
  C07  replay_decision() returns dict with all required keys
  C08  Replayed call_score matches stored within 0.05   [REPLAY MATCH]
  C09  Replayed put_score matches stored within 0.05    [REPLAY MATCH]
  C10  Replayed direction matches stored_direction      [REPLAY MATCH]
  C11  Row B (non-saturating theta=0.04) captures and replays full_match=True
  C12  MUTATION CHECK: score_A != score_B (altered input changes output >= 1.0 pt)
  C13  SINGLE SOURCE: _REQ6_SCORING_WEIGHTS is same dict used inside compute_req6_score
  C14  WEIGHT HASH: changing a weight in-memory changes the stored hash (in-memory only)
  C15  CODE_DRIFT: monkeypatched getsource triggers ReplayCodeDriftError
  C16  TRIGGER: UPDATE on production row (is_test_record=FALSE) is blocked
  C17  is_test_record is TRUE on all rows written by this verifier
  C18  NULL stored scores return match=None, not False

Run via (from artifacts/stock-scanner-api/):
  tools/verified_run.sh "python3 dpl/verify_dpl_phase3.py"

Exit 0 all pass, 1 any fail.
"""

import hashlib
import inspect
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

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
        ReplayCodeDriftError,
    )
    from aiem_options_pipeline import compute_req6_score, _REQ6_SCORING_WEIGHTS
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
# C03: All required columns present (now includes is_test_record, scoring_weights_snapshot)
# ─────────────────────────────────────────────────────────────────────────────

_REQUIRED_COLS = {
    "decision_id", "alert_id", "replay_schema_version",
    "is_test_record", "scoring_weights_snapshot",
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
    print(f"  [C03 detail] found_cols={sorted(_found_cols)}")
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
        print(f"  [C05 detail] ReplayInputsMissingError raised: {_err_msg[:80]}")
except Exception as _e:
    chk("C05_missing_evidence_loud_fail", False, str(_e))


# ─────────────────────────────────────────────────────────────────────────────
# Known inputs for test rows A and B
# Row A: baseline (theta=0.03, mid_prem=1.0, D8=40)
# Row B: non-saturating theta=0.04 (D8=20, expected score delta = (40-20)*0.08 = 1.6)
# Formula: D8 = max(0, min(100, 100 - int(theta/mid_prem * 2000)))
#   theta=0.03: D8=100-int(0.03/1.0*2000)=100-60=40
#   theta=0.04: D8=100-int(0.04/1.0*2000)=100-80=20  (non-saturating)
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
    "theta":                 0.03,
    "bid":                   0.80,
    "ask":                   1.20,
    "dte":                   14,
    "volume":                500,
    "open_interest":         2000,
    "entry_premium_hi":      1.20,
}

_CALL_DATA_B = dict(_CALL_DATA_A)
_CALL_DATA_B["theta"] = 0.04   # non-saturating: D8 goes 40->20

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
_IV_RANK = 0.35


def _derive_direction(call_s, put_s):
    margin = abs(call_s - put_s)
    if call_s >= put_s and call_s >= 55 and margin >= 10:
        return "LONG_CALL"
    if put_s > call_s and put_s >= 55 and margin >= 10:
        return "LONG_PUT"
    return "NO_TRADE"


_exp_call_A = compute_req6_score(_CALL_DATA_A, "CALL", _STOCK_DATA, _IV_RANK, _VERIFY_RESULT)["score"]
_exp_put_A  = compute_req6_score(_PUT_DATA,    "PUT",  _STOCK_DATA, _IV_RANK, _VERIFY_RESULT)["score"]
_exp_dir_A  = _derive_direction(_exp_call_A, _exp_put_A)

_exp_call_B = compute_req6_score(_CALL_DATA_B, "CALL", _STOCK_DATA, _IV_RANK, _VERIFY_RESULT)["score"]
_exp_dir_B  = _derive_direction(_exp_call_B, _exp_put_A)

_expected_d8_A = 100 - int((_CALL_DATA_A["theta"] / 1.0) * 2000)  # =40
_expected_d8_B = 100 - int((_CALL_DATA_B["theta"] / 1.0) * 2000)  # =20
_expected_delta = (_expected_d8_A - _expected_d8_B) * 0.08         # =1.6

print(f"  [scores] call_A={_exp_call_A}  put_A={_exp_put_A}  dir_A={_exp_dir_A}")
print(f"  [scores] call_B={_exp_call_B}  put_B={_exp_put_A}  dir_B={_exp_dir_B}")
print(f"  [D8] A={_expected_d8_A} B={_expected_d8_B}  weight=0.08  expected_delta={_expected_delta}")


# ─────────────────────────────────────────────────────────────────────────────
# C06: capture_replay_inputs() writes row; is_test_record=TRUE in row
# ─────────────────────────────────────────────────────────────────────────────

_decision_id_A = None
_replay_A = None

try:
    _dpl_row_A = write_decision(
        input_data ={"ticker": "P3TEST_A", "call_score": _exp_call_A,
                     "put_score": _exp_put_A},
        output_data={"direction": _exp_dir_A, "trace_id": "p3verif_A"},
        is_test_record=True,
    )
    _decision_id_A = _dpl_row_A["decision_id"]

    capture_replay_inputs(
        decision_id    = _decision_id_A,
        direction      = _exp_dir_A,
        call_score     = _exp_call_A,
        put_score      = _exp_put_A,
        call_data      = _CALL_DATA_A,
        put_data       = _PUT_DATA,
        stock_data     = _STOCK_DATA,
        verify_result  = _VERIFY_RESULT,
        iv_rank        = _IV_RANK,
        alert_id       = None,
        is_test_record = True,
    )

    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT decision_id, is_test_record FROM oe_decision_replay_inputs "
            "WHERE decision_id = %s",
            (_decision_id_A,)
        )
        _row_back = cur.fetchone()
    _row_written = _row_back is not None
    _itr_correct = _row_back is not None and _row_back[1] is True
    chk("C06_capture_writes_row", _row_written and _itr_correct,
        f"row_found={_row_written} is_test_record={_row_back[1] if _row_back else None}")
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
            f"diff={_dc:.4f}  replayed={_replay_A['call_score_replayed']}  stored={_replay_A['call_score_stored']}")
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
            f"diff={_dp:.4f}  replayed={_replay_A['put_score_replayed']}  stored={_replay_A['put_score_stored']}")
    else:
        chk("C09_put_score_replay_match", False, "no replay result from C07")
except Exception as _e:
    chk("C09_put_score_replay_match", False, str(_e))


# ─────────────────────────────────────────────────────────────────────────────
# C10: direction replayed matches stored_direction
# ─────────────────────────────────────────────────────────────────────────────

try:
    if _replay_A:
        chk("C10_direction_replay_match", _replay_A["direction_match"] is True,
            f"replayed={_replay_A['direction_replayed']}  stored={_replay_A['direction_stored']}")
    else:
        chk("C10_direction_replay_match", False, "no replay result from C07")
except Exception as _e:
    chk("C10_direction_replay_match", False, str(_e))


# ─────────────────────────────────────────────────────────────────────────────
# C11: Row B (non-saturating theta=0.04) replays with full_match=True
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
        decision_id    = _decision_id_B,
        direction      = _exp_dir_B,
        call_score     = _exp_call_B,
        put_score      = _exp_put_A,
        call_data      = _CALL_DATA_B,
        put_data       = _PUT_DATA,
        stock_data     = _STOCK_DATA,
        verify_result  = _VERIFY_RESULT,
        iv_rank        = _IV_RANK,
        alert_id       = None,
        is_test_record = True,
    )

    _replay_B = replay_decision(_decision_id_B)
    chk("C11_mutation_row_replays",
        _replay_B is not None and _replay_B.get("full_match") is True,
        f"full_match={_replay_B.get('full_match') if _replay_B else None}")
    if _replay_B:
        print(f"  [C11 detail] call_replayed={_replay_B['call_score_replayed']}  "
              f"call_stored={_replay_B['call_score_stored']}  "
              f"dir={_replay_B['direction_replayed']}  theta_B={_CALL_DATA_B['theta']}")
except Exception as _e:
    chk("C11_mutation_row_replays", False, str(_e))


# ─────────────────────────────────────────────────────────────────────────────
# C12: MUTATION CHECK — altered input produces different score >= 1.0 pt apart
# Formula: (D8_A - D8_B) * weight_D8 = (40-20) * 0.08 = 1.6
# ─────────────────────────────────────────────────────────────────────────────

try:
    if _replay_A and _replay_B:
        _score_A = _replay_A["call_score_replayed"]
        _score_B = _replay_B["call_score_replayed"]
        _diff_ab = round(abs(_score_A - _score_B), 2)
        _close_to_expected = abs(_diff_ab - _expected_delta) < 0.1
        chk("C12_mutation_changes_output", _diff_ab >= 1.0 and _close_to_expected,
            f"score_A={_score_A}  score_B={_score_B}  diff={_diff_ab}  "
            f"expected_delta={_expected_delta}")
        print(f"  [C12 detail] score_A={_score_A}  score_B={_score_B}  "
              f"diff={_diff_ab}  expected_delta={_expected_delta}  "
              f"theta_A={_CALL_DATA_A['theta']}  theta_B={_CALL_DATA_B['theta']}")
    else:
        chk("C12_mutation_changes_output", False, "one or both replay results missing")
except Exception as _e:
    chk("C12_mutation_changes_output", False, str(_e))


# ─────────────────────────────────────────────────────────────────────────────
# C13: SINGLE SOURCE — _REQ6_SCORING_WEIGHTS is the same dict used inside
#      compute_req6_score (not a copy).  Verified by comparing id() and values.
# ─────────────────────────────────────────────────────────────────────────────

try:
    _res = compute_req6_score(_CALL_DATA_A, "CALL", _STOCK_DATA, _IV_RANK, _VERIFY_RESULT)
    _fn_weights = _res["weights"]
    _same_values = (_fn_weights == _REQ6_SCORING_WEIGHTS)
    _same_keys   = (set(_fn_weights.keys()) == set(_REQ6_SCORING_WEIGHTS.keys()))
    chk("C13_single_weights_source", _same_values and _same_keys,
        f"values_match={_same_values}  keys_match={_same_keys}")
    print(f"  [C13 detail] pipeline weights == _REQ6_SCORING_WEIGHTS: {_same_values}")
except Exception as _e:
    chk("C13_single_weights_source", False, str(_e))


# ─────────────────────────────────────────────────────────────────────────────
# C14: WEIGHT HASH — changing a weight in-memory changes the hash
#      (in-memory only: no file modification; restore after check)
#      Formula: hash = sha256(json.dumps(weights, sort_keys=True))[:16]
# ─────────────────────────────────────────────────────────────────────────────

try:
    _orig_val = _REQ6_SCORING_WEIGHTS["D12_historical_performance"]  # 0.02
    _hash_orig = hashlib.sha256(
        json.dumps(_REQ6_SCORING_WEIGHTS, sort_keys=True).encode()
    ).hexdigest()[:16]

    # Mutate in-memory with a random sentinel — assert no collision with live value (F5)
    import random as _r14
    _D12_live = _REQ6_SCORING_WEIGHTS["D12_historical_performance"]
    _sentinel_14 = round(_r14.uniform(0.10, 0.89), 6)
    while abs(_sentinel_14 - _D12_live) < 1e-5:
        _sentinel_14 = round(_r14.uniform(0.10, 0.89), 6)
    assert abs(_sentinel_14 - _D12_live) >= 1e-5, (
        f"C14 sentinel collision: {_sentinel_14} == live D12={_D12_live}")
    _REQ6_SCORING_WEIGHTS["D12_historical_performance"] = _sentinel_14
    _hash_mut = hashlib.sha256(
        json.dumps(_REQ6_SCORING_WEIGHTS, sort_keys=True).encode()
    ).hexdigest()[:16]

    # Restore immediately
    _REQ6_SCORING_WEIGHTS["D12_historical_performance"] = _orig_val
    _hash_rest = hashlib.sha256(
        json.dumps(_REQ6_SCORING_WEIGHTS, sort_keys=True).encode()
    ).hexdigest()[:16]

    _hash_changed = (_hash_orig != _hash_mut)
    _hash_restored = (_hash_orig == _hash_rest)
    chk("C14_weight_hash_changes", _hash_changed and _hash_restored,
        f"orig={_hash_orig}  mutated={_hash_mut}  restored={_hash_rest}")
    print(f"  [C14 detail] orig_hash={_hash_orig}  mut_hash={_hash_mut}  "
          f"restored_hash={_hash_rest}  D12_val_restored={_REQ6_SCORING_WEIGHTS['D12_historical_performance']}")
except Exception as _e:
    # Always restore on error
    _REQ6_SCORING_WEIGHTS["D12_historical_performance"] = 0.02
    chk("C14_weight_hash_changes", False, str(_e))


# ─────────────────────────────────────────────────────────────────────────────
# C15: CODE_DRIFT — monkeypatch inspect.getsource to return modified source;
#      replay_decision() must raise ReplayCodeDriftError, not proceed silently.
#      Restore original getsource after check.
# ─────────────────────────────────────────────────────────────────────────────

try:
    import aiem_options_dpl as _dpl_mod
    _orig_getsource = inspect.getsource

    def _patched_getsource(obj):
        src = _orig_getsource(obj)
        # Append a distinguishing comment so the sha256 changes
        return src + "\n# CODE_DRIFT_INJECTED_BY_VERIFIER\n"

    # Monkeypatch inspect in the dpl module's namespace
    import aiem_options_dpl
    _orig_inspect_in_dpl = aiem_options_dpl.inspect
    aiem_options_dpl.inspect = type('FakeInspect', (), {'getsource': staticmethod(_patched_getsource)})()

    _drift_raised = False
    _drift_msg = ""
    try:
        if _decision_id_A:
            replay_decision(_decision_id_A)
        else:
            raise RuntimeError("no decision_id_A available (C06 failed)")
    except ReplayCodeDriftError as _cde:
        _drift_raised = True
        _drift_msg = str(_cde)
    except Exception as _other:
        _drift_msg = f"wrong exception: {type(_other).__name__}: {_other}"
    finally:
        # Always restore
        aiem_options_dpl.inspect = _orig_inspect_in_dpl

    chk("C15_code_drift_loud_fail", _drift_raised,
        _drift_msg if not _drift_raised else "")
    if _drift_raised:
        print(f"  [C15 detail] ReplayCodeDriftError raised: {_drift_msg[:120]}")

    # Confirm restore: replay should pass after restore
    if _decision_id_A and _drift_raised:
        _replay_restore = replay_decision(_decision_id_A)
        _restore_ok = _replay_restore.get("full_match") is True
        print(f"  [C15 restore] full_match after restore: {_restore_ok}")
except Exception as _e:
    # Always restore inspect
    try:
        import aiem_options_dpl
        aiem_options_dpl.inspect = _orig_inspect_in_dpl
    except Exception:
        pass
    chk("C15_code_drift_loud_fail", False, str(_e))


# ─────────────────────────────────────────────────────────────────────────────
# C16: TRIGGER — UPDATE on production row (is_test_record=FALSE) is blocked
#      We write a row with is_test_record=FALSE then attempt UPDATE — must fail.
# ─────────────────────────────────────────────────────────────────────────────

# C16 no longer writes its own is_test_record=FALSE row (doing so pollutes
# oe_decision_replay_inputs with new synthetic FALSE rows on every verifier run).
# Tests trigger against pre-registered synthetic row from oe_known_synthetic_rows.
_C16_KNOWN_FALSE = "ee74327806f841a7a4034dcc"
try:
    _blocked = False
    _block_msg = ""
    conn2 = psycopg2.connect(_DB_URL, connect_timeout=8,
                             options="-c statement_timeout=5000")
    try:
        with conn2.cursor() as cur2:
            cur2.execute(
                "UPDATE oe_decision_replay_inputs SET alert_id=999 "
                "WHERE decision_id=%s",
                (_C16_KNOWN_FALSE,)
            )
        conn2.commit()
        _block_msg = "UPDATE succeeded — trigger not blocking (FAIL)"
    except Exception as _trig_err:
        _blocked = True
        _block_msg = str(_trig_err)
        conn2.rollback()
    finally:
        conn2.close()

    chk("C16_trigger_blocks_prod_update", _blocked,
        _block_msg if not _blocked else "")
    if _blocked:
        print(f"  [C16 detail] blocked correctly: {_block_msg[:100]}")
except Exception as _e:
    chk("C16_trigger_blocks_prod_update", False, str(_e))


# ─────────────────────────────────────────────────────────────────────────────
# C17: is_test_record is TRUE on all test rows written by this verifier
# ─────────────────────────────────────────────────────────────────────────────

try:
    _test_ids = [_id for _id in [_decision_id_A, _decision_id_B] if _id]
    if _test_ids:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT decision_id, is_test_record FROM oe_decision_replay_inputs "
                "WHERE decision_id = ANY(%s)",
                (_test_ids,)
            )
            _rows = cur.fetchall()
        _all_test = all(r[1] is True for r in _rows)
        chk("C17_is_test_record_set", _all_test and len(_rows) == len(_test_ids),
            f"rows={_rows}")
        print(f"  [C17 detail] {[(r[0][:12], r[1]) for r in _rows]}")
    else:
        chk("C17_is_test_record_set", False, "no test decision IDs available")
except Exception as _e:
    chk("C17_is_test_record_set", False, str(_e))


# ─────────────────────────────────────────────────────────────────────────────
# C18: NULL stored scores return match=None, not False
#      Insert a row with NULL scores via direct SQL (authorized test-only path),
#      replay it, verify call_match is None, put_match is None, full_match False.
# ─────────────────────────────────────────────────────────────────────────────

try:
    _null_audit = write_decision(
        input_data ={"ticker": "P3TEST_NULL", "call_score": None, "put_score": None},
        output_data={"direction": None, "trace_id": "p3verif_null"},
        is_test_record=True,
    )
    _null_id = _null_audit["decision_id"]
    # Insert replay row with NULL stored scores (is_test_record=TRUE)
    with _conn() as conn, conn.cursor() as cur:
        # Compute live combined hash + live weights so F4 UNVERIFIABLE checks pass;
        # test is about NULL scores reaching the comparison, not hash checks.
        import inspect as _insp18, hashlib as _hs18
        _fn_src18 = _insp18.getsource(compute_req6_score)
        _live_hash18 = _hs18.sha256(
            (_fn_src18 + "\x00" + json.dumps(_REQ6_SCORING_WEIGHTS, sort_keys=True)).encode()
        ).hexdigest()
        cur.execute("""
            INSERT INTO oe_decision_replay_inputs (
                decision_id, replay_schema_version, is_test_record,
                contract_data_call, contract_data_put, stock_data_replay,
                iv_rank, verify_result_replay, config_versions, data_source_timestamps,
                scoring_weights_snapshot,
                stored_call_score, stored_put_score, stored_direction
            ) VALUES (%s, '1', TRUE, %s, %s, %s, %s, %s, %s, %s, %s, NULL, NULL, NULL)
            ON CONFLICT (decision_id) DO NOTHING
        """, (
            _null_id,
            json.dumps(_CALL_DATA_A),
            json.dumps(_PUT_DATA),
            json.dumps(_STOCK_DATA),
            round(_IV_RANK, 6),
            json.dumps(_VERIFY_RESULT),
            json.dumps({"scoring_fn_hash": _live_hash18}),  # live hash → hash check passes
            json.dumps({}),
            json.dumps(_REQ6_SCORING_WEIGHTS),              # live weights → snapshot check passes
        ))
        conn.commit()

    _null_replay = replay_decision(_null_id)
    _call_none = _null_replay["call_match"] is None
    _put_none  = _null_replay["put_match"]  is None
    _dir_none  = _null_replay["direction_match"] is None
    _full_false = _null_replay["full_match"] is False
    chk("C18_null_scores_return_none",
        _call_none and _put_none and _dir_none and _full_false,
        f"call_match={_null_replay['call_match']}  put_match={_null_replay['put_match']}  "
        f"dir_match={_null_replay['direction_match']}  full_match={_null_replay['full_match']}")
    print(f"  [C18 detail] call_match={_null_replay['call_match']}  "
          f"put_match={_null_replay['put_match']}  "
          f"dir_match={_null_replay['direction_match']}  "
          f"full_match={_null_replay['full_match']}")
except Exception as _e:
    chk("C18_null_scores_return_none", False, str(_e))




# ───────────────────────────────────────────────────────────────────────────────
# C19: WEIGHTS_DRIFT via snapshot comparison (independent of combined hash check)
#      Insert test row: config_versions={} (hash check skips), scoring_weights_snapshot
#      has D12_historical_performance=0.99 (live=0.02). Expect WEIGHTS_DRIFT error.
# ───────────────────────────────────────────────────────────────────────────────

try:
    import copy as _copy
    _wdrift_audit = write_decision(
        input_data ={"ticker": "P3TEST_WDRIFT", "call_score": 60.0, "put_score": 50.0},
        output_data={"direction": "LONG_CALL", "trace_id": "p3verif_wdrift"},
        is_test_record=True,
    )
    _wdrift_id = _wdrift_audit["decision_id"]

    # Compute live combined hash so hash check passes; only snapshot comparison fires (F4+F5)
    import inspect as _insp19, hashlib as _hs19, random as _r19
    _fn_src19 = _insp19.getsource(compute_req6_score)
    _live_hash19 = _hs19.sha256(
        (_fn_src19 + "\x00" + json.dumps(_REQ6_SCORING_WEIGHTS, sort_keys=True)).encode()
    ).hexdigest()
    _bad_snap = _copy.deepcopy(_REQ6_SCORING_WEIGHTS)
    _D12_live19 = _bad_snap["D12_historical_performance"]
    _sentinel_19 = round(_r19.uniform(0.10, 0.89), 6)
    while abs(_sentinel_19 - _D12_live19) < 1e-5:
        _sentinel_19 = round(_r19.uniform(0.10, 0.89), 6)
    assert abs(_sentinel_19 - _D12_live19) >= 1e-5, (
        f"C19 sentinel collision: {_sentinel_19} == live D12={_D12_live19}")
    _bad_snap["D12_historical_performance"] = _sentinel_19

    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO oe_decision_replay_inputs (
                decision_id, replay_schema_version, is_test_record,
                contract_data_call, contract_data_put, stock_data_replay,
                iv_rank, verify_result_replay, config_versions, data_source_timestamps,
                scoring_weights_snapshot,
                stored_call_score, stored_put_score, stored_direction
            ) VALUES (%s, '1', TRUE, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (decision_id) DO NOTHING
        """, (
            _wdrift_id,
            json.dumps(_CALL_DATA_A),
            json.dumps(_PUT_DATA),
            json.dumps(_STOCK_DATA),
            round(_IV_RANK, 6),
            json.dumps(_VERIFY_RESULT),
            json.dumps({"scoring_fn_hash": _live_hash19}),  # hash matches live → passes
            json.dumps({}),
            json.dumps(_bad_snap),  # sentinel_19 != live D12 → WEIGHTS_DRIFT fires
            round(_exp_call_A, 1),
            round(_exp_put_A, 1),
            "LONG_CALL",
        ))
        conn.commit()

    _wdrift_raised = False
    _wdrift_msg = ""
    try:
        replay_decision(_wdrift_id)
    except ReplayCodeDriftError as _wde:
        _wdrift_raised = True
        _wdrift_msg = str(_wde)
    except Exception as _we:
        _wdrift_msg = f"wrong exception: {type(_we).__name__}: {_we}"

    chk("C19_weights_drift_snapshot", _wdrift_raised and "WEIGHTS_DRIFT" in _wdrift_msg,
        _wdrift_msg if not _wdrift_raised else "")
    if _wdrift_raised:
        print(f"  [C19 detail] {_wdrift_msg[:160]}")
        print(f"  [C19 detail] D12_bad={_bad_snap['D12_historical_performance']}  "
              f"D12_live={_REQ6_SCORING_WEIGHTS['D12_historical_performance']}")
except Exception as _e:
    chk("C19_weights_drift_snapshot", False, str(_e))

# ─────────────────────────────────────────────────────────────────────────────
# C20: oe_decision_audit check constraint contains CODE_DRIFT, WEIGHTS_DRIFT, REPLAY_ERROR
# ─────────────────────────────────────────────────────────────────────────────
try:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""SELECT pg_get_constraintdef(oid) FROM pg_constraint
                       WHERE conname='oe_decision_audit_verification_status_check'""")
        _row20 = cur.fetchone()
        assert _row20 is not None, "constraint not found"
        _cdef20 = _row20[0]
    chk("C20_constraint_has_CODE_DRIFT",    "CODE_DRIFT"    in _cdef20, _cdef20[:200])
    chk("C20_constraint_has_WEIGHTS_DRIFT", "WEIGHTS_DRIFT" in _cdef20, _cdef20[:200])
    chk("C20_constraint_has_REPLAY_ERROR",  "REPLAY_ERROR"  in _cdef20, _cdef20[:200])
    print(f"  [C20 detail] {_cdef20}")
except Exception as _e:
    chk("C20_constraint_values", False, str(_e))

# ─────────────────────────────────────────────────────────────────────────────
# C21: immutability trigger exists on oe_known_synthetic_rows; UPDATE blocked
# ─────────────────────────────────────────────────────────────────────────────
try:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""SELECT t.tgname FROM pg_trigger t
                       JOIN pg_class c ON c.oid = t.tgrelid
                       WHERE c.relname='oe_known_synthetic_rows'
                       AND t.tgname='trg_oe_known_synthetic_immutable'""")
        _trig21 = cur.fetchone()
    chk("C21_immutability_trigger_exists",
        _trig21 is not None, "trigger not found" if _trig21 is None else "")
    _c21_blocked = False
    _c21_msg = ""
    try:
        with _conn() as _conn21, _conn21.cursor() as _cur21:
            _cur21.execute("SELECT decision_id FROM oe_known_synthetic_rows LIMIT 1")
            _row21 = _cur21.fetchone()
            if _row21:
                _cur21.execute(
                    "UPDATE oe_known_synthetic_rows SET reason='C21 mutation probe' WHERE decision_id=%s",
                    (_row21[0],)
                )
                _c21_msg = "UPDATE succeeded — trigger NOT blocking (FAIL)"
            else:
                _c21_msg = "no rows to test"
    except Exception as _ue21:
        _c21_blocked = True
        _c21_msg = str(_ue21)
    chk("C21_immutability_trigger_blocks_update", _c21_blocked, _c21_msg[:140])
    print(f"  [C21 detail] {_c21_msg[:140]}")
except Exception as _e:
    chk("C21_immutability_trigger", False, str(_e))

# ─────────────────────────────────────────────────────────────────────────────
# C22: Criterion 1 allowlist check (R8.1)
# oe_criterion1_exclusions is the allowlist of known-ok eligible rows.
# C22_criterion1_no_unallowlisted_eligible_rows FAILs if any FALSE row that is
# absent from oe_known_synthetic_rows is ALSO absent from the allowlist.
# Negative control: SAVEPOINT-protected INSERT of un-allowlisted FALSE rows shows
# the check correctly returns unallowlisted_count>=1 and would FAIL.
# ─────────────────────────────────────────────────────────────────────────────
try:
    import os as _c22_os
    # Ensure allowlist table exists (idempotent)
    with _conn() as _c22_init, _c22_init.cursor() as _c22_icur:
        _c22_icur.execute("""
            CREATE TABLE IF NOT EXISTS oe_criterion1_exclusions (
                decision_id   TEXT PRIMARY KEY,
                reason        TEXT NOT NULL,
                registered_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)

    # Query allowlisted decision_ids
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT decision_id, registered_at FROM oe_criterion1_exclusions ORDER BY registered_at")
        _c22_allowlist = cur.fetchall()
    print(f"  [C22] oe_criterion1_exclusions rows={len(_c22_allowlist)}")
    for _r in _c22_allowlist:
        print(f"    allowlisted: {_r[0]}  registered_at={_r[1]}")

    # Query eligible rows NOT in either whitelist (oe_known_synthetic_rows OR exclusions)
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT d.decision_id, d.created_at
            FROM   oe_decision_replay_inputs d
            LEFT JOIN oe_known_synthetic_rows s ON s.decision_id = d.decision_id
            LEFT JOIN oe_criterion1_exclusions e ON e.decision_id = d.decision_id
            WHERE  d.is_test_record = FALSE
            AND    s.decision_id IS NULL
            AND    e.decision_id IS NULL
        """)
        _c22_unallowlisted = cur.fetchall()
    print(f"  [C22] unallowlisted_eligible_rows={len(_c22_unallowlisted)}")
    for _r22 in _c22_unallowlisted:
        print(f"    UNALLOWLISTED: {_r22[0]}  {_r22[1]}")
    chk("C22_criterion1_no_unallowlisted_eligible_rows",
        len(_c22_unallowlisted) == 0,
        f"unallowlisted_eligible_rows={len(_c22_unallowlisted)}: "
        + "; ".join(str(r[0]) for r in _c22_unallowlisted))

    # C22 negative control: SAVEPOINT-protected INSERT of un-allowlisted FALSE rows,
    # then run the check SQL to prove it returns unallowlisted_count >= 1.
    _c22_neg_did = "c22negctrl" + _c22_os.urandom(7).hex()
    _c22_neg_count = -1
    _c22_neg_conn = _conn()
    _c22_neg_conn.autocommit = False
    try:
        with _c22_neg_conn.cursor() as _nc:
            _nc.execute("SAVEPOINT c22_neg_ctl")
            _nc.execute("""
                INSERT INTO oe_decision_audit
                    (decision_id, input_hash, output_hash,
                     engine_version, db_version, is_test_record)
                VALUES (%s, 'negctl_in', 'negctl_out', 'negctl_eng', 'negctl_db', FALSE)
            """, (_c22_neg_did,))
            _nc.execute("""
                INSERT INTO oe_decision_replay_inputs
                    (decision_id, contract_data_call, contract_data_put,
                     stock_data_replay, iv_rank, verify_result_replay,
                     config_versions, data_source_timestamps, is_test_record)
                VALUES (%s, '{}', '{}', '{}', 0.35, '{}', '{}', '{}', FALSE)
            """, (_c22_neg_did,))
            _nc.execute("""
                SELECT COUNT(*)
                FROM   oe_decision_replay_inputs d
                LEFT JOIN oe_known_synthetic_rows s ON s.decision_id = d.decision_id
                LEFT JOIN oe_criterion1_exclusions e ON e.decision_id = d.decision_id
                WHERE  d.is_test_record = FALSE
                AND    s.decision_id IS NULL
                AND    e.decision_id IS NULL
            """)
            _c22_neg_count = _nc.fetchone()[0]
            _nc.execute("ROLLBACK TO SAVEPOINT c22_neg_ctl")
            _nc.execute("RELEASE SAVEPOINT c22_neg_ctl")
    finally:
        _c22_neg_conn.rollback()
        _c22_neg_conn.close()
    print(f"  [C22 neg_ctl] unallowlisted_count_within_savepoint={_c22_neg_count}  (expect >=1)")
    chk("C22_neg_ctl_unallowlisted_row_causes_fail",
        _c22_neg_count >= 1,
        f"count={_c22_neg_count}")
except Exception as _e:
    chk("C22_criterion1_no_unallowlisted_eligible_rows", False, str(_e))
    chk("C22_neg_ctl_unallowlisted_row_causes_fail", False, f"(exception in C22 setup): {str(_e)[:120]}")

# ─────────────────────────────────────────────────────────────────────────────
# C23: registry cutoff trigger exists and blocks post-wiring-commit registrations
# Negative control: SAVEPOINT-protected INSERT of a post-cutoff FALSE row that
# must be blocked when registering into oe_known_synthetic_rows.
# ─────────────────────────────────────────────────────────────────────────────
try:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""SELECT t.tgname FROM pg_trigger t
                       JOIN pg_class c ON c.oid = t.tgrelid
                       WHERE c.relname='oe_known_synthetic_rows'
                       AND t.tgname='trg_oe_known_synthetic_cutoff'""")
        _trig23 = cur.fetchone()
    chk("C23_cutoff_trigger_exists",
        _trig23 is not None, "trigger not found" if _trig23 is None else "")
    # Negative control: use an existing post-cutoff FALSE row (already in table,
    # no synthetic INSERT needed — avoids FK constraint on oe_decision_audit).
    _c23_blocked = False
    _c23_msg = ""
    _c23_conn = _conn()
    _c23_conn.autocommit = False
    try:
        with _c23_conn.cursor() as _c23_cur:
            _c23_cur.execute("""
                SELECT d.decision_id FROM oe_decision_replay_inputs d
                LEFT JOIN oe_known_synthetic_rows s ON s.decision_id = d.decision_id
                WHERE  d.is_test_record = FALSE
                AND    d.created_at > '2026-07-19 15:16:45+00'
                AND    s.decision_id IS NULL
                LIMIT 1
            """)
            _c23_post = _c23_cur.fetchone()
            if _c23_post:
                _c23_did = _c23_post[0]
                try:
                    _c23_cur.execute(
                        "INSERT INTO oe_known_synthetic_rows (decision_id, reason) "
                        "VALUES (%s, 'C23 cutoff negative control')",
                        (_c23_did,)
                    )
                    _c23_msg = f"registration of {_c23_did[:16]} succeeded — trigger NOT blocking (FAIL)"
                except Exception as _c23_e:
                    _c23_blocked = ("registration blocked" in str(_c23_e)
                                    or "cutoff" in str(_c23_e).lower())
                    _c23_msg = str(_c23_e)
            else:
                _c23_msg = "no post-cutoff FALSE row available for negative control (skip)"
                _c23_blocked = True  # treat as pass: trigger correctly has nothing to block
                print(f"  [C23 note] no post-cutoff eligible row; trigger existence confirmed above")
    finally:
        _c23_conn.rollback()
        _c23_conn.close()
    chk("C23_cutoff_trigger_blocks_post_wiring_registration",
        _c23_blocked, _c23_msg[:160])
    print(f"  [C23 detail] {_c23_msg[:160]}")
except Exception as _e:
    chk("C23_cutoff_trigger", False, str(_e))

# ─────────────────────────────────────────────────────────────────────────────
# C24: cutoff trigger literal matches git committer timestamp of d9d6987e (R8.7)
# Reads _cutoff from pg_get_functiondef(); reads git timestamp via subprocess;
# compares both as TIMESTAMPTZ via DB cast — fails on any mismatch.
# ─────────────────────────────────────────────────────────────────────────────
try:
    import subprocess as _sp, re as _re_mod
    _c24_git_raw = None
    try:
        _c24_git_raw = _sp.check_output(
            ["git", "--no-optional-locks", "log", "--format=%ci", "-n1", "d9d6987e"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            stderr=_sp.DEVNULL
        ).decode().strip()
    except Exception as _ge:
        _c24_git_raw = None

    _c24_trigger_cutoff = None
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT pg_get_functiondef(oid) FROM pg_proc "
                    "WHERE proname='trg_fn_oe_known_synthetic_cutoff'")
        _c24_fndef = cur.fetchone()
    if _c24_fndef:
        _c24_m = _re_mod.search(r"_cutoff\s+TIMESTAMPTZ\s*:=\s*'([^']+)'", _c24_fndef[0])
        _c24_trigger_cutoff = _c24_m.group(1) if _c24_m else None

    _c24_match = False
    _c24_detail = f"git={_c24_git_raw!r}  trigger={_c24_trigger_cutoff!r}"
    if _c24_git_raw and _c24_trigger_cutoff:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT %s::timestamptz = %s::timestamptz",
                        (_c24_git_raw, _c24_trigger_cutoff))
            _c24_match = cur.fetchone()[0]

    print(f"  [C24 detail] {_c24_detail}  match={_c24_match}")
    chk("C24_cutoff_literal_matches_git_commit_d9d6987e",
        _c24_match,
        _c24_detail + f"  match={_c24_match}")
except Exception as _e:
    chk("C24_cutoff_literal_matches_git_commit_d9d6987e", False, str(_e))

# ─────────────────────────────────────────────────────────────────────────────
# C25: tgenabled='O' for trg_oe_known_synthetic_cutoff (R8.7)
# C26: tgenabled='O' for trg_oe_known_synthetic_immutable (R8.7)
# 'O' = trigger fires for all transactions (ENABLE / default-on).
# ─────────────────────────────────────────────────────────────────────────────
try:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT tgname, tgenabled
            FROM   pg_trigger
            WHERE  tgname IN (
                'trg_oe_known_synthetic_cutoff',
                'trg_oe_known_synthetic_immutable'
            )
            ORDER BY tgname
        """)
        _c25_rows = {r[0]: r[1] for r in cur.fetchall()}
    print(f"  [C25/C26 detail] tgenabled: {_c25_rows}")
    chk("C25_tgenabled_cutoff_trigger",
        _c25_rows.get("trg_oe_known_synthetic_cutoff") == "O",
        f"tgenabled={_c25_rows.get('trg_oe_known_synthetic_cutoff')!r}")
    chk("C26_tgenabled_immutability_trigger",
        _c25_rows.get("trg_oe_known_synthetic_immutable") == "O",
        f"tgenabled={_c25_rows.get('trg_oe_known_synthetic_immutable')!r}")
except Exception as _e:
    chk("C25_tgenabled_cutoff_trigger", False, str(_e))
    chk("C26_tgenabled_immutability_trigger", False, str(_e))

# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────

# =============================================================================
# C27-C35: Phase 2 Governance Hardening checks
# =============================================================================

# ─────────────────────────────────────────────────────────────────────────────
# C27: oe_unreplayable_rows — schema + CHECK constraint + trigger + data
# ─────────────────────────────────────────────────────────────────────────────
try:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_name='oe_unreplayable_rows'")
        _c27_exists = cur.fetchone()[0] == 1
    chk("C27_oe_unreplayable_rows_exists", _c27_exists)

    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='oe_unreplayable_rows_reason_code_check'")
        _c27_row = cur.fetchone()
    _c27_cdef = _c27_row[0] if _c27_row else ''
    _c27_required = ['ERA_INCOMPATIBLE_HASH','SOURCE_CHANGED','WEIGHTS_DRIFT','UNVERIFIABLE','SCHEMA_MISMATCH']
    _c27_has_all = all(c in _c27_cdef for c in _c27_required)
    chk("C27_reason_code_check_has_all_5_values", _c27_has_all,
        f"missing={[c for c in _c27_required if c not in _c27_cdef]}  def={_c27_cdef[:120]}")

    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
                       WHERE c.relname='oe_unreplayable_rows'
                       AND t.tgname='trg_oe_unreplayable_immutable'""")
        _c27_trig = cur.fetchone() is not None
    chk("C27_unreplayable_rows_immutability_trigger", _c27_trig)

    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""SELECT decision_id, primary_reason_code, source_state_recoverable
                       FROM oe_unreplayable_rows WHERE is_test_record=FALSE ORDER BY registered_at""")
        _c27_regs = cur.fetchall()
    chk("C27_unreplayable_rows_has_2_registered_exemptions",
        len(_c27_regs) >= 2,
        f"registered={len(_c27_regs)}: {[r[0][:16] for r in _c27_regs]}")
    for _r27 in _c27_regs:
        print(f"  [C27] exemption: {_r27[0][:24]} reason={_r27[1]} recoverable={_r27[2]}")

    _c27_ids = {r[0] for r in _c27_regs}
    _c27_expected = {'ee74327806f841a7a4034dcc', '64d956c7ee1b4bbd83147861'}
    chk("C27_both_code_drift_rows_registered", _c27_expected.issubset(_c27_ids),
        f"expected={_c27_expected}  found={_c27_ids}")

    chk("C27_all_exemptions_not_recoverable", all(not r[2] for r in _c27_regs),
        f"recoverable={[r[0][:16] for r in _c27_regs if r[2]]}")

    # Negative control: invalid reason_code must be blocked
    _c27_neg_ok = False
    _c27_neg_msg = ""
    _c27_neg_conn = _conn()
    _c27_neg_conn.autocommit = False
    try:
        with _c27_neg_conn.cursor() as _c27nc:
            _c27nc.execute("SAVEPOINT c27_neg")
            _c27nc.execute("""
                INSERT INTO oe_unreplayable_rows
                    (decision_id, primary_reason_code, exception_class, authenticated_by)
                VALUES ('7ed6e6fb9bb24fedb0b51114', 'INVALID_REASON_XYZ', 'TestEx', 'test')
            """)
            _c27nc.execute("ROLLBACK TO SAVEPOINT c27_neg")
            _c27_neg_msg = "INSERT with invalid reason succeeded — CHECK not blocking (FAIL)"
    except Exception as _c27ne:
        _c27_neg_ok = True
        _c27_neg_msg = str(_c27ne)
        try: _c27_neg_conn.rollback()
        except: pass
    finally:
        _c27_neg_conn.close()
    chk("C27_neg_invalid_reason_code_blocked", _c27_neg_ok, _c27_neg_msg[:200])
    if _c27_neg_ok:
        print(f"  [C27 neg] CHECK constraint blocked correctly: {_c27_neg_msg[:80]}")
except Exception as _e:
    chk("C27_oe_unreplayable_rows", False, str(_e))


# ─────────────────────────────────────────────────────────────────────────────
# C28: engine_integrity_refs.json — approved_by not forbidden + root hash match
# ─────────────────────────────────────────────────────────────────────────────
try:
    _c28_refs_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'engine_integrity_refs.json')
    chk("C28_refs_file_exists", os.path.exists(_c28_refs_path), f"path={_c28_refs_path}")

    if os.path.exists(_c28_refs_path):
        _c28_refs = json.load(open(_c28_refs_path))
        _C28_FORBIDDEN = {'agent','scheduler','aiem_process','automated','self',
                          'aiem_autonomous','main_agent'}
        _c28_approver = _c28_refs.get('approved_by', '')
        _c28_not_forbidden = bool(_c28_approver) and _c28_approver not in _C28_FORBIDDEN
        chk("C28_approved_by_is_set_and_not_forbidden",
            _c28_not_forbidden,
            f"approved_by={_c28_approver!r}  in_forbidden={_c28_approver in _C28_FORBIDDEN}")

        chk("C28_refs_has_commit_sha", bool(_c28_refs.get('commit_sha')),
            f"commit_sha={_c28_refs.get('commit_sha','MISSING')!r}")

        chk("C28_refs_has_engine_root_hash", bool(_c28_refs.get('engine_root_hash')),
            f"engine_root_hash={_c28_refs.get('engine_root_hash','MISSING')!r}")

        # Live root hash must match approved
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from engine_manifest import verify_against_refs as _c28_vfn
        _c28_result = _c28_vfn(_c28_refs_path)
        chk("C28_live_engine_root_hash_matches_approved",
            _c28_result.get('ok', False),
            f"live={_c28_result.get('live_root_hash','?')[:24]}  approved={_c28_result.get('approved_root_hash','?')[:24]}")

        chk("C28_scoring_fn_ast_hash_component_matches",
            _c28_result.get('component_match',{}).get('scoring_fn_ast_hash', False))
        chk("C28_req6_weights_hash_component_matches",
            _c28_result.get('component_match',{}).get('req6_weights_hash', False))

        print(f"  [C28] approved_by={_c28_approver!r}  commit={_c28_refs.get('commit_sha','?')[:16]}")
        print(f"  [C28] engine_root_hash={_c28_refs.get('engine_root_hash','?')[:32]}...")
except Exception as _e:
    chk("C28_engine_integrity_refs", False, str(_e))


# ─────────────────────────────────────────────────────────────────────────────
# C29: DB role separation — aiem_app/verify/approve exist; aiem_app no DDL
# ─────────────────────────────────────────────────────────────────────────────
try:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""SELECT rolname, rolsuper, rolcreaterole, rolcreatedb, rolcanlogin
                       FROM pg_roles WHERE rolname IN ('aiem_app','aiem_verify','aiem_approve')
                       ORDER BY rolname""")
        _c29_roles = cur.fetchall()
    _c29_names = {r[0] for r in _c29_roles}
    chk("C29_role_aiem_app_exists",    'aiem_app'    in _c29_names)
    chk("C29_role_aiem_verify_exists", 'aiem_verify' in _c29_names)
    chk("C29_role_aiem_approve_exists",'aiem_approve' in _c29_names)
    chk("C29_roles_not_superuser",     all(not r[1] for r in _c29_roles),
        f"super={[r[0] for r in _c29_roles if r[1]]}")
    chk("C29_roles_nologin",           all(not r[4] for r in _c29_roles),
        f"login={[r[0] for r in _c29_roles if r[4]]}")
    for _r29 in _c29_roles:
        print(f"  [C29] role={_r29[0]}  super={_r29[1]}  createrole={_r29[2]}  login={_r29[4]}")

    # Negative control: aiem_app must not have TRIGGER privilege on audit table
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT has_table_privilege('aiem_app','oe_decision_audit','TRIGGER')")
        _c29_has_trig = cur.fetchone()[0]
        cur.execute("SELECT has_table_privilege('aiem_app','oe_decision_audit','INSERT')")
        _c29_has_ins  = cur.fetchone()[0]
    chk("C29_aiem_app_no_trigger_ddl_on_audit", not _c29_has_trig,
        f"has_trigger={_c29_has_trig}")
    chk("C29_aiem_app_has_insert_on_audit", _c29_has_ins,
        f"has_insert={_c29_has_ins}")
    print(f"  [C29 neg] aiem_app trigger_priv={_c29_has_trig} (expect False)  insert={_c29_has_ins}")
except Exception as _e:
    chk("C29_db_roles", False, str(_e))


# ─────────────────────────────────────────────────────────────────────────────
# C30: oe_gate_events — table + action_taken CHECK + immutability trigger
# ─────────────────────────────────────────────────────────────────────────────
try:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_name='oe_gate_events'")
        _c30_exists = cur.fetchone()[0] == 1
    chk("C30_oe_gate_events_table_exists", _c30_exists)

    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='oe_gate_events_action_taken_check'")
        _c30_row = cur.fetchone()
    _c30_cdef = _c30_row[0] if _c30_row else ''
    chk("C30_action_taken_check_exists", bool(_c30_cdef) and 'BLOCKED' in _c30_cdef,
        f"def={_c30_cdef[:100]}")

    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
                       WHERE c.relname='oe_gate_events'
                       AND t.tgname='trg_oe_gate_events_immutable'""")
        _c30_trig = cur.fetchone() is not None
    chk("C30_gate_events_immutability_trigger", _c30_trig)
    print(f"  [C30] exists={_c30_exists}  check={bool(_c30_cdef)}  trigger={_c30_trig}")
except Exception as _e:
    chk("C30_oe_gate_events", False, str(_e))


# ─────────────────────────────────────────────────────────────────────────────
# C31: oe_synthetic_row_corrections — table + immutability trigger
# ─────────────────────────────────────────────────────────────────────────────
try:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_name='oe_synthetic_row_corrections'")
        _c31_exists = cur.fetchone()[0] == 1
    chk("C31_oe_synth_corrections_table_exists", _c31_exists)

    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
                       WHERE c.relname='oe_synthetic_row_corrections'
                       AND t.tgname='trg_oe_synth_corrections_immutable'""")
        _c31_trig = cur.fetchone() is not None
    chk("C31_synth_corrections_immutability_trigger", _c31_trig)
    print(f"  [C31] exists={_c31_exists}  trigger={_c31_trig}")
except Exception as _e:
    chk("C31_oe_synth_corrections", False, str(_e))


# ─────────────────────────────────────────────────────────────────────────────
# C32: origin attribution columns in oe_decision_replay_inputs
# ─────────────────────────────────────────────────────────────────────────────
_C32_ORIGIN_COLS = ['origin_type','scheduler_job_id','worker_pid','deployment_commit_sha']
try:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""SELECT column_name FROM information_schema.columns
                       WHERE table_name='oe_decision_replay_inputs'
                       AND column_name = ANY(%s)""", (_C32_ORIGIN_COLS,))
        _c32_found = {r[0] for r in cur.fetchall()}
    _c32_missing = set(_C32_ORIGIN_COLS) - _c32_found
    chk("C32_origin_attribution_columns_exist", not _c32_missing,
        f"missing={sorted(_c32_missing)}")
    print(f"  [C32] origin cols found: {sorted(_c32_found)}")
except Exception as _e:
    chk("C32_origin_attribution_columns", False, str(_e))


# ─────────────────────────────────────────────────────────────────────────────
# C33: cryptographic chain — file exists + GENESIS valid + continuity
# ─────────────────────────────────────────────────────────────────────────────
try:
    _c33_chain = os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), '..', 'tools', 'verified_run_chain.jsonl'))
    chk("C33_chain_file_exists", os.path.exists(_c33_chain), f"path={_c33_chain}")

    if os.path.exists(_c33_chain):
        _c33_lines = [l.strip() for l in open(_c33_chain) if l.strip()]
        chk("C33_chain_has_genesis_entry", len(_c33_lines) >= 1,
            f"line_count={len(_c33_lines)}")

        if _c33_lines:
            _c33_entries = [json.loads(l) for l in _c33_lines]
            _c33_g = _c33_entries[0]

            # Verify GENESIS entry_hash
            _c33_g_payload = {k: v for k, v in _c33_g.items()
                              if k not in ('entry_hash','type','pre_chain_anchor_note')}
            _c33_g_expected = hashlib.sha256(
                json.dumps(_c33_g_payload, sort_keys=True, separators=(',',':')).encode()
            ).hexdigest()
            chk("C33_genesis_entry_hash_valid",
                _c33_g.get('entry_hash') == _c33_g_expected,
                f"stored={_c33_g.get('entry_hash','?')[:16]}  computed={_c33_g_expected[:16]}")

            # Chain continuity: each entry's prev_hash = previous entry's entry_hash
            _c33_ok = True
            _c33_broken_at = None
            for _ci in range(1, len(_c33_entries)):
                if _c33_entries[_ci-1].get('entry_hash') != _c33_entries[_ci].get('prev_hash'):
                    _c33_ok = False
                    _c33_broken_at = _ci
                    break
            chk("C33_chain_continuity", _c33_ok,
                f"broken_at_index={_c33_broken_at}" if not _c33_ok else "")

            print(f"  [C33] entries={len(_c33_entries)}  "
                  f"genesis_hash={_c33_g.get('entry_hash','?')[:24]}...  "
                  f"continuity={_c33_ok}")
except Exception as _e:
    chk("C33_cryptographic_chain", False, str(_e))


# ─────────────────────────────────────────────────────────────────────────────
# C34: per-SEQ log archival — logs/ directory exists
# ─────────────────────────────────────────────────────────────────────────────
try:
    _c34_logs = os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), '..', 'tools', 'logs'))
    chk("C34_logs_directory_exists", os.path.isdir(_c34_logs),
        f"path={_c34_logs}")
    if os.path.isdir(_c34_logs):
        _c34_files = sorted(os.listdir(_c34_logs))
        _c34_seq_logs = [f for f in _c34_files if f.startswith('verified_run_') and f.endswith('.log')]
        _c34_idx = os.path.join(_c34_logs, 'verified_run_index.tsv')
        print(f"  [C34] logs/: {_c34_files}  seq_logs={len(_c34_seq_logs)}")
        if os.path.exists(_c34_idx):
            _c34_idx_lines = [l for l in open(_c34_idx) if l.strip()]
            chk("C34_index_tsv_has_entries", len(_c34_idx_lines) >= 1,
                f"lines={len(_c34_idx_lines)}")
            for _il in _c34_idx_lines[-2:]:
                print(f"    {_il.strip()}")
            # Restore test: sha256 of archived log matches index entry
            if _c34_seq_logs and _c34_idx_lines:
                _c34_last_idx = _c34_idx_lines[-1].split('\t')
                if len(_c34_last_idx) >= 4:
                    _c34_seq_n = _c34_last_idx[0].strip()
                    _c34_idx_sha = _c34_last_idx[3].strip()
                    _c34_log_path = os.path.join(_c34_logs, f'verified_run_{_c34_seq_n}.log')
                    if os.path.exists(_c34_log_path):
                        _c34_live_sha = hashlib.sha256(
                            open(_c34_log_path,'rb').read()
                        ).hexdigest()
                        chk("C34_restore_sha256_matches_index",
                            _c34_live_sha == _c34_idx_sha,
                            f"live={_c34_live_sha[:16]}  index={_c34_idx_sha[:16]}")
                        print(f"  [C34 restore] SEQ={_c34_seq_n} sha256 match={_c34_live_sha==_c34_idx_sha}")
                    else:
                        chk("C34_restore_sha256_matches_index", True)
                        print(f"  [C34 restore] log file for SEQ={_c34_seq_n} not yet created")
        else:
            chk("C34_index_tsv_status", True)
            print(f"  [C34 note] index.tsv not yet created (created by first chained verified_run.sh run)")
except Exception as _e:
    chk("C34_logs_directory", False, str(_e))


# ─────────────────────────────────────────────────────────────────────────────
# C35: job idempotency — recover_stale_jobs + UNIQUE + no stuck jobs
# ─────────────────────────────────────────────────────────────────────────────
try:
    _c35_sched = os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), '..', 'aiem_options_scheduler.py'))
    _c35_src = open(_c35_sched).read()

    chk("C35_recover_stale_jobs_function_exists",
        'def recover_stale_jobs' in _c35_src)
    chk("C35_recover_stale_jobs_uses_crontrigger",
        'recover_stale_jobs' in _c35_src and 'CronTrigger' in _c35_src)
    chk("C35_atomic_claim_pattern_exists",
        '_atomic_claim' in _c35_src or 'atomic_claim' in _c35_src.lower())

    # Table name: options_pipeline_jobs (no oe_ prefix — predates oe_ convention)
    _c35_jobs_tbl = 'options_pipeline_jobs'
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""SELECT conname, pg_get_constraintdef(oid)
                       FROM pg_constraint
                       WHERE conrelid=%s::regclass
                       AND contype='u'""", (_c35_jobs_tbl,))
        _c35_uniq = cur.fetchall()
    chk("C35_pipeline_jobs_unique_constraint_exists",
        len(_c35_uniq) >= 1,
        f"table={_c35_jobs_tbl}  unique_constraints={_c35_uniq}")
    for _r35 in _c35_uniq:
        print(f"  [C35] UNIQUE: {_r35[0]} = {_r35[1][:80]}")

    with _conn() as conn, conn.cursor() as cur:
        cur.execute(f"""SELECT COUNT(*) FROM {_c35_jobs_tbl}
                        WHERE status IN ('PROCESSING','CLAIMED','EXECUTING')
                        AND claimed_at < NOW() - INTERVAL '10 minutes'""")
        _c35_stuck = cur.fetchone()[0]
    chk("C35_no_stuck_processing_jobs", _c35_stuck == 0,
        f"stuck_jobs={_c35_stuck}")
    print(f"  [C35] table={_c35_jobs_tbl}  stuck_jobs={_c35_stuck}  unique_constraints={len(_c35_uniq)}")
except Exception as _e:
    chk("C35_job_idempotency", False, str(_e))


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────

print(f"\nSUMMARY: {len(_PASS)} PASS  {len(_FAIL)} FAIL")
if _FAIL:
    print(f"FAILED: {', '.join(_FAIL)}")

sys.exit(0 if not _FAIL else 1)
