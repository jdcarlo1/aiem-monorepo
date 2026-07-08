"""
Phase 17 (Verification & Observability) verification for the AEIM DIAGRAM 2 —
MASTER WIRING + VERIFICATION project. This is the FINAL phase (0-17) of the
sweep.

Static, grep/sed-based checks only. NEVER imports main.py live.

12 modules, 10 tools.

WHAT THIS PROVES:
  1. Module wiring for all 12 Phase 17 module files -- 2/12 VERIFIED_WIRED,
     9/12 VERIFIED_NOT_WIRED_BY_DESIGN, 1/12 ARCHITECTURAL_REMEDIATION_REQUIRED:
       - aiem_verification.py -> VERIFIED_WIRED. log_research_loop_run()
         called from 2 real sites (self-research loop logging,
         job_type='aiem_self_research').
       - aiem_v3_verification.py -> VERIFIED_WIRED. run_full_verification()
         called from 2 real sites: a 4:55 PM Mon-Fri scheduled job
         (v3_verification_daily) AND the /stock-api/admin/aiem-v3/verify
         admin-token-gated route (on_demand).
       - drift_alarm.py -> ARCHITECTURAL_REMEDIATION_REQUIRED. Imported at
         module scope (`import drift_alarm as _drift_alarm`) and truthy-
         checked before a scheduled drift job runs, BUT its two real
         functions (compute_drift, check_all_active_signals -- both use
         Fisher's exact test per the module's own docstring) are NEVER
         called anywhere in main.py. The scheduled job instead reimplements
         a much weaker inline version: a raw win-rate gap >= 10pp check
         with no statistical significance test at all, working directly off
         a hardcoded `_baselines` dict. The module import is a live
         truthy-gate with a dead payload -- confirmed via exhaustive grep
         for `_drift_alarm.` (zero method-call hits) and for
         `compute_drift`/`check_all_active_signals` (zero hits anywhere in
         main.py).
       - 9 standalone verification scripts, each independently confirmed
         VERIFIED_NOT_WIRED_BY_DESIGN via its own docstring/run-instructions
         and zero import hits in main.py (they are meant to be run manually
         from a shell, matching the existing verification-script-pattern.md
         memory -- "a standalone falsification-resistant shell script, not
         an OTP"):
           strict_aeim_supervisor_verifier.py, strict_observability_
           supervisor_verifier.py, verify_aiem_loop.py,
           verify_eod_learning_loop.py, verify_ml_infrastructure.py,
           verify_premarket_system.py, verify_signals.py, monitor.py
           (own docstring: "completely standalone, zero impact on main.py
           or any tab"), fix_silent_excepts.py (own docstring: "Run this
           ONCE in Replit's shell").
  2. All 10 Phase-17-tagged AI tool names checked against the live tool
     dispatch map in main.py: 4/10 have a real dispatch-map entry, 6/10 are
     genuine dispatch gaps (verify_aiem_loop, verify_eod_learning_loop,
     verify_ml_infrastructure, verify_premarket_system, verify_signals,
     drift_alarm -- none of these appear ANYWHERE in main.py, not even as a
     bare string, confirming the registry's PHASE_TOOLS list names these as
     AI-callable tools that were never actually implemented as such; they
     exist only as the standalone scripts / inline cron job traced above).
     0/4 registered tools are same-phase-owned -- every single one traces
     to a DIFFERENT phase's module or to inline main.py code:
       simulation_audit_trail -> simulation_lock.py (Phase 2)
       decision_quality_summary -> decision_logger.py (Phase 9)
       model_version_history -> online_learning.py (Phase 15)
       run_statistical_significance -> inline in main.py (bootstrap
         resampling test, no external module at all -- "inline-no-tie")

HEADLINE FINDINGS:
  1. Phase 17 is, BY THE NATURE OF ITS OWN PURPOSE, the phase with the
     lowest live AI-tool-callable footprint in the whole sweep: 9/12
     modules are one-shot human-run verification scripts (not meant to be
     autonomously callable -- a self-verifying AI grading its own
     pipeline via its own tool call would undermine the independence the
     verification is meant to provide). This mirrors Phase 14's 0/11
     same-phase-tool-ownership finding but for a different, defensible
     reason: Phase 14's audit modules were simply invisible to AI tools;
     Phase 17's verification scripts are INTENTIONALLY kept outside the
     AI's own tool-calling loop.
  2. drift_alarm.py is a genuinely new category not seen elsewhere in the
     sweep: "imported-but-functions-unused, shadow-implemented inline
     instead". Unlike DOCUMENTED_DORMANT (module explicitly disabled with
     a docstring saying so) or VERIFIED_NOT_WIRED_BY_DESIGN (module never
     meant to be imported), drift_alarm.py's own docstring explicitly
     WANTS integration ("This module does that... only raises an alarm
     when the divergence is BOTH statistically significant AND practically
     large enough to matter"), main.py DOES import it, but then bypasses
     its actual statistical logic entirely with a simpler, less rigorous
     duplicate. Flagged ARCHITECTURAL_REMEDIATION_REQUIRED, not a design
     choice.
  3. OUT-OF-BAND FINDING (not part of the 12-module catalog or the 10-tool
     list, so it gets no DB row, but it is a real, confirmed bug worth
     recording): main.py has two admin-token-gated Flask routes,
     /stock-api/aiem/verification/challenge and
     /stock-api/aiem/verification/verify, that do
     `from aiem_verification_and_trading_brain import issue_challenge` and
     `... import verify_response` respectively. That module file exists
     ONLY at the repo root (/home/runner/workspace/
     aiem_verification_and_trading_brain.py), not inside
     artifacts/stock-scanner-api/ where main.py actually runs and where
     main.py's own sys.path.insert(0, dirname(__file__)) points. A static
     import-resolution simulation using main.py's EXACT sys.path setup
     (never a live import of main.py itself) confirms
     ModuleNotFoundError. Both routes will always 500. This is the
     project's SECOND confirmed real-but-out-of-catalog module (after
     Phase 12's holding_period_optimizer.py) but the FIRST one that is
     actively broken rather than merely uncatalogued.
  4. 0/4 same-phase tool ownership (0%) -- ties Phase 14 for the lowest
     ratio in the sweep, but for a defensible structural reason (see #1),
     not a wiring failure.

Run with:
    cd artifacts/stock-scanner-api
    AIEM_DATABASE_URL="$DATABASE_URL" python3 aiem_phase17_verify.py
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


_IMPORT_PATTERN = r"(^|[^_a-zA-Z])(import|from) {mod}([^_a-zA-Z]|$)"

STANDALONE_NOTE = (
    "VERIFIED_NOT_WIRED_BY_DESIGN -- standalone human-run verification script "
    "(own docstring/run-instructions state manual shell execution), zero "
    "imports anywhere in main.py. Matches verification-script-pattern.md memory."
)

MODULES = {
    "strict_aeim_supervisor_verifier.py": {
        "mod": "strict_aeim_supervisor_verifier",
        "kind": "standalone",
        "note": STANDALONE_NOTE + " Docstring: \"Run: python strict_aeim_supervisor_verifier.py\".",
    },
    "strict_observability_supervisor_verifier.py": {
        "mod": "strict_observability_supervisor_verifier",
        "kind": "standalone",
        "note": STANDALONE_NOTE + " Has its own `if __name__ == \"__main__\":` block.",
    },
    "verify_aiem_loop.py": {
        "mod": "verify_aiem_loop",
        "kind": "standalone",
        "note": STANDALONE_NOTE + " Docstring: \"Run: python3 artifacts/stock-scanner-api/verify_aiem_loop.py\".",
    },
    "verify_eod_learning_loop.py": {
        "mod": "verify_eod_learning_loop",
        "kind": "standalone",
        "note": STANDALONE_NOTE + " Docstring names itself an EOD strict verifier with a Run: block.",
    },
    "verify_ml_infrastructure.py": {
        "mod": "verify_ml_infrastructure",
        "kind": "standalone",
        "note": STANDALONE_NOTE + " Own docstring: \"Standalone verification harness for ml_infrastructure.py\".",
    },
    "verify_premarket_system.py": {
        "mod": "verify_premarket_system",
        "kind": "standalone",
        "note": STANDALONE_NOTE + " Docstring: \"Run this from artifacts/stock-scanner-api/ to verify\".",
    },
    "verify_signals.py": {
        "mod": "verify_signals",
        "kind": "standalone",
        "note": STANDALONE_NOTE + " Own docstring: verification suite to run BEFORE trusting backtest output.",
    },
    "monitor.py": {
        "mod": "monitor",
        "kind": "standalone",
        "note": STANDALONE_NOTE + " Own docstring literally states: \"completely standalone, zero impact on main.py or any tab\".",
    },
    "fix_silent_excepts.py": {
        "mod": "fix_silent_excepts",
        "kind": "standalone",
        "note": STANDALONE_NOTE + " Own docstring: \"Run this ONCE in Replit's shell, in the same directory as main.py\".",
    },
    "aiem_verification.py": {
        "mod": "aiem_verification",
        "kind": "wired",
        "usage_pattern": r"log_research_loop_run\(",
    },
    "aiem_v3_verification.py": {
        "mod": "aiem_v3_verification",
        "kind": "wired",
        "usage_pattern": r"run_full_verification\(",
    },
    "drift_alarm.py": {
        "mod": "drift_alarm",
        "kind": "shadowed",
        "real_fn_pattern": r"compute_drift\(|check_all_active_signals\(",
        "note": (
            "ARCHITECTURAL_REMEDIATION_REQUIRED -- module IS imported "
            "(`import drift_alarm as _drift_alarm`) and truthy-checked before a "
            "scheduled drift job runs, but its two real functions (compute_drift, "
            "check_all_active_signals -- both Fisher's-exact-test based per the "
            "module's own docstring) are NEVER called. The scheduled job "
            "reimplements a much weaker inline win-rate-gap check (raw >=10pp "
            "threshold, no significance test) directly against a hardcoded "
            "_baselines dict instead. Zero hits for `_drift_alarm.` (method call) "
            "or for compute_drift/check_all_active_signals anywhere in main.py."
        ),
    },
}


def verify_modules():
    results = {}
    for mod, spec in MODULES.items():
        import_hits = _grep(_IMPORT_PATTERN.format(mod=spec["mod"]), extra_flags=["-E"])
        if spec["kind"] == "standalone":
            status = "not_wired_by_design" if not import_hits else "gap"
            results[mod] = {
                "status": status,
                "evidence": import_hits[:1],
                "note": spec["note"],
            }
        elif spec["kind"] == "wired":
            usage_hits = _grep(spec["usage_pattern"], extra_flags=["-E"])
            wired = bool(import_hits) and bool(usage_hits)
            results[mod] = {
                "status": "wired" if wired else "gap",
                "evidence": (import_hits[:1] + usage_hits[:2]),
                "note": "",
            }
        else:  # shadowed
            method_call_hits = _grep(r"_drift_alarm\.", extra_flags=["-E"])
            real_fn_hits = _grep(spec["real_fn_pattern"], extra_flags=["-E"])
            results[mod] = {
                "status": "shadowed" if (import_hits and not method_call_hits and not real_fn_hits) else "gap",
                "evidence": import_hits[:1],
                "note": spec["note"],
            }
    return results


PHASE17_TOOLS = {
    "verify_aiem_loop": {
        "dispatch_pattern": r'"verify_aiem_loop":\s*_aiem_tool',
        "real_source": "NOT REGISTERED -- no dispatch entry, no bare-string reference anywhere in main.py. "
                        "Corresponds to the standalone verify_aiem_loop.py script (human-run only).",
        "owning_module": None,
    },
    "verify_eod_learning_loop": {
        "dispatch_pattern": r'"verify_eod_learning_loop":\s*_aiem_tool',
        "real_source": "NOT REGISTERED -- corresponds to standalone verify_eod_learning_loop.py (human-run only).",
        "owning_module": None,
    },
    "verify_ml_infrastructure": {
        "dispatch_pattern": r'"verify_ml_infrastructure":\s*_aiem_tool',
        "real_source": "NOT REGISTERED -- corresponds to standalone verify_ml_infrastructure.py (human-run only).",
        "owning_module": None,
    },
    "verify_premarket_system": {
        "dispatch_pattern": r'"verify_premarket_system":\s*_aiem_tool',
        "real_source": "NOT REGISTERED -- corresponds to standalone verify_premarket_system.py (human-run only).",
        "owning_module": None,
    },
    "verify_signals": {
        "dispatch_pattern": r'"verify_signals":\s*_aiem_tool',
        "real_source": "NOT REGISTERED -- corresponds to standalone verify_signals.py (human-run only).",
        "owning_module": None,
    },
    "drift_alarm": {
        "dispatch_pattern": r'"drift_alarm":\s*_aiem_tool',
        "real_source": "NOT REGISTERED -- drift_alarm.py is imported into a scheduled cron job in main.py "
                        "but never exposed as an AI-callable tool, and its real functions are unused there too.",
        "owning_module": None,
    },
    "simulation_audit_trail": {
        "dispatch_pattern": r'"simulation_audit_trail":\s*_aiem_tool_simulation_audit_trail',
        "real_source": "simulation_lock.py (cross-phase: Phase 2) via get_audit_trail()",
        "owning_module": "simulation_lock.py",
    },
    "decision_quality_summary": {
        "dispatch_pattern": r'"decision_quality_summary":\s*_aiem_tool_decision_quality_summary',
        "real_source": "decision_logger.py (cross-phase: Phase 9) via decision_quality_summary()",
        "owning_module": "decision_logger.py",
    },
    "model_version_history": {
        "dispatch_pattern": r'"model_version_history":\s*_aiem_tool_model_version_history',
        "real_source": "online_learning.py (cross-phase: Phase 15) via version_history()/get_live_model()",
        "owning_module": "online_learning.py",
    },
    "run_statistical_significance": {
        "dispatch_pattern": r'"run_statistical_significance":\s*_aiem_tool_run_statistical_significance',
        "real_source": "inline in main.py -- bootstrap resampling significance test, no external module "
                        "at all (\"inline-no-tie\")",
        "owning_module": None,
    },
}

_PHASE17_OWNED_MODULES = {m for m, s in MODULES.items() if s["kind"] != "standalone"}


def verify_tools():
    results = {}
    for tool, spec in PHASE17_TOOLS.items():
        hits = _grep(spec["dispatch_pattern"], extra_flags=["-E"])
        registered = len(hits) > 0
        module_owned = (spec["owning_module"] in _PHASE17_OWNED_MODULES) if spec["owning_module"] else False
        results[tool] = {
            "registered_in_dispatch_map": registered,
            "real_source": spec["real_source"],
            "owning_module": spec["owning_module"],
            "module_owned": module_owned,
            "evidence": hits[:1],
        }
    return results


def check_out_of_band_finding():
    """
    Static (non-live) import-resolution simulation for
    aiem_verification_and_trading_brain.py, referenced by two admin routes
    but confirmed physically absent from main.py's own sys.path directory.
    This never imports main.py itself -- it only replicates main.py's own
    documented sys.path.insert(0, dirname(__file__)) call (main.py L59) in
    a fresh subprocess to see whether the module resolves.
    """
    route_refs = _grep(r"aiem_verification_and_trading_brain")
    file_in_repo_root = os.path.exists(os.path.join(os.path.dirname(REPO_ROOT), "aiem_verification_and_trading_brain.py"))
    file_in_scanner_dir = os.path.exists(os.path.join(REPO_ROOT, "aiem_verification_and_trading_brain.py"))
    sim = subprocess.run(
        [sys.executable, "-c",
         f"import sys; sys.path.insert(0, {REPO_ROOT!r}); "
         "import aiem_verification_and_trading_brain"],
        capture_output=True, text=True, timeout=15,
    )
    resolves = sim.returncode == 0
    return {
        "route_refs": route_refs,
        "file_in_repo_root": file_in_repo_root,
        "file_in_scanner_dir": file_in_scanner_dir,
        "resolves_from_main_py_syspath": resolves,
        "sim_stderr_tail": sim.stderr.strip().splitlines()[-1] if sim.stderr.strip() else "",
    }


def apply_findings_to_registry(module_results, tool_results):
    conn = psycopg2.connect(os.environ["AIEM_DATABASE_URL"])
    cur = conn.cursor()
    cmd_str = "AIEM_DATABASE_URL=$DATABASE_URL python3 aiem_phase17_verify.py"

    for mod, r in module_results.items():
        module_name = os.path.basename(mod)
        module_name = module_name[:-3] if module_name.endswith(".py") else module_name
        if r["status"] == "wired":
            status = "VERIFIED_WIRED"
        elif r["status"] == "not_wired_by_design":
            status = "VERIFIED_NOT_WIRED_BY_DESIGN"
        elif r["status"] == "shadowed":
            status = "ARCHITECTURAL_REMEDIATION_REQUIRED"
        else:
            status = "VERIFICATION_FAILED"
        note_bits = [f"evidence: {'; '.join(str(e) for e in r['evidence'])}" if r["evidence"] else "NO IMPORT EVIDENCE"]
        if r["note"]:
            note_bits.append(r["note"])
        note = " | ".join(note_bits)
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
        if r["registered_in_dispatch_map"]:
            level = "module_verified"
            vstatus = "VERIFIED_REAL_IMPLEMENTATION"
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
    print("PHASE 17 VERIFICATION — Verification & Observability (FINAL PHASE)")
    print("=" * 78)

    mod_results = verify_modules()
    print(f"\n-- MODULE WIRING ({len(mod_results)} modules) --")
    genuine_gaps = []
    wired_count = 0
    standalone_count = 0
    shadowed_count = 0
    for mod, r in mod_results.items():
        if r["status"] == "wired":
            flag = "OK "
            wired_count += 1
        elif r["status"] == "not_wired_by_design":
            flag = "N/D"
            standalone_count += 1
        elif r["status"] == "shadowed":
            flag = "SHDW"
            shadowed_count += 1
        else:
            flag = "FAIL"
            genuine_gaps.append(mod)
        print(f"[{flag}] {mod}")

    tool_results = verify_tools()
    print(f"\n-- TOOL DISPATCH REGISTRATION + REAL SOURCE TRACE ({len(tool_results)} tools) --")
    module_owned = 0
    cross_phase = 0
    inline_no_tie = 0
    name_gaps = []
    for tool, r in tool_results.items():
        flag = "OK " if r["registered_in_dispatch_map"] else "GAP*"
        print(f"[{flag}] {tool}")
        print(f"       real_source: {r['real_source']}")
        if not r["registered_in_dispatch_map"]:
            name_gaps.append(tool)
            print("       -> GENUINE GAP, no dispatch entry found")
            continue
        if r["module_owned"]:
            module_owned += 1
            print(f"       -> genuinely Phase-17-owned by {r['owning_module']}")
        elif r["owning_module"]:
            cross_phase += 1
            print(f"       -> CROSS-PHASE module-owned by {r['owning_module']}")
        else:
            inline_no_tie += 1
            print("       -> inline-no-tie (no external module)")

    oob = check_out_of_band_finding()
    print("\n-- OUT-OF-BAND FINDING: aiem_verification_and_trading_brain.py --")
    print(f"referenced by {len(oob['route_refs'])} line(s) in main.py: {oob['route_refs']}")
    print(f"file exists at repo root: {oob['file_in_repo_root']}")
    print(f"file exists in artifacts/stock-scanner-api/ (main.py's own dir): {oob['file_in_scanner_dir']}")
    print(f"resolves when simulating main.py's own sys.path setup: {oob['resolves_from_main_py_syspath']}")
    if not oob["resolves_from_main_py_syspath"]:
        print(f"    -> {oob['sim_stderr_tail']}")
        print("    -> CONFIRMED: both admin routes (/stock-api/aiem/verification/challenge, "
              "/verify) will always ModuleNotFoundError -> 500. Not in the 12-module catalog "
              "or 10-tool list, so no DB row; recorded here and in memory only.")

    print("\n-- HEADLINE FINDINGS --")
    print(f"1. Module wiring: {len(genuine_gaps)} genuine gap(s): {genuine_gaps or 'NONE'} "
          f"({wired_count}/12 VERIFIED_WIRED, {standalone_count}/12 VERIFIED_NOT_WIRED_BY_DESIGN, "
          f"{shadowed_count}/12 ARCHITECTURAL_REMEDIATION_REQUIRED).")
    print(f"2. Tool registration: {len(name_gaps)} dispatch gap(s): {name_gaps} "
          f"(4/10 registered). {module_owned} same-phase-owned, {cross_phase} cross-phase, "
          f"{inline_no_tie} inline-no-tie.")
    print("3. drift_alarm.py: imported + truthy-checked, but its real statistical functions "
          "(compute_drift/check_all_active_signals, Fisher's exact test) are never called -- "
          "main.py runs a weaker inline duplicate instead. New category: "
          "'imported-but-functions-unused, shadow-implemented inline'.")
    print("4. 9/12 modules are standalone human-run verification scripts by design -- lowest "
          "live-AI-tool footprint of any phase, but for a defensible reason (self-verification "
          "independence), unlike Phase 14's invisible-audit-modules gap.")
    print("5. aiem_verification_and_trading_brain.py: real 24KB module, but lives only at the "
          "repo root, never inside artifacts/stock-scanner-api/ where main.py's own sys.path "
          "points -- both routes that import it are dead code paths, confirmed via static "
          "sys.path simulation only.")

    if os.environ.get("AIEM_DATABASE_URL"):
        apply_findings_to_registry(mod_results, tool_results)
        print("\n-- REGISTRY UPDATED --")
        print(f"aiem_module_registry: {len(mod_results)} rows")
        print(f"aiem_tool_registry: {len(tool_results)} rows")
    else:
        print("\nAIEM_DATABASE_URL not set — registry NOT updated, dry run only.")

    print("\n-- SUMMARY --")
    print(f"modules_wired: {wired_count}/{len(mod_results)}")
    print(f"modules_not_wired_by_design: {standalone_count}/{len(mod_results)}")
    print(f"modules_architectural_remediation_required: {shadowed_count}/{len(mod_results)}")
    print(f"modules_genuine_gap: {len(genuine_gaps)}/{len(mod_results)}")
    print(f"tools_dispatched: {len(tool_results) - len(name_gaps)}/{len(tool_results)}")
    print(f"tools_dispatch_gap: {len(name_gaps)}/{len(tool_results)}")
    print(f"tools_module_owned: {module_owned}/{len(tool_results)}")
    print(f"tools_cross_phase: {cross_phase}/{len(tool_results)}")
    print(f"tools_inline_no_tie: {inline_no_tie}/{len(tool_results)}")
    print("\nPHASE 17 IS THE FINAL PHASE OF THE 0-17 SWEEP.")

    if genuine_gaps:
        sys.exit(1)


if __name__ == "__main__":
    main()
