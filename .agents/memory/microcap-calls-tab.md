---
name: Micro/small-cap calls tab
description: Why the micro/small unusual-calls tab needs a hard market-cap ceiling, enforced twice, and where its coverage limit comes from
---

The "micro/small-cap calls" tab (`unusual_calls_microcap_log`,
`_run_microcap_options_scan` / `_scan_one`, `_get_microcap_tickers`) must enforce
a hard market-cap ceiling: **exclude anything >= $2B.**

## Enforce it in TWO places (defense in depth)
1. `_scan_one` skips (`return hits`) when `fast_info.market_cap >= 2_000_000_000`,
   placed BEFORE the `tk.options` fetch so large caps don't burn rate-limited
   yfinance option-chain calls.
2. The GET route adds `cap_tier IN ('nano','micro','small')` so a stale/bad `mid`
   row can never surface even if some other writer inserts one.

**Why:** the scan computed `cap_tier` (nano/micro/small/mid) but originally never
used it to filter, so 100% of a day's rows came back as `mid` — mega caps mislabeled
(AVGO ~$1.7T, NFLX, SNDK at $2037/share). Two pollution sources fed it: (a) curated
static-universe names graduate into large caps over time (RKLB/ASTS/RBLX are now
multi-$10B), and (b) large-cap Yahoo screeners (`most_actives`,
`undervalued_large_caps`, `portfolio_anchors`, `solid_midcap_growth_funds`) seeded
mega caps. Those screeners were removed; the $2B gate is the real backstop.

## Coverage ceiling (the honest limit)
Micro/small coverage is inherently capped by the free yfinance feed (scan runs at
~3 workers, rate-limits). Many static biotech names are delisted / no-data
(BLUE, SAVA, CNCE, ADAP, GRTS, SEAS, STER, FREYR, GNUS, ...). Biotech Finviz screens
were added (`ind_biotechnology`, `sec_healthcare`) but only names with *liquid*
options surface. Comprehensive biotech/micro coverage = the paid Polygon
full-market feed, same ceiling as every other scanner.

## Deliberate choice: unknown market_cap is KEPT
`mc_val == 0` (fast_info.market_cap unavailable) falls through labeled `micro` on
purpose — dropping unknowns would lose legit thin micro/biotech names. Tradeoff: a
large cap with missing market_cap could leak; the route-level `cap_tier` filter is
the backstop, and a `tk.info.get("marketCap")` fallback is the next step if leaks
recur.
