# AIEM Portfolio Engine — Phase 2 Verified Work
**Date:** 2026-07-18  
**Result:** 97 / 97 PASS — 0 FAIL  
**Evidence chain:** `artifacts/stock-scanner-api/evidence_chain.log` seq=1  
**Entry hash:** `a5bfc0a1106e9f0e4dba98fb7126f5ab812a15f4ca59861b2ba7a349a4bc9e1e`  
**Output SHA-256:** `2271c6b5556e80ba9df4d035e53cecda0f887d01b92e2ba7f399c03d3b79a527`  
**Config SHA-256:** `00de7b8c6a6516476bc50508b73a090783c3d9de1b10bff69c6eff897298eba4`

---

## Package Structure

```
artifacts/stock-scanner-api/aiem_portfolio_engine/
├── __init__.py        exports: run_portfolio_gate, PortfolioDecision
├── config.py          constants + pe_config_sha()
├── db.py              bootstrap_portfolio_tables() — 4 ape_ tables
├── snapshot.py        S1  Portfolio Snapshot Engine
├── greeks.py          S2  Aggregate Portfolio Greeks
├── limits.py          S3+S7  Concentration Controls & Risk Budgets
├── correlation.py     S4  Correlation & Duplicate-Risk
├── stress.py          S5  17-Scenario Portfolio Stress Test
├── valuation.py       S6  Liquidity-Adjusted Valuation
├── optimizer.py       S8  Kelly + Utility Portfolio Optimization
└── gate.py            S9+S11+S12  Orchestrator + Audit Evidence Chain
```

---

## 4 New Database Tables

| Table | Purpose |
|---|---|
| `ape_portfolio_snapshots` | Point-in-time portfolio state snapshots |
| `ape_portfolio_greeks` | Aggregate delta/gamma/vega/theta/rho/charm/vanna/vomma per snapshot |
| `ape_stress_results` | Per-scenario stress P&L across 17 scenarios |
| `ape_gate_decisions` | Every gate decision with full audit JSON + SHA-256 chain |

---

## Key Configuration (config.py)

| Constant | Value |
|---|---|
| `PE_GATING_ENABLED` | `False` (observe mode — logs every decision, never blocks) |
| `PORTFOLIO_CAPITAL` | `$100,000` |
| `CONTRACT_MULTIPLIER` | `100` |
| `MAX_TICKER_CONCENTRATION` | `20%` of portfolio |
| `MAX_SECTOR_CONCENTRATION` | `35%` of portfolio |
| `MAX_CORRELATION_CLUSTER_EXP` | `30%` of portfolio per named cluster |
| `MAX_SIMULTANEOUS_POSITIONS` | `12` |
| `MAX_BUYING_POWER_UTILIZATION` | `80%` |
| `MAX_PORTFOLIO_DELTA` | `300` dollar-delta |
| `STRESS_TEST_LOSS_LIMIT` | `$15,000` |
| `KELLY_FRACTION` | `0.25` (quarter-Kelly) |

---

## Scheduler Wiring

`aiem_strat_scheduler.py` — `run_portfolio_gate()` is called between `decision == TRADE` and `insert_paper_trade()`.  
Gate runs in observe mode: result logged to `ape_gate_decisions`, trade never blocked until `PE_GATING_ENABLED = True`.

---

## All 97 Verified Tests

### [IMPORTS] Module structure (16 tests)

| # | Test | Result |
|---|---|---|
| IMP01 | aiem_portfolio_engine package imports | PASS |
| IMP01 | run_portfolio_gate exported from package | PASS |
| IMP01 | PortfolioDecision exported from package | PASS |
| IMP_CONFIG | aiem_portfolio_engine.config imports | PASS |
| IMP_DB | aiem_portfolio_engine.db imports | PASS |
| IMP_SNAPSHOT | aiem_portfolio_engine.snapshot imports | PASS |
| IMP_GREEKS | aiem_portfolio_engine.greeks imports | PASS |
| IMP_LIMITS | aiem_portfolio_engine.limits imports | PASS |
| IMP_CORRELATION | aiem_portfolio_engine.correlation imports | PASS |
| IMP_STRESS | aiem_portfolio_engine.stress imports | PASS |
| IMP_VALUATION | aiem_portfolio_engine.valuation imports | PASS |
| IMP_OPTIMIZER | aiem_portfolio_engine.optimizer imports | PASS |
| IMP_GATE | aiem_portfolio_engine.gate imports | PASS |
| IMP_SCHED | aiem_strat_scheduler.py is readable for import check | PASS |
| IMP_WIRE | run_portfolio_gate referenced in aiem_strat_scheduler.py | PASS |
| IMP_WIRE | PE_GATING_ENABLED guard in aiem_strat_scheduler.py | PASS |

