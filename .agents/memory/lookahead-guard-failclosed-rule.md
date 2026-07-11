---
name: Lookahead guard fail-closed consistency
description: When adding provenance/date checks to stage3_lookahead_bias_check, every source must fail closed on missing data. Any pass-on-missing exception requires a concrete documented mechanism.
---

## Rule

When a new `elif source == "X"` branch is added to `stage3_lookahead_bias_check`, it must handle the missing-data case (no DB row, NULL result) with a `raise RuntimeError(ERROR_CODE=...)`, not a fall-through to `passed: True`.

## Why

A pick with `source='X'` claims to originate from table X. If that table has no row at check time, two states are indistinguishable:
- (a) data was valid when pick was generated, table since cleared
- (b) pick was generated from future-dated data, now gone

These are indistinguishable → provenance unknown → hard violation, same as `PRICE_PROVENANCE_UNKNOWN_DATE` in G6.

## How to apply

For any new source branch:
1. Query the relevant table/endpoint for the date column (MAX or per-ticker)
2. `if row is None or row[0] is None: raise RuntimeError("...ERROR_CODE=SOURCE_NAME_MISSING...")`
3. `if date > today: raise RuntimeError("...ERROR_CODE=SOURCE_NAME_FUTURE_DATE...")`
4. Falls through (→ `passed: True`) ONLY when date is verified ≤ today

## What triggered this

During review of the first pass, `multi_signal` was left with `if _ms_row is not None and _ms_row[0] is not None:` — the missing-row case silently fell through to pass. Reviewer caught the inconsistency with `conviction_stack`'s empty-table handling. Fix required a second pass.

## Decision labeling corollary

Do not label implementation choices with pre-approved decision numbers. If a numbered decision was a scope ruling (e.g. "fix data_snapshot.py post-loop gate"), the implementation inside that scope (e.g. 5% contamination threshold) is not "Decision N" — it is an implementation choice within the approved scope. If it needs sign-off, assign it a new number explicitly.
