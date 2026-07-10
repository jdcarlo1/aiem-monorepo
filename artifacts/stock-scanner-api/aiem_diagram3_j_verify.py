"""
TEST J — AUTOMATIC TEST-PROPAGATION (Path B spec Section 10).

Spec text (paraphrased from the authoritative spec):
  "Start with one root event marked is_test_record=true. Confirm every
  downstream Diagram 3 record automatically inherits the test-record status
  and root_trace_id, without any intermediate caller having to pass it
  explicitly."

This harness proves the REAL contextvars-based propagation mechanism
(`trace_context` / `get_trace_context()` in aiem_diagram3_governance.py)
actually carries is_test_record + root_trace_id through every write helper
on the G0-G5 Path B surface -- not just the two that were already audited
and found correct in a prior session (require_governance_authorization,
acknowledge_governance_decision), but also request_governance_action, which
this session's audit found was NOT reading ambient trace_context at all
(it silently defaulted is_test_record to its own explicit-param-only
default) until the fix applied earlier in this same session.

What this proves, with real DB rows (never fabricated/assumed):
  1. One `with trace_context(root_trace_id=X, is_test_record=True):` block
     wraps THREE real write calls (G0 authorization, G1 authorization,
     request_governance_action) plus one acknowledgement, with ZERO
     explicit is_test_record/root_trace_id keyword arguments passed by
     this harness to any of them -- if propagation is broken, at least one
     resulting row will show is_test_record=False or a foreign root_trace_id.
  2. A CONTROL call to request_governance_action made immediately
     AFTER the `with` block exits (same process, same thread, no context
     manager active) must NOT inherit the just-exited context -- proving
     the contextvar is correctly scoped to the `with` block's lifetime,
     not leaked as global mutable state. This is the negative-control half
     of TEST J: propagation must be automatic INSIDE the block and absent
     OUTSIDE it.
  3. The one disclosed structural gap (d3_governance_actions has no
     trace_id/root_trace_id column) is checked as "N/A -- disclosed", never
     silently skipped or faked as a pass.

Every row queried below is a REAL row this harness's own calls just wrote
via the real production functions (not raw SQL, not synthetic fixtures) --
this exercises the exact code path a live D2 run would use.

Usage:
  python3 aiem_diagram3_j_verify.py
"""
import sys
import json
import uuid

import aiem_diagram3_governance as d3gov


def _fetch_one(conn, sql: str, params: tuple):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))


