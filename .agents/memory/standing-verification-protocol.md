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

## Rule 8 — Revert-then-verify, never verify-then-revert (added 2026-07-19)
When a file is known to contain an unapproved destructive statement (DELETE/TRUNCATE/DROP), that file may not be executed for any purpose — including evidence-gathering, verification, or snapshot capture — until it is reverted or explicitly approved.

**Why:** The DELETE run at ~23:35:47Z on 2026-07-18 was executed as "evidence-gathering" before the code was reverted. This deleted 17 test rows and the log was later overwritten, making the exact run timestamp unrecoverable. The execution itself caused the harm; the reason for running it was irrelevant.

**How to apply:** Order of operations is always REVERT first → VERIFY after. There is no exception for "before snapshot," "evidence capture," or "just checking." If the file contains destructive code, revert it before touching it.

## Verify-chain.sh native output format (recorded here for reference)
`entry_hash=<first 16 hex chars>...` is the script's native OK-line format (`stored_hash[:16]` at line 76). Not a display artifact. FAIL/BREAK paths print the full 64-char hash.

## Rule 9 — verified_run.sh calling convention + verifier format (added 2026-07-22)
Three permanent constraints on evidence-chain sealing:

1. **Single argument**: `bash ../../tools/verified_run.sh "bash tools/verify_XYZ.sh"` — ONE arg only; the command IS the label. Passing a label as arg 1 causes exit 127 (label executed as shell command) and a stray chain entry.

2. **Paths relative to `artifacts/stock-scanner-api/`**: `verified_run.sh` is always invoked via `cd artifacts/stock-scanner-api && bash ../../tools/verified_run.sh ...`. Verifier scripts run in that directory. Any `grep`/`awk` inside a verifier must use `main.py` (not `artifacts/stock-scanner-api/main.py`).

3. **PSV8 SUMMARY format**: PSV8 checks for a line matching `SUMMARY: N PASS  M FAIL` (two spaces before FAIL). Source: `verify_chain.sh:282` → `print(f"SUMMARY: {len(passes)} PASS  {len(fails)} FAIL")`. Verifier must emit exactly this format as its last line before `exit`.

**Why:** D24 sealing produced two stray chain entries (SEQ=77 exit 127, SEQ=78 exit 1) before SEQ=79 (clean 8/8 PASS, 9/9 PSV) due to arg-order and path bugs discovered only at seal time.
