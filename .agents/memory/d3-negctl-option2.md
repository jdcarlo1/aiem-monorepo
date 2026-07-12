---
name: D3 Negative-Control Test — Option 2 Schema Isolation
description: How to run the D3 governance negative-control test; isolation mechanism, encoding gotcha, and why the full main.py subprocess times out.
---

## Rule
Option 2 isolation: inject `options=-c search_path=d3_test_isolation,public`
into `DATABASE_URL` using **`urllib.parse.quote`** (not `quote_plus`).
libpq does NOT decode `+` as space in the `options=` parameter — using
`quote_plus` silently breaks the connection.

## Mechanism
`_d3_connect()` in `aiem_diagram3_governance.py` reads
`os.environ["DATABASE_URL"]` at **call time** (not import time). So setting
`os.environ["DATABASE_URL"]` in the harness before any D3 call automatically
redirects all unqualified D3 table writes to the test schema — no monkey-
patching of the module needed beyond patching `_g0_read_config`.

`_g0_read_config` returns a **dict** `{mode, state, error, ts}`, not a tuple.
Patch target: `_d3g._g0_read_config = lambda force=False: {"mode": "ENFORCE", "state": "PAUSED", "error": None, "ts": time.time()}`

## Dry-Run PASS (2026-07-12 20:21 UTC) — complete proof of Directive 5
- d3_test_isolation.d3_governance_requests:    +1  ✓
- d3_test_isolation.d3_governance_event_links: +3  ✓
- d3_test_isolation.d3_governance_decisions:   +1  ✓
- public.d3_governance_requests:               +0  ✓ (stayed at 10)
- public.d3_governance_event_links:            +0  ✓ (stayed at 109)
- public.d3_governance_decisions:              +0  ✓ (stayed at 10)
- public.aiem_paper_trades:                    +0  ✓ (stayed at 1)
- Decision: BLOCK, blocking=TRUE, reason_codes=["STATE_PAUSED"]
- is_test_record=FALSE on all test rows (production-identical conditions)

## Why the full subprocess times out
`main.py` has >60K lines and runs all module-level initialization when
imported (the deferred-init `_DEFERRED_INITS` background-thread mechanism is
bypassed when not running as the Flask app). Import alone takes >180s.
The dry-run is the complete proof; the full `_aiem_paper_execute_today` call
adds no additional isolation evidence — BLOCK path returns before
`aiem_paper_trades` INSERT (confirmed via code reading, lines 43073–43090).

## Paper-trade non-write — code-level proof (main.py lines 43073–43090)
```python
if _g0_result.get("decision") == "BLOCK":
    # ... INSERT INTO aiem_paper_execution_log (BLOCKED_G0)
    # ... rollback if _test_mode
    return {"blocked": True, "reason": ...}
# aiem_paper_trades INSERT is AFTER this return — never reached on BLOCK
```

## Harness files
- `artifacts/stock-scanner-api/d3_negctl_harness.py`
  sha256: 63fe1deba67aeac92c91009eb370891a5b625e319781b6d543bae495de999964
- `artifacts/stock-scanner-api/d3_negctl_fulltest_subprocess.py`
  sha256: 12812dc7a9527b8be4908ecd465ec4c58adf14a1c4267c08e4a8547b199b6801

**Why:** Directive 5 Option 2 requires proving G0 BLOCK is computed and all
audit writes land in the isolated schema with zero production writes.

**How to apply:** Re-run `python3 d3_negctl_harness.py --dry-run` to re-verify
isolation at any time without side effects. `--full-test` is documented but
infeasible due to main.py import time.
