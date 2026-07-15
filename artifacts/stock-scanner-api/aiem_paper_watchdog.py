#!/usr/bin/env python3
"""
aiem_paper_watchdog.py — External paper trade watchdog (Protection #5).

Runs as a SEPARATE process, completely independent of the stock-api Flask
process. Polls paper_trade_job_ledger every 2 minutes via direct DB access.

After 9:46 AM ET on a trading weekday, if the ledger shows no terminal status
(COMPLETED or SKIPPED) for today, this process POSTs to the stock-api admin
endpoint to trigger execution.

The admin endpoint calls _aiem_paper_execute_today(trigger_source="external_watchdog")
which goes through try_claim() — exactly-once is preserved even when the external
watchdog fires simultaneously with the scheduler or internal watchdog.

Evidence: .local/paper_watchdog.log (persistent, survives restarts).
Heartbeat: paper_trade_watchdog_heartbeat table.
"""

import os
import sys
import json
import time
import datetime
import urllib.request
import urllib.error
import psycopg2
import pytz

_ET          = pytz.timezone("America/New_York")
_DB_URL      = os.getenv("DATABASE_URL", "")
_ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")
_LOG_FILE    = "/home/runner/workspace/.local/paper_watchdog.log"
_POLL_SEC    = 120

_API_PORT  = os.getenv("PORT", "5050")
_API_BASE  = f"http://localhost:{_API_PORT}"


def _log(event: dict):
    event.setdefault("ts", datetime.datetime.utcnow().isoformat() + "Z")
    event.setdefault("pid", os.getpid())
    msg = json.dumps(event)
    print(msg, flush=True)
    try:
        os.makedirs(os.path.dirname(_LOG_FILE), exist_ok=True)
        with open(_LOG_FILE, "a") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def _db():
    return psycopg2.connect(_DB_URL, connect_timeout=5)


def _get_ledger_status(date_str: str) -> str:
    try:
        with _db() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT status FROM paper_trade_job_ledger "
                "WHERE business_date = %s",
                (date_str,),
            )
            row = cur.fetchone()
            return row[0] if row else "PENDING"
    except Exception as e:
        _log({"event": "DB_ERROR", "error": str(e)})
        return "DB_ERROR"


def _write_watchdog_heartbeat():
    try:
        with _db() as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO paper_trade_watchdog_heartbeat
                    (process_type, last_alive, pid, status)
                VALUES ('external_watchdog', NOW(), %s, 'alive')
            """, (os.getpid(),))
            conn.commit()
    except Exception:
        pass


def _find_api_port() -> str:
    """
    Discover the stock-api PORT. The process may not bind to PORT env
    if that var is used by a different artifact. Try common port, then
    check if stock-api is reachable.
    """
    candidates = [
        os.getenv("STOCK_API_PORT", ""),
        "5050", "8080", "3001", "8000",
    ]
    for p in candidates:
        if not p:
            continue
        try:
            url = f"http://localhost:{p}/stock-api/ping"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=3):
                return p
        except Exception:
            continue
    return _API_PORT


def _trigger_recovery(trigger_note: str = "") -> bool:
    """POST to admin endpoint to force paper trade execution via external watchdog."""
    port = _find_api_port()
    url  = f"http://localhost:{port}/stock-api/admin/run-paper-today"
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps({"trigger_source": "external_watchdog",
                             "note": trigger_note}).encode(),
            headers={
                "Content-Type": "application/json",
                "X-Admin-Token": _ADMIN_TOKEN,
                "X-Trigger-Source": "external_watchdog",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode()[:300]
            _log({"event": "TRIGGER_SENT", "http_status": resp.status,
                  "body": body, "port": port})
            return True
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:200]
        _log({"event": "TRIGGER_HTTP_ERROR", "code": e.code,
              "body": body, "port": port})
    except Exception as e:
        _log({"event": "TRIGGER_ERROR", "error": str(e), "port": port})
    return False


def main():
    _log({"event": "WATCHDOG_START", "poll_sec": _POLL_SEC, "pid": os.getpid()})
    print(f"[paper_watchdog] external watchdog started pid={os.getpid()} "
          f"poll_interval={_POLL_SEC}s", flush=True)

    while True:
        try:
            now_et   = datetime.datetime.now(_ET)
            today    = now_et.date()
            h, m     = now_et.hour, now_et.minute
            date_str = str(today)

            _write_watchdog_heartbeat()

            is_wday  = now_et.weekday() < 5
            past_946 = (h > 9) or (h == 9 and m >= 46)
            before_4 = h < 16

            if is_wday and past_946 and before_4:
                status = _get_ledger_status(date_str)
                terminal = {"COMPLETED", "SKIPPED"}

                if status not in terminal and status != "DB_ERROR":
                    note = f"{date_str} status={status} at {h:02d}:{m:02d} ET"
                    _log({
                        "event": "WATCHDOG_RECOVERY_TRIGGERED",
                        "date": date_str,
                        "ledger_status": status,
                        "time_et": f"{h:02d}:{m:02d}",
                    })
                    print(f"[paper_watchdog] RECOVERY: {note} — sending trigger",
                          flush=True)
                    _trigger_recovery(trigger_note=note)
                else:
                    _log({
                        "event": "WATCHDOG_CHECK_OK",
                        "date": date_str,
                        "ledger_status": status,
                        "time_et": f"{h:02d}:{m:02d}",
                    })

        except Exception as exc:
            _log({"event": "WATCHDOG_LOOP_ERROR", "error": str(exc)})

        time.sleep(_POLL_SEC)


if __name__ == "__main__":
    main()
