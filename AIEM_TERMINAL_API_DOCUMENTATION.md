# AIEM Institutional Terminal — API Documentation
**Version:** 1.0 (compatibility layer over `/stock-api/`)  
**Authentication:** All admin endpoints require `X-Admin-Token: <secret>` header (HMAC compare_digest).  
**Base URL (dev):** `http://localhost:5050`  
**Base URL (prod):** served via Replit proxy at `/stock-api/`  
**Versioning:** No formal version prefix yet. Tracked here as v1.0. Breaking changes will increment version.  
**Deprecation Policy:** Endpoints marked `[DEPRECATED]` will be removed after 60-day notice period.

---

## AUTHENTICATION

### Roles
| Role | Current Implementation | Status |
|---|---|---|
| Administrator | `X-Admin-Token` header (single shared secret) | IMPLEMENTED |
| Viewer | Not implemented | NOT IMPLEMENTED |
| Trader | Not implemented | NOT IMPLEMENTED |
| Analyst | Not implemented | NOT IMPLEMENTED |
| Risk Manager | Not implemented | NOT IMPLEMENTED |
| Auditor | Not implemented | NOT IMPLEMENTED |
| Institutional Due-Diligence Viewer | Not implemented | NOT IMPLEMENTED |

### Error Responses (Auth)
| Code | Meaning |
|---|---|
| 401 | Missing or malformed token |
| 403 | Token present but invalid |

---

## PUBLIC ENDPOINTS (No Authentication)

### GET /stock-api/health
Check API server liveness.

**Auth:** None  
**Response:**
```json
{ "status": "ok", "timestamp": "2026-07-21T18:00:00Z" }
```
**Errors:** 503 if database unavailable  
**Freshness:** Real-time  
**Source:** Flask app state  
**Operating Mode:** N/A

---

### GET /stock-api/market/overview
Current market regime, advance/decline, and sector rotation snapshot.

**Auth:** None  
**Parameters:** None  
**Response:**
```json
{
  "advance_decline": { "advancing": 2100, "declining": 1400 },
  "indices": { "spy": {...}, "qqq": {...} },
  "sectors": [...]
}
```
**Freshness:** Cached; updated by scheduled job  
**Source:** `polygon_market_daily`, `polygon_rvol_scan`  
**Operating Mode:** LIVE DATA

---

### GET /stock-api/aiem-paper-portfolio
Open paper trade positions and portfolio summary.

**Auth:** None  
**Parameters:** None  
**Response:**
```json
{
  "account_value": 100000.0,
  "trades": [...],
  "total_pnl": -174.14,
  "total_pnl_pct": -0.0017
}
```
**Source Table:** `aiem_paper_trades` WHERE status='OPEN'  
**Operating Mode:** PAPER TRADING — SIMULATION ONLY  
**Freshness:** Real-time DB query  
**Note:** Values are paper/simulation. No real money involved.

---

### GET /stock-api/paper-trades
Alias of `/stock-api/aiem-paper-portfolio`.

**Auth:** None  
**Operating Mode:** PAPER TRADING — SIMULATION ONLY

---

### GET /stock-api/gap-volume-signal
Stocks meeting gap ≥1% + RVOL ≥2x criteria for current/last trading day.

**Auth:** None  
**Parameters:**
| Name | Type | Default | Description |
|---|---|---|---|
| limit | integer | 50 | Max rows returned |
| date | string (YYYY-MM-DD) | latest | Target date |

**Response:**
```json
{ "signals": [...], "count": 27, "date": "2026-07-21", "source": "polygon_rvol_scan" }
```
**Source Table:** `polygon_rvol_scan`  
**Operating Mode:** LIVE DATA — OOS validated (WR=58.6%, p=0.002, oos_edge=2.5%)  
**Errors:** 400 (invalid limit or date format), 503 (DB unavailable)

---

### GET /stock-api/gamma-wall
GEX gamma wall levels for SPX/SPY options.

**Auth:** None  
**Source:** `oi_daily_snapshot`, options chain computation  
**Operating Mode:** LIVE DATA

---

### GET /stock-api/charm-cascade
Charm-driven delta risk signals.

**Auth:** None  
**Source:** Computed from options chain data  
**Operating Mode:** LIVE DATA

---

### GET /stock-api/aiem-predictions
AIEM autonomous engine predictions for current date.

**Auth:** None  
**Source Table:** `aiem_process_predictions`  
**Operating Mode:** LIVE DATA (autonomous engine output)  
**Freshness:** Updated by `aiem_process.py` scanner

---

### GET /stock-api/unusual-calls
Unusual options call activity (VOI ≥ threshold, premium ≥ $250k).

**Auth:** None  
**Parameters:**
| Name | Type | Default | Description |
|---|---|---|---|
| limit | integer | 150 | Max results |
| cache_only | boolean | false | Return cached results without live fetch |

**Source:** `call_sweep_log` + live Tradier chain  
**Operating Mode:** LIVE DATA

---

### GET /stock-api/washout-ignition-signal
Stocks with washout ignition pattern (gap + volume + close strength).

**Auth:** None  
**Source Table:** `polygon_rvol_scan`  
**Operating Mode:** LIVE DATA

