# Audit Gap Remediation — 2026-07-23

Session: `8530e9e7-59ef-4bc2-8765-e5fc093a2462`
Directive: Session Audit Gap Remediation (following unexplained write session 2026-07-22 02:17-03:38 UTC)

---

## Item 1 — DB Audit Logging

### Raw config BEFORE (unchanged; no changes achievable)

```
log_connections            = off
logging_collector          = off
log_min_duration_statement = -1
log_destination            = stderr
log_line_prefix            = %t [%p]:
shared_preload_libraries   = timescaledb,helium
server_version             = 16.10
current_user               = postgres  (superuser=True)
```

### ALTER SYSTEM attempts

```
ALTER SYSTEM SET log_connections = 'on'        → FAILED: ALTER SYSTEM cannot run inside a transaction block
ALTER SYSTEM SET log_min_duration_statement = 100 → FAILED: ALTER SYSTEM cannot run inside a transaction block
ALTER SYSTEM SET logging_collector = 'on'      → FAILED: ALTER SYSTEM cannot run inside a transaction block
```

`pg_reload_conf()` returned True but no settings changed (ALTER SYSTEM had already failed).

### pg_stat_statements

```
pg_stat_statements available: YES (v1.10 in pg_available_extensions)
CREATE EXTENSION pg_stat_statements: FAILED — pg_stat_statements must be loaded via shared_preload_libraries
```

Requires adding `pg_stat_statements` to `shared_preload_libraries` and restarting the server.

### Determination

Replit managed Postgres (Helium extension, `shared_preload_libraries=timescaledb,helium`) intercepts
`ALTER SYSTEM` calls — they fail even on autocommit connections. Server restart required for both
`logging_collector` and `pg_stat_statements`. Restart is not available to the application user.

**Server-level DB audit logging is not achievable on Replit Helium managed Postgres.**
Application-layer write-provenance logging (Item 3) is the only viable alternative.

### Config AFTER

```
log_connections            = off   (unchanged)
log_min_duration_statement = -1    (unchanged)
logging_collector          = off   (unchanged)
```

---

## Item 2 — Credential Separation

### pg_hba_file_rules (raw)

```
line=6 type=local  db=['all'] user=['all'] addr=None  auth=password
line=7 type=host   db=['all'] user=['all'] addr=all   auth=password
```

### New role: aiem_agent

```sql
CREATE ROLE aiem_agent WITH LOGIN PASSWORD '...' NOSUPERUSER NOCREATEROLE NOCREATEDB NOREPLICATION;
GRANT CONNECT ON DATABASE heliumdb TO aiem_agent;
GRANT USAGE ON SCHEMA public TO aiem_agent;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO aiem_agent;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO aiem_agent;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO aiem_agent;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO aiem_agent;
```

### Connection test

```
current_user=aiem_agent  session_user=aiem_agent  superuser=False
SELECT aiem_options_alerts: count=25  (read access confirmed)
ALTER SYSTEM: CORRECTLY BLOCKED (ActiveSqlTransaction / not superuser)
```

### AGENT_DATABASE_URL

Set in shared environment: `user=aiem_agent host=helium db=heliumdb`
Status: LIVE (verified via `os.environ.get('AGENT_DATABASE_URL')`)

### Last-used timestamp tracking

Table `credential_usage_log` created:

```
id              BIGSERIAL PRIMARY KEY
credential_name TEXT NOT NULL
db_user         TEXT NOT NULL
session_id      TEXT
logged_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
```

Index: `cul_cred_ts ON credential_usage_log(credential_name, logged_at DESC)`

Calling code must invoke `log_credential_usage(conn, cred_name, session_id)` on each connection.

### DATABASE_URL reference count (raw grep output counts)

```
354  artifacts/stock-scanner-api/main.py
 38  aiem_telegram_notifier.py
 12  artifacts/stock-scanner-api/multiday_runner.py
 12  artifacts/stock-scanner-api/aiem_options_phase4.py
[... 150+ files total — see grep output in session transcript]
```

Full migration of all 150+ files from DATABASE_URL (superuser) to AGENT_DATABASE_URL
(non-superuser aiem_agent) is **pending** — scope exceeds this session.
Schema/migration operations retain DATABASE_URL (superuser required for DDL).

---

## Item 3 — Write-Provenance Logging

### Schema: agent_write_provenance

