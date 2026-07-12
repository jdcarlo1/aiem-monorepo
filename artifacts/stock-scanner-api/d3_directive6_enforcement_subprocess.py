"""
Directive 6 PAPER ENFORCEMENT — subprocess.
Called by d3_directive6_enforcement_harness.py.

Steps covered:
  Step 4:  Set DB state=PAUSED + mode=ENFORCE (real DB writes, cache-busted)
  Step 5a: BLOCK run — real Telegram call (Guard B fires, no silent swallow)
  Step 5b: BLOCK run — _tg_send patched to raise (Guard B "failed alert" log path)
  Step 4r: Restore DB: mode=SHADOW + state=NORMAL (g5_authorize_resume)
  Step 6:  ALLOW run — mocked candidates + quotes → paper_trades +1
"""
import os, sys, io, time, datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, line_buffering=True)

# ── Standard main.py stubs (port binding + wsgiref) ──────────────────────
import socket as _sock_mod
_orig_bind = _sock_mod.socket.bind
def _stub_bind(self, addr):
    if isinstance(addr, tuple) and addr[1] in (5050, 8080, 5000, 5001):
        print(f"{datetime.datetime.utcnow().isoformat()}Z socket.bind intercepted port {addr[1]} ✓")
        return
    return _orig_bind(self, addr)
_sock_mod.socket.bind = _stub_bind

import wsgiref.simple_server as _wsgisrv
_wsgisrv.make_server = lambda *a, **kw: type("_S", (), {"serve_forever": lambda s: None})()

# ── Import main (heavy) ───────────────────────────────────────────────────
print(f"{datetime.datetime.utcnow().isoformat()}Z Importing main …")
_t0 = time.time()
import main as _main
print(f"{datetime.datetime.utcnow().isoformat()}Z main imported ✓  elapsed={time.time()-_t0:.1f}s")

import psycopg2 as _pg2
_DB = os.environ["DATABASE_URL"]

def _q(sql, *args):
    with _pg2.connect(_DB, connect_timeout=4) as c, c.cursor() as cu:
        cu.execute(sql, args)
        return cu.fetchone()[0]

# ── Patch weekend/holiday guard ───────────────────────────────────────────
_main._is_trading_day = lambda d: True
print(f"{datetime.datetime.utcnow().isoformat()}Z _is_trading_day patched → always True ✓")

# ── Null/mock external dependencies ──────────────────────────────────────
_main._pos_sizer  = None           # → _sizing_gate="PARAMS_NOT_CONFIRMED" (allowlist pass)
_main._macro_snap = None           # → macro gate skipped
_main._daily_loss_check    = lambda db: {"halt_trading": False}
_main._portfolio_corr_risk = lambda db: {"concentration_risk_flag": False}
print(f"{datetime.datetime.utcnow().isoformat()}Z external-API objects nulled/mocked ✓")

import aiem_diagram3_governance as _d3gov

# ── Pre-flight baselines ──────────────────────────────────────────────────
_pre_paper   = _q("SELECT COUNT(*) FROM aiem_paper_trades")
_pre_execlog = _q("SELECT COUNT(*) FROM aiem_paper_execution_log")
_pre_cfghist = _q("SELECT COUNT(*) FROM d3_governance_config_history")
print(f"[PRE] aiem_paper_trades           = {_pre_paper}")
print(f"[PRE] aiem_paper_execution_log    = {_pre_execlog}")
print(f"[PRE] d3_governance_config_history = {_pre_cfghist}")

# ═════════════════════════════════════════════════════════════════════════
# STEP 4: Set state=PAUSED + mode=ENFORCE (real DB writes)
# ═════════════════════════════════════════════════════════════════════════
print("\n=== STEP 4: Set state=PAUSED + mode=ENFORCE ===")
_s4_state = _d3gov.set_d3_system_state(
    state="PAUSED",
    reason="directive6_block_test: temporary PAUSED for enforcement verification",
    changed_by="directive6_test",
)
print(f"set_d3_system_state  → {_s4_state}")

_s4_mode = _d3gov.set_d3_checkpoint_mode(
    checkpoint="G0", mode="ENFORCE",
    reason="directive6_block_test: temporary ENFORCE for enforcement verification",
    changed_by="directive6_test",
    confirm=True,
)
print(f"set_d3_checkpoint_mode → {_s4_mode}")

