---
name: CorrelationGuard 60s cache lets open-position cap be oversold in one fast batch
description: aiem_risk_guards.CorrelationGuard._load_open() caches for 60s, so a single fast force-execute run can admit more trades than the declared _CG_MAX_OPEN_POSITIONS cap.
---

`CorrelationGuard._load_open()` (aiem_risk_guards.py) caches the open-positions DB query for 60
seconds (`self._cache_ts`). `check()` calls `_load_open()` fresh each time but gets the SAME
cached snapshot for the whole run if the run finishes inside that 60s window.

**Why this matters:** a batch of candidates evaluated in under a minute (typical for
`_aiem_paper_execute_today`) never sees its own just-inserted trades reflected in `n_open`, so
the `n_open >= _CG_MAX_OPEN_POSITIONS` (20) check keeps comparing against the stale start-of-run
count for every candidate in that run. Observed live 2026-07-08: run started at 17 open, admitted
8 candidates in ~13s, ended at 25 open — 5 over the declared cap of 20 — purely because of the
cache, not because the gate was bypassed or weakened by anyone.

**How to apply:** if asked to enforce the open-position cap strictly per-run (not just
per-day-boundary), either drop the 60s TTL for this guard or increment an in-memory counter
locally as trades are admitted within the same run, instead of re-trusting `_load_open()`'s
cache mid-batch. Do not silently patch this without flagging it — it's a real risk-limit gap the
user should decide whether/when to fix, per the project's "never weaken risk limits without
explicit sign-off" rule (weakening includes accidentally, not just deliberately).
