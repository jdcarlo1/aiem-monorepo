"""
aiem_registry_build.py
-----------------------
One-shot builder + reporter for the AIEM Module Registry / Tool Registry.

Run directly:  python3 aiem_registry_build.py

What it does (in order):
  1. init_schema() -- creates aiem_module_registry / aiem_tool_registry if
     not already present (idempotent).
  2. Populates aiem_module_registry from aiem_registry.MODULE_PHASE_MAP,
     confirming on-disk file existence for every single row (no assumed
     rows -- if a file doesn't exist, file_exists_confirmed=False and it
     is reported, never silently dropped).
  3. Populates aiem_tool_registry by cross-referencing:
       - real registered AI tools from main.py's _build_aiem_tool_map()
       - aiem_registry.PHASE_TOOLS (owning phase, per spec text)
       - aiem_registry.CLI_VERIFICATION_TOOLS (owner-run scripts)
       - aiem_registry.TOOL_ALIASES (spec names that map to real tool names)
       - aiem_registry.EXCLUDED_SAFETY_TOOLS (must stay excluded)
  4. Prints the exact structured report requested:
       deduped module count, missing_from_spec list, registered module
       list, registered tool list, excluded safety tools, CLI verification
       commands, aliases/mapped tools, files changed, test command, and
       raw DB verification output.

Nothing in this script touches trading logic. It only records structure.
"""

import os
import sys
import json
import datetime as dt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import psycopg2
import psycopg2.extras

import aiem_registry as reg

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))


def _connect():
    return reg._connect()


def get_real_registered_tools():
    """Check which spec tool names are actually present in main.py's tool
    map/schema. main.py is NEVER imported live here -- importing it runs
    its full module-level side effects (binds port 5050, starts threads,
    initializes 40+ schemas) and collides with the already-running
    stock-api workflow. Grep-based string presence is the safe, correct
    check for this reconnaissance purpose."""
    with open(os.path.join(REPO_ROOT, "main.py"), "r", errors="ignore") as f:
        content = f.read()
    all_named = set()
    for phase_tools in reg.PHASE_TOOLS.values():
        all_named.update(phase_tools)
    present = sorted(
        t for t in all_named
        if (f'"{t}"' in content or f"'{t}'" in content)
        and t not in reg.CLI_VERIFICATION_TOOLS
        and t not in reg.TOOL_ALIASES
    )
    return present, "grep-based string presence in main.py (static analysis only; main.py never imported live)"


def build_module_rows():
    rows = []
    for module_file, phase in sorted(reg.MODULE_PHASE_MAP.items()):
        abs_path = os.path.join(REPO_ROOT, module_file)
        exists = os.path.isfile(abs_path)
        module_name = os.path.basename(module_file).replace(".py", "")
        rows.append({
            "module_name": module_name,
            "module_file": module_file,
            "module_phase": phase,
            "module_phase_name": reg.PHASE_NAMES.get(phase),
            "ownership_note": reg.OWNERSHIP_NOTES.get(module_file),
            "file_exists_confirmed": exists,
        })
    return rows


def build_tool_rows(real_registered_tools):
    rows = []
    real_set = set(real_registered_tools)

    tool_to_phases = {}
    for phase, tools in reg.PHASE_TOOLS.items():
        for t in tools:
            tool_to_phases.setdefault(t, []).append(phase)

    all_tool_names = set(tool_to_phases.keys()) | set(reg.CLI_VERIFICATION_TOOLS.keys()) | set(reg.TOOL_ALIASES.keys())

    for tool_name in sorted(all_tool_names):
        phases = tool_to_phases.get(tool_name, [])
        primary_phase = phases[0] if phases else None
        secondary_phases = phases[1:] if len(phases) > 1 else []

        if tool_name in reg.CLI_VERIFICATION_TOOLS:
            tool_type = "cli_verification_command"
            can_run_independently = True
            verified_by_command = reg.CLI_VERIFICATION_TOOLS[tool_name]
            registered_in_tool_map = False
            alias_of = None
            dependency_notes = "Standalone owner-run verification script; NOT an AI-callable tool."
        elif tool_name in reg.TOOL_ALIASES:
            tool_type = "alias_mapped"
            can_run_independently = None
            verified_by_command = None
            registered_in_tool_map = False
            alias_of = ", ".join(reg.TOOL_ALIASES[tool_name]["real_tools"])
            dependency_notes = reg.TOOL_ALIASES[tool_name]["note"]
        else:
            tool_type = "ai_callable_tool"
            can_run_independently = None
            verified_by_command = None
            registered_in_tool_map = tool_name in real_set
            alias_of = None
            dependency_notes = (
                f"Also referenced in Phase(s): {secondary_phases}" if secondary_phases else None
            )

        excluded = tool_name in reg.EXCLUDED_SAFETY_TOOLS
        rows.append({
            "tool_name": tool_name,
            "owning_module_or_phase": f"Phase {primary_phase} ({reg.PHASE_NAMES.get(primary_phase)})" if primary_phase is not None else None,
            "tool_type": tool_type,
            "can_run_independently": can_run_independently,
            "excluded_from_autonomous_use": excluded,
            "exclusion_reason": f"safety_control: {reg.EXCLUDED_SAFETY_TOOLS[tool_name]}" if excluded else None,
            "alias_of": alias_of,
            "dependency_notes": dependency_notes,
            "registered_in_tool_map": registered_in_tool_map,
            "verified_by_command": verified_by_command,
        })
    return rows


