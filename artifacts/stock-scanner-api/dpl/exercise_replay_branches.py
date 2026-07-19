#!/usr/bin/env python3
"""
exercise_replay_branches.py — R6.1 branch exercise harness
Exercises all 6 branches of the DPL Phase 3 replay check:
  Branch 1: full_match=False       -> log.critical + _tg() [STUBBED]
  Branch 2: ReplayCodeDriftError   -> log.critical + _tg() [STUBBED] + UPDATE oe_decision_audit [REAL DB]
  Branch 3: generic Exception      -> log.warning
  x2 sites: TRADE (~line 1957) and NO_TRADE (~line 1729)

_test_mode: only is_test_record=TRUE rows used. No production rows touched.
_tg(): STUBBED — records calls, does NOT send Telegram.
"""
import sys, os, logging, io
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import psycopg2
import aiem_options_dpl as _dpl
from aiem_options_dpl import write_decision, bootstrap_dpl_phase3

_DB_URL = os.environ['DATABASE_URL']

# ── _tg stub: records calls, does NOT send Telegram ──────────────────────────
_tg_calls = []
def _tg(text: str) -> bool:
    _tg_calls.append(text)
    return True  # STUBBED

def _make_log():
    logger = logging.getLogger(f'branch_ex_{id(object())}')
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(logging.Formatter('%(levelname)s %(message)s'))
    logger.addHandler(h)
    return logger, buf

# ── Setup: create is_test_record=TRUE row for CODE_DRIFT UPDATE test ─────────
bootstrap_dpl_phase3(_DB_URL)
_aud = write_decision(
    input_data ={"ticker": "BRTEST", "call_score": 55.0, "put_score": 45.0},
    output_data={"direction": "LONG_CALL", "trace_id": "branch_exercise_r6"},
    is_test_record=True,
    db_url=_DB_URL,
)
_TEST_DID = _aud["decision_id"]
print(f"[setup] test decision_id={_TEST_DID[:20]}  is_test_record=True\n")

def run_branch(site, tag, desc, fn):
    global _tg_calls
    _tg_calls = []
    log, buf = _make_log()
    print(f"{'='*68}")
    print(f"SITE={site}  branch={tag}  {desc}")
    print(f"{'='*68}")
    fn(log, {"decision_id": _TEST_DID})
    out = buf.getvalue().strip()
    if out:
        for line in out.splitlines():
            print(f"  LOG: {line}")
    else:
        print("  LOG: (empty)")
    if _tg_calls:
        print(f"  _tg: STUBBED — fired {len(_tg_calls)}x; msg[:100]={_tg_calls[0][:100]!r}")
    else:
        print("  _tg: not called")
    print()

# ──────────────────────────────────────────────────────────────────────────────
# TRADE site (~line 1957 in aiem_options_scheduler.py)
# Exact code from scheduler, variable names mapped to test context.
# ──────────────────────────────────────────────────────────────────────────────

def trade_T1_mismatch(log, _dpl_trade_result):
    """Branch T1: full_match=False -> log.critical + _tg()"""
    orig = _dpl.replay_decision
    _dpl.replay_decision = lambda *a, **kw: {
        "full_match": False, "call_match": False,
        "put_match": True,   "direction_match": True}
    try:
        _rpl = _dpl.replay_decision(_dpl_trade_result["decision_id"])
        if not _rpl["full_match"]:
            _mm = (
                f"[DPL MISMATCH] TRADE "
                f"decision_id={_dpl_trade_result['decision_id'][:16]} "
                f"call_match={_rpl['call_match']} "
                f"put_match={_rpl['put_match']} "
                f"dir_match={_rpl['direction_match']}"
            )
            log.critical(_mm)
            _tg(_mm)
    except _dpl.ReplayCodeDriftError as _rce:
        _dm = (
            f"[DPL CODE_DRIFT] TRADE "
            f"decision_id={_dpl_trade_result['decision_id'][:16]}: {_rce}"
        )
        log.critical(_dm); _tg(_dm)
        try:
            with psycopg2.connect(_DB_URL, connect_timeout=4) as _dc, _dc.cursor() as _du:
                _du.execute("UPDATE oe_decision_audit SET verification_status='CODE_DRIFT'"
                            " WHERE decision_id=%s", (_dpl_trade_result["decision_id"],))
        except Exception as _dbu:
            log.warning(f"[dpl] CODE_DRIFT status update failed: {_dbu}")
    except Exception as _re:
        log.warning(f"[dpl] replay check TRADE failed: {_re}")
    finally:
        _dpl.replay_decision = orig

