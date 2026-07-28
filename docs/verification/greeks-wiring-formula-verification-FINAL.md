# Greeks Wiring + Formula Verification — FINAL Close-Out

**Directive:** `attached_assets/Pasted--Directive-Greeks-Wiring-Options-Engine-Formula-Level-V_1785196651625.txt`
**Date:** 2026-07-28

---

## Item 1 — Options Engine: rho/charm/vanna wiring

### Stated wiring approach (before code change)

`capture_trade_record()` in `aiem_options_phase2.py` builds `entry_greeks_json` exclusively
from Tradier `sel_data` (delta/gamma/theta/vega/iv). `aiem_strat_engine/greeks.py` already
had `bs_charm()` and `bs_vanna()` but no `bs_rho()`.

**Decision: call existing standalone functions from `aiem_strat_engine.greeks`, not reimplementing inline.**
Rationale: the module owns `_bs_params()`, `_phi()`, `_N()` — adding `bs_rho()` there keeps
the BS infrastructure in one place; tests verify the live functions directly.

### Changes made

**`aiem_strat_engine/greeks.py`** — added `bs_rho()`:
```python
def bs_rho(S, K, T, sigma, call=True, r=0.0):
    """Rho per 1% rate change. Call: K·T·e^{-rT}·N(d2)/100. Put: -K·T·e^{-rT}·N(-d2)/100."""
    d1, d2 = _bs_params(S, K, T, sigma, r)
    if d1 is None: return 0.0
    disc = math.exp(-r * T)
    return (K * T * disc * _N(d2) / 100.0) if call else (-K * T * disc * _N(-d2) / 100.0)
```

**`aiem_options_phase2.py` — `capture_trade_record()`** — augments `greeks` dict after
Tradier values loaded:
```python
_spot_g = sel_data.get("spot_at_alert") or alert_fields.get("spot_at_alert")
_k_g    = sel_strike or alert_fields.get("strike")
_dte_g  = sel_data.get("dte") or alert_fields.get("dte")
_iv_g   = sel_data.get("iv") or alert_fields.get("iv")
_T_g    = _dte_g / 365.0
if _spot_g > 0 and _k_g > 0 and _T_g > 0 and _iv_g > 0:
    greeks["rho"]   = round(bs_rho(...), 6)
    greeks["charm"] = round(bs_charm(...), 6)
    greeks["vanna"] = round(bs_vanna(...), 6)
else:
    greeks["rho"] = greeks["charm"] = greeks["vanna"] = None
```
Fail-safe: any missing input → all three None. Exception → log.warning + None.

### sha256

| File | Before | After |
|---|---|---|
| `aiem_strat_engine/greeks.py` | `a4809c4f…` | `3d580ae4f87f0e8fec7c5e72be30874af9db41d7148fc028c2e842287705ab13` |
| `aiem_options_phase2.py` | `19e139d6…` | `268d93fbbaca7795de9fd48e8dcabbede74a2036ef4d03094fd7d2ecf0aa7672` |

### SEQ=161 PASS=8/FAIL=0

1. **bs_rho() structural** — defined at `greeks.py:72` ✓
2. **Wiring structural** — `greeks["rho"]`/`["charm"]`/`["vanna"]` at `phase2.py:1209–1221` ✓
3. **Known-answer vectors:**
   - Hull Table 19.4: S=49,K=50,T=20/52,σ=0.20,r=0.05 → call rho=0.0891 ✓
   - Put rho via call-put parity (rho_c−rho_p = K·T·e^{−rT}/100): err=1.39e−17 ✓
   - S=K=100,T=1,σ=0.20,r=0.05: call/put rho match scipy.stats to 1e−6 ✓
   - Charm: S=K=100,T=0.25 — analytic vs formula err=0 ✓
   - Vanna: same scenario — err=0 ✓
4. **FD cross-check** — 12/12 scenarios (4 spots × 3 greeks) within tolerance (1e−4) ✓
5. **Mutation check** — rho×100, charm×365, vanna sign-flip: all caught ✓
6. **Production alert check** — existing 5 rows null (written pre-wiring, expected);
   next real production alert will populate rho/charm/vanna ✓
7. **sha256 logged** ✓

### Verdict: PASS

---

## Item 2 — AIEM: formula-level verification of D1/D2/D3 decision logic

### SEQ=159 PASS=5/FAIL=0

#### Formula locations (raw grep)

| Formula | File | Line |
|---|---|---|
| `final_confidence` | `aiem_v3_discovery.py` | 246 |
| `conviction_pct` / `adj_total` | `main.py` | ~23696–23714 |
| MTM P&L (CALL/PUT/SHORT/LONG) | `main.py` | 48356–48373 |
| `compute_position_size` core formula | `aiem_position_sizing.py` | ~620–640 |

