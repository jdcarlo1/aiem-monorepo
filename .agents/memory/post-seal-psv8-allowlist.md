---
name: post_seal_verify PSV8 allowlist
description: PSV8 check design, allowlist patterns, SKIP/WARN behavior, and sha256 of the fixed file.
---

# PSV8 — PASS/FAIL totals in archive

## Rule
PSV8 only runs for commands that are test-suite runs (expected to produce `^SUMMARY:` line).
Non-test-suite commands (health checks, utilities) are SKIPPED, not failed.

**Why:** PSV8 previously failed unconditionally for any command not producing a `SUMMARY:` line — including `check_scheduler_drift.sh`. Design gap confirmed SEQ=88–120 era.

## Allowlist regex (line 275 of post_seal_verify.sh)
```
grep -qE '(verify_|/test_|negctl|/opp[0-9]|dpl/verify)'
```

- Matches: `verify_phase*.py`, `verify_*.sh`, `tools/test_*.py`, `negctl*.sh/py`, `opp040_verify.sh`, `dpl/verify_dpl_phase3.py`
- Does NOT match: `check_scheduler_drift.sh`, one-shot utility scripts

## Outcomes
- CMD matches allowlist + SUMMARY: present → **PASS**
- CMD matches allowlist + SUMMARY: absent → **FAIL** (script broke its own contract)
- CMD not on allowlist + no SUMMARY: in archive → **SKIP**
- CMD not on allowlist + SUMMARY: IS in archive → **WARN** `PSV8_allowlist_out_of_sync` (allowlist needs updating)

## Summary line format
```
POST-SEAL SUMMARY: X PASS  Y FAIL  Z SKIPPED  W WARN
```
All four counters always shown. SKIPPED and WARNED checks listed on separate lines.

## How to apply
When adding a new test-suite script that produces `SUMMARY:`:
- If its path contains `verify_`, `/test_`, `negctl`, `/opp[0-9]`, or `dpl/verify` → already covered, no change needed
- Otherwise → add its pattern to the regex on line 275, OR the WARN will fire on the first verified_run.sh execution and alert you

## sha256 of fixed file
`35e2aae12f0576a7cddab5f5b0431b09d1ff9cd7af4948d1f864cfba70fbefbe`  `artifacts/stock-scanner-api/tools/post_seal_verify.sh`

## Proven at SEQs
- SEQ=162 (`bash tools/check_scheduler_drift.sh`) → SKIP
- SEQ=163 (`bash tools/check_scheduler_drift.sh`) → SKIP  
- SEQ=161 (`bash /tmp/verify_item1_greeks_wiring.sh`) → PASS
- SEQ=9999 synthetic (non-allowlist CMD with SUMMARY: in archive) → WARN
