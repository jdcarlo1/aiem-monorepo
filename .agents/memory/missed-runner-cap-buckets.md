---
name: Missed-runner cap-bucket report (now part of 4:30 PM combined EOD report)
description: How the missed-runner compute logic buckets missed movers by market cap and explains why each was missed; shared fingerprint module; ticker-cap cache; merged into aiem_eod_report.
---

As of 2026-06-30, the old separate 4:30 PM `aiem_grade_outcomes()` + 4:45 PM
`aiem_missed_runner_analysis()` jobs were retired and merged into ONE 4:30 PM job,
`aiem_eod_report()`, because the user never reliably got the 4:45 PM report (see
`polygon-403-today-snapshot.md` for the root cause). The two compute paths were split into
pure functions — `_aiem_grade_predictions(conn, cur, today)` and
`_aiem_find_missed_runners(conn, cur, today)` — that return dicts instead of sending Telegram
directly; `aiem_eod_report()` calls both, builds `_aiem_build_narrative(grade, missed, today)`
("what we learned / how we'll improve" paragraph synthesized from win-rate + top missed reason),
and sends ONE combined Telegram message + supplementary chart/bucket-detail messages.

Missed 20%+ movers are grouped into 4 market-cap tiers (micro <$300M, small $300M-$2B, mid
$2B-$10B, large >$10B), **top 10 per tier as of 2026-06-30** (was top 20 — narrowed for genuine
deep-dive review rather than a long flat list), with a per-ticker "why missed" reason chosen by
priority: behavioral-fingerprint match (sim>=0.92 vs `pre_move_templates`) > opening-gap proxy
(>=5%) > news catalyst > volume surge (>=3x) > pre-move RSI/volume setup > "no clear precursor".
Delivery is Telegram-only for this process — `aiem_autonomous.py` has no email channel; that only
exists in `main.py`'s web app.

**Predictability check (added 2026-06-30):** same-day pattern tags above only explain what was
visible the day it happened. `_aiem_predictability_check(prior_bars, runner)` (pure function, pairs
with `_calc_rsi`) instead looks ONLY at bars strictly before today — 30-day OHLCV window — for: a
volume build-up (>=2x a 10-day baseline) the day before, the stock already moving >=5% the prior
day, or RSI(14) at an oversold (<=30) or overbought (>=70) extreme. Verdict is `predictable` (has a
precursor — AIEM arguably should have caught it) vs `no_precursor` (a true surprise, no warning in
the daily history). Counts are tallied per EOD run (`predictable_n`/`surprise_n`) and surfaced in
both the Telegram report and the learning narrative — first live run found all 4 missed runners
were `predictable` (e.g. 27x volume buildup, RSI 94 extreme, already-in-motion prior day).

`artifacts/stock-scanner-api/behavioral_fingerprint.py` is a new pure-function module
(`compute_fingerprint`, `cosine_similarity`, `best_template_match`) extracted so both `main.py` and
`aiem_autonomous.py` can compute the same 14-dim fingerprint without HTTP coupling between processes.
`main.py`'s own local fingerprint functions were deliberately left untouched/unmigrated (lower risk,
architect-approved) — the shared module is only wired into `aiem_autonomous.py` so far.

Market-cap lookups are cached in `aiem_ticker_reference_cache` (7-day TTL) via
`_aiem_get_ticker_reference_cached()` to avoid blowing Polygon rate limits when bucketing up to 150
candidates/day (`_MISSED_RUNNER_CAP_LOOKUP_LIMIT`).

**Why:** `pre_move_templates`/fingerprint math already existed in `main.py`'s behavioral engine and
is the deepest "why" signal available — duplicating it via HTTP would have added cross-process
latency/coupling for no benefit since both processes share the same DB.

**How to apply:** when adding more cap-tier-aware AIEM jobs, reuse `_aiem_cap_bucket()` /
`_aiem_get_ticker_reference_cached()` / `_aiem_behavioral_why()` rather than re-deriving market cap
or fingerprint logic locally.

**Self-reflection extension (added 2026-06-30, same day):** `_aiem_predictability_check` now also
checks THIS MORNING's premarket tape (`_aiem_get_premarket_minutes_yahoo` — Yahoo 1m chart with
`includePrePost=true`, since Polygon blocks same-day minute aggs on this plan), YESTERDAY's EOD
close position within its daily range (`eod_range_position`, top/bottom 15% on >=1.5x volume), and
a multi-day "slow grinder" streak (3-5 consecutive up-days, no single day >10%, >=8% cumulative).
`reason_cat` priority order is now: premarket > grind > EOD-close > fingerprint > gap > news >
volume > RSI > none. Each `reason_cat` maps to a fixed `(lesson, corrective_action)` tuple in the
module-level `_REASON_LESSONS` dict — both the per-ticker Telegram detail block and the aggregate
`_aiem_build_narrative()` paragraph pull from this same dict so the "what we learned" text never
drifts out of sync between the per-name view and the summary. User explicitly required the action
line to be a concrete next-time fix, not just a restated pattern name.

**Why:** the report was criticized as "trash"/too terse and as only logging patterns without saying
what changes as a result — a fixed lookup table keeps the lesson/action text deterministic (no LLM
call in this file) while still being genuinely per-pattern instead of generic boilerplate.

**How to apply:** any new `reason_cat` value added to the priority chain MUST get a matching entry
in `_REASON_LESSONS`, or it silently falls back to the generic "no clear takeaway" default.
