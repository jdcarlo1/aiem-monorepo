---
name: Directive 14 Tier-1 Wiring
description: GARCH/VPIN/Hurst wiring into bull_bear_debate.signal_context — production path, root cause, and fix
---

## Root cause (D14 Phase 1-2 audit)

### VPIN & Hurst
- Computed correctly in `layer9_statistical_edge.py:compute_layer9_score()`
- Stored at `packet.microstructure["components"]["vpin_toxicity"]["raw"]` etc.
- But `_h_bull_bear_debate` passed the entire nested dict as `signal_context["microstructure"]`
- Debate functions read flat top-level keys via `signal_context.get("vpin_raw")` → always None
- **Result: VPIN and Hurst contributed zero to every verdict.**

### GARCH
- Computed in `specialist_council.py` but `_h_specialist_council` runs AFTER `_h_bull_bear_debate`
- GARCH result was therefore never available to the debate
- **Result: GARCH vote was always 0 in the debate.**

## Critical production path discovery (D14 Phase 3)

`run_full_cycle()` is **only called in `aiem_master_orchestrator.py` line 2047 inside
`if __name__ == "__main__":` — test-only, NEVER called in production.**

The actual production path at 9:42 AM ET:
```
APScheduler CronTrigger(mon-fri 09:42 ET, id="aiem_paper_execute")  [main.py L16049]
  → lambda: _aiem_paper_execute_today(trigger_source="scheduled_942")
  → _aiem_paper_pick_candidates()
  → for _top in picks[:3]: _bull_bear.run_bull_bear_debate(_tt, _ctx)  [L17222→17330]
```

`_ctx` was built with only 5 keys: `price, trade_type, signal_source, signal_detail, score`.
No D14 keys reached the debate via this path before the fix.

## The fix (D14 Phase 3b — main.py lines 17226–17330)

Injected between `_ctx = {...}` (L17225) and `_deb = run_bull_bear_debate(...)` (L17330):

1. **DB read from layer9_scores** — direct columns, no recomputation:
   `SELECT statistical_score, regime, hurst_raw, vpin_raw, computed_at WHERE ticker=%s`
   → injects `layer9_score`, `layer9_regime`, `hurst_raw` (0.5 default), `vpin_raw` (0 default)
   → `hurst_score=0.0`, `vpin_score=0.0` (not stored; raw values drive all debate logic)

2. **GARCH compute from polygon_market_daily bars (~35ms)** — not stored in layer9_scores:
   `garch_regime_indicator(df)` from `volatility_clustering.py`
   → injects `garch_vote`, `garch_reason`

3. **Writes D14_LAYER9 + D14_DEBATE_PRE** to `.local/d14_live_capture.log` before debate.
4. **Writes D14_DEBATE_POST** after debate (includes `d14_tier1_activation` map).
5. All blocks isolated in `try/except` — never disrupts production.

**Why vpin_score/hurst_score are 0.0:** These aren't stored in layer9_scores (only computed
transiently inside compute_layer9_score). The raw values are the only thing that drives all
threshold-based debate logic in bull_bear_debate.py.

## Evidence chain
- `/tmp/d14_p2_evidence.log` — 11 entries, CHAIN VALID as of 2026-07-15
- `.local/d14_live_capture.log` — 6 prior entries (direct-invoke trigger); will receive
  `D14_LAYER9/PRE/POST` with `trigger_source=scheduled_942` after 9:42 AM ET scheduled run
- Confirmation log tags in stock-api stdout: `[D14_INJECT]`, `[D14_DEBATE_POST]`
- Orchestrator path (test-only): `[D14_BULL_BEAR]`

## Prior smoke test results (direct-invocation, not scheduler path)
- VPIN=0.073 (low toxicity) → `vpin_in_bull_thesis=true` ✓
- Hurst=0.759 (trending) → `hurst_in_bull_thesis=true` ✓
- GARCH vote=0 (neutral/flat) → correctly silent in both theses ✓
- All 8 checks_passed in D14_LIVE_DEBATE_POST event ✓

## Status as of 2026-07-15 00:15 ET
D14 Tier-1 PENDING — scheduler injection code live since 04:13 UTC restart.
Real 9:42 AM ET evidence fires in ~9h 27min.
After run: grep `[D14_INJECT]` and `[D14_DEBATE_POST]` in stock-api logs + read capture file.
