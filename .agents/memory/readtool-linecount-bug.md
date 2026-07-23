---
name: read tool line-count mismatch on large files
description: For files ≥70k lines, the read tool reports a wrong (much smaller) file length and silently clips to the last 50 lines when the requested offset exceeds its internal limit. Use wc -l or sed for truth.
---

## Rule
For any file that might exceed ~20k lines (e.g. main.py), do NOT trust the
"file length" number reported by the read tool when you give it a large offset.
Always verify actual line count with `wc -l <file>` before reading deep offsets.

## Why
Observed: `read(file_path="main.py", offset=69240)` reported "file length 20100"
and showed the last 50 lines (20051-20100). Actual `wc -l` = 70103. The tool
silently capped and returned wrong content without a clear error.

## How to apply
- Use `wc -l <file>` before `read(offset=N, limit=M)` for any large source file.
- Use `sed -n 'N,Mp' <file>` as a reliable alternative for reading specific line ranges in large files.
- grep -n results with line numbers ≥ 30k should trigger a wc -l sanity check.
