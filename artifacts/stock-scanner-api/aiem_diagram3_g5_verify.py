"""
Controlled TEST K proof harness for G5 (RECOVERY AND RESUMPTION), per the
Path B spec Section 4/6/7/8 and the Section 12-13 addition (Recovery
Orchestrator contacts D3_GOVERNANCE_SERVICE at G5; "no protected operation
may resume without RESUME authorization").

Drives the ACTUAL live Flask admin endpoints over real HTTP against the
running stock-api process:
    GET  /stock-api/admin/d3/g5/status
    POST /stock-api/admin/d3/g5/resume
    POST /stock-api/admin/d3/g0/system-state   (used only to PROVE the raw
                                                 bypass is refused, and to
                                                 seed PAUSED/RECOVERY_REQUIRED
                                                 as the "incident" precondition
                                                 -- entering a gated state is
                                                 not itself a resume)
so this proves the real wiring (route -> g5_authorize_resume ->
require_governance_authorization -> set_d3_system_state guard), not just the
policy math in isolation.

TEST K1 (happy path): NORMAL -> PAUSED (raw, unguarded) -> attempt raw
bypass PAUSED -> NORMAL (must be refused, 400) -> real G5 resume (must
ALLOW, real state change to NORMAL, real full-chain verification ran).

TEST K2 (blocked path, ENFORCE): NORMAL -> RECOVERY_REQUIRED, with one real
REQUESTED CRITICAL-type row seeded in d3_governance_actions (the same
unresolved-actions gate G3 already enforces) -> G5 resume attempt must
BLOCK (403) in ENFORCE mode and the state must NOT change -> resolve the
action for real via check_action_status() -> retry resume -> must ALLOW,
real state change to NORMAL.

All rows written are real, permanent, honestly-tagged test data
(action_type prefixed G5TEST_CRITICAL_, reason/notes disclose this is a
controlled proof harness -- append-only ledger convention, same as
G2TEST_*/G3TEST_*/G4TEST_*). Restores G5's real mode and the system state to
NORMAL at the end, in a finally block, even if an assertion fails midway.
Safe to re-run any time as a regression check after future G5 changes.

Run directly: python3 aiem_diagram3_g5_verify.py
Requires ADMIN_TOKEN in the environment and the stock-api process already
running on STOCK_API_PORT (default 5050).
"""
import os
import sys

import requests

import aiem_diagram3_governance as d3

BASE_URL = f"http://127.0.0.1:{os.environ.get('STOCK_API_PORT', 5050)}"
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
HEADERS = {"X-Admin-Token": ADMIN_TOKEN, "Content-Type": "application/json"}


def hr(title):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def _get(path):
    resp = requests.get(f"{BASE_URL}{path}", headers=HEADERS, timeout=15)
    try:
        return resp.status_code, resp.json()
    except Exception:
        return resp.status_code, {"_raw": resp.text}


def _post(path, body):
    resp = requests.post(f"{BASE_URL}{path}", headers=HEADERS, json=body, timeout=20)
    try:
        return resp.status_code, resp.json()
    except Exception:
        return resp.status_code, {"_raw": resp.text}


def _set_state_raw(state, reason):
    return _post("/stock-api/admin/d3/g0/system-state",
                  {"state": state, "reason": reason, "changed_by": "aiem_diagram3_g5_verify.py"})


def _resume(target_state, reason):
    return _post("/stock-api/admin/d3/g5/resume",
                  {"target_state": target_state, "reason": reason,
                   "changed_by": "aiem_diagram3_g5_verify.py"})


def _current_state():
    status, body = _get("/stock-api/admin/d3/g5/status")
    if status != 200:
        raise RuntimeError(f"could not read g5 status: {status} {body}")
    return body["system_state"]["state"]


