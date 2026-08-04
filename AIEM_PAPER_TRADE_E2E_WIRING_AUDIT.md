# AIEM Paper Trading Path — End-to-End Wiring Audit

**Date:** 2026-08-04  
**Live server:** `artifacts/stock-scanner-api/main.py` (not root `main.py`)  
**Scope:** Scheduler → pick → execute → MTM → close → learning loop → Diagram 2 / orchestrator

---

## Verdict

The **scheduled paper path is end-to-end wired** for open → close → trust/Thompson/audit_trace. PPO and full RL run as a **batch tail of MTM**, not inside the per-trade close funnel. Diagram 2 stages 1–19 are **instrumented** on the execute path via `execute_stage()`; Diagram 1 `run_full_cycle()` is a **parallel analysis lane** that does **not** insert real `aiem_paper_trades` rows. Several gates remain **SHADOW** or **fail-open**.

---

## Pipeline diagram

```text
[ Cron mon-fri 08:00 ET ]  id=v3_discovery_premarket
        │  aiem_v3_discovery.run_discovery → aiem_discovery_memory
        │  aiem_v3_technical.run_technical_analysis
        ▼
[ Cron mon-fri 09:00 ET ]  id=macro_precompute
        │  aiem_macro_engine.compute_macro_snapshot
        ▼
[ Cron mon-fri 09:42 ET ]  id=aiem_paper_execute
        │  trigger_source="scheduled_942"
        ▼
┌──────────────────────────────────────────────────────────────────┐
│  _aiem_paper_execute_today (main.py:19071)                       │
│  • trading-day check                                             │
│  • pg_advisory_xact_lock date gate (fail-closed)                 │
│  • aiem_paper_recovery.try_claim ledger (fail-OPEN on exception) │
│  • _AIEM_PAPER_LOCK threading                                    │
│  • D3 G0 boot auth (SHADOW default — cosmetic block)             │
│  • aiem_paper_execution_log RUNNING                              │
│  • skip if any aiem_paper_trades for today                       │
│  • picks = _aiem_paper_pick_candidates()                         │
│  • kill_switch / daily_loss / portfolio_corr (fail-OPEN on err)  │
│  • D3 G1 (SHADOW)                                                │
│  • macro gate (BEAR_SEVERE hard block; err → proceed)            │
│  • quotes + stage4 revalidate + bull/bear top-3                  │
│  • per pick: sizing FAIL-CLOSED → D2 stages 1-17 → G2/G3 SHADOW  │
│  • INSERT aiem_paper_trades (+ audit_trace_id, plan ids)         │
│  • D2 stages 18-19 post-insert                                   │
└──────────────────────────────────────────────────────────────────┘
        │
        │  (also: startup_catchup / startup_recovery / admin force)
        ▼
[ Cron mon-fri 10:15 ET ]  id=aiem_paper_heartbeat  (watchdog Telegram)
        │
        ▼
[ Cron mon-fri 16:01 ET ]  id=aiem_paper_mtm
        ▼
┌──────────────────────────────────────────────────────────────────┐
│  _aiem_paper_mark_to_market (main.py:49677)                      │
│  • load OPEN rows; quotes + indicators + council                 │
│  • _rules_mtm_decision: hard -15% stop; ≥2 exit vs hold signals  │
│  • 14-day safety → CLOSED_EXPIRED                                │
│  • EXIT → _aiem_close_paper_trade_and_run_loop(mode=close)       │
│  • HOLD → UPDATE last_price/pnl (or needs_review if stale quote) │
│  • bg: RL pipeline + edge_filter log + maybe_run_ppo_training()  │
└──────────────────────────────────────────────────────────────────┘
        ▼
┌──────────────────────────────────────────────────────────────────┐
│  _aiem_close_paper_trade_and_run_loop (main.py:49202)             │
│  CAS: learning_loop_fired_at + status='OPEN'                     │
│  • UPDATE close fields                                           │
│  • audit_trace: log_outcome_for_trade                            │
│  • D2 stage 20 post_trade_analytics                              │
│  • trust EMA → signal_trust_weights + record_trust_update        │
│  • update_paper_thompson                                         │
│  • log_learning_update_step (ppo_trained=False here)             │
│  • D2 stages 21-23 learning_feedback / feedback_loop / memory    │
│  • D3 link_paper_trade_close                                     │
└──────────────────────────────────────────────────────────────────┘

PARALLEL (not the paper INSERT path):
[ Interval 2 min ]  id=candidate_intake_poll
  → PENDING_FULL_ANALYSIS rows from pick_candidates
  → diagram1_candidate_intake → orch.run_full_cycle()
  → _h_paper_trade is SHADOW ONLY ("Real execute via _aiem_paper_execute_today")
```

