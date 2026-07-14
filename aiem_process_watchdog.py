#!/usr/bin/env python3
"""
aiem_process_watchdog.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Permanent watchdog for the aiem-process workflow.

PROBLEM THIS SOLVES
  aiem-process does a nightly os._exit(0) at 3:02 AM ET to flush memory.
  Replit's platform should auto-restart it within seconds, but occasionally
  fails to do so silently — leaving aiem-process dead for the rest of the
  day with no alert. This causes missed 9:20 AM independent stock pick scans
  and missed Telegram alerts.

WHAT IT DOES
  1. Checks every 2 minutes whether aiem_process.py is running via pgrep.
  2. Skips checks during the nightly reset window (3:00–3:10 AM ET) —
     the process is expected to be briefly dead then.
  3. If dead for 2 consecutive checks (≥4 min outside reset window):
     a. Sends a Telegram alert to the owner immediately.
     b. Spawns aiem_process.py directly as a subprocess so the scan
        and alerts resume without waiting for manual intervention.
  4. Logs every check and action to /tmp/aiem_process_watchdog.log.
  5. Never self-terminates — runs permanently as its own workflow.

NIGHTLY RESET GRACE WINDOW
  3:00–3:10 AM ET (UTC-4 in summer, UTC-5 in winter). During this window
  we allow the process to be down without raising an alert. If it is still
  dead at 3:10 AM ET we treat it as a failure and restart.
"""

import os
import sys
import time
import subprocess
import datetime
import urllib.request
import urllib.parse

# ── Config ────────────────────────────────────────────────────────────────
PROCESS_SCRIPT   = "/home/runner/workspace/artifacts/stock-scanner-api/aiem_process.py"
CHECK_INTERVAL   = 120        # seconds between checks
MISS_THRESHOLD   = 2          # consecutive misses before action
LOG_FILE         = "/tmp/aiem_process_watchdog.log"
GRACE_START_H    = 3          # ET hour  — start of nightly reset grace window
GRACE_START_M    = 0          # ET minute
GRACE_END_H      = 3          # ET hour  — end of grace window
GRACE_END_M      = 10         # ET minute

# ── Timezone helper ───────────────────────────────────────────────────────
try:
    import zoneinfo
    _ET = zoneinfo.ZoneInfo("America/New_York")
except ImportError:
    import pytz
    _ET = pytz.timezone("America/New_York")


def _now_et():
    return datetime.datetime.now(tz=_ET)


def _utc_now():
    return datetime.datetime.now(datetime.timezone.utc)


def _ts():
    return _utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Logging ───────────────────────────────────────────────────────────────
def _log(msg: str):
    line = f"[AIEM-WD {_ts()}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ── Telegram ──────────────────────────────────────────────────────────────
def _tg_send(text: str) -> bool:
    token   = "".join(os.environ.get("TELEGRAM_BOT_TOKEN", "").split())
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        _log("[telegram] no token/chat_id — cannot send alert")
        return False
    try:
        payload = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text":    text,
            "parse_mode": "HTML",
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status == 200
    except Exception as e:
        _log(f"[telegram] send failed: {e}")
        return False


