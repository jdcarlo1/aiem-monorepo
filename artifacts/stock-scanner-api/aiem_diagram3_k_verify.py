"""
TEST K proof harness -- PAUSE / RECOVERY / RESUME, per spec Section 4/6/7/8
and the Section 12-13 addition ("no protected operation may resume without
RESUME authorization").

This complements aiem_diagram3_g5_verify.py (which already proves the real
HTTP route -> g5_authorize_resume -> require_governance_authorization ->
set_d3_system_state wiring for the resume step itself, K1/K2). What that
harness does NOT explicitly prove is the other half of TEST K's expected
sequence: "No protected execution may occur while PAUSED or
RECOVERY_REQUIRED." This script proves exactly that, end to end, by driving
the REAL production functions directly (same functions main.py's scheduler
call sites use for G0, e.g. the P3.5 premarket_open_tracker boot-authorization
checkpoint) -- not a reimplementation:

    require_governance_authorization(checkpoint="G0", run_kind="TRADE_EXECUTING", ...)
    set_d3_system_state(...)            (raw admin transition, unguarded exits)
    g5_authorize_resume(...)            (the one real resume entrypoint)
    set_d3_checkpoint_mode(...)         (forces G0/G5 into real ENFORCE for this run)

Full sequence driven and asserted:
    NORMAL  (G0 TRADE_EXECUTING must ALLOW)
     -> PAUSED                          (raw admin transition -- simulated incident)
        (G0 TRADE_EXECUTING must BLOCK)
     -> RECOVERY_REQUIRED               (raw admin transition, gated->gated, unguarded)
        (G0 TRADE_EXECUTING must BLOCK)
     -> verified recovery               (real g5_authorize_resume ALLOW,
                                          RECOVERY_VERIFIED + CHAIN_INTACT reason codes)
     -> NORMAL                          (G0 TRADE_EXECUTING must ALLOW again)

G0 and G5 are forced into real ENFORCE mode for the duration of this run
(both are SHADOW by default in this phased rollout) so the BLOCK/ALLOW
results above are real enforcement, not just advisory would_block logging --
otherwise TEST K's "no protected execution may occur" claim would not
actually be tested.

IMPORTANT standalone-process note (found and fixed during this harness's own
first run): G5's chain-verification decision (_evaluate_g5_decision) checks
an in-memory baseline hash (_D3_BASELINE_HASH) that is normally set once by
d3_startup() -> run_phase0_baseline_freeze() when the long-running stock-api
process boots. A bare `python3` script that only does `import
aiem_diagram3_governance` never runs that startup path, so
_D3_BASELINE_HASH is None in THIS process and G5's resume decision would
honestly report BASELINE_INVALID:IN_MEMORY_HASH_UNSET and BLOCK even though
the real chain is intact -- not a fabricated pass, but also not what TEST K
is trying to prove. This script calls the real run_phase0_baseline_freeze()
(force=False -- a cheap idempotent read since the baseline already exists;
it does NOT write, re-freeze, or touch d3_architecture_baseline) once at
startup to load the SAME real baseline hash into this process's memory
before exercising G5, so the resume decision below is a genuine, fully
verified ALLOW rather than an artifact of running outside the live process.

Restores G0's and G5's original modes and the system state to NORMAL in a
finally block even if an assertion fails midway; the finally block restores
checkpoint modes to SHADOW BEFORE attempting the final state restore, so a
stuck non-NORMAL state can always be recovered (SHADOW's g5_authorize_resume
always ALLOWs per this codebase's existing mode convention) even if
something upstream failed. All governance rows written are real, permanent,
honestly-tagged test data (is_test_record=True, trigger_source/reason
disclose this is a controlled TEST K proof harness), following the same
append-only ledger convention as every other verify script in this suite
(aiem_diagram3_i_verify.py, _j_verify.py, _g3/_g5_verify.py).

Run directly: python3 aiem_diagram3_k_verify.py
Safe to re-run any time as a regression check.
"""
import sys

import aiem_diagram3_governance as d3

ENTRYPOINT = "aiem_diagram3_k_verify"
CHANGED_BY = "aiem_diagram3_k_verify.py"


