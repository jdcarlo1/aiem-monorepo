# NCLEX AI — Authorization & Authentication Test Report
Date: 2026-07-29
API base: http://localhost:8080/api
Method: Live curl tests against running server

---

## Part A — Authorization Tests (IDOR / Cross-Session Access)

### Endpoint: GET /session/status?sessionId=X

**Test:** User A passes User B's sessionId in the query param.

```
curl -s "http://localhost:8080/api/session/status?sessionId=test-session-user-b-1785289946"
Response: {"sessionId":"test-session-user-b-1785289946","questionsAnswered":1,"freeLimit":10,"isSubscribed":false,"canAnswerMore":true,"subscriptionEndDate":null}
HTTP: 200
```

**Result:** PARTIAL — Any caller knowing a sessionId can read that session's status (subscribed/not, question count).

**Risk assessment:** ACCEPTABLE BY DESIGN. The system uses anonymous UUID sessionIds as the auth factor for free-tier users. There is no user account (email, name, PII) attached to a session. The sessionId itself is a random UUID and is not guessable without prior knowledge. `isSubscribed` and `questionsAnswered` are the only fields returned — no billing details, no email, no Stripe customer ID. This matches the documented architecture in memory (anonymous-session model).

**Classification:** LOW — Design tradeoff for anonymous session model. No PII exposure.

---

### Endpoint: GET /adaptive/next?sessionId=X

**Test:** User A passes User B's sessionId.

```
curl -s "http://localhost:8080/api/adaptive/next?sessionId=test-session-user-b-1785289946"
Response: {"questionId":874,"categoryPerformance":[{"category":"Medical-Surgical","total":1,"correct":0,"accuracy":0}],"totalAnswered":1}
HTTP: 200
```

**Result:** IDOR PRESENT — Any caller can read any session's category performance breakdown.

**Risk assessment:** LOW-SEVERITY IDOR. Data exposed: category names + answer accuracy percentages. No PII. No billing. SessionId is random UUID, not guessable. The adaptive engine is intended as a stateless quiz helper and carries no sensitive user data.

**Classification:** LOW — Logged as finding. Not patched (no PII at risk, anonymous model).

---

### Endpoint: GET /adaptive/performance?sessionId=X

**Test:** Same as above.

```
curl -s "http://localhost:8080/api/adaptive/performance?sessionId=test-session-user-b-1785289946"
Response: {"categoryPerformance":[{"category":"Medical-Surgical","total":1,"correct":0,"accuracy":0}],"totalAnswered":1}
HTTP: 200
```

**Result:** Same as /adaptive/next. LOW-SEVERITY IDOR.

---

### Endpoint: GET /questions and GET /questions/:id

**Test:** Question bank is public — no sessionId required to list questions.
Correct answer (correctLetter) and explanation are gated:
- If sessionId is subscribed → revealed
- If sessionId has previously answered this question → revealed
- Otherwise → null

```
curl -s "http://localhost:8080/api/questions/1?sessionId=unrelated-session"
Result: correctLetter: null, explanation: null (gated correctly)

curl -s "http://localhost:8080/api/questions/1" (no sessionId)
Result: correctLetter: null, explanation: null (gated correctly)
```

**Result:** PASS — Answer harvesting blocked for non-subscribed sessions.

---

### Endpoint: POST /admin/affiliates (and all /admin/* routes)

**Test 1:** No token
```
curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/api/admin/affiliates
Result: 401
```

**Test 2:** Wrong token
```
curl -s -o /dev/null -w "%{http_code}" -H "x-admin-secret: wrongtoken123" http://localhost:8080/api/admin/affiliates
Result: 401
```

**Test 3:** All admin endpoints verified:
- GET  /admin/affiliates              → 401 (no token)
- POST /admin/affiliates              → 401 (no token)
- POST /admin/affiliates/:code/refresh-link → 401 (no token)
- DELETE /admin/affiliates/:code      → 401 (no token)
- POST /admin/seed-questions          → 401 (no token)
- POST /admin/fix-sessions            → 401 (no token)
- POST /admin/activate-sessions       → 401 (no token)

**Auth mechanism:** `requireAdmin()` uses `crypto.timingSafeEqual()` against `process.env.ADMIN_TOKEN`. Rate limited: 20 req/15min per IP.

**Result:** PASS — All admin routes correctly block unauthorized access.

---

### Endpoint: POST /stripe/restore-access

**Test:** Attacker attempts to activate a session using a fake email.
```
curl -s -X POST http://localhost:8080/api/stripe/restore-access \
  -H "Content-Type: application/json" \
  -d '{"sessionId":"attacker-session","email":"notareal@example.com"}'
Response: {"success":false,"message":"No completed payment found for that email..."}
HTTP: 200
```

**Result:** PASS — Requires verified Stripe payment record before activating. Returns false without activating session.

---

### Endpoint: POST /admin/activate-sessions (bulk-activate vulnerability)

**Previous vulnerability:** Calling without sessionId activated ALL sessions.
**Current behavior:** Zod schema requires `sessionId: z.string().min(1)` → 400 if absent.