---

## 1. Scheduler job that triggers paper execute

| Field | Evidence |
|---|---|
| **Job id** | `aiem_paper_execute` |
| **Cron** | `mon-fri`, hour=9, minute=42, timezone=`_ET` |
| **Callable** | `lambda: _aiem_paper_execute_today(trigger_source="scheduled_942")` |
| **Lines** | `artifacts/stock-scanner-api/main.py:17683-17688` |

Related scheduled siblings (same block `17601-17820`):

| Job id | Cron | Function |
|---|---|---|
| `v3_discovery_premarket` | 08:00 | `_run_v3_discovery_premarket` |
| `macro_precompute` | 09:00 | `_run_macro_precompute` |
| `aiem_paper_heartbeat` | 10:15 | `_aiem_paper_heartbeat_check` |
| `aiem_paper_mtm` | 16:01 | `_aiem_paper_mark_to_market` |
| `aiem_paper_drift` | 16:35 | `_aiem_paper_drift_check` |
| `v3_learning_cycle` | 16:45 | `aiem_v3_learning.run_learning_cycle` |
| `v3_verification_daily` | 16:55 | `aiem_v3_verification.run_full_verification` |
| `aiem_exit_review` | 9–15 `*/30` | `aiem_exit_engine.review_open_positions` — **`ai_stock_picks` only, NOT `aiem_paper_trades`** (`aiem_exit_engine.py:194-198`) |

Other execute triggers: `startup_catchup` (~8805), `startup_recovery` (~8941), `admin_run_paper_today` (~21311).

---

## 2. `_aiem_paper_pick_candidates` — sources & discovery status gates

**Def:** `main.py:48075`

### Signal sources read

| # | Source key | Table / module | Lines |
|---|---|---|---|
| 1 | `conviction_stack` | `conviction_stack_watchlist` | 48203-48211 |
| 2 | `sweep` | `call_sweep_log` | 48213-48227 |
| 3 | `unusual_calls` | `unusual_calls_log` | 48229-48251 |
| 4 | `gap_volume` | `polygon_rvol_scan` | 48253-48270 |
| 4b | `washout_reclaim`, `momentum_continuation`, `thrust_pullback`, `building_thrust`, `gap_ignition` | `aiem_pre_move_signals` | 48272-48314 |
| 5 | `aiem_ai` | `ai_trade_log` (HIGH/EXTREME BULLISH) | 48316-48332 |
| 6 | `multi_signal` | `scan_result_cache` endpoint=`multi-signal` | 48334-48359 |
| 7 | `oi_buildup` | `oi_daily_snapshot` CTE | 48361-48399 |
| 8 | `washout_ignition` | `washout_ignition_signal` | 48401-48418 |
| 10 | `squeeze_reversion` | `aiem_squeeze_signals` | 48420-48447 |
| 11 | `aiem_v3_discovery` | `aiem_discovery_memory` via `get_todays_discoveries` / live scan + `aiem_v3_orchestrator` | 48452-48531 |
| 12 | `fear_premium_gex` | `options_structure_scan` (PUT) | 48533-48575 |
| 13 | `gap_down_distribution` | `polygon_rvol_scan` (SHORT) | 48577-48606 |

### Discovery **status** gates (hard)

| Signal | Gate | Evidence |
|---|---|---|
| `washout_ignition` | `aiem_signal_discoveries.id=9` must be `'validated'` | `48403-48418` |
| `squeeze_reversion` | `aiem_signal_discoveries` where `hypothesis_text='Short_Squeeze_Reversion'` must be `'validated'` | `48426-48445` |

