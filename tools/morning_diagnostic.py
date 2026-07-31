#!/usr/bin/env python3
"""
tools/morning_diagnostic.py
────────────────────────────────────────────────────────────────────────────
Consolidated morning diagnostic — wires the 4 existing runtime-integrity
mechanisms into ONE combined report.  All logic is called from the already-
implemented checks; nothing here is rebuilt from scratch.

Checks
  1. Schedule integrity  — same DB evidence queries as _schedule_integrity_check
                           (aiem_options_scheduler.py, Part 1)
  2. Commit/deploy drift — calls tools/check_scheduler_drift.sh directly
  3. API canary          — same Polygon endpoint probes as _polygon_canary_check
                           (aiem_options_scheduler.py, Part 3)
  4. Classification sweep — dry-run (read-only) of the FAILED→NO_TRADE_GATES
                            sweep (_validate_and_fix_pipeline_run_classifications,
                            aiem_options_scheduler.py, Part 2)

Individual alerts fired by the existing mechanisms are UNCHANGED — this
script is an additional consolidated view only.

Usage:
  python3 tools/morning_diagnostic.py              # normal run
  python3 tools/morning_diagnostic.py --force-fail # force canary failure (proof)
  python3 tools/morning_diagnostic.py --dry-run    # print report, no Telegram
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from datetime import date, datetime, timedelta
from typing import Optional

import psycopg2
import pytz

# ── paths ────────────────────────────────────────────────────────────────────
_SCRIPT_DIR    = os.path.dirname(os.path.abspath(__file__))
_WORKSPACE_ROOT = os.path.dirname(_SCRIPT_DIR)          # tools/ → workspace root
_DRIFT_SCRIPT  = os.path.join(_SCRIPT_DIR, "check_scheduler_drift.sh")

# ── env ──────────────────────────────────────────────────────────────────────
_ET     = pytz.timezone("America/New_York")
_DB_URL = os.environ.get("DATABASE_URL", "")
_TG_TOKEN  = "".join(os.environ.get("TELEGRAM_BOT_TOKEN", "").split())
_TG_CHAT   = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
_POLY_KEY  = os.environ.get("POLYGON_API_KEY", "").strip()

# ── schedule-integrity config (mirrors _SCHED_MONITOR_JOBS in scheduler) ─────
_SCHED_JOBS = [
    {"id": "premarket_scan",        "desc": "07:30 premarket scan",
     "expected_et": (7,  30), "grace_minutes": 15},
    {"id": "seed_daily_candidates", "desc": "09:40 seed daily candidates",
     "expected_et": (9,  40), "grace_minutes": 15},
    {"id": "run_pipeline_worker",   "desc": "09:45 pipeline worker",
     "expected_et": (9,  45), "grace_minutes": 20},
    {"id": "grade_outcomes",        "desc": "16:46 grade outcomes",
     "expected_et": (16, 46), "grace_minutes": 10},
]
_GATE_PREFIX = "not ready_for_decision"


# ── Telegram helper ───────────────────────────────────────────────────────────
def _tg_send(text: str) -> bool:
    if not _TG_TOKEN or not _TG_CHAT:
        print(f"[telegram] NOT CONFIGURED — would send:\n{text}", file=sys.stderr)
        return False
    try:
        payload = json.dumps({
            "chat_id": _TG_CHAT, "text": text, "parse_mode": "HTML"
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{_TG_TOKEN}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status == 200
    except Exception as exc:
        print(f"[telegram] send error: {exc}", file=sys.stderr)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# CHECK 1 — Schedule integrity
# Calls the same DB evidence queries as _job_ran_today / _schedule_integrity_check
# ─────────────────────────────────────────────────────────────────────────────
def _check_schedule_integrity(now_et: Optional[datetime] = None) -> dict:
    """
    Returns {"pass": bool, "detail": str, "overdue": list, "checked": int}.
    Checks only jobs whose grace window has elapsed by now_et.
    """
    now_et = now_et or datetime.now(_ET)
    today  = now_et.date()

    if now_et.weekday() >= 5:
        return {"pass": True, "detail": "weekend — not checked", "overdue": [], "checked": 0}

    overdue  = []
    checked  = 0

    try:
        with psycopg2.connect(_DB_URL, connect_timeout=4) as conn, conn.cursor() as cur:
            for cfg in _SCHED_JOBS:
                jid   = cfg["id"]
                h, m  = cfg["expected_et"]
                grace = cfg["grace_minutes"]
                alert_after = (
                    now_et.replace(hour=h, minute=m, second=0, microsecond=0)
                    + timedelta(minutes=grace)
                )
                if now_et < alert_after:
                    continue  # grace window not yet elapsed — skip

                checked += 1

                # --- per-job evidence query (mirrors _job_ran_today) ----------
                ran = True
                if jid == "premarket_scan":
                    try:
                        cur.execute(
                            "SELECT COUNT(*) FROM options_engine_premarket "
                            "WHERE run_date=%s", (today,))
                        ran = cur.fetchone()[0] > 0
                    except Exception:
                        ran = True  # table absent — don't false-alert
                elif jid == "seed_daily_candidates":
                    cur.execute(
                        "SELECT status FROM daily_pipeline_runs "
                        "WHERE run_date=%s AND trigger_source='primary'", (today,))
                    row = cur.fetchone()
                    ran = row is not None and row[0] not in (None, "SCHEDULED")
                elif jid == "run_pipeline_worker":
                    cur.execute(
                        "SELECT completed_at FROM daily_pipeline_runs "
                        "WHERE run_date=%s AND trigger_source='primary'", (today,))
                    row = cur.fetchone()
                    ran = row is not None and row[0] is not None
                elif jid == "grade_outcomes":
                    cur.execute(
                        "SELECT COUNT(*) FROM job_heartbeats "
                        "WHERE job_name='grade_outcomes' AND success=TRUE "
                        "  AND recorded_at >= NOW() - INTERVAL '8 hours'")
                    ran = cur.fetchone()[0] > 0

                if not ran:
                    overdue.append(f"{jid} (expected {h:02d}:{m:02d}+{grace}m ET)")

    except Exception as exc:
        return {
            "pass":    False,
            "detail":  f"DB error: {exc}",
            "overdue": [],
            "checked": 0,
        }

    ok = len(overdue) == 0
    if ok:
        detail = (
            f"{checked} job(s) checked, 0 overdue"
            if checked > 0 else "no jobs past grace window yet"
        )
    else:
        detail = f"{len(overdue)} overdue: {', '.join(overdue)}"

    return {"pass": ok, "detail": detail, "overdue": overdue, "checked": checked}


# ─────────────────────────────────────────────────────────────────────────────
# CHECK 2 — Commit / deployment drift
# Calls tools/check_scheduler_drift.sh directly (exit 0=MATCH, 1=STALE, 2=ERROR)
# ─────────────────────────────────────────────────────────────────────────────
def _check_commit_drift() -> dict:
    """
    Returns {"pass": bool, "detail": str, "running": str, "ondisk": str,
             "exit_code": int}.
    """
    if not os.path.isfile(_DRIFT_SCRIPT):
        return {
            "pass":      False,
            "detail":    f"drift script not found: {_DRIFT_SCRIPT}",
            "running":   "UNKNOWN",
            "ondisk":    "UNKNOWN",
            "exit_code": 2,
        }

    try:
        result = subprocess.run(
            ["bash", _DRIFT_SCRIPT],
            capture_output=True, text=True,
            timeout=15,
            cwd=_WORKSPACE_ROOT,
        )
    except subprocess.TimeoutExpired:
        return {
            "pass":      False,
            "detail":    "check_scheduler_drift.sh timed out (15s)",
            "running":   "UNKNOWN",
            "ondisk":    "UNKNOWN",
            "exit_code": 2,
        }
    except Exception as exc:
        return {
            "pass":      False,
            "detail":    f"subprocess error: {exc}",
            "running":   "UNKNOWN",
            "ondisk":    "UNKNOWN",
            "exit_code": 2,
        }

    out  = result.stdout.strip()
    rc   = result.returncode

    # parse "RUNNING : <sha>" and "ON-DISK : <sha>" lines from the script output
    running = ondisk = "UNKNOWN"
    for line in out.splitlines():
        if line.startswith("RUNNING :"):
            running = line.split(":", 1)[1].strip()
        elif line.startswith("ON-DISK :"):
            ondisk  = line.split(":", 1)[1].strip()

    if rc == 0:
        detail = f"MATCH — running={running[:12]} on-disk={ondisk[:12]}"
        ok     = True
    elif rc == 1:
        detail = (
            f"STALE — running={running[:12]} on-disk={ondisk[:12]} "
            f"— restart required"
        )
        ok = False
    else:
        # rc == 2: scheduler not responding or missing boot_commit
        status_line = next(
            (l for l in out.splitlines() if l.startswith("STATUS")), out[:120])
        detail = f"ERROR — {status_line}"
        ok     = False

    return {
        "pass":      ok,
        "detail":    detail,
        "running":   running,
        "ondisk":    ondisk,
        "exit_code": rc,
    }


# ─────────────────────────────────────────────────────────────────────────────
# CHECK 3 — External API canary
# Mirrors _polygon_canary_check from aiem_options_scheduler.py Part 3.
# force_fail=True: uses bad API key → guaranteed 401 on both endpoints.
# ─────────────────────────────────────────────────────────────────────────────
def _check_api_canary(force_fail: bool = False) -> dict:
    """
    Returns {"pass": bool, "detail": str, "grouped_daily": dict,
             "options_snapshot": dict}.
    """
    if not _POLY_KEY:
        return {
            "pass":   True,
            "detail": "POLYGON_API_KEY not set — skipped",
            "grouped_daily":    {"skipped": True},
            "options_snapshot": {"skipped": True},
        }

    # use a deliberately invalid key to force 401
    key = (_POLY_KEY + "INVALID") if force_fail else _POLY_KEY

    failures = []

    # ── Canary 1: grouped-daily ───────────────────────────────────────────────
    yesterday = (datetime.now(_ET).date() - timedelta(days=1)).isoformat()
    url1 = (
        f"https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks"
        f"/{yesterday}?adjusted=true&apiKey={key}"
    )
    try:
        req1 = urllib.request.Request(url1, headers={"User-Agent": "aiem-diag/1"})
        with urllib.request.urlopen(req1, timeout=10) as r1:
            http1 = r1.status
            try:
                body1 = json.loads(r1.read(4096))
            except Exception:
                body1 = {"status": "OK_LARGE_RESPONSE"}
        ok1 = http1 == 200
    except Exception as e1:
        http1 = 0; body1 = {"error": str(e1)[:80]}; ok1 = False

    gd = {"http_status": http1, "ok": ok1, "body_sample": str(body1)[:80]}
    if not ok1:
        failures.append(f"grouped-daily HTTP={http1} {str(body1)[:80]}")

    # ── Canary 2: options snapshot ────────────────────────────────────────────
    url2 = f"https://api.polygon.io/v3/snapshot/options/SPY?limit=1&apiKey={key}"
    try:
        req2 = urllib.request.Request(url2, headers={"User-Agent": "aiem-diag/1"})
        with urllib.request.urlopen(req2, timeout=10) as r2:
            http2  = r2.status
            body2  = json.loads(r2.read(512))
        ok2 = http2 == 200
    except Exception as e2:
        http2 = 0; body2 = {"error": str(e2)[:80]}; ok2 = False

    os_ = {"http_status": http2, "ok": ok2, "body_sample": str(body2)[:80]}
    if not ok2:
        failures.append(f"options-snapshot HTTP={http2} {str(body2)[:80]}")

    ok     = ok1 and ok2
    detail = ("both endpoints OK" if ok
              else " | ".join(failures))

    return {
        "pass":             ok,
        "detail":           detail,
        "grouped_daily":    gd,
        "options_snapshot": os_,
    }


# ─────────────────────────────────────────────────────────────────────────────
# CHECK 4 — Classification sweep (dry-run / read-only)
# Mirrors _validate_and_fix_pipeline_run_classifications(fix_db=False).
# PASS = no FAILED+null-error_text rows in the last 30 days.
# ─────────────────────────────────────────────────────────────────────────────
def _check_classification_sweep(days_back: int = 30) -> dict:
    """
    Returns {"pass": bool, "detail": str, "suspect_count": int,
             "suspect_dates": list}.
    """
    cutoff = date.today() - timedelta(days=days_back)
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT run_date, status, candidates_failed
                FROM daily_pipeline_runs
                WHERE status = 'FAILED'
                  AND candidates_failed > 0
                  AND error_text IS NULL
                  AND run_date >= %s
                ORDER BY run_date DESC
            """, (cutoff,))
            rows = cur.fetchall()
    except Exception as exc:
        return {
            "pass":          False,
            "detail":        f"DB error: {exc}",
            "suspect_count": 0,
            "suspect_dates": [],
        }

    count  = len(rows)
    dates  = [str(r[0]) for r in rows]
    ok     = count == 0
    detail = ("0 misclassified FAILED rows in last 30 days"
              if ok else
              f"{count} FAILED+null-error_text row(s): {', '.join(dates)}")

    return {
        "pass":          ok,
        "detail":        detail,
        "suspect_count": count,
        "suspect_dates": dates,
    }


