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

Schedule: once daily at 9:20 AM ET (after the market-open scanners have had
a few minutes to populate ai_short_calls_log for the day, before options
picks would be actionable at 9:30 open) plus a daily outcome backfill pass
right after. Also runs once immediately on startup so a fresh deploy /
workflow restart doesn't wait until the next 9:20 AM to have data.
"""
import datetime
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from daily_picks import run_daily_job
from reports import backfill_outcomes

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:
    _ET = datetime.timezone.utc

RUN_HOUR_ET = 9
RUN_MINUTE_ET = 20
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
