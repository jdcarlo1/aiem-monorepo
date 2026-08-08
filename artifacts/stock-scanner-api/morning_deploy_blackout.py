"""Morning autonomous-window deploy blackout helpers (testable).

Window: Mon–Fri 08:50–10:20 America/New_York.
Replit cannot programmatically refuse Publish (no public deploy API) —
see .github/workflows/deploy-on-merge.yml. These helpers power:
  - stock-api boot Telegram alert
  - CI / deploy-on-merge blackout messaging
  - proof harnesses
"""
from __future__ import annotations

import datetime as dt
from typing import Callable, Optional, Tuple

BLACKOUT_START_MINS = 8 * 60 + 50   # 08:50 ET
BLACKOUT_END_MINS = 10 * 60 + 20    # 10:20 ET
ET_ZONE_NAME = "America/New_York"


def et_now() -> dt.datetime:
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo(ET_ZONE_NAME))
    except Exception:
        import pytz
        return dt.datetime.now(pytz.timezone(ET_ZONE_NAME))


def mins_since_midnight(now_et: dt.datetime) -> int:
    return int(now_et.hour) * 60 + int(now_et.minute)


def in_morning_blackout(now_et: Optional[dt.datetime] = None) -> bool:
    """True Mon–Fri inclusive 08:50–10:20 ET."""
    now = now_et or et_now()
    if now.weekday() >= 5:
        return False
    m = mins_since_midnight(now)
    return BLACKOUT_START_MINS <= m <= BLACKOUT_END_MINS


def boot_alert_message(now_et: dt.datetime) -> str:
    return (
        f"⚠️ stock-api BOOT during morning autonomous window "
        f"({now_et.strftime('%H:%M')} ET {now_et.date()}). "
        f"Publish/redeploy in 08:50–10:20 ET risks missing Loop B (9:07) "
        f"and paper execute (9:42). Prefer Publish before 08:45 ET or after "
        f"10:30 ET. Catchup + 10:15 retry will attempt recovery."
    )


def fire_boot_alert_if_in_window(
    *,
    now_et: Optional[dt.datetime] = None,
    tg_send: Optional[Callable[[str], object]] = None,
    log: Optional[Callable[[str], None]] = None,
) -> Tuple[bool, Optional[str]]:
    """Return (fired, message). Calls tg_send(message) when inside window."""
    now = now_et or et_now()
    _log = log or (lambda s: print(s, flush=True))
    if not in_morning_blackout(now):
        _log(
            f"[startup] morning-window boot alert skipped "
            f"(outside 08:50–10:20 ET weekday; now={now.isoformat()})"
        )
        return False, None
    msg = boot_alert_message(now)
    _log(f"[startup] {msg}")
    if callable(tg_send):
        tg_send(msg)
        _log("[startup] morning-window telegram sent")
    else:
        _log("[startup] morning-window telegram skipped (no tg_send)")
    return True, msg


def watchdog_needs_retry(status_info: dict) -> bool:
    """Mirror internal watchdog zero-pick retry gate (post-fix)."""
    st = status_info.get("status")
    pc = status_info.get("picks_count")
    attempts = int(status_info.get("recovery_attempts") or 0)
    return (
        st not in {"COMPLETED", "SKIPPED"}
        or (
            st in {"COMPLETED", "SKIPPED"}
            and (pc is None or int(pc or 0) == 0)
            and attempts < 5
        )
    )


def watchdog_needs_retry_BEFORE(status_info: dict) -> bool:
    """Pre-fix gate: SKIPPED/COMPLETED were fully terminal."""
    return status_info.get("status") not in {"COMPLETED", "SKIPPED"}


def deploy_reminder_mode(now_et: Optional[dt.datetime] = None) -> str:
    """For deploy-on-merge Telegram: 'safe' | 'blackout' | 'weekend'."""
    now = now_et or et_now()
    if now.weekday() >= 5:
        return "weekend"
    if in_morning_blackout(now):
        return "blackout"
    return "safe"
