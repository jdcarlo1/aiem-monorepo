# Clock reconciliation decision (Directive follow-up §2)

## Raw compare (12 Mondays, 2025-01-06 → 2025-04-07)

Source console: `CLOCK_COMPARE_CONSOLE.txt`  
Summary JSON: `CLOCK_COMPARE_SUMMARY.json`

| Method vs BT_ASOF | exact_match_pct | mean_abs_entry_diff | mean_abs_pnl_diff |
|-------------------|-----------------|---------------------|-------------------|
| narrow\|PAPER_0930 | 0.0 | 434.11 | 2129.44 |
| narrow\|PAPER_EXACT | 100.0 | 0.0 | 0.0 |
| rr\|PAPER_0930 | 0.0 | 397.14 | 6207.57 |
| rr\|PAPER_EXACT | 100.0 | 0.0 | 0.0 |

**Verdict:** pre-settle Monday 09:30 lookback fills **materially diverge** from catalog BT Monday daily closes. Not cosmetic.

## Decision (paper → match backtest)

- **Reject:** live lookback fill at Mon 09:30 using prior session premiums (`PAPER_0930`).
- **Adopt:** entry fill = Polygon daily option close dated **exactly that Monday** (`PAPER_EXACT` ≡ `BT_ASOF` on this window).
- **Code change:** `price_legs_polygon(..., require_exact=True)` on entry; status `WAITING_MONDAY_DAILY` until Monday bars exist.
- **Flatten:** keep expiry Friday ≥15:30 SPY-bar gate; option mark remains Polygon daily asof that day (catalog last-daily flatten). Intraday option quotes are not in the validated BT.

Flatten 15:30 vs “last daily bar” does not create a second option-price methodology when marks are daily aggs; the divergent methodology was entry lookback vs Monday close.
