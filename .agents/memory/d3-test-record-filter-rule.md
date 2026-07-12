---
name: D3 governance table is_test_record filter rule
description: Standing rule — every production read on any of the six D3 governance tables must filter WHERE is_test_record = FALSE, with one documented exception for chain integrity.
---

## Rule
Every SELECT/aggregation/dashboard/health-check query against any of the six D3 governance tables must include `WHERE is_test_record = FALSE` (or `AND is_test_record = FALSE` when combined with other filters), UNLESS it falls into one of the two approved exemption categories below.

## The six tables
1. `d3_governance_requests`
2. `d3_governance_decisions`
3. `d3_governance_acks`
4. `d3_governance_event_links`
5. `d3_governance_actions`
6. (no d3_governance_ledger or d3_governance_policy tables exist as of this writing)

## Approved exemptions
1. **Verify/test scripts by design**: `aiem_diagram3_verification.py`, `aiem_diagram3_g3_verify.py`, `aiem_diagram3_g5_verify.py`, `aiem_diagram3_j_verify.py`, `aiem_diagram3_i_verify.py`, `aiem_diagram3_k_verify.py`. These scripts exist to verify test records and must read them.
2. **Chain-integrity predecessor hash lookup** (`aiem_diagram3_governance.py` line ~1071): `SELECT event_hash FROM d3_governance_event_links ORDER BY id DESC LIMIT 1`. This reads the last row's hash (regardless of is_test_record) to use as `previous_event_hash` for the next insert. Filtering it would create a verifiable hash-chain gap. This is the ONLY production read that must not be filtered.

## Approved admin opt-in
The `/stock-api/admin/d3/actions` endpoint accepts `?include_test=true` to show test rows alongside production rows. Default is is_test_record = FALSE.

## Why
Test governance rows (is_test_record=True) are permanently committed to these tables as an audit trail of governance system exercises. Without the filter, health-check COUNTs, aggregation queries, and manifest generators inflate production metrics with test data. Confirmed real impact: before fix, d3_governance_requests=8 / d3_governance_decisions=8 (5 were test); after fix these correctly return 3.

## How to apply
Before writing any new SELECT on any of the six tables: add `AND is_test_record = FALSE` to the WHERE clause. If there is no WHERE clause yet, add `WHERE is_test_record = FALSE`. Failure to include the filter is a protocol violation — raise it explicitly rather than silently fixing it.
