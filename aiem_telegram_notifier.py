"""
aiem_telegram_notifier.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AIEM TELEGRAM NOTIFIER — READ-ONLY, always-on production process (FIX B).

PURPOSE
  Sends AIEM's own INDEPENDENT picks (Workstream D) to Telegram by READING
  the aiem_independent_picks table that main.py's
  _run_aiem_independent_scan() (9:20 AM ET) already wrote. These are picks
  AIEM reasoned to on its own from RAW Polygon data only (no pre-computed
  conviction/composite score is ever handed to AIEM for this run) - up to
  30 picks TOTAL combined across stocks and call options, AIEM's own split.
  This process NEVER scans and NEVER writes to aiem_independent_picks (or
  any other table) — it is a pure notifier, so it cannot race or collide
  with main.py, which remains the single canonical writer.

  Previously (through 2026-06-30) this process sent a 5-pick brief sourced
  from aiem_predictions (the website-scored candidates handed to AIEM).
  That was replaced on 2026-07-01 per explicit user direction: they want
  AIEM's own independently-reasoned picks, not picks derived from the
  website's pre-computed scores. If no rows exist in aiem_independent_picks
  for today, we fail closed: send a "data not ready" message and stop. We
  do not scan.

SCHEDULE (Eastern Time)
  09:30  Mon-Fri   Independent picks brief — reads today's
                   aiem_independent_picks (written by main.py's 9:20 AM
                   independent scan), sends the Telegram message. (9:30
                   leaves a 10-minute buffer after the 9:20 canonical write
                   so the read never races the write.)

IDEMPOTENCY (no duplicate sends across restarts/redeploys)
  Owns one dedicated table it created itself, `aiem_notifier_log`
  (send_date DATE PRIMARY KEY). Before sending, it does an atomic
  INSERT ... ON CONFLICT DO NOTHING claim for today's ET date. Only the
  process that wins the claim sends. If a redeploy overlap causes two
  instances to be alive at 9:30 AM ET simultaneously, only one will
  successfully claim the row and send; the other logs "already sent
  today, skipping" and does nothing. This table is owned solely by this
  script — it is NOT aiem_independent_picks, so this does not reintroduce
  a two-writer collision.

FAILURE VISIBILITY
  If the Telegram send fails (bad token, network error) or the DB read
  fails, this is recorded in `aiem_notifier_log` (status column) and in
  /api/health's `last_run`. This process has no secondary delivery
  channel of its own — the existing uptime-monitor.py (SMTP-based) has
  been extended to check this service's /api/health after 9:35 AM ET on
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
                                              # gate as the real 9:30 job
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
def _fetch_todays_independent_picks():
    """Read-only: AIEM's own independent picks (Workstream D) for today,
    both pick_types combined, ordered by AIEM's own confidence score so the
    message shows its highest-conviction ideas first regardless of type."""
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
        cur = conn.cursor()
        cur.execute("""
            SELECT pick_type, ticker, confidence_score, rationale,
                   option_strike, option_expiry
            FROM aiem_independent_picks
            WHERE pick_date = %s
            ORDER BY confidence_score DESC NULLS LAST
            LIMIT 30
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
    (e.g. during a redeploy overlap) - via the 'in_progress' exclusion below,
    which blocks a second claimant for as long as a first attempt could
    plausibly still be in flight.

    Also allows a RETRY the same day after a definitive failure (bad token,
    Telegram API error, etc.) - a transient failure must not permanently
    lock out the rest of the day. Only a *confirmed success*
    ('sent_ok=True...' or 'sent_empty ok=True...') is treated as terminal."""
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
        _ensure_notifier_log_table(conn)
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO aiem_notifier_log (send_date, status, claimed_at)
            VALUES (%s, 'in_progress', now())
            ON CONFLICT (send_date) DO UPDATE
                SET status = 'in_progress', claimed_at = now()
                WHERE aiem_notifier_log.status NOT LIKE 'sent_ok=True%%'
                  AND aiem_notifier_log.status NOT LIKE 'sent_empty ok=True%%'
                  AND (
                        aiem_notifier_log.status <> 'in_progress'
                        OR aiem_notifier_log.claimed_at < now() - interval '10 minutes'
                      )
            RETURNING send_date
            """,
            (send_date,)
        )
        row = cur.fetchone()
        conn.commit()
        return row is not None
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


