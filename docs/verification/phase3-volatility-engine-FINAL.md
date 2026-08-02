# Phase 3 — Volatility Intelligence: Final Verification Record

**Directive:** AIEM Options Autonomy Master Directive  
**Phase:** 3 — Volatility Intelligence  
**Completion date:** 2026-08-02  
**Sealed run:** SEQ=172 (tools/verified_run_chain.jsonl)  
**entry_hash:** 09418cd1e6098a78032809de27934d68e9b36a3d09643fa0ccd9be97500bc821  
**Command:** `cd artifacts/stock-scanner-api && python3 verify_phase3_volatility_engine.py`  
**Exit code:** 0  
**Output SHA-256:** cb6452321e561dc45647a7ce03d3aa6b8cca5245ebc9352a186be3e7ef87dadd  
**PSV result:** 8 PASS / 0 FAIL / 1 SKIPPED (PSV8 — no SUMMARY: line expected for non-test-suite cmd)  
**SEAL_STALE warning:** present (non-blocking; engine_root_hash mismatch, to be resolved at next re-seal)

---

## Canonical Hash Verification

| File | SHA-256 (first 8) | Status |
|------|-------------------|--------|
| `tools/verified_run.sh` | `dce94f6e` | ✓ MATCH |
| `artifacts/stock-scanner-api/verify_chain.sh` | `ca7896c7` | ✓ MATCH |

---

## File SHA-256 (post-evidence state)

| File | SHA-256 |
|------|---------|
| `aiem_options_volatility_engine.py` | `fd9ef5b5e93e2abeb7566f079c8f1568f5debaa46c02c7ca74d6bedca6806eb7` |
| `verify_phase3_volatility_engine.py` | `1115dbddb71d433853521e8cbf8c15902e7b7bc83eedfc92917a2f57f35524c5` |

---

## Item Evidence

### Item 12 — IV Rank/Percentile: Rolling Window Inputs

**Ticker:** DOCU  
**DB seed source:** `oe_options_metrics` (6 rows, 252-day window)

Raw rolling-window rows:

| scan_date | iv | iv_rank_stored |
|-----------|----|----------------|
| 2026-07-23 | 0.541900 | 67.50 |
| 2026-07-23 | 0.568995 | 67.50 |
| 2026-07-29 | 0.877300 | 100.00 |
| 2026-07-29 | 0.921165 | 100.00 |
| 2026-07-30 | 1.100400 | 100.00 |
| 2026-07-30 | 1.155420 | 100.00 |

After `set()` dedup: 6 unique values → `[0.5419, 0.568995, 0.8773, 0.921165, 1.1004, 1.15542]`

Engine result:

| Field | Value |
|-------|-------|
| `atm_iv` | 0.607065 (live Polygon chain) |
| `iv_rank` | 10.62 |
| `iv_percentile` | 33.33 |
| `iv_low_window` | 0.5419 |
| `iv_high_window` | 1.15542 |
| `iv_window_rows` | 6 |
| `volatility_regime` | LOW |
| `realized_vol_20d` | 0.574999 |
| `vrp` | 0.032066 |
| `term_ratio` | 0.9998 (FLAT) |
| `pc_skew_pp` | -6.64 (CALL_SKEW) |
| `expected_move` | 4.6885 (8.551%) |
| `status` | OK |

**Formula hand-check inputs (for independent verification):**

```
current_iv    = 0.607065
rolling_low   = 0.541900
rolling_high  = 1.155420

iv_rank = (0.607065 - 0.541900) / max(1.155420 - 0.541900, 1e-6) × 100
        = 0.065165 / 0.613520 × 100
        = 10.62

iv_percentile = 2 values < 0.607065 in [0.5419, 0.568995, 0.8773, 0.921165, 1.1004, 1.15542]
              = 2/6 × 100 = 33.33
```

**Engine matches manual formula:** iv_rank MATCH ✓, iv_percentile MATCH ✓

**Status: PASS**

---

### Item 6 — HIGH/EXTREME IV Regime Case

**Ticker:** DUOL  
**Situation:** Current live ATM IV (1.636) significantly above historical maximum stored in `oe_options_metrics` (0.925), driving iv_rank = 100.0

Raw rolling-window rows for DUOL:

| scan_date | iv | iv_rank_stored |
|-----------|----|----------------|
| 2026-07-23 | 0.765500 | 100.00 |
| 2026-07-23 | 0.803775 | 100.00 |
| 2026-07-30 | 0.882015 | 100.00 |
| 2026-07-30 | 0.925365 | 100.00 |

Engine result:

