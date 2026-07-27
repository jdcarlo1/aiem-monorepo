"""
AIEM Process — standalone nano-cap premarket scanner.
Zero dependency on Flask / main.py. Completely independent engine.

Watches a DIFFERENT universe than the main scanner:
  price $1-$20, float <20M, gap >2%, premarket vol >50K
  — low-float nano caps with explosive premarket moves.
  These stocks do NOT appear in the main scanner (no options needed).

Alerts delivered via Telegram (TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID).

Daily schedule (all times ET, Mon–Fri):
  6:55 AM   warm-up: one Polygon call → 8 000+ stocks cached
  7:00 AM+  premarket scan every 15 min (7:00–9:15)
  9:30–10:30 open watcher every 5 min — Telegram alert when confidence ≥72
  4:30 PM   grade T1 outcomes
  4:35 PM   grade T3 / T5 outcomes
  4:45 PM   find missed runners
  5:00 PM   pattern gap analysis
  5:15 PM   write signal discoveries
  6:00 PM   nightly learn — update signal trust weights

Data flow:
  Polygon /v2/snapshot → 8 000+ stocks (one call, 2-3 s)
       ↓  price $1–$20        (~2 000)
       ↓  premarket vol > 50K   (~400)
       ↓  gap > 2 %             (~100)
       ↓  float < 20 M           (~30–50)
       ↓  AIEM scores each
       →  top 10 picks written to aiem_process_predictions
"""

import os
import sys
import time
import json
_BOOT_TIME = time.time()  # process start time for /health uptime
import math
import logging
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, date

# ── Early health server — must start BEFORE slow imports (aiem_optprob etc.) ─
# aiem_optprob imports scipy/numpy/sklearn which take 30-60 s on a cold
# production container.  Replit's promote-phase prober fires immediately on
# startup; if the port is silent during that window the deploy fails.
# All the code here uses only stdlib (already imported above), so this thread
# is live in < 1 s — well before any heavy import blocks the main thread.
_AIEM_PROCESS_PORT = int(os.environ.get("AIEM_PROCESS_PORT", "5055"))
MAX_SCAN_TRIGGERS_PER_DAY = 10   # hard daily cap on accepted scan triggers (watchdog + GH Actions)
# Ceiling without cap (traced):
#   Watchdog  : 39 poll cycles × 3 retries = 117 POST attempts max (only on persistent failure)
#   GH Actions: 32 cron runs   × 3 retries =  96 POST attempts max
#   Combined  : 213 accepted triggers/day without this cap


def _rs_gate_check(run_id, _test_date=None, _trace_id=None):
    """Pre-action gate check for /run-scan — runs BEFORE threading.Thread(...).start().

    Gates (fail closed on any DB error):
      G1  Kill switch  — aiem_watchdog_flags.morning_watchdog_trigger_enabled must be 'true'
      G2  Daily cap    — morning_watchdog_audit.triggers_fired < MAX_SCAN_TRIGGERS_PER_DAY
                         enforced atomically via SELECT FOR UPDATE + UPDATE in one transaction
      G3a No active RUNNING lease in morning_scan_runs (prevents concurrent scan launch)
      G3b No existing SUCCEEDED slot today (scan already completed)
      G4  Evidence chain file accessible (tools/verified_run_chain.jsonl readable)

    Records every call (accepted or blocked) to aiem_scan_trigger_log.
    _test_date: if set (date object), use instead of date.today() for G2/G3 queries only —
                allows tests to target a clean date without touching production data.
    """
    import psycopg2 as _pg, os as _os, json as _jsc
    from datetime import date as _dg
    _db_url  = _os.environ.get("DATABASE_URL", "")
    _today   = _test_date if _test_date is not None else _dg.today()
    _result  = {"allowed": False, "reason": "gate_error", "trigger_count": -1}
    _conn    = None

    _PSC_DDL = (
        "CREATE TABLE IF NOT EXISTS pipeline_stage_checkpoints "
        "(id BIGSERIAL PRIMARY KEY, trace_id TEXT NOT NULL, stage TEXT NOT NULL, "
        "stage_order INT NOT NULL, payload JSONB, "
        "written_at TIMESTAMPTZ DEFAULT NOW(), "
        "CONSTRAINT psc_trace_stage_uq UNIQUE (trace_id, stage))"
    )
    _PSC_INS = (
        "INSERT INTO pipeline_stage_checkpoints "
        "(trace_id, stage, stage_order, payload, written_at) "
        "VALUES (%s, %s, %s, %s::jsonb, NOW()) "
        "ON CONFLICT (trace_id, stage) DO UPDATE "
        "SET payload=EXCLUDED.payload, written_at=NOW()"
    )
    _PSC_ORD = {"TRIGGER_EVALUATED": 4, "TRIGGER_LOGGED": 5}

    def _chk_write(stage, payload=None):
        if not _trace_id:
            return
        try:
            with _pg.connect(_db_url, connect_timeout=3) as _cc, _cc.cursor() as _kc:
                _kc.execute(_PSC_DDL)
                _kc.execute(_PSC_INS, (
                    _trace_id, stage, _PSC_ORD.get(stage, 99),
                    _jsc.dumps(payload) if payload is not None else None))
                _cc.commit()
        except Exception as _ce:
            import logging as _clog
            _clog.getLogger(__name__).error(
                f"[checkpoint] {stage} failed trace={str(_trace_id)[:8]}: {_ce}")
    try:
        _conn = _pg.connect(_db_url, connect_timeout=5)
        _conn.autocommit = False
        _cur  = _conn.cursor()
        # Ensure tables exist (idempotent — each CREATE is a no-op after first run)
        _cur.execute("""
            CREATE TABLE IF NOT EXISTS aiem_watchdog_flags (
                flag_name  TEXT PRIMARY KEY,
                flag_value TEXT NOT NULL,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )""")
        _cur.execute("""
            INSERT INTO aiem_watchdog_flags (flag_name, flag_value)
            VALUES ('morning_watchdog_trigger_enabled', 'true')
            ON CONFLICT DO NOTHING""")
        _cur.execute("""
            CREATE TABLE IF NOT EXISTS morning_watchdog_audit (
                audit_date     DATE PRIMARY KEY,
                triggers_fired INT  NOT NULL DEFAULT 0,
                updated_at     TIMESTAMPTZ DEFAULT NOW()
            )""")
        _cur.execute("""
            CREATE TABLE IF NOT EXISTS aiem_scan_trigger_log (
                id                  BIGSERIAL   PRIMARY KEY,
                run_id              TEXT        NOT NULL,
                logged_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                action              TEXT        NOT NULL,
                reason              TEXT        NOT NULL,
                trigger_count_at_time INT
            )""")
        _conn.commit()

        # G1 — Kill switch (watchdog/GH can read; only Joel can set to 'false')
        _cur.execute(
            "SELECT flag_value FROM aiem_watchdog_flags "
            "WHERE flag_name='morning_watchdog_trigger_enabled'")
        _krow = _cur.fetchone()
        if _krow and _krow[0].strip().lower() == 'false':
            _result = {"allowed": False, "reason": "kill_switch", "trigger_count": -1}
            _chk_write("TRIGGER_EVALUATED", {"action": "blocked", "reason": _result["reason"]})
            _cur.execute(
                "INSERT INTO aiem_scan_trigger_log (run_id, action, reason) "
                "VALUES (%s, 'blocked', %s)", (run_id, _result["reason"]))
            _conn.commit()
            _chk_write("TRIGGER_LOGGED", {"action": "blocked", "reason": _result["reason"]})
            return _result

        # G2 — Daily cap (atomic: INSERT default row → lock row → check → conditional increment)
        _cur.execute("""
            INSERT INTO morning_watchdog_audit (audit_date, triggers_fired)
            VALUES (%s, 0) ON CONFLICT DO NOTHING
        """, (_today,))
        _cur.execute(
            "SELECT triggers_fired FROM morning_watchdog_audit "
            "WHERE audit_date=%s FOR UPDATE", (_today,))
        _cap_row = _cur.fetchone()
        _current = _cap_row[0] if _cap_row else 0
        if _current >= MAX_SCAN_TRIGGERS_PER_DAY:
            _result = {"allowed": False,
                       "reason": f"daily_cap:{_current}/{MAX_SCAN_TRIGGERS_PER_DAY}",
                       "trigger_count": _current}
            _chk_write("TRIGGER_EVALUATED", {"action": "blocked", "reason": _result["reason"], "count": _current})
            _cur.execute(
                "INSERT INTO aiem_scan_trigger_log "
                "(run_id, action, reason, trigger_count_at_time) "
                "VALUES (%s, 'blocked', %s, %s)",
                (run_id, _result["reason"], _current))
            _conn.commit()
            _chk_write("TRIGGER_LOGGED", {"action": "blocked", "reason": _result["reason"]})
            return _result

        # G3a — No active unexpired RUNNING lease (prevents concurrent scan launch)
        try:
            _cur.execute("""
                SELECT COUNT(*) FROM morning_scan_runs
                WHERE market_date=%s AND status='RUNNING'
                  AND lease_expires_at > NOW()
            """, (_today,))
            if _cur.fetchone()[0] > 0:
                _result = {"allowed": False,
                           "reason": "verification_gate:active_running_lease",
                           "trigger_count": _current}
                _chk_write("TRIGGER_EVALUATED", {"action": "blocked", "reason": _result["reason"]})
                _cur.execute(
                    "INSERT INTO aiem_scan_trigger_log "
                    "(run_id, action, reason, trigger_count_at_time) "
                    "VALUES (%s, 'blocked', %s, %s)",
                    (run_id, _result["reason"], _current))
                _conn.commit()
                _chk_write("TRIGGER_LOGGED", {"action": "blocked", "reason": _result["reason"]})
                return _result
            # G3b — No existing SUCCEEDED slot (scan already completed today)
            _cur.execute("""
                SELECT COUNT(*) FROM morning_scan_runs
                WHERE market_date=%s AND status='SUCCEEDED'
            """, (_today,))
            if _cur.fetchone()[0] > 0:
                _result = {"allowed": False,
                           "reason": "verification_gate:scan_already_succeeded",
                           "trigger_count": _current}
                _chk_write("TRIGGER_EVALUATED", {"action": "blocked", "reason": _result["reason"]})
                _cur.execute(
                    "INSERT INTO aiem_scan_trigger_log "
                    "(run_id, action, reason, trigger_count_at_time) "
                    "VALUES (%s, 'blocked', %s, %s)",
                    (run_id, _result["reason"], _current))
                _conn.commit()
                _chk_write("TRIGGER_LOGGED", {"action": "blocked", "reason": _result["reason"]})
                return _result
        except Exception as _v3e:
            _result = {"allowed": False,
                       "reason": "verification_gate:morning_scan_runs_unqueryable",
                       "trigger_count": _current}
            _chk_write("TRIGGER_EVALUATED", {"action": "blocked", "reason": _result["reason"]})
            _cur.execute(
                "INSERT INTO aiem_scan_trigger_log "
                "(run_id, action, reason, trigger_count_at_time) "
                "VALUES (%s, 'blocked', %s, %s)",
                (run_id, _result["reason"], _current))
            _conn.commit()
            _chk_write("TRIGGER_LOGGED", {"action": "blocked", "reason": _result["reason"]})
            return _result

        # G4 — Evidence chain file accessible
        _chain_path = _os.path.join(
            _os.path.dirname(_os.path.abspath(__file__)),
            "tools", "verified_run_chain.jsonl")
        if not _os.path.isfile(_chain_path):
            _result = {"allowed": False, "reason": "evidence_chain:file_not_found",
                       "trigger_count": _current}
            _chk_write("TRIGGER_EVALUATED", {"action": "blocked", "reason": _result["reason"]})
            _cur.execute(
                "INSERT INTO aiem_scan_trigger_log "
                "(run_id, action, reason, trigger_count_at_time) "
                "VALUES (%s, 'blocked', %s, %s)",
                (run_id, _result["reason"], _current))
            _conn.commit()
            _chk_write("TRIGGER_LOGGED", {"action": "blocked", "reason": _result["reason"]})
            return _result

        # All gates passed — atomically increment accepted trigger count
        _cur.execute("""
            UPDATE morning_watchdog_audit
            SET triggers_fired = triggers_fired + 1, updated_at = NOW()
            WHERE audit_date=%s
        """, (_today,))
        _new_count = _current + 1
        _chk_write("TRIGGER_EVALUATED", {"action": "accepted", "reason": "all_gates_pass", "count": _new_count})
        _cur.execute(
            "INSERT INTO aiem_scan_trigger_log "
            "(run_id, action, reason, trigger_count_at_time) "
            "VALUES (%s, 'accepted', 'all_gates_pass', %s)",
            (run_id, _new_count))
        _conn.commit()
        _chk_write("TRIGGER_LOGGED", {"action": "accepted", "count": _new_count})
        _result = {"allowed": True, "reason": "all_gates_pass", "trigger_count": _new_count}
        return _result

    except Exception as _ge:
        if _conn:
            try: _conn.rollback()
            except: pass
        _result = {"allowed": False,
                   "reason": f"gate_db_error:{type(_ge).__name__}",
                   "trigger_count": -1}
        # Best-effort audit log on gate exception
        try:
            _c2 = _pg.connect(_db_url, connect_timeout=3)
            _c2.cursor().execute(
                "INSERT INTO aiem_scan_trigger_log (run_id, action, reason) "
                "VALUES (%s, 'blocked', %s)", (run_id, _result["reason"]))
            _c2.commit(); _c2.close()
        except: pass
        return _result
    finally:
        if _conn:
            try: _conn.close()
            except: pass


