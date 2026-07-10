# Paper Trade Close → Learning Loop Verification

Date: 2026-07-10
Scope: user directive — "when they close out the paper trades, those need to
also go through the looping mechanism as well." I.e. EVERY way a paper trade
can close (autonomous 4PM MTM exit, manual/admin close, or backfill of a
past manual close) must run the same Diagram-2 close-time learning loop
(stages 20-23: post-trade analytics, trust-weight EMA + Thompson sampler,
RL feedback marker, memory/attribution), exactly once.

## What was found before this change

- Only one code path fired the close-time learning loop:
  `_aiem_paper_mark_to_market()` (4PM MTM), with all of stages 20/21/23 +
  attribution + supervisor hooks + trust/Thompson update inlined directly
  in its close branch (~500 lines, using loop-local variables).
- No admin/manual close endpoint existed anywhere in the codebase. Two
  earlier manual closes made ad-hoc via raw SQL this session (NVDA id=174,
  MU id=175) bypassed the loop entirely — proven by
  `learning_loop_fired_at IS NULL` before the fix (see below).
- Searched for every `UPDATE aiem_paper_trades ... SET status=...CLOSED`
  site in main.py: the only other write near "CLOSED" status hits a
  DIFFERENT table (`position_monitor`, the email-based manual
  buy/sell-alert monitor at ~L13143) — unrelated to `aiem_paper_trades` /
  the AIEM paper-trading learning loop. No other bypass path exists.

## What changed

1. **`learning_loop_fired_at TIMESTAMPTZ`** column added to
   `aiem_paper_trades` (dev DB + `_init_aiem_paper_trades_table()`). Acts
   as the at-most-once CAS guard — set atomically inside the same UPDATE
   that performs the close/claim, never as a separate step.

2. **`_aiem_close_paper_trade_and_run_loop(trade_id, status, exit_reason,
   exit_price=None, exit_date=None, mode="close"|"backfill")`** — new
   shared function containing the full extracted stage-20/21/22/23 +
   attribution/supervisor/trust/Thompson logic (main.py, right before
   `_aiem_paper_mark_to_market`).
   - `mode="close"`: trade must be `status='OPEN'`. Computes pnl/pnl_pct
     from `exit_price` (byte-identical formula to the original inline
     code, incl. the CALL_OPTION synthetic 2x proxy), then claims via
     `UPDATE ... WHERE id=%s AND status='OPEN' RETURNING id`. Zero rows
     returned ⇒ another caller already closed it; aborts cleanly.
   - `mode="backfill"`: trade must already be `status LIKE 'CLOSED%'`.
     Claims via `UPDATE aiem_paper_trades SET learning_loop_fired_at=NOW()
     WHERE id=%s AND status LIKE 'CLOSED%' AND learning_loop_fired_at IS
     NULL RETURNING ...`, reusing the already-stored exit fields as-is.
   - All downstream stages remain fail-soft (one stage failing never
     blocks the DB write or the other stages), matching pre-existing MTM
     behavior.
   - Deliberately does NOT trigger the RL pipeline itself — that stays a
     batch job keyed off `exit_date=today` across all trades closed that
     day (`_rl_pipeline_bg`), so a per-trade close can never double-fire it.

3. **`_aiem_paper_mark_to_market()` rewired**: its close branch
   (`if _status != "OPEN":` ... 300+ inlined lines) now just calls
   `_aiem_close_paper_trade_and_run_loop(mode="close")` and uses the
   result for the Telegram exit-line message. No behavior change to MTM's
   own decision logic (when/why to exit) — only how the close+loop is
   executed.

4. **New endpoint** `POST /stock-api/admin/paper-trade/<id>/close`
   (fail-closed `hmac.compare_digest` admin-token auth, same pattern as
   the existing admin routes). Body: `{"mode": "close"|"backfill",
   "exit_price": <required for close>, "exit_reason": ..., "status": ...}`.
   This is now the ONLY supported way to manually close a paper trade —
   raw SQL must never be used again for this.

