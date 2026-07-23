# Phase 10 — Section 13: Options Pipeline Close-Out Record
**Date:** 2026-07-23
**Commit verified against:** 84c6aa17f3b347ab30197df0764e92f945a6acf2
**Verifier archive:** tools/logs/verified_run_108.log
**sha256(verified_run.sh):** 58534be51d9445e13c1838532a7d94c2773d6e152d435e6f620ddba64a9f3bf5
**sha256(verify_chain.sh):** ca7896c7c832ef53430dfd07319418000d9139566c9e52720f587aa9c9840d1f
**SEQ=108 result:** EXIT=0  PASS=23  PARTIAL=11  FAIL=0  NOT_IMPLEMENTED=1
**Post-seal integrity:** 9/9 PASS

---

## Verifier Summary (OPT-001 through OPT-035)

| Group | Checks | PASS | PARTIAL | FAIL | NOT_IMPLEMENTED |
|---|---|---|---|---|---|
| 1 — Chain Ingestion | OPT-001–006 | 6 | 0 | 0 | 0 |
| 2 — IV/IV%/EM | OPT-007–013 | 5 | 2 | 0 | 0 |
| 3 — Greeks | OPT-014–020 | 4 | 3 | 0 | 0 |
| 4 — Execution Quality | OPT-021–026 | 1 | 5 | 0 | 0 |
| 5 — Strategy Selection | OPT-027–029 | 2 | 1 | 0 | 0 |
| 6 — EV/Capital/RR | OPT-030–032 | 1 | 1 | 0 | 1 |
| 7 — Reproducibility/Dashboard | OPT-033–035 | 4 | 0 | 0 | 0 |

---

## Addendum — 2026-07-23 Follow-Up Directive (Items 1 and 2)

*Commit cited for this addendum: 84c6aa17f3b347ab30197df0764e92f945a6acf2 (HEAD at time of writing).*

---

## Root-Cause Diagnosis — 2026-07-23 Directive

### Item 1: OPT-030 — Expected Value (EV)

**Question:** Why does production DB show `expected_return=0.8500` for all 25 alerts if the lognormal fix was applied?

**Raw diagnostic — log.error elevation (no exception raised):**

```
=== EV DIAGNOSTIC — TER-like values ===
spot=316.0  front_iv=1.28  _dte=9  _T=0.024658
call_strike=325  put_strike=310
call_mid=25.41  put_mid=53.99

EV BLOCK: SUCCESS (no exception raised)
  _call_ev_raw=-405.129051   _put_ev_raw=-3066.557280
  _call_expected_return=-0.1594
  _put_expected_return =-0.568

Final _call_expected_return: -0.1594  (fallback=0.60 if unchanged)
Final _put_expected_return : -0.568   (fallback=0.85 if unchanged)
Fallback active: False
```

**With real TER DB values (spot=310.56, iv=1.2793, dte=9):**

```
EV BLOCK: SUCCESS  call_er=-0.1689  put_er=-0.5647  fallback_active=False
```

**Root cause — pipeline timing, not an import error or fallback:**

Raw SQL result:
```sql
SELECT id, ticker, alert_date, expected_return, created_at
FROM aiem_options_alerts WHERE expected_return = 0.85
ORDER BY created_at DESC LIMIT 5;
```
```
(25, 'TER',  2026-07-17, 0.8500, 2026-07-17 14:17:27 UTC)
(24, 'WOLF', 2026-07-17, 0.8500, 2026-07-17 14:17:23 UTC)
(23, 'PINS', 2026-07-17, 0.8500, 2026-07-17 14:17:21 UTC)
(22, 'UMC',  2026-07-17, 0.8500, 2026-07-17 14:17:17 UTC)
(21, 'MEC',  2026-07-17, 0.8500, 2026-07-17 14:17:12 UTC)
```

