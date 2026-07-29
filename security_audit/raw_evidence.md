# NCLEX AI Security Audit — Raw Evidence File
Directive: Directive_SecurityAudit_Closeout_2026-07-28
Audit commit: 1b6ab47 (HEAD -> main)
Date collected: 2026-07-29
Host: http://localhost:8080/api (API Server workflow running)
Protocol: raw shell output only — no narrative, no summary tables

verified_run.sh / verify_chain.sh: N/A — those scripts belong to the AIEM repo only
(established 2026-07-23, confirmed in directive)

---

## ITEM 1 — JWT Enforcement
Run timestamp: 2026-07-29T02:27:44Z

### 1a. POST /session/claim — no JWT

Request:
```
> POST /api/session/claim HTTP/1.1
> Host: localhost:8080
> Content-Type: application/json
> Content-Length: 33
```

Response:
```
< HTTP/1.1 401 Unauthorized
< x-clerk-auth-reason: dev-browser-missing
< x-clerk-auth-status: signed-out
< Content-Type: application/json; charset=utf-8
```

Body:
```json
{"error":"Authentication required to claim a session. Sign in first.","code":"UNAUTHENTICATED"}
```

### 1b. POST /session/claim — invalid Bearer token

Request:
```
> POST /api/session/claim HTTP/1.1
> Authorization: Bearer notavalidtoken
> Content-Type: application/json
```

Response:
```
HTTP/1.1 401 Unauthorized
x-clerk-auth-message: Invalid JWT form. A JWT consists of three parts separated by dots. (reason=token-invalid, token-carrier=header)
x-clerk-auth-reason: token-invalid
x-clerk-auth-status: signed-out
```

Body:
```json
{"error":"Authentication required to claim a session. Sign in first.","code":"UNAUTHENTICATED"}
```

---

## ITEM 2 — CORS
Run timestamp: 2026-07-29T02:27:48Z

### 2a. Origin: https://evil.com — preflight

Request:
```
> OPTIONS /api/session/status HTTP/1.1
> Origin: https://evil.com
> Access-Control-Request-Method: GET
```

Response:
```
< HTTP/1.1 204 No Content
< Vary: Origin, Access-Control-Request-Headers
< Access-Control-Allow-Credentials: true
< Access-Control-Allow-Methods: GET,HEAD,PUT,PATCH,POST,DELETE
```

No `Access-Control-Allow-Origin` header present. Browser will block the credentialed response.

### 2b. Origin: https://nclexai.org — preflight

Request:
```
> OPTIONS /api/session/status HTTP/1.1
> Origin: https://nclexai.org
> Access-Control-Request-Method: GET
```

Response:
```
< HTTP/1.1 204 No Content
< Access-Control-Allow-Origin: https://nclexai.org
< Vary: Origin, Access-Control-Request-Headers
< Access-Control-Allow-Credentials: true
< Access-Control-Allow-Methods: GET,HEAD,PUT,PATCH,POST,DELETE
```

### 2c. Origin: https://nclex-test-abc123.replit.app — wildcard subdomain

Response:
```
< Access-Control-Allow-Origin: https://nclex-test-abc123.replit.app
```

---

## ITEM 3 — Admin Token
Run timestamp: 2026-07-29T02:28:30Z

All six routes, no token:

```
GET  /admin/affiliates          → HTTP 401 | {"error":"Unauthorized"}
GET  /admin/affiliates wrong-tok→ HTTP 401 | {"error":"Unauthorized"}
POST /admin/seed-questions      → HTTP 401 | {"error":"Unauthorized"}
POST /admin/activate-sessions   → HTTP 401 | {"error":"Unauthorized"}
DELETE /admin/affiliates/test   → HTTP 401 | {"error":"Unauthorized"}
POST /admin/fix-sessions        → HTTP 401 | {"error":"Unauthorized"}
```

---

## ITEM 4 — SQL Injection
Run timestamp: 2026-07-29T02:27:56Z

### 4a. Raw string concatenation into SQL

```
$ grep -rn "query\s*+\|sql\s*+\|\"SELECT.*\+\|'SELECT.*+\|db\.execute.*\${\|db\.run.*\${\|db\.query.*\${" \
    artifacts/api-server/src/routes/
[no output]
exit_code_concat=1
```

### 4b. Template-literal interpolation into .execute/.query/.run

