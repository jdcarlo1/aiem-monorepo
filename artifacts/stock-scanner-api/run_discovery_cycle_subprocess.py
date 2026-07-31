#!/usr/bin/env python3
"""
Memory-isolated discovery cycle runner.

Spawned as a subprocess by _discovery_cycle_job() in main.py so that the
1.3M-row backtest data load runs in its own address space.  If this process
is OOM-killed, the Flask server (parent) is completely unaffected.

Usage:
  python3 run_discovery_cycle_subprocess.py <templates_json_path> <result_json_path>

Arguments:
  templates_json_path  — path to JSON file containing Module-2-ranked templates
                         (written by parent before spawning)
  result_json_path     — path where this script writes output JSON:
                           { "cycle": <run_cycle() return value>,
                             "wl":    <run_tiered_wl_cycle() return value> }

Exit codes:
  0  — run_cycle() completed (result may still be aborted_no_data)
  1  — unrecoverable error (import failure, result write failure, etc.)
"""
import sys
import os
import json


def main() -> int:
    if len(sys.argv) < 3:
        print(
            "usage: run_discovery_cycle_subprocess.py "
            "<templates_json> <result_json>",
            file=sys.stderr,
        )
        return 1

    templates_path = sys.argv[1]
    result_path    = sys.argv[2]

    # ── Load Module-2-ranked templates written by the parent ─────────────────
    templates = None
    try:
        with open(templates_path) as f:
            templates = json.load(f)
    except Exception as e:
        # Non-fatal: fall back to default template order inside run_cycle()
        print(f"[dc_subprocess] templates load failed ({e}) — using default order",
              file=sys.stderr)

    # ── Import the engine (adds this dir to sys.path first) ──────────────────
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    try:
        import aiem_discovery_engine as _de
    except Exception as imp_err:
        print(f"[dc_subprocess] import error: {imp_err}", file=sys.stderr)
        return 1

    engine = _de.get_discovery_engine()

    # ── Main discovery cycle ──────────────────────────────────────────────────
    cycle_result: dict = {}
    cycle_ok = True
    try:
        cycle_result = engine.run_cycle(templates=templates or None)
        print(
            f"[dc_subprocess] run_cycle done — "
            f"proposed={cycle_result.get('proposed', 0)} "
            f"rejected={cycle_result.get('rejected', 0)} "
            f"status={cycle_result.get('run_status', 'completed')}"
        )
    except Exception as e:
        cycle_result = {
            "run_status": "error",
            "error":      str(e),
            "proposed":   0,
            "rejected":   0,
            "total_templates": 0,
            "results":    [],
        }
        cycle_ok = False
        print(f"[dc_subprocess] run_cycle error: {e}", file=sys.stderr)

    # ── Per-tier Win/Loss learning cycle ─────────────────────────────────────
    # Also memory-intensive (loads daily_market_movers); keep it here so the
    # parent process is isolated from both data loads.
    wl_result: dict = {}
    try:
        wl_result = engine.run_tiered_wl_cycle()
        print(f"[dc_subprocess] wl_cycle done: {wl_result}")
    except Exception as wle:
        wl_result = {"error": str(wle)}
        print(f"[dc_subprocess] wl_cycle error (non-fatal): {wle}", file=sys.stderr)

    # ── Write combined output for the parent to read ──────────────────────────
    output = {"cycle": cycle_result, "wl": wl_result}
    try:
        with open(result_path, "w") as f:
            # default=str handles datetime/Decimal objects from DB result rows
            json.dump(output, f, default=str)
    except Exception as we:
        print(f"[dc_subprocess] failed to write result to {result_path}: {we}",
              file=sys.stderr)
        return 1

    return 0 if cycle_ok else 1


if __name__ == "__main__":
    sys.exit(main())
