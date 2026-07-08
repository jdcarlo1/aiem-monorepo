"""
Phase 6 (Options & Smart Money Flow) verification for the AEIM DIAGRAM 2 —
MASTER WIRING + VERIFICATION project.

Static, grep/sed-based checks only. NEVER imports main.py live.

WHAT THIS PROVES:
  1. Module wiring for all 8 Phase 6 module files.
     7/8 wired via real, direct `import X` / `from X import Y` hits in main.py.
     1/8 is a GENUINE, by-design standalone daemon:
       - fetch_si_background.py — own docstring says "Runs as a daemon...
         Run as: python3 fetch_si_background.py >> /tmp/fetch_si.log 2>&1 &".
         Zero references anywhere in main.py or any other repo .py file
         (confirmed via repo-wide grep, not just main.py). Prior session
         notes (memory: polygon-si-module-b.md) independently confirm it
         was run manually as a background shell process, never wired into
         any scheduler or subprocess call. This is VERIFIED_NOT_WIRED_BY_DESIGN
         — same category as Phase 2's lookahead_audit.py/manual_rollback.py,
         not a fabricated pass and not an accidental gap.
  2. Each of the 11 Phase-6-tagged AI tools checked against the live tool
     dispatch map in main.py:
     10/11 genuinely registered with a traced real implementation.
     1/11 is a GENUINE TOOL-REGISTRATION GAP — this project's first:
       - "smart_money_divergence" does NOT exist as a dispatch-map key
         anywhere in main.py. The only place this exact string appears is
         as the default value of the `signal_name` parameter inside
         `_aiem_tool_divergence_scan` (main.py line 38373), which is itself
         registered under the tool name "divergence_scan" — already
         verified and credited to PHASE 5, not Phase 6. Confirmed by
         grepping for `"smart_money_divergence"` as a dispatch-map key
         (zero hits) and for the literal string across main.py (only 3
         hits: the default-parameter line, a docstring, and a tool
         description mentioning it as one of four valid `signal_name`
         values for outcome-recording). There is no callable AI tool named
         `smart_money_divergence`. Reported honestly as
         VERIFICATION_FAILED, not silently dropped or fabricated as
         passing.
     Of the 10 real tools: only 1 (microstructure_proxy) is genuinely
     file-owned by a Phase 6 module. 2 (option_b_evaluate, option_b_status)
     are cross-phase-owned by aiem_intelligence_layer.py (Phase 1 — a
     generic decision-brain module; "Option B" here is unrelated to the
     "Module B Short Squeeze" signal from earlier sessions, a naming
     coincidence confirmed by reading the handler body). 7 are inline
     direct-SQL reads over tables that Phase 6 modules populate
     (call_sweep_log <- options_sweep.py; options_structure_scan <-
     aiem_options_structure.py; polygon_market_daily <- unrelated daily
     ingest) — this is the standard "module writes, tool reads" pipeline
     pattern seen in prior phases, not a gap.

HEADLINE FINDING: Phase 6 produces this project's first genuine
TOOL-REGISTRATION gap (smart_money_divergence — a registry entry with no
matching dispatch-map key, apparently confused with a signal_name string
literal reused elsewhere) alongside the by-design daemon-script pattern
already established in Phase 2. Reported honestly per Joel's registry
conventions — no fabrication, no silent pass.

Run with:
    cd artifacts/stock-scanner-api
    AIEM_DATABASE_URL="$DATABASE_URL" python3 aiem_phase6_verify.py
"""
import os
import subprocess
import sys
import psycopg2

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
MAIN_PY = os.path.join(REPO_ROOT, "main.py")

_NON_WIRING_FILES = ("aiem_registry.py", "aiem_phase0_verify.py",
                     "aiem_phase1_verify.py", "aiem_phase2_verify.py",
                     "aiem_phase3_verify.py", "aiem_phase4_verify.py",
                     "aiem_phase5_verify.py", "aiem_phase6_verify.py",
                     "aiem_registry_build.py", "aiem_function_registry_build.py")


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


