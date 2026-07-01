"""
aiem_telegram_notifier.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AIEM TELEGRAM NOTIFIER — READ-ONLY, always-on production process (FIX B).

PURPOSE
  Sends the AIEM morning picks to Telegram by READING the aiem_predictions
  table that main.py's _run_aiem_morning_scan() (Fix #12, 9:05 AM ET)
  already wrote. This process NEVER scans and NEVER writes to
  aiem_predictions (or any other table) — it is a pure notifier, so it
  cannot race or collide with main.py, which remains the single canonical
  writer of aiem_predictions.

  This intentionally does NOT reuse aiem_autonomous.py, because that file's
  aiem_morning_brief() falls back to running its own aiem_premarket_scan()
  (a second, independent writer to aiem_predictions) whenever it finds no
  rows — that fallback is exactly the two-writer collision this notifier
  exists to avoid. If no rows exist here, we fail closed: send a
  "data not ready" message and stop. We do not scan.

SCHEDULE (Eastern Time)
  09:15  Mon-Fri   Morning brief — reads today's aiem_predictions (written
                   by main.py at 9:05 AM), sends the Telegram message.
                   (9:15 leaves a 10-minute buffer after the 9:05 canonical
                   write so the read never races the write.)

IDEMPOTENCY (no duplicate sends across restarts/redeploys)
  Owns one dedicated table it created itself, `aiem_notifier_log`
  (send_date DATE PRIMARY KEY). Before sending, it does an atomic
  INSERT ... ON CONFLICT DO NOTHING claim for today's ET date. Only the
  process that wins the claim sends. If a redeploy overlap causes two
  instances to be alive at 9:15 AM ET simultaneously, only one will
  successfully claim the row and send; the other logs "already sent
  today, skipping" and does nothing. This table is owned solely by this
  script — it is NOT aiem_predictions, so this does not reintroduce the
  two-writer collision the notifier exists to avoid.

FAILURE VISIBILITY
  If the Telegram send fails (bad token, network error) or the DB read
  fails, this is recorded in `aiem_notifier_log` (status column) and in
  /api/health's `last_run`. This process has no secondary delivery
  channel of its own — the existing uptime-monitor.py (SMTP-based) has
  been extended to check this service's /api/health after 9:20 AM ET on
  weekdays and email the owner if the day's send did not succeed, since
  a healthy HTTP 200 from this service does not by itself prove today's
  message actually reached Telegram.

HEALTH CHECK
  GET /api/health  → {"status","scheduler","db","last_run","mode"}
  Bound to AIEM_HEALTH_PORT (default 5051).

REQUIRED ENV VARS
  DATABASE_URL                          — postgres connection string
  TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID — Telegram delivery

MANUAL TEST
  python3 aiem_telegram_notifier.py --once   # sends one brief immediately,
                                              # does not start the scheduler,
                                              # still goes through the same
                                              # claim-before-send idempotency
                                              # gate as the real 9:15 job
"""

import os
import sys
import json
import logging
import threading
import urllib.request
from datetime import date, datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

import psycopg2
import pytz
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [AIEM-NOTIFIER] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger('AIEM-NOTIFIER')

ET = pytz.timezone('America/New_York')
DATABASE_URL = os.environ.get('DATABASE_URL', '')
_HEALTH_PORT = int(os.environ.get('AIEM_HEALTH_PORT', '5051'))

_scheduler_ref = None
_last_run = {"status": "not_run_yet", "timestamp": None}


