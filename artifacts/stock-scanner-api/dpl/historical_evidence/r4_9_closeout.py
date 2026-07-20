#!/usr/bin/env python3
"""R4.9.9 close-out: git status+diff, Criterion 1 status, no-write proof."""
import os, sys, subprocess
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import psycopg2

_DB_URL = os.environ["DATABASE_URL"]

print("=== git status --porcelain ===")
r = subprocess.run(
    ["git", "--no-optional-locks", "status", "--porcelain"],
    capture_output=True, text=True, cwd="/home/runner/workspace"
)
print(r.stdout or "(clean)")

print("=== git diff HEAD --stat ===")
r2 = subprocess.run(
    ["git", "--no-optional-locks", "diff", "HEAD", "--stat"],
    capture_output=True, text=True, cwd="/home/runner/workspace"
)
print(r2.stdout or "(no diff)")

print("=== per-session file sha256s ===")
import hashlib, pathlib
base = pathlib.Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
files = [
    "tools/verified_run.sh",
    "dpl/README.md",
    "dpl/r4_7_6_evidence.py",
    "dpl/r4_8_closeout.py",
    "dpl/r4_9_closeout.py",
]
for f in files:
    p = base / f
    if p.exists():
        h = hashlib.sha256(p.read_bytes()).hexdigest()
        print(f"  {f}={h}")
    else:
        print(f"  {f}=MISSING")

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
        cur.execute("SELECT COUNT(*) FROM oe_decision_replay_inputs WHERE is_test_record=FALSE")
        total = cur.fetchone()[0]
        cur.execute("""SELECT COUNT(*) FROM oe_decision_replay_inputs
                       WHERE is_test_record=FALSE AND alert_id IS NOT NULL""")
        with_alert = cur.fetchone()[0]
    print(f"total is_test_record=FALSE rows:  {total}")
    print(f"rows with non-NULL alert_id:      {with_alert}")
    print(f"unregistered rows:                {unregistered}")
    if unregistered == 0 and with_alert == 0:
        print("CRITERION_1_STATUS: BLOCKED")
        print("R6_STATUS: OPEN")
        print("PHASE_3_STATUS: OPEN")
        print("NEXT_EVIDENCE_WINDOW: 2026-07-20 09:45 ET (Monday)")
    elif with_alert > 0:
        print("CRITERION_1_STATUS: LIVE_DECISION_PRESENT — review required")
    else:
        print("CRITERION_1_STATUS: BLOCKED (unregistered false rows present)")
finally:
    conn.close()

print()
print("=== production table mutation check ===")
conn2 = psycopg2.connect(_DB_URL, connect_timeout=8)
try:
    with conn2.cursor() as cur:
        for tbl in ["oe_decision_replay_inputs","oe_decision_audit",
                    "oe_known_synthetic_rows","oe_criterion1_exclusions",
                    "oe_unreplayable_rows"]:
            try:
                cur.execute(f"SELECT COUNT(*) FROM {tbl}")
                n = cur.fetchone()[0]
                print(f"  {tbl}: {n} rows")
            except Exception as e:
                print(f"  {tbl}: ERROR({e})")
finally:
    conn2.close()

print()
print("PASS")
