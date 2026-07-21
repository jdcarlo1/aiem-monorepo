---
name: bash set -u dollar-sign in python3 -c double-quoted block
description: $N in Python comments or strings inside bash python3 -c "..." double-quoted blocks cause set -u unbound-variable errors. Fix: rewrite comments, or use heredoc with single-quoted delimiter.
---

## Rule

Any `$N` (positional parameter) or `${VAR}` reference inside a `python3 -c "..."` bash double-quoted string
is expanded by bash BEFORE python3 sees it. With `set -u` active, unbound positional parameters (`$4`,
`$2`, etc.) cause an immediate abort: `line N: $4: unbound variable`.

## Where it bit us

`tools/verified_run.sh` — Python code block embedded in bash double-quoted string. A comment line read:
```
# Column 4 (1-indexed) = archive_sha256.  PSV2: awk -F'\t' '$1==seq {print $4}'.
```
Bash saw `$4` (4th positional param), `set -u` fired, script exited at line 113 with no output and no
chain entry written. The script exit was silent because `python3 -c "..." 2>&1 || echo "[dpl_chain] ERROR..."`
never ran — bash aborted the whole `python3 -c "..."` invocation before starting python.

## Fix applied

Rewrote the comment to avoid `$N` references:
```
# Column 4 (1-indexed) = archive_sha256.  PSV2 reads col-4 with awk field split.
```

## Correct pattern for future python blocks

**Option 1 — safest (no bash expansion at all):** use a single-quoted heredoc delimiter:
```bash
VAR1="$SOME_BASH_VAR"
export _PY_VAR1="$VAR1"
python3 << 'PYEOF'
import os
val = os.environ['_PY_VAR1']
# any $N reference in comments is safe here
PYEOF
```

**Option 2 — minimal fix:** escape all `$N` as `\$N` and `${VAR}` as `\${VAR}` in comments and
string literals that bash should NOT expand:
```python
# awk -F'\t' '\$1==seq {print \$4}'  # safe — bash strips the backslash → python sees $4
```

**Why:**  In bash double-quotes, `\` only quoting `$`, `` ` ``, `"`, `\`, newline — so `\$4` in
double-quotes becomes literal `$4` passed to python. This preserves the python comment text exactly.

## Diagnosis pattern

If a `python3 -c "..."` block in a `set -u` script silently fails at the line the block STARTS
(not inside the Python code), check every `$` reference in the embedded Python for positional params.
The bash error references the line number of the opening `python3 -c "` quote, not the Python line.
