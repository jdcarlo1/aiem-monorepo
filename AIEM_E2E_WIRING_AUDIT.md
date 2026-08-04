# AIEM E2E Wiring Audit — Dashboard · Options Pipeline · Telegram · Services

**Date:** 2026-08-04  
**Scope:** `/workspace/artifacts/aiem-dashboard/src`, `/workspace/artifacts/stock-scanner-api/{main.py,aiem_sse.py,aiem_options_scheduler.py,alert_gateway.py,...}`, root `aiem_telegram_notifier.py`, `artifact.toml`, `.replit`  
**Verdict labels:** **WIRED** | **PARTIAL** | **UNWIRED**

---

## A) Dashboard pages → API endpoints

### Per-page map

| Page | Path | Endpoints called | Backend exists? | Status |
|---|---|---|---|---|
| `login.tsx` | `/` | `POST /stock-api/auth/login` | `main.py` via `aiem_auth.py` | **WIRED** |
| `CommandCenter.tsx` | `/command` | `GET /health`, `/readyz`, `/admin/macro/latest`, `/admin/scheduler-jobs`, `/admin/job-heartbeats`; SSE `/events/stream` via `useEventStream` | `main.py` + `aiem_sse.py` blueprint | **WIRED** |
| `Scheduler.tsx` | `/scheduler` | `GET /admin/scheduler-jobs`; `POST /admin/scheduler-jobs/<id>/force` | `main.py` L72316, L72336 | **WIRED** (FORCE button has `onClick`) |
| `Alerts.tsx` | `/alerts` | `GET /admin/job-heartbeats`; `GET /admin/telegram-alerts?limit=40` | `main.py` L61689, L72948 | **WIRED** |
| `Opportunities.tsx` | `/opportunities` | `/aiem-predictions`, `/gap-volume-signal`, `/options-pipeline/candidates`, `/washout-ignition-signal`, `/pullback-reentry`, `/momentum-exhaustion` | all in `main.py` | **WIRED** |
| `PaperTrades.tsx` | `/paper-trades` | `/aiem-paper-portfolio`, `/paper-trades`, `/admin/paper-fill-audit` | `main.py` L50308–50560 | **WIRED** (stock paper book only — not OE `oe_trade_records` / `aiem_options_alerts`) |
| `Decisions.tsx` | `/decisions` | `/admin/decision-audit?limit=50`, `/admin/gate-events?limit=50` | `main.py` L72465, L72543 | **WIRED** |
| `Proof.tsx` | `/proof` | `GET /admin/evidence-chain/status`; `POST /admin/aiem-verify-proof` | `main.py` L72760, L24724 | **WIRED** |
| `Risk.tsx` | `/risk` | `/admin/position-sizing-log?limit=50`, `/gamma-wall`, `/charm-cascade` | `main.py` | **WIRED** |
| `Options.tsx` | `/options` | `/admin/pipeline-checkpoint`, `/admin/aiem-pipeline-audit` | `main.py` L25012, L50623 | **PARTIAL** — does not call `/admin/daily-pipeline-runs` despite UI comments about that table |
| `Regime.tsx` | `/regime` | `/admin/macro/latest`, `/admin/macro/history?days=60` | `main.py` L21545, L73582 | **WIRED** |
| `Signals.tsx` | `/signals` | `/admin/signal-discoveries`, `/gap-volume-signal` | `main.py` | **WIRED** |
| `Council.tsx` | `/council` | `/admin/council-runs?limit=50` | `main.py` L72609 | **WIRED** |
| `Probability.tsx` | `/probability` | `/aiem-probability-engine/daily-picks`, `.../track-record` | `main.py` L51214, L51274 | **WIRED** |
| `Performance.tsx` | `/performance` | `/paper-performance` | `main.py` L50824 | **WIRED** |
| `Calibration.tsx` | `/calibration` | `/aiem-probability-engine/calibration` | `main.py` L51431 | **WIRED** |
| `Learning.tsx` | `/learning` | `/admin/closed-loop-summary` (no poll interval) | `main.py` L51012 | **PARTIAL** — right panel “ML PIPELINE TRAINING” is an explicit stub (`NOT IMPLEMENTED`); no refetch control |
| `Audit.tsx` | `/audit` | `/admin/audit/{chain-status,docs,doc-content,run-log,run-log-detail,run-script}` | `main.py` L12586–12795 | **WIRED** |
| `not-found.tsx` | fallback | none | n/a | — |
| `Dashboard.tsx` | **not routed** | `GET {BASE}/stock-api/options/reconcile` | `main.py` L1965 | **UNWIRED** — file exists, **not in `App.tsx` Router**, not in Sidebar |

### Shared (non-page) callers

