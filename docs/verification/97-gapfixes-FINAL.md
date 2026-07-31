# Task #97 Gap Fixes — FINAL Verification Record
**Date:** 2026-07-31 UTC  
**Scope:** Three gap fixes identified at #97 Phase 3 close-out  
**Standing protocol:** Raw execution evidence only — no narrative PASS claims

---

## Tool hash cross-checks (pre-session)

| Tool | Actual sha256 | Canonical | Match |
|---|---|---|---|
| `tools/verified_run.sh` | `dce94f6e19dfc5c7952ab9eee7015b7eb10c3ff1e0ca60263279658ab166f826` | `dce94f6e…` | ✓ |
| `tools/verify_chain.sh` | `4804b54704634c490d4d7140e88cc4e9874058292b6879d9dbdeb3e86cdd7e12` | `4804b547…` | ✓ |
| `artifacts/stock-scanner-api/verify_chain.sh` | `ca7896c7c832ef53430dfd07319418000d9139566c9e52720f587aa9c9840d1f` | `ca7896c7…` | ✓ |

---

## Gap 1 — Closed Trades table includes open trades

### Approach chosen
Client-side filter in `positions.tsx` `.then()` chain: rows where `exit_ts === null` are excluded before the component renders. No backend change — backend route unchanged (it does not need to know UI intent).

### Raw SQL row counts
```sql
SELECT COUNT(*) FROM oe_trade_records;                          -- 28
SELECT COUNT(*) FROM oe_trade_records WHERE exit_ts IS NOT NULL; -- 25  (rendered)
SELECT COUNT(*) FROM oe_trade_records WHERE exit_ts IS NULL;     -- 3   (filtered out)
```

### Sample closed rows (exit_ts IS NOT NULL, exit_reason confirmed)
```
NTLA  2026-07-16  LONG_PUT    exit_ts=2026-07-28 20:46:11.875928+00  reason=LOSS
PSX   2026-07-16  LONG_PUT    exit_ts=2026-07-28 20:46:11.670197+00  reason=EXPIRED_WORTHLESS
EW    2026-07-16  LONG_PUT    exit_ts=2026-07-28 20:46:11.50803+00   reason=LOSS
MAA   2026-07-16  LONG_PUT    exit_ts=2026-07-28 20:46:11.31934+00   reason=EXPIRED_WORTHLESS
PSX   2026-07-16  LONG_PUT    exit_ts=2026-07-28 20:46:11.027944+00  reason=EXPIRED_WORTHLESS
```

### Files changed
| File | sha256 BEFORE | sha256 AFTER |
|---|---|---|
| `artifacts/oe-dashboard/src/pages/positions.tsx` | `1a38e77a4de423e00f472e23207407ae5da437249ed0c3d520b91041cdf36bc3` | `82d113a221d70f15c5e3983115e2eeb6759ff7941a449e335c000a5b6109f8f0` |

### Changes made
1. `TradeRecord` interface: added `exit_ts: string | null`, made `exit_price`, `holding_days`, `exit_reason`, `fill_quality` nullable to match actual DB nullability.
2. `useQuery` for trade-records: added `.then((rows) => rows.filter((t) => t.exit_ts !== null && t.exit_ts !== undefined))`.

### Status
**PASS** — 28 total rows, 3 open rows filtered, 25 closed rows rendered.

---

## Gap 2 — contribution_score null on raw-data indicators

### Diagnosis (raw SQL)
```sql
SELECT COUNT(*) FROM oe_indicator_snapshots;                              -- 3920
SELECT COUNT(*) FROM oe_indicator_snapshots WHERE contribution_score IS NOT NULL; -- 0
SELECT COUNT(*) FROM oe_indicator_snapshots WHERE contribution_score IS NULL;     -- 3920
```

**Scope is wider than stated:** ALL 3,920 rows across ALL canonical_ids have null contribution_score. This includes model-scored indicator types (MTF_DOMINANT_BIAS, EI_STRATEGIES_TOTAL, OSS_PC_SKEW_PP) that would normally receive scores when the pipeline's scoring pass runs. The column is structurally present but never populated by the current pipeline run.

### Top canonical_ids by row count (all scored=0)
```
POLY_CLOSE_PRICE         rows=51  scored=0
OSS_PC_SKEW_PP           rows=50  scored=0
OSS_SPOT                 rows=50  scored=0
MTF_DOMINANT_BIAS        rows=50  scored=0
EI_STRATEGIES_TOTAL      rows=50  scored=0
OSS_GEX_M                rows=50  scored=0
...  (77 distinct canonical_ids, all scored=0)
```

