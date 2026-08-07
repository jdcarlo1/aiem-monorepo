# AIEM Discovery + Module 1–7 Gate Integrity Audit

**Date:** 2026-08-04  
**Scope:** `/workspace/artifacts/stock-scanner-api/`  
**Naming:** This audit covers the **gate-integrity** Module 1–7 stack (outcome → decay → promotion → human gate → pattern discovery → rediscovery → sector rotation). A separate, differently numbered “discovery-cycle” Module 1–7 (GP / Thompson / SGD / adversarial / etc.) exists in `main.py` ~L2656–3337 and is **not** the subject of this report except where noted.

**Verdicts:** `WIRED` = scheduled + writes intended tables + status/alert path consistent. `PARTIAL` = code exists and is invoked but has integrity gaps, bypasses, or incomplete live coupling. `UNWIRED` = missing, never scheduled, or never reaches live alerts.

---

## Summary tables

### Module 1→7 (gate integrity)

| Module | Name | Schedule | Writes | Changes `aiem_signal_discoveries.status`? | Verdict |
|---|---|---|---|---|---|
| **1** | Outcome Tracker | Daily 02:00 ET | `aiem_discovery_outcomes` | **No** | **WIRED** |
| **2** | Decay & Failure Analyzer | Sun 02:30 ET | `aiem_module2_evaluations` | **No** (by design) | **WIRED** |
| **3** | Hypothesis Promotion Evaluator | Sun 03:00 ET | `aiem_module3_evaluations` | **No** (by design) | **WIRED** |
| **4** | Human Approval Gate | On-demand routes only | `aiem_signal_actions` + status UPDATE | **Yes, only on human POST** | **PARTIAL** |
| **5** | Pattern Discovery Engine | Sun 04:00 ET | `aiem_signal_discoveries` (`hypothesis`) + `aiem_module5_test_results` | Inserts only (`hypothesis`) | **WIRED** |
| **6** | Rediscovery Engine | Sun 04:15 ET + retire trigger | Descendant `hypothesis` rows + `aiem_rediscovery_runs` | Inserts only | **WIRED** |
| **7** | Sector Rotation | Mon–Fri 17:00 ET | `aiem_sector_rotation` / alerts log; live ±0.5 pts in L8 scoring | N/A (not discovery-status) | **WIRED** |

### Discovery → live-alert path

| Path segment | Verdict | Notes |
|---|---|---|
| Agent/`mkt_save_discovery` → `aiem_signal_discoveries` (`validated`) | **WIRED** | Hard OOS/WR/n/Bonferroni gates |
| Research grid → discoveries | **PARTIAL** | Writes `aiem_research_insights` only; needs agent/`mkt_save_discovery` to promote |
| Module 5/6 → `hypothesis` rows | **WIRED** | Never auto-`validated` |
| Module 1 outcomes → Module 2/3 | **WIRED** | Both consume `aiem_discovery_outcomes` |
| Module 2/3 → Module 4 pending | **PARTIAL** | Pending is **pull-computed** from M2 `failing`/`decaying` only; M3 `promote_ready` is TG + manual `promote`, not in `get_pending_actions` |
| Module 4 → status change → suppress live | **PARTIAL** | Human path correct; **weekly auto-retire bypasses M4**; structural scanners mostly **ignore** status |
| Discovery id=9 → washout live alert | **WIRED** (gate correct) | Live scan+email suppressed unless `validated` |
| Structural scanners (bounce/pullback/exhaust) → TG | **PARTIAL / ungated** | Live TG without discovery-status check |
| Squeeze → paper | **WIRED** (gated) | Paper path requires `validated`; `run_scan` records-only |
| Provenance/signing on discovery rows | **UNWIRED** | HMAC only on admin proof / orchestrator packets / D2–D3 traces |

---

## 1. How discoveries are created → `aiem_signal_discoveries`

### 1a. Research agent tool: `mkt_save_discovery`

- Tool impl: `main.py:31197–31395` (`_mkt_tool_save_discovery`)
- Registered in tool map: `main.py:39954`
- Tool schema: `main.py:41384+`
- **Hard gates before INSERT:** `oos_edge > 0`, `signal_win_rate >= 54`, `signal_n >= 200`, Bonferroni `p_value`, redundancy Jaccard check
- **Write:** `INSERT … status='validated'` at `main.py:31371–31375`
- Chat/agent prompt instructs: OOS then save (`main.py:6861`, `43433`)

### 1b. Continuous research / indicator grid (does **not** write discoveries directly)

