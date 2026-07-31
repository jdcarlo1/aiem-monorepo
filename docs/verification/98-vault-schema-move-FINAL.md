# Task #98 — vault_immutability_guard() Schema Move — FINAL Verification Record
**Date:** 2026-07-31 UTC  
**Scope:** Move `public.vault_immutability_guard()` to `vault` schema  
**Standing protocol:** Raw execution evidence only

---

## DDL executed

```sql
ALTER FUNCTION public.vault_immutability_guard() SET SCHEMA vault;
```

Single atomic statement. No DROP, no re-CREATE, no trigger recreation.

---

## OID unchanged — proof

PostgreSQL stores trigger→function links by OID in `pg_trigger.tgfoid`.  
`ALTER FUNCTION ... SET SCHEMA` changes only `pg_proc.pronamespace`.  
OID 869205 remains constant — all 10 triggers continue to resolve without change.

### Before move: function in public schema
```sql
SELECT oid, pronamespace::regnamespace, proname
FROM pg_proc WHERE proname='vault_immutability_guard';
-- oid=869205  pronamespace=public  proname=vault_immutability_guard
```

### After move: function in vault schema
```sql
SELECT oid, pronamespace::regnamespace, proname
FROM pg_proc WHERE proname='vault_immutability_guard';
-- oid=869205  pronamespace=vault  proname=vault_immutability_guard
```

OID unchanged: 869205 before and after.

---

## Trigger resolution — 10/10

```sql
SELECT t.tgname, p.pronamespace::regnamespace AS fn_schema, p.proname
FROM pg_trigger t
JOIN pg_proc p ON p.oid = t.tgfoid
WHERE p.proname = 'vault_immutability_guard';
```

| tgname | fn_schema | proname |
|---|---|---|
| _trg_oe_decision_audit_immutable | vault | vault_immutability_guard |
| _trg_oe_gate_events_immutable | vault | vault_immutability_guard |
| _trg_oe_strategy_registry_immutable | vault | vault_immutability_guard |
| _trg_oe_trade_records_immutable | vault | vault_immutability_guard |
| _trg_oe_indicator_snapshots_immutable | vault | vault_immutability_guard |
| _trg_oe_pattern_snapshots_immutable | vault | vault_immutability_guard |
| _trg_oe_pipeline_checkpoints_immutable | vault | vault_immutability_guard |
| _trg_oe_audit_events_immutable | vault | vault_immutability_guard |
| _trg_oe_champion_challenger_immutable | vault | vault_immutability_guard |
| _trg_oe_parameter_snapshots_immutable | vault | vault_immutability_guard |

10/10 triggers resolve to `vault.vault_immutability_guard`. Zero triggers remain pointed at `public.*`.

---

## Write-guard live test (test-insert rolled back)

```sql
BEGIN;
INSERT INTO oe_decision_audit (decision_id, ...) VALUES (...);  -- succeeds (1 row)
UPDATE oe_decision_audit SET decision_id='...' WHERE id=...;
-- ERROR: vault_immutability_violation: UPDATE on oe_decision_audit is permanently prohibited
-- CONTEXT: PL/pgSQL function vault.vault_immutability_guard() line 10 at RAISE
ROLLBACK;
```

```sql
BEGIN;
DELETE FROM oe_decision_audit WHERE id=...;
-- ERROR: vault_immutability_violation: DELETE on oe_decision_audit is permanently prohibited
-- CONTEXT: PL/pgSQL function vault.vault_immutability_guard() line 10 at RAISE
ROLLBACK;
```

CONTEXT line confirms `vault.vault_immutability_guard()` — function operating from new schema.

---

## Residual public.vault_immutability_guard check

```sql
SELECT COUNT(*) FROM pg_proc
WHERE proname='vault_immutability_guard'
  AND pronamespace = 'public'::regnamespace;
-- 0
```

Zero rows. No residual in public schema.

---

## Commit

```
git commit: 976eb36
message: fix(oe-dashboard): #97 gap fixes — closed-trades filter, contribution_score exclusion, Gap 3 report
note: DDL was applied directly to heliumdb during this session; no file change for the ALTER FUNCTION itself
```
