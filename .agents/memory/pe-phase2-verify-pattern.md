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
177/177 PASS in portfolio_engine_verify.py; config_sha=a3cd9d610d824e0b635f4bcf9137187812e4d0770ec45de9d206dbeab92637b1  
4 ape_ tables confirmed live in DB (ape_gate_decisions has 8 rows, all from observe-mode runs).  
PE_GATING_ENABLED=True (enforcement mode — enabled 2026-07-23 per RISK-038).  
**Why enforcement was off:** deliberate observe-mode during shadow period. Enabled after negative-control test proved REJECT→gate_passed()=False and APPROVE→True.

## Phase 6 RISK-036–039 final verdicts
RISK-036 (violations block recommendations): PASS — PE_GATING_ENABLED=True; REJECT→gate_passed()=False; strat_scheduler.py:445 branch blocks insert_paper_trade().  
RISK-037 (gate before final decision): PASS — run_portfolio_gate() at strat_scheduler.py:432, insert_paper_trade() at :452.  
RISK-038 (gate unbypassable): PASS — no config bypass active; REJECT→gate_passed()=False.  
RISK-039 (blocked trades record exact rule): PASS — ConcentrationBreach.limit_name+details carry exact rule name and values.  
NOT_IMPLEMENTED_V1: 9 items (intraday correlation, L2 depth, pending orders, realized intraday, etc.) — by design.
