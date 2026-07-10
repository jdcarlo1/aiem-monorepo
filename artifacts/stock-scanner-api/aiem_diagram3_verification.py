"""
T-E / T-G: Independent hash-chain validator, tamper-detection proof, and
real end-to-end timeline printer for d3_governance_event_links.

This script does NOT reimplement the hashing logic from scratch — it
imports the exact same `_D3_EVENT_FIELDS_BY_VERSION` and `_canonical_bytes`
functions that `aiem_diagram3_governance._d3_emit_event()` uses to compute
`event_hash`, so there is zero risk of the validator silently drifting from
production and reporting false PASS/FAIL results.

Usage:
  python3 aiem_diagram3_verification.py verify
      Full-table hash-chain verification + tamper-detection proof (T-E).

  python3 aiem_diagram3_verification.py timeline --trace-id <id>
      Prints one real ordered event timeline for a given trace id (matched
      against governance_trace_id / root_trace_id / diagram1_trace_id /
      diagram2_trace_id / diagram3_trace_id / governance_cycle_id), sourced
      only from real DB rows — used for the Path-A end-to-end proof (T-G).

`verify` performs four real checks:

  1. verify_chain()  — recomputes event_hash for every real row in the live
     table and confirms (a) the row's own hash and (b) the
     previous_event_hash link to the prior row; also flags duplicate
     event_id values, duplicate non-null idempotency_key values, and
     impossible timestamp ordering (completed_at < started_at, or
     emitted_at < completed_at).
  2. insert_labeled_test_event() — inserts ONE real, clearly-labeled
     (is_test_record=True, reason_code='T-E_TAMPER_TEST') event through the
     real production emitter, so tamper checks run against a genuine
     disclosed TEST row rather than an arbitrary real production event.
  3. tamper_test_trigger_blocks_update() — attempts a REAL UPDATE (raw SQL,
     bypassing the app layer) against that TEST row inside a transaction
     that is unconditionally rolled back, proving the DB-level append-only
     trigger genuinely rejects mutation even when the app layer is
     bypassed. Never commits — zero permanent change to the table.
  4. tamper_test_hash_detection() — takes an in-memory COPY of that TEST
     row (no DB write at all), flips one field, and proves verify_chain()'s
     hash math flags it as a MISMATCH — the fallback proof that the hash
     chain itself would catch tampering even in a hypothetical scenario
     where the append-only trigger was bypassed entirely (e.g. a superuser
     doing a direct row edit outside Postgres's own trigger enforcement).

Exit code 0 = all real checks passed. Non-zero = a real problem was found.
"""
import sys
import copy
import hashlib
import datetime
import json
import argparse

import psycopg2
import psycopg2.extras

import aiem_diagram3_governance as d3gov


def _row_to_dict(cur, row):
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))


def _ts(v):
    """
    Match the exact string production had in hand at hash-computation time.
    `_d3_emit_event()` hashes `v.isoformat()` on whatever naive/aware
    datetime the caller passed in (usually naive `datetime.utcnow()`)
    BEFORE it goes into a TIMESTAMPTZ column. Reading it back later,
    psycopg2 returns a *timezone-aware* UTC datetime — an isoformat() of
    that produces a "+00:00" suffix the original hash never saw. To
    honestly re-derive the same bytes, tz-aware values read back from the
    DB must first be normalized to naive UTC.
    """
    if isinstance(v, datetime.datetime):
        if v.tzinfo is not None:
            v = v.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        return v.isoformat()
    if isinstance(v, datetime.date):
        return v.isoformat()
    return v


def _parse_dt(v):
    """Best-effort parse of a stored timestamp (already a datetime from
    psycopg2, or an isoformat string on an in-memory tampered copy) into a
    comparable naive-UTC datetime. Returns None if not parseable."""
    if v is None:
        return None
    if isinstance(v, datetime.datetime):
        if v.tzinfo is not None:
            return v.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        return v
    if isinstance(v, str):
        try:
            dt = datetime.datetime.fromisoformat(v)
            if dt.tzinfo is not None:
                dt = dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
            return dt
        except ValueError:
            return None
    return None


