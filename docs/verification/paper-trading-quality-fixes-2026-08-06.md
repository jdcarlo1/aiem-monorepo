# Paper Trading Quality Fixes (2026-08-06)

Addresses the five Paper Trades quality gaps observed after the Aug 5–6 session.

## Issues → fixes

| # | Problem | Fix |
|---|---|---|
| 1 | Open CALLs showed `last_price: null`, `pnl: null` | Intraday job `aiem_paper_refresh_marks` (Mon–Fri 9–15 ET :05/:35); portfolio GET refreshes if any open mark is null; force-mtm refreshes before EOD MTM |
| 2 | Synthetic 2× underlying option P&L only | Capture `option_entry_mid` at open; prefer live Tradier option mid MTM when premium entry exists; otherwise show option last + labeled synthetic P&L |
| 3 | Near-duplicate OPEN mega-caps across days | Dedup blocks ticker if **any** `status='OPEN'` row exists (not only same `trade_date`) |
| 4 | Prob-engine identical AAPL/NVDA/PLTR scores | Detect identical prob vectors; apply Polygon rvol/gap tie-break + warning |
| 5 | Empty Loop B → scanner filled Paper book | Paper primary = today's `aiem_predictions` (`aiem_loop_b`); `scanner_ai_trades` is fallback / gap-fill |

## Deploy dependency (critical)

Items 1–4 are in this PR. Item 5 only works when **Loop B actually writes predictions**:

1. Merge this PR
2. **Publish / redeploy stock-api before Friday 9:07 AM ET**
3. Confirm `GET /stock-api/aiem-predictions` → non-empty `today_predictions`
4. Confirm new paper opens carry `signal_source=aiem_loop_b` when Loop B succeeded

See also: `docs/verification/aiem-loop-b-failure-root-cause-2026-08-06.md`

## Remaining honesty

- Without `option_entry_mid` (legacy OPEN rows), option dollar P&L may still be the 2× underlying proxy — UI / MTM labels mark_source accordingly.
- True IV/theta option pricing is not modeled; we use contract mid when Tradier chain data is available.
