---
name: AI Short Calls enrichment
description: How the AI Short Calls tab selects picks and what signals feed the AI
---

## Signal pipeline

1. Unusual calls scanner populates `unusual_calls_log` (Vol/OI, premium, urgency)
2. `_bg_aisc()` background thread runs OpenAI call — signals sent include:
   - Vol/OI, prem, OTM%, IV, urgency, days_out (from unusual_calls_log)
   - `conviction_stack` score from `conviction_stack_watchlist` (total_pts/10 + layer names)
   - `oi_buildup` days from `oi_daily_snapshot` (distinct snapshot days last 7d)
3. AI ranks by: conviction_stack ≥8 first → oi_buildup 3d+ second → Vol/OI third
4. SMP enrichment (smp_score/label/layers) added AFTER AI picks — shown on card, not used for selection

## Fallback behavior
- conviction_stack: if table empty (holiday, scanner not run) → sends `NO_DATA` to AI, no crash
- oi_buildup: if no snapshot rows → sends `0d`, no crash
- Both wrapped in try/except independently

**Why:** Sweeps alone are 40-50% noise. Adding conviction_stack (dark_pool+short_int+sweep all confirming) upgrades the pick to a multi-signal setup — historically much higher probability.
