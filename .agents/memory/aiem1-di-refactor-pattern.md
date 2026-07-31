---
name: AIEM-1 DI refactor — _aiem_paper_pick_candidates injection pattern
description: How the DI refactor was done, gotchas with regex hitting eff-resolution block comments, and the dead-zone/forward-ref timing issue.
---

# AIEM-1 DI Refactor Pattern

**Rule:** When adding injectable deps to a large function via regex substitution,
the eff-resolution block (which maps `injected_param or MODULE_GLOBAL`) is INSIDE
the function body and will be hit by the same regex. Fix immediately after the
substitution pass.

**How to apply:**

1. Write the eff-resolution block using the ORIGINAL module global names (e.g.
   `_db_url_eff = db_url or _DB_URL`).
2. Run `re.sub(r'\b_DB_URL\b', '_db_url_eff', fn_body)`.
3. This turns the eff block line into `_db_url_eff = db_url or _db_url_eff`
   — a circular self-reference that raises `NameError` at runtime when the
   param is `None` (production case).
4. After the substitution loop, patch the 4 affected lines back to the correct
   module globals: `_DB_URL`, `_fred_macro`, `_social_sentiment`,
   `_specialist_council`.
5. The patterns `_psycopg2\.connect\(` and `_econ_is_high_impact_day\(` (which
   require the `(` suffix) are NOT affected because the eff block uses bare
   references without `(`.
6. The regex also hits COMMENTS in the param list (e.g. `# replaces _DB_URL`
   becomes `# replaces _db_url_eff`). Fix those too — cosmetic but confusing.

**Dead-zone / forward-ref timing issue:**

The `_aiem_paper_pick_candidates` function is at line ~47857, after the Flask
route dead zone (~29315–41826). The test endpoint was placed at line 21118
(before the dead zone). The endpoint CAN reference the function because both
are in the same module globals dict — Python fully parses + executes the whole
file before any request is handled. The NameError seen in the first dryrun run
was because the server hadn't finished loading (deferred inits still running
in background threads) when the test was fired <30s after restart.

**Verification count (commit 5581c95):**
- 7 `_psycopg2.connect(` → `_pg2_eff.connect(`
- 19 `_DB_URL` → `_db_url_eff` (including eff block itself — 1 extra; fixed)
- 5 `_fred_macro` → `_fred_macro_eff` (including eff block — fixed)
- 1 `_econ_is_high_impact_day(` → `_econ_eff(`
- 5 `_social_sentiment` → `_social_eff` (including eff block — fixed)
- 5 `_specialist_council` → `_council_eff` (including eff block — fixed)