def trade_T2_drift(log, _dpl_trade_result):
    """Branch T2: ReplayCodeDriftError -> log.critical + _tg() + UPDATE oe_decision_audit"""
    orig = _dpl.replay_decision
    _dpl.replay_decision = lambda *a, **kw: (_ for _ in ()).throw(
        _dpl.ReplayCodeDriftError(
            "[Phase 3] CODE_DRIFT detected for decision_id='test'. "
            "stored combined_hash='aabbccdd' live combined_hash='eeff0011'"
        )
    )
    try:
        _rpl = _dpl.replay_decision(_dpl_trade_result["decision_id"])
        if not _rpl["full_match"]:
            _mm = f"[DPL MISMATCH] TRADE ..."
            log.critical(_mm); _tg(_mm)
    except _dpl.ReplayCodeDriftError as _rce:
        _dm = (
            f"[DPL CODE_DRIFT] TRADE "
            f"decision_id={_dpl_trade_result['decision_id'][:16]}: {_rce}"
        )
        log.critical(_dm); _tg(_dm)
        try:
            with psycopg2.connect(_DB_URL, connect_timeout=4) as _dc, _dc.cursor() as _du:
                _du.execute("UPDATE oe_decision_audit SET verification_status='CODE_DRIFT'"
                            " WHERE decision_id=%s", (_dpl_trade_result["decision_id"],))
        except Exception as _dbu:
            log.warning(f"[dpl] CODE_DRIFT status update failed: {_dbu}")
    except Exception as _re:
        log.warning(f"[dpl] replay check TRADE failed: {_re}")
    finally:
        _dpl.replay_decision = orig

def trade_T3_generic(log, _dpl_trade_result):
    """Branch T3: generic Exception -> log.warning"""
    orig = _dpl.replay_decision
    _dpl.replay_decision = lambda *a, **kw: (_ for _ in ()).throw(
        RuntimeError("simulated DB timeout")
    )
    try:
        _rpl = _dpl.replay_decision(_dpl_trade_result["decision_id"])
        if not _rpl["full_match"]:
            _mm = "..."
            log.critical(_mm); _tg(_mm)
    except _dpl.ReplayCodeDriftError as _rce:
        _dm = "..."
        log.critical(_dm); _tg(_dm)
    except Exception as _re:
        log.warning(f"[dpl] replay check TRADE failed: {_re}")
    finally:
        _dpl.replay_decision = orig

run_branch("TRADE", "T1", "full_match=False → log.critical + _tg()",       trade_T1_mismatch)
run_branch("TRADE", "T2", "ReplayCodeDriftError → log.critical + _tg() + UPDATE", trade_T2_drift)
run_branch("TRADE", "T3", "generic Exception → log.warning",               trade_T3_generic)

# ──────────────────────────────────────────────────────────────────────────────
# NO_TRADE site (~line 1729 in aiem_options_scheduler.py)
# ──────────────────────────────────────────────────────────────────────────────

def nt_NT1_mismatch(log, _dpl_nt_result):
    """Branch NT1: full_match=False -> log.critical + _tg()"""
    orig = _dpl.replay_decision
    _dpl.replay_decision = lambda *a, **kw: {
        "full_match": False, "call_match": True,
        "put_match": False,  "direction_match": True}
    try:
        _rpl_nt = _dpl.replay_decision(_dpl_nt_result["decision_id"])
        if not _rpl_nt["full_match"]:
            _mm_nt = (
                f"[DPL MISMATCH] NO_TRADE "
                f"decision_id={_dpl_nt_result['decision_id'][:16]} "
                f"call_match={_rpl_nt['call_match']} "
                f"put_match={_rpl_nt['put_match']} "
                f"dir_match={_rpl_nt['direction_match']}"
            )
            log.critical(_mm_nt); _tg(_mm_nt)
    except _dpl.ReplayCodeDriftError as _rce_nt:
        _dm_nt = (
            f"[DPL CODE_DRIFT] NO_TRADE "
            f"decision_id={_dpl_nt_result['decision_id'][:16]}: {_rce_nt}"
        )
        log.critical(_dm_nt); _tg(_dm_nt)
        try:
            with psycopg2.connect(_DB_URL, connect_timeout=4) as _dc_nt, \
                 _dc_nt.cursor() as _du_nt:
                _du_nt.execute("UPDATE oe_decision_audit SET verification_status='CODE_DRIFT'"
                               " WHERE decision_id=%s", (_dpl_nt_result["decision_id"],))
        except Exception as _dbu_nt:
            log.warning(f"[dpl] CODE_DRIFT status update failed: {_dbu_nt}")
    except Exception as _re_nt:
        log.warning(f"[dpl] replay check NO_TRADE failed: {_re_nt}")
    finally:
        _dpl.replay_decision = orig

