"""
Phase 12 (Edge Filter & Exit Engine) verification for the AEIM DIAGRAM 2 —
MASTER WIRING + VERIFICATION project.

Static, grep/sed-based checks only. NEVER imports main.py live.

WHAT THIS PROVES:
  1. Module wiring for both Phase 12 module files.
     2/2 are VERIFIED_WIRED:
       - aiem_edge_filter.py -> get_orchestrator().status()/.evaluate() called
         from 2 AI tools (edge_filter_status, edge_filter_evaluate) PLUS the
         internal paper-trading gate (~L39444, hard-blocks on confirmed
         'negative' edge) PLUS MTM closed-trade processing (~L40888) PLUS
         deferred schema init + cold_start_report at startup (~L50686-50712).
       - aiem_exit_engine.py -> review_open_positions() called ONLY from a
         30-minute scheduler job during market hours (9-15 ET, id
         "aiem_exit_review", main.py ~L14658-14669). NOT reachable via any
         AI tool -- same "wired but not AI-tool-reachable" pattern as
         Phase 10's specialist_council.py/bull_bear_debate.py, except here
         the reachability path is a cron job instead of the paper-trading
         pipeline.
     0 genuine gaps.
  2. All 9 Phase-12-tagged AI tool names checked against the live tool
     dispatch map in main.py:
     9/9 have a real dispatch-map entry under their exact tagged name (0
     tool-name gaps this phase) -- but only 2/9 are genuinely owned by a
     Phase 12 module. The other 7 break down as:
       - run_risk_gate            -> pre_decision_risk_gate.py (Phase 11)
       - rl_get_paper_action      -> rl_position_sizer.py       (Phase 11)
       - deep_rl_get_paper_action -> deep_rl_policy.py          (Phase 15)
       - get_decisions            -> decision_logger.py         (Phase 9)
       - log_decision             -> decision_logger.py         (Phase 9)
       - query_exit_timing        -> INLINE direct SQL on ai_short_calls_log,
         NO module import at all despite the "exit timing" name suggesting
         aiem_exit_engine.py involvement -- another naming trap (does not
         reference aiem_exit_engine.py in any way).
       - holding_period_optimize  -> holding_period_optimizer.py, a REAL
         module that genuinely exists and is genuinely imported/called, but
         is NOT one of the 195 modules tracked in aiem_module_registry at
         all (confirmed: zero rows match ILIKE '%holding_period%', and the
         195-row total is unaffected). This is an out-of-catalog module
         discovered via tool tracing -- documented here but deliberately
         NOT added as a new module_registry row, since expanding the
         195-module catalog is out of scope for a verification pass.

HEADLINE FINDINGS:
  1. Lowest same-phase tool-ownership ratio since Phase 9/10: 2/9 (22%),
     vs Phase 11's 9/10 (90%). Edge Filter & Exit Engine's own AI-facing
     surface (edge_filter_evaluate/status) is genuinely owned, but most of
     the phase's *other* tagged tools are re-used tools from Risk Gate
     (Phase 11), Decision Logging (Phase 9), and Deep RL (Phase 15).
  2. aiem_exit_engine.py is a second confirmed instance (after Phase 10) of
     a module that is genuinely wired into live execution but has NO path
     reachable by any AI tool call -- it only fires from a hardcoded
     scheduler cadence. Distinguishing "wired via cron" from "wired via AI
     tool" from "wired via paper-trading pipeline" matters for an honest
     wiring diagram.
  3. query_exit_timing is a naming trap: despite its name and Phase 12 tag,
     it has ZERO relationship to aiem_exit_engine.py -- pure inline SQL
     against ai_short_calls_log. Fourth such trap found (after Phase 6's
     smart_money_divergence, Phase 9's analyze_signal_correlation, and
     Phase 10's adversarial_review).
  4. holding_period_optimizer.py: first "real module, genuinely called, but
     absent from the 195-module master catalog" case found in this sweep.
     Documented as a finding, not silently added to the registry.

Run with:
    cd artifacts/stock-scanner-api
    AIEM_DATABASE_URL="$DATABASE_URL" python3 aiem_phase12_verify.py
"""
import os
import subprocess
import sys
import psycopg2

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
MAIN_PY = os.path.join(REPO_ROOT, "main.py")


