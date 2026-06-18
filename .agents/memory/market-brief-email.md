---
name: Owner premarket brief email (8:30 ET)
description: Design constraints for the daily owner "premarket brief" email that aggregates several signal endpoints before the bell
---

# Owner premarket brief (daily 8:30 ET)

A daily owner email that mirrors the manual premarket rundown: premarket movers,
gamma pressure, dark-pool accumulation, fresh far-OTM sweeps, convergence, unusual
calls, plus a rule-based BOTTOM LINE. Wired as an owner-email `kind` (see
owner-email-scheduler.md). Has a token-gated admin preview/trigger route with a
`?dry=1` HTML-preview mode (no send).

## Durable constraints (non-obvious)

- **Always-send, even when empty.** Unlike every other owner email (which is
  silent-when-empty), this brief sends daily regardless.
  **Why:** the owner explicitly wants a consistent daily read "just so I have an
  idea," even on quiet mornings. Don't "optimize" it into silent-when-empty.

- **Pre-open aggregator emails must call heavy option-chain scanners in
  cache/DB-only mode, never trigger a cold live scan.**
  **Why:** the owner-email slot is claimed in owner_email_log BEFORE the send (see
  owner-email-scheduler.md), so a slow/hanging cold scan can burn the day's brief
  with no retry. At 8:30 (premarket) a live full-universe option-chain scan is also
  low-value — options aren't trading yet.
  **How to apply:** `/stock-api/unusual-calls` supports `?cache_only=1` (serves
  in-memory cache → today's ET DB rows → empty; never scans live). Use it from any
  pre-open digest. `/convergence` is already self-bounded (`as_completed(timeout=22)`
  + `cancel_futures`), `/darkpool` is FINRA (fast), `/premarket` is fast_info,
  gamma/far-otm are DB reads — those are safe to call live.

- **Aggregate via the endpoints' own in-process GET (Flask test_client), not by
  re-querying tables.** Reuses each endpoint's cache + single-flight lock and keeps
  one source of truth for each signal's shape/thresholds. One bad endpoint returns
  `{}` and that section omits itself — a single failure never breaks the whole email.
