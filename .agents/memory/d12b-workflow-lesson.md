---
name: D12b workflow-only lesson
description: Replit background processes die when the bash command exits; only registered workflows survive via the process supervisor.
---

# Replit Background Process Survival Rule

Any Python (or other) process launched via `setsid`, `nohup`, or `&` inside a bash tool
call **will be killed** within ~30s of the bash command exiting. This is a Replit sandbox
constraint — the platform cleans up processes it doesn't own.

**Why:** Replit's process supervisor tracks only workflow-managed PIDs. Non-workflow
processes are not protected and are cleaned up after the bash shell closes.

**How to apply:** Any background monitoring daemon, scheduler, or long-running script
that must survive beyond the current bash command MUST be registered as a Replit
workflow via `configureWorkflow()` (workflows skill), then started with `restartWorkflow()`.

## Secondary bugs caught during D12b

### pgrep false-positive with shell pipe
`pgrep -af 'd12_monitor.py' | grep python3` matches the `sh -c` process running
the pipeline itself (its argv contains both strings). Use instead:
```bash
pgrep -f "python3 /full/path/to/script.py"
```
The full path is unique enough that no other process matches it.

### Duplicate log lines from stdout-redirect + file-write
If `_log()` calls both `print()` (stdout) AND `open(file, "a").write()`,
and stdout is redirected to the SAME file, every line appears twice.
Fix: remove `print()` from `_log()` and rely only on the explicit file write.
Or redirect stdout to a different file (e.g., `_stdout.log`) from the log file.
