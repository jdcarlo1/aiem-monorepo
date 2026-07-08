---
name: AEIM DIAGRAM 2 — Phase 16 (Alerts & Notifications) findings
description: Verification results for Phase 16 of the AIEM master wiring/verification sweep — first transitive-only module wiring found; earnings_calendar.py name-collision trap identified.
---

Phase 16 covers 9 modules: alerts.py, email_alerts.py, sms_alerts.py,
telegram_charts.py, news_catalyst.py, news_catalyst_monitor.py,
reddit_sentiment.py, social_sentiment.py, earnings_calendar.py.

**Module wiring: 9/9 VERIFIED_WIRED, 0 genuine gaps** — but one is
transitive-only: **earnings_calendar.py has zero direct `import` in
main.py.** It is reached one hop removed, via premarket_open_trader.py
(which main.py does import at 2 real call sites — init_schema() and
evaluate_ticker()) → evaluate_ticker() internally calls
`earnings_calendar.should_avoid_entry()`. This is the first
transitive-only wiring found across the whole sweep; distinct from
"table-level coupling" (Phase 14/15) because it's a real Python import
chain, just indirect.

**Name-collision trap:** main.py has its OWN inline `earnings_calendar`
DB table, its own `_populate_earnings_calendar()`, and its own
`earnings_calendar()` Flask route — unrelated to the earnings_calendar.py
module, just sharing the name. Five other modules
(aiem_selloff_reversion.py, aiem_short_squeeze.py,
aiem_momentum_exhaustion.py, momentum_trade_trainer.py,
aiem_pullback_reentry.py) query the `earnings_calendar` TABLE directly via
raw SQL — that is coupling to main.py's inline logic, NOT evidence of
earnings_calendar.py module wiring. Always check whether a module name
found via `grep -rl` across the repo reflects a real import vs. a
same-named table/route living in main.py itself.

**sms_alerts.py reconfirmation:** send_sms()'s own docstring states every
alert is now delivered via email, not SMS (Twilio/tmomail removed) — kept
the function name so existing callers work unchanged. Independently
reconfirms the earlier sms-delivery-solution.md finding via this phase's
grep trace.

**Tool tracing (5 tools, 5/5 registered):**
- 3 same-phase: send_discovery_alert → email_alerts.py (gated by a hard
  code-level `risk_gate_passed` check that logs to decision_logger and
  blocks the send rather than trusting model discretion);
  reddit_sentiment → reddit_sentiment.py; check_news_catalyst_risk →
  news_catalyst_monitor.py.
- 2 cross-phase: get_literature_briefs → literature_scanner.py (Phase 4);
  event_risk_check → aiem_risk_guards.py (Phase 11).

email_alerts.py has 44 references across main.py — it is the shared
delivery backbone for many phases beyond Phase 16, not an
exclusive dependency.

Verification script: `artifacts/stock-scanner-api/aiem_phase16_verify.py`.
Applied to DB: 9 module rows in aiem_module_registry (all
VERIFIED_WIRED), 5 tool rows in aiem_tool_registry (all
VERIFIED_REAL_IMPLEMENTATION).
