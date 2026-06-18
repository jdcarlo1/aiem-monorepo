---
name: Timezone "blank tabs" rule (StockScanner)
description: Why prod tabs blanked after 8pm ET and the hard rules for date filtering in the stock-scanner-api SQL/Python
---

# Evening blank-tab timezone bug — durable rules

**Root cause:** the prod server clock AND the Postgres session timezone are **GMT/UTC**.
So SQL `CURRENT_DATE` / `now()::date` and Python `date.today()` all return the **UTC**
calendar date, which rolls to "tomorrow" at **8 PM ET** (00:00 UTC). Any "today" filter
built on those went empty every evening → blank tabs. The US market day is **ET**.

**Why the obvious global fix is WRONG (do not do it):** there are TWO SQL conventions in
`artifacts/stock-scanner-api/main.py`:
- **(a) buggy** raw `CURRENT_DATE` / `now()::date` filters — these are what blank out.
- **(b) already-correct** boundary filters that end in `... AT TIME ZONE 'America/New_York') AT TIME ZONE 'UTC'`.
  Convention (b) produces a **naive** timestamp that is then compared to a `timestamptz`
  column; Postgres recasts the naive operand using the **session tz (GMT)**, which makes (b)
  correct **only while the session stays GMT**.
- Therefore **NEVER** set the session timezone to ET globally (no `ALTER ROLE`, no psycopg2
  `options="-c timezone=..."`, no monkeypatch). It would fix (a) but silently BREAK every (b)
  query. Keep the session GMT and fix convention-(a) sites surgically instead.

**Canonical session-independent ET expressions to use (verified on the GMT DB):**
- DATE columns (scan_date, snap_date, alert_date, signal_date, sweep_date, etc.):
  `<col> = (now() AT TIME ZONE 'America/New_York')::date`; ranges use `(now() AT TIME ZONE 'America/New_York')::date - N` (date − int = date).
- TIMESTAMPTZ columns (first_seen/last_seen/created_at/saved_at are TIMESTAMPTZ):
  `(<col> AT TIME ZONE 'America/New_York')::date = (now() AT TIME ZONE 'America/New_York')::date`.
  This correctly attributes 8pm–midnight ET events (stored as next-day UTC) to the ET day.
- ET-midnight as a real `timestamptz` instant (for `last_seen >= {boundary}`):
  `(now() AT TIME ZONE 'America/New_York')::date::timestamp AT TIME ZONE 'America/New_York'`.
- Python: use the `_et_today()` / `_et_today_iso()` helpers, never `date.today()` / `datetime.now().date()`.

**Leave alone:** convention-(b) ET-boundary queries; `DEFAULT CURRENT_DATE` in DDL (those
tables are written intraday when UTC date == ET date, so stored keys are correct);
display/projection `AT TIME ZONE 'UTC' AS ...` casts; `EXTRACT(HOUR ... 'UTC')` market-hour windows.

**Residual edge case (not fixed, low risk):** alert-log tables whose INSERT omits the date
column rely on `DEFAULT CURRENT_DATE`; a *manual/forced* scan run after 8pm ET would store
tomorrow's UTC date. Real writers only run during market hours. Hardening = pass explicit
`_et_today()` on insert or make the column DEFAULT ET-aware.

**Deploy:** SQL/Python fixes only take effect after the owner **republishes**. The prod
`executeSql` connection is a **read-only replica** — cannot ALTER prod from here.
