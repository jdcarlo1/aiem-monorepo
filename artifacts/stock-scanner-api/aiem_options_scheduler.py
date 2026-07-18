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

    except Exception as e:
        log.error(f"[stale_recovery] error: {e}")

    if recovered:
        log.info(f"[stale_recovery] recovered={recovered}  failed_perm={failed_perm}")
        _tg(
            f"🔄 <b>OPTIONS PIPELINE: Stale Job Recovery</b>\n"
            f"Recovered {recovered} stuck job(s) → PENDING for re-execution.\n"
            f"Permanently failed: {failed_perm}"
        )
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
    scan_date = scan_date or date.today()
    seeded = 0
    dupes  = 0
    candidates = []

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

    # Write seed event to durable run log
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=4) as _dc, _dc.cursor() as _cu:
            _cu.execute("""
                INSERT INTO daily_pipeline_runs
                    (run_date, trigger_source, status, candidates_seeded, started_at)
                VALUES (%s, 'primary', 'RUNNING', %s, NOW())
                ON CONFLICT (run_date, trigger_source) DO UPDATE
                    SET status='RUNNING',
                        candidates_seeded=EXCLUDED.candidates_seeded,
                        started_at=COALESCE(daily_pipeline_runs.started_at, NOW())
            """, (scan_date, seeded))
            _dc.commit()
    except Exception as _de:
        log.warning(f"[seed] daily_pipeline_runs write failed: {_de}")

    return {"seeded": seeded, "skipped_duplicates": dupes,
            "candidates": [r[0] for r in candidates]}


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
    try:
        import aiem_options_phase2 as _p2
        _p2.bootstrap_phase2(_DB_URL)
        _p2_ready = True
    except Exception as _p2_init_e:
        log.debug(f"[exec] phase2 init skipped: {_p2_init_e}")
        _p2_ready = False
        _p2       = None

    # ── Phase III Phase 3: Analysis & Attribution (non-fatal) ────────────────
    try:
        import aiem_options_phase3 as _p3
        _p3.bootstrap_phase3(_DB_URL)
        _p3_ready = True
    except Exception as _p3_init_e:
        log.debug(f"[exec] phase3 init skipped: {_p3_init_e}")
        _p3_ready = False
        _p3       = None

    t_start = time.time()

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
            _pmd_q   = "STALE" if _pmd_age > 172800 else "FRESH"
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
            _rc("OSS", "OSS_GEX_REGIME",  None, None, "NEUTRAL",
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
            log.debug(f"[exec] [{trace_id}] options chain skipped: {_oc_e}")

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
            log.debug(f"[exec] [{trace_id}] execution_intelligence skipped: {_ei_e}")

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

        # Strike levels: strategy design parameters (±2.5% from spot)
        put_strike  = round(spot * 0.975 / 5) * 5
        call_strike = round(spot * 1.025 / 5) * 5

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
                    if _grk.get("delta") is not None:
                        call_delta_bs        = round(abs(float(_grk["delta"])), 4)
                        call_probability_itm = call_delta_bs
                elif _typ == "put" and abs(_sk - put_strike) < 7.5:
                    put_vol = int(_o.get("volume") or 0)
                    put_oi  = int(_o.get("open_interest") or 0)
                    if _grk.get("delta") is not None:
                        put_delta_bs        = round(float(_grk["delta"]), 4)
                        put_probability_itm = round(abs(float(_grk["delta"])), 4)
            log.info(
                f"[exec] [{trace_id}] Tradier chain expiry={_exp} "
                f"call δ={call_delta_bs} vol={call_vol} oi={call_oi}  "
                f"put δ={put_delta_bs} vol={put_vol} oi={put_oi}"
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
            "expected_return":     0.60,
            "slippage_pct":        round(call_spread * 0.5, 4),
            "entry_premium_lo":    call_bid, "entry_premium_hi": call_ask,
            "profit_target":       round((call_bid + call_ask) * 0.5, 2),
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
            "expected_return":     0.85,
            "slippage_pct":        round(put_spread * 0.5, 4),
            "entry_premium_lo":    put_bid, "entry_premium_hi": put_ask,
            "profit_target":       round((put_bid + put_ask) * 0.8, 2),
            "stop_level":          f"Close above ${spot + 5:.0f}",
        }

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
            try:
                _reg_mod.assert_data_freshness(trace_id, _CRITICAL_FRESHNESS_IDS,
                                               172800, _reg_db)
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
                        verify_data=verify_result if "verify_result" in dir() else {},
                        stock_data=stock_data   if "stock_data"   in dir() else {},
                        db_url=_DB_URL,
                    )
                except Exception as _p3_nt_rc_e:
                    log.debug(f"[phase3] no_trade root_cause skipped: {_p3_nt_rc_e}")
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
                    log.debug(f"[phase3] no_trade kb_entry skipped: {_p3_nt_kb_e}")
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
                _p2.capture_trade_record(
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
            except Exception as _tr_e:
                log.debug(f"[phase2] trade_record capture skipped: {_tr_e}")
            try:
                _p2.update_decision_alert_id(trace_id, alert_id, _DB_URL,
                                             chain_hash=chain_sha)
            except Exception as _uda_e:
                log.debug(f"[phase2] update_decision_alert_id skipped: {_uda_e}")

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
        log.error(f"[exec] FAILED job_id={job_id} ticker={ticker}: {e}")
        try:
            with psycopg2.connect(_DB_URL, connect_timeout=4) as conn, conn.cursor() as cur:
                cur.execute("""
                    UPDATE options_pipeline_jobs
                    SET status='FAILED', completed_at=NOW(),
                        error_text=%s
                    WHERE id=%s
                """, (err_msg, job_id))
                conn.commit()
        except Exception as de:
            log.error(f"[exec] failed to write FAILED status: {de}")

        _write_heartbeat(False, err_msg)
        _tg(
            f"❌ <b>OPTIONS PIPELINE FAILED</b>\n"
            f"job_id={job_id}  ticker={ticker}  trace_id={trace_id}\n"
            f"Error: {err_msg[:200]}\n"
            f"elapsed={elapsed}s"
        )
        return {"error": err_msg, "job_id": job_id, "ticker": ticker,
                "trace_id": trace_id}

# ─────────────────────────────────────────────────────────────────────────────
# WORKER — claim and execute all PENDING jobs for today
# ─────────────────────────────────────────────────────────────────────────────

def run_pipeline_worker(scan_date: date = None, max_jobs: int = 10) -> dict:
    """
    Claim and execute all PENDING jobs for scan_date (default: today).
    Called by the 09:45 scheduler job.
    """
    scan_date = scan_date or date.today()
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

        result = _execute_job(job_id, ticker, scan_date, claim_id)
        results.append(result)
        if "error" in result:
            skipped += 1
        else:
            executed += 1

    log.info(f"[worker] scan_date={scan_date}  executed={executed}  errors={skipped}")

    # Update durable run log with final counts
    no_trade_count = sum(1 for r in results if r.get("direction") == "NO_TRADE")
    final_status   = "COMPLETED" if executed > 0 else ("FAILED" if skipped > 0 else "NO_TRADE")
    first_trace    = next((r.get("trace_id") for r in results if r.get("trace_id")), None)
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=4) as _wc, _wc.cursor() as _wu:
            _wu.execute("""
                INSERT INTO daily_pipeline_runs
                    (run_date, trigger_source, status, trace_id,
                     candidates_executed, candidates_no_trade, candidates_failed,
                     completed_at)
                VALUES (%s, 'primary', %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (run_date, trigger_source) DO UPDATE
                    SET status=EXCLUDED.status,
                        trace_id=COALESCE(EXCLUDED.trace_id, daily_pipeline_runs.trace_id),
                        candidates_executed=EXCLUDED.candidates_executed,
                        candidates_no_trade=EXCLUDED.candidates_no_trade,
                        candidates_failed=EXCLUDED.candidates_failed,
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
    """
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
            log.debug(f"[phase3] grade_outcomes_job p3 step skipped: {_p3g_e}")
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

_scheduler_ref = None

class _HealthHandler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
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

        body = json.dumps(health).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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

    # ── Step 0: Register today's run as SCHEDULED (dedup signal for backup) ─
    try:
        _today_et = date.today()
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
            if _today_count == 0:
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

    sched.add_job(_seed_job, CronTrigger(day_of_week="mon-fri", hour=9, minute=40),
                  id="seed_daily_candidates", replace_existing=True)

    # 09:45 ET — execute pipeline
    def _execute_job_wrapper():
        log.info("[scheduler] 09:45 pipeline worker starting")
        run_pipeline_worker()

    sched.add_job(_execute_job_wrapper, CronTrigger(day_of_week="mon-fri", hour=9, minute=45),
                  id="run_pipeline_worker", replace_existing=True)

    # 07:30 ET — premarket intelligence scan (before market open)
    def _premarket_job():
        log.info("[scheduler] 07:30 premarket scan starting")
        premarket_scan_job()

    sched.add_job(_premarket_job, CronTrigger(day_of_week="mon-fri", hour=7, minute=30),
                  id="premarket_scan", replace_existing=True)

    # 09:30 ET — intraday premarket update (break/fail of PM high/low)
    def _pm_intraday_update_job():
        log.info("[scheduler] 09:30 intraday PM update starting")
        try:
            import aiem_premarket_intel as _pm_mod
            with psycopg2.connect(_DB_URL, connect_timeout=4) as _c, _c.cursor() as _u:
                _u.execute(
                    "SELECT ticker FROM options_engine_premarket WHERE run_date=%s",
                    (date.today(),)
                )
                for (t,) in _u.fetchall():
                    try:
                        _pm_mod.update_intraday(t)
                    except Exception as _ue:
                        log.debug(f"[pm_intraday] {t}: {_ue}")
        except Exception as _pme:
            log.warning(f"[pm_intraday] failed: {_pme}")

    sched.add_job(_pm_intraday_update_job,
                  CronTrigger(day_of_week="mon-fri", hour=9, minute=36),
                  id="pm_intraday_update", replace_existing=True)

    # 16:46 ET — grade outcomes
    sched.add_job(grade_outcomes_job,
                  CronTrigger(day_of_week="mon-fri", hour=16, minute=46),
                  id="grade_outcomes", replace_existing=True)

    # Every 5 min — stale recovery
    sched.add_job(recover_stale_jobs,
                  CronTrigger(minute="*/5"),
                  id="stale_recovery", replace_existing=True)

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
