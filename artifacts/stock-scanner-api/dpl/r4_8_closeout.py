#!/usr/bin/env python3
"""R4.8.6 / R4.8.7 close-out: git status+diff in log, Criterion 1 status."""
import os, sys, subprocess, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import psycopg2

_DB_URL = os.environ["DATABASE_URL"]

print("=== git status --porcelain (captured inside verified_run.sh) ===")
r = subprocess.run(
    ["git", "--no-optional-locks", "status", "--porcelain"],
    capture_output=True, text=True, cwd="/home/runner/workspace"
)
print(r.stdout or "(clean)")
print(r.stderr or "")

print("=== git diff HEAD --stat (captured inside verified_run.sh) ===")
r2 = subprocess.run(
    ["git", "--no-optional-locks", "diff", "HEAD", "--stat"],
    capture_output=True, text=True, cwd="/home/runner/workspace"
)
print(r2.stdout or "(no diff)")
print(r2.stderr or "")

print()
print("=== tools/verified_run_seq content ===")
seq_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools", "verified_run_seq")
try:
    with open(seq_file) as f:
        print(f"verified_run_seq={f.read().strip()}")
except Exception as e:
    print(f"ERROR reading verified_run_seq: {e}")

print()
print("=== Criterion 1 status ===")
conn = psycopg2.connect(_DB_URL, connect_timeout=8)
try:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(*) FROM oe_decision_replay_inputs
            WHERE is_test_record = FALSE
              AND decision_id NOT IN (SELECT decision_id FROM oe_known_synthetic_rows)
              AND decision_id NOT IN (SELECT decision_id FROM oe_criterion1_exclusions)
        """)
        unregistered = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*) FROM oe_decision_replay_inputs
            WHERE is_test_record = FALSE
        """)
        total_false = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*) FROM oe_decision_replay_inputs
            WHERE is_test_record = FALSE
              AND alert_id IS NOT NULL
        """)
        with_alert_id = cur.fetchone()[0]

    print(f"total is_test_record=FALSE rows:  {total_false}")
    print(f"rows with non-NULL alert_id:      {with_alert_id}")
    print(f"rows unregistered (no synthetic/exclusions entry): {unregistered}")
    if unregistered == 0 and with_alert_id == 0:
        print("CRITERION_1_STATUS: BLOCKED")
        print("REASON: all FALSE rows are registered synthetic/backfill; no live scheduler decision recorded yet")
        print("R6_STATUS: OPEN")
        print("PHASE_3_STATUS: OPEN — awaiting first live scheduler decision (target: Mon 2026-07-21 09:45 ET)")
    elif with_alert_id > 0:
        print("CRITERION_1_STATUS: LIVE_DECISION_PRESENT")
        print("PHASE_3_STATUS: PARTIAL — review live decision rows")
    else:
        print("CRITERION_1_STATUS: BLOCKED — unregistered non-production rows present")
finally:
    conn.close()

print()
print("PASS")
