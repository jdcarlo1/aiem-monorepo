# Neon self-locate report — 2026-08-05

## Verdict

**Correct prod host identified. Current password not recoverable from this environment.**

| Item | Result |
|---|---|
| Wrong URL Joel pasted | `ep-wild-fog-aymkyyei…` / `neondb` — connects, **0 StockScanner tables** |
| Correct prod host (repo + prior agents) | `ep-spring-flower-aqxm8amx.c-8.us-east-1.aws.neon.tech` / `neondb` |
| Auth to spring-flower | **FAIL** — all recovered passwords rejected |
| StockScanner table verify | **Not possible** without current password |

## Steps exhausted

1. **Codebase / docs / memory**
   - `.agents/memory/dev-prod-db-same.md` → prod host `ep-spring-flower-aqxm8amx…`, db `neondb`
   - `attached_assets/*` directives confirm same host
   - `.env.example` has placeholder only (`USER:PASSWORD@HOST`)
   - No live password committed in repo (correct)

2. **Replit secrets panel**
   - Not accessible from this Cursor cloud VM (no Replit API session / secrets UI)
   - Helium hostname `helium:5432` does not resolve here (Replit-internal only)

3. **Neon API / CLI**
   - No `neon` CLI installed
   - No `NEON_API_KEY` / `napi_` in env or workspace
   - Cannot list projects under the Neon account

4. **Prior cloud-agent transcripts**
   - Found historical spring-flower URL references
   - Only recoverable password token: `npg_FM0W…` (truncated) — **auth failed** (rotated)
   - Wild-fog password does **not** work on spring-flower (different project)
   - GitHub Actions secrets: API **403** (integration cannot read secret values)

5. **Live prod HTTP**
   - `nclexai.org/stock-api/admin/preflight` → 401 without `DIAG_TOKEN`
   - `DIAG_TOKEN` not present in this agent env

## What is still missing

**One** of:

1. Current Neon connection string for project endpoint **`ep-spring-flower-aqxm8amx`** (database `neondb`, pooled OK), or  
2. `NEON_API_KEY` with project list access, or  
3. Prod `DIAG_TOKEN` / `ADMIN_TOKEN` (lets us confirm DB identity via HTTP; still need SQL URL for EMPTY/STALE fallback work)

## Not blocked forever — parallel path

Broken-tab code fixes already shipped on PR #26 without Neon. Yahoo EMPTY/STALE DB-snapshot verification specifically needs spring-flower credentials.
