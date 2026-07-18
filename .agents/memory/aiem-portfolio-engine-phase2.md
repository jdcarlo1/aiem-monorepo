---
name: AIEM Portfolio Engine Phase 2
description: aiem_portfolio_engine/ package — 11 submodules, 97/97 verified, PE_GATING_ENABLED=False observe mode
---

# AIEM Portfolio Engine Phase 2

## Package location
`artifacts/stock-scanner-api/aiem_portfolio_engine/`

## Submodule map
| File | Spec sections |
|---|---|
| config.py | constants + pe_config_sha() |
| db.py | bootstrap_portfolio_tables() — 4 tables |
| snapshot.py | S1 portfolio snapshot |
| greeks.py | S2 aggregate Greeks |
| limits.py | S3+S7 concentration + risk budget |
| correlation.py | S4 correlation/duplicate-risk |
| stress.py | S5 17-scenario stress test |
| valuation.py | S6 liquidity-adjusted valuation |
| optimizer.py | S8 Kelly + utility optimization |
| gate.py | S9+S11+S12 orchestrator + audit chain |
| __init__.py | exports run_portfolio_gate + PortfolioDecision |

## DB tables (all `ape_` prefixed)
- `ape_portfolio_snapshots`
- `ape_portfolio_greeks`
- `ape_stress_results`
- `ape_gate_decisions`

## Key constants (from config.py)
- `PE_GATING_ENABLED = False` (observe mode — never blocks trades until toggled)
- `PORTFOLIO_CAPITAL = 100_000`
- `MAX_CORRELATION_CLUSTER_EXP = 0.30` (30% cluster cap)
- `STRESS_TEST_LOSS_LIMIT = 15_000`
- `config_sha = 00de7b8c6a6516476bc50508b73a090783c3d9de1b10bff69c6eff897298eba4`

## gate_passed() rule
`gate_passed()` checks `self.pe_gating_enabled` (instance field), NOT the global `PE_GATING_ENABLED`.
This allows test code to override per-instance. In production, `run_portfolio_gate()` always passes the global constant through.

**Why:** Global constant is False (observe mode). Tests that probe enforcement behavior must pass `pe_gating_enabled=True` to the PortfolioDecision constructor.

## NOT_IMPLEMENTED_V1 items (6)
intraday_correlation, market_depth_L2, candidate_combination_optimization, common_factor_exposure, pending_orders, realized_pnl_intraday

## Scheduler wiring
`aiem_strat_scheduler.py` — `run_portfolio_gate()` called between `decision==TRADE` and `insert_paper_trade()`. Import at top, `PE_GATING_ENABLED` guard wraps the call.

## Verification
- 97/97 PASS (31 positive + 17 NC + 49 additional)
- Chain: `artifacts/stock-scanner-api/evidence_chain.log` seq=1
- entry_hash: `a5bfc0a1106e9f0e4dba98fb7126f5ab812a15f4ca59861b2ba7a349a4bc9e1e`
- output_sha256: `2271c6b5556e80ba9df4d035e53cecda0f887d01b92e2ba7f399c03d3b79a527`
- Verify script: `artifacts/stock-scanner-api/portfolio_engine_verify.py`

## To enable enforcement
Change `PE_GATING_ENABLED = False` → `True` in `config.py` once >= N live-observe cycles validate behavior.