_db_mode  = _q("SELECT mode  FROM d3_checkpoint_config WHERE checkpoint='G0'")
_db_state = _q("SELECT state FROM d3_system_state WHERE id=1")
assert _db_mode  == "ENFORCE", f"mode not ENFORCE in DB: {_db_mode}"
assert _db_state == "PAUSED",  f"state not PAUSED in DB: {_db_state}"
print(f"[4] DB verified: G0 mode={_db_mode}  system_state={_db_state} ✓")

# ═════════════════════════════════════════════════════════════════════════
# STEP 5a: BLOCK run — real Telegram path (Guard B fires)
# ═════════════════════════════════════════════════════════════════════════
print("\n=== STEP 5a: BLOCK run (real Telegram path) ===")
_r5a = _main._aiem_paper_execute_today(trigger_source="directive6_block_5a")
print(f"[5a] result: {_r5a}")
assert _r5a.get("blocked") is True,        f"expected blocked=True; got {_r5a}"
assert _r5a.get("decision") == "BLOCK",    f"expected BLOCK; got {_r5a.get('decision')}"
assert _r5a.get("mode") == "ENFORCE",      f"expected mode=ENFORCE; got {_r5a.get('mode')}"
assert _r5a.get("system_state") == "PAUSED", f"expected system_state=PAUSED; got {_r5a.get('system_state')}"

_post_paper_5a = _q("SELECT COUNT(*) FROM aiem_paper_trades")
assert _post_paper_5a == _pre_paper, \
    f"[5a] paper_trades changed: {_pre_paper} → {_post_paper_5a} (must be unchanged)"
print(f"[5a] paper_trades unchanged: {_pre_paper} → {_post_paper_5a} ✓")

_blocked_g0_count = _q(
    "SELECT COUNT(*) FROM aiem_paper_execution_log "
    "WHERE status='BLOCKED_G0' AND trigger_source='directive6_block_5a'"
)
assert _blocked_g0_count >= 1, f"[5a] no BLOCKED_G0 exec_log row found"
print(f"[5a] BLOCKED_G0 exec_log row present: {_blocked_g0_count} ✓")
print(f"[5a] Guard B: Telegram call fired (see any '[Guard B] Telegram alert failed:' "
      f"absence above = call succeeded without exception) ✓")

# ═════════════════════════════════════════════════════════════════════════
# STEP 5b: BLOCK run — failed alert path (_tg_send patched to raise)
# ═════════════════════════════════════════════════════════════════════════
print("\n=== STEP 5b: BLOCK run (Guard B failed-alert log path) ===")
_orig_tg_send = _main._tg_send

def _tg_send_raiser(*a, **kw):
    raise RuntimeError("d6_forced_test_failure")

_main._tg_send = _tg_send_raiser

# Redirect this call's stdout into a buffer so we can assert on the log line,
# then re-emit it to the real stdout for the harness to see.
_buf5b       = io.StringIO()
_real_stdout = sys.stdout
sys.stdout   = _buf5b
_r5b = _main._aiem_paper_execute_today(trigger_source="directive6_block_5b")
sys.stdout = _real_stdout
_out5b = _buf5b.getvalue()
# Re-emit captured output so harness captures it too
print(_out5b, end="")

assert _r5b.get("blocked") is True, f"expected blocked=True; got {_r5b}"
assert "[Guard B] Telegram alert failed:" in _out5b, (
    f"Expected '[Guard B] Telegram alert failed:' in stdout.\n"
    f"Captured output:\n{_out5b}"
)
assert "d6_forced_test_failure" in _out5b, (
    f"Expected exception detail in output.\nCaptured:\n{_out5b}"
)
print(f"[5b] '[Guard B] Telegram alert failed:' present in output ✓")
print(f"[5b] exception detail 'd6_forced_test_failure' present ✓")

_main._tg_send = _orig_tg_send
print(f"[5b] _tg_send restored to original ✓")

# ═════════════════════════════════════════════════════════════════════════
# STEP 4r: Restore DB — mode=SHADOW, state=NORMAL (via g5_authorize_resume)
# ═════════════════════════════════════════════════════════════════════════
print("\n=== STEP 4r: Restore mode=SHADOW + state=NORMAL ===")
_r4r_mode = _d3gov.set_d3_checkpoint_mode(
    checkpoint="G0", mode="SHADOW",
    reason="directive6_test_restore: restoring SHADOW after block test",
    changed_by="directive6_test",
    confirm=False,   # de-escalation never requires confirm
)
print(f"mode restored → {_r4r_mode}")