# ─────────────────────────────────────────────────────────────
# TELEGRAM SEND (no DB write side effects)
# ─────────────────────────────────────────────────────────────
def _tg_send(text: str) -> bool:
    """Send a message to the Telegram owner chat. Silent no-op when not configured."""
    token = "".join(os.environ.get("TELEGRAM_BOT_TOKEN", "").split())
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        log.warning("[telegram] TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not configured - skipping send")
        return False
    try:
        payload = json.dumps({"chat_id": chat_id, "text": text}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            ok = json.loads(r.read()).get("ok", False)
            if not ok:
                log.warning("[telegram] API responded without ok=true")
            return ok
    except Exception as e:
        log.warning(f"[telegram] send failed: {e}")
        return False


# ─────────────────────────────────────────────────────────────
# DB READ — SELECT ONLY. This process must never INSERT/UPDATE/DELETE
# aiem_predictions; main.py is the single canonical writer.
# ─────────────────────────────────────────────────────────────
def _fetch_todays_picks():
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
        cur = conn.cursor()
        cur.execute("""
            SELECT ticker, rank, confidence_score, signal_basis, reasoning, predicted_move
            FROM aiem_predictions
            WHERE prediction_date = %s
            ORDER BY rank ASC LIMIT 5
        """, (date.today(),))
        return cur.fetchall()
    finally:
        if conn:
            conn.close()


# ─────────────────────────────────────────────────────────────
# IDEMPOTENCY — this notifier owns this ONE small table itself.
# It is NOT aiem_predictions, so writing here does not reintroduce
# the two-writer collision this whole notifier exists to avoid.
# ─────────────────────────────────────────────────────────────
def _ensure_notifier_log_table(conn):
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS aiem_notifier_log (
            send_date  DATE PRIMARY KEY,
            claimed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            status     TEXT,
            updated_at TIMESTAMPTZ
        )
    """)
    conn.commit()


def _claim_todays_send(send_date) -> bool:
    """Atomic claim: returns True only if THIS call won the right to send today.
    Prevents duplicate sends if two process instances are alive at once
    (e.g. during a redeploy overlap)."""
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
        _ensure_notifier_log_table(conn)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO aiem_notifier_log (send_date, status) VALUES (%s, 'in_progress') "
            "ON CONFLICT (send_date) DO NOTHING",
            (send_date,)
        )
        conn.commit()
        won = cur.rowcount == 1
        return won
    finally:
        if conn:
            conn.close()


def _record_send_result(send_date, status: str):
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
        cur = conn.cursor()
        cur.execute(
            "UPDATE aiem_notifier_log SET status = %s, updated_at = now() WHERE send_date = %s",
            (status, send_date)
        )
        conn.commit()
    except Exception as e:
        log.warning(f"_record_send_result failed (non-fatal, claim already held): {e}")
    finally:
        if conn:
            conn.close()


def send_morning_brief():
    """09:15 AM ET Mon-Fri. Read-only w.r.t. aiem_predictions; sends what
    main.py already computed. Claim-before-send guarantees at most one
    send per ET calendar date even if two instances are alive at once."""
    today = date.today()

    try:
        won_claim = _claim_todays_send(today)
    except Exception as e:
        log.error(f"morning_brief: idempotency claim failed (DB unreachable): {e}")
        _last_run.update(status=f"claim_db_error: {e}", timestamp=datetime.utcnow().isoformat())
        return

    if not won_claim:
        log.info(f"morning_brief: {today} already sent (or in progress) by another instance - skipping duplicate")
        _last_run.update(status="skipped_duplicate", timestamp=datetime.utcnow().isoformat())
        return

    try:
        picks = _fetch_todays_picks()
    except Exception as e:
        log.error(f"morning_brief: DB read failed: {e}")
        _last_run.update(status=f"db_error: {e}", timestamp=datetime.utcnow().isoformat())
        _record_send_result(today, f"failed_db_error: {e}")
        return

    if not picks:
        ok = _tg_send(
            f"AIEM 9:15 AM: No picks found for {today.strftime('%a %b %d')} - "
            f"data not ready or market quiet. (Read-only notifier - did not run a scan.)"
        )
        log.warning(f"morning_brief: no picks found in aiem_predictions for today (telegram sent={ok})")
        status = f"sent_empty ok={ok}"
        _last_run.update(status=status, timestamp=datetime.utcnow().isoformat())
        _record_send_result(today, status)
        return

    lines = [f"AIEM Morning Picks - {today.strftime('%a %b %d')}",
             "----------------------"]
    for ticker, rank, conf, sig_basis, reasoning, predicted in picks:
        lines.append(f"#{rank} ${ticker}  {(conf or 0):.0f}/100")
        lines.append(f"   {predicted or ''}")
        short_reason = (reasoning or '')[:80]
        if short_reason:
            lines.append(f"   {short_reason}")
    lines.append("----------------------")
    lines.append("Watch open at 9:30 AM ET")

    ok = _tg_send("\n".join(lines))
    log.info(f"morning_brief: sent={ok} picks={len(picks)}")
    status = f"sent_ok={ok}"
    _last_run.update(status=status, timestamp=datetime.utcnow().isoformat())
    _record_send_result(today, status)


# ─────────────────────────────────────────────────────────────
# HEALTH SERVER — stdlib HTTPServer, GET-only, read-only DB probe
# ─────────────────────────────────────────────────────────────
def _fetch_today_notifier_log_status():
    """Cross-instance source of truth for 'did today's send actually happen',
    read from the shared DB row rather than this process's own in-memory
    _last_run (which would be wrong if a *different* instance won the claim
    and sent, e.g. during a redeploy overlap)."""
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=5)
        cur = conn.cursor()
        cur.execute(
            "SELECT status, updated_at FROM aiem_notifier_log WHERE send_date = %s",
            (date.today(),)
        )
        row = cur.fetchone()
        if not row:
            return {"status": "not_run_yet", "updated_at": None}
        return {"status": row[0], "updated_at": row[1].isoformat() if row[1] else None}
    except Exception as e:
        return {"status": f"log_lookup_error: {e}", "updated_at": None}
    finally:
        if conn:
            conn.close()


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not self.path.rstrip('/').endswith('/api/health'):
            self.send_response(404)
            self.end_headers()
            return

        health = {
            "status":       "ok",
            "timestamp":    datetime.utcnow().isoformat(),
            "scheduler":    "unknown",
            "db":           "unknown",
            "last_run":     _last_run,                          # this process's own memory
            "today_status": _fetch_today_notifier_log_status(), # shared DB truth - use this for monitoring
            "mode":         "read_only_notifier",
        }
        try:
            health["scheduler"] = "running" if (_scheduler_ref and _scheduler_ref.running) else "stopped"
        except Exception:
            health["scheduler"] = "error"
        try:
            conn = psycopg2.connect(DATABASE_URL, connect_timeout=5)
            conn.close()
            health["db"] = "connected"
        except Exception as e:
            health["db"] = f"error: {e}"
            health["status"] = "degraded"

        body = json.dumps(health).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # suppress per-request noise from stdlib logging


def _start_health_server():
    srv = HTTPServer(("0.0.0.0", _HEALTH_PORT), _HealthHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True, name="aiem-notifier-health")
    t.start()
    log.info(f"Health endpoint: http://0.0.0.0:{_HEALTH_PORT}/api/health")


def main():
    global _scheduler_ref
    if not DATABASE_URL:
        log.error("DATABASE_URL not set - exiting")
        return

    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
        _ensure_notifier_log_table(conn)
        log.info("aiem_notifier_log table ready")
    except Exception as e:
        log.error(f"could not ensure aiem_notifier_log table at startup: {e}")
    finally:
        if conn:
            conn.close()

    _start_health_server()

    scheduler = BlockingScheduler(timezone=ET)
    _scheduler_ref = scheduler

    scheduler.add_job(
        send_morning_brief,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=15, timezone=ET),
        id="aiem_morning_brief_notifier",
        replace_existing=True,
    )

    log.info("AIEM Telegram Notifier started (read-only, sends 9:15 AM ET Mon-Fri)")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    if "--once" in sys.argv:
        log.info("Manual test mode: sending one brief now, scheduler NOT started")
        send_morning_brief()
    else:
        main()