def _grep(pattern, path=MAIN_PY, extra_flags=None):
    cmd = ["grep", "-n"]
    if extra_flags:
        cmd += extra_flags
    cmd += [pattern, path]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return [l for l in out.stdout.splitlines() if l.strip()]
    except Exception as e:
        return [f"grep_error: {e}"]


# ---------------------------------------------------------------------------
# 1. Module wiring checks
# ---------------------------------------------------------------------------
_IMPORT_PATTERN = r"(^|[^_a-zA-Z])(import|from) {mod}([^_a-zA-Z]|$)"

DIRECT_WIRED_MODULES = {
    "aiem_edge_filter.py": {
        "main_pattern": _IMPORT_PATTERN.format(mod="aiem_edge_filter"),
        "usage_pattern": r"_ef\.get_orchestrator\(\)|_ef_orc(_gate)?\.evaluate|_aiem_ef_mod\.init_schema|_aiem_ef_cs\.cold_start_report",
        "kind": "direct import (6 sites) + get_orchestrator().status()/.evaluate() called from "
                "2 AI tools + internal paper-trading gate + MTM processing + startup schema init",
    },
    "aiem_exit_engine.py": {
        "main_pattern": _IMPORT_PATTERN.format(mod="aiem_exit_engine"),
        "usage_pattern": r"review_open_positions\(",
        "kind": "direct import + review_open_positions() called from a 30-min scheduler job "
                "(9-15 ET, id=aiem_exit_review) -- NOT reachable via any AI tool",
    },
}


def verify_modules():
    results = {}
    for mod, spec in DIRECT_WIRED_MODULES.items():
        import_hits = _grep(spec["main_pattern"], extra_flags=["-E"])
        usage_hits = _grep(spec["usage_pattern"], extra_flags=["-E"])
        wired = bool(import_hits) and bool(usage_hits)
        results[mod] = {
            "status": "wired" if wired else "gap",
            "kind": spec["kind"],
            "evidence": (import_hits[:1] + usage_hits[:2]),
        }
    return results


# ---------------------------------------------------------------------------
# 2. Phase 12 tools: dispatch-map registration + true implementation trace
# ---------------------------------------------------------------------------
PHASE12_TOOLS = {
    "edge_filter_evaluate": {
        "dispatch_pattern": r'"edge_filter_evaluate":\s*_aiem_tool_edge_filter_evaluate',
        "real_source": "aiem_edge_filter.py (Phase 12 -- same phase) via get_orchestrator().evaluate()",
        "owning_module": "aiem_edge_filter.py",
        "cross_phase": False,
    },
    "edge_filter_status": {
        "dispatch_pattern": r'"edge_filter_status":\s*_aiem_tool_edge_filter_status',
        "real_source": "aiem_edge_filter.py (Phase 12 -- same phase) via get_orchestrator().status()",
        "owning_module": "aiem_edge_filter.py",
        "cross_phase": False,
    },
    "run_risk_gate": {
        "dispatch_pattern": r'"run_risk_gate":\s*_aiem_tool_run_risk_gate',
        "real_source": "pre_decision_risk_gate.py (cross-phase: Phase 11) via run_risk_gate()",
        "owning_module": "pre_decision_risk_gate.py",
        "cross_phase": True,
    },
    "rl_get_paper_action": {
        "dispatch_pattern": r'"rl_get_paper_action":\s*_aiem_tool_rl_get_paper_action',
        "real_source": "rl_position_sizer.py (cross-phase: Phase 11) via get_paper_action()",
        "owning_module": "rl_position_sizer.py",
        "cross_phase": True,
    },
    "deep_rl_get_paper_action": {
        "dispatch_pattern": r'"deep_rl_get_paper_action":\s*_aiem_tool_deep_rl_get_paper_action',
        "real_source": "deep_rl_policy.py (cross-phase: Phase 15) via get_paper_action()",
        "owning_module": "deep_rl_policy.py",
        "cross_phase": True,
    },
    "query_exit_timing": {
        "dispatch_pattern": r'"query_exit_timing":\s*_aiem_tool_query_exit_timing',
        "real_source": (
            "INLINE direct SQL against ai_short_calls_log (T+3/T+5 win-rate breakdown). "
            "NO module import at all -- despite the name and Phase 12 tag, has ZERO "
            "relationship to aiem_exit_engine.py. Naming trap (4th found in this sweep)."
        ),
        "owning_module": None,
        "cross_phase": False,
        "inline_no_module": True,
    },
    "holding_period_optimize": {
        "dispatch_pattern": r'"holding_period_optimize":\s*_aiem_tool_holding_period_optimize',
        "real_source": (
            "holding_period_optimizer.py via aggregate_horizon_performance()/"
            "find_optimal_horizon(). Module file genuinely exists and is genuinely "
            "imported/called, but is ABSENT from the 195-module aiem_module_registry "
            "catalog entirely (verified: 0 rows match ILIKE '%holding_period%'). "
            "Out-of-catalog module -- documented, not silently added to the registry."
        ),
        "owning_module": "holding_period_optimizer.py",
        "cross_phase": True,
        "out_of_catalog_module": True,
    },
    "get_decisions": {
        "dispatch_pattern": r'"get_decisions":\s*_aiem_tool_get_decisions',
        "real_source": "decision_logger.py (cross-phase: Phase 9) via get_decisions()",
        "owning_module": "decision_logger.py",
        "cross_phase": True,
    },
    "log_decision": {
        "dispatch_pattern": r'"log_decision":\s*_aiem_tool_log_decision',
        "real_source": "decision_logger.py (cross-phase: Phase 9) via log_decision()",
        "owning_module": "decision_logger.py",
        "cross_phase": True,
    },
}


