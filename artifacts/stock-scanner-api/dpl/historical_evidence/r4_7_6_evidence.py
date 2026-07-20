#!/usr/bin/env python3
"""R4.7.6 evidence: R4.4 SQL + replay_decision CODE_DRIFT confirmation."""
import os, sys, json, psycopg2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_DB_URL = os.environ["DATABASE_URL"]
import aiem_options_dpl as _dpl

print("=== R4.4: all is_test_record=FALSE rows with origin evidence ===")
conn = psycopg2.connect(_DB_URL, connect_timeout=8)
try:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                d.decision_id,
                to_char(d.created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"') AS created_at,
                d.alert_id,
                s.reason AS synthetic_reason,
                e.reason AS exclusions_reason
            FROM oe_decision_replay_inputs d
            JOIN oe_decision_audit a ON a.decision_id = d.decision_id
            LEFT JOIN oe_known_synthetic_rows s ON s.decision_id = d.decision_id
            LEFT JOIN oe_criterion1_exclusions e ON e.decision_id = d.decision_id
            WHERE d.is_test_record = FALSE
            ORDER BY d.created_at;
        """)
        rows = cur.fetchall()
        print(f"row_count={len(rows)}")
        for r in rows:
            print(json.dumps({
                "decision_id": r[0],
                "created_at": r[1],
                "alert_id": r[2],
                "synthetic_reason": r[3],
                "exclusions_reason": r[4],
            }, default=str))
finally:
    conn.close()

print()
print("=== R4.5: replay_decision CODE_DRIFT confirmation ===")
for did in ("ee74327806f841a7a4034dcc", "64d956c7ee1b4bbd83147861"):
    print(f"--- decision_id={did} ---")
    try:
        result = _dpl.replay_decision(did, db_url=_DB_URL)
        print(f"  full_match={result['full_match']} call_match={result['call_match']} put_match={result['put_match']}")
    except _dpl.ReplayCodeDriftError as e:
        print(f"  ReplayCodeDriftError: {e}")
    except _dpl.ReplayInputsMissingError as e:
        print(f"  ReplayInputsMissingError: {e}")
    except Exception as e:
        print(f"  Exception({type(e).__name__}): {e}")

print()
print("PASS")
