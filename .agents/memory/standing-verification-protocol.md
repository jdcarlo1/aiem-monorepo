---
name: Standing verification protocol
description: Five-rule evidence protocol + lean output format directive — permanent, not per-task
---

All five rules are in permanent force. Narrative claims — "fixed," "tested," "found," "confirmed," or described code snippets with cited line numbers — are rejected without the matching raw output. If evidence cannot be produced, say so explicitly instead of substituting a description.

## Rule 1 — Raw terminal output for execution claims
Paste the actual command and its unedited stdout/stderr. Do not describe what a command produced.

## Rule 2 — Exact before/after diffs
File changes must show a real diff, not a described summary of what changed.

## Rule 3 — sha256 before/after for changed files
Any file modification must include raw `sha256sum` output before and after.

## Rule 4 — Raw SQL + full result set for DB claims
Any claim about DB state must include the literal SQL and the complete unedited result set (no "..." row omissions, no row-count substitutions).

## Rule 5 — Raw grep/sed output for any code-location or code-behavior claim (added 2026-07-17)
Any claim about a line number, function location, or code behavior must be backed by raw terminal output — not a described or reconstructed snippet. Satisfied by e.g.:

```
grep -n "<pattern>" <file>
sed -n '<start>,<end>p' <file>
```

pasted in full, raw, unedited, showing actual line numbers and content.

**Why this was added:** In ASE Round 2 addendum, the agent cited "line 76 does X, line 46 does Y" from description alone. The user had to run the commands themselves to verify. That round-trip is eliminated by requiring raw output up front with every code-location claim.

**Not satisfied by:**
- Quoting what a line "says" without a command that shows it
- Summarizing what a function does without pasted output confirming it
- Line-number citations not accompanied by the raw output that produced them

## Lean Output Format Directive (effective 2026-07-17)
Replaces long-form narrative reports. For any completion claim, output ONLY:

1. **COMMAND + RAW OUTPUT** — literal terminal output only. For test suites: final pass/fail summary line(s) only, not per-test blocks.
2. **SHA-256** — only for files that changed this session. One line: `sha256sum <file>` before and after. Do not re-hash unchanged files.
3. **DATA CHANGES** — only if rows were inserted/updated/deleted: exact SQL run + row count affected. If >10 rows need review, attach via `\copy` CSV — do NOT paste rows inline.
4. **MODIFICATION STATUS** — `git diff HEAD --stat` output only. If empty: `git diff HEAD: no changes.`

Do NOT include:
- Restated context, section banners, dividers, or headers
- "Interpretation" or narrative commentary
- Sample rows described as "representative" — attach real files or omit
- Re-verification of things that didn't change this session

**Why:** Prior sessions generated 18,000-line packages with prose, repeated headers, and "representative row" descriptions. The directive separates format (lean) from proof standard (unchanged — raw evidence still required).

## Rule 6 — No destructive statement in any file without prior in-session approval (added 2026-07-19)
No DELETE, TRUNCATE, or DROP statement may be written into any source file, script, or verifier — even if the file will not be executed immediately — without Joel's explicit prior approval in that session. Flag intent and wait every time. This applies to code changes, not just direct SQL execution.

**Why:** Agent wrote `DELETE FROM oe_audit_events WHERE is_test_record=TRUE` into verify_phase5.py without prior approval (replacing the existing TRUNCATE). File was committed before the violation was caught. Approved TRUNCATE was then approved separately. The rule is: intent must be flagged and approved before the line is written.

**How to apply:** Before writing any DELETE/TRUNCATE/DROP into a file, state the exact statement you intend to write and wait for explicit approval. "Already present" or "replaces existing destructive statement" is not an exemption.

## Rule 7 — DB role disclosure (added 2026-07-19)
The agent connects to the database as `postgres` (rolsuper=True, rolbypassrls=True). It holds DELETE+TRUNCATE+UPDATE+INSERT+SELECT on all tables including production tables (aiem_paper_trades, d3_governance_decisions, oe_* series). No grants have been revoked. Any grant changes require Joel's explicit direction.

## Verify-chain.sh native output format (recorded here for reference)
`entry_hash=<first 16 hex chars>...` is the script's native OK-line format (`stored_hash[:16]` at line 76). Not a display artifact. FAIL/BREAK paths print the full 64-char hash.
