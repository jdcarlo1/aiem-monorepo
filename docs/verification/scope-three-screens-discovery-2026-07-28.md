# Scope: Three New Screens — Discovery Record
# Directive: Pasted-DIRECTIVE-Scope-Three-New-Screens-Specialist-Council-Au_1784948867629.txt (2026-07-24)
# Produced: 2026-07-28T00:00Z UTC / 2026-07-27 20:00 ET
# Status: PRE-BUILD — checklists only, no code written

---

## 1. SPECIALIST COUNCIL

### 1a. Raw Discovery — What Exists

**Table:** `aiem_specialist_council_runs`
```sql
SELECT column_name, data_type FROM information_schema.columns
WHERE table_name='aiem_specialist_council_runs' ORDER BY ordinal_position;
```
Schema (from `specialist_council.py:180`):
```
id                 BIGSERIAL PRIMARY KEY
run_time           TIMESTAMPTZ NOT NULL DEFAULT NOW()
context            TEXT NOT NULL       -- comma/array of specialist names invoked
ticker             TEXT NOT NULL
trace_id           TEXT                -- written back from trade execution (main.py:19764)
registered_members JSONB NOT NULL
invoked_members    JSONB NOT NULL
abstained_members  JSONB NOT NULL
abstentions        JSONB NOT NULL
opinions           JSONB NOT NULL      -- list of {specialist_name, vote, confidence, reasoning, category}
weighted_vote      DOUBLE PRECISION
variance           DOUBLE PRECISION
weights            JSONB
```

Row count:
```sql
SELECT COUNT(*) FROM aiem_specialist_council_runs;
-- 293
```

Sample opinions field (2026-07-27T20:01:23Z UTC / 2026-07-27 16:01 ET, ticker=SLN):
```json
[
  {"vote": 0.0, "category": "momentum", "reasoning": "RSI 61.42, CMF 0.1137, overall buy",
   "confidence": 0.7, "specialist_name": "technicals"},
  {"vote": 0.0, "category": "macro", "reasoning": "FRED yield curve + credit spread macro state",
   "confidence": 0.65, "specialist_name": "fred_macro"}
]
```

**Trace linkage:** `main.py:19764` — after paper trade execution, `aiem_specialist_council_runs.trace_id` is UPDATEd with the D2 trace_id:
```python
"UPDATE aiem_specialist_council_runs "
"SET trace_id = %s WHERE id = %s",
(_d2_trace_id, _d2_council_run_id),
```
This means a council run row CAN be joined to a paper trade via `trace_id`.

**Known schema anomaly:** `information_schema.columns` and `pg_attribute` both return 26 entries (13 columns × 2 — inheritance artifact). Actual column definitions are authoritative from `specialist_council.py:180` above. The `id` column is BIGSERIAL (integer), not timestamptz — psycopg2 cursor mapping issue when used without named columns in SELECT *.

**Specialists currently firing:** `technicals`, `fred_macro` (confirmed from 293 live rows). Additional specialists wired but may abstain: `signal_engine`, `bull_bear`, `social_sentiment`, `volatility_clustering`, `macro_cross_asset`.

**Gap:** No direct join from `aiem_specialist_council_runs` to `aiem_paper_trades` — only via `trace_id` → `oe_trade_records`. Pre-pick council runs (fires before trace_id exists) have trace_id=NULL until writeback.

### 1b. Checklist — SC-001 to SC-010

```
SC-001  Route /aiem/specialist-council registered in App.tsx and Sidebar.tsx
        PASS: route renders without 500; page loads with real data from DB

SC-002  Council run table rendered live from DB
        PASS: table shows rows from aiem_specialist_council_runs ORDER BY run_time DESC;
              no in-memory-only fallback; shows last updated timestamp

SC-003  Per-run detail view: opinions breakdown
        PASS: clicking a row expands per-specialist vote/confidence/reasoning/category
              sourced from opinions JSONB; no placeholder text

SC-004  Weighted vote and variance displayed per run
        PASS: weighted_vote and variance columns shown; NULL displayed as "—", not 0

SC-005  Trade link: trace_id → paper trade
        PASS: rows where trace_id IS NOT NULL show a linked trade indicator;
              clicking navigates to that trade's detail (or shows trade_id inline)

SC-006  Filter by ticker
        PASS: ticker filter input reduces table rows client-side or via ?ticker= param;
              empty filter shows all rows

SC-007  Abstention tracking
        PASS: abstained_members JSONB rendered per row; "0 abstentions" shown accurately,
              not hidden when empty

SC-008  No fabricated data
        PASS: grep Specialist.tsx for any hardcoded vote/confidence/specialist_name string
              returns zero matches

SC-009  Stale indicator when DB query returns 0 rows
        PASS: if COUNT=0, screen shows "No council runs found" — not a spinner or blank white

SC-010  Admin-token gated backend endpoint
        PASS: curl without ADMIN_TOKEN header → 401; with → 200 + real data
```