| Field | Value |
|-------|-------|
| `atm_iv` | 1.635937 (live Polygon chain) |
| `iv_rank` | 100.0 |
| `iv_percentile` | 100.0 |
| `iv_low_window` | 0.7655 |
| `iv_high_window` | 0.925365 |
| `iv_window_rows` | 4 |
| `volatility_regime` | **EXTREME** |
| `realized_vol_20d` | 0.636230 |
| `vrp` | 0.999707 |
| `status` | OK |

**Downstream behavior per directive §7 (EXTREME regime):**  
→ Defined-risk credit / condor family strategies  
→ Long premium strategies penalised (IV expensive relative to history)  
→ Behavior proof deferred to Phase 5 per directive scope

**Status: PASS** — `volatility_regime=EXTREME`, `iv_rank=100.0 ≥ 50`

---

### Item 7 — LOW IV Regime Case

**Ticker:** DOCU (same engine run as Item 12)

| Field | Value |
|-------|-------|
| `atm_iv` | 0.607065 (live Polygon chain) |
| `iv_rank` | 10.62 |
| `iv_percentile` | 33.33 |
| `volatility_regime` | **LOW** |
| `status` | OK |

**Downstream behavior per directive §7 (LOW regime):**  
→ Long vol / debit spread strategies favoured  
→ Short vol / credit strategies penalised (IV cheap relative to history)  
→ Behavior proof deferred to Phase 5 per directive scope

**Status: PASS** — `volatility_regime=LOW`, `iv_rank=10.62 < 20`

---

## Supporting Checks

### Negative Control
Ticker `__FAKE_NC__` (no price data anywhere):
- `status = NO_DATA`
- `blocking_reason = spot_missing:no_price_data_for___FAKE_NC__`
- `atm_iv = None`, `iv_rank = None`
- No synthetic fallback, no exception raised
- **PASS**

### HV20 Formula Cross-Check (ticker=AA, 509 bars)
```
n=20 returns  mean=-0.00364223  variance=0.0006721355
HV20 = sqrt(0.0006721355) × sqrt(252)
     = 0.02592558 × 15.874508
     = 0.411556
Engine _compute_hv20("AA") = 0.411556
MATCH: |0.411556 - 0.411556| < 1e-4 ✓
```
**PASS**

### IV Rank vs IV Percentile Independence
Demonstrated on skewed distribution `[0.2×8, 0.8, 1.0]` with `current_iv=0.82`:
- `iv_rank = 77.50` (position in range)
- `iv_percentile = 90.00` (fraction below)
- Diverge by 12.5 percentage points — confirms they are distinct computations
- **PASS**

### Regime Boundary Check (8 boundary cases)

| iv_rank | Expected | Got | Result |
|---------|----------|-----|--------|
| 0.00 | LOW | LOW | PASS |
| 19.99 | LOW | LOW | PASS |
| 20.00 | NORMAL | NORMAL | PASS |
| 49.99 | NORMAL | NORMAL | PASS |
| 50.00 | HIGH | HIGH | PASS |
| 79.99 | HIGH | HIGH | PASS |
| 80.00 | EXTREME | EXTREME | PASS |
| 100.00 | EXTREME | EXTREME | PASS |

**ALL 8 PASS**

### Module Constant Audit
`_IV_HISTORY_MIN_ROWS = 3` — verified from live import ✓

---

## Summary

| Item | Description | Status |
|------|-------------|--------|
| 12 | IV rank/percentile history + rolling-window inputs | **PASS** |
| 6 | HIGH/EXTREME IV regime (DUOL, iv_rank=100, EXTREME) | **PASS** |
| 7 | LOW IV regime (DOCU, iv_rank=10.62, LOW) | **PASS** |
| — | Negative control (NO_DATA) | **PASS** |
| — | HV20 formula cross-check | **PASS** |
| — | IV rank vs percentile independence | **PASS** |
| — | Regime boundaries (8 cases) | **PASS** |

**Verified SEQ=172. All 7 checks PASS. Phase 3 evidence complete.**

---

## TLA Governance

`aiem_options_volatility_engine.py` matches the `aiem_options_*.py` protected-file pattern.  
Joel must apply the patch at `tools/tla_patches/phase3_volatility_engine.patch` in a TTY terminal,  
run `python3 tools/issue_tla.py`, and commit with the issued token.

Patch workflow (same as Phase 2):
```bash
git apply --index tools/tla_patches/phase3_volatility_engine.patch
python3 tools/issue_tla.py   # requires TTY — run in Replit shell
# → issues TLA-xxxxxxxx
git commit -m "Implement Phase 3 volatility intelligence engine [TLA-xxxxxxxx]"
```

`verify_phase3_volatility_engine.py` and this document are **not** TLA-gated and can be committed independently.
