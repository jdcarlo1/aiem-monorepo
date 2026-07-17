#!/usr/bin/env python3
"""
AIEM Market-Hours Watchdog
============================
Standalone — zero imports from the main codebase.
Runs from GitHub Actions every minute during market hours.

Checks four ordered checkpoints then decides whether to trigger the
backup runner.  Never executes trades.  Every check is logged.

Required env vars:
  DATABASE_URL          Postgres connection string
  TELEGRAM_BOT_TOKEN    Telegram bot token
  TELEGRAM_CHAT_ID      Telegram chat ID

Optional (forwarded to backup runner if recovery is triggered):
  POLYGON_API_KEY
  TRADIER_API_TOKEN_2
  TRIGGER_SOURCE        Default: watchdog_github_actions
"""
import json, logging, os, subprocess, sys, time, uuid
import urllib.request
from datetime import date, datetime, timezone, timedelta

try:
    import psycopg2
except ImportError:
    print("psycopg2 not installed — run: pip install psycopg2-binary", file=sys.stderr)
    sys.exit(1)

# ─── Config ───────────────────────────────────────────────────────────────────
_DB_URL   = os.environ.get("DATABASE_URL", "")
_TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
_TG_CHAT  = os.environ.get("TELEGRAM_CHAT_ID", "")
_TRIGGER  = os.environ.get("TRIGGER_SOURCE", "watchdog_github_actions")

# Eastern Time (EDT = UTC-4; adjust to UTC-5 Nov-Mar if needed)
_ET       = timezone(timedelta(hours=-4))

# ─── Checkpoint windows (ET, 24-h) ────────────────────────────────────────────
# Each checkpoint is only evaluated after its earliest possible completion time.
# If the clock is before the window, the check is SKIP (not FAIL) — no alert.
_WINDOWS = {
    "vm_heartbeat":   (9, 35),   # VM/scheduler alive — check all market hours
    "polygon_scan":   (9, 15),   # polygon_rvol_scan populated by 8:35 AM job
    "seed_9_40":      (9, 55),   # options_pipeline_jobs seeded by 9:45 AM job
    "pipeline_9_45":  (10, 10),  # all jobs DONE by ~10:05 AM
}

# Only trigger recovery between these ET hours (inclusive start)
_RECOVERY_OPEN_H,  _RECOVERY_OPEN_M  = 9, 55
_RECOVERY_CLOSE_H, _RECOVERY_CLOSE_M = 15, 0

# VM heartbeat considered stale if last_success older than this many minutes
_HB_STALE_MINUTES = 30

# Suppress duplicate recovery triggers within this many minutes
_RECOVERY_COOLDOWN_MINUTES = 25

# How many consecutive watchdog runs with pipeline FAIL before alerting
# (avoids noisy alerts in the seconds right after the window opens)
_FAIL_ALERT_THRESHOLD = 2

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)sZ] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S"
)
log = logging.getLogger("aiem-watchdog")


# ─── Utilities ────────────────────────────────────────────────────────────────

