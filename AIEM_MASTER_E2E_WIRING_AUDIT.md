# AIEM Master End-to-End Wiring Audit — POST-FIX STATUS

**Date:** 2026-08-04 (updated after full wiring pass)  
**Branch:** `cursor/aeim-wiring-audit-f9ec`  
**Live entry:** `artifacts/stock-scanner-api/main.py`

---

## Executive verdict (after fixes)

| Path | Before | After |
|---|---|---|
| Stock paper open→close→trust/Thompson | WIRED | **WIRED** |
| ASE strat paper execute | PARTIAL (open gate) | **WIRED** via `execute_selected_paper_trade_fail_closed` |
| Options Stage 8 kill switch | UNWIRED | **WIRED** (`kill_switch_reason`) |
| Operational controls schema/recover | UNWIRED | **WIRED** (strat + options startup) |
| Modules 1–3, 5–7 | WIRED | **WIRED** |
| Module 4 human gate | PARTIAL (auto-retire bypass) | **WIRED** — auto-retire recommends only; UI at `/module4` |
| D3 governance | PARTIAL SHADOW | **PARTIAL BY DESIGN** — still SHADOW; modes API + UI badge |
| Options D12 score | hardcoded 50 | **WIRED** — graded `oe_trade_records` WR (n≥5) |
| Telegram ledger coverage | PARTIAL | **WIRED** — options / backup / ASE strat now log |
| Dashboard gaps | several | **WIRED** — Trace, Module4, options paper panel, FORCE, SSE |

---

## Intentionally NOT flipped

**Two paper books remain separate tables.** UI shows both; stock vs options engines stay separate.

**Live brokerage reconcile** still needs a real broker API — paper self-reconcile is what runs today.

**D3 G0/G2/G3 are now ENFORCE** (integrity wiring this pass). De-escalate via `set_d3_checkpoint_mode` if paper flow is too strict.

---

## What was fixed in this PR

### Backend
1. Options + backup + ASE Telegram → `alert_gateway.log_alert`
2. ASE paper path → `aiem_operational_controls.execute_selected_paper_trade_fail_closed`
3. `install_schema` + `recover_and_reconcile` on strat/options startup
4. Kill-switch gate before options Stage 8 persist
5. D12 from real graded options trade history
6. Auto-retire no longer sets `status='retired'` — recommends + Telegram for Module 4
7. Admin routes: paper-job-ledger, daily-pipeline-runs, governance-decisions, telegram-alerts, trace-explorer, governance-modes, scheduler force

### Dashboard
1. Scheduler FORCE wired
2. SSE on Command Center
3. Alerts → telegram ledger
4. Decisions → governance decisions + D3 mode badge
5. Options → daily pipeline runs
6. Paper Trades → job ledger + options paper (`oe_trade_records`)
7. Trace Explorer (`/trace`)
8. Module 4 Signal Gate (`/module4`)
9. Orphan `Dashboard.tsx` redirects to `/trace`

---

## Remaining known limits (not wiring bugs)

1. **Live brokerage positions** — paper reconciler is live; real broker API still required for live-account reconcile  
2. **Minute-bar intraday features** — RF trains/scores on daily OHLCV proxies until minute bars are ingested  
3. **Two paper books remain separate tables** — UI shows both; engines stay separate by design  

### Infrastructure + integrity (wired this pass)
- **ml_training_runs** table + `GET /admin/ml-training-runs` + Learning panel bound  
- **Adaptive policies** API + Learning panel (trust weights / Thompson / retrain)  
- **position_reconciler** paper path scheduled 16:10 ET (never mock)  
- **Intraday RF** train/promote/load + Sunday job; orchestrator uses live model when present  
- **Deep RL** train-from-paper + Sunday job; promote when held-out reward > 0  
- **Orchestrator `_h_paper_trade`** real INSERT via unique-constraint-safe helper  
- **D2 fail-closed** on mandatory stage FAIL; **G0/G2/G3 → ENFORCE** at startup  
- **Discovery HMAC** columns + sign on `mkt_save_discovery`  
- **PPO** close-funnel `maybe_run_ppo_training` + honest `ppo_trained` flag  
- **Structural TG gates** bounce/pullback/exhaust require `validated` discovery  
- **Research → hypothesis bridge** Sunday 05:30 ET (never auto-validates)  

---

## Companion reports

- `AIEM_PAPER_TRADE_E2E_WIRING_AUDIT.md`
- `AIEM_DISCOVERY_MODULES_GATE_AUDIT.md`
- `AIEM_E2E_WIRING_AUDIT.md`
- `AIEM_WIRING_AUDIT_2026-08-04.md`
