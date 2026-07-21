# AIEM DASHBOARD — PHASE A
## Code Inventory
**Generated:** 2026-07-21 | **Git HEAD:** 327a02c8 | **Scan scope:** artifacts/stock-scanner-api/

---

## Summary
- **Total Python modules:** 239
- **Main API file:** main.py (69,000+ lines, 333 routes)
- **Total API routes:** 333
- **Freeze status:** aiem_options_scheduler.py + aiem_paper_recovery.py FROZEN through 2026-07-22 09:45 ET

---

## Module Inventory by Category

### Core Engine Modules

| File | SHA-256 | Primary Function | Dashboard Area |
|------|---------|-----------------|----------------|
| main.py | ee8489ed5d7d5233e7f728a44b99e02606e5ad6c68034289d881eb9a10e6b423 | Flask app, 333 routes, 69k+ lines | ALL screens |
| aiem_options_scheduler.py | d622b70ffbe708c060b7495858cf6c651b456c1e1a78b244cddff14bfba2e8a4 | Options pipeline CronTrigger scheduler | System Operations |
| aiem_options_pipeline.py | bbcddcc13bd364bd4a49c4eb728b48f90194cc40ef676280e16c8e8d64a741e6 | Full options decision pipeline | Live Decisions |
| aiem_paper_recovery.py | b94944a43c9c6b1f01ccf6d23d44261f3258c755989bcbb99fce74d9baff894b | Paper trade recovery/claim logic | Paper Trading |
| aiem_options_intel.py | 442f9cda03d945b2602c39bfc42a3f451f11eec36585d93451743a09a8096b3c | Options chain intelligence | Options Intelligence |
| specialist_council.py | aa167486e9d84bcc218742496f54a097eabe3b2ea639f2caca6d4218627e2fde | Specialist council debate | Specialist Council |
| pre_decision_risk_gate.py | 9aab066031c8189322595f10e7edf6091f6e13ebe65436c49466411b1ca692a1 | Risk gate enforcement | Portfolio Risk |
| aiem_position_sizing.py | 65c90f37892e14ebeee1626650198e8245328d0c7ad72688610bfcb220424a9c | Position size computation | Portfolio Risk |
| aiem_attribution.py | f876fcfb27bbb31b345b645af7fdd2c213ba7487c916195cc4e104e7008d4846 | Trade attribution recording | Performance Analytics |
| aiem_closed_loop_learning.py | f354bf5aea9c3f2b7bc2378dc3cb91f54d18f6a5bab446c96e85b8e0ee933ecc | Learning loop stages 1-23 | Learning Center |
| regime_detector.py | 00de5304e04d723ff7337043b3fe8f3fd388670df7b9959db19c282dae1518cd | get_current_regime() from DB | Command Center |
| market_regime_overlay.py | 30aeae549e9680d51f7f0379ba9468044c7608762bf21e03ce735ec16a691a6c | combine_regime_votes(), weekly check | Command Center |
| aiem_macro_engine.py | ec825e65ff8cd6a0f25556f2b41b373f6b69030d6462fab0e6ef68fe291814c9 | Macro scoring (12 tables) | Command Center |
| aiem_premarket_intel.py | f0f0f4b54d4a06466f4f3475d78da40497a33bcaf60bf6fbf8a14fdaec915271 | Premarket intelligence | Opportunity Queue |
| aiem_optprob.py | ed4dc02fde073b5275e6b1fc193e7ea43bf30f4da560338cf093fc7730f4f37114 | compute_options_probability_matrix() | Probability & Calibration |

### Governance & Audit Modules

| File | SHA-256 | Primary Function | Dashboard Area |
|------|---------|-----------------|----------------|
| aiem_diagram3_governance.py | fe1e9e1ee51e66464ba5ef1277a42dd76eaf076583a96c92250a10a1ec7380e1 | D3 governance G0-G5, hash-chain | Audit & Verification |
| aiem_options_dpl.py | cd807a36bf82c2d70d00e8e6cd07b9bfa1fc2d219f7066ce94f53a5c5f1f30f1 | Decision Provenance Layer | Decision Proof |
| aiem_pipeline_audit.py | e2ec70e79f11082918cc2b19989794b4477a221a7e78a00a8e7d2c46350fba00 | Pipeline audit log writer | Audit & Verification |
| aiem_diagram2_trace_audit.py | 6f45ea77fed77f242ae960bce68aceadc4a40f136b373596a10ba0e1b8675f9f | D2 trace audit stage helpers | Decision Proof |
| decision_logging_helper.py | ff1b4aa2356295cce2c8d006793fdcb091aa88d2a512ff77665ed208bffb615f | Decision type logging wiring | Audit & Verification |
| aiem_security.py | 1c45484bb74da4816dca9920422c78a37642376dfdc955783b50b94258b37536 | HMAC signing, key rotation | Administration |
| aiem_provenance.py | 531dfb91623dd737a0b87ed80e90b86471b576350aaae8f3ceb2437c7e2236cc | Provenance chain tracking | Audit & Verification |

