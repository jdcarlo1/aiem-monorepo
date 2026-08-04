# AIEM Master End-to-End Wiring Audit

**Date:** 2026-08-04  
**Live entry:** `artifacts/stock-scanner-api/main.py` via `stock_api_wrapper.sh` (root `main.py` is a hello stub)  
**Companion detail reports:**
- `AIEM_PAPER_TRADE_E2E_WIRING_AUDIT.md` — paper open→close→learn
- `AIEM_DISCOVERY_MODULES_GATE_AUDIT.md` — Modules 1–7 + discovery→alert
- `AIEM_E2E_WIRING_AUDIT.md` — dashboard · options · telegram · services
- `AIEM_WIRING_AUDIT_2026-08-04.md` — initial gap list + fixes in this PR

**Verdict labels:** **WIRED** | **PARTIAL** | **UNWIRED** | **BY DESIGN**

---

## Executive verdict

The stock **paper-trading loop is end-to-end wired** (9:42 execute → 16:01 MTM → close funnel → trust/Thompson/audit_trace).  
The **discovery gate stack (M1–M3, M5–M7) is scheduled and writing**, but **Module 4 is only human-POST**, while a **weekly auto-retire bypasses it**, and **D3 governance is SHADOW** (logs `would_block`, does not stop trades).  
The **dashboard is mostly wired** after this PR’s fixes; several new admin APIs still need screens, and options paper lives in a **separate ledger** from the Paper Trades page.

---

## System map (production)

```text
┌──────────────────────────────────────────────────────────────────────────┐
│ artifact.toml services (production)                                      │
│  stock-api ──► main.py (Flask + APScheduler ~150 jobs)                   │
│  aiem-process ──► aiem_process.py (nano-cap scanner)                     │
│  aiem-telegram ──► aiem_telegram_notifier.py (tab digests)               │
│  options-pipeline-scheduler ──► aiem_options_scheduler.py                │
│       └─ requires OE_SCHEDULER_ENABLED=1 (fail-closed)                   │
│  ase-strat-scheduler ──► aiem_strat_scheduler.py                         │
│  probability-engine-scheduler ──► aiem_probability_engine/daily_scheduler│
└──────────────────────────────────────────────────────────────────────────┘
        │                         │                         │
        ▼                         ▼                         ▼
 aiem_paper_trades          aiem_options_alerts        telegram_alert_ledger
 (stock paper book)         oe_trade_records           (via alert_gateway)
        │                   (options book — SEPARATE)
        ▼
 Dashboard PaperTrades / Performance   ≠   Options page / OE dashboard
```

---

## Path 1 — Stock paper trade (core money path)

```text
08:00  v3_discovery_premarket → aiem_discovery_memory
09:00  macro_precompute
09:42  aiem_paper_execute → _aiem_paper_execute_today
         ├─ advisory lock (fail-closed) + ledger try_claim
         ├─ G0/G1 SHADOW → pick_candidates
         ├─ macro hard-block → sizing FAIL-CLOSED
         ├─ D2 stages 1–17 (audit; stage FAIL does not block INSERT)
         └─ INSERT aiem_paper_trades (OPEN + audit_trace_id)
16:01  aiem_paper_mtm → rules EXIT / 14d / −15% stop
         └─ _aiem_close_paper_trade_and_run_loop
              ├─ trust update + Thompson + audit_trace  ✅ WIRED
              └─ PPO/RL on MTM batch only               ⚠ PARTIAL
```

| Stage | Verdict | Key evidence |
|---|---|---|
| Scheduler 9:42 execute | **WIRED** | `main.py` job `aiem_paper_execute` |
| Pick sources + washout/squeeze status gates | **WIRED** | id=9 / Short_Squeeze_Reversion must be `validated` |
| Kill-switch / daily-loss / PCR exception paths | **PARTIAL** | Many exception handlers fail-open |
| Position sizing | **WIRED** | Fail-closed |
| D3 G0–G3/G6 | **PARTIAL** | Default **SHADOW** — would_block only |
| INSERT paper trades | **WIRED** | `aiem_paper_trades` |
| Diagram1 `run_full_cycle` → real INSERT | **UNWIRED** | `_h_paper_trade` is shadow-only |
| MTM + close funnel | **WIRED** | All live closes via `_aiem_close_paper_trade_and_run_loop` |
| Trust / Thompson / audit_trace | **WIRED** | Close funnel |
| PPO training | **PARTIAL** | MTM batch; close reports `ppo_trained=False` |
| `aiem_operational_controls` | **UNWIRED** | Zero importers |

---

## Path 2 — Discovery → Modules 1–7 → live alerts

```text
Create discovery
  ├─ mkt_save_discovery → status=validated (hard OOS/WR/n gates)     WIRED
  ├─ Module 5/6 → status=hypothesis                                  WIRED
  └─ research grid → aiem_research_insights only (not discoveries)   PARTIAL

02:00 ET  Module 1 → aiem_discovery_outcomes (no status change)      WIRED
02:30 Sun Module 2 → aiem_module2_evaluations (failing/decaying)     WIRED
03:00 Sun Module 3 → aiem_module3_evaluations (promote_ready)        WIRED
         Module 4  → human POST only → aiem_signal_actions           PARTIAL
18:00 Sun auto_retire → UPDATE status='retired' (bypasses M4!)       CONFLICT
04:00/04:15 Module 5/6 → new hypothesis rows                         WIRED
17:00 M–F Module 7 sector rotation → L8 ±0.5 pts                     WIRED

Live alerts
  ├─ washout id=9 → status-gated                                     WIRED
  ├─ squeeze → paper gated; scan records-only                        WIRED
  └─ provenance HMAC on discovery rows                               UNWIRED
```