- Loop: `_mkt_continuous_research_loop` `main.py:35707–35767` (nights/weekends)
- Grid battery: `_mkt_indicator_grid_battery` `main.py:35326+`
- Explicit comment: findings go to `aiem_research_insights`, **never** straight to `aiem_signal_discoveries` (`main.py:35222–35223`, `35401` type=`indicator_grid_finding`)
- Agent must later run OOS + `mkt_save_discovery`

### 1c. Module 5 batch discovery

- `aiem_module5_discovery.py:503–514` → `INSERT … status='hypothesis'`, `invented_indicator='module5_fisher_bh'`
- Scheduled Sunday 04:00 ET: `main.py:5779–5814`

### 1d. Module 6 rediscovery

- `aiem_module6_rediscovery.py:360–373` → descendant `status='hypothesis'`, lineage via `parent_signal_id` / `generation`
- Scheduled Sunday 04:15 ET: `main.py:5816–5851`
- Also triggered on Module 4 `retire`: `main.py:69185–69197`

### 1e. `aiem_process` gap hypotheses

- `aiem_process.py:1882–1976` (`aiem_write_signal_discoveries`) → `status='hypothesis'` from miss/pick gap patterns

### 1f. Structural scanner registration (startup / deferred init)

| Scanner | Register site | Default status |
|---|---|---|
| Oversold bounce | `aiem_selloff_reversion.py:984–1028` | forces `'hypothesis'` |
| Short squeeze | `aiem_short_squeeze.py:888–965` | `'hypothesis'` (never auto-promote, L925) |
| Pullback re-entry | `aiem_pullback_reentry.py:1214–1236` | `'hypothesis'` |
| Momentum exhaustion | `aiem_momentum_exhaustion.py:966–990` | `'hypothesis'` |

Deferred init hooks: `main.py:4224–4225`, `60607–60610`.

### Schema

- Table create: `main.py:30000–30021` (`aiem_signal_discoveries`)

---

## 2. Module 1 — Outcome checker

| Item | Evidence |
|---|---|
| Function | `_mkt_check_discovery_outcomes` `main.py:36214–36386` |
| Job wrapper | `_mkt_run_discovery_outcome_check` `main.py:36389–36420` (isolated_research_scope) |
| Schedule | Daily **02:00 ET**, id=`discovery_outcome_check` `main.py:5710–5726` |
| Admin trigger | `POST /stock-api/admin/run-discovery-outcome-check` `main.py:51094–51107` |
| Table | `aiem_discovery_outcomes` created `main.py:36159–36190`; insert `main.py:36195–36211` |
| Inputs | Rows with `status IN ('validated','hypothesis','retired')` `main.py:36259` |
| Writes | One outcome row per discovery per run: either `retestable=True` + realized stats, or `retestable=False` + `skip_reason`/`skip_code` |
| Status change? | **No** — outcomes only |

**Adapters (by discovery id):**

| IDs | Adapter | Lines |
|---|---|---|
| 9 | washout ignition detector reuse | `36308–36311`, `30431+` |
| 2,3,4,5 | chain SQL | `36313–36321`, `30315+` |
| 1 | accumulation rolling | `36323–36331`, `30408+` |
| 6 / else | generic test + lag/delta fallback (7/8) | `36333–36351` |

---

## 3. Module 2 — Decay

| Item | Evidence |
|---|---|
| Module | `aiem_module2_decay.py` (docstring L1–26: does **not** promote/retire) |
| Import | `main.py:401–405` as `_m2` |
| Schedule | Sun **02:30 ET**, id=`module2_decay_check` `main.py:5728–5750` |
| Admin | `POST` run + `GET /aiem/module2-status` `main.py:68890–68928` |
| Writes | Upsert `aiem_module2_evaluations` `aiem_module2_decay.py:511–548` |
| Verdicts | `failing` / `decaying` / `holding` / `insufficient_n` when `evaluable_now` |
| Status change? | **No** — evaluations only |

---

## 4. Module 3 — exists / scheduled / wired?

**Yes — exists, scheduled, wired.**

| Item | Evidence |
|---|---|
| Module | `aiem_module3_promotion.py` |
| Import | `main.py:411–415` as `_m3` |
| Schema init | `main.py:38572–38578` → `aiem_module3_evaluations` |
| Schedule | Sun **03:00 ET**, id=`module3_promotion_check` `main.py:5752–5778` |
| Behavior | Classifies `status='hypothesis'` only (`aiem_module3_promotion.py:220–224`); upserts evaluations; **never** changes discovery status (doc L6–7) |
| Alert | TG on `promote_ready` / `hypothesis_failing` via `_build_module3_tg_message` `main.py:5769–5770`, `68933–68951` |
| Admin | `POST /admin/run-module3-promotion`, `GET /aiem/module3-status` |

Also invoked from discovery-cycle post-process as `_dc_module5_promotion_check` → `run_module3()` (`main.py:2828–2849`) — naming collision with gate Module 5.

