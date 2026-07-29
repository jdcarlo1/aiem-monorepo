# NCLEX AI — Pre-Sale Security Matrix
**Date:** 2026-07-29
**Auditor:** Automated + manual code review + live endpoint testing
**Scope:** artifacts/api-server (Express 5 API), artifacts/nclex-prep (React/Vite frontend)
**API URL tested:** http://localhost:8080/api (production: https://nclexai.org/api)

---

## Security Matrix

| Security Area | Test Performed | Status | Evidence |
|---|---|---|---|
| **Authentication — JWT enforcement** | Direct URL access to `/session/claim` without Clerk JWT → 401. Expired/missing token handled by Clerk SDK + explicit null check. | ✅ PASS | authz_test.md § Part B |
| **Authentication — Admin token** | All 7 `/admin/*` endpoints tested with no token and wrong token. Timing-safe comparison via `crypto.timingSafeEqual`. | ✅ PASS | authz_test.md § Part A (Admin) |
| **Authentication — Subscription restore** | POST `/stripe/restore-access` with fake email returns `success: false` without activating session. Requires verified Stripe payment record. | ✅ PASS | authz_test.md § Part A (restore-access) |
| **Authorization — Cross-session data access** | User A reading User B's `/session/status` and `/adaptive/*` via sessionId param. Session status returns no PII. Adaptive returns category accuracy stats only. SessionId is random UUID. | ⚠️ LOW / DESIGN | authz_test.md § Part A (session/status, adaptive) |
| **Authorization — Answer harvesting** | GET `/questions/:id` without subscription or prior answer → `correctLetter: null`, `explanation: null`. Tested with unrelated sessionId and no sessionId. | ✅ PASS | authz_test.md § Part A (questions/:id) |
| **Authorization — Bulk session activation** | Previous vulnerability: no sessionId activated all sessions. Now: Zod requires sessionId, `requireAdmin` gate added. Without token → 401. Missing sessionId → 400. | ✅ PASS | authz_test.md § Part A (activate-sessions) |
| **Authorization — Admin routes** | GET/POST/DELETE on all `/admin/*` routes return 401 without correct `x-admin-secret` header. Rate limited to 20 req/15 min per IP. | ✅ PASS | authz_test.md § Part A (Admin) |
| **SQL Injection** | All database queries use Drizzle ORM with parameterized queries. All user inputs validated via Zod before reaching DB layer. No raw SQL string concatenation found in codebase. | ✅ PASS | Code review: artifacts/api-server/src/routes/*.ts — all routes use drizzle `.where(eq(...))` pattern |
| **XSS — API responses** | API returns JSON only. No HTML rendering on the server. No user input reflected as raw HTML. | ✅ PASS | Code review: no res.send(html), no template rendering |
| **XSS — Frontend rendering** | React JSX escapes all interpolated values by default. One `dangerouslySetInnerHTML` found in chart.tsx — injects CSS custom properties (`--color-key: value`) sourced from developer config object, not user input. | ✅ PASS | Code review: nclex-prep/src/components/ui/chart.tsx:79 — source is THEMES config, not user data |
| **CSRF** | API uses JSON body with custom `sessionId` field — not cookie-based sessions. Browsers do not auto-submit JSON to cross-origin endpoints. CORS allowlist (post-fix) prevents cross-origin credentialed requests. No CSRF tokens implemented (not required for this auth model). | ✅ PASS | authz_test.md § Part C; app.ts CORS fix |
| **CORS misconfiguration** | **PRE-FIX:** `cors({ origin: true, credentials: true })` reflected any origin including `https://evil.com` with `Access-Control-Allow-Credentials: true`. **POST-FIX:** Explicit allowlist: `nclexai.org`, `*.replit.app`, `*.replit.dev`, `*.janeway.replit.dev`, `*.repl.co`. Evil origin returns no ACAO header (browser blocks credentialed response). | ✅ PASS (fixed) | authz_test.md § Part C; git diff app.ts |
| **Security Headers** | Helmet middleware installed and active on all router-mounted endpoints. Headers confirmed: `X-Frame-Options: SAMEORIGIN`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `X-DNS-Prefetch-Control: off`, `X-Permitted-Cross-Domain-Policies: none`, `Cross-Origin-Opener-Policy: same-origin`, `Cross-Origin-Resource-Policy: same-origin`. | ✅ PASS (fixed) | authz_test.md § Part D |
| **Rate Limiting** | Admin routes: 20 req / 15 min per IP via `express-rate-limit`. General API endpoints (session/answer, stripe/checkout): no per-endpoint rate limit beyond Stripe SDK's own limits. | ⚠️ PARTIAL | authz_test.md § Part A; adminAuth.ts |
| **File Uploads** | No file upload endpoints exist in the codebase. No `multer`, `busboy`, or multipart form handlers. | ✅ N/A — NOT APPLICABLE | Code review: routes/index.ts — no upload routes |
| **Secrets / Credentials** | Pattern-based grep across all .ts/.tsx/.js/.env/.json/.yaml source files for Stripe keys, AWS keys, GitHub tokens, Slack tokens, Google API keys, hardcoded passwords/secrets. | ✅ PASS | secret_scan.txt |
| **Dependencies — CRITICAL** | pnpm audit: 0 CRITICAL | ✅ PASS | dependency_scan.txt |
| **Dependencies — HIGH (runtime)** | 8 HIGH after update (down from 11). All 8 are build-time tools (vite, postcss, babel) or non-user-reachable transitive deps (form-data via Stripe SDK, brace-expansion in glob tools, js-yaml in build config). None exploitable via production request paths. | ✅ PASS | dependency_scan.txt (exploitability assessment section) |
| **Dependencies — HIGH (build tools)** | vite path traversal (dev server only, not in production). postcss sourceMappingURL traversal (build time). Documented and assessed as non-exploitable in production. | ✅ PASS | dependency_scan.txt |

---

## Fixes Applied During This Audit

| Issue | Severity | Fix Applied | File |
|---|---|---|---|
| CORS wildcard `origin: true` with credentials | HIGH | Replaced with explicit allowlist (nclexai.org + Replit domains + localhost dev) | artifacts/api-server/src/app.ts |
| Missing security headers | MEDIUM | Installed and configured `helmet` middleware | artifacts/api-server/src/app.ts |
| pnpm audit HIGH count reduction | MEDIUM | `pnpm update vite postcss http-proxy-middleware --recursive` reduced HIGH from 11 → 8 | pnpm-lock.yaml |

---

## Open Findings (Not Fixed)

| Finding | Severity | Reason Not Fixed |
|---|---|---|
| `/adaptive/next` + `/adaptive/performance` IDOR | LOW | By design: anonymous UUID session model. No PII exposed (category accuracy stats only). Fixing requires adding session ownership to anonymous-session model which would break the UX. |
| Rate limiting on general API endpoints | LOW | Session-scoped DB writes (questionsAnswered counter) provide natural throttling. Adding IP rate limiting is a future hardening task. |
| `verify-checkout` returns customer email to caller | LOW | Caller must possess a valid Stripe `checkoutSessionId` to retrieve email. This is the intended UX for the post-payment confirmation flow. |

---

## Evidence Files Index

| File | Contents |
|---|---|
| `security_audit/dependency_scan.txt` | Full `pnpm audit` output + exploitability assessment for all HIGH findings |
| `security_audit/secret_scan.txt` | Secret pattern scan results (clean), env var handling verification |
| `security_audit/authz_test.md` | Live curl test results for all auth/authz scenarios, CORS tests, security header verification |
| `security_audit/SECURITY_MATRIX.md` | This file |

---

## Attestation

All PASS verdicts in this matrix are backed by one or more of:
- Live endpoint tests with raw curl output (authz_test.md)
- Full pnpm audit JSON output with exploitability analysis (dependency_scan.txt)
- Pattern-based grep across entire source tree (secret_scan.txt)
- Direct source code review with file:line citations

No PASS verdict is asserted without a corresponding evidence file entry.