def _start_process_health_server():
    """Start the single HTTP server on port 5055.

    Handles ALL paths from the start so no second bind is needed:
      GET  /          → health check (always 200)
      GET  /status    → last scan result from _LAST_SCAN
      POST /run-scan  → trigger scan via _SCAN_FN_REGISTRY["run_scan"] (503 until registered)
      POST /run-grade → trigger grade via _SCAN_FN_REGISTRY["run_grade"] (503 until registered)
    _admin_server() will register the real callables and create the DB table — no new bind.
    """
    import socketserver, json as _json_hs
    from http.server import BaseHTTPRequestHandler
    class _H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/status"):
                with _STATE_LOCK:
                    snap = dict(_LAST_SCAN)
                body = _json_hs.dumps(snap if snap else {"status": "no_scan_yet"}).encode()
            elif self.path.startswith("/morning-scan-status"):
                # DB-backed: returns morning_scan_runs rows for today
                try:
                    import psycopg2 as _pg2m
                    from datetime import date as _dm
                    _mc = _pg2m.connect(
                        os.environ.get("DATABASE_URL", ""), connect_timeout=5
                    )
                    _mk = _mc.cursor()
                    _td = _dm.today()
                    _mk.execute("""
                        SELECT run_key, scheduled_slot, status, attempt_count,
                               started_at, completed_at, result_count, error
                        FROM morning_scan_runs WHERE market_date=%s
                        ORDER BY scheduled_slot
                    """, (_td,))
                    _rows = _mk.fetchall()
                    _mc.close()
                    body = _json_hs.dumps({
                        "date": str(_td),
                        "slots": [
                            {
                                "run_key": r[0], "slot": r[1], "status": r[2],
                                "attempts": r[3],
                                "started_at":  str(r[4]) if r[4] else None,
                                "completed_at": str(r[5]) if r[5] else None,
                                "result_count": r[6], "error": r[7],
                            }
                            for r in _rows
                        ],
                        "succeeded_count": sum(1 for r in _rows if r[2] == "SUCCEEDED"),
                    }).encode()
                except Exception as _mse:
                    body = _json_hs.dumps({"error": str(_mse)}).encode()
            elif self.path.startswith("/health"):
                import time as _ht
                _uptime_s = int(_ht.time() - _BOOT_TIME)
                _last_cp = None
                try:
                    import psycopg2 as _hpg, os as _hos
                    _hc = _hpg.connect(_hos.environ.get("DATABASE_URL", ""),
                                       connect_timeout=3)
                    _hk = _hc.cursor()
                    _hk.execute(
                        "SELECT MAX(written_at) FROM pipeline_stage_checkpoints")
                    _hr = _hk.fetchone()
                    _last_cp = str(_hr[0]) if _hr and _hr[0] else None
                    _hc.close()
                except Exception:
                    pass
                body = _json_hs.dumps({
                    "status": "ok",
                    "uptime_s": _uptime_s,
                    "pid": os.getpid(),
                    "boot_ts": _ht.strftime(
                        "%Y-%m-%dT%H:%M:%SZ", _ht.gmtime(_BOOT_TIME)),
                    "last_checkpoint_ts": _last_cp,
                }).encode()
            else:
                body = b'{"status":"ok"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            import uuid as _u, json as _jp
            if self.path == "/run-scan":
                fn = _SCAN_FN_REGISTRY.get("run_scan")
                if fn is None:
                    self.send_response(503)
                    body = b'{"error":"not_ready","hint":"scheduler still loading"}'
                else:
                    _rid  = str(_u.uuid4())
                    # Extract optional trace_id from POST body (watchdog sends it)
                    _body_trace_id = None
                    try:
                        _clen = int(self.headers.get("Content-Length", "0") or "0")
                        _body_raw = self.rfile.read(_clen) if _clen > 0 else b""
                        if _body_raw:
                            _body_trace_id = _jp.loads(_body_raw).get("trace_id")
                    except Exception:
                        pass
                    _gate = _rs_gate_check(_rid, _trace_id=_body_trace_id)
                    if not _gate["allowed"]:
                        # 409 for idempotency-class blocks; 429 for cap/switch blocks
                        _rcode = (409 if _gate["reason"].startswith("verification_gate")
                                  else 429)
                        self.send_response(_rcode)
                        body = _jp.dumps({
                            "status":        "blocked",
                            "run_id":        _rid,
                            "reason":        _gate["reason"],
                            "trigger_count": _gate["trigger_count"],
                        }).encode()
                    else:
                        threading.Thread(
                            target=fn, args=(_rid,), daemon=True).start()
                        self.send_response(200)
                        body = _jp.dumps({
                            "status":        "triggered",
                            "run_id":        _rid,
                            "trigger_count": _gate["trigger_count"],
                        }).encode()
            elif self.path == "/run-grade":
                fn = _SCAN_FN_REGISTRY.get("run_grade")
                if fn is None:
                    self.send_response(503)
                    body = b'{"error":"not_ready","hint":"scheduler still loading"}'
                else:
                    threading.Thread(target=fn, daemon=True).start()
                    self.send_response(200)
                    body = b'{"status":"grade_triggered"}'
            elif self.path == "/run-warmup":
                fn = _SCAN_FN_REGISTRY.get("run_warmup")
                if fn is None:
                    self.send_response(503)
                    body = b'{"error":"not_ready","hint":"scheduler still loading"}'
                else:
                    threading.Thread(target=fn, daemon=True,
                                     name="run-warmup-manual").start()
                    self.send_response(202)
                    body = b'{"status":"warmup_triggered"}'
            else:
                self.send_response(404)
                body = b'{"error":"not_found"}'
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    class _S(socketserver.TCPServer):
        allow_reuse_address = True
    try:
        srv = _S(("0.0.0.0", _AIEM_PROCESS_PORT), _H)
        threading.Thread(target=srv.serve_forever, daemon=True,
                         name="aiem-process-health").start()
        print(f"[aiem-process] health server listening on :{_AIEM_PROCESS_PORT}", flush=True)
    except Exception as _he:
        print(f"[aiem-process] early health server error: {_he}", file=sys.stderr, flush=True)

_start_process_health_server()

# ── Production startup stagger — yield CPU to stock-api during cold boot ──────
# The production VM is an e2-small: 0.5 vCPU shared across ALL services that
# start simultaneously.  stock-api must import numpy/pandas/sklearn/xgboost
# (lines 68-142 of main.py) before its werkzeug health server binds — roughly
# 90-120 s on 0.5 vCPU.  If aiem_optprob's scipy/numpy imports run at the same
# time, both processes share 0.25 vCPU each, doubling stock-api's startup time
# so it misses the 175 s promote-phase health-check timeout.
# Sleeping here costs nothing — the health server above is already serving 200
# on port 5055, so the promote-phase prober for THIS service passes immediately.
# After the sleep, stock-api's Flask server is up and the full 0.5 vCPU is ours.
if os.environ.get("REPLIT_DEPLOYMENT"):
    print("[aiem-process] production cold-start: sleeping 100 s to yield CPU to stock-api", flush=True)
    time.sleep(100)

# ── Slow imports — wrapped so a failure can never kill the health server ───────
try:
    import pytz
    import psycopg2
    import psycopg2.extras
    import aiem_optprob
    import aiem_firstcandle
    print("[aiem-process] all slow imports loaded ✓", flush=True)
except Exception as _slow_import_err:
    import traceback as _tb
    print(f"[aiem-process] CRITICAL: slow import failed — {_slow_import_err}", flush=True)
    _tb.print_exc()
    # Stay alive so the health server thread (daemon=True) keeps running.
    # The deploy can promote; the service will be non-functional until restarted.
    print("[aiem-process] health server still running — deploy will promote; fix import and redeploy", flush=True)
    while True:
        time.sleep(3600)

# ── Socket-liveness default for every psycopg2.connect() in this process ────
def _make_safe_pg_connect(_orig_connect):
    def _safe(*_pa, **_pk):
        _pk.setdefault("connect_timeout", 10)
        _pk.setdefault("keepalives", 1)
        _pk.setdefault("keepalives_idle", 10)
        _pk.setdefault("keepalives_interval", 5)
        _pk.setdefault("keepalives_count", 3)
        _pk.setdefault("tcp_user_timeout", 30000)
        return _orig_connect(*_pa, **_pk)
    return _safe
psycopg2.connect = _make_safe_pg_connect(psycopg2.connect)

ET = pytz.timezone("US/Eastern")

# ─────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────
DB_URL        = os.environ.get("DATABASE_URL", "")
POLYGON_KEY   = os.environ.get("POLYGON_API_KEY", "")
TRADIER_TOKEN = (os.environ.get("TRADIER_API_TOKEN_2") or
                 os.environ.get("TRADIER_API_TOKEN", ""))
TG_TOKEN      = "".join(os.environ.get("TELEGRAM_BOT_TOKEN", "").split())
TG_CHAT_ID    = os.environ.get("TELEGRAM_CHAT_ID", "8609255707").strip()

# Funnel thresholds
MIN_PRICE         = 1.0
MAX_PRICE         = 20.0
MIN_PM_VOLUME     = 50_000
MIN_GAP_PCT       = 2.0
MAX_FLOAT_SHARES  = 20_000_000
CONFIDENCE_THRESH = 50          # S1b/S1c/S1d score 54-61%; gap_large (non-validated) tops out at ~48%
CANDIDATE_LIMIT   = 50          # max after float filter before scoring

# ─────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s [AIEM] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("aiem")


# ─────────────────────────────────────────────────────────────
# MODULE-LEVEL PIPELINE STATE
# (passed between the 4:30–5:15 jobs without hitting the DB again)
# ─────────────────────────────────────────────────────────────
_STATE = {
    "universe":    [],   # list of {ticker, price, prev_close, volume, gap_pct, float_shares}
    "picks":       [],   # today's top-10 predictions (from premarket scan)
    "misses":      [],   # stocks that ran >5% but AIEM didn't pick
    "gap_patterns": {},  # signal → {in_picks, in_misses} tallies
}
_STATE_LOCK = threading.Lock()
_PREMARKET_SCAN_LOCK = threading.Lock()   # one premarket scan at a time
_LAST_SCAN: dict = {}                      # in-memory record of the last triggered scan
_SCAN_FN_REGISTRY: dict = {}              # populated once _run_manual_scan is defined

# Rotating cursor for the deep-ITM options-probability segment scans.
# Mutable dict so aiem_optprob can advance it across the 6 daily runs.
_optprob_cursor_state: dict = {"cursor": 0}


# ─────────────────────────────────────────────────────────────
# HELPERS: DB
# ─────────────────────────────────────────────────────────────
def _db():
    return psycopg2.connect(DB_URL, connect_timeout=10)