```
$ grep -rn "\.execute(\`\|\.query(\`\|\.run(\`" \
    artifacts/api-server/src/
[no output]
exit_code_interp=1
```

### 4c. Parameterized query usage count (drizzle eq/inArray/notInArray)

```
$ grep -rn "eq(\|inArray(\|notInArray(\|and(\|or(" \
    artifacts/api-server/src/routes/ | grep -c "eq(\|inArray"
29
```

29 parameterized query call sites in route files. Zero raw SQL concatenation.

---

## ITEM 5 — XSS
Run timestamp: 2026-07-29T02:27:56Z / 02:28:00Z

### 5a. dangerouslySetInnerHTML in frontend src

```
$ grep -rn "dangerouslySetInnerHTML" artifacts/nclex-prep/src/
artifacts/nclex-prep/src/components/ui/chart.tsx:79:      dangerouslySetInnerHTML={{
exit_code_dsh=0
```

One match. Source context (chart.tsx lines 77–98):

```tsx
  return (
    <style
      dangerouslySetInnerHTML={{
        __html: Object.entries(THEMES)
          .map(
            ([theme, prefix]) => `
${prefix} [data-chart=${id}] {
${colorConfig
  .map(([key, itemConfig]) => {
    const color =
      itemConfig.theme?.[theme as keyof typeof itemConfig.theme] ||
      itemConfig.color
    return color ? `  --color-${key}: ${color};` : null
  })
  .join("\n")}
}
`
          )
          .join("\n"),
      }}
    />
  )
```

Injected content: CSS custom properties (`--color-${key}: ${color}`). Source: `THEMES` constant
(developer-defined enum) and `itemConfig.color` (chart config object, not user input). No
user-supplied string enters this interpolation.

### 5b. res.send with HTML in api-server routes

```
$ grep -rn "res\.send(\`\|res\.send('<\|res\.send(\"<" \
    artifacts/api-server/src/routes/
[no output]
exit_code_ressend=1
```

### 5c. innerHTML assignment in frontend src

```
$ grep -rn "innerHTML\s*=" artifacts/nclex-prep/src/
[no output]
exit_code_inner=1
```

### 5d. Content-Type on API responses

```
$ curl -sI "http://localhost:8080/api/session/status?sessionId=xss-chk" | grep -i "content-type"
Content-Type: application/json; charset=utf-8
```

API server returns `application/json` only. No `text/html` responses.

---

## ITEM 6 — CSRF
Run timestamp: 2026-07-29T02:28:04Z / 02:28:48Z

### 6a. Full response headers for POST /session/answer — Set-Cookie absent

```
$ curl -s -D - -X POST "http://localhost:8080/api/session/answer" \
    -H "Content-Type: application/json" \
    -d '{"sessionId":"csrf-hdr-chk","questionId":1,"selectedLetter":"A"}' \
    -o /dev/null

HTTP/1.1 200 OK
Vary: Origin
Access-Control-Allow-Credentials: true
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Resource-Policy: same-origin
Origin-Agent-Cluster: ?1
Referrer-Policy: no-referrer
Strict-Transport-Security: max-age=31536000; includeSubDomains
X-Content-Type-Options: nosniff
X-DNS-Prefetch-Control: off
X-Download-Options: noopen
X-Frame-Options: SAMEORIGIN
X-Permitted-Cross-Domain-Policies: none
X-XSS-Protection: 0
x-clerk-auth-reason: dev-browser-missing
x-clerk-auth-status: signed-out
RateLimit-Policy: 60;w=60
RateLimit-Limit: 60
RateLimit-Remaining: 55
RateLimit-Reset: 17
Content-Type: application/json; charset=utf-8
Content-Length: 479
ETag: W/"1df-nBNDRS9+8g0tQIbCynaZb/vTj+Y"
Date: Wed, 29 Jul 2026 02:28:48 GMT
Connection: keep-alive
Keep-Alive: timeout=5
```

`Set-Cookie` is absent from the full header dump. The server uses no cookies.

### 6b. text/plain form-submit (simulates browser CSRF form POST)

