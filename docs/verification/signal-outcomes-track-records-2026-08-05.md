# Signal outcomes + track records fix (2026-08-05)

## Root causes

1. **Post-4:30 PM graders gated by `_intraday_scan_allowed()`** (window ends 4:30 ET). Jobs at 4:32–4:37 never ran (signal outcomes, eod sweep outcomes, conviction outcomes, insider outcomes, AI trade outcomes race).
2. **`update_signal_outcome_prices` only selected `t3_price IS NULL`** — once T+3 filled, T+5/T+10 never updated (`t10_win` was 0 across the table).
3. **Neon pooler** rejected `statement_timeout` in connect options → grader crashed on pooler URLs.
4. **signal_outcomes writers stalled** after July 28 when bull-flow POST wasn’t hit — seed from `unusual_calls_log` added.

## Fixes

| Fix | Effect |
|---|---|
| Remove intraday gate on post-close outcome jobs | Graders actually fire after close |
| Revisit rows missing t5/t10 | Full T+3/T+5/T+10 win rates |
| Neon-safe `_connect()` | Graders work on pooler |
| `_seed_signal_outcomes_from_calls` | New Outcomes rows from calls log |
| Insider grader: T+5 without earnings_date | Fills empty `insider_outcomes` |
| Opportunistic bg grade on GET `/outcomes` | Catch-up if T+10 still sparse |

## Live Neon backfill (this session)

| Metric | Before | After |
|---|---|---|
| signal_outcomes rows | 872 (max 7/28) | **1914** (max **8/5**) |
| t3 filled | 872 | **1017+** |
| t5 filled | 196 | **653+** |
| t10 filled | **0** | **354+** |

## Paper Money after publish

| Question | Answer |
|---|---|
| Will `aiem_paper_trades` show? | **Yes** — GET `/aiem-paper-portfolio` is public; reads existing **169** rows (OPEN/CLOSED). |
| Will Aug 5 picks appear? | **No invent** — Aug 5 ledger is FAILED (`lock_contention`). Reclaim allows **retry** (Pick Now / next watchdog), does not fabricate that morning’s fills. |
| Will next trading day work? | **Should** — fresh `try_claim` INSERT for the new date; FAILED reclaim for zero-pick failed days; reconnect on closed sockets. |

## Already working track records (fresh)

- `ai_trade_log` / `ai_short_calls_log` — Aug 5, outcomes populated
- `eod_sweep_log` grades — Aug 5
- `0dte/paper-trades` — 125 rows
- PE track-record endpoint — 100 rows
