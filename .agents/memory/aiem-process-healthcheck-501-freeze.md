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
