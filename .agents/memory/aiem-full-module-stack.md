---
name: AIEM full module stack
description: All user-provided modules wired into main.py, tool counts, and key wiring conventions
---

## Module roster (complete as of June 27 2026 — user confirmed no more modules)
1. breakout_signature_discovery — breakout_discover, breakout_extract_features
2. premarket_gap_continuation_scanner — gap_continuation_score, squeeze_subscore
3. intraday_continuation_scanner — intraday_continuation_score, intraday_compute_features
4. automated_retrain_pipeline — retrain_pending/approve/reject/history (DB: retrain_runs)
5. smart_money_divergence_detector — divergence_scan, check_price_bullish
6. market_regime_overlay — regime_overlay_check, regime_overlay_manual
7. vwap_indicators — vwap_compute_features, vwap_price_vs, vwap_reclaim_detect
8. meta_learning_signal_trust — trust_classify_context, trust_update, trust_get_weights, trust_get_history, trust_apply_to_candidates (DB: signal_trust_weights + signal_trust_history)

**Total AIEM tools: ~57** (added 4 background-system live read tools: get_meta_learning_weights, get_m2_decay_status, get_m6_rediscovery_status, get_bh_fdr_status)

## Background-system live read tools (added 2026-07-03)
Pure DB-read tools that give AIEM mid-chat access to background pipeline outputs:
- `get_meta_learning_weights` — reads `signal_trust_weights` (rolling win rate + trust weight per signal/context bucket)
- `get_m2_decay_status` — reads `aiem_signal_discoveries` LEFT JOIN `aiem_signal_actions` (decay verdicts, realized WR, retire reasons)
- `get_m6_rediscovery_status` — reads `aiem_rediscovery_runs` (variations_tested/passed per retired parent signal)
- `get_bh_fdr_status` — reads `aiem_signal_discoveries` ordered by status/p_value (full BH-FDR corrected ledger)
All 4 are in BOTH tool maps (`_build_aiem_tool_map` + research agent map) + `_AIEM_AGENT_TOOLS` schemas.
Note: `trust_get_weights` (meta_learning module) already existed in research agent map; `get_meta_learning_weights` is the new general-purpose version in the primary map.

## Wiring conventions (must follow for every new tool)
- Relative imports (`from . import X`) → change to `import X`
- All wrappers use lazy imports inside the function (NOT top-level)
- Schema init blocks: SIBLING of outer [aiem_integrity] try/except, NOT inside it
- Add to: (1) _build_aiem_tool_map() dict, (2) research agent tool map dict (~line 35027), (3) _AIEM_AGENT_TOOLS schema list, (4) _AIEM_AGENT_SYSTEM prompt description

## Key architectural notes
- vwap_indicators: ANALYTICAL signal (not execution) — distinct from execution_simulator's TWAP/VWAP fill algos
- meta_learning: exponentially-decayed trust weights per (signal_name, context_bucket); 3 buckets: calm_supportive / mixed / volatile_or_cautious; needs ≥15 outcomes before adjusting trust
- trust_apply_to_candidates: call LAST before any multi-signal recommendation (after trust_get_weights confirms a signal is still healthy)

## Morning market-open AIEM sessions (4 new)
- 8:45 AM: premarket_gap_scan (10 iters, DB-only via mkt_get_stock_history + polygon_rvol_scan)
- 9:31 AM: market_open_9h31 (8 iters, cross-confirms 8:45 candidates)
- 9:35 AM: early_momentum_9h35 (8 iters, grind vs spike detection)
- 9:40 AM: followthrough_9h40 (8 iters, cross-session confirmation = highest confidence)
- All 4 call send_discovery_alert autonomously (max 5/day, 20 min cooldown)

**Why:** `_run_aiem_focused_session` spawns daemon threads OUTSIDE APScheduler's 4-worker pool — no scheduler contention. All morning tools use DB/OpenAI only (mkt_analyze_top_movers is pure SQL against polygon_market_daily).
