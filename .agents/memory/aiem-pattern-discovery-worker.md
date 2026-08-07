---
name: AIEM pattern discovery continuous worker
description: How Pattern Discovery directive is handed to AIEM (workflow restart = message)
---

## Work order
`Directive_PatternDiscovery_Framework_2026-08-05` — assigned to AIEM 24/7.

## Communication (no inbox)
- Runner: `artifacts/stock-scanner-api/aiem_pattern_discovery_runner.py`
- Workflow: `artifacts/stock-scanner: pattern-discovery`
- Production service: `pattern-discovery` on port **5058** (`artifact.toml`)
- Restart that workflow / Publish = telling AIEM to start
- Trigger: `POST :5058/trigger/run`
- Status: `GET :5058/` or `/tmp/aiem_pattern_discovery_status.json`

## Isolation
Does **not** touch D1/D2/D3 or live Pattern Lab dashboard. Same standalone pattern as `aiem_stat_research_runner.py`.

## Cursor agent rule
Do **not** run this discovery loop yourself. Wire + restart AIEM; report status from health/logs/evidence files.