# ── Process detection ─────────────────────────────────────────────────────
def _is_aiem_process_alive() -> bool:
    """True if at least one python process running aiem_process.py exists."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "aiem_process.py"],
            capture_output=True, text=True
        )
        return bool(result.stdout.strip())
    except Exception as e:
        _log(f"[pgrep] error: {e}")
        return True   # fail-open: don't restart on pgrep errors


def _count_aiem_pids() -> list:
    try:
        result = subprocess.run(
            ["pgrep", "-f", "aiem_process.py"],
            capture_output=True, text=True
        )
        return [p for p in result.stdout.strip().splitlines() if p]
    except Exception:
        return []


# ── Grace window ──────────────────────────────────────────────────────────
def _in_grace_window() -> bool:
    """True during the nightly reset window — process is expected to be down."""
    now = _now_et()
    h, m = now.hour, now.minute
    start_min = GRACE_START_H * 60 + GRACE_START_M
    end_min   = GRACE_END_H   * 60 + GRACE_END_M
    current   = h * 60 + m
    return start_min <= current <= end_min


# ── Restart ───────────────────────────────────────────────────────────────
def _restart_aiem_process():
    """Spawn aiem_process.py as a detached subprocess."""
    _log("Spawning aiem_process.py as subprocess...")
    try:
        log_path = "/tmp/aiem_process_watchdog_spawn.log"
        proc = subprocess.Popen(
            [sys.executable, PROCESS_SCRIPT],
            stdout=open(log_path, "a"),
            stderr=subprocess.STDOUT,
            close_fds=True,
            start_new_session=True,
        )
        _log(f"Spawned aiem_process.py as PID={proc.pid}")
        return proc.pid
    except Exception as e:
        _log(f"[ERROR] Failed to spawn aiem_process.py: {e}")
        return None


# ── Main loop ─────────────────────────────────────────────────────────────
def main():
    _log(f"=== AIEM PROCESS WATCHDOG STARTED PID={os.getpid()} ===")
    _log(f"Checking every {CHECK_INTERVAL}s | miss threshold={MISS_THRESHOLD} | "
         f"grace window={GRACE_START_H:02d}:{GRACE_START_M:02d}–"
         f"{GRACE_END_H:02d}:{GRACE_END_M:02d} ET")

    consecutive_misses = 0
    last_alert_ts      = None
    ALERT_COOLDOWN     = 1800   # don't re-alert more than once per 30 min

    while True:
        try:
            alive = _is_aiem_process_alive()
            grace = _in_grace_window()

            if alive:
                if consecutive_misses > 0:
                    _log(f"aiem-process is back alive after {consecutive_misses} miss(es)")
                consecutive_misses = 0
                pids = _count_aiem_pids()
                _log(f"aiem-process OK — PIDs: {pids}")

            else:
                if grace:
                    _log("aiem-process not detected — inside nightly reset grace window, skipping")
                    consecutive_misses = 0
                else:
                    consecutive_misses += 1
                    _log(f"aiem-process NOT running (miss #{consecutive_misses}/{MISS_THRESHOLD})")

                    if consecutive_misses >= MISS_THRESHOLD:
                        now_ts = _utc_now().timestamp()
                        should_alert = (
                            last_alert_ts is None or
                            (now_ts - last_alert_ts) >= ALERT_COOLDOWN
                        )

                        if should_alert:
                            msg = (
                                "⚠️ <b>AIEM-PROCESS IS DOWN</b>\n"
                                "━━━━━━━━━━━━━━━━━━━━\n"
                                f"Detected at {_now_et().strftime('%I:%M %p ET on %a %b %d')}\n"
                                f"Down for {consecutive_misses * CHECK_INTERVAL // 60}+ minutes "
                                f"outside nightly reset window.\n\n"
                                "Attempting automatic restart now...\n"
                                "Stock pick scans and Telegram alerts may have been delayed."
                            )
                            ok = _tg_send(msg)
                            _log(f"Telegram alert sent: {ok}")
                            last_alert_ts = now_ts

                        new_pid = _restart_aiem_process()
                        consecutive_misses = 0

                        if new_pid:
                            time.sleep(15)
                            if _is_aiem_process_alive():
                                _tg_send(
                                    f"✅ <b>aiem-process restarted successfully</b> (PID {new_pid})\n"
                                    "Scans and alerts will resume normally."
                                )
                                _log(f"Restart confirmed alive PID={new_pid}")
                            else:
                                _tg_send(
                                    "❌ <b>aiem-process restart FAILED</b>\n"
                                    "Manual intervention required — please restart the "
                                    "aiem-process workflow in Replit."
                                )
                                _log("Restart failed — process still not detected after 15s")

        except Exception as e:
            _log(f"[WATCHDOG ERROR] {e}")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