def main():
    if not ADMIN_TOKEN:
        print("FATAL: ADMIN_TOKEN not set in environment -- cannot drive the real admin endpoint.")
        sys.exit(2)

    failures = []

    cfg_before = d3.get_d3_checkpoint_config()["checkpoints"]
    g5_mode_before = next(r["mode"] for r in cfg_before if r["checkpoint"] == "G5")
    state_before = _current_state()
    print(f"[setup] G5 mode before test = {g5_mode_before!r}, system_state before = {state_before!r}")

    if state_before != "NORMAL":
        print(f"FATAL: refusing to run against a live system that is not NORMAL "
              f"(state={state_before!r}) -- this harness mutates real system state.")
        sys.exit(2)

    try:
        d3.set_d3_checkpoint_mode(checkpoint="G5", mode="ENFORCE",
                                   reason="TEST K controlled proof run",
                                   changed_by="aiem_diagram3_g5_verify.py", confirm=True)

        # ------------------------------------------------------------------
        hr("TEST K1 — HAPPY PATH: NORMAL -> PAUSED -> raw bypass refused -> G5 resume ALLOWS")
        # ------------------------------------------------------------------
        status_pause, body_pause = _set_state_raw("PAUSED", "TEST K1 - simulate incident")
        print(f"[K1] set PAUSED: HTTP {status_pause} body={body_pause}")

        status_bypass, body_bypass = _set_state_raw("NORMAL", "TEST K1 - attempted raw bypass")
        print(f"[K1] raw bypass PAUSED->NORMAL: HTTP {status_bypass} body={body_bypass}")
        state_after_bypass_attempt = _current_state()
        print(f"[K1] state after bypass attempt = {state_after_bypass_attempt!r}")

        ok_bypass_refused = (
            status_bypass == 400
            and "g5 recovery authorization" in str(body_bypass.get("error", "")).lower()
            and state_after_bypass_attempt == "PAUSED"
        )
        print(f"TEST K1 (raw bypass refused) {'PASS' if ok_bypass_refused else 'FAIL'}")
        if not ok_bypass_refused:
            failures.append("TEST K1 (raw bypass refused)")

        status_resume1, body_resume1 = _resume("NORMAL", "TEST K1 - authorized resume")
        print(f"[K1] G5 resume PAUSED->NORMAL: HTTP {status_resume1} body={body_resume1}")
        state_after_resume1 = _current_state()
        print(f"[K1] state after authorized resume = {state_after_resume1!r}")

        ok_k1_resume = (
            status_resume1 == 200
            and body_resume1.get("decision") == "ALLOW"
            and "RECOVERY_VERIFIED" in body_resume1.get("reason_codes", [])
            and any(str(rc).startswith("CHAIN_INTACT:") for rc in body_resume1.get("reason_codes", []))
            and body_resume1.get("state_change", {}).get("old_state") == "PAUSED"
            and body_resume1.get("state_change", {}).get("new_state") == "NORMAL"
            and state_after_resume1 == "NORMAL"
            and body_resume1.get("governance_decision_id") is not None
            and body_resume1.get("ledger_event_id") is not None
        )
        print(f"TEST K1 (authorized resume, real chain verification, real state change) "
              f"{'PASS' if ok_k1_resume else 'FAIL'}")
        if not ok_k1_resume:
            failures.append("TEST K1 (authorized resume)")

        # ------------------------------------------------------------------
        hr("TEST K2 — ENFORCE BLOCKS RESUME ON REAL UNRESOLVED CRITICAL ACTION, "
           "THEN ALLOWS AFTER REAL RESOLUTION")
        # ------------------------------------------------------------------
        status_recov, body_recov = _set_state_raw("RECOVERY_REQUIRED", "TEST K2 - simulate incident requiring recovery")
        print(f"[K2] set RECOVERY_REQUIRED: HTTP {status_recov} body={body_recov}")

        action_result = d3.request_governance_action(
            phase="P7_G5_TEST_K2",
            action_type="G5TEST_CRITICAL_UNRESOLVED_INCIDENT",
            target_type="system",
            target_id="TEST_K2",
            reason="G5TEST controlled proof harness row -- real unresolved CRITICAL action "
                   "seeded to prove G5 resume is blocked while it stays REQUESTED",
            is_test_record=True,
        )
        print(f"[K2] seeded unresolved action: {action_result}")
        ok_action_seeded = action_result.get("requested") is True
        if not ok_action_seeded:
            failures.append("TEST K2 (seed unresolved action)")

        status_blocked, body_blocked = _resume("NORMAL", "TEST K2 - resume attempt while action unresolved")
        print(f"[K2] G5 resume attempt (should BLOCK): HTTP {status_blocked} body={body_blocked}")
        state_after_blocked = _current_state()
        print(f"[K2] state after blocked attempt = {state_after_blocked!r}")

        ok_k2_blocked = (
            status_blocked == 403
            and body_blocked.get("decision") == "BLOCK"
            and any("UNRESOLVED_ACTIONS" in str(rc) for rc in body_blocked.get("reason_codes", []))
            and body_blocked.get("state_change") is None
            and state_after_blocked == "RECOVERY_REQUIRED"
        )
        print(f"TEST K2 (blocked while unresolved) {'PASS' if ok_k2_blocked else 'FAIL'}")
        if not ok_k2_blocked:
            failures.append("TEST K2 (blocked while unresolved)")

        resolve_result = d3.check_action_status(action_result.get("action_id", ""))
        print(f"[K2] resolved action: {resolve_result}")

        status_resume2, body_resume2 = _resume("NORMAL", "TEST K2 - authorized resume after resolution")
        print(f"[K2] G5 resume after resolution: HTTP {status_resume2} body={body_resume2}")
        state_after_resume2 = _current_state()
        print(f"[K2] state after resolved resume = {state_after_resume2!r}")

        ok_k2_allowed = (
            status_resume2 == 200
            and body_resume2.get("decision") == "ALLOW"
            and body_resume2.get("state_change", {}).get("old_state") == "RECOVERY_REQUIRED"
            and body_resume2.get("state_change", {}).get("new_state") == "NORMAL"
            and state_after_resume2 == "NORMAL"
        )
        print(f"TEST K2 (allowed after real resolution) {'PASS' if ok_k2_allowed else 'FAIL'}")
        if not ok_k2_allowed:
            failures.append("TEST K2 (allowed after resolution)")

    finally:
        final_state = _current_state()
        if final_state != "NORMAL":
            print(f"[teardown] state was left at {final_state!r} -- forcing back to NORMAL via g5_authorize_resume")
            d3.g5_authorize_resume(target_state="NORMAL", reason="teardown safety restore",
                                    changed_by="aiem_diagram3_g5_verify.py")
        d3.set_d3_checkpoint_mode(checkpoint="G5", mode=g5_mode_before,
                                   reason="restore after controlled test run",
                                   changed_by="aiem_diagram3_g5_verify.py",
                                   confirm=(g5_mode_before == "ENFORCE"))
        cfg_after = d3.get_d3_checkpoint_config()["checkpoints"]
        g5_mode_after = next(r["mode"] for r in cfg_after if r["checkpoint"] == "G5")
        print(f"\n[teardown] G5 mode restored to {g5_mode_after!r} "
              f"(matches before={g5_mode_after == g5_mode_before}); "
              f"system_state = {_current_state()!r}")

    hr("SUMMARY")
    if failures:
        print(f"FAILED: {failures}")
        sys.exit(1)
    else:
        print("ALL TESTS (K1, K2) PASSED")
        sys.exit(0)


if __name__ == "__main__":
    main()
