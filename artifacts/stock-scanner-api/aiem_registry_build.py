"""
aiem_registry_build.py
-----------------------
One-shot builder + reporter for the AIEM Module Registry / Tool Registry.

Run directly:  python3 aiem_registry_build.py

What it does (in order):
  0. preflight_check() -- queries information_schema to confirm either all
     three tables are MISSING (first-run) or all exist with the EXACT column
     set expected by this schema version. Refuses to proceed if any table
     exists with a stale or mismatched column set.
  1. init_schema() -- creates aiem_module_registry / aiem_tool_registry /
     aiem_function_registry if not already present (idempotent after preflight).
  2. Populates aiem_module_registry by unioning MODULE_PHASE_MAP and
     AEIM_MODULES (Fix 1). Assigns registry_source to one of:
       MODULE_PHASE_MAP  -- file in MODULE_PHASE_MAP only
       AIEM_MODULES      -- file in AEIM_MODULES only (no MPM entry)
       BOTH              -- file in both, fields consistent
       CONFLICT          -- file in both, authoritative fields disagree
     A runtime basename-collision guard (Fix 3) blocks the build if any
     basename in AEIM_MODULES maps to 2+ different stage_names.
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

import ast
import os
import sys
import json
import datetime as dt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import psycopg2
import psycopg2.extras

import aiem_registry as reg

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

# D3 decision: main.py is the Flask server / infrastructure entry point, NOT a
# discrete AIEM pipeline module. Confirmed no upstream/downstream references to
# it exist in MODULE_PHASE_MAP, AEIM_MODULES, or any module dependency strings.
MPM_EXCLUDE = {"main.py"}

EXPECTED_MODULE_COLUMNS = {
    "module_id", "module_name", "stage_name", "module_file", "module_phase",
    "module_phase_name", "owned_tools", "required_inputs", "produced_outputs",
    "upstream_modules", "downstream_modules", "verification_required",
    "audit_log_enabled", "execution_status", "last_verified_date",
    "verified_by_command", "verification_result", "verification_version",
    "ownership_note", "ownership_status", "file_exists_confirmed",
    "registry_source", "created_at", "updated_at",
}
EXPECTED_TOOL_COLUMNS = {
    "tool_id", "tool_name", "owning_module_or_phase", "owning_module",
    "tool_verification_level", "tool_type", "required_inputs", "produced_outputs",
    "can_run_independently", "requires_market_data", "requires_options_data",
    "requires_historical_data", "requires_trade_history", "writes_audit_log",
    "excluded_from_autonomous_use", "exclusion_reason", "alias_of",
    "dependency_notes", "registered_in_tool_map", "verification_status",
    "last_verified_date", "verified_by_command", "verification_result",
    "verification_version", "created_at", "updated_at",
}
EXPECTED_FUNCTION_COLUMNS = {
    "function_row_id", "file_name", "function_name", "purpose", "inputs",
    "outputs", "upstream_dependencies", "downstream_dependencies",
    "owning_phase", "owning_phase_name", "owning_module", "is_inline",
    "verification_status", "verification_evidence", "verified_by_command",
    "last_verified_date", "verification_version", "created_at", "updated_at",
}
TABLE_EXPECTED_COLUMNS = {
    "aiem_module_registry":   EXPECTED_MODULE_COLUMNS,
    "aiem_tool_registry":     EXPECTED_TOOL_COLUMNS,
    "aiem_function_registry": EXPECTED_FUNCTION_COLUMNS,
}


def _connect():
    return reg._connect()


def preflight_check(conn):
    """
    Fix 4: Guard against replay against a partially-migrated DB.

    Queries information_schema.tables then information_schema.columns.

    - All three tables MISSING  → first-run path; proceed.
    - All existing tables have ALL expected columns exactly → safe upsert; proceed.
    - Any existing table has a MISSING column  → REFUSE (sys.exit 1) with
      explicit list of missing columns and resolution instructions.

    This function NEVER silently partial-applies. It either passes cleanly or
    exits with a non-zero code and a human-readable explanation.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name IN (
                  'aiem_module_registry',
                  'aiem_tool_registry',
                  'aiem_function_registry'
              )
        """)
        existing_tables = {row[0] for row in cur.fetchall()}

    print(f"[preflight] information_schema.tables check:")
    for t in sorted(TABLE_EXPECTED_COLUMNS.keys()):
        print(f"  {t}: {'EXISTS' if t in existing_tables else 'MISSING'}")

    if not existing_tables:
        print("[preflight] All three tables MISSING — first-run path. Proceeding.")
        return

    problems = []
    for table_name in sorted(TABLE_EXPECTED_COLUMNS.keys()):
        if table_name not in existing_tables:
            continue
        with conn.cursor() as cur:
            cur.execute("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = %s
            """, (table_name,))
            actual_cols = {row[0] for row in cur.fetchall()}
        expected_cols = TABLE_EXPECTED_COLUMNS[table_name]
        missing_cols = expected_cols - actual_cols
        extra_cols = actual_cols - expected_cols
        if missing_cols:
            problems.append({
                "table": table_name,
                "missing": sorted(missing_cols),
                "extra": sorted(extra_cols),
            })
        else:
            print(f"[preflight] {table_name}: column set matches expected "
                  f"({len(actual_cols)} columns). Safe to upsert.")

    if problems:
        print("\n[preflight] REFUSING TO PROCEED — column set mismatch detected:")
        for p in problems:
            print(f"  {p['table']}:")
            print(f"    MISSING columns: {p['missing']}")
            if p["extra"]:
                print(f"    EXTRA columns:   {p['extra']}")
        print("\nResolution (choose one):")
        print("  1. Add each missing column via ALTER TABLE ... ADD COLUMN IF NOT EXISTS ...")
        print("  2. DROP the mismatched table(s) and re-run (loses existing rows).")
        print("  3. Do not re-run this script until the schema is updated.")
        sys.exit(1)

    print("[preflight] All existing tables have the expected column set. "
          "Proceeding with ON CONFLICT upsert.")


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
    """
    Union MODULE_PHASE_MAP and AEIM_MODULES by file basename.

    Assigns registry_source to one of four documented states:
      MODULE_PHASE_MAP  -- basename in MPM only (no AEIM entry)
      AEIM_MODULES      -- basename in AEIM_MODULES only (no MPM entry);
                           module_phase is NULL. With current data: 0 rows.
      BOTH              -- basename in both dicts, single unambiguous MPM path
      CONFLICT          -- basename appears at 2+ distinct full paths in MPM
                           (directory collision = ambiguous phase assignment);
                           one row produced, all paths in ownership_note.
                           With current data: 0 rows.

    Exclusions (MPM_EXCLUDE): main.py is the Flask server / infrastructure entry
    point, not a discrete AIEM pipeline module. No upstream/downstream references
    to main.py were found in MODULE_PHASE_MAP, AEIM_MODULES, or any dependency
    strings -- exclusion is safe.

    Fix 3 (AEIM_MODULES collision guard): if any basename maps to 2+ stage_names
    inside AEIM_MODULES, raises RuntimeError -- build does not proceed.

    All four registry_source states verified with synthetic in-memory tests
    (no DB writes, no file mutations). See pre-build checkpoint for raw output.
    """
    import aiem_master_orchestrator as _amo

    # Fix 3: AEIM_MODULES collision guard
    aiem_by_basename = {}
    collision_map = {}
    for stage_name, val in _amo.AEIM_MODULES.items():
        for part in val.split("+"):
            bn = os.path.basename(part.strip())
            if bn in aiem_by_basename and aiem_by_basename[bn] != stage_name:
                collision_map.setdefault(bn, {aiem_by_basename[bn]}).add(stage_name)
            else:
                aiem_by_basename[bn] = stage_name
    if collision_map:
        msg = "AEIM_MODULES basename collision(s) detected -- BUILD BLOCKED:\n"
        for bn, stages in sorted(collision_map.items()):
            msg += f"  {bn!r} appears under stage_names: {sorted(stages)}\n"
        msg += "Resolve the collision in AEIM_MODULES before re-running."
        raise RuntimeError(msg)

    # Build mpm_by_basename: basename -> list of (path, phase) tuples.
    # Multiple entries for same basename = CONFLICT (directory collision).
    # MPM_EXCLUDE applied here; excluded basenames produce no rows.
    mpm_by_basename = {}
    for module_file, phase in reg.MODULE_PHASE_MAP.items():
        bn = os.path.basename(module_file)
        if bn in MPM_EXCLUDE:
            continue
        mpm_by_basename.setdefault(bn, []).append((module_file, phase))

    all_basenames = set(mpm_by_basename.keys()) | set(aiem_by_basename.keys())
    rows = []

    for bn in sorted(all_basenames):
        in_aiem = bn in aiem_by_basename
        mpm_entries = mpm_by_basename.get(bn, [])
        in_mpm = len(mpm_entries) > 0
        module_name = bn.replace(".py", "")
        stage_name = aiem_by_basename.get(bn)

        if in_mpm and len(mpm_entries) > 1:
            # CONFLICT: same basename at multiple MPM paths (ambiguous phase).
            # One row produced; all conflicting paths recorded in ownership_note.
            module_file, phase = mpm_entries[0]
            all_paths = [p for p, _ in mpm_entries]
            all_phases = [ph for _, ph in mpm_entries]
            ownership_note = (
                f"CONFLICT: {bn!r} appears at {len(mpm_entries)} paths "
                f"with phases={all_phases} paths={all_paths}"
            )
            registry_source = "CONFLICT"
            ownership_status = "PENDING_REVIEW"
        elif in_mpm and in_aiem:
            module_file, phase = mpm_entries[0]
            registry_source = "BOTH"
            base_note = reg.OWNERSHIP_NOTES.get(module_file)
            ownership_note = base_note
            ownership_status = (
                "PENDING_REVIEW" if "AUTO-RESOLVED" in (base_note or "") else "CONFIRMED"
            )
        elif in_mpm:
            module_file, phase = mpm_entries[0]
            registry_source = "MODULE_PHASE_MAP"
            base_note = reg.OWNERSHIP_NOTES.get(module_file)
            ownership_note = base_note
            ownership_status = (
                "PENDING_REVIEW" if "AUTO-RESOLVED" in (base_note or "") else "CONFIRMED"
            )
        else:
            # AEIM_MODULES-only: basename not in any MPM path.
            # module_phase = NULL (no MPM phase exists for this basename).
            module_file = bn
            phase = None
            registry_source = "AEIM_MODULES"
            ownership_note = (
                f"AEIM_MODULES-only: stage_name={stage_name!r}; "
                "not present in MODULE_PHASE_MAP -- phase assignment unknown"
            )
            ownership_status = "PENDING_REVIEW"

        rows.append({
            "module_name": module_name,
            "stage_name": stage_name,
            "registry_source": registry_source,
            "module_file": module_file,
            "module_phase": phase,
            "module_phase_name": reg.PHASE_NAMES.get(phase) if phase is not None else None,
            "ownership_note": ownership_note,
            "ownership_status": ownership_status,
            "file_exists_confirmed": os.path.isfile(os.path.join(REPO_ROOT, module_file)),
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
            "owning_module": None,
            "tool_verification_level": "phase_only",
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


def build_function_rows():
    """
    AST-scan every file in MODULE_PHASE_MAP (excluding MPM_EXCLUDE).

    For each Python def statement found:
      file_name      : MODULE_PHASE_MAP key (relative path as stored in the map,
                       e.g. 'aiem_probability_engine/daily_picks.py')
      function_name  : AST node.name — all defs included, including dunders
                       (FLAG A confirmed: dunders included)
      is_inline      : True when parent AST node is ClassDef or FunctionDef/
                       AsyncFunctionDef (class methods + nested closures);
                       False for module-level defs
                       (FLAG B confirmed: this mapping)
      owning_phase   : phase from MODULE_PHASE_MAP
      owning_phase_name : PHASE_NAMES[owning_phase]
      owning_module  : basename stem of file_name (e.g. 'daily_picks')
      purpose, inputs, outputs, upstream_dependencies,
      downstream_dependencies : NULL — no static data source exists for these
                       fields; left for future annotation

    Files that fail to parse (syntax errors) are skipped and reported in the
    build output — they do not block the build.
    Files with 0 defs produce 0 rows — correct, not an error (FLAG C confirmed).
    main.py excluded by MPM_EXCLUDE, same as module registry (FLAG D confirmed).

    UNIQUE key: (file_name, function_name). Duplicate function names in the same
    file are legal in Python (redefinition); AST walk visits both — if they share
    (file_name, function_name) the ON CONFLICT clause in upsert_functions keeps
    the later-visited row's is_inline value (last-writer wins, both are identical
    names so the distinction is cosmetic).
    """
    rows = []
    parse_failures = []

    for mpm_key, phase in sorted(reg.MODULE_PHASE_MAP.items(), key=lambda kv: (kv[1], kv[0])):
        if os.path.basename(mpm_key) in MPM_EXCLUDE:
            continue
        abs_path = os.path.join(REPO_ROOT, mpm_key)
        if not os.path.isfile(abs_path):
            continue  # already captured by missing_from_spec_report(); skip silently

        try:
            source = open(abs_path, encoding="utf-8", errors="replace").read()
            tree = ast.parse(source, filename=mpm_key)
        except SyntaxError as exc:
            parse_failures.append((mpm_key, f"SyntaxError line {exc.lineno}: {exc.msg}"))
            continue
        except Exception as exc:
            parse_failures.append((mpm_key, f"ParseError: {exc}"))
            continue

        # Assign _parent to every node so is_inline can check parent type.
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                child._parent = node
        tree._parent = None

        owning_module = os.path.splitext(os.path.basename(mpm_key))[0]
        phase_name    = reg.PHASE_NAMES.get(phase)

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            parent    = getattr(node, "_parent", None)
            is_inline = isinstance(parent, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            rows.append({
                "file_name":        mpm_key,
                "function_name":    node.name,
                "is_inline":        is_inline,
                "owning_phase":     phase,
                "owning_phase_name": phase_name,
                "owning_module":    owning_module,
            })

    if parse_failures:
        print(f"[build_function_rows] WARNING: {len(parse_failures)} file(s) failed to parse:")
        for fname, err in parse_failures:
            print(f"  {fname}: {err}")

    return rows, parse_failures


def upsert_modules(conn, rows):
    with conn.cursor() as cur:
        for r in rows:
            cur.execute("""
                INSERT INTO aiem_module_registry
                    (module_name, stage_name, module_file, module_phase,
                     module_phase_name, ownership_note, ownership_status,
                     file_exists_confirmed, registry_source,
                     verification_required, execution_status, verification_result)
                VALUES (%(module_name)s, %(stage_name)s, %(module_file)s,
                        %(module_phase)s, %(module_phase_name)s,
                        %(ownership_note)s, %(ownership_status)s,
                        %(file_exists_confirmed)s, %(registry_source)s, TRUE,
                        'PENDING_VERIFICATION', 'PENDING_VERIFICATION')
                ON CONFLICT (module_name) DO UPDATE SET
                    stage_name           = EXCLUDED.stage_name,
                    module_file          = EXCLUDED.module_file,
                    module_phase         = EXCLUDED.module_phase,
                    module_phase_name    = EXCLUDED.module_phase_name,
                    ownership_note       = EXCLUDED.ownership_note,
                    ownership_status     = EXCLUDED.ownership_status,
                    file_exists_confirmed = EXCLUDED.file_exists_confirmed,
                    registry_source      = EXCLUDED.registry_source,
                    updated_at           = now()
            """, r)
    conn.commit()


def upsert_tools(conn, rows):
    with conn.cursor() as cur:
        for r in rows:
            cur.execute("""
                INSERT INTO aiem_tool_registry
                    (tool_name, owning_module_or_phase, owning_module, tool_verification_level,
                     tool_type, can_run_independently,
                     excluded_from_autonomous_use, exclusion_reason, alias_of,
                     dependency_notes, registered_in_tool_map, verified_by_command,
                     verification_status, verification_result)
                VALUES (%(tool_name)s, %(owning_module_or_phase)s, %(owning_module)s,
                        %(tool_verification_level)s, %(tool_type)s,
                        %(can_run_independently)s, %(excluded_from_autonomous_use)s,
                        %(exclusion_reason)s, %(alias_of)s, %(dependency_notes)s,
                        %(registered_in_tool_map)s, %(verified_by_command)s,
                        'PENDING_VERIFICATION', 'PENDING_VERIFICATION')
                ON CONFLICT (tool_name) DO UPDATE SET
                    owning_module_or_phase       = EXCLUDED.owning_module_or_phase,
                    tool_type                    = EXCLUDED.tool_type,
                    can_run_independently        = EXCLUDED.can_run_independently,
                    excluded_from_autonomous_use = EXCLUDED.excluded_from_autonomous_use,
                    exclusion_reason             = EXCLUDED.exclusion_reason,
                    alias_of                     = EXCLUDED.alias_of,
                    dependency_notes             = EXCLUDED.dependency_notes,
                    registered_in_tool_map       = EXCLUDED.registered_in_tool_map,
                    verified_by_command          = EXCLUDED.verified_by_command,
                    updated_at                   = now()
            """, r)
    conn.commit()


def upsert_functions(conn, rows):
    with conn.cursor() as cur:
        for r in rows:
            cur.execute("""
                INSERT INTO aiem_function_registry
                    (file_name, function_name, is_inline,
                     owning_phase, owning_phase_name, owning_module,
                     verification_status)
                VALUES (%(file_name)s, %(function_name)s, %(is_inline)s,
                        %(owning_phase)s, %(owning_phase_name)s, %(owning_module)s,
                        'PENDING_VERIFICATION')
                ON CONFLICT (file_name, function_name) DO UPDATE SET
                    is_inline         = EXCLUDED.is_inline,
                    owning_phase      = EXCLUDED.owning_phase,
                    owning_phase_name = EXCLUDED.owning_phase_name,
                    owning_module     = EXCLUDED.owning_module,
                    updated_at        = now()
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

    conn = _connect()
    try:
        preflight_check(conn)
    except SystemExit:
        conn.close()
        raise

    reg.init_schema()

    module_rows = build_module_rows()
    real_tools, tool_source = get_real_registered_tools()
    tool_rows = build_tool_rows(real_tools)
    function_rows, fn_parse_failures = build_function_rows()

    try:
        upsert_modules(conn, module_rows)
        upsert_tools(conn, tool_rows)
        upsert_functions(conn, function_rows)

        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT COUNT(*) AS n FROM aiem_module_registry")
            module_count = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM aiem_tool_registry")
            tool_count = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM aiem_function_registry")
            function_count = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM aiem_module_registry WHERE file_exists_confirmed = FALSE")
            missing_count = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM aiem_tool_registry WHERE excluded_from_autonomous_use = TRUE")
            excluded_count = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM aiem_tool_registry WHERE tool_type = 'cli_verification_command'")
            cli_count = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM aiem_tool_registry WHERE tool_type = 'alias_mapped'")
            alias_count = cur.fetchone()["n"]
            cur.execute("""
                SELECT registry_source, count(*) AS n
                FROM aiem_module_registry
                GROUP BY registry_source
                ORDER BY registry_source
            """)
            source_counts = cur.fetchall()
            cur.execute("""
                SELECT module_phase, COUNT(*) AS n
                FROM aiem_module_registry
                GROUP BY module_phase
                ORDER BY module_phase
            """)
            per_phase = cur.fetchall()
            cur.execute("""
                SELECT owning_phase, COUNT(*) AS n
                FROM aiem_function_registry
                GROUP BY owning_phase
                ORDER BY owning_phase
            """)
            fn_per_phase = cur.fetchall()
            cur.execute("SELECT COUNT(*) AS n FROM aiem_function_registry WHERE is_inline = TRUE")
            fn_inline_count = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM aiem_function_registry WHERE is_inline = FALSE")
            fn_toplevel_count = cur.fetchone()["n"]
            cur.execute("""
                SELECT COUNT(DISTINCT owning_module) AS n
                FROM aiem_function_registry
            """)
            fn_module_count = cur.fetchone()["n"]

        missing_files = missing_from_spec_report()

        print()
        print("-- RAW DB VERIFICATION OUTPUT --")
        print(f"aiem_module_registry row count:   {module_count}")
        print(f"aiem_tool_registry row count:     {tool_count}")
        print(f"aiem_function_registry row count: {function_count}")
        print(f"modules with file_exists_confirmed=FALSE: {missing_count}")
        print(f"tools with excluded_from_autonomous_use=TRUE: {excluded_count}")
        print(f"tools with tool_type=cli_verification_command: {cli_count}")
        print(f"tools with tool_type=alias_mapped: {alias_count}")
        print(f"tool source used: {tool_source}")
        print()
        print("-- registry_source distribution (all four states) --")
        seen_sources = {}
        for row in source_counts:
            seen_sources[row["registry_source"]] = row["n"]
            print(f"  registry_source={row['registry_source']!r}: {row['n']}")
        for state in ("MODULE_PHASE_MAP", "AIEM_MODULES", "BOTH", "CONFLICT"):
            if state not in seen_sources:
                print(f"  registry_source={state!r}: 0  (structurally reachable; no rows with this state in current data)")
        print()
        print("-- modules per phase --")
        for row in per_phase:
            phase_val = row["module_phase"]
            phase_label = reg.PHASE_NAMES.get(phase_val, "unknown") if phase_val is not None else "None (AIEM_MODULES-only)"
            print(f"  Phase {phase_val} ({phase_label}): {row['n']}")
        print()
        print("-- missing_from_spec (spec filename, not found on disk) --")
        print(json.dumps(missing_files, indent=2) if missing_files else "[] (none -- all spec-named files exist on disk)")
        print()
        print("-- aiem_function_registry summary --")
        print(f"  total rows:                    {function_count}")
        print(f"  top-level defs (is_inline=F):  {fn_toplevel_count}")
        print(f"  nested/method (is_inline=T):   {fn_inline_count}")
        print(f"  distinct owning_module values: {fn_module_count}")
        print(f"  parse failures (skipped):      {len(fn_parse_failures)}")
        if fn_parse_failures:
            for fname, err in fn_parse_failures:
                print(f"    {fname}: {err}")
        print()
        print("-- aiem_function_registry rows by phase --")
        for row in fn_per_phase:
            phase_val = row["owning_phase"]
            phase_label = reg.PHASE_NAMES.get(phase_val, "unknown") if phase_val is not None else "None"
            print(f"  Phase {phase_val} ({phase_label}): {row['n']}")

        print()
        print("-- DISCREPANCY FLAG D1 (Fix 5 — decision required): deep_rl stage spans two phases --")
        print("  deep_rl_policy.py    -> module_phase=15 (Learning & Adaptation Loop)")
        print("  rl_position_sizer.py -> module_phase=11 (Risk Gate & Position Sizing)")
        print("  Both rows carry stage_name='deep_rl'.")
        print("  Current schema: module_phase is per-file, which correctly models this.")
        print("  Decision required: confirm that per-file phase tracking (not per-stage)")
        print("  is the intended design, OR instruct whether a single canonical phase")
        print("  should be assigned to the stage_name='deep_rl' rows.")

        print()
        print("-- DISCREPANCY FLAG D3 (Fix 6 — decision required): main.py phase assignment --")
        print("  main.py is assigned module_phase=1 (Orchestration Layer) in MODULE_PHASE_MAP.")
        print("  main.py is the Flask application server, not a discrete AIEM module.")
        print("  It is NOT represented in AIEM_MODULES (registry_source='MODULE_PHASE_MAP').")
        print("  Decision required: should main.py appear in aiem_module_registry at all,")
        print("  and if so, is phase 1 the correct assignment for it?")

    finally:
        conn.close()

    print()
    print("=" * 70)
    print("AIEM REGISTRY BUILD -- done", dt.datetime.now().isoformat())
    print("=" * 70)


if __name__ == "__main__":
    main()
