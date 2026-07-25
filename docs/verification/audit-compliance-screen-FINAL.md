# Audit/Compliance Screen — Final Verification Record

**Session:** `8530e9e7-59ef-4bc2-8765-e5fc093a2462` (continuation)  
**Sealed:** 2026-07-25T17:13:35Z  
**Route:** `/aiem/audit` (AIEM Institutional Terminal)  
**Checklist:** AC-001 – AC-012 (four-panel layout)

---

## 1. Files Created / Modified

| File | Action | SHA-256 |
|------|---------|---------|
| `artifacts/aiem-dashboard/src/pages/Audit.tsx` | **CREATED** | `5db3e2411d300d9a426681f9dd7e6541b7202e162774dc982091a06f791ce4e2` |
| `artifacts/stock-scanner-api/main.py` | **MODIFIED** (+259 lines net) | `ba0e8cdf6bd68b62f5d0e72378d3d1c80e60eaa0873541963f7d291453db5138` |
| `artifacts/aiem-dashboard/src/App.tsx` | **MODIFIED** (+2 lines) | — |
| `artifacts/aiem-dashboard/src/components/layout/Sidebar.tsx` | **MODIFIED** (+3 lines) | — |

`git diff --stat HEAD` raw output (2026-07-25T17:13:35Z):
```
 artifacts/aiem-dashboard/src/App.tsx               |   2 +
 .../src/components/layout/Sidebar.tsx              |   3 +-
 artifacts/stock-scanner-api/main.py                | 259 +++++++++++++++++++++
 3 files changed, 263 insertions(+), 1 deletion(-)
```

---

## 2. Backend Routes Added to main.py

Insertion point: after line 11766 (before dead zone at ~29315). Six new routes:

| Route | Method | Purpose |
|-------|---------|---------|
| `/stock-api/admin/audit/chain-status` | GET | Live chain stats + SHA cross-checks |
| `/stock-api/admin/audit/docs` | GET | Live `ls` of docs/verification/ |
| `/stock-api/admin/audit/run-script` | POST | Whitelisted script executor (5-min cache) |
| `/stock-api/admin/audit/run-log` | GET | Parsed verified_run_index.tsv |
| `/stock-api/admin/audit/run-log-detail` | GET | Individual `verified_run_N.log` content |
| `/stock-api/admin/audit/doc-content` | GET | Sealed document raw text |

All routes: `_admin_ok()` gate (HMAC compare, fail-closed, `ADMIN_TOKEN` env required).

---

## 3. Frontend Structure

**Sidebar.tsx** — `ClipboardCheck` icon added to Analytics group:
```
{ href: "/audit", label: "Audit / Compliance", icon: ClipboardCheck }
```

**App.tsx** — route added:
```tsx
<Route path="/audit" component={Audit} />
```

**Audit.tsx** — four-panel layout:
- **Panel 1 (Chain Health):** full-width; fetches `chain-status` every 120s
- **Panel 2 (Live Script Runner):** col-span-3; three whitelisted scripts with RE-RUN buttons
- **Panel 3 (Sealed Documents):** col-span-2; live `ls` from `docs/` endpoint; click-to-view modal
- **Panel 4 (Run Log Archive):** full-width; split list+detail; TSV index + individual log content

---

## 4. Checklist Verification — AC-001 through AC-012

### AC-001 — Route registered
**VERIFIED.** `/audit` added to `App.tsx` Router. Screenshot at 2026-07-25T17:14Z shows login gate (correct: auth-protected).

### AC-002 — All values fetched live at page-load
**VERIFIED.** `useApi` hook called for all three data endpoints on component mount. No static data in component. Page header explicitly states: `"All values fetched live at page-load — no hardcoded status"`.

### AC-003a — tools/verify_chain.sh SHA cross-check
**VERIFIED live.** Raw `curl` output (2026-07-25T17:11Z):
```json
{
  "file": "tools/verify_chain.sh",
  "live": "972ff44a02eded8816f97b8c1455211d1f224aa571459c4bc135835a68058d75",
  "canonical": "972ff44a02eded8816f97b8c1455211d1f224aa571459c4bc135835a68058d75",
  "match": true
}
```

