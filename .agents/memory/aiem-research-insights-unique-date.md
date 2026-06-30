---
name: aiem_research_insights one-row-per-day constraint
description: research_date has a table-wide UNIQUE constraint (not per session_name) — loops of per-finding INSERTs silently lose all but the first row every day.
---

`aiem_research_insights` (defined in `main.py`) has `research_date DATE NOT NULL UNIQUE` — that
constraint is global across the WHOLE table, not scoped per `session_name`. Any job that loops and
does one `INSERT` per finding for `today` (the pre-existing `aiem_missed_runner_analysis` pattern, and
likely others across `main.py` that follow the same loop-insert shape) will succeed on the first row
and then abort the transaction on every subsequent insert for the rest of that run — silently dropping
the remaining findings with no visible error unless you check job logs closely.

**Why:** discovered while testing the cap-bucket missed-runner upgrade — a mocked 4-bucket run showed
3 of 4 findings vanish with "duplicate key value violates unique constraint
aiem_research_insights_research_date_key" followed by "current transaction is aborted" for every
subsequent statement on that connection.

**How to apply:** any job writing multiple findings for the same day to this table must accumulate
them into ONE combined string and do a single `INSERT ... ON CONFLICT (research_date) DO UPDATE SET
findings = aiem_research_insights.findings || E'\n' || EXCLUDED.findings` (append, never overwrite —
other jobs/processes may have already written a row for today under a different session_name).
Fixed this way in `aiem_missed_runner_analysis()`; the same upsert-append pattern should be used for
any other per-day multi-finding writer to this table, including ones in `main.py` not yet audited.
