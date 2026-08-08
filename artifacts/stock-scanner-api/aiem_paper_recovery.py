"""
aiem_paper_recovery.py — Production-grade paper trade execution recovery system.

Protection inventory (maps to user requirements 1-10):
  1. APScheduler misfire   — misfire_grace_time=600s already set globally in job_defaults
  2. DB job ledger         — paper_trade_job_ledger; UNIQUE(business_date); one row per day
  3. Startup reconciliation— _paper_startup_reconciler thread in main.py (independent of startup_catchup)
  4. Internal watchdog     — start_internal_watchdog(); 2-min poll; activates after 9:44 AM ET
  5. External watchdog     — aiem_paper_watchdog.py; separate process; polls DB + calls admin HTTP
  6. Heartbeat monitoring  — paper_trade_watchdog_heartbeat table; written by both watchdogs
  7. Pre-run readiness     — mark_readiness() writes PRE_RUN_CHECK event before execution begins
  8. Post-run completion   — mark_completed/mark_failed + watchdog retry on FAILED status
  9. Atomic exactly-once   — try_claim() uses INSERT … ON CONFLICT DO NOTHING; cross-process safe
  10. Durable evidence     — .local/paper_trade_evidence.log (not /tmp); survives restarts

Tables:
  paper_trade_job_ledger         — authoritative state machine per business date
  paper_trade_watchdog_heartbeat — liveness proof for both internal and external watchdogs
"""

import os
import json
import datetime
import threading
import psycopg2
import pytz

_ET = pytz.timezone("America/New_York")
_DB_URL = os.getenv("DATABASE_URL", "")
_EVIDENCE_LOG = "/home/runner/workspace/.local/paper_trade_evidence.log"

CRASH_CLAIM_AGE_SEC = 600


def _db():
    return psycopg2.connect(_DB_URL, connect_timeout=5)


def _log_evidence(event: dict):
    """
    Append JSON event to .local/paper_trade_evidence.log.
    .local/ is persistent storage that survives VM restarts (Protection #10).
    """
    event.setdefault("ts", datetime.datetime.utcnow().isoformat() + "Z")
    event.setdefault("pid", os.getpid())
    try:
        os.makedirs(os.path.dirname(_EVIDENCE_LOG), exist_ok=True)
        with open(_EVIDENCE_LOG, "a") as f:
            f.write(json.dumps(event) + "\n")
    except Exception as e:
        print(f"[paper_recovery] evidence log write error (non-fatal): {e}")


