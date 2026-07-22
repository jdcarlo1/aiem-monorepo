#!/usr/bin/env python3
"""
Negative-control test for aiem_process.py aiem_research_insights bare-INSERT fix.

BEFORE fix: bare INSERT at line 1700 → UNIQUE(research_date) violation when
main.py has already written today's row → falls into except/rollback → silent drop.

AFTER fix: ON CONFLICT(research_date) DO UPDATE SET findings = old || '\\n' || new
→ merge; no exception; both contributions visible in the row.

Protocol:
  Step 1: DELETE any existing test row for test_date (safe — test_date is synthetic).
  Step 2: INSERT row A (simulating main.py write, findings='MAIN_PY_FINDING').
  Step 3: INSERT row B with ON CONFLICT pattern (simulating post-fix aiem_process write).
  Step 4: SELECT and verify merged findings contain BOTH strings.
  Step 5: INSERT row B again WITHOUT ON CONFLICT (simulating pre-fix bare INSERT)
          → expect psycopg2.errors.UniqueViolation.
  Step 6: Confirm exception type is UniqueViolation (proves old code would have failed).
  Step 7: Rollback + DELETE test rows (cleanup).

Approval gate: Steps 1-7 require no user approval — all writes use a synthetic
test_date far in the future ('2099-12-31') that cannot collide with real data.
"""
import os, sys, psycopg2, psycopg2.errors, datetime

DB  = os.environ["DATABASE_URL"]
TEST_DATE = datetime.date(2099, 12, 31)   # synthetic, never a real market day

def section(title):
    print(); print("=" * 70); print(title); print("=" * 70)