def recompute_event_hash(row: dict) -> str:
    """
    Recompute event_hash for one row using the SAME field set and
    canonicalization the production emitter used, selected by that row's
    own event_schema_version (defaulting to 1 for pre-migration rows that
    predate the column, matching production's own default behavior).
    """
    version = row.get("event_schema_version") or 1
    fields = d3gov._D3_EVENT_FIELDS_BY_VERSION.get(version)
    if fields is None:
        raise ValueError(
            f"row id={row.get('id')} has unknown event_schema_version={version} "
            "— no field set registered for it; cannot honestly verify."
        )
    payload = {}
    for k in fields + ["previous_event_hash"]:
        v = row.get(k)
        payload[k] = _ts(v)
    return hashlib.sha256(d3gov._canonical_bytes(payload)).hexdigest()


def verify_chain(rows: list) -> dict:
    """
    Walks the real chain in id order. Returns a report dict — never raises
    on a mismatch, so the caller gets a full list of every problem found
    rather than stopping at the first one. Checks per row:
      - previous_event_hash correctly links to the prior row's event_hash
      - event_hash recomputation matches the stored value
      - no duplicate event_id across the whole set
      - no duplicate non-null idempotency_key across the whole set
      - completed_at is not before started_at; emitted_at is not before
        completed_at (when both are present) — "impossible timestamp
        ordering"
    """
    mismatches = []
    prev_hash = "GENESIS"
    checked = 0

    seen_event_ids = {}
    seen_idempotency_keys = {}
    duplicate_event_ids = set()
    duplicate_idempotency_keys = set()
    for row in rows:
        eid = row.get("event_id")
        if eid:
            seen_event_ids.setdefault(eid, []).append(row.get("id"))
        ikey = row.get("idempotency_key")
        if ikey:
            seen_idempotency_keys.setdefault(ikey, []).append(row.get("id"))
    for eid, ids in seen_event_ids.items():
        if len(ids) > 1:
            duplicate_event_ids.add(eid)
    for ikey, ids in seen_idempotency_keys.items():
        if len(ids) > 1:
            duplicate_idempotency_keys.add(ikey)

    for row in rows:
        checked += 1
        problems = []

        if row.get("previous_event_hash") != prev_hash:
            problems.append({
                "type": "CHAIN_LINK_BROKEN",
                "expected_previous_event_hash": prev_hash,
                "actual_previous_event_hash": row.get("previous_event_hash"),
            })

        stored_hash = row.get("event_hash")
        try:
            recomputed = recompute_event_hash(row)
            if recomputed != stored_hash:
                problems.append({
                    "type": "EVENT_HASH_MISMATCH",
                    "stored_event_hash": stored_hash,
                    "recomputed_event_hash": recomputed,
                })
        except ValueError as e:
            problems.append({"type": "UNVERIFIABLE_SCHEMA_VERSION", "detail": str(e)})

        eid = row.get("event_id")
        if eid and eid in duplicate_event_ids:
            problems.append({
                "type": "DUPLICATE_EVENT_ID",
                "event_id": eid,
                "also_used_by_row_ids": [i for i in seen_event_ids[eid] if i != row.get("id")],
            })

        ikey = row.get("idempotency_key")
        if ikey and ikey in duplicate_idempotency_keys:
            problems.append({
                "type": "DUPLICATE_IDEMPOTENCY_KEY",
                "idempotency_key": ikey,
                "also_used_by_row_ids": [i for i in seen_idempotency_keys[ikey] if i != row.get("id")],
            })

        started = _parse_dt(row.get("started_at"))
        completed = _parse_dt(row.get("completed_at"))
        emitted = _parse_dt(row.get("emitted_at"))
        if started and completed and completed < started:
            problems.append({
                "type": "IMPOSSIBLE_TIMESTAMP_ORDER",
                "detail": "completed_at is before started_at",
                "started_at": str(started), "completed_at": str(completed),
            })
        if completed and emitted and emitted < completed:
            problems.append({
                "type": "IMPOSSIBLE_TIMESTAMP_ORDER",
                "detail": "emitted_at is before completed_at",
                "completed_at": str(completed), "emitted_at": str(emitted),
            })

        if problems:
            mismatches.append({
                "id": row.get("id"),
                "governance_trace_id": row.get("governance_trace_id"),
                "event_schema_version": row.get("event_schema_version"),
                "problems": problems,
            })

        # Chain continues from the row's OWN stored hash regardless of
        # whether it verified — a real chain walk must follow what's
        # actually on disk to correctly localize exactly where a forgery
        # started, not silently "correct" itself after one bad row.
        prev_hash = stored_hash

    return {
        "rows_checked": checked,
        "chain_intact": len(mismatches) == 0,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
    }


