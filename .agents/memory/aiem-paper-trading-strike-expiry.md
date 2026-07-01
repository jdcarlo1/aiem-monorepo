---
name: AIEM paper trading strike/expiry sourcing
description: How CALL_OPTION strike/expiry data flows into aiem_paper_trades, and a dormant broken pick-source query discovered along the way
---

`aiem_paper_trades` (main.py) models CALL_OPTION picks as a synthetic 2x-underlying-move proxy — `entry_price` is the underlying stock price, not a real option premium, and P&L is not computed from real options pricing (no strike/IV/theta). Strike (`NUMERIC(10,2)`) and expiry (`TEXT`) columns are additive/display-only; they do not feed the P&L calc.

Real strike/expiry values come from whichever signal source produced the pick, inside `_aiem_paper_pick_candidates()`:
- `call_sweep_log` — has strike/expiry
- `unusual_calls_log` — has strike/expiry
- `ai_trade_log` — has `entry_strike`/`expiry`
- `oi_daily_snapshot` block — **pre-existing broken query**, references nonexistent columns `oi_change_pct`/`days_building`. It's silently swallowed by the outer try/except around candidate sourcing, so it never raises but also never contributes picks. Left unfixed as it predates and is unrelated to strike/expiry display work — flag if asked to fix pick-source coverage.

**Why:** Strike/expiry were entirely absent from the paper-trading UI (CALL rows showed no strike/expiry), and diagnosing it required tracing 4 separate candidate sources with inconsistent column names before finding the real data existed upstream but was never plumbed through `_add()` → INSERT → the `aiem_paper_portfolio()` SELECT queries → frontend.

**How to apply:** When adding new fields to paper-trading picks, they must be threaded through all 4 points: candidate SELECT queries → `_add()` helper params → INSERT in `_aiem_paper_execute_today()` → both SELECT queries (open_positions, closed_trades) in the `aiem_paper_portfolio()` endpoint → `AiemPaperTrade`/`AiemPaperClosedTrade` TS interfaces in `api.ts` → Dashboard.tsx tables. Existing OPEN rows created before a field is added will show null/"—" since the scheduler already ran; only future scheduler runs backfill real values.
