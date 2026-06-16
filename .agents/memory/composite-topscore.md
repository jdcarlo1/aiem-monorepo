---
name: Composite 8+ / TOP SCORE tab
description: Design decisions & conventions for StockScanner's "TOP SCORE 8+" feature (composite_scan.py + TopScoreTab) — read before changing scoring horizons, ETF exclusion, or the snapshot/track-record logic.
---

# TOP SCORE 8+ feature

The "💎 TOP SCORE 8+" tab shows today's full single-name 8.0+ composite list
(ETFs/funds excluded), ranked most-bullish→least, plus a daily track record of
the actionable cohort (score≥8, vol≥1.5×, non-fund). Backend lives in
`artifacts/stock-scanner-api/composite_scan.py`; UI is `TopScoreTab` in
`Dashboard.tsx`; client fetchers in `lib/api.ts`.

## ETF / fund exclusion — keep UNKNOWNs IN
- Classification is **in-process only** (yfinance `fast_info.quote_type`, fallback
  `.info`). Fresh/separate processes get yfinance-rate-limited, so it must run
  inside the long-lived stock-api process (snapshot job / scheduler).
- Coverage fills **gradually over daily runs** — on any given day most names are
  still `UNKNOWN`. That is expected, not a bug.
- **Never drop UNKNOWN names** to "clean" the list. UNKNOWN is treated as EQUITY on
  purpose; dropping them would silently remove ADRs/REITs/dual-class (e.g. MAC).
  `classify_missing` retries anything whose `status != 'ok'`, so unknowns get
  re-attempted; only the denylist + confirmed FUND_TYPES are excluded.
- **Why:** strict exclusion would cause false negatives (losing real stocks) that
  are worse than the occasional unverified fund slipping through; the tab surfaces
  the verified-vs-unclassified count so the gap is honest.

## Track-record horizon convention (decided)
- Entry = **next session OPEN** after snap_date (`future[0]` open, where `future`
  is bars strictly after snap_date) — this is what the user can actually buy, and
  it's free of look-ahead bias.
- 1/2/3/4 weeks = **5/10/15/20 trading sessions held** = exit at the **close of
  future index 4/9/14/19** (NOT 5/10/15/20 — `future[0]` is the entry session, so
  index 5 would be a 6-session hold). Off-by-one here was an early bug.
- Track record measures **STOCK** returns, not option P&L.

## snapshot_today must stay same-day idempotent
- It classifies the FULL 8+ set but logs only the cohort. Before upsert it
  **deletes** today's `composite_watchlist` rows whose ticker is not in the new
  keep set, so manual reruns / late reclassification can't leave a stale cohort.
- **Why:** snapshot is manually triggerable and reclassification can flip a name
  to a fund between runs; without the delete the day's cohort would only grow.

## Scheduling
- Scan 16:15, snapshot+classify 16:45, outcomes fill 9:50 + 17:00, Mon–Fri ET.
- snapshot gated on ≥1000 score rows for the day (don't log a half-built scan).
