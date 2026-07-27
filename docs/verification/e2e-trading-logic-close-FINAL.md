# E2E Trading Logic Verification — Close-Out Record

**Directive ref:** Directive_EndToEnd_TradingLogicVerification_OE_AIEM_2026-07-27  
**Date closed:** 2026-07-27  
**Commit:** see git log — "fix: E2E trading-logic close-out — items 1 & 2"

---

## Item 1 — Stage 11 AIEM v3 discovery revalidation

### Finding

`aiem_v3_discovery` was never in `_RV_PASS_THROUGH_SRCS`. The directive's characterisation of it as "PASS_THROUGH" was based on the docstring comment "Full module re-run not feasible at exec time" — which was the explanation for WHY a DB check is used instead of a live re-run, not the routing decision. The actual dispatch has always been section D (DB-backed sources via `_rv_db_meta`).

### What the revalidation checks

```sql
SELECT DISTINCT ticker FROM aiem_decision_history
WHERE ticker = ANY(%(tickers)s)
  AND decision_date = (CURRENT_TIMESTAMP AT TIME ZONE 'America/New_York')::date
  AND decision IN ('BUY', 'SMALL_BUY')
  AND final_confidence >= 0.42
```

- **Direction check:** `decision IN ('BUY', 'SMALL_BUY')` — only bullish decisions pass  
- **Min-confidence check:** `final_confidence >= 0.42` — the original AIEM v3 admission threshold  
- **Staleness bound:** `decision_date = today ET` — no yesterday decision can pass; bounded to same calendar day

### Why DB (not live re-run)

Full AIEM v3 re-run at execution time is not feasible — the module runs a 7-layer scoring chain over 200+ tickers, takes ~5 minutes, and involves API calls to Polygon + Tradier that would violate rate limits if called twice in the same 9:35 AM window. The DB row written by `run_orchestrator()` → `store_decisions()` in the same pipeline run is the authoritative current-state record. This is option (a) from the directive: "re-check min_confidence/direction against current data."

### Code change

Rewrote the function docstring lead for `aiem_v3_discovery` from "Full module re-run not feasible at exec time, but..." to "DB revalidation: aiem_decision_history / decision IN (BUY,SMALL_BUY) [direction check] / + final_confidence>=0.42 [min_confidence check] / + decision_date=today ET [staleness bound]..." so the routing intent is unambiguous.

### Raw evidence — SEQ=153

```
verified_run.sh SEQ=153  exit_code=0  PASS=5 FAIL=0
entry_hash: d40bc4e646626b676d2ed5af72599f65034008b10a0b7201a65d91c7d2533ae3

--- grep: _RV_PASS_THROUGH_SRCS contents (shows: conviction_stack / multi_signal /
    washout_ignition / squeeze_reversion — aiem_v3_discovery NOT present) ---
PASS: aiem_v3_discovery NOT in _RV_PASS_THROUGH_SRCS

--- grep: _rv_db_meta at line 18182 ---
"aiem_v3_discovery": (_rv_valid_v3, "decision BUY/SMALL_BUY + confidence>=0.42 today (aiem_decision_history)")
PASS: aiem_v3_discovery wired into _rv_db_meta

--- grep: updated docstring leads with 'DB revalidation' ---
PASS: docstring leads with 'DB revalidation'

--- SQL negative control: FAKEXYZ_NEGCTRL ---
rows returned: []   (0 rows — would be REJECTED; aiem_decision_history today rows: 0)
PASS: 0 rows — FAKEXYZ_NEGCTRL would be REJECTED by DB check
PASS: all aiem_v3_discovery candidates rejected today (no BUY/SMALL_BUY rows in DB)
```

---

## Item 2 — Unrecognized-source fail-open with durable log

### Old state (gap)

Section E logged a WARNING to stdout and attempted a bus publish (wrapped in bare `except Exception: pass` — ephemeral, lost on restart). No DB write. The stdout line is gone after any process restart; the bus event is in-memory only.

### Fix implemented