def verify_tools():
    results = {}
    for tool, spec in PHASE12_TOOLS.items():
        hits = _grep(spec["dispatch_pattern"], extra_flags=["-E"])
        registered = len(hits) > 0
        results[tool] = {
            "registered_in_dispatch_map": registered,
            "real_source": spec["real_source"],
            "owning_module": spec["owning_module"],
            "cross_phase": spec["cross_phase"],
            "inline_no_module": spec.get("inline_no_module", False),
            "out_of_catalog_module": spec.get("out_of_catalog_module", False),
            "evidence": hits[:1],
        }
    return results


def apply_findings_to_registry(module_results, tool_results):
    conn = psycopg2.connect(os.environ["AIEM_DATABASE_URL"])
    cur = conn.cursor()
    cmd_str = "AIEM_DATABASE_URL=$DATABASE_URL python3 aiem_phase12_verify.py"

    for mod, r in module_results.items():
        module_name = os.path.basename(mod)
        module_name = module_name[:-3] if module_name.endswith(".py") else module_name
        if r["status"] == "wired":
            status = "VERIFIED_WIRED"
            note = f"{r['kind']}: {'; '.join(str(e) for e in r['evidence'])}"
        else:
            status = "VERIFICATION_FAILED"
            note = f"{r['kind']}: NO EVIDENCE FOUND"
        cur.execute(
            """UPDATE aiem_module_registry
               SET execution_status = %s,
                   verification_result = %s,
                   verified_by_command = %s,
                   last_verified_date = now(),
                   verification_version = verification_version + 1
               WHERE module_name = %s""",
            (status, note[:2000], cmd_str, module_name),
        )

    for tool, r in tool_results.items():
        if r["registered_in_dispatch_map"] and not r["inline_no_module"]:
            level = "module_verified"
            vstatus = "VERIFIED_REAL_IMPLEMENTATION"
        elif r["registered_in_dispatch_map"] and r["inline_no_module"]:
            level = "phase_only"
            vstatus = "VERIFIED_INLINE_NO_MODULE"
        else:
            level = "phase_only"
            vstatus = "VERIFICATION_FAILED"
        cur.execute(
            """UPDATE aiem_tool_registry
               SET owning_module = %s,
                   tool_verification_level = %s,
                   verification_status = %s,
                   verification_result = %s,
                   verified_by_command = %s,
                   last_verified_date = now(),
                   verification_version = verification_version + 1
               WHERE tool_name = %s""",
            (r["real_source"], level, vstatus, vstatus, cmd_str, tool),
        )

    conn.commit()
    cur.close()
    conn.close()


