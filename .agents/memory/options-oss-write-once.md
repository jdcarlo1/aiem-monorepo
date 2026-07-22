---
name: OSS write-once guard
description: options_structure_scan ON CONFLICT changed to DO NOTHING — first write per (ticker, scan_date) is permanent
---

## Rule
`options_structure_scan` INSERT uses `ON CONFLICT (ticker, scan_date) DO NOTHING`.
First scheduler run of the day for a given ticker+date wins permanently.

**Why:** The `aiem_options_alert_snapshots` mechanism captures oss data at alert-creation time and uses it to compute `h1_polygon`. If the scheduler overwrites oss fields after alert creation but before snapshot capture (or if snapshot is missing), the stored h1 becomes unverifiable. Alerts 1-20 were permanently lost this way (oss overwritten, original values gone). Fixed 2026-07-22.

**How to apply:**
- If a bad first write needs correcting: explicit `DELETE FROM options_structure_scan WHERE ticker=X AND scan_date=Y` + re-insert. No automatic correction path.
- Do not add a DO UPDATE SET back without Joel's authorization.
- Any future INSERT into this table in a new module must use DO NOTHING, not DO UPDATE.