v3 discovery uses **confidence ≥ 0.42**, not `aiem_signal_discoveries.status` (`aiem_v3_discovery.py:335-348`, pick call `48461`).

### Other pick-time gates

| Gate | Behavior | Lines |
|---|---|---|
| PortfolioCircuitBreaker | halt if tripped; **fail-open** on exception | 48103-48118 |
| Economic calendar | high-impact day → `[]`; **fail-open** on exception | 48120-48128 |
| FRED macro bias | soft score shaping | 48130-48142 |
| Drift learning gate | ×0.35 / ×0.70 from `drift_check_log` | 48144-48172 |
| News catalyst | remove high-risk; **fail-open** | 48658-48673 |
| Trust ≥5 outcomes | multiply score | 48675-48704 |
| Thompson | ×(0.5+sampled) | 48706-48737 |
| Macro risk-off | cap 10, options ×0.60 | 48739-48748 |
| EdgeFilter / OptionBBrain / Correlation / Liquidity / EventRisk | hard blocks; whole intel block **fail-open** | 48750-48914 |

**Writes from pick:** `aiem_candidate_rankings`, `aiem_candidate_queue`, `aiem_candidate_pipeline` (`PENDING_FULL_ANALYSIS`) — `48916-49047`.

---

## 3. `_aiem_paper_execute_today` — gates & writes

**Def:** `main.py:19071`

### Gates (order)

| Stage | Fail mode | Lines |
|---|---|---|
| NYSE trading day | skip | 19093-19095 |
| Date advisory lock | **fail-closed** | 19097-19125 |
| Ledger `try_claim` | skip if owned; **exception → fail-OPEN** (in-process lock only) | 19136-19154 |
| Threading lock | SKIPPED_LOCK_HELD | 19156-19179 |
| D3 **G0** | BLOCK path exists; default **SHADOW** = cosmetic | 19181-19287 |
| Already traded today | SKIPPED | 19365-19370 |
| Kill switch / daily loss / portfolio corr | halt on trip; **exception → proceed (fail-open)** | 19400-19468 |
| D3 **G1** | SHADOW default | 19506-19584 |
| Macro gate | hard block BEAR_SEVERE; err → proceed | 19586-19614 |
| Stage-4 revalidate | fail-open on many sub-checks | 19619-19628 |
| Per-ticker sizing | **fail-closed** allowlist `APPROVED` \| `PARAMS_NOT_CONFIRMED` | 19986-20084 |
| D3 **G6** PIT | SHADOW; exception **fail-open** | 19909-19963 |
| D3 **G2** / **G3** | SHADOW default; exception fail-closed BLOCK | 20543-20690 |
| Order dedup + live position cap | hard skip / fail-closed | 20706-20795 |

### What it writes

- `aiem_paper_execution_log` (RUNNING → SUCCESS/NO_CANDIDATES/…)
- `paper_trade_job_ledger` via recovery marks
- `aiem_paper_trades` INSERT (`20797-20820`): OPEN row with fill/sizing/audit_trace_id/candidate_id/execution_plan_id
- Thompson/entry_score backfill UPDATEs (`20875+`)
- `aiem_diagram2_trace_audit` via `_d2_run` / `record_terminal`
- Bus / governance ack / supervisor hooks / debate persistence

---

## 4. Mark-to-market path

| Field | Evidence |
|---|---|
| Schedule | `aiem_paper_mtm` — mon-fri **16:01** ET — `17776-17780` |
| Function | `_aiem_paper_mark_to_market` — `49677` |
| Exit rules | hard stop `pnl <= -15%` (`50009-50011`); ≥2 exit signals and more exit than hold (`50070-50076`); 14-day → `CLOSED_EXPIRED` (`50115-50121`) |
| Close helper | **`_aiem_close_paper_trade_and_run_loop(..., mode="close")`** — `50158-50161` |
| Hold path | UPDATE `last_price`/`pnl` only — **does not** close — `50181-50187` |
| Post-MTM batch | `_rl_pipeline_bg` → `aiem_rl_engine.run_full_rl_pipeline` + `maybe_run_ppo_training` — `50217-50300` |

Admin force MTM: ~`50551-50556`.

---

## 5. Close → learning loop — funnel completeness

