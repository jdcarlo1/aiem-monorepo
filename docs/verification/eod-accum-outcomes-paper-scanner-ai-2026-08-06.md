# EOD accumulation outcomes + Paper Money from scanner AI Trades

Date: 2026-08-06  
Branch: `cursor/stockscanner-full-tabs-live-5ace`

## EOD accumulation outcomes

**Problem:** Morning job only graded *yesterday’s* picks via `_td_intraday` (fragile / useless for backfill). 10 of 51 picks had no outcome; track success was incomplete.

**Fix:**
- `_grade_eod_accum_pick` + `_backfill_eod_accum_outcomes` use Tradier **daily** OHLC for the next session after `scan_date` (optional intraday refine when that session is today).
- Scheduler job grades **all ungraded** picks in a 120-day lookback.
- `GET /stock-api/eod-accum-track?backfill=1` triggers the same backfill; response includes `summary.ungraded_remaining` and `backfill` meta.

**Live Neon (this session):** graded the remaining 10 picks → **51/51** outcomes, gap-up hit rate **72.5%** (37/51). Single-environment check: prod Neon only.

## Paper Money ← scanner-ranked AI Trades (not OpenAI)

**Problem:** Paper candidates were dominated by `gap_volume` / `multi_signal`; `aiem_ai` was a thin HIGH/EXTREME-only DB slice and did not match the “paper from AI Trades / indicators” directive.

**Fix:**
- Primary source `scanner_ai_trades` (scores 16–26) from:
  1. in-memory `_ait_cache` (deterministic AI Trades worker),
  2. `ai_trade_log` BULLISH HIGH/EXTREME/**MEDIUM** last 2d,
  3. live rank via `_deterministic_ai_trades_from_pool` / stock buys from unusual calls + Layer 9 + composite when sparse.
- Other sources remain as fillers; higher scanner scores win `_add` conflicts.
- Execution revalidation knows `scanner_ai_trades` (ai_trade_log or unusual-calls fallback).
- `_ai_trades_worker` no longer hard-requires OpenAI init (ranking was already deterministic).

Does **not** invent historical paper fills (e.g. Aug 5 FAILED ledger). Next scheduled/recover execute will prefer scanner-ranked AI Trades.
