#!/usr/bin/env python3
"""CI / pre-publish gate for the morning autonomous window.

Exit 1 if now is Mon–Fri 08:50–10:20 America/New_York unless
ALLOW_MORNING_PUBLISH=1 is set (explicit manual override).

Replit cannot refuse the Publish button itself (no public deploy API).
This is the next-best structural guard: fail CI / preflight checks so a
mid-window publish cannot be "accidentally approved" without override.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from morning_deploy_blackout import deploy_reminder_mode, et_now, in_morning_blackout


def main() -> int:
    now = et_now()
    mode = deploy_reminder_mode(now)
    print(f"now_et={now.isoformat()}")
    print(f"mode={mode}")
    print(f"in_morning_blackout={in_morning_blackout(now)}")
    override = os.environ.get("ALLOW_MORNING_PUBLISH", "") == "1"
    print(f"ALLOW_MORNING_PUBLISH={override}")

    if mode != "blackout":
        print("OK outside blackout — publish permitted by this gate")
        return 0

    if override:
        print(
            "OVERRIDE: ALLOW_MORNING_PUBLISH=1 set — "
            "proceeding despite 08:50–10:20 ET blackout"
        )
        return 0

    print(
        "BLOCKED: Mon–Fri 08:50–10:20 ET autonomous window. "
        "Do not Publish stock-api now. Wait until after 10:30 ET "
        "(or before 08:45 ET). To force: ALLOW_MORNING_PUBLISH=1"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
