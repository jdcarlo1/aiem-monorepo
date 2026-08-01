"""
daily_scheduler.py - standalone process that runs the AIEM Probability
Engine's daily job (score new candidates, backfill elapsed outcomes, rank
today's top N) on its own schedule.

This is a DELIBERATELY separate process from main.py / aiem_autonomous.py.
Per the isolation contract for this package: it must never be imported by,
or share a scheduler/thread pool with, the live trading app. It only reads
ai_short_calls_log / polygon_market_daily (read-only) and writes to the two
tables this package owns (aiem_probability_engine_predictions and
aiem_probability_engine_daily_picks). If this process crashes or is stopped,
nothing about live scanning, alerts, or paper trading is affected.

Schedule: once daily at 10:30 AM ET plus a daily outcome backfill pass
right after. Timing is deliberate, not arbitrary: the source data this
engine ranks (ai_short_calls_log) is written once a day by main.py's
"AI short calls" job at 10:15 AM ET - which itself waits until 45 minutes
after the 9:30 AM open before scoring, because several of the 9
conviction layers (rvol, dark_pool_score, gamma_score, vol_oi,
sector_heat_score) are options/volume/dark-pool signals that are
thin-to-nonexistent premarket and only become meaningful once real
intraday trading volume and options order flow accumulate. Running this
job any earlier (it used to be 9:20 AM, BEFORE that 10:15 AM source job)
meant it would score an empty/yesterday's candidate list every morning.
10:30 AM leaves a 15-minute buffer after 10:15 AM for that job to finish
writing today's picks. Also runs once immediately on startup so a fresh
deploy / workflow restart doesn't wait until the next 10:30 AM to have
data (this startup run may legitimately find nothing if it happens
before 10:15 AM that same day - that's expected, not a bug).
"""
import datetime
import http.server
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── Socket-liveness default for every psycopg2.connect() in this process ────
# This package's modules (data_snapshot.py, reports.py, daily_picks.py,
# live_query.py, context.py, pit_metrics.py, pit_correction.py) all call raw
# psycopg2.connect(DB_URL) with no keepalives. connect_timeout alone only
# bounds the initial TCP/SSL handshake, not a recv()/send() on an already-
# established connection — if the DB's TCP path dies silently (no clean
# FIN/RST), a raw connect() can block this process's scheduler thread
# forever. This mirrors the fix applied to main.py / aiem_process.py /
# aiem_telegram_notifier.py (see .agents/memory/db-pool-liveness-watchdog.md).
# Patching the psycopg2 module object here (the process entry point, before
# the daily job actually runs) covers every call site in this package since
# they all do `import psycopg2; psycopg2.connect(...)` — same shared module
# object, attribute resolved at call time, not import time.
import psycopg2 as _pg_patch
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
_pg_patch.connect = _make_safe_pg_connect(_pg_patch.connect)
del _pg_patch, _make_safe_pg_connect

# ── Minimal health server ─────────────────────────────────────────────────────
# GCE deployment probes GET /health on startup. This process is a pure batch
# scheduler with no Flask — a lightweight HTTPServer thread satisfies the probe
# without adding any runtime dependency.
_HEALTH_PORT = int(os.environ.get("PROB_ENGINE_PORT", "5056"))


class _HealthHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'{"status":"ok","service":"probability-engine-scheduler"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_a):  # silence access logs
        pass


def _start_health_server() -> None:
    try:
        srv = http.server.HTTPServer(("0.0.0.0", _HEALTH_PORT), _HealthHandler)
        print(f"[daily_scheduler] health server on port {_HEALTH_PORT}", flush=True)
        srv.serve_forever()
    except Exception as _he:
        print(f"[daily_scheduler] health server error: {_he}", flush=True)


threading.Thread(target=_start_health_server, daemon=True, name="health-server").start()

from daily_picks import run_daily_job
from reports import backfill_outcomes

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:
    _ET = datetime.timezone.utc

RUN_HOUR_ET = 10
RUN_MINUTE_ET = 30
CHECK_INTERVAL_SEC = 60


def _now_et() -> datetime.datetime:
    return datetime.datetime.now(_ET)


def _run_once(reason: str) -> None:
    print(f"[daily_scheduler] running job now ({reason}) at {_now_et().isoformat()}", flush=True)
    try:
        run_daily_job(n=10)
    except Exception as e:
        print(f"[daily_scheduler] run_daily_job failed: {e}", flush=True)
    try:
        updated = backfill_outcomes(batch_limit=500)
        print(f"[daily_scheduler] backfill_outcomes updated {updated} rows", flush=True)
    except Exception as e:
        print(f"[daily_scheduler] backfill_outcomes failed: {e}", flush=True)


def main() -> None:
    print("[daily_scheduler] starting - AIEM Probability Engine daily job runner "
          f"(scheduled {RUN_HOUR_ET:02d}:{RUN_MINUTE_ET:02d} ET, isolated from main.py)", flush=True)

    _run_once("startup catch-up")
    last_run_date = _now_et().date()

    while True:
        time.sleep(CHECK_INTERVAL_SEC)
        now = _now_et()
        if now.weekday() >= 5:
            continue
        if now.date() == last_run_date:
            continue
        if now.hour > RUN_HOUR_ET or (now.hour == RUN_HOUR_ET and now.minute >= RUN_MINUTE_ET):
            _run_once("scheduled 9:20 AM ET")
            last_run_date = now.date()


if __name__ == "__main__":
    main()