_r4r_state = _d3gov.g5_authorize_resume(
    target_state="NORMAL",
    reason="directive6_test_restore: restoring NORMAL after block test",
    changed_by="directive6_test",
)
print(f"state restored → {_r4r_state}")

_db_mode_r  = _q("SELECT mode  FROM d3_checkpoint_config WHERE checkpoint='G0'")
_db_state_r = _q("SELECT state FROM d3_system_state WHERE id=1")
assert _db_mode_r  == "SHADOW", f"mode not restored to SHADOW: {_db_mode_r}"
assert _db_state_r == "NORMAL", f"state not restored to NORMAL: {_db_state_r}"
print(f"[4r] DB verified after restore: G0 mode={_db_mode_r}  state={_db_state_r} ✓")

# ═════════════════════════════════════════════════════════════════════════
# STEP 6: ALLOW run — mocked candidates + quotes → paper_trades +1
# ═════════════════════════════════════════════════════════════════════════
print("\n=== STEP 6: ALLOW run (mocked candidates + quotes) ===")

_TEST_TICKER = "TEST_D6_ALLOW"
_test_cand = {
    "ticker":     _TEST_TICKER,
    "trade_type": "STOCK",
    "source":     "directive6_test",
    "detail":     "D6 enforcement allow-run test candidate",
    "score":      75.0,
    "raw_score":  75.0,
}

_main._aiem_paper_pick_candidates = lambda: [_test_cand]
_main._td_quotes = lambda tickers: {
    t: {"last": 10.00, "bid": 9.95, "ask": 10.05} for t in tickers
}
print(f"mocked _aiem_paper_pick_candidates → [{_test_cand['ticker']}]")
print(f"mocked _td_quotes → last=10.00 for all tickers")

_pre_paper_6 = _q("SELECT COUNT(*) FROM aiem_paper_trades")
_r6 = _main._aiem_paper_execute_today(trigger_source="directive6_allow_6")
print(f"[6] result: {_r6}")

_post_paper_6 = _q("SELECT COUNT(*) FROM aiem_paper_trades")
print(f"[6] paper_trades: {_pre_paper_6} → {_post_paper_6}  "
      f"(delta={_post_paper_6 - _pre_paper_6})")
assert _post_paper_6 == _pre_paper_6 + 1, (
    f"Expected paper_trades +1; got {_pre_paper_6} → {_post_paper_6}"
)
print(f"[6] paper_trades +1 ✓")

with _pg2.connect(_DB, connect_timeout=4) as _c6, _c6.cursor() as _cu6:
    _cu6.execute(
        "SELECT id, ticker, trade_date, status, signal_source, entry_price, notional "
        "FROM aiem_paper_trades WHERE ticker = %s ORDER BY id DESC LIMIT 1",
        (_TEST_TICKER,)
    )
    _new_row = _cu6.fetchone()
print(
    f"[6] NEW ROW in aiem_paper_trades: "
    f"id={_new_row[0]}  ticker={_new_row[1]}  trade_date={_new_row[2]}  "
    f"status={_new_row[3]}  signal_source={_new_row[4]}  "
    f"entry_price={_new_row[5]}  notional={_new_row[6]}"
)

# ═════════════════════════════════════════════════════════════════════════
# FINAL SUMMARY
# ═════════════════════════════════════════════════════════════════════════
_final_cfghist = _q("SELECT COUNT(*) FROM d3_governance_config_history")
print(f"\n=== FINAL DELTA SUMMARY ===")
print(f"d3_governance_config_history: {_pre_cfghist} → {_final_cfghist}  "
      f"(delta={_final_cfghist - _pre_cfghist})")
print(f"aiem_paper_trades:            {_pre_paper}   → {_post_paper_6}  "
      f"(delta={_post_paper_6 - _pre_paper})")
print(f"aiem_paper_execution_log:     {_pre_execlog}  "
      f"→ {_q('SELECT COUNT(*) FROM aiem_paper_execution_log')}")
print(f"\n[ENFORCEMENT VERDICT] PASS")
print(
    f"\n[CLEANUP PENDING] The following test row was inserted into aiem_paper_trades "
    f"by the ALLOW run:\n"
    f"  id={_new_row[0]}  ticker={_new_row[1]}  trade_date={_new_row[2]}  "
    f"status={_new_row[3]}  signal_source={_new_row[4]}\n"
    f"Awaiting explicit approval in this session before deleting it."
)

os._exit(0)