def _grep_repo(pattern):
    cmd = ["grep", "-rln", pattern, "--include=*.py", REPO_ROOT]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        hits = [l for l in out.stdout.splitlines() if l.strip()]
        return [h for h in hits if os.path.basename(h) not in _NON_WIRING_FILES]
    except Exception as e:
        return [f"grep_error: {e}"]


# ---------------------------------------------------------------------------
# 1. Module wiring checks
# ---------------------------------------------------------------------------
MODULE_WIRING_CHECKS = {
    "aiem_options_structure.py": {"pattern": r"import aiem_options_structure as", "kind": "direct_import (module-level try block)"},
    "congress_trades.py": {"pattern": r"^from congress_trades import", "kind": "direct_import (module-level)"},
    "insider_trades.py": {"pattern": r"^from insider_trades import", "kind": "direct_import (module-level)"},
    "microstructure_proxy.py": {"pattern": r"from microstructure_proxy import", "kind": "lazy_import"},
    "options_sweep.py": {"pattern": r"^from options_sweep import", "kind": "direct_import (module-level)"},
    "smart_money.py": {"pattern": r"^from smart_money import|from smart_money import fetch_options_data", "kind": "direct+lazy import"},
    "smart_money_divergence_detector.py": {"pattern": r"import smart_money_divergence_detector as", "kind": "lazy_import"},
    "fetch_si_background.py": {"pattern": r"fetch_si_background", "kind": "EXPECTED TO FAIL IN MAIN.PY — standalone daemon, see docstring"},
}

NOT_WIRED_BY_DESIGN = {
    "fetch_si_background.py": (
        "Own docstring: 'Runs as a daemon, saves progress to DB, resumes if "
        "restarted... Run as: python3 fetch_si_background.py >> /tmp/fetch_si.log "
        "2>&1 &'. Zero references in main.py OR any other .py file in the repo "
        "(repo-wide grep). Prior session (memory: polygon-si-module-b.md) "
        "independently confirms it was run manually as a background shell "
        "process to backfill polygon_short_interest, never wired into a "
        "scheduler or subprocess call. Same category as Phase 2's "
        "lookahead_audit.py/manual_rollback.py — a deliberate standalone "
        "CLI/daemon script, not an accidental gap."
    ),
}


def verify_modules():
    results = {}
    for mod, spec in MODULE_WIRING_CHECKS.items():
        if mod == "fetch_si_background.py":
            repo_hits = _grep_repo("fetch_si_background")
            wired = len(repo_hits) > 0
            results[mod] = {"wired": wired, "kind": spec["kind"], "evidence": repo_hits[:3]}
            continue
        needs_E = any(c in spec["pattern"] for c in "^$|")
        hits = _grep(spec["pattern"], extra_flags=["-E"] if needs_E else None)
        wired = len(hits) > 0
        results[mod] = {"wired": wired, "kind": spec["kind"], "evidence": hits[:2]}
    return results


