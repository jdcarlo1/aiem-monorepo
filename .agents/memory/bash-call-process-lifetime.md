---
name: long-running jobs across separate bash tool calls
description: background processes/state do not survive between separate bash tool invocations in this environment; long offline jobs need checkpoint/resume, not backgrounding
---

Confirmed via direct test: a process backgrounded in one `bash` tool call
(e.g. `sleep 300 &`) does not survive into the next `bash` tool call — each
call appears to run in a context where prior backgrounded processes are
gone. You cannot kick off a long job and "check back later" across multiple
tool calls the way you could in a persistent terminal.

**Why:** This matters for any analysis/backtest/migration job whose total
runtime exceeds the ~120s single-call budget. Naively backgrounding it and
polling in a later call does not work here.

**How to apply:** For jobs that exceed one call's time budget, build a
resumable checkpoint pattern instead: persist intermediate state to disk
(pickle/JSON/DB row) on a time budget, return a clear "incomplete, rerun to
resume" signal, and have the same command/script pick up from the checkpoint
on the next invocation. See `event_study_backtest.py`'s
`_build_or_load_panels()` for a concrete implementation (checkpoints a
per-ticker feature-panel dict to a pickle file every N tickers and on a
`time_budget_seconds` deadline; reruns of the same command resume instead of
restarting). Combine with profiling the actual bottleneck first — a real
perf fix (see `rolling-apply-backtest-perf.md`) can sometimes eliminate the
need for checkpointing entirely by getting the job under budget.