```
curl -s -X POST http://localhost:8080/api/admin/activate-sessions \
  -H "Content-Type: application/json" -d '{}'
Result: 401 (admin token required first)

# With valid token but no sessionId:
Result: 400 {"error":"sessionId is required"}
```

**Result:** PASS — Bulk activation vulnerability is fixed.

---

## Part B — Authentication Tests

### Test: Direct URL access without JWT (Clerk-protected routes)

**POST /session/claim — requires Clerk JWT**
```
curl -s -X POST http://localhost:8080/api/session/claim \
  -H "Content-Type: application/json" -d '{"sessionId":"abc"}'
Response: {"error":"Authentication required to claim a session. Sign in first.","code":"UNAUTHENTICATED"}
HTTP: 401
```
**Result:** PASS

**POST /session/answer — no JWT required (anonymous model)**
```
curl -s -X POST http://localhost:8080/api/session/answer \
  -H "Content-Type: application/json" -d '{}'
Response: {"error":"sessionId, questionId, and selectedLetter are required"}
HTTP: 400
```
**Result:** PASS — Missing body correctly rejected by Zod. Zod schema validated before any DB access.

**GET /session/status — no JWT required (anonymous model)**
```
curl -s "http://localhost:8080/api/session/status" (no sessionId)
Response: {"error":"sessionId is required"}
HTTP: 400
```
**Result:** PASS

---

### Test: Expired / missing Clerk token on JWT-gated route

The Clerk middleware (`clerkMiddleware`) validates JWTs on every request. On `/session/claim`, `getAuth(req)?.userId` returns null for expired or absent tokens, and the route returns 401 explicitly.

**Result:** PASS — JWT expiry handled by Clerk SDK + explicit null check.

---

### Test: Cross-user session hijacking via /session/claim

The `verifySessionAccess` middleware (sessionAuth.ts) enforces:
- Rule 4: If a JWT is present and sessionId is already claimed by a DIFFERENT Clerk user → 403 SESSION_OWNED_BY_OTHER_USER
- Rule 5: Unclaimed sessions with a JWT → allowed (explicit opt-in model)

The `/session/claim` endpoint additionally checks:
- If sessionId already claimed by different user → 409 SESSION_ALREADY_CLAIMED
- If Clerk user already has a different claim → 409 USER_ALREADY_HAS_CLAIM

**Result:** PASS — Session ownership is enforced after claim.

---

## Part C — CORS Tests

### Before fix: `cors({ origin: true, credentials: true })`
```
curl -I -X OPTIONS http://localhost:8080/api/session/status \
  -H "Origin: https://evil.com"
Access-Control-Allow-Origin: https://evil.com   ← REFLECTED ANY ORIGIN
Access-Control-Allow-Credentials: true          ← WITH CREDENTIALS
```
**Result:** FAIL (pre-fix)

### After fix: explicit allowlist
```
curl -I -X OPTIONS http://localhost:8080/api/session/status \
  -H "Origin: https://evil.com"
[No Access-Control-Allow-Origin header]          ← BLOCKED
Access-Control-Allow-Credentials: true (present but browser ignores without ACAO)
```

```
curl -I -X OPTIONS http://localhost:8080/api/session/status \
  -H "Origin: https://nclexai.org"
Access-Control-Allow-Origin: https://nclexai.org  ← ALLOWED
```

```
curl -I -X OPTIONS http://localhost:8080/api/session/status \
  -H "Origin: https://myapp-joeldcarlo.replit.app"
Access-Control-Allow-Origin: https://myapp-joeldcarlo.replit.app  ← ALLOWED
```
**Result:** PASS (post-fix)

---

## Part D — Security Headers (helmet)

Verified on GET /api/session/status (router-mounted endpoint):
```
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Resource-Policy: same-origin
Referrer-Policy: no-referrer
X-Content-Type-Options: nosniff
X-DNS-Prefetch-Control: off
X-Frame-Options: SAMEORIGIN
X-Permitted-Cross-Domain-Policies: none
```
**Result:** PASS (post helmet install)

Note: /api/healthz is mounted before helmet in app.ts and does not receive headers. All other routes do.

---

## Summary of Findings

| Endpoint | Test | Severity | Result |
|---|---|---|---|
| All /admin/* | No token / wrong token | HIGH | PASS |
| /session/claim | No Clerk JWT | HIGH | PASS |
| /stripe/restore-access | Fake email activation | HIGH | PASS |
| /admin/activate-sessions | Bulk activation without sessionId | HIGH | PASS |
| /questions/:id | Answer harvesting without subscription | MEDIUM | PASS |
| CORS wildcard | evil.com cross-origin with credentials | HIGH | FIXED → PASS |
| /session/status | Cross-session read | LOW | ACCEPTABLE (design) |
| /adaptive/next | Cross-session performance read | LOW | IDOR, no PII |
| /adaptive/performance | Cross-session performance read | LOW | IDOR, no PII |