**Intended single funnel:** `_aiem_close_paper_trade_and_run_loop` (`49202`), documented as the only supported close (`50769-50776`).

| Caller | Uses funnel? | Evidence |
|---|---|---|
| MTM exits | **YES** | `50159` |
| Admin `POST .../paper-trade/<id>/close` | **YES** (close + backfill modes) | `50810-50816` |
| Raw SQL `UPDATE aiem_paper_trades SET status=CLOSED...` in live server | **No production bypass found** | Main-server UPDATEs are fill flags (`49168`), thompson/meta (`20875`, `20939`), or the funnel itself (`49286`) |
| Email close | **Different table** `position_monitor` | `15664-15667` |
| `aiem_exit_engine` | **Different table** `ai_stock_picks` | `aiem_exit_engine.py:194-198` |
| ASE `close_paper_trade` | **Different table** `ase_paper_trades` | `aiem_strat_engine/paper_trader.py:250` |
| `alpha_train_pipeline` | Updates alpha labels only (not status) | `alpha_train_pipeline.py:111-115` |

**Residual risk:** Historical raw-SQL closes are handled via `mode="backfill"` CAS on `learning_loop_fired_at` (`49306-49331`). Any future raw SQL that sets `CLOSED*` **without** calling the funnel still bypasses learning until backfill.

---

## 6. Learning loop stages that actually fire

| Stage | Where | Fires? |
|---|---|---|
| **audit_trace / outcome** | `log_outcome_for_trade` in close funnel | **YES** if `audit_trace_id` set — `49335-49345` |
| **trust** EMA + `record_trust_update` + UPSERT `signal_trust_weights` | close funnel | **YES** — `49419-50507` |
| **Thompson** `update_paper_thompson` | close funnel | **YES** — `49509-49523` |
| D2 20–23 orchestration markers | close funnel (needs trace_id) | **YES** — `49347-49640` |
| Attribution + supervisor hooks 5/6 | close funnel | **YES** |
| **PPO** `maybe_run_ppo_training` | **MTM batch only**, not per close | **YES** after MTM (`50287-50293`); close logs `ppo_trained=False` (`49538`) |
| Full RL `run_full_rl_pipeline` | MTM batch | **YES** (`50220-50247`); stage 22 is marker only (`49571-49574`) |
| v3 learning cycle | separate 16:45 cron | **YES** (batch, not per-trade) |

---

## 7. Diagram 2 / orchestrator vs `run_full_cycle`

| Path | Role in paper trading |
|---|---|
| **Paper execute D2 wiring** | After sizing pass, `_d2_orch.execute_stage` stages **1–17** then **18–19** post-insert (`20377-21000`). Uses `get_orchestrator()` but **not** `run_full_cycle()`. Failures per stage are caught → **legacy insert still proceeds** (`20422-20436`, `20523-20524`). |
| **Paper close D2** | Stages **20–23** inside close funnel via same orchestrator (`49347-49640`). |
| **`run_full_cycle`** | Driven by `candidate_intake_poll` every 2 min (`18427-18440`) on `PENDING_FULL_ANALYSIS` rows written by pick (`49011-49047`). |
| **`_h_paper_trade`** | Explicit shadow stub — **does not INSERT** `aiem_paper_trades`; note: *"Real execute via _aiem_paper_execute_today() at 9:42 AM"* (`aiem_master_orchestrator.py:1590-1610`). |

**Conclusion:** Diagram 1 `run_full_cycle` is **wired as parallel analysis**, **UNWIRED as paper order placement**. Real paper opens only from `_aiem_paper_execute_today`.

---

## 8. Fail-open gaps / cosmetic gates / dormant producers