### Options Engine Modules (Phase 2-5)

| File | SHA-256 | Primary Function | Dashboard Area |
|------|---------|-----------------|----------------|
| aiem_options_phase2.py | ebc66070caa7b7ca2e51c6f66691dccb6f75758d65dc3e00f2abb8f8f943e0bf | Options Phase 2 scoring | Live Decisions |
| aiem_options_phase3.py | 2c9908aebe8cd70948da3dbe3bf11f7f8a2be98327e09723057b78b6ab917d2f | Phase 3 analysis/attribution | Performance Analytics |
| aiem_options_phase4.py | e2d99a79d7ca45c728bb144fa23902e658ed72d01161a839cd2a40898ed1abfb | Phase 4 portfolio/learning | Portfolio Risk |
| aiem_options_phase5.py | c57520412cb9ec4b5e04c3a4a7366afefd427012212e7a5993cb9fd6a25e46c5 | Phase 5 adaptive control/governance | System Operations |
| aiem_options_registries.py | cbab39e3e951f3428865dac39e5d3aaa122050543598d7c3251e2c1a1dd99bb8 | Strategy/indicator registries | Live Decisions |
| aiem_options_structure.py | 080c2c3383f7d5d89f2df4fc884c8b9496555ec19a99910305afc6caddb29da9 | Options structure scan | Options Intelligence |

### Statistical & ML Modules

| File | SHA-256 | Primary Function | Dashboard Area |
|------|---------|-----------------|----------------|
| layer9_statistical_edge.py | 07a7eb97869fc3a8aa0e47c5c20896f71114309e3dc0a8b8d902b76d2dcd6dcf | Layer 9 quant indicators | Indicator Laboratory |
| advanced_quant_indicators.py | 575cd47a3416fe99ce2ca7fce4e5f1e7f535757d04c329d334ba5f519135efe7 | GARCH, VPIN, Hurst, Amihud | Indicator Laboratory |
| aiem_stat_tests.py | 9765687a50d2e82ac766f7f3f3f583652a12fa3d0b741c513f8857dde22356cb | Fisher test, BH-FDR, lag harness | Research & Hypotheses |
| ml_engine.py | 4d7626030256c2f8bc06986db91f6e3330769ec768950b7354922dc2f7b4b3c2 | XGBoost ML pipeline | Learning Center |
| aiem_supervisor.py | 6f0fc43b3e1cb3a2a4ced7f9473832abf7280305c70657fb1e94908abb1672ae | 7-module supervisor meta-reasoning | System Operations |

### Signal Discovery Modules

| File | SHA-256 | Primary Function | Dashboard Area |
|------|---------|-----------------|----------------|
| aiem_discovery_engine.py | bcd97fb380f5839f5a425d22cffe263ae7a36fab3c0a75b6c2705d9e2c6322e1 | Signal discovery pipeline | Research & Hypotheses |
| aiem_signal_discoveries (table) | — | Stores 5 validated discoveries | Research & Hypotheses |
| aiem_module2_decay.py | 17af1719883ed46bd0d7b46880edab2bfcb0e28b5e14e0491c36df8f7929db2d | Signal decay analysis | Research & Hypotheses |
| aiem_module3_promotion.py | 6a5378cb74df935e0bb636646a3f1aa17e35c06f9a33a3edaa357f385cc061d8 | Signal promotion gates | Research & Hypotheses |
| hypothesis_registry.py | 46a766fae2a7bb3d9c51576519e643809dfda4605affc1a4e85885de69ed549e | Hypothesis tracking | Research & Hypotheses |

### Data Source Modules