def _db_log_scan(run_id, trigger_source, status, started_at=None, completed_at=None,
                  freshness_date=None, candidate_count=None, error_message=None):
    """Upsert a row in premarket_scan_runs. Silently ignores errors."""
    try:
        conn = _db()
        try:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO premarket_scan_runs
                    (run_id, trigger_source, triggered_at, started_at, completed_at,
                     status, source_freshness_date, candidate_count, error_message)
                VALUES (%s, %s, NOW(), %s, %s, %s, %s, %s, %s)
                ON CONFLICT (run_id) DO UPDATE SET
                    status                = EXCLUDED.status,
                    started_at            = COALESCE(premarket_scan_runs.started_at,
                                                     EXCLUDED.started_at),
                    completed_at          = EXCLUDED.completed_at,
                    source_freshness_date = EXCLUDED.source_freshness_date,
                    candidate_count       = EXCLUDED.candidate_count,
                    error_message         = EXCLUDED.error_message
            """, (run_id, trigger_source, started_at, completed_at, status,
                   freshness_date, candidate_count, error_message))
            conn.commit()
        finally:
            conn.close()
    except Exception as _dls_e:
        log.warning(f"_db_log_scan error (non-fatal): {_dls_e}")


_US_HOLIDAYS_2026 = {
    date(2026, 1,  1),   # New Year's Day
    date(2026, 1, 19),   # MLK Day
    date(2026, 2, 16),   # Presidents Day
    date(2026, 4,  3),   # Good Friday
    date(2026, 5, 25),   # Memorial Day
    date(2026, 6, 19),   # Juneteenth
    date(2026, 7,  3),   # Independence Day (observed — July 4 is Saturday)
    date(2026, 9,  7),   # Labor Day
    date(2026, 11, 26),  # Thanksgiving
    date(2026, 12, 25),  # Christmas
}

def _market_day() -> bool:
    today = datetime.now(ET).date()
    return today.weekday() < 5 and today not in _US_HOLIDAYS_2026


# ─────────────────────────────────────────────────────────────
# HELPERS: POLYGON
# ─────────────────────────────────────────────────────────────
def _pg_get(url: str, timeout: int = 15) -> dict:
    """GET a Polygon URL; return parsed JSON or {}."""
    try:
        sep = "&" if "?" in url else "?"
        full = f"{url}{sep}apiKey={POLYGON_KEY}"
        req  = urllib.request.Request(full, headers={"User-Agent": "AIEM/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        log.warning(f"polygon GET error ({url[:60]}…): {e}")
        return {}


def _polygon_all_snapshot() -> list:
    """
    ONE call: fetch snapshot for ALL US stocks (~8 000+).
    Returns list of dicts:
      {ticker, price, prev_close, gap_pct, volume, avg_volume}
    Takes ~2-3 s.
    """
    data = _pg_get(
        "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers"
        "?include_otc=false",
        timeout=20,
    )
    results = []
    for t in (data.get("tickers") or []):
        sym = (t.get("ticker") or "").upper()
        if not sym or len(sym) > 5 or "." in sym or "/" in sym:
            continue
        day      = t.get("day")      or {}
        prev_day = t.get("prevDay")  or {}
        price    = float(day.get("c") or day.get("o") or 0)
        prev     = float(prev_day.get("c") or 0)
        vol      = int(day.get("v") or 0)
        avg_vol  = int(t.get("min", {}).get("av") or prev_day.get("v") or 1)
        gap_pct  = float(t.get("todaysChangePerc") or 0)
        results.append({
            "ticker":      sym,
            "price":       price,
            "prev_close":  prev,
            "gap_pct":     gap_pct,
            "volume":      vol,
            "avg_volume":  avg_vol,
            "float_shares": None,   # filled in stage 4
        })
    log.info(f"snapshot returned {len(results)} tickers")
    return results


def _polygon_snapshot_tickers(tickers: list) -> dict:
    """Snapshot for a specific list of tickers. Returns {sym: {...}}."""
    if not tickers:
        return {}
    batch = ",".join(tickers[:100])
    data  = _pg_get(
        f"https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers"
        f"?tickers={batch}",
        timeout=10,
    )
    out = {}
    for t in (data.get("tickers") or []):
        sym  = (t.get("ticker") or "").upper()
        day  = t.get("day") or {}
        prev = t.get("prevDay") or {}
        out[sym] = {
            "price":      float(day.get("c") or day.get("o") or 0),
            "open":       float(day.get("o") or 0),
            "prev_close": float(prev.get("c") or 0),
            "volume":     int(day.get("v") or 0),
            "gap_pct":    float(t.get("todaysChangePerc") or 0),
        }
    return out


def _polygon_ref_batch(tickers: list) -> dict:
    """
    Fetch float (shares outstanding) from Polygon reference for each ticker.
    Runs up to 10 parallel requests. Returns {sym: float_shares}.
    """
    def _fetch_one(sym):
        data = _pg_get(
            f"https://api.polygon.io/v3/reference/tickers/{sym}", timeout=6
        )
        res = data.get("results") or {}
        shares = (res.get("weighted_shares_outstanding") or
                  res.get("share_class_shares_outstanding") or None)
        return sym, int(shares) if shares else None

    result = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(_fetch_one, t): t for t in tickers}
        for fut in as_completed(futures):
            try:
                sym, shares = fut.result()
                result[sym] = shares
            except Exception:
                pass
    return result


def _polygon_prev_close_batch(tickers: list) -> dict:
    """
    Fetch previous-day close for a list of tickers using Polygon prev agg.
    Returns {sym: close_price}.
    """
    def _fetch_one(sym):
        data = _pg_get(
            f"https://api.polygon.io/v2/aggs/ticker/{sym}/prev", timeout=6
        )
        results = (data.get("results") or [{}])
        close = results[0].get("c") if results else None
        return sym, float(close) if close else None

    result = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(_fetch_one, t): t for t in tickers}
        for fut in as_completed(futures):
            try:
                sym, close = fut.result()
                result[sym] = close
            except Exception:
                pass
    return result


# ─────────────────────────────────────────────────────────────
# HELPERS: TRADIER (fallback for quotes when Polygon incomplete)
# ─────────────────────────────────────────────────────────────
def _td_quotes(symbols: list) -> dict:
    """Fetch live quotes from Tradier for up to 200 symbols. Uses urllib (no requests)."""
    if not TRADIER_TOKEN or not symbols:
        return {}
    try:
        batch   = ",".join(symbols[:200])
        req     = urllib.request.Request(
            f"https://api.tradier.com/v1/markets/quotes?symbols={batch}",
            headers={"Authorization": f"Bearer {TRADIER_TOKEN}",
                     "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            resp = json.loads(r.read())
        raw = resp.get("quotes", {}).get("quote", [])
        if isinstance(raw, dict):
            raw = [raw]
        return {
            q["symbol"]: {
                "price":      float(q.get("last") or q.get("open") or 0),
                "prev_close": float(q.get("prevclose") or 0),
                "open":       float(q.get("open") or 0),
                "volume":     int(q.get("volume") or 0),
                "avg_volume": int(q.get("average_volume") or 1),
            }
            for q in raw if q.get("symbol")
        }
    except Exception as e:
        log.warning(f"td_quotes error: {e}")
        return {}


def _polygon_grouped_daily_universe() -> list:
    """
    Fetch full market OHLCV from Polygon grouped-daily for the most recent
    available trading day (goes back up to 7 calendar days).
    Returns list of dicts with ticker + prev_close for gap calculation.
    The live gap is computed later via _tradier_live_update().
    """
    et_now = datetime.now(ET)
    for days_back in range(1, 8):
        check_date = (et_now - timedelta(days=days_back)).date()
        if check_date.weekday() >= 5:          # skip weekends
            continue
        date_str = check_date.strftime("%Y-%m-%d")
        try:
            data    = _pg_get(
                f"https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks/{date_str}"
                f"?adjusted=true&include_otc=false",
                timeout=25,
            )
            results = data.get("results") or []
            if len(results) < 100:
                log.info(f"grouped_daily {date_str}: only {len(results)} rows — trying earlier date")
                continue
            log.info(f"grouped_daily: {date_str} → {len(results):,} stocks")
            out = []
            for r in results:
                sym = (r.get("T") or "").upper()
                if not sym or len(sym) > 5 or "." in sym or "/" in sym:
                    continue
                close = float(r.get("c") or 0)
                high  = float(r.get("h") or 0)
                low   = float(r.get("l") or 0)
                vol   = int(r.get("v") or 0)
                # T-1 close_strength: where did yesterday close in its range?
                # 1.0 = closed at high, 0.0 = closed at low — knowable at 9:30 AM
                t1_cs = round((close - low) / (high - low), 4) if (high - low) > 0 else 0.0
                out.append({
                    "ticker":             sym,
                    "price":              close,
                    "prev_close":         close,   # will be refined by Tradier
                    "prev_close_strength": t1_cs,  # Signal #3 gate input
                    "volume":             0,        # will be filled by Tradier (today's volume)
                    "avg_volume":         max(vol, 1),
                    "gap_pct":            0.0,
                    "float_shares":       None,
                })
            with _STATE_LOCK:
                _STATE["source_freshness_date"] = date_str
            return out
        except Exception as e:
            log.warning(f"grouped_daily {date_str}: {e}")
    log.error("grouped_daily: could not find usable trading day in last 7 days")
    return []


def _tradier_live_update(candidates: list) -> list:
    """
    Enrich candidates with Tradier live prices.
    Calculates today's gap_pct and rvol for each ticker.
    Calls Tradier in batches of 200 (stays within rate limits).
    """
    if not candidates or not TRADIER_TOKEN:
        return candidates
    syms = [c["ticker"] for c in candidates]
    live: dict = {}
    for i in range(0, len(syms), 200):
        batch = syms[i:i + 200]
        try:
            req = urllib.request.Request(
                f"https://api.tradier.com/v1/markets/quotes?symbols={','.join(batch)}",
                headers={"Authorization": f"Bearer {TRADIER_TOKEN}",
                         "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                resp = json.loads(r.read())
            raw = resp.get("quotes", {}).get("quote", [])
            if isinstance(raw, dict):
                raw = [raw]
            for q in raw:
                s = (q.get("symbol") or "").upper()
                if s:
                    live[s] = {
                        "price":      float(q.get("last") or q.get("open") or 0),
                        "open":       float(q.get("open") or 0),
                        "volume":     int(q.get("volume") or 0),
                        "avg_volume": int(q.get("average_volume") or 1),
                        "prev_close": float(q.get("prevclose") or 0),
                    }
        except Exception as e:
            log.warning(f"tradier live update batch {i//200+1}: {e}")

    enriched = []
    for c in candidates:
        sym = c["ticker"]
        td  = live.get(sym)
        if not td:
            enriched.append(c)
            continue
        cur_price  = td["price"]
        prev_close = td["prev_close"] or c["prev_close"] or 1
        vol        = td["volume"]
        avg_vol    = td["avg_volume"] or 1
        gap_pct    = ((cur_price - prev_close) / prev_close * 100) if prev_close else 0
        # Sanity caps: >200% gap = reverse-split artifact; avg_vol <5K = meaningless RVOL
        if gap_pct > 200:
            gap_pct = 0.0
        rvol = round(vol / avg_vol, 2) if avg_vol >= 5_000 else 0.0
        enriched.append({
            **c,
            "price":      cur_price,
            "prev_close": prev_close,
            "volume":     vol,
            "avg_volume": avg_vol,
            "gap_pct":    round(gap_pct, 2),
            "rvol":       rvol,
        })
    return enriched


# ─────────────────────────────────────────────────────────────
# HELPERS: ALERT
# ─────────────────────────────────────────────────────────────
def _tg_send(message: str, *, signal_source: str = "aiem_process_nanocap",
             ticker: str = None, alert_class: str = "SIGNAL",
             audit_trace_id: str = None, trigger_price: float = None,
             is_test: bool = False) -> bool:
    """Send a Telegram message. Returns True if API responded ok:true.

    Defaults to alert_class='SIGNAL' under signal_source='aiem_process_nanocap'
    since every alert this process sends is a ticker-bearing nano-cap pick —
    this is the one sender where 'SIGNAL' is the correct default rather than
    an opt-in, so it starts building a TELEGRAM_ALERTS trust history
    immediately. Logging is fail-open via alert_gateway and never blocks
    or is blocked by the actual Telegram send."""
    send_text = message
    if alert_class == "SIGNAL" and signal_source != "unclassified":
        try:
            import alert_gateway as _ag_trust
            send_text = message + _ag_trust.get_trust_display(signal_source)
        except Exception as _te:
            log.warning(f"trust display error (non-fatal): {_te}")
    ok = False
    if not TG_TOKEN or not TG_CHAT_ID:
        log.warning("_tg_send: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set")
        ok = False
    else:
        try:
            payload = json.dumps({
                "chat_id":    TG_CHAT_ID,
                "text":       send_text,
                "parse_mode": "HTML",
            }).encode()
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                resp = json.loads(r.read())
                ok = resp.get("ok", False)
                if ok:
                    log.info(f"Telegram sent: {message[:60]}…")
                else:
                    log.warning(f"Telegram API error: {resp}")
        except Exception as _e:
            log.warning(f"Telegram send failed: {_e}")
            ok = False
    try:
        import alert_gateway as _ag
        _ag.log_alert(message, signal_source=signal_source, ticker=ticker,
                       alert_class=alert_class, audit_trace_id=audit_trace_id,
                       trigger_price=trigger_price, is_test=is_test, sent_ok=ok)
    except Exception as _ge:
        log.warning(f"alert_gateway logging error (non-fatal): {_ge}")
    return ok


def _send_alert(message: str) -> None:
    """Backward-compat wrapper — delegates to Telegram.

    This is used for the combined multi-pick daily summary, so the whole
    message is logged as alert_class='INFO' (not gradeable on its own);
    per-ticker SIGNAL rows for the individual picks are logged separately
    by the caller via _log_pick_signals() so each ticker+price gets its
    own gradeable row.
    """
    _tg_send(message, alert_class="INFO")


def _log_pick_signals(qualifiers: list, sent_ok: bool) -> None:
    """Log one gradeable SIGNAL row per ticker pick that has a real live
    price (cur_price > 0). Fallback picks scored on premarket data only
    (cur_price == 0.0) have no real trigger_price to grade against, so
    they are skipped rather than logged with a fake price."""
    try:
        import alert_gateway as _ag, uuid as _uuid
        trace_id = f"aiem_process_nanocap_{_uuid.uuid4().hex[:12]}"
        for q in qualifiers:
            price = q.get("price") or 0
            if not price:
                continue
            _ag.log_alert(
                f"{q['ticker']} sig={q.get('sig')} conf={q.get('conf')} "
                f"price={price} stop={q.get('stop')}",
                signal_source="aiem_process_nanocap", ticker=q["ticker"],
                alert_class="SIGNAL", audit_trace_id=trace_id,
                trigger_price=price, sent_ok=sent_ok,
            )
    except Exception as _ge:
        log.warning(f"_log_pick_signals: alert_gateway logging error (non-fatal): {_ge}")


# ─────────────────────────────────────────────────────────────
# HELPER: SIGNAL TRUST WEIGHTS
# ─────────────────────────────────────────────────────────────
def _load_trust_weights(conn) -> dict:
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT signal_name, trust_weight, rolling_win_rate, n_outcomes_observed
            FROM signal_trust_weights ORDER BY trust_weight DESC
        """)
        return {
            r[0]: {"trust": float(r[1] or 1.0), "win_rate": float(r[2] or 0.5), "n": r[3] or 0}
            for r in cur.fetchall()
        }
    except Exception as e:
        log.warning(f"trust weight load error: {e}")
        return {}