---

## 2. AUDIT/COMPLIANCE SCREEN

### 2a. Raw Discovery — What Exists

**Already built.** `docs/verification/audit-compliance-screen-FINAL.md` sealed 2026-07-25T17:13:35Z.

Inventory of verification artifacts (live `ls docs/verification/` at 2026-07-28T00:00Z UTC):
```
56 files
Most recent: greeks-wiring-formula-verification-FINAL.md (2026-07-28)
             item1-live-greeks-population-status-2026-07-28.md
             item2-e2e-trading-logic-status-2026-07-28.md
             item3-governance-trust-incidents-2026-07-28.md
```

Existing screen routes:
```
GET  /stock-api/admin/audit/chain-status    — live chain stats + SHA cross-checks
GET  /stock-api/admin/audit/docs            — live ls of docs/verification/
POST /stock-api/admin/audit/run-script      — whitelisted script executor (5-min cache)
GET  /stock-api/admin/audit/run-log         — parsed verified_run_index.tsv
GET  /stock-api/admin/audit/run-log-detail  — individual verified_run_N.log content
GET  /stock-api/admin/audit/doc-content     — sealed document raw text
```

Frontend: `artifacts/aiem-dashboard/src/pages/Audit.tsx` (SHA-256: `5db3e241...`)
Sidebar: `/audit` entry with `ClipboardCheck` icon.

**oe_decision_audit:**
```sql
SELECT COUNT(*), is_test_record FROM oe_decision_audit GROUP BY is_test_record;
-- TRUE: 346, FALSE: 15
SELECT COUNT(*) FROM oe_decision_audit WHERE identity_json IS NOT NULL;
-- 8 (all from test executions with AAPL/TEST data, created 2026-07-19)
```
Production non-test rows (15): identity_json=NULL on all — detail JSON columns were
not wired for production writes at time of last non-test run (2026-07-19).

**Conclusion:** Audit/Compliance screen is built and verified (AC-001 through AC-012 all PASS
per sealed FINAL doc). No scoping work needed. Checklist already exists and is closed.

**Reference:** `docs/verification/audit-compliance-screen-FINAL.md`

---

## 3. LEARNING CENTER + RESEARCH & HYPOTHESES

### 3a. Raw Discovery — What Exists

```sql
-- All learning/research/hypothesis tables
SELECT table_name FROM information_schema.tables
WHERE table_schema='public'
AND (table_name LIKE '%learning%' OR table_name LIKE '%hypothesis%'
     OR table_name LIKE '%research%' OR table_name LIKE '%insight%')
ORDER BY table_name;
```
```
aiem_learning_proposals
aiem_research_audit_sessions
aiem_research_hypotheses
aiem_research_insights
aiem_research_tool_audit
aiem_supervisor_bad_learning_flags
aiem_supervisor_learning_review
d3_learning_approvals
hypothesis_counter
hypothesis_registry
oe_interaction_hypotheses
```

Row counts:
```sql
SELECT 'aiem_research_hypotheses'     AS tbl, COUNT(*) FROM aiem_research_hypotheses
UNION ALL
SELECT 'hypothesis_registry',                  COUNT(*) FROM hypothesis_registry
UNION ALL
SELECT 'aiem_learning_proposals',              COUNT(*) FROM aiem_learning_proposals
UNION ALL
SELECT 'd3_learning_approvals',                COUNT(*) FROM d3_learning_approvals
UNION ALL
SELECT 'aiem_supervisor_learning_review',      COUNT(*) FROM aiem_supervisor_learning_review
UNION ALL
SELECT 'aiem_signal_discoveries',              COUNT(*) FROM aiem_signal_discoveries
UNION ALL
SELECT 'aiem_research_audit_sessions',         COUNT(*) FROM aiem_research_audit_sessions
UNION ALL
SELECT 'aiem_supervisor_bad_learning_flags',   COUNT(*) FROM aiem_supervisor_bad_learning_flags;
```
```
aiem_research_hypotheses          20
hypothesis_registry                0   ← no completed/locked results
aiem_learning_proposals            3   ← all auto-skipped (insufficient graded data: 0 rows, need >=30)
d3_learning_approvals              0
aiem_supervisor_learning_review   22
aiem_signal_discoveries            5
aiem_research_audit_sessions     141
aiem_supervisor_bad_learning_flags 0
```

Sample `aiem_research_hypotheses` (most recent, 2026-07-27):
```
id=20: "The existing validated gap+RVOL discovery ... should act as a confirming prior"
id=19: "Among qualified premarket gap candidates, high squeeze_subscore setups..."
id=18: "Among premarket gap candidates passing the ML threshold, names without
        institutional divergence will have higher practical watchlist quality"
```