| File | SHA-256 | Primary Function | Dashboard Area |
|------|---------|-----------------|----------------|
| scanner.py | 746c200e049027e9fcbbf7ec88d3c7e0b30de1168c295d209c72a9ba470c4ab0 | Core scanner engine | Opportunity Queue |
| indicators.py | 9806ddcb1832d663d5d9d90f010e0d4d42d8971b5b6d019d2fddc3e0dfa43496 | Technical indicator library | Indicator Laboratory |
| candlestick_patterns.py | dde58bddd9a8718bdfcacf09d4c7ab56322746e87a6aca8f06f89f47043b68ea | Candlestick pattern detection | Indicator Laboratory |
| fred_macro.py | 53325347e6204e4b3632dd1bf8f173941f751d01ce8149357dcef8df14d353c8 | FRED macro data fetching | Command Center |
| social_sentiment.py | 602563e37c83151f0c8d61a0b00c3ce303b9db017c1d69f126795edf6d981014 | Social sentiment scoring | Opportunity Queue |

---

## Key Function Locations

### Market Regime
- `regime_detector.py:88` — `get_current_regime(db_url, proxy_ticker)` — reads `regime_history` table
- `market_regime_overlay.py:203` — `combine_regime_votes()` — aggregates multiple regime signals
- `market_regime_overlay.py:301` — `get_weekly_regime_check()` — weekly lookback
- `main.py:43513` — `_aiem_tool_get_current_regime()` — AIEM tool wrapper
- `main.py:5811` — `_run_regime_monitor_job()` — scheduler job

### Premarket Intelligence
- `aiem_premarket_intel.py` — full premarket module
- `main.py:50988` — `/stock-api/premarket` route

### Candidate Scoring & Paper Trading
- `main.py:45562` — `_aiem_paper_pick_candidates()` — candidate selection engine
- `main.py:16910` — `_aiem_paper_execute_today(trigger_source, _test_mode)` — execution entry point

### Specialist Council
- `specialist_council.py:295` — `run_council(context, ticker, inputs)` — main entry point
- `specialist_council.py:253` — `persist_council_run(context, ticker, registered, ...)` — DB writer

### Risk Gate
- `pre_decision_risk_gate.py:173` — `run_risk_gate(...)` — full gate enforcement

### Probability Engine
- `aiem_optprob.py:252` — `compute_options_probability_matrix(...)` — probability matrix
- `main.py:48317` — `/stock-api/aiem-probability-engine/daily-picks` — serves picks

### Position Sizing
- `aiem_position_sizing.py:538` — `compute_position_size(...)` — sizing computation

### Attribution
- `aiem_attribution.py:117` — `record_attribution(...)` — writes attribution record
- `aiem_attribution.py:187` — `get_attribution_for_trade(trade_id)` — reads by trade
- `aiem_attribution.py:223` — `get_recent_attributions(limit)` — recent list

### Learning Loop
- `aiem_closed_loop_learning.py:550` — `log_learning_update_step(...)` — logs each stage

### Options Pipeline
- `aiem_options_scheduler.py:655` — `_execute_job(job_id, ticker, scan_date, claim_id)` — full pipeline execution
- `aiem_options_scheduler.py:2509` — `run_pipeline_worker(scan_date)` — worker wrapper

### Hash-Chain / Audit
- `aiem_diagram3_governance.py` — G0-G5 enforcement, d3_governance_event_links writer
- `dpl/engine_integrity_refs.json` — engine root hash (e8ee92c9...)
- `dpl/engine_manifest.py` — `build_manifest()` — live hash computation
- `tools/verified_run.sh` — chain wrapper, SEQ=61

---

## Verification Modules (not dashboard-facing, inventory only)
239 total Python modules include 40+ verification/backtest scripts (aiem_phase*_verify.py, ase_*_verification.py, verify_*.py, backtest_*.py). These are dev tools, not runtime — not dashboard-relevant.

---

## Modules Confirmed NOT Dashboard-Relevant
- All `backtest_*.py` (20 files) — offline analysis tools
- All `ase_*_verification.py` (11 files) — ASE audit scripts
- All `aiem_phase*_verify.py` (18 files) — phase verification harnesses
- All `d3_directive6_*.py` (3 files) — D3 enforcement test harnesses
- `self_coding_orchestrator.py` — experimental, not in production path