def send_independent_picks_brief():
    """09:30 AM ET Mon-Fri. Read-only w.r.t. aiem_independent_picks; sends
    AIEM's OWN independently-reasoned picks (Workstream D) - up to 30 total
    combined across stocks and call options, AIEM's own split, ranked by
    AIEM's own confidence score. Claim-before-send guarantees at most one
    send per ET calendar date even if two instances are alive at once."""
    today = date.today()

    try:
        won_claim = _claim_todays_send(today)
    except Exception as e:
        log.error(f"independent_picks_brief: idempotency claim failed (DB unreachable): {e}")
        _last_run.update(status=f"claim_db_error: {e}", timestamp=datetime.utcnow().isoformat())
        return

    if not won_claim:
        log.info(f"independent_picks_brief: {today} already sent (or in progress) by another instance - skipping duplicate")
        _last_run.update(status="skipped_duplicate", timestamp=datetime.utcnow().isoformat())
        return

    try:
        picks = _fetch_todays_independent_picks()
    except Exception as e:
        log.error(f"independent_picks_brief: DB read failed: {e}")
        _last_run.update(status=f"db_error: {e}", timestamp=datetime.utcnow().isoformat())
        _record_send_result(today, f"failed_db_error: {e}")
        return

    if not picks:
        ok = _tg_send(
            f"AIEM 9:30 AM: No independent picks found for {today.strftime('%a %b %d')} - "
            f"data not ready, or AIEM found nothing genuinely convincing today. "
            f"(Read-only notifier - did not run a scan.)"
        )
        log.warning(f"independent_picks_brief: no picks found in aiem_independent_picks for today (telegram sent={ok})")
        status = f"sent_empty ok={ok}"
        _last_run.update(status=status, timestamp=datetime.utcnow().isoformat())
        _record_send_result(today, status)
        return

    header = f"AIEM Independent Picks - {today.strftime('%a %b %d')} ({len(picks)})"
    sub = "AIEM's own reasoning on raw data - no pre-scored input"
    lines = []
    for i, (pick_type, ticker, conf, rationale, strike, expiry) in enumerate(picks, start=1):
        conf_txt = f"{float(conf):.1f}/10" if conf is not None else "?/10"
        short_reason = (rationale or "")[:55]
        if pick_type == "call_option":
            strike_txt = f"${float(strike):.2f}" if strike is not None else "?"
            exp_txt = expiry.strftime("%m/%d") if hasattr(expiry, "strftime") else (str(expiry) if expiry else "?")
            lines.append(f"#{i} ${ticker} CALL {strike_txt} exp {exp_txt} - {conf_txt} - {short_reason}")
        else:
            lines.append(f"#{i} ${ticker} STOCK - {conf_txt} - {short_reason}")

    # Telegram caps a single message at 4096 chars - chunk defensively so a
    # 30-pick list never silently truncates or fails to send.
    chunks, cur_chunk = [], [header, sub, "----------------------"]
    cur_len = sum(len(l) + 1 for l in cur_chunk)
    for line in lines:
        if cur_len + len(line) + 1 > 3500:
            chunks.append(cur_chunk)
            cur_chunk, cur_len = [], 0
        cur_chunk.append(line)
        cur_len += len(line) + 1
    if cur_chunk:
        chunks.append(cur_chunk)

    all_ok = True
    for idx, chunk in enumerate(chunks, start=1):
        text = "\n".join(chunk)
        if len(chunks) > 1:
            text = f"(part {idx}/{len(chunks)})\n" + text
        all_ok = _tg_send(text) and all_ok

    log.info(f"independent_picks_brief: sent={all_ok} picks={len(picks)} parts={len(chunks)}")
    status = f"sent_ok={all_ok}"
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
        send_independent_picks_brief,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=30, timezone=ET),
        id="aiem_independent_picks_notifier",
        replace_existing=True,
    )

    log.info("AIEM Telegram Notifier started (read-only, sends 9:30 AM ET Mon-Fri)")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    if "--once" in sys.argv:
        log.info("Manual test mode: sending one brief now, scheduler NOT started")
        send_independent_picks_brief()
    else:
        main()