def _tg(text: str, silent: bool = False):
    """Send a Telegram message.  Never raises."""
    if not _TG_TOKEN or not _TG_CHAT:
        return
    url  = f"https://api.telegram.org/bot{_TG_TOKEN}/sendMessage"
    body = json.dumps({
        "chat_id":              _TG_CHAT,
        "text":                 text,
        "parse_mode":           "HTML",
        "disable_notification": silent,
    }).encode()
    req = urllib.request.Request(url, data=body,
                                  headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception as e:
        log.warning(f"[telegram] send failed: {e}")


def _db():
    return psycopg2.connect(_DB_URL, connect_timeout=8)


def _after(h: int, m: int, now_et: datetime) -> bool:
    return (now_et.hour, now_et.minute) >= (h, m)


def _in_recovery_window(now_et: datetime) -> bool:
    open_ok  = (now_et.hour, now_et.minute) >= (_RECOVERY_OPEN_H,  _RECOVERY_OPEN_M)
    close_ok = (now_et.hour, now_et.minute) <= (_RECOVERY_CLOSE_H, _RECOVERY_CLOSE_M)
    return open_ok and close_ok


# ─── Checkpoint queries ───────────────────────────────────────────────────────

class CheckResult:
    def __init__(self, name: str, status: str, detail: str = ""):
        self.name   = name    # checkpoint label
        self.status = status  # PASS | FAIL | SKIP
        self.detail = detail  # human-readable context

    def __str__(self):
        icon = {"PASS": "✅", "FAIL": "❌", "SKIP": "⏭"}.get(self.status, "❓")
        return f"{icon} {self.name}: {self.status}  {self.detail}"


def check_vm_heartbeat(conn, today: date, now_et: datetime) -> CheckResult:
    name = "vm_heartbeat"
    if not _after(*_WINDOWS[name], now_et):
        return CheckResult(name, "SKIP", "before window")
    stale_ts = datetime.utcnow().replace(tzinfo=timezone.utc) - timedelta(minutes=_HB_STALE_MINUTES)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT job_name, last_success, consecutive_failures
                FROM job_heartbeats
                WHERE job_name = 'options_pipeline_scheduler'
            """)
            row = cur.fetchone()
    except Exception as e:
        return CheckResult(name, "FAIL", f"DB error: {e}")

    if not row:
        return CheckResult(name, "FAIL", "options_pipeline_scheduler row missing from job_heartbeats")
    job_name, last_success, consec_fail = row
    if last_success is None:
        return CheckResult(name, "FAIL", "last_success is NULL — scheduler never wrote a heartbeat")
    last_success_utc = last_success.replace(tzinfo=timezone.utc) if last_success.tzinfo is None else last_success
    age_min = round((datetime.now(timezone.utc) - last_success_utc).total_seconds() / 60, 1)
    if last_success_utc < stale_ts:
        return CheckResult(name, "FAIL",
            f"last_success={last_success_utc.isoformat()} ({age_min} min ago) "
            f"— stale (threshold={_HB_STALE_MINUTES} min)")
    return CheckResult(name, "PASS",
        f"last_success {age_min} min ago  consecutive_failures={consec_fail}")


def check_polygon_scan(conn, today: date, now_et: datetime) -> CheckResult:
    name = "polygon_scan"
    if not _after(*_WINDOWS[name], now_et):
        return CheckResult(name, "SKIP", "before 09:15 ET window")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM polygon_rvol_scan WHERE scan_date = %s", (today,))
            n = cur.fetchone()[0]
    except Exception as e:
        return CheckResult(name, "FAIL", f"DB error: {e}")
    if n == 0:
        return CheckResult(name, "FAIL", f"polygon_rvol_scan has 0 rows for {today}")
    return CheckResult(name, "PASS", f"{n} rows in polygon_rvol_scan for {today}")


def check_seed(conn, today: date, now_et: datetime) -> CheckResult:
    name = "seed_9_40"
    if not _after(*_WINDOWS[name], now_et):
        return CheckResult(name, "SKIP", "before 09:55 ET window")
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) total,
                       COUNT(*) FILTER (WHERE status = 'PENDING') pending,
                       COUNT(*) FILTER (WHERE status = 'DONE')    done
                FROM options_pipeline_jobs
                WHERE scan_date = %s
            """, (today,))
            row = cur.fetchone()
    except Exception as e:
        return CheckResult(name, "FAIL", f"DB error: {e}")

    total, pending, done = row
    if total == 0:
        return CheckResult(name, "FAIL",
            f"0 rows in options_pipeline_jobs for {today} — seed never ran")
    return CheckResult(name, "PASS",
        f"total={total}  done={done}  pending={pending}")


