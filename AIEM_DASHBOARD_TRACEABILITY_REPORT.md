# AIEM DASHBOARD — PHASE A
## Traceability Report
**Generated:** 2026-07-21 | **Scope:** End-to-end trace from signal fire to audit record

---

## Overview

AIEM has a layered traceability system using multiple complementary identifiers. No single identifier follows a trade from signal detection through execution through learning loop — each layer mints its own ID, and cross-references are maintained via foreign keys in linking tables.

---

## Identifier Registry

| Identifier | Minted In | Used In | Description |
|------------|----------|---------|-------------|
| `trace_id` | aiem_options_scheduler.py:655 (per job) | options_pipeline_jobs, oe_indicator_snapshots, aiem_pipeline_audit_log | Options pipeline job-level trace |
| `_d2_trace_id` | main.py:17629 (early) / main.py:18114 (final) | aiem_diagram2_trace_audit, aiem_specialist_council_runs, aiem_paper_trades | Paper trade full-pipeline trace |
| `_d2_trace_id_early` | main.py:17629 | specialist_council.py:persist_council_run | Pre-execution council trace (UPDATE'd after mint) |
| `decision_id` | aiem_options_dpl.py | oe_decision_audit (PK), oe_gate_events | Options decision-level UUID |
| `execution_plan_id` | aiem_diagram3_governance.py:1218 | d3_governance_event_links | Links governance decision to paper trade |
| `governance_decision_id` | aiem_diagram3_governance.py | d3_governance_decisions (PK), d3_governance_event_links | D3 governance chain ID |
| `audit_trace_id` | main.py:17618/17823 | aiem_supervisor_loop_audit, aiem_pipeline_audit_log | Supervisor loop audit reference |
| `job_id` | aiem_options_scheduler.py | options_pipeline_jobs (FK), oe_decision_audit | Scheduler job execution ID |
| `council_run_id` | specialist_council.py:253 | aiem_specialist_council_runs, aiem_diagram2_trace_audit | Council session UUID |
| `gate_event_id` | aiem_options_phase5.py | oe_gate_events (PK) | Immutable gate event record |
| `chain_seq` (SEQ) | tools/verified_run.sh (flock) | evidence_chain.log | Engine integrity chain sequence number |

---

## Trace Chains

### Chain A: Paper Trading (AIEM Diagram 2)

```
Signal detected
    │
    ▼
_aiem_paper_pick_candidates() [main.py:45562]
    │
    ▼ generates
_d2_trace_id_early (UUID, main.py:17629)
    │
    ├──► specialist_council.persist_council_run() → aiem_specialist_council_runs.trace_id
    │
    ▼
run_risk_gate() [pre_decision_risk_gate.py:173]
    │ (if PASS)
    ▼
compute_position_size() [aiem_position_sizing.py:538]
    │
    ▼
_aiem_paper_execute_today() [main.py:16910]
    │ mints final _d2_trace_id [main.py:18114]
    │ UPDATEs council run row with final trace_id [main.py:18133]
    │
    ├──► aiem_paper_trades.d2_trace_id ← _d2_trace_id stored
    ├──► aiem_position_sizing_log.logged_at
    ├──► paper_trade_job_ledger.status = COMPLETED
    ├──► D3 governance G0 pre-check → d3_governance_decisions
    │
    ▼
Intraday MTM (4PM ET)
    │
    ▼
Learning loop stages 1-23 [aiem_closed_loop_learning.py:550]
    │
    ▼
aiem_supervisor_loop_audit [audit_trace_id = _d2_trace_id]
```

**Traceability gap:** `_d2_trace_id` IS stored in `aiem_paper_trades` but there is no API endpoint to query a trade by trace_id. You can do it via SQL only.

**Dashboard linkage:** 
- Trade row → council run: JOIN aiem_paper_trades t JOIN aiem_specialist_council_runs c ON c.trace_id = t.d2_trace_id
- Trade row → supervisor audit: JOIN aiem_supervisor_loop_audit s ON s.trade_id = t.id

---

### Chain B: Options Pipeline (AIEM Diagram 1)

```
seed_daily_candidates() [09:40 ET, aiem_options_scheduler.py]
    │ produces candidates in options_pipeline_jobs (status=PENDING)
    │
    ▼
run_pipeline_worker() [09:45 ET, aiem_options_scheduler.py]
    │ iterates options_pipeline_jobs WHERE status=PENDING
    │
    ▼
_execute_job(job_id, ticker, scan_date, claim_id) [aiem_options_scheduler.py:655]
    │
    │ trace_id = f"exec_{_exec_id}" [main.py:17116] or claim_id
    │
    ├──► oe_indicator_snapshots.trace_id
    ├──► oe_decision_audit.decision_id (via aiem_options_dpl.py)
    ├──► oe_gate_events.trace_id (on gate fire)
    ├──► aiem_pipeline_audit_log.trace_id (all stages logged)
    │
    ▼
TRADE or NO_TRADE decision
    │ (if TRADE)
    ▼
D3 governance enforcement [aiem_diagram3_governance.py]
    │
    ├──► d3_governance_decisions.trace_id
    ├──► d3_governance_event_links.execution_plan_id
    ├──► d3_governance_event_links.paper_trade_id (if paper trade created)
    │
    ▼
Hash chain integrity [tools/verified_run.sh]
    │ SEQ increments, SHA-256 chain
    └──► evidence_chain.log
```

**Traceability strength:** OPTIONS pipeline has the strongest audit trail — every stage logged to aiem_pipeline_audit_log, immutable hash chain via oe_decision_audit, D3 governance links execution_plan_id to paper_trade_id.

---

### Chain C: Supervisor Loop

```
Paper trade opened
    │
    ▼
Supervisor monitor [aiem_supervisor.py]
    │ fires post-trade, post-pick, daily, weekly
    │
    ├──► aiem_supervisor_event_log [per event]
    ├──► aiem_supervisor_loop_audit [per trade]
    │         │ audit_trace_id = trade._d2_trace_id
    │         │ trade_id = aiem_paper_trades.id
    │         │ stage: ENTRY|INTRADAY|EXIT|LEARNING
    │
    └──► aiem_supervisor_risk_review [risk assessments]
```

---

### Chain D: D3 Governance (Hash Chain)

```
Any governance event
    │
    ▼
aiem_diagram3_governance.create_governance_event() [line:1218]
    │
    ├──► d3_governance_requests (request record)
    ├──► d3_governance_decisions (decision record)
    │         governance_decision_id = UUID
    │         checkpoint = G0|G1|G2|G3|G4|G5
    ├──► d3_governance_acks (acknowledgement)
    ├──► d3_change_log (change record)
    └──► d3_governance_event_links
              execution_plan_id ← links to options job
              paper_trade_id ← links to paper trade
              recommendation_id ← links to recommendation
              decision_id ← links to oe_decision_audit
```

**Immutability:** D3 governance tables support only INSERT (DELETE/UPDATE blocked by trigger on is_test_record=FALSE rows).

---

## Cross-Chain Join Patterns

### Find full audit trail for a paper trade (by trade ID)
```sql
-- Step 1: Get the trade and its trace
SELECT id, ticker, trade_date, d2_trace_id, signal_source
FROM aiem_paper_trades
WHERE id = :trade_id AND is_test_record = FALSE;

-- Step 2: Get council run
SELECT run_time, context, registered_members
FROM aiem_specialist_council_runs
WHERE trace_id = :d2_trace_id;

-- Step 3: Get supervisor audit
SELECT stage, decision, rationale, created_at
FROM aiem_supervisor_loop_audit
WHERE trade_id = :trade_id;

-- Step 4: Get governance decisions
SELECT checkpoint, decision, reasoning, created_at
FROM d3_governance_decisions
WHERE trace_id = :d2_trace_id;
```

### Find options pipeline audit by ticker and date
```sql
-- Step 1: Get job
SELECT id, trace_id, status, selected_score
FROM options_pipeline_jobs
WHERE ticker = :ticker AND scan_date = :date;

-- Step 2: Get all audit stages
SELECT stage_order, source_system, processing_system, logged_at, detail
FROM aiem_pipeline_audit_log
WHERE trace_id = :trace_id
ORDER BY logged_at;

-- Step 3: Get decision audit
SELECT verification_status, identity_json, technical_json, options_intel_json
FROM oe_decision_audit
WHERE decision_id = :trace_id  -- or join via trace_id
AND is_test_record = FALSE;

-- Step 4: Get gate events
SELECT gate_name, action_taken, fired_at
FROM oe_gate_events
WHERE trace_id = :trace_id
AND is_test_record = FALSE;
```

---

## Traceability Gaps

| Gap | Severity | Description |
|-----|----------|-------------|
| No single unified trace ID | MEDIUM | Paper trade trace (_d2_trace_id) ≠ options pipeline trace (job_id/decision_id) — no master trace linking all chains |
| No API endpoint for trace-by-trade | MEDIUM | `GET /stock-api/admin/aiem-pipeline-audit/<trace_id>` exists for options; no equivalent for paper trade _d2_trace_id |
| oe_decision_audit not queryable via dashboard route | HIGH | 341 rows, no GET endpoint — requires direct SQL |
| oe_gate_events not queryable via dashboard route | HIGH | 4 rows, no GET endpoint — requires direct SQL |
| aiem_position_sizing_log not exposed via API | MEDIUM | 207 rows, no GET endpoint |
| aiem_specialist_council_runs not exposed via API | MEDIUM | 219 rows, no GET endpoint |
| paper_trade_job_ledger not exposed via API | LOW | 5 rows, relevant for run history |
| Chain C → Chain B cross-link | LOW | Supervisor audit knows trade_id but not options job trace_id |
| Hash chain SEQ not in DB | MEDIUM | evidence_chain.log is a file, not a queryable table |

---

## Trust & Verification Properties

| Layer | Immutability | Audit Completeness | Dashboard Queryable |
|-------|-------------|-------------------|-------------------|
| oe_decision_audit | YES (trigger) | COMPLETE | NO (missing route) |
| d3_governance_decisions | YES (trigger) | COMPLETE | PARTIAL (via admin/module4-history) |
| evidence_chain.log | YES (flock+SHA-256) | COMPLETE | NO (file only) |
| aiem_pipeline_audit_log | NO (can be updated) | COMPLETE (284 rows) | YES (admin route) |
| aiem_verification_log | NO | PARTIAL | YES (signed-proof route) |
| paper_trade_job_ledger | NO | PARTIAL (5 rows) | NO |

**Critical finding:** The two most immutable tables (oe_decision_audit, d3_governance_decisions) are the least queryable from the dashboard. Phase B must expose these.