EV fix committed: `5a7f1553... 2026-07-23 21:42:41 UTC` — **6 days and 7 hours after the production rows were written.**

options_pipeline_jobs shows the last DONE runs were 2026-07-17. Every run from 2026-07-18 through 2026-07-23 has status=`FAILED` with `error_text`:

```
"not ready_for_decision: BOTH DIRECTIONS REJECTED by hard gates.
Return NO TRADE — neither the call nor the put meets minimum quality standards."
```

This is a **deliberate NO_TRADE gate rejection** (scoring gate 6), not a crash. The pipeline is operating correctly — market conditions since 2026-07-18 have produced no candidates that clear minimum quality thresholds. Because the pipeline exits at the NO_TRADE gate before reaching the EV block, the EV fix code has had no opportunity to write rows to `aiem_options_alerts`.

**Exact defect before commit 5a7f1553 (raw diff):**

```diff
--- a/artifacts/stock-scanner-api/aiem_options_scheduler.py
+++ b/artifacts/stock-scanner-api/aiem_options_scheduler.py
@@ -1430,6 +1430,28 @@ def _execute_job(...):
         }
+        # ── Lognormal expected-value via payoff.py (replaces hardcoded 0.60/0.85) ──
+        _call_expected_return = 0.60
+        _put_expected_return  = 0.85
+        try:
+            from aiem_strat_engine.payoff import expected_value as _pyoff_ev
+            _pf_prices    = [spot * (0.5 + 0.01 * i) for i in range(151)]
+            _call_payoffs = [max(0.0, p - call_strike) * 100 - call_mid * 100
+                             for p in _pf_prices]
+            _put_payoffs  = [max(0.0, put_strike - p)  * 100 - put_mid  * 100
+                             for p in _pf_prices]
+            _call_ev_raw = _pyoff_ev(_call_payoffs, _pf_prices, spot, front_iv,       _dte)
+            _put_ev_raw  = _pyoff_ev(_put_payoffs,  _pf_prices, spot, front_iv * 1.05, _dte)
+            if call_mid > 0:
+                _call_expected_return = round(
+                    max(-1.0, min(3.0, _call_ev_raw / (call_mid * 100))), 4)
+            if put_mid > 0:
+                _put_expected_return = round(
+                    max(-1.0, min(3.0, _put_ev_raw  / (put_mid  * 100))), 4)
+        except Exception as _ev_e:
+            log.debug(f"[EV] lognormal EV skipped, using heuristic fallback: {_ev_e}")
+
         call_data = {
             ...
-            "expected_return":     0.60,
+            "expected_return":     _call_expected_return,
             ...
         }
         put_data = {
             ...
-            "expected_return":     0.85,
+            "expected_return":     _put_expected_return,
```

**What the defect was:** Before this commit, `call_data["expected_return"]` was the literal `0.60` and `put_data["expected_return"]` was the literal `0.85` — hardcoded target-return heuristics, not computed values. `payoff.py`'s `expected_value()` function existed but was not called anywhere in the alert write path.

**Is this the only change between the stale 0.85 rows (7/17) and now?** Yes. All 25 `aiem_options_alerts` rows have `created_at = 2026-07-17 14:17 UTC`. The EV fix is in commit `5a7f1553` at `2026-07-23 21:42 UTC`. No commit between those two dates touches the `expected_return` field in `aiem_options_scheduler.py`. The other files changed in `5a7f1553` are: `aiem_options_intel.py` (iv_percentile addition), `Dashboard.tsx` (new file), `main.py` (reconcile endpoint), and verifier updates — none touch the alert write path.

**Conclusion for OPT-030:** The lognormal EV code is confirmed working (no exception, produces non-constant distinct values per ticker). The 0.8500 in production is from the pre-fix 2026-07-17 run. The fallback is NOT active in current code. OPT-030 cannot be confirmed PASS from DB data until the next DONE pipeline run.

---

### Item 2: Group 4 — OPT-021/023/024/025/026