# ─────────────────────────────────────────────────────────────
# CORE: AIEM SCORING ENGINE (trust-weighted, 9 signals)
# ─────────────────────────────────────────────────────────────
def aiem_score_ticker(ticker: str, data: dict, trust_weights: dict):
    """
    Returns (confidence 0-100, signal_basis str, reasoning str, predicted_move str).
    Each signal weighted by its historical trust from signal_trust_weights.
    """
    signals, reasoning = {}, []
    raw_score = max_score = 0.0

    price    = data.get("price") or data.get("premarket_price") or 0
    prev     = data.get("prev_close") or price or 1
    vol      = data.get("volume") or data.get("premarket_volume") or 0
    avg_vol  = data.get("avg_volume") or 1
    flt      = data.get("float_shares") or 999_000_000
    si       = data.get("short_interest_pct") or 0
    spread   = data.get("bid_ask_spread_pct") or 999
    coiling  = data.get("consolidating") or False
    catalyst = data.get("has_catalyst") or False

    gap_pct   = ((price - prev) / prev * 100) if prev else 0
    vol_ratio = vol / avg_vol if avg_vol > 0 else 0
    prev_cs   = float(data.get("prev_close_strength") or 0)

    def _add(name, base, cond, desc=""):
        nonlocal raw_score, max_score
        t   = trust_weights.get(name, {}).get("trust", 1.0)
        eff = base * t
        max_score += eff
        if cond:
            signals[name] = eff
            raw_score    += eff
            if desc:
                reasoning.append(desc)

    # S1 — Premarket gap
    if   gap_pct >= 15: _add("gap_explosive",  18, True, f"Explosive gap +{gap_pct:.1f}%")
    elif gap_pct >= 10: _add("gap_large",       14, True, f"Large gap +{gap_pct:.1f}%")
    elif gap_pct >= 5:  _add("gap_moderate",    10, True, f"Moderate gap +{gap_pct:.1f}%")
    elif gap_pct >= 2:  _add("gap_small",        5, True, f"Small gap +{gap_pct:.1f}%")
    else:               _add("gap_small",        5, False)

    # S1b — Gap sweet spot (15–25% = validated high-WR zone)
    # Backtest: 864W/153L over 13 months, avg win +18.1%, median +15.4%
    _add("gap_sweet_spot", 5, 15 <= gap_pct < 25,
         f"Sweet spot gap {gap_pct:.1f}% (85% WR zone, +18% avg)")

    # S1c — Signal #3: Momentum Carry (full — top 20% of prior range)
    # Gap 15-22% + T-1 close_strength >= 0.80
    # Backtest: 1,738 trades, WR=96.0%, AvgRet=+13.85%, PF=47.2x, Sharpe=+1.78
    # Logic: stock closed in top 20% of its range yesterday AND gaps again today
    # = real momentum carry, not a gap-and-trap.  All inputs knowable at 9:30 AM.
    # Combined with S1b → +13 pts total for the highest-conviction setups.
    _add("momentum_carry", 8, 15 <= gap_pct < 22 and prev_cs >= 0.80,
         f"Momentum carry: gap {gap_pct:.1f}% in sweet zone + T-1 closed strong ({prev_cs:.2f})")

    # S1d — Soft Carry (upper 40% of prior range — middle tier)
    # Gap 15-22% + T-1 close_strength 0.60–0.79 (mutually exclusive with S1c)
    # More picks than S1c alone; still meaningfully better than random gappers.
    # Combined with S1b → +9 pts total.
    _add("soft_carry", 4, 15 <= gap_pct < 22 and 0.60 <= prev_cs < 0.80,
         f"Soft carry: gap {gap_pct:.1f}% in sweet zone + T-1 mid-range close ({prev_cs:.2f})")

    # S2 — Volume surge
    if   vol_ratio >= 5:   _add("volume_surge_extreme",   20, True, f"Volume {vol_ratio:.1f}x — extreme")
    elif vol_ratio >= 3:   _add("volume_surge_high",      15, True, f"Volume {vol_ratio:.1f}x — strong")
    elif vol_ratio >= 1.5: _add("volume_surge_moderate",   8, True, f"Volume {vol_ratio:.1f}x — elevated")
    else:                  _add("volume_surge_moderate",   8, False)

    # S3 — Float
    if   flt < 5_000_000:  _add("float_micro",  18, True, f"Micro float {flt/1e6:.1f}M")
    elif flt < 15_000_000: _add("float_low",    12, True, f"Low float {flt/1e6:.1f}M")
    elif flt < 50_000_000: _add("float_medium",  6, True, f"Mid float {flt/1e6:.1f}M")
    else:                  _add("float_medium",  6, False)

    # S4 — Short interest
    if   si >= 25: _add("short_squeeze_high",      16, True, f"SI {si:.1f}% — squeeze setup")
    elif si >= 15: _add("short_squeeze_moderate",  10, True, f"SI {si:.1f}%")
    elif si >= 8:  _add("short_squeeze_low",        5, True, f"SI {si:.1f}%")
    else:          _add("short_squeeze_low",        5, False)

    # S5 — Catalyst
    _add("catalyst_present",    15, catalyst,        "Catalyst detected")

    # S6 — Tight spread
    _add("tight_spread",         8, spread < 0.5,   f"Tight spread {spread:.2f}%")

    # S7 — Consolidation
    _add("consolidating",       12, coiling,         "Coiling — tight range setup")

    # S8 — Price in breakout range
    _add("price_breakout_range", 6, 1.0 <= price <= 20.0, f"Price ${price:.2f} in range")

    # S9 — Gap + volume combo (strongest)
    _add("gap_volume_combo",    20, gap_pct >= 8 and vol_ratio >= 3,
         f"COMBO gap {gap_pct:.1f}% + vol {vol_ratio:.1f}x")

    conf = min(100, round((raw_score / max_score * 100) if max_score > 0 else 0, 1))

    move = (
        "Strong breakout likely — high conviction long" if conf >= 80
        else "Moderate breakout — watch open" if conf >= 65
        else "Possible setup — needs open confirmation" if conf >= 50
        else "Low conviction — monitor only"
    )
    return conf, ", ".join(signals), " | ".join(reasoning) or "No strong signals", move


# ─────────────────────────────────────────────────────────────
# JOB 0: WARM-UP  (6:55 AM)
# One Polygon call → cache full universe → funnel to top candidates
# ─────────────────────────────────────────────────────────────
def aiem_warmup():
    """
    6:55 AM: Build candidate universe using Polygon grouped-daily (previous
    trading day) — no live snapshot needed.  Filters by price $1-$20 and
    avg-volume > 10K to keep the Tradier batch calls manageable (~1 000 tickers).
    Live gap / RVOL are computed by the 7:00 premarket scan via Tradier.
    """
    if not _market_day():
        return
    log.info("warmup: fetching Polygon grouped-daily universe…")
    t0 = time.time()

    all_tickers = _polygon_grouped_daily_universe()
    log.info(f"grouped_daily returned {len(all_tickers):,} stocks")

    # Stage 1: price $1–$20 (using prev-day close as proxy)
    s1 = [t for t in all_tickers if MIN_PRICE <= t["price"] <= MAX_PRICE]
    log.info(f"stage1 price ${MIN_PRICE}-${MAX_PRICE}: {len(all_tickers):,} → {len(s1):,}")

    # Stage 2: avg volume > 10K (light filter — Tradier will apply tighter RVOL at scan time)
    s2 = [t for t in s1 if t["avg_volume"] >= 10_000]
    log.info(f"stage2 avg_vol >10K: {len(s1):,} → {len(s2):,}")

    log.info(f"warmup complete in {time.time()-t0:.1f}s — {len(s2):,} candidates cached for premarket scan")
    with _STATE_LOCK:
        _STATE["universe"] = s2
    freshness_date = _STATE.get("source_freshness_date")
    return freshness_date, len(s2)


# ─────────────────────────────────────────────────────────────
# JOB 1: PREMARKET SCAN  (7:00–9:15 AM, every 15 min)
# ─────────────────────────────────────────────────────────────
def aiem_premarket_scan():
    """
    Score cached universe with trust-weighted AIEM engine.
    Write top 10 to aiem_process_predictions (replaces today's each run).
    Refreshes live prices via Tradier on each pass (no Polygon snapshot needed).
    """
    if not _market_day():
        return
    now_et = datetime.now(ET)
    log.info(f"premarket_scan at {now_et.strftime('%H:%M ET')}")

    with _STATE_LOCK:
        base_universe = list(_STATE["universe"])

    if not base_universe:
        log.info("premarket_scan: warmup universe empty — skipping")
        return

    # Refresh all candidates with live Tradier prices → computes gap_pct + rvol
    log.info(f"premarket_scan: refreshing {len(base_universe):,} candidates via Tradier…")
    enriched = _tradier_live_update(base_universe)

    # Apply the same funnel with live data
    s1 = [t for t in enriched if MIN_PRICE    <= (t.get("price") or 0) <= MAX_PRICE]
    s2 = [t for t in s1       if (t.get("volume") or 0) >= MIN_PM_VOLUME]
    s3 = [t for t in s2       if (t.get("gap_pct") or 0) >= MIN_GAP_PCT]
    log.info(f"funnel: {len(enriched):,} → price {len(s1):,} → vol {len(s2):,} → gap {len(s3):,}")

    universe = s3   # float filter skipped (no reliable live float source; scoring handles it)

    with _STATE_LOCK:
        _STATE["universe"] = enriched   # keep full enriched list for next pass

    if not universe:
        log.info("premarket_scan: no candidates after funnel")
        return 0

    log.info(f"premarket_scan: scoring {len(universe)} candidates")

    _n_written = 0
    conn = None
    try:
        conn = _db()
        trust_weights = _load_trust_weights(conn)
        cur = conn.cursor()

        scored = []
        for t in universe[:CANDIDATE_LIMIT]:
            try:
                conf, sig_basis, reasoning, move = aiem_score_ticker(
                    t["ticker"], t, trust_weights
                )
                scored.append({**t, "confidence": conf,
                                "signal_basis": sig_basis,
                                "reasoning": reasoning,
                                "predicted_move": move})
            except Exception as e:
                log.warning(f"score error {t['ticker']}: {e}")

        scored.sort(key=lambda x: x["confidence"], reverse=True)
        top10 = scored[:10]

        today = datetime.now(ET).date()
        cur.execute("DELETE FROM aiem_process_predictions WHERE prediction_date = %s", (today,))
        for rank, p in enumerate(top10, 1):
            cur.execute("""
                INSERT INTO aiem_process_predictions
                    (prediction_date, ticker, rank, confidence_score,
                     signal_basis, reasoning, predicted_move, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
            """, (today, p["ticker"], rank, p["confidence"],
                  p["signal_basis"], p["reasoning"], p["predicted_move"]))

        conn.commit()
        with _STATE_LOCK:
            _STATE["picks"] = top10

        _n_written = len(top10)
        log.info(f"premarket_scan: wrote {_n_written} predictions")
        for p in top10[:3]:
            log.info(f"  #{p['ticker']} conf={p['confidence']} gap={p['gap_pct']:.1f}% — {p['reasoning'][:70]}")

    except Exception as e:
        log.error(f"premarket_scan DB error: {e}")
        if conn:
            try: conn.rollback()
            except: pass
    finally:
        if conn:
            try: conn.close()
            except: pass
    return _n_written


