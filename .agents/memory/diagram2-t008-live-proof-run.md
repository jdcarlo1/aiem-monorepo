---
name: Diagram 2 T008 live end-to-end proof run
description: How the real production Diagram2 pipeline was exercised end-to-end (not simulated) to produce raw aiem_diagram2_trace_audit evidence, and what that run incidentally revealed.
---

## What was done
To free capacity under the real portfolio gates (never bypassed/weakened), the oldest open
`aiem_paper_trades` positions were closed manually with real Tradier quotes, mirroring the
exact production MTM close formulas (stock: `(last-entry)*qty`; CALL_OPTION: synthetic 2x
proxy `pnl_pct=max(-100, move_pct*2)`). Manual closes use a distinct `status='CLOSED_MANUAL'`
(not in the CHECK constraint — none exists on this column) with an explicit `exit_reason` tag
citing user authorization, so they're honestly distinguishable from `CLOSED_AIEM`/expired closes.

**Why:** the task required proving the real pipeline runs end-to-end on a genuinely fresh
candidate without weakening the 20-position `CorrelationGuard` cap or any other real gate.

**How to apply:** if you ever need to manually close paper positions again for a similar reason,
reuse this exact UPDATE shape and status tag; never reuse `CLOSED_AIEM` for a non-AI close.

## Real gates encountered live (both worked as designed)
1. `aiem_risk_guards.CorrelationGuard` (`_CG_MAX_OPEN_POSITIONS=20`) — the binding open-position cap.
2. `portfolio_correlation_risk.py`'s `check_current_portfolio_risk()` — a SEPARATE gate that
   SKIPS THE ENTIRE DAY'S RUN (not per-candidate) if any `CORRELATION_GROUPS` bucket (mega_tech,
   semis, ev_meme, biotech_meme, crypto_adjacent) has >=3 of the currently-open positions. Hit
   this live: 4 mega_tech names (AAPL/MSFT/META/AMZN) blocked the whole run until 2 were closed.

## Finding: CorrelationGuard cap can be oversold within one fast batch run
See [correlation-guard-cache-staleness](correlation-guard-cache-staleness.md) — root cause and why
it let 8 trades in (17->25 open) against a declared cap of 20 in one execution. This is a real,
pre-existing production gap, not something introduced or worked around during this session; it
was surfaced and reported to the user rather than silently patched.

## Result achieved (real evidence, not fabricated)
- One force-execute run admitted 8 real candidates, each recorded 19 real stages in
  `aiem_diagram2_trace_audit` (stage_order 1-19) with real timestamps/function names/hashes.
- 2 of 8 (NVDA, MU) are clean 19/19 PASS on entry — stayed OPEN after the first force-mtm poll
  (no AI EXIT yet), so stages 20-21 correctly hadn't fired for these two.
- The other 6 show genuine real FAILs at stage 13 (probability_engine — no `ai_short_calls_log`
  row for that ticker) and/or stage 15/19 (specialist_council/bull_bear_persistence — debate
  batch limited to top-ranked candidates only) — expected, honest pipeline behavior.
- FULL 21/21-STAGE CLOSURE achieved organically, same session, no fabrication: HYPG (id=178) and
  OXY (id=180) were closed by a genuine AI EXIT decision on a LATER scheduled MTM pass (not
  forced) — real technical reasons ("CMF -0.18 distribution", "MACD momentum fading"). Their
  trace_ids then picked up real stage 20 (`post_trade_analytics` / `log_outcome_for_trade`) and
  stage 21 (`learning_feedback` / trust-weight EMA + Thompson update), both PASS, completing the
  full entry-to-learning loop with real timestamps. Both traces also carried the same honest
  stage 13/15/19 FAILs as the other non-clean candidates — a fully-passing 21/21 run was not
  observed, only a fully-recorded (21-stage, mixed PASS/FAIL) one; do not overstate this as
  "21/21 PASS" when reporting — it is 21/21 stages RECORDED with real outcomes, most PASS.
