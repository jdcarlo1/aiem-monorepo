# Phase 2 — API Standardization: DRAFT Verification Record

**Phase:** Phase 2 of 12, Section 3: API Standardization (API-001–040)
**Status:** OPEN — Phase 2 does NOT close until the items listed below are resolved or explicitly deferred with user sign-off.
**Verification date:** 2026-07-22
**Baseline commit (pre-remediation):** `7f12c155c9279e079030c40165e4b8728f774b4b`
**Post-remediation HEAD:** `0d8bc5d369611b0bdb11fe638c3051cafcbe3d70`
**Scope:** AIEM Institutional Terminal API (6 routes: health + 5 admin)
**Standing protocol:** Raw evidence per item; no fabricated data; no narration without proof.
**Supersedes:** `phase2-api-standardization-FINAL.md` — that file was committed claiming Phase 2 closed; this DRAFT records the corrected verdict.

---

## Corrected Summary Verdict

| Category | Count |
|---|---|
| PASS | 31 |
| PARTIAL | 4 |
| NOT_IMPLEMENTED (open) | 4 |
| N/A (documented) | 1 |

**TOTAL: 40 items (API-001–040)**

Phase 2 does NOT close here. The four NOT_IMPLEMENTED items and four PARTIAL items remain open. Phase 3 may not begin or be declared against this phase until either (a) all items reach PASS, or (b) each open item is explicitly deferred with user sign-off logged in this file.

---

## What changed from the overclaimed FINAL record

The file `phase2-api-standardization-FINAL.md` (sha256 `a8e11e44...` before correction) claimed 34 PASS / 1 PARTIAL / 4 carried. That was wrong:

- **API-011** was promoted to PASS — it is PARTIAL (offset-only pagination, no cursor)
- **API-012** was promoted to PASS — it is PARTIAL (no standardized filter language)
- **API-029** was promoted to PASS — it is PARTIAL (implicit allowlist, undocumented)
- **API-032** was double-counted — listed as both the 1 PARTIAL and one of the 4 carried; it is one item
- **API-013** was absent — it is NOT_IMPLEMENTED (no sort param exposed)
- **API-020** was absent — it is NOT_IMPLEMENTED (no deprecation policy in spec)
- **API-028** was absent — it is NOT_IMPLEMENTED (no sort param; no allowlist)

Corrected: 34 - 3 (moved to PARTIAL) = **31 PASS / 4 PARTIAL / 4 NOT_IMPLEMENTED / 1 N/A**

---

## Raw grep evidence for every open item

### API-011 PARTIAL — Offset-only pagination (no cursor)

```
$ grep -ni "cursor\|next_cursor\|next_page_token" lib/api-spec/aiem-terminal-openapi.yaml
(no output — exit=1)
```

Offset/limit is implemented. Cursor pagination is absent from spec and routes.

### API-012 PARTIAL — No standardized filter language

```
$ grep -ni "filter.*language\|query.*language\|standardized.*filter\|filter_by.*spec" lib/api-spec/aiem-terminal-openapi.yaml
(no output — exit=1)
```

Individual filter params (ticker, date, gate_name, etc.) exist. No standardized query filter language.

### API-013 NOT_IMPLEMENTED — Sorting not documented; no sort param exposed

```
$ grep -n "sort_by\|sort_order\|order_by\|&sort\|?sort" artifacts/stock-scanner-api/main.py | grep -E "^69[0-9]{3}:"
(no output — exit=1)
```

Terminal routes hardcode `ORDER BY created_at DESC` (or equivalent). No sort parameter is accepted. Item is NOT_IMPLEMENTED, not N/A: sorting could be documented as "hardcoded DESC by timestamp" with that noted in the spec, but it is not.

### API-020 NOT_IMPLEMENTED — Deprecation policy not in spec

```
$ grep -ni "deprecat\|sunset\|lifecycle\|policy" lib/api-spec/aiem-terminal-openapi.yaml
(no output — exit=1)
```

`lib/api-spec/aiem-terminal-openapi.yaml` contains no deprecation policy, sunset header, or lifecycle documentation.

### API-028 NOT_IMPLEMENTED — Sorting allowlist absent (no sort param)

```
$ grep -n "sort.*allowlist\|SORT_FIELDS\|allowed_sort\|sort_whitelist" artifacts/stock-scanner-api/main.py | grep -E "^69[0-9]{3}:"
(no output — exit=1)
```

No sort parameter is accepted by any Terminal route; no allowlist exists.

### API-029 PARTIAL — Filtering allowlist implicit, undocumented

```
$ grep -ni "filter.*allowlist\|allowed_filter\|FILTER_FIELDS\|filter_whitelist" lib/api-spec/aiem-terminal-openapi.yaml
(no output — exit=1)
```

Filtering is allowlisted by construction (only explicitly coded `request.args.get(...)` params are evaluated; unknown params are silently ignored). The allowlist is not documented in the spec or in code as an explicit data structure.

