# NCLEX AI Security Audit — CLOSEOUT-FINAL
Directive: Directive_SecurityAudit_Closeout_2026-07-28
Audit commit: 1b6ab47 (HEAD -> main, "Update API server logic and expand security audit documentation")
Date: 2026-07-29
Raw evidence file: security_audit/raw_evidence.md

verified_run.sh / verify_chain.sh: N/A to this repo (AIEM repo only, established 2026-07-23)

---

## Corrected Security Matrix

Every PASS below is backed by raw output in raw_evidence.md at the cited item.
Every PARTIAL is explicitly described. No row is labeled PASS without raw evidence
from this session.

| # | Security Area | Status | raw_evidence.md ref |
|---|---|---|---|
| 1 | JWT enforcement — /session/claim blocks no-token and invalid token | ✅ PASS | Item 1a, 1b |
| 2 | CORS — evil.com blocked (no ACAO), nclexai.org allowed, *.replit.app allowed | ✅ PASS | Item 2a, 2b, 2c |
| 3 | Admin token — all 6 admin routes return 401 with no token or wrong token | ✅ PASS | Item 3 |
| 4 | SQL injection — zero raw concatenation; 29 parameterized query call sites | ✅ PASS | Item 4a, 4b, 4c |
| 5 | XSS (API) — zero res.send(html); all responses Content-Type: application/json | ✅ PASS | Item 5b, 5c, 5d |
| 6 | XSS (frontend) — one dangerouslySetInnerHTML at chart.tsx:79; source is THEMES config constant (not user input); zero innerHTML assignments | ✅ PASS | Item 5a (source code shown) |
| 7 | CSRF — no Set-Cookie in full header dump; text/plain form-submit returns 400; JSON-only API; CORS blocks cross-origin credentialed requests | ✅ PASS | Item 6a, 6b, 6c |
| 8 | Rate limit — session/answer: 429 at req 61 (limit=60/min) | ✅ PASS | Item 9a |
| 9 | Rate limit — stripe/restore-access: 429 at req 6 (limit=5/15min) | ✅ PASS | Item 9b |
| 10 | Rate limit — stripe/checkout: 429 at req 21 (limit=20/15min) | ✅ PASS | Item 9c |
| 11 | Adaptive IDOR (#74) — anonymous caller → 200 (both routes, full headers) | ✅ PASS | Item 10a, 10b |
| 12 | Adaptive IDOR (#74) — Clerk user A reading session claimed by user B → 403 | ⚠️ PARTIAL | Item 10c |
| 13 | CVE GHSA-hmw2-7cc7-3qxx (form-data) — files.upload never called; only messages.create() | ✅ PASS | Item 7a, 7b |
| 14 | CVE lib__api-spec chain (7 CVEs) — orval in devDependencies; zero runtime imports | ✅ PASS | Item 8a, 8b |
| 15 | Changed files — SHA256 before/after; git diff for app.ts and adaptive.ts | ✅ PASS | Item 11a–11d |
| 16 | Evidence chain protocol | ✅ STATED | Item 12 |

---

## Open Item

**Row 12 — Adaptive IDOR cross-user 403 (PARTIAL)**

Cannot be tested in the build environment. Requires two live Clerk-issued JWTs with one
session explicitly claimed by one account. Per directive Item 2: Joel will run this manually
with two real accounts outside the build environment. No substitution accepted.

State: PARTIAL pending manual live test.

---

## What This File Does Not Cover

The following rows from earlier matrix versions have raw evidence in raw_evidence.md but were
not part of the directive's explicit re-evidence list. They are referenced here for completeness
but are not re-attested in CLOSEOUT-FINAL:

- Answer harvesting via /questions/:id (correctLetter gating)
- Bulk session activation gate
- Security headers (helmet)
- Secrets / credential scan
- Dependency HIGH count (pnpm audit)

Those rows retain the evidence from authz_test.md (prior session) and dependency_scan.txt.
The directive did not require re-running them; they are not marked PASS or PARTIAL here.

---

## Evidence File Index

| File | Contents |
|---|---|
| security_audit/raw_evidence.md | All raw curl/grep output for this session (Items 1–12) |
| security_audit/dependency_cve_analysis.md | Per-CVE reachability verdict for all 8 HIGH findings |
| security_audit/dependency_scan.txt | Full pnpm audit text output |
| security_audit/secret_scan.txt | Pattern scan results + env var handling |
| security_audit/authz_test.md | Prior session evidence (Parts A–F) |
| security_audit/SECURITY_MATRIX.md | Full matrix (includes rows not covered by this directive) |
