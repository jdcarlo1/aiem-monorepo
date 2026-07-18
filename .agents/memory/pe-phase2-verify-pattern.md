---
name: Portfolio Engine Phase 2 verify pattern
description: Key lessons from PE Phase 2 gap remediation — stress formula, verify tooling, CORRELATION_GROUPS layout, _HAS_BS flag
---

## Stress P&L Formula
Full parametric: `pl = (Δ·dS + ½·Γ·dS² + ν·dσ) × qty × mult`  
`dσ = iv_change_pct` (raw float, e.g. 0.25 for +25 IV pts). Theta term omitted for `time_decay_days=0` scenarios.  
`_HAS_BS = False` (aiem_strat_engine not importable) → delta-gamma-vega always used, never BS re-pricing.  
**Why:** assignment_risk has iv_change=+0.25; reference formula that ignores vega gives -296 not -292.25.  
**How to apply:** Any stress reference calculation must include the vega term for scenarios with iv_change≠0.

## CORRELATION_GROUPS layout (5 groups)
`mega_tech`: AAPL, AMZN, GOOG, GOOGL, META, MSFT, NVDA  
`semis`: AMAT, AMD, AVGO, CRDO, INTC, LSCC, MRVL, MU, NVDA  
`ev_meme`: LCID, RIVN, TSLA  
`biotech_meme`: BYND, HOOD, MRNA  
`crypto_adjacent`: COIN, HIVE, MARA, RIOT  
Scale: 0.0 (no shared cluster) / 0.75 (shared=1) / 1.0 (shared≥2).  
NVDA is in BOTH mega_tech and semis.

## verified_run.sh (tools/verified_run.sh)
- flock-protected monotonic SEQ at /tmp/portfolio_engine_verify_seq
- Does NOT cd to REPO_ROOT — CMD is run from caller's CWD
- Call: `cd artifacts/stock-scanner-api && bash tools/verified_run.sh "python portfolio_engine_verify.py --section ALL"`
- sha256 pinned in header output
- **Why:** cd inside the script causes double-path when called from workspace root

## pe_evidence_b2.py (tools/pe_evidence_b2.py)
39 B2 tests: Charm/Vanna/Vomma (3 TV × 3 greeks × 2 methods + mutation), beta_similarity (5 TV + mutation), stress math (5 TV + mutation).  
FD charm sign: `(delta_hi - delta_lo) / (2*dt) / 365` — positive, matching greeks.py convention (∂Δ/∂T per day).  
Vanna mutation: use TV3 (ITM, |d2|=0.69) not TV1 (ATM, d2≈0 → near-zero vanna → unmeasurable relative shift).

## PE Phase 2 final state
177/177 PASS in portfolio_engine_verify.py; config_sha=1799776c18b69ae5a6a7a5471b76f7a72bbb01db790e67544d32cf434dd75758  
4 ape_ tables confirmed live in DB (ape_gate_decisions has 6 rows from test runs).  
PE_GATING_ENABLED=False (observe mode).