Added a **primary durable DB write** to `aiem_execution_revalidation_log` with `action='PASS_UNRECOGNIZED_SOURCE'` **before** the pick is approved. This executes unconditionally; only a DB connection failure (non-fatal, logged) can prevent it. The bus publish is now a secondary best-effort signal.

Also fixed a schema drift: the `action` column was `VARCHAR(16)` in the live DB (too narrow for the 24-char value `PASS_UNRECOGNIZED_SOURCE`) while the `CREATE TABLE` specifies `VARCHAR(32)`. Added `ALTER COLUMN action TYPE VARCHAR(32)` to the existing schema-setup block so it runs idempotently on every restart.

Policy chosen: **fail-open with durable log** (not fail-closed). Rationale: fail-closed on an unrecognized source would silently block any new signal source that hasn't yet been registered in the dispatch — too aggressive for a living system. The DB log makes every pass inspectable, alertable, and persistent.

### Raw evidence — SEQ=156

```
verified_run.sh SEQ=156  exit_code=0  PASS=5 FAIL=0
entry_hash: ac826c7e8ae728a40a0b74bf4177dbe821766c6804f484faf8e06d0d153f4f82

--- grep: PASS_UNRECOGNIZED_SOURCE at line 18360 ---
PASS: PASS_UNRECOGNIZED_SOURCE action constant present in code

--- grep: _rv_unk_c block at lines 18349-18367 ---
PASS: DB connection block present in section E

--- negative control INSERT + read-back ---
ticker='TSTUNK' source='totally_unknown_source_negctrl_xyz'
PASS: DB row written — id=1 ticker=TSTUNK source=totally_unknown_source_negctrl_xyz
  action=PASS_UNRECOGNIZED_SOURCE
  failed_checks=source '...' not in dispatch registry — add to _stage4_execution_revalidate; passing through

--- grep: _rv_approved.append at line 18384 ---
PASS: fail-open preserved — _rv_approved.append present after DB log

--- revalidation_log summary ---
total=1  PASS_UNRECOGNIZED_SOURCE=1
```

### SQL confirmation (raw)

```sql
SELECT id, ticker, source, action, failed_checks
FROM aiem_execution_revalidation_log
WHERE action = 'PASS_UNRECOGNIZED_SOURCE';
-- id=1, TSTUNK, totally_unknown_source_negctrl_xyz, PASS_UNRECOGNIZED_SOURCE, ...
```

---

## Item 3 — AIEM D1/D2/D3 formula-level and decision-logic verification

### Plain factual answer

**No formula-level or decision-logic verification exists for AIEM's own D1/D2/D3 trading decisions.**

The three verification files that do exist cover different concerns:

| File | What it covers | What it does NOT cover |
|---|---|---|
| `aiem_verification.py` | HMAC-SHA256 response integrity, request auth, replay protection | Scoring formulas, conviction weights, pick ranking logic |
| `aiem_v3_verification.py` | System health checks (DB ping, data freshness, engine liveness) | How scores are computed, how layers are combined, thresholds |
| `aiem_diagram3_verification.py` | Hash-chain integrity for `d3_governance_event_links` | Paper trading decision formulas, MTM P&L, exit logic |

There is no equivalent to the Options Engine's FIN-001..042 (42 formula math dual-method checks), no cross-implementation consistency check for the 5-layer conviction stack weighting, no verified derivation of the `final_confidence` threshold (0.42), no formula audit for MTM P&L calculation, and no specification of the pick-ranking and position-sizing formulas against an independent reference (the way Hull and CBOE docs anchor the Options Engine formulas).

**This is a genuine gap relative to the Options Engine.** It does not mean the AIEM formulas are wrong — it means there is no independently verifiable evidence that they are correct. The next step, if this gap is to be closed, would be a FIN-style audit targeting: the conviction layer weight matrix, the final_confidence computation, the MTM net-of-costs P&L formula, and the position-sizing rule.

---

## Full Verdict — Both Systems

### Options Engine (aiem_options_pipeline.py + aiem_options_scheduler.py)

**Core formulas and integrity checks: PASS**