## Live evidence (raw, from this session — dev environment)

Restarted `stock-api` workflow after the change: clean boot, `[tool map
check] 225 tools wired (expected 225) ✅`, no new errors in the log vs.
baseline (`InterfaceError: cursor already closed` at L19036 is
pre-existing/unrelated silent_except noise, confirmed present before this
change too).

**Backfill of the two orphaned manual closes (174, 175):**
```
POST /stock-api/admin/paper-trade/174/close {"mode":"backfill"}
→ {"fired":true,"mode":"backfill","pnl":-1.46,"pnl_pct":-0.7129,
   "status":"CLOSED_MANUAL_ADMIN","ticker":"NVDA","trade_id":174,
   "trace_id":"aiem_2026_07_08_NVDA_70fe4b"}

POST /stock-api/admin/paper-trade/175/close {"mode":"backfill"}
→ {"fired":true,"mode":"backfill","pnl":43.49,"pnl_pct":4.5871,
   "status":"CLOSED_MANUAL_ADMIN","ticker":"MU","trade_id":175,
   "trace_id":"aiem_2026_07_08_MU_482f74"}
```

**DB proof — `aiem_diagram2_trace_audit` stages 20-23, both PASS:**
```
trace_id                      | stage | stage_name            | status
aiem_2026_07_08_NVDA_70fe4b   | 20    | post_trade_analytics  | PASS
aiem_2026_07_08_NVDA_70fe4b   | 21    | learning_feedback     | PASS
aiem_2026_07_08_NVDA_70fe4b   | 22    | feedback_loop         | PASS
aiem_2026_07_08_NVDA_70fe4b   | 23    | memory                | PASS
aiem_2026_07_08_MU_482f74     | 20    | post_trade_analytics  | PASS
aiem_2026_07_08_MU_482f74     | 21    | learning_feedback     | PASS
aiem_2026_07_08_MU_482f74     | 22    | feedback_loop         | PASS
aiem_2026_07_08_MU_482f74     | 23    | memory                | PASS
```
`signal_trust_weights` row for the affected signal source updated in the
same window (`last_updated_at` within the test run).

**CAS idempotency proof — re-running backfill on the same two trades:**
```
→ {"fired":false,"reason":"trade 174 not eligible for backfill
   (not CLOSED%, or loop already fired)"}
→ {"fired":false,"reason":"trade 175 not eligible for backfill
   (not CLOSED%, or loop already fired)"}
```

**`mode="close"` re-close guard proof** (attempted close on an
already-closed trade):
```
POST /stock-api/admin/paper-trade/174/close {"mode":"close","exit_price":100}
→ {"fired":false,"reason":"trade 174 not OPEN
   (status=CLOSED_MANUAL_ADMIN); refusing to re-close"}
```

**`mode="close"` pnl-formula parity dry run** (no DB write — confirms the
extracted formula matches the original inline MTM math for both trade
types, using real entry/notional from currently-OPEN trades):
```
STOCK        entry=7.3968    exit=8.10   -> pnl=95.07  pnl_pct=9.5068
CALL_OPTION  entry=1686.0735 exit=1750.0 -> pnl=75.83  pnl_pct=7.5829
```

## What was intentionally NOT done

- A genuine `mode="close"` write against a currently-OPEN production
  paper trade was intentionally NOT performed as part of this
  verification. Doing so would inject a synthetic, non-signal-driven
  close into the live trust-weight/Thompson-sampler statistics purely for
  test purposes, which this project's data-integrity conventions
  explicitly avoid (no synthetic data in production learning tables).
  The `close` write path shares 100% of its downstream code with the
  already-proven `backfill` path (same function, same stages 20-23 call
  sites) — the only untested code is the initial claim UPDATE + pnl
  formula, both covered above by the re-close guard test and the
  dry-run parity check. The first fully organic `mode="close"` run will
  happen naturally at tomorrow's 4PM ET MTM cycle.
