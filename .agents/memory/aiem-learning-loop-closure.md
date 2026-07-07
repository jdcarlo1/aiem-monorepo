---
name: AIEM learning loop closure
description: How the closed learning loop actually works now — what reads what, where the gates are, and what proof exists
---

## Rule
signal_trust_weights (PAPER_TRADING context) is the read-back path for trust weights.
drift_check_log verdict is the read-back path for the decay analyzer.
Both are read inside _aiem_paper_pick_candidates() at each 9:35 AM execution.

## Gate 1 — Drift penalty (main.py, before def _add)
- Reads drift_check_log DISTINCT ON signal_source last 4 days
- ALERT_UNDERPERFORMING → _drift_mult[source] = 0.35
- Applied inside _add(): _eff = score * _drift_mult.get(source, 1.0)
- Log prefix: [learning_gate] {source} score×0.35 (live_wr=... gap=...pp)

## Gate 2 — Trust weight (main.py, before "Apply macro risk-off")
- Reads signal_trust_weights WHERE context_bucket='PAPER_TRADING' AND n_outcomes_observed>=5
- Applies tw multiplier directly: _twcand["score"] = round(score * tw, 4)
- Log prefix: [learning_gate] trust_weight {source}×{tw:.3f} (n={n})

## Write path — MTM exit (main.py, inside _aiem_paper_mark_to_market)
- After each trade closes: inline EMA update to signal_trust_weights
- EMA: new_wr = 0.95 * prior + 0.05 * outcome; trust = clamp(new_wr * 2, 0.2, 2.0)
- Context bucket: PAPER_TRADING
- Log prefix: [learning_gate] trust updated: {src} WIN/LOSS pnl=...% → trust=... (n=...)
- Does NOT call meta_learning_signal_trust.update_trust_weight() (requires AIEM_DATABASE_URL)

## Current trust weights (July 2026, after 81-trade backfill)
- multi_signal:    0.200 (floor)  — 0% EMA WR, 31 trades
- unusual_calls:   0.200 (floor)  — 0% EMA WR, 9 trades  
- gap_volume:      0.787          — 39% EMA WR, 37 trades
- aiem_ai:         1.805          — 90% EMA WR, 3 trades (small sample)
- conviction_stack: 2.000 (cap)   — 100% EMA WR, 1 trade (small sample)

## Combined effect
multi_signal with ALERT_UNDERPERFORMING:
- Gate 1: ×0.35 (drift penalty)
- Gate 2: ×0.20 (trust weight)
- Net: ×0.07 effective score — functionally excluded from top picks

## Proof of functional closure (July 7 2026)
LRCX: picked 4 consecutive days (June 30 – July 6) through 3 losses and ALERT_UNDERPERFORMING.
After changes: LRCX BLOCKED — NO_TRADE on first triggered cycle.
Log: [gate] LRCX BLOCKED — NO_TRADE (edge=insufficient_data, regime_ok=True, risk_ok=False)

## What's still not functional
- Step 5 (discovery): rejects 100% of templates because no template beats baseline+2pp
  in current OOS window — this is correct behavior, not a bug (market has weak patterns)
- Step 6 (variations): nothing passes step 5 to reach this stage
- Thompson sampler: dc_template_feedback still 0 rows (no template ever promoted)
- These are the remaining items on the roadmap's #1 priority
**Why:** The most dangerous failure mode is writes that are never read. The drift gate
and trust weight read paths are the minimal viable closure of the loop.