| Item | Classification | Evidence |
|---|---|---|
| D3 G0/G1/G2/G3/G6 default **SHADOW** | Cosmetic (log `would_block`, do not block) | `aiem_diagram3_governance.py:565,636-643`; comments in execute path |
| Ledger claim exception | Fail-open | `19152-19154` |
| Kill switch / DLL / PCR exceptions | Fail-open | `19418-19420`, `19443-19445`, `19466-19468` |
| Circuit breaker / econ / news / intel-gate exceptions | Fail-open | `48117-48118`, `48127-48128`, `48671`, `48913-48914` |
| G6 check exception | Fail-open | `19939` |
| Macro gate exception | Fail-open (proceed) | `19613-19614` |
| D2 `_d2_run` stage failures | Fail-open (trade still inserts) | `20434-20436`, `20523-20524` |
| Stage3 helper returns hardcoded CLEAR for ks/dll/pcr | Cosmetic relative to batch gates (real work is PIT/lookahead/missing) | `aiem_diagram2_stage_helpers.py:413-418` |
| `aiem_operational_controls` | Dormant / never imported on paper path | prior audit `AIEM_WIRING_AUDIT_2026-08-04.md` |
| Diagram1 `_h_paper_trade` | Dormant producer for real paper rows | orchestrator `1590-1610` |
| `aiem_exit_review` scheduler | Different product surface (`ai_stock_picks`) | `17762-17774` |
| Unvalidated discoveries (squeeze, washout_ignition) | Correctly gated OUT until `validated` | `48403-48445` |
| Module 2 `failing` not auto-retiring `validated` | Integrity gap (signals can keep feeding paper) | prior audit |

---

## WIRED / PARTIAL / UNWIRED table

| Stage | Status | Notes |
|---|---|---|
| Premarket v3 discovery cron → memory | **WIRED** | 08:00 + pick fallback |
| Macro precompute → execute macro gate | **WIRED** | 09:00 + hard BEAR_SEVERE |
| Scheduler `aiem_paper_execute` @ 9:42 | **WIRED** | |
| Pick multi-source aggregation | **WIRED** | 13+ sources |
| Discovery status gates (id=9 / squeeze) | **WIRED** | Only when `validated` |
| Trust / Thompson / drift re-rank | **WIRED** | Consumed at pick |
| Risk/intel gates at pick | **PARTIAL** | Logic present; exceptions fail-open |
| Execute serialization + ledger claim | **PARTIAL** | Lock fail-closed; claim exception fail-open |
| Kill / daily loss / portfolio corr | **PARTIAL** | Trip halts; errors proceed |
| Position sizing gate | **WIRED** | Fail-closed allowlist |
| D3 G0–G3 / G6 | **PARTIAL** | Code wired; SHADOW = cosmetic |
| INSERT `aiem_paper_trades` | **WIRED** | |
| Diagram 2 stages 1–19 on execute | **PARTIAL** | Instrumented; stage FAIL does not block insert |
| Diagram 1 `run_full_cycle` → paper INSERT | **UNWIRED** | Shadow note only |
| Candidate intake poll | **WIRED** (analysis only) | Does not open paper trades |
| MTM @ 16:01 + rules exits | **WIRED** | |
| Close → `_aiem_close_paper_trade_and_run_loop` | **WIRED** | MTM + admin |
| Trust update on close | **WIRED** | |
| Thompson update on close | **WIRED** | |
| audit_trace outcome on close | **WIRED** | Needs non-null `audit_trace_id` |
| PPO training | **PARTIAL** | MTM batch only; needs ≥10 buffer rows |
| RL full pipeline | **PARTIAL** | MTM batch; stage 22 is marker |
| Raw SQL close bypass on live path | **WIRED** (absent) | Funnel enforced in server code; backfill exists for legacy |

---

## File index (absolute)

- `/workspace/artifacts/stock-scanner-api/main.py` — live paper path
- `/workspace/artifacts/stock-scanner-api/aiem_master_orchestrator.py` — `execute_stage` / `run_full_cycle` / shadow `_h_paper_trade`
- `/workspace/artifacts/stock-scanner-api/aiem_diagram2_stage_helpers.py` — D2 stage helper checks
- `/workspace/artifacts/stock-scanner-api/aiem_closed_loop_learning.py` — trust / Thompson / PPO
- `/workspace/artifacts/stock-scanner-api/aiem_diagram3_governance.py` — G0–G6 SHADOW defaults
- `/workspace/artifacts/stock-scanner-api/diagram1_candidate_intake.py` — Diagram 1 poller
- `/workspace/artifacts/stock-scanner-api/aiem_exit_engine.py` — `ai_stock_picks` only
- `/workspace/artifacts/stock-scanner-api/aiem_v3_discovery.py` — discovery memory reads
