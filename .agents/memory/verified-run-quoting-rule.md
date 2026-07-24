---
name: verified_run.sh quoting rule
description: verified_run.sh requires a single quoted command string — separate args silently truncate to $1 only, producing an empty archive and PSV8 failure
---

## Rule

Always invoke `tools/verified_run.sh` with the full command as a **single quoted string**:

```bash
# CORRECT
bash tools/verified_run.sh "python3 tools/test_catchup_guard.py"

# WRONG — only $1 ("python3") is used as CMD; test never runs; archive is empty
bash tools/verified_run.sh python3 tools/test_catchup_guard.py
```

**Why:** The script assigns `CMD="$1"` (or equivalent). Extra positional args are silently ignored. The command runs, but output is empty, so the archive has no `SUMMARY:` line → PSV8 fails with "SUMMARY: line not found in archive."

**How to apply:** Any time verified_run.sh is called from shell, CI, or docs, wrap the command in double quotes. This applies to the `tools/` version (top-level workspace) and the `artifacts/stock-scanner-api/tools/` version equally.

**How it was discovered:** PSV8 failed at SEQ=115 with `CMD=python3` in the archive header — archive was empty because interactive Python produced no output. The CORRECT invocation at SEQ=116 immediately produced 9/9 PASS.