---

### GET /stock-api/pullback-reentry
Module L pullback re-entry candidates.

**Auth:** None  
**Source:** `aiem_pullback_reentry_log`  
**Operating Mode:** LIVE DATA

---

### GET /stock-api/momentum-exhaustion
Module M momentum exhaustion candidates.

**Auth:** None  
**Source:** `aiem_momentum_exhaustion_log`  
**Operating Mode:** LIVE DATA

---

## ADMIN ENDPOINTS (X-Admin-Token required)

### GET /stock-api/admin/macro/latest
Current AIEM macro regime and score.

**Auth:** X-Admin-Token  
**Role Required:** Administrator  
**Response:**
```json
{
  "macro_score": 56.0,
  "regime": "BULL_MODERATE",
  "position_size_modifier": 1.0,
  "summary": "...",
  "snapshot_date": "2026-07-21"
}
```
**Source Table:** `aiem_macro_daily`  
**Freshness:** Updated daily at 09:00 ET  
**Operating Mode:** LIVE DATA  
**Errors:** 403 (unauthorized), 503 (DB unavailable)

---

### GET /stock-api/admin/macro/history
Historical macro scores for chart rendering.

**Auth:** X-Admin-Token  
**Parameters:**
| Name | Type | Default | Max | Description |
|---|---|---|---|---|
| days | integer | 30 | 365 | Days of history |

**Response:**
```json
{ "rows": [{"date": "2026-07-21", "score": 56.0, "regime": "BULL_MODERATE", "position_size_modifier": 1.0}], "count": 7 }
```
**Source Table:** `aiem_macro_daily`  
**Errors:** 400 (invalid days), 403 (unauthorized), 503 (DB unavailable)

---

### GET /stock-api/admin/decision-audit
Options engine decision audit log.

**Auth:** X-Admin-Token  
**Parameters:**
| Name | Type | Default | Description |
|---|---|---|---|
| limit | integer | 50 | Max rows |

**Response:**
```json
{ "rows": [...], "count": 15 }
```
**Source Table:** `oe_decision_audit` WHERE is_test_record=FALSE  
**Verification Status:** Hash chain verified  
**Operating Mode:** PRODUCTION AUDIT LOG

---

### GET /stock-api/admin/gate-events
Options engine gate block/allow events.

**Auth:** X-Admin-Token  
**Source Table:** `oe_gate_events` WHERE is_test_record=FALSE  
**Verification Status:** Chain-linked

---

### GET /stock-api/admin/council-runs
Specialist council deliberation runs.

**Auth:** X-Admin-Token  
**Parameters:**
| Name | Type | Default | Description |
|---|---|---|---|
| limit | integer | 100 | Max rows |

**Source Table:** `aiem_specialist_council_runs`  
**Operating Mode:** PAPER TRADING COUNCIL

---

### GET /stock-api/admin/position-sizing-log
Position sizing decisions with signal source and notional.

**Auth:** X-Admin-Token  
**Source Table:** `aiem_position_sizing_log`

---

### GET /stock-api/admin/evidence-chain/status
Cryptographic evidence chain status (SEQ, hash, last entry).

**Auth:** X-Admin-Token  
**Source Table:** `evidence_chain` (log file + DB)  
**Verification Status:** SHA-256 hash chain

---

### GET /stock-api/admin/scheduler-jobs
APScheduler job list with next_run times.

**Auth:** X-Admin-Token  
**Response:**
```json
{ "jobs": [{"id": "...", "name": "...", "func": "...", "trigger": "cron", "next_run": "2026-07-22T09:45:00"}], "job_count": 274 }
```
**Source:** APScheduler in-process state  
**Freshness:** Real-time  
**Note:** job_count=274 is the live total; `jobs` array may be paginated by scheduler internals

---

### GET /stock-api/admin/job-heartbeats
Last success/failure timestamps for all scheduled jobs.

**Auth:** X-Admin-Token  
**Response:**
```json
{ "jobs": [{"job_name": "...", "last_success": "...", "last_attempt": "...", "last_error": null, "consecutive_failures": 0}] }
```
**Source Table:** `job_heartbeats`  
**Freshness:** Updated on each job completion

---

### GET /stock-api/admin/closed-loop-summary
Summary of AIEM closed-loop learning gaps and audit status.

**Auth:** X-Admin-Token  
**Response Keys:** gap1_audit_trace, gap2_trust_history, gap3_thompson, gap4_ppo_training, gap5_candidate_rankings  
**Source:** `aiem_closed_loop_learning` tables  
**Operating Mode:** AUDIT / ANALYSIS

---

### GET /stock-api/admin/paper-fill-audit
Paper trade fill audit log.

**Auth:** X-Admin-Token  
**Source Table:** `aiem_paper_trades`, `aiem_position_sizing_log`  
**Operating Mode:** PAPER TRADING AUDIT

---

### GET /stock-api/admin/signal-discoveries
All registered signal discoveries with statistical metrics.

