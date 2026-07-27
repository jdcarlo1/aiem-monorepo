# OPP-040 Endpoint + Dashboard Panel — FINAL

**Date:** 2026-07-27  
**Directive:** attached_assets/Pasted--Directive-OPP-040-Endpoint-Dashboard-Panel-2026-07-27-_1785190509445.txt  
**Commit verified against:** c0cbbfd (HEAD → main at time of build)

---

## Endpoint Specification

**Route:** `GET /stock-api/options-pipeline/candidates`  
**Function:** `opp_040_candidates()`  
**Auth:** `X-Admin-Token` header — same `_admin_ok()` guard used on all admin routes  
**Registration line:** 12332 (dead zone starts ~29315 — 3000-line safe margin)

### Real column name mappings (spec → actual DB)

| Spec name | Actual column | Table |
|---|---|---|
| `scan_date` | `alert_date` | `aiem_options_alerts` |
| `req6_score` | `selected_score` | `aiem_options_alerts` |
| `status` | `outcome_status` | `aiem_options_alerts` |
| `trace_id` | *(no such column)* | *(join key is `audit_chain_sha256`)* |

### Query parameters

| Param | Type | Default | Max | Source |
|---|---|---|---|---|
| `alert_date` | YYYY-MM-DD | today ET (`datetime.now(_ET_TZ).date()`) | — | live clock |
| `direction` | CALL\|PUT | *(none — all)* | — | substring ILIKE match |
| `min_score` | float | 0.0 | — | no lower bound unless specified |
| `status` | string | *(none — all)* | — | ILIKE on `outcome_status` |
| `limit` | int | 100 | 500 | OPP-040 spec §limit |
| `offset` | int | 0 | — | pagination convention |

### Numeric constants — source trace

| Constant | Value | Source |
|---|---|---|
| limit default | 100 | OPP spec: `opp_040_050_spec.md` line 25 `default 100` |
| limit max | 500 | OPP spec: `opp_040_050_spec.md` line 25 `max 500` |
| connect_timeout | 5 | Crash forensics pattern (`crash_forensics_lifecycle.py:93,155`) |
| min_score default | 0.0 | No lower bound — return all unless caller filters |
| offset default | 0 | Standard pagination convention |

### Join logic

```sql
FROM aiem_options_alerts a
LEFT JOIN oe_decision_audit d
  ON d.decision_id = a.audit_chain_sha256
 AND d.is_test_record = FALSE
WHERE a.alert_date = %(alert_date)s
  AND a.selected_score >= %(min_score)s
  [AND a.direction ILIKE %(dir_pat)s]
  [AND a.outcome_status ILIKE %(st_pat)s]
ORDER BY a.created_at DESC
LIMIT %(lim)s OFFSET %(off)s
```

**Join result as of 2026-07-27:** 0 matches (`oe_decision_audit.decision_id` values are 24-char hex; `audit_chain_sha256` values are 64-char SHA256 — different schemes). LEFT JOIN returns all alerts with NULL audit fields. When the pipeline is updated to write a matching key, audit fields will populate automatically with no endpoint change needed.

---

## Dashboard Integration

**File:** `artifacts/aiem-dashboard/src/pages/Opportunities.tsx`  
**Change:** Added third panel "OPTIONS PIPELINE CANDIDATES" below the 2-column grid.

- Polls `/stock-api/options-pipeline/candidates` every 60s via existing `useApi` hook
- `useApi` automatically sends `X-Admin-Token` header from `getToken()` (line 31 of `use-api.ts`)
- Displays: TICKER, DIRECTION (green=CALL, red=PUT), SCORE, STATUS, STRIKE, EXPIRY, AUDIT
- AUDIT column: shows `verification_status` if `decision_id` non-null, else "NO AUDIT"
- Manual REFRESH button
- "N TOTAL · YYYY-MM-DD" in panel header

---

## Evidence

### File SHA256 before/after

| File | Before | After |
|---|---|---|
| `main.py` | `aa2b296a353a8d39ac3ac10c06ddb82fb34d975b5ff8c179beaf5cb612099014` | `f9d5da68b5e0b63eca8821002042360e18b5ab1a18c230c782c08ac58d6872d5` |
| `Opportunities.tsx` | `bb29b450d40b5fe4cb04152ef9d2de1139e8f1ab0b761e5adef52e3fc5d65e16` | `9d031e8dea5785f081f053e02ee7bf655573eb29b6421d800187b0633b6986b8` |

### verified_run.sh chain entries

```
SEQ=151  exit_code=127  (quoting error — command not invoked; logged but not evidence run)
SEQ=152  exit_code=0    PASS=8 FAIL=0
  entry_hash: cb9c06ec267b25694dec9549acc10903279d7d97681c8aaf32b607bbbdf07729
  output_sha256: ab30ba16d4a6f489c33b596dace38da02d0460e440e89ca3cea748e048c0d7f0
```

**verify_chain.sh (tools/ — hash 4804b547):** CHAIN VALID, 2 entries, no tampering detected.

### Live HTTP test results (SEQ=152 raw output)

```
route registration:
  12332:@app.route("/stock-api/options-pipeline/candidates", methods=["GET"])
  ROUTE_LINE=12332 < 29315 PASS

live GET 2026-07-17 (total=5):
  TER LONG_PUT 65.4 OPEN
  WOLF LONG_PUT 66.4 OPEN
  PINS LONG_PUT 70.3 OPEN
  UMC LONG_PUT 70.1 OPEN
  MEC LONG_PUT 65.7 OPEN

direction filter PUT: total=5 PASS
min_score=70 filter: total=2 PASS
401 no token: error=unauthorized PASS
400 bad date: error contains "invalid" PASS
limit cap 9999→500: limit=500 PASS
LEFT JOIN null coverage: decision_id null=5/5 (no join key yet) PASS

SUMMARY: PASS=8 FAIL=0
```

### SQL verification

```sql
-- aiem_options_alerts rows
SELECT COUNT(*) FROM aiem_options_alerts;          -- 25
SELECT COUNT(*) FROM aiem_options_alerts WHERE alert_date = '2026-07-17';  -- 5

-- oe_decision_audit prod rows
SELECT COUNT(*) FROM oe_decision_audit WHERE is_test_record = FALSE;  -- 15

-- join key overlap (expected 0 — different hash schemes)
SELECT COUNT(*) FROM aiem_options_alerts a
JOIN oe_decision_audit d ON d.decision_id = a.audit_chain_sha256
AND d.is_test_record = FALSE;  -- 0
```

### git diff --stat

```
artifacts/aiem-dashboard/src/pages/Opportunities.tsx  |  72 ++++++++++++-
artifacts/stock-scanner-api/main.py                   | 110 +++++++++++++++++++
2 files changed, 179 insertions(+), 3 deletions(-)
```

---

## Verifier canonical hashes (confirmed unchanged)

```
dce94f6e19dfc5c7952ab9eee7015b7eb10c3ff1e0ca60263279658ab166f826  tools/verified_run.sh
ca7896c7c832ef53430dfd07319418000d9139566c9e52720f587aa9c9840d1f  artifacts/stock-scanner-api/verify_chain.sh
```