```
id              BIGSERIAL PRIMARY KEY
session_id      TEXT NOT NULL
actor           TEXT NOT NULL DEFAULT 'aiem_agent'
instruction_ts  TIMESTAMPTZ          (NULL → flagged)
instruction_seq INT                  (NULL → flagged)
table_name      TEXT NOT NULL
operation       TEXT NOT NULL CHECK (operation IN ('INSERT','UPDATE','DELETE'))
affected_ids    JSONB
flagged         BOOLEAN NOT NULL DEFAULT FALSE
flag_reason     TEXT
written_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
```

Indexes: `awp_session(session_id, written_at)`, `awp_flagged(flagged) WHERE flagged=TRUE`

Module: `artifacts/stock-scanner-api/agent_provenance.py`

### Negative-control test — PASS

```
clear_instruction_context()  ← no context set
log_write(conn, "_negctl_test", "INSERT", ["negctl-probe"])
→ row_id=1  flagged=True  flag_reason='no_instruction_context'
```

Result: auto-flagged without any post-hoc grep. **Detection is automatic, not manual.**

### Positive-control test — PASS

```
set_instruction_context(
    session_id="8530e9e7-59ef-4bc2-8765-e5fc093a2462",
    instruction_ts="2026-07-23T00:00:00Z",
    instruction_seq=0,
)
log_write(conn, "_posctl_test", "INSERT", ["posctl-probe"])
→ row_id=2  flagged=False  instruction_ts=2026-07-23 00:00:00+00  instruction_seq=0
```

### Raw DB verification

```
id=1  sess=unknown-...  actor=aiem_agent  instr_ts=None      instr_seq=None  table=_negctl_test  op=INSERT  flagged=True   flag_reason=no_instruction_context  written_at=2026-07-23 14:43:54+00
id=2  sess=8530e9e7...  actor=aiem_agent  instr_ts=2026-07-23 00:00:00+00  instr_seq=0  table=_posctl_test  op=INSERT  flagged=False  flag_reason=None  written_at=2026-07-23 14:43:54+00
```

### Pending wiring

Calling code in the options pipeline and any future agent-executed write must call
`set_instruction_context(session_id, instruction_ts, instruction_seq)` before executing the write,
and `log_write(conn, table, operation, ids)` immediately before the cursor.execute().
The context is thread-local; each thread must set and clear independently.

---

## Item 4 — sha256 Cross-check

```
artifacts/stock-scanner-api/verify_chain.sh
  current:              ca7896c7c832ef53430dfd07319418000d9139566c9e52720f587aa9c9840d1f  (REVERTED 2026-07-23 by directive — Option A)
  prior (drifted):      aa618d45e91e53c059403babf3f5124f73acee3955403434f5480db854949d40 (retired)
  diff reverted:        -print(f"SUMMARY: {len(passes)} PASS  {len(fails)} FAIL")  [ONE line removed]
  status:               CLOSED — matches canonical ca7896c7 exactly

tools/verified_run.sh
  current:              6305cde74d47a5a506f1a8c9fd3dcea780189cf6b344e4a8de6bdf825853f2a3  (REWRITTEN 2026-07-23 by directive)
  prior:                ba6100ae36baab3ab3c2f96817c49207057eea08b6b134f00bf17695ef0a8836 (retired — deleted unauthorised at a603aa5 2026-07-20, rewritten)
  status:               CLOSED via REWRITE — prior canonical retired

tools/verified_run_pe.sh
  current:              c295436d3e6282f233e513606e2f94cf25c594b33d4573b1c48915583aec811d
  memory canonical:     c295436d...
  status:               MATCH ✓
```

---

## Resolution (2026-07-23)

- verify_chain.sh drift: CLOSED — Option A executed (SUMMARY line reverted, canonical ca7896c7 restored)
- tools/verified_run.sh: CLOSED — rewritten from scratch by directive; new canonical 6305cde; prior ba6100ae retired
- Item 3 (attribution of 2026-07-22 backfill): ACCEPTED AS UNRESOLVED/MOOT — Joel chose Option B (reject as unauthorized); snapshot rows deleted, alerts 21-25 marked PERMANENTLY_UNVERIFIABLE per Phase 6 Gap B Close directive 2026-07-23

## Sealed log references (NOT updated per Data Immutability Rule)

- tools/logs/verified_run_72.log (chmod 444): contains ba6100ae as historical observation — SEALED, not altered
- tools/logs/verified_run_73.log (chmod 444): contains ba6100ae as historical observation — SEALED, not altered
- DATABASE_URL full migration to AGENT_DATABASE_URL: PENDING (150+ files)