Sample `aiem_learning_proposals` (all 3 rows):
```
proposed_at=2026-07-27T00:30Z, n_samples=0, accepted=False,
reason='insufficient graded data: 0 rows (need >=30)', version_saved='auto-skipped'
-- same pattern for 2026-07-20 and 2026-07-13
```

Sample `aiem_supervisor_learning_review` (2026-07-27):
```
ticker=unusual_calls, review_verdict=FLAGGED, reason='small_sample(n=8<10)',
recommended_action='REQUIRE_MORE_DATA'

ticker=gap_volume, review_verdict=FLAGGED, reason='signal_below_retirement_WR(25.0%)',
recommended_action='ALLOW_UPDATE'
```

`aiem_signal_discoveries` (all 5 rows):
```
id=1: Pullback_ReEntry_MomentumIntact  status=hypothesis  WR=0.647  p=0.000
id=2: Momentum_Exhaustion_MultiSignal  status=hypothesis  WR=0.436  p=0.9999
id=3: Oversold_Bounce_Uptrend          status=hypothesis  WR=0.750  p=0.309
id=4: Short_Squeeze_Reversion          status=hypothesis  WR=NULL   p=NULL
id=5: gap_volume_signal_name_proof     status=validated   WR=0.586  p=0.002  oos_edge=2.5
```

### 3b. Minimum Viable Data Model (for gaps)

`hypothesis_registry` — 0 rows. This table exists but is never written to. The schema has
`locked`, `result`, `result_recorded_at` for finalizing a hypothesis outcome. No code writes
to it. A screen cannot show completed hypothesis lifecycle without this table being populated.

`d3_learning_approvals` — 0 rows. D3 approval workflow for model updates is defined but no
approvals have ever been issued (all proposals are auto-skipped due to n_samples<30).

### 3c. Checklist — LC-001 to LC-012

```
LC-001  Route /aiem/learning registered in App.tsx and Sidebar.tsx
        PASS: route renders without 500; page loads with real data

LC-002  Signal discoveries panel: live query on aiem_signal_discoveries
        PASS: all 5 rows shown; status column (hypothesis/validated/retired) color-coded;
              WR/p-value/oos_edge displayed; NULLs shown as "—"

LC-003  Active hypotheses panel: live query on aiem_research_hypotheses
        PASS: rows shown ORDER BY registered_at DESC; hypothesis_text full text displayed;
              no truncation without expand option

LC-004  Learning proposals history: live query on aiem_learning_proposals
        PASS: rows shown with proposed_at, n_samples, accepted, reason;
              "auto-skipped" reason shown honestly, not hidden

LC-005  Supervisor learning review: live query on aiem_supervisor_learning_review
        PASS: shows per-signal review_verdict, old/new trust_score, recommended_action;
              FLAGGED verdict shown in amber/red, not suppressed

LC-006  Research session audit: live query on aiem_research_audit_sessions
        PASS: 141 rows surfaced; shows session_type, started_at, ended_at, verdict,
              discovery_saved (bool), strict_pass (bool)

LC-007  Hypothesis registry shown honestly when empty
        PASS: hypothesis_registry has 0 rows; screen shows "No completed hypothesis
              results locked" — not a blank panel or spinner

LC-008  D3 learning approvals shown honestly when empty
        PASS: d3_learning_approvals has 0 rows; screen shows "No model promotions approved"

LC-009  No fabricated completion status
        PASS: grep Learning.tsx for any hardcoded 'VALIDATED'/'APPROVED'/'PROMOTED' string
              returns zero results — all values come from DB queries

LC-010  Stale data indicator
        PASS: each panel shows "last fetched [timestamp]"; if endpoint returns error,
              panel shows error state not blank or stale success

LC-011  Admin-token gated backend endpoint(s)
        PASS: all /stock-api/admin/learning/* endpoints → 401 without ADMIN_TOKEN

LC-012  aiem_supervisor_bad_learning_flags shown honestly when empty
        PASS: 0 rows → "No bad learning flags recorded" — not hidden
```

---

## CLOSE-OUT STATUS

| Screen | Discovery Complete | Checklist Written | Built |
|--------|-------------------|-------------------|-------|
| Specialist Council | YES | SC-001 to SC-010 | NO |
| Audit/Compliance | N/A (already built + FINAL sealed 2026-07-25) | AC-001 to AC-012 (existing) | YES |
| Learning Center | YES | LC-001 to LC-012 | NO |

Directive closes when SC and LC checklists above are accepted and building is directed.
Audit/Compliance is already closed per existing FINAL doc.