def init_schema():
    """
    Create tables if they do not exist. Safe to call on every boot.
    Called from _DEFERRED_INITS so schema is always present before first execution.
    """
    with _db() as conn, conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS paper_trade_job_ledger (
                id                BIGSERIAL    PRIMARY KEY,
                business_date     DATE         NOT NULL UNIQUE,
                status            TEXT         NOT NULL DEFAULT 'PENDING',
                execution_id      TEXT         UNIQUE,
                trigger_source    TEXT,
                claimed_at        TIMESTAMPTZ,
                started_at        TIMESTAMPTZ,
                completed_at      TIMESTAMPTZ,
                picks_count       INTEGER,
                error_text        TEXT,
                heartbeat_at      TIMESTAMPTZ,
                watchdog_checks   INTEGER      DEFAULT 0,
                recovery_attempts INTEGER      DEFAULT 0,
                created_at        TIMESTAMPTZ  DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS paper_trade_watchdog_heartbeat (
                id            BIGSERIAL    PRIMARY KEY,
                process_type  TEXT         NOT NULL,
                execution_id  TEXT,
                last_alive    TIMESTAMPTZ  DEFAULT NOW(),
                pid           INTEGER,
                status        TEXT,
                note          TEXT
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_ptjl_business_date
            ON paper_trade_job_ledger (business_date)
        """)
        conn.commit()
    print("[paper_recovery] schema OK — paper_trade_job_ledger + paper_trade_watchdog_heartbeat")
    _log_evidence({"event": "SCHEMA_INIT", "pid": os.getpid()})


def mark_readiness(business_date: datetime.date, trigger_source: str):
    """
    Protection #7 — pre-run readiness verification.
    Called BEFORE the DB claim so there is evidence of intent even if the
    claim is denied (dedup proof) or DB is briefly unreachable.
    """
    can_db = False
    try:
        with _db() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            can_db = True
    except Exception:
        pass
    _log_evidence({
        "event": "PRE_RUN_CHECK",
        "business_date": str(business_date),
        "trigger_source": trigger_source,
        "db_reachable": can_db,
        "weekday": business_date.weekday(),
    })


def try_claim(business_date: datetime.date, execution_id: str,
               trigger_source: str) -> bool:
    """
    Protection #9 — atomic exactly-once execution across ALL callers.

    Returns True  — this caller now owns execution for business_date.
    Returns False — another process already claimed or completed it.

    Algorithm:
      Step 1: INSERT … ON CONFLICT DO NOTHING  → if rowcount=1, we own it.
      Step 2: If rowcount=0 and existing row is CLAIMED but claimed_at is
              older than CRASH_CLAIM_AGE_SEC → crash-after-claim recovery:
              steal the stale claim (old process crashed mid-execution).
      Step 3: Otherwise deny — row is active CLAIMED/EXECUTING/COMPLETED/SKIPPED.
    """
    date_str = str(business_date)
    with _db() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO paper_trade_job_ledger
                (business_date, status, execution_id, trigger_source, claimed_at)
            VALUES (%s, 'CLAIMED', %s, %s, NOW())
            ON CONFLICT (business_date) DO NOTHING
            RETURNING id
        """, (date_str, execution_id, trigger_source))
        row = cur.fetchone()
        if row:
            conn.commit()
            _log_evidence({
                "event": "LEDGER_CLAIMED",
                "via": "INSERT",
                "business_date": date_str,
                "execution_id": execution_id,
                "trigger_source": trigger_source,
            })
            print(f"[paper_recovery] CLAIMED {date_str} "
                  f"execution_id={execution_id} trigger={trigger_source}")
            return True

        # Step 2a-pre — claim a PENDING row (admin reset or first-ever insert)
        # PENDING is the initial state and the state an admin sets when manually
        # resetting a row.  The INSERT above will fail (ON CONFLICT DO NOTHING)
        # when the row already exists, so we need a separate UPDATE path.
        cur.execute("""
            UPDATE paper_trade_job_ledger
            SET status          = 'CLAIMED',
                execution_id    = %s,
                trigger_source  = %s,
                claimed_at      = NOW()
            WHERE business_date = %s
              AND status        = 'PENDING'
            RETURNING id
        """, (execution_id, trigger_source, date_str))
        row_pending = cur.fetchone()
        if row_pending:
            conn.commit()
            _log_evidence({
                "event": "LEDGER_CLAIMED",
                "via": "UPDATE_PENDING",
                "business_date": date_str,
                "execution_id": execution_id,
                "trigger_source": trigger_source,
            })
            print(f"[paper_recovery] CLAIMED (from PENDING) {date_str} "
                  f"execution_id={execution_id} trigger={trigger_source}")
            return True

        # Step 2a — steal stale CLAIMED row (crashed before execution started)
        cur.execute("""
            UPDATE paper_trade_job_ledger
            SET status          = 'CLAIMED',
                execution_id    = %s,
                trigger_source  = %s,
                claimed_at      = NOW(),
                recovery_attempts = recovery_attempts + 1
            WHERE business_date = %s
              AND status        = 'CLAIMED'
              AND claimed_at    < NOW() - (%s * INTERVAL '1 second')
            RETURNING id, recovery_attempts
        """, (execution_id, trigger_source, date_str, CRASH_CLAIM_AGE_SEC))
        row2 = cur.fetchone()
        if row2:
            conn.commit()
            _log_evidence({
                "event": "LEDGER_CRASH_RECOVERY",
                "via": "stale_CLAIMED",
                "business_date": date_str,
                "execution_id": execution_id,
                "trigger_source": trigger_source,
                "recovery_attempt": row2[1],
            })
            print(f"[paper_recovery] CRASH-RECOVERY (stale CLAIMED) "
                  f"for {date_str} attempt=#{row2[1]} trigger={trigger_source}")
            return True

        # Step 2b — steal stale EXECUTING row (crashed mid-execution, no heartbeat)
        # Was 5 minutes — too aggressive: scheduled_942 runs often take 10–15 min
        # (Aug 5 2026: lock held 13:42→13:55 while watchdog stole at 13:49 →
        # lock_contention_after_claim). Require 20 min with dead heartbeat.
        cur.execute("""
            UPDATE paper_trade_job_ledger
            SET status          = 'CLAIMED',
                execution_id    = %s,
                trigger_source  = %s,
                claimed_at      = NOW(),
                recovery_attempts = recovery_attempts + 1
            WHERE business_date = %s
              AND status        = 'EXECUTING'
              AND started_at    < NOW() - INTERVAL '20 minutes'
              AND (heartbeat_at IS NULL
                   OR heartbeat_at < NOW() - INTERVAL '15 minutes')
            RETURNING id, recovery_attempts
        """, (execution_id, trigger_source, date_str))
        row3 = cur.fetchone()
        if row3:
            conn.commit()
            _log_evidence({
                "event": "LEDGER_CRASH_RECOVERY",
                "via": "stale_EXECUTING",
                "business_date": date_str,
                "execution_id": execution_id,
                "trigger_source": trigger_source,
                "recovery_attempt": row3[1],
            })
            print(f"[paper_recovery] CRASH-RECOVERY (stale EXECUTING) "
                  f"for {date_str} attempt=#{row3[1]} trigger={trigger_source}")
            return True

        # Step 2d — Recovery triggers must NOT overwrite a terminal scheduled_942
        # row that already has real trades (picks_count > 0).
        #
        # Root cause (2026-07-27): after a VM restart, startup_recovery runs at
        # T+45s and finds PENDING (the cron hasn't fired yet).  It claims the row,
        # finds NO_CANDIDATES, and marks SKIPPED.  The 9:42 cron (Step 2c above)
        # then steals back the SKIPPED zero-picks row — correct.  But the inverse
        # also matters: if scheduled_942 already completed with real trades and the
        # VM is then restarted later in the day, startup_recovery must not steal the
        # completed row and re-execute.  That would produce duplicate trades and
        # corrupt the ledger's trigger_source attribution.
        #
        # Guard logic:
        #   • Only applies to recovery callers (startup_recovery, *_watchdog, admin).
        #   • Only blocks if the existing row was placed by scheduled_942 AND has
        #     a terminal status (COMPLETED or SKIPPED) AND picks_count > 0.
        #   • Does NOT block crash-recovery (Steps 2a/2b): a stale CLAIMED/EXECUTING
        #     row means the process crashed mid-run and recovery is legitimate.
        _RECOVERY_TRIGGERS = {"startup_recovery", "internal_watchdog",
                               "external_watchdog", "admin"}
        if trigger_source in _RECOVERY_TRIGGERS:
            cur.execute("""
                SELECT status, picks_count
                FROM paper_trade_job_ledger
                WHERE business_date  = %s
                  AND trigger_source = 'scheduled_942'
                  AND status         IN ('COMPLETED', 'SKIPPED')
                  AND picks_count    > 0
            """, (date_str,))
            guard_row = cur.fetchone()
            if guard_row:
                conn.commit()
                _log_evidence({
                    "event":          "LEDGER_CLAIM_DENIED_RECOVERY_GUARD",
                    "reason":         "scheduled_942_terminal_with_real_trades",
                    "business_date":  date_str,
                    "trigger_source": trigger_source,
                    "existing_status":   guard_row[0],
                    "existing_picks":    guard_row[1],
                })
                print(
                    f"[paper_recovery] claim DENIED — {trigger_source} blocked: "
                    f"scheduled_942 already has terminal status={guard_row[0]} "
                    f"picks={guard_row[1]} for {date_str}"
                )
                return False

        # Step 2c — override a COMPLETED/SKIPPED zero-picks row.
        # Root cause (2026-07-20): startup_recovery at 9:00 AM finds NO_CANDIDATES,
        # calls mark_skipped() → ledger status=SKIPPED, picks_count=NULL.  At 9:42
        # the scheduled run hits try_claim(), INSERT fails (UNIQUE conflict), all
        # UPDATE paths fail (row is not PENDING/CLAIMED/EXECUTING) → returns False →
        # the real scheduled run is silently skipped.
        #
        # Aug 7 2026 addendum: only scheduled_942 had this override.  When a
        # mid-morning Publish/restart missed 9:42 entirely, startup_recovery
        # could mark SKIPPED (NO_CANDIDATES before Loop B warmed) and then
        # internal_watchdog / startup_catchup treated SKIPPED as terminal —
        # day stayed at 0 picks forever.  Extend override to post-window
        # recovery triggers + scheduled_1015 retry, capped for non-cron callers.
        # Guard: picks_count > 0 means actual trades were executed — do NOT override.
        _ZERO_PICK_OVERRIDE_TRIGGERS = {
            "scheduled_942",
            "scheduled_1015",
            "startup_catchup",
            "startup_recovery",
            "internal_watchdog",
            "external_watchdog",
            "admin",
        }
        if trigger_source in _ZERO_PICK_OVERRIDE_TRIGGERS:
            _cron_override = trigger_source in ("scheduled_942", "scheduled_1015")
            cur.execute("""
                UPDATE paper_trade_job_ledger
                SET status          = 'CLAIMED',
                    execution_id    = %s,
                    trigger_source  = %s,
                    claimed_at      = NOW(),
                    recovery_attempts = COALESCE(recovery_attempts, 0) + 1
                WHERE business_date = %s
                  AND status        IN ('COMPLETED', 'SKIPPED')
                  AND (picks_count IS NULL OR picks_count = 0)
                  AND (
                    %s
                    OR COALESCE(recovery_attempts, 0) < 5
                  )
                RETURNING id, recovery_attempts
            """, (execution_id, trigger_source, date_str, _cron_override))
            row2c = cur.fetchone()
            if row2c:
                conn.commit()
                _log_evidence({
                    "event": "LEDGER_CLAIMED",
                    "via": "OVERRIDE_ZERO_PICKS",
                    "business_date": date_str,
                    "execution_id": execution_id,
                    "trigger_source": trigger_source,
                    "recovery_attempt": row2c[1],
                })
                print(f"[paper_recovery] CLAIMED (override zero-picks, {trigger_source}) "
                      f"{date_str} execution_id={execution_id}")
                return True

        # Step 2e — reclaim FAILED so watchdog / scheduled / admin can retry.
        # mark_failed() documents that try_claim must steal FAILED, but the UPDATE
        # path was missing — Aug 5 2026 stayed FAILED after "connection already
        # closed" and every later claim was denied. Cap recovery_attempts at 5.
        cur.execute("""
            UPDATE paper_trade_job_ledger
            SET status          = 'CLAIMED',
                execution_id    = %s,
                trigger_source  = %s,
                claimed_at      = NOW(),
                started_at      = NULL,
                completed_at    = NULL,
                error_text      = NULL,
                recovery_attempts = COALESCE(recovery_attempts, 0) + 1
            WHERE business_date = %s
              AND status        = 'FAILED'
              AND COALESCE(recovery_attempts, 0) < 5
              AND (picks_count IS NULL OR picks_count = 0)
            RETURNING id, recovery_attempts
        """, (execution_id, trigger_source, date_str))
        row_failed = cur.fetchone()
        if row_failed:
            conn.commit()
            _log_evidence({
                "event": "LEDGER_CLAIMED",
                "via": "RECLAIM_FAILED",
                "business_date": date_str,
                "execution_id": execution_id,
                "trigger_source": trigger_source,
                "recovery_attempt": row_failed[1],
            })
            print(f"[paper_recovery] CLAIMED (reclaim FAILED) {date_str} "
                  f"attempt=#{row_failed[1]} trigger={trigger_source}")
            return True

        cur.execute("""
            SELECT status, execution_id, trigger_source, claimed_at, completed_at
            FROM paper_trade_job_ledger WHERE business_date = %s
        """, (date_str,))
        existing = cur.fetchone()
        conn.commit()

    reason = existing[0] if existing else "UNKNOWN"
    owner  = existing[1] if existing else None
    _log_evidence({
        "event": "LEDGER_CLAIM_DENIED",
        "business_date": date_str,
        "our_execution_id": execution_id,
        "trigger_source": trigger_source,
        "existing_status": reason,
        "existing_owner": owner,
    })
    print(f"[paper_recovery] claim DENIED for {date_str}: "
          f"status={reason} owner={owner} caller={trigger_source}")
    return False


def mark_started(business_date: datetime.date, execution_id: str):
    """Transition CLAIMED → EXECUTING. Updates heartbeat to prove liveness."""
    date_str = str(business_date)
    with _db() as conn, conn.cursor() as cur:
        cur.execute("""
            UPDATE paper_trade_job_ledger
            SET status='EXECUTING', started_at=NOW(), heartbeat_at=NOW()
            WHERE business_date=%s AND execution_id=%s
        """, (date_str, execution_id))
        conn.commit()
    _log_evidence({
        "event": "LEDGER_EXECUTING",
        "business_date": date_str,
        "execution_id": execution_id,
    })


def mark_completed(business_date: datetime.date, execution_id: str,
                   picks_count: int):
    """Transition → COMPLETED. Protection #8 post-run completion proof."""
    date_str = str(business_date)
    with _db() as conn, conn.cursor() as cur:
        cur.execute("""
            UPDATE paper_trade_job_ledger
            SET status='COMPLETED', completed_at=NOW(),
                picks_count=%s, heartbeat_at=NOW()
            WHERE business_date=%s AND execution_id=%s
        """, (picks_count, date_str, execution_id))
        conn.commit()
    _log_evidence({
        "event": "LEDGER_COMPLETED",
        "business_date": date_str,
        "execution_id": execution_id,
        "picks_count": picks_count,
    })
    print(f"[paper_recovery] COMPLETED {date_str} "
          f"execution_id={execution_id} picks={picks_count}")


def mark_skipped(business_date: datetime.date, execution_id: str, reason: str):
    """
    Transition → SKIPPED (loss limit, governance block, no candidates).
    Terminal for picks_count>0 days.  Zero-pick SKIPPED may be reclaimed by
    Step 2c override triggers (scheduled_942/1015 + recovery callers).
    """
    date_str = str(business_date)
    with _db() as conn, conn.cursor() as cur:
        cur.execute("""
            UPDATE paper_trade_job_ledger
            SET status='SKIPPED', completed_at=NOW(),
                error_text=%s, heartbeat_at=NOW()
            WHERE business_date=%s AND execution_id=%s
        """, (reason, date_str, execution_id))
        conn.commit()
    _log_evidence({
        "event": "LEDGER_SKIPPED",
        "business_date": date_str,
        "execution_id": execution_id,
        "reason": reason,
    })
    print(f"[paper_recovery] SKIPPED {date_str} "
          f"execution_id={execution_id} reason={reason}")


def mark_failed(business_date: datetime.date, execution_id: str, error: str):
    """
    Transition → FAILED. Watchdog WILL retry on FAILED during market hours.
    Post-run recovery (Protection #8): next watchdog poll triggers re-execution
    via try_claim which will see FAILED status and attempt a fresh claim.
    """
    date_str = str(business_date)
    with _db() as conn, conn.cursor() as cur:
        cur.execute("""
            UPDATE paper_trade_job_ledger
            SET status='FAILED', completed_at=NOW(), error_text=%s
            WHERE business_date=%s AND execution_id=%s
        """, (error[:500], date_str, execution_id))
        conn.commit()
    _log_evidence({
        "event": "LEDGER_FAILED",
        "business_date": date_str,
        "execution_id": execution_id,
        "error": error[:300],
    })
    print(f"[paper_recovery] FAILED {date_str} "
          f"execution_id={execution_id}: {error[:100]}")


def get_today_status(business_date: datetime.date) -> dict:
    """Read current ledger state. Returns {'status': 'PENDING', 'exists': False} if no row."""
    date_str = str(business_date)
    with _db() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT status, execution_id, trigger_source, claimed_at,
                   started_at, completed_at, picks_count, recovery_attempts
            FROM paper_trade_job_ledger WHERE business_date=%s
        """, (date_str,))
        row = cur.fetchone()
    if not row:
        return {"status": "PENDING", "exists": False}
    return {
        "exists": True,
        "status": row[0],
        "execution_id": row[1],
        "trigger_source": row[2],
        "claimed_at": str(row[3]) if row[3] else None,
        "started_at": str(row[4]) if row[4] else None,
        "completed_at": str(row[5]) if row[5] else None,
        "picks_count": row[6],
        "recovery_attempts": row[7],
    }


def write_watchdog_heartbeat(process_type: str, pid: int = None,
                              status: str = "alive", note: str = None):
    """
    Protection #6 — heartbeat monitoring. Write liveness proof to DB.
    Non-fatal: a DB hiccup during heartbeat must never affect execution.
    """
    try:
        with _db() as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO paper_trade_watchdog_heartbeat
                    (process_type, last_alive, pid, status, note)
                VALUES (%s, NOW(), %s, %s, %s)
            """, (process_type, pid or os.getpid(), status, note))
            conn.commit()
    except Exception:
        pass