def check_pipeline(conn, today: date, now_et: datetime) -> CheckResult:
    name = "pipeline_9_45"
    if not _after(*_WINDOWS[name], now_et):
        return CheckResult(name, "SKIP", "before 10:10 ET window")
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) total,
                       COUNT(*) FILTER (WHERE status = 'DONE')    done,
                       COUNT(*) FILTER (WHERE status = 'FAILED')  failed,
                       COUNT(*) FILTER (WHERE status IN ('PENDING','CLAIMED','EXECUTING')) still_open
                FROM options_pipeline_jobs
                WHERE scan_date = %s
            """, (today,))
            row = cur.fetchone()
    except Exception as e:
        return CheckResult(name, "FAIL", f"DB error: {e}")

    total, done, failed, still_open = row
    if total == 0:
        # Seed hasn't run yet — not a pipeline failure, covered by seed check
        return CheckResult(name, "SKIP", "no jobs seeded yet")
    if still_open > 0:
        return CheckResult(name, "FAIL",
            f"{still_open} jobs still PENDING/EXECUTING  done={done}  total={total}")
    if done < total and failed > 0:
        return CheckResult(name, "FAIL",
            f"{failed} jobs FAILED  done={done}  total={total}")
    return CheckResult(name, "PASS",
        f"all {total} jobs DONE  failed={failed}")


# ─── Recovery helpers ──────────────────────────────────────────────────────────

def _recovery_already_triggered(conn, today: date) -> tuple:
    """
    Return (already_ran: bool, detail: str).
    Checks daily_pipeline_runs for a COMPLETED or RUNNING backup trigger
    within the cooldown window.
    """
    try:
        with conn.cursor() as cur:
            # Check 1: primary completed — daily_pipeline_runs row
            cur.execute("""
                SELECT trigger_source, status, completed_at
                FROM daily_pipeline_runs
                WHERE run_date = %s AND status IN ('COMPLETED', 'RUNNING')
                ORDER BY created_at DESC LIMIT 1
            """, (today,))
            dpr_row = cur.fetchone()
            if dpr_row and len(dpr_row) >= 3:
                return True, f"daily_pipeline_runs: {dpr_row[1]} by {dpr_row[0]} at {dpr_row[2]}"

            # Check 2: all pipeline jobs independently done
            cur.execute("""
                SELECT COUNT(1) AS total,
                       SUM(CASE WHEN status='DONE' THEN 1 ELSE 0 END) AS done
                FROM options_pipeline_jobs WHERE scan_date = %s
            """, (today,))
            cnt_row = cur.fetchone()
            if cnt_row and len(cnt_row) >= 2:
                total_n = cnt_row[0] or 0
                done_n  = cnt_row[1] or 0
                if total_n > 0 and total_n == done_n:
                    return True, f"options_pipeline_jobs all DONE ({total_n} rows)"

            # Check 3: backup runner heartbeat within cooldown
            # Note: %% escapes the literal % in LIKE pattern (psycopg2 treats bare % as placeholder)
            cur.execute("""
                SELECT job_name, last_success FROM job_heartbeats
                WHERE job_name LIKE 'backup_runner_%%'
                  AND last_success >= NOW() - INTERVAL '25 minutes'
                LIMIT 1
            """)
            hb_row = cur.fetchone()
            if hb_row:
                return True, f"backup_runner heartbeat: {hb_row[0]} at {hb_row[1]}"

    except Exception as e:
        log.warning(f"[recovery_check] DB error: {e!r}")
    return False, ""


def _log_watchdog_run(conn, today: date, checks: list, recovery_triggered: bool):
    """Write a short heartbeat row so we can audit watchdog activity."""
    try:
        summary = " | ".join(f"{c.name}={c.status}" for c in checks)
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO job_heartbeats (job_name, last_success, last_attempt, last_error)
                VALUES ('watchdog_github_actions', NOW(), NOW(), %s)
                ON CONFLICT (job_name) DO UPDATE
                    SET last_success=NOW(),
                        last_attempt=NOW(),
                        last_error=EXCLUDED.last_error
            """, (f"recovery={recovery_triggered} | {summary}",))
        conn.commit()
    except Exception as e:
        log.warning(f"[heartbeat] write failed: {e}")


