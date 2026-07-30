"""
aiem_options_scheduler.py — Standalone 24/7 Options Pipeline Scheduler

Runs as its own Replit workflow (separate process from stock-api/main.py).
This separation means:
  A. stock-api failure does NOT kill the scheduler — it is truly external
  B. On VM reboot Replit restarts both workflows independently
  C. The DB job queue bridges failures — jobs survive any process crash

Architecture
────────────
  DB table: options_pipeline_jobs  (UNIQUE ticker+scan_date = idempotency)
  State machine: PENDING → CLAIMED → EXECUTING → DONE | FAILED
  Stale recovery:
    CLAIMED  > 5 min  → reset to PENDING  (crash after claim)
    EXECUTING > 10 min → reset to PENDING  (crash mid-execution)
  Max 3 recovery attempts before FAILED.
  Missed-schedule backfill: on startup, look for PENDING rows from last 24 h.
  Telegram alerts: seeding, failure, recovery, completion, stuck jobs.
  Heartbeat: writes to job_heartbeats every 5 min so notifier can watch it.
  Health endpoint: GET /health → JSON (port 5053).

Schedule (ET, Mon-Fri)
  09:40 — seed daily candidates (top bearish setups from options_structure_scan)
  09:45 — execute pipeline for each seeded job
  16:46 — grade expired alerts (Stage 9 / Stage 10 — learning loop)
  00:05 — clean up jobs older than 30 days
"""

import os
import sys
import json
import time
import uuid
import hashlib
import logging
import threading
import subprocess
import urllib.request
import urllib.parse
from datetime import datetime, date, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler

import psycopg2
import psycopg2.extras
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
import pytz

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

_DB_URL       = os.environ["DATABASE_URL"]
_ET           = pytz.timezone("America/New_York")
_HEALTH_PORT  = int(os.environ.get("OPTIONS_SCHEDULER_PORT", "5053"))
_STALE_CLAIM_SECS    = 300    # 5 min  → CLAIMED  too old
_STALE_EXEC_SECS     = 600    # 10 min → EXECUTING too old
_MAX_RECOVERY_TRIES  = 3
_HEARTBEAT_JOB_NAME  = "options_pipeline_scheduler"

# ─────────────────────────────────────────────────────────────────────────────
# PIPELINE STAGE CHECKPOINTS
# ─────────────────────────────────────────────────────────────────────────────
try:
    import aiem_pipeline_checkpoints as _chkp
    _chkp.ensure_tables(_DB_URL)
except Exception as _chkp_init_e:
    import logging as _chkp_log
    _chkp_log.getLogger(__name__).warning(
        f"[scheduler] checkpoint module init failed: {_chkp_init_e}")
    _chkp = None
_SCHEDULER_NAME      = "aiem_options_scheduler"

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s %(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
    handlers=[logging.StreamHandler(sys.stdout)],
    force=True,
)
sys.stdout.reconfigure(line_buffering=True)
log = logging.getLogger(_SCHEDULER_NAME)

# ─────────────────────────────────────────────────────────────────────────────
# BOOT IDENTITY — captured once at process start, never re-read at runtime.
# Used by the drift check (Step 2) and Telegram alert (Step 3) to detect when
# a deploy happened after this process started.
# ─────────────────────────────────────────────────────────────────────────────
try:
    _BOOT_COMMIT = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=os.path.dirname(os.path.abspath(__file__)),
        stderr=subprocess.DEVNULL,
        text=True,
    ).strip()
except Exception:
    _BOOT_COMMIT = "UNKNOWN"

_BOOT_PID  = os.getpid()
_BOOT_TIME = datetime.utcnow()

log.info(
    f"[boot] pid={_BOOT_PID}  commit={_BOOT_COMMIT}  "
    f"boot_utc={_BOOT_TIME.strftime('%Y-%m-%dT%H:%M:%SZ')}"
)

# ─────────────────────────────────────────────────────────────────────────────
# TELEGRAM
# ─────────────────────────────────────────────────────────────────────────────