| Module | Verdict | Changes discovery `status`? |
|---|---|---|
| 1 Outcome | **WIRED** | No |
| 2 Decay | **WIRED** | No (by design) |
| 3 Promotion | **WIRED** | No (by design) |
| 4 Human gate | **PARTIAL** | Yes, only on human POST; auto-retire bypasses |
| 5 Pattern discovery | **WIRED** | Inserts `hypothesis` |
| 6 Rediscovery | **WIRED** | Inserts `hypothesis` |
| 7 Sector rotation | **WIRED** | N/A |

**Integrity conflict:** `_mkt_auto_retire_decaying_discoveries` (Sun 18:00) can `UPDATE status='retired'` without Module 4 — contradicts the “human-in-the-loop kill switch” design in `AIEM_OPEN_ITEMS.md`.

---

## Path 3 — Options pipeline

| Item | Verdict |
|---|---|
| Service in `artifact.toml` | **WIRED** |
| `OE_SCHEDULER_ENABLED=1` required | **WIRED** (fail-closed; env footgun if unset) |
| Daily seed 09:40 → execute 09:45 → grade 16:46 | **WIRED** |
| Writes `aiem_options_alerts` / `oe_trade_records` | **WIRED** |
| Same book as dashboard Paper Trades | **UNWIRED** — separate ledger |
| D12 historical performance | **PARTIAL** — hardcoded `50` |
| `aiem_operational_controls` | **UNWIRED** |
| GH Actions failover → emergency-run → backup_runner | **WIRED** |
| Options `_tg` → `telegram_alert_ledger` | **UNWIRED** |

---

## Path 4 — Telegram

| Sender | Ledger via `alert_gateway`? | Verdict |
|---|---|---|
| `aiem_telegram_notifier.py` (service) | Yes | **WIRED** |
| `main.py` `_tg_send` | Yes | **WIRED** |
| `aiem_process.py` `_tg_send` | Yes | **WIRED** |
| `aiem_options_scheduler._tg` | **No** | **PARTIAL** |
| `aiem_backup_runner._tg` | **No** | **PARTIAL** |
| Dashboard Alerts → `/admin/telegram-alerts` | Yes | **WIRED** (this PR) |

---

## Path 5 — Dashboard ↔ API

### Routed pages — all call existing backends (after this PR)

Command Center (incl. SSE), Scheduler (incl. FORCE), Alerts (incl. telegram ledger), Opportunities, Paper Trades, Decisions, Proof, Risk, Options, Regime, Signals, Council, Probability, Performance, Calibration, Learning, Audit.

### Remaining gaps

| Gap | Verdict |
|---|---|
| `Dashboard.tsx` orphaned (not in `App.tsx` / Sidebar) | **UNWIRED** |
| `/admin/paper-job-ledger` → Paper Trades page | **WIRED** (this pass) |
| `/admin/daily-pipeline-runs` → Options page | **WIRED** (this pass) |
| `/admin/governance-decisions` → Decisions page | **WIRED** (this pass) |
| `/admin/trace-explorer` — API yes, page no | **PARTIAL** |
| Learning “ML PIPELINE TRAINING” panel | **UNWIRED** stub |
| Paper Trades shows stock book only (not OE) | **PARTIAL** |

---

## Top integrity risks (ordered)

1. **D3 SHADOW** — governance never blocks live paper execute until flipped to ENFORCE.  
2. **Auto-retire vs Module 4** — weekly job retires without human gate.  
3. **`aiem_operational_controls` dormant** — kill switches / fail-closed options paper wrapper unused.  
4. **Fail-open exception paths** in paper execute (ledger claim / kill-switch / PCR errors).  
5. **Two paper books** — stock `aiem_paper_trades` vs options `aiem_options_alerts` / `oe_trade_records`; dashboard only shows stock.  
6. **D12 = 50** — options scoring uses fake neutral historical performance.  
7. **Options/backup Telegram bypass ledger** — Alerts page undercounts those sends.  
8. **Discovery provenance unwired** — signing is for sessions/D2–D3, not discovery rows.

---

## What this PR already fixed

- Scheduler FORCE button → real API  
- SSE hook mounted on Command Center  
- Five missing admin routes added  
- Alerts page → `telegram_alert_ledger`  
- Empty root `aiem_security.py` → re-export live module  
- Decisions → governance-decisions; Options → daily-pipeline-runs; Paper Trades → paper-job-ledger  
- Full E2E audit suite (master + paper + discovery + dashboard/options/telegram)  

---

## Recommended fix order (next)

1. Decide: **auto-retire** stays (document as intentional) **or** route through Module 4.  
2. Flip selected D3 checkpoints G0/G1/G2 to **ENFORCE** once shadow logs look clean — or keep SHADOW and stop claiming “governance blocks trades.”  
3. Wire `aiem_operational_controls` into options Stage-8 / paper-execute.  
4. Surface unused admin APIs on Decisions / Options / Audit pages.  
5. Replace D12 placeholder with graded `oe_trade_records` win-rate when n is sufficient.  
6. Route options/backup `_tg` through `alert_gateway.log_alert`.  
7. Either mount orphan `Dashboard.tsx` or delete/archive it.
