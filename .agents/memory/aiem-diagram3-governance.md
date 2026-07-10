---
name: AIEM Diagram 3 Governance Layer
description: Pure supervisory governance layer over D1+D2; 16 phases, 17 d3_ tables, 25 admin endpoints, advisory-only (cannot enforce), tamper-evident event ledger; NEVER modifies D1/D2
---

# AIEM Diagram 3 — Autonomous Governance, Self-Optimization & Evolution Layer

**Why:** D3 is the executive governance layer above D1 (orchestration) and D2 (verification). Its purpose is monitoring, integrity enforcement, rollback protection, and optimization recommendations — never code changes.

**Core rule:** D3 reads from production tables only. It never writes to D1/D2 tables. No fabricated metrics.

## Key Files
- `artifacts/stock-scanner-api/aiem_diagram3_governance.py` — 16 phases (phase0-phase15), schema, `install_d3_routes()`, `request_governance_action()`/`check_action_status()`
- `artifacts/stock-scanner-api/aiem_diagram3_verification.py` — independent CLI validator (`verify`/`timeline` commands), re-derives PASS/FAIL from its own SQL rather than trusting the governance module's own claims
- `artifacts/stock-scanner-api/AIEM_DIAGRAM3_STRICT_VERIFICATION_REPORT.md` — full strict verification report (2026-07-10), the authoritative source of truth for what's proven vs. gapped

## 17 D3 Tables (real count — see log-count gotcha below)
d3_architecture_baseline, d3_system_health_snapshots, d3_performance_snapshots,
d3_strategy_registry, d3_model_governance, d3_learning_approvals, d3_change_log,
d3_version_history, d3_rollback_registry, d3_optimization_recommendations,
d3_system_forecasts, d3_security_reports, d3_architecture_status,
d3_executive_reports, d3_evolution_plan, d3_governance_event_links (event ledger),
d3_governance_actions (action/enforcement-status tracking)

**Log-count gotcha:** the boot line `[d3_governance] schema init complete — 72 d3_
tables ready` is mislabeled — `72` = `len(_SCHEMA_STMTS)`, i.e. every CREATE
TABLE/INDEX/TRIGGER statement combined, not a table count. Always get the real
count via `information_schema.tables WHERE table_name LIKE 'd3\_%'` (=17), never
trust that log line as a table count.

## Baseline Hash (first production freeze)
`61a65ca7587d79fd...` (first 16 hex chars used as short ref elsewhere). Frozen
2026-07-08 20:51:19 UTC. 195 modules, 220 tools, 21 D2 stages. Protected=True. Never overwrite.

## 25 Admin Endpoints
All at `/stock-api/admin/d3/` with `X-Admin-Token` auth (verified live via grep of
`install_d3_routes()`, not from memory of an older count):
status, baseline, freeze-baseline, discovery, health, performance, strategy-registry,
model-registry, learning-approvals, change-log, log-change, version-history, rollback,
optimization, forecast, security, architecture, executive-report, generate-report,
evolution-plan, trace/<trace_id>, run-cycle, actions, actions/<action_id>,
actions/<action_id>/recheck

## Wiring in main.py
1. `import aiem_diagram3_governance as _d3_gov` + `_DEFERRED_INITS.append(lambda: _d3_gov.d3_startup())`
2. `_d3_gov.install_d3_routes(app)`
3. **New (2026-07-10):** real per-trade provenance link in
   `_aiem_close_paper_trade_and_run_loop` → `link_paper_trade_close(...)` — the
   ONLY live D2→D3 wiring point across all ~21 D2 stages/18 events (confirmed via
   `aiem_diagram3_verification.py timeline`). A lightweight single-event append,
   not a full 15-phase cycle (too slow for the hot path).

## Critical DB Gotcha
`aiem_paper_trades.status` uses UPPERCASE: `'OPEN'`, `'CLOSED_AIEM'`, `'CLOSED_MANUAL'`, `'CANCELLED'`.
All D3 queries must use these exact strings (not lowercase 'open'/'closed').

## Enforcement scoping — D3 is advisory-only, structurally (confirmed 2026-07-10)
`d3_governance_actions.status` has a DB `CHECK` constraint that physically blocks
the literal value `'ENFORCED'` (proved via a rejected raw `UPDATE`). No code path
anywhere lets D3 block/veto/alter a D2 decision — this is not a bug to fix casually,
it reflects that D2 never calls into D3 and checks a return value before proceeding.
`NOT_ENFORCED` is therefore the correct, permanent status for architecture-drift and
rejected-learning-proposal actions. `ADVISORY_ACKNOWLEDGED` IS a real reachable
status (confirmed via a Phase 6 negative-control test) — it means D3 independently
re-checked the target row's real DB state and found it self-consistent with the
decision, NOT a cross-service ack (no separate D2 "owner" service exists — this is
one Python monolith).

## Tamper-evidence: ledger is trigger-protected, actions table is NOT (gap)
`d3_governance_event_links` has a DB trigger blocking `UPDATE`/`DELETE` on chained
rows (proved live). `d3_governance_actions` (newer, T-F) only has the CHECK-constraint
protection on the `'ENFORCED'` value — rows can otherwise still be updated after
creation. Flagged as a real, undone hardening gap, not fixed this session.

## Test-record disclosure gotcha
`is_test_record` is opt-in per-call, not auto-inferred from context. Production code
paths (phase6, phase9, the trade-close hook) triggered by a deliberately-inserted
upstream test row do NOT automatically propagate `is_test_record=true` into the
resulting `d3_governance_event_links`/`d3_governance_actions` rows unless the caller
explicitly passes it. Always trace via `target_id`/`root_trace_id`, don't trust the
flag alone to find every test-originated row.

## Known Gap: Per-Trace Linkage — PARTIALLY CLOSED (2026-07-10, was "unfixed" before)
Originally: zero D3 columns joined a governance check to an individual `trace_id`/
trade. Now: `d3_governance_event_links` has `root_trace_id`/`diagram3_trace_id`/
`paper_trade_id` columns and IS populated — but only for the one wired event (trade
close). All other D2 stages/events still have zero D3 linkage. Don't claim full
per-trace governance coverage — it's exactly one event type, verified live via
`aiem_diagram3_verification.py timeline --trace-id <id>`.

## Path B — open question, unresolved
The newer "D2<->D3 integration" spec (758 lines) truncates mid-Section-10
immediately after introducing "PATH A" (the trade-close hook, now verified live).
"PATH B" is never defined anywhere in the spec text seen by any session so far.
Multiple `user_query` attempts to ask the user directly failed with a tool-level
"prompt already pending" error. If this comes up again, ask early and don't guess.

## How to Apply
- Add governance metadata for any new module: call `log_change(module, reason, expected_impact)`
- Force re-baseline after architectural changes: `POST /d3/freeze-baseline {"force": true}`
- Executive summary on demand: `POST /d3/generate-report`
- Phase 6 learning approval: APPROVE requires new_score >= current_score AND n>=100 samples
- To request a governance decision and track its (advisory) status: `request_governance_action()` then `check_action_status()` — never assume a status of "ENFORCED" is possible, the DB will reject it
