# Item 1 — Live rho/charm/vanna Population Check
**Directive:** Three Open Items Closeout (2026-07-28)
**Date:** 2026-07-28
**Status: OPEN — no post-wiring production row exists yet**

---

## Current State

The wiring commit (`819f20c`, 2026-07-28T00:08 UTC) modified:
- `aiem_strat_engine/greeks.py` — added `bs_rho()`
- `aiem_options_phase2.py` — `capture_trade_record()` now computes rho/charm/vanna

Query: newest `oe_trade_records` rows as of 2026-07-28T00:10 UTC:

```
id  | ticker | created_at                              | rho  | charm | vanna | iv
----+--------+-----------------------------------------+------+-------+-------+-------
 25 | WOLF   | 2026-07-23 00:45:39.183496+00           | null | null  | null  | 2.4398
 24 | PINS   | 2026-07-23 00:45:39.163759+00           | null | null  | null  | 1.149
 23 | UMC    | 2026-07-23 00:45:39.053514+00           | null | null  | null  | 2.926
 22 | MEC    | 2026-07-23 00:45:38.905486+00           | null | null  | null  | 5.1272
 21 | PSX    | 2026-07-23 00:45:38.571988+00           | null | null  | null  | 0.4177
```

All 5 rows created 2026-07-23 — 5 days before the wiring commit. Null rho/charm/vanna on these rows is expected: they were written by the pre-wiring code path which only stored delta/gamma/theta/vega/iv.

---

## Why the Nulls Are Not a Wiring Bug

The existing rows' `entry_greeks_json` contains:
```json
{"iv": 2.4398, "vega": 0.22, "delta": -0.42, "gamma": 0.05, "theta": -0.04}
```

`spot_at_alert`, `strike`, and `dte` are absent from `entry_greeks_json` on these rows because those keys were never written to that dict by the old code. The wiring code reads them from `alert_fields`, NOT from `entry_greeks_json`.

---

## Field-Path Proof: Next Real Alert WILL Populate

`capture_trade_record()` at `aiem_options_phase2.py:1198–1208`:
```python
_spot_g = float(sel_data.get("spot_at_alert") or alert_fields.get("spot_at_alert") or 0)
_k_g    = float(sel_strike or alert_fields.get("strike") or 0)
_dte_g  = float(sel_data.get("dte") or alert_fields.get("dte") or 0)
_iv_g   = float(sel_data.get("iv") or alert_fields.get("iv") or 0)
_T_g    = _dte_g / 365.0
if _spot_g > 0 and _k_g > 0 and _T_g > 0 and _iv_g > 0:
    greeks["rho"]   = round(bs_rho(_spot_g, _k_g, _T_g, _iv_g, _call_g), 6)
    greeks["charm"] = round(bs_charm(_spot_g, _k_g, _T_g, _iv_g, _call_g), 6)
    greeks["vanna"] = round(bs_vanna(_spot_g, _k_g, _T_g, _iv_g), 6)
```

`alert_fields` constructed at `aiem_options_scheduler.py:2164–2203` (raw grep):
```
2164:        alert_fields = {
2165:            "ticker":              ticker,
2167:            "strike":              sel_strike,        ← sel_strike (non-zero on real alert)
2169:            "dte":                 9,                 ← hardcoded 9 DTE
2172:            "spot_at_alert":       spot,              ← real Tradier spot price
2177:            "iv":                  sel_data["iv"],    ← real Tradier IV
```

`sel_strike` is also passed directly as a positional argument at line 2359:
```
2359:                    sel_data=sel_data,
2360:                    sel_strike=sel_strike,      ← sel_strike = put_strike or call_strike (non-zero)
```

Evidence from `aiem_options_alerts` confirms real alerts carry non-null spot/strike/dte:
```
id=25 WOLF:  strike=30.0  dte=9  spot_at_alert=29.73  iv_val=2.4398
id=24 WOLF:  strike=305.0 dte=9  spot_at_alert=310.56 iv_val=1.2793
id=23 PINS:  strike=20.0  dte=9  spot_at_alert=22.475 iv_val=1.149
```

**Conclusion:** On the next production alert, `_spot_g`, `_k_g`, `_T_g`, `_iv_g` will all be > 0, the guard will pass, and rho/charm/vanna will be computed and stored.

---

## Independent Cross-Check (for the record, pre-confirm)

For WOLF (spot=29.73, strike=30.0, dte=9, iv=2.4398, LONG_PUT):
```python
T = 9/365.0 = 0.02466
bs_rho(29.73, 30.0, 0.02466, 2.4398, call=False)
  d1 = (ln(29.73/30.0) + (0.0 + 0.5*2.4398²)*0.02466) / (2.4398*sqrt(0.02466))
     = (ln(0.991) + 0.5*5.953*0.02466) / (2.4398*0.157)
     = (-0.00904 + 0.07338) / 0.38304
     = 0.1670
  d2 = 0.1670 - 2.4398*0.157 = 0.1670 - 0.3831 = -0.2161
  rho_put = -K*T*e^(-rT)*N(-d2) / 100
           = -30.0 * 0.02466 * 1.0 * N(0.2161) / 100
           = -30.0 * 0.02466 * 0.5856 / 100
           ≈ -0.000433  (per 1% rate change, deep near-expiry ATM)
```
This is the expected order of magnitude for a 9-DTE near-ATM put. The cross-check will be updated with the actual live value when the row is written.

---

## Status

**OPEN.** Directive requirement: "PASS only if a real live row shows all three populated and matching independent computation."

This document is the pre-confirmation record. Item 1 will be closed (PASS or FAIL) by appending a Section 2 below with the actual row query and cross-check once the next production alert fires.

---

## Canonical Hashes (at time of writing)

```
tools/verified_run.sh   dce94f6e19dfc5c7952ab9eee7015b7eb10c3ff1e0ca60263279658ab166f826
tools/verify_chain.sh   4804b54704634c490d4d7140e88cc4e9874058292b6879d9dbdeb3e86cdd7e12
```

verify_chain.sh: see Item 2 document for SEQ=162 run.
