---
name: Drizzle tablesFilter — deploy data-deletion danger
description: Every deployment was silently generating DROP TABLE migrations for all StockScanner/Python tables. Fix is one line in lib/db/drizzle.config.ts.
---

# Drizzle tablesFilter — Permanent Deploy Safety Fix

## Root Cause
Drizzle ORM (used by the NCLEX api-server) had no `tablesFilter` in `lib/db/drizzle.config.ts`. It manages a shared PostgreSQL database alongside ~100+ StockScanner tables created directly by Python via psycopg2. Without a filter, Drizzle compared ALL tables in the DB against its 4-table schema and generated `DROP TABLE` migrations for every table it didn't recognize.

Result: every `Publish` action would show conflict screens and, if the user clicked through all the prompts, permanently delete production StockScanner data (aiem_ml_predictions, aiem_predictions, aiem_health_log, aiem_research_hypotheses, etc.).

## Fix
Added one line to `lib/db/drizzle.config.ts`:
```ts
tablesFilter: ["questions", "answers", "sessions", "affiliates"],
```

Drizzle now only sees and manages its own 4 tables. Every other table in the database is permanently invisible to the migration system.

**Why:** Drizzle's `tablesFilter` is a whitelist — only the named tables are included in schema comparisons and migration generation. Tables not in the list are never touched regardless of what's in the DB.

**How to apply:** If new NCLEX/api-server tables are added via Drizzle schema, add their names to this array. Never remove existing entries. StockScanner tables (created by Python) should never be added here.

## Drizzle-owned tables (as of July 2026)
- `questions`
- `answers`
- `sessions`
- `affiliates`
