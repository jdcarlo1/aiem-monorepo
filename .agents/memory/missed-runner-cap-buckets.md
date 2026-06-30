---
name: Missed-runner cap-bucket report (4:45 PM job)
description: How aiem_missed_runner_analysis buckets missed movers by market cap and explains why each was missed; shared fingerprint module; ticker-cap cache.
---

The 4:45 PM ET `aiem_missed_runner_analysis()` job in `aiem_autonomous.py` now groups missed 20%+
movers into 4 market-cap tiers (micro <$300M, small $300M-$2B, mid $2B-$10B, large >$10B), top 20
per tier, with a per-ticker "why missed" reason chosen by priority: behavioral-fingerprint match
(sim>=0.92 vs `pre_move_templates`) > opening-gap proxy (>=5%) > news catalyst > volume surge (>=3x)
> "no clear precursor". Delivery is Telegram-only for this process (one overview + one message per
non-empty tier, well under the 4096-char cap) — `aiem_autonomous.py` has no email channel; that only
exists in `main.py`'s web app.

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