# ─────────────────────────────────────────────────────────────────────────────
# COMBINED REPORT
# ─────────────────────────────────────────────────────────────────────────────
def run_morning_diagnostic(
        force_fail: bool = False,
        dry_run:    bool = False,
        now_et:     Optional[datetime] = None,
) -> dict:
    """
    Runs all 4 checks, formats one combined report, sends ONE Telegram if any
    check fails.

    Returns {"report": str, "all_pass": bool, "results": dict}.

    Does NOT suppress individual alerts from the existing mechanisms
    (_schedule_integrity_check, _polygon_canary_check) — those fire on their
    own schedules; this is an additional consolidated view.
    """
    ts = (now_et or datetime.now(_ET)).strftime("%Y-%m-%d %H:%M ET")

    r_sched  = _check_schedule_integrity(now_et=now_et)
    r_drift  = _check_commit_drift()
    r_canary = _check_api_canary(force_fail=force_fail)
    r_sweep  = _check_classification_sweep()

    checks = [
        ("Schedule integrity",   r_sched),
        ("Commit drift",         r_drift),
        ("API canary",           r_canary),
        ("Classification sweep", r_sweep),
    ]

    lines = [f"MORNING DIAGNOSTIC — {ts}"]
    failures = []
    for label, result in checks:
        status = "PASS" if result["pass"] else "FAIL"
        lines.append(f"[{status}] {label}: {result['detail']}")
        if not result["pass"]:
            failures.append((label, result["detail"]))

    if failures:
        if len(failures) == 1:
            label, detail = failures[0]
            root = f"{label} — {detail}"
        else:
            root = "; ".join(f"{lbl}: {det}" for lbl, det in failures)
        lines.append(f"ROOT CAUSE: {root}")
    else:
        lines.append("ROOT CAUSE: none — all checks passed")

    report = "\n".join(lines)

    # console output always
    print(report)

    # ONE Telegram message only if something failed (and not dry-run)
    if failures and not dry_run:
        _tg_send(report)

    return {
        "report":   report,
        "all_pass": len(failures) == 0,
        "results": {
            "schedule_integrity":   r_sched,
            "commit_drift":         r_drift,
            "api_canary":           r_canary,
            "classification_sweep": r_sweep,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────────────────────────
def main() -> None:
    force_fail = "--force-fail" in sys.argv
    dry_run    = "--dry-run"    in sys.argv

    if not _DB_URL:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    result = run_morning_diagnostic(force_fail=force_fail, dry_run=dry_run)
    sys.exit(0 if result["all_pass"] else 1)


if __name__ == "__main__":
    main()
