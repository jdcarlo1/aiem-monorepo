#!/usr/bin/env python3
"""
AIEM Pattern Discovery Runner — continuous worker
Directive_PatternDiscovery_Framework_2026-08-05

This IS how the Cursor/Replit agent "tells AIEM" to work 24/7:
  edit this brain + register workflow + restart
    `artifacts/stock-scanner: pattern-discovery`
  → AIEM wakes up running the directive. No chat inbox.

Standalone — does NOT touch AIEM D1/D2/D3 or the live Pattern Lab dashboard.
Mirrors aiem_stat_research_runner.py (own process, own health port).

Health: :5058/   Trigger: POST :5058/trigger/run
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DIR))

DIRECTIVE_ID = "Directive_PatternDiscovery_Framework_2026-08-05"
HEALTH_PORT = int(os.environ.get("PATTERN_DISCOVERY_PORT", "5058"))
OUT_DIR = Path(
    os.environ.get(
        "PATTERN_DISCOVERY_OUT",
        "/home/runner/workspace/docs/verification"
        if Path("/home/runner/workspace").exists()
        else str(DIR.parent.parent / "docs" / "verification"),
    )
)
CACHE_PATH = os.environ.get(
    "PATTERN_DISCOVERY_CACHE", "/tmp/spy_1y_discovery.pkl"
)
# After a full Steps 1–5 report, sleep then re-run (rolling window stays fresh).
CYCLE_SLEEP_SECS = int(os.environ.get("PATTERN_DISCOVERY_CYCLE_SLEEP", str(6 * 3600)))
STATUS_PATH = Path("/tmp/aiem_pattern_discovery_status.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [pattern-discovery] %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("pattern-discovery")

_lock = threading.Lock()
_state = {
    "directive": DIRECTIVE_ID,
    "status": "starting",
    "phase": None,
    "cycle": 0,
    "last_start_utc": None,
    "last_finish_utc": None,
    "last_error": None,
    "last_report": None,
    "variants_tested": None,
    "final_survivors": None,
    "boot_utc": datetime.now(timezone.utc).isoformat(),
}


def _write_status(**kwargs):
    _state.update(kwargs)
    _state["updated_utc"] = datetime.now(timezone.utc).isoformat()
    try:
        STATUS_PATH.write_text(json.dumps(_state, indent=2, default=str))
    except Exception as e:
        log.warning("status write failed: %s", e)


def run_one_cycle(force: bool = False) -> dict:
    """Execute the full discovery framework once (Steps 1–5)."""
    with _lock:
        if _state.get("status") == "running" and not force:
            return {"ok": False, "error": "already_running"}
        _write_status(
            status="running",
            phase="import",
            last_start_utc=datetime.now(timezone.utc).isoformat(),
            last_error=None,
            cycle=int(_state.get("cycle") or 0) + 1,
        )

    log.info("=" * 72)
    log.info("AIEM accepting work order: %s", DIRECTIVE_ID)
    log.info("Split will be confirmed in runner stdout BEFORE results (Step 1).")
    log.info("=" * 72)

    try:
        # Ensure Polygon key present (Replit Secrets / env)
        if not os.environ.get("POLYGON_API_KEY"):
            # Common Replit alternate names
            for k in ("POLYGON_KEY", "POLYGON_API"):
                if os.environ.get(k):
                    os.environ["POLYGON_API_KEY"] = os.environ[k]
                    break
        if not os.environ.get("POLYGON_API_KEY"):
            raise RuntimeError(
                "POLYGON_API_KEY not set — AIEM cannot pull real bars"
            )

        from pattern_discovery import run_discovery as rd

        # argv for argparse inside main()
        argv_prev = sys.argv[:]
        sys.argv = [
            "run_discovery.py",
            "--symbol",
            "SPY",
            "--cache",
            CACHE_PATH,
            "--out-dir",
            str(OUT_DIR),
        ]
        # Reuse cache across cycles if present
        if Path(CACHE_PATH).exists():
            sys.argv.append("--skip-fetch")
            # First cycle after boot should refresh if cache older than 24h
            age_h = (time.time() - Path(CACHE_PATH).stat().st_mtime) / 3600
            if age_h > 24:
                sys.argv = [a for a in sys.argv if a != "--skip-fetch"]
                log.info("Cache age %.1fh > 24h — re-fetching Polygon", age_h)

        _write_status(phase="discovery_main")
        try:
            rd.main()
        finally:
            sys.argv = argv_prev

        report_path = OUT_DIR / "pattern-discovery-FINAL.json"
        summary = {}
        if report_path.exists():
            summary = json.loads(report_path.read_text())
        _write_status(
            status="idle_between_cycles",
            phase="complete",
            last_finish_utc=datetime.now(timezone.utc).isoformat(),
            last_report=str(report_path),
            variants_tested=summary.get("variants_tested"),
            final_survivors=summary.get("final_survivor_count"),
        )
        log.info(
            "Cycle done — tested=%s survivors=%s report=%s",
            summary.get("variants_tested"),
            summary.get("final_survivor_count"),
            report_path,
        )
        return {"ok": True, "summary": {
            "variants_tested": summary.get("variants_tested"),
            "final_survivor_count": summary.get("final_survivor_count"),
            "survival_rate": summary.get("survival_rate_vs_tested"),
        }}
    except Exception as e:
        log.error("Cycle failed: %s", e)
        traceback.print_exc()
        _write_status(
            status="error",
            phase="failed",
            last_error=str(e),
            last_finish_utc=datetime.now(timezone.utc).isoformat(),
        )
        return {"ok": False, "error": str(e)}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log.info("http " + fmt, *args)

    def _json(self, code: int, payload: dict):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/health", "/aiem-process/", "/status"):
            self._json(200, {"ok": True, **_state})
            return
        self._json(404, {"ok": False, "error": "not_found"})

    def do_POST(self):
        if self.path in ("/trigger/run", "/run"):
            # Non-blocking kick
            threading.Thread(
                target=run_one_cycle, kwargs={"force": False}, daemon=True
            ).start()
            self._json(202, {"ok": True, "accepted": True, "directive": DIRECTIVE_ID})
            return
        self._json(404, {"ok": False, "error": "not_found"})


def _start_health():
    try:
        srv = HTTPServer(("0.0.0.0", HEALTH_PORT), _Handler)
        log.info("health/trigger listening on :%s", HEALTH_PORT)
        srv.serve_forever()
    except Exception as e:
        log.error("health server failed: %s", e)


def main():
    log.info(
        "AIEM Pattern Discovery Runner boot — directive=%s port=%s",
        DIRECTIVE_ID,
        HEALTH_PORT,
    )
    _write_status(status="booted", phase="health_start")
    threading.Thread(target=_start_health, name="pd-health", daemon=True).start()
    time.sleep(0.5)

    # Continuous 24/7 loop — this is the work order execution
    while True:
        result = run_one_cycle()
        log.info("cycle result: %s", result)
        log.info(
            "Sleeping %ss before next cycle (24/7 worker stays alive)",
            CYCLE_SLEEP_SECS,
        )
        _write_status(status="sleeping", phase="cycle_sleep")
        time.sleep(CYCLE_SLEEP_SECS)


if __name__ == "__main__":
    main()
