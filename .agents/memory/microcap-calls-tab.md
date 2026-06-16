---
name: Micro/small-cap calls tab
description: How the micro/small unusual-calls tab gets comprehensive coverage on the free feed (Finviz-meta gate + rotating shard), its $2B ceiling, and its loosened "unusual" thresholds
---

The "micro/small-cap calls" tab (`unusual_calls_microcap_log`,
`_run_microcap_options_scan` / `_scan_one`, `_get_microcap_tickers`) scans sub-$2B
optionable names for unusual CALL flow on the FREE yfinance + Finviz feed.

## Hard market-cap ceiling — enforce in TWO places (defense in depth)
1. `_scan_one` skips (`return hits`) when `market_cap >= 2_000_000_000`, BEFORE the
   `tk.options` fetch so large caps don't burn rate-limited option-chain calls.
2. The GET route adds `cap_tier IN ('nano','micro','small')` so a stale/bad row can
   never surface even if some other writer inserts one.

**Why:** the scan computed `cap_tier` but originally never filtered on it, so a day's
rows came back mislabeled `mid` (mega caps like AVGO/NFLX leaked in). Pollution
sources: curated static names that graduated to large cap (RKLB/ASTS), and large-cap
Yahoo screeners (now removed). The $2B gate is the real backstop.

## Coverage: the universe is comprehensive; the per-scan ceiling is Yahoo
- **Universe (~2,200 names):** built from Finviz `cap_smallunder,sh_opt_option`
  paginated. KEY: Finviz blocks CONCURRENT scraping but a SINGLE SEQUENTIAL
  paginated stream works fine (deep pagination, ~0.2s/page). Finviz MUST run
  sequentially, OUTSIDE the concurrent Yahoo ThreadPool.
- **Free price/cap gate (no yfinance per name):** `_finviz` parses the `v=111`
  screener ROWS (price, market_cap, change_pct, volume) into module-level
  `_microcap_meta`. `_scan_one` reads price/cap from `_microcap_meta` with ZERO
  `fast_info` calls — only option_chain calls remain. This killed a no_price death
  spiral (was no_price=1867) where per-name `fast_info` fell back to history() and
  rate-limited the whole scan. Names with NO Finviz row are skipped (`no_meta`) —
  the Finviz comprehensive universe already supersets optionable sub-$2B names.
  (This supersedes the older "unknown-cap kept as micro" choice.)
- **Per-scan ceiling = Yahoo option-chain rate limit.** A FRESH/uncontended session
  reads ~500–640 chains before the circuit breaker (`_rl_stop`, trips at 40 rate
  limits) stops it; a session contended with `ai_trades`/`options_flow` drops to
  ~170. workers=5, expiries capped ≤6 within ≤90 DTE.
- **Rotating shard sweeps the universe across the day.** Every scan does a fixed
  priority HEAD (80, biggest movers by |change%| then volume) + a ROTATING tail
  window. `_microcap_shard{date,pos}` advances AFTER each scan by the actual tail
  names processed (`scanned_ok+no_options+no_meta+no_price+large_skip+rate_limited+
  error − head`), so consecutive scans cover FRESH names (verified contiguous:
  start 0→608→940). The 4 scheduled scans (10:30/3:30/4:00/4:15 ET) + warmers
  accumulate distinct names; tab reads days=1 so coverage compounds in the DB.
  **Why advance by actual work, not a fixed step:** a fixed step (250) re-scanned an
  overlapping slice because each scan covers ~400–600 tail names → 0 net new.
  **Gotcha:** `_cov["total"]` is bumped for EVERY submitted future (even after the
  breaker short-circuits), so it equals the full universe — do NOT use it for cursor
  advancement; derive "processed" from the outcome sub-counters instead.

## "Unusual" thresholds — premium floor TIERED by cap (user-directed, June 2026)
`min_voi=1.0`, `min_vol=10`, `oi≥5`, `spread≤40%`, `otm≥−10%`; premium floor scales
with cap: **nano <$50M → $10K, micro $50M–$300M → $25K, small $300M–$2B → $50K**
(unknown-cap falls through as small). Far-OTM (>40%) still needs voi≥5 AND
prem≥$200K (hedge-fund sweep).
**Why:** the same dollar premium means very different things across tiers — what
moves a THIN stock is buying large relative to its float/volume (and the dealer
gamma hedging it triggers), not raw premium. The user iterated to this: 5K/15K flat
was too strict, 1K/2.5K flat admitted retail-sized noise that moves nothing, tiered
"real money" per size is the balance. Effect on one fresh scan: names_with_hits
21 (strict) → 36 (1K flat) → 12 (tiered conviction names).

## Concurrency + display
All scan entry points (manual POST, empty-tab auto-scan, scheduled jobs + warmers)
MUST be serialized — they funnel through a non-blocking module lock so overlapping
triggers can't race the shard cursor or double-spend the Yahoo budget; duplicates
log "scan already running" and bow out. GET route LIMIT is 1000 (was 200) so a full
day's accumulation can display. Shard cursor is in-memory (resets on restart,
self-heals via rotation); DB persistence deferred as low-value since the stock-api
isn't restarted mid-session in normal operation.

## The honest finding the user accepted
Even with full coverage, marginal hit-rate in the deep tail is ~1%; "hundreds of
distinct names in a single day" is not supported by actual option data at the strict
thresholds. The two real levers are (a) loosen thresholds (done) and (b) the paid
Polygon full-market snapshot (user adding later) for full per-scan coverage.