def run_test_j() -> int:
    root_trace_id = f"TESTJ_{uuid.uuid4().hex}"
    report = {"test": "TEST J — AUTOMATIC TEST-PROPAGATION", "root_trace_id": root_trace_id,
              "inside_context": {}, "outside_context_control": {}, "assertions": []}
    exit_code = 0

    print(f"root_trace_id under test: {root_trace_id}")
    print("Entering `with trace_context(root_trace_id=..., is_test_record=True):` block\n")

    with d3gov.trace_context(root_trace_id=root_trace_id, is_test_record=True):
        auth_g0 = d3gov.require_governance_authorization(
            checkpoint="G0", entrypoint="aiem_diagram3_j_verify",
            run_kind="SCAN_ONLY", requested_action="TEST_J_G0_PROPAGATION_CHECK",
        )
        auth_g1 = d3gov.require_governance_authorization(
            checkpoint="G1", entrypoint="aiem_diagram3_j_verify",
            run_kind="SCAN_ONLY", requested_action="TEST_J_G1_PROPAGATION_CHECK",
        )
        action = d3gov.request_governance_action(
            phase="TEST_J_VERIFY", action_type="TESTJ_PROPAGATION_CHECK",
            target_type="test_harness", target_id=root_trace_id,
            reason="TEST J — verifying request_governance_action inherits ambient "
                   "trace_context after this session's fix (no is_test_record kwarg "
                   "passed here on purpose)",
        )
        ack = d3gov.acknowledge_governance_decision(
            governance_decision_id=auth_g0["governance_decision_id"],
            action_taken="TEST_J_VERIFY_NOOP", continued=True, blocked=False,
            acknowledged_by="aiem_diagram3_j_verify",
        )

    print("Exited `with` block. Making CONTROL call with no active context...\n")
    control_action = d3gov.request_governance_action(
        phase="TEST_J_VERIFY", action_type="TESTJ_CONTROL_NO_CONTEXT",
        target_type="test_harness", target_id=root_trace_id,
        reason="TEST J control — same root_trace_id string passed only as target_id "
               "(inert data field), made OUTSIDE any trace_context block; must NOT "
               "inherit is_test_record=True or root_trace_id from the just-exited context",
    )

    report["inside_context"] = {
        "auth_g0": auth_g0, "auth_g1": auth_g1, "action": action, "ack": ack,
    }
    report["outside_context_control"] = {"control_action": control_action}

    conn = d3gov._d3_connect()

    def check(label, actual, expected, note=""):
        nonlocal exit_code
        ok = actual == expected
        verdict = "PASS" if ok else "FAIL"
        if not ok:
            exit_code = 1
        entry = {"label": label, "expected": expected, "actual": actual, "verdict": verdict, "note": note}
        report["assertions"].append(entry)
        print(f"  [{verdict}] {label}: expected={expected!r} actual={actual!r} {note}")
        return ok

    print("\n── d3_governance_requests (G0 + G1, written inside context) ──")
    req_g0 = _fetch_one(conn,
        "SELECT root_trace_id, is_test_record FROM d3_governance_requests WHERE governance_request_id = %s",
        (auth_g0["governance_request_id"],))
    req_g1 = _fetch_one(conn,
        "SELECT root_trace_id, is_test_record FROM d3_governance_requests WHERE governance_request_id = %s",
        (auth_g1["governance_request_id"],))
    check("G0 request root_trace_id inherited", req_g0["root_trace_id"] if req_g0 else None, root_trace_id)
    check("G0 request is_test_record inherited", req_g0["is_test_record"] if req_g0 else None, True)
    check("G1 request root_trace_id inherited", req_g1["root_trace_id"] if req_g1 else None, root_trace_id)
    check("G1 request is_test_record inherited", req_g1["is_test_record"] if req_g1 else None, True)

    print("\n── d3_governance_decisions (G0 + G1) ──")
    dec_g0 = _fetch_one(conn,
        "SELECT trace_id, is_test_record FROM d3_governance_decisions WHERE governance_decision_id = %s",
        (auth_g0["governance_decision_id"],))
    dec_g1 = _fetch_one(conn,
        "SELECT trace_id, is_test_record FROM d3_governance_decisions WHERE governance_decision_id = %s",
        (auth_g1["governance_decision_id"],))
    check("G0 decision trace_id inherited", dec_g0["trace_id"] if dec_g0 else None, root_trace_id)
    check("G0 decision is_test_record inherited", dec_g0["is_test_record"] if dec_g0 else None, True)
    check("G1 decision trace_id inherited", dec_g1["trace_id"] if dec_g1 else None, root_trace_id)
    check("G1 decision is_test_record inherited", dec_g1["is_test_record"] if dec_g1 else None, True)

    print("\n── d3_governance_event_links (G0 auth, G1 auth, request_governance_action) ──")
    ev_g0 = _fetch_one(conn,
        "SELECT root_trace_id, is_test_record FROM d3_governance_event_links WHERE id = %s",
        (auth_g0["ledger_event_id"],))
    ev_g1 = _fetch_one(conn,
        "SELECT root_trace_id, is_test_record FROM d3_governance_event_links WHERE id = %s",
        (auth_g1["ledger_event_id"],))
    ev_action = _fetch_one(conn,
        "SELECT root_trace_id, is_test_record FROM d3_governance_event_links WHERE id = %s",
        (action["governance_event_id"],))
    check("G0 ledger event root_trace_id inherited", ev_g0["root_trace_id"] if ev_g0 else None, root_trace_id)
    check("G0 ledger event is_test_record inherited", ev_g0["is_test_record"] if ev_g0 else None, True)
    check("G1 ledger event root_trace_id inherited", ev_g1["root_trace_id"] if ev_g1 else None, root_trace_id)
    check("G1 ledger event is_test_record inherited", ev_g1["is_test_record"] if ev_g1 else None, True)
    check("request_governance_action ledger event root_trace_id inherited (THIS SESSION'S FIX)",
          ev_action["root_trace_id"] if ev_action else None, root_trace_id)
    check("request_governance_action ledger event is_test_record inherited (THIS SESSION'S FIX)",
          ev_action["is_test_record"] if ev_action else None, True)

    print("\n── d3_governance_actions (request_governance_action's own tracking row) ──")
    act_row = _fetch_one(conn,
        "SELECT is_test_record FROM d3_governance_actions WHERE action_id = %s",
        (action["action_id"],))
    check("action row is_test_record inherited (THIS SESSION'S FIX)",
          act_row["is_test_record"] if act_row else None, True)
    report["assertions"].append({
        "label": "action row root_trace_id", "expected": "N/A", "actual": "N/A",
        "verdict": "N/A — DISCLOSED GAP",
        "note": "d3_governance_actions has no trace_id/root_trace_id column at all "
                "(structural, pre-existing, disclosed in request_governance_action's "
                "docstring) — root_trace_id propagation for this table is necessarily "
                "scoped to the ledger event row only (checked above), not this tracking row.",
    })
    print("  [N/A — DISCLOSED GAP] action row root_trace_id: d3_governance_actions has "
          "no trace_id/root_trace_id column (structural, disclosed)")

    print("\n── d3_governance_acks ──")
    ack_row = _fetch_one(conn,
        "SELECT trace_id, is_test_record FROM d3_governance_acks WHERE governance_ack_id = %s",
        (ack["governance_ack_id"],))
    check("ack trace_id inherited", ack_row["trace_id"] if ack_row else None, root_trace_id)
    check("ack is_test_record inherited", ack_row["is_test_record"] if ack_row else None, True)

    print("\n── CONTROL: outside-context call must NOT inherit the just-exited context ──")
    ctrl_act_row = _fetch_one(conn,
        "SELECT is_test_record FROM d3_governance_actions WHERE action_id = %s",
        (control_action["action_id"],))
    ctrl_ev_row = _fetch_one(conn,
        "SELECT root_trace_id, is_test_record FROM d3_governance_event_links WHERE id = %s",
        (control_action["governance_event_id"],))
    check("control action row is_test_record did NOT leak True",
          ctrl_act_row["is_test_record"] if ctrl_act_row else None, False,
          note="(proves the contextvar reset on __exit__, not leaked as global state)")
    check("control ledger event is_test_record did NOT leak True",
          ctrl_ev_row["is_test_record"] if ctrl_ev_row else None, False)
    check("control ledger event root_trace_id did NOT inherit the exited context's root_trace_id",
          (ctrl_ev_row["root_trace_id"] if ctrl_ev_row else None) != root_trace_id, True,
          note=f"actual root_trace_id column value={ctrl_ev_row['root_trace_id'] if ctrl_ev_row else None!r} "
               f"(expected: anything OTHER than {root_trace_id!r})")

    conn.close()

    print("\n" + "=" * 70)
    print(json.dumps(report, indent=2, default=str))
    print("=" * 70)
    n_fail = sum(1 for a in report["assertions"] if a["verdict"] == "FAIL")
    print(f"\n{n_fail} FAIL / {len(report['assertions'])} total assertions. "
          f"Overall exit_code={exit_code} "
          f"({'TEST J PASS — automatic propagation confirmed end-to-end with real rows' if exit_code == 0 else 'TEST J FAIL — see FAIL rows above'})")
    return exit_code


if __name__ == "__main__":
    sys.exit(run_test_j())
