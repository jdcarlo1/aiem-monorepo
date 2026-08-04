# AIEM Wiring Audit — 2026-08-04

**Scope:** Live entry points under `artifacts/stock-scanner-api/` + `artifacts/aiem-dashboard/`  
**Standard:** Raw evidence only. “Unwired by design” is labeled separately from accidental gaps.

---

## Fixed in this PR

| Gap | Evidence | Fix |
|---|---|---|
| Scheduler FORCE button was dead UI | `Scheduler.tsx` had `<button>FORCE</button>` with no `onClick` / no API | Wired to `POST /stock-api/admin/scheduler-jobs/<job_id>/force` |
| SSE hook never mounted | `use-event-stream.ts` exported `useEventStream`; zero page imports | Mounted on Command Center with live event strip |
| Missing admin routes (gap analysis P1/P2) | No routes for paper-job-ledger, daily-pipeline-runs, governance-decisions, telegram-alerts, trace-explorer | Added all five under `main.py` dashboard admin section |
| Alerts page claimed no telegram ledger | UI note said dispatch logs “not stored”; `telegram_alert_ledger` exists | Alerts page now reads `/admin/telegram-alerts` |
| Root `aiem_security.py` was 0 bytes | Landmine duplicate vs live `artifacts/stock-scanner-api/aiem_security.py` | Root stub re-exports the live module |

---

## Still unwired / incomplete (not fixed here)

### High — integrity / safety

1. **Module 2 `failing` does not auto-retire live signals**  
   Module 4 exists (`/admin/module4-*`) but only acts on human `POST` approve. Signals with `decay_verdict=failing` can stay `validated` and keep firing (`AIEM_OPEN_ITEMS.md`).

2. **`aiem_operational_controls.py` never imported**  
   Kill switches, fail-closed paper execute wrapper, recover/reconcile — usage examples are commented out; zero callers outside the file.

3. **D3 governance checkpoints default to `SHADOW`**  
   SHADOW logs `would_block` but does not block. Intentional until admin flips to `ENFORCE`.

### Medium — stubs / dormant producers

4. **Orchestrator stub handlers** (`aiem_master_orchestrator.py`)  
   `_h_gaussian_process`, `_h_signal_drift_monitor`, `_h_security`, literature/hypothesis handlers often only probe metadata / `get_unreviewed_briefs()` — not full execution.

5. **`literature_scanner.scan_and_save` never scheduled**  
   Needs external `search_fn`; orchestrator only reads unreviewed briefs.

6. **`position_reconciler.reconcile_positions` intentionally dormant**  
   Documented; risk-gate mismatch check always passes because `reconciliation_log` is never populated.

7. **Options D12 historical performance hardcoded to 50**  
   `aiem_options_pipeline.py` ~L312: `scores["D12_historical_performance"] = 50`.

8. **Root orphans (not in artifact.toml services)**  
   `aiem_autonomous.py`, `aiem_chat_demo.py`, `aiem_standalone_scanner.py`, etc. are not launched by production services. Live process is `aiem_process.py` via wrapper. Root `main.py` is a hello stub — real server is `artifacts/stock-scanner-api/main.py`.

9. **`OE_SCHEDULER_ENABLED` fail-closed**  
   Options scheduler does not start jobs unless env flag is set — ops footgun.

10. **Provenance on discoveries**  
    Signing is wired for chat/sessions & Diagram-2 traces, not for `aiem_signal_discoveries` rows (`AIEM_OPEN_ITEMS.md`).

### Low / by design

11. **Tool map intentional exclusions** — 10 tools in `_build_aiem_tool_map` are listed in `_TOOL_REGISTRY_INTENTIONAL_EXCLUSIONS` and correctly omitted from OpenAI schema (not a sync bug).

12. **Probability engine isolation** — not imported by `main.py` by design (own scheduler service).

13. **`check_model_swap_wiring.sh`** — greps root `*.py` only; `model_swap_patches` retired in favor of `online_learning.py`. Script gives a stale picture.

---

## Entry-point map (correct)

| Process | Actual command |
|---|---|
| stock-api | `stock_api_wrapper.sh` → `artifacts/stock-scanner-api/main.py` |
| aiem-process | `aiem_process_wrapper.sh` → `aiem_process.py` |
| aiem-telegram | `notifier_wrapper.sh` → root `aiem_telegram_notifier.py` |
| options-pipeline-scheduler | `aiem_options_scheduler.py` (needs `OE_SCHEDULER_ENABLED`) |
| probability-engine-scheduler | `aiem_probability_engine/daily_scheduler.py` |
| ase-strat-scheduler | `aiem_strat_scheduler.py` |

---

## Recommended next wiring (priority order)

1. Wire `aiem_operational_controls` into options paper-execute path (fail-closed).  
2. Close Module 4 loop: surface Module 2 `failing` in pending queue automatically (still human-approve).  
3. Replace D12 placeholder with real historical win-rate once enough graded options outcomes exist.  
4. Decide go/no-go on `literature_scanner` search API before scheduling `scan_and_save`.