def insert_labeled_test_event(conn) -> dict:
    """
    Inserts ONE real, clearly-labeled TEST event through the actual
    production emitter (`_d3_emit_event`), so the tamper checks below run
    against a genuine, disclosed TEST row instead of an arbitrary real
    production event. This IS a real committed row — is_test_record=True,
    reason_code/reason_detail make it unambiguous, and it stays in the
    ledger forever per the append-only design (consistent with how
    verification events are already handled per T-C).
    """
    now = datetime.datetime.utcnow()
    row = d3gov._d3_emit_event(
        governance_cycle_id=f"T-E_VERIFY_{now.strftime('%Y%m%d%H%M%S')}",
        governance_phase="PHASE_0_BASELINE_FREEZE",
        governance_check_name="hash_chain_tamper_test",
        governance_function="aiem_diagram3_verification.insert_labeled_test_event",
        started_at=now,
        completed_at=now,
        check_result="RECORDED",
        reason_code="T-E_TAMPER_TEST",
        reason_detail="Synthetic event inserted solely to exercise the append-only "
                       "trigger and hash-mismatch detection logic; not a real "
                       "governance observation.",
        is_test_record=True,
        conn=conn,
    )
    conn.commit()
    return row


def tamper_test_trigger_blocks_update(conn, sample_row_id: int) -> dict:
    """
    Proves the REAL append-only trigger on the REAL table rejects mutation,
    even via raw SQL that bypasses the Python app layer entirely. Runs
    inside its own transaction that is unconditionally rolled back — even
    on success this makes zero permanent change to the live table.
    """
    result = {"check": "trigger_blocks_update", "sample_row_id": sample_row_id}
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE d3_governance_event_links SET reason_detail = %s WHERE id = %s",
                ("TAMPER_TEST_SHOULD_NEVER_PERSIST", sample_row_id),
            )
        # If we get here, the UPDATE was NOT blocked — a real problem.
        conn.rollback()
        result["trigger_blocked_update"] = False
        result["verdict"] = "FAIL — append-only trigger did not reject the UPDATE"
    except psycopg2.Error as e:
        conn.rollback()
        msg = str(e)
        result["trigger_blocked_update"] = "append-only" in msg.lower()
        result["db_error_message"] = msg.strip()
        result["verdict"] = (
            "PASS — real raw-SQL UPDATE attempt was rejected by the live DB trigger"
            if result["trigger_blocked_update"]
            else f"FAIL — UPDATE was rejected but not by the expected trigger: {msg}"
        )
    return result


def tamper_test_hash_detection(real_row: dict) -> dict:
    """
    In-memory-only proof (zero DB writes) that the hash math itself detects
    tampering. Takes a real row already fetched from the DB, flips one
    non-hash field on a COPY, and confirms verify_chain() flags it.
    """
    tampered = copy.deepcopy(real_row)
    original_value = tampered.get("check_result")
    tampered["check_result"] = "TAMPERED_" + str(original_value or "NONE")
    # event_hash is left untouched on purpose — this simulates an attacker
    # who edited a payload column but did not (or could not, without the
    # signing material) recompute a matching hash.
    report = verify_chain([tampered])
    detected = report["mismatch_count"] == 1 and any(
        p["type"] == "EVENT_HASH_MISMATCH"
        for p in report["mismatches"][0]["problems"]
    )
    return {
        "check": "in_memory_hash_mutation_detection",
        "row_id_used_as_template": real_row.get("id"),
        "field_tampered": "check_result",
        "original_value": original_value,
        "tampered_value": tampered["check_result"],
        "detected_as_mismatch": detected,
        "verdict": "PASS — hash math flags the tampered field" if detected
        else "FAIL — tampering was NOT detected (this would be a real bug)",
    }


def fetch_all_events(conn):
    cur = conn.cursor()
    cur.execute("SELECT * FROM d3_governance_event_links ORDER BY id ASC")
    rows = [_row_to_dict(cur, r) for r in cur.fetchall()]
    cur.close()
    return rows