def run():
    conn = psycopg2.connect(DB, connect_timeout=5)
    conn.autocommit = False
    cur  = conn.cursor()

    # ── Step 1: clean slate for test_date ────────────────────────────────────
    section("STEP 1 — DELETE any pre-existing row for test_date 2099-12-31")
    cur.execute("DELETE FROM aiem_research_insights WHERE research_date = %s", (TEST_DATE,))
    deleted = cur.rowcount
    conn.commit()
    print(f"DELETE rowcount: {deleted}  (0 or 1 expected)")

    # ── Step 2: INSERT row A (main.py path) ───────────────────────────────────
    section("STEP 2 — INSERT row A (simulates main.py write for today)")
    print("SQL: INSERT INTO aiem_research_insights (research_date, findings, ...) VALUES (...)")
    print("     ON CONFLICT (research_date) DO UPDATE SET findings = ... || EXCLUDED.findings")
    cur.execute("""
        INSERT INTO aiem_research_insights
            (research_date, findings, confidence, session_name, created_at)
        VALUES (%s, %s, %s, 'test_main_py', NOW())
        ON CONFLICT (research_date) DO UPDATE
            SET findings   = aiem_research_insights.findings || E'\\n' || EXCLUDED.findings,
                confidence = EXCLUDED.confidence
    """, (TEST_DATE, 'MAIN_PY_FINDING', '75'))
    conn.commit()
    cur.execute("SELECT research_date, findings, confidence, session_name FROM aiem_research_insights WHERE research_date=%s", (TEST_DATE,))
    row = cur.fetchone()
    print(f"After step 2: {row}")
    assert row is not None, "FAIL: row not found after step 2"
    assert 'MAIN_PY_FINDING' in row[1], f"FAIL: expected MAIN_PY_FINDING in findings, got: {row[1]}"
    print("STEP 2: PASS")

    # ── Step 3: INSERT row B (aiem_process.py post-fix path) ─────────────────
    section("STEP 3 — INSERT row B with ON CONFLICT (simulates post-fix aiem_process write)")
    print("SQL: INSERT INTO aiem_research_insights (...) VALUES (...)")
    print("     ON CONFLICT (research_date) DO UPDATE SET findings = old || E'\\\\n' || new")
    cur.execute("""
        INSERT INTO aiem_research_insights
            (research_date, findings, confidence, session_name, created_at)
        VALUES (%s, %s, %s, 'aiem_process_nightly_learn', NOW())
        ON CONFLICT (research_date) DO UPDATE
            SET findings   = aiem_research_insights.findings || E'\\n' || EXCLUDED.findings,
                confidence = EXCLUDED.confidence
    """, (TEST_DATE, 'AIEM_PROCESS_FINDING', '80'))
    conn.commit()
    print("No exception raised — INSERT accepted via ON CONFLICT path")

    # ── Step 4: Verify merged findings ───────────────────────────────────────
    section("STEP 4 — SELECT and verify merged findings")
    cur.execute("SELECT research_date, findings, confidence, session_name FROM aiem_research_insights WHERE research_date=%s", (TEST_DATE,))
    merged = cur.fetchone()
    print(f"Merged row: {merged}")
    has_main  = 'MAIN_PY_FINDING'    in (merged[1] or '')
    has_aiem  = 'AIEM_PROCESS_FINDING' in (merged[1] or '')
    print(f"Contains MAIN_PY_FINDING:    {has_main}")
    print(f"Contains AIEM_PROCESS_FINDING: {has_aiem}")
    assert has_main,  "FAIL: MAIN_PY_FINDING missing after merge"
    assert has_aiem,  "FAIL: AIEM_PROCESS_FINDING missing after merge"
    print("STEP 4: PASS — both contributions visible, no data dropped")

    # ── Step 5: Reset and test bare INSERT (pre-fix path) ────────────────────
    section("STEP 5 — Re-seed row A, then bare INSERT row B (pre-fix simulation)")
    # Row already exists from steps 2-4; test bare INSERT against it.
    print("Row already exists with research_date=2099-12-31. Attempting bare INSERT:")
    print("SQL: INSERT INTO aiem_research_insights (...) VALUES (...)  -- no ON CONFLICT")
    bare_exc = None
    try:
        cur.execute("""
            INSERT INTO aiem_research_insights
                (research_date, findings, confidence, session_name, created_at)
            VALUES (%s, %s, %s, 'aiem_process_nightly_learn', NOW())
        """, (TEST_DATE, 'AIEM_PROCESS_FINDING_BARE', '80'))
        conn.commit()
        print("FAIL: no exception raised — pre-fix INSERT should have raised UniqueViolation")
    except psycopg2.errors.UniqueViolation as e:
        bare_exc = e
        print(f"Exception type: {type(e).__name__}")
        print(f"Exception msg:  {e.pgerror}")
        conn.rollback()
        print("Rolled back (correct — pre-fix code would have silently swallowed this)")
    except Exception as e:
        bare_exc = e
        conn.rollback()
        print(f"Unexpected exception type: {type(e).__name__}: {e}")

    # ── Step 6: Confirm exception was UniqueViolation ─────────────────────────
    section("STEP 6 — Confirm exception type")
    is_unique_violation = isinstance(bare_exc, psycopg2.errors.UniqueViolation)
    print(f"bare_exc is UniqueViolation: {is_unique_violation}")
    assert is_unique_violation, f"FAIL: expected UniqueViolation, got {type(bare_exc)}"
    print("STEP 6: PASS — proves pre-fix bare INSERT would have failed and silently dropped")

    # ── Step 7: Cleanup ───────────────────────────────────────────────────────
    section("STEP 7 — Cleanup test row")
    cur.execute("DELETE FROM aiem_research_insights WHERE research_date = %s", (TEST_DATE,))
    cleaned = cur.rowcount
    conn.commit()
    print(f"DELETE rowcount: {cleaned}  (1 expected)")
    assert cleaned == 1, f"FAIL: expected 1 deleted, got {cleaned}"
    print("STEP 7: PASS — test row removed")
    conn.close()

    # ── SUMMARY ───────────────────────────────────────────────────────────────
    section("SUMMARY")
    print("  Step 1 (clean slate):            PASS")
    print("  Step 2 (main.py path insert):    PASS")
    print("  Step 3 (post-fix upsert):        PASS  — no exception")
    print("  Step 4 (merge verify):           PASS  — both findings present")
    print("  Step 5 (pre-fix bare insert):    PASS  — UniqueViolation raised")
    print("  Step 6 (exception type check):   PASS  — UniqueViolation confirmed")
    print("  Step 7 (cleanup):                PASS")
    print()
    print("SUMMARY: 7 PASS 0 FAIL")
    print("T_RESEARCH_INSIGHTS_CONFLICT OVERALL: PASS")

if __name__ == "__main__":
    try:
        run()
        sys.exit(0)
    except AssertionError as e:
        print(f"\nASSERTION FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        import traceback
        traceback.print_exc()
        sys.exit(1)