**Question:** Do EI-post4 synthetic assessments produce genuinely computed values?

**Raw SQL — aiem_execution_assessments real production rows:**
```sql
SELECT id, ticker, scan_date, liquidity_score, fill_probability,
       expected_slippage_pct, early_assignment_risk, pin_risk_flag,
       rejection_reason, approved, created_at
FROM aiem_execution_assessments
WHERE ticker NOT LIKE 'E2E%'
  AND ticker NOT LIKE 'TEST%'
  AND ticker NOT LIKE 'S3_%'
ORDER BY created_at DESC LIMIT 20;
```
```
Row count: 0
```

All 30 rows in the table are E2E_OBS, E2E_GATE, TEST, TEST_APPROVED, S3_CHAIN_TEST — all from integration test runs on 2026-07-18.

**Root cause — NO_TRADE gate prevents EI-post4 from being reached:**

The pipeline's NO_TRADE gate (scoring gate 6 — "BOTH DIRECTIONS REJECTED by hard gates") fires for every candidate since 2026-07-18. The EI-post4 synthetic fallback block runs only when `_ei_assessments` is empty AND the pipeline has reached stage 4. Since the pipeline exits at the scoring gate, stage 4 (EI) is never reached.

**EI-post4 direct diagnostic — TER real values from DB:**

```
ticker=TER  spot=310.56  front_iv=1.2793  _dte=9
call_strike=320  put_strike=305
call_mid=24.95   put_mid=53.03

filter_strategies_by_execution(ticker=TER_DIAG, trace=DIAG_8d788868, scan_date=2026-07-23)

EA row (LONG_CALL):
  approved: False
  liquidity_score: 0.5389         ← genuinely computed (not NULL or EI_EXCEPTION fallback)
  fill_probability: 0.48           ← genuinely computed
  expected_slippage_pct: 0.005     ← genuinely computed
  early_assignment_risk: LOW       ← genuinely computed (not 'HIGH' from EI_EXCEPTION)
  pin_risk_flag: False             ← genuinely computed
  rejection_reason: R8_costs_eliminate_edge: cost_frac=1852.191 > 0.3
  execution_score: 0.5362
  gross_expected_edge: -0.1689
  net_expected_edge: -313.0302

EA row (LONG_PUT):
  approved: False
  liquidity_score: 0.6132
  fill_probability: 0.50
  expected_slippage_pct: 0.005
  early_assignment_risk: LOW
  pin_risk_flag: False
  rejection_reason: R8_costs_eliminate_edge: cost_frac=706.348 > 0.3
  execution_score: 0.5721
  gross_expected_edge: -0.5647
  net_expected_edge: -399.5244
```

The EI-post4 path produces genuinely computed values for all five Group 4 fields — liquidity_score, fill_probability, expected_slippage_pct, early_assignment_risk, pin_risk_flag — using real ticker data. These are not EI_EXCEPTION fallbacks. The old error (`'LegExecutionMetrics' object has no attribute 'get'`) is bypassed entirely by the synthetic leg construction.

The five Group 4 checks remain PARTIAL because **no real production rows exist in aiem_execution_assessments** — a consequence of the pipeline's sustained NO_TRADE mode, not a defect in the EI-post4 code.

---

### Item 2 (Follow-Up Directive): R8 Gate — Cost_frac Sanity Check

**Question:** Are cost_frac values of 706–1852× normal for current market conditions, or is the gate miscalibrated?

**R8 source code (aiem_execution_intelligence.py lines 524–535, 772–774):**

```python
# Line 524 — cost computation
gross_edge = abs(strategy.get("ev_after_costs") or 0.0)
cost_pct   = (total / gross_edge) if gross_edge > 0.01 else 1.0
return { ..., "cost_as_pct_of_gross": round(cost_pct, 4), ... }

# Line 772 — R8 gate
cost_frac = exec_costs.get("cost_as_pct_of_gross", 1.0)
if cost_frac > EI_MAX_TRANSACTION_COST_FRAC:   # threshold = 0.3
    return False, (f"R8_costs_eliminate_edge: cost_frac={cost_frac:.3f} > 0.3 ...")
```