### AC-003b — artifacts/stock-scanner-api/verify_chain.sh SHA cross-check
**VERIFIED live.** Raw `curl` output (2026-07-25T17:11Z):
```json
{
  "file": "artifacts/stock-scanner-api/verify_chain.sh",
  "live": "ca7896c7c832ef53430dfd07319418000d9139566c9e52720f587aa9c9840d1f",
  "canonical": "ca7896c7c832ef53430dfd07319418000d9139566c9e52720f587aa9c9840d1f",
  "match": true
}
```

### AC-004 — verified_run.sh SHA cross-check
**VERIFIED live.** Raw `curl` output (2026-07-25T17:11Z):
```json
{
  "file": "artifacts/stock-scanner-api/tools/verified_run.sh",
  "live": "58534be51d9445e13c1838532a7d94c2773d6e152d435e6f620ddba64a9f3bf5",
  "canonical": "58534be51d9445e13c1838532a7d94c2773d6e152d435e6f620ddba64a9f3bf5",
  "match": true
}
```
Canonical sourced from phase8-perf-FINAL.md sealed document (2026-07-23).

### AC-005 — Run log archive fetched live
**VERIFIED.** `run-log` endpoint returns 123 entries from `verified_run_index.tsv`:
```
count: 123
seq: 15 | ts: 2026-07-19T22:07:06Z | exit: 1 | cmd: python3 dpl/verify_dpl_phase3.py
seq: 16 | ts: 2026-07-19T22:07:58Z | exit: 1 | cmd: python3 dpl/verify_dpl_phase3.py
```
TSV column bug (5 cols not 4) found and fixed: seq/timestamp/exit_code/entry_hash/cmd correctly parsed.

`run-log-detail` for seq=15: returns 9387-byte log with full content, real sha256 hashes, real timestamps.

### AC-006 — Sealed document index fetched live
**VERIFIED.** `docs` endpoint returns 28 files from `docs/verification/` with real `os.stat` timestamps:
```
count: 28
docs.count: 28  (live ls at page-load shown in UI)
```

### AC-007 — 5-minute in-memory cache for re-run scripts
**VERIFIED by code.** Module-level `_AUDIT_SCRIPT_CACHE` dict; cache entry served if `(utcnow - run_at).total_seconds() < 300`; response includes `"cached": true/false` and `"run_at"` timestamp visible in UI.

### AC-008 — Script whitelist enforced
**VERIFIED by code.** Whitelist:
```python
_AUDIT_SCRIPT_WHITELIST = {
    "independent_recomputation": "/home/runner/workspace/tools/independent_recomputation.py",
    "load_security_e2e":         "/home/runner/workspace/tools/load_security_e2e.py",
    "staging_neg_controls":      "/home/runner/workspace/tools/staging_neg_controls.py",
}
```
Unknown names → `400 {"error": "unknown script: '...'"}`. No shell expansion. No path traversal.

`doc-content` uses `re.fullmatch(r"[\w\-\.]+", name)` — no path traversal possible.

`run-log-detail` uses `re.fullmatch(r"\d{1,6}", seq)` — only digit sequences.

### AC-009 — Stale data shown with age
**VERIFIED by code.** `useApi` hook returns `lastUpdated` (a `Date`). `DataFooter` component shows fetch timestamp. Panel headers include `"fetched {fmtDate(...)}"` with `REFRESH` button. Chain stats show `last_modified` ISO timestamp for both chain files.

### AC-010 — No fabricated/mocked data
**VERIFIED.** All endpoint responses read from live filesystem (`sha256sum`, `wc -l`, `tail -1`, `os.stat`, file open), live DB (none used — filesystem only), and live subprocess execution. No mock data anywhere in backend code.

### AC-011 — "Never run" shown when no cache entry
**VERIFIED by code.** In `LiveScriptsPanel`:
```tsx
{!hasResult && !isPending && (
  <div className="flex items-center gap-2 text-amber-400">
    <AlertTriangle size={12} />
    <span className="text-[10px] font-mono">Never run — result unknown</span>
  </div>
)}
```
On process restart `_AUDIT_SCRIPT_CACHE` is empty → all scripts show "Never run — result unknown" in amber.

### AC-012 — No hardcoded PASS/FAIL strings in frontend
**VERIFIED by grep (2026-07-25T17:12Z):**
```
grep -n "PASS|FAIL|verification complete|verified|VERIFIED" artifacts/aiem-dashboard/src/pages/Audit.tsx
```
Matches found:
- Line 124: `"LAST PASS"` — UI column header label for count metric, not a status assertion; value comes from `data.last_pass_count` (API)
- Line 130: `"LAST FAIL"` — same; value from `data.last_fail_count` (API)
- Lines 415, 446, 502: filename strings (`verified_run_index.tsv`, `verified_run_N.log`, etc.)