### [S1] Portfolio Snapshot Engine (8 tests)

| # | Test | Detail |
|---|---|---|
| P01 | Empty snapshot reconciled | PASS |
| P01 | n_open_positions == 0 | PASS |
| P01 | cash_available == PORTFOLIO_CAPITAL | got 100,000.0 — PASS |
| P02 | committed_capital sum | got 6,500 — PASS |
| P02 | n_open_positions == 3 | PASS |
| P02 | cash_available correct | got 93,500.0 — PASS |
| P03 | _get_sector("AAPL") → XLK | PASS |
| P04 | LONG_STRADDLE = is_long_vol | PASS |
| P04 | IRON_CONDOR = is_short_vol | PASS |
| NC1 | Bad snapshot → reconciled=False | PASS |
| NC1 | Bad snapshot has reconcile_error | PASS |
| NC2 | Empty snapshot n_positions == 0 | PASS |

### [S2] Aggregate Portfolio Greeks (14 tests)

| # | Test | Detail |
|---|---|---|
| P05 | Empty portfolio delta=0 | PASS |
| P05 | Empty portfolio gamma=0 | PASS |
| P05 | Empty portfolio vega=0 | PASS |
| P05 | Empty portfolio theta=0 | PASS |
| P06 | Single long call delta > 0 | got 45.0 — PASS |
| P06 | Single long call gamma > 0 | got 2.0 — PASS |
| P06 | Single long call theta < 0 (time decay) | got -5.0 — PASS |
| P06 | Single long call vega > 0 | got 15.0 — PASS |
| P07 | Short call delta < 0 | got -45.0 — PASS |
| P08 | BCS net delta = 20 (long 150C + short 155C) | expected 20.00, got 20.0000 — PASS |
| P09 | 2 contracts × 0.50 delta × 100 = 100 | got 100.0 — PASS |
| P10 | AFTER delta > BEFORE delta (candidate adds delta) | before=45.0, after=75.0 — PASS |
| NC3 | Short put delta is non-zero | got 40.0 — PASS |
| NC4 | CONTRACT_MULTIPLIER is 100 (not 1) | PASS |
| P11 | BS fallback for missing greeks gives non-zero delta | got 99.65 — PASS |

### [S3+S7] Concentration Controls & Risk Budgets (10 tests)

| # | Test | Detail |
|---|---|---|
| P12 | Empty portfolio — no concentration breaches | 0 breaches — PASS |
| P13 | AAPL ticker concentration breach detected | ticker_pct=23.00% — PASS |
| P14 | Sector concentration breach for XLK | sector_pct=37.00% — PASS |
| NC5 | Position count limit breach detected | PASS |
| P15 | Buying power utilization breach | bp_util=84.00% — PASS |
| NC6 | Undefined-risk strategy always blocked | PASS |
| P16 | Healthy portfolio passes risk budget | 0 breaches — PASS |
| NC7 | Portfolio delta breach detected | \|delta\|=400.0 > 300.0 — PASS |
| NC8 | Stress test loss limit breach detected | worst_stress=-20,000, limit=15,000 — PASS |

### [S4] Correlation & Duplicate-Risk (6 tests)

| # | Test | Detail |
|---|---|---|
| P17 | Empty portfolio — no correlation risk | action=APPROVE — PASS |
| P18 | mega_tech cluster breach → REDUCE/REJECT | 3×8k+8k=32k=32% > 30% — action=REDUCE — PASS |
| P19 | intraday_correlation declared NOT_IMPLEMENTED | PASS |
| NC9 | Extreme correlation score → REJECT | duplicate_risk=0.9 — PASS |
| P20 | Semis cluster detected for NVDA+AMD+INTC | clusters=['semis'] — PASS |

### [S5] Portfolio Stress Test (8 tests)

| # | Test | Detail |
|---|---|---|
| P21 | Exactly 17 stress scenarios run | got 17 — PASS |
| P21 | All required scenario names present | PASS |
| P22 | Long call gains on spot_up_2pct | pl_portfolio=144.00 — PASS |
| P23 | Adding 5 long calls makes spot_down_5pct P/L worse | before=-281.25, after=-1,312.50 — PASS |
| NC10 | Stress limit breach check runs without error | breach count=10 — PASS |
| P24 | worst_stress_loss returns finite value | wsl=-1,482.75 — PASS |
| P24 | worst_stress_loss == min(pl_combined) | wsl=-1,482.75, min_pl=-1,482.75 — PASS |

### [S6] Liquidity-Adjusted Valuation (5 tests)