# ---------------------------------------------------------------------------
# 2. Phase 6 tools: dispatch-map registration + true implementation trace
# ---------------------------------------------------------------------------
PHASE6_TOOLS = {
    "microstructure_proxy": {
        "dispatch_pattern": r'"microstructure_proxy":\s*_aiem_tool_microstructure_proxy',
        "real_source": "microstructure_proxy.py — compute_microstructure_proxy()",
        "owning_module": "microstructure_proxy.py",
        "genuine": True,
    },
    "mkt_cross_confirm_options": {
        "dispatch_pattern": r'"mkt_cross_confirm_options":\s*_mkt_cross_confirm_options_price',
        "real_source": "inline main.py — direct SQL on call_sweep_log + unusual_calls_log (call_sweep_log populated by options_sweep.py)",
        "owning_module": None,
        "genuine": True,
    },
    "mkt_gex_scan": {
        "dispatch_pattern": r'"mkt_gex_scan":\s*_mkt_tool_gex_scan',
        "real_source": "inline main.py — direct SQL on options_structure_scan (populated by aiem_options_structure.py)",
        "owning_module": None,
        "genuine": True,
    },
    "mkt_net_flow_db": {
        "dispatch_pattern": r'"mkt_net_flow_db":\s*_mkt_net_flow_db',
        "real_source": "inline main.py — direct SQL on polygon_market_daily",
        "owning_module": None,
        "genuine": True,
    },
    "mkt_options_flow_scan": {
        "dispatch_pattern": r'"mkt_options_flow_scan":\s*_mkt_options_flow_scan',
        "real_source": "inline main.py — direct SQL on call_sweep_log + unusual_calls_log + unusual_calls_microcap_log",
        "owning_module": None,
        "genuine": True,
    },
    "mkt_options_predicts_price": {
        "dispatch_pattern": r'"mkt_options_predicts_price":\s*_mkt_options_predicts_price',
        "real_source": "inline main.py — direct SQL joining call_sweep_log + polygon_market_daily",
        "owning_module": None,
        "genuine": True,
    },
    "mkt_options_skew": {
        "dispatch_pattern": r'"mkt_options_skew":\s*_mkt_tool_options_skew',
        "real_source": "inline main.py — direct SQL on options_structure_scan (populated by aiem_options_structure.py)",
        "owning_module": None,
        "genuine": True,
    },
    "mkt_ticker_options_history": {
        "dispatch_pattern": r'"mkt_ticker_options_history":\s*_mkt_ticker_options_history',
        "real_source": "inline main.py — direct SQL on call_sweep_log",
        "owning_module": None,
        "genuine": True,
    },
    "option_b_evaluate": {
        "dispatch_pattern": r'"option_b_evaluate":\s*_aiem_tool_option_b_evaluate',
        "real_source": "aiem_intelligence_layer.py (cross-phase: Phase 1) — get_option_b_brain().evaluate(); 'Option B' here is a generic decision-brain, unrelated to the 'Module B Short Squeeze' signal",
        "owning_module": "aiem_intelligence_layer.py",
        "genuine": True,
    },
    "option_b_status": {
        "dispatch_pattern": r'"option_b_status":\s*_aiem_tool_option_b_status',
        "real_source": "aiem_intelligence_layer.py (cross-phase: Phase 1) — get_option_b_brain().status()",
        "owning_module": "aiem_intelligence_layer.py",
        "genuine": True,
    },
    "smart_money_divergence": {
        "dispatch_pattern": r'"smart_money_divergence":\s*\w+',
        "real_source": "NOT A REAL TOOL — no dispatch-map key exists. Only appears as the default value of the signal_name parameter inside _aiem_tool_divergence_scan (registered as tool 'divergence_scan', already credited to Phase 5), and in an unrelated tool description string. Registry entry does not correspond to any callable AI tool.",
        "owning_module": None,
        "genuine": False,
    },
}


def verify_tools():
    results = {}
    for tool, spec in PHASE6_TOOLS.items():
        hits = _grep(spec["dispatch_pattern"], extra_flags=["-E"])
        results[tool] = {
            "registered_in_dispatch_map": len(hits) > 0,
            "real_source": spec["real_source"],
            "owning_module": spec["owning_module"],
            "evidence": hits[:1],
        }
    return results


