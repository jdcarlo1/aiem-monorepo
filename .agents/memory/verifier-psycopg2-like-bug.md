---
name: verifier psycopg2 LIKE bug
description: psycopg2 interprets % in LIKE clauses as positional params when called with empty tuple
---

**Rule:** In psycopg2, `cur.execute(sql, ())` causes IndexError when sql contains `LIKE '%pattern%'`
because psycopg2 tries to format `%p` as positional argument 0 from the empty tuple.

**Why:** psycopg2 uses `%s` / `%(name)s` syntax for parameter substitution. Any `%` in the SQL
string is treated as a format specifier when params is provided (including empty tuple).
With `params=()`, `%p` → `IndexError: tuple index out of range`.

**How to apply:**
```python
def db(sql, params=None):
    conn = psycopg2.connect(_DB_URL)
    cur  = conn.cursor()
    if params:           # only pass params when truthy — NOT `params or ()`
        cur.execute(sql, params)
    else:
        cur.execute(sql)  # no params arg → no % expansion
    rows = cur.fetchall()
    conn.close()
    return rows
```

Alternative: escape literal `%` as `%%` in LIKE clauses:
```sql
WHERE table_name LIKE '%%log%%'   -- becomes LIKE '%log%' after psycopg2 formatting
```

**Discovered:** Phase 11 verifier, 2026-07-23. Symptom was IndexError on a table-scan query
with no placeholders being passed `params or ()` (which evaluates to empty tuple).
