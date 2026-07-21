# AIEM DASHBOARD — PHASE A
## Real-Time Capability Inventory
**Generated:** 2026-07-21 | **Source:** artifacts/stock-scanner-api/main.py + frontend scan

---

## Summary
| Capability | Status |
|------------|--------|
| Server-Sent Events (SSE) | PARTIAL — 1 endpoint, chat only |
| WebSocket | NOT_IMPLEMENTED |
| Redis | NOT_IMPLEMENTED |
| Message queues | NOT_IMPLEMENTED |
| Database notifications (LISTEN/NOTIFY) | NOT_IMPLEMENTED |
| Internal event bus | PARTIAL — aiem_communication_bus.py exists |
| Polling endpoints | PARTIAL — several endpoints support polling |
| Scheduler events | READY — APScheduler, 7 jobs |
| Worker heartbeats | READY — paper_trade_watchdog_heartbeat (2,321 rows) |
| Telegram events | READY — telegram_alert_ledger (1,144 rows) |

---

## 1. Server-Sent Events (SSE)

### Confirmed SSE Endpoint
**File:** `artifacts/stock-scanner-api/main.py:66824`  
**Route:** `GET /stock-api/aiem/chat/stream`  
**Mimetype:** `text/event-stream` (confirmed at line 66824)  
**Producer:** AIEM chat session worker  
**Consumer:** Browser (via EventSource)  
**Payload:** Streaming token-by-token LLM output from AIEM focused session  
**Delivery frequency:** Token-by-token during chat response  
**Duplicate protection:** job_id deduplication  
**Reconnection handling:** NOT_IMPLEMENTED — no Last-Event-ID support  
**Missed-event recovery:** NOT_IMPLEMENTED  
**Runtime proof:** `/stock-api/aiem/chat/stream` confirmed live in route list (line 66690)  
**Dashboard suitability:** PARTIAL — suitable for AIEM chat widget only; not a general event bus  

### Frontend SSE Usage
Grep of `artifacts/stock-scanner/src/` found **zero** EventSource references.  
The SSE endpoint exists server-side but the current dashboard frontend does **not** consume it.

---

## 2. WebSocket

**Status:** NOT_IMPLEMENTED  
No `flask_socketio`, `websockets`, `socket.io`, or WebSocket upgrade code found in any module.  
No `ws://` or `wss://` references in main.py or frontend source.

---

## 3. Polling Endpoints

The following endpoints are designed for or are suitable for periodic polling:

| Route | Poll Frequency | Purpose | Dashboard Screen |
|-------|---------------|---------|-----------------|
| GET /stock-api/health | Any | Health probe | System Operations |
| GET /stock-api/healthz | Any | Health probe | System Operations |
| GET /stock-api/admin/job-health | 30s | Job health status | System Operations |
| GET /stock-api/admin/job-heartbeats | 60s | Worker heartbeats | System Operations |
| GET /stock-api/aiem-paper-portfolio | 60s | Open positions | Paper Trading |
| GET /stock-api/admin/pipeline-checkpoint | 30s | Options pipeline status | Live Decisions |
| GET /stock-api/admin/aiem-process/last-scan-status | 60s | Scan status | Opportunity Queue |
| GET /stock-api/admin/scheduler-jobs | 30s | Scheduler job list + next fire times | System Operations |
| GET /stock-api/aiem/chat/\<job_id\> | 2s | Chat job status (polling fallback) | AIEM Chat |

**Evidence:** `main.py:11519` contains `"poll_url": "/stock-api/admin/aiem-process/last-scan-status"` — explicit polling URL contract in API response.

---

## 4. Internal Event Bus

**File:** `artifacts/stock-scanner-api/aiem_communication_bus.py`  
**SHA-256:** ef327bbccd7f240dfca6bcc34ddd35cae44ae53c647deb9381def4e1a36d27d4  
**Status:** PARTIAL  
**Description:** Internal communication bus for AIEM modules. Exists as a module but is not exposed via WebSocket or SSE to the frontend.  
**Producer:** Various AIEM modules  
**Consumer:** Internal only (not browser-accessible)  
**Dashboard suitability:** NOT READY — would need an SSE/WebSocket bridge  

---

## 5. Scheduler Events

**Scheduler:** APScheduler (BlockingScheduler in aiem_options_scheduler.py, BackgroundScheduler in main.py)  
**Status:** READY  
**Runtime proof:** `/stock-api/admin/scheduler-jobs` endpoint returns live job list with next_run_time

### Confirmed Scheduled Jobs (options-pipeline-scheduler)
| Job ID | Trigger | Next Fire |
|--------|---------|-----------|
| stale_recovery | cron every 5 min | live |
| daily_trace_report | mon-fri 16:44 ET | live |
| grade_outcomes | mon-fri 16:46 ET | live |
| premarket_scan | mon-fri 07:30 ET | live |
| pm_intraday_update | mon-fri 09:36 ET | live |
| seed_daily_candidates | mon-fri 09:40 ET | 2026-07-22 |
| run_pipeline_worker | mon-fri 09:45 ET | 2026-07-22 |

**Dashboard API:** `GET /stock-api/admin/scheduler-jobs` returns all jobs with next_run_time.  
**Suitability:** READY — dashboard can poll this endpoint every 30s for live scheduler status.

---

## 6. Worker Heartbeats

**Table:** `paper_trade_watchdog_heartbeat`  
**Rows:** 2,321 (confirmed 2026-07-15 to 2026-07-21 15:46)  
**Columns:** `process_type, execution_id, last_alive, pid, status, note`  
**Writer:** `aiem_paper_watchdog.py` + `aiem_options_scheduler.py`  
**Status:** READY  
**Dashboard suitability:** READY — query this table for liveness; last_alive within 5 min = healthy

---

## 7. Telegram Events

**Table:** `telegram_alert_ledger`  
**Rows:** 1,144 (confirmed 2026-07-13 to 2026-07-21)  
**Writer:** `aiem_telegram_notifier.py` — 18 tab briefs, all confirmed live  
**Status:** READY  
**Dashboard suitability:** READY — query for last N alerts; filter by alert_type for notification history

---

## 8. Email Events

**Table:** `owner_email_log` (21 rows confirmed)  
**Writer:** `email_alerts.py` — owner email scheduler  
**Status:** READY (read-only for dashboard)  
**Dashboard suitability:** READY — shows email delivery history  

---

## Real-Time Architecture Gap Summary

For a true real-time dashboard the following work is needed:

| Gap | Priority | Work Required |
|-----|----------|--------------|
| No WebSocket server | P1 | Add flask-socketio or dedicated WebSocket service |
| SSE covers only chat | P1 | Extend SSE to emit pipeline events, new paper trades, gate fires |
| No LISTEN/NOTIFY | P2 | Add PostgreSQL LISTEN on key tables |
| Frontend has no EventSource | P0 | Dashboard frontend must implement EventSource for streaming |
| No reconnection / missed-event recovery | P1 | Add Last-Event-ID header support to SSE endpoint |

**Interim solution (polling):** Dashboard can achieve near-real-time with 10-30 second polling against existing endpoints. This requires no backend changes and is sufficient for Phase B build start.
