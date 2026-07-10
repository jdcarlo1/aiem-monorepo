"""
TEST I — HASH-TAMPER NEGATIVE CONTROL (Path B spec Section 10).

Spec text:
  "Attempt to UPDATE or DELETE a historical governance event and governance
  action. Expected: Database rejects the change or the immutable
  superseding-record policy prevents silent mutation."

This harness proves that MINIMUM requirement (one real UPDATE + one real
DELETE attempt against a genuinely pre-existing historical row in
d3_governance_event_links, and the same against d3_governance_actions), and
then goes further to prove the same for the three Section-12F ledger tables
added alongside G0-G5 (d3_governance_requests, d3_governance_decisions,
d3_governance_acks) — all five are part of the same tamper-evidence
guarantee (Section 7) and all five have their own DB-level append-only (or
field-guard) trigger, so a TEST I proof that only covered two of the five
would understate what is actually protected.

Every attempt below:
  - targets a REAL, pre-existing row already in the live table (queried by
    MIN(id), i.e. the oldest real row — never a row created by this harness
    itself, so this is a genuine "historical record" per the spec wording).
  - runs inside its own transaction that is UNCONDITIONALLY rolled back,
    whether the statement is rejected or (in a hypothetical regression)
    succeeds — this script can never make a permanent change to the ledger.
  - uses raw psycopg2 SQL, bypassing every Python-layer guard, so a PASS
    here proves DB-level enforcement, not just "the app never calls this".
  - captures the verbatim raw DB error text into the report (never just a
    boolean) so the evidence is independently inspectable.

d3_governance_actions is special-cased: its trigger (trg_d3ga_guard) is a
field-guard, not a blanket block — it deliberately ALLOWS status/checked_at/
check_detail to change (that's how a real REQUESTED action legitimately
resolves to APPROVED/REJECTED later) and only blocks mutation of the
identity/request fields (action_id, governance_event_id, requested_at,
phase, action_type, target_type, target_id, reason, is_test_record,
created_at) plus all DELETEs. So the UPDATE attempt against
d3_governance_actions below targets `reason` (a guarded identity field),
not `status` — an UPDATE to `status` succeeding would NOT be a tamper-proof
failure, it is the designed legitimate-transition path and proving THAT is
blocked would be testing the wrong thing.

Usage:
  python3 aiem_diagram3_i_verify.py
"""
import sys
import json

import psycopg2

import aiem_diagram3_governance as d3gov


# Each entry: (table, id_query, update_sql_template, delete_sql_template,
#              expected_error_substring)
_TARGETS = [
    {
        "table": "d3_governance_event_links",
        "trigger": "trg_d3gel_immutable",
        "update_sql": "UPDATE d3_governance_event_links SET reason_detail = %s WHERE id = %s",
        "update_params": ("TEST_I_TAMPER_ATTEMPT_SHOULD_NEVER_PERSIST",),
        "delete_sql": "DELETE FROM d3_governance_event_links WHERE id = %s",
        "expected_substring": "append-only",
    },
    {
        "table": "d3_governance_actions",
        "trigger": "trg_d3ga_guard",
        "update_sql": "UPDATE d3_governance_actions SET reason = %s WHERE id = %s",
        "update_params": ("TEST_I_TAMPER_ATTEMPT_SHOULD_NEVER_PERSIST",),
        "delete_sql": "DELETE FROM d3_governance_actions WHERE id = %s",
        "expected_substring": "immutable",
        "expected_substring_delete": "append-only",
    },
    {
        "table": "d3_governance_requests",
        "trigger": "trg_d3gr_immutable",
        "update_sql": "UPDATE d3_governance_requests SET requested_action = %s WHERE id = %s",
        "update_params": ("TEST_I_TAMPER_ATTEMPT_SHOULD_NEVER_PERSIST",),
        "delete_sql": "DELETE FROM d3_governance_requests WHERE id = %s",
        "expected_substring": "append-only",
    },
    {
        "table": "d3_governance_decisions",
        "trigger": "trg_d3gd_immutable",
        "update_sql": "UPDATE d3_governance_decisions SET policy_version = %s WHERE id = %s",
        "update_params": ("TEST_I_TAMPER_ATTEMPT_SHOULD_NEVER_PERSIST",),
        "delete_sql": "DELETE FROM d3_governance_decisions WHERE id = %s",
        "expected_substring": "append-only",
    },
    {
        "table": "d3_governance_acks",
        "trigger": "trg_d3gack_immutable",
        "update_sql": "UPDATE d3_governance_acks SET acknowledged_by = %s WHERE id = %s",
        "update_params": ("TEST_I_TAMPER_ATTEMPT_SHOULD_NEVER_PERSIST",),
        "delete_sql": "DELETE FROM d3_governance_acks WHERE id = %s",
        "expected_substring": "append-only",
    },
]


