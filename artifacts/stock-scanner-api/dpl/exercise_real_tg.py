#!/usr/bin/env python3
"""
exercise_real_tg.py — R8.3 proof: REAL _tg on a single branch (T3)

_tg() below is copied verbatim from aiem_options_scheduler.py lines 77-95,
with one addition: the response body is printed to show message_id.

sed -n '77,95p' aiem_options_scheduler.py confirms the scheduler's live function.
This script runs the T3 branch body using that real function, not a stub.
"""
import sys, os, json, urllib.request, logging, io
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import psycopg2
import aiem_options_dpl as _dpl
from aiem_options_dpl import write_decision, bootstrap_dpl_phase3

_DB_URL = os.environ['DATABASE_URL']

# ── REAL _tg — verbatim from aiem_options_scheduler.py lines 77-95 ───────────
# Difference from scheduler: urllib response body printed for message_id proof.
log_tg = logging.getLogger("real_tg")
log_tg.setLevel(logging.WARNING)
log_tg.addHandler(logging.StreamHandler())

def _tg(text: str) -> bool:
    token   = "".join(os.environ.get("TELEGRAM_BOT_TOKEN", "").split())
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        log_tg.warning("[telegram] token/chat_id not configured")
        return False
    try:
        payload = json.dumps({"chat_id": chat_id, "text": text,
                              "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            body = json.loads(r.read())
            print(f"  [real _tg] status={r.status}  ok={body.get('ok')}  "
                  f"message_id={body.get('result', {}).get('message_id')}  "
                  f"chat_id={body.get('result', {}).get('chat', {}).get('id')}")
            return r.status == 200
    except Exception as e:
        log_tg.warning(f"[telegram] send failed: {e}")
        return False

# ── Setup: is_test_record=TRUE row for T3 proof ──────────────────────────────
bootstrap_dpl_phase3(_DB_URL)
_aud = write_decision(
    input_data ={"ticker": "R8REALTG", "call_score": 55.0, "put_score": 45.0},
    output_data={"direction": "LONG_CALL", "trace_id": "r8_real_tg_proof_2026-07-19"},
    is_test_record=True,
    db_url=_DB_URL,
)
_TEST_DID = _aud["decision_id"]
print(f"[setup] test decision_id={_TEST_DID[:24]}  is_test_record=True")
print(f"[setup] TELEGRAM_CHAT_ID env={os.environ.get('TELEGRAM_CHAT_ID', '(not set)')}")
print()

# ── T3 branch body with REAL _tg ─────────────────────────────────────────────
_logger = logging.getLogger("r8_real_tg_branch")
_logger.setLevel(logging.DEBUG)
_buf = io.StringIO()
_h = logging.StreamHandler(_buf)
_h.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
_logger.addHandler(_h)

print("=" * 68)
print("SITE=TRADE  branch=T3  generic Exception → log.critical + REAL _tg + UPDATE REPLAY_ERROR")
print("=" * 68)

_re = RuntimeError("[R8.3 proof] real _tg T3 branch — 2026-07-19")
_dpl_trade_result = {"decision_id": _TEST_DID}

_re_msg = (
    f"[DPL REPLAY_ERROR] TRADE "
    f"decision_id={_dpl_trade_result['decision_id'][:16]}: {_re}"
)
_logger.critical(_re_msg)
print(f"  LOG: {_buf.getvalue().strip()}")

print(f"  [calling REAL _tg...]")
_ok = _tg(_re_msg)
print(f"  [_tg returned] ok={_ok}")

try:
    with psycopg2.connect(_DB_URL, connect_timeout=4) as _dc_re, \
         _dc_re.cursor() as _du_re:
        _du_re.execute(
            "UPDATE oe_decision_audit SET verification_status='REPLAY_ERROR' WHERE decision_id=%s",
            (_TEST_DID,)
        )
    print("  [DB] UPDATE verification_status='REPLAY_ERROR': committed")
except Exception as _dbu_re:
    print(f"  [DB] UPDATE failed: {_dbu_re}")

with psycopg2.connect(_DB_URL) as _vc, _vc.cursor() as _vcur:
    _vcur.execute(
        "SELECT verification_status FROM oe_decision_audit WHERE decision_id=%s",
        (_TEST_DID,)
    )
    _row = _vcur.fetchone()
    print(f"  [DB verify] decision_id={_TEST_DID[:16]}  verification_status={_row[0] if _row else 'NO ROW'}")

print()
print("DONE")