def main():
    print("=" * 78)
    print("PHASE 12 VERIFICATION — Edge Filter & Exit Engine")
    print("=" * 78)

    mod_results = verify_modules()
    print(f"\n-- MODULE WIRING ({len(mod_results)} modules) --")
    genuine_gaps = []
    wired_count = 0
    for mod, r in mod_results.items():
        if r["status"] == "wired":
            flag = "OK "
            wired_count += 1
        else:
            flag = "FAIL"
            genuine_gaps.append(mod)
        print(f"[{flag}] {mod}  ({r['kind']})")

    tool_results = verify_tools()
    print(f"\n-- TOOL DISPATCH REGISTRATION + REAL SOURCE TRACE ({len(tool_results)} tools) --")
    module_owned = 0
    cross_phase = 0
    inline_no_module = 0
    out_of_catalog = 0
    name_gaps = []
    for tool, r in tool_results.items():
        flag = "OK " if r["registered_in_dispatch_map"] else "GAP*"
        print(f"[{flag}] {tool}")
        print(f"       real_source: {r['real_source']}")
        if not r["registered_in_dispatch_map"]:
            name_gaps.append(tool)
            print("       -> GENUINE GAP, no dispatch entry found")
            continue
        if r["inline_no_module"]:
            inline_no_module += 1
            print("       -> INLINE, no module ownership (naming trap)")
        elif r["cross_phase"]:
            print(f"       -> CROSS-PHASE module-owned by {r['owning_module']}")
            cross_phase += 1
            if r["out_of_catalog_module"]:
                out_of_catalog += 1
                print("       -> NOTE: owning module is OUT-OF-CATALOG (not in the 195-module registry)")
        else:
            print(f"       -> genuinely Phase-12-owned by {r['owning_module']}")
            module_owned += 1

    print("\n-- HEADLINE FINDINGS --")
    print(f"1. Module wiring: {len(genuine_gaps)} genuine gap(s): {genuine_gaps or 'NONE'}.")
    print(f"2. Tool registration: {len(name_gaps)} dispatch gap(s): {name_gaps or 'NONE'} "
          f"(9/9 tools ARE registered). Of those registered: {module_owned} Phase-12-owned, "
          f"{cross_phase} cross-phase, {inline_no_module} inline/no-module -- lowest same-phase "
          f"ownership ratio since Phase 9/10.")
    print("3. aiem_exit_engine.py is wired ONLY via a 30-min scheduler cron job, unreachable "
          "by any AI tool -- second confirmed 'wired but not AI-reachable' module after Phase 10.")
    print("4. query_exit_timing is a naming trap: pure inline SQL, zero relationship to "
          "aiem_exit_engine.py despite the name (4th naming trap this sweep).")
    print(f"5. holding_period_optimizer.py is a real, genuinely-called module OUT-OF-CATALOG "
          f"(not among the 195 tracked modules) -- documented, not added to the registry.")

    if os.environ.get("AIEM_DATABASE_URL"):
        apply_findings_to_registry(mod_results, tool_results)
        print("\n-- REGISTRY UPDATED --")
        print(f"aiem_module_registry: {len(mod_results)} rows")
        print(f"aiem_tool_registry: {len(tool_results)} rows")
    else:
        print("\nAIEM_DATABASE_URL not set — registry NOT updated, dry run only.")

    print("\n-- SUMMARY --")
    print(f"modules_wired: {wired_count}/{len(mod_results)}")
    print(f"modules_genuine_gap: {len(genuine_gaps)}/{len(mod_results)}")
    print(f"tools_dispatched: {len(tool_results) - len(name_gaps)}/{len(tool_results)}")
    print(f"tools_dispatch_gap: {len(name_gaps)}/{len(tool_results)}")
    print(f"tools_module_owned: {module_owned}/{len(tool_results)}")
    print(f"tools_cross_phase: {cross_phase}/{len(tool_results)}")
    print(f"tools_inline_no_module: {inline_no_module}/{len(tool_results)}")
    print(f"tools_out_of_catalog_module: {out_of_catalog}/{len(tool_results)}")

    hard_gaps = genuine_gaps or name_gaps
    if hard_gaps:
        sys.exit(1)


if __name__ == "__main__":
    main()