# ─────────────────────────────────────────────────────────────
# JOB 2: OPEN WATCHER  (9:30–10:30 AM, every 5 min)
# ─────────────────────────────────────────────────────────────
def aiem_open_watcher():
    """
    At open: re-score predictions with live Polygon prices.
    Blend premarket score (40%) + live score (60%).
    Fire SMS the moment AIEM's blended confidence crosses the threshold.
    """
    if not _market_day():
        return
    now_et = datetime.now(ET)
    h, m   = now_et.hour, now_et.minute
    now_mins = h * 60 + m
    if not (570 <= now_mins <= 930):  # 9:30 AM – 3:30 PM ET
        return

    conn = None
    try:
        conn = _db()
        cur  = conn.cursor()
        today = datetime.now(ET).date()

        cur.execute("""
            SELECT ticker, rank, confidence_score, signal_basis, reasoning, predicted_move
            FROM aiem_process_predictions WHERE prediction_date = %s ORDER BY rank
        """, (today,))
        picks = cur.fetchall()
        if not picks:
            return

        cur.execute("""
            SELECT ticker FROM signal_fire_log
            WHERE fire_date = %s AND signal_name = 'AIEM_OPEN_ALERT'
        """, (today,))
        already = {r[0] for r in cur.fetchall()}

        trust_weights = _load_trust_weights(conn)

        # Fetch live prices from Polygon in one batch call
        syms        = [p[0] for p in picks]
        live_prices = _polygon_snapshot_tickers(syms)

        # Fallback to Tradier if Polygon returns nothing
        if not live_prices:
            td = _td_quotes(syms)
            live_prices = {s: {"price": d["price"], "prev_close": d["prev_close"],
                               "volume": d["volume"], "avg_volume": d["avg_volume"]}
                           for s, d in td.items()}

        # Only send the grouped alert once per day (at 9:30 AM first pass)
        if "DAILY_SUMMARY" in already:
            return

        # Determine if live prices are usable (Polygon 403 is permanent on current plan)
        live_prices_available = bool(live_prices)
        if not live_prices_available:
            log.warning("open_watcher: no live prices available (Polygon 403 + Tradier unavailable) — using premarket scores only")

        # Score every pick. If live prices are unavailable for a ticker, fall
        # back to the premarket score so picks are never silently dropped.
        qualifiers = []
        for ticker, rank, base_conf, sig_basis, reasoning, predicted_move in picks:
            live = live_prices.get(ticker, {})
            if live:
                # Full live re-score path
                if "momentum_carry" in (sig_basis or ""):
                    inferred_prev_cs = 0.85
                elif "soft_carry" in (sig_basis or ""):
                    inferred_prev_cs = 0.70
                else:
                    inferred_prev_cs = 0.0
                live_data = {
                    "price":               live.get("price"),
                    "prev_close":          live.get("prev_close"),
                    "volume":              live.get("volume", 0),
                    "avg_volume":          live.get("avg_volume", 1),
                    "prev_close_strength": inferred_prev_cs,
                }
                live_conf, _, live_reason, live_move = aiem_score_ticker(
                    ticker, live_data, trust_weights
                )
                # base_conf comes from the NUMERIC confidence_score DB column,
                # so psycopg2 hands it back as decimal.Decimal — mixing that
                # with a plain float in arithmetic raises TypeError and was
                # silently crashing every run that had live prices available
                # (i.e. every run where Tradier/Polygon actually worked), which
                # meant the Telegram alert never fired even when everything
                # else was healthy. Cast both operands to float explicitly.
                blended   = round(float(base_conf) * 0.4 + float(live_conf) * 0.6, 1)
                cur_price = live.get("price") or 0
                reason    = live_reason
            else:
                # Fallback: no live price — use premarket score as-is
                blended   = float(base_conf)
                cur_price = 0.0
                reason    = (reasoning or sig_basis or "premarket signal")
                live_conf = None

            log.info(f"{ticker} score={blended:.1f} (pre={base_conf} live={live_conf}) sig={sig_basis}")
            if blended >= CONFIDENCE_THRESH:
                stop_price = round(cur_price * 0.90, 2) if cur_price else None
                qualifiers.append({
                    "ticker": ticker, "rank": rank, "conf": blended,
                    "sig": sig_basis or "", "price": cur_price,
                    "stop": stop_price, "reason": reason,
                })

        if not qualifiers:
            log.info("open_watcher: no picks crossed threshold — no alert sent")
            return

        # Group by signal tier
        s1c   = [q for q in qualifiers if "momentum_carry" in q["sig"]]
        s1d   = [q for q in qualifiers if "soft_carry"     in q["sig"]]
        s1b   = [q for q in qualifiers if q not in s1c and q not in s1d]

        def _fmt_pick(q):
            stop = f"  |  Stop ≈ ${q['stop']:.2f}" if q["stop"] else ""
            return (f"  {q['ticker']}  |  Open ${q['price']:.2f}  |  Conf {q['conf']:.0f}/100{stop}\n"
                    f"  💡 {q['reason'][:100]}")

        lines = [
            f"⚡ AIEM S1B · S1C · S1D — Morning Picks",
            f"📅 {now_et.strftime('%a %b %-d, %Y')}  |  {now_et.strftime('%I:%M %p ET')}",
            f"{'─' * 32}",
        ]
        if s1c:
            lines.append("\n🟢 S1c — Full Carry (highest conviction)")
            lines.append("Gap 15-22% + prior session closed top 20%")
            for q in s1c:
                lines.append(_fmt_pick(q))
        if s1d:
            lines.append("\n🔵 S1d — Soft Carry")
            lines.append("Gap 15-22% + prior session closed upper 40%")
            for q in s1d:
                lines.append(_fmt_pick(q))
        if s1b:
            lines.append("\n🟡 S1b — Gap Zone")
            lines.append("Gap 15-25% validated sweet-spot")
            for q in s1b:
                lines.append(_fmt_pick(q))
        lines.append(f"\n🛑 -10% hard stop on all names  |  Size $500-$1,000/pick")
        lines.append(f"📊 {len(qualifiers)} pick{'s' if len(qualifiers) != 1 else ''} confirmed at open")

        # Skip-command check — did the owner reply SKIP NANO within the last 90 min?
        # Polls Telegram getUpdates (non-blocking, no webhook required).
        _skip_nano = False
        try:
            import time as _st
            _tg_skip_token   = TG_TOKEN
            _tg_skip_chat_id = TG_CHAT_ID
            _cutoff = _st.time() - 90 * 60
            _skip_req = urllib.request.Request(
                f"https://api.telegram.org/bot{_tg_skip_token}/getUpdates?timeout=0&limit=50",
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(_skip_req, timeout=6) as _skip_r:
                _skip_data = json.loads(_skip_r.read())
            for _upd in _skip_data.get("result", []):
                _msg = _upd.get("message", {})
                if str(_msg.get("chat", {}).get("id", "")) != str(_tg_skip_chat_id):
                    continue
                if _msg.get("date", 0) < _cutoff:
                    continue
                _txt = (_msg.get("text") or "").upper().strip()
                if "SKIP NANO" in _txt or "SKIP ALL" in _txt:
                    _skip_nano = True
                    break
        except Exception as _sk_e:
            log.warning(f"open_watcher: skip-check error (proceeding): {_sk_e}")

        if _skip_nano:
            log.info("open_watcher: owner replied SKIP NANO — suppressing today's nano alert")
            cur.execute("""
                INSERT INTO signal_fire_log
                    (signal_name, ticker, fire_date, metadata, logged_at)
                VALUES ('AIEM_OPEN_ALERT', 'DAILY_SUMMARY', %s, %s::jsonb, NOW())
                ON CONFLICT (signal_name, ticker, fire_date) DO NOTHING
            """, (today, json.dumps({"picks": 0, "skipped_by_owner": True})))
            conn.commit()
            return

        _sent_ok = _tg_send("\n".join(lines), alert_class="INFO")
        _log_pick_signals(qualifiers, _sent_ok)

        # Log as sent so we don't fire again today
        cur.execute("""
            INSERT INTO signal_fire_log
                (signal_name, ticker, fire_date, metadata, logged_at)
            VALUES ('AIEM_OPEN_ALERT', 'DAILY_SUMMARY', %s, %s::jsonb, NOW())
            ON CONFLICT (signal_name, ticker, fire_date) DO NOTHING
        """, (today, json.dumps({"picks": len(qualifiers)})))
        conn.commit()
        log.info(f"open_watcher: grouped alert sent — {len(qualifiers)} picks ({len(s1c)} S1c, {len(s1d)} S1d, {len(s1b)} S1b)")

    except Exception as e:
        log.error(f"open_watcher error: {e}")
        if conn:
            try: conn.rollback()
            except: pass
    finally:
        if conn:
            try: conn.close()
            except: pass


# ─────────────────────────────────────────────────────────────
# JOB 3: GRADE OUTCOMES — T1  (4:30 PM)
# ─────────────────────────────────────────────────────────────
def aiem_grade_outcomes():
    """
    EOD: pull today's closes from Polygon for each prediction.
    Write T1 result to aiem_prediction_outcomes.
    """
    if not _market_day():
        return
    today = datetime.now(ET).date()
    log.info(f"grade_outcomes for {today}")

    conn = None
    try:
        conn = _db()
        cur  = conn.cursor()

        cur.execute("""
            SELECT p.ticker FROM aiem_process_predictions p
            LEFT JOIN aiem_process_outcomes o
                ON p.ticker = o.ticker AND p.prediction_date = o.prediction_date
            WHERE p.prediction_date = %s AND o.id IS NULL
        """, (today,))
        tickers = [r[0] for r in cur.fetchall()]

        if not tickers:
            log.info("grade_outcomes: nothing to grade")
            return

        # Primary: Tradier quotes — returns both "price" (last/close) and
        # "open" (day open). Use Tradier first; Polygon snapshot 403s permanently
        # on the current plan for live/snapshot endpoints.
        td    = _td_quotes(tickers)
        snaps = {s: {"price": d["price"], "open": d["open"], "prev_close": d["prev_close"]}
                 for s, d in td.items() if d.get("price")}

        # Fallback: Polygon snapshot (may 403 on current plan, kept for future)
        if not snaps:
            snaps = _polygon_snapshot_tickers(tickers)

        graded = 0
        for ticker in tickers:
            try:
                q = snaps.get(ticker, {})
                close_price = q.get("price")
                open_price  = q.get("open") or close_price
                if not close_price or not open_price:
                    continue
                t1_ret = (close_price - open_price) / open_price * 100
                cur.execute("""
                    INSERT INTO aiem_process_outcomes
                        (prediction_date, ticker, entry_price, t1_price, t1_return, graded_at)
                    VALUES (%s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (prediction_date, ticker) DO UPDATE
                        SET t1_price  = EXCLUDED.t1_price,
                            t1_return = EXCLUDED.t1_return,
                            graded_at = NOW()
                """, (today, ticker, open_price, close_price, round(t1_ret, 4)))
                graded += 1
                log.info(f"{ticker}: open={open_price:.2f} close={close_price:.2f} ({t1_ret:+.1f}%) {'WIN' if t1_ret > 0 else 'LOSS'}")
            except Exception as e:
                log.warning(f"grade {ticker}: {e}")

        conn.commit()
        log.info(f"grade_outcomes: graded {graded}/{len(tickers)}")

    except Exception as e:
        log.error(f"grade_outcomes error: {e}")
        if conn:
            try: conn.rollback()
            except: pass
    finally:
        if conn:
            try: conn.close()
            except: pass


# ─────────────────────────────────────────────────────────────
# JOB 3B: GRADE T3 / T5  (4:35 PM)
# ─────────────────────────────────────────────────────────────
def aiem_grade_t3_t5():
    if not _market_day():
        return
    conn = None
    try:
        conn = _db()
        cur  = conn.cursor()
        today = datetime.now(ET).date()

        for n, col_p, col_r, col_w in [
            (3, "t3_price", "t3_return", "win_t3"),
            (5, "t5_price", "t5_return", "win_t5"),
        ]:
            target = today - timedelta(days=n)
            cur.execute(f"""
                SELECT ticker, entry_price FROM aiem_process_outcomes
                WHERE prediction_date = %s AND {col_p} IS NULL AND entry_price IS NOT NULL
            """, (target,))
            rows = cur.fetchall()
            if not rows:
                continue

            # Use polygon_market_daily for T3/T5 closes — it's already populated
            # daily and is not subject to the Polygon live-snapshot 403.
            # Find the closest available scan_date at or after the target date.
            tickers_needed = [r[0] for r in rows]
            cur.execute("""
                SELECT DISTINCT scan_date FROM polygon_market_daily
                WHERE scan_date >= %s
                ORDER BY scan_date ASC LIMIT 1
            """, (target,))
            nearest = cur.fetchone()
            price_map = {}
            if nearest:
                snap_date = nearest[0]
                cur.execute("""
                    SELECT ticker, close_price FROM polygon_market_daily
                    WHERE scan_date = %s AND ticker = ANY(%s)
                """, (snap_date, tickers_needed))
                price_map = {r[0]: float(r[1]) for r in cur.fetchall()}
                log.info(f"T{n}: using polygon_market_daily scan_date={snap_date} for {len(price_map)} tickers")

            # Tradier fallback for any tickers not in polygon_market_daily
            missing = [t for t in tickers_needed if t not in price_map]
            if missing:
                td_fb = _td_quotes(missing)
                for sym, d in td_fb.items():
                    if d.get("price"):
                        price_map[sym] = d["price"]

            for ticker, entry in rows:
                try:
                    price = price_map.get(ticker)
                    if price and entry:
                        ret = (price - entry) / entry * 100
                        cur.execute(f"""
                            UPDATE aiem_process_outcomes
                            SET {col_p}=%s, {col_r}=%s, {col_w}=%s
                            WHERE prediction_date=%s AND ticker=%s
                        """, (price, round(ret, 4), ret > 0, target, ticker))
                        log.info(f"T{n} {ticker}: {ret:+.1f}%")
                except Exception as e:
                    log.warning(f"T{n} grade {ticker}: {e}")

        conn.commit()
    except Exception as e:
        log.error(f"grade_t3_t5 error: {e}")
        if conn:
            try: conn.rollback()
            except: pass
    finally:
        if conn:
            try: conn.close()
            except: pass


# ─────────────────────────────────────────────────────────────
# JOB 4: FIND MISSED RUNNERS  (4:45 PM)
# Stocks that ran >5% today that AIEM didn't pick
# ─────────────────────────────────────────────────────────────
def aiem_find_missed_runners():
    """
    Pull today's big movers from Polygon (stocks up ≥5%).
    Compare against today's picks — anything not picked is a miss.
    Cache misses in _STATE for pattern analysis at 5:00 PM.
    """
    if not _market_day():
        return
    log.info("find_missed_runners: pulling today's movers…")

    # Polygon snapshot — filter for big movers
    all_snap = _polygon_all_snapshot()
    movers   = [
        t for t in all_snap
        if t["gap_pct"] >= 5.0 and t["volume"] >= 100_000 and MIN_PRICE <= t["price"] <= MAX_PRICE
    ]
    log.info(f"find_missed_runners: {len(movers)} stocks up ≥5% today")

    # Get float for movers
    float_map = _polygon_ref_batch([t["ticker"] for t in movers])
    for t in movers:
        t["float_shares"] = float_map.get(t["ticker"])

    # What did AIEM pick today?
    with _STATE_LOCK:
        picks_today = {p["ticker"] for p in _STATE.get("picks", [])}

    # If in-memory empty, fall back to DB
    if not picks_today:
        try:
            conn = _db()
            cur  = conn.cursor()
            cur.execute(
                "SELECT ticker FROM aiem_process_predictions WHERE prediction_date = %s",
                (datetime.now(ET).date(),)
            )
            picks_today = {r[0] for r in cur.fetchall()}
            conn.close()
        except Exception:
            pass

    misses = [t for t in movers if t["ticker"] not in picks_today]
    log.info(f"find_missed_runners: {len(misses)} missed (picked {len(picks_today)}, total movers {len(movers)})")
    for m in misses[:5]:
        log.info(f"  MISS {m['ticker']} +{m['gap_pct']:.1f}% vol={m['volume']:,}")

    with _STATE_LOCK:
        _STATE["misses"] = misses


# ─────────────────────────────────────────────────────────────
# JOB 5: PATTERN GAP ANALYSIS  (5:00 PM)
# Why did AIEM miss? What signals were present?
# ─────────────────────────────────────────────────────────────
def aiem_pattern_gap_analysis():
    """
    For each missed runner: score it as if AIEM had seen it this morning.
    Tally which signals appear in misses but were ABSENT from picks.
    These are the signals AIEM under-weighted — candidates for trust boost.
    """
    if not _market_day():
        return

    with _STATE_LOCK:
        misses = list(_STATE.get("misses", []))
        picks  = list(_STATE.get("picks",  []))

    if not misses:
        log.info("pattern_gap_analysis: no misses today — great day!")
        return

    log.info(f"pattern_gap_analysis: analysing {len(misses)} misses vs {len(picks)} picks")

    # Score misses with neutral trust weights (see what AIEM would have computed)
    neutral_weights = {}
    signal_in_misses: dict[str, int] = {}
    signal_in_picks:  dict[str, int] = {}

    for m in misses:
        _, sig_basis, _, _ = aiem_score_ticker(m["ticker"], m, neutral_weights)
        for sig in [s.strip() for s in sig_basis.split(",") if s.strip()]:
            signal_in_misses[sig] = signal_in_misses.get(sig, 0) + 1

    for p in picks:
        for sig in [s.strip() for s in (p.get("signal_basis") or "").split(",") if s.strip()]:
            signal_in_picks[sig] = signal_in_picks.get(sig, 0) + 1

    # Signals that appear in misses more than picks (normalised) = gap signals
    gap_patterns = {}
    all_sigs = set(signal_in_misses) | set(signal_in_picks)
    for sig in all_sigs:
        miss_rate = signal_in_misses.get(sig, 0) / max(len(misses), 1)
        pick_rate = signal_in_picks.get(sig, 0)  / max(len(picks),  1)
        gap_patterns[sig] = {
            "in_misses":  signal_in_misses.get(sig, 0),
            "in_picks":   signal_in_picks.get(sig, 0),
            "miss_rate":  round(miss_rate, 3),
            "pick_rate":  round(pick_rate, 3),
            "gap":        round(miss_rate - pick_rate, 3),
        }

    # Log top gaps
    top_gaps = sorted(gap_patterns.items(), key=lambda x: x[1]["gap"], reverse=True)[:5]
    for sig, stats in top_gaps:
        log.info(
            f"  GAP signal '{sig}': in_misses={stats['in_misses']} "
            f"in_picks={stats['in_picks']} gap={stats['gap']:+.2f}"
        )

    with _STATE_LOCK:
        _STATE["gap_patterns"] = gap_patterns


# ─────────────────────────────────────────────────────────────
# JOB 6: WRITE SIGNAL DISCOVERIES  (5:15 PM)
# Save statistically meaningful gaps to aiem_signal_discoveries
# ─────────────────────────────────────────────────────────────
def aiem_write_signal_discoveries():
    """
    Any signal that appeared in ≥5 misses today, with a miss_rate ≥ 60%,
    and a gap ≥ 0.25 above pick_rate, is flagged as a hypothesis and written
    to aiem_signal_discoveries (status='hypothesis').

    Gates applied here (pre-insert):
      1. in_misses >= 5      — minimum observations to reduce noise
      2. miss_rate >= 0.60   — signal must appear in ≥60% of missed runners
      3. gap >= 0.25         — meaningful difference over pick_rate

    Promotion gate (in nightly_learn, not here):
      4. rolling_win_rate >= 0.55 AND n_outcomes_observed >= 10
         (applied via signal_trust_weights join before status → 'validated')

    Scale convention: signal_win_rate and baseline_win_rate are stored on
    the 0-100 percentage scale, matching _mkt_tool_save_discovery.
    """
    if not _market_day():
        return

    with _STATE_LOCK:
        gap_patterns = dict(_STATE.get("gap_patterns", {}))
        misses       = list(_STATE.get("misses", []))

    if not gap_patterns:
        log.info("write_signal_discoveries: no gap patterns — skipping")
        return

    # Gates (mirrors _mkt_tool_save_discovery conventions):
    #   1. in_misses >= 5          — minimum sample size to reduce noise
    #   2. miss_rate >= 0.60       — signal must fire in ≥60% of missed runners
    #   3. gap >= 0.25             — raised from 0.20; requires a more decisive gap
    # NOTE: status='hypothesis' rows are further gated at promotion time by
    # nightly_learn (rolling_win_rate >= 0.55 AND n_outcomes_observed >= 10).
    hypotheses = [
        (sig, stats) for sig, stats in gap_patterns.items()
        if stats["in_misses"] >= 5
        and stats["miss_rate"] >= 0.60
        and stats["gap"] >= 0.25
    ]

    if not hypotheses:
        log.info("write_signal_discoveries: no significant gaps today")
        return

    log.info(f"write_signal_discoveries: saving {len(hypotheses)} hypotheses")

    conn = None
    try:
        conn = _db()
        cur  = conn.cursor()
        today = datetime.now(ET).date()

        saved = 0
        for sig, stats in hypotheses:
            # Build hypothesis text
            hypothesis = (
                f"Signal '{sig}' appears in {stats['in_misses']} missed runners "
                f"({stats['miss_rate']:.0%} rate) vs {stats['in_picks']} picks "
                f"({stats['pick_rate']:.0%} rate) — gap {stats['gap']:+.2f}. "
                f"AIEM may be under-weighting this signal."
            )
            conditions = {
                "signal_name":   sig,
                "date_observed": today.isoformat(),
                "miss_rate":     stats["miss_rate"],
                "pick_rate":     stats["pick_rate"],
                "gap":           stats["gap"],
                "n_misses":      stats["in_misses"],
                "n_picks":       stats["in_picks"],
                "missed_tickers": [m["ticker"] for m in misses[:10]],
            }

            # signal_win_rate and baseline_win_rate are stored on 0-100 scale
            # (percentage), matching _mkt_tool_save_discovery convention.
            # miss_rate and pick_rate are raw fractions (0-1); multiply by 100.
            cur.execute("""
                INSERT INTO aiem_signal_discoveries
                    (hypothesis_text, conditions_json, horizon,
                     signal_n, signal_win_rate, baseline_win_rate,
                     edge_broad, status, discovered_at, notes, signal_name)
                VALUES (%s, %s::jsonb, %s, %s, %s, %s, %s, %s, NOW(), %s, %s)
            """, (
                hypothesis,
                json.dumps(conditions),
                "1d",
                stats["in_misses"],
                round(stats["miss_rate"] * 100, 2),
                round(stats["pick_rate"] * 100, 2),
                round(stats["gap"] * 100, 2),
                "hypothesis",
                f"auto-discovered by aiem_process on {today}",
                sig,
            ))
            saved += 1
            log.info(f"  saved hypothesis: {sig} (gap={stats['gap']:+.2f})")

        conn.commit()
        log.info(f"write_signal_discoveries: saved {saved} hypotheses")

    except Exception as e:
        log.error(f"write_signal_discoveries error: {e}")
        if conn:
            try: conn.rollback()
            except: pass
    finally:
        if conn:
            try: conn.close()
            except: pass


# ─────────────────────────────────────────────────────────────
# JOB 8: WEEKEND SUMMARY  (9:30 AM Sat & Sun)
# Re-delivers the most recent weekday's top AIEM picks so you
# never miss a morning review even when markets are closed.
# ─────────────────────────────────────────────────────────────
def aiem_weekend_summary():
    """Send the most recent weekday's top AIEM picks at 9:30 AM on weekends."""
    now_et = datetime.now(ET)
    # Dedup: only fire once per weekend day
    today_str = now_et.strftime("%Y-%m-%d")
    try:
        conn = _db()
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM signal_fire_log WHERE fire_date=%s AND signal_name='WEEKEND_SUMMARY' AND ticker='DAILY_SUMMARY'",
            (now_et.date(),)
        )
        if cur.fetchone():
            conn.close()
            return
        conn.close()
    except Exception as _e:
        log.warning(f"weekend_summary dedup check error: {_e}")

    try:
        conn = _db()
        cur = conn.cursor()
        # Get most recent weekday's predictions
        cur.execute("""
            SELECT DISTINCT prediction_date FROM aiem_process_predictions
            WHERE prediction_date < CURRENT_DATE
              AND EXTRACT(DOW FROM prediction_date) BETWEEN 1 AND 5
            ORDER BY prediction_date DESC LIMIT 1
        """)
        row = cur.fetchone()
        if not row:
            conn.close()
            log.info("weekend_summary: no recent predictions found")
            return
        pred_date = row[0]

        cur.execute("""
            SELECT ticker, confidence_score, signal_basis, predicted_move_pct, reasoning
            FROM aiem_process_predictions
            WHERE prediction_date = %s
            ORDER BY confidence_score DESC
            LIMIT 20
        """, (pred_date,))
        picks = cur.fetchall()
        conn.close()
    except Exception as _e:
        log.error(f"weekend_summary DB error: {_e}")
        return

    if not picks:
        log.info("weekend_summary: no picks for most recent weekday")
        return

    day_name = pred_date.strftime("%a %b %-d")
    lines = [
        f"📅 Weekend Preview — AIEM Top Picks ({day_name})",
        f"AIEM's own reasoning on raw data — no pre-scored input",
        f"{'─' * 30}",
    ]
    for i, (ticker, conf, sig, move, reason) in enumerate(picks, 1):
        conf_f = float(conf) if conf else 0
        move_f = float(move) if move else 0
        lines.append(
            f"#{i} ${ticker}  conf={conf_f:.0f}  move={move_f:+.1f}%"
            + (f"  [{sig}]" if sig else "")
        )
    lines.append(f"{'─' * 30}")
    lines.append("⚠️ Market is closed — these are {}'s picks for context.".format(day_name))
    lines.append("Next live alert: Monday 9:30 AM ET")

    msg = "\n".join(lines)
    try:
        _tg(msg)
        # Record in signal_fire_log to prevent duplicate sends
        conn = _db()
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO signal_fire_log (fire_date, signal_name, ticker, logged_at)
               VALUES (%s, 'WEEKEND_SUMMARY', 'DAILY_SUMMARY', NOW())
               ON CONFLICT DO NOTHING""",
            (now_et.date(),)
        )
        conn.commit()
        conn.close()
        log.info(f"weekend_summary: sent {len(picks)} picks from {pred_date}")
    except Exception as _e:
        log.error(f"weekend_summary send error: {_e}")


# JOB 7: NIGHTLY LEARN  (6:00 PM)
# Update signal trust weights from the last 30 days of outcomes
# THIS IS WHERE AIEM GETS SMARTER EVERY DAY
# ─────────────────────────────────────────────────────────────
def aiem_nightly_learn():
    """
    Join aiem_predictions → aiem_prediction_outcomes for last 30 days.
    For each signal: compute win rate, update trust weight.
    trust_weight > 1.0 = AIEM boosts this signal tomorrow.
    trust_weight < 1.0 = AIEM down-weights this signal tomorrow.
    """
    if not _market_day():
        return
    today = datetime.now(ET).date()
    log.info(f"nightly_learn for {today}")

    conn = None
    try:
        conn = _db()
        cur  = conn.cursor()

        cur.execute("""
            SELECT p.signal_basis, p.confidence_score,
                   o.t1_return, o.win_t3, o.win_t5, o.t3_return
            FROM aiem_process_predictions p
            JOIN aiem_process_outcomes o
                ON p.ticker = o.ticker AND p.prediction_date = o.prediction_date
            WHERE p.prediction_date >= %s AND o.t1_return IS NOT NULL
        """, (today - timedelta(days=30),))
        rows = cur.fetchall()

        if not rows:
            log.info("nightly_learn: no graded outcomes yet — come back tomorrow")
            return

        tallies: dict = {}
        for sig_basis, _, t1_ret, win_t3, win_t5, t3_ret in rows:
            if not sig_basis:
                continue
            win = bool(win_t3) if win_t3 is not None else bool((t1_ret or 0) > 0)
            ret = float(t3_ret or t1_ret or 0)
            for sig in [s.strip() for s in sig_basis.split(",") if s.strip()]:
                if sig not in tallies:
                    tallies[sig] = {"wins": 0, "total": 0, "ret": 0.0}
                tallies[sig]["total"] += 1
                tallies[sig]["ret"]   += ret
                if win:
                    tallies[sig]["wins"] += 1

        updated = 0
        for sig, t in tallies.items():
            n          = t["total"]
            win_rate   = t["wins"] / n if n > 0 else 0.5
            avg_ret    = t["ret"] / n  if n > 0 else 0
            sample_con = min(1.0, n / 20)
            raw_trust  = 0.5 + (win_rate - 0.5) * 2
            trust_w    = 1.0 + (raw_trust - 0.5) * sample_con

            cur.execute("""
                INSERT INTO signal_trust_weights
                    (signal_name, context_bucket, rolling_win_rate,
                     n_outcomes_observed, trust_weight, last_updated_at)
                VALUES (%s, 'AIEM_PREMARKET', %s, %s, %s, NOW())
                ON CONFLICT (signal_name, context_bucket) DO UPDATE
                    SET rolling_win_rate    = EXCLUDED.rolling_win_rate,
                        n_outcomes_observed = EXCLUDED.n_outcomes_observed,
                        trust_weight        = EXCLUDED.trust_weight,
                        last_updated_at     = NOW()
            """, (sig, round(win_rate, 4), n, round(trust_w, 4)))

            updated += 1
            log.info(
                f"  '{sig}': wr={win_rate:.1%} n={n} "
                f"trust={trust_w:.3f} avg_ret={avg_ret:+.1f}%"
            )

        conn.commit()
        log.info(f"nightly_learn: updated {updated} signal weights")

        # Promote any hypothesis that now has ≥10 samples and wr > 55%
        cur.execute("""
            UPDATE aiem_signal_discoveries sd
            SET status = 'validated', confirmed_at = NOW()
            FROM signal_trust_weights stw
            WHERE sd.status = 'hypothesis'
              AND stw.signal_name = (sd.conditions_json->>'signal_name')
              AND stw.n_outcomes_observed >= 10
              AND stw.rolling_win_rate >= 0.55
        """)
        promoted = cur.rowcount
        conn.commit()
        if promoted:
            log.info(f"nightly_learn: promoted {promoted} hypotheses → validated")

        # Log research insight
        top3 = sorted(tallies.items(), key=lambda x: x[1]["wins"]/max(x[1]["total"],1), reverse=True)[:3]
        findings = "Top signals: " + ", ".join(
            f"{s}({t['wins']}/{t['total']})" for s, t in top3
        )
        cur.execute("""
            INSERT INTO aiem_research_insights
                (research_date, findings, confidence, session_name, created_at)
            VALUES (%s, %s, %s, 'aiem_process_nightly_learn', NOW())
            ON CONFLICT (research_date) DO UPDATE
                SET findings   = aiem_research_insights.findings || E'\\n' || EXCLUDED.findings,
                    confidence = EXCLUDED.confidence
        """, (today, findings, str(round(updated / max(len(tallies), 1) * 100, 1))))
        conn.commit()
        log.info(f"nightly_learn insight: {findings}")

    except Exception as e:
        log.error(f"nightly_learn error: {e}")
        if conn:
            try: conn.rollback()
            except: pass
    finally:
        if conn:
            try: conn.close()
            except: pass


# ─────────────────────────────────────────────────────────────
# MAIN — scheduler wiring
# ─────────────────────────────────────────────────────────────
def main():
    log.info("AIEM Process starting…")
    log.info(f"  DB:       {'OK' if DB_URL        else 'MISSING'}")
    log.info(f"  Polygon:  {'OK' if POLYGON_KEY   else 'MISSING'}")
    log.info(f"  Tradier:  {'OK' if TRADIER_TOKEN else 'MISSING'}")
    log.info(f"  Telegram: {'OK' if TG_TOKEN      else 'MISSING — alerts will not fire'}")

    # ── One-time Option-B backfill trigger (remove after 2026-07-26) ──────────
    # Fires ONLY during the 3 AM nightly-reset window (aiem_process restarts at
    # ~3:03 AM after os._exit at 3:02 AM). Flag file is deleted after run so
    # this executes exactly once regardless of subsequent restarts.
    import os as _bf_os, datetime as _bf_dt, zoneinfo as _bf_zi, subprocess as _bf_sub
    _bf_flag   = "/home/runner/workspace/.local/run_backfill_tonight"
    _bf_script = "/home/runner/workspace/artifacts/stock-scanner-api/tools/backfill_gap_rvol.py"
    _bf_evid   = "/home/runner/workspace/artifacts/stock-scanner-api/tools/post_backfill_evidence.py"
    _bf_log    = "/home/runner/workspace/.local/backfill_option_b_output.log"
    if _bf_os.path.exists(_bf_flag):
        _bf_now = _bf_dt.datetime.now(_bf_zi.ZoneInfo("America/New_York"))
        if 3 <= _bf_now.hour < 4:
            log.info(f"[backfill] Starting Option-B at {_bf_now.strftime('%H:%M:%S ET')}")
            try:
                with open(_bf_log, "w") as _bf_lf:
                    _bf_lf.write(f"=== Option B backfill started {_bf_now.isoformat()} ===\n")
                # Step 1: backfill
                _bf_r1 = _bf_sub.run(
                    ["python3", _bf_script],
                    capture_output=True, text=True, timeout=900,
                )
                with open(_bf_log, "a") as _bf_lf:
                    _bf_lf.write(_bf_r1.stdout)
                    if _bf_r1.stderr:
                        _bf_lf.write("STDERR:\n" + _bf_r1.stderr)
                for _l in _bf_r1.stdout.splitlines():
                    log.info(f"[backfill] {_l}")
                log.info(f"[backfill] exit_code={_bf_r1.returncode}")
                # Step 2: evidence collection
                _bf_r2 = _bf_sub.run(
                    ["python3", _bf_evid],
                    capture_output=True, text=True, timeout=600,
                )
                with open(_bf_log, "a") as _bf_lf:
                    _bf_lf.write("\n=== POST-BACKFILL EVIDENCE ===\n")
                    _bf_lf.write(_bf_r2.stdout)
                    if _bf_r2.stderr:
                        _bf_lf.write("STDERR:\n" + _bf_r2.stderr)
                for _l in _bf_r2.stdout.splitlines():
                    log.info(f"[backfill-evid] {_l}")
                log.info(f"[backfill] Evidence log: {_bf_log}")
            except Exception as _bf_e:
                log.error(f"[backfill] Exception: {_bf_e}")
                with open(_bf_log, "a") as _bf_lf:
                    _bf_lf.write(f"EXCEPTION: {_bf_e}\n")
            finally:
                try:
                    _bf_os.remove(_bf_flag)
                    log.info("[backfill] Flag file removed — will not re-run")
                except Exception:
                    pass
        else:
            log.info(f"[backfill] Flag exists but hour={_bf_now.hour} (not 3 AM window) — skipping")
    # ── End one-time backfill trigger ─────────────────────────────────────────

    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron        import CronTrigger
    from apscheduler.executors.pool       import ThreadPoolExecutor as _APPool

    sched = BackgroundScheduler(
        executors={"default": _APPool(max_workers=3)},
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 300},
        timezone=ET,
    )

    # 6:55 AM — warm-up (one Polygon call, builds candidate cache)
    sched.add_job(aiem_warmup, CronTrigger(day_of_week="mon-fri", hour=6, minute=55, timezone=ET),
                  id="aiem_warmup", replace_existing=True)

    # 7:00–9:15 AM — premarket scan every 15 min
    sched.add_job(aiem_premarket_scan,
                  CronTrigger(day_of_week="mon-fri", hour="7-9", minute="*/15", timezone=ET),
                  id="aiem_premarket_scan", replace_existing=True)

    # 9:30 AM – 3:30 PM — open watcher: every 5 min through 10:30 (primary
    # window), then every 15 min as a catch-up net through the rest of the
    # trading day. The function itself is idempotent (checks signal_fire_log
    # for today's DAILY_SUMMARY before doing anything), so extra ticks are
    # harmless. This exists because a container restart/redeploy that lands
    # outside 9:30-10:30 AM used to cause a PERMANENT miss for that day with
    # no recovery — see _startup_open_watcher_catchup below.
    sched.add_job(aiem_open_watcher,
                  CronTrigger(day_of_week="mon-fri", hour="9,10", minute="*/5", timezone=ET),
                  id="aiem_open_watcher", replace_existing=True)
    sched.add_job(aiem_open_watcher,
                  CronTrigger(day_of_week="mon-fri", hour="11-15", minute="*/15", timezone=ET),
                  id="aiem_open_watcher_catchup_net", replace_existing=True)

    # ── Startup full catch-up: handles any restart between 9:00 AM and 3:30 PM.
    #
    # Three scenarios are covered:
    #   A) Restart during 9:00-9:29 AM — run warmup+premarket now so the
    #      scheduled open_watcher at 9:30 fires with a fresh predictions table.
    #   B) Restart during 9:30 AM-3:30 PM, premarket already ran — just fire
    #      open_watcher immediately (original behaviour).
    #   C) Restart during 9:30 AM-3:30 PM, predictions EMPTY (premarket missed
    #      overnight) — run warmup+premarket first, THEN fire open_watcher.
    #      This is the exact scenario that caused today's miss.
    def _startup_full_catchup():
        import time as _t
        _t.sleep(12)  # let scheduler bind and DB/network settle
        now_et = datetime.now(ET)
        if now_et.weekday() >= 5:           # skip weekends
            return
        if not _market_day():               # skip holidays
            return
        now_mins = now_et.hour * 60 + now_et.minute

        # ── Slot-aware idempotent catchup ──────────────────────────────────────
        # NO STARTUP BLOCK: startup during the premarket window is safe because
        # this function checks whether predictions already exist before running.
        # Rules:
        # - Does NOT delete predictions if a SUCCEEDED morning_scan_runs slot exists.
        # - Uses PostgreSQL advisory lock to prevent race with GH Actions/watchdog.
        # - Writes to morning_scan_runs before and after execution for full audit.
        # - If morning_scan_runs is unavailable, fails open (scan still runs if needed).

        # Run from 6:50 AM to 3:30 PM ET on trading days
        if not (6*60 + 50 <= now_mins <= 15*60 + 30):
            return

        today = now_et.date()
        today_str = today.isoformat()
        _LOCK_KEY = 987654321   # advisory lock key, reserved for morning catchup

        # Ensure morning_scan_runs table exists (idempotent CREATE IF NOT EXISTS)
        try:
            _c0 = _db(); _k0 = _c0.cursor()
            _k0.execute("""
                CREATE TABLE IF NOT EXISTS morning_scan_runs (
                    id              SERIAL PRIMARY KEY,
                    run_key         TEXT UNIQUE NOT NULL,
                    job_name        TEXT NOT NULL,
                    market_date     DATE NOT NULL,
                    scheduled_slot  TEXT NOT NULL,
                    owner           TEXT NOT NULL DEFAULT 'unknown',
                    lease_expires_at TIMESTAMPTZ,
                    attempt_count   INTEGER NOT NULL DEFAULT 0,
                    started_at      TIMESTAMPTZ,
                    completed_at    TIMESTAMPTZ,
                    status          TEXT NOT NULL DEFAULT 'PENDING',
                    error           TEXT,
                    result_count    INTEGER,
                    created_at      TIMESTAMPTZ DEFAULT NOW(),
                    CONSTRAINT morning_scan_runs_slot_uq
                        UNIQUE (job_name, market_date, scheduled_slot)
                )
            """)
            _c0.commit(); _c0.close()
        except Exception as _e0:
            log.warning(f"[catchup] morning_scan_runs init: {_e0}")

        # Check current DB state: SUCCEEDED slots, predictions count, alert fired
        try:
            _c1 = _db(); _k1 = _c1.cursor()
            _k1.execute("""
                SELECT COUNT(*) FROM morning_scan_runs
                WHERE job_name='premarket_scan' AND market_date=%s AND status='SUCCEEDED'
            """, (today,))
            _succeeded = _k1.fetchone()[0]
            _k1.execute(
                "SELECT COUNT(*) FROM aiem_process_predictions WHERE prediction_date=%s",
                (today,)
            )
            _pred_count = _k1.fetchone()[0]
            _k1.execute(
                "SELECT 1 FROM signal_fire_log "
                "WHERE fire_date=%s AND signal_name='AIEM_OPEN_ALERT' AND ticker='DAILY_SUMMARY'",
                (today,)
            )
            _already_fired = bool(_k1.fetchone())
            _c1.close()
        except Exception as _e1:
            log.warning(f"[catchup] DB state check: {_e1} — skipping")
            return

        if _already_fired:
            log.info("[catchup] today's open alert already sent — nothing to do")
            return

        # Preserve existing data: SUCCEEDED slot + predictions → do not overwrite
        if _succeeded > 0 and _pred_count > 0:
            log.info(
                f"[catchup] {_succeeded} SUCCEEDED slot(s), {_pred_count} predictions intact — "
                f"skipping emergency scan to preserve existing data"
            )
            if 9*60 + 30 <= now_mins <= 15*60 + 30:
                log.info(f"[catchup] Firing open_watcher at {now_et.strftime('%H:%M ET')}")
                try:
                    aiem_open_watcher()
                except Exception as _ow0:
                    log.error(f"[catchup] open_watcher error: {_ow0}")
            return

        # Before warmup window (6:55 AM): nothing to do yet
        if now_mins < 6*60 + 55:
            log.info(f"[catchup] Before 6:55 AM warmup window — returning")
            return

        # Acquire PostgreSQL advisory lock (prevents race with GH Actions / watchdog)
        _lconn = None; _lheld = False
        try:
            _lconn = _db(); _lcur = _lconn.cursor()
            _lcur.execute("SELECT pg_try_advisory_lock(%s)", (_LOCK_KEY,))
            _lheld = _lcur.fetchone()[0]
        except Exception as _le:
            log.warning(f"[catchup] advisory lock: {_le}")

        if not _lheld:
            if _lconn:
                try: _lconn.close()
                except: pass
            log.info("[catchup] advisory lock held by another executor — skipping")
            return

        # Claim this slot in morning_scan_runs (ON CONFLICT: reclaim if stale/failed)
        _slot_str = now_et.strftime("%H:%M")
        _run_key  = f"premarket_scan:{today_str}:{_slot_str}"
        # Stage 6: SCAN_RUN_CREATED — write-before-work, before morning_scan_runs INSERT
        try:
            import aiem_pipeline_checkpoints as _s6chkp
            _s6_db = __import__('os').environ.get("DATABASE_URL", "")
            _s6_tid = _s6chkp.get_or_set_trace_id(today, _s6_db)
            _s6chkp.chk(_s6_tid, "SCAN_RUN_CREATED",
                         {"run_key": _run_key, "market_date": str(today)}, _s6_db)
        except Exception as _s6e:
            log.error(f"[checkpoint] SCAN_RUN_CREATED failed: {_s6e}")
        try:
            _lcur.execute("""
                INSERT INTO morning_scan_runs
                    (run_key, job_name, market_date, scheduled_slot, owner,
                     status, started_at, lease_expires_at, attempt_count)
                VALUES (%s,'premarket_scan',%s,%s,'aiem-process-startup',
                        'RUNNING',NOW(),NOW()+INTERVAL '10 minutes',1)
                ON CONFLICT (run_key) DO UPDATE
                    SET status='RUNNING', started_at=NOW(),
                        lease_expires_at=NOW()+INTERVAL '10 minutes',
                        attempt_count=morning_scan_runs.attempt_count+1,
                        owner='aiem-process-startup'
                    WHERE morning_scan_runs.status IN ('PENDING','FAILED')
                       OR morning_scan_runs.lease_expires_at < NOW()
            """, (_run_key, today, _slot_str))
            _lconn.commit()
        except Exception as _ce:
            log.warning(f"[catchup] lease claim: {_ce}")

        log.info(
            f"[catchup] {_pred_count} predictions / {_succeeded} SUCCEEDED slots for {today_str} "
            f"at {now_et.strftime('%H:%M ET')} — running warmup + premarket scan "
            f"(run_key={_run_key})"
        )

        _scan_err = None; _result_n = 0
        try:
            aiem_warmup()
        except Exception as _we:
            log.error(f"[catchup] warmup: {_we}"); _scan_err = str(_we)
        try:
            _result_n = aiem_premarket_scan() or 0
        except Exception as _se:
            log.error(f"[catchup] premarket_scan: {_se}"); _scan_err = str(_se)

        # Release advisory lock
        try:
            _lcur.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_KEY,))
            _lconn.commit(); _lconn.close()
        except Exception as _ule:
            log.warning(f"[catchup] lock release: {_ule}")
            try: _lconn.close()
            except: pass

        # Update morning_scan_runs with final status
        try:
            _c2 = _db(); _k2 = _c2.cursor()
            _k2.execute(
                "SELECT COUNT(*) FROM aiem_process_predictions WHERE prediction_date=%s",
                (today,)
            )
            _final_n = _k2.fetchone()[0]
            _fin_st  = "SUCCEEDED" if _final_n > 0 else "FAILED"
            _k2.execute("""
                UPDATE morning_scan_runs
                SET status=%s, completed_at=NOW(), result_count=%s, error=%s
                WHERE run_key=%s
            """, (_fin_st, _final_n, _scan_err, _run_key))
            _c2.commit(); _c2.close()
            log.info(f"[catchup] {_run_key} → {_fin_st} ({_final_n} predictions)")
        except Exception as _upe:
            log.warning(f"[catchup] status update: {_upe}")

        # Fire open_watcher if in open window
        if 9*60 + 30 <= now_mins <= 15*60 + 30:
            log.info(f"[catchup] Firing open_watcher at {now_et.strftime('%H:%M ET')}")
            try:
                aiem_open_watcher()
            except Exception as _ow2:
                log.error(f"[catchup] open_watcher: {_ow2}")
        else:
            log.info(f"[catchup] Pre-open done at {now_et.strftime('%H:%M ET')} — "
                     f"open_watcher scheduled at 9:30 AM")

    import threading as _ct
    _ct.Thread(target=_startup_full_catchup, daemon=True, name="startup-full-catchup").start()

    # 4:30 PM — grade T1 outcomes
    sched.add_job(aiem_grade_outcomes,
                  CronTrigger(day_of_week="mon-fri", hour=16, minute=30, timezone=ET),
                  id="aiem_grade_outcomes", replace_existing=True)

    # 4:35 PM — grade T3 / T5 outcomes
    sched.add_job(aiem_grade_t3_t5,
                  CronTrigger(day_of_week="mon-fri", hour=16, minute=35, timezone=ET),
                  id="aiem_grade_t3_t5", replace_existing=True)

    # 4:45 PM — find missed runners (stocks that ran but AIEM didn't pick)
    sched.add_job(aiem_find_missed_runners,
                  CronTrigger(day_of_week="mon-fri", hour=16, minute=45, timezone=ET),
                  id="aiem_find_missed_runners", replace_existing=True)

    # 5:00 PM — pattern gap analysis (why did we miss?)
    sched.add_job(aiem_pattern_gap_analysis,
                  CronTrigger(day_of_week="mon-fri", hour=17, minute=0, timezone=ET),
                  id="aiem_pattern_gap_analysis", replace_existing=True)

    # 5:10 PM — capture top 100 winners/losers per market cap tier (feeds discovery engine)
    def _daily_tiered_movers_job():
        try:
            from daily_market_movers import run_daily_tiered_movers_job
            api_key = os.environ.get("POLYGON_API_KEY", "")
            result  = run_daily_tiered_movers_job(top_n=100, api_key=api_key)
            log.info("[tiered-movers] %s", result)
        except Exception as _e:
            log.error("[tiered-movers] error: %s", _e)

    sched.add_job(_daily_tiered_movers_job,
                  CronTrigger(day_of_week="mon-fri", hour=17, minute=10, timezone=ET),
                  id="aiem_daily_tiered_movers", replace_existing=True)

    # 5:15 PM — write signal discoveries to DB
    sched.add_job(aiem_write_signal_discoveries,
                  CronTrigger(day_of_week="mon-fri", hour=17, minute=15, timezone=ET),
                  id="aiem_write_signal_discoveries", replace_existing=True)

    # 5:30 PM — per-tier discovery cycle (nano/small/mid/large pattern search)
    def _discovery_cycle_job():
        try:
            from aiem_discovery_engine import get_discovery_engine
            eng    = get_discovery_engine()
            result = eng.run_tiered_wl_cycle()
            log.info("[discovery-cycle] %s", result)
        except Exception as _e:
            log.error("[discovery-cycle] error: %s", _e)

    sched.add_job(_discovery_cycle_job,
                  CronTrigger(day_of_week="mon-fri", hour=17, minute=30, timezone=ET),
                  id="aiem_discovery_cycle", replace_existing=True)

    # 6:00 PM — nightly learn (update trust weights, promote hypotheses)
    sched.add_job(aiem_nightly_learn,
                  CronTrigger(day_of_week="mon-fri", hour=18, minute=0, timezone=ET),
                  id="aiem_nightly_learn", replace_existing=True)

    # NIGHTLY SELF-EXIT REMOVED (root cause of five consecutive morning failures).
    # os._exit(0) at 3:02 AM caused Replit to fail restarting the process for
    # 3–6 hours on multiple days. The process now stays alive continuously.
    # Recovery is handled by: GH Actions (every 5 min), the aiem-telegram morning
    # watchdog (every 5 min 6:50–10:00 AM ET), and the existing aiem-process watchdog.
    log.info("[nightly-reset] self-exit REMOVED — process stays alive continuously")

    # ── Deep-ITM Options Probability scan (AIEM-owned, fully independent) ───
    # Full ~6,635-ticker options-active universe, pre-filtered to
    # avg_vol_30d>=2M via polygon_market_daily (zero extra API calls).
    # Rotated across 6 segments/day at :35 past hour (10:35–15:35 ET),
    # offset from the unusual-calls scan so the two jobs never compete for
    # the Tradier rate limiter at the same moment.
    # 4:10 PM digest sends ONE Telegram message with the day's top-20.
    aiem_optprob.init_optprob_table(DB_URL)
    aiem_firstcandle.init_firstcandle_table(DB_URL)

    def _optprob_scan_job(label: str = "segment"):
        aiem_optprob.run_optprob_deep_itm_scan(
            db_url=DB_URL,
            tg_send=_tg_send,
            cursor_state=_optprob_cursor_state,
            label=label,
        )

    def _optprob_digest_job():
        aiem_optprob.run_optprob_daily_digest(db_url=DB_URL, tg_send=_tg_send)

    for _h in (10, 11, 12, 13, 14, 15):
        sched.add_job(
            _optprob_scan_job,
            CronTrigger(day_of_week="mon-fri", hour=_h, minute=35, timezone=ET),
            id=f"aiem_optprob_scan_{_h}",
            kwargs={"label": f"{_h}:35"},
            replace_existing=True,
        )

    sched.add_job(
        _optprob_digest_job,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=10, timezone=ET),
        id="aiem_optprob_digest",
        replace_existing=True,
    )
    log.info("[aiem_optprob] 6 segment scans (10:35-15:35 ET) + digest (16:10 ET) scheduled")

    # ── First-candle capture + outcome fill ───────────────────────────────────
    # 9:36 AM: first 5-min candle has just closed — capture OHLCV for every
    #          morning gap-up stock so AIEM can build the intraday dataset.
    # 4:45 PM: market settled — fill day_close / day_win on today's rows.
    def _firstcandle_capture_job():
        aiem_firstcandle.run_firstcandle_capture(db_url=DB_URL)

    def _firstcandle_outcome_job():
        aiem_firstcandle.run_firstcandle_outcome_fill(db_url=DB_URL)

    sched.add_job(
        _firstcandle_capture_job,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=36, timezone=ET),
        id="aiem_firstcandle_capture",
        replace_existing=True,
    )
    sched.add_job(
        _firstcandle_outcome_job,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=45, timezone=ET),
        id="aiem_firstcandle_outcome",
        replace_existing=True,
    )
    log.info("[aiem_firstcandle] first-candle capture (9:36 AM) + outcome fill (4:45 PM) scheduled")

    # ── Admin HTTP server (port 5055) for manual scan triggers ──────────────
    def _run_manual_scan(run_id, trigger_source="gha"):
        # Non-blocking acquire — if a scan is already running, skip safely
        if not _PREMARKET_SCAN_LOCK.acquire(blocking=False):
            log.info(f"[run_id={run_id}] premarket scan already running — skipped")
            with _STATE_LOCK:
                _LAST_SCAN.update({"run_id": run_id, "status": "skipped",
                                    "reason": "scan_already_running"})
            _db_log_scan(run_id, trigger_source, "skipped",
                          error_message="scan_already_running")
            return

        started_at = datetime.now(ET)
        log.info(f"[run_id={run_id}] warmup + premarket scan starting")
        _db_log_scan(run_id, trigger_source, "running", started_at=started_at)
        freshness_date, universe_size, candidate_count = None, 0, 0
        try:
            wu = aiem_warmup()
            if wu:
                freshness_date, universe_size = wu
            candidate_count = aiem_premarket_scan() or 0
            completed_at = datetime.now(ET)
            log.info(f"[run_id={run_id}] scan complete — candidates={candidate_count} "
                     f"freshness={freshness_date} universe={universe_size}")
            with _STATE_LOCK:
                _LAST_SCAN.update({
                    "run_id":                run_id,
                    "status":               "success",
                    "trigger_source":       trigger_source,
                    "started_at":           started_at.isoformat(),
                    "completed_at":         completed_at.isoformat(),
                    "source_freshness_date": str(freshness_date) if freshness_date else None,
                    "universe_size":         universe_size,
                    "candidate_count":       candidate_count,
                })
            _db_log_scan(run_id, trigger_source, "success",
                          started_at, completed_at, freshness_date, candidate_count)
        except Exception as _e:
            _err = str(_e)[:500]
            log.error(f"[run_id={run_id}] scan error: {_err}")
            with _STATE_LOCK:
                _LAST_SCAN.update({"run_id": run_id, "status": "error", "error": _err})
            _db_log_scan(run_id, trigger_source, "error",
                          started_at, datetime.now(ET), error_message=_err)
        finally:
            _PREMARKET_SCAN_LOCK.release()

    def _admin_server():
        # ── Ensure the scan-run ledger table exists ──────────────────────
        try:
            _sc = _db()
            _scu = _sc.cursor()
            _scu.execute("""
                CREATE TABLE IF NOT EXISTS premarket_scan_runs (
                    id                    SERIAL      PRIMARY KEY,
                    run_id                TEXT        NOT NULL UNIQUE,
                    trigger_source        TEXT        NOT NULL DEFAULT 'gha',
                    triggered_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    started_at            TIMESTAMPTZ,
                    completed_at          TIMESTAMPTZ,
                    status                TEXT        NOT NULL DEFAULT 'triggered',
                    source_freshness_date DATE,
                    candidate_count       INTEGER,
                    error_message         TEXT
                )
            """)
            _sc.commit()
            _sc.close()
            log.info("premarket_scan_runs table ready")
        except Exception as _ste:
            log.warning(f"premarket_scan_runs init (non-fatal): {_ste}")

        def _run_manual_grade():
            log.info("admin: manual grade triggered")
            try:
                aiem_grade_outcomes()
                aiem_grade_t3_t5()
                log.info("admin: manual grade complete")
            except Exception as _e:
                log.error(f"admin: manual grade error: {_e}")

        # Register callables in the shared registry so the early health server
        # (already bound to port 5055) can dispatch them. No second bind needed.
        _SCAN_FN_REGISTRY["run_scan"]   = _run_manual_scan
        _SCAN_FN_REGISTRY["run_grade"]  = _run_manual_grade
        _SCAN_FN_REGISTRY["run_warmup"] = aiem_warmup
        log.info("Admin trigger functions registered in _SCAN_FN_REGISTRY — :5055 ready")

    threading.Thread(target=_admin_server, daemon=True).start()
    log.info("Admin trigger server listening on :5055")

    # ── aiem-process heartbeat — writes to DB every 3 min ─────────────────────
    # External monitors (Telegram notifier) query aiem_process_heartbeat to detect
    # a hung or dead process even when pgrep shows the PID as alive.
    def _ensure_heartbeat_table():
        try:
            conn = _db()
            try:
                cur = conn.cursor()
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS aiem_process_heartbeat (
                        id   SERIAL PRIMARY KEY,
                        ts   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        pid  INTEGER NOT NULL DEFAULT 0
                    )
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_aiem_process_heartbeat_ts
                    ON aiem_process_heartbeat (ts DESC)
                """)
                conn.commit()
            finally:
                conn.close()
            log.info("[heartbeat] aiem_process_heartbeat table ready")
        except Exception as _hte:
            log.warning(f"[heartbeat-init] table create failed (non-fatal): {_hte}")

    def _heartbeat_writer():
        _INTERVAL = 180   # 3 min
        time.sleep(15)    # let scheduler settle first
        while True:
            try:
                conn = _db()
                try:
                    cur = conn.cursor()
                    cur.execute(
                        "INSERT INTO aiem_process_heartbeat (ts, pid) VALUES (NOW(), %s)",
                        (os.getpid(),)
                    )
                    conn.commit()
                    # NOTE: DELETE removed — aiem_deletion_guard blocks it and rolls back
                    # the INSERT in the same transaction. INSERT-only keeps rows accumulating
                    # (indexed on ts DESC; monitor queries use MAX(ts) and remain fast).
                finally:
                    conn.close()
            except Exception as _hwe:
                log.warning(f"[heartbeat] write failed (non-fatal): {_hwe}")
            time.sleep(_INTERVAL)

    _ensure_heartbeat_table()
    threading.Thread(target=_heartbeat_writer, daemon=True,
                     name="aiem-process-heartbeat").start()
    log.info("[heartbeat] writing every 3 min → aiem_process_heartbeat")

    sched.start()

    log.info("Scheduler running — 18 jobs:")
    log.info("  6:55 AM               warm-up (Polygon full snapshot)")
    log.info("  7:00–9:15 every 15m   premarket scan + funnel")
    log.info("  9:30–10:30 every  5m  open watcher + Telegram alert (primary)")
    log.info("  9:36 AM               first-candle capture (gap-up universe, Tradier 5-min bar)")
    log.info("  11:00–3:30 every 15m  open watcher catch-up net (idempotent)")
    log.info("  10:35–15:35 every hr  deep-ITM options-prob scan (6 segments, own Tradier chain)")
    log.info("  4:10 PM               deep-ITM options-prob digest (top-20 Telegram)")
    log.info("  4:30 PM               grade T1 outcomes")
    log.info("  4:35 PM               grade T3/T5 outcomes")
    log.info("  4:45 PM               first-candle outcome fill (day_close / day_win)")
    log.info("  4:45 PM               find missed runners")
    log.info("  5:00 PM               pattern gap analysis")
    log.info("  5:15 PM               write signal discoveries")
    log.info("  6:00 PM               nightly learn")

    try:
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        log.info("Shutting down…")
        sched.shutdown(wait=False)


if __name__ == "__main__":
    main()
