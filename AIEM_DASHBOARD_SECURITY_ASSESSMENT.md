# AIEM DASHBOARD — PHASE A
## Security & Commercial Deployment Assessment (Section 10 Response)
**Generated:** 2026-07-21

---

## Assessment Key
- **READY** — implemented and adequate for current use
- **PARTIAL** — partially implemented; gaps documented
- **MISSING** — not implemented
- **NOT_VERIFIED** — not audited in Phase A; cannot confirm

---

## Authentication

| Item | Status | Detail |
|------|--------|--------|
| Admin route auth | READY | `X-Admin-Token` header checked via `hmac.compare_digest` against `ADMIN_TOKEN` env secret; fail-closed |
| AIEM chat auth | READY | HMAC signing via `aiem_security.py` |
| Public routes | READY | Explicitly no auth; intentional (scanner product) |
| Session tokens | PARTIAL | Chat sessions use job_id; no server-side session invalidation |
| ADMIN_TOKEN rotation | PARTIAL | `key_rotation_pattern.md` documents rotation; no grace period; requires Replit secret update |
| Multi-tenant tokens | MISSING | Single global ADMIN_TOKEN; no per-tenant auth |
| JWT / OAuth | MISSING | Not implemented; ADMIN_TOKEN is bearer-equivalent |

---

## Role-Based Access Control (RBAC)

| Item | Status | Detail |
|------|--------|--------|
| Admin vs public separation | PARTIAL | Two tiers: `X-Admin-Token` routes vs public routes; no fine-grained roles |
| Read vs write admin | MISSING | Admin token grants full access — read AND write AND trigger; no read-only admin role |
| Subscriber vs owner | PARTIAL | Some user routes exist (`/stock-api/user/prefs`) but not enforced by role |
| Row-level security | MISSING | No PostgreSQL RLS; any DB connection reads all tables |

---

## Tenant Isolation

| Item | Status | Detail |
|------|--------|--------|
| Multi-tenant isolation | MISSING | Single-tenant only; all data shared in one DB |
| Tenant-scoped tokens | MISSING | One ADMIN_TOKEN per deployment |
| Tenant-scoped data | MISSING | No tenant_id column in any table |
| **Implication for sale** | MISSING | AIEM as currently built is single-operator only — institutional sale means one AIEM instance per customer |

---

## API Key Handling

| Item | Status | Detail |
|------|--------|--------|
| ADMIN_TOKEN storage | READY | Stored in Replit Secrets env var; never in code |
| POLYGON_API_KEY | READY | Stored in Replit Secrets |
| TRADIER_API_TOKEN | READY | Stored in Replit Secrets |
| AIEM_HMAC_SECRET | READY | Stored in Replit Secrets |
| Keys in code | READY | No hardcoded keys found |
| Keys in logs | NOT_VERIFIED | Logs not audited for accidental key leakage |
| Key rotation procedure | PARTIAL | Documented for signing key; ADMIN_TOKEN requires manual Replit update |

---

## Session Security

| Item | Status | Detail |
|------|--------|--------|
| Session expiry | PARTIAL | Chat verify links: 7-day TTL. Admin session: none (stateless header check) |
| Session fixation protection | MISSING | No session management for admin |
| CSRF protection | MISSING | No CSRF tokens on any POST route |
| Cookie security | NOT_VERIFIED | No session cookies in use (stateless); N/A |

---

## CORS Policy

| Item | Status | Detail |
|------|--------|--------|
| CORS configured | PARTIAL | Flask default allows all origins in dev; not audited for explicit CORS headers |
| CORS for admin routes | NOT_VERIFIED | Admin routes do not add CORS headers by default |
| Recommendation for production | MISSING | Set `Access-Control-Allow-Origin: https://<aiem-dashboard-domain>` on admin routes |

---

## Rate Limiting

| Item | Status | Detail |
|------|--------|--------|
| Admin route rate limiting | MISSING | No rate limiting on any route |
| Public route rate limiting | MISSING | No rate limiting |
| AIEM chat rate limiting | PARTIAL | `all_sessions_share_aiem_qa_lock` serializes concurrent requests but does not reject |
| Recommendation | MISSING | Add Flask-Limiter or nginx rate limit before production multi-user deployment |

---

## Audit Logging

| Item | Status | Detail |
|------|--------|--------|
| Pipeline audit logging | READY | `aiem_pipeline_audit_log` (284 rows) — per-stage logging |
| D3 governance audit | READY | Immutable hash-chain via trigger |
| Decision audit | READY | `oe_decision_audit` (341 rows, cryptographic) |
| Admin action audit | MISSING | No log of who called which admin route when |
| Failed auth audit | NOT_VERIFIED | 403 responses not logged to any table |

---

## Data Export Restrictions

| Item | Status | Detail |
|------|--------|--------|
| `get-source-export` route | PARTIAL | Route exists (`/stock-api/get-source-export`) but not audited for what it exports |
| `all-code-text` route | PARTIAL | Route exists (`/stock-api/all-code-text`) — returns code; admin-gated? Not confirmed |
| CSV export | MISSING | No CSV export for any audit table |
| PII export restriction | NOT_VERIFIED | No PII confirmed in any table; not fully audited |

---

## Database Access Boundaries

| Item | Status | Detail |
|------|--------|--------|
| Single DB shared | PARTIAL | All products share one DB; no schema separation |
| Row-level security | MISSING | No PostgreSQL RLS |
| Read-only DB user for dashboard | MISSING | Dashboard would use full R/W credentials |
| Recommendation | MISSING | Create a read-only PostgreSQL role for dashboard queries |

---

## Domain and HTTPS

| Item | Status | Detail |
|------|--------|--------|
| HTTPS in development | READY | Replit proxy provides HTTPS via mTLS |
| Custom domain | NOT_VERIFIED | Not configured; would require Replit deployment + domain setup |
| HSTS | NOT_VERIFIED | Not configured in Flask |
| Certificate management | NOT_VERIFIED | Delegated to Replit deployment infrastructure |

---

## Environment Separation

| Item | Status | Detail |
|------|--------|--------|
| Dev vs prod DB | PARTIAL | Separate DB URLs; schema drift fixed 2026-07-11 |
| Dev vs prod secrets | PARTIAL | Separate Replit secrets per environment |
| Environment variable validation | PARTIAL | Main.py checks for required secrets at startup |
| Production monitoring | PARTIAL | Health endpoint + Telegram watchdog alerts |

---

## Dependency Vulnerability Status

| Item | Status | Detail |
|------|--------|--------|
| Python packages | NOT_VERIFIED | No `pip audit` run in Phase A; see validation skill for security scan |
| Node.js packages | NOT_VERIFIED | Frontend not audited |
| Known CVEs | NOT_VERIFIED | Not checked in Phase A |
| Recommendation | MISSING | Run `runDependencyAudit` via security_scan skill before production |

---

## Summary for Commercial Deployment Readiness

| Requirement | Status | Blocker for Sale? |
|-------------|--------|-------------------|
| Basic auth (single-operator) | READY | No |
| Multi-tenant auth | MISSING | YES — for multiple institutional customers |
| RBAC | MISSING | YES — operators need read vs write vs trigger roles |
| CSRF protection | MISSING | YES for write routes |
| Rate limiting | MISSING | YES for production |
| Audit logging (admin actions) | MISSING | YES for compliance |
| Read-only DB user | MISSING | RECOMMENDED |
| CORS explicit policy | MISSING | YES for cross-origin dashboard |
| Dependency audit | NOT_VERIFIED | YES before production |
| Single-operator deployment | READY | — single customer can use now |