---

## 5. Module 4 — human gate

| Route | Purpose | Lines |
|---|---|---|
| `GET /stock-api/admin/module4-pending` | List actionable M2 `failing`/`decaying` | `69112–69133` |
| `POST /stock-api/admin/module4-approve` | Apply retire/downgrade/keep/promote | `69136–69202` |
| `GET /stock-api/admin/module4-history` | Audit trail | `69205+` |

**Core invariants** (`aiem_module4_gate.py:8–21`): never auto-changes status; every transition needs POST.

**Does anything auto-enqueue failing signals?**

- **No durable enqueue queue.** `get_pending_actions` (`aiem_module4_gate.py:74–117`) **computes** pending on read from `aiem_module2_evaluations` where `decay_verdict IN ('failing','decaying')` and no later action.
- Module 3 `promote_ready` / `hypothesis_failing` are **not** included in `get_pending_actions` (no `module3` reference in `aiem_module4_gate.py`). Humans are told via TG to POST `module4-approve` with `promote`/`retire`.
- Module 2/3 do **not** auto-call `apply_action`.

**Integrity conflict — auto-retire bypasses Module 4:**

- `_mkt_auto_retire_decaying_discoveries` `main.py:36635–36697` directly `UPDATE … status='retired'` for validated signals with edge drift
- Scheduled Sun **18:00 ET** `main.py:8199–8205` (`aiem_auto_retire_weekly`)
- This contradicts Module 4’s “NEVER changes status automatically” invariant

---

## 6. Module 5 / 6 / 7 — scheduled? wired to live?

### Module 5 — **WIRED**

- Schedule Sun 04:00 ET `main.py:5779–5814`
- Admin `POST /admin/run-module5-discovery`, `GET /aiem/module5-status` `68997–69039`
- Inserts `hypothesis` discoveries; TG if `n_new > 0`
- Live alerts: only after Module 1→3→4 promote to `validated` **and** a live scanner exists for that condition (usually none for Fisher grid patterns)

### Module 6 — **WIRED**

- Schedule Sun 04:15 ET `main.py:5847–5851`
- Admin status/run routes `69045–69074`
- Retire-trigger background batch `main.py:69185–69197`
- Same: descendants start as `hypothesis`; no auto live alert

### Module 7 — **WIRED** (live scoring, not discovery-status)

- Schedule Mon–Fri **17:00 ET** `main.py:5908–5912`
- Writes sector snapshots / tier alerts; TG for tier≥2 `main.py:5853–5903`
- **Live coupling:** L8 conviction scoring applies ±0.5 pts from Tier-3 sectors `main.py:24341–24373` via `_m7.get_all_tier3_sectors`
- Admin `POST /admin/run-module7-scan`, `GET /aiem/module7-status` `69080–69106`

---

## 7. Discovery IDs — live scanners vs record-only

### Known research discoveries (ids 1–9) — Module 1 adapter map

| ID | Shape | Live scanner that fires alerts from this discovery? | Status-gated? |
|---|---|---|---|
| **1** | Quiet accumulation (rolling) | **Record/retest only** — no dedicated live alert scanner wired to id=1 | N/A |
| **2,3** | 3-day catalyst→inside→gap chain | **Record/retest only** (S7c BigCat+InsideDay+Gap email is a **separate** path, not gated on these ids) | N/A |
| **4** | Same-day gap-down reversal | Record/retest only | N/A |
| **5** | Prior washout → flat gap | Record/retest only | N/A |
| **6** | Generic field_min/max | Record/retest only | N/A |
| **7,8** | Indicator lag/delta | Record/retest only | N/A |
| **9** | Washout Ignition state machine | **LIVE** `_scan_washout_ignition_signal` `67823+`; email 08:45 ET via `washout_ignition` schedule | **Yes** — live path requires `status='validated'` (`67865–67883`); backtest exempt |

**id=9 paper path also gated:** `main.py:48401–48418`. Comment at `18774–18775` notes status is “never true” in production → effectively suppressed.

### Structural scanners (separate discovery rows by `hypothesis_text`)

| Scanner | Live schedule | Writes signal table | TG / email | Discovery status gate on live path? | Paper gate? |
|---|---|---|---|---|---|
| Bounce (`Oversold_Bounce_Uptrend`) | 10:00 & 14:00 ET `7955–7967` | yes | **TG on CONFIRMED** `aiem_selloff_reversion.py:622–628` | **NO** | not in paper gate list |
| Squeeze (`Short_Squeeze_Reversion`) | 10:15 & 14:15 `7987–7998` | `aiem_squeeze_signals` | record-oriented `run_scan` (no TG in module) | N/A for TG | **YES** `48420–48445` |
| Pullback Module L | 10:30 & 14:30 `8010–8021` | yes | **TG on CONFIRMED** `aiem_pullback_reentry.py:643–645` | **NO** | no |
| Exhaustion Module M | 10:45 & 14:45 `8033+` | yes | **TG** `aiem_momentum_exhaustion.py:697` | **NO** | no |
| Washout Ignition id=9 | 08:45 ET | `washout_ignition_signal` | email+owner chart | **YES** | **YES** |