def hr(title):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def _g0_trade_executing(label):
    res = d3.require_governance_authorization(
        checkpoint="G0",
        entrypoint=ENTRYPOINT,
        run_kind="TRADE_EXECUTING",
        requested_action="TEST_K_PROTECTED_EXECUTION_PROBE",
        trigger_source="TEST_K_controlled_proof",
        is_test_record=True,
    )
    print(f"[{label}] G0 TRADE_EXECUTING -> decision={res.get('decision')} "
          f"would_block={res.get('would_block')} mode={res.get('mode')} "
          f"system_state={res.get('system_state')} "
          f"reason_codes={res.get('reason_codes') or res.get('reason_code')}")
    return res


def main():
    failures = []

    cfg_before = d3.get_d3_checkpoint_config()["checkpoints"]
    g0_mode_before = next(r["mode"] for r in cfg_before if r["checkpoint"] == "G0")
    g5_mode_before = next(r["mode"] for r in cfg_before if r["checkpoint"] == "G5")

    with d3._d3_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT state FROM d3_system_state WHERE id = 1")
            state_before = cur.fetchone()[0]

    print(f"[setup] G0 mode before = {g0_mode_before!r}, G5 mode before = {g5_mode_before!r}, "
          f"system_state before = {state_before!r}")

    if state_before != "NORMAL":
        print(f"FATAL: refusing to run against a live system that is not NORMAL "
              f"(state={state_before!r}) -- this harness mutates real system state.")
        sys.exit(2)

    # Load the REAL, already-frozen architecture baseline hash into this
    # standalone process's memory (see module docstring). force=False means
    # this is a pure idempotent read of the existing protected baseline row
    # -- it does not write or re-freeze anything.
    baseline = d3.run_phase0_baseline_freeze(force=False)
    print(f"[setup] baseline loaded into process memory: status={baseline.get('status')} "
          f"hash={(baseline.get('BASELINE_HASH') or '')[:16]}... "
          f"in_memory_hash_now={(d3._D3_BASELINE_HASH or '')[:16]}...")
    if not d3._D3_BASELINE_HASH:
        print("FATAL: could not load a real baseline hash into memory -- refusing to run "
              "TEST K's G5 verified-recovery step against an unset baseline.")
        sys.exit(2)

    try:
        d3.set_d3_checkpoint_mode(checkpoint="G0", mode="ENFORCE",
                                   reason="TEST K controlled proof run -- real enforcement of "
                                          "'no protected execution while PAUSED/RECOVERY_REQUIRED'",
                                   changed_by=CHANGED_BY, confirm=True)

        # ------------------------------------------------------------------
        hr("STEP 0 — BASELINE: NORMAL -> G0 TRADE_EXECUTING must ALLOW")
        # ------------------------------------------------------------------
        res0 = _g0_trade_executing("step0-NORMAL")
        ok0 = res0.get("decision") == "ALLOW" and res0.get("system_state") == "NORMAL"
        print(f"STEP 0 (baseline ALLOW while NORMAL) {'PASS' if ok0 else 'FAIL'}")
        if not ok0:
            failures.append("STEP 0 (baseline ALLOW)")

        # ------------------------------------------------------------------
        hr("STEP 1 — NORMAL -> PAUSED (simulated controlled critical condition)")
        # ------------------------------------------------------------------
        state_change_1 = d3.set_d3_system_state(
            state="PAUSED", reason="TEST K controlled proof -- simulate critical condition",
            changed_by=CHANGED_BY,
        )
        print(f"[step1] {state_change_1}")
        ok1_transition = state_change_1["old_state"] == "NORMAL" and state_change_1["new_state"] == "PAUSED"
        print(f"STEP 1 (NORMAL -> PAUSED transition) {'PASS' if ok1_transition else 'FAIL'}")
        if not ok1_transition:
            failures.append("STEP 1 (NORMAL -> PAUSED transition)")

        res1 = _g0_trade_executing("step1-PAUSED")
        ok1_block = (
            res1.get("decision") == "BLOCK"
            and res1.get("would_block") is True
            and res1.get("system_state") == "PAUSED"
            and res1.get("mode") == "ENFORCE"
        )
        print(f"STEP 1 (G0 BLOCKS TRADE_EXECUTING while PAUSED, real ENFORCE) "
              f"{'PASS' if ok1_block else 'FAIL'}")
        if not ok1_block:
            failures.append("STEP 1 (G0 blocks while PAUSED)")

        # ------------------------------------------------------------------
        hr("STEP 2 — PAUSED -> RECOVERY_REQUIRED (gated -> gated escalation, unguarded)")
        # ------------------------------------------------------------------
        state_change_2 = d3.set_d3_system_state(
            state="RECOVERY_REQUIRED", reason="TEST K controlled proof -- escalate to recovery",
            changed_by=CHANGED_BY,
        )
        print(f"[step2] {state_change_2}")
        ok2_transition = (state_change_2["old_state"] == "PAUSED"
                           and state_change_2["new_state"] == "RECOVERY_REQUIRED")
        print(f"STEP 2 (PAUSED -> RECOVERY_REQUIRED transition) {'PASS' if ok2_transition else 'FAIL'}")
        if not ok2_transition:
            failures.append("STEP 2 (PAUSED -> RECOVERY_REQUIRED transition)")

        res2 = _g0_trade_executing("step2-RECOVERY_REQUIRED")
        ok2_block = (
            res2.get("decision") == "BLOCK"
            and res2.get("would_block") is True
            and res2.get("system_state") == "RECOVERY_REQUIRED"
            and res2.get("mode") == "ENFORCE"
        )
        print(f"STEP 2 (G0 BLOCKS TRADE_EXECUTING while RECOVERY_REQUIRED, real ENFORCE) "
              f"{'PASS' if ok2_block else 'FAIL'}")
        if not ok2_block:
            failures.append("STEP 2 (G0 blocks while RECOVERY_REQUIRED)")

        # ------------------------------------------------------------------
        hr("STEP 3 — raw bypass attempt RECOVERY_REQUIRED -> NORMAL must be refused")
        # ------------------------------------------------------------------
        bypass_raised = False
        try:
            d3.set_d3_system_state(
                state="NORMAL", reason="TEST K - attempted raw bypass (must be refused)",
                changed_by=CHANGED_BY,
            )
        except ValueError as e:
            bypass_raised = True
            print(f"[step3] raw bypass correctly refused: {e}")
        ok3 = bypass_raised
        print(f"STEP 3 (raw bypass RECOVERY_REQUIRED -> NORMAL refused, fail-closed) "
              f"{'PASS' if ok3 else 'FAIL'}")
        if not ok3:
            failures.append("STEP 3 (raw bypass refused)")

        with d3._d3_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT state FROM d3_system_state WHERE id = 1")
                state_after_bypass_attempt = cur.fetchone()[0]
        ok3_state_unchanged = state_after_bypass_attempt == "RECOVERY_REQUIRED"
        print(f"STEP 3 (state unchanged after refused bypass) "
              f"{'PASS' if ok3_state_unchanged else 'FAIL'}")
        if not ok3_state_unchanged:
            failures.append("STEP 3 (state unchanged after refused bypass)")

        # ------------------------------------------------------------------
        hr("STEP 4 — REAL VERIFIED RECOVERY: g5_authorize_resume RECOVERY_REQUIRED -> NORMAL")
        # ------------------------------------------------------------------
        # G5 is forced to real ENFORCE too, so this ALLOW is a real enforced
        # verification, not SHADOW's always-ALLOW convention.
        d3.set_d3_checkpoint_mode(checkpoint="G5", mode="ENFORCE",
                                   reason="TEST K controlled proof run -- real resume verification",
                                   changed_by=CHANGED_BY, confirm=True)

        resume_result = d3.g5_authorize_resume(
            target_state="NORMAL", reason="TEST K - verified recovery resume",
            changed_by=CHANGED_BY,
        )
        print(f"[step4] {resume_result}")
        ok4 = (
            resume_result.get("decision") == "ALLOW"
            and "RECOVERY_VERIFIED" in (resume_result.get("reason_codes") or [])
            and any(str(rc).startswith("CHAIN_INTACT:") for rc in (resume_result.get("reason_codes") or []))
            and (resume_result.get("state_change") or {}).get("old_state") == "RECOVERY_REQUIRED"
            and (resume_result.get("state_change") or {}).get("new_state") == "NORMAL"
        )
        print(f"STEP 4 (verified recovery -> real ALLOW -> real state change to NORMAL) "
              f"{'PASS' if ok4 else 'FAIL'}")
        if not ok4:
            failures.append("STEP 4 (verified recovery)")

        # ------------------------------------------------------------------
        hr("STEP 5 — CONFIRM: NORMAL -> G0 TRADE_EXECUTING must ALLOW again")
        # ------------------------------------------------------------------
        res5 = _g0_trade_executing("step5-NORMAL-restored")
        ok5 = res5.get("decision") == "ALLOW" and res5.get("system_state") == "NORMAL"
        print(f"STEP 5 (protected execution resumes normally after verified recovery) "
              f"{'PASS' if ok5 else 'FAIL'}")
        if not ok5:
            failures.append("STEP 5 (execution resumes after recovery)")

    finally:
        # Restore checkpoint modes to their ORIGINAL values first, before
        # attempting any state recovery below. This matters: if something
        # upstream failed and left the system non-NORMAL while G5 is still
        # forced ENFORCE, a resume attempt could itself be (correctly)
        # BLOCKed by real verification, leaving production stuck. Restoring
        # to the original (SHADOW, in this codebase's default phased
        # rollout) mode FIRST guarantees a resume can always succeed
        # afterwards -- SHADOW's g5_authorize_resume always ALLOWs per this
        # codebase's existing mode convention (see g5_authorize_resume
        # docstring), so this is the correct, safe order for a controlled
        # test harness that must never leave live production stuck.
        d3.set_d3_checkpoint_mode(checkpoint="G0", mode=g0_mode_before,
                                   reason="restore after TEST K controlled proof run",
                                   changed_by=CHANGED_BY, confirm=(g0_mode_before == "ENFORCE"))
        d3.set_d3_checkpoint_mode(checkpoint="G5", mode=g5_mode_before,
                                   reason="restore after TEST K controlled proof run",
                                   changed_by=CHANGED_BY, confirm=(g5_mode_before == "ENFORCE"))

        with d3._d3_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT state FROM d3_system_state WHERE id = 1")
                final_state = cur.fetchone()[0]
        if final_state != "NORMAL":
            print(f"[teardown] state was left at {final_state!r} -- forcing back to NORMAL "
                  f"via g5_authorize_resume (checkpoint modes already restored to original)")
            try:
                d3.g5_authorize_resume(target_state="NORMAL", reason="teardown safety restore",
                                        changed_by=CHANGED_BY)
            except Exception as e:
                print(f"[teardown] g5_authorize_resume restore failed: {e} -- manual intervention "
                      f"IS required, NOT silently overriding via raw state setter")

        cfg_after = d3.get_d3_checkpoint_config()["checkpoints"]
        g0_mode_after = next(r["mode"] for r in cfg_after if r["checkpoint"] == "G0")
        g5_mode_after = next(r["mode"] for r in cfg_after if r["checkpoint"] == "G5")
        with d3._d3_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT state FROM d3_system_state WHERE id = 1")
                state_final = cur.fetchone()[0]
        print(f"\n[teardown] G0 mode restored to {g0_mode_after!r} "
              f"(matches before={g0_mode_after == g0_mode_before}); "
              f"G5 mode restored to {g5_mode_after!r} "
              f"(matches before={g5_mode_after == g5_mode_before}); "
              f"system_state = {state_final!r}")

    hr("SUMMARY")
    if failures:
        print(f"FAILED: {failures}")
        sys.exit(1)
    else:
        print("ALL TESTS (STEP 0-5, full NORMAL->PAUSED->RECOVERY_REQUIRED->verified "
              "recovery->NORMAL sequence + real G0 protected-execution blocking) PASSED")
        sys.exit(0)


if __name__ == "__main__":
    main()