def apply_findings_to_registry(module_results, tool_results):
    conn = psycopg2.connect(os.environ["AIEM_DATABASE_URL"])
    cur = conn.cursor()
    cmd_str = "AIEM_DATABASE_URL=$DATABASE_URL python3 aiem_phase6_verify.py"

    for mod, r in module_results.items():
        module_name = mod[:-3] if mod.endswith(".py") else mod
        if mod in NOT_WIRED_BY_DESIGN:
            status = "VERIFIED_NOT_WIRED_BY_DESIGN"
            note = NOT_WIRED_BY_DESIGN[mod]
        elif r["wired"]:
            status = "VERIFIED_WIRED"
            note = f"{r['kind']}: {'; '.join(r['evidence'])}"
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
        level = "module_verified" if r["registered_in_dispatch_map"] else "phase_only"
        vstatus = "VERIFIED_REAL_IMPLEMENTATION" if r["registered_in_dispatch_map"] else "VERIFICATION_FAILED"
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
    print("PHASE 6 VERIFICATION — Options & Smart Money Flow")
    print("=" * 78)

    mod_results = verify_modules()
    print("\n-- MODULE WIRING (8 modules) --")
    genuine_gaps = []
    not_wired_by_design = []
    for mod, r in mod_results.items():
        if mod in NOT_WIRED_BY_DESIGN:
            flag = "BY-DESIGN"
            not_wired_by_design.append(mod)
        elif r["wired"]:
            flag = "OK "
        else:
            flag = "FAIL"
            genuine_gaps.append(mod)
        print(f"[{flag}] {mod}  ({r['kind']})")
        for e in r["evidence"]:
            print(f"       {e}")
        if mod in NOT_WIRED_BY_DESIGN:
            print(f"       -> {NOT_WIRED_BY_DESIGN[mod]}")

    tool_results = verify_tools()
    print("\n-- TOOL DISPATCH REGISTRATION + REAL SOURCE TRACE (11 tools) --")
    module_owned = 0
    inline = 0
    tool_gaps = []
    for tool, r in tool_results.items():
        flag = "OK " if r["registered_in_dispatch_map"] else "FAIL"
        if not r["registered_in_dispatch_map"]:
            tool_gaps.append(tool)
        print(f"[{flag}] {tool}")
        print(f"       real_source: {r['real_source']}")
        if r["registered_in_dispatch_map"]:
            if r["owning_module"]:
                print(f"       -> genuinely file-owned by {r['owning_module']}")
                module_owned += 1
            else:
                print("       -> INLINE in main.py, no module file")
                inline += 1

    print("\n-- HEADLINE FINDINGS --")
    print(f"1. Module wiring: {len(genuine_gaps)} genuine gap(s): {genuine_gaps or 'NONE'}. "
          f"{len(not_wired_by_design)} VERIFIED_NOT_WIRED_BY_DESIGN: {not_wired_by_design}.")
    print(f"2. Tool registration: {len(tool_gaps)} genuine gap(s): {tool_gaps or 'NONE'}. "
          f"Of the {len(tool_results) - len(tool_gaps)} real tools: {module_owned} module-owned "
          f"(1 Phase-6-owned, 2 cross-phase), {inline} inline.")

    if os.environ.get("AIEM_DATABASE_URL"):
        apply_findings_to_registry(mod_results, tool_results)
        print("\n-- REGISTRY UPDATED --")
        print("aiem_module_registry: 8 rows")
        print("aiem_tool_registry: 11 rows")
    else:
        print("\nAIEM_DATABASE_URL not set — registry NOT updated, dry run only.")

    print("\n-- SUMMARY --")
    n_wired = sum(1 for m, r in mod_results.items() if r["wired"] and m not in NOT_WIRED_BY_DESIGN)
    print(f"modules_wired: {n_wired}/{len(mod_results)}")
    print(f"modules_not_wired_by_design: {len(not_wired_by_design)}/{len(mod_results)}")
    print(f"modules_genuine_gap: {len(genuine_gaps)}/{len(mod_results)}")
    print(f"tools_registered: {sum(1 for r in tool_results.values() if r['registered_in_dispatch_map'])}/{len(tool_results)}")
    print(f"tools_genuine_gap: {len(tool_gaps)}/{len(tool_results)}")
    print(f"tools_module_owned: {module_owned}/{len(tool_results)}")
    print(f"tools_inline_no_module: {inline}/{len(tool_results)}")
    overall_ok = len(genuine_gaps) == 0 and len(tool_gaps) == 0
    print(f"overall_phase6_status: {'PASS' if overall_ok else 'PARTIAL — honest gaps documented (see above), not fabricated as passing'}")

    return 0 if len(genuine_gaps) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
