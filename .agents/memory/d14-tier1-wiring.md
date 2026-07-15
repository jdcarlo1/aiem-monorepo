---
name: Directive 14 Tier-1 Indicator Wiring
description: Root cause, fix, and evidence for GARCH/VPIN/Hurst wiring into bull_bear_debate — all three were functionally inert before D14
---

## Root Cause (all three indicators — discovered Phase 2 audit)

### VPIN & Hurst
- Computed correctly in `layer9_statistical_edge.py:compute_layer9_score()`
- Stored at `packet.microstructure["components"]["vpin_toxicity"]["raw"]` and `["hurst_regime"]["raw"]`
- But `_h_bull_bear_debate` in orchestrator passed the entire nested dict as `signal_context["microstructure"]`
- Debate functions (`build_bull_case` etc.) read flat top-level keys via `signal_context.get("vpin_raw")` → always `None`
- **Result: VPIN and Hurst contributed zero to every bull/bear verdict ever.**

### GARCH
- Computed in `specialist_council.py` (line 82, `garch_regime_indicator(df)`)
- But `_h_specialist_council` runs AFTER `_h_bull_bear_debate` in the orchestrator pipeline (confirmed by stage order: layer9 → bull_bear → specialist)
- GARCH result was therefore never available to the debate
- **Result: GARCH vote was always 0 in the debate.**

### Existing bug found during audit
- Even existing indicators (rvol, rsi, cmf) were nested under `packet.technical` → `signal_context["technical"]["rvol"]`
- Debate functions read `signal_context.get("rvol")` (flat) → also always `None`
- **All debate scoring was running on None/0 for every indicator.**

## Fix Applied (D14 Phase 2)

### aiem_master_orchestrator.py — `_h_layer9_statistical_edge`
- Added GARCH computation inline at the END of this stage (same stage as VPIN/Hurst, runs before debate)
- `garch_regime_indicator(df)` called, result stored in `packet.microstructure["garch_vote"]` / `["garch_reason"]`
- Guarded: requires `len(rows) >= 30`; fails-safe to `vote=0`

### aiem_master_orchestrator.py — `_h_bull_bear_debate`
- Now extracts `_tech = packet.technical or {}` and `_ms = packet.microstructure or {}`
- Flattens ALL technical fields to top-level signal_context keys: `rvol`, `rsi_14`, `cmf_20`, etc.
- Extracts VPIN: `signal_context["vpin_raw"] = _ms_comp.get("vpin_toxicity", {}).get("raw", 0)`
- Extracts Hurst: `signal_context["hurst_raw"] = _ms_comp.get("hurst_regime", {}).get("raw", 0.5)`
- Extracts GARCH: `signal_context["garch_vote"] = _ms.get("garch_vote", 0)`
- Added `[D14_BULL_BEAR]` print line for live-confirmation evidence capture

### bull_bear_debate.py — all 4 functions
- `build_bull_case`: reads `garch_vote`, `vpin_raw`, `hurst_raw`; scores +0.05/+0.05/+0.08 on bullish conditions
- `build_bear_case`: reads all three; scores +0.08/+0.10/+0.05 on bearish conditions  
- `run_risk_review`: reads `vpin_raw` (>0.70 → +0.15 risk), `garch_vote` (-1 → +0.10 risk)
- `run_contradiction_check`: adds `vpin_vs_cmf` contradiction type (VPIN>0.50 conflicts CMF>0.05)

## SHA-256 Evidence
- `bull_bear_debate.py` BEFORE: `a0a1d4bd04090e048a7f8f2364d00b5ae43d6037c2b68ba0456a0fb0c80b92c7`
- `bull_bear_debate.py` AFTER: `6d6f8433ab7fa7e2f71cd1c0254bb76de692752f9d8e5a3825f88e4932e7217e`
- `aiem_master_orchestrator.py` AFTER (final, includes log line): `f7bb37bd59c6b3ce276d9df2d31f83fe02edac4a87bcbf9bc6628829ef60a738`
- Evidence chain: `/tmp/d14_p2_evidence.log` (8 entries, CHAIN VALID)

## Live Confirmation
- Log tag: `[D14_BULL_BEAR]` fires for every ticker entering the debate
- Will appear in stock-api logs at 9:42 AM ET when `_aiem_paper_execute_today()` runs the orchestrator
- Grep: `grep "\[D14_BULL_BEAR\]" /tmp/logs/artifactsstock-scanner_stock-api_*.log`

## Smoke Test Results (entry #7, exit_code=0)
- Test A (bullish context): BULL SCORE=0.730, verdict=BUY, GARCH/VPIN/Hurst all in bull_thesis ✓
- Test B (bearish context): BEAR SCORE=0.880, risk=HIGH, GARCH/VPIN/Hurst all in bear_thesis ✓
- Test C (VPIN/CMF contradiction): `vpin_vs_cmf` contradiction fired ✓
- Test D (neutral): all three indicators correctly silent ✓
