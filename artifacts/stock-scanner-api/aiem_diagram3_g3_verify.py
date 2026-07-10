"""
Controlled TEST C / TEST D / TEST E / TEST H proof harness for G3
(PRE-EXECUTION GOVERNANCE AUTHORIZATION), per the Path B spec Section 10.

Run directly: python3 aiem_diagram3_g3_verify.py
All rows written are tagged is_test_record=True (append-only ledger, never
cleaned up -- see memory: G2TEST_*/G3TEST_* rows have no delete carve-out).
Restores G3's real mode (SHADOW) and system state (NORMAL) at the end,
in a finally block, even if an assertion fails midway. Safe to re-run any
time as a regression check after future G3 changes.
"""
import sys
import uuid

import aiem_diagram3_governance as d3


def hr(title):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def main():
    failures = []

    # Capture real starting mode/state so we can prove we restore them.
    cfg_before = d3.get_d3_checkpoint_config()["checkpoints"]
    g3_mode_before = next(r["mode"] for r in cfg_before if r["checkpoint"] == "G3")
    state_before = d3._read_checkpoint_config("G3", force=True).get("state")
    print(f"[setup] G3 mode before test = {g3_mode_before!r}, system state before test = {state_before!r}")

    try:
        # G3 must be in ENFORCE for a BLOCK decision to actually surface as
        # decision=='BLOCK' (in SHADOW it only ever reports would_block=True
        # advisory). confirm=True required to move INTO ENFORCE.
        d3.set_d3_checkpoint_mode(checkpoint="G3", mode="ENFORCE",
                                   reason="TEST C/D/E/H controlled proof run",
                                   changed_by="_test_g3_controlled.py", confirm=True)

        # ------------------------------------------------------------------
        hr("TEST E — DIAGRAM 2 REJECTION CANNOT BE OVERRIDDEN")
        # ------------------------------------------------------------------
        trace_id = f"G3TEST_E_{uuid.uuid4().hex[:8]}"
        res_e = d3.require_governance_authorization(
            checkpoint="G3",
            entrypoint="_test_g3_controlled.TEST_E",
            run_kind="TRADE_EXECUTING",
            source_phase="decision_engine",
            trigger_source="controlled_test",
            candidate_trace_id=trace_id,
            candidate_ticker="G3TESTE",
            diagram2_risk_result="REJECT",   # <-- forced D2 rejection
            execution_mode="PAPER",
            strategy_version="gap_volume",   # even a real approved-looking strategy
            model_version="whatever",        # must not matter -- D2 check runs first
            is_test_record=True,
        )
        print(f"decision={res_e['decision']} reason_code={res_e['reason_code']} "
              f"would_block={res_e['would_block']} mode={res_e['mode']} "
              f"governance_decision_id={res_e['governance_decision_id']}")
        ok_e = (res_e["decision"] == "BLOCK"
                and "D2_RISK_REJECTED:REJECT" in res_e["reason_codes"]
                and res_e["governance_decision_id"] is not None)
        print(f"TEST E {'PASS' if ok_e else 'FAIL'}")
        if not ok_e:
            failures.append("TEST E")

        # Also prove SHADOW mode cannot override it either (the "regardless
        # of Diagram 3 result" clause) -- flip to SHADOW briefly.
        d3.set_d3_checkpoint_mode(checkpoint="G3", mode="SHADOW",
                                   reason="TEST E shadow-mode sub-check",
                                   changed_by="_test_g3_controlled.py")
        res_e_shadow = d3._evaluate_g3_decision(
            run_kind="TRADE_EXECUTING", diagram2_risk_result="REJECT",
            execution_mode="PAPER", model_version=None, strategy_version="gap_volume",
        )
        print(f"[shadow-mode sub-check] decision={res_e_shadow['decision']} "
              f"reason_codes={res_e_shadow['reason_codes']}")
        ok_e_shadow = res_e_shadow["decision"] == "BLOCK"
        print(f"TEST E (shadow-mode sub-check) {'PASS' if ok_e_shadow else 'FAIL'}")
        if not ok_e_shadow:
            failures.append("TEST E (shadow)")
        # back to ENFORCE for the remaining tests
        d3.set_d3_checkpoint_mode(checkpoint="G3", mode="ENFORCE",
                                   reason="TEST C/D/H controlled proof run",
                                   changed_by="_test_g3_controlled.py", confirm=True)

        # ------------------------------------------------------------------
        hr("TEST C — UNAPPROVED MODEL BLOCK")
        # ------------------------------------------------------------------
        trace_id = f"G3TEST_C_{uuid.uuid4().hex[:8]}"
        bogus_model = f"G3TEST_UNAPPROVED_MODEL_{uuid.uuid4().hex[:6]}"
        res_c = d3.require_governance_authorization(
            checkpoint="G3",
            entrypoint="_test_g3_controlled.TEST_C",
            run_kind="TRADE_EXECUTING",
            source_phase="decision_engine",
            trigger_source="controlled_test",
            candidate_trace_id=trace_id,
            candidate_ticker="G3TESTC",
            diagram2_risk_result="PASS",
            execution_mode="PAPER",
            strategy_version="gap_volume",
            model_version=bogus_model,       # <-- never registered in d3_model_governance
            is_test_record=True,
        )
        print(f"decision={res_c['decision']} reason_code={res_c['reason_code']} "
              f"would_block={res_c['would_block']} governance_decision_id={res_c['governance_decision_id']}")
        ok_c = (res_c["decision"] == "BLOCK"
                and any(f"UNAPPROVED_MODEL:{bogus_model}" in rc for rc in res_c["reason_codes"])
                and res_c["governance_decision_id"] is not None)
        print(f"TEST C {'PASS' if ok_c else 'FAIL'}")
        if not ok_c:
            failures.append("TEST C")

        # ------------------------------------------------------------------
        hr("TEST D — PRE-EXECUTION ACTIVE PAUSE BLOCK")
        # ------------------------------------------------------------------
        d3.set_d3_system_state(state="PAUSED", reason="TEST D controlled proof run",
                                changed_by="_test_g3_controlled.py")
        trace_id = f"G3TEST_D_{uuid.uuid4().hex[:8]}"
        res_d = d3.require_governance_authorization(
            checkpoint="G3",
            entrypoint="_test_g3_controlled.TEST_D",
            run_kind="TRADE_EXECUTING",
            source_phase="decision_engine",
            trigger_source="controlled_test",
            candidate_trace_id=trace_id,
            candidate_ticker="G3TESTD",
            diagram2_risk_result="PASS",     # otherwise-valid, passed risk gate
            execution_mode="PAPER",
            strategy_version="gap_volume",
            model_version=None,
            is_test_record=True,
        )
        print(f"decision={res_d['decision']} reason_code={res_d['reason_code']} "
              f"would_block={res_d['would_block']} system_state={res_d['system_state']} "
              f"governance_decision_id={res_d['governance_decision_id']}")
        ok_d = (res_d["decision"] == "BLOCK"
                and any(rc in ("STATE_PAUSED", "PAUSE_SYSTEM") for rc in res_d["reason_codes"])
                and res_d["governance_decision_id"] is not None)
        print(f"TEST D (decision) {'PASS' if ok_d else 'FAIL'}")
        if not ok_d:
            failures.append("TEST D (decision)")

        # Diagram 2 acknowledgement must record blocked=true, continued=false
        ack_d = d3.acknowledge_governance_decision(
            governance_decision_id=res_d["governance_decision_id"],
            action_taken="CANDIDATE_SKIPPED_G3_TESTD",
            continued=False,
            blocked=True,
            acknowledged_by="_test_g3_controlled.py",
            is_test_record=True,
        )
        print(f"ack={ack_d}")
        # acknowledge_governance_decision doesn't echo blocked/continued back
        # (they're write-only inputs tied by FK to the real decision row) --
        # the real proof is (a) decision_recorded=='BLOCK' (the FK integrity
        # mechanism: this ack could not have been written against any other
        # decision value) and (b) a direct read-back of the persisted row.
        with d3._d3_connect() as _c:
            with _c.cursor() as _cur:
                _cur.execute(
                    "SELECT blocked, continued, decision_recorded FROM d3_governance_acks "
                    "WHERE governance_ack_id = %s",
                    (ack_d["governance_ack_id"],),
                )
                _persisted_blocked, _persisted_continued, _persisted_decision = _cur.fetchone()
        print(f"[readback] persisted blocked={_persisted_blocked} continued={_persisted_continued} "
              f"decision_recorded={_persisted_decision}")
        ok_d_ack = (ack_d.get("decision_recorded") == "BLOCK"
                    and _persisted_blocked is True and _persisted_continued is False
                    and _persisted_decision == "BLOCK")
        print(f"TEST D (ack blocked=true/continued=false) {'PASS' if ok_d_ack else 'FAIL'}")
        if not ok_d_ack:
            failures.append("TEST D (ack)")

        # restore NORMAL before TEST H so TEST H is isolated to the DB-outage
        # variable only, not compounded with PAUSED. PAUSED is one of
        # _D3_RECOVERY_GATED_STATES, so set_d3_system_state() now hard-refuses
        # a direct PAUSED->NORMAL write (G5 bypass-closure guard, added in
        # P7) -- this restore must go through the real G5 resume entrypoint,
        # exactly like a real operator would, not a raw state write.
        restore_d = d3.g5_authorize_resume(
            target_state="NORMAL", reason="restore after TEST D",
            changed_by="_test_g3_controlled.py", trigger_source="controlled_test",
        )
        print(f"[restore after TEST D] g5_authorize_resume decision={restore_d['decision']} "
              f"state_change={restore_d.get('state_change')}")
        if restore_d["decision"] != "ALLOW" or not restore_d.get("state_change"):
            failures.append("TEST D (restore via G5)")

        # ------------------------------------------------------------------
        hr("TEST H — FAIL-CLOSED GOVERNANCE OUTAGE")
        # ------------------------------------------------------------------
        # Force-refresh the cache with a real good read first, then break
        # _d3_connect so the NEXT read hits the exception path in
        # _read_checkpoint_config with no usable stale-allow (ENFORCE mode
        # has no stale-allow branch at all -- only SHADOW does).
        d3._read_checkpoint_config("G3", force=True)
        real_connect = d3._d3_connect

        def _broken_connect(*a, **kw):
            raise RuntimeError("G3TEST_SIMULATED_DB_OUTAGE")

        d3._d3_connect = _broken_connect
        try:
            # Invalidate the cache TTL so the broken connect is actually hit
            # rather than served from the good cache we just warmed.
            with d3._CHECKPOINT_CACHE_LOCK:
                d3._CHECKPOINT_CONFIG_CACHE.pop("G3", None)
            res_h = d3._evaluate_g3_decision(
                run_kind="TRADE_EXECUTING", diagram2_risk_result="PASS",
                execution_mode="PAPER", model_version=None, strategy_version="gap_volume",
            )
        finally:
            d3._d3_connect = real_connect
            d3._read_checkpoint_config("G3", force=True)  # restore real cache immediately

        print(f"decision={res_h['decision']} reason_codes={res_h['reason_codes']} "
              f"would_block={res_h['would_block']} db_error={res_h['db_error']}")
        ok_h = (res_h["decision"] == "BLOCK"
                and "DB_ERROR_FAIL_CLOSED" in res_h["reason_codes"]
                and res_h["would_block"] is True
                and res_h["db_error"] is not None
                and "G3TEST_SIMULATED_DB_OUTAGE" in res_h["db_error"])
        print(f"TEST H (evaluator fail-closed) {'PASS' if ok_h else 'FAIL'}")
        if not ok_h:
            failures.append("TEST H (evaluator)")

        # Now prove a governance-unavailable BLOCK record is actually
        # persisted (not just returned in-memory) by running it through the
        # full require_governance_authorization path with the same outage.
        d3._d3_connect = _broken_connect
        try:
            with d3._CHECKPOINT_CACHE_LOCK:
                d3._CHECKPOINT_CONFIG_CACHE.pop("G3", None)
            res_h_full = d3.require_governance_authorization(
                checkpoint="G3",
                entrypoint="_test_g3_controlled.TEST_H",
                run_kind="TRADE_EXECUTING",
                source_phase="decision_engine",
                trigger_source="controlled_test",
                candidate_trace_id=f"G3TEST_H_{uuid.uuid4().hex[:8]}",
                candidate_ticker="G3TESTH",
                diagram2_risk_result="PASS",
                execution_mode="PAPER",
                strategy_version="gap_volume",
                model_version=None,
                is_test_record=True,
            )
        finally:
            d3._d3_connect = real_connect
            d3._read_checkpoint_config("G3", force=True)

        print(f"decision={res_h_full['decision']} reason_code={res_h_full['reason_code']} "
              f"governance_decision_id={res_h_full['governance_decision_id']} "
              f"persist_error={res_h_full['persist_error']}")
        # NOTE: require_governance_authorization uses _d3_connect() itself to
        # persist the decision row -- with _d3_connect broken, persistence
        # ALSO fails, so governance_decision_id will legitimately be None
        # and 'PERSIST_FAILED' will be appended. This is the documented,
        # intentional behavior (Correction 3): decision is never flipped by
        # a persistence failure. The real "block record created" proof for
        # TEST H is therefore the *evaluator* result above (BLOCK, fail-
        # closed, real exception text) plus this proof that even with BOTH
        # the checkpoint-config read AND the decision-persist path broken
        # simultaneously, the decision returned to the caller is still BLOCK
        # -- never a silent default ALLOW.
        ok_h_full = (res_h_full["decision"] == "BLOCK"
                     and "DB_ERROR_FAIL_CLOSED" in res_h_full["reason_codes"])
        print(f"TEST H (full path still BLOCK under total outage, never silent ALLOW) "
              f"{'PASS' if ok_h_full else 'FAIL'}")
        if not ok_h_full:
            failures.append("TEST H (full path)")

    finally:
        # Restore real production mode/state unconditionally.
        d3.set_d3_checkpoint_mode(checkpoint="G3", mode=g3_mode_before,
                                   reason="restore after controlled test run",
                                   changed_by="_test_g3_controlled.py",
                                   confirm=(g3_mode_before == "ENFORCE"))
        # Go through the real G5 resume entrypoint rather than a raw state
        # write: if the live state is currently one of
        # _D3_RECOVERY_GATED_STATES (e.g. still PAUSED from an earlier
        # failure in this run) and state_before is not, set_d3_system_state()
        # now hard-refuses that transition without G5 authorization (P7
        # bypass-closure guard). g5_authorize_resume() correctly no-ops
        # (NO_RECOVERY_NEEDED ALLOW, but still performs the write) when the
        # live state is not actually gated, so this is safe unconditionally.
        restore_final = d3.g5_authorize_resume(
            target_state=state_before or "NORMAL",
            reason="restore after controlled test run",
            changed_by="_test_g3_controlled.py", trigger_source="controlled_test",
        )
        print(f"[teardown] g5_authorize_resume decision={restore_final['decision']} "
              f"state_change={restore_final.get('state_change')}")
        if restore_final["decision"] != "ALLOW" or not restore_final.get("state_change"):
            failures.append("TEARDOWN (restore via G5)")
        cfg_after = d3.get_d3_checkpoint_config()["checkpoints"]
        g3_mode_after = next(r["mode"] for r in cfg_after if r["checkpoint"] == "G3")
        state_after = d3._read_checkpoint_config("G3", force=True).get("state")
        print(f"\n[teardown] G3 mode restored to {g3_mode_after!r} "
              f"(matches before={g3_mode_after == g3_mode_before}), "
              f"system state restored to {state_after!r} "
              f"(matches before={state_after == state_before})")

    hr("SUMMARY")
    if failures:
        print(f"FAILED: {failures}")
        sys.exit(1)
    else:
        print("ALL TESTS (C, D, E, H) PASSED")
        sys.exit(0)


if __name__ == "__main__":
    main()