def nt_NT2_drift(log, _dpl_nt_result):
    """Branch NT2: ReplayCodeDriftError -> log.critical + _tg() + UPDATE"""
    orig = _dpl.replay_decision
    _dpl.replay_decision = lambda *a, **kw: (_ for _ in ()).throw(
        _dpl.ReplayCodeDriftError(
            "[Phase 3] CODE_DRIFT detected for decision_id='test'. "
            "stored combined_hash='aabbccdd' live combined_hash='eeff0011'"
        )
    )
    try:
        _rpl_nt = _dpl.replay_decision(_dpl_nt_result["decision_id"])
        if not _rpl_nt["full_match"]:
            _mm_nt = f"[DPL MISMATCH] NO_TRADE ..."
            log.critical(_mm_nt); _tg(_mm_nt)
    except _dpl.ReplayCodeDriftError as _rce_nt:
        _dm_nt = (
            f"[DPL CODE_DRIFT] NO_TRADE "
            f"decision_id={_dpl_nt_result['decision_id'][:16]}: {_rce_nt}"
        )
        log.critical(_dm_nt); _tg(_dm_nt)
        try:
            with psycopg2.connect(_DB_URL, connect_timeout=4) as _dc_nt, \
                 _dc_nt.cursor() as _du_nt:
                _du_nt.execute("UPDATE oe_decision_audit SET verification_status='CODE_DRIFT'"
                               " WHERE decision_id=%s", (_dpl_nt_result["decision_id"],))
        except Exception as _dbu_nt:
            log.warning(f"[dpl] CODE_DRIFT status update failed: {_dbu_nt}")
    except Exception as _re_nt:
        log.warning(f"[dpl] replay check NO_TRADE failed: {_re_nt}")
    finally:
        _dpl.replay_decision = orig

def nt_NT3_generic(log, _dpl_nt_result):
    """Branch NT3: generic Exception -> log.warning"""
    orig = _dpl.replay_decision
    _dpl.replay_decision = lambda *a, **kw: (_ for _ in ()).throw(
        RuntimeError("simulated network error")
    )
    try:
        _rpl_nt = _dpl.replay_decision(_dpl_nt_result["decision_id"])
        if not _rpl_nt["full_match"]:
            _mm_nt = "..."
            log.critical(_mm_nt); _tg(_mm_nt)
    except _dpl.ReplayCodeDriftError as _rce_nt:
        _dm_nt = "..."
        log.critical(_dm_nt); _tg(_dm_nt)
    except Exception as _re_nt:
        log.warning(f"[dpl] replay check NO_TRADE failed: {_re_nt}")
    finally:
        _dpl.replay_decision = orig

run_branch("NO_TRADE", "NT1", "full_match=False → log.critical + _tg()",         nt_NT1_mismatch)
run_branch("NO_TRADE", "NT2", "ReplayCodeDriftError → log.critical + _tg() + UPDATE", nt_NT2_drift)
run_branch("NO_TRADE", "NT3", "generic Exception → log.warning",                 nt_NT3_generic)

# ── Verify T2+NT2 CODE_DRIFT UPDATE both committed to DB ─────────────────────
print("--- DB verification: verification_status after T2 and NT2 ---")
with psycopg2.connect(_DB_URL) as c, c.cursor() as cur:
    cur.execute("SELECT verification_status FROM oe_decision_audit WHERE decision_id=%s",
                (_TEST_DID,))
    row = cur.fetchone()
    print(f"  verification_status = {row[0] if row else 'NO ROW'}")
print("\nDONE")