| # | Test | Detail |
|---|---|---|
| P25 | Conservative value <= mid value | cons=196.0, mid=300.0 — PASS |
| P26 | Multi-leg liquidation cost >= 0 | exit_cost=100.00 — PASS |
| P27 | Candidate increases liq-adj max loss | PASS |
| NC11 | Liquidity limit breach check runs | breach=True — PASS |
| P28 | market_depth_L2 declared NOT_IMPLEMENTED | PASS |

### [S8] Portfolio Optimization (5 tests)

| # | Test | Detail |
|---|---|---|
| P29 | Optimization returns valid decision | got APPROVE — PASS |
| P29 | Both utility scores are non-negative | PASS |
| P30 | BP exhaustion leads to REJECT or REDUCE | decision=REJECT — PASS |
| NC12 | candidate_combination_optimization declared NOT_IMPLEMENTED | PASS |
| P31 | Zero-EV candidate returns valid decision | got DEFER — PASS |

### [S9+S11+S12] Gate Orchestrator + Audit Evidence (17 tests)

| # | Test | Detail |
|---|---|---|
| P32 | PE_GATING_ENABLED == False (observe mode) | PASS |
| P33 | pe_config_sha() returns 64-char lowercase hex | got 00de7b8c… — PASS |
| P34 | _evidence_hash is deterministic | PASS |
| P34 | Evidence hash is 64 chars | PASS |
| P35 | Evidence hash changes when payload changes | PASS |
| P36 | GENESIS_HASH is 64 zeros | got 0000000000000000… — PASS |
| P37 | gate_passed() == True in observe mode even for REJECT | pe_gating_enabled=False — PASS |
| P38 | gate_passed() == False for REJECT in gating mode | PASS |
| NC13 | Reconcile failure → REJECT | got REJECT — PASS |
| NC13 | Reconcile failure → approved_size=0 | PASS |
| NC14 | Chain produces unique hashes at each step | PASS |
| NC14 | Chain diverges from genesis | PASS |
| NC15 | Bypass attempt produces different hash (chain breaks) | PASS |
| P39 | NOT_IMPLEMENTED_V1 has >= 5 items | got 6 — PASS |
| P39 | pending_orders declared NOT_IMPLEMENTED | PASS |
| P40 | Config covers >= 20 keys | got 23 — PASS |
| P40 | PE_GATING_ENABLED in sha keys | PASS |
| P40 | STRESS_TEST_LOSS_LIMIT in sha keys | PASS |

### [DB] Table Bootstrap (5 tests)

| # | Test | Result |
|---|---|---|
| DB01 | bootstrap_portfolio_tables() ran without error | PASS |
| DB_TABLE | ape_portfolio_snapshots exists | PASS |
| DB_TABLE | ape_portfolio_greeks exists | PASS |
| DB_TABLE | ape_stress_results exists | PASS |
| DB_TABLE | ape_gate_decisions exists | PASS |

---

## 17 Stress Scenarios Covered

| Scenario | Description |
|---|---|
| spot_up_2pct | +2% spot move |
| spot_down_2pct | -2% spot move |
| spot_up_5pct | +5% spot move |
| spot_down_5pct | -5% spot move |
| spot_up_10pct | +10% spot move |
| spot_down_10pct | -10% spot move |
| spot_down_20pct | -20% spot move (crash) |
| vol_up_25pct | +25% IV spike |
| vol_down_25pct | -25% IV crush |
| vol_spike_50pct | +50% vol spike (VIX event) |
| time_decay_1day | 1 day theta burn |
| time_decay_1week | 7 day theta burn |
| spot_down10_vol_up30 | -10% spot + +30% vol (double-threat) |
| spot_up10_vol_down20 | +10% spot + -20% vol (vol crush on winner) |
| gap_down_15pct | -15% gap down (earnings miss) |
| gap_up_15pct | +15% gap up (earnings beat) |
| flat_decay_2weeks | 0% move + 14 days theta |

---

## 6 NOT_IMPLEMENTED_V1 Items (deferred to Phase 3)

These are logged in every `PortfolioDecision.not_implemented` field so nothing is silently missing:

1. `intraday_correlation` — real-time correlation vs open positions
2. `market_depth_L2` — L2 order book depth for liquidity sizing
3. `candidate_combination_optimization` — Kelly-optimal multi-candidate allocation
4. `common_factor_exposure` — macro-factor / beta-sector overlap detection
5. `pending_orders` — in-flight orders in buying-power calculation
6. `realized_pnl_intraday` — intraday MTM P&L on open positions

---

## To Enable Enforcement

When ready to move from observe → enforce:

```python
# artifacts/stock-scanner-api/aiem_portfolio_engine/config.py
PE_GATING_ENABLED = True   # change from False
```

Then re-run `python portfolio_engine_verify.py --section ALL` to confirm 97/97 still pass.
