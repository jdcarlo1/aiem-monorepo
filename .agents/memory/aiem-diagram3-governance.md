---
name: AIEM Diagram 3 Governance Layer
description: Pure supervisory governance layer over D1+D2; 15 phases, 15 d3_ tables, 18 admin endpoints; NEVER modifies D1/D2
---

# AIEM Diagram 3 — Autonomous Governance, Self-Optimization & Evolution Layer

**Why:** D3 is the executive governance layer above D1 (orchestration) and D2 (verification). Its purpose is monitoring, integrity enforcement, rollback protection, and optimization recommendations — never code changes.

**Core rule:** D3 reads from production tables only. It never writes to D1/D2 tables. No fabricated metrics.

## Key File
`artifacts/stock-scanner-api/aiem_diagram3_governance.py`

## 15 D3 Tables (all prefixed `d3_`)
d3_architecture_baseline, d3_system_health_snapshots, d3_performance_snapshots,
d3_strategy_registry, d3_model_governance, d3_learning_approvals, d3_change_log,
d3_version_history, d3_rollback_registry, d3_optimization_recommendations,
d3_system_forecasts, d3_security_reports, d3_architecture_status,
d3_executive_reports, d3_evolution_plan

## Baseline Hash (first production freeze)
`61a65ca7587d79fd1588b780cdac3be10649e1eca24ab15caf6b377f85f7edd8`
Frozen 2026-07-08 20:51:19 UTC. 195 modules, 220 tools, 21 D2 stages. Protected=True. Never overwrite.

## 18 Admin Endpoints
All at `/stock-api/admin/d3/` with `X-Admin-Token` auth:
- GET: status, baseline, discovery, health, performance, strategy-registry, model-registry,
  learning-approvals, change-log, version-history, rollback, optimization, forecast,
  security, architecture, executive-report, evolution-plan
- POST: freeze-baseline (get_json silent), generate-report, log-change (get_json silent)

## Wiring in main.py
1. `import aiem_diagram3_governance as _d3_gov` + `_DEFERRED_INITS.append(lambda: _d3_gov.d3_startup())` near line 51042
2. `_d3_gov.install_d3_routes(app)` after `_install_aiem_auditor_routes(app)` near line 61676

## Critical DB Gotcha
`aiem_paper_trades.status` uses UPPERCASE: `'OPEN'`, `'CLOSED_AIEM'`, `'CLOSED_MANUAL'`, `'CANCELLED'`.
All D3 queries must use these exact strings (not lowercase 'open'/'closed').

## Production Verified Outputs (2026-07-08)
- ARCHITECTURE_STATUS: INTACT (D1 ✓ D2 ✓ CommBus ✓ Learning ✓)
- FINAL_PASS: True | SYSTEM_READY_FOR_NEXT_CYCLE: True
- SECURITY_HEALTH: SECURE | ROLLBACK_READY: True
- GOVERNANCE_ACTIVE: True | VERSION_STATUS: 1.0.0

## How to Apply
- Add governance metadata for any new module: call `log_change(module, reason, expected_impact)` from the D3 module
- Force re-baseline after architectural changes: `POST /d3/freeze-baseline {"force": true}`
- Executive summary on demand: `POST /d3/generate-report`
- Phase 6 learning approval runs automatically — APPROVE requires new_score >= current_score AND n>=100 samples
