#!/usr/bin/env python3
"""
AIEM Options Pipeline Backup Runner
====================================
Standalone — zero imports from the main codebase.
Runs from GitHub Actions (or any external host) when the primary
Replit VM misses the morning 9:40–9:45 ET seed/execute window.

Required env vars:
  DATABASE_URL          Postgres connection string
  POLYGON_API_KEY       Polygon.io key (for rvol health-check)
  TRADIER_API_TOKEN     Tradier token (TOKEN_2 preferred)
  TELEGRAM_BOT_TOKEN    Telegram bot token
  TELEGRAM_CHAT_ID      Telegram chat ID
  TRIGGER_SOURCE        Optional override (default: backup_github_actions)
"""
import os, sys, json, hashlib, logging, time, uuid
import urllib.request, urllib.error, urllib.parse
from datetime import date, datetime, timezone, timedelta

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("psycopg2 not installed — run: pip install psycopg2-binary", file=sys.stderr)
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)sZ] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S"
)
log = logging.getLogger("aiem-backup")

_DB_URL       = os.environ.get("DATABASE_URL", "")
_POLYGON_KEY  = os.environ.get("POLYGON_API_KEY", "")
_TRADIER_TOK  = os.environ.get("TRADIER_API_TOKEN_2") or os.environ.get("TRADIER_API_TOKEN", "")
_TG_TOKEN     = os.environ.get("TELEGRAM_BOT_TOKEN", "")
_TG_CHAT      = os.environ.get("TELEGRAM_CHAT_ID", "")
_TRIGGER      = os.environ.get("TRIGGER_SOURCE", "backup_github_actions")

_ET           = timezone(timedelta(hours=-4))   # EDT; adjust to -5 for EST if needed
_ADVISORY_KEY = 9400945                         # Postgres advisory lock key — "9:40 options run"
_SEED_LIMIT   = 5                               # candidates per day


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _db():
    return psycopg2.connect(_DB_URL, connect_timeout=8)


def _tg(text: str):
    if not _TG_TOKEN or not _TG_CHAT:
        return
    url = f"https://api.telegram.org/bot{_TG_TOKEN}/sendMessage"
    body = json.dumps({"chat_id": _TG_CHAT, "text": text, "parse_mode": "HTML"}).encode()
    req = urllib.request.Request(url, data=body,
                                  headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception as e:
        log.warning(f"[telegram] {e}")


def _http_get(url: str, headers: dict = None, timeout: int = 10) -> dict:
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        log.warning(f"[http] GET {url[:60]}: {e}")
        return {}


def _get_prev_chain_hash(conn) -> str:
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT chain_hash FROM options_pipeline_jobs
                WHERE chain_hash IS NOT NULL
                ORDER BY id DESC LIMIT 1
            """)
            row = cur.fetchone()
            return row[0] if row else "genesis"
    except Exception:
        return "genesis"


def _compute_chain_hash(job_id, ticker, scan_date, trace_id, direction, prev_hash) -> str:
    payload = f"{job_id}:{ticker}:{scan_date}:{trace_id or ''}:{direction}:{prev_hash}"
    return hashlib.sha256(payload.encode()).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# DB BOOTSTRAP
# ─────────────────────────────────────────────────────────────────────────────

def _bootstrap(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS daily_pipeline_runs (
                id                  BIGSERIAL PRIMARY KEY,
                run_date            DATE        NOT NULL,
                trigger_source      VARCHAR(48) NOT NULL DEFAULT 'primary',
                status              VARCHAR(20) NOT NULL DEFAULT 'SCHEDULED',
                claim_id            VARCHAR(48),
                trace_id            VARCHAR(48),
                polygon_rvol_rows   INT,
                oss_rows            INT,
                candidates_seeded   INT,
                candidates_executed INT,
                candidates_no_trade INT,
                candidates_failed   INT,
                error_text          TEXT,
                started_at          TIMESTAMPTZ,
                completed_at        TIMESTAMPTZ,
                created_at          TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(run_date, trigger_source)
            );
            CREATE INDEX IF NOT EXISTS idx_dpr_run_date
                ON daily_pipeline_runs(run_date);

            CREATE TABLE IF NOT EXISTS options_pipeline_jobs (
                id                BIGSERIAL PRIMARY KEY,
                ticker            VARCHAR(20)  NOT NULL,
                scan_date         DATE         NOT NULL,
                status            VARCHAR(20)  NOT NULL DEFAULT 'PENDING',
                claim_id          VARCHAR(48),
                trace_id          VARCHAR(48),
                alert_id          INT,
                direction         VARCHAR(12),
                selected_score    NUMERIC(5,1),
                trigger_source    VARCHAR(48)  NOT NULL DEFAULT 'scheduler',
                error_text        TEXT,
                recovery_attempts INT          DEFAULT 0,
                created_at        TIMESTAMPTZ  DEFAULT NOW(),
                claimed_at        TIMESTAMPTZ,
                executing_at      TIMESTAMPTZ,
                completed_at      TIMESTAMPTZ,
                heartbeat_at      TIMESTAMPTZ,
                chain_hash        VARCHAR(64),
                UNIQUE(ticker, scan_date)
            );
        """)
    conn.commit()
    log.info("[bootstrap] schema ready")