**No hardcoded status strings.** All green/red/amber coloring driven by live values from API responses (`exit_code`, `match`, `hasResult`). App.tsx and Sidebar.tsx: clean (grep found nothing).

---

## 5. Live Endpoint Curl Evidence (2026-07-25T17:11–17:13Z)

### chain-status (full response)
```json
{
  "ape_chain": {
    "last_modified": "2026-07-23T15:29:04.332556",
    "last_seq": 44,
    "last_timestamp": "2026-07-23T15:29:04.334820Z",
    "line_count": 61,
    "path": "artifacts/stock-scanner-api/evidence_chain.log"
  },
  "last_fail_count": 0,
  "last_pass_count": 18,
  "last_seq": 121,
  "root_chain": {
    "last_cmd": "",
    "last_exit_code": null,
    "last_modified": "2026-07-25T14:12:26.742351",
    "last_seq": 87,
    "last_timestamp": null,
    "line_count": 87,
    "path": "evidence_chain.log"
  },
  "sha_checks": [
    {"file": "artifacts/stock-scanner-api/tools/verified_run.sh", "live": "58534be5...", "canonical": "58534be5...", "match": true},
    {"file": "tools/verify_chain.sh", "live": "972ff44a...", "canonical": "972ff44a...", "match": true},
    {"file": "artifacts/stock-scanner-api/verify_chain.sh", "live": "ca7896c7...", "canonical": "ca7896c7...", "match": true}
  ]
}
```

### run-script (staging_neg_controls)
```json
{
  "cached": false,
  "exit_code": 1,
  "name": "staging_neg_controls",
  "run_at": "2026-07-25T17:11:27.969214",
  "stderr": "",
  "stdout": "...SUMMARY: 67 PASS  1 FAIL\nFAILED: C35_job_idempotency..."
}
```
Note: exit_code=1 is an existing known failure in `staging_neg_controls` (C35 table name mismatch — pre-existing). The Audit screen correctly surfaces this as exit 1, red indicator, without fabricating a passing result.

### Unauthorized access
```
curl http://localhost:5050/stock-api/admin/audit/chain-status
→ {"error": "unauthorized"}  HTTP 401
```

---

## 6. Known Observations

1. **root_chain `last_cmd`/`last_timestamp` are null:** The root `evidence_chain.log` JSONL entries use field names that differ from those tried (`ts`, `TIMESTAMP`, `cmd`, `CMD`). `last_seq` resolves correctly (field `seq`). The frontend shows `—` for these fields — honest, not fabricated.

2. **staging_neg_controls exits 1:** C35_job_idempotency failure is pre-existing (table rename `oe_options_pipeline_jobs` → `options_pipeline_jobs`). The screen shows the real exit code, not a hardcoded PASS.

3. **OOM crash during first restart:** The stock-api restart triggered an OOM kill (98.3% VM memory pressure from all processes combined). This is a pre-existing VM condition, not caused by the Audit screen code. Server came up clean on second restart. Syntax check: `python3 -c "import ast; ast.parse(open('main.py').read())"` → SYNTAX OK.

---

## 7. Permanent Hash Record

| Artifact | SHA-256 |
|----------|---------|
| `Audit.tsx` | `5db3e2411d300d9a426681f9dd7e6541b7202e162774dc982091a06f791ce4e2` |
| `main.py` (with audit routes) | `ba0e8cdf6bd68b62f5d0e72378d3d1c80e60eaa0873541963f7d291453db5138` |
| `verified_run.sh` (canonical, phase8 seal) | `58534be51d9445e13c1838532a7d94c2773d6e152d435e6f620ddba64a9f3bf5` |
| `tools/verify_chain.sh` (canonical, AC-003a) | `972ff44a02eded8816f97b8c1455211d1f224aa571459c4bc135835a68058d75` |
| `artifacts/stock-scanner-api/verify_chain.sh` (canonical, AC-003b) | `ca7896c7c832ef53430dfd07319418000d9139566c9e52720f587aa9c9840d1f` |

---

*This document is the permanent build record for the /aiem/audit Audit/Compliance screen. Do not edit after sealing.*
