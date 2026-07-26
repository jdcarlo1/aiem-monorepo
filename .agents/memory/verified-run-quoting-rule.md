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

## Inner-quote mangling artefact (entry_hash recomputation failure)

When a command passed to `verified_run.sh` contains **both embedded `\"` escapes and `'` literals** (e.g. a `python3 -c "…os.environ[\"KEY\"]…IN ('tbl1','tbl2')…"` inline script), the `command` field written to the JSON log does not byte-for-byte reproduce the original `$CMD` bash variable.

Root cause: `verified_run.sh` serialises via `python3 -c "print(json.dumps({'command': '''$CMD''', …}))"`. The triple-quote Python string loses the distinction between `\"` (escaped double-quote) and `"`, so the stored `command` string differs from the original.

Effect: entry_hash recomputation fails for that entry (`hashlib.sha256(canonical.encode())` where `canonical` includes the stored command != the actual command). PSV9 also reports mismatch.

**What is NOT affected:** `output_sha256` is computed from the actual process stdout before serialisation, so the captured output is correct. `prev_hash` chain continuity is intact. The raw archive file in `tools/logs/verified_run_N.log` contains the actual output.

**Mitigation:** Avoid inline `python3 -c "…"` with nested `\"` inside `verified_run.sh`. Instead write the script to `/tmp/script_name.py` first and pass `python3 /tmp/script_name.py` as the wrapped command. This produces a clean, quote-free command string that recomputes correctly.