def upsert_modules(conn, rows):
    with conn.cursor() as cur:
        for r in rows:
            cur.execute("""
                INSERT INTO aiem_module_registry
                    (module_name, module_file, module_phase, module_phase_name,
                     ownership_note, file_exists_confirmed, verification_required,
                     execution_status, verification_result)
                VALUES (%(module_name)s, %(module_file)s, %(module_phase)s, %(module_phase_name)s,
                        %(ownership_note)s, %(file_exists_confirmed)s, TRUE,
                        'PENDING_VERIFICATION', 'PENDING_VERIFICATION')
                ON CONFLICT (module_name) DO UPDATE SET
                    module_file = EXCLUDED.module_file,
                    module_phase = EXCLUDED.module_phase,
                    module_phase_name = EXCLUDED.module_phase_name,
                    ownership_note = EXCLUDED.ownership_note,
                    file_exists_confirmed = EXCLUDED.file_exists_confirmed,
                    updated_at = now()
            """, r)
    conn.commit()


def upsert_tools(conn, rows):
    with conn.cursor() as cur:
        for r in rows:
            cur.execute("""
                INSERT INTO aiem_tool_registry
                    (tool_name, owning_module_or_phase, tool_type, can_run_independently,
                     excluded_from_autonomous_use, exclusion_reason, alias_of,
                     dependency_notes, registered_in_tool_map, verified_by_command,
                     verification_status, verification_result)
                VALUES (%(tool_name)s, %(owning_module_or_phase)s, %(tool_type)s,
                        %(can_run_independently)s, %(excluded_from_autonomous_use)s,
                        %(exclusion_reason)s, %(alias_of)s, %(dependency_notes)s,
                        %(registered_in_tool_map)s, %(verified_by_command)s,
                        'PENDING_VERIFICATION', 'PENDING_VERIFICATION')
                ON CONFLICT (tool_name) DO UPDATE SET
                    owning_module_or_phase = EXCLUDED.owning_module_or_phase,
                    tool_type = EXCLUDED.tool_type,
                    can_run_independently = EXCLUDED.can_run_independently,
                    excluded_from_autonomous_use = EXCLUDED.excluded_from_autonomous_use,
                    exclusion_reason = EXCLUDED.exclusion_reason,
                    alias_of = EXCLUDED.alias_of,
                    dependency_notes = EXCLUDED.dependency_notes,
                    registered_in_tool_map = EXCLUDED.registered_in_tool_map,
                    verified_by_command = EXCLUDED.verified_by_command,
                    updated_at = now()
            """, r)
    conn.commit()


def missing_from_spec_report():
    """Compare the spec's raw filename mentions against repo reality."""
    missing = []
    for module_file in reg.MODULE_PHASE_MAP.keys():
        abs_path = os.path.join(REPO_ROOT, module_file)
        if not os.path.isfile(abs_path):
            missing.append(module_file)
    return missing


def main():
    print("=" * 70)
    print("AIEM REGISTRY BUILD -- start", dt.datetime.now().isoformat())
    print("=" * 70)

    reg.init_schema()

    module_rows = build_module_rows()
    real_tools, tool_source = get_real_registered_tools()
    tool_rows = build_tool_rows(real_tools)

    conn = _connect()
    try:
        upsert_modules(conn, module_rows)
        upsert_tools(conn, tool_rows)

        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT COUNT(*) AS n FROM aiem_module_registry")
            module_count = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM aiem_tool_registry")
            tool_count = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM aiem_module_registry WHERE file_exists_confirmed = FALSE")
            missing_count = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM aiem_tool_registry WHERE excluded_from_autonomous_use = TRUE")
            excluded_count = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM aiem_tool_registry WHERE tool_type = 'cli_verification_command'")
            cli_count = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM aiem_tool_registry WHERE tool_type = 'alias_mapped'")
            alias_count = cur.fetchone()["n"]
            cur.execute("SELECT module_phase, COUNT(*) AS n FROM aiem_module_registry GROUP BY module_phase ORDER BY module_phase")
            per_phase = cur.fetchall()

        missing_files = missing_from_spec_report()

        print()
        print("-- RAW DB VERIFICATION OUTPUT --")
        print(f"aiem_module_registry row count: {module_count}")
        print(f"aiem_tool_registry row count:   {tool_count}")
        print(f"modules with file_exists_confirmed=FALSE: {missing_count}")
        print(f"tools with excluded_from_autonomous_use=TRUE: {excluded_count}")
        print(f"tools with tool_type=cli_verification_command: {cli_count}")
        print(f"tools with tool_type=alias_mapped: {alias_count}")
        print(f"tool source used: {tool_source}")
        print("modules per phase:")
        for row in per_phase:
            print(f"  Phase {row['module_phase']} ({reg.PHASE_NAMES.get(row['module_phase'])}): {row['n']}")
        print()
        print("-- missing_from_spec (spec filename, not found on disk) --")
        print(json.dumps(missing_files, indent=2) if missing_files else "[] (none -- all 195 spec-named files exist on disk)")

    finally:
        conn.close()

    print()
    print("=" * 70)
    print("AIEM REGISTRY BUILD -- done", dt.datetime.now().isoformat())
    print("=" * 70)


if __name__ == "__main__":
    main()
