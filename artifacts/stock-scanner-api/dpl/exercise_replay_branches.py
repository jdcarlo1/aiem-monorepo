#!/usr/bin/env python3
"""
exercise_replay_branches.py — R8.4 branch exercise harness
6 distinct decision_ids (one per branch). verification_status read immediately
after each branch that writes to DB.

  Branch T1/NT1: full_match=False       -> log.critical + _tg() [STUBBED]; no DB write
  Branch T2/NT2: ReplayCodeDriftError   -> log.critical + _tg() [STUBBED]
                                           + UPDATE CODE_DRIFT/WEIGHTS_DRIFT [REAL DB]
  Branch T3/NT3: generic Exception      -> log.critical + _tg() [STUBBED]
                                           + UPDATE REPLAY_ERROR [REAL DB]

_tg(): STUBBED — records calls, does NOT send Telegram.
For real _tg proof see exercise_real_tg.py (R8.3).
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
    return True  # STUBBED — see exercise_real_tg.py for live proof

def _make_log():
    logger = logging.getLogger(f'branch_ex_{id(object())}')
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(logging.Formatter('%(levelname)s %(message)s'))
    logger.addHandler(h)
    return logger, buf

# ── Setup: 6 distinct is_test_record=TRUE rows ────────────────────────────────
bootstrap_dpl_phase3(_DB_URL)

def _make_decision(tag):
    return write_decision(
        input_data ={"ticker": f"BR{tag}", "call_score": 55.0, "put_score": 45.0},
        output_data={"direction": "LONG_CALL", "trace_id": f"branch_exercise_r8_{tag}"},
        is_test_record=True,
        db_url=_DB_URL,
    )

_did = {tag: _make_decision(tag)["decision_id"]
        for tag in ("T1", "T2", "T3", "NT1", "NT2", "NT3")}

print("[setup] 6 distinct decision_ids (all is_test_record=True):")
for tag, did in _did.items():
    print(f"  {tag}: {did[:24]}")
print()

def _db_read_status(decision_id):
    with psycopg2.connect(_DB_URL) as c, c.cursor() as cur:
        cur.execute("SELECT verification_status FROM oe_decision_audit WHERE decision_id=%s",
                    (decision_id,))
        row = cur.fetchone()
        return row[0] if row else "NO ROW"

def run_branch(site, tag, desc, fn, did):
    global _tg_calls
    _tg_calls = []
    log, buf = _make_log()
    print(f"{'='*68}")
    print(f"SITE={site}  branch={tag}  {desc}")
    print(f"{'='*68}")
    fn(log, {"decision_id": did})
    out = buf.getvalue().strip()
    for line in out.splitlines():
        print(f"  LOG: {line}")
    if _tg_calls:
        print(f"  _tg: STUBBED — fired {len(_tg_calls)}x; msg[:100]={_tg_calls[0][:100]!r}")
    else:
        print("  _tg: not called")
    status = _db_read_status(did)
    print(f"  DB SELECT verification_status WHERE decision_id={did[:16]}: {status!r}")
    print()

# ─────────────────────────────────────────────────────────────────────────────
# TRADE site
# ─────────────────────────────────────────────────────────────────────────────

def trade_T1_mismatch(log, _dpl_trade_result):
    orig = _dpl.replay_decision
    _dpl.replay_decision = lambda *a, **kw: {
        "full_match": False, "call_match": False,
        "put_match": True,   "direction_match": True}
    try:
        _rpl = _dpl.replay_decision(_dpl_trade_result["decision_id"])
        if not _rpl["full_match"]:
            _mm = (f"[DPL MISMATCH] TRADE decision_id={_dpl_trade_result['decision_id'][:16]} "
                   f"call_match={_rpl['call_match']} put_match={_rpl['put_match']} "
                   f"dir_match={_rpl['direction_match']}")
            log.critical(_mm); _tg(_mm)
    except _dpl.ReplayCodeDriftError as _rce:
        _dm = (f"[DPL CODE_DRIFT] TRADE decision_id={_dpl_trade_result['decision_id'][:16]}: {_rce}")
        log.critical(_dm); _tg(_dm)
        try:
            with psycopg2.connect(_DB_URL, connect_timeout=4) as _dc, _dc.cursor() as _du:
                _vs = "WEIGHTS_DRIFT" if "WEIGHTS_DRIFT" in str(_rce) else "CODE_DRIFT"
                _du.execute("UPDATE oe_decision_audit SET verification_status=%s WHERE decision_id=%s",
                            (_vs, _dpl_trade_result["decision_id"]))
        except Exception as _dbu:
            log.warning(f"[dpl] drift status update failed: {_dbu}")
    except Exception as _re:
        _re_msg = (f"[DPL REPLAY_ERROR] TRADE decision_id={_dpl_trade_result['decision_id'][:16]}: {_re}")
        log.critical(_re_msg); _tg(_re_msg)
        try:
            with psycopg2.connect(_DB_URL, connect_timeout=4) as _dc, _dc.cursor() as _du:
                _du.execute("UPDATE oe_decision_audit SET verification_status='REPLAY_ERROR' WHERE decision_id=%s",
                            (_dpl_trade_result["decision_id"],))
        except Exception as _dbu:
            log.warning(f"[dpl] REPLAY_ERROR status update failed: {_dbu}")
    finally:
        _dpl.replay_decision = orig

def trade_T2_drift(log, _dpl_trade_result):
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
            log.critical("..."); _tg("...")
    except _dpl.ReplayCodeDriftError as _rce:
        _dm = (f"[DPL CODE_DRIFT] TRADE decision_id={_dpl_trade_result['decision_id'][:16]}: {_rce}")
        log.critical(_dm); _tg(_dm)
        try:
            with psycopg2.connect(_DB_URL, connect_timeout=4) as _dc, _dc.cursor() as _du:
                _vs = "WEIGHTS_DRIFT" if "WEIGHTS_DRIFT" in str(_rce) else "CODE_DRIFT"
                _du.execute("UPDATE oe_decision_audit SET verification_status=%s WHERE decision_id=%s",
                            (_vs, _dpl_trade_result["decision_id"]))
        except Exception as _dbu:
            log.warning(f"[dpl] drift status update failed: {_dbu}")
    except Exception as _re:
        _re_msg = (f"[DPL REPLAY_ERROR] TRADE decision_id={_dpl_trade_result['decision_id'][:16]}: {_re}")
        log.critical(_re_msg); _tg(_re_msg)
        try:
            with psycopg2.connect(_DB_URL, connect_timeout=4) as _dc, _dc.cursor() as _du:
                _du.execute("UPDATE oe_decision_audit SET verification_status='REPLAY_ERROR' WHERE decision_id=%s",
                            (_dpl_trade_result["decision_id"],))
        except Exception as _dbu:
            log.warning(f"[dpl] REPLAY_ERROR status update failed: {_dbu}")
    finally:
        _dpl.replay_decision = orig

def trade_T3_generic(log, _dpl_trade_result):
    orig = _dpl.replay_decision
    _dpl.replay_decision = lambda *a, **kw: (_ for _ in ()).throw(
        RuntimeError("simulated DB timeout")
    )
    try:
        _rpl = _dpl.replay_decision(_dpl_trade_result["decision_id"])
        if not _rpl["full_match"]:
            log.critical("..."); _tg("...")
    except _dpl.ReplayCodeDriftError as _rce:
        _dm = (f"[DPL CODE_DRIFT] TRADE decision_id={_dpl_trade_result['decision_id'][:16]}: {_rce}")
        log.critical(_dm); _tg(_dm)
        try:
            with psycopg2.connect(_DB_URL, connect_timeout=4) as _dc, _dc.cursor() as _du:
                _vs = "WEIGHTS_DRIFT" if "WEIGHTS_DRIFT" in str(_rce) else "CODE_DRIFT"
                _du.execute("UPDATE oe_decision_audit SET verification_status=%s WHERE decision_id=%s",
                            (_vs, _dpl_trade_result["decision_id"]))
        except Exception as _dbu:
            log.warning(f"[dpl] drift status update failed: {_dbu}")
    except Exception as _re:
        _re_msg = (f"[DPL REPLAY_ERROR] TRADE decision_id={_dpl_trade_result['decision_id'][:16]}: {_re}")
        log.critical(_re_msg); _tg(_re_msg)
        try:
            with psycopg2.connect(_DB_URL, connect_timeout=4) as _dc, _dc.cursor() as _du:
                _du.execute("UPDATE oe_decision_audit SET verification_status='REPLAY_ERROR' WHERE decision_id=%s",
                            (_dpl_trade_result["decision_id"],))
        except Exception as _dbu:
            log.warning(f"[dpl] REPLAY_ERROR status update failed: {_dbu}")
    finally:
        _dpl.replay_decision = orig

# ─────────────────────────────────────────────────────────────────────────────
# NO_TRADE site
# ─────────────────────────────────────────────────────────────────────────────

def nt_NT1_mismatch(log, _dpl_nt_result):
    orig = _dpl.replay_decision
    _dpl.replay_decision = lambda *a, **kw: {
        "full_match": False, "call_match": True,
        "put_match": False,  "direction_match": True}
    try:
        _rpl_nt = _dpl.replay_decision(_dpl_nt_result["decision_id"])
        if not _rpl_nt["full_match"]:
            _mm_nt = (f"[DPL MISMATCH] NO_TRADE decision_id={_dpl_nt_result['decision_id'][:16]} "
                      f"call_match={_rpl_nt['call_match']} put_match={_rpl_nt['put_match']} "
                      f"dir_match={_rpl_nt['direction_match']}")
            log.critical(_mm_nt); _tg(_mm_nt)
    except _dpl.ReplayCodeDriftError as _rce_nt:
        _dm_nt = (f"[DPL CODE_DRIFT] NO_TRADE decision_id={_dpl_nt_result['decision_id'][:16]}: {_rce_nt}")
        log.critical(_dm_nt); _tg(_dm_nt)
        try:
            with psycopg2.connect(_DB_URL, connect_timeout=4) as _dc, _dc.cursor() as _du:
                _vs = "WEIGHTS_DRIFT" if "WEIGHTS_DRIFT" in str(_rce_nt) else "CODE_DRIFT"
                _du.execute("UPDATE oe_decision_audit SET verification_status=%s WHERE decision_id=%s",
                            (_vs, _dpl_nt_result["decision_id"]))
        except Exception as _dbu:
            log.warning(f"[dpl] drift status update failed: {_dbu}")
    except Exception as _re_nt:
        _re_msg_nt = (f"[DPL REPLAY_ERROR] NO_TRADE decision_id={_dpl_nt_result['decision_id'][:16]}: {_re_nt}")
        log.critical(_re_msg_nt); _tg(_re_msg_nt)
        try:
            with psycopg2.connect(_DB_URL, connect_timeout=4) as _dc, _dc.cursor() as _du:
                _du.execute("UPDATE oe_decision_audit SET verification_status='REPLAY_ERROR' WHERE decision_id=%s",
                            (_dpl_nt_result["decision_id"],))
        except Exception as _dbu:
            log.warning(f"[dpl] REPLAY_ERROR status update failed: {_dbu}")
    finally:
        _dpl.replay_decision = orig

def nt_NT2_drift(log, _dpl_nt_result):
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
        _dm_nt = (f"[DPL CODE_DRIFT] NO_TRADE decision_id={_dpl_nt_result['decision_id'][:16]}: {_rce_nt}")
        log.critical(_dm_nt); _tg(_dm_nt)
        try:
            with psycopg2.connect(_DB_URL, connect_timeout=4) as _dc, _dc.cursor() as _du:
                _vs = "WEIGHTS_DRIFT" if "WEIGHTS_DRIFT" in str(_rce_nt) else "CODE_DRIFT"
                _du.execute("UPDATE oe_decision_audit SET verification_status=%s WHERE decision_id=%s",
                            (_vs, _dpl_nt_result["decision_id"]))
        except Exception as _dbu:
            log.warning(f"[dpl] drift status update failed: {_dbu}")
    except Exception as _re_nt:
        _re_msg_nt = (f"[DPL REPLAY_ERROR] NO_TRADE decision_id={_dpl_nt_result['decision_id'][:16]}: {_re_nt}")
        log.critical(_re_msg_nt); _tg(_re_msg_nt)
        try:
            with psycopg2.connect(_DB_URL, connect_timeout=4) as _dc, _dc.cursor() as _du:
                _du.execute("UPDATE oe_decision_audit SET verification_status='REPLAY_ERROR' WHERE decision_id=%s",
                            (_dpl_nt_result["decision_id"],))
        except Exception as _dbu:
            log.warning(f"[dpl] REPLAY_ERROR status update failed: {_dbu}")
    finally:
        _dpl.replay_decision = orig

def nt_NT3_generic(log, _dpl_nt_result):
    orig = _dpl.replay_decision
    _dpl.replay_decision = lambda *a, **kw: (_ for _ in ()).throw(
        RuntimeError("simulated network error")
    )
    try:
        _rpl_nt = _dpl.replay_decision(_dpl_nt_result["decision_id"])
        if not _rpl_nt["full_match"]:
            log.critical("..."); _tg("...")
    except _dpl.ReplayCodeDriftError as _rce_nt:
        _dm_nt = (f"[DPL CODE_DRIFT] NO_TRADE decision_id={_dpl_nt_result['decision_id'][:16]}: {_rce_nt}")
        log.critical(_dm_nt); _tg(_dm_nt)
        try:
            with psycopg2.connect(_DB_URL, connect_timeout=4) as _dc, _dc.cursor() as _du:
                _vs = "WEIGHTS_DRIFT" if "WEIGHTS_DRIFT" in str(_rce_nt) else "CODE_DRIFT"
                _du.execute("UPDATE oe_decision_audit SET verification_status=%s WHERE decision_id=%s",
                            (_vs, _dpl_nt_result["decision_id"]))
        except Exception as _dbu:
            log.warning(f"[dpl] drift status update failed: {_dbu}")
    except Exception as _re_nt:
        _re_msg_nt = (f"[DPL REPLAY_ERROR] NO_TRADE decision_id={_dpl_nt_result['decision_id'][:16]}: {_re_nt}")
        log.critical(_re_msg_nt); _tg(_re_msg_nt)
        try:
            with psycopg2.connect(_DB_URL, connect_timeout=4) as _dc, _dc.cursor() as _du:
                _du.execute("UPDATE oe_decision_audit SET verification_status='REPLAY_ERROR' WHERE decision_id=%s",
                            (_dpl_nt_result["decision_id"],))
        except Exception as _dbu:
            log.warning(f"[dpl] REPLAY_ERROR status update failed: {_dbu}")
    finally:
        _dpl.replay_decision = orig

# ── Run all 6 branches (one decision_id each) ─────────────────────────────────
run_branch("TRADE",    "T1",  "full_match=False → log.critical + _tg(); no DB write",            trade_T1_mismatch, _did["T1"])
run_branch("TRADE",    "T2",  "ReplayCodeDriftError → log.critical + _tg() + UPDATE CODE_DRIFT", trade_T2_drift,    _did["T2"])
run_branch("TRADE",    "T3",  "generic Exception → log.critical + _tg() + UPDATE REPLAY_ERROR",  trade_T3_generic,  _did["T3"])
run_branch("NO_TRADE", "NT1", "full_match=False → log.critical + _tg(); no DB write",            nt_NT1_mismatch,   _did["NT1"])
run_branch("NO_TRADE", "NT2", "ReplayCodeDriftError → log.critical + _tg() + UPDATE CODE_DRIFT", nt_NT2_drift,      _did["NT2"])
run_branch("NO_TRADE", "NT3", "generic Exception → log.critical + _tg() + UPDATE REPLAY_ERROR",  nt_NT3_generic,    _did["NT3"])

print("DONE")