def fetch_events_for_trace(conn, trace_id: str):
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM d3_governance_event_links
        WHERE governance_trace_id = %(t)s OR root_trace_id = %(t)s
           OR diagram1_trace_id = %(t)s OR diagram2_trace_id = %(t)s
           OR diagram3_trace_id = %(t)s OR governance_cycle_id = %(t)s
        ORDER BY id ASC
        """,
        {"t": trace_id},
    )
    rows = [_row_to_dict(cur, r) for r in cur.fetchall()]
    cur.close()
    return rows


def print_timeline(rows: list, trace_id: str):
    print(f"Real ordered event timeline for trace_id={trace_id!r} "
          f"({len(rows)} real row(s) found)\n")
    if not rows:
        print("NO REAL ROWS FOUND for this trace_id — nothing to print. "
              "This is reported honestly rather than fabricating a timeline.")
        return
    prev_completed = None
    for i, row in enumerate(rows, start=1):
        started = _parse_dt(row.get("started_at"))
        completed = _parse_dt(row.get("completed_at"))
        duration_ms = None
        if started and completed:
            duration_ms = round((completed - started).total_seconds() * 1000, 2)
        print(f"[{i}] id={row.get('id')} event_id={row.get('event_id')}")
        print(f"    event_type       = {row.get('event_type')}")
        print(f"    emitted_at       = {row.get('emitted_at')}")
        print(f"    diagram          = d2_phase={row.get('d2_phase')} d3_phase={row.get('d3_phase') or row.get('governance_phase')}")
        print(f"    module.function  = {row.get('producer_module') or row.get('governance_module')}."
              f"{row.get('producer_function') or row.get('governance_function')}"
              f" -> {row.get('consumer_module')}.{row.get('consumer_function')}")
        print(f"    input_ids        = {row.get('input_record_ids')}")
        print(f"    output_ids       = {row.get('output_record_ids')}")
        print(f"    check_result     = {row.get('check_result')}  "
              f"enforcement={row.get('enforcement_action')}/{row.get('enforcement_status')}")
        print(f"    duration_ms      = {duration_ms}")
        print(f"    is_test_record   = {row.get('is_test_record')}")
        print(f"    previous_event_hash = {row.get('previous_event_hash')}")
        print(f"    event_hash          = {row.get('event_hash')}")
        if prev_completed and started and started < prev_completed:
            print(f"    ** WARNING: this event's started_at ({started}) is before the "
                  f"previous event's completed_at ({prev_completed}) — out-of-order **")
        prev_completed = completed or prev_completed
        print()


def run_verify() -> int:
    conn = d3gov._d3_connect()

    rows = fetch_all_events(conn)
    print(f"Fetched {len(rows)} real rows from d3_governance_event_links.\n")

    chain_report = verify_chain(rows)
    print("── Check 1: real chain verification (hash, duplicates, timestamp order) ──")
    print(json.dumps(chain_report, indent=2, default=str))

    exit_code = 0
    if not chain_report["chain_intact"]:
        exit_code = 1

    print("\n── Check 2: insert one real, clearly-labeled TEST event ──")
    test_row = insert_labeled_test_event(conn)
    print(json.dumps({
        "id": test_row["id"], "event_id": test_row["event_id"],
        "is_test_record": test_row["is_test_record"],
        "reason_code": test_row["reason_code"],
    }, indent=2, default=str))

    print("\n── Check 3: append-only trigger tamper test (raw SQL UPDATE, rolled back) ──")
    trigger_report = tamper_test_trigger_blocks_update(conn, test_row["id"])
    print(json.dumps(trigger_report, indent=2, default=str))
    if not trigger_report.get("trigger_blocked_update"):
        exit_code = 1

    print("\n── Check 4: in-memory hash-mutation detection (zero DB writes) ──")
    hash_report = tamper_test_hash_detection(test_row)
    print(json.dumps(hash_report, indent=2, default=str))
    if not hash_report.get("detected_as_mismatch"):
        exit_code = 1

    conn.close()

    print(f"\nOverall exit_code={exit_code} "
          f"({'ALL REAL CHECKS PASSED' if exit_code == 0 else 'A REAL PROBLEM WAS FOUND'})")
    print(f"Note: the TEST event inserted in Check 2 (id={test_row['id']}) remains "
          f"permanently in the ledger per its append-only design — it is fully "
          f"disclosed via is_test_record=true and reason_code='T-E_TAMPER_TEST'.")
    return exit_code


def run_timeline(trace_id: str) -> int:
    conn = d3gov._d3_connect()
    rows = fetch_events_for_trace(conn, trace_id)
    conn.close()
    print_timeline(rows, trace_id)
    return 0 if rows else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("verify", help="Run full hash-chain verification + tamper tests")
    tl = sub.add_parser("timeline", help="Print real ordered event timeline for one trace")
    tl.add_argument("--trace-id", required=True)
    args = parser.parse_args()

    if args.command == "timeline":
        return run_timeline(args.trace_id)
    return run_verify()


if __name__ == "__main__":
    sys.exit(main())
