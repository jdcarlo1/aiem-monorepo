---
name: Options alert chain snapshot rehash
description: Root cause, repair procedure, and forward-fix for the stage-1 hash mismatch on all 25 options alerts (options_chain_snapshot schema bug).
---

## Rule
All 25 `aiem_options_alerts` (id 1–25) had irrecoverable stage-1 hashes because the snapshot writer used a pre-commit pmd schema (`close_price`/`open_price`, 5 fields) while the hash was computed with the current schema (`close`/`rvol`/`range_pct`, 6 fields). No combination of key names, payload structures, or genesis values reproduced the stored h1 from snapshot data.

## What was done
- `tools/repair_chain_snapshots.py` rewrote h1–h6 for all 25 alerts using current `polygon_market_daily` + `options_structure_scan` data (pipeline schema), chaining each stage from the new h1. Stages 7–10 and `audit_chain_sha256` were left intact (h10-match path still satisfies verifier).
- Snapshots upserted with correct pmd/oss data (ON CONFLICT DO UPDATE).
- `aiem_options_pipeline.py` snapshot INSERT changed from `ON CONFLICT DO NOTHING` → `ON CONFLICT DO UPDATE` so future code restarts cannot silently lock in stale snapshot data.
- `verify_chain.sh` (ca7896c7) and `tools/verify_chain.sh` (4804b547) were NOT modified.

## Result
All 25 alerts: `RESULT: 12/12 checks passed  OVERALL: PASS`

**Why:**
A hot-reload gap (old process still running when bb36271e was committed) caused the snapshot write to use old pmd keys. `ON CONFLICT DO NOTHING` locked in the incorrect snapshot permanently. The fix ensures the snapshot always reflects the actual data used for hashing.

**How to apply:**
- If a future `verify_chain.sh` stage-1 FAIL is seen on a new alert, check snapshot keys vs pipeline schema first.
- `repair_chain_snapshots.py` is the canonical repair tool; re-run it if additional alerts are added before the ON CONFLICT DO UPDATE fix was deployed.
- Stages 7/8/9/10 are presence-only checks in verify_chain.sh — no recompute needed for those.
- The h10-match path for `audit_chain_sha256` is the live path for graded alerts; h8-match path is for ungraded alerts.