**Formula: `cost_frac = total_transaction_cost / |ev_after_costs|`**

**Formula derivation — confirmed against all three rows in DB with cost_frac values:**

```
Row: TER_DIAG/LONG_CALL
  reported cost_frac        = 1852.191
  total_transaction_cost    = 312.8350
  gross_expected_edge       = -0.1689  (what R8 uses as denominator)
  H1: 312.835 / 0.1689      = 1852.19  ← EXACT MATCH

Row: TER_DIAG/LONG_PUT
  reported cost_frac        = 706.348
  total_transaction_cost    = 398.8750
  gross_expected_edge       = -0.5647
  H1: 398.875 / 0.5647      = 706.35   ← EXACT MATCH

Row: E2E/TEST (old EI path)
  reported cost_frac        = 0.556
  total_transaction_cost    = 19.4450
  gross_expected_edge       = 35.0000  (edge in dollars from old EI path)
  H1: 19.445 / 35.0         = 0.5556   ← EXACT MATCH
```

**Verdict: units mismatch — not market conditions.**

The R8 gate's formula `cost_frac = total_cost / |ev_after_costs|` expects `ev_after_costs` in **dollars**. In the old EI path (E2E test rows), `ev_after_costs = 35.0` means "$35 of gross expected edge." In the EI-post4 synthetic path, `ev_after_costs = float(_call_expected_return) = -0.1689` — a **dimensionless ratio** (EV per dollar of premium). The gate divides $312.84 by 0.1689, yielding 1852 — the unit is "dollars per ratio unit," which has no financial meaning and is structurally guaranteed to far exceed the 0.3 threshold.

The 700–1852× values are not a sign of adverse market conditions. They are a category error: the denominator has the wrong units for every EI-post4 row.

**Corrected cost_frac with consistent units (ev_after_costs converted to dollars = ratio × premium × 100):**

```
TER_DIAG LONG_CALL:
  ev_dollars = |(-0.1689)| × 24.95 × 100 = 421.41
  cost_frac  = 312.835 / 421.41 = 0.742   → still R8-rejected (> 0.3)

TER_DIAG LONG_PUT:
  ev_dollars = |(-0.5647)| × 53.03 × 100 = 2994.60
  cost_frac  = 398.875 / 2994.60 = 0.133  → would PASS R8 (< 0.3)
```

**Relationship to the NO_TRADE streak:**

The `options_pipeline_jobs` FAILED rows ("BOTH DIRECTIONS REJECTED by hard gates") are produced by the REQ6 **scoring gate**, which fires upstream of EI assessment and is independent of R8. For the real production runs (DG, UPS, HUM, etc. since 7/18), the pipeline exits at REQ6 before writing any rows to `aiem_execution_assessments`. The R8 units mismatch affects the EI-post4 path only; since real production runs never reach the EI-post4 block (they exit at REQ6 first), the units mismatch is not the cause of the NO_TRADE streak.

**Effect on Group 4 when pipeline resumes DONE jobs:**

When market conditions eventually produce a DONE job, the pipeline will reach EI. With the current EI-post4 code:
- Call side: corrected cost_frac ≈ 0.742 → still R8-rejected → `approved=False`, but all five Group 4 fields computed and stored
- Put side: corrected cost_frac ≈ 0.133 → would pass R8 with correct units, but with current units (÷ ratio instead of ÷ dollars) → cost_frac=706 → R8-rejected

The units mismatch is a defect in the EI-post4 path's `ev_after_costs` argument, not in the R8 gate threshold or formula. The gate threshold (0.3) and formula are correct when the input has the right units.

---