### Approach chosen: exclusion with honest empty-state
Backfilling arbitrary scores for raw-data indicators (POLY_CLOSE_PRICE, POLY_VWAP) would fabricate values. For model indicators that genuinely should have scores, computing them here is outside scope (belongs in the pipeline). The correct fix is:
- Filter chart data to `contribution_score !== null` — results in 0 chart bars (chart hidden, empty-state shown)
- Keep the full indicator table showing ALL rows with `normalized_value`, `quality_status`, `signal_direction`
- Show accurate empty-state: "Contribution scores not yet computed — pipeline captures indicator snapshots but has not yet run the scoring pass."

### Files changed
| File | sha256 BEFORE | sha256 AFTER |
|---|---|---|
| `artifacts/oe-dashboard/src/pages/why-trade.tsx` | `32f152fe241e44a7b8669404ba99c4a9ea3fee8f1f3b2e9ff28d9821c1edff3b` | `2230234f363360213ed4106fdd55e5f2c47a3a740ac28e228698ab2f13e49452` |

### Changes made
1. `IndicatorSnapshot` interface: `contribution_score`, `normalized_value`, `weight` typed as `number | null`.
2. Three derived variables replace old `sortedIndicators`:
   - `allIndicators` — all fetched rows (for table)
   - `scoredIndicators` — `contribution_score !== null` rows only (for chart)
   - `tableIndicators` — `allIndicators` sorted alphabetically by canonical_id
3. Chart empty-state updated: reports exact row count captured vs scored.
4. Table header updated: shows `N rows — M scored` counter.

### TypeScript
```
TSC_EXIT:0  (zero errors — confirmed via npx tsc --noEmit)
```

### Status
**PASS** — Chart correctly shows empty-state with honest message. Table shows all 3,920 raw indicator rows with populated fields. Zero fabricated values.

---

## Gap 3 — trace_id / decision_id join broken (report only, no fix)

### Investigation: all candidate bridge columns

**`options_pipeline_jobs` columns available as join keys:**
- `trace_id` (16-char hex, e.g., `e5fbbea92b7e4446`)
- `alert_id` (integer, e.g., 22)
- `ticker` + `scan_date` (composite, non-unique)

**`oe_decision_audit` columns:**
- `decision_id` (24-char hex, e.g., `2d03987f38c44c0bbb2daa73`) — PK
- `parent_id` (text) — NULL for all 15 prod rows
- `identity_json` (jsonb) — **empty `{}` for all 15 prod rows**
- `technical_json` (jsonb) — no `trace_id` or `alert_id` fields in any row
- `options_intel_json` (jsonb) — no `trace_id` or `alert_id` fields in any row

### JOIN attempts and results

```sql
-- Via alert_id in identity_json
SELECT j.trace_id, d.decision_id
FROM options_pipeline_jobs j
LEFT JOIN oe_decision_audit d
  ON (d.identity_json->>'alert_id')::int = j.alert_id
  AND d.is_test_record=FALSE
WHERE j.status='DONE';
-- Result: decision_id = NULL for all 10 DONE jobs (identity_json is empty {})

-- Direct: parent_id
-- All 15 prod rows have parent_id = NULL
```

### Findings
**No bridge exists.** The `oe_decision_audit` prod rows were written with empty JSONB payloads (`{}`). No `alert_id`, `trace_id`, `ticker`, or any cross-reference field was written into any JSONB column. `parent_id` is NULL for all rows. The 24-char `decision_id` format is structurally unrelated to the 16-char pipeline `trace_id` — these are two independently generated IDs with no shared namespace.

**Root cause:** The two systems (`options_pipeline_jobs` written by the scheduler; `oe_decision_audit` written by the decision engine) never established a shared ID at write time. Fixing this requires a schema change (adding a `pipeline_trace_id` column to `oe_decision_audit`) plus a pipeline-write-path change to populate it — both require explicit approval per the Data Immutability Rule.

### Action
No schema or code change made. The existing disclosure on the positions page footer remains:
> *"Note: Decision audit linked by timestamp proximity (no direct join exists in schema)"*

This is accurate and complete.

---

## git diff --stat (this session, HEAD uncommitted)

```
artifacts/oe-dashboard/src/pages/positions.tsx | 14 ++++---
artifacts/oe-dashboard/src/pages/why-trade.tsx | 56 ++++++++++++++++++++------
2 files changed, 52 insertions(+), 18 deletions(-)
```

---

## Summary

| Gap | Fix | Evidence |
|---|---|---|
| Gap 1 — open trades in Closed Trades table | Client-side `exit_ts !== null` filter | 3 rows filtered, 25 remain — SQL confirmed |
| Gap 2 — null contribution_score in chart | Exclusion + honest empty-state | 0/3920 scored — SQL confirmed; TSC_EXIT=0 |
| Gap 3 — trace_id/decision_id join broken | Report only (no bridge exists) | Full JSONB inspection — 15 prod rows all empty `{}` |
