#!/usr/bin/env python3
"""
exercise_replay_branches.py — R7 branch exercise harness
Exercises all 6 branches of the DPL Phase 3 replay check:
  Branch 1 (T1/NT1): full_match=False       -> log.critical + _tg() [STUBBED]
  Branch 2 (T2/NT2): ReplayCodeDriftError   -> log.critical + _tg() [STUBBED]
                                               + UPDATE oe_decision_audit (CODE_DRIFT or WEIGHTS_DRIFT) [REAL DB]
  Branch 3 (T3/NT3): generic Exception      -> log.critical + _tg() [STUBBED]
                                               + UPDATE oe_decision_audit (REPLAY_ERROR) [REAL DB]
  x2 sites: TRADE (~line 1961) and NO_TRADE (~line 1729)

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

# ── Setup: create is_test_record=TRUE row for drift/error UPDATE tests ────────
bootstrap_dpl_phase3(_DB_URL)
_aud = write_decision(
    input_data ={"ticker": "BRTEST", "call_score": 55.0, "put_score": 45.0},
    output_data={"direction": "LONG_CALL", "trace_id": "branch_exercise_r7"},
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

# ─ shared T3/NT3 body — mirrors updated scheduler exception handler ───────────

def _t3_body(log, _dpl_trade_result, _re):
    _re_msg = (
        f"[DPL REPLAY_ERROR] TRADE "
        f"decision_id={_dpl_trade_result['decision_id'][:16]}: {_re}"
    )
    log.critical(_re_msg)
    _tg(_re_msg)
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=4) as _dc_re, \
             _dc_re.cursor() as _du_re:
            _du_re.execute(
                "UPDATE oe_decision_audit "
                "SET verification_status='REPLAY_ERROR' "
                "WHERE decision_id=%s",
                (_dpl_trade_result["decision_id"],)
            )
    except Exception as _dbu_re:
        log.warning(f"[dpl] REPLAY_ERROR status update failed: {_dbu_re}")

def _nt3_body(log, _dpl_nt_result, _re_nt):
    _re_msg_nt = (
        f"[DPL REPLAY_ERROR] NO_TRADE "
        f"decision_id={_dpl_nt_result['decision_id'][:16]}: {_re_nt}"
    )
    log.critical(_re_msg_nt)
    _tg(_re_msg_nt)
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=4) as _dc_re_nt, \
             _dc_re_nt.cursor() as _du_re_nt:
            _du_re_nt.execute(
                "UPDATE oe_decision_audit "
                "SET verification_status='REPLAY_ERROR' "
                "WHERE decision_id=%s",
                (_dpl_nt_result["decision_id"],)
            )
    except Exception as _dbu_re_nt:
        log.warning(f"[dpl] REPLAY_ERROR status update failed: {_dbu_re_nt}")

# ──────────────────────────────────────────────────────────────────────────────
# TRADE site
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
        _dm = f"[DPL CODE_DRIFT] TRADE decision_id={_dpl_trade_result['decision_id'][:16]}: {_rce}"
        log.critical(_dm); _tg(_dm)
        try:
            with psycopg2.connect(_DB_URL, connect_timeout=4) as _dc, _dc.cursor() as _du:
                _vs_trade = "WEIGHTS_DRIFT" if "WEIGHTS_DRIFT" in str(_rce) else "CODE_DRIFT"
                _du.execute("UPDATE oe_decision_audit SET verification_status=%s WHERE decision_id=%s",
                            (_vs_trade, _dpl_trade_result["decision_id"],))
        except Exception as _dbu:
            log.warning(f"[dpl] drift status update failed: {_dbu}")
    except Exception as _re:
        _t3_body(log, _dpl_trade_result, _re)
    finally:
        _dpl.replay_decision = orig

def trade_T2_drift(log, _dpl_trade_result):
    """Branch T2: ReplayCodeDriftError -> log.critical + _tg() + UPDATE CODE_DRIFT/WEIGHTS_DRIFT"""
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
            log.critical(f"[DPL MISMATCH] TRADE ..."); _tg("...")
    except _dpl.ReplayCodeDriftError as _rce:
        _dm = (
            f"[DPL CODE_DRIFT] TRADE "
            f"decision_id={_dpl_trade_result['decision_id'][:16]}: {_rce}"
        )
        log.critical(_dm); _tg(_dm)
        try:
            with psycopg2.connect(_DB_URL, connect_timeout=4) as _dc, _dc.cursor() as _du:
                _vs_trade = "WEIGHTS_DRIFT" if "WEIGHTS_DRIFT" in str(_rce) else "CODE_DRIFT"
                _du.execute("UPDATE oe_decision_audit SET verification_status=%s WHERE decision_id=%s",
                            (_vs_trade, _dpl_trade_result["decision_id"],))
        except Exception as _dbu:
            log.warning(f"[dpl] drift status update failed: {_dbu}")
    except Exception as _re:
        _t3_body(log, _dpl_trade_result, _re)
    finally:
        _dpl.replay_decision = orig

def trade_T3_generic(log, _dpl_trade_result):
    """Branch T3: generic Exception -> log.critical + _tg() + UPDATE REPLAY_ERROR"""
    orig = _dpl.replay_decision
    _dpl.replay_decision = lambda *a, **kw: (_ for _ in ()).throw(
        RuntimeError("simulated DB timeout")
    )
    try:
        _rpl = _dpl.replay_decision(_dpl_trade_result["decision_id"])
        if not _rpl["full_match"]:
            log.critical("..."); _tg("...")
    except _dpl.ReplayCodeDriftError as _rce:
        log.critical("..."); _tg("...")
    except Exception as _re:
        _t3_body(log, _dpl_trade_result, _re)
    finally:
        _dpl.replay_decision = orig

run_branch("TRADE", "T1", "full_match=False → log.critical + _tg()",                         trade_T1_mismatch)
run_branch("TRADE", "T2", "ReplayCodeDriftError → log.critical + _tg() + UPDATE CODE_DRIFT", trade_T2_drift)
run_branch("TRADE", "T3", "generic Exception → log.critical + _tg() + UPDATE REPLAY_ERROR",  trade_T3_generic)

# ──────────────────────────────────────────────────────────────────────────────
# NO_TRADE site
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
        _dm_nt = f"[DPL CODE_DRIFT] NO_TRADE decision_id={_dpl_nt_result['decision_id'][:16]}: {_rce_nt}"
        log.critical(_dm_nt); _tg(_dm_nt)
        try:
            with psycopg2.connect(_DB_URL, connect_timeout=4) as _dc_nt, _dc_nt.cursor() as _du_nt:
                _vs_nt = "WEIGHTS_DRIFT" if "WEIGHTS_DRIFT" in str(_rce_nt) else "CODE_DRIFT"
                _du_nt.execute("UPDATE oe_decision_audit SET verification_status=%s WHERE decision_id=%s",
                               (_vs_nt, _dpl_nt_result["decision_id"],))
        except Exception as _dbu_nt:
            log.warning(f"[dpl] drift status update failed: {_dbu_nt}")
    except Exception as _re_nt:
        _nt3_body(log, _dpl_nt_result, _re_nt)
    finally:
        _dpl.replay_decision = orig

def nt_NT2_drift(log, _dpl_nt_result):
    """Branch NT2: ReplayCodeDriftError -> log.critical + _tg() + UPDATE CODE_DRIFT/WEIGHTS_DRIFT"""
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
            log.critical("..."); _tg("...")
    except _dpl.ReplayCodeDriftError as _rce_nt:
        _dm_nt = (
            f"[DPL CODE_DRIFT] NO_TRADE "
            f"decision_id={_dpl_nt_result['decision_id'][:16]}: {_rce_nt}"
        )
        log.critical(_dm_nt); _tg(_dm_nt)
        try:
            with psycopg2.connect(_DB_URL, connect_timeout=4) as _dc_nt, _dc_nt.cursor() as _du_nt:
                _vs_nt = "WEIGHTS_DRIFT" if "WEIGHTS_DRIFT" in str(_rce_nt) else "CODE_DRIFT"
                _du_nt.execute("UPDATE oe_decision_audit SET verification_status=%s WHERE decision_id=%s",
                               (_vs_nt, _dpl_nt_result["decision_id"],))
        except Exception as _dbu_nt:
            log.warning(f"[dpl] drift status update failed: {_dbu_nt}")
    except Exception as _re_nt:
        _nt3_body(log, _dpl_nt_result, _re_nt)
    finally:
        _dpl.replay_decision = orig

def nt_NT3_generic(log, _dpl_nt_result):
    """Branch NT3: generic Exception -> log.critical + _tg() + UPDATE REPLAY_ERROR"""
    orig = _dpl.replay_decision
    _dpl.replay_decision = lambda *a, **kw: (_ for _ in ()).throw(
        RuntimeError("simulated network error")
    )
    try:
        _rpl_nt = _dpl.replay_decision(_dpl_nt_result["decision_id"])
        if not _rpl_nt["full_match"]:
            log.critical("..."); _tg("...")
    except _dpl.ReplayCodeDriftError as _rce_nt:
        log.critical("..."); _tg("...")
    except Exception as _re_nt:
        _nt3_body(log, _dpl_nt_result, _re_nt)
    finally:
        _dpl.replay_decision = orig

run_branch("NO_TRADE", "NT1", "full_match=False → log.critical + _tg()",                          nt_NT1_mismatch)
run_branch("NO_TRADE", "NT2", "ReplayCodeDriftError → log.critical + _tg() + UPDATE CODE_DRIFT",  nt_NT2_drift)
run_branch("NO_TRADE", "NT3", "generic Exception → log.critical + _tg() + UPDATE REPLAY_ERROR",   nt_NT3_generic)

# ── DB verification: last status after all 6 branches ────────────────────────
# Execution order: T1 (no DB write) → T2 (CODE_DRIFT) → T3 (REPLAY_ERROR) →
#                  NT1 (no write)   → NT2 (CODE_DRIFT) → NT3 (REPLAY_ERROR)
# Final status = REPLAY_ERROR (NT3 runs last)
print("--- DB verification: verification_status after all 6 branches ---")
with psycopg2.connect(_DB_URL) as c, c.cursor() as cur:
    cur.execute("SELECT verification_status FROM oe_decision_audit WHERE decision_id=%s",
                (_TEST_DID,))
    row = cur.fetchone()
    print(f"  verification_status = {row[0] if row else 'NO ROW'}")
print("\nDONE")
