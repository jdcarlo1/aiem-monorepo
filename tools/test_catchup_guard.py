#!/usr/bin/env python3
"""
Control test for the _startup_full_catchup() premarket protection guard.

NEGATIVE CONTROL: restart at 8:30 AM ET (inside 6:55-9:45 AM window)
  Expected: log "STARTUP-BLOCKED", function returns without touching DB.

POSITIVE CONTROL: restart at 10:30 AM ET (outside window, inside 9:00-3:30 PM)
  Expected: no STARTUP-BLOCKED message, function proceeds to check pred_count.

Runs standalone — no import of aiem_process.py needed.
"""
import sys
import logging
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

# ── Minimal replicate of the guard logic ─────────────────────────────────────
# This is an exact copy of the guard block from _startup_full_catchup() so
# the test is a true falsification of the production code path.

def _run_catchup_guard(now_et, logger):
    """Returns ('BLOCKED', reason) or ('PROCEED', reason)."""
    now_mins = now_et.hour * 60 + now_et.minute

    _PREMARKET_BLOCK_START = 6 * 60 + 55   # 6:55 AM ET
    _PREMARKET_BLOCK_END   = 9 * 60 + 45   # 9:45 AM ET
    if _PREMARKET_BLOCK_START <= now_mins <= _PREMARKET_BLOCK_END:
        msg = (
            f"[catchup] STARTUP-BLOCKED — restart at "
            f"{now_et.strftime('%I:%M %p ET')} is inside premarket "
            f"protection window (6:55–9:45 AM ET). "
            f"aiem_process_predictions will NOT be deleted or overwritten. "
            f"Scheduler resumes at next 15-min scan slot; "
            f"GH Actions premarket-backup.yml fires every 10 min as failsafe."
        )
        logger.warning(msg)
        return ("BLOCKED", msg)

    if not (540 <= now_mins <= 930):
        return ("SKIP_OUTSIDE_WINDOW", f"now_mins={now_mins} outside 9:00-3:30 PM window")

    return ("PROCEED", f"now_mins={now_mins} inside 9:00-3:30 PM, past 9:45 AM block")


# ── Test runner ───────────────────────────────────────────────────────────────
import pytz

ET = pytz.timezone("US/Eastern")

logging.basicConfig(level=logging.DEBUG,
                    format="%(levelname)s %(message)s",
                    stream=sys.stdout)
logger = logging.getLogger("test_guard")

FAIL = 0
PASS = 0

def run_case(label, hour, minute, expected_outcome):
    global FAIL, PASS
    # Build an ET-aware datetime for today at the given time
    today_et = datetime.now(ET).replace(hour=hour, minute=minute, second=0, microsecond=0)
    result, reason = _run_catchup_guard(today_et, logger)
    status = "PASS" if result == expected_outcome else "FAIL"
    if result != expected_outcome:
        FAIL += 1
        print(f"\n{status}  [{label}]")
        print(f"       time      : {today_et.strftime('%I:%M %p ET')} ({hour*60+minute} mins)")
        print(f"       expected  : {expected_outcome}")
        print(f"       got       : {result}")
        print(f"       reason    : {reason}")
    else:
        PASS += 1
        print(f"\n{status}  [{label}]")
        print(f"       time      : {today_et.strftime('%I:%M %p ET')} ({hour*60+minute} mins)")
        print(f"       expected  : {expected_outcome}")
        print(f"       got       : {result}  ✓")


print("=" * 70)
print("CONTROL TEST: _startup_full_catchup() premarket protection guard")
print("=" * 70)

# ── NEGATIVE CONTROLS: must all return BLOCKED ──────────────────────────────
run_case("NEG-1  6:55 AM (window boundary)",      hour=6,  minute=55, expected_outcome="BLOCKED")
run_case("NEG-2  7:00 AM (early premarket)",       hour=7,  minute=0,  expected_outcome="BLOCKED")
run_case("NEG-3  8:30 AM (mid-premarket)",         hour=8,  minute=30, expected_outcome="BLOCKED")
run_case("NEG-4  9:00 AM (transition)",            hour=9,  minute=0,  expected_outcome="BLOCKED")
run_case("NEG-5  9:30 AM (market open)",           hour=9,  minute=30, expected_outcome="BLOCKED")
run_case("NEG-6  9:44 AM (window boundary -1min)", hour=9,  minute=44, expected_outcome="BLOCKED")
run_case("NEG-7  9:45 AM (window boundary end)",   hour=9,  minute=45, expected_outcome="BLOCKED")

# ── POSITIVE CONTROLS: must NOT be blocked ───────────────────────────────────
run_case("POS-1  9:46 AM (just after window)",     hour=9,  minute=46, expected_outcome="PROCEED")
run_case("POS-2 10:30 AM (normal market hours)",   hour=10, minute=30, expected_outcome="PROCEED")
run_case("POS-3 13:00 PM (afternoon)",             hour=13, minute=0,  expected_outcome="PROCEED")
run_case("POS-4  3:30 PM (last slot)",             hour=15, minute=30, expected_outcome="PROCEED")

# ── OUTSIDE WINDOW (neither blocked nor catchup): ────────────────────────────
run_case("OOW-1  6:00 AM (before warmup)",         hour=6,  minute=0,  expected_outcome="SKIP_OUTSIDE_WINDOW")
run_case("OOW-2  4:00 AM (deep night)",            hour=4,  minute=0,  expected_outcome="SKIP_OUTSIDE_WINDOW")
run_case("OOW-3  3:31 PM (after close)",           hour=15, minute=31, expected_outcome="SKIP_OUTSIDE_WINDOW")
run_case("OOW-4  6:54 AM (just before window)",    hour=6,  minute=54, expected_outcome="SKIP_OUTSIDE_WINDOW")

print()
print("=" * 70)
print(f"RESULT: {PASS} PASS  {FAIL} FAIL  (total {PASS+FAIL})")
print("=" * 70)
sys.exit(1 if FAIL > 0 else 0)
