---
name: Telegram alert trust/audit pipeline
description: App-wide Telegram alert ledger + grading + trust-weight soft-gate; phases and what's deferred
---

Every Telegram alert app-wide is meant to route through `alert_gateway.log_alert()` (fail-open, `telegram_alert_ledger` table) so bad signal sources get tracked and eventually demoted. Phased rollout, each phase requiring the prior one plus explicit approval to proceed:

- **Phase 1** (ledger + logging): `alert_gateway.py` — every `_tg_send`/`_tg` sender logs to `telegram_alert_ledger` with `signal_source`/`ticker`/`alert_class` (`SIGNAL` vs `INFO`)/`trigger_price`. Untagged legacy call sites default to `unclassified`/`INFO` — a strict no-op.
- **Phase 2** (tagging): ~7 high-value senders tagged with real `signal_source` + `ticker` + `trigger_price` so their alerts are gradeable. **Gotcha found and fixed**: a `SIGNAL`-class alert with `ticker=NULL` or `trigger_price=NULL` is permanently ungradeable — always source a real price (last close, spot, etc.) before tagging a sender as `SIGNAL`.
- **Phase 3** (grading + trust feedback): `alert_grading.py` runs daily (4:38 PM ET) — D+1/D+3/D+5 forward returns from `trigger_price`; D+3 is the decision horizon that calls `meta_learning_signal_trust.update_trust_weight(signal_name, context_bucket='TELEGRAM_ALERTS', ...)`. This context bucket is kept strictly separate from `PAPER_TRADING`/`AIEM_MICROCAP`/`AIEM_PREMARKET`.
- **Phase 4** (soft gate — DONE): `alert_gateway.get_trust_display(signal_source, min_n=5)` returns a short suffix (e.g. "— source trust: 62% WR · weight 1.24 (n=14)") appended to the outgoing Telegram TEXT (not just a DB annotation) in the 6 base senders, gated on `n_outcomes_observed>=5` so early noise doesn't show. The suffix is appended only to the text actually sent — `log_alert()` still receives the original untagged text so grading math is untouched. Weekly digest (`alert_grading.build_weekly_digest()`, Fri 4:50 PM ET, after the day's grading) sends a per-source WR/trust/trend/Phase-5-readiness summary; explicitly labeled "informational — no gating active".
- **Phase 5** (hard gating / suppression): explicitly deferred — requires separate user approval AND n>=20 graded outcomes per source before any real suppression logic is added.

**Key constraint**: never call `meta_learning_signal_trust` directly from a sender's hot send path — its `_connect()` raises if `AIEM_DATABASE_URL` is unset, violating the fail-open contract. Trust lookups for suffixes must go through `alert_gateway.py` (uses `DATABASE_URL`, wrapped try/except, always returns `""` on any error). `alert_gateway.py` (`DATABASE_URL`) and `meta_learning_signal_trust.py` (`AIEM_DATABASE_URL`) are assumed to point at the same database — confirmed identical as of 2026-07, but would silently split-brain trust writes vs. reads if that ever changes.

Verification pattern used for this pipeline: insert a temporary fake `signal_trust_weights` / `telegram_alert_ledger` row (prefixed `zzz_test_*`), run the function under test, inspect output, then delete the fake rows and confirm 0 remain — never leave test data in these production tables.
