---
name: Standing verification protocol
description: Five-rule evidence protocol for all AIEM (and Joel project) work — permanent, not per-task
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

## Verify-chain.sh native output format (recorded here for reference)
`entry_hash=<first 16 hex chars>...` is the script's native OK-line format (`stored_hash[:16]` at line 76). Not a display artifact. FAIL/BREAK paths print the full 64-char hash.
