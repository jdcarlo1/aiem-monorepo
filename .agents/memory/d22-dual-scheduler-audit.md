---
name: D22 dual-scheduler shared-table audit
description: aiem_process.py and main.py both run schedulers that write to 4 shared tables. All are guarded — no collision risk. Diagnostic only, no fixes needed.
---

## Context
Two independent schedulers run simultaneously:
- `aiem_process.py` — BlockingScheduler (PIDs 232, 277, workflow "aiem-process"), 18 jobs
- `main.py` — APScheduler (inside stock-api Flask process)

## Shared tables and guards

| Table | main.py writer | aiem_process.py writer | Guard |
|---|---|---|---|
| `signal_fire_log` | `log_signal_fired()` — ad-hoc whenever signal fires | `aiem_premarket_scan` (7–9 AM ET ×15min) + `aiem_open_watcher` (9–10:30 AM ×5min, then ×15min) | `ON CONFLICT (signal_name, ticker, fire_date) DO NOTHING` on both sides — idempotent |
| `signal_trust_weights` | Paper trade close loop (EOD scan ~4:15 PM ET) | `aiem_nightly_learn` (6:00 PM ET) | `ON CONFLICT (signal_name, context_bucket) DO UPDATE SET last_updated_at=NOW()` — last-writer-wins upsert; 45-min gap between windows |
| `aiem_signal_discoveries` | `save_discovery()` — inserts `status='validated'`; supervisor can UPDATE to `status='retired'` | `aiem_write_signal_discoveries` (5:15 PM ET) — inserts `status='hypothesis'`; `aiem_nightly_learn` (6:00 PM ET) — UPDATEs hypothesis→validated | No UNIQUE constraint on INSERT → distinct rows by source; UPDATE targets are different rows |
| `aiem_research_insights` | ML retraining / various jobs | `aiem_nightly_learn` (6:00 PM ET) — findings are top-3 signal summary | `ON CONFLICT (research_date) DO UPDATE SET findings = existing \|\| '\n' \|\| new` — append-safe; both writers preserve content |

## Result
No write collision risk. The dual-scheduler design is intentional:
- aiem_process.py owns all scheduled full-universe scans + learning cycle
- main.py owns live per-request signal firing + paper trade close learning
- Shared tables use database UNIQUE constraints + ON CONFLICT clauses as the coordination primitive (no advisory locks needed)

**Why:** Each writer operates on distinct rows or uses idempotent upserts. The only table where both writers can touch the same row (`aiem_research_insights` keyed by `research_date`) uses append-on-conflict, so no content is lost.