---

## 8. Provenance / signing

| Mechanism | Wired to discoveries? | Evidence |
|---|---|---|
| `aiem_provenance.sign_payload` | **No** | Imported `main.py:450–453`; only used for `/admin/aiem-signed-proof` sector_heat (`24697–24720`). **Zero** calls around `_mkt_tool_save_discovery` / Module 5/6 inserts |
| Orchestrator provenance stage | Session/trade packets | `aiem_master_orchestrator.py` `_h_provenance` ~L1646 |
| Diagram-2/3 hash chains | Trace/governance | `aiem_diagram2_trace_audit.py`, `aiem_diagram3_governance.py` |
| `agent_provenance.log_write` | Intended for agent DB writes | `agent_provenance.py:61+`; **not** called from `_mkt_tool_save_discovery` |

**Conclusion:** provenance/signing is wired to **admin proof / sessions / traces**, not to discovery row creation.

---

## 9. Washout ignition & other scanners — status-gated correctly?

| Scanner | Correctly status-gated? | Evidence |
|---|---|---|
| **Washout Ignition (id=9)** | **YES** for live scan+alert and paper | `67865–67883`, `48401–48418`; fail-closed on DB error |
| **Squeeze** | **YES** for paper; live scan still **records** while `hypothesis` | `48420–48445`; register forces hypothesis `aiem_short_squeeze.py:925` |
| **Bounce** | **NO** | `run_scan` sends TG without reading discovery status `aiem_selloff_reversion.py:550–628`; schedule always fires `7955–7967` |
| **Pullback** | **NO** | TG without status check `aiem_pullback_reentry.py:643–645` |
| **Exhaustion** | **NO** | TG without status check `aiem_momentum_exhaustion.py:697` |
| Intelligence layer decay check | **Weak / PARTIAL** | Looks for status in `('rejected','invalid')` only — not `retired`/`hypothesis`/`failing` (`aiem_intelligence_layer.py:526–535`) |

---

## Cross-cutting integrity issues

1. **Module 4 vs auto-retire:** Human gate claims exclusive control; weekly `_mkt_auto_retire_decaying_discoveries` mutates status directly (`36686`, scheduled `8200–8205`).
2. **Module 4 pending omits Module 3 promotions:** `promote_ready` is TG-only; not in `get_pending_actions`.
3. **Live alert vs discovery status:** Only washout (and squeeze paper) honor `validated`; bounce/pullback/exhaust fire live while registered as `hypothesis`.
4. **Research → live gap:** Grid findings stay in `aiem_research_insights` until agent/`mkt_save_discovery`; Module 5/6 hypotheses need M1→M3→M4 before `validated`, and still usually lack a live scanner.
5. **Naming collision risk:** Discovery-cycle “Module 5” = promotion check calling `aiem_module3_promotion`; gate “Module 5” = pattern discovery engine.

---

## File index (primary)

| File | Role |
|---|---|
| `artifacts/stock-scanner-api/main.py` | Schedules, save/outcome/auto-retire, washout scanner, routes, L8 M7 bonus |
| `artifacts/stock-scanner-api/aiem_module2_decay.py` | Module 2 |
| `artifacts/stock-scanner-api/aiem_module3_promotion.py` | Module 3 |
| `artifacts/stock-scanner-api/aiem_module4_gate.py` | Module 4 |
| `artifacts/stock-scanner-api/aiem_module5_discovery.py` | Module 5 |
| `artifacts/stock-scanner-api/aiem_module6_rediscovery.py` | Module 6 |
| `artifacts/stock-scanner-api/aiem_module7_sector_rotation.py` | Module 7 |
| `artifacts/stock-scanner-api/aiem_provenance.py` | HMAC signing (not on discoveries) |
| `artifacts/stock-scanner-api/aiem_process.py` | Gap → hypothesis discoveries |
| `artifacts/stock-scanner-api/aiem_selloff_reversion.py` | Bounce live + register |
| `artifacts/stock-scanner-api/aiem_short_squeeze.py` | Squeeze scan + register |
| `artifacts/stock-scanner-api/aiem_pullback_reentry.py` | Module L |
| `artifacts/stock-scanner-api/aiem_momentum_exhaustion.py` | Module M |