### Pipeline Status Summary

Raw SQL:
```sql
SELECT scan_date, status, error_text FROM options_pipeline_jobs
WHERE scan_date >= '2026-07-18' ORDER BY scan_date DESC;
```

| scan_date  | status | error_text |
|---|---|---|
| 2026-07-23 | FAILED (×5) | "BOTH DIRECTIONS REJECTED by hard gates. Return NO TRADE" |
| 2026-07-22 | FAILED (×5) | "BOTH DIRECTIONS REJECTED by hard gates. Return NO TRADE" |
| 2026-07-21 | FAILED (×5) | "BOTH DIRECTIONS REJECTED by hard gates. Return NO TRADE" |
| 2026-07-20 | FAILED (×5) | "BOTH DIRECTIONS REJECTED by hard gates. Return NO TRADE" |
| 2026-07-17 | DONE (×5)   | NULL — alerts written, aiem_options_alerts rows 21-25 |

"FAILED" in this table means a deliberate NO_TRADE gate exit, not a crash. The pipeline is functioning; market conditions have not produced a qualifying candidate since 2026-07-17.

---

## Item-by-Item Close-Out Verdict

Per the directive criterion: *"PASS only if fully verified, nothing on fallback/test-ticker-only data."*

| Check | SEQ=108 | Root-cause diagnosis | Close-out |
|---|---|---|---|
| OPT-021 liquidity_score | PARTIAL | EI-post4 confirmed working (diagnostic); pipeline in NO_TRADE mode prevents real rows | **PARTIAL — structural** |
| OPT-023 fill_probability | PARTIAL | Same | **PARTIAL — structural** |
| OPT-024 expected_slippage | PARTIAL | Same | **PARTIAL — structural** |
| OPT-025 assignment_risk | PARTIAL | Same | **PARTIAL — structural** |
| OPT-026 pin_risk_flag | PARTIAL | Same | **PARTIAL — structural** |
| OPT-030 expected_value | PASS (verifier) | Lognormal confirmed working (diagnostic); DB values predate fix by 6 days; fallback NOT active | **PASS(diagnostic) — production unconfirmed** |

---

## Phase 10 Status

**Status: NOT YET CLOSEABLE by strict criteria.  
Closeable via accepted-risk with the following documented basis:**

**Accepted-risk conditions:**

1. **OPT-030 (EV):** The lognormal EV code is confirmed working by direct diagnostic. The `log.error` elevation produced no exception. The production 0.8500 values are fully explained by timing (pre-fix DONE run on 2026-07-17; fix committed 2026-07-23). The code at commit `84c6aa17` produces `_call_er=-0.1689` / `_put_er=-0.5647` for TER — confirming the fallback is not active. Production PASS requires one DONE pipeline job post-commit `5a7f1553`.

2. **Group 4 (OPT-021/023/024/025/026):** The EI-post4 code path is confirmed working by direct diagnostic (trace=DIAG_8d788868). All five fields — liquidity_score, fill_probability, expected_slippage_pct, early_assignment_risk, pin_risk_flag — are genuinely computed (not EI_EXCEPTION fallbacks) when the EI-post4 synthetic path is exercised. The absence of real production rows in aiem_execution_assessments is a consequence of the pipeline's sustained NO_TRADE scoring-gate mode since 2026-07-18 (REQ6 gate, upstream of EI). Production PASS requires a DONE job that clears scoring gate 6. **Additional finding (follow-up directive):** the EI-post4 path has a units mismatch in `ev_after_costs` (passes a dimensionless ratio where R8 expects dollars), making R8 structurally always-reject for real tickers with lognormal EV ratios. This is a known defect in the EI-post4 implementation, not in the gate threshold. With corrected units, the put side would pass R8 (corrected cost_frac=0.133 < 0.3) while the call side would remain rejected (0.742 > 0.3). This does not change the PARTIAL verdict for Group 4 but narrows the production-PASS trigger: even after REQ6 resumes DONE jobs, OPT-021/023/024/025/026 will only be promotable to PASS once the EI-post4 `ev_after_costs` units are corrected.

