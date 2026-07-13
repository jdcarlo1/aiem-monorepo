---
name: VM multi-service default healthcheck vs BaseHTTPRequestHandler
description: Why a service with only do_POST (no do_GET) in a path-routed multi-service VM deployment can cause repeated startup-healthcheck failures and a "site frozen on publish" symptom
---

## The quirk
For VM deployments, any `[[services]]` block in `artifact.toml` without an explicit
`[services.<name>.production.health.startup] path = "..."` override gets a **default
startup probe of `GET /`** on that service's `localPort`.

If that service is implemented with Python's `http.server.BaseHTTPRequestHandler` and
only defines `do_POST` (e.g. an admin/trigger-only endpoint), any GET request —
including the platform's own healthcheck — falls through to the base class default and
returns **HTTP 501 Unsupported method**, not 404.

## Why it matters
- The deploy log shows a flood of `healthcheck failed error=healthcheck /<service>/ returned status 501`
  followed by `healthcheck failed after exhausting all attempts attempts=100` during the
  **promote** phase — this is a real, distinctive fingerprint, not noise.
- Promote-phase healthcheck failure can make a redeploy look "stuck"/"frozen" right after
  publishing, even though the underlying app processes are fine and other services
  (main API/web) are already returning 200s in the same log window.
- This is easy to miss because the app "looks up" once the deploy eventually settles —
  the failure window is transient (~5 min) but recurs on every future publish/restart
  until fixed.

**Why:** `BaseHTTPRequestHandler` has no default `do_GET`; an unhandled HTTP method is a
501, not a 404, which is a less obvious log signature to grep for.

**How to apply:** When a background/admin-only Python service is added to `artifact.toml`
as its own `[[services]]` entry (own `localPort` + `paths`), always give it a trivial
`do_GET` returning 200 (even if it does nothing else), OR add an explicit
`[services.<name>.production.health.startup]` path if the default `GET /` isn't
appropriate. Grep deployment logs for `healthcheck failed` + `exhausting all attempts`
as the first move when a user reports a deploy "freeze" on a multi-service VM artifact.

## July 2026 — slow-import timing bug (root cause of repeated deploy failures)

The early health server was placed AFTER `import aiem_optprob` and `import aiem_firstcandle`
in aiem_process.py. On a cold production container those imports take 30-60 s.
Replit's promote-phase prober fires immediately on startup, got no response during
that window, and killed the deploy every time.

**Fix**: moved `_start_process_health_server()` call to BEFORE the slow imports —
right after the stdlib imports (os, sys, threading etc.) at line ~45. The health
server thread now binds and serves in <1 s before aiem_optprob even starts loading.

**Rule**: Any service that has slow imports (scipy, sklearn, pandas, xgboost) MUST
start its health server using ONLY stdlib (already imported), before ANY third-party
import runs. Even 10 seconds of silence at startup can kill a deploy.