def _oldest_real_row_id(conn, table: str):
    """The real, pre-existing oldest row in the live table -- never a row
    this harness creates itself. Returns None if the table is empty (a
    genuine 'nothing to tamper-test yet' state, reported honestly, not
    treated as a pass)."""
    with conn.cursor() as cur:
        cur.execute(f"SELECT MIN(id) FROM {table}")
        row = cur.fetchone()
        return row[0] if row else None


def _attempt(conn, sql: str, params: tuple, expected_substring: str) -> dict:
    """
    Runs one raw SQL mutation attempt inside its own transaction, always
    rolled back regardless of outcome. Returns the raw result -- never
    invents a PASS if the statement silently succeeds.
    """
    result = {"sql": sql, "params": params}
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            affected = cur.rowcount
        # If we get here, the DB did NOT raise -- a real problem, not a pass.
        conn.rollback()
        result["blocked"] = False
        result["rows_would_have_been_affected"] = affected
        result["verdict"] = (
            f"FAIL — statement was NOT rejected by the DB "
            f"(rowcount={affected}); tamper-evidence trigger is missing or broken"
        )
    except psycopg2.Error as e:
        conn.rollback()
        msg = str(e).strip()
        result["db_error_message"] = msg
        matched = expected_substring.lower() in msg.lower()
        result["blocked"] = True
        result["expected_substring_matched"] = matched
        result["verdict"] = (
            f"PASS — real raw-SQL attempt rejected by the live DB trigger"
            if matched else
            f"PARTIAL — statement WAS rejected, but not with the expected "
            f"'{expected_substring}' wording (still a real rejection, just "
            f"verify the trigger is the one you think it is): {msg}"
        )
    return result


def run_test_i() -> int:
    conn = d3gov._d3_connect()
    report = {"test": "TEST I — HASH-TAMPER NEGATIVE CONTROL", "tables": []}
    exit_code = 0

    for target in _TARGETS:
        table = target["table"]
        row_id = _oldest_real_row_id(conn, table)
        entry = {"table": table, "trigger": target["trigger"], "target_row_id": row_id}

        if row_id is None:
            entry["skipped"] = True
            entry["reason"] = (
                f"{table} has zero rows in the live DB right now -- nothing "
                f"real to tamper-test against. Not a PASS, not a FAIL: "
                f"genuinely not-yet-applicable."
            )
            report["tables"].append(entry)
            continue

        print(f"\n── {table} (trigger={target['trigger']}, real historical row id={row_id}) ──")

        upd = _attempt(
            conn, target["update_sql"], target["update_params"] + (row_id,),
            target.get("expected_substring", "append-only"),
        )
        print("  UPDATE attempt:")
        print(f"    {upd['verdict']}")
        if upd.get("db_error_message"):
            print(f"    raw DB error: {upd['db_error_message']}")
        entry["update_attempt"] = upd
        if not upd["blocked"]:
            exit_code = 1

        dele = _attempt(
            conn, target["delete_sql"], (row_id,),
            target.get("expected_substring_delete", target.get("expected_substring", "append-only")),
        )
        print("  DELETE attempt:")
        print(f"    {dele['verdict']}")
        if dele.get("db_error_message"):
            print(f"    raw DB error: {dele['db_error_message']}")
        entry["delete_attempt"] = dele
        if not dele["blocked"]:
            exit_code = 1

        report["tables"].append(entry)

    conn.close()

    print("\n" + "=" * 70)
    print(json.dumps(report, indent=2, default=str))
    print("=" * 70)
    print(f"\nOverall exit_code={exit_code} "
          f"({'ALL REAL TAMPER ATTEMPTS BLOCKED (TEST I PASS)' if exit_code == 0 else 'A REAL TAMPER ATTEMPT SUCCEEDED (TEST I FAIL)'})")
    print("Every UPDATE/DELETE above ran inside a rolled-back transaction -- "
          "zero permanent changes were made to any table by this script.")
    return exit_code


if __name__ == "__main__":
    sys.exit(run_test_i())
