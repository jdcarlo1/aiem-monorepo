---
name: Paper trade close → learning loop funnel
description: All aiem_paper_trades closes (MTM auto-exit, admin manual close, backfill) must route through one shared function so the Diagram-2 close-loop (stages 20-23) always fires exactly once.
---

Before this fix, `aiem_paper_trades` had exactly one write path that ran
the close-time learning loop (trust EMA + Thompson update + attribution +
supervisor hooks + D2 stages 20/21/22/23): the inline close branch of the
4PM MTM job. Any other way of closing a trade (there was no admin
endpoint — manual closes were done via raw SQL) silently skipped the loop
with no error, no log, nothing — the trade just sat CLOSED forever with
no learning signal extracted from it.

**Rule going forward:** never write `UPDATE aiem_paper_trades SET
status='CLOSED...'` directly (via raw SQL, a new endpoint, or a new bg
job). Always call `_aiem_close_paper_trade_and_run_loop(trade_id, status,
exit_reason, exit_price=None, exit_date=None, mode="close"|"backfill")`
in `main.py` instead:
- `mode="close"` — trade is still OPEN, you're the one deciding to exit it
  now (needs `exit_price`). CAS-claims via `WHERE status='OPEN'`.
- `mode="backfill"` — trade is already CLOSED (e.g. was closed by hand
  before this funnel existed, or by some other legacy path) and just
  needs the loop run against its existing stored exit values. CAS-claims
  via `WHERE status LIKE 'CLOSED%' AND learning_loop_fired_at IS NULL`.

**Why:** `learning_loop_fired_at` is the single at-most-once guard,
written atomically inside the same claiming UPDATE — so two callers
racing to close/backfill the same trade can't double-fire stages 20-23
or double-apply a trust-weight/Thompson update. Splitting "check if
already fired" from "mark as fired" into two steps would reopen that
race.

**How to apply:** if you ever add a new way to close a paper trade
(another admin route, a new automated exit job, a CLI script, etc.),
route it through this same function — don't reimplement the close SQL or
the downstream stage calls. If you're auditing for "did every close fire
the loop", the tell is `aiem_paper_trades.learning_loop_fired_at IS NULL
AND status LIKE 'CLOSED%'` = orphaned close that never looped.

Manual/admin closes go through `POST
/stock-api/admin/paper-trade/<id>/close` (hmac-compare_digest admin-token
auth) — this is the only sanctioned manual entry point; it just calls the
shared function.

Full raw-SQL/API verification evidence:
`artifacts/stock-scanner-api/AIEM_PAPER_TRADE_CLOSE_LOOP_VERIFICATION.md`.
