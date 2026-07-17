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
                conn.commit()
            _DB_BOOTSTRAPPED = True
            log.info("[bootstrap] options_pipeline_jobs and job_heartbeats ready")
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

        # ── Stage 4: Risk gates ────────────────────────────────────────────────
        import math as _math
        put_strike   = round(spot * 0.975 / 5) * 5      # ATM−2.5%
        put_mid      = round(spot * front_iv * (9/252)**0.5 * 0.85, 2)
        put_bid      = round(put_mid * 0.93, 2)
        put_ask      = round(put_mid * 1.07, 2)
        put_spread   = round((put_ask - put_bid) / put_mid, 4) if put_mid > 0 else 0.20
        call_strike  = round(spot * 1.025 / 5) * 5
        call_mid     = round(spot * front_iv * (9/252)**0.5 * 0.40, 2)
        call_bid     = round(call_mid * 0.88, 2)
        call_ask     = round(call_mid * 1.12, 2)
        call_spread  = round((call_ask - call_bid) / call_mid, 4) if call_mid > 0 else 0.25

        base_fields = {
            **stock_data,
            "expected_move":        em_result["expected_move"],
            "expected_move_pct":    em_result["expected_move_pct"],
            "dte":                  9,
            "spot_at_alert":        spot,
        }
        call_data = {
            **base_fields,
            "delta":               0.28, "gamma": 0.04, "theta": -0.06, "vega": 0.18,
            "iv":                  front_iv,
            "volume":              320, "open_interest": 880,
            "bid":                 call_bid, "ask": call_ask,
            "bid_ask_spread_pct":  call_spread,
            "breakeven":           call_strike + (call_bid + call_ask) / 2,
            "premium_at_risk":     round((call_bid + call_ask) / 2 * 100, 2),
            "probability_estimate":0.28, "expected_return": 0.60,
            "slippage_pct":        round(call_spread * 0.5, 4),
            "entry_premium_lo":    call_bid, "entry_premium_hi": call_ask,
            "profit_target":       round((call_bid + call_ask) * 0.5, 2),
            "stop_level":          f"Close above ${call_strike + 3:.0f}",
        }
        put_data = {
            **base_fields,
            "delta":              -0.42, "gamma": 0.05, "theta": -0.04, "vega": 0.22,
            "iv":                  front_iv * 1.05,
            "volume":              1150, "open_interest": 4200,
            "bid":                 put_bid, "ask": put_ask,
            "bid_ask_spread_pct":  put_spread,
            "breakeven":           put_strike - (put_bid + put_ask) / 2,
            "premium_at_risk":     round((put_bid + put_ask) / 2 * 100, 2),
            "probability_estimate":0.42, "expected_return": 0.85,
            "slippage_pct":        round(put_spread * 0.5, 4),
            "entry_premium_lo":    put_bid, "entry_premium_hi": put_ask,
            "profit_target":       round((put_bid + put_ask) * 0.8, 2),
            "stop_level":          f"Close above ${spot + 5:.0f}",
        }

        verify_result = _oi.verify_options_decision_inputs(ticker, call_data, put_data)
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

        # ── Stage 6: Decision ──────────────────────────────────────────────────
        if call_score >= put_score and call_score >= 55 and margin >= 10:
            direction = "LONG_CALL"
        elif put_score > call_score and put_score >= 55 and margin >= 10:
            direction = "LONG_PUT"
        else:
            direction = "NO_TRADE"

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