# ─────────────────────────────────────────────────────────────────────────────
# DEDUP CHECKS
# ─────────────────────────────────────────────────────────────────────────────

def _primary_already_ran(conn, today: date) -> bool:
    """Return True if primary completed today's run — backup must exit."""
    with conn.cursor() as cur:
        # Check 1: any completed run in daily_pipeline_runs
        cur.execute("""
            SELECT status FROM daily_pipeline_runs
            WHERE run_date = %s AND status = 'COMPLETED'
            LIMIT 1
        """, (today,))
        if cur.fetchone():
            log.info("[dedup] daily_pipeline_runs shows COMPLETED — primary already ran")
            return True

        # Check 2: all today's pipeline jobs are DONE
        cur.execute("""
            SELECT COUNT(*) total,
                   COUNT(*) FILTER (WHERE status = 'DONE') done
            FROM options_pipeline_jobs
            WHERE scan_date = %s
        """, (today,))
        row = cur.fetchone()
        if row and row[0] > 0 and row[0] == row[1]:
            log.info(f"[dedup] {row[0]} jobs all DONE — primary already ran")
            return True

        # Check 3: heartbeat written recently AND all jobs are in-progress
        # A stale heartbeat (>35 min) means the VM is likely down — allow recovery.
        # We do NOT use heartbeat alone as a block; job-state is the ground truth.
        cur.execute("""
            SELECT last_success FROM job_heartbeats
            WHERE job_name = 'options_pipeline_scheduler'
              AND last_success >= NOW() - INTERVAL '35 minutes'
        """)
        hb_row = cur.fetchone()
        if hb_row:
            # Scheduler is alive — only dedup if there are NO pending jobs left
            cur.execute("""
                SELECT COUNT(*) FILTER (WHERE status IN ('PENDING','CLAIMED','EXECUTING'))
                FROM options_pipeline_jobs WHERE scan_date = %s
            """, (today,))
            pending = cur.fetchone()
            if pending and pending[0] == 0:
                log.info("[dedup] scheduler heartbeat fresh + 0 pending jobs — primary handled it")
                return True
            log.info(f"[dedup] scheduler heartbeat fresh but {pending[0] if pending else '?'} "
                     f"jobs still pending — backup will claim unclaimed slots")

    return False


# ─────────────────────────────────────────────────────────────────────────────
# ADVISORY LOCK
# ─────────────────────────────────────────────────────────────────────────────

def _try_lock(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (_ADVISORY_KEY,))
        return cur.fetchone()[0]


def _unlock(conn):
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", (_ADVISORY_KEY,))
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# POLYGON HEALTH CHECK
# ─────────────────────────────────────────────────────────────────────────────

def _polygon_rvol_check(conn, today: date) -> int:
    """Return today's polygon_rvol_scan row count (0 if missing)."""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM polygon_rvol_scan WHERE scan_date = %s", (today,))
            return cur.fetchone()[0]
    except Exception:
        return 0


