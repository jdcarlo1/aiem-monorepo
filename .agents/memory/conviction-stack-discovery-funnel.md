---
name: Conviction stack discovery funnel
description: Why the 7-Layer Conviction Stack found "0 at 8+ / max 2/10", and the rule for widening it without blowing rate limits
---

# Conviction stack discovery funnel

The 7-Layer Conviction Stack scores candidates on 8 layers, but it can only
score a name on the HEAVY layers (short interest, dark pool, float pressure) if
that name is in the "active" set. Historically `active` was seeded ONLY from the
cheap in-memory signals (OI accumulation + charm + gamma). Consequence: a stock
being pre-positioned via far-OTM call sweeps, unusual call buying, or EOD
accumulation — but NOT yet showing OI build — only ever earned its single L7/L8
point and was capped around 2/10, so it stayed invisible. That is why the owner
saw "40 scored, 0 at 8+, max 2/10": the funnel was too narrow, the indicator was
fine.

**Rule:** seed the conviction `active` set from ALL the cheap daily signal tables
we already collect (far-OTM sweeps from `_get_far_otm_sweeps`, EOD accumulation
from `eod_accum_picks`), not just OI/charm/gamma — then let the heavy layers
enrich them. A sweep-seeded name that then picks up short-interest + dark-pool +
float can legitimately reach 8+ (verified: FCEL hit 8.5 EXTREME once seeded).

**Why ranking matters more than the cap:** the rate-limited yfinance work is
already internally bounded — `_get_short_interest` only fetches `tickers[:50]`
and `_get_float_pressure_signals` only `tickers[:30]`; dark pool is a single FINRA
file filtered in-memory. So widening the candidate pool does NOT meaningfully
increase yfinance load — what matters is the ORDER of `active`. Rank by a
seed_priority (real OI/charm/gamma points first, then sweep premium/vol_oi, then
accumulation score) so the strongest pre-positioned names land in the first
30/50 slots that actually get the short-interest/float fetch.

**Force/on-demand tickers must sort to the FRONT, not be appended at the tail** —
otherwise a forced single-ticker score silently misses short-interest/float
because L4/L6 only read the front of `active`. Give them top priority before the
sort.

**Score magnitude varies run-to-run** because the free yfinance L4/L6 fetches
intermittently rate-limit (you'll see `YFRateLimitError` in logs). The SAME name
can show 8.5 one minute and 6.0 the next purely because the float fetch failed.
This is the data-source ceiling, not a logic bug. Consistent, reliable, true
full-market conviction scoring requires a paid full-market feed (Polygon) — see
`scanner-data-source-ceiling.md` / `paid-data-feed-options.md`. That is a cost
decision for the owner; do not build it without sign-off.