- **FIN-001..042**: 42 formula math dual-method checks (Greek functions, BS pricing, probability functions, strategy payoffs, REQ6 dimensions) — PASS, seq=44, 2026-07-23
- **TRACE-051/052/053 + 056/057/058**: Hash-chain integrity + negative controls — PASS, seq=43, 2026-07-23
- **Stage-level revalidation** (`_stage4_execution_revalidate`): All 10 known sources have explicit routing (live-check, PASS_THROUGH with documented reason, or DB revalidation). Unrecognized sources now log durably (Item 2 above). — PASS
- **Phase 6–10 verification docs** in `docs/verification/` covering risk gates, probability calibration, performance, indicator math, and pipeline integrity — PASS

**Known gaps (not addressed by this directive):**

- `rho` — sourced from Tradier pass-through only; no dual-method check against an independent implementation or reference. Status: **accepted-risk**
- `charm`, `vanna` — computed in `greeks.py` but not stored in `aiem_options_alerts`; no storage-path verification. Status: **accepted-risk**

Both gaps are documented in the Phase 10 close-out.

### AIEM paper trading system (main.py `_aiem_paper_execute_today`)

**Operational verification only.** Evidence exists for:

- **Exactly-once ledger** (paper trade dedup via `aiem_paper_execution_log`): verified live
- **Governance gate** (G0 checkpoint via `aiem_diagram3_governance`): hash-chain verified, tamper-detect proven
- **Execution revalidation** (`_stage4_execution_revalidate`): all 10 source types now have explicit routing with durable logs
- **Learning loop closure**: MTM → learning loop funnel verified (all closes route through `_aiem_close_paper_trade_and_run_loop`)

**Not verified:**
- Formula-level math for the 5-layer conviction scoring (layer weights, aggregation)
- `final_confidence` derivation and threshold selection (0.42)
- MTM P&L formula (net-of-costs, slippage)
- Position-sizing formula (notional per name)

These are unverified by design (no FIN-style audit has been run for AIEM). This is the honest state.

---

## Evidence Chain Summary

| SEQ | Timestamp | Item | Verdict |
|---|---|---|---|
| 153 | 2026-07-27T22:59:41Z | Item 1: aiem_v3_discovery NOT PASS_THROUGH; DB revalidation confirmed; negative control | PASS=5/FAIL=0 |
| 156 | 2026-07-27T23:00:50Z | Item 2: unrecognized-source durable DB log; fail-open preserved | PASS=5/FAIL=0 |

**verify_chain.sh (tools/ hash 4804b547):** CHAIN VALID — all entries verified, no tampering.

### Item A — Tool canonical sha256 (cross-check)

```
dce94f6e19dfc5c7952ab9eee7015b7eb10c3ff1e0ca60263279658ab166f826  tools/verified_run.sh
4804b54704634c490d4d7140e88cc4e9874058292b6879d9dbdeb3e86cdd7e12  tools/verify_chain.sh
```

| File | Canonical | Note |
|---|---|---|
| `tools/verified_run.sh` | `dce94f6e19dfc5c7952ab9eee7015b7eb10c3ff1e0ca60263279658ab166f826` | Re-baselined at commit `c058d12` (hash-quoting bug fix); confirmed by Joel 2026-07-27. Full record: `docs/verification/verified_run_rebaseline-c058d12-FINAL.md`. Prior value `97589232...` cited in this directive was a stale reference predating that re-baseline — not unresolved drift. |
| `tools/verify_chain.sh` | `4804b54704634c490d4d7140e88cc4e9874058292b6879d9dbdeb3e86cdd7e12` | Unchanged. |

**Item A status: PASS** — live files match both canonicals exactly (untruncated); no investigation pending.

### SHA256 before/after main.py

| State | SHA256 |
|---|---|
| Before (after OPP-040) | `f9d5da68b5e0b63eca8821002042360e18b5ab1a18c230c782c08ac58d6872d5` |
| After (this close-out) | `f471b53060610659975d5fc87e0fde1de65db69fec803236c9e7904c440e065b` |