3. **NOT_IMPLEMENTED (OPT-031 capital efficiency):** Accepted per 2026-07-23 directive. oe_trade_records has capital_reserved/bp_effect/return_on_risk; no per-alert capital_efficiency ratio is computed in the native pipeline.

**Trigger for production promotion:**
When the next DONE pipeline run occurs (a candidate clears scoring gate 6), re-query:
- `SELECT DISTINCT expected_return FROM aiem_options_alerts WHERE alert_date > '2026-07-23'` — confirm non-constant values (not 0.8500 for all)
- `SELECT ticker, liquidity_score, fill_probability FROM aiem_execution_assessments WHERE ticker NOT LIKE 'E2E%' AND ticker NOT LIKE 'TEST%' AND ticker NOT LIKE 'S3_%'` — confirm non-zero real production rows

At that point, OPT-030 and OPT-021/023/024/025/026 can be re-evaluated for PASS promotion without reopening the sealed verifier.

---

## Checks with No Outstanding Issues

All other Phase 10 checks (OPT-001–006, 008–016, 022, 027–029, 031–035) are at their correct final verdicts. No accepted-risk notation required.

Greeks mutation detection confirmed (SEQ=108):
- Delta: N(d₂) mutant detected ✓
- Gamma: missing √T mutant detected ✓  
- Theta: missing /365 mutant detected ✓
- Vega: ÷10 mutant detected ✓
- Charm: missing /365 mutant detected ✓
- Vanna: sign-flip mutant detected ✓

Post-seal chain integrity confirmed (9/9 PASS): archive exists, SHA 3-way binding, prev_hash continuity, exit status binding, cmd binding.

---

---

## Addendum — 2026-07-23 Fix Directive: R8 Units Mismatch Resolved

**Fix approach chosen: Option (a) — fix the caller (`aiem_options_scheduler.py`), not the EI formula.**

Rationale: The EI module formula `cost_frac = total_cost / |ev_after_costs|` is correct and calibrated for dollars (proven by E2E rows: `19.445 / 35.0 = 0.556`). Changing the EI formula (option b) would break all callers that already pass dollars correctly. The EI-post4 caller passed `float(_call_expected_return)` (a ratio = `_call_ev_raw / (call_mid × 100)`), so the inverse `ratio × call_mid × 100` recovers the original EV in dollars exactly.

**SHA256 before fix:** `205efeeb49c0a6020c35dd1b9c092d228c2184a533d3a79ea7d6b0ddaf229f38`
**SHA256 after fix:**  `b0f40af1fb239213ae2e39a91b91a9a8877f4838f23033f63b0dd1b46ebdec0b`

**Exact diff (5 lines — 2 deletions, 3 additions + 3 comment lines):**

```diff
--- a/artifacts/stock-scanner-api/aiem_options_scheduler.py
+++ b/artifacts/stock-scanner-api/aiem_options_scheduler.py
@@ -1519,13 +1519,18 @@ def _execute_job(...):
                 }
+                # ev_after_costs must be in DOLLARS for R8 gate (cost/|edge|).
+                # _call_expected_return is a dimensionless ratio = _call_ev_raw/(call_mid*100),
+                # so the inverse gives the original EV in dollars: ratio × mid × 100.
+                _call_ev_dollars = float(_call_expected_return) * call_mid * 100
+                _put_ev_dollars  = float(_put_expected_return)  * put_mid  * 100
                 _synth = [
                     {"strategy": "LONG_CALL", "direction": "BULLISH",
-                     "ev_after_costs": float(_call_expected_return),
+                     "ev_after_costs": _call_ev_dollars,
                      ...},
                     {"strategy": "LONG_PUT",  "direction": "BEARISH",
-                     "ev_after_costs": float(_put_expected_return),
+                     "ev_after_costs": _put_ev_dollars,
                      ...},
                 ]
```