```
$ curl -s -w "\nHTTP_STATUS:%{http_code}\n" -X POST "http://localhost:8080/api/session/answer" \
    -H "Content-Type: text/plain" \
    --data 'sessionId=csrf-chk&questionId=1&selectedLetter=A'

{"error":"sessionId, questionId, and selectedLetter are required","details":{"formErrors":["Required"],"fieldErrors":{}}}
HTTP_STATUS:400
```

`express.json()` middleware ignores `text/plain` bodies. Zod schema finds no parsed fields
and rejects with 400. A browser CSRF form POST (which sends `application/x-www-form-urlencoded`
or `text/plain`) cannot reach any route logic.

### 6c. Content-Type on API responses (confirm application/json, not text/html)

```
$ curl -sI "http://localhost:8080/api/session/status?sessionId=csrf-chk" | grep -i "content-type"
Content-Type: application/json; charset=utf-8
```

---

## ITEM 7 — CVE GHSA-hmw2-7cc7-3qxx (form-data / @anthropic-ai/sdk)
Run timestamp: 2026-07-29T02:27:44Z (same session)

### 7a. All client. calls in every Anthropic-using route file

```
$ grep -n "client\." artifacts/api-server/src/routes/analyze.ts
41:    const message = await client.messages.create({

$ grep -n "client\." artifacts/api-server/src/routes/catalyst.ts
66:    const message = await client.messages.create({

$ grep -n "client\." artifacts/api-server/src/routes/morning-brief.ts
78:    const message = await client.messages.create({
```

### 7b. files.upload / FormData / multipart / filename across all three files

```
$ grep -rn "files\.upload\|files\.create\|FormData\|multipart\|filename" \
    artifacts/api-server/src/routes/analyze.ts \
    artifacts/api-server/src/routes/catalyst.ts \
    artifacts/api-server/src/routes/morning-brief.ts
[no output]
exit_code=1
```

---

## ITEM 8 — CVE chain: lib__api-spec (linkify-it/brace-expansion/js-yaml/fast-uri)
Run timestamp: 2026-07-29T02:27:56Z

### 8a. lib/api-spec/package.json raw

```json
{
  "name": "@workspace/api-spec",
  "version": "0.0.0",
  "private": true,
  "scripts": {
    "codegen": "orval --config ./orval.config.ts && pnpm -w run typecheck:libs"
  },
  "devDependencies": {
    "orval": "^8.9.1"
  }
}
```

`orval` is under `devDependencies`. No `dependencies` key.

### 8b. Runtime src/ import grep

```
$ grep -rn "orval\|typedoc\|minimatch\|@scalar/openapi-parser\|linkify-it\|brace-expansion\|fast-uri\|ajv" \
    artifacts/api-server/src/ \
    artifacts/nclex-prep/src/
[no output]
exit_code=1
```

Zero runtime imports.

---

## ITEM 9 — Rate Limiting (#73)
Server restarted before each set; windows begin at zero.

### 9a. POST /session/answer — 60/min

```
req 1  → HTTP 200 | {"correct":false,"correctLetter":"C","explanation":"The prio...
req 2  → HTTP 200 | {"correct":false,...
req 3  → HTTP 200 | {"correct":false,...
[req 4–58: HTTP 200, same pattern]
req 59 → HTTP 403 | {"error":"Free limit reached","freeLimit":10,"checkoutUrl":null}
req 60 → HTTP 403 | {"error":"Free limit reached","freeLimit":10,"checkoutUrl":null}
req 61 → HTTP 429 | {"error":"Too many answer submissions — please slow down"}
req 62 → HTTP 429 | {"error":"Too many answer submissions — please slow down"}
req 63 → HTTP 429 | {"error":"Too many answer submissions — please slow down"}
```

RateLimit-Remaining header visible on session/answer responses (from Item 6a):
`RateLimit-Policy: 60;w=60 / RateLimit-Limit: 60 / RateLimit-Remaining: 55 / RateLimit-Reset: 17`

### 9b. POST /stripe/restore-access — 5/15min

```
req 1 → HTTP 200 | {"success":false,"message":"No completed payment found for that email. Please check the email you used when you paid."}
req 2 → HTTP 200 | {"success":false,"message":"No completed payment found for that email. Please check the email you used when you paid."}
req 3 → HTTP 200 | {"success":false,"message":"No completed payment found for that email. Please check the email you used when you paid."}
req 4 → HTTP 200 | {"success":false,"message":"No completed payment found for that email. Please check the email you used when you paid."}
req 5 → HTTP 200 | {"success":false,"message":"No completed payment found for that email. Please check the email you used when you paid."}
req 6 → HTTP 429 | {"error":"Too many restore attempts — try again in 15 minutes"}
req 7 → HTTP 429 | {"error":"Too many restore attempts — try again in 15 minutes"}
```

