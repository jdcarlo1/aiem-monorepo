"""
aiem_options_phase4.py  —  Phase III Phase 4: Portfolio & Operational Learning
================================================================================
Sections 15-17 of the AIEM Standalone Options Engine Phase III directive.

15. Portfolio Learning           → oe_portfolio_context
16. No-Trade Learning            → oe_no_trade_candidates
17. Operational/System Learning  → oe_incidents

Isolation:  zero imports from D1/D2/D3.  All tables prefixed oe_.
Failure:    every public function is non-fatal — log and return, never raise.
Data guard: no delete/truncate on any existing row.
Stats gate: min n=20 before statistical claims; statistical_claim=False when n<20.
Portfolio guard: violated_limits≠[] AND pnl>0 → decision_quality=BAD; KB refuses SUCCESS_TRADE.
Operational guard: OPERATIONAL incidents never carry model root-cause categories.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("aiem_options_phase4")

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

_MIN_N_FOR_STATS     = 20    # minimum sample for statistical claims

# Portfolio limits (paper trading engine) — env-overridable, defaults unchanged
_MAX_OPEN_POSITIONS  = int(os.environ.get("OE_MAX_OPEN_POSITIONS", "10") or 10)
_MAX_SINGLE_TICKER   = int(os.environ.get("OE_MAX_SINGLE_TICKER", "2") or 2)
_MAX_TOTAL_RISK_USD  = float(os.environ.get("OE_MAX_TOTAL_RISK_USD", "20000") or 20000)

# Incident taxonomy — deterministic, order matters (first match wins).
# All incidents in this module are OPERATIONAL by design; MODEL errors are
# never recorded as incidents.
_INCIDENT_PATTERNS: List[Tuple[str, str, str, str]] = [
    # (lowercased_substr, failure_type, classification, recommendation)
    ("missing polygon/oss", "MISSING_DATA",    "OPERATIONAL",
     "Check polygon_market_daily + options_structure_scan for scan_date/ticker."),
    ("missing data",        "MISSING_DATA",    "OPERATIONAL",
     "Upstream data missing for ticker/date; verify Polygon API quota."),
    ("no snapshot",         "MISSING_DATA",    "OPERATIONAL",
     "Polygon snapshot unavailable; check Polygon API or scan pipeline."),
    ("stale_claimed_reset", "STALE_CLAIM",     "OPERATIONAL",
     "Job stuck in CLAIMED >5 min; stale-claim recovery reset it to PENDING."),
    ("stale_executing_reset","STALE_CLAIM",    "OPERATIONAL",
     "Job stuck in EXECUTING >10 min; stale-claim recovery reset it."),
    ("rate limit",          "RATE_LIMIT",      "OPERATIONAL",
     "API rate-limit hit; add backoff or reduce scan frequency."),
    ("429",                 "RATE_LIMIT",      "OPERATIONAL",
     "HTTP 429 from upstream API; implement exponential backoff."),
    ("connection timeout",  "DB_ERROR",        "OPERATIONAL",
     "DB connection timeout; check pool health and DB availability."),
    ("psycopg2",            "DB_ERROR",        "OPERATIONAL",
     "DB error; check connection string, schema, and pool config."),
    ("worker crash",        "WORKER_CRASH",    "OPERATIONAL",
     "Worker process crashed; check VM memory and error logs."),
    ("schedulermiss",       "SCHEDULER_MISS",  "OPERATIONAL",
     "Scheduled job did not run; check VM uptime and APScheduler status."),
    ("on conflict",         "DUPLICATE",       "OPERATIONAL",
     "Duplicate job attempt; dedup guard functioning correctly."),
    ("duplicate",           "DUPLICATE",       "OPERATIONAL",
     "Duplicate execution detected; dedup gate should prevent double-processing."),
    ("hash mismatch",       "CHAIN_FAILURE",   "OPERATIONAL",
     "SHA-256 audit chain integrity failure; do not count as model error."),
    ("verification chain",  "CHAIN_FAILURE",   "OPERATIONAL",
     "Audit chain break; investigate stage hash inputs."),
]
_DEFAULT_INCIDENT = ("UNKNOWN_OPERATIONAL", "OPERATIONAL",
                     "Investigate error_text manually for root cause.")


# ─────────────────────────────────────────────────────────────────────────────
# BOOTSTRAP
# ─────────────────────────────────────────────────────────────────────────────

def bootstrap_phase4(db_url: str = "") -> bool:
    """
    Create Phase 4 tables if they do not exist.
    Returns True on success, False on failure.
    """
    import psycopg2
    url = db_url or os.environ.get("DATABASE_URL", "")
    try:
        with psycopg2.connect(url, connect_timeout=6) as conn, conn.cursor() as cur:

            # ── Section 15: Portfolio context at decision time ─────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS oe_portfolio_context (
                    id                      BIGSERIAL PRIMARY KEY,
                    alert_id                INTEGER,
                    trace_id                VARCHAR(64),
                    ticker                  VARCHAR(12) NOT NULL,
                    scan_date               DATE        NOT NULL,
                    snapshot_ts             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    n_open_positions        INTEGER     NOT NULL DEFAULT 0,
                    total_max_risk_usd      NUMERIC     NOT NULL DEFAULT 0,
                    ticker_concentration    JSONB       NOT NULL DEFAULT '{}',
                    portfolio_delta         NUMERIC,
                    portfolio_gamma         NUMERIC,
                    portfolio_theta         NUMERIC,
                    portfolio_vega          NUMERIC,
                    violated_limits         JSONB       NOT NULL DEFAULT '[]',
                    any_violation           BOOLEAN     NOT NULL DEFAULT FALSE,
                    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE(trace_id)
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_oe_pc_scan_date
                    ON oe_portfolio_context(scan_date)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_oe_pc_violation
                    ON oe_portfolio_context(any_violation)
            """)

            # ── Section 16: No-Trade candidates ───────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS oe_no_trade_candidates (
                    id                      BIGSERIAL PRIMARY KEY,
                    job_id                  INTEGER     NOT NULL,
                    trace_id                VARCHAR(64),
                    ticker                  VARCHAR(12) NOT NULL,
                    scan_date               DATE        NOT NULL,
                    call_score              NUMERIC,
                    put_score               NUMERIC,
                    rejection_reasons       JSONB       NOT NULL DEFAULT '[]',
                    market_snapshot         JSONB       NOT NULL DEFAULT '{}',
                    spot_at_rejection       NUMERIC,
                    expected_move_pct       NUMERIC,
                    spot_t1                 NUMERIC,
                    spot_t5                 NUMERIC,
                    outcome_classification  VARCHAR(32),
                    classified_at           TIMESTAMPTZ,
                    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE(job_id)
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_oe_ntc_scan_date
                    ON oe_no_trade_candidates(scan_date)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_oe_ntc_classification
                    ON oe_no_trade_candidates(outcome_classification)
            """)

            # ── Section 17: Operational incidents ─────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS oe_incidents (
                    id                      BIGSERIAL PRIMARY KEY,
                    incident_ts             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    ticker                  VARCHAR(12),
                    scan_date               DATE,
                    failure_source          VARCHAR(100) NOT NULL,
                    failure_type            VARCHAR(50)  NOT NULL,
                    classification          VARCHAR(20)  NOT NULL DEFAULT 'OPERATIONAL',
                    error_text              TEXT,
                    remediation             TEXT,
                    reference_id            VARCHAR(200),
                    resolved                BOOLEAN     NOT NULL DEFAULT FALSE,
                    resolved_at             TIMESTAMPTZ,
                    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE(failure_source, reference_id, failure_type)
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_oe_inc_failure_type
                    ON oe_incidents(failure_type)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_oe_inc_ticker
                    ON oe_incidents(ticker)
            """)

            conn.commit()
            log.info("[phase4] bootstrap: oe_portfolio_context, "
                     "oe_no_trade_candidates, oe_incidents — ready")
            return True

    except Exception as exc:
        log.error(f"[phase4] bootstrap_phase4 failed: {exc}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 17 — OPERATIONAL & SYSTEM-FAILURE LEARNING
# ─────────────────────────────────────────────────────────────────────────────

def classify_incident(error_text: str,
                      failure_source: str = "") -> Tuple[str, str, str]:
    """
    Pure function — no DB access.
    Deterministically map (error_text, failure_source) →
    (failure_type, classification, recommendation).

    classification is always 'OPERATIONAL'; MODEL errors are never recorded
    as incidents.  failure_type drives remediation routing.
    """
    needle = (error_text or "").lower()
    for (pattern, ftype, cls, rec) in _INCIDENT_PATTERNS:
        if pattern in needle:
            return (ftype, cls, rec)
    # fallback: still OPERATIONAL
    return _DEFAULT_INCIDENT


def record_incident(
    failure_source: str,
    error_text:     str,
    ticker:         Optional[str]  = None,
    scan_date:      Optional[date] = None,
    reference_id:   Optional[str]  = None,
    db_url:         str            = "",
) -> dict:
    """
    Classify and persist one operational incident.
    Idempotent: ON CONFLICT (failure_source, reference_id, failure_type) DO NOTHING.
    Returns {'recorded': bool, 'failure_type': str, 'classification': str}.
    """
    import psycopg2
    url   = db_url or os.environ.get("DATABASE_URL", "")
    ref   = reference_id or f"{ticker or ''}_{scan_date or ''}"
    ftype, cls, rec = classify_incident(error_text, failure_source)
    try:
        with psycopg2.connect(url, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO oe_incidents
                    (failure_source, failure_type, classification,
                     error_text, remediation, ticker, scan_date,
                     reference_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (failure_source, reference_id, failure_type) DO NOTHING
            """, (failure_source, ftype, cls, error_text, rec,
                  ticker, scan_date, ref))
            recorded = cur.rowcount > 0
            conn.commit()
            if recorded:
                log.info(f"[phase4] incident recorded: source={failure_source} "
                         f"type={ftype} ticker={ticker} scan_date={scan_date}")
            return {"recorded": recorded, "failure_type": ftype,
                    "classification": cls, "remediation": rec}
    except Exception as exc:
        log.error(f"[phase4] record_incident failed: {exc}")
        return {"recorded": False, "failure_type": ftype,
                "classification": cls, "error": str(exc)}


def scan_operational_failures(days_back: int = 7, db_url: str = "") -> dict:
    """
    Scan job_heartbeats, options_pipeline_jobs, and daily_pipeline_runs for
    unrecorded operational failures.  Records each as an oe_incidents row.

    Never mislabels operational failures as model errors.
    Returns {'scanned': N, 'new_incidents': M, 'sources': [...]}.
    """
    import psycopg2
    url      = db_url or os.environ.get("DATABASE_URL", "")
    new      = 0
    sources  = []
    cutoff   = date.today() - timedelta(days=days_back)

    try:
        with psycopg2.connect(url, connect_timeout=6) as conn, conn.cursor() as cur:

            # ── job_heartbeats: last_error IS NOT NULL ──────────────────────
            cur.execute("""
                SELECT job_name, last_error, last_attempt
                FROM job_heartbeats
                WHERE last_error IS NOT NULL
            """)
            for job_name, err_text, last_attempt in cur.fetchall():
                ftype, _, _ = classify_incident(err_text or "", job_name)
                # Only record if clearly operational (all are, by design)
                ref = f"jh_{job_name}"
                cur2_results = _record_if_new(
                    conn, failure_source=f"job_heartbeats:{job_name}",
                    error_text=err_text or "",
                    ticker=None, scan_date=None, reference_id=ref,
                )
                if cur2_results:
                    new += 1
                    sources.append(f"job_heartbeats:{job_name}")

            # ── options_pipeline_jobs: status='FAILED' ──────────────────────
            cur.execute("""
                SELECT id, ticker, scan_date, error_text
                FROM options_pipeline_jobs
                WHERE status = 'FAILED'
                  AND scan_date >= %s
            """, (cutoff,))
            for job_id, ticker, sd, err_text in cur.fetchall():
                ref = f"opj_{job_id}"
                result = _record_if_new(
                    conn, failure_source="options_pipeline_jobs",
                    error_text=err_text or "job failed",
                    ticker=ticker, scan_date=sd, reference_id=ref,
                )
                if result:
                    new += 1
                    sources.append(f"pipeline_job:{job_id}:{ticker}")

            # ── daily_pipeline_runs: FAILED or stranded SCHEDULED ──────────
            cur.execute("""
                SELECT run_date, trigger_source, status
                FROM daily_pipeline_runs
                WHERE (status = 'FAILED'
                       OR (status = 'SCHEDULED' AND run_date < CURRENT_DATE))
                  AND run_date >= %s
            """, (cutoff,))
            for run_date, trigger_src, status in cur.fetchall():
                err = (f"daily_pipeline_run status={status} "
                       f"trigger={trigger_src} run_date={run_date}")
                ref = f"dpr_{run_date}_{trigger_src}"
                result = _record_if_new(
                    conn, failure_source="daily_pipeline_runs",
                    error_text=err, ticker=None, scan_date=run_date,
                    reference_id=ref,
                )
                if result:
                    new += 1
                    sources.append(f"pipeline_run:{run_date}:{status}")

            conn.commit()

    except Exception as exc:
        log.error(f"[phase4] scan_operational_failures failed: {exc}")
        return {"scanned": 0, "new_incidents": 0, "sources": [], "error": str(exc)}

    log.info(f"[phase4] scan_operational_failures: new={new}")
    return {"scanned": len(sources), "new_incidents": new, "sources": sources}


def _record_if_new(conn, failure_source: str, error_text: str,
                   ticker, scan_date, reference_id: str) -> bool:
    """
    Insert one incident row.  Returns True if a new row was inserted.
    Caller must commit.
    """
    ftype, cls, rec = classify_incident(error_text, failure_source)
    ref = reference_id or f"{ticker}_{scan_date}"
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO oe_incidents
                (failure_source, failure_type, classification,
                 error_text, remediation, ticker, scan_date, reference_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (failure_source, reference_id, failure_type) DO NOTHING
        """, (failure_source, ftype, cls, error_text, rec,
              ticker, scan_date, ref))
        return cur.rowcount > 0


def get_incident_report(days_back: int = 30, db_url: str = "") -> dict:
    """
    Return a summary of incidents over the last N days.
    """
    import psycopg2
    url = db_url or os.environ.get("DATABASE_URL", "")
    try:
        with psycopg2.connect(url, connect_timeout=4) as conn, conn.cursor() as cur:
            cutoff = date.today() - timedelta(days=days_back)
            cur.execute("""
                SELECT failure_type, classification, COUNT(*) AS n
                FROM oe_incidents
                WHERE created_at >= %s
                GROUP BY failure_type, classification
                ORDER BY n DESC
            """, (cutoff,))
            by_type = [{"failure_type": r[0], "classification": r[1],
                        "count": r[2]} for r in cur.fetchall()]
            cur.execute("""
                SELECT COUNT(*) FROM oe_incidents
                WHERE created_at >= %s
            """, (cutoff,))
            total = cur.fetchone()[0]
            cur.execute("""
                SELECT COUNT(*) FROM oe_incidents
                WHERE created_at >= %s AND classification='OPERATIONAL'
            """, (cutoff,))
            operational = cur.fetchone()[0]
            return {"total": total, "operational": operational,
                    "model": total - operational, "by_type": by_type}
    except Exception as exc:
        log.error(f"[phase4] get_incident_report failed: {exc}")
        return {"total": 0, "operational": 0, "model": 0, "by_type": [],
                "error": str(exc)}


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 16 — NO-TRADE LEARNING
# ─────────────────────────────────────────────────────────────────────────────

def record_no_trade_candidate(
    job_id:            int,
    trace_id:          str,
    ticker:            str,
    scan_date:         date,
    call_score:        float,
    put_score:         float,
    rejection_reasons: List[str],
    market_snapshot:   Dict[str, Any],
    spot_at_rejection: Optional[float],
    expected_move_pct: Optional[float] = None,
    db_url:            str             = "",
) -> dict:
    """
    Persist one NO_TRADE candidate for outcome tracking.
    Idempotent: ON CONFLICT (job_id) DO NOTHING.
    """
    import psycopg2
    url = db_url or os.environ.get("DATABASE_URL", "")
    try:
        with psycopg2.connect(url, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO oe_no_trade_candidates
                    (job_id, trace_id, ticker, scan_date,
                     call_score, put_score, rejection_reasons,
                     market_snapshot, spot_at_rejection, expected_move_pct)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (job_id) DO NOTHING
            """, (job_id, trace_id, ticker, scan_date,
                  call_score, put_score,
                  json.dumps(rejection_reasons),
                  json.dumps(market_snapshot),
                  spot_at_rejection, expected_move_pct))
            recorded = cur.rowcount > 0
            conn.commit()
            log.info(f"[phase4] no_trade_candidate: job_id={job_id} "
                     f"{ticker} {scan_date} recorded={recorded}")
            return {"recorded": recorded, "job_id": job_id,
                    "ticker": ticker, "scan_date": str(scan_date)}
    except Exception as exc:
        log.error(f"[phase4] record_no_trade_candidate failed: {exc}")
        return {"recorded": False, "error": str(exc)}


def backfill_no_trade_candidates(db_url: str = "") -> dict:
    """
    Backfill oe_no_trade_candidates from existing options_pipeline_jobs rows
    with direction='NO_TRADE'.  Uses polygon_market_daily for spot price.
    Code-driven backfill — no manual DB inserts.
    """
    import psycopg2
    url = db_url or os.environ.get("DATABASE_URL", "")
    filled = 0
    skipped = 0
    try:
        with psycopg2.connect(url, connect_timeout=6) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT opj.id, opj.ticker, opj.scan_date, opj.trace_id,
                       opj.selected_score, opj.error_text
                FROM options_pipeline_jobs opj
                LEFT JOIN oe_no_trade_candidates ntc ON ntc.job_id = opj.id
                WHERE opj.direction = 'NO_TRADE'
                  AND ntc.id IS NULL
            """)
            rows = cur.fetchall()
            for job_id, ticker, sd, trace_id, sel_score, err_text in rows:
                # Attempt to get spot price from polygon_market_daily
                cur.execute("""
                    SELECT close_price, vwap
                    FROM polygon_market_daily
                    WHERE ticker = %s AND scan_date = %s
                    LIMIT 1
                """, (ticker, sd))
                pmd = cur.fetchone()
                spot = float(pmd[0]) if pmd else None

                # Attempt to get expected_move_pct from options_structure_scan
                cur.execute("""
                    SELECT gex_regime, front_iv
                    FROM options_structure_scan
                    WHERE ticker = %s AND scan_date = %s
                    LIMIT 1
                """, (ticker, sd))
                oss = cur.fetchone()

                rejection_reasons = [err_text] if err_text else ["NO_TRADE: neither direction meets score+margin gates"]
                market_snapshot = {
                    "source": "backfill",
                    "scan_date": str(sd),
                    "close_price": float(pmd[0]) if pmd else None,
                    "vwap":        float(pmd[1]) if pmd else None,
                    "gex_regime":  oss[0] if oss else None,
                    "front_iv":    float(oss[1]) if oss else None,
                }

                cur.execute("""
                    INSERT INTO oe_no_trade_candidates
                        (job_id, trace_id, ticker, scan_date,
                         call_score, put_score, rejection_reasons,
                         market_snapshot, spot_at_rejection, expected_move_pct)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (job_id) DO NOTHING
                """, (job_id, trace_id, ticker, sd,
                      float(sel_score) if sel_score else None,
                      float(sel_score) if sel_score else None,
                      json.dumps(rejection_reasons),
                      json.dumps(market_snapshot),
                      spot, None))
                if cur.rowcount > 0:
                    filled += 1
                else:
                    skipped += 1

            conn.commit()

    except Exception as exc:
        log.error(f"[phase4] backfill_no_trade_candidates failed: {exc}")
        return {"filled": 0, "skipped": 0, "error": str(exc)}

    log.info(f"[phase4] backfill_no_trade_candidates: filled={filled} skipped={skipped}")
    return {"filled": filled, "skipped": skipped}


def track_no_trade_outcomes(days_back: int = 30, db_url: str = "") -> dict:
    """
    For NO_TRADE candidates with scan_date <= today-5, look up T+1 and T+5
    spot prices from polygon_market_daily and classify as:
      FALSE_REJECTION  — either direction would have been profitable
      CORRECT_REJECTION — neither direction was profitable
      UNDETERMINED      — price data not yet available

    FALSE_REJECTION: price moved > expected_move_pct (or >3% default) in
    either direction within 5 trading days.
    """
    import psycopg2
    url    = db_url or os.environ.get("DATABASE_URL", "")
    graded = 0
    cutoff = date.today() - timedelta(days=days_back)
    price_cutoff = date.today() - timedelta(days=5)

    try:
        with psycopg2.connect(url, connect_timeout=6) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT id, ticker, scan_date, spot_at_rejection, expected_move_pct
                FROM oe_no_trade_candidates
                WHERE outcome_classification IS NULL
                  AND scan_date >= %s
                  AND scan_date <= %s
            """, (cutoff, price_cutoff))
            rows = cur.fetchall()

            for ntc_id, ticker, sd, spot_orig, emp in rows:
                if spot_orig is None:
                    continue

                spot_base = float(spot_orig)
                move_threshold = float(emp) if emp else 0.03

                # T+1: first trading day after scan_date
                cur.execute("""
                    SELECT close_price, scan_date AS price_date
                    FROM polygon_market_daily
                    WHERE ticker = %s AND scan_date > %s
                    ORDER BY scan_date ASC
                    LIMIT 1
                """, (ticker, sd))
                t1_row = cur.fetchone()
                spot_t1 = float(t1_row[0]) if t1_row else None

                # T+5: 5th trading day after scan_date
                cur.execute("""
                    SELECT close_price
                    FROM polygon_market_daily
                    WHERE ticker = %s AND scan_date > %s
                    ORDER BY scan_date ASC
                    LIMIT 1 OFFSET 4
                """, (ticker, sd))
                t5_row = cur.fetchone()
                spot_t5 = float(t5_row[0]) if t5_row else None

                if spot_t5 is None:
                    classification = "UNDETERMINED"
                else:
                    pct_move = abs(spot_t5 - spot_base) / spot_base if spot_base > 0 else 0
                    if pct_move >= move_threshold:
                        classification = "FALSE_REJECTION"
                    else:
                        classification = "CORRECT_REJECTION"

                cur.execute("""
                    UPDATE oe_no_trade_candidates
                    SET spot_t1               = %s,
                        spot_t5               = %s,
                        outcome_classification = %s,
                        classified_at          = NOW()
                    WHERE id = %s
                """, (spot_t1, spot_t5, classification, ntc_id))
                graded += 1

            conn.commit()

    except Exception as exc:
        log.error(f"[phase4] track_no_trade_outcomes failed: {exc}")
        return {"graded": 0, "error": str(exc)}

    log.info(f"[phase4] track_no_trade_outcomes: graded={graded}")
    return {"graded": graded}


def compute_rejection_rates(db_url: str = "") -> dict:
    """
    Compute false-rejection and correct-rejection rates.

    When n < _MIN_N_FOR_STATS (20), returns statistical_claim=False and
    reports the rate arithmetic honestly with the caveat.  Never fabricates
    statistical significance on small samples.
    """
    import psycopg2
    url = db_url or os.environ.get("DATABASE_URL", "")
    try:
        with psycopg2.connect(url, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT
                    COUNT(*) AS n_total,
                    SUM(CASE WHEN outcome_classification IS NOT NULL
                             AND outcome_classification != 'UNDETERMINED'
                             THEN 1 ELSE 0 END) AS n_classified,
                    SUM(CASE WHEN outcome_classification = 'FALSE_REJECTION'
                             THEN 1 ELSE 0 END) AS n_false,
                    SUM(CASE WHEN outcome_classification = 'CORRECT_REJECTION'
                             THEN 1 ELSE 0 END) AS n_correct
                FROM oe_no_trade_candidates
            """)
            row = cur.fetchone()
            n_total, n_classified, n_false, n_correct = (
                int(row[0] or 0), int(row[1] or 0),
                int(row[2] or 0), int(row[3] or 0),
            )

            false_rate   = (n_false   / n_classified) if n_classified > 0 else None
            correct_rate = (n_correct / n_classified) if n_classified > 0 else None
            below_gate   = n_classified < _MIN_N_FOR_STATS

            return {
                "n_total":              n_total,
                "n_classified":         n_classified,
                "n_false_rejection":    n_false,
                "n_correct_rejection":  n_correct,
                "false_rejection_rate": round(false_rate,   4) if false_rate   is not None else None,
                "correct_rejection_rate": round(correct_rate, 4) if correct_rate is not None else None,
                "statistical_claim":    not below_gate,
                "reason":               (f"n_classified={n_classified} < "
                                         f"min_n={_MIN_N_FOR_STATS}; rates are "
                                         f"descriptive only") if below_gate else "n>=20",
            }
    except Exception as exc:
        log.error(f"[phase4] compute_rejection_rates failed: {exc}")
        return {"n_total": 0, "n_classified": 0, "statistical_claim": False,
                "error": str(exc)}


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 15 — PORTFOLIO LEARNING
# ─────────────────────────────────────────────────────────────────────────────

def _derive_portfolio_book(conn) -> dict:
    """
    Derive current open book from aiem_options_alerts WHERE outcome_status='OPEN'.
    Returns book metrics needed for limit checks.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT ticker, direction, max_premium_risk,
                   delta_val, gamma_val, theta_val, vega_val
            FROM aiem_options_alerts
            WHERE outcome_status = 'OPEN'
        """)
        rows = cur.fetchall()

    n_open      = len(rows)
    total_risk  = sum(float(r[2] or 0) for r in rows)
    ticker_counts: Dict[str, int] = {}
    p_delta = p_gamma = p_theta = p_vega = 0.0

    for ticker, direction, risk, delta, gamma, theta, vega in rows:
        ticker_counts[ticker] = ticker_counts.get(ticker, 0) + 1
        p_delta += float(delta or 0)
        p_gamma += float(gamma or 0)
        p_theta += float(theta or 0)
        p_vega  += float(vega  or 0)

    return {
        "n_open_positions":  n_open,
        "total_risk_usd":    total_risk,
        "ticker_counts":     ticker_counts,
        "portfolio_delta":   round(p_delta, 4),
        "portfolio_gamma":   round(p_gamma, 4),
        "portfolio_theta":   round(p_theta, 4),
        "portfolio_vega":    round(p_vega,  4),
    }