**Auth:** X-Admin-Token  
**Response:**
```json
{ "rows": [{"id": 5, "hypothesis_text": "...", "signal_name": "gap_volume", "signal_win_rate": 0.586, "signal_n": 312, "status": "validated", "oos_edge": 2.5, "p_value": 0.002, "discovered_at": "...", "confirmed_at": null}], "count": 5 }
```
**Source Table:** `aiem_signal_discoveries`  
**Verification Status:** id=5 validated (OOS confirmed, p=0.002)  
**Operating Mode:** STATISTICAL RESEARCH

---

### GET /stock-api/admin/pipeline-checkpoint
Options engine pipeline checkpoint status.

**Auth:** X-Admin-Token  
**Source Table:** `daily_pipeline_runs`

---

### GET /stock-api/admin/aiem-pipeline-audit
Full AIEM pipeline audit with trace IDs.

**Auth:** X-Admin-Token  
**Source:** `aiem_closed_loop_learning`, pipeline trace tables

---

### POST /stock-api/admin/aiem-verify-proof
Verify a signed HMAC or JWT audit proof token.

**Auth:** X-Admin-Token  
**Body:** `{ "token": "<hmac_or_jwt>" }`  
**Response:** `{ "valid": true, "payload": {...} }`  
**Verification:** HMAC-SHA256 with AIEM signing key

---

## STRUCTURED ERROR RESPONSES

All endpoints return structured errors in this format:

```json
{ "error": "description", "detail": "optional additional context" }
```

| HTTP Code | Meaning |
|---|---|
| 400 | Invalid request parameters (bad type, out of range, invalid format) |
| 401 | Missing authentication header |
| 403 | Valid header format but token is wrong |
| 404 | Resource not found |
| 503 | Database unavailable or backend timeout |

---

## DATA CONTRACTS

### Paper Trade Object
```json
{
  "id": 1,
  "ticker": "AAPL",
  "trade_type": "CALL_OPTION",
  "entry_price": 150.00,
  "entry_date": "2026-07-15",
  "shares": 1,
  "notional": 15000.00,
  "pnl": -12.50,
  "pnl_pct": -0.083,
  "status": "OPEN",
  "signal_source": "gap_volume"
}
```
**Note:** `trade_type` values: `CALL_OPTION`, `STOCK`, `PUT_OPTION`, `SHORT`  
**Note:** `pnl` and `pnl_pct` are mark-to-market (unrealized for OPEN positions)  
**Operating Mode:** PAPER TRADING — SIMULATION. No real money.

### Signal Discovery Object
```json
{
  "id": 5,
  "hypothesis_text": "gap_volume_signal_name_proof",
  "signal_name": "gap_volume",
  "signal_win_rate": 0.586,
  "signal_n": 312,
  "status": "validated",
  "oos_edge": 2.5,
  "p_value": 0.002,
  "discovered_at": "2026-07-11T17:46:49",
  "confirmed_at": null
}
```
**Note:** `status` values: `hypothesis`, `validated`, `retired`  
**Note:** `oos_edge` is out-of-sample edge in percentage points above baseline

### Macro Snapshot Object
```json
{
  "macro_score": 56.0,
  "regime": "BULL_MODERATE",
  "position_size_modifier": 1.0,
  "snapshot_date": "2026-07-21",
  "summary": "..."
}
```
**Note:** `regime` values: `BULL`, `BULL_MODERATE`, `NEUTRAL`, `BEAR_MODERATE`, `BEAR`  
**Note:** `position_size_modifier` range: 0.5–1.5

### Job Heartbeat Object
```json
{
  "job_name": "aiem_nightly_learn",
  "last_success": "2026-07-21T18:00:00",
  "last_attempt": "2026-07-21T18:00:00",
  "last_error": null,
  "consecutive_failures": 0
}
```

---

## PAGINATION

Current implementation: Most endpoints accept a `limit` parameter (integer, max 500).  
No cursor-based pagination is implemented.  
No `offset` parameter exists.  
**Status:** PARTIAL — full keyset pagination is a deferred item.

## FILTERING

Some endpoints accept `date` (YYYY-MM-DD) and `ticker` query parameters.  
No standardized filter query language exists.  
**Status:** PARTIAL

## SORTING

No standardized `sort_by` / `sort_dir` parameters.  
Server-side defaults apply (typically `ORDER BY id DESC` or `ORDER BY timestamp DESC`).  
**Status:** NOT IMPLEMENTED

## FRESHNESS

Each endpoint table lists the update frequency.  
Freshness timestamps are returned in ISO 8601 UTC format.  
Dashboard polls each endpoint independently (30s–300s intervals).

## KNOWN LIMITATIONS & DEFERRED ITEMS

1. **No versioned URL prefix** — all endpoints at `/stock-api/`. Version prefix `/api/v1/terminal/` deferred.
2. **No OpenAPI spec file** — this document serves as the interim API reference.
3. **No cursor pagination** — limit-only for now.
4. **Role-based access control** — single admin token; 7 roles deferred.
5. **No rate limiting** — deferred (Replit proxy provides basic protection).
6. **No WebSocket/SSE** — polling only; streaming deferred.
7. **No CSRF protection** — sessionStorage token not vulnerable to CSRF by default (no cookies).