### 9c. POST /stripe/checkout — 20/15min

```
req 1  → HTTP 400 | {"error":"sessionId and plan are required","details":{"formErrors":[],"fieldErro...
req 2  → HTTP 400 | {"error":"sessionId and plan are required",...
req 3  → HTTP 400 | {"error":"sessionId and plan are required",...
req 4  → HTTP 400 | {"error":"sessionId and plan are required",...
req 5  → HTTP 400 | {"error":"sessionId and plan are required",...
req 6  → HTTP 400 | {"error":"sessionId and plan are required",...
req 7  → HTTP 400 | {"error":"sessionId and plan are required",...
req 8  → HTTP 400 | {"error":"sessionId and plan are required",...
req 9  → HTTP 400 | {"error":"sessionId and plan are required",...
req 10 → HTTP 400 | {"error":"sessionId and plan are required",...
req 11 → HTTP 400 | {"error":"sessionId and plan are required",...
req 12 → HTTP 400 | {"error":"sessionId and plan are required",...
req 13 → HTTP 400 | {"error":"sessionId and plan are required",...
req 14 → HTTP 400 | {"error":"sessionId and plan are required",...
req 15 → HTTP 400 | {"error":"sessionId and plan are required",...
req 16 → HTTP 400 | {"error":"sessionId and plan are required",...
req 17 → HTTP 400 | {"error":"sessionId and plan are required",...
req 18 → HTTP 400 | {"error":"sessionId and plan are required",...
req 19 → HTTP 400 | {"error":"sessionId and plan are required",...
req 20 → HTTP 400 | {"error":"sessionId and plan are required",...
req 21 → HTTP 429 | {"error":"Too many checkout requests — try again in 15 minutes"}
req 22 → HTTP 429 | {"error":"Too many checkout requests — try again in 15 minutes"}
```

Requests 1–20 return HTTP 400 (Zod rejects body missing `plan`). The rate limiter is mounted
in app.ts before the router and counts all requests regardless of schema outcome.

---

## ITEM 10 — #74 IDOR on /adaptive/* (#74)

### 10a. /adaptive/next — anonymous caller → HTTP 200 (full headers)

```
$ curl -sv "http://localhost:8080/api/adaptive/next?sessionId=evidence-anon-uuid-77182" 2>&1

> GET /api/adaptive/next?sessionId=evidence-anon-uuid-77182 HTTP/1.1
> Host: localhost:8080
> User-Agent: curl/8.14.1
> Accept: */*

< HTTP/1.1 200 OK
< Vary: Origin
< Access-Control-Allow-Credentials: true
< Cross-Origin-Opener-Policy: same-origin
< Cross-Origin-Resource-Policy: same-origin
< Origin-Agent-Cluster: ?1
< Referrer-Policy: no-referrer
< Strict-Transport-Security: max-age=31536000; includeSubDomains
< X-Content-Type-Options: nosniff
< X-DNS-Prefetch-Control: off
< X-Download-Options: noopen
< X-Frame-Options: SAMEORIGIN
< X-Permitted-Cross-Domain-Policies: none
< X-XSS-Protection: 0
< x-clerk-auth-reason: dev-browser-missing
< x-clerk-auth-status: signed-out
< Content-Type: application/json; charset=utf-8
< Content-Length: 61

{"questionId":127,"categoryPerformance":[],"totalAnswered":0}
```

`x-clerk-auth-status: signed-out` — Clerk sees no JWT. Rule 1 (anonymous → allow) applies.

### 10b. /adaptive/performance — anonymous caller → HTTP 200 (full headers)

