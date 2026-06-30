---
name: Watch-criteria prospective scan alert flood fix
description: Why the missed-runner prospective re-screen flooded with ~1775 matches on first live dry-run, and how it was bounded
---

`_aiem_scan_watch_criteria()` (the T004 "re-screen tomorrow for yesterday's missed-runner pattern" feature in `aiem_autonomous.py`) flooded with **1775 new alerts in a single run** on first live dry-run against the real DB, with only 18 active criteria rows.

**Why:** for non-gap metrics (`eod_range_position`, `volume_buildup_x`, `rsi_14`, `grind_streak_days`), `threshold_value` is a generic *retrospective* detection bar (e.g. `rsi_14 >= 70`, `volume_buildup_x >= 2`) used by `_aiem_predictability_check` to EXPLAIN a missed runner after the fact. That bar is common across a multi-thousand-ticker price-banded universe, not rare — scanning the broad universe prospectively on the generic bar (instead of the missed-runner's own extreme `observed_value`) turns "yesterday's specific pattern reappeared" into "most of the market clears a loose threshold."

**How to apply:** any future broad-universe prospective re-screen built from retrospective explanatory thresholds needs all three of: (1) tighten the prospective threshold to the originating event's `observed_value` (`_aiem_effective_watch_threshold()`), not the generic bar, (2) per-criterion top-K cap by margin (`_WATCH_MAX_MATCHES_PER_CRITERION` = 5), (3) a global per-run cap after coalescing duplicate (ticker, metric) matches across multiple active criteria rows (`_WATCH_MAX_ALERTS_PER_RUN` = 25, in `_aiem_scan_watch_criteria`). Re-verified after the fix: same 18 active criteria → 25 alerts (capped), second call → 0 (dedupe via `aiem_watch_alerts` still intact). The `premarket_gap_pct` path is intentionally exempt from threshold-tightening — it already cross-checks against the small, already-gap/volume-filtered `candidates` list, not the broad universe.