def _check_portfolio_limits(book: dict) -> List[str]:
    """
    Check book against portfolio limits.
    Returns list of violated limit descriptions (empty = no violations).
    """
    violations: List[str] = []

    if book["n_open_positions"] >= _MAX_OPEN_POSITIONS:
        violations.append(
            f"MAX_OPEN_POSITIONS: {book['n_open_positions']} >= {_MAX_OPEN_POSITIONS}"
        )

    for ticker, cnt in book["ticker_counts"].items():
        if cnt > _MAX_SINGLE_TICKER:
            violations.append(
                f"MAX_SINGLE_TICKER: {ticker} appears {cnt}x > {_MAX_SINGLE_TICKER}"
            )

    if book["total_risk_usd"] >= _MAX_TOTAL_RISK_USD:
        violations.append(
            f"MAX_TOTAL_RISK_USD: ${book['total_risk_usd']:.0f} >= ${_MAX_TOTAL_RISK_USD}"
        )

    return violations


def capture_portfolio_context(
    alert_id:  Optional[int],
    trace_id:  str,
    ticker:    str,
    scan_date: date,
    db_url:    str = "",
) -> dict:
    """
    Snapshot the portfolio book state at decision time and check limits.
    Called BEFORE the new alert is committed — so the new trade is not yet
    in the open book.  Idempotent: ON CONFLICT (trace_id) DO UPDATE.
    """
    import psycopg2
    url = db_url or os.environ.get("DATABASE_URL", "")
    try:
        with psycopg2.connect(url, connect_timeout=6) as conn:
            book       = _derive_portfolio_book(conn)
            violations = _check_portfolio_limits(book)
            any_viol   = len(violations) > 0

            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO oe_portfolio_context
                        (alert_id, trace_id, ticker, scan_date,
                         n_open_positions, total_max_risk_usd,
                         ticker_concentration,
                         portfolio_delta, portfolio_gamma,
                         portfolio_theta, portfolio_vega,
                         violated_limits, any_violation)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (trace_id) DO UPDATE
                        SET alert_id          = EXCLUDED.alert_id,
                            violated_limits   = EXCLUDED.violated_limits,
                            any_violation     = EXCLUDED.any_violation
                """, (
                    alert_id, trace_id, ticker, scan_date,
                    book["n_open_positions"],
                    book["total_risk_usd"],
                    json.dumps(book["ticker_counts"]),
                    book["portfolio_delta"], book["portfolio_gamma"],
                    book["portfolio_theta"], book["portfolio_vega"],
                    json.dumps(violations), any_viol,
                ))
                conn.commit()

            log.info(f"[phase4] portfolio_context: ticker={ticker} "
                     f"n_open={book['n_open_positions']} "
                     f"violations={len(violations)} any_viol={any_viol}")
            return {
                "captured": True, "n_open": book["n_open_positions"],
                "total_risk_usd": book["total_risk_usd"],
                "violations": violations, "any_violation": any_viol,
            }

    except Exception as exc:
        log.error(f"[phase4] capture_portfolio_context failed: {exc}")
        return {"captured": False, "error": str(exc)}


def apply_portfolio_learning_guard(
    alert_id: int,
    pnl_pct:  float,
    db_url:   str = "",
) -> dict:
    """
    Section 15 enforcement: if a trade was profitable (pnl_pct > 0) AND
    it violated portfolio limits at entry, force decision_quality='BAD'.

    A profitable trade that violates portfolio objectives must NOT be learned
    as acceptable.  This is a hard invariant — not a heuristic.

    Returns:
      decision_quality : 'PASS' | 'BAD' | 'UNKNOWN'
      violated_limits  : list of violated limit strings
      reason           : explanation
    """
    import psycopg2
    url = db_url or os.environ.get("DATABASE_URL", "")
    try:
        with psycopg2.connect(url, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT violated_limits, any_violation
                FROM oe_portfolio_context
                WHERE alert_id = %s
                LIMIT 1
            """, (alert_id,))
            row = cur.fetchone()

            if row is None:
                return {
                    "decision_quality": "UNKNOWN",
                    "violated_limits":  [],
                    "reason":           "no_portfolio_context_row_for_alert_id",
                }

            violated_raw, any_viol = row
            violated = (json.loads(violated_raw)
                        if isinstance(violated_raw, str)
                        else (violated_raw or []))

            if not any_viol or pnl_pct <= 0:
                return {
                    "decision_quality": "PASS",
                    "violated_limits":  violated,
                    "reason":           ("no_violations" if not any_viol
                                         else "pnl_not_positive"),
                }

            # Profitable + violations → FORCE BAD
            log.warning(
                f"[phase4] portfolio_guard: alert_id={alert_id} pnl_pct={pnl_pct:.2%} "
                f"FORCED BAD — violations: {violated}"
            )
            return {
                "decision_quality": "BAD",
                "violated_limits":  violated,
                "reason":           "profitable_but_portfolio_limits_violated_at_entry",
            }

    except Exception as exc:
        log.error(f"[phase4] apply_portfolio_learning_guard failed: {exc}")
        return {"decision_quality": "UNKNOWN", "violated_limits": [],
                "reason": f"error: {exc}"}