| Caller | Endpoints | Status |
|---|---|---|
| `components/layout/AppLayout.tsx` | `GET /stock-api/auth/me` | **WIRED** (`aiem_auth.py`) |
| `lib/auth.ts` | `POST /stock-api/auth/logout` | **WIRED** (`aiem_auth.py` L444) |
| `hooks/use-event-stream.ts` | `EventSource /stock-api/events/stream` | **WIRED** — mounted on Command Center |

### page → missing API

None of the **routed** pages call a missing route. All listed GET/POST paths resolve in `main.py` or `aiem_sse.py` / `aiem_auth.py`.

### API → unused by dashboard (dashboard-facing admin APIs with no page consumer)

| Endpoint | Defined | Consumed by UI? | Status |
|---|---|---|---|
| `GET /stock-api/admin/paper-job-ledger` | `main.py` | PaperTrades.tsx | **WIRED** |
| `GET /stock-api/admin/daily-pipeline-runs` | `main.py` | Options.tsx | **WIRED** |
| `GET /stock-api/admin/governance-decisions` | `main.py` | Decisions.tsx | **WIRED** |
| `GET /stock-api/admin/trace-explorer` | `main.py` | **nothing** | **UNWIRED** (API ready, no page) |
| `GET /stock-api/options/reconcile` | `main.py` | only orphan `Dashboard.tsx` | **UNWIRED** to live UI |
| Many other admin/tooling routes (`/admin/emergency-run`, discovery-cycle, 0dte, model/*, etc.) | `main.py` | intentionally ops/API, not terminal screens | out of dashboard scope |

### Dead / stub UI controls

| Location | Finding | Status |
|---|---|---|
| `Scheduler.tsx` FORCE | Wired to force endpoint | **WIRED** |
| `Learning.tsx` “ML PIPELINE TRAINING” panel | Hard-coded “DATA UNAVAILABLE / NOT IMPLEMENTED” — no API | **UNWIRED** (acknowledged stub) |
| `Learning.tsx` RefreshCw icon | Decorative only (not a button / no refetch) | **PARTIAL** |
| `Dashboard.tsx` | Entire page orphaned from router | **UNWIRED** |
| `Alerts.tsx` | No “send test telegram” control (admin APIs `test-telegram` / `send-market-brief` exist in API only) | **PARTIAL** (read-only feed) |

---

## B) Options pipeline (`aiem_options_scheduler.py`)

### `OE_SCHEDULER_ENABLED` — **WIRED** (fail-closed gate)

```4664:4696:artifacts/stock-scanner-api/aiem_options_scheduler.py
# OE_SCHEDULER_ENABLED is a fail-closed explicit opt-in: set it to "1" ONLY
# in the production Deployment secrets.
_oe_enabled  = os.environ.get("OE_SCHEDULER_ENABLED") == "1"
...
if _is_gce:
    sched.start()
else:
    log.warning("[startup] OE_SCHEDULER_ENABLED != '1' — APScheduler NOT started")
```

- Process still boots (health server + keepalive) when unset; **cron jobs do not fire**.
- Set in `.replit` `[userenv.production]` as `OE_SCHEDULER_ENABLED = "1"`.
- **Not** set in `artifacts/stock-scanner/.replit-artifact/artifact.toml` `[services.env]` for `options-pipeline-scheduler` — relies on deployment userenv. Workspace/dev without the flag → scheduler idle (**ops footgun** → treat as **PARTIAL** for env wiring).

### Main stages of daily pipeline — **WIRED**

**Cron (ET, Mon–Fri)** when enabled:

| Time | Job id | Role |
|---|---|---|
| 07:30 | `premarket_scan` | Premarket intel → `options_engine_premarket` |
| 09:30 | PM intraday update | Break/fail of PM levels |
| 09:40 | `seed_daily_candidates` | Seed `options_pipeline_jobs` + `daily_pipeline_runs` |
| 09:45 | `run_pipeline_worker` | Claim PENDING → `_execute_job` (full pipeline) |
| 16:44 | `daily_trace_report` | DPL daily audit report |
| 16:46 | `grade_outcomes_job` | Stage 9/10 learning / grade expired alerts |
| + | stale recovery, schedule integrity, Polygon canary, trigger plans | Watchdogs |

**Per-ticker `_execute_job` stages (approx.):**  
1 Polygon/DB → 2 stock analysis → PM / MTF / PAT / OC / EI / CCS → 3 options analysis → 4 risk gates → 5 REQ6 scoring → 6 decision → 7 alert fields → integrity gate → **8 DB persist** → Phase2 counterfactual + trade record → DPL decision write → `options_engine_runs` → grade path later.

### Where paper trades get written — **WIRED** (options book, not stock paper)

| Write | Table | Path |
|---|---|---|
| Stage 8 | `aiem_options_alerts` (+ snapshots) | `_pipe.save_options_alert()` ← `aiem_options_pipeline.py` |
| Phase 2 | `oe_trade_records` | `_p2.capture_trade_record()` ← `aiem_options_phase2.py` L1153/L1260 |
| Audit | `options_engine_runs`, `oe_decision_audit`, `oe_gate_events` | scheduler / DPL |

**Not** written by this scheduler: `aiem_paper_trades` (stock paper book used by dashboard Paper Trades / Performance). Options paper is a **separate** ledger → dashboard Paper Trades page does **not** show OE alerts (**PARTIAL** product wiring).

### D12 placeholder — **PARTIAL**

```311:312:artifacts/stock-scanner-api/aiem_options_pipeline.py
# ── D12: Historical performance ────────────────────────────────────────────
scores["D12_historical_performance"] = 50   # neutral — no historical win rate yet
```

Weight `D12_historical_performance: 0.02` is live in REQ6; score is hardcoded 50.

### `aiem_operational_controls` — **UNWIRED**

Repo-wide import search: **only** self-references / commented usage examples inside `aiem_operational_controls.py`. Zero live callers from scheduler, pipeline, or `main.py`. Kill-switch / fail-closed paper execute wrapper is dormant.

### Failover / backup runner — **WIRED**

| Layer | Path | Status |
|---|---|---|
| Standalone runner | `artifacts/stock-scanner-api/aiem_backup_runner.py` | **WIRED** |
| In-VM trigger | `POST /stock-api/admin/emergency-run` → subprocess runner (`main.py` L25043) | **WIRED** |
| GH Actions morning | `.github/workflows/morning-backup.yml` → emergency-run @ 9:50/10:10 ET | **WIRED** |
| GH Actions watchdog | `.github/workflows/market-hours-watchdog.yml` | **WIRED** |
| Heartbeat visibility | `aiem_watchdog.py` looks for `backup_runner_%` heartbeats | **WIRED** |

Backup runner is **not** an `artifact.toml` always-on service (by design: on-demand / GH).

---

## C) Telegram

### Live send paths (who actually hits Telegram API)

| Process | Entry | Send helper | Ledger? |
|---|---|---|---|
| **aiem-telegram service** | `notifier_wrapper.sh` → **root** `/workspace/aiem_telegram_notifier.py` | `_tg_send` L252 | **Yes** — `alert_gateway.log_alert` |
| **stock-api** | `main.py` `_tg_send` L15746 | Bot API sendMessage | **Yes** — `alert_gateway.log_alert` |
| **aiem-process** | `aiem_process.py` `_tg_send` ~L1039 | Bot API | **Yes** — `alert_gateway.log_alert` |
| **options scheduler** | `aiem_options_scheduler._tg` L114 | Bot API **direct** | **No** — does **not** call `alert_gateway` |

### `alert_gateway.py` vs notifier

- `alert_gateway.py` is **ledger + trust lookup only** (Phase 1). It does **not** send Telegram.
- Docstring: every process should call `log_alert` after send; fail-open.
- **Live alerts** = multiple senders; **canonical scheduled briefs / tab digests** = root `aiem_telegram_notifier.py` (production service `aiem-telegram`).
- `main.py` also sends many live/ops alerts (job health, drift, price+call flow, paper exits, etc.).

### `telegram_alert_ledger` writers — **PARTIAL** coverage

| Writer | Status |
|---|---|
| `alert_gateway.log_alert` (schema owner) | **WIRED** |
| Callers: notifier, `main._tg_send`, `aiem_process`, selloff/pullback/momentum/news/sms modules | **WIRED** |
| `aiem_options_scheduler._tg` | **UNWIRED** to ledger |
| `aiem_backup_runner._tg` | **UNWIRED** to ledger (standalone send) |
| Grading | `alert_grading.py` updates outcomes on ledger | **WIRED** |

### Dashboard Alerts page consumer — **WIRED**

- `Alerts.tsx` → `GET /stock-api/admin/telegram-alerts?limit=40` → reads `telegram_alert_ledger`.
- Also shows `job_heartbeats` (failure log), not the Telegram send path itself.
- Telegram notifier heartbeat may show “NOT TRACKED” if no `job_heartbeats` row matches “telegram”/“notif” (**PARTIAL** UX).

---

## D) `artifact.toml` services vs `.replit` workflows

### Production services (`artifacts/stock-scanner/.replit-artifact/artifact.toml`)

| Service | Port | Production run | In `.replit` Project workflow? |
|---|---|---|---|
| `web` (stock-scanner UI) | 21411 | static build | No (served by deployment router) |
| `stock-api` | 5050 | `stock_api_wrapper.sh` | **No** |
| `aiem-telegram` | 5052 | `notifier_wrapper.sh` → root notifier | **No** |
| `aiem-process` | 5055 | `aiem_process_wrapper.sh` | **Yes** (but runs bare `aiem_process.py`, **no wrapper**) |
| `options-pipeline-scheduler` | 5053 | `aiem_options_scheduler.py` | **No** |
| `ase-strat-scheduler` | 5054 | `aiem_strat_scheduler.py` | **No** |
| `probability-engine-scheduler` | 5056 | `daily_scheduler.py` | **Yes** |

### AIEM dashboard artifact

| Service | Notes |
|---|---|
| `artifacts/aiem-dashboard` `web` `/aiem/` | Production static; **not** referenced by `.replit` workflows |

### `.replit` Project auto-start

Only starts:
1. `probability-engine-scheduler`
2. `aiem-process` (unwrapped)

`git-autosync` workflow exists but is **commented out** of Project auto-start.

### Findings

| Finding | Status |
|---|---|
| GCE/production launches services from **artifact.toml**, not `.replit` workflows | expected |
| Workspace “Project” workflow missing stock-api, telegram, options scheduler, ase-strat, dashboards | **PARTIAL** for local/workspace parity |
| `aiem-process` workflow bypasses crash-forensics wrapper used in production | **PARTIAL** |
| Options scheduler depends on `OE_SCHEDULER_ENABLED=1` in production userenv | **WIRED** in prod env; fragile if secret missing |
| No production service entry for `aiem_backup_runner` / `aiem_operational_controls` | by design / **UNWIRED** respectively |
| Root orphans (`aiem_autonomous.py`, `aiem_standalone_scanner.py`, root `main.py` stub) | **UNWIRED** to artifact services |

---

## Cross-cutting summary

### WIRED
- Nearly all routed dashboard pages ↔ existing API routes  
- SSE on Command Center  
- Scheduler FORCE control  
- Alerts page ↔ `telegram_alert_ledger`  
- Options daily cron + stage pipeline + alert/trade DB writes  
- GH Actions ↔ `/admin/emergency-run` ↔ `aiem_backup_runner.py`  
- Production `artifact.toml` service set for core AIEM processes  
- Notifier + main + aiem-process Telegram sends with ledger logging  

### PARTIAL
- Options UI omits `daily-pipeline-runs` / reconcile screens  
- Paper Trades UI = stock book only; OE writes `aiem_options_alerts` / `oe_trade_records`  
- Learning ML panel stub; closed-loop fetch has no poll  
- `OE_SCHEDULER_ENABLED` fail-closed env footgun  
- Options scheduler Telegram bypasses ledger  
- Workspace workflows ≠ production service set; aiem-process wrapper mismatch  
- D12 hardcoded 50  

### UNWIRED
- `pages/Dashboard.tsx` (orphaned) + `/options/reconcile` unused by live nav  
- Admin APIs: `paper-job-ledger`, `daily-pipeline-runs`, `governance-decisions`, `trace-explorer` (no page)  
- `aiem_operational_controls.py` (zero callers)  
- Options/backup `_tg` → no `telegram_alert_ledger` rows  

---

## Evidence index (primary paths)

- Dashboard pages: `/workspace/artifacts/aiem-dashboard/src/pages/`  
- Router: `/workspace/artifacts/aiem-dashboard/src/App.tsx`  
- API: `/workspace/artifacts/stock-scanner-api/main.py`  
- SSE: `/workspace/artifacts/stock-scanner-api/aiem_sse.py`  
- Options scheduler: `/workspace/artifacts/stock-scanner-api/aiem_options_scheduler.py`  
- Options scoring/D12 + alert persist: `/workspace/artifacts/stock-scanner-api/aiem_options_pipeline.py`  
- OE trade records: `/workspace/artifacts/stock-scanner-api/aiem_options_phase2.py`  
- Operational controls: `/workspace/artifacts/stock-scanner-api/aiem_operational_controls.py`  
- Backup: `/workspace/artifacts/stock-scanner-api/aiem_backup_runner.py`  
- Ledger: `/workspace/artifacts/stock-scanner-api/alert_gateway.py`  
- Live notifier: `/workspace/aiem_telegram_notifier.py` + `notifier_wrapper.sh`  
- Services: `/workspace/artifacts/stock-scanner/.replit-artifact/artifact.toml`  
- Workflows: `/workspace/.replit`  
- GH failover: `/workspace/.github/workflows/morning-backup.yml`, `market-hours-watchdog.yml`