def _tg(text: str) -> bool:
    token   = "".join(os.environ.get("TELEGRAM_BOT_TOKEN", "").split())
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        log.warning("[telegram] token/chat_id not configured")
        return False
    try:
        payload = json.dumps({"chat_id": chat_id, "text": text,
                              "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status == 200
    except Exception as e:
        log.warning(f"[telegram] send failed: {e}")
        return False

# ─────────────────────────────────────────────────────────────────────────────
# DB BOOTSTRAP
# ─────────────────────────────────────────────────────────────────────────────

_DB_BOOTSTRAPPED = False

def _bootstrap_db() -> None:
    global _DB_BOOTSTRAPPED
    if _DB_BOOTSTRAPPED:
        return
    last_exc = None
    for attempt in range(1, 4):
        try:
            with psycopg2.connect(_DB_URL, connect_timeout=15) as conn, conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS options_pipeline_jobs (
                        id                  BIGSERIAL PRIMARY KEY,
                        ticker              VARCHAR(20)  NOT NULL,
                        scan_date           DATE         NOT NULL,
                        status              VARCHAR(20)  NOT NULL DEFAULT 'PENDING',
                        claim_id            VARCHAR(48),
                        trace_id            VARCHAR(48),
                        alert_id            INTEGER,
                        direction           VARCHAR(12),
                        selected_score      NUMERIC(5,1),
                        trigger_source      VARCHAR(48)  DEFAULT 'scheduler',
                        error_text          TEXT,
                        recovery_attempts   INTEGER      DEFAULT 0,
                        created_at          TIMESTAMPTZ  DEFAULT NOW(),
                        claimed_at          TIMESTAMPTZ,
                        executing_at        TIMESTAMPTZ,
                        completed_at        TIMESTAMPTZ,
                        heartbeat_at        TIMESTAMPTZ,
                        chain_hash          VARCHAR(64),
                        UNIQUE(ticker, scan_date)
                    )
                """)
                cur.execute("""
                    ALTER TABLE options_pipeline_jobs
                        ADD COLUMN IF NOT EXISTS chain_hash VARCHAR(64)
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS options_engine_premarket (
                        id                   BIGSERIAL PRIMARY KEY,
                        ticker               VARCHAR(20)  NOT NULL,
                        run_date             DATE         NOT NULL,
                        premarket_gap        NUMERIC(8,4),
                        premarket_high       NUMERIC(12,4),
                        premarket_low        NUMERIC(12,4),
                        premarket_volume     BIGINT,
                        pm_rvol              NUMERIC(8,4),
                        pm_trend_quality     NUMERIC(6,4),
                        premarket_score      NUMERIC(6,4),
                        premarket_direction  VARCHAR(12),
                        premarket_confidence NUMERIC(6,4),
                        risk_flags_json      JSONB,
                        raw_data_json        JSONB,
                        intraday_updated_at  TIMESTAMPTZ,
                        pm_high_broken       BOOLEAN,
                        pm_low_held          BOOLEAN,
                        created_at           TIMESTAMPTZ DEFAULT NOW(),
                        UNIQUE(ticker, run_date)
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS options_engine_mtf (
                        id                   BIGSERIAL PRIMARY KEY,
                        ticker               VARCHAR(20)  NOT NULL,
                        run_date             DATE         NOT NULL,
                        alignment_score      NUMERIC(6,4),
                        conflict_score       NUMERIC(6,4),
                        dominant_bias        VARCHAR(12),
                        entry_timing_status  VARCHAR(20),
                        timeframes_json      JSONB,
                        created_at           TIMESTAMPTZ DEFAULT NOW(),
                        UNIQUE(ticker, run_date)
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS options_engine_runs (
                        id                   BIGSERIAL PRIMARY KEY,
                        run_id               VARCHAR(64)  NOT NULL UNIQUE,
                        trace_id             VARCHAR(48),
                        ticker               VARCHAR(20)  NOT NULL,
                        run_date             DATE         NOT NULL,
                        stocks_scanned       INTEGER      DEFAULT 0,
                        contracts_evaluated  INTEGER      DEFAULT 0,
                        selected_ticker      VARCHAR(20),
                        selected_strategy    VARCHAR(64),
                        decision             VARCHAR(20),
                        premarket_score      NUMERIC(6,4),
                        mtf_alignment_score  NUMERIC(6,4),
                        pattern_score        NUMERIC(6,4),
                        final_ccs            NUMERIC(8,4),
                        trigger_chain_json   JSONB,
                        created_at           TIMESTAMPTZ DEFAULT NOW()
                    )
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_opj_status_date
                        ON options_pipeline_jobs(status, scan_date)
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS job_heartbeats (
                        job_name            VARCHAR(100) PRIMARY KEY,
                        last_success        TIMESTAMP,
                        last_attempt        TIMESTAMP,
                        last_error          TEXT,
                        consecutive_failures INTEGER DEFAULT 0
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS aiem_execution_assessments (
                        id                        BIGSERIAL PRIMARY KEY,
                        candidate_id              VARCHAR(64) NOT NULL UNIQUE,
                        trace_id                  VARCHAR(48),
                        strategy_id               VARCHAR(64),
                        ticker                    VARCHAR(20)  NOT NULL,
                        scan_date                 DATE         NOT NULL,
                        strategy_name             VARCHAR(64),
                        n_legs                    INTEGER,
                        bid                       NUMERIC(10,4),
                        ask                       NUMERIC(10,4),
                        mid                       NUMERIC(10,4),
                        spread_pct                NUMERIC(8,4),
                        volume                    INTEGER,
                        open_interest             INTEGER,
                        bid_size                  INTEGER,
                        ask_size                  INTEGER,
                        iv                        NUMERIC(8,4),
                        dte                       INTEGER,
                        fill_probability          NUMERIC(6,4),
                        mid_fill_probability      NUMERIC(6,4),
                        expected_entry_price      NUMERIC(10,4),
                        conservative_entry_price  NUMERIC(10,4),
                        expected_slippage_pct     NUMERIC(8,4),
                        expected_slippage_dollars NUMERIC(10,4),
                        spread_cost_dollars       NUMERIC(10,4),
                        commission_dollars        NUMERIC(10,4),
                        market_impact_dollars     NUMERIC(10,4),
                        total_transaction_cost    NUMERIC(10,4),
                        legging_risk_score        NUMERIC(6,4),
                        exit_liquidity_score      NUMERIC(6,4),
                        early_assignment_risk     VARCHAR(10),
                        pin_risk_flag             BOOLEAN DEFAULT FALSE,
                        liquidity_score           NUMERIC(6,4),
                        gross_expected_edge       NUMERIC(10,4),
                        net_expected_edge         NUMERIC(10,4),
                        execution_uncertainty     NUMERIC(8,4),
                        execution_score           NUMERIC(6,4),
                        approved                  BOOLEAN NOT NULL,
                        rejection_reason          VARCHAR(200),
                        position_size_factor      NUMERIC(6,4),
                        actual_fill_price         NUMERIC(10,4),
                        actual_slippage           NUMERIC(10,4),
                        actual_transaction_cost   NUMERIC(10,4),
                        fill_prob_error           NUMERIC(8,4),
                        entry_price_error         NUMERIC(10,4),
                        slippage_error            NUMERIC(10,4),
                        cost_error                NUMERIC(10,4),
                        config_sha256             VARCHAR(64),
                        raw_assessment_json       JSONB,
                        gating_enabled            BOOLEAN DEFAULT FALSE,
                        created_at                TIMESTAMPTZ DEFAULT NOW(),
                        updated_at                TIMESTAMPTZ DEFAULT NOW()
                    )
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_ei_ticker_date
                        ON aiem_execution_assessments(ticker, scan_date)
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_ei_trace_id
                        ON aiem_execution_assessments(trace_id)
                """)
                conn.commit()
            _DB_BOOTSTRAPPED = True
            log.info("[bootstrap] options_pipeline_jobs, job_heartbeats, and aiem_execution_assessments ready")
            # Phase III Phase 1 — bootstrap registry tables (idempotent, non-fatal)
            try:
                import aiem_options_registries as _reg_boot
                _reg_boot.bootstrap_registries(_DB_URL)
                log.info("[bootstrap] oe_registries (Phase III Phase 1) tables ready")
            except Exception as _rb_e:
                log.warning(f"[bootstrap] oe_registries bootstrap skipped: {_rb_e}")
            return
        except Exception as e:
            last_exc = e
            log.warning(f"[bootstrap] attempt {attempt}/3 failed: {e} — retrying in 5s")
            if attempt < 3:
                time.sleep(5)
    log.error(f"[bootstrap] all 3 attempts FAILED: {last_exc}")
    raise last_exc

# ─────────────────────────────────────────────────────────────────────────────
# HEARTBEAT
# ─────────────────────────────────────────────────────────────────────────────

def _write_heartbeat(success: bool, error: str = None) -> None:
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=4) as conn, conn.cursor() as cur:
            if success:
                cur.execute("""
                    INSERT INTO job_heartbeats (job_name, last_success, last_attempt, consecutive_failures)
                    VALUES (%s, NOW(), NOW(), 0)
                    ON CONFLICT (job_name) DO UPDATE
                    SET last_success=NOW(), last_attempt=NOW(), consecutive_failures=0
                """, (_HEARTBEAT_JOB_NAME,))
            else:
                cur.execute("""
                    INSERT INTO job_heartbeats (job_name, last_attempt, last_error, consecutive_failures)
                    VALUES (%s, NOW(), %s, 1)
                    ON CONFLICT (job_name) DO UPDATE
                    SET last_attempt=NOW(), last_error=%s,
                        consecutive_failures=job_heartbeats.consecutive_failures + 1
                """, (_HEARTBEAT_JOB_NAME, error or "unknown", error or "unknown"))
            conn.commit()
    except Exception as e:
        log.warning(f"[heartbeat] write failed: {e}")

# ─────────────────────────────────────────────────────────────────────────────
# CHAIN HASH — Merkle-style tamper-evident log per completed job
# ─────────────────────────────────────────────────────────────────────────────

def _get_prev_chain_hash(conn) -> str:
    """Return chain_hash of the most recent DONE job (Merkle prev_hash)."""
    try:
        with conn.cursor() as _cur:
            _cur.execute("""
                SELECT chain_hash FROM options_pipeline_jobs
                WHERE chain_hash IS NOT NULL
                ORDER BY id DESC LIMIT 1
            """)
            _row = _cur.fetchone()
            return _row[0] if _row else "genesis"
    except Exception:
        return "genesis"


def _compute_chain_hash(job_id: int, ticker: str, scan_date, trace_id: str,
                         direction: str, prev_hash: str) -> str:
    """SHA-256 Merkle chain: each DONE job hashes its own fields + prev hash."""
    payload = f"{job_id}:{ticker}:{scan_date}:{trace_id or ''}:{direction}:{prev_hash}"
    return hashlib.sha256(payload.encode()).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# STALE JOB RECOVERY
# ─────────────────────────────────────────────────────────────────────────────

def recover_stale_jobs() -> dict:
    """
    Reset jobs stuck in CLAIMED or EXECUTING back to PENDING.
    Also closes daily_pipeline_runs RUNNING rows that have been open for > 2 hours
    with no completed_at (deadman check) — marks them FAILED.
    Called at startup AND every 5 min by the scheduler.
    Returns {recovered: N, failed_permanently: M}
    """
    recovered = 0
    failed_perm = 0
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=6) as conn, conn.cursor() as cur:
            # CLAIMED > 5 min → reset to PENDING
            cur.execute("""
                UPDATE options_pipeline_jobs
                SET status='PENDING', claim_id=NULL, claimed_at=NULL,
                    error_text = COALESCE(error_text,'') || ' | stale_CLAIMED_reset@' || NOW()::text,
                    recovery_attempts = recovery_attempts + 1
                WHERE status = 'CLAIMED'
                  AND claimed_at < NOW() - INTERVAL '5 minutes'
                  AND recovery_attempts < %s
                RETURNING id, ticker, scan_date, recovery_attempts
            """, (_MAX_RECOVERY_TRIES,))
            rows = cur.fetchall()
            for r in rows:
                log.warning(f"[stale] reset CLAIMED→PENDING  id={r[0]} {r[1]} {r[2]}  attempts={r[3]}")
                recovered += 1

            # EXECUTING > 10 min → reset to PENDING (or FAILED if max retries)
            cur.execute("""
                WITH stale AS (
                    SELECT id, ticker, scan_date, recovery_attempts
                    FROM options_pipeline_jobs
                    WHERE status = 'EXECUTING'
                      AND executing_at < NOW() - INTERVAL '10 minutes'
                )
                UPDATE options_pipeline_jobs AS j
                SET status = CASE
                        WHEN stale.recovery_attempts >= %s THEN 'FAILED'
                        ELSE 'PENDING'
                    END,
                    claim_id = NULL, claimed_at = NULL, executing_at = NULL,
                    error_text = COALESCE(j.error_text,'') || ' | stale_EXECUTING_reset@' || NOW()::text,
                    recovery_attempts = stale.recovery_attempts + 1
                FROM stale WHERE j.id = stale.id
                RETURNING j.id, j.ticker, j.scan_date, j.status, j.recovery_attempts
            """, (_MAX_RECOVERY_TRIES,))
            rows = cur.fetchall()
            conn.commit()
            for r in rows:
                if r[3] == "FAILED":
                    log.error(f"[stale] FAILED permanently id={r[0]} {r[1]} {r[2]}  attempts={r[4]}")
                    failed_perm += 1
                    _tg(
                        f"⚠️ <b>OPTIONS PIPELINE JOB FAILED PERMANENTLY</b>\n"
                        f"id={r[0]}  ticker={r[1]}  scan_date={r[2]}\n"
                        f"Exceeded {_MAX_RECOVERY_TRIES} recovery attempts.\n"
                        f"Manual investigation required."
                    )
                else:
                    log.warning(f"[stale] reset EXECUTING→PENDING  id={r[0]} {r[1]} {r[2]}  attempts={r[4]}")
                    recovered += 1

            # ── daily_pipeline_runs deadman: RUNNING > 2h with no completed_at ──
            # recover_stale_jobs previously only touched options_pipeline_jobs.
            # A scheduler that writes status='RUNNING' and then crashes before
            # writing completed_at leaves the row stuck forever — future startups
            # see the RUNNING row, hit ON CONFLICT DO NOTHING, and never seed.
            cur.execute("""
                UPDATE daily_pipeline_runs
                SET status       = 'FAILED',
                    completed_at = NOW(),
                    error_text   = COALESCE(error_text,'') ||
                                   ' | deadman_timeout@' || NOW()::text
                WHERE status     = 'RUNNING'
                  AND started_at < NOW() - INTERVAL '2 hours'
                  AND completed_at IS NULL
                RETURNING id, run_date, started_at
            """)
            zombie_rows = cur.fetchall()
            conn.commit()
            for zr in zombie_rows:
                log.warning(
                    f"[stale] daily_pipeline_runs zombie  id={zr[0]}"
                    f"  run_date={zr[1]}  started={zr[2]}  → FAILED (deadman 2h)"
                )
                _tg(
                    f"⚠️ <b>OPTIONS PIPELINE: Zombie Run Closed</b>\n"
                    f"daily_pipeline_runs id={zr[0]}  run_date={zr[1]}\n"
                    f"RUNNING since {zr[2]} with no completed_at — deadman 2h.\n"
                    f"Marked FAILED automatically."
                )

    except Exception as e:
        log.error(f"[stale_recovery] error: {e}")

    if recovered:
        log.info(f"[stale_recovery] recovered={recovered}  failed_perm={failed_perm}")
        _tg(
            f"🔄 <b>OPTIONS PIPELINE: Stale Job Recovery</b>\n"
            f"Recovered {recovered} stuck job(s) → PENDING for re-execution.\n"
            f"Permanently failed: {failed_perm}"
        )

    # ── Commit-drift alert (Step 3) ──────────────────────────────────────────
    # Fires at most once per process lifetime, only after a 15-min grace period
    # so normal deploy+immediate-restart cycles don't produce noise.
    global _DRIFT_ALERT_SENT
    if not _DRIFT_ALERT_SENT and _BOOT_COMMIT != "UNKNOWN":
        try:
            _dsk = subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=os.path.dirname(os.path.abspath(__file__)),
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
            if _dsk != _BOOT_COMMIT:
                _drift_secs = (datetime.utcnow() - _BOOT_TIME).total_seconds()
                if _drift_secs >= 900:  # 15-minute grace
                    _tg(
                        f"🔴 <b>SCHEDULER RUNNING STALE CODE</b>\n"
                        f"Running : <code>{_BOOT_COMMIT[:12]}</code>\n"
                        f"On-disk : <code>{_dsk[:12]}</code>\n"
                        f"Process started {round(_drift_secs / 60)}m ago — code on disk has changed.\n"
                        f"⚠️ <b>Restart the options-pipeline-scheduler workflow to load new code.</b>"
                    )
                    _DRIFT_ALERT_SENT = True
                    log.warning(
                        f"[drift] STALE: running={_BOOT_COMMIT[:12]} "
                        f"disk={_dsk[:12]} drift={round(_drift_secs/60)}m "
                        f"— Telegram alert sent"
                    )
                else:
                    log.info(
                        f"[drift] mismatch detected (running={_BOOT_COMMIT[:12]} "
                        f"disk={_dsk[:12]}) drift={round(_drift_secs/60,1)}m "
                        f"— within 15-min grace, no alert yet"
                    )
        except Exception as _da_e:
            log.debug(f"[drift] check failed: {_da_e}")

    return {"recovered": recovered, "failed_permanently": failed_perm}

# ─────────────────────────────────────────────────────────────────────────────
# SEED DAILY CANDIDATES
# ─────────────────────────────────────────────────────────────────────────────

def seed_daily_candidates(scan_date: date = None, limit: int = 5) -> dict:
    """
    Insert PENDING jobs for today's top options candidates.
    UNIQUE(ticker, scan_date) prevents duplicates across calls.
    Returns {seeded: N, skipped_duplicates: M}
    """
    scan_date = scan_date or datetime.now(_ET).date()
    seeded = 0
    dupes  = 0
    candidates = []
    _double_zero = False   # True when both primary and fallback queries return 0 rows

    # Stage 7: SEED_STAGE — write-before-work, before candidate query runs
    try:
        if _chkp:
            _s7_tid = _chkp.get_or_set_trace_id(scan_date, _DB_URL,
                                                  new_trace_id=str(uuid.uuid4()))
            _chkp.chk(_s7_tid, "SEED_STAGE",
                       {"scan_date": str(scan_date), "limit": limit}, _DB_URL)
    except Exception as _s7e:
        log.warning(f"[seed] checkpoint SEED_STAGE failed: {_s7e}")

    try:
        with psycopg2.connect(_DB_URL, connect_timeout=6) as conn, conn.cursor() as cur:
            # Top FEAR_PREMIUM bearish candidates with both OSS + PMD data.
            # polygon_market_daily is EOD data — it never has today's date on
            # the same calendar day.  Join on the latest available date so
            # a VM restart after 09:45 (missed-seed recovery) still seeds.
            cur.execute("""
                SELECT o.ticker, o.scan_date, o.pc_skew_pp, o.gex_regime, o.pc_skew_tag
                FROM options_structure_scan o
                JOIN polygon_market_daily p
                    ON p.ticker = o.ticker
                   AND p.scan_date = (SELECT MAX(scan_date) FROM polygon_market_daily)
                WHERE o.scan_date = %s
                  AND o.pc_skew_pp IS NOT NULL
                  AND o.front_iv > 0
                  AND o.spot > 10
                ORDER BY o.pc_skew_pp DESC
                LIMIT %s
            """, (scan_date, limit))
            candidates = cur.fetchall()

            # Fallback: if today's scan_date has 0 eligible OSS rows (OSS scan not
            # yet run, or init-before-query race where startup seeded jobs before the
            # 09:40 cron), retry with MAX(scan_date) — same pattern used by
            # _seed_from_polygon_universe to avoid empty-handed seeding.
            if not candidates:
                log.warning(f"[seed] 0 eligible OSS rows for scan_date={scan_date}; "
                            f"retrying with MAX(scan_date) fallback")
                cur.execute("""
                    SELECT o.ticker, o.scan_date, o.pc_skew_pp, o.gex_regime, o.pc_skew_tag
                    FROM options_structure_scan o
                    JOIN polygon_market_daily p
                        ON p.ticker = o.ticker
                       AND p.scan_date = (SELECT MAX(scan_date) FROM polygon_market_daily)
                    WHERE o.scan_date = (SELECT MAX(scan_date) FROM options_structure_scan)
                      AND o.pc_skew_pp IS NOT NULL
                      AND o.front_iv > 0
                      AND o.spot > 10
                    ORDER BY o.pc_skew_pp DESC
                    LIMIT %s
                """, (limit,))
                candidates = cur.fetchall()
                if candidates:
                    log.info(f"[seed] fallback: found {len(candidates)} rows for "
                             f"MAX scan_date={candidates[0][1]}")
                else:
                    log.warning(f"[seed] fallback also returned 0 rows — "
                                f"double-zero condition; OSS has no qualifying rows on any date")
                    _double_zero = True

            for row in candidates:
                ticker, sd, _, _, _ = row
                try:
                    cur.execute("""
                        INSERT INTO options_pipeline_jobs
                            (ticker, scan_date, status, trigger_source)
                        VALUES (%s, %s, 'PENDING', 'daily_scheduler')
                        ON CONFLICT (ticker, scan_date) DO NOTHING
                    """, (ticker, sd))
                    if cur.rowcount > 0:
                        seeded += 1
                        log.info(f"[seed] seeded {ticker} {sd}")
                    else:
                        dupes += 1
                        log.info(f"[seed] skip duplicate {ticker} {sd}")
                except Exception as ie:
                    log.warning(f"[seed] insert error {ticker}: {ie}")

            # If startup backfill pre-seeded jobs before the 09:40 cron ran, all
            # candidates are duplicates → seeded=0 → daily_pipeline_runs gets
            # candidates_seeded=0, making it look like nothing was queued.
            # Count pre-existing PENDING jobs to report an accurate total.
            if seeded == 0 and dupes > 0:
                cur.execute(
                    "SELECT COUNT(*) FROM options_pipeline_jobs "
                    "WHERE scan_date=%s AND status='PENDING'", (scan_date,)
                )
                _pre_pending = cur.fetchone()[0] or 0
                if _pre_pending > 0:
                    log.info(f"[seed] all {dupes} were duplicates; "
                             f"{_pre_pending} PENDING jobs already exist for {scan_date} "
                             f"— reporting accurate count")
                    seeded = _pre_pending

            conn.commit()

    except Exception as e:
        log.error(f"[seed] query failed: {e}")
        return {"seeded": 0, "skipped_duplicates": 0, "error": str(e)}

    log.info(f"[seed] scan_date={scan_date}  seeded={seeded}  skipped={dupes}  "
             f"candidates={[r[0] for r in candidates]}")
    if seeded:
        _tg(
            f"📋 <b>OPTIONS PIPELINE: Daily Jobs Seeded</b>\n"
            f"scan_date={scan_date}  seeded={seeded}  skipped_dupes={dupes}\n"
            f"Tickers: {', '.join(r[0] for r in candidates[:seeded])}"
        )
    elif _double_zero:
        _tg(
            f"⚠️ <b>OPTIONS PIPELINE: SEED DOUBLE-ZERO</b>\n"
            f"scan_date={scan_date}  primary_rows=0  fallback_rows=0\n"
            f"OSS has no qualifying rows on any date — pipeline will NOT run today.\n"
            f"Check options_structure_scan table and OSS scan logs."
        )

    # Write seed event to durable run log
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=4) as _dc, _dc.cursor() as _cu:
            # seeded=0 regardless of _double_zero means nothing will execute —
            # write NO_CANDIDATES so the deadman never sees a stuck RUNNING row.
            _run_status = "NO_CANDIDATES" if (_double_zero or seeded == 0) else "RUNNING"
            # NOTE (informational-counter): candidates_seeded here is a last-writer-wins
            # summary.  When /run-seed and the natural 09:40 cron both land within the
            # same minute, the 09:40 cron's ON CONFLICT DO UPDATE overwrites this field
            # with _pre_pending (count of PENDING jobs at that instant), which may differ
            # from the total inserted by the earlier call.
            # AUTHORITATIVE count: SELECT COUNT(*) FROM options_pipeline_jobs
            #                      WHERE scan_date=<date> — not this column.
            _cu.execute("""
                INSERT INTO daily_pipeline_runs
                    (run_date, trigger_source, status, candidates_seeded, started_at)
                VALUES (%s, 'primary', %s, %s, NOW())
                ON CONFLICT (run_date, trigger_source) DO UPDATE
                    SET status=EXCLUDED.status,
                        candidates_seeded=EXCLUDED.candidates_seeded,
                        started_at=COALESCE(daily_pipeline_runs.started_at, NOW())
            """, (scan_date, _run_status, seeded))
            _dc.commit()
    except Exception as _de:
        log.warning(f"[seed] daily_pipeline_runs write failed: {_de}")

    ret = {"seeded": seeded, "skipped_duplicates": dupes,
           "candidates": [r[0] for r in candidates]}
    if _double_zero:
        ret["error"] = "zero_candidates"
    return ret


# ─────────────────────────────────────────────────────────────────────────────
# POLYGON UNIVERSE SEED — direct scan, independent of stock-scanning website
# ─────────────────────────────────────────────────────────────────────────────

def _seed_from_polygon_universe(scan_date: date = None, limit: int = 20) -> list:
    """
    Pull top candidates from polygon_rvol_scan (populated by aiem_process directly
    from Polygon grouped-daily — NOT from the stock-scanning website).
    Returns list of (ticker,) tuples for use in seed_daily_candidates.
    Falls back to empty list on any error.
    """
    scan_date = scan_date or date.today()
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT ticker
                FROM polygon_rvol_scan
                WHERE scan_date = (SELECT MAX(scan_date) FROM polygon_rvol_scan)
                  AND rvol    >= 1.5
                  AND volume  >= 500000
                  AND close_price >= 5.0
                ORDER BY rvol * ABS(gap_pct) DESC NULLS LAST
                LIMIT %s
            """, (limit,))
            rows = cur.fetchall()
            log.info(f"[polygon_universe] found {len(rows)} candidates from polygon_rvol_scan")
            return rows
    except Exception as e:
        log.warning(f"[polygon_universe] query failed: {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# PREMARKET SCAN JOB — runs 07:30 ET, computes PM intel for today's candidates
# ─────────────────────────────────────────────────────────────────────────────

def premarket_scan_job(scan_date: date = None) -> dict:
    """
    07:30 ET job: fetch premarket intelligence for all seeded + universe tickers.
    Stores results in options_engine_premarket for use by _execute_job at 09:45.
    """
    scan_date = scan_date or date.today()
    processed = 0
    errors    = 0

    # Gather tickers: already-seeded pipeline jobs + polygon universe candidates
    tickers: list[str] = []
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT ticker FROM options_pipeline_jobs
                WHERE scan_date = %s AND status IN ('PENDING','CLAIMED','EXECUTING')
            """, (scan_date,))
            tickers = [r[0] for r in cur.fetchall()]
    except Exception as e:
        log.warning(f"[pm_scan] seeded tickers query failed: {e}")

    # Add polygon universe candidates not already in the list
    universe_rows = _seed_from_polygon_universe(scan_date, limit=15)
    for (t,) in universe_rows:
        if t not in tickers:
            tickers.append(t)

    if not tickers:
        log.info(f"[pm_scan] no tickers to scan for {scan_date}")
        return {"processed": 0, "errors": 0, "tickers": []}

    log.info(f"[pm_scan] running premarket intel for {len(tickers)} tickers: {tickers}")

    try:
        import aiem_premarket_intel as _pm_mod
        for ticker in tickers:
            try:
                result = _pm_mod.get_premarket_intel(ticker, scan_date, store=True)
                log.info(
                    f"[pm_scan] {ticker}: score={result.get('premarket_score','?')} "
                    f"dir={result.get('premarket_direction','?')} "
                    f"flags={result.get('premarket_risk_flags',[])}"
                )
                processed += 1
            except Exception as _te:
                log.warning(f"[pm_scan] {ticker} failed: {_te}")
                errors += 1
    except ImportError as _ie:
        log.error(f"[pm_scan] aiem_premarket_intel not available: {_ie}")
        errors = len(tickers)

    _tg(
        f"🌅 <b>OPTIONS ENGINE: Premarket Scan Complete</b>\n"
        f"scan_date={scan_date}  processed={processed}  errors={errors}\n"
        f"Tickers: {', '.join(tickers[:processed])}"
    )
    return {"processed": processed, "errors": errors, "tickers": tickers}


# ─────────────────────────────────────────────────────────────────────────────
# ATOMIC CLAIM
# ─────────────────────────────────────────────────────────────────────────────

def _atomic_claim(claim_id: str, scan_date: date) -> tuple[int, str] | None:
    """
    Atomically claim one PENDING job for scan_date.
    Uses SELECT ... FOR UPDATE SKIP LOCKED — safe for concurrent callers.
    Returns (job_id, ticker) or None if nothing available.
    """
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=6) as conn, conn.cursor() as cur:
            cur.execute("""
                WITH candidate AS (
                    SELECT id FROM options_pipeline_jobs
                    WHERE status = 'PENDING'
                      AND scan_date = %s
                      AND recovery_attempts < %s
                    ORDER BY created_at ASC
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE options_pipeline_jobs
                SET status = 'CLAIMED',
                    claim_id = %s,
                    claimed_at = NOW()
                FROM candidate
                WHERE options_pipeline_jobs.id = candidate.id
                RETURNING options_pipeline_jobs.id, options_pipeline_jobs.ticker
            """, (scan_date, _MAX_RECOVERY_TRIES, claim_id))
            row = cur.fetchone()
            conn.commit()
            return row   # (id, ticker) or None
    except Exception as e:
        log.error(f"[claim] atomic claim failed: {e}")
        return None

# ─────────────────────────────────────────────────────────────────────────────
# EXECUTE ONE JOB
# ─────────────────────────────────────────────────────────────────────────────

def _execute_job(job_id: int, ticker: str, scan_date: date, claim_id: str) -> dict:
    """
    Run the full 10-stage options pipeline for one job.
    Updates job status through EXECUTING → DONE | FAILED.
    Returns the pipeline result dict.
    """
    trace_id = hashlib.sha256(
        f"{ticker}{scan_date}{claim_id}".encode()
    ).hexdigest()[:16]

    log.info(f"[exec] START job_id={job_id} ticker={ticker} "
             f"scan_date={scan_date} trace_id={trace_id} claim_id={claim_id}")

    # Lookup pipeline-level trace_id (set by watchdog at WATCHDOG_POLL stage 1)
    _pipeline_tid = None
    try:
        if _chkp:
            _pipeline_tid = _chkp.get_or_set_trace_id(scan_date, _DB_URL)
    except Exception as _ptid_e:
        log.debug(f"[exec] pipeline_trace_id lookup failed: {_ptid_e}")

    # ── Scheduler causal trace (R8 Item 8 — non-fatal) ────────────────────────
    _strace_ctx = None
    try:
        import sys as _strace_sys
        import os as _strace_os
        _strace_dpl_dir = _strace_os.path.join(
            _strace_os.path.dirname(_strace_os.path.abspath(__file__)), 'dpl')
        if _strace_dpl_dir not in _strace_sys.path:
            _strace_sys.path.insert(0, _strace_dpl_dir)
        import scheduler_trace as _sched_trace_mod
        _sched_trace_mod.bootstrap(_DB_URL)
        _strace_ctx = _sched_trace_mod.TraceContext(
            trace_id=trace_id,
            db_url=_DB_URL,
        )
        _strace_ctx.write_stage(
            "SCHEDULER_FIRE",
            ticker=ticker,
            scan_date=scan_date,
            job_id=job_id,
            job_claim_timestamp=datetime.utcnow().isoformat() + "Z",
            metadata={
                "claim_id": claim_id,
                "scheduler_name": _SCHEDULER_NAME,
                "cron": "09:45 ET Mon-Fri",
            },
        )
    except Exception as _strace_init_e:
        log.debug(f"[scheduler_trace] init/SCHEDULER_FIRE failed (non-fatal): {_strace_init_e}")

    # Mark EXECUTING + heartbeat
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                UPDATE options_pipeline_jobs
                SET status='EXECUTING', executing_at=NOW(), trace_id=%s,
                    heartbeat_at=NOW()
                WHERE id=%s AND claim_id=%s
            """, (trace_id, job_id, claim_id))
            conn.commit()
    except Exception as e:
        log.error(f"[exec] failed to mark EXECUTING: {e}")
        return {"error": str(e)}

    # ── Phase III Phase 1: Registry helpers (non-fatal — never block pipeline) ──
    try:
        import aiem_options_registries as _reg_mod
        _reg_db     = _DB_URL
        _reg_ts_now = datetime.utcnow()
        _reg_ready  = True

        def _rc(family: str, cid: str, raw, norm=None, sig: str = "NEUTRAL",
                conf=None, d_ts=None, fresh: int = None, q: str = None,
                sup=None, txt: str = None) -> None:
            """Register + snap one indicator value. Fully non-fatal."""
            try:
                _reg_mod.register_indicator(
                    cid, cid.replace("_", " ").title(), family,
                    "aiem_options_scheduler.py", "_execute_job", {}, _reg_db)
                _reg_mod.snap_indicator(
                    trace_id, ticker, scan_date, cid, raw, norm, sig, conf,
                    d_ts or _reg_ts_now, fresh,
                    q or ("MISSING" if raw is None else "FRESH"),
                    sup, None, None, None, txt, _reg_db)
            except Exception as _rce:
                log.debug(f"[registry] snap {cid}: {_rce}")

        def _rc_pat(cid: str, name: str, family: str, conf=None,
                    timeframe: str = None, actionable: bool = None,
                    influenced: bool = None, data: dict = None) -> None:
            """Register + snap one pattern occurrence. Fully non-fatal."""
            try:
                _reg_mod.register_pattern(
                    cid, name, family, "aiem_pattern_engine.py",
                    "detect_for_ticker", "1.0", _reg_db)
                _reg_mod.snap_pattern(
                    trace_id, ticker, scan_date, cid, timeframe,
                    conf, actionable, influenced, data or {}, None, _reg_db)
            except Exception as _rpe:
                log.debug(f"[registry] pat {cid}: {_rpe}")

    except Exception as _reg_init_e:
        log.debug(f"[exec] registry init skipped: {_reg_init_e}")
        _reg_ready  = False
        _reg_mod    = None
        _reg_db     = _DB_URL
        _reg_ts_now = datetime.utcnow()
        def _rc(*a, **k):     pass  # noqa
        def _rc_pat(*a, **k): pass  # noqa

    # ── Phase III Phase 2: Strategy/Decision/Outcome capture (non-fatal) ─────
    # Stage 8: P2_INIT — write-before-work, before bootstrap_phase2
    try:
        if _chkp and _pipeline_tid:
            _chkp.chk(_pipeline_tid, "P2_INIT",
                       {"ticker": ticker, "scan_date": str(scan_date),
                        "job_id": job_id}, _DB_URL)
    except Exception as _s8e:
        log.warning(f"[exec] checkpoint P2_INIT failed: {_s8e}")
    try:
        import aiem_options_phase2 as _p2
        _p2.bootstrap_phase2(_DB_URL)
        _p2_ready = True
    except Exception as _p2_init_e:
        log.debug(f"[exec] phase2 init skipped: {_p2_init_e}")
        _p2_ready = False
        _p2       = None
    log.info(f"[exec] [{trace_id}] [P2_INIT] _p2_ready={_p2_ready}")

    # ── Phase III Phase 3: Analysis & Attribution (non-fatal) ────────────────
    try:
        import aiem_options_phase3 as _p3
        _p3.bootstrap_phase3(_DB_URL)
        _p3_ready = True
    except Exception as _p3_init_e:
        log.warning(f"[phase3] init failed: {_p3_init_e}")
        _p3_ready = False
        _p3       = None

    # ── Phase III Phase 4: Portfolio & Operational Learning (non-fatal) ───────
    try:
        import aiem_options_phase4 as _p4
        _p4.bootstrap_phase4(_DB_URL)
        _p4_ready = True
    except Exception as _p4_init_e:
        log.warning(f"[phase4] init failed: {_p4_init_e}")
        _p4_ready = False
        _p4       = None

    # ── Phase III Phase 5: Adaptive Control & Governance (non-fatal) ─────────
    try:
        import aiem_options_phase5 as _p5
        _p5.bootstrap_phase5(_DB_URL)
        _p5.seed_initial_champion(_DB_URL)
        _p5_ready = True
    except Exception as _p5_init_e:
        log.warning(f"[phase5] init failed: {_p5_init_e}")
        _p5_ready = False
        _p5       = None

    # ── DPL Phase 1: Immutable Audit Record (non-fatal) ──────────────────────
    try:
        import aiem_options_dpl as _dpl
        _dpl.bootstrap_dpl(_DB_URL)
        # R8 Item 4/7: Correction ledger + quarantine tables
        try:
            import sys as _cl_sys, os as _cl_os
            _cl_dpl_dir = _cl_os.path.join(
                _cl_os.path.dirname(_cl_os.path.abspath(__file__)), 'dpl')
            if _cl_dpl_dir not in _cl_sys.path:
                _cl_sys.path.insert(0, _cl_dpl_dir)
            import correction_ledger as _corr_ledger
            _corr_ledger.bootstrap(_DB_URL)
            _corr_ledger.populate_known_corrections(_DB_URL)
            _corr_ledger.populate_legacy_non_replayable(_DB_URL)
        except Exception as _cl_e:
            log.debug(f"[correction_ledger] init failed (non-fatal): {_cl_e}")
        _dpl_ready = True
        # B17 (R7): non-verifier consumer — log contamination exclusions at startup so
        # the scheduler never silently includes contaminated replay-input rows.
        try:
            _excl = _dpl.get_contamination_exclusions(_DB_URL)
            if _excl:
                log.warning(f"[dpl] {len(_excl)} contamination exclusion(s) active: "
                            + ", ".join(e.get('decision_id','?') for e in _excl))
            else:
                log.info("[dpl] oe_contamination_exclusions: 0 rows (no exclusions active)")
        except Exception as _excl_e:
            log.warning(f"[dpl] contamination exclusion read failed (non-fatal): {_excl_e}")
    except Exception as _dpl_init_e:
        log.warning(f"[dpl] init failed: {_dpl_init_e}")
        _dpl_ready = False
        _dpl       = None

    t_start = time.time()
    _gate_fired = [False]  # defined before outer try — always reachable in except (Item 7)

    try:
        import aiem_options_intel   as _oi
        import aiem_options_pipeline as _pipe
        import psycopg2 as _pg2

        # ── Stage 1: Pull Polygon data from DB ────────────────────────────────
        # polygon_market_daily is EOD data — it never contains today's date
        # on the same calendar day.  Use the most recent available row for
        # the ticker so missed-seed recovery still executes correctly.
        with _pg2.connect(_DB_URL, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT scan_date, close_price, open_price, vwap, close_strength
                FROM polygon_market_daily
                WHERE ticker=%s
                ORDER BY scan_date DESC
                LIMIT 1
            """, (ticker,))
            pmd = cur.fetchone()
            cur.execute("""
                SELECT spot, front_iv, gex_m, gex_regime, gamma_flip_price,
                       pc_skew_pp, pc_skew_tag, term_ratio, term_tag, back_iv
                FROM options_structure_scan
                WHERE ticker=%s AND scan_date=%s
            """, (ticker, scan_date))
            oss = cur.fetchone()

        if not pmd or not oss:
            raise ValueError(f"missing Polygon/OSS data for {ticker} {scan_date}")

        # ── Trace: MARKET_DATA_CAPTURE ─────────────────────────────────────────
        if _strace_ctx is not None:
            try:
                _strace_ctx.write_stage(
                    "MARKET_DATA_CAPTURE",
                    ticker=ticker, scan_date=scan_date, job_id=job_id,
                    metadata={
                        "pmd_date": str(pmd[0]),
                        "has_oss": oss is not None,
                        "close_price": float(pmd[1]) if pmd[1] else None,
                        "spot": float(oss[0]) if oss[0] else None,
                    },
                )
            except Exception as _st_mdc_e:
                log.debug(f"[scheduler_trace] MARKET_DATA_CAPTURE: {_st_mdc_e}")

        close_price  = float(pmd[1])
        vwap         = float(pmd[3])
        close_str    = float(pmd[4])
        spot         = float(oss[0])
        front_iv_pct = float(oss[1])
        front_iv     = front_iv_pct / 100.0
        gex_regime   = oss[3]
        pc_skew_pp   = float(oss[5])
        pc_skew_tag  = oss[6]
        term_tag     = oss[8]

        # ── REGISTRY: Stage 1 — Polygon ingestion + Options Structure Scan ────
        if _reg_ready:
            _pmd_dt  = datetime(pmd[0].year, pmd[0].month, pmd[0].day, 17, 0)
            _pmd_age = int((_reg_ts_now - _pmd_dt).total_seconds())
            _dow_pmd  = datetime.now(_ET).weekday()
            _pmd_stale_thresh = 345600 if _dow_pmd <= 1 else 172800
            _pmd_q   = "STALE" if _pmd_age > _pmd_stale_thresh else "FRESH"
            # Polygon ingestion subsystem
            _rc("POLYGON", "POLY_CLOSE_PRICE",    close_price, min(1.0, close_price/500.0),
                "NEUTRAL", None, _pmd_dt, _pmd_age, _pmd_q)
            _rc("POLYGON", "POLY_OPEN_PRICE",     float(pmd[2]), None,
                "NEUTRAL", None, _pmd_dt, _pmd_age, _pmd_q)
            _rc("POLYGON", "POLY_VWAP",           vwap, None,
                "NEUTRAL", None, _pmd_dt, _pmd_age, _pmd_q)
            _rc("POLYGON", "POLY_CLOSE_STRENGTH", close_str, close_str,
                "BULLISH" if close_str > 0.6 else "BEARISH" if close_str < 0.4 else "NEUTRAL",
                None, _pmd_dt, _pmd_age, _pmd_q)
            # Options Structure Scan subsystem
            _rc("OSS", "OSS_SPOT",        spot,     None, "NEUTRAL",  None, _pmd_dt, _pmd_age, _pmd_q)
            _rc("OSS", "OSS_FRONT_IV",    front_iv, front_iv,
                "HIGH_VOL" if front_iv > 0.40 else "LOW_VOL" if front_iv < 0.20 else "NEUTRAL",
                None, _pmd_dt, _pmd_age, _pmd_q)
            _rc("OSS", "OSS_GEX_M",       float(oss[2]) if oss[2] is not None else None,
                None, "NEUTRAL", None, _pmd_dt, _pmd_age, _pmd_q)
            # Encode regime as a numeric proxy so raw_value is not None
            # (snap_indicator forces quality_status=MISSING when raw=None).
            # 1.0=LONG_GAMMA (supportive), -1.0=SHORT_GAMMA (risky), 0.0=NEUTRAL
            _gex_raw = (1.0 if gex_regime == "LONG_GAMMA"
                        else -1.0 if gex_regime == "SHORT_GAMMA"
                        else 0.0) if gex_regime else None
            _rc("OSS", "OSS_GEX_REGIME",  _gex_raw, None, "NEUTRAL",
                None, _pmd_dt, _pmd_age, _pmd_q, txt=gex_regime)
            _rc("OSS", "OSS_PC_SKEW_PP",  pc_skew_pp, min(1.0, abs(pc_skew_pp)/30.0),
                "BEARISH" if pc_skew_tag == "FEAR_PREMIUM"
                else "BULLISH" if pc_skew_tag == "CALL_SKEW" else "NEUTRAL",
                None, _pmd_dt, _pmd_age, _pmd_q, txt=pc_skew_tag)
            _rc("OSS", "OSS_TERM_RATIO",  float(oss[7]) if oss[7] is not None else None, None,
                "BEARISH" if term_tag == "INVERTED" else "NEUTRAL",
                None, _pmd_dt, _pmd_age, _pmd_q, txt=term_tag)
            _rc("OSS", "OSS_BACK_IV",     float(oss[9])/100.0 if oss[9] is not None else None,
                None, "NEUTRAL", None, _pmd_dt, _pmd_age, _pmd_q)
            log.debug(f"[registry] stage1 snapped 11 indicators trace_id={trace_id}")

        # ── Stage 2: Stock analysis ────────────────────────────────────────────
        stock_direction = "BEAR" if (
            close_price < vwap and close_str < 0.4 and pc_skew_tag == "FEAR_PREMIUM"
        ) else "BULL"

        market_regime = (
            "LONG_GAMMA_FEAR_PREMIUM" if (pc_skew_tag == "FEAR_PREMIUM" and gex_regime == "SHORT_GAMMA")
            else "SHORT_GAMMA_TRENDING" if gex_regime == "SHORT_GAMMA"
            else "NEUTRAL"
        )

        stock_data = {
            "stock_direction": stock_direction,
            "market_regime":   market_regime,
            "iv_rank":         None,
            "iv_crush_risk":   "MODERATE_INVERTED_TERM" if term_tag == "INVERTED" else "LOW",
            "vwap_position":   "BELOW_VWAP" if close_price < vwap else "ABOVE_VWAP",
            "sector_strength": "LAGGING_SECTOR" if stock_direction == "BEAR" else "LEADING",
            "market_breadth":  "NEGATIVE" if stock_direction == "BEAR" else "POSITIVE",
            "close_strength":  close_str,
            "pc_skew_tag":     pc_skew_tag,
        }

        # ── REGISTRY: Stage 2 — Technical-indicator + Market-regime engines ───
        if _reg_ready:
            _rc("TECH",   "TECH_STOCK_DIRECTION",   None, None,
                "BULLISH" if stock_direction == "BULL" else "BEARISH", txt=stock_direction)
            _rc("TECH",   "TECH_VWAP_POSITION",     None, None,
                "BULLISH" if close_price >= vwap else "BEARISH",
                txt=stock_data["vwap_position"])
            _rc("TECH",   "TECH_CLOSE_STRENGTH",    close_str, close_str,
                "BULLISH" if close_str > 0.6 else "BEARISH" if close_str < 0.4 else "NEUTRAL")
            _rc("TECH",   "TECH_IV_CRUSH_RISK",     None, None, "NEUTRAL",
                txt=stock_data["iv_crush_risk"])
            _rc("REGIME", "MKT_REGIME_TAG",         None, None,
                "BEARISH" if "FEAR" in market_regime or "SHORT_GAMMA" in market_regime
                else "NEUTRAL", txt=market_regime)
            _rc("REGIME", "MKT_GEX_REGIME",         None, None,
                "BEARISH" if gex_regime == "SHORT_GAMMA" else "NEUTRAL", txt=gex_regime)
            _rc("REGIME", "MKT_SKEW_TAG",           None, None,
                "BEARISH" if pc_skew_tag == "FEAR_PREMIUM" else "NEUTRAL", txt=pc_skew_tag)
            _rc("REGIME", "MKT_TERM_STRUCTURE",     None, None,
                "BEARISH" if term_tag == "INVERTED" else "NEUTRAL", txt=term_tag)
            _rc("VOLREG", "VOLREG_FRONT_IV_CLASS",  None, None,
                "HIGH_VOL" if front_iv > 0.40 else "LOW_VOL" if front_iv < 0.20 else "NEUTRAL",
                txt="HIGH_IV" if front_iv > 0.40 else "LOW_IV")
            log.debug(f"[registry] stage2 snapped 9 indicators trace_id={trace_id}")

        # ── Stage PM: Premarket Intelligence ──────────────────────────────────
        pm_intel: dict = {}
        try:
            import aiem_premarket_intel as _pm_mod
            pm_intel = _pm_mod.get_premarket_intel(
                ticker, scan_date, prev_close=close_price, store=True)
            log.info(f"[exec] [{trace_id}] PM score={pm_intel.get('premarket_score','?')} "
                     f"dir={pm_intel.get('premarket_direction','?')} "
                     f"bars={pm_intel.get('bars_count',0)}")
        except Exception as _pm_e:
            log.debug(f"[exec] [{trace_id}] premarket_intel skipped: {_pm_e}")
            pm_intel = {"premarket_score": 0.5, "premarket_direction": "NEUTRAL",
                        "premarket_confidence": 0.0, "premarket_risk_flags": ["SKIPPED"]}

        # ── REGISTRY: Stage PM — Premarket scan + intraday scan subsystems ────
        if _reg_ready:
            _pm_score = float(pm_intel.get("premarket_score") or 0.5)
            _pm_conf  = float(pm_intel.get("premarket_confidence") or 0.0)
            _pm_dir   = str(pm_intel.get("premarket_direction") or "NEUTRAL")
            _pm_q     = "STALE" if "SKIPPED" in str(pm_intel.get("premarket_risk_flags", [])) \
                        else "FRESH"
            _pm_sig   = ("BULLISH" if _pm_dir in ("BULL", "BULLISH") else
                         "BEARISH" if _pm_dir in ("BEAR", "BEARISH") else "NEUTRAL")
            # Premarket scan subsystem
            _rc("PM", "PM_SCORE",         _pm_score, _pm_score, _pm_sig, _pm_conf, q=_pm_q)
            _rc("PM", "PM_DIRECTION",     None, None, _pm_sig,  _pm_conf, q=_pm_q, txt=_pm_dir)
            _rc("PM", "PM_CONFIDENCE",    _pm_conf, _pm_conf,   "NEUTRAL", q=_pm_q)
            _rc("PM", "PM_GAP_PCT",       pm_intel.get("premarket_gap"), None, "NEUTRAL", q=_pm_q)
            _rc("PM", "PM_VOLUME_RATIO",  pm_intel.get("pm_rvol"), None,
                "BULLISH" if (pm_intel.get("pm_rvol") or 0) > 1.5 else "NEUTRAL", q=_pm_q)
            _rc("PM", "PM_TREND_QUALITY", pm_intel.get("pm_trend_quality"), None, "NEUTRAL", q=_pm_q)
            # Intraday scan subsystem (surfaced from premarket module)
            _rc("INTRA", "INTRA_PM_HIGH_BROKEN",
                1.0 if pm_intel.get("pm_high_broken") else 0.0, None,
                "BULLISH" if pm_intel.get("pm_high_broken") else "NEUTRAL", q=_pm_q)
            _rc("INTRA", "INTRA_PM_LOW_HELD",
                1.0 if pm_intel.get("pm_low_held") else 0.0, None,
                "BEARISH" if pm_intel.get("pm_low_held") is False else "NEUTRAL", q=_pm_q)
            log.debug(f"[registry] stagepm snapped 8 indicators trace_id={trace_id}")

        # ── Stage MTF: Multi-Timeframe Analysis ───────────────────────────────
        mtf_result: dict = {}
        try:
            import aiem_multitimeframe as _mtf_mod
            mtf_result = _mtf_mod.analyze_ticker(ticker, scan_date, store=True)
            log.info(f"[exec] [{trace_id}] MTF alignment={mtf_result.get('timeframe_alignment_score','?')} "
                     f"bias={mtf_result.get('dominant_bias','?')} "
                     f"timing={mtf_result.get('entry_timing_status','?')}")
        except Exception as _mtf_e:
            log.debug(f"[exec] [{trace_id}] multitimeframe skipped: {_mtf_e}")
            mtf_result = {"timeframe_alignment_score": 0.5, "conflict_score": 0.5,
                          "dominant_bias": "NEUTRAL", "entry_timing_status": "UNCLEAR"}

        # ── REGISTRY: Stage MTF — Multi-timeframe analysis subsystem ──────────
        if _reg_ready:
            _mtf_al = float(mtf_result.get("timeframe_alignment_score") or 0.5)
            _mtf_cf = float(mtf_result.get("conflict_score") or 0.5)
            _mtf_bi = str(mtf_result.get("dominant_bias") or "NEUTRAL")
            _mtf_q  = "STALE" if (_mtf_al == 0.5 and _mtf_cf == 0.5 and
                                   _mtf_bi == "NEUTRAL") else "FRESH"
            _mtf_sig = ("BULLISH" if _mtf_bi == "BULLISH" else
                        "BEARISH" if _mtf_bi == "BEARISH" else "NEUTRAL")
            _rc("MTF", "MTF_ALIGNMENT_SCORE",  _mtf_al, _mtf_al,
                _mtf_sig, q=_mtf_q)
            _rc("MTF", "MTF_CONFLICT_SCORE",   _mtf_cf, _mtf_cf,
                "NEUTRAL", q=_mtf_q)
            _rc("MTF", "MTF_DOMINANT_BIAS",    None, None, _mtf_sig,
                txt=_mtf_bi, q=_mtf_q)
            _rc("MTF", "MTF_ENTRY_TIMING",     None, None, "NEUTRAL",
                txt=str(mtf_result.get("entry_timing_status") or "UNCLEAR"), q=_mtf_q)
            _rc("MTF", "MTF_BULLISH_TF_COUNT",
                mtf_result.get("bullish_tf_count"), None, "NEUTRAL", q=_mtf_q)
            _rc("MTF", "MTF_BEARISH_TF_COUNT",
                mtf_result.get("bearish_tf_count"), None, "NEUTRAL", q=_mtf_q)
            log.debug(f"[registry] stagemtf snapped 6 indicators trace_id={trace_id}")

        # ── Stage PAT: All Verified Patterns (candlestick/chart/harmonic/EW/VPA/Wyckoff)
        pattern_score  = 0.5
        pattern_result: dict = {}
        try:
            from aiem_pattern_engine import detect_for_ticker as _detect_pat
            pattern_result = _detect_pat(ticker, thesis=stock_direction, lookback=60)
            pattern_score  = pattern_result.get("pattern_score", 0.5)
            log.info(f"[exec] [{trace_id}] pattern_score={pattern_score:.3f} "
                     f"({len(pattern_result.get('all_patterns', []))} patterns detected)")
        except Exception as _pat_e:
            log.debug(f"[exec] [{trace_id}] pattern detection skipped: {_pat_e}")

        # ── REGISTRY: Stage PAT — Candlestick engine + Chart-pattern engine ───
        if _reg_ready:
            _pat_q = "STALE" if pattern_score == 0.5 and not pattern_result else "FRESH"
            _pat_err_q = "ERROR" if (not pattern_result and pattern_score == 0.5) else _pat_q
            _rc("PAT", "PAT_SCORE",    pattern_score, pattern_score,
                "BULLISH" if pattern_score > 0.6 else "BEARISH" if pattern_score < 0.4
                else "NEUTRAL", q=_pat_err_q)
            _rc("PAT", "PAT_COUNT",    len(pattern_result.get("all_patterns", [])), None,
                "NEUTRAL", q=_pat_q)
            _rc("PAT", "PAT_BULLISH",  len([p for p in pattern_result.get("all_patterns", [])
                                           if p.get("sentiment","").upper() == "BULLISH"]),
                None, "NEUTRAL", q=_pat_q)
            _rc("PAT", "PAT_BEARISH",  len([p for p in pattern_result.get("all_patterns", [])
                                           if p.get("sentiment","").upper() == "BEARISH"]),
                None, "NEUTRAL", q=_pat_q)
            # Register + snap every individual detected pattern
            for _p in pattern_result.get("all_patterns", []):
                _p_cid  = f"PAT_{str(_p.get('name','UNKNOWN')).upper().replace(' ','_')[:40]}"
                _p_fam  = str(_p.get("family", "chart")).lower()
                _rc_pat(_p_cid, str(_p.get("name","?")), _p_fam,
                        conf=float(_p.get("confidence") or 0.5),
                        timeframe=str(_p.get("timeframe", "daily")),
                        actionable=bool(_p.get("actionable", False)),
                        influenced=bool(_p.get("influencing", False)),
                        data={k: v for k, v in _p.items()
                              if k not in ("name","family","confidence")})
            log.debug(f"[registry] stagepat snapped 4+{len(pattern_result.get('all_patterns',[]))} "
                      f"pattern entries trace_id={trace_id}")

        # ── Stage OC: Real Polygon Options Chain ──────────────────────────────
        options_chain: dict   = {"calls": [], "puts": [], "contracts_total": 0}
        chain_strategies: list = []
        best_chain_strategy: dict | None = None
        contracts_evaluated = 0
        final_ccs = 0.0
        try:
            import aiem_polygon_options_chain as _chain_mod
            options_chain = _chain_mod.fetch_options_chain(ticker, min_dte=5, max_dte=21)
            contracts_evaluated = options_chain.get("contracts_total", 0)
            _direction_bias = (
                "BULLISH" if stock_direction == "BULL" else
                "BEARISH" if stock_direction == "BEAR" else "NEUTRAL"
            )
            chain_strategies = _chain_mod.evaluate_all_strategies(
                options_chain, spot, direction_bias=_direction_bias)
            if chain_strategies:
                best_chain_strategy = chain_strategies[0]
            log.info(
                f"[exec] [{trace_id}] options chain: {contracts_evaluated} contracts, "
                f"{len(chain_strategies)} strategies, "
                f"best={best_chain_strategy.get('strategy','none') if best_chain_strategy else 'none'}"
            )
        except Exception as _oc_e:
            log.warning(f"[exec] [{trace_id}] options chain skipped: {_oc_e}")

        # ── REGISTRY: Stage OC — Options-chain ingestion + Strategy generator ─
        if _reg_ready:
            _oc_q = "FRESH" if contracts_evaluated > 0 else "STALE"
            _rc("OC", "OC_CONTRACTS_TOTAL", contracts_evaluated, None, "NEUTRAL", q=_oc_q)
            _rc("OC", "OC_STRATEGIES_COUNT", len(chain_strategies), None, "NEUTRAL", q=_oc_q)
            _rc("OC", "OC_BEST_STRATEGY",    None, None, "NEUTRAL",
                txt=(best_chain_strategy.get("strategy") if best_chain_strategy else None),
                q=_oc_q)
            _rc("OC", "OC_CHAIN_CALLS_CNT",
                len(options_chain.get("calls", [])), None, "NEUTRAL", q=_oc_q)
            _rc("OC", "OC_CHAIN_PUTS_CNT",
                len(options_chain.get("puts", [])), None, "NEUTRAL", q=_oc_q)
            log.debug(f"[registry] stageoc snapped 5 indicators trace_id={trace_id}")

        # ── Stage EI: Execution Intelligence ──────────────────────────────────
        # Assesses fill probability, liquidity, execution costs, and net edge
        # for every strategy candidate.  In OBSERVE mode (EI_GATING_ENABLED=False)
        # all strategies pass through unchanged — assessments are saved to DB only.
        # In GATING mode (True) only EI-approved strategies continue to CCS.
        _ei_assessments: list = []
        try:
            import aiem_execution_intelligence as _ei_mod
            _ei_strategies, _ei_assessments = _ei_mod.filter_strategies_by_execution(
                chain_strategies,
                trace_id=trace_id,
                scan_date=scan_date,
                ticker=ticker,
                spot=spot,
                db_url=_DB_URL,
            )
            if _ei_strategies is not None and len(_ei_strategies) > 0:
                chain_strategies     = _ei_strategies
                best_chain_strategy  = chain_strategies[0]
            n_ei_approved = sum(1 for a in _ei_assessments if a.approved)
            log.info(
                f"[exec] [{trace_id}] EI: {n_ei_approved}/{len(_ei_assessments)} "
                f"strategies approved "
                f"({'GATING' if _ei_mod.EI_GATING_ENABLED else 'OBSERVE'})"
            )
        except Exception as _ei_e:
            log.warning(f"[exec] [{trace_id}] execution_intelligence skipped: {_ei_e}")

        # ── REGISTRY: Stage EI — Execution Intelligence + Strategy comparison ─
        if _reg_ready:
            _n_ei_all  = len(_ei_assessments)
            _n_ei_ok   = sum(1 for a in _ei_assessments if getattr(a, "approved", False))
            _ei_q      = "FRESH" if _n_ei_all > 0 else "STALE"
            _rc("EI", "EI_STRATEGIES_TOTAL",   _n_ei_all, None, "NEUTRAL", q=_ei_q)
            _rc("EI", "EI_STRATEGIES_APPROVED", _n_ei_ok, None,
                "BULLISH" if _n_ei_ok > 0 else "BEARISH", q=_ei_q)
            _rc("EI", "EI_APPROVAL_RATE",
                round(_n_ei_ok / _n_ei_all, 4) if _n_ei_all > 0 else None, None,
                "NEUTRAL", q=_ei_q)
            if _n_ei_all > 0 and _ei_assessments:
                _best_ea = _ei_assessments[0]
                _rc("EI", "EI_BEST_FILL_PROB",
                    getattr(_best_ea, "fill_probability", None), None, "NEUTRAL", q=_ei_q)
                _rc("EI", "EI_BEST_LIQ_SCORE",
                    getattr(_best_ea, "liquidity_score", None), None, "NEUTRAL", q=_ei_q)
                _rc("EI", "EI_BEST_NET_EDGE",
                    getattr(_best_ea, "net_expected_edge", None), None, "NEUTRAL", q=_ei_q)
            log.debug(f"[registry] stageei snapped 6 indicators trace_id={trace_id}")

        # (Phase 2 strategy candidate capture moved to after Stage 6 where
        #  direction, call_data, and put_data are all resolved.)

        # ── Stage CCS: Capital Compounding Score on best real-chain strategy ──
        try:
            if best_chain_strategy:
                from aiem_strat_engine.scoring import compute_capital_compounding_score as _ccs_fn
                _ccs_result = _ccs_fn(
                    pop=best_chain_strategy.get("pop", 0.50),
                    ev_after_costs=float(best_chain_strategy.get("ev_after_costs") or 0.0),
                    max_loss=float(best_chain_strategy.get("max_loss") or 500),
                    max_profit=float(best_chain_strategy.get("max_profit") or 1000),
                    risk_class="DEFINED_RISK",
                    execution_mode="paper",
                    liquidity=1.0 if best_chain_strategy.get("liquid") else 0.3,
                    strategy_direction=best_chain_strategy.get("direction", "NEUTRAL"),
                    thesis=market_regime,
                    strategy_vol_thesis="HIGH_IV" if front_iv > 0.40 else "LOW_IV",
                    vol_regime="HIGH_IV" if front_iv > 0.40 else "LOW_IV",
                    market_regime=market_regime,
                    iv_rank=iv_rank if "iv_rank" in dir() else 0.5,
                    strategy_family=best_chain_strategy.get("strategy", "other").lower()[:20],
                    pattern_score=pattern_score,
                    portfolio_capital=100_000.0,
                    pm_intel_score=float(pm_intel.get("premarket_score", 0.5)),
                    mtf_alignment_score=float(mtf_result.get("timeframe_alignment_score", 0.5)),
                )
                final_ccs = _ccs_result.get("capital_compounding_score", 0.0)
                best_chain_strategy["ccs"] = final_ccs
                best_chain_strategy["ccs_components"] = _ccs_result
                log.info(f"[exec] [{trace_id}] CCS={final_ccs:.4f} "
                         f"strategy={best_chain_strategy.get('strategy')}")
        except Exception as _ccs_e:
            log.debug(f"[exec] [{trace_id}] CCS computation skipped: {_ccs_e}")

        # ── REGISTRY: Stage CCS — Portfolio Optimization + Portfolio Risk ──────
        if _reg_ready:
            _ccs_q = "FRESH" if final_ccs > 0.0 else "STALE"
            _rc("CCS", "CCS_SCORE",         final_ccs, final_ccs,
                "BULLISH" if final_ccs > 0.70 else "BEARISH" if final_ccs < 0.30
                else "NEUTRAL", q=_ccs_q)
            _rc("CCS", "CCS_BEST_STRATEGY", None, None, "NEUTRAL",
                txt=(best_chain_strategy.get("strategy") if best_chain_strategy else None),
                q=_ccs_q)
            if best_chain_strategy:
                _rc("CCS", "CCS_STRATEGY_POP",
                    best_chain_strategy.get("pop"), None, "NEUTRAL", q=_ccs_q)
                _rc("CCS", "CCS_STRATEGY_EV",
                    best_chain_strategy.get("ev_after_costs"), None,
                    "BULLISH" if (best_chain_strategy.get("ev_after_costs") or 0) > 0
                    else "BEARISH", q=_ccs_q)
                _rc("CCS", "CCS_RISK_CLASS",  None, None, "NEUTRAL",
                    txt=best_chain_strategy.get("risk_class", "UNKNOWN"), q=_ccs_q)
            log.debug(f"[registry] stageccs snapped 5 indicators trace_id={trace_id}")

        # ── Proof logging: PM + MTF + PAT + OC stages ─────────────────────────
        try:
            import aiem_pipeline_proof as _proof
            _proof.log_stage(trace_id=trace_id, ticker=ticker, thesis=stock_direction,
                             stage="premarket_intel",
                             data={k: v for k, v in pm_intel.items()
                                   if k not in ("sector", "raw_data_json")})
            _proof.log_stage(trace_id=trace_id, ticker=ticker, thesis=stock_direction,
                             stage="multitimeframe",
                             data={"alignment_score": mtf_result.get("timeframe_alignment_score"),
                                   "conflict_score":  mtf_result.get("conflict_score"),
                                   "dominant_bias":   mtf_result.get("dominant_bias"),
                                   "entry_timing":    mtf_result.get("entry_timing_status"),
                                   "bullish_tfs":     mtf_result.get("bullish_tf_count"),
                                   "bearish_tfs":     mtf_result.get("bearish_tf_count")})
            _proof.log_stage(trace_id=trace_id, ticker=ticker, thesis=stock_direction,
                             stage="pattern_scan_options_engine",
                             data={"pattern_score": pattern_score,
                                   "n_patterns": len(pattern_result.get("all_patterns", []))})
            _proof.log_stage(trace_id=trace_id, ticker=ticker, thesis=stock_direction,
                             stage="options_chain_polygon",
                             data={"contracts_total":      contracts_evaluated,
                                   "strategies_evaluated": len(chain_strategies),
                                   "best_strategy":        (best_chain_strategy.get("strategy")
                                                            if best_chain_strategy else None),
                                   "best_ccs":             final_ccs})
        except Exception as _pp_e:
            log.debug(f"[exec] [{trace_id}] proof log skipped: {_pp_e}")

        # ── Stage 3: Options analysis ──────────────────────────────────────────
        em_result  = _oi.compute_expected_move(ticker, dte_days=9)
        ivr_result = _oi.compute_iv_rank_live(ticker)
        oi_result  = _oi.compute_oi_by_strike(ticker)
        bs_result  = _oi.compute_bearish_signals(min_fear_pp=40.0)

        if "error" in em_result:
            raise ValueError(f"compute_expected_move: {em_result['error']}")
        if "error" in ivr_result:
            raise ValueError(f"compute_iv_rank_live: {ivr_result['error']}")

        iv_rank = float(ivr_result["iv_rank"]) / 100.0
        stock_data["iv_rank"] = iv_rank

        options_analysis = {
            "expected_move":   em_result,
            "iv_rank":         ivr_result,
            "oi_by_strike":    oi_result,
            "bearish_signals": {
                "count":   bs_result.get("count", 0),
                "ticker_row": next(
                    (r for r in bs_result.get("results", []) if r["ticker"] == ticker),
                    None,
                ),
            },
        }

        # ── REGISTRY: Stage 3 — Options analytics + Volatility-regime engine ──
        if _reg_ready:
            _iv_rank_raw = float(ivr_result.get("iv_rank", 0.0) or 0.0)
            _em_val      = em_result.get("expected_move")
            _em_pct      = em_result.get("expected_move_pct")
            # Options analytics subsystem
            _rc("OPT", "OPT_EXPECTED_MOVE",     _em_val, None, "NEUTRAL")
            _rc("OPT", "OPT_EXPECTED_MOVE_PCT",  _em_pct, _em_pct/100.0 if _em_pct else None,
                "NEUTRAL")
            _rc("OPT", "OPT_IV_RANK",            _iv_rank_raw, _iv_rank_raw/100.0,
                "HIGH_VOL" if _iv_rank_raw > 50 else "LOW_VOL")
            _rc("OPT", "OPT_IV_PERCENTILE",
                ivr_result.get("iv_percentile"), None, "NEUTRAL")
            _rc("OPT", "OPT_HV_20D",
                ivr_result.get("historical_vol_20d"), None, "NEUTRAL")
            _rc("OPT", "OPT_OI_BELOW_SPOT",
                oi_result.get("oi_below_spot") if not isinstance(oi_result.get("oi_below_spot"),
                str) else None, None, "NEUTRAL")
            _rc("OPT", "OPT_OI_ABOVE_SPOT",
                oi_result.get("oi_above_spot") if not isinstance(oi_result.get("oi_above_spot"),
                str) else None, None, "NEUTRAL")
            _rc("OPT", "OPT_BEARISH_SIGNAL_COUNT",
                bs_result.get("count", 0), None,
                "BEARISH" if bs_result.get("count", 0) > 0 else "NEUTRAL")
            # Volatility-regime engine subsystem (full suite)
            _rc("VOLREG", "VOLREG_IV_RANK",       _iv_rank_raw, _iv_rank_raw/100.0,
                "HIGH_VOL" if _iv_rank_raw > 50 else "LOW_VOL")
            _iv_hv20 = ivr_result.get("historical_vol_20d")
            _vrp     = (round(front_iv - _iv_hv20, 4) if _iv_hv20 and front_iv else None)
            _rc("VOLREG", "VOLREG_VRP",           _vrp, None,
                "HIGH_PREM" if (_vrp or 0) > 0.05 else "LOW_PREM" if (_vrp or 0) < -0.05
                else "NEUTRAL")
            _rc("VOLREG", "VOLREG_TERM_RATIO",
                float(oss[7]) if oss[7] is not None else None, None,
                "BEARISH" if term_tag == "INVERTED" else "NEUTRAL", txt=term_tag)
            log.debug(f"[registry] stage3 snapped 11 indicators trace_id={trace_id}")

        # ── Stage 4: Risk gates ────────────────────────────────────────────────
        import math as _math

        _dte = 9                        # strategy DTE target (design parameter)
        _T   = _dte / 252.0             # fraction of trading year

        # Black-Scholes helpers (standard normal CDF and PDF)
        _N    = lambda x: 0.5 * (1.0 + _math.erf(x / _math.sqrt(2.0)))
        _npdf = lambda x: _math.exp(-0.5 * x * x) / _math.sqrt(2.0 * _math.pi)

        def _bs_d1d2(S, K, sig, T):
            """d1, d2 from Black-Scholes (r=0 simplification)."""
            if sig <= 0 or T <= 0 or S <= 0 or K <= 0:
                return 0.0, -0.1
            d1 = (_math.log(S / K) + 0.5 * sig**2 * T) / (sig * _math.sqrt(T))
            return d1, d1 - sig * _math.sqrt(T)

        # Strike levels — increment adapts to spot price to match real market
        # strike grids; floor/ceil guarantee put is OTM (below spot) and call
        # is OTM (above spot) for all spot values including sub-$5 tickers.
        _sinc       = 1.0 if spot < 5 else 2.5 if spot < 25 else 5.0
        put_strike  = _math.floor(spot * 0.975 / _sinc) * _sinc
        call_strike = _math.ceil(spot * 1.025 / _sinc) * _sinc
        if put_strike >= spot:   # hard OTM guard for exact-multiple edge case
            put_strike  -= _sinc
        if call_strike <= spot:
            call_strike += _sinc

        # Pricing — unchanged; derived from live spot + front_iv per ticker
        put_mid    = round(spot * front_iv * _T**0.5 * 0.85, 2)
        put_bid    = round(put_mid * 0.93, 2)
        put_ask    = round(put_mid * 1.07, 2)
        put_spread = round((put_ask - put_bid) / put_mid, 4) if put_mid > 0 else 0.20
        call_mid   = round(spot * front_iv * _T**0.5 * 0.40, 2)
        call_bid   = round(call_mid * 0.88, 2)
        call_ask   = round(call_mid * 1.12, 2)
        call_spread = round((call_ask - call_bid) / call_mid, 4) if call_mid > 0 else 0.25

        # Black-Scholes greeks — computed live from spot + front_iv (vary per ticker/date)
        _cd1, _cd2 = _bs_d1d2(spot, call_strike, front_iv, _T)
        _pd1, _pd2 = _bs_d1d2(spot, put_strike,  front_iv, _T)
        _sv         = max(spot * front_iv * _math.sqrt(_T), 1e-9)
        call_delta_bs        = round(_N(_cd1), 4)
        call_probability_itm = round(_N(_cd2), 4)        # prob call expires ITM
        call_gamma_bs        = round(_npdf(_cd1) / _sv, 6)
        call_theta_bs        = round(-(spot * front_iv * _npdf(_cd1)) / (2.0 * _math.sqrt(_T) * 365), 4)
        call_vega_bs         = round(spot * _math.sqrt(_T) * _npdf(_cd1) / 100.0, 4)
        put_delta_bs         = round(_N(_pd1) - 1.0, 4)  # put delta (negative)
        put_probability_itm  = round(1.0 - _N(_pd1), 4)  # prob put expires ITM
        put_gamma_bs         = round(_npdf(_pd1) / _sv, 6)
        put_theta_bs         = round(-(spot * front_iv * _npdf(_pd1)) / (2.0 * _math.sqrt(_T) * 365), 4)
        put_vega_bs          = round(spot * _math.sqrt(_T) * _npdf(_pd1) / 100.0, 4)

        # Live Tradier options chain: volume + OI for target strikes
        # Also refines delta and probability_itm when greeks are available.
        # Fallback on any exception: volume=0, OI=0, BS greeks retained.
        call_vol, call_oi = 0, 0
        put_vol,  put_oi  = 0, 0
        try:
            _tok = "".join(os.environ.get("TRADIER_API_TOKEN_2",
                           os.environ.get("TRADIER_API_TOKEN", "")).split())
            if not _tok:
                raise ValueError("no Tradier token")
            _exp = scan_date + timedelta(days=13)
            while _exp.weekday() != 4:          # walk forward to nearest Friday
                _exp += timedelta(days=1)
            _url = (
                f"https://api.tradier.com/v1/markets/options/chains"
                f"?symbol={ticker}&expiration={_exp.strftime('%Y-%m-%d')}&greeks=true"
            )
            _req = urllib.request.Request(
                _url,
                headers={"Authorization": f"Bearer {_tok}", "Accept": "application/json"},
            )
            with urllib.request.urlopen(_req, timeout=8) as _resp:
                _raw = json.loads(_resp.read())
            _opts = (_raw.get("options") or {}).get("option") or []
            if isinstance(_opts, dict):
                _opts = [_opts]
            for _o in _opts:
                _sk  = float(_o.get("strike") or 0)
                _typ = _o.get("option_type", "")
                _grk = _o.get("greeks") or {}
                if _typ == "call" and abs(_sk - call_strike) < 7.5:
                    call_vol = int(_o.get("volume") or 0)
                    call_oi  = int(_o.get("open_interest") or 0)
                    _cb, _ca = _o.get("bid"), _o.get("ask")
                    if _cb is not None and _ca is not None and float(_cb) > 0 and float(_ca) > 0:
                        call_mid = round((float(_cb) + float(_ca)) / 2, 2)
                    if _grk.get("delta") is not None:
                        call_delta_bs        = round(abs(float(_grk["delta"])), 4)
                        call_probability_itm = call_delta_bs
                elif _typ == "put" and abs(_sk - put_strike) < 7.5:
                    put_vol = int(_o.get("volume") or 0)
                    put_oi  = int(_o.get("open_interest") or 0)
                    _pb, _pa = _o.get("bid"), _o.get("ask")
                    if _pb is not None and _pa is not None and float(_pb) > 0 and float(_pa) > 0:
                        put_mid = round((float(_pb) + float(_pa)) / 2, 2)
                    if _grk.get("delta") is not None:
                        put_delta_bs        = round(float(_grk["delta"]), 4)
                        put_probability_itm = round(abs(float(_grk["delta"])), 4)
            log.info(
                f"[exec] [{trace_id}] Tradier chain expiry={_exp} "
                f"call δ={call_delta_bs} vol={call_vol} oi={call_oi} call_mid={call_mid}  "
                f"put δ={put_delta_bs} vol={put_vol} oi={put_oi} put_mid={put_mid}"
            )
        except Exception as _trd_e:
            log.warning(
                f"[exec] [{trace_id}] Tradier chain skipped (BS greeks active): {_trd_e}"
            )

        base_fields = {
            **stock_data,
            "expected_move":        em_result["expected_move"],
            "expected_move_pct":    em_result["expected_move_pct"],
            "dte":                  _dte,
            "spot_at_alert":        spot,
        }

        # ── Lognormal expected-value via payoff.py (replaces hardcoded 0.60/0.85) ──
        _call_expected_return = 0.60
        _put_expected_return  = 0.85
        try:
            from aiem_strat_engine.payoff import expected_value as _pyoff_ev
            _pf_prices    = [spot * (0.5 + 0.01 * i) for i in range(151)]
            _call_payoffs = [max(0.0, p - call_strike) * 100 - call_mid * 100
                             for p in _pf_prices]
            _put_payoffs  = [max(0.0, put_strike - p)  * 100 - put_mid  * 100
                             for p in _pf_prices]
            _call_ev_raw = _pyoff_ev(_call_payoffs, _pf_prices, spot, front_iv,       _dte)
            _put_ev_raw  = _pyoff_ev(_put_payoffs,  _pf_prices, spot, front_iv * 1.05, _dte)
            if call_mid > 0:
                _call_expected_return = round(
                    max(-1.0, min(3.0, _call_ev_raw / (call_mid * 100))), 4)
            if put_mid > 0:
                _put_expected_return = round(
                    max(-1.0, min(3.0, _put_ev_raw  / (put_mid  * 100))), 4)
        except Exception as _ev_e:
            import traceback as _ev_tb
            log.warning(
                f"[EV] lognormal EV skipped, using heuristic fallback "
                f"ticker={ticker} spot={spot} front_iv={front_iv} _dte={_dte} "
                f"call_mid={call_mid} put_mid={put_mid} "
                f"call_strike={call_strike} put_strike={put_strike} "
                f"exception={type(_ev_e).__name__}: {_ev_e}\n"
                + _ev_tb.format_exc()
            )

        call_data = {
            **base_fields,
            "delta":               call_delta_bs,
            "gamma":               call_gamma_bs,
            "theta":               call_theta_bs,
            "vega":                call_vega_bs,
            "iv":                  front_iv,
            "volume":              call_vol,
            "open_interest":       call_oi,
            "bid":                 call_bid, "ask": call_ask,
            "bid_ask_spread_pct":  call_spread,
            "breakeven":           call_strike + (call_bid + call_ask) / 2,
            "premium_at_risk":     round((call_bid + call_ask) / 2 * 100, 2),
            "probability_estimate":call_probability_itm,
            "expected_return":     _call_expected_return,
            "slippage_pct":        round(call_spread * 0.5, 4),
            "entry_premium_lo":    call_bid, "entry_premium_hi": call_ask,
            "profit_target":       round((call_bid + call_ask) * 0.8, 2),
            "stop_level":          f"Close above ${call_strike + 3:.0f}",
        }
        put_data = {
            **base_fields,
            "delta":               put_delta_bs,
            "gamma":               put_gamma_bs,
            "theta":               put_theta_bs,
            "vega":                put_vega_bs,
            "iv":                  front_iv * 1.05,
            "volume":              put_vol,
            "open_interest":       put_oi,
            "bid":                 put_bid, "ask": put_ask,
            "bid_ask_spread_pct":  put_spread,
            "breakeven":           put_strike - (put_bid + put_ask) / 2,
            "premium_at_risk":     round((put_bid + put_ask) / 2 * 100, 2),
            "probability_estimate":put_probability_itm,
            "expected_return":     _put_expected_return,
            "slippage_pct":        round(put_spread * 0.5, 4),
            "entry_premium_lo":    put_bid, "entry_premium_hi": put_ask,
            "profit_target":       round((put_bid + put_ask) * 0.8, 2),
            "stop_level":          f"Close above ${spot + 5:.0f}",
        }

        # ── Stage EI-Post4: EI assessment using BS/Tradier legs ───────────────
        # When chain_strategies is empty (Polygon returns zero-quote contracts
        # outside market hours), construct synthetic single-leg strategies from
        # call_data/put_data and run EI on them so aiem_execution_assessments
        # always has at least BS-based fill_prob/liquidity_score entries.
        if not _ei_assessments:
            try:
                import aiem_execution_intelligence as _ei_mod2
                def _make_synth_leg(d: dict, ctype: str) -> dict:
                    m = (float(d.get("bid", 0) or 0) + float(d.get("ask", 0) or 0)) / 2
                    return {
                        "action":            "BUY",
                        "contract_type":     ctype,
                        "strike":            float(d.get("strike", 0.0) or 0.0),
                        "expiration_date":   "",
                        "dte":               int(d.get("dte", _dte) or _dte),
                        "bid":               float(d.get("bid", 0.0) or 0.0),
                        "ask":               float(d.get("ask", 0.0) or 0.0),
                        "mid":               round(m or float(d.get("bid", 0.0) or 0.0), 4),
                        "implied_volatility":float(d.get("iv", front_iv) or front_iv),
                        "delta":             float(abs(d.get("delta", 0.0) or 0.0)),
                        "volume":            int(d.get("volume", 0) or 0),
                        "open_interest":     int(d.get("open_interest", 0) or 0),
                        "bid_ask_spread_pct":float(d.get("bid_ask_spread_pct", 0.25) or 0.25),
                        "bid_size": 0, "ask_size": 0,
                    }
                # ev_after_costs must be in DOLLARS for R8 gate (cost/|edge|).
                # _call_expected_return is a dimensionless ratio = _call_ev_raw/(call_mid*100),
                # so the inverse gives the original EV in dollars: ratio × mid × 100.
                _call_ev_dollars = float(_call_expected_return) * call_mid * 100
                _put_ev_dollars  = float(_put_expected_return)  * put_mid  * 100
                _synth = [
                    {"strategy": "LONG_CALL", "direction": "BULLISH",
                     "ev_after_costs": _call_ev_dollars,
                     "liquid": call_vol > 50 and call_oi > 100,
                     "legs": [_make_synth_leg(call_data, "call")]},
                    {"strategy": "LONG_PUT",  "direction": "BEARISH",
                     "ev_after_costs": _put_ev_dollars,
                     "liquid": put_vol > 50 and put_oi > 100,
                     "legs": [_make_synth_leg(put_data, "put")]},
                ]
                _, _ei_assessments = _ei_mod2.filter_strategies_by_execution(
                    _synth, trace_id=trace_id, scan_date=scan_date,
                    ticker=ticker, spot=spot, db_url=_DB_URL,
                )
                log.info(
                    f"[exec] [{trace_id}] EI-post4 (synthetic BS legs): "
                    f"{len(_ei_assessments)} assessments written"
                )
            except Exception as _ei_p4_e:
                log.warning(f"[exec] [{trace_id}] EI-post4 skipped: {_ei_p4_e}")

        # ── REGISTRY: Stage 4 — BS greeks + Probability/EV + Risk Gate ────────
        if _reg_ready:
            # Probability/EV engine subsystem (Black-Scholes)
            _rc("BS", "BS_CALL_DELTA",    call_delta_bs, abs(call_delta_bs),
                "BULLISH" if call_delta_bs > 0.4 else "NEUTRAL")
            _rc("BS", "BS_CALL_GAMMA",    call_gamma_bs, None, "NEUTRAL")
            _rc("BS", "BS_CALL_THETA",    call_theta_bs, None,
                "BEARISH" if (call_theta_bs or 0) < -0.05 else "NEUTRAL")
            _rc("BS", "BS_CALL_VEGA",     call_vega_bs,  None, "NEUTRAL")
            _rc("BS", "BS_CALL_POP",      call_probability_itm, call_probability_itm,
                "BULLISH" if call_probability_itm >= 0.35 else "BEARISH")
            _rc("BS", "BS_CALL_VOLUME",   call_vol,   None,
                "BULLISH" if call_vol > 100 else "NEUTRAL")
            _rc("BS", "BS_CALL_OI",       call_oi,    None, "NEUTRAL")
            _rc("BS", "BS_CALL_SPREAD",   call_spread, None,
                "BEARISH" if call_spread > 0.15 else "NEUTRAL")
            _rc("BS", "BS_PUT_DELTA",     put_delta_bs,  abs(put_delta_bs),
                "BEARISH" if abs(put_delta_bs) > 0.4 else "NEUTRAL")
            _rc("BS", "BS_PUT_GAMMA",     put_gamma_bs,  None, "NEUTRAL")
            _rc("BS", "BS_PUT_THETA",     put_theta_bs,  None,
                "BEARISH" if (put_theta_bs or 0) < -0.05 else "NEUTRAL")
            _rc("BS", "BS_PUT_VEGA",      put_vega_bs,   None, "NEUTRAL")
            _rc("BS", "BS_PUT_POP",       put_probability_itm, put_probability_itm,
                "BEARISH" if put_probability_itm >= 0.35 else "NEUTRAL")
            _rc("BS", "BS_PUT_VOLUME",    put_vol,   None,
                "BEARISH" if put_vol > 100 else "NEUTRAL")
            _rc("BS", "BS_PUT_OI",        put_oi,    None, "NEUTRAL")
            _rc("BS", "BS_PUT_SPREAD",    put_spread, None,
                "BEARISH" if put_spread > 0.15 else "NEUTRAL")
            # Position-sizing engine subsystem
            _rc("SIZE", "SIZE_CALL_PREMIUM_AT_RISK",
                call_data.get("premium_at_risk"), None, "NEUTRAL")
            _rc("SIZE", "SIZE_PUT_PREMIUM_AT_RISK",
                put_data.get("premium_at_risk"), None, "NEUTRAL")
            _rc("SIZE", "SIZE_CALL_SLIPPAGE",
                call_data.get("slippage_pct"), None,
                "BEARISH" if (call_data.get("slippage_pct") or 0) > 0.10 else "NEUTRAL")
            _rc("SIZE", "SIZE_PUT_SLIPPAGE",
                put_data.get("slippage_pct"), None,
                "BEARISH" if (put_data.get("slippage_pct") or 0) > 0.10 else "NEUTRAL")
            log.debug(f"[registry] stage4 snapped 20 indicators trace_id={trace_id}")
            # Options metrics capture — full chain snapshot for CALL and PUT
            try:
                _reg_mod.capture_options_metrics(
                    trace_id, ticker, scan_date, "CALL",
                    {**call_data, "_data_source": "BS_TRADIER", "iv_rank": iv_rank * 100},
                    _reg_db)
                _reg_mod.capture_options_metrics(
                    trace_id, ticker, scan_date, "PUT",
                    {**put_data, "_data_source": "BS_TRADIER", "iv_rank": iv_rank * 100},
                    _reg_db)
                # Enrich with OSS fields (same for both directions)
                _reg_mod.enrich_metrics_oss(
                    trace_id,
                    pc_skew_pp=pc_skew_pp, pc_skew_tag=pc_skew_tag,
                    term_ratio=float(oss[7]) if oss[7] is not None else None,
                    term_tag=term_tag,
                    front_iv=front_iv,
                    back_iv=float(oss[9])/100.0 if oss[9] is not None else None,
                    gex_m=float(oss[2]) if oss[2] is not None else None,
                    gex_regime=gex_regime,
                    gamma_flip_price=float(oss[4]) if oss[4] is not None else None,
                    iv_rank=iv_rank * 100,
                    db_url=_reg_db,
                )
                log.debug(f"[registry] options_metrics captured CALL+PUT trace_id={trace_id}")
            except Exception as _omc_e:
                log.debug(f"[registry] options_metrics capture skipped: {_omc_e}")

        verify_result = _oi.verify_options_decision_inputs(ticker, call_data, put_data)

        # ── REGISTRY: Failure tests (Phase III Phase 1) ───────────────────────
        # These are the ONLY registry calls that can block the pipeline.
        # On failure: inject into verify_result → ready_for_decision=False →
        # existing gate raises ValueError → job marked FAILED.
        # Three tests: missing-indicator, pattern-scan-incomplete, stale-data.
        if _reg_ready:
            _REQUIRED_IDS = [
                "POLY_CLOSE_PRICE", "POLY_VWAP", "OSS_FRONT_IV", "OSS_GEX_REGIME",
                "OPT_IV_RANK", "BS_CALL_DELTA", "BS_PUT_DELTA",
                "BS_CALL_POP", "BS_PUT_POP",
            ]
            _CRITICAL_FRESHNESS_IDS = ["POLY_CLOSE_PRICE", "OSS_FRONT_IV"]
            _reg_gate_failures: list = []
            try:
                _reg_mod.assert_no_missing_indicators(trace_id, _REQUIRED_IDS, _reg_db)
            except _reg_mod.RegistryValidationError as _rve:
                _reg_gate_failures.append(f"REGISTRY_MISSING_INDICATOR: {_rve}")
                log.error(f"[exec] [{trace_id}] REGISTRY GATE: {_rve}")
            try:
                _reg_mod.assert_pattern_scan_complete(trace_id, _reg_db)
            except _reg_mod.RegistryValidationError as _rpve:
                _reg_gate_failures.append(f"REGISTRY_PATTERN_INCOMPLETE: {_rpve}")
                log.error(f"[exec] [{trace_id}] REGISTRY GATE: {_rpve}")
            # Weekend-aware freshness threshold.
            # Friday close → Monday 9:45 AM is ~65-68h (>48h flat threshold).
            # 3-day holiday weekend can reach ~89h. Use 96h (345600s) on Mon/Tue
            # to cover all post-weekend cases; 48h (172800s) mid-week is fine.
            _dow_now = datetime.now(_ET).weekday()
            _freshness_secs = 345600 if _dow_now <= 1 else 172800  # Mon/Tue=96h, else 48h
            try:
                _reg_mod.assert_data_freshness(trace_id, _CRITICAL_FRESHNESS_IDS,
                                               _freshness_secs, _reg_db)
            except _reg_mod.RegistryValidationError as _rfve:
                _reg_gate_failures.append(f"REGISTRY_STALE_DATA: {_rfve}")
                log.error(f"[exec] [{trace_id}] REGISTRY GATE: {_rfve}")
            if _reg_gate_failures:
                _rf_text = "; ".join(_reg_gate_failures)
                verify_result["gate_failures"] = (
                    (verify_result.get("gate_failures") or []) +
                    [f"REGISTRY: {f}" for f in _reg_gate_failures]
                )
                verify_result["call_eligible"]      = False
                verify_result["put_eligible"]       = False
                verify_result["ready_for_decision"] = False
                verify_result["verdict"]            = f"REGISTRY VALIDATION FAILED — {_rf_text}"
                log.error(
                    f"[exec] [{trace_id}] REGISTRY VALIDATION BLOCKED PIPELINE: {_rf_text}")
            else:
                log.debug(f"[exec] [{trace_id}] registry failure tests: all 3 PASS")

        if "error" in verify_result:
            raise ValueError(f"verify_options_decision_inputs: {verify_result['error']}")
        if not verify_result.get("ready_for_decision"):
            raise ValueError(f"not ready_for_decision: {verify_result.get('verdict')}")

        # ── Stage 5: REQ6 scoring ──────────────────────────────────────────────
        call_scoring = _pipe.compute_req6_score(call_data, "CALL", stock_data, iv_rank, verify_result)
        put_scoring  = _pipe.compute_req6_score(put_data,  "PUT",  stock_data, iv_rank, verify_result)
        call_score   = call_scoring["score"]
        put_score    = put_scoring["score"]
        margin       = abs(call_score - put_score)

        # ── REGISTRY: Stage 5 — REQ6 scoring (Recommendation engine inputs) ───
        if _reg_ready:
            _rc("REQ6", "REQ6_CALL_SCORE",  call_score, call_score/100.0,
                "BULLISH" if call_score >= 55 else "BEARISH")
            _rc("REQ6", "REQ6_PUT_SCORE",   put_score,  put_score/100.0,
                "BEARISH" if put_score >= 55 else "NEUTRAL")
            _rc("REQ6", "REQ6_MARGIN",      margin, margin/100.0,
                "BULLISH" if margin >= 10 else "NEUTRAL")
            # Capture each of the 12 dimension scores (from call_scoring / put_scoring)
            for _dim_name, _dim_val in (call_scoring.get("dimensions") or {}).items():
                _d_cid = f"REQ6_CALL_{str(_dim_name).upper().replace(' ','_')[:30]}"
                _rc("REQ6", _d_cid, float(_dim_val) if _dim_val is not None else None,
                    None, "NEUTRAL")
            for _dim_name, _dim_val in (put_scoring.get("dimensions") or {}).items():
                _d_cid = f"REQ6_PUT_{str(_dim_name).upper().replace(' ','_')[:30]}"
                _rc("REQ6", _d_cid, float(_dim_val) if _dim_val is not None else None,
                    None, "NEUTRAL")
            log.debug(f"[registry] stage5 REQ6 snapped trace_id={trace_id}")

        # ── Stage 6: Decision ──────────────────────────────────────────────────
        # DETERMINISTIC TIE-BREAKING (Item 8):
        # call_score >= put_score → LONG_CALL (>= gives CALL precedence on exact tie).
        # put_score > call_score (strict) → LONG_PUT.
        # Both require score >= 55 AND margin >= 10; otherwise → NO_TRADE.
        # Scores are round(x,1) from compute_req6_score — no float ambiguity.
        # Identical inputs always produce identical scores → identical direction.
        if call_score >= put_score and call_score >= 55 and margin >= 10:
            direction = "LONG_CALL"
        elif put_score > call_score and put_score >= 55 and margin >= 10:
            direction = "LONG_PUT"
        else:
            direction = "NO_TRADE"

        # ── REGISTRY: Stage 6 — Decision (Recommendation engine) ────────────────
        if _reg_ready:
            _rc("DECISION", "DECISION_DIRECTION", None, None,
                "BULLISH" if direction == "LONG_CALL" else
                "BEARISH" if direction == "LONG_PUT" else "NEUTRAL",
                txt=direction)
            _rc("DECISION", "DECISION_CALL_SCORE",  call_score, call_score/100.0,
                "BULLISH" if call_score >= 55 else "NEUTRAL")
            _rc("DECISION", "DECISION_PUT_SCORE",   put_score,  put_score/100.0,
                "BEARISH" if put_score >= 55 else "NEUTRAL")
            _rc("DECISION", "DECISION_MARGIN",      margin, margin/100.0,
                "BULLISH" if margin >= 10 else "NEUTRAL")
            log.debug(f"[registry] stage6 snapped direction={direction} trace_id={trace_id}")

        # ── Trace: DECISION ────────────────────────────────────────────────────
        if _strace_ctx is not None:
            try:
                _strace_ctx.write_stage(
                    "DECISION",
                    ticker=ticker, scan_date=scan_date, job_id=job_id,
                    completion_status=direction,
                    metadata={
                        "direction":  direction,
                        "call_score": float(call_score),
                        "put_score":  float(put_score),
                        "margin":     round(float(margin), 1),
                    },
                )
            except Exception as _st_dec_e:
                log.debug(f"[scheduler_trace] DECISION: {_st_dec_e}")

        # ── Phase 2: Strategy candidates (all strategies considered this run) ───
        # Captured here (after Stage 6) so that direction, call_data, and
        # put_data are all fully resolved — not at Stage EI where they are
        # still undefined.
        if _p2_ready:
            try:
                _p2.capture_strategy_candidates(
                    trace_id=trace_id,
                    ticker=ticker,
                    scan_date=scan_date,
                    chain_strategies=chain_strategies,
                    ei_assessments=_ei_assessments,
                    call_data=call_data,
                    put_data=put_data,
                    call_score=call_score,
                    put_score=put_score,
                    selected_direction=direction,
                    db_url=_DB_URL,
                )
            except Exception as _sc_e:
                log.debug(f"[phase2] strategy candidate capture skipped: {_sc_e}")

        # ── Phase 2: Decision record (captures NO_TRADE, APPROVE, SUBSTITUTE) ─
        if _p2_ready:
            try:
                _p2.capture_decision_record(
                    trace_id=trace_id, ticker=ticker, scan_date=scan_date,
                    direction=direction,
                    call_score=call_score, put_score=put_score, margin=margin,
                    call_scoring=call_scoring, put_scoring=put_scoring,
                    verify_result=verify_result,
                    chain_strategies=chain_strategies,
                    stock_data=stock_data,
                    execution_plan_id=str(job_id),
                    db_url=_DB_URL,
                )
            except Exception as _dr_e:
                log.debug(f"[phase2] decision_record capture skipped: {_dr_e}")

        # ── Phase 4: portfolio context snapshot at decision time ──────────────
        if _p4_ready:
            try:
                _p4.capture_portfolio_context(
                    alert_id=None, trace_id=trace_id,
                    ticker=ticker, scan_date=scan_date,
                    db_url=_DB_URL,
                )
            except Exception as _p4_pc_e:
                log.debug(f"[phase4] capture_portfolio_context skipped: {_p4_pc_e}")

        if direction == "NO_TRADE":
            with _pg2.connect(_DB_URL, connect_timeout=4) as conn, conn.cursor() as cur:
                _nt_prev_hash = _get_prev_chain_hash(conn)
                _nt_chain_hash = _compute_chain_hash(
                    job_id, ticker, scan_date, trace_id, "NO_TRADE", _nt_prev_hash)
                cur.execute("""
                    UPDATE options_pipeline_jobs
                    SET status='DONE', completed_at=NOW(),
                        direction='NO_TRADE', selected_score=%s,
                        chain_hash=%s,
                        error_text='NO_TRADE: neither direction meets score+margin gates'
                    WHERE id=%s
                """, (max(call_score, put_score), _nt_chain_hash, job_id))
                conn.commit()
            log.info(f"[exec] job_id={job_id} {ticker} → NO_TRADE "
                     f"call={call_score}  put={put_score}  margin={round(margin,1)} "
                     f"chain={_nt_chain_hash[:16]}")
            _write_heartbeat(True)
            # ── Phase 3: root-cause + KB entry for NO_TRADE decisions ─────────
            if _p3_ready:
                try:
                    _p3.record_root_cause(
                        alert_id=0,
                        outcome_type="NO_TRADE",
                        trace_id=trace_id,
                        ticker=ticker,
                        scan_date=scan_date,
                        direction="NO_TRADE",
                        scoring_data={"call_score": call_score, "put_score": put_score,
                                      "margin": round(margin, 1)},
                        verify_data=locals().get("verify_result", {}),
                        stock_data=locals().get("stock_data", {}),
                        db_url=_DB_URL,
                    )
                except Exception as _p3_nt_rc_e:
                    log.warning(f"[phase3] no_trade root_cause failed: {_p3_nt_rc_e}")
                try:
                    _p3.add_knowledge_base_entry(
                        kb_type="SUCCESS_NO_TRADE",
                        ticker=ticker,
                        scan_date=scan_date,
                        fingerprint={"call_score": call_score, "put_score": put_score,
                                     "margin": round(margin, 1),
                                     "trace_id": trace_id},
                        decision_quality="GOOD",
                        trace_id=trace_id,
                        db_url=_DB_URL,
                    )
                except Exception as _p3_nt_kb_e:
                    log.warning(f"[phase3] no_trade kb_entry failed: {_p3_nt_kb_e}")
            # ── Phase 4: record NO_TRADE candidate for outcome tracking ─────────
            if _p4_ready:
                try:
                    _p4.record_no_trade_candidate(
                        job_id=job_id, trace_id=trace_id,
                        ticker=ticker, scan_date=scan_date,
                        call_score=float(call_score),
                        put_score=float(put_score),
                        rejection_reasons=[
                            "NO_TRADE: neither direction meets score+margin gates",
                            f"call_score={call_score} put_score={put_score} "
                            f"margin={round(margin, 1)}",
                        ],
                        market_snapshot={
                            "call_score": float(call_score),
                            "put_score":  float(put_score),
                            "margin":     round(float(margin), 1),
                            "trace_id":   trace_id,
                        },
                        spot_at_rejection=None,
                        db_url=_DB_URL,
                    )
                except Exception as _p4_nt_e:
                    log.warning(f"[phase4] record_no_trade_candidate failed: {_p4_nt_e}")
            # ── DPL Phase 2: NO_TRADE decision capture ────────────────────────
            if _dpl_ready:
                try:
                    _dpl_ctx_nt = _dpl.assemble_dpl_context(
                        ticker=ticker, scan_date=scan_date, trace_id=trace_id,
                        direction="NO_TRADE",
                        stock_data=locals().get("stock_data", {}),
                        verify_result=locals().get("verify_result", {}),
                        chain_strategies=locals().get("chain_strategies", []),
                        pm_intel=locals().get("pm_intel", {}),
                        mtf_result=locals().get("mtf_result", {}),
                        pattern_result=locals().get("pattern_result", {}),
                        call_score=call_score, put_score=put_score,
                        db_url=_DB_URL,
                    )
                    _dpl_nt_result = _dpl.write_decision(
                        input_data={"ticker": ticker, "trace_id": trace_id,
                                    "call_score": float(call_score),
                                    "put_score":  float(put_score)},
                        output_data={"direction": "NO_TRADE",
                                     "chain_hash": _nt_chain_hash,
                                     "trace_id":   trace_id},
                        context=_dpl_ctx_nt,
                        is_test_record=False,
                        db_url=_DB_URL,
                    )
                    log.info(f"[dpl] NO_TRADE decision written trace_id={trace_id}")
                    # Stage 11: DECISION_WRITTEN — confirmed write to oe_decision_audit
                    try:
                        if _chkp and _pipeline_tid:
                            _chkp.chk(_pipeline_tid, "DECISION_WRITTEN",
                                       {"ticker": ticker, "direction": "NO_TRADE",
                                        "decision_id": _dpl_nt_result.get("decision_id", "")[:16]},
                                       _DB_URL)
                    except Exception as _s11nt_e:
                        log.warning(f"[exec] checkpoint DECISION_WRITTEN NO_TRADE failed: {_s11nt_e}")
                    # ── DPL Phase 3: Replay inputs capture ─────────────────
                    try:
                        _dpl.capture_replay_inputs(
                            decision_id=_dpl_nt_result["decision_id"],
                            direction="NO_TRADE",
                            call_score=float(call_score),
                            put_score=float(put_score),
                            call_data=call_data,
                            put_data=put_data,
                            stock_data=locals().get("stock_data", {}),
                            verify_result=locals().get("verify_result", {}),
                            iv_rank=iv_rank,
                            alert_id=None,
                            origin_type="SCHEDULER",
                            scheduler_job_id=job_id,
                            worker_pid=os.getpid(),
                            db_url=_DB_URL,
                        )
                        # ── DPL Phase 3: Post-capture replay check ──────────
                        # POST-DECISION DETECTOR ONLY — NOT a pre-trade gate.
                        # The decision is already committed before this runs.
                        # This does NOT satisfy any pre-trade blocking requirement.
                        try:
                            _rpl_nt = _dpl.replay_decision(
                                _dpl_nt_result["decision_id"]
                            )
                            if not _rpl_nt["full_match"]:
                                _mm_nt = (
                                    f"[DPL MISMATCH] NO_TRADE "
                                    f"decision_id={_dpl_nt_result['decision_id'][:16]} "
                                    f"call_match={_rpl_nt['call_match']} "
                                    f"put_match={_rpl_nt['put_match']} "
                                    f"dir_match={_rpl_nt['direction_match']}"
                                )
                                log.critical(_mm_nt)
                                _tg(_mm_nt)
                        except _dpl.ReplayCodeDriftError as _rce_nt:
                            _dm_nt = (
                                f"[DPL CODE_DRIFT] NO_TRADE "
                                f"decision_id={_dpl_nt_result['decision_id'][:16]}: {_rce_nt}"
                            )
                            log.critical(_dm_nt)
                            _tg(_dm_nt)
                            try:
                                with psycopg2.connect(_DB_URL, connect_timeout=4) as _dc_nt,                                      _dc_nt.cursor() as _du_nt:
                                    _vs_nt = "WEIGHTS_DRIFT" if "WEIGHTS_DRIFT" in str(_rce_nt) else "CODE_DRIFT"
                                    _du_nt.execute(
                                        "UPDATE oe_decision_audit "
                                        "SET verification_status=%s "
                                        "WHERE decision_id=%s",
                                        (_vs_nt, _dpl_nt_result["decision_id"],)
                                    )
                            except Exception as _dbu_nt:
                                log.warning(f"[dpl] drift status update failed: {_dbu_nt}")
                        except Exception as _re_nt:
                            _re_msg_nt = (
                                f"[DPL REPLAY_ERROR] NO_TRADE "
                                f"decision_id={_dpl_nt_result['decision_id'][:16]}: {_re_nt}"
                            )
                            log.critical(_re_msg_nt)
                            _tg(_re_msg_nt)
                            try:
                                with psycopg2.connect(_DB_URL, connect_timeout=4) as _dc_re_nt, \
                                     _dc_re_nt.cursor() as _du_re_nt:
                                    _du_re_nt.execute(
                                        "UPDATE oe_decision_audit "
                                        "SET verification_status='REPLAY_ERROR' "
                                        "WHERE decision_id=%s",
                                        (_dpl_nt_result["decision_id"],)
                                    )
                            except Exception as _dbu_re_nt:
                                log.warning(f"[dpl] REPLAY_ERROR status update failed: {_dbu_re_nt}")
                    except Exception as _p3_nt_e:
                        # Item 14: NO new decision may become unreplayable.
                        # Register in oe_unreplayable_rows then re-raise.
                        log.critical(
                            f"[dpl][REPLAY_BLOCK] capture_replay_inputs NO_TRADE failed "
                            f"trace_id={trace_id} decision_id={_dpl_nt_result.get('decision_id','?')}: {_p3_nt_e}"
                        )
                        try:
                            with psycopg2.connect(_DB_URL, connect_timeout=4) as _rreg_nt, \
                                 _rreg_nt.cursor() as _rreg_nt_c:
                                _rreg_nt_c.execute(
                                    "INSERT INTO oe_unreplayable_rows "
                                    "(decision_id, reason_code, recoverable, is_test_record) "
                                    "VALUES (%s, 'REPLAY_ERROR', FALSE, FALSE) "
                                    "ON CONFLICT (decision_id) DO NOTHING",
                                    (_dpl_nt_result.get('decision_id'),)
                                )
                        except Exception as _rreg_nt_e:
                            log.warning(f"[dpl] oe_unreplayable_rows insert failed: {_rreg_nt_e}")
                        raise RuntimeError(
                            f"[REPLAY_BLOCK] NO_TRADE replay capture failed — "
                            f"decision_id={_dpl_nt_result.get('decision_id','?')}: {_p3_nt_e}"
                        ) from _p3_nt_e
                except Exception as _dpl_nt_e:
                    log.warning(f"[dpl] write_decision NO_TRADE failed: {_dpl_nt_e}")

            # ── Trace: PAPER_EXECUTION_OR_NO_TRADE (NO_TRADE path) ────────────
            if _strace_ctx is not None:
                try:
                    _strace_ctx.write_stage(
                        "PAPER_EXECUTION_OR_NO_TRADE",
                        ticker=ticker, scan_date=scan_date, job_id=job_id,
                        completion_status="NO_TRADE",
                        metadata={
                            "direction": "NO_TRADE",
                            "call_score": float(call_score),
                            "put_score": float(put_score),
                            "chain_hash": _nt_chain_hash[:24] if _nt_chain_hash else None,
                        },
                    )
                    _strace_ctx.write_stage(
                        "OUTCOME_TRACKING",
                        ticker=ticker, scan_date=scan_date, job_id=job_id,
                        completion_status="WIRED",
                        metadata={"p3_ready": _p3_ready, "p4_ready": _p4_ready},
                    )
                except Exception as _st_pent_e:
                    log.debug(f"[scheduler_trace] PAPER_EXECUTION_NO_TRADE: {_st_pent_e}")

            return {"job_id": job_id, "ticker": ticker, "direction": "NO_TRADE",
                    "call_score": call_score, "put_score": put_score,
                    "trace_id": trace_id, "chain_hash": _nt_chain_hash}

        # ── Stage 7: Alert fields ──────────────────────────────────────────────
        sel_data  = put_data   if direction == "LONG_PUT"  else call_data
        sel_score = put_score  if direction == "LONG_PUT"  else call_score
        opp_score = call_score if direction == "LONG_PUT"  else put_score
        sel_strike = put_strike if direction == "LONG_PUT" else call_strike
        expiry_str = (date.today() + timedelta(days=9)).isoformat()

        alert_fields = {
            "ticker":              ticker,
            "direction":           "BEARISH" if direction == "LONG_PUT" else "BULLISH",
            "strike":              sel_strike,
            "expiry":              expiry_str,
            "dte":                 9,
            "entry_premium_lo":    sel_data["bid"],
            "entry_premium_hi":    sel_data["ask"],
            "spot_at_alert":       spot,
            "delta":               sel_data["delta"],
            "gamma":               sel_data["gamma"],
            "theta":               sel_data["theta"],
            "vega":                sel_data["vega"],
            "iv":                  sel_data["iv"],
            "volume":              sel_data["volume"],
            "open_interest":       sel_data["open_interest"],
            "bid":                 sel_data["bid"],
            "ask":                 sel_data["ask"],
            "bid_ask_spread_pct":  sel_data["bid_ask_spread_pct"],
            "expected_move":       em_result["expected_move"],
            "expected_move_pct":   em_result["expected_move_pct"],
            "breakeven":           sel_data["breakeven"],
            "max_premium_risk":    sel_data["premium_at_risk"],
            "probability_estimate":sel_data["probability_estimate"],
            "expected_return":     sel_data["expected_return"],
            "profit_target":       sel_data["profit_target"],
            "stop_level":          sel_data["stop_level"],
            "selected_score":      sel_score,
            "opposite_score":      opp_score,
            "why_selected_won":    (
                f"{direction} scored {sel_score:.1f} vs opponent {opp_score:.1f} "
                f"(margin={round(margin,1)}). "
                f"skew={pc_skew_tag} regime={gex_regime} term={term_tag} "
                f"close_strength={close_str:.3f}"
            ),
            "main_risks": (
                f"IV crush (iv_rank={ivr_result['iv_rank']}); "
                f"theta decay 9 DTE; gap risk."
            ),
        }
        scoring_data = {
            "call_score": call_score, "put_score": put_score,
            "margin": round(margin, 1), "winner": direction,
            "call_scoring": call_scoring, "put_scoring": put_scoring,
        }

        # ── Engine Integrity Gate (F2/F3/F4/F6 — R11 remediation) ─────────────
        # Extracted to dpl/integrity_gate.py. Gate enforces:
        #   Step 1: refs file exists (no bypass for any environment, F4)
        #   Step 2: engine root-hash matches refs (hash-match integrity)
        #   Steps 4-5: dpl_production_certification starts APPROVED + approved_at set
        #   Step 6: approved_by in APPROVED_IDENTITIES allowlist (not blocklist, F2)
        #   Step 7: refs.commit_sha == live git HEAD at call time (F3)
        # Every exception path raises IntegrityGateError → block.
        import os as _ieg_os, sys as _ieg_sys
        _ieg_refs_path = _ieg_os.path.join(
            _ieg_os.path.dirname(_ieg_os.path.abspath(__file__)),
            'dpl', 'engine_integrity_refs.json'
        )
        _ieg_dpl_dir = _ieg_os.path.dirname(_ieg_refs_path)
        if _ieg_dpl_dir not in _ieg_sys.path:
            _ieg_sys.path.insert(0, _ieg_dpl_dir)

        def _ieg_log_block(reason: str, exc_cls: str = '', exc_detail: str = '',
                           live_hash: str = '', expected_hash: str = '') -> None:
            """Best-effort DB event log on block; never swallows the block."""
            _gate_fired[0] = True
            try:
                import psycopg2 as _pg_ieg, subprocess as _sub_ieg
                _git_sha = ''
                try:
                    _git_sha = _sub_ieg.check_output(
                        ['git', 'rev-parse', 'HEAD'], stderr=_sub_ieg.DEVNULL
                    ).decode().strip()[:40]
                except Exception:
                    pass
                _c = _pg_ieg.connect(_DB_URL, connect_timeout=3)
                with _c, _c.cursor() as _cur:
                    _cur.execute(
                        "INSERT INTO oe_gate_events "
                        "  (gate_name,ticker,trace_id,live_hash,expected_hash,"
                        "   mismatch_detail,action_taken,"
                        "   candidate_id,pipeline_job_id,git_commit,reason) "
                        "VALUES ('ENGINE_INTEGRITY',%s,%s,%s,%s,%s,'BLOCKED',%s,%s,%s,%s) "
                        "ON CONFLICT DO NOTHING",
                        (ticker, trace_id, live_hash[:64], expected_hash[:64],
                         f"reason={reason} exc={exc_cls}: {exc_detail}"[:500],
                         str(job_id), str(job_id), _git_sha,
                         (f"{exc_cls}: {exc_detail}" if exc_cls else reason)[:500]),
                    )
            except Exception as _le:
                log.warning(f"[integrity_gate] gate_event log failed (block still raised): {_le}")

        from integrity_gate import run_integrity_gate, IntegrityGateError
        try:
            _ieg_result = run_integrity_gate(
                _ieg_refs_path,
                block_fn=_ieg_log_block,
                log_fn=log.info,
            )
        except IntegrityGateError as _ieg_e:
            raise ValueError(str(_ieg_e))

        # ── Trace: RISK_GATE (integrity gate passed) ──────────────────────
        if _strace_ctx is not None:
            try:
                _strace_ctx.write_stage(
                    "RISK_GATE",
                    ticker=ticker, scan_date=scan_date, job_id=job_id,
                    completion_status="PASS",
                    metadata={
                        "gate": "ENGINE_INTEGRITY",
                        "root_hash": _ieg_result.get("live_root_hash", "")[:24],
                    },
                )
            except Exception as _st_rg_e:
                log.debug(f"[scheduler_trace] RISK_GATE: {_st_rg_e}")

        # ── Stage 8: DB persist ────────────────────────────────────────────────
        save_result = _pipe.save_options_alert(
            ticker=ticker,
            direction=direction,
            stock_data=stock_data,
            options_analysis=options_analysis,
            verify_result=verify_result,
            scoring_data=scoring_data,
            alert_fields=alert_fields,
            trace_id=trace_id,
        )
        if not save_result.get("saved"):
            raise ValueError(f"save_options_alert failed: {save_result.get('error')}")

        alert_id    = save_result["alert_id"]
        chain_sha   = save_result["audit_chain_sha256"]
        elapsed     = round(time.time() - t_start, 2)

        # ── REGISTRY: Stage 8 — Paper execution + Verification system ─────────
        if _reg_ready:
            # Back-fill alert_id on oe_options_metrics rows now that it's known
            try:
                _reg_mod.update_metrics_alert_id(trace_id, alert_id, _reg_db)
                log.debug(f"[registry] stage8 metrics alert_id={alert_id} linked trace_id={trace_id}")
            except Exception as _rsa_e:
                log.debug(f"[registry] stage8 alert_id link skipped: {_rsa_e}")
            # Scheduler / verification subsystem
            _rc("VERIFY", "VERIFY_ALERT_ID",   float(alert_id), None, "NEUTRAL",
                txt=str(alert_id))
            _rc("VERIFY", "VERIFY_CHAIN_SHA",  None, None, "NEUTRAL",
                txt=chain_sha[:24] if chain_sha else None)
            _rc("VERIFY", "VERIFY_ELAPSED_S",  elapsed, None, "NEUTRAL")

        # ── Phase 2: Counterfactual snapshot + Trade record ───────────────────
        # Stage 9: P2_GATE — write-before-work, before counterfactual capture
        try:
            if _chkp and _pipeline_tid:
                _chkp.chk(_pipeline_tid, "P2_GATE",
                           {"ticker": ticker, "direction": direction,
                            "alert_id": alert_id, "p2_ready": _p2_ready}, _DB_URL)
        except Exception as _s9e:
            log.warning(f"[exec] checkpoint P2_GATE failed: {_s9e}")
        log.info(f"[exec] [{trace_id}] [P2_GATE] _p2_ready={_p2_ready} direction={direction} alert_id={alert_id}")
        if _p2_ready:
            try:
                _p2.capture_counterfactual_snapshot(
                    alert_id=alert_id,
                    trace_id=trace_id,
                    ticker=ticker,
                    scan_date=scan_date,
                    options_chain=options_chain,
                    call_data=call_data,
                    put_data=put_data,
                    chain_strategies=chain_strategies,
                    spot=spot,
                    front_iv=front_iv,
                    db_url=_DB_URL,
                )
            except Exception as _cf_e:
                log.debug(f"[phase2] counterfactual_snapshot skipped: {_cf_e}")
            try:
                # Stage 10: P2_CAPTURE — write-before-work, before capture_trade_record
                try:
                    if _chkp and _pipeline_tid:
                        _chkp.chk(_pipeline_tid, "P2_CAPTURE",
                                   {"ticker": ticker, "alert_id": alert_id,
                                    "direction": direction}, _DB_URL)
                except Exception as _s10e:
                    log.warning(f"[exec] checkpoint P2_CAPTURE failed: {_s10e}")
                log.info(f"[exec] [{trace_id}] [P2_CAPTURE] calling capture_trade_record alert_id={alert_id} ticker={ticker}")
                _tr_result = _p2.capture_trade_record(
                    alert_id=alert_id,
                    trace_id=trace_id,
                    ticker=ticker,
                    scan_date=scan_date,
                    direction=direction,
                    sel_data=sel_data,
                    sel_strike=sel_strike,
                    alert_fields=alert_fields,
                    call_score=call_score,
                    put_score=put_score,
                    stock_data=stock_data,
                    verify_result=verify_result,
                    best_chain_strategy=best_chain_strategy,
                    call_scoring=call_scoring,
                    put_scoring=put_scoring,
                    db_url=_DB_URL,
                )
                log.info(f"[exec] [{trace_id}] [P2_CAPTURE] capture_trade_record returned tr_id={_tr_result}")
            except Exception as _tr_e:
                log.debug(f"[phase2] trade_record capture skipped: {_tr_e}")
            try:
                _p2.update_decision_alert_id(trace_id, alert_id, _DB_URL,
                                             chain_hash=chain_sha)
            except Exception as _uda_e:
                log.debug(f"[phase2] update_decision_alert_id skipped: {_uda_e}")

        # ── DPL Phase 2: TRADE decision capture ───────────────────────────────
        if _dpl_ready:
            try:
                _dpl_ctx = _dpl.assemble_dpl_context(
                    ticker=ticker, scan_date=scan_date, trace_id=trace_id,
                    direction=direction, alert_id=alert_id,
                    sel_data=sel_data, stock_data=stock_data,
                    verify_result=verify_result,
                    chain_strategies=chain_strategies,
                    best_chain_strategy=best_chain_strategy,
                    sel_strike=sel_strike, expiry_str=expiry_str,
                    alert_fields=alert_fields, pm_intel=pm_intel,
                    mtf_result=mtf_result, pattern_result=pattern_result,
                    em_result=em_result, ivr_result=ivr_result,
                    call_score=call_score, put_score=put_score,
                    db_url=_DB_URL,
                )
                _dpl_trade_result = _dpl.write_decision(
                    input_data={"ticker": ticker, "trace_id": trace_id,
                                "call_score": float(call_score),
                                "put_score":  float(put_score),
                                "direction":  direction},
                    output_data={"alert_id":   alert_id,
                                 "direction":  direction,
                                 "chain_sha":  chain_sha,
                                 "trace_id":   trace_id},
                    context=_dpl_ctx,
                    is_test_record=False,
                    db_url=_DB_URL,
                )
                log.info(
                    f"[dpl] TRADE decision written trace_id={trace_id} "
                    f"alert_id={alert_id}"
                )
                # Stage 11: DECISION_WRITTEN — confirmed write to oe_decision_audit
                try:
                    if _chkp and _pipeline_tid:
                        _chkp.chk(_pipeline_tid, "DECISION_WRITTEN",
                                   {"ticker": ticker, "direction": "TRADE",
                                    "alert_id": alert_id,
                                    "decision_id": _dpl_trade_result.get("decision_id", "")[:16]},
                                   _DB_URL)
                except Exception as _s11t_e:
                    log.warning(f"[exec] checkpoint DECISION_WRITTEN TRADE failed: {_s11t_e}")
                # ── DPL Phase 3: Replay inputs capture ─────────────────────
                try:
                    _dpl.capture_replay_inputs(
                        decision_id=_dpl_trade_result["decision_id"],
                        direction=direction,
                        call_score=float(call_score),
                        put_score=float(put_score),
                        call_data=call_data,
                        put_data=put_data,
                        stock_data=stock_data,
                        verify_result=verify_result,
                        iv_rank=iv_rank,
                        alert_id=alert_id,
                        origin_type="SCHEDULER",
                        scheduler_job_id=job_id,
                        worker_pid=os.getpid(),
                        db_url=_DB_URL,
                    )
                    # ── DPL Phase 3: Post-capture replay check ──────────────
                    # POST-DECISION DETECTOR ONLY — NOT a pre-trade gate.
                    # The decision is already committed before this runs.
                    # This does NOT satisfy any pre-trade blocking requirement.
                    try:
                        _rpl = _dpl.replay_decision(
                            _dpl_trade_result["decision_id"]
                        )
                        if not _rpl["full_match"]:
                            _mm = (
                                f"[DPL MISMATCH] TRADE "
                                f"decision_id={_dpl_trade_result['decision_id'][:16]} "
                                f"call_match={_rpl['call_match']} "
                                f"put_match={_rpl['put_match']} "
                                f"dir_match={_rpl['direction_match']}"
                            )
                            log.critical(_mm)
                            _tg(_mm)
                    except _dpl.ReplayCodeDriftError as _rce:
                        _dm = (
                            f"[DPL CODE_DRIFT] TRADE "
                            f"decision_id={_dpl_trade_result['decision_id'][:16]}: {_rce}"
                        )
                        log.critical(_dm)
                        _tg(_dm)
                        try:
                            with psycopg2.connect(_DB_URL, connect_timeout=4) as _dc,                                  _dc.cursor() as _du:
                                _vs_trade = "WEIGHTS_DRIFT" if "WEIGHTS_DRIFT" in str(_rce) else "CODE_DRIFT"
                                _du.execute(
                                    "UPDATE oe_decision_audit "
                                    "SET verification_status=%s "
                                    "WHERE decision_id=%s",
                                    (_vs_trade, _dpl_trade_result["decision_id"],)
                                )
                        except Exception as _dbu:
                            log.warning(f"[dpl] drift status update failed: {_dbu}")
                    except Exception as _re:
                        _re_msg = (
                            f"[DPL REPLAY_ERROR] TRADE "
                            f"decision_id={_dpl_trade_result['decision_id'][:16]}: {_re}"
                        )
                        log.critical(_re_msg)
                        _tg(_re_msg)
                        try:
                            with psycopg2.connect(_DB_URL, connect_timeout=4) as _dc_re, \
                                 _dc_re.cursor() as _du_re:
                                _du_re.execute(
                                    "UPDATE oe_decision_audit "
                                    "SET verification_status='REPLAY_ERROR' "
                                    "WHERE decision_id=%s",
                                    (_dpl_trade_result["decision_id"],)
                                )
                        except Exception as _dbu_re:
                            log.warning(f"[dpl] REPLAY_ERROR status update failed: {_dbu_re}")
                except Exception as _p3_e:
                    # Item 14: NO new decision may become unreplayable.
                    # Register in oe_unreplayable_rows then re-raise.
                    log.critical(
                        f"[dpl][REPLAY_BLOCK] capture_replay_inputs TRADE failed "
                        f"trace_id={trace_id} decision_id={_dpl_trade_result.get('decision_id','?')}: {_p3_e}"
                    )
                    try:
                        with psycopg2.connect(_DB_URL, connect_timeout=4) as _rreg_t, \
                             _rreg_t.cursor() as _rreg_t_c:
                            _rreg_t_c.execute(
                                "INSERT INTO oe_unreplayable_rows "
                                "(decision_id, reason_code, recoverable, is_test_record) "
                                "VALUES (%s, 'REPLAY_ERROR', FALSE, FALSE) "
                                "ON CONFLICT (decision_id) DO NOTHING",
                                (_dpl_trade_result.get('decision_id'),)
                            )
                    except Exception as _rreg_t_e:
                        log.warning(f"[dpl] oe_unreplayable_rows insert failed: {_rreg_t_e}")
                    raise RuntimeError(
                        f"[REPLAY_BLOCK] TRADE replay capture failed — "
                        f"decision_id={_dpl_trade_result.get('decision_id','?')}: {_p3_e}"
                    ) from _p3_e
            except Exception as _dpl_e:
                log.warning(
                    f"[dpl] write_decision TRADE failed trace_id={trace_id}: {_dpl_e}"
                )

        # ── Write options_engine_runs (full trigger-chain audit record) ────────
        try:
            _run_id_oe = f"oe_{ticker}_{scan_date}_{trace_id[:8]}"
            with psycopg2.connect(_DB_URL, connect_timeout=4) as _oe_c, _oe_c.cursor() as _oe_u:
                _oe_u.execute("""
                    INSERT INTO options_engine_runs (
                        run_id, trace_id, ticker, run_date,
                        stocks_scanned, contracts_evaluated,
                        selected_ticker, selected_strategy, decision,
                        premarket_score, mtf_alignment_score,
                        pattern_score, final_ccs,
                        trigger_chain_json
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (run_id) DO NOTHING
                """, (
                    _run_id_oe, trace_id, ticker, scan_date,
                    1, contracts_evaluated,
                    ticker,
                    best_chain_strategy.get("strategy") if best_chain_strategy else None,
                    direction,
                    pm_intel.get("premarket_score"),
                    mtf_result.get("timeframe_alignment_score"),
                    pattern_score, final_ccs,
                    json.dumps({
                        "trigger": "seed_daily_candidates→run_pipeline_worker→_execute_job",
                        "scheduler_jobs": [
                            "premarket_scan@07:30ET",
                            "seed_daily_candidates@09:40ET",
                            "run_pipeline_worker@09:45ET",
                        ],
                        "premarket": {k: v for k, v in pm_intel.items()
                                      if k not in ("sector",)},
                        "mtf_summary": {
                            "alignment_score": mtf_result.get("timeframe_alignment_score"),
                            "dominant_bias":   mtf_result.get("dominant_bias"),
                            "conflict_score":  mtf_result.get("conflict_score"),
                            "entry_timing":    mtf_result.get("entry_timing_status"),
                        },
                        "pattern_score":        pattern_score,
                        "n_patterns_detected":  len(pattern_result.get("all_patterns", [])),
                        "contracts_evaluated":  contracts_evaluated,
                        "best_chain_strategy":  {
                            k: v for k, v in (best_chain_strategy or {}).items()
                            if k not in ("legs", "ccs_components")
                        } if best_chain_strategy else None,
                        "final_ccs":            final_ccs,
                        "req6_call_score":      call_score,
                        "req6_put_score":       put_score,
                        "req6_decision":        direction,
                        "alert_id":             alert_id,
                        "chain_sha256":         chain_sha,
                    }),
                ))
                _oe_c.commit()
            log.info(f"[exec] [{trace_id}] options_engine_runs written: {_run_id_oe}")
        except Exception as _oe_e:
            log.warning(f"[exec] [{trace_id}] options_engine_runs write failed: {_oe_e}")

        # ── Trace: AUDIT_RECORD ────────────────────────────────────────────────
        if _strace_ctx is not None:
            try:
                _strace_ctx.write_stage(
                    "AUDIT_RECORD",
                    ticker=ticker, scan_date=scan_date, job_id=job_id,
                    alert_id=alert_id,
                    metadata={"run_id": _run_id_oe, "chain_sha": chain_sha[:24] if chain_sha else None},
                )
            except Exception as _st_ar_e:
                log.debug(f"[scheduler_trace] AUDIT_RECORD: {_st_ar_e}")

        # Mark job DONE — compute Merkle chain_hash
        with _pg2.connect(_DB_URL, connect_timeout=4) as conn, conn.cursor() as cur:
            _done_prev_hash = _get_prev_chain_hash(conn)
            _done_chain_hash = _compute_chain_hash(
                job_id, ticker, str(scan_date), trace_id, direction, _done_prev_hash)
            cur.execute("""
                UPDATE options_pipeline_jobs
                SET status='DONE', completed_at=NOW(),
                    alert_id=%s, direction=%s, selected_score=%s, trace_id=%s,
                    chain_hash=%s
                WHERE id=%s
            """, (alert_id, direction, sel_score, trace_id, _done_chain_hash, job_id))
            conn.commit()

        log.info(
            f"[exec] DONE job_id={job_id} ticker={ticker} direction={direction} "
            f"alert_id={alert_id} chain={chain_sha[:16]} opj_chain={_done_chain_hash[:16]} "
            f"elapsed={elapsed}s trace_id={trace_id}"
        )
        _write_heartbeat(True)

        # ── Trace: PAPER_EXECUTION_OR_NO_TRADE (TRADE path) ───────────────────
        if _strace_ctx is not None:
            try:
                _strace_ctx.write_stage(
                    "PAPER_EXECUTION_OR_NO_TRADE",
                    ticker=ticker, scan_date=scan_date, job_id=job_id,
                    alert_id=alert_id,
                    completion_status=direction,
                    metadata={
                        "direction": direction,
                        "alert_id": alert_id,
                        "sel_score": float(sel_score),
                        "opj_chain": _done_chain_hash[:24],
                    },
                )
                _strace_ctx.write_stage(
                    "OUTCOME_TRACKING",
                    ticker=ticker, scan_date=scan_date, job_id=job_id,
                    alert_id=alert_id,
                    completion_status="WIRED",
                    metadata={"learning_loop": "grade_outcomes@16:46ET",
                              "p3_ready": _p3_ready, "p4_ready": _p4_ready},
                )
            except Exception as _st_pet_e:
                log.debug(f"[scheduler_trace] PAPER_EXECUTION_TRADE: {_st_pet_e}")

        _tg(
            f"✅ <b>OPTIONS PIPELINE COMPLETE</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Ticker: <b>{ticker}</b>   Decision: <b>{direction}</b>\n"
            f"Selected score: {sel_score}/100   Opponent: {opp_score}/100\n"
            f"Strike: ${sel_strike}   Expiry: {expiry_str}   DTE: 9\n"
            f"Entry: ${sel_data['bid']:.2f}–${sel_data['ask']:.2f}\n"
            f"Breakeven: ${alert_fields['breakeven']:.2f}\n"
            f"alert_id={alert_id}  trace_id={trace_id}\n"
            f"chain={chain_sha[:24]}…\n"
            f"elapsed={elapsed}s"
        )

        return {
            "job_id":    job_id,
            "ticker":    ticker,
            "direction": direction,
            "alert_id":  alert_id,
            "trace_id":  trace_id,
            "chain_sha": chain_sha,
            "call_score": call_score,
            "put_score":  put_score,
            "elapsed_s":  elapsed,
        }

    except Exception as e:
        elapsed = round(time.time() - t_start, 2)
        err_msg = str(e)[:500]
        # Classify outcome before logging so the label is accurate.
        # Hard-gate rejection ("not ready_for_decision: BOTH DIRECTIONS REJECTED...")
        # is a deliberate NO_TRADE decision by the quality gates — not a crash.
        # Reserve FAILED / FAILED_GATE for genuine exceptions; use NO_TRADE_GATES
        # for gate-rejected outcomes so daily_pipeline_runs.status is searchable.
        _is_gate_reject = err_msg.startswith("not ready_for_decision")
        _final_status = ('NO_TRADE_GATES' if _is_gate_reject
                         else 'FAILED_GATE' if _gate_fired[0]
                         else 'FAILED')
        log.error(f"[exec] {_final_status} job_id={job_id} ticker={ticker}: {e}")
        # Hard-gate rejection is a complete NO_TRADE decision.
        # Write chain_hash and PAPER_EXECUTION_OR_NO_TRADE trace so the audit is continuous.
        _failed_chain_hash = None
        try:
            with psycopg2.connect(_DB_URL, connect_timeout=4) as conn, conn.cursor() as cur:
                if _is_gate_reject:
                    try:
                        _fg_prev = _get_prev_chain_hash(conn)
                        _failed_chain_hash = _compute_chain_hash(
                            job_id, ticker, scan_date, trace_id,
                            "NO_TRADE_HARD_GATE", _fg_prev)
                    except Exception as _fg_ch_e:
                        log.debug(f"[exec] chain_hash for gate-reject failed (non-fatal): {_fg_ch_e}")
                cur.execute("""
                    UPDATE options_pipeline_jobs
                    SET status=%s, completed_at=NOW(),
                        error_text=%s,
                        chain_hash=%s
                    WHERE id=%s
                """, (_final_status, err_msg, _failed_chain_hash, job_id))
                conn.commit()
        except Exception as de:
            log.error(f"[exec] failed to write {_final_status} status: {de}")

        # ── Trace: PAPER_EXECUTION_OR_NO_TRADE (hard-gate rejection path) ──────
        if _strace_ctx is not None and _is_gate_reject:
            try:
                _strace_ctx.write_stage(
                    "PAPER_EXECUTION_OR_NO_TRADE",
                    ticker=ticker, scan_date=scan_date, job_id=job_id,
                    completion_status="NO_TRADE_HARD_GATE",
                    metadata={
                        "direction": "NO_TRADE",
                        "error": err_msg[:200],
                        "chain_hash": _failed_chain_hash[:24] if _failed_chain_hash else None,
                    },
                )
            except Exception as _st_fg_e:
                log.debug(f"[scheduler_trace] PAPER_EXEC hard-gate: {_st_fg_e}")

        _write_heartbeat(False, err_msg)
        # ── Phase 4: record operational incident ─────────────────────────────
        if _p4_ready:
            try:
                _p4.record_incident(
                    failure_source="options_pipeline_scheduler:_execute_job",
                    error_text=err_msg,
                    ticker=ticker, scan_date=scan_date,
                    reference_id=f"opj_{job_id}",
                    db_url=_DB_URL,
                )
            except Exception as _p4_inc_e:
                log.debug(f"[phase4] record_incident skipped: {_p4_inc_e}")
        if _is_gate_reject:
            _tg(
                f"⛔ <b>OPTIONS: NO TRADE (Hard Gates)</b>\n"
                f"job_id={job_id}  ticker={ticker}  trace_id={trace_id}\n"
                f"Reason: {err_msg[:200]}\n"
                f"elapsed={elapsed}s"
            )
        else:
            _tg(
                f"❌ <b>OPTIONS PIPELINE FAILED</b>\n"
                f"job_id={job_id}  ticker={ticker}  trace_id={trace_id}\n"
                f"Error: {err_msg[:200]}\n"
                f"elapsed={elapsed}s"
            )
        return {"error": err_msg, "job_id": job_id, "ticker": ticker,
                "trace_id": trace_id, "is_gate_reject": _is_gate_reject}

# ─────────────────────────────────────────────────────────────────────────────
# WORKER — claim and execute all PENDING jobs for today
# ─────────────────────────────────────────────────────────────────────────────

def run_pipeline_worker(scan_date: date = None, max_jobs: int = 10) -> dict:
    """
    Claim and execute all PENDING jobs for scan_date (default: today).
    Called by the 09:45 scheduler job.
    """
    scan_date = scan_date or datetime.now(_ET).date()
    executed = 0
    skipped  = 0
    results  = []

    for _ in range(max_jobs):
        claim_id = f"sched_{uuid.uuid4().hex[:20]}"
        claimed  = _atomic_claim(claim_id, scan_date)
        if not claimed:
            break   # no more PENDING jobs
        job_id, ticker = claimed
        log.info(f"[worker] claimed job_id={job_id} ticker={ticker} claim_id={claim_id}")

        # ── Trace: JOB_CLAIM ─────────────────────────────────────────────────
        try:
            import sys as _jc_sys, os as _jc_os
            _jc_dpl_dir = _jc_os.path.join(
                _jc_os.path.dirname(_jc_os.path.abspath(__file__)), 'dpl')
            if _jc_dpl_dir not in _jc_sys.path:
                _jc_sys.path.insert(0, _jc_dpl_dir)
            import scheduler_trace as _jc_st_mod
            _jc_st_mod.bootstrap(_DB_URL)
            _jc_tid = hashlib.sha256(
                f"{ticker}{scan_date}{claim_id}".encode()
            ).hexdigest()[:16]
            _jc_ctx = _jc_st_mod.TraceContext(trace_id=_jc_tid, db_url=_DB_URL)
            _jc_ctx.write_stage(
                "JOB_CLAIM",
                ticker=ticker, scan_date=scan_date, job_id=job_id,
                job_claim_timestamp=datetime.utcnow().isoformat() + "Z",
                metadata={"claim_id": claim_id, "worker_pid": os.getpid()},
            )
        except Exception as _jc_e:
            log.debug(f"[scheduler_trace] JOB_CLAIM: {_jc_e}")

        result = _execute_job(job_id, ticker, scan_date, claim_id)
        results.append(result)
        if "error" in result:
            skipped += 1
        else:
            executed += 1

    log.info(f"[worker] scan_date={scan_date}  executed={executed}  errors={skipped}")

    # Update durable run log with final counts.
    # Distinguish gate-rejections (deliberate NO_TRADE quality decisions) from
    # genuine crashes so daily_pipeline_runs.status is meaningful:
    #   NO_TRADE_GATES  — all candidates rejected by hard quality gates (normal)
    #   FAILED          — at least one unexpected exception (crash / timeout)
    #   COMPLETED       — at least one trade executed
    no_trade_count = sum(1 for r in results if r.get("direction") == "NO_TRADE")
    gate_rejected  = sum(1 for r in results if r.get("is_gate_reject"))
    final_status   = ("COMPLETED"     if executed > 0
                      else "NO_TRADE_GATES" if gate_rejected == skipped and skipped > 0
                      else "FAILED"         if skipped > 0
                      else "NO_TRADE")
    first_trace    = next((r.get("trace_id") for r in results if r.get("trace_id")), None)
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=4) as _wc, _wc.cursor() as _wu:
            # NOTE (informational-counter): candidates_failed (= skipped) is a
            # last-writer-wins summary written by every run_pipeline_worker call.
            # When /run-now and the natural 09:45 cron process different slices
            # of the same day's jobs, the last ON CONFLICT DO UPDATE wins and
            # reflects only that call's skipped count — not the day total.
            # AUTHORITATIVE count: SELECT COUNT(*) FROM options_pipeline_jobs
            #                      WHERE scan_date=<date> AND status='FAILED'
            #                      (or 'NO_TRADE_GATES') — not this column.
            _wu.execute("""
                INSERT INTO daily_pipeline_runs
                    (run_date, trigger_source, status, trace_id,
                     candidates_executed, candidates_no_trade, candidates_failed,
                     started_at, completed_at)
                VALUES (%s, 'primary', %s, %s, %s, %s, %s, NOW(), NOW())
                ON CONFLICT (run_date, trigger_source) DO UPDATE
                    SET status=EXCLUDED.status,
                        trace_id=COALESCE(EXCLUDED.trace_id, daily_pipeline_runs.trace_id),
                        candidates_executed=EXCLUDED.candidates_executed,
                        candidates_no_trade=EXCLUDED.candidates_no_trade,
                        candidates_failed=EXCLUDED.candidates_failed,
                        started_at=COALESCE(daily_pipeline_runs.started_at, NOW()),
                        completed_at=NOW()
            """, (scan_date, final_status, first_trace,
                  executed, no_trade_count, skipped))
            _wc.commit()
    except Exception as _we:
        log.warning(f"[worker] daily_pipeline_runs write failed: {_we}")

    return {"executed": executed, "errors": skipped, "jobs": results}

# ─────────────────────────────────────────────────────────────────────────────
# MISSED-SCHEDULE BACKFILL
# ─────────────────────────────────────────────────────────────────────────────

def backfill_missed_jobs() -> dict:
    """
    On startup: look for PENDING jobs from the last 24 h (missed during downtime).
    Execute them now.  This is the recovery path for VM reboots and process crashes.
    Skipped on weekends: market data is always >48 h stale on Sat/Sun, so every
    job would hit REGISTRY_STALE_DATA and fail — matching the Mon-Fri CronTrigger
    guards on the seed and execute jobs.
    """
    _today_dow = datetime.now(_ET).weekday()  # Mon=0 … Sun=6
    if _today_dow >= 5:
        log.info(f"[backfill] skipping — today is {'Saturday' if _today_dow == 5 else 'Sunday'} "
                 f"(no market data, all registry checks would fail with REGISTRY_STALE_DATA)")
        return {"backfilled_dates": [], "skipped_reason": "weekend"}
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT scan_date FROM options_pipeline_jobs
                WHERE status = 'PENDING'
                  AND scan_date >= CURRENT_DATE - INTERVAL '1 day'
                ORDER BY scan_date ASC
            """)
            missed_dates = [r[0] for r in cur.fetchall()]
    except Exception as e:
        log.error(f"[backfill] query failed: {e}")
        return {"error": str(e)}

    if not missed_dates:
        log.info("[backfill] no missed PENDING jobs")
        return {"backfilled_dates": []}

    log.warning(f"[backfill] found missed jobs for dates: {missed_dates}")
    _tg(
        f"🔁 <b>OPTIONS PIPELINE: Startup Backfill</b>\n"
        f"Found {len(missed_dates)} date(s) with PENDING jobs from before restart:\n"
        f"{', '.join(str(d) for d in missed_dates)}\n"
        f"Executing now..."
    )

    all_results = {}
    for sd in missed_dates:
        log.info(f"[backfill] running worker for {sd}")
        result = run_pipeline_worker(scan_date=sd)
        all_results[str(sd)] = result

    return {"backfilled_dates": [str(d) for d in missed_dates], "results": all_results}

# ─────────────────────────────────────────────────────────────────────────────
# PART 1 — SCHEDULE-INTEGRITY MONITOR
# Catches jobs silently skipped because the scheduler restarted after their
# cron window — APScheduler misfires are never retried automatically.
# Exact scenario caught: 09:40 seed job, VM restart at 12:57, silent skip.
# ─────────────────────────────────────────────────────────────────────────────

_SCHED_MONITOR_JOBS = [
    {"id": "premarket_scan",        "desc": "07:30 premarket scan",
     "expected_et": (7, 30),  "grace_minutes": 15},
    {"id": "seed_daily_candidates", "desc": "09:40 seed daily candidates",
     "expected_et": (9, 40),  "grace_minutes": 15},
    {"id": "run_pipeline_worker",   "desc": "09:45 pipeline worker",
     "expected_et": (9, 45),  "grace_minutes": 20},
    {"id": "grade_outcomes",        "desc": "16:46 grade outcomes",
     "expected_et": (16, 46), "grace_minutes": 10},
]


def _job_ran_today(cur, job_id: str, today) -> bool:
    """Return True if DB evidence shows this job ran today."""
    if job_id == "premarket_scan":
        try:
            cur.execute(
                "SELECT COUNT(*) FROM options_engine_premarket WHERE run_date=%s",
                (today,))
            return cur.fetchone()[0] > 0
        except Exception:
            return True  # table absent — don't false-alert
    elif job_id == "seed_daily_candidates":
        cur.execute(
            "SELECT status FROM daily_pipeline_runs "
            "WHERE run_date=%s AND trigger_source='primary'",
            (today,))
        row = cur.fetchone()
        return row is not None and row[0] not in (None, "SCHEDULED")
    elif job_id == "run_pipeline_worker":
        cur.execute(
            "SELECT completed_at FROM daily_pipeline_runs "
            "WHERE run_date=%s AND trigger_source='primary'",
            (today,))
        row = cur.fetchone()
        return row is not None and row[0] is not None
    elif job_id == "grade_outcomes":
        # job_heartbeats columns: last_success, last_attempt, consecutive_failures
        # grade_outcomes may not be wired to record_job_success → guard missing row
        cur.execute(
            "SELECT last_success FROM job_heartbeats "
            "WHERE job_name='grade_outcomes'")
        row = cur.fetchone()
        if row is None or row[0] is None:
            return True  # not wired to heartbeat — don't false-alert
        # last_success stored UTC-naive; grade fires at 16:46 ET (=20:46 UTC same day)
        return row[0].date() >= today
    return True  # unknown job — don't alert


def _schedule_integrity_check(force_now_et=None) -> list:
    """
    Runs every 15 minutes and on startup.
    For every monitored daily job, checks whether DB evidence of a run exists
    once the expected fire time + grace period has elapsed today.
    Fires a Telegram alert immediately on any overdue job.

    Returns list of overdue job_ids (empty = all good).
    force_now_et: datetime override for testing only.
    """
    now_et = force_now_et or datetime.now(_ET)
    today  = now_et.date()

    if now_et.weekday() >= 5:
        log.debug("[sched_integrity] weekend — skipping")
        return []

    overdue = []
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=4) as conn, conn.cursor() as cur:
            for cfg in _SCHED_MONITOR_JOBS:
                jid   = cfg["id"]
                h, m  = cfg["expected_et"]
                grace = cfg["grace_minutes"]
                alert_after = (
                    now_et.replace(hour=h, minute=m, second=0, microsecond=0)
                    + timedelta(minutes=grace)
                )
                if now_et < alert_after:
                    continue  # grace period not expired yet for this job
                if _job_ran_today(cur, jid, today):
                    continue

                overdue.append(jid)
                msg = (
                    f"⚠️ <b>SCHEDULE MISS DETECTED</b>\n"
                    f"Job: <code>{jid}</code>\n"
                    f"Expected: {h:02d}:{m:02d} ET  |  Grace: {grace} min\n"
                    f"Alert cutoff: {alert_after.strftime('%H:%M ET')}\n"
                    f"Detected at: {now_et.strftime('%H:%M:%S ET')}\n"
                    f"Evidence of run: NONE\n"
                    f"Root cause: scheduler restarted after cron window — "
                    f"APScheduler misfires are not retried automatically.\n"
                    f"Date: {today}"
                )
                log.warning(f"[sched_integrity] OVERDUE job={jid} date={today}")
                _tg(msg)
    except Exception as e:
        log.warning(f"[sched_integrity] check error: {e}")

    return overdue


def _test_misfire_proof() -> dict:
    """
    PROOF TEST — Part 1.
    Simulates the exact scenario: scheduler restart after 09:40 window,
    seed job silently skipped (daily_pipeline_runs stays SCHEDULED).

    1. Records current daily_pipeline_runs status for today.
    2. Temporarily sets row to status='SCHEDULED'.
    3. Calls _schedule_integrity_check() with force_now_et = 10:00 ET today
       (past the 09:55 grace cutoff for seed_daily_candidates).
    4. Verifies overdue list contains 'seed_daily_candidates' and
       Telegram alert was fired.
    5. Restores original row status.
    """
    now_et = datetime.now(_ET)
    today  = now_et.date()
    result = {
        "test": "forced_misfire_proof", "date": str(today),
        "passed": False, "overdue_jobs": [], "detail": "",
    }
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=4) as conn, conn.cursor() as cur:
            # Save original
            cur.execute(
                "SELECT status FROM daily_pipeline_runs "
                "WHERE run_date=%s AND trigger_source='primary'", (today,))
            row = cur.fetchone()
            original_status = row[0] if row else None

            # Force to SCHEDULED sentinel
            cur.execute("""
                INSERT INTO daily_pipeline_runs (run_date, trigger_source, status)
                VALUES (%s, 'primary', 'SCHEDULED')
                ON CONFLICT (run_date, trigger_source) DO UPDATE SET status='SCHEDULED'
            """, (today,))
            conn.commit()

            # Run check with fake time = 10:00 ET (past 09:55 grace)
            fake_time = now_et.replace(hour=10, minute=0, second=0, microsecond=0)
            overdue = _schedule_integrity_check(force_now_et=fake_time)

            result["overdue_jobs"] = overdue
            result["passed"] = "seed_daily_candidates" in overdue
            result["detail"] = (
                f"Set status=SCHEDULED for {today}, "
                f"ran check at fake_time=10:00 ET (grace cutoff 09:55 ET). "
                f"Overdue detected: {overdue}. "
                f"Telegram alert fired: {result['passed']}"
            )

            # Restore
            if original_status is None:
                cur.execute(
                    "DELETE FROM daily_pipeline_runs "
                    "WHERE run_date=%s AND trigger_source='primary'", (today,))
            else:
                cur.execute(
                    "UPDATE daily_pipeline_runs SET status=%s "
                    "WHERE run_date=%s AND trigger_source='primary'",
                    (original_status, today))
            conn.commit()
    except Exception as e:
        result["detail"] = f"error: {e}"
        log.error(f"[misfire_proof] {e}")

    log.info(f"[misfire_proof] result={result}")
    return result


# ─────────────────────────────────────────────────────────────────────────────
# PART 2 — STATUS / CLASSIFICATION INTEGRITY CHECK
# Detects and corrects daily_pipeline_runs rows where status=FAILED but the
# underlying options_pipeline_jobs are all gate-rejections (not real crashes).
# Root cause of the Jul 28-29 misclassification:
#   Per-job status was written as FAILED (old code, before _is_gate_reject
#   classification was added). Worker rollup saw gate_rejected=0 → FAILED.
#   error_text stayed NULL because no crash description existed.
# ─────────────────────────────────────────────────────────────────────────────

_GATE_REJECT_PREFIX = "not ready_for_decision"


def _validate_and_fix_pipeline_run_classifications(
        fix_db: bool = True, days_back: int = 30) -> dict:
    """
    Sweeps daily_pipeline_runs for misclassified FAILED rows:
      - status = 'FAILED'
      - candidates_failed > 0
      - error_text IS NULL

    For each, queries options_pipeline_jobs for that date:
      - All failed jobs have 'not ready_for_decision' error → reclassify to
        NO_TRADE_GATES, populate error_text with gate summary.
      - Any real crash present → leave status=FAILED, populate error_text
        from the first real exception.

    fix_db=False is a dry-run that returns what would change.
    """
    cutoff = date.today() - timedelta(days=days_back)
    fixed = []
    dry_run_would_fix = []

    try:
        with psycopg2.connect(_DB_URL, connect_timeout=6) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT id, run_date, status, candidates_failed, error_text
                FROM daily_pipeline_runs
                WHERE status = 'FAILED'
                  AND candidates_failed > 0
                  AND error_text IS NULL
                  AND run_date >= %s
                ORDER BY run_date DESC
            """, (cutoff,))
            suspect_rows = cur.fetchall()
            log.info(f"[classify_fix] found {len(suspect_rows)} suspect FAILED rows "
                     f"(null error_text) since {cutoff}")

            for row_id, run_date, status, n_failed, _err in suspect_rows:
                cur.execute("""
                    SELECT id, ticker, status, error_text
                    FROM options_pipeline_jobs
                    WHERE scan_date = %s
                      AND status IN ('FAILED', 'NO_TRADE_GATES')
                """, (run_date,))
                jobs = cur.fetchall()
                if not jobs:
                    continue

                all_gate = all(
                    (j[3] or "").startswith(_GATE_REJECT_PREFIX) for j in jobs)
                first_real_crash = next(
                    (j[3] for j in jobs
                     if j[3] and not j[3].startswith(_GATE_REJECT_PREFIX)),
                    None)

                if all_gate:
                    new_status = "NO_TRADE_GATES"
                    new_err    = (
                        f"Reclassified from FAILED: {len(jobs)} gate-rejection(s) "
                        f"on {run_date}. Sample: "
                        f"{(jobs[0][3] or '')[:120]}"
                    )
                elif first_real_crash:
                    new_status = "FAILED"
                    new_err    = first_real_crash[:400]
                else:
                    continue

                entry = {
                    "daily_pipeline_runs_id": row_id,
                    "run_date":         str(run_date),
                    "old_status":       status,
                    "new_status":       new_status,
                    "job_count":        len(jobs),
                    "all_gate_reject":  all_gate,
                    "new_error_text":   new_err[:120],
                }
                if fix_db:
                    cur.execute("""
                        UPDATE daily_pipeline_runs
                        SET status=%s, error_text=%s
                        WHERE id=%s
                    """, (new_status, new_err, row_id))
                    fixed.append(entry)
                    log.info(
                        f"[classify_fix] id={row_id} date={run_date} "
                        f"{status} → {new_status}")
                else:
                    dry_run_would_fix.append(entry)

            if fix_db and fixed:
                conn.commit()
                log.info(f"[classify_fix] committed {len(fixed)} fix(es)")

    except Exception as e:
        log.error(f"[classify_fix] error: {e}")
        return {"error": str(e)}

    return {
        "fixed":             fixed,
        "dry_run_would_fix": dry_run_would_fix,
        "fix_db":            fix_db,
    }


# ─────────────────────────────────────────────────────────────────────────────
# PART 3 — EXTERNAL API CONTRACT CANARY
# Lightweight pre-batch checks on Polygon endpoints.
# Runs at 07:45 ET and 09:30 ET (before the 09:40 seed job).
# On any failure: loud Telegram alert — does NOT use the fallback-to-cache
# path that masked the Jul-30 74-ticker Polygon 400 burst.
# ─────────────────────────────────────────────────────────────────────────────

def _polygon_canary_check(force_fail: bool = False) -> dict:
    """
    Canary checks for the two Polygon endpoints the daily pipeline depends on:
      1. /v2/aggs/grouped/locale/us/market/stocks/{date} — grouped daily OHLCV
      2. /v3/snapshot/options/{ticker}?limit=1          — options chain snapshot

    On any non-200 / error response: fires Telegram alert immediately.
    Does NOT fall back to cache — silent cache fallback is the exact failure
    mode this canary is designed to expose.

    force_fail=True: deliberately uses bad params to prove the alert fires.
    """
    api_key = os.environ.get("POLYGON_API_KEY", "").strip()
    if not api_key:
        log.warning("[canary] POLYGON_API_KEY not set — skipping")
        return {"skipped": True, "reason": "no POLYGON_API_KEY"}

    results = {}
    alerts  = []

    # ── Canary 1: grouped-daily ───────────────────────────────────────────────
    if force_fail:
        test_date = "1900-01-01"  # guaranteed no-data date
    else:
        test_date = (datetime.now(_ET).date() - timedelta(days=1)).isoformat()

    url1 = (
        f"https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks"
        f"/{test_date}?adjusted=true&apiKey={api_key}"
    )
    try:
        req1 = urllib.request.Request(url1, headers={"User-Agent": "aiem-canary/1"})
        with urllib.request.urlopen(req1, timeout=10) as r1:
            http1 = r1.status
            # grouped-daily response can be megabytes; read enough to parse
            # status/error fields but don't load the full body into memory.
            try:
                body1 = json.loads(r1.read(4096))
            except Exception:
                # Truncated JSON = very large valid response → status was 200
                body1 = {"status": "OK_LARGE_RESPONSE", "resultsCount": 1}
        ok1 = http1 == 200
    except Exception as e1:
        http1  = 0
        body1  = {"error": str(e1)}
        ok1    = False

    results["grouped_daily"] = {
        "endpoint": f"/v2/aggs/grouped/locale/us/market/stocks/{test_date}",
        "http_status": http1, "ok": ok1,
        "body_sample": str(body1)[:160],
    }
    if not ok1:
        alerts.append(
            f"🚨 <b>POLYGON CANARY FAIL — grouped-daily</b>\n"
            f"Endpoint: {results['grouped_daily']['endpoint']}\n"
            f"HTTP {http1}  |  {str(body1)[:200]}\n"
            f"⚠️ <b>seed_daily_candidates WILL FAIL</b> if unresolved by 09:40 ET.\n"
            f"force_fail={force_fail}"
        )

    # ── Canary 2: options snapshot ────────────────────────────────────────────
    if force_fail:
        canary_ticker = "BADTICKER_CANARY_XYZ"  # invalid ticker
    else:
        canary_ticker = "SPY"

    url2 = (
        f"https://api.polygon.io/v3/snapshot/options/{canary_ticker}"
        f"?limit=1&apiKey={api_key}"
    )
    try:
        req2 = urllib.request.Request(url2, headers={"User-Agent": "aiem-canary/1"})
        with urllib.request.urlopen(req2, timeout=10) as r2:
            http2  = r2.status
            body2  = json.loads(r2.read(512))
        ok2 = http2 == 200
    except Exception as e2:
        http2  = 0
        body2  = {"error": str(e2)}
        ok2    = False

    results["options_snapshot"] = {
        "endpoint": f"/v3/snapshot/options/{canary_ticker}?limit=1",
        "http_status": http2, "ok": ok2,
        "body_sample": str(body2)[:160],
    }
    if not ok2:
        alerts.append(
            f"🚨 <b>POLYGON CANARY FAIL — options snapshot</b>\n"
            f"Endpoint: {results['options_snapshot']['endpoint']}\n"
            f"HTTP {http2}  |  {str(body2)[:200]}\n"
            f"⚠️ <b>execute_pipeline_job WILL FAIL</b> for all tickers.\n"
            f"force_fail={force_fail}"
        )

    for alert_msg in alerts:
        log.error(f"[canary] {alert_msg}")
        _tg(alert_msg)

    log.info(
        f"[canary] grouped_daily={ok1} options_snapshot={ok2} "
        f"alerts_fired={len(alerts)} force_fail={force_fail}"
    )
    return {
        "results":      results,
        "alerts_fired": len(alerts),
        "all_ok":       ok1 and ok2,
    }


# ─────────────────────────────────────────────────────────────────────────────
# GRADE OUTCOMES (4:46 PM job — stages 9-10)
# ─────────────────────────────────────────────────────────────────────────────

def grade_outcomes_job() -> dict:
    try:
        import aiem_options_pipeline as _pipe
        result = _pipe.grade_options_outcomes(days_back=30)
        n = result.get("graded_count", 0)
        log.info(f"[grade] graded={n}  wr={result.get('win_rate_pct')}%")
        _write_heartbeat(True)
        # ── Phase 3: root-cause batch + scorecard rebuild after grading ────────
        try:
            import aiem_options_phase3 as _p3g
            _p3g.record_root_cause_batch(days_back=30, db_url=_DB_URL)
            _p3g.rebuild_all_scorecards(db_url=_DB_URL)
        except Exception as _p3g_e:
            log.warning(f"[phase3] grade_outcomes_job p3 step failed: {_p3g_e}")
        # ── Phase 4: No-Trade outcome tracking + operational failure scan ───────
        try:
            import aiem_options_phase4 as _p4g
            _p4g.track_no_trade_outcomes(days_back=30, db_url=_DB_URL)
            _p4g.scan_operational_failures(days_back=7, db_url=_DB_URL)
        except Exception as _p4g_e:
            log.warning(f"[phase4] grade_outcomes_job p4 step failed: {_p4g_e}")
        # ── Phase 5: Governance summary + audit chain health ─────────────────
        try:
            import aiem_options_phase5 as _p5g
            _p5g_summary = _p5g.get_governance_summary(db_url=_DB_URL)
            log.info(f"[phase5] governance: {_p5g_summary}")
        except Exception as _p5g_e:
            log.warning(f"[phase5] grade_outcomes_job p5 step failed: {_p5g_e}")
        if n:
            _tg(
                f"📊 <b>OPTIONS OUTCOMES GRADED</b>\n"
                f"Graded: {n}  |  Win rate: {result.get('win_rate_pct')}%"
            )
        return result
    except Exception as e:
        log.error(f"[grade] error: {e}")
        _write_heartbeat(False, str(e))
        return {"error": str(e)}

# ─────────────────────────────────────────────────────────────────────────────
# HEALTH ENDPOINT
# ─────────────────────────────────────────────────────────────────────────────

_scheduler_ref    = None
_DRIFT_ALERT_SENT = False   # fires at most once per process lifetime (Step 3)

class _HealthHandler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        # /jobs — registered jobs with trigger + next_run_time (Item 1 timezone proof)
        if self.path.rstrip('/') == '/jobs' and _scheduler_ref:
            _jobs_out = []
            for _j in _scheduler_ref.get_jobs():
                _jobs_out.append({
                    "id":            _j.id,
                    "trigger":       str(_j.trigger),
                    "next_run_time": str(_j.next_run_time) if _j.next_run_time else None,
                })
            _jbody = json.dumps({"jobs": _jobs_out, "ts": datetime.utcnow().isoformat() + "Z"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(_jbody)))
            self.end_headers()
            self.wfile.write(_jbody)
            return
        health = {
            "status":    "ok",
            "scheduler": "running" if (_scheduler_ref and _scheduler_ref.running) else "stopped",
            "service":   _SCHEDULER_NAME,
            "ts":        datetime.utcnow().isoformat() + "Z",
        }
        try:
            with psycopg2.connect(_DB_URL, connect_timeout=2) as conn, conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM options_pipeline_jobs WHERE status='PENDING'")
                health["pending_jobs"] = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM options_pipeline_jobs WHERE status='EXECUTING'")
                health["executing_jobs"] = cur.fetchone()[0]
                cur.execute("""
                    SELECT last_success, consecutive_failures
                    FROM job_heartbeats WHERE job_name=%s
                """, (_HEARTBEAT_JOB_NAME,))
                hb = cur.fetchone()
                if hb:
                    health["last_heartbeat"] = str(hb[0])
                    health["consecutive_failures"] = hb[1]
                health["db"] = "ok"
        except Exception as e:
            health["db"] = f"error: {e}"
            health["status"] = "degraded"

        # ── Commit-drift fields (Step 2) ─────────────────────────────────────
        try:
            _hc_disk = subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=os.path.dirname(os.path.abspath(__file__)),
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
        except Exception:
            _hc_disk = "UNKNOWN"
        health["boot_commit"]  = _BOOT_COMMIT
        health["disk_commit"]  = _hc_disk
        health["commit_match"] = (
            _BOOT_COMMIT == _hc_disk and _BOOT_COMMIT != "UNKNOWN"
        )
        if not health["commit_match"]:
            _hc_drift = (datetime.utcnow() - _BOOT_TIME).total_seconds()
            health["drift_minutes"] = round(_hc_drift / 60, 1)

        body = json.dumps(health).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        path = self.path.rstrip('/')
        # /run-synthetic — exercises bootstrap_phase2 + capture_trade_record +
        # options_engine_runs within THIS scheduler process.  All steps logged
        # with log.info so they appear in the scheduler workflow log stream.
        if path == '/run-synthetic':
            import uuid as _uuid, json as _json
            from datetime import date as _sdate
            _sid = _uuid.uuid4().hex[:12].upper()
            _res = {"sid": _sid, "pid": os.getpid()}
            log.info(f"[synth] [{_sid}] START pid={os.getpid()} path=/run-synthetic")
            # Step 1: bootstrap_phase2 (_p2_ready check)
            try:
                import aiem_options_phase2 as _sp2
                _sp2.bootstrap_phase2(_DB_URL)
                _p2r = True
                log.info(f"[synth] [{_sid}] [P2_INIT] bootstrap_phase2 OK → _p2_ready=True")
            except Exception as _be:
                _p2r = False
                log.warning(f"[synth] [{_sid}] [P2_INIT] bootstrap_phase2 FAILED: {_be} → _p2_ready=False")
            _res['p2_ready'] = _p2r
            if not _p2r:
                body = _json.dumps(_res).encode()
                self.send_response(200); self.send_header("Content-Type","application/json")
                self.send_header("Content-Length", str(len(body))); self.end_headers()
                self.wfile.write(body); return
            # Step 2: capture_trade_record
            _sd = _sdate.today()
            _sel = {"bid": 2.50, "ask": 2.60, "delta": -0.35, "gamma": 0.02,
                    "theta": -0.05, "vega": 0.15, "iv": 0.35, "slippage_pct": 0.05,
                    "premium_at_risk": 255.0, "profit_target": 510.0,
                    # Item 1: spot_at_alert + dte needed for rho/charm/vanna computation
                    "spot_at_alert": 198.0, "dte": 9}
            log.info(f"[synth] [{_sid}] [P2_CAPTURE] calling capture_trade_record alert_id=8888 ticker=SYNTH_SCHED scan_date={_sd}")
            _tr = _sp2.capture_trade_record(
                alert_id=8888, trace_id=f"SYNTH_{_sid}", ticker="SYNTH_SCHED",
                scan_date=_sd, direction="LONG_PUT", sel_data=_sel, sel_strike=200.0,
                alert_fields={"breakeven": 197.5, "spot_at_alert": 198.0, "strike": 200.0,
                               "dte": 9, "iv": 0.35},
                call_score=0.0, put_score=75.0,
                stock_data={"market_regime": "BEAR", "sector": "SYNTH"},
                verify_result={"gate_passed": True}, db_url=_DB_URL,
            )
            log.info(f"[synth] [{_sid}] [P2_CAPTURE] capture_trade_record returned tr_id={_tr}")
            _res['tr_id'] = _tr
            # Step 3: options_engine_runs
            _run_id = f"oe_SYNTH_SCHED_{_sd}_{_sid[:8]}"
            try:
                with psycopg2.connect(_DB_URL, connect_timeout=4) as _oc, _oc.cursor() as _ou:
                    _ou.execute("""
                        INSERT INTO options_engine_runs
                            (run_id,trace_id,ticker,run_date,stocks_scanned,
                             contracts_evaluated,selected_ticker,selected_strategy,
                             decision,trigger_chain_json)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (run_id) DO NOTHING
                    """, (_run_id, f"SYNTH_{_sid}", "SYNTH_SCHED", _sd,
                          1, 5, "SYNTH_SCHED", "LONG_PUT", "LONG_PUT",
                          json.dumps({"trigger":"run-synthetic","sid":_sid,"pid":os.getpid()})))
                    _oc.commit()
                log.info(f"[synth] [{_sid}] [OE_RUNS] options_engine_runs written run_id={_run_id}")
                _res['oe_run_id'] = _run_id
            except Exception as _oee:
                log.warning(f"[synth] [{_sid}] [OE_RUNS] options_engine_runs write FAILED: {_oee}")
                _res['oe_run_error'] = str(_oee)
            log.info(f"[synth] [{_sid}] DONE tr_id={_tr} run_id={_run_id}")
            body = _json.dumps(_res).encode()
            self.send_response(200); self.send_header("Content-Type","application/json")
            self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body)
            return
        # /run-now — calls run_pipeline_worker() directly in this process;
        # useful to see _p2_ready in live _execute_job() logs when a PENDING job exists.
        if path == '/run-now':
            import json as _json
            try:
                _rr = run_pipeline_worker()
                body = _json.dumps({"triggered": True, "result": _rr}).encode()
            except Exception as _rne:
                log.warning(f"[health] /run-now error: {_rne}")
                body = _json.dumps({"triggered": True, "error": str(_rne)}).encode()
            self.send_response(200); self.send_header("Content-Type","application/json")
            self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body)
            return
        # /run-seed — triggers seed_daily_candidates() for today's scan_date;
        # idempotent: ON CONFLICT DO NOTHING prevents duplicate candidate rows.
        if path == '/run-seed':
            import json as _json, threading as _sth, datetime as _sdt
            _scan_date = _sdt.date.today()
            def _do_seed(_sd=_scan_date):
                try:
                    result = seed_daily_candidates(scan_date=_sd)
                    log.info(f"[run-seed] complete result={result}")
                except Exception as _se:
                    log.warning(f"[run-seed] seed failed: {_se}")
            _sth.Thread(target=_do_seed, daemon=True, name="run-seed-manual").start()
            body = _json.dumps({"status": "seed_triggered",
                                "scan_date": str(_scan_date)}).encode()
            self.send_response(202); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(405); self.end_headers()

def _start_health_server():
    srv = HTTPServer(("0.0.0.0", _HEALTH_PORT), _HealthHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True, name="opt-sched-health")
    t.start()
    log.info(f"[health] http://0.0.0.0:{_HEALTH_PORT}/health")

# ─────────────────────────────────────────────────────────────────────────────
# HEARTBEAT BACKGROUND THREAD (every 5 min)
# ─────────────────────────────────────────────────────────────────────────────

def _heartbeat_loop():
    while True:
        time.sleep(300)
        _write_heartbeat(True)
        log.debug("[heartbeat] written")

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    global _scheduler_ref
    log.info(f"[startup] {_SCHEDULER_NAME} starting…")

    _bootstrap_db()
    _start_health_server()

    # ── Boot-identity record (Step 1) ────────────────────────────────────────
    # Write one row to process_lifecycle_log so the loaded commit is queryable.
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=4) as _bl_c, \
             _bl_c.cursor() as _bl_cur:
            _bl_cur.execute("""
                INSERT INTO process_lifecycle_log (process_name, pid, git_sha, started_at)
                VALUES (%s, %s, %s, NOW())
            """, (_SCHEDULER_NAME, _BOOT_PID, _BOOT_COMMIT))
            _bl_c.commit()
        log.info(
            f"[startup] BOOT  pid={_BOOT_PID}  commit={_BOOT_COMMIT}  "
            f"recorded in process_lifecycle_log"
        )
    except Exception as _ble:
        log.warning(f"[startup] process_lifecycle_log insert failed: {_ble}")

    # ── Step 0: Register today's run as SCHEDULED (dedup signal for backup) ─
    # Weekday guard: SCHEDULED rows on weekends create misleading pipeline state
    # (Jul-25 Saturday was erroneously marked SCHEDULED). Use ET-aware date so
    # post-midnight-UTC restarts don't register tomorrow's date as today.
    try:
        _now_et0  = datetime.now(_ET)
        _today_et = _now_et0.date()
        if _now_et0.weekday() >= 5:  # 5=Sat, 6=Sun
            log.info(f"[startup] daily_pipeline_runs: skipping SCHEDULED insert — "
                     f"today is {'Saturday' if _now_et0.weekday()==5 else 'Sunday'} "
                     f"(not a trading day)")
        else:
            with psycopg2.connect(_DB_URL, connect_timeout=4) as _sc0, _sc0.cursor() as _cu0:
                _cu0.execute("""
                    INSERT INTO daily_pipeline_runs
                        (run_date, trigger_source, status)
                    VALUES (%s, 'primary', 'SCHEDULED')
                    ON CONFLICT (run_date, trigger_source) DO NOTHING
                """, (_today_et,))
                _sc0.commit()
            log.info(f"[startup] daily_pipeline_runs: SCHEDULED registered for {_today_et}")
    except Exception as _sc0e:
        log.warning(f"[startup] daily_pipeline_runs SCHEDULED insert failed: {_sc0e}")

    # ── Step 1: Startup stale recovery ──────────────────────────────────────
    log.info("[startup] running stale job recovery…")
    stale_result = recover_stale_jobs()
    log.info(f"[startup] stale recovery: {stale_result}")

    # ── Step 2: Missed-schedule backfill (existing PENDING rows) ───────────
    log.info("[startup] running missed-schedule backfill…")
    backfill_result = backfill_missed_jobs()
    log.info(f"[startup] backfill: {backfill_result}")

    # ── Step 2b: Missed-SEED detection — VM restarted after 9:45 window ────
    # If the VM restarted AFTER the 9:40 seed window but BEFORE EOD, and
    # today has zero rows in options_pipeline_jobs, seed + execute immediately.
    try:
        _now_et = datetime.now(_ET)
        _is_weekday = _now_et.weekday() < 5          # Mon=0 … Fri=4
        _after_window = _now_et.hour > 9 or (_now_et.hour == 9 and _now_et.minute >= 46)
        _before_eod   = _now_et.hour < 15 or (_now_et.hour == 15 and _now_et.minute <= 30)
        if _is_weekday and _after_window and _before_eod:
            with psycopg2.connect(_DB_URL, connect_timeout=4) as _sc, _sc.cursor() as _scu:
                _scu.execute(
                    "SELECT COUNT(*) FROM options_pipeline_jobs WHERE scan_date = %s",
                    (_now_et.date(),)
                )
                _today_count = _scu.fetchone()[0]
                # Also check whether today's run row is still stuck at SCHEDULED.
                # This covers the case where the scheduler restarted after the 09:40
                # window: options_pipeline_jobs count=0 is already caught above, but if
                # seed_daily_candidates was silently skipped (exception in prior run),
                # the SCHEDULED sentinel in daily_pipeline_runs is the reliable indicator.
                # Cutoff: 3:30 PM ET — options pipeline requires same-session fill-or-kill;
                # entries after 3:30 PM miss the live trading window before market close.
                _scu.execute(
                    "SELECT status FROM daily_pipeline_runs "
                    "WHERE run_date = %s AND trigger_source = 'primary'",
                    (_now_et.date(),)
                )
                _dpr_row = _scu.fetchone()
                _dpr_status = _dpr_row[0] if _dpr_row else None
            # Trigger catch-up if:
            #   (a) no jobs queued yet in options_pipeline_jobs (count=0), OR
            #   (b) daily_pipeline_runs row is still SCHEDULED (seed never ran/committed)
            if _today_count == 0 or _dpr_status == 'SCHEDULED':
                log.warning(
                    f"[startup] missed-seed detected: 0 rows for {_now_et.date()} "
                    f"(VM restarted after 09:45 window). Seeding + executing now…"
                )
                _tg("[MISSED-SEED RECOVERY] OPTIONS PIPELINE\n"
                    + f"date={_now_et.date()}  time={_now_et.strftime('%H:%M ET')}\n"
                    + "VM restarted after 09:45 window. Seeding + executing now.")
                _ms_seed = seed_daily_candidates(scan_date=_now_et.date())
                log.info(f"[startup] missed-seed result: {_ms_seed}")
                if _ms_seed.get("seeded", 0) > 0:
                    _ms_exec = run_pipeline_worker(scan_date=_now_et.date())
                    log.info(f"[startup] missed-seed exec: {_ms_exec}")
            else:
                log.info(f"[startup] no missed-seed: {_today_count} row(s) already exist for {_now_et.date()}")
    except Exception as _ms_e:
        log.warning(f"[startup] missed-seed check error: {_ms_e}")

    # ── Step 3: APScheduler ─────────────────────────────────────────────────
    sched = BackgroundScheduler(timezone=_ET)

    # 09:40 ET — seed daily candidates
    def _seed_job():
        log.info("[scheduler] 09:40 seed job starting")
        seed_daily_candidates()

    sched.add_job(_seed_job, CronTrigger(day_of_week="mon-fri", hour=9, minute=40, timezone=_ET),
                  id="seed_daily_candidates", replace_existing=True)

    # 09:45 ET — execute pipeline
    def _execute_job_wrapper():
        log.info("[scheduler] 09:45 pipeline worker starting")
        run_pipeline_worker()

    sched.add_job(_execute_job_wrapper, CronTrigger(day_of_week="mon-fri", hour=9, minute=45, timezone=_ET),
                  id="run_pipeline_worker", replace_existing=True)

    # 07:30 ET — premarket intelligence scan (before market open)
    def _premarket_job():
        log.info("[scheduler] 07:30 premarket scan starting")
        premarket_scan_job()

    sched.add_job(_premarket_job, CronTrigger(day_of_week="mon-fri", hour=7, minute=30, timezone=_ET),
                  id="premarket_scan", replace_existing=True)

    # 09:30 ET — intraday premarket update (break/fail of PM high/low)
    def _pm_intraday_update_job():
        log.info("[scheduler] 09:30 intraday PM update starting")
        try:
            import aiem_premarket_intel as _pm_mod
            with psycopg2.connect(_DB_URL, connect_timeout=4) as _c, _c.cursor() as _u:
                _u.execute(
                    "SELECT ticker FROM options_engine_premarket WHERE run_date=%s",
                    (datetime.now(_ET).date(),)
                )
                for (t,) in _u.fetchall():
                    try:
                        _pm_mod.update_intraday(t)
                    except Exception as _ue:
                        log.debug(f"[pm_intraday] {t}: {_ue}")
        except Exception as _pme:
            log.warning(f"[pm_intraday] failed: {_pme}")

    sched.add_job(_pm_intraday_update_job,
                  CronTrigger(day_of_week="mon-fri", hour=9, minute=36, timezone=_ET),
                  id="pm_intraday_update", replace_existing=True)

    # 16:44 ET — DPL daily trace report (Item 10: full audit evidence for the day)
    def _daily_trace_report_job():
        log.info("[scheduler] 16:44 daily trace report starting")
        try:
            import importlib.util as _ilu, os as _os
            _dtr_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                       "dpl", "daily_trace_report.py")
            _spec = _ilu.spec_from_file_location("daily_trace_report", _dtr_path)
            _mod  = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_mod)
            from datetime import datetime as _dt, timezone as _tz, timedelta as _td
            _et = _tz(timedelta(hours=-4))
            _rdate = _dt.now(_et).date()
            _report = _mod.build_report(_rdate)
            _path   = _mod.save_report(_report)
            log.info(f"[daily_trace_report] saved to {_path}  "
                     f"sha256={_report.get('report_sha256','?')[:16]}  "
                     f"decisions={_report['summary']['total_decisions']}")
        except Exception as _dtr_e:
            log.warning(f"[daily_trace_report] failed: {_dtr_e}")

    sched.add_job(_daily_trace_report_job,
                  CronTrigger(day_of_week="mon-fri", hour=16, minute=44, timezone=_ET),
                  id="daily_trace_report", replace_existing=True)

    # 16:46 ET — grade outcomes
    sched.add_job(grade_outcomes_job,
                  CronTrigger(day_of_week="mon-fri", hour=16, minute=46, timezone=_ET),
                  id="grade_outcomes", replace_existing=True)

    # Every 5 min — stale recovery
    sched.add_job(recover_stale_jobs,
                  CronTrigger(minute="*/5"),
                  id="stale_recovery", replace_existing=True)

    # ── Runtime Integrity Monitoring ─────────────────────────────────────────

    # Every 15 min — schedule-integrity monitor (Part 1)
    # Catches jobs silently skipped when the scheduler restarts after their
    # cron window; APScheduler misfires are never retried automatically.
    sched.add_job(_schedule_integrity_check,
                  CronTrigger(minute="*/15"),
                  id="sched_integrity_check", replace_existing=True)

    # 07:45 ET — Polygon API canary before premarket window (Part 3)
    sched.add_job(_polygon_canary_check,
                  CronTrigger(day_of_week="mon-fri", hour=7, minute=45, timezone=_ET),
                  id="polygon_canary_preopen", replace_existing=True)

    # 09:30 ET — Polygon API canary 10 min before seed job fires (Part 3)
    sched.add_job(_polygon_canary_check,
                  CronTrigger(day_of_week="mon-fri", hour=9, minute=30, timezone=_ET),
                  id="polygon_canary_preseed", replace_existing=True)

    # Every 4 hours — pipeline run classification validation sweep (Part 2)
    sched.add_job(
        lambda: _validate_and_fix_pipeline_run_classifications(fix_db=True),
        CronTrigger(hour="*/4", minute=5),
        id="classification_integrity_sweep", replace_existing=True)

    # 10:00 AM ET Mon-Fri — consolidated morning diagnostic (all 4 checks in one report)
    # Calls tools/morning_diagnostic.py as subprocess so it runs standalone and
    # sends ONE combined Telegram if any check fails.  Individual alerts from
    # the existing mechanisms still fire on their own — this is an additional view.
    def _morning_diagnostic_job():
        import subprocess as _sp
        script = os.path.normpath(
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "..", "..", "tools", "morning_diagnostic.py"))
        res = _sp.run(
            ["python3", script],
            capture_output=True, text=True, timeout=90,
            cwd=os.path.dirname(script))
        for line in (res.stdout + res.stderr).splitlines():
            log.info(f"[morning_diag] {line}")
        if res.returncode not in (0, 1):  # 1 = checks failed (expected); anything else is crash
            log.error(f"[morning_diag] unexpected rc={res.returncode}")

    sched.add_job(
        _morning_diagnostic_job,
        CronTrigger(day_of_week="mon-fri", hour=10, minute=0, timezone=_ET),
        id="morning_diagnostic", replace_existing=True)

    # ── TEST CYCLE (only when TEST_CYCLE_OFFSET_SECS is set) ────────────────
    # Proves the scheduler fires jobs automatically at a scheduled time.
    # Set TEST_CYCLE_OFFSET_SECS=N to fire a full seed+execute cycle N seconds
    # from now.  TEST_SCAN_DATE overrides the scan date (default: yesterday).
    _test_offset = int(os.environ.get("TEST_CYCLE_OFFSET_SECS", "0"))
    if _test_offset > 0:
        _raw_test_date = os.environ.get("TEST_SCAN_DATE", "")
        if _raw_test_date:
            from datetime import date as _date_cls
            _test_sd = _date_cls.fromisoformat(_raw_test_date)
        else:
            _test_sd = (datetime.now(_ET) - timedelta(days=1)).date()

        _fire_at = datetime.now(_ET) + timedelta(seconds=_test_offset)
        _test_run_id = uuid.uuid4().hex[:12]

        def _test_cycle_job():
            log.info(
                f"[TEST_CYCLE] *** APScheduler fired automatically ***  "
                f"run_id={_test_run_id}  scan_date={_test_sd}  "
                f"fire_ts={datetime.utcnow().isoformat()}Z"
            )
            seed_result   = seed_daily_candidates(scan_date=_test_sd)
            worker_result = run_pipeline_worker(scan_date=_test_sd)
            log.info(
                f"[TEST_CYCLE] COMPLETE  run_id={_test_run_id}  "
                f"seeded={seed_result.get('seeded',0)}  "
                f"executed={worker_result.get('executed',0)}  "
                f"errors={worker_result.get('errors',0)}"
            )

        sched.add_job(
            _test_cycle_job,
            DateTrigger(run_date=_fire_at, timezone=_ET),
            id="test_cycle_auto",
            replace_existing=True,
        )
        log.info(
            f"[TEST_CYCLE] one-shot job scheduled — fires automatically at "
            f"{_fire_at.strftime('%Y-%m-%dT%H:%M:%S%z')}  "
            f"run_id={_test_run_id}  scan_date={_test_sd}"
        )

    # Every 5 min — heartbeat
    threading.Thread(target=_heartbeat_loop, daemon=True, name="hb").start()

    sched.start()
    _scheduler_ref = sched

    # Log next run times
    for job in sched.get_jobs():
        log.info(f"[scheduler] job={job.id}  next={job.next_run_time}")

    _write_heartbeat(True)
    _tg(
        f"🟢 <b>OPTIONS PIPELINE SCHEDULER STARTED</b>\n"
        f"Stale recovered: {stale_result.get('recovered',0)}\n"
        f"Backfill dates: {backfill_result.get('backfilled_dates',[])}\n"
        f"Health: http://0.0.0.0:{_HEALTH_PORT}/health\n"
        f"Jobs scheduled: seed@09:40ET, execute@09:45ET, grade@16:46ET"
    )

    log.info("[startup] scheduler running — entering keepalive loop")
    try:
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        log.info("[shutdown] stopping scheduler")
        sched.shutdown(wait=False)

if __name__ == "__main__":
    main()