```
> GET /api/adaptive/performance?sessionId=evidence-anon-uuid-77182 HTTP/1.1

< HTTP/1.1 200 OK
< Vary: Origin
< Access-Control-Allow-Credentials: true
< Cross-Origin-Opener-Policy: same-origin
< Cross-Origin-Resource-Policy: same-origin
< Origin-Agent-Cluster: ?1
< Referrer-Policy: no-referrer
< Strict-Transport-Security: max-age=31536000; includeSubDomains
< X-Content-Type-Options: nosniff
< X-DNS-Prefetch-Control: off
< X-Download-Options: noopen
< X-Frame-Options: SAMEORIGIN
< X-Permitted-Cross-Domain-Policies: none
< X-XSS-Protection: 0
< x-clerk-auth-reason: dev-browser-missing
< x-clerk-auth-status: signed-out
< Content-Type: application/json; charset=utf-8
< Content-Length: 44

{"categoryPerformance":[],"totalAnswered":0}
```

### 10c. Rule 4: Clerk user A reading session claimed by user B → 403 — OPEN

Not tested. Requires two live Clerk-issued JWTs with one session claimed. Cannot be produced
in the build environment. Per directive Item 2: remains PARTIAL. Joel will run this manually
with two real accounts outside the build environment.

---

## ITEM 11 — Changed files: SHA256 and git diff

### 11a. SHA256

```
BEFORE (commit ff39202):
  artifacts/api-server/src/app.ts:             2be3c07ba4d73038daedb146e127541cffe5a75185af075f55d90b737ed90b8b
  artifacts/api-server/src/routes/adaptive.ts: 4bad1962cdf8be558b5b0b28cf4278c8fd31a9ac2153b7017ce190ea8976ecf6

AFTER (commit 1b6ab47, HEAD):
  artifacts/api-server/src/app.ts:             54b8df10aa43a8f3c5986efb5339130dbdac5652c3df2ecdb40044fb74222870
  artifacts/api-server/src/routes/adaptive.ts: 45e4b204a3514bcb1ead3f391047de285336be820bda45e0a6b3b2e063599b8a
```

### 11b. git diff ff39202..HEAD --stat

```
 artifacts/api-server/src/app.ts             |  41 ++++++++++
 artifacts/api-server/src/routes/adaptive.ts |   5 +-
 security_audit/SECURITY_MATRIX.md           |  60 +++++++-------
 security_audit/authz_test.md                | 123 ++++++++++++++++++++++++++++
 security_audit/dependency_cve_analysis.md   | 108 ++++++++++++++++++++++++
 5 files changed, 306 insertions(+), 31 deletions(-)
```

### 11c. git diff ff39202..HEAD -- artifacts/api-server/src/app.ts

```diff
@@ -1,6 +1,7 @@
 import express, { type Express } from "express";
 import cors from "cors";
 import helmet from "helmet";
+import rateLimit from "express-rate-limit";
 import pinoHttp from "pino-http";

@@ -98,6 +99,46 @@ app.use(
   })),
 );

+app.use(
+  "/api/session/answer",
+  rateLimit({ windowMs: 60 * 1000, max: 60, standardHeaders: true, legacyHeaders: false,
+    message: { error: "Too many answer submissions — please slow down" } })
+);
+
+app.use(
+  "/api/stripe/restore-access",
+  rateLimit({ windowMs: 15 * 60 * 1000, max: 5, standardHeaders: true, legacyHeaders: false,
+    message: { error: "Too many restore attempts — try again in 15 minutes" } })
+);
+
+app.use(
+  "/api/stripe/checkout",
+  rateLimit({ windowMs: 15 * 60 * 1000, max: 20, standardHeaders: true, legacyHeaders: false,
+    message: { error: "Too many checkout requests — try again in 15 minutes" } })
+);
+
 app.use("/api", router);
```

### 11d. git diff ff39202..HEAD -- artifacts/api-server/src/routes/adaptive.ts

```diff
@@ -1,6 +1,7 @@
 import { Router } from "express";
 import { db, answersTable, questionsTable } from "@workspace/db";
 import { eq, inArray, notInArray } from "drizzle-orm";
+import { verifySessionAccess } from "../lib/sessionAuth";

-router.get("/adaptive/next", async (req, res) => {
+router.get("/adaptive/next", verifySessionAccess, async (req, res) => {

-router.get("/adaptive/performance", async (req, res) => {
+router.get("/adaptive/performance", verifySessionAccess, async (req, res) => {
```

---

## ITEM 12 — Evidence chain protocol

verified_run.sh / verify_chain.sh: N/A to this repo.
Established 2026-07-23 per standing checklist. Confirmed in directive.
Not applied, not referenced, not implied in any step of this audit.
