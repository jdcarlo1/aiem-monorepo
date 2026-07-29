# NCLEX AI — Pre-Sale Security Matrix
**Date:** 2026-07-29 (updated after #73 / #74 closure)
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
| **Authorization — Cross-session data access** | `verifySessionAccess` middleware applied to `/adaptive/next` and `/adaptive/performance`. Rule 4: JWT present + sessionId claimed by different user → 403. Anonymous access (Rule 1) preserved — existing quiz flow unbroken. | ✅ PASS (fixed) | authz_test.md § Part F |
| **Authorization — Answer harvesting** | GET `/questions/:id` without subscription or prior answer → `correctLetter: null`, `explanation: null`. Tested with unrelated sessionId and no sessionId. | ✅ PASS | authz_test.md § Part A (questions/:id) |
| **Authorization — Bulk session activation** | Zod requires sessionId; `requireAdmin` gate added. Without token → 401. Missing sessionId → 400. | ✅ PASS | authz_test.md § Part A (activate-sessions) |
| **Authorization — Admin routes** | GET/POST/DELETE on all `/admin/*` routes return 401 without correct `x-admin-secret` header. Rate limited to 20 req/15 min per IP. | ✅ PASS | authz_test.md § Part A (Admin) |
| **SQL Injection** | All database queries use Drizzle ORM with parameterized queries. All user inputs validated via Zod before reaching DB layer. No raw SQL string concatenation found in codebase. | ✅ PASS | Code review: artifacts/api-server/src/routes/*.ts — all routes use drizzle `.where(eq(...))` pattern |
| **XSS — API responses** | API returns JSON only. No HTML rendering on the server. No user input reflected as raw HTML. | ✅ PASS | Code review: no res.send(html), no template rendering |
| **XSS — Frontend rendering** | React JSX escapes all interpolated values by default. One `dangerouslySetInnerHTML` found in chart.tsx — injects CSS custom properties (`--color-key: value`) sourced from developer config object, not user input. | ✅ PASS | Code review: nclex-prep/src/components/ui/chart.tsx:79 — source is THEMES config, not user data |
| **CSRF** | API uses JSON body with custom `sessionId` field — not cookie-based sessions. Browsers do not auto-submit JSON to cross-origin endpoints. CORS allowlist (post-fix) prevents cross-origin credentialed requests. | ✅ PASS | authz_test.md § Part C; app.ts CORS fix |
| **CORS misconfiguration** | **PRE-FIX:** `origin: true` reflected any origin with credentials. **POST-FIX:** Explicit allowlist. Evil origin returns no ACAO header. | ✅ PASS (fixed) | authz_test.md § Part C |
| **Security Headers** | Helmet middleware: 7 headers confirmed on all router-mounted endpoints. `X-Frame-Options: SAMEORIGIN`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `X-DNS-Prefetch-Control: off`, `X-Permitted-Cross-Domain-Policies: none`, `Cross-Origin-Opener-Policy: same-origin`, `Cross-Origin-Resource-Policy: same-origin`. | ✅ PASS (fixed) | authz_test.md § Part D |
| **Rate Limiting** | Three endpoints now rate-limited with live 429 evidence: `session/answer` 60/min (429 at req #61); `stripe/restore-access` 5/15min (429 at req #6); `stripe/checkout` 20/15min (429 at req #21). Admin routes: 20/15min (existing). | ✅ PASS (fixed) | authz_test.md § Part E |
| **File Uploads** | No file upload endpoints exist in the codebase. No `multer`, `busboy`, or multipart form handlers. | ✅ N/A | Code review: routes/index.ts — no upload routes |
| **Secrets / Credentials** | Pattern-based grep across all source files: 0 matches. All secrets read from `process.env` only. | ✅ PASS | secret_scan.txt |
| **Dependencies — CRITICAL** | pnpm audit: 0 CRITICAL | ✅ PASS | dependency_scan.txt |
| **Dependencies — HIGH (all 8)** | All 8 remaining HIGH CVEs confirmed **genuinely unreachable** in production: 7 exist exclusively in build-time tooling (`lib__api-spec > orval > typedoc/minimatch/@scalar`); 1 (form-data via @anthropic-ai/sdk) is in a runtime dep but the vulnerable code path (`files.upload()`) is never called — our routes use `messages.create()` only. | ✅ PASS | dependency_cve_analysis.md (per-CVE verdict table) |

---

## Fixes Applied During This Audit

| Issue | Severity | Fix Applied | File | Evidence |
|---|---|---|---|---|
| CORS wildcard `origin: true` with credentials | HIGH | Explicit allowlist (nclexai.org + Replit domains + localhost dev) | artifacts/api-server/src/app.ts | authz_test.md § Part C |
| Missing security headers | MEDIUM | helmet middleware installed and configured | artifacts/api-server/src/app.ts | authz_test.md § Part D |
| IDOR on /adaptive/next and /adaptive/performance | LOW | `verifySessionAccess` middleware applied to both routes | artifacts/api-server/src/routes/adaptive.ts | authz_test.md § Part F |
| No rate limiting on session/answer | MEDIUM | 60 req/min per IP — 429 at req #61 (live proof) | artifacts/api-server/src/app.ts | authz_test.md § Part E |
| No rate limiting on stripe/restore-access | MEDIUM | 5 req/15min per IP — 429 at req #6 (live proof) | artifacts/api-server/src/app.ts | authz_test.md § Part E |
| No rate limiting on stripe/checkout | MEDIUM | 20 req/15min per IP — 429 at req #21 (live proof) | artifacts/api-server/src/app.ts | authz_test.md § Part E |
| pnpm audit HIGH count reduction | MEDIUM | `pnpm update vite postcss http-proxy-middleware` (11 → 8 HIGH) | pnpm-lock.yaml | dependency_scan.txt |

---

## Open Findings

None. All items from the initial audit are now closed.

---

## Evidence Files Index

| File | Contents |
|---|---|
| `security_audit/SECURITY_MATRIX.md` | This file — master verdict table |
| `security_audit/authz_test.md` | Live curl output for all auth/authz/CORS/headers/rate-limit/IDOR tests (Parts A–F) |
| `security_audit/dependency_scan.txt` | Full `pnpm audit` output + initial exploitability assessment |
| `security_audit/dependency_cve_analysis.md` | Per-CVE reachability verdict for all 8 remaining HIGH findings |
| `security_audit/secret_scan.txt` | Pattern scan results (clean) + env var handling verification |

---

## Attestation

Every PASS verdict is backed by at least one of:
- Live endpoint test with raw curl output (authz_test.md)
- Confirmed 429 response body with threshold verification (authz_test.md § Part E)
- Full pnpm audit output + per-CVE code-path trace (dependency_cve_analysis.md)
- Pattern-based grep across entire source tree (secret_scan.txt)
- Direct source code review with file:line citations

No PASS verdict is asserted without a corresponding evidence file entry.
**No open findings remain.**