def beat(business_date: datetime.date, execution_id: str):
    """Update heartbeat_at on the ledger row while a long execution is in progress."""
    date_str = str(business_date)
    try:
        with _db() as conn, conn.cursor() as cur:
            cur.execute("""
                UPDATE paper_trade_job_ledger SET heartbeat_at=NOW()
                WHERE business_date=%s AND execution_id=%s
            """, (date_str, execution_id))
            conn.commit()
    except Exception:
        pass


def start_internal_watchdog(execute_fn, is_trading_day_fn, et_tz,
                            d14_verify_fn=None):
    """
    Protection #4 — internal watchdog (inside stock-api process).

    Wakes every 2 minutes. After 9:44 AM ET on a trading weekday, checks
    the DB ledger. If status is not terminal (COMPLETED or SKIPPED), calls
    execute_fn() which goes through try_claim() — exactly-once is preserved.

    After execute_fn() completes (recovery path), calls d14_verify_fn() if
    provided. d14_verify_fn checks D14_LAYER9_READ / D14_DEBATE_PRE /
    D14_DEBATE_POST + SHA-256 chain; on failure it auto-retries once and
    sends a Telegram alert.

    Also writes DB heartbeats for Protection #6.
    """
    import time

    def _loop():
        time.sleep(120)
        while True:
            try:
                now_et = datetime.datetime.now(et_tz)
                today  = now_et.date()
                h, m   = now_et.hour, now_et.minute

                write_watchdog_heartbeat("internal_watchdog")

                is_wday  = now_et.weekday() < 5
                past_944 = (h > 9) or (h == 9 and m >= 44)
                before_4 = h < 16

                if is_wday and past_944 and before_4 and is_trading_day_fn(today):
                    status_info = get_today_status(today)
                    _st = status_info.get("status")
                    _pc = status_info.get("picks_count")
                    # Non-terminal OR zero-pick terminal (Aug 7: late redeploy left
                    # SKIPPED/NO_CANDIDATES and old logic never retried).
                    _needs = (
                        _st not in {"COMPLETED", "SKIPPED"}
                        or (
                            _st in {"COMPLETED", "SKIPPED"}
                            and (_pc is None or int(_pc or 0) == 0)
                            and int(status_info.get("recovery_attempts") or 0) < 5
                        )
                    )
                    if _needs:
                        print(f"[paper_watchdog_internal] {today} "
                              f"status={_st} picks={_pc} at {h:02d}:{m:02d} ET "
                              f"— triggering internal_watchdog recovery")
                        _log_evidence({
                            "event": "INTERNAL_WATCHDOG_FIRED",
                            "business_date": str(today),
                            "status_before": _st,
                            "picks_count_before": _pc,
                            "time_et": f"{h:02d}:{m:02d}",
                        })
                        try:
                            execute_fn()
                        except Exception as exc:
                            print(f"[paper_watchdog_internal] recovery error: {exc}")
                        # D14 verification: fires after any recovery attempt
                        if d14_verify_fn:
                            try:
                                d14_verify_fn()
                            except Exception as d14_exc:
                                print(f"[paper_watchdog_internal] "
                                      f"D14 verify error (non-fatal): {d14_exc}")
            except Exception as outer_exc:
                print(f"[paper_watchdog_internal] loop error (non-fatal): {outer_exc}")

            time.sleep(120)

    t = threading.Thread(target=_loop, daemon=True, name="paper_internal_watchdog")
    t.start()
    print("[paper_recovery] internal watchdog started "
          "(2-min interval, activates after 9:44 AM ET on trading days)")
    _log_evidence({"event": "INTERNAL_WATCHDOG_STARTED"})
    return t