def backfill_portfolio_context(db_url: str = "") -> dict:
    """
    Best-effort backfill for existing graded alerts (approximate — book state
    at original entry time is not fully reconstructable, but current-state
    violation check is applied as a conservative lower bound).
    Only fills rows that don't yet exist in oe_portfolio_context.
    """
    import psycopg2
    url    = db_url or os.environ.get("DATABASE_URL", "")
    filled = 0
    try:
        with psycopg2.connect(url, connect_timeout=6) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT a.id, a.ticker, a.alert_date
                FROM aiem_options_alerts a
                LEFT JOIN oe_portfolio_context pc ON pc.alert_id = a.id
                WHERE pc.id IS NULL
                ORDER BY a.id
            """)
            rows = cur.fetchall()

        for alert_id, ticker, alert_date in rows:
            trace = f"backfill_{alert_id}"
            capture_portfolio_context(
                alert_id=alert_id, trace_id=trace,
                ticker=ticker, scan_date=alert_date, db_url=url,
            )
            filled += 1

    except Exception as exc:
        log.error(f"[phase4] backfill_portfolio_context failed: {exc}")
        return {"filled": 0, "error": str(exc)}

    log.info(f"[phase4] backfill_portfolio_context: filled={filled}")
    return {"filled": filled}


def get_portfolio_learning_report(db_url: str = "") -> dict:
    """
    Summary of portfolio-context captures and learning-guard decisions.
    """
    import psycopg2
    url = db_url or os.environ.get("DATABASE_URL", "")
    try:
        with psycopg2.connect(url, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM oe_portfolio_context")
            total = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM oe_portfolio_context WHERE any_violation=TRUE")
            with_violations = cur.fetchone()[0]
            # Graded alerts that had violations AND positive pnl
            cur.execute("""
                SELECT COUNT(*)
                FROM oe_portfolio_context pc
                JOIN aiem_options_alerts a ON a.id = pc.alert_id
                WHERE pc.any_violation = TRUE
                  AND a.pnl_pct > 0
                  AND a.outcome_status NOT IN ('OPEN','PENDING')
            """)
            profitable_violated = cur.fetchone()[0]
            return {
                "total_captures":          total,
                "with_violations":         with_violations,
                "profitable_and_violated": profitable_violated,
                "guard_status":            ("no_profitable_violated_graded_yet"
                                            if profitable_violated == 0
                                            else "guard_active"),
            }
    except Exception as exc:
        log.error(f"[phase4] get_portfolio_learning_report failed: {exc}")
        return {"total_captures": 0, "error": str(exc)}