def _trigger_recovery(today: date, now_et: datetime, failed_checks: list) -> int:
    """
    Invoke the backup runner as a subprocess.
    The backup runner owns ALL trade logic and dedup — watchdog never touches it.
    Returns subprocess exit code.
    """
    script = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "aiem_backup_runner.py"
    )
    env = {**os.environ, "TRIGGER_SOURCE": _TRIGGER}
    log.info(f"[recovery] launching backup runner: {script}")
    try:
        result = subprocess.run(
            [sys.executable, script],
            env=env,
            timeout=600,       # 10 min hard cap
            capture_output=True,
            text=True,
        )
        # Echo every line from backup runner with [RECOVERY-LOG] prefix + timestamp
        for line in result.stdout.splitlines():
            log.info(f"[RECOVERY-LOG] {line}")
        if result.stderr:
            for line in result.stderr.splitlines():
                log.info(f"[RECOVERY-ERR] {line}")
        log.info(f"[recovery] backup runner exited: code={result.returncode}")
        return result.returncode
    except subprocess.TimeoutExpired:
        log.error("[recovery] backup runner timed out after 600s")
        return 1
    except Exception as e:
        log.error(f"[recovery] failed to launch backup runner: {e}")
        return 1


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    run_id   = uuid.uuid4().hex[:8]
    now_utc  = datetime.now(timezone.utc)
    now_et   = now_utc.astimezone(_ET)
    today    = now_et.date()

    log.info(f"[watchdog] run_id={run_id}  time={now_et.strftime('%H:%M:%S ET')}  date={today}")

    # ── Market-hours gate ────────────────────────────────────────────────────
    if now_et.weekday() >= 5:
        log.info("[watchdog] weekend — exit")
        sys.exit(0)

    market_open  = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now_et.replace(hour=16, minute=0,  second=0, microsecond=0)
    if not (market_open <= now_et <= market_close):
        log.info(f"[watchdog] outside market hours ({now_et.strftime('%H:%M ET')}) — exit")
        sys.exit(0)

    if not _DB_URL:
        log.error("[watchdog] DATABASE_URL not set — cannot run checks")
        sys.exit(1)

    # ── Run checkpoints ──────────────────────────────────────────────────────
    conn    = None
    checks  = []
    try:
        conn = _db()

        checks = [
            check_vm_heartbeat(conn, today, now_et),
            check_polygon_scan(conn, today, now_et),
            check_seed(conn,          today, now_et),
            check_pipeline(conn,      today, now_et),
        ]
    except Exception as e:
        log.error(f"[watchdog] DB connection failed: {e}")
        _tg(f"🚨 <b>WATCHDOG DB FAILURE</b>\nrun_id={run_id}\n"
            f"Cannot connect to database: {str(e)[:200]}")
        sys.exit(1)

    # ── Log all results ──────────────────────────────────────────────────────
    for c in checks:
        log.info(f"[check] {c}")

    failed       = [c for c in checks if c.status == "FAIL"]
    passed       = [c for c in checks if c.status == "PASS"]
    skipped      = [c for c in checks if c.status == "SKIP"]
    log.info(f"[watchdog] summary: {len(passed)} PASS  {len(failed)} FAIL  {len(skipped)} SKIP")

    recovery_triggered = False

    # ── Decide if recovery is needed ─────────────────────────────────────────
    seed_failed     = any(c.name == "seed_9_40"     and c.status == "FAIL" for c in checks)
    pipeline_failed = any(c.name == "pipeline_9_45" and c.status == "FAIL" for c in checks)
    vm_failed       = any(c.name == "vm_heartbeat"  and c.status == "FAIL" for c in checks)

    needs_recovery = (seed_failed or pipeline_failed) and _in_recovery_window(now_et)

    if needs_recovery:
        already_ran, detail = _recovery_already_triggered(conn, today)
        if already_ran:
            log.info(f"[watchdog] recovery not needed — {detail}")
        else:
            # ── Trigger recovery ─────────────────────────────────────────────
            fail_names = ", ".join(c.name for c in failed)
            log.warning(f"[watchdog] RECOVERY TRIGGERED  failed={fail_names}  "
                        f"time={now_et.strftime('%H:%M ET')}  run_id={run_id}")

            _tg(
                f"🚨 <b>WATCHDOG: RECOVERY TRIGGERED</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"run_id={run_id}  time={now_et.strftime('%H:%M ET')}\n"
                f"Failed checkpoints:\n"
                + "\n".join(f"  {c}" for c in failed) +
                f"\n\nLaunching backup pipeline runner…"
            )

            rc = _trigger_recovery(today, now_et, failed)
            recovery_triggered = True

            if rc != 0:
                _tg(
                    f"⚠️ <b>WATCHDOG: BACKUP RUNNER EXITED {rc}</b>\n"
                    f"run_id={run_id}\nCheck GitHub Actions logs for details."
                )
            else:
                log.info(f"[watchdog] recovery complete  rc={rc}")

    elif failed:
        # Alert on any failure that can't be auto-recovered.
        # Polygon scan: backup runner doesn't write rvol_scan — alert only.
        # VM heartbeat: can't restart Replit VM from GH Actions — alert only.
        non_recoverable = [c for c in failed
                           if c.name in ("vm_heartbeat", "polygon_scan")]
        if non_recoverable:
            _tg(
                f"⚠️ <b>WATCHDOG ALERT</b>  run_id={run_id}\n"
                f"time={now_et.strftime('%H:%M ET')}\n"
                + "\n".join(str(c) for c in non_recoverable) +
                f"\n\n(Alert-only — cannot auto-recover this checkpoint)"
            , silent=True)
    else:
        log.info("[watchdog] all active checkpoints PASS — no action needed")

    # ── Write watchdog heartbeat ──────────────────────────────────────────────
    _log_watchdog_run(conn, today, checks, recovery_triggered)

    if conn:
        try:
            conn.close()
        except Exception:
            pass

    log.info(f"[watchdog] done  run_id={run_id}  recovery_triggered={recovery_triggered}")
    sys.exit(0)


if __name__ == "__main__":
    main()
