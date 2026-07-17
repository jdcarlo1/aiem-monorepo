---
name: ASE Directive v2 — remediation lessons
description: Root causes of the 6 FAILs in verify_ase_directive_v2.py and the greeks.py production bug found during remediation
---

## Production bug fixed: aggregate() zero→None erasure
`greeks.py` aggregate() returned `{k: round(v,6) if v!=0.0 else None}`.
A delta-neutral straddle has EXACTLY 0.0 net delta, which became None.
**Fix:** return `{k: round(v,6) for k,v in totals.items()}` always.
**Why:** None must mean "not computable", not "computed to zero". Zero is a meaningful greek value (delta-neutral).

## Verifier test errors (all in verify_ase_directive_v2.py only)

### TB.005 — Butterfly debit gate
Short call mid was 2.00 → net = 3-2×2+1 = 0 → debit=False.
Fix: short mid = 1.20 → net = 3-2×1.2+1 = 1.60 → debit=True.

### TE.E03 — _I_vega had spurious /100
Verifier helper had `return S*phi(d1)*sqrt(T)/100`. Production bs_vega has no /100.
Fix: remove /100 from _I_vega, AND remove *100 from _I_vomma (vomma = vega*d1*d2/sigma).

### TE.E05 — _I_charm missing /365
Production bs_charm divides by 365 (per-day units). Verifier _I_charm did not.
Fix: add `/365.0` to _I_charm return.

### TE.E10 — _I_color formula was 2x
Old formula: `-gam*(r*d1/sq + (2-d1*d2)/(2T))`
Correct Haug formula: `-gam/(2T) * (2rT + 1 + d1*(2rT-d2*sq)/sq)`
**How to apply:** when writing independent Color verifier always use FD to cross-check.

### TE.E11 — straddle aggregate delta
Two parts: (1) _make_leg always hardcoded delta=0.50; add delta= param.
(2) aggregate() 0.0→None (production bug above).
Fix: straddle put leg gets `delta=-0.50`, aggregate() now returns 0.0.

### TH.H05 — BCS at short K
Long 95@3 + short 105@1, S=105: P&L = (105-95-3) + 1 = 8.00 not 7.90.
Expected value in verifier was wrong.

## Convention: independent greek helpers must match production per-unit convention
- Vega: no /100 (production returns raw, not "per 1 vol point")
- Charm: divide by 365 (per day)
- Rho: divide by 100 (per 1% rate change)