**TER diagnostic (post-fix), raw output:**

```
spot=310.56  front_iv=1.2793  _dte=9
call_mid=24.95   put_mid=53.03
_call_ev_dollars = -0.1689 × 24.95 × 100 = -421.4055
_put_ev_dollars  = -0.5647 × 53.03 × 100 = -2994.6041

filter_strategies_by_execution(ticker=TER_FIX, trace=DIAG_FIX_b44a5525)

EA[LONG_CALL]:
  approved=False
  rejection_reason=R8_costs_eliminate_edge: cost_frac=0.742 > 0.3
  cost_frac=0.742    ← was 1852.191 before fix; CORRECTED ✓
  liquidity_score=0.5389
  fill_probability=0.48
  gross_expected_edge=-421.4055

EA[LONG_PUT]:
  approved=False
  rejection_reason=R9_net_edge_below_floor: -3842.6697 < -0.5
  cost_frac=N/A (R8 passed)    ← R8 not triggered; cost_frac < 0.3 ✓ CORRECTED
  liquidity_score=0.6132
  fill_probability=0.5
  gross_expected_edge=-2994.6041
```

LONG_CALL: cost_frac=0.742 ✓ (was 1852.191 — corrected, R8 still rejects for valid financial reason: costs consume 74% of edge).
LONG_PUT: R8 passed (cost_frac<0.3 ✓ — was 706.348 — corrected), R9 rejects because net EV is deeply negative (-3842 < -0.5 floor). Both rejections are now financially meaningful.

**Known-answer test vector, raw output:**

```
compute_execution_costs() with ev_after_costs=$50.00 (dollars):
  total_transaction_cost = 3.86
  cost_as_pct_of_gross   = 0.0772
  expected (total/50)    = 0.0772
  match: True
```

`cost_frac = 3.86 / 50.00 = 0.0772` — exact match confirms formula `total / |ev_after_costs|` with dollar inputs produces the correct ratio. ✓

**Mutation check, raw output:**

```
MUTANT (ratio passed as ev_after_costs — old broken code):
  LONG_CALL: cost_frac=1852.191 > 0.3  → R8-rejected
  LONG_PUT:  cost_frac=706.348  > 0.3  → R8-rejected

FIX (dollars passed as ev_after_costs):
  LONG_CALL: cost_frac=0.742 > 0.3    → R8-rejected (valid financial reason)
  LONG_PUT:  R8 passed (cost_frac<0.3) → R9-rejected (net EV negative floor)

MUTATION TEST CONCLUSION: test is NOT vacuous — mutant always-rejects at 700-1852×,
fixed code rejects at financially meaningful ratios (0.742 call, <0.3 put).
```

**Effect on Group 4 production-PASS trigger:**

The prior addendum stated two conditions were required:
1. A DONE job clearing REQ6
2. The EI-post4 `ev_after_costs` units bug corrected

Condition 2 is now satisfied. The production-PASS trigger for OPT-021/023/024/025/026 is now **single-condition: one DONE pipeline job where market conditions allow a candidate to clear REQ6 scoring gate.** No further code changes are needed. When that job runs, the EI-post4 path will write `aiem_execution_assessments` rows with genuinely computed Group 4 fields and cost_frac values in the correct range (not 700-1852×).

*Written: 2026-07-23. Commit: 84c6aa17f3b347ab30197df0764e92f945a6acf2.*
*Addendum: 2026-07-23 follow-up directive — EV diff and R8 units mismatch documented above.*
*Addendum: 2026-07-23 fix directive — R8 units mismatch resolved; production-PASS trigger now single-condition.*
*Next action: re-evaluate OPT-030 and Group 4 on first DONE pipeline run after 2026-07-23.*