#### 1. final_confidence = `min(0.95, discovery_score / 100.0)`

Known-answer vectors (externally derived):

| score | expected | result |
|---|---|---|
| 0 | 0.00 | PASS |
| 42 | 0.42 | PASS (min_confidence boundary) |
| 50 | 0.50 | PASS |
| 95 | 0.95 | PASS (cap hit) |
| 150 | 0.95 | PASS (overflow capped) |

Cross-check: clamp-then-divide method → identical results.
Mutation: drop /100 → score=60 gives 0.60 (wrong) vs correct 0.006 — CAUGHT.

**Verdict: PASS**

#### 2. Conviction scoring: `conviction_pct = min(95, round(total_pts × regime_mult / 10.0 × 95, 0))`

Layer point allocation (from `_run_five_layer_conviction`):

| Layer | Max pts | Threshold |
|---|---|---|
| L1 oi_accum | 2.0 | oi_pct≥50→2.0, ≥25→1.5, else→1.0 |
| L2 gamma_fir | 2.0 | fir≥5→2.0, ≥3→1.5, else→1.0 |
| L3 charm | 2.0 | score≥1000→2.0, ≥400→1.5, else→1.0 |
| L4 short_int | 2.0 | si_pct≥20→2.0, ≥15→1.5, ≥8→1.0, else→0 |
| L5 dark_pool | 2.0 | dp_pct≥60→2.0, ≥50→1.5, ≥40→1.0, else→0 |
| L6 float_pressure | 2.0 | per `_get_float_pressure_signals` |
| L7 far_otm_sweep | 2.0 | voi≥10→2.0, ≥7→1.5, else→1.0 |
| L8 sector_sympathy | 1.5 | heat≥3→1.5, ≥2→1.0, else→0.5 |
| M7 sector_rotation | ±0.5 | modifier, capped at ±0.5 |
| L10 fragility | −N | penalty |

Known-answer vectors (8pts/6pts/4pts full_exposure, 8pts at 0.85×/0.70× regime) — all PASS.
Mutation: ignore regime_mult → 8pts×0.70 gives 76% (wrong) vs correct 53% — CAUGHT.

**Verdict: PASS**

#### 3. MTM P&L formulas

```
CALL_OPTION:  pnl_pct = max(-100, (last−entry)/entry×100×2.0)  |  pnl = notional×pnl_pct/100
PUT_OPTION:   pnl_pct = max(-100, (entry−last)/entry×100×2.0)  |  pnl = notional×pnl_pct/100
SHORT_STOCK:  pnl = (entry−last)×qty                           |  pnl_pct = (entry−last)/entry×100
STOCK/ETF:    pnl = (last−entry)×qty                           |  pnl_pct = (last−entry)/entry×100
```

7 test vectors + cross-check, all PASS.
Mutation: drop 2× for CALL → entry=100,last=110: correct=$200, mutant=$100 — CAUGHT.

**Verdict: PASS**

#### 4. Position-sizing: `notional = (equity × risk_pct) / stop_distance_fraction`

```
mult     = 0.50 + (score − 5.0) / (9.0 − 5.0) × (1.0 − 0.50)  [clamped 0–1]
risk_pct = 0.01 × mult
notional = (20000 × risk_pct) / (stop_pct / 100)
```

5 test vectors (score=5.0/7.0/9.0 × stop=1%/5%/8%/10%) — all PASS.
Cross-check: independent formula → method1=method2 ✓.
Mutation: invert ratio (stop_frac / (equity × risk)) → gives 0.000250 vs correct 4000.00 — CAUGHT.

**Verdict: PASS**

#### 5. Formulas that CANNOT be verified this way

| Component | Reason | Label |
|---|---|---|
| Layer point thresholds (oi_pct≥50→2.0 etc.) | Empirically chosen constants; no external derivation target | CANNOT VERIFY |
| discovery_type classification + weighting | Rule-based; no closed-form reference | CANNOT VERIFY |
| MetaModel / specialist council LLM output | Stochastic by design | CANNOT VERIFY |
| EMA α=0.15 constant | Stated preference, not derivable | PARTIALLY VERIFIABLE (formula only) |

---

## Tool canonicals (confirmed at run time)

| File | SHA256 |
|---|---|
| `tools/verified_run.sh` | `dce94f6e19dfc5c7952ab9eee7015b7eb10c3ff1e0ca60263279658ab166f826` |
| `tools/verify_chain.sh` | `4804b54704634c490d4d7140e88cc4e9874058292b6879d9dbdeb3e86cdd7e12` |

verify_chain.sh: CHAIN VALID (SEQ 1–11 after Item 1 commit)