### API-032 PARTIAL — DB timeout present; WSGI-level timeout absent

Statement-timeout (outgoing DB call) is present on all 4 SQL routes:

```
$ grep -n "statement_timeout=5000" artifacts/stock-scanner-api/main.py | grep -E "^69[0-9]{3}:"
69069:                             options="-c statement_timeout=5000") as conn:
69143:                             options="-c statement_timeout=5000") as conn:
69210:                             options="-c statement_timeout=5000") as conn:
69285:                             options="-c statement_timeout=5000") as conn:
```

No WSGI-level or per-route wall-clock timeout wraps the entire request lifecycle. `signal.alarm` is unsafe in werkzeug threaded mode. PARTIAL: outgoing DB timeout only.

---

## PASS items — unchanged from remediation work

All items listed as PASS below were verified by raw grep/curl in the session that produced this record. Evidence is in the companion `evidence_chain.log` entries (pre-correction session) and in the standing chat record for this session (2026-07-22).

| Item | Description | Verdict |
|---|---|---|
| API-001 | Terminal spec file exists | PASS |
| API-002 | All 6 paths documented | PASS |
| API-003 | Request params documented | PASS |
| API-004 | Auth requirement documented | PASS |
| API-005 | Error shapes in spec | PASS |
| API-006 | Auth security scheme | PASS |
| API-007 | Pagination documented (limit/offset) | PASS |
| API-008 | Filtering params documented | PASS |
| API-009 | Source tables in spec | PASS |
| API-010 | Response schema documented | PASS |
| API-014 | Freshness notes in spec | PASS |
| API-015 | Tags present | PASS |
| API-016 | OperationIds present | PASS |
| API-017 | Verification status documented | PASS |
| API-018 | Structured error shape (code field) | PASS |
| API-019 | Version documented (1.0.0 in info block) | PASS |
| API-021 | Contract tests exist | PASS |
| API-022 | Field mismatch detection | PASS |
| API-023 | Schema validation | PASS |
| API-024 | Numeric params reject invalid values | PASS (pre-existing) |
| API-025 | Date params reject invalid values | PASS |
| API-027 | Pagination limits enforced (≤200) | PASS (pre-existing) |
| API-030 | SQL queries parameterized | PASS (pre-existing) |
| API-031 | Statement timeouts configured | PASS |
| API-033 | Endpoint latency recorded (elapsed_ms) | PASS |
| API-034 | API counts reconcile with SQL | PASS |
| API-035 | Live HTTP testing (Terminal-scoped) | PASS |
| API-036 | Missing-auth tests | PASS |
| API-037 | Wrong-role tests | PASS |
| API-038 | Malformed-input tests | PASS |
| API-039 | Empty-result tests | PASS |

**Count: 31 PASS**

| Item | Description | Verdict |
|---|---|---|
| API-011 | Pagination documented | PARTIAL — offset-only; cursor deferred |
| API-012 | Filtering documented | PARTIAL — no standardized filter language |
| API-029 | Filtering allowlist | PARTIAL — implicit by construction; undocumented |
| API-032 | Request timeout configured | PARTIAL — DB timeout only; no WSGI timeout |

**Count: 4 PARTIAL**

| Item | Description | Verdict |
|---|---|---|
| API-013 | Sorting documented | NOT_IMPLEMENTED |
| API-020 | Deprecation policy documented | NOT_IMPLEMENTED |
| API-028 | Sorting allowlist | NOT_IMPLEMENTED |
| API-040 | Stale-data tests | NOT_IMPLEMENTED |

**Count: 4 NOT_IMPLEMENTED**

| Item | Description | Verdict |
|---|---|---|
| API-026 | Boolean param validation | N/A — no boolean params in Terminal routes |

**Count: 1 N/A**

**Total: 31 + 4 + 4 + 1 = 40 ✓**

---

## Open items — resolution required before Phase 2 closes

| Item | What is needed |
|---|---|
| API-011 | Decision: implement cursor pagination, OR explicitly defer with user sign-off |
| API-012 | Decision: implement standardized filter language (e.g. `filter[field]=value`), OR defer |
| API-013 | Add spec note documenting hardcoded sort order (DESC by timestamp per route), OR expose a sort param |
| API-020 | Add deprecation policy section to `aiem-terminal-openapi.yaml` |
| API-028 | Document sort allowlist in spec (currently N/A — no sort param accepted; document that) |
| API-029 | Explicitly document filter allowlist in spec (which params are accepted; unknown params silently ignored) |
| API-032 | Implement WSGI-level request timeout, OR explicitly defer with documented reason |
| API-040 | Decision: implement stale-data detection on Terminal routes, OR formally defer |

Phase 2 closes only when each row above has either a PASS verdict with raw evidence, or a user-signed deferral note appended to this file.