def _polygon_grouped_daily_fetch(today: date) -> int:
    """
    Fetch Polygon grouped-daily for the most recent trading day and
    return how many results came back (does NOT write to DB — the
    primary's polygon_rvol_scan table is populated by main.py).
    Returns row count as a health indicator.
    """
    if not _POLYGON_KEY:
        log.warning("[polygon] POLYGON_API_KEY not set — skipping health fetch")
        return 0
    url = (f"https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks/"
           f"{today.isoformat()}?adjusted=true&apiKey={_POLYGON_KEY}")
    data = _http_get(url, timeout=20)
    count = data.get("resultsCount", 0)
    log.info(f"[polygon] grouped-daily {today}: {count} results from API")
    return count


# ─────────────────────────────────────────────────────────────────────────────
# SEED CANDIDATES
# ─────────────────────────────────────────────────────────────────────────────

def _seed_candidates(conn, today: date) -> list:
    """
    Insert PENDING jobs from options_structure_scan.
    Identical logic to primary — uses MAX(scan_date) from polygon_market_daily
    so we are never blocked by today's data not existing (it's EOD data).
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT o.ticker, o.scan_date,
                   o.pc_skew_pp, o.gex_regime, o.pc_skew_tag,
                   o.spot, o.front_iv, NULL
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
        """, (today, _SEED_LIMIT))
        candidates = cur.fetchall()

    if not candidates:
        # Fallback — OSS without PMD join (PMD might be empty for some dates)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT o.ticker, o.scan_date,
                       o.pc_skew_pp, o.gex_regime, o.pc_skew_tag,
                       o.spot, o.front_iv, NULL
                FROM options_structure_scan o
                WHERE o.scan_date = %s
                  AND o.pc_skew_pp IS NOT NULL
                  AND o.front_iv > 0
                  AND o.spot > 10
                ORDER BY o.pc_skew_pp DESC
                LIMIT %s
            """, (today, _SEED_LIMIT))
            candidates = cur.fetchall()
        log.warning(f"[seed] PMD join returned 0; fallback OSS-only: {len(candidates)} candidates")

    seeded = []
    with conn.cursor() as cur:
        for row in candidates:
            ticker = row[0]
            try:
                cur.execute("""
                    INSERT INTO options_pipeline_jobs
                        (ticker, scan_date, status, trigger_source)
                    VALUES (%s, %s, 'PENDING', %s)
                    ON CONFLICT (ticker, scan_date) DO NOTHING
                """, (ticker, today, _TRIGGER))
                if cur.rowcount > 0:
                    seeded.append(row)
                    log.info(f"[seed] seeded {ticker} {today}")
                else:
                    log.info(f"[seed] skip duplicate {ticker} {today}")
            except Exception as e:
                log.warning(f"[seed] insert {ticker}: {e}")
    conn.commit()
    log.info(f"[seed] seeded={len(seeded)} of {len(candidates)} candidates")
    return seeded


# ─────────────────────────────────────────────────────────────────────────────
# RULE-BASED SCORING  (no LLM, no local modules)
# ─────────────────────────────────────────────────────────────────────────────

def _rule_score(pc_skew_tag, gex_regime, close_str, front_iv, term_tag, pc_skew_pp):
    """
    Compute PUT and CALL scores (0–100) using hard rules derived from the
    options structure signals.  Mirrors the intent of compute_req6_score
    without requiring the aiem_options_pipeline module.
    """
    close_str = close_str or 0.5

    put_score = 0.0
    # Skew signal — primary driver
    if pc_skew_tag == "FEAR_PREMIUM":
        put_score += 40
    if gex_regime in ("SHORT_GAMMA", "NEAR_FLIP"):
        put_score += 25
    # Price action
    if close_str < 0.35:
        put_score += 20
    elif close_str < 0.50:
        put_score += 10
    # Volatility structure
    if term_tag == "INVERTED":
        put_score += 10
    if front_iv and front_iv > 50:
        put_score += 5
    # Extreme skew bonus
    if pc_skew_pp and pc_skew_pp > 200:
        put_score += 5
    put_score = min(put_score, 100.0)

    call_score = 0.0
    if pc_skew_tag != "FEAR_PREMIUM":
        call_score += 20
    if gex_regime == "LONG_GAMMA":
        call_score += 30
    if close_str > 0.65:
        call_score += 20
    elif close_str > 0.55:
        call_score += 10
    if front_iv and front_iv < 35:
        call_score += 10
    call_score = min(call_score, 100.0)

    return put_score, call_score


# ─────────────────────────────────────────────────────────────────────────────
# EXECUTE ONE JOB
# ─────────────────────────────────────────────────────────────────────────────

def _execute_job(conn, candidate_row, today: date) -> dict:
    """
    Execute the simplified backup pipeline for one candidate.
    Returns result dict with direction, trace_id, chain_hash.
    """
    ticker       = candidate_row[0]
    pc_skew_pp   = float(candidate_row[2] or 0)
    gex_regime   = candidate_row[3] or "NEUTRAL"
    pc_skew_tag  = candidate_row[4] or ""
    spot         = float(candidate_row[5] or 0)
    front_iv_pct = float(candidate_row[6] or 0)
    front_iv     = front_iv_pct / 100.0
    close_str    = float(candidate_row[7] or 0.5) if len(candidate_row) > 7 and candidate_row[7] else 0.5

    trace_id = uuid.uuid4().hex[:16]
    t0       = time.time()

    # Claim the job
    claim_id = f"backup_{uuid.uuid4().hex[:16]}"
    job_id   = None
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id FROM options_pipeline_jobs
            WHERE ticker = %s AND scan_date = %s AND status = 'PENDING'
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        """, (ticker, today))
        row = cur.fetchone()
        if not row:
            log.info(f"[exec] {ticker}: no PENDING job (already claimed by primary)")
            return {"ticker": ticker, "direction": "DEDUP", "trace_id": trace_id}
        job_id = row[0]
        cur.execute("""
            UPDATE options_pipeline_jobs
            SET status='EXECUTING', executing_at=NOW(), trace_id=%s,
                claim_id=%s, heartbeat_at=NOW()
            WHERE id=%s
        """, (trace_id, claim_id, job_id))
    conn.commit()

    try:
        # Pull full OSS row
        with conn.cursor() as cur:
            cur.execute("""
                SELECT spot, front_iv, gex_m, gex_regime, gamma_flip_price,
                       pc_skew_pp, pc_skew_tag, term_ratio, term_tag, back_iv
                FROM options_structure_scan
                WHERE ticker=%s AND scan_date=%s
            """, (ticker, today))
            oss = cur.fetchone()

        if not oss:
            raise ValueError(f"OSS missing for {ticker} {today}")

        spot         = float(oss[0] or spot)
        front_iv_pct = float(oss[1] or front_iv_pct)
        front_iv     = front_iv_pct / 100.0
        gex_regime   = oss[3] or gex_regime
        pc_skew_pp   = float(oss[5] or pc_skew_pp)
        pc_skew_tag  = oss[6] or pc_skew_tag
        term_tag     = oss[8] or ""

        # Pull latest PMD for close_strength
        with conn.cursor() as cur:
            cur.execute("""
                SELECT close_price, open_price, vwap, close_strength
                FROM polygon_market_daily
                WHERE ticker=%s
                ORDER BY scan_date DESC LIMIT 1
            """, (ticker,))
            pmd = cur.fetchone()

        if pmd and pmd[3] is not None:
            close_str = float(pmd[3])

        # Rule-based scoring
        put_score, call_score = _rule_score(
            pc_skew_tag, gex_regime, close_str, front_iv_pct, term_tag, pc_skew_pp)
        margin = abs(put_score - call_score)

        # Decision gate: score >= 55, margin >= 10
        if call_score >= put_score and call_score >= 55 and margin >= 10:
            direction = "LONG_CALL"
        elif put_score > call_score and put_score >= 55 and margin >= 10:
            direction = "LONG_PUT"
        else:
            direction = "NO_TRADE"

        sel_score = max(put_score, call_score)

        # Price calculations
        put_strike  = round(spot * 0.975 / 5) * 5
        put_mid     = round(spot * front_iv * (9 / 252) ** 0.5 * 0.85, 2)
        call_strike = round(spot * 1.025 / 5) * 5
        call_mid    = round(spot * front_iv * (9 / 252) ** 0.5 * 0.40, 2)
        expiry_str  = (today + timedelta(days=9)).isoformat()

        # Chain hash
        prev_hash  = _get_prev_chain_hash(conn)
        chain_hash = _compute_chain_hash(job_id, ticker, str(today), trace_id, direction, prev_hash)

        # Mark DONE
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE options_pipeline_jobs
                SET status='DONE', completed_at=NOW(),
                    direction=%s, selected_score=%s,
                    trace_id=%s, chain_hash=%s
                WHERE id=%s
            """, (direction, round(sel_score, 1), trace_id, chain_hash, job_id))
        conn.commit()

        # Write paper trade if direction is a real trade
        if direction != "NO_TRADE":
            trade_type = "OPTION_PUT" if direction == "LONG_PUT" else "OPTION_CALL"
            entry_mid  = put_mid if direction == "LONG_PUT" else call_mid
            strike     = put_strike if direction == "LONG_PUT" else call_strike
            with conn.cursor() as cur:
                # Pre-check: avoid duplicate paper trade for same ticker/date/source
                # (aiem_paper_trades has no unique constraint — explicit guard required)
                cur.execute("""
                    SELECT id FROM aiem_paper_trades
                    WHERE trade_date = %s AND ticker = %s AND signal_source = %s
                    LIMIT 1
                """, (today, ticker, _TRIGGER))
                _pt_existing = cur.fetchone()

                if _pt_existing:
                    log.info(f"[exec] paper trade already exists id={_pt_existing[0]} "
                             f"for {ticker}/{today}/{_TRIGGER} — skipping duplicate")
                else:
                    cur.execute("""
                        INSERT INTO aiem_paper_trades
                            (trade_date, ticker, trade_type, entry_price, notional,
                             signal_source, signal_detail, audit_trace_id,
                             strike, expiry, entry_score, status)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'OPEN')
                    """, (
                        today, ticker, trade_type, entry_mid, 100.00,
                        _TRIGGER,
                        f"put={put_score:.0f} call={call_score:.0f} margin={margin:.0f} "
                        f"skew={pc_skew_tag} regime={gex_regime}",
                        trace_id, strike, expiry_str, round(sel_score, 2)
                    ))
                    conn.commit()
                    log.info(f"[exec] paper trade written: {ticker} {direction} "
                             f"strike={strike} entry={entry_mid} trace={trace_id}")
            conn.commit()

        elapsed = round(time.time() - t0, 2)
        log.info(f"[exec] DONE job_id={job_id} {ticker} → {direction} "
                 f"put={put_score:.0f} call={call_score:.0f} margin={margin:.0f} "
                 f"chain={chain_hash[:16]} elapsed={elapsed}s")

        return {
            "job_id":     job_id,
            "ticker":     ticker,
            "direction":  direction,
            "put_score":  put_score,
            "call_score": call_score,
            "sel_score":  sel_score,
            "trace_id":   trace_id,
            "chain_hash": chain_hash,
            "elapsed_s":  elapsed,
        }

    except Exception as e:
        err = str(e)[:400]
        log.error(f"[exec] FAILED job_id={job_id} {ticker}: {e}")
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE options_pipeline_jobs
                SET status='FAILED', error_text=%s, completed_at=NOW()
                WHERE id=%s
            """, (err, job_id))
        conn.commit()
        return {"job_id": job_id, "ticker": ticker, "direction": "FAILED",
                "error": err, "trace_id": trace_id}


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    if not _DB_URL:
        log.error("DATABASE_URL not set")
        sys.exit(1)

    now_et = datetime.now(_ET)
    today  = now_et.date()
    log.info(f"[backup] AIEM backup runner starting  trigger={_TRIGGER}  "
             f"time={now_et.strftime('%H:%M ET')}  date={today}")

    # Only run on weekdays
    if now_et.weekday() >= 5:
        log.info("[backup] weekend — nothing to do")
        sys.exit(0)

    conn = None
    try:
        conn = _db()
        _bootstrap(conn)

        # ── Dedup check ──────────────────────────────────────────────────────
        if _primary_already_ran(conn, today):
            log.info("[backup] PRIMARY ALREADY RAN — exiting cleanly (no duplicate)")
            _tg(f"ℹ️ <b>BACKUP RUNNER</b> [{_TRIGGER}]\n"
                f"Primary already completed {today} — no action needed.")
            sys.exit(0)

        # ── Advisory lock ────────────────────────────────────────────────────
        # Wait up to 2 min for primary to finish if it is mid-flight
        locked = False
        for attempt in range(12):
            if _try_lock(conn):
                locked = True
                break
            log.info(f"[backup] advisory lock held by another session — "
                     f"waiting 10s (attempt {attempt+1}/12)…")
            time.sleep(10)
            if _primary_already_ran(conn, today):
                log.info("[backup] primary finished while we waited — exiting")
                sys.exit(0)

        if not locked:
            log.warning("[backup] could not acquire advisory lock after 2 min — "
                        "primary may be mid-run; aborting to avoid duplicate")
            sys.exit(0)

        log.info("[backup] advisory lock acquired")

        # Re-check after lock (primary may have just finished)
        if _primary_already_ran(conn, today):
            log.info("[backup] DEDUP after lock — primary finished first")
            _unlock(conn)
            sys.exit(0)

        # ── Claim the day ────────────────────────────────────────────────────
        run_claim_id = f"{_TRIGGER}_{uuid.uuid4().hex[:12]}"
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO daily_pipeline_runs
                    (run_date, trigger_source, status, claim_id, started_at)
                VALUES (%s, %s, 'RUNNING', %s, NOW())
                ON CONFLICT (run_date, trigger_source) DO UPDATE
                    SET status='RUNNING', claim_id=EXCLUDED.claim_id,
                        started_at=NOW()
            """, (today, _TRIGGER, run_claim_id))
        conn.commit()

        # ── Polygon health check ─────────────────────────────────────────────
        rvol_rows = _polygon_rvol_check(conn, today)
        log.info(f"[backup] polygon_rvol_scan rows for {today}: {rvol_rows}")
        if rvol_rows == 0:
            log.warning("[backup] polygon_rvol_scan empty for today — "
                        "primary warm-up also missed; proceeding with OSS data")
            _polygon_grouped_daily_fetch(today)  # health ping only, not written to DB

        # OSS row count
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM options_structure_scan WHERE scan_date=%s", (today,))
            oss_rows = cur.fetchone()[0]
        log.info(f"[backup] options_structure_scan rows for {today}: {oss_rows}")

        # ── Seed candidates ──────────────────────────────────────────────────
        _tg(f"⚙️ <b>BACKUP RUNNER ACTIVATED</b> [{_TRIGGER}]\n"
            f"Primary VM missed 09:40–09:45 window.\n"
            f"date={today}  time={now_et.strftime('%H:%M ET')}\n"
            f"polygon_rvol_rows={rvol_rows}  oss_rows={oss_rows}\n"
            f"Seeding candidates + running pipeline now…")

        candidates = _seed_candidates(conn, today)
        seeded_count = len(candidates)

        if seeded_count == 0:
            # Primary may have already seeded but not finished executing.
            # Look for any PENDING jobs we can claim.
            log.info("[backup] seed=0 — checking for pre-existing PENDING jobs from primary")
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT j.ticker, j.scan_date,
                           o.pc_skew_pp, o.gex_regime, o.pc_skew_tag,
                           o.spot, o.front_iv, NULL
                    FROM options_pipeline_jobs j
                    JOIN options_structure_scan o
                      ON o.ticker = j.ticker AND o.scan_date = j.scan_date
                    WHERE j.scan_date = %s AND j.status = 'PENDING'
                    ORDER BY o.pc_skew_pp DESC NULLS LAST
                """, (today,))
                candidates = cur.fetchall()
            log.info(f"[backup] {len(candidates)} pre-existing PENDING job(s) found")

            if not candidates:
                log.warning("[backup] 0 candidates seeded and 0 PENDING jobs — nothing to do")
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE daily_pipeline_runs
                        SET status='NO_TRADE', completed_at=NOW(),
                            polygon_rvol_rows=%s, oss_rows=%s,
                            candidates_seeded=0, candidates_executed=0
                        WHERE run_date=%s AND trigger_source=%s
                    """, (rvol_rows, oss_rows, today, _TRIGGER))
                conn.commit()
                _tg(f"⚠️ <b>BACKUP RUNNER: NO CANDIDATES</b>\n"
                    f"options_structure_scan had no qualifying rows for {today}.\n"
                    f"oss_rows={oss_rows}")
                _unlock(conn)
                sys.exit(0)

        # ── Execute pipeline ─────────────────────────────────────────────────
        results  = []
        executed = no_trade = failed = dedup = 0
        trace_ids = []

        for cand in candidates:
            result = _execute_job(conn, cand, today)
            results.append(result)
            direction = result.get("direction", "FAILED")
            if direction == "DEDUP":
                dedup += 1
            elif direction == "FAILED":
                failed += 1
            elif direction == "NO_TRADE":
                no_trade += 1
                executed += 1
            else:
                executed += 1
            if result.get("trace_id"):
                trace_ids.append(result["trace_id"])

        log.info(f"[backup] pipeline complete: seeded={seeded_count} executed={executed} "
                 f"no_trade={no_trade} failed={failed} dedup={dedup}")

        # ── Update daily_pipeline_runs ────────────────────────────────────────
        final_trace = trace_ids[0] if trace_ids else None
        final_status = "COMPLETED" if (executed + dedup) > 0 else ("FAILED" if failed > 0 else "NO_TRADE")
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE daily_pipeline_runs
                SET status=%s, trace_id=%s, completed_at=NOW(),
                    polygon_rvol_rows=%s, oss_rows=%s,
                    candidates_seeded=%s, candidates_executed=%s,
                    candidates_no_trade=%s, candidates_failed=%s
                WHERE run_date=%s AND trigger_source=%s
            """, (final_status, final_trace, rvol_rows, oss_rows,
                  seeded_count, executed, no_trade, failed, today, _TRIGGER))
        conn.commit()

        # ── Update primary heartbeat table so primary knows backup ran ───────
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO job_heartbeats (job_name, last_success, last_attempt)
                VALUES (%s, NOW(), NOW())
                ON CONFLICT (job_name) DO UPDATE
                    SET last_success=NOW(), last_attempt=NOW()
            """, (f"backup_runner_{_TRIGGER}",))
        conn.commit()

        # ── Telegram summary ─────────────────────────────────────────────────
        lines = [f"✅ <b>BACKUP PIPELINE COMPLETE</b> [{_TRIGGER}]",
                 f"━━━━━━━━━━━━━━━━━━━━",
                 f"date={today}  status={final_status}",
                 f"seeded={seeded_count}  executed={executed}  "
                 f"no_trade={no_trade}  failed={failed}"]
        for r in results:
            if r.get("direction") not in ("DEDUP", "FAILED"):
                lines.append(
                    f"  {r['ticker']}: {r['direction']}  "
                    f"put={r.get('put_score',0):.0f}  call={r.get('call_score',0):.0f}  "
                    f"trace={r.get('trace_id','')}")
        lines.append(f"chain_hash={results[0].get('chain_hash','')[:24]}…" if results else "")
        _tg("\n".join(lines))

        log.info(f"[backup] ALL DONE  status={final_status}  "
                 f"trace_ids={trace_ids}")
        _unlock(conn)
        sys.exit(0)

    except Exception as e:
        log.error(f"[backup] FATAL: {e}", exc_info=True)
        _tg(f"🚨 <b>BACKUP RUNNER FATAL ERROR</b> [{_TRIGGER}]\n{str(e)[:300]}")
        if conn:
            try:
                _unlock(conn)
            except Exception:
                pass
        sys.exit(1)
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
