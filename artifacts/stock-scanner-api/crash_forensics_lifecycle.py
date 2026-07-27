#!/usr/bin/env python3
"""
Crash Forensics — process lifecycle logger (Gap 1).

Called by the wrapper shell scripts around each long-running process.
Uses a fresh psycopg2.connect() — NEVER the app's connection pool.
If the DB is the crash cause, the pool is unavailable; a direct connect
with a 5-second timeout bails fast and falls back to /tmp JSON.

Usage (from wrapper scripts only):
  python3 crash_forensics_lifecycle.py start <process_name>
      → INSERTs a process_lifecycle_log row; writes row ID to
        /tmp/<process_name>_lifecycle_id for the exit call to UPDATE.

  python3 crash_forensics_lifecycle.py exit <process_name> <exit_code>
      → UPDATEs the row with exit_code + exit_reason.
        Always writes /tmp/<process_name>_last_exit.json as local fallback
        (survives even total DB failure).

Exit code → exit_reason mapping:
    0   clean_exit       (scheduled nightly reset, or intentional os._exit(0))
    1   exception        (unhandled Python exception → default exit code)
    137 SIGKILL_OOM      (kernel OOM killer: only reliable post-hoc OOM signal)
    143 SIGTERM          (graceful shutdown signal)
    128+N signal_N       (other signal)
"""

import os
import sys
import json
import datetime


# ── Helpers ───────────────────────────────────────────────────────────────────

def _db_url() -> str:
    return os.environ.get("DATABASE_URL", "")


def _reason(code: int) -> str:
    if code == 0:    return "clean_exit"
    if code == 1:    return "exception"
    if code == 137:  return "SIGKILL_OOM"      # kernel OOM killer
    if code == 143:  return "SIGTERM"
    if code > 128:   return f"signal_{code - 128}"
    return f"exit_{code}"


def _git_sha() -> str:
    try:
        import subprocess
        r = subprocess.run(
            ["git", "-C", os.path.dirname(os.path.abspath(__file__)),
             "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=3,
        )
        return r.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _ensure_table(cur) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS process_lifecycle_log (
            id           BIGSERIAL    PRIMARY KEY,
            process_name TEXT         NOT NULL,
            pid          INT,
            git_sha      TEXT,
            started_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            exited_at    TIMESTAMPTZ,
            exit_code    INT,
            exit_reason  TEXT
        )
    """)


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_start(process_name: str) -> None:
    """Insert a start row; save its ID to /tmp for the exit call."""
    url  = _db_url()
    pid  = os.getpid()     # PID of the wrapper script (parent of python)
    sha  = _git_sha()
    now  = datetime.datetime.utcnow()
    id_file = f"/tmp/{process_name}_lifecycle_id"

    if not url:
        print(f"[lifecycle] start: DATABASE_URL not set — skipping DB write", flush=True)
        return

    try:
        import psycopg2
        with psycopg2.connect(url, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                _ensure_table(cur)
                cur.execute(
                    """
                    INSERT INTO process_lifecycle_log
                        (process_name, pid, git_sha, started_at)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id
                    """,
                    (process_name, pid, sha, now),
                )
                row_id = cur.fetchone()[0]
            conn.commit()
        with open(id_file, "w") as f:
            f.write(str(row_id))
        print(
            f"[lifecycle] start: process={process_name} pid={pid} "
            f"sha={sha} row_id={row_id}",
            flush=True,
        )
    except Exception as e:
        print(f"[lifecycle] start DB write failed (non-fatal): {e}", flush=True)


def cmd_exit(process_name: str, exit_code: int) -> None:
    """Update the start row with exit info; always write /tmp fallback first."""
    url     = _db_url()
    reason  = _reason(exit_code)
    now     = datetime.datetime.utcnow()
    id_file = f"/tmp/{process_name}_lifecycle_id"
    json_file = f"/tmp/{process_name}_last_exit.json"

    # ── Local fallback first — survives DB failure completely ─────────────────
    try:
        with open(json_file, "w") as f:
            json.dump(
                {
                    "process":     process_name,
                    "exit_code":   exit_code,
                    "exit_reason": reason,
                    "exited_at":   now.isoformat() + "Z",
                },
                f,
            )
    except Exception:
        pass

    # ── DB write ──────────────────────────────────────────────────────────────
    if not url:
        print(f"[lifecycle] exit: DATABASE_URL not set — /tmp fallback written", flush=True)
        return

    row_id = None
    try:
        with open(id_file) as f:
            row_id = int(f.read().strip())
    except Exception:
        pass

    try:
        import psycopg2
        with psycopg2.connect(url, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                _ensure_table(cur)
                if row_id:
                    cur.execute(
                        """
                        UPDATE process_lifecycle_log
                        SET    exited_at   = %s,
                               exit_code  = %s,
                               exit_reason = %s
                        WHERE  id = %s
                        """,
                        (now, exit_code, reason, row_id),
                    )
                else:
                    # start row was never written — insert a minimal exit-only record
                    cur.execute(
                        """
                        INSERT INTO process_lifecycle_log
                            (process_name, git_sha, started_at,
                             exited_at, exit_code, exit_reason)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (process_name, _git_sha(), now, now, exit_code, reason),
                    )
            conn.commit()
        print(
            f"[lifecycle] exit: process={process_name} "
            f"code={exit_code} reason={reason}",
            flush=True,
        )
    except Exception as e:
        print(
            f"[lifecycle] exit DB write failed "
            f"(fallback written to {json_file}): {e}",
            flush=True,
        )


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(
            f"Usage: {sys.argv[0]} start <process_name>\n"
            f"       {sys.argv[0]} exit  <process_name> <exit_code>",
            file=sys.stderr,
        )
        sys.exit(1)

    verb         = sys.argv[1]
    process_name = sys.argv[2]

    if verb == "start":
        cmd_start(process_name)
    elif verb == "exit":
        if len(sys.argv) < 4:
            print("exit verb requires <exit_code> argument", file=sys.stderr)
            sys.exit(1)
        cmd_exit(process_name, int(sys.argv[3]))
    else:
        print(f"Unknown verb: {verb!r}. Expected start or exit.", file=sys.stderr)
        sys.exit(1)
