"""
aiem_options_registries.py  —  Phase III Phase 1: Data Capture & Registries

Five DB tables provide full per-run observability for the standalone AIEM Options Engine.

Tables
------
  oe_indicator_registry   — canonical metadata per indicator (auto-discovered at runtime)
  oe_indicator_snapshots  — per-run per-indicator raw/normalised values + quality
  oe_pattern_registry     — canonical pattern definitions (candlestick / chart / harmonic / etc.)
  oe_pattern_snapshots    — per-run pattern occurrences with detection metadata
  oe_options_metrics      — complete options-chain snapshot at decision time (all Greeks + flow)

Design rules
------------
* Isolation: imports NOTHING from main.py / D1-D3 AIEM systems.
* Auto-discovery: subsystems call register_indicator() / register_pattern() at the point
  of computation — no static lists.  Missing registration → MISSING row → failure test fires.
* Non-fatal by default: every public call catches its own exceptions; pipeline never dies
  from a registry error unless assert_* raises RegistryValidationError.
* Failure gates (assert_*) are the ONLY path that can block a pipeline run:
    - assert_no_missing_indicators  — any required indicator with quality_status MISSING
    - assert_pattern_scan_complete  — pattern stage must have logged at least one entry
    - assert_data_freshness         — critical indicators must not be stale beyond threshold
"""

import hashlib
import json
import os
import threading
from datetime import datetime, date
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.extras

_DB_URL = os.environ.get("DATABASE_URL", "")

# ─────────────────────────────────────────────────────────────────────────────
# SENTINEL / EXCEPTION
# ─────────────────────────────────────────────────────────────────────────────

class RegistryValidationError(Exception):
    """Raised by assert_* functions when a required capture condition fails."""


# ─────────────────────────────────────────────────────────────────────────────
# IN-PROCESS REGISTRATION CACHE  (avoids repeated DB upserts per indicator)
# ─────────────────────────────────────────────────────────────────────────────

_REG_CACHE_LOCK = threading.Lock()
_INDICATOR_CACHE: Dict[str, str] = {}   # canonical_id → sha256
_PATTERN_CACHE:   Dict[str, str] = {}   # canonical_id → sha256

def _ind_sha(canonical_id: str, source_file: str, params: dict) -> str:
    payload = f"{canonical_id}|{source_file}|{json.dumps(params, sort_keys=True)}"
    return hashlib.sha256(payload.encode()).hexdigest()

def _pat_sha(canonical_id: str, source_file: str, detector_version: str) -> str:
    payload = f"{canonical_id}|{source_file}|{detector_version}"
    return hashlib.sha256(payload.encode()).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# BOOTSTRAP  (idempotent — call once at scheduler startup)
# ─────────────────────────────────────────────────────────────────────────────

_BOOTSTRAPPED = False
_BOOTSTRAP_LOCK = threading.Lock()


def bootstrap_registries(db_url: str = "") -> None:
    global _BOOTSTRAPPED
    with _BOOTSTRAP_LOCK:
        if _BOOTSTRAPPED:
            return
        db_url = db_url or _DB_URL
        try:
            with psycopg2.connect(db_url, connect_timeout=6) as conn, conn.cursor() as cur:

                # ── oe_indicator_registry ─────────────────────────────────────
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS oe_indicator_registry (
                        id               SERIAL PRIMARY KEY,
                        canonical_id     VARCHAR(128) UNIQUE NOT NULL,
                        name             VARCHAR(256)        NOT NULL,
                        family           VARCHAR(64),
                        source_file      VARCHAR(256),
                        source_function  VARCHAR(256),
                        parameters       JSONB    DEFAULT '{}',
                        timeframe        VARCHAR(32),
                        sha256           VARCHAR(64),
                        registered_at    TIMESTAMPTZ DEFAULT NOW(),
                        updated_at       TIMESTAMPTZ DEFAULT NOW()
                    )
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS oe_ir_family_idx
                        ON oe_indicator_registry(family)
                """)

                # ── oe_indicator_snapshots ────────────────────────────────────
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS oe_indicator_snapshots (
                        id                BIGSERIAL   PRIMARY KEY,
                        trace_id          VARCHAR(64) NOT NULL,
                        ticker            VARCHAR(20) NOT NULL,
                        scan_date         DATE        NOT NULL,
                        canonical_id      VARCHAR(128) NOT NULL,
                        raw_value         NUMERIC,
                        raw_value_text    TEXT,
                        normalized_value  NUMERIC,
                        signal_direction  VARCHAR(16),
                        confidence        NUMERIC,
                        signal_ts         TIMESTAMPTZ,
                        data_ts           TIMESTAMPTZ,
                        freshness_seconds INTEGER,
                        quality_status    VARCHAR(32) DEFAULT 'FRESH',
                        supported_decision BOOLEAN,
                        weight            NUMERIC,
                        contribution_score NUMERIC,
                        regime_context    VARCHAR(64),
                        captured_at       TIMESTAMPTZ DEFAULT NOW()
                    )
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS oe_is_trace_idx
                        ON oe_indicator_snapshots(trace_id, canonical_id)
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS oe_is_quality_idx
                        ON oe_indicator_snapshots(trace_id, quality_status)
                """)

                # ── oe_pattern_registry ───────────────────────────────────────
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS oe_pattern_registry (
                        id               SERIAL PRIMARY KEY,
                        canonical_id     VARCHAR(128) UNIQUE NOT NULL,
                        name             VARCHAR(256)        NOT NULL,
                        family           VARCHAR(64),
                        source_file      VARCHAR(256),
                        source_function  VARCHAR(256),
                        detector_version VARCHAR(32),
                        sha256           VARCHAR(64),
                        registered_at    TIMESTAMPTZ DEFAULT NOW()
                    )
                """)

                # ── oe_pattern_snapshots ──────────────────────────────────────
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS oe_pattern_snapshots (
                        id                     BIGSERIAL   PRIMARY KEY,
                        trace_id               VARCHAR(64) NOT NULL,
                        ticker                 VARCHAR(20) NOT NULL,
                        scan_date              DATE        NOT NULL,
                        canonical_id           VARCHAR(128) NOT NULL,
                        timeframe              VARCHAR(32),
                        detection_confidence   NUMERIC,
                        actionable             BOOLEAN,
                        influenced_recommendation BOOLEAN,
                        pattern_data           JSONB,
                        regime                 VARCHAR(64),
                        outcome                VARCHAR(32) DEFAULT 'OPEN',
                        failure_reason         TEXT,
                        mfe_pct                NUMERIC,
                        mae_pct                NUMERIC,
                        captured_at            TIMESTAMPTZ DEFAULT NOW()
                    )
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS oe_ps_trace_idx
                        ON oe_pattern_snapshots(trace_id)
                """)

                # ── oe_options_metrics ────────────────────────────────────────
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS oe_options_metrics (
                        id                BIGSERIAL   PRIMARY KEY,
                        trace_id          VARCHAR(64) NOT NULL,
                        alert_id          INTEGER,
                        ticker            VARCHAR(20) NOT NULL,
                        scan_date         DATE        NOT NULL,
                        direction         VARCHAR(12),
                        strike            NUMERIC,
                        expiry            DATE,
                        dte               INTEGER,
                        bid               NUMERIC,
                        ask               NUMERIC,
                        mid               NUMERIC,
                        last_price        NUMERIC,
                        spread            NUMERIC,
                        spread_pct        NUMERIC,
                        volume            INTEGER,
                        open_interest     INTEGER,
                        vol_oi_ratio      NUMERIC,
                        iv                NUMERIC,
                        iv_rank           NUMERIC,
                        iv_percentile     NUMERIC,
                        hv_20d            NUMERIC,
                        realized_vol      NUMERIC,
                        vrp               NUMERIC,
                        pc_skew_pp        NUMERIC,
                        pc_skew_tag       VARCHAR(32),
                        term_ratio        NUMERIC,
                        term_tag          VARCHAR(32),
                        front_iv          NUMERIC,
                        back_iv           NUMERIC,
                        expected_move     NUMERIC,
                        expected_move_pct NUMERIC,
                        probability_itm   NUMERIC,
                        pop               NUMERIC,
                        delta             NUMERIC,
                        gamma             NUMERIC,
                        theta             NUMERIC,
                        vega              NUMERIC,
                        rho               NUMERIC,
                        vanna             NUMERIC,
                        charm             NUMERIC,
                        vomma             NUMERIC,
                        speed             NUMERIC,
                        color             NUMERIC,
                        ultima            NUMERIC,
                        gex_m             NUMERIC,
                        gex_regime        VARCHAR(32),
                        gamma_flip_price  NUMERIC,
                        unusual_activity  BOOLEAN,
                        oi_buildup_pct    NUMERIC,
                        slippage_pct      NUMERIC,
                        fill_probability  NUMERIC,
                        breakeven         NUMERIC,
                        max_profit        NUMERIC,
                        max_loss          NUMERIC,
                        ev                NUMERIC,
                        return_on_risk    NUMERIC,
                        premium_at_risk   NUMERIC,
                        capital_requirement NUMERIC,
                        data_source       VARCHAR(32),
                        outcome           VARCHAR(32) DEFAULT 'OPEN',
                        pnl_pct           NUMERIC,
                        captured_at       TIMESTAMPTZ DEFAULT NOW()
                    )
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS oe_om_trace_dir_idx
                        ON oe_options_metrics(trace_id, direction)
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS oe_om_alert_idx
                        ON oe_options_metrics(alert_id)
                        WHERE alert_id IS NOT NULL
                """)

                conn.commit()
            _BOOTSTRAPPED = True
        except Exception as e:
            print(f"[oe_registries] WARNING: bootstrap failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# INDICATOR REGISTRY
# ─────────────────────────────────────────────────────────────────────────────

def register_indicator(
    canonical_id:    str,
    name:            str,
    family:          str,
    source_file:     str,
    source_function: str,
    params:          dict,
    db_url:          str = "",
) -> None:
    """
    Upsert a canonical indicator definition.  Idempotent — safe to call on
    every pipeline run.  Uses in-process cache to skip the DB round-trip when
    already registered in this process lifetime.
    """
    db_url = db_url or _DB_URL
    sha = _ind_sha(canonical_id, source_file, params)
    with _REG_CACHE_LOCK:
        if _INDICATOR_CACHE.get(canonical_id) == sha:
            return
    try:
        with psycopg2.connect(db_url, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO oe_indicator_registry
                    (canonical_id, name, family, source_file, source_function,
                     parameters, sha256, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (canonical_id) DO UPDATE
                    SET name=EXCLUDED.name, family=EXCLUDED.family,
                        source_file=EXCLUDED.source_file,
                        source_function=EXCLUDED.source_function,
                        parameters=EXCLUDED.parameters,
                        sha256=EXCLUDED.sha256,
                        updated_at=NOW()
            """, (canonical_id, name, family, source_file, source_function,
                  json.dumps(params), sha))
            conn.commit()
        with _REG_CACHE_LOCK:
            _INDICATOR_CACHE[canonical_id] = sha
    except Exception as e:
        print(f"[oe_registries] register_indicator {canonical_id} failed: {e}")


def snap_indicator(
    trace_id:          str,
    ticker:            str,
    scan_date,
    canonical_id:      str,
    raw_value,
    normalized_value:  Optional[float] = None,
    signal_direction:  str = "NEUTRAL",
    confidence:        Optional[float] = None,
    data_ts:           Optional[datetime] = None,
    freshness_seconds: Optional[int] = None,
    quality_status:    str = "FRESH",
    supported_decision: Optional[bool] = None,
    weight:            Optional[float] = None,
    contribution_score: Optional[float] = None,
    regime_context:    Optional[str] = None,
    raw_value_text:    Optional[str] = None,
    db_url:            str = "",
) -> None:
    """
    Record one indicator value for this pipeline run.
    raw_value=None → quality_status is forced to MISSING.
    """
    db_url = db_url or _DB_URL
    if raw_value is None and quality_status not in ("ERROR", "STALE"):
        quality_status = "MISSING"
    try:
        rv_num  = float(raw_value) if raw_value is not None else None
        rv_norm = float(normalized_value) if normalized_value is not None else None
        rv_text = raw_value_text or (str(raw_value) if raw_value is not None else None)
        with psycopg2.connect(db_url, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO oe_indicator_snapshots
                    (trace_id, ticker, scan_date, canonical_id,
                     raw_value, raw_value_text, normalized_value,
                     signal_direction, confidence, signal_ts, data_ts,
                     freshness_seconds, quality_status,
                     supported_decision, weight, contribution_score, regime_context)
                VALUES (%s,%s,%s,%s, %s,%s,%s, %s,%s,NOW(),%s, %s,%s, %s,%s,%s,%s)
            """, (
                trace_id, ticker.upper(), scan_date, canonical_id,
                rv_num, rv_text, rv_norm,
                signal_direction, confidence, data_ts,
                freshness_seconds, quality_status,
                supported_decision, weight, contribution_score, regime_context,
            ))
            conn.commit()
    except Exception as e:
        print(f"[oe_registries] snap_indicator {canonical_id} failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# PATTERN REGISTRY
# ─────────────────────────────────────────────────────────────────────────────

def register_pattern(
    canonical_id:     str,
    name:             str,
    family:           str,
    source_file:      str,
    source_function:  str,
    detector_version: str,
    db_url:           str = "",
) -> None:
    """Upsert a canonical pattern definition. Idempotent."""
    db_url = db_url or _DB_URL
    sha = _pat_sha(canonical_id, source_file, detector_version)
    with _REG_CACHE_LOCK:
        if _PATTERN_CACHE.get(canonical_id) == sha:
            return
    try:
        with psycopg2.connect(db_url, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO oe_pattern_registry
                    (canonical_id, name, family, source_file, source_function,
                     detector_version, sha256)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (canonical_id) DO UPDATE
                    SET name=EXCLUDED.name, family=EXCLUDED.family,
                        source_file=EXCLUDED.source_file,
                        source_function=EXCLUDED.source_function,
                        detector_version=EXCLUDED.detector_version,
                        sha256=EXCLUDED.sha256
            """, (canonical_id, name, family, source_file, source_function,
                  detector_version, sha))
            conn.commit()
        with _REG_CACHE_LOCK:
            _PATTERN_CACHE[canonical_id] = sha
    except Exception as e:
        print(f"[oe_registries] register_pattern {canonical_id} failed: {e}")


def snap_pattern(
    trace_id:                  str,
    ticker:                    str,
    scan_date,
    canonical_id:              str,
    timeframe:                 Optional[str] = None,
    detection_confidence:      Optional[float] = None,
    actionable:                Optional[bool] = None,
    influenced_recommendation: Optional[bool] = None,
    pattern_data:              Optional[dict] = None,
    regime:                    Optional[str] = None,
    db_url:                    str = "",
) -> None:
    """Record one pattern occurrence for this pipeline run."""
    db_url = db_url or _DB_URL
    try:
        with psycopg2.connect(db_url, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO oe_pattern_snapshots
                    (trace_id, ticker, scan_date, canonical_id, timeframe,
                     detection_confidence, actionable, influenced_recommendation,
                     pattern_data, regime)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                trace_id, ticker.upper(), scan_date, canonical_id, timeframe,
                detection_confidence, actionable, influenced_recommendation,
                json.dumps(pattern_data or {}), regime,
            ))
            conn.commit()
    except Exception as e:
        print(f"[oe_registries] snap_pattern {canonical_id} failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# OPTIONS METRICS CAPTURE
# ─────────────────────────────────────────────────────────────────────────────

def capture_options_metrics(
    trace_id:  str,
    ticker:    str,
    scan_date,
    direction: str,
    data:      dict,
    db_url:    str = "",
) -> None:
    """
    Capture the full options-chain metric snapshot for one direction (CALL or PUT).
    Called twice per run — once for call_data, once for put_data.
    alert_id is NULL at this point; call update_metrics_alert_id() after Stage 8.
    """
    db_url = db_url or _DB_URL

    def _f(key, default=None):
        v = data.get(key, default)
        return float(v) if v is not None else None

    def _i(key, default=None):
        v = data.get(key, default)
        return int(v) if v is not None else None

    # Derived fields
    bid  = _f("bid")
    ask  = _f("ask")
    mid  = round((bid + ask) / 2, 4) if bid is not None and ask is not None else None
    vol  = _f("volume")
    oi   = _f("open_interest")
    voi  = round(vol / oi, 4) if vol and oi else None
    iv   = _f("iv")
    iv_rank = _f("iv_rank")
    ev   = ((_f("probability_estimate") * _f("expected_return")) -
            ((1 - _f("probability_estimate")) * 1.0)
            if _f("probability_estimate") is not None and _f("expected_return") is not None
            else None)
    # return_on_risk = probability-weighted expected return per unit of capital at risk.
    # Old formula (profit_target / premium_at_risk) was a per-share / per-contract mismatch
    # that always produced 0.016 (PUTs) or 0.010 (CALLs) — not a real metric.
    # ev is already the correct quantity: pop * expected_return_ratio - (1-pop) * 1.0,
    # where expected_return_ratio comes from lognormal payoff analysis and varies per option.
    rr   = round(ev, 4) if ev is not None else None

    try:
        with psycopg2.connect(db_url, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO oe_options_metrics (
                    trace_id, ticker, scan_date, direction,
                    bid, ask, mid, spread, spread_pct,
                    volume, open_interest, vol_oi_ratio,
                    iv, iv_rank,
                    expected_move, expected_move_pct,
                    probability_itm, pop,
                    delta, gamma, theta, vega,
                    slippage_pct, fill_probability,
                    breakeven, max_loss, ev, return_on_risk,
                    premium_at_risk,
                    dte, data_source
                ) VALUES (
                    %s,%s,%s,%s,
                    %s,%s,%s,%s,%s,
                    %s,%s,%s,
                    %s,%s,
                    %s,%s,
                    %s,%s,
                    %s,%s,%s,%s,
                    %s,%s,
                    %s,%s,%s,%s,
                    %s,
                    %s,%s
                )
            """, (
                trace_id, ticker.upper(), scan_date, direction.upper(),
                bid, ask, mid,
                round(ask - bid, 4) if ask and bid else None,
                _f("bid_ask_spread_pct"),
                _i("volume"), _i("open_interest"), voi,
                iv, iv_rank,
                _f("expected_move"), _f("expected_move_pct"),
                _f("probability_estimate"), _f("probability_estimate"),
                _f("delta"), _f("gamma"), _f("theta"), _f("vega"),
                _f("slippage_pct"), None,
                _f("breakeven"), _f("premium_at_risk"), ev, rr,
                _f("premium_at_risk"),
                _i("dte"), data.get("_data_source", "BS_TRADIER"),
            ))
            conn.commit()
    except Exception as e:
        print(f"[oe_registries] capture_options_metrics {direction} failed: {e}")


def update_metrics_alert_id(trace_id: str, alert_id: int, db_url: str = "") -> None:
    """Backfill alert_id on oe_options_metrics rows after Stage 8 save."""
    db_url = db_url or _DB_URL
    try:
        with psycopg2.connect(db_url, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                UPDATE oe_options_metrics SET alert_id=%s
                WHERE trace_id=%s AND alert_id IS NULL
            """, (alert_id, trace_id))
            conn.commit()
    except Exception as e:
        print(f"[oe_registries] update_metrics_alert_id failed: {e}")


def update_metrics_outcome(
    trace_id: str,
    outcome:  str,
    pnl_pct:  Optional[float],
    db_url:   str = "",
) -> None:
    """Record WIN/LOSS/EXPIRED_WORTHLESS and pnl_pct on oe_options_metrics at grading time."""
    db_url = db_url or _DB_URL
    try:
        with psycopg2.connect(db_url, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                UPDATE oe_options_metrics
                SET outcome=%s, pnl_pct=%s
                WHERE trace_id=%s
            """, (outcome, pnl_pct, trace_id))
            conn.commit()
    except Exception as e:
        print(f"[oe_registries] update_metrics_outcome failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# ENRICHMENT: push OSS + pattern fields into options_metrics after full run
# ─────────────────────────────────────────────────────────────────────────────

def enrich_metrics_oss(
    trace_id:         str,
    pc_skew_pp:       Optional[float],
    pc_skew_tag:      Optional[str],
    term_ratio:       Optional[float],
    term_tag:         Optional[str],
    front_iv:         Optional[float],
    back_iv:          Optional[float],
    gex_m:            Optional[float],
    gex_regime:       Optional[str],
    gamma_flip_price: Optional[float],
    iv_rank:          Optional[float],
    db_url:           str = "",
) -> None:
    """Update OSS-derived columns on oe_options_metrics (same for CALL and PUT rows)."""
    db_url = db_url or _DB_URL
    try:
        with psycopg2.connect(db_url, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                UPDATE oe_options_metrics
                SET pc_skew_pp=%s, pc_skew_tag=%s,
                    term_ratio=%s,  term_tag=%s,
                    front_iv=%s,    back_iv=%s,
                    gex_m=%s,       gex_regime=%s,
                    gamma_flip_price=%s,
                    iv_rank=%s
                WHERE trace_id=%s
            """, (
                pc_skew_pp, pc_skew_tag, term_ratio, term_tag,
                front_iv, back_iv, gex_m, gex_regime, gamma_flip_price,
                iv_rank, trace_id,
            ))
            conn.commit()
    except Exception as e:
        print(f"[oe_registries] enrich_metrics_oss failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# FAILURE GATES  (assert_* raise RegistryValidationError to block pipeline)
# ─────────────────────────────────────────────────────────────────────────────

def assert_no_missing_indicators(
    trace_id:     str,
    required_ids: List[str],
    db_url:       str = "",
) -> None:
    """
    Raise RegistryValidationError if any required indicator has quality_status='MISSING'
    in oe_indicator_snapshots for this trace_id.

    Called by _execute_job before Stage 5 REQ6 scoring.
    """
    db_url = db_url or _DB_URL
    try:
        with psycopg2.connect(db_url, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT canonical_id, quality_status
                FROM oe_indicator_snapshots
                WHERE trace_id = %s
                  AND canonical_id = ANY(%s)
                  AND quality_status IN ('MISSING', 'ERROR')
            """, (trace_id, required_ids))
            bad_rows = cur.fetchall()

            # Also detect required IDs that were never snapped at all
            cur.execute("""
                SELECT canonical_id FROM oe_indicator_snapshots
                WHERE trace_id = %s AND canonical_id = ANY(%s)
            """, (trace_id, required_ids))
            snapped_ids = {r[0] for r in cur.fetchall()}
            never_snapped = [cid for cid in required_ids if cid not in snapped_ids]

    except Exception as e:
        raise RegistryValidationError(f"DB query failed in assert_no_missing: {e}")

    failures = []
    for cid, status in bad_rows:
        failures.append(f"{cid}={status}")
    for cid in never_snapped:
        failures.append(f"{cid}=NEVER_SNAPPED")

    if failures:
        raise RegistryValidationError(
            f"Required indicators not captured: {', '.join(failures)}"
        )


def assert_pattern_scan_complete(trace_id: str, db_url: str = "") -> None:
    """
    Raise RegistryValidationError if the pattern-scan stage produced no snapshots
    for this trace_id.  Fires when aiem_pattern_engine import failed entirely.
    """
    db_url = db_url or _DB_URL
    try:
        with psycopg2.connect(db_url, connect_timeout=4) as conn, conn.cursor() as cur:
            # Either a pattern_snapshot row exists, or PAT_SCORE was snapped as FRESH/STALE
            cur.execute("""
                SELECT COUNT(*)
                FROM oe_indicator_snapshots
                WHERE trace_id = %s
                  AND canonical_id = 'PAT_SCORE'
                  AND quality_status NOT IN ('ERROR')
            """, (trace_id,))
            count = cur.fetchone()[0]
    except Exception as e:
        raise RegistryValidationError(f"DB query failed in assert_pattern_scan: {e}")

    if count == 0:
        raise RegistryValidationError(
            f"Pattern scan stage produced no PAT_SCORE snapshot for trace_id={trace_id}"
        )


def assert_data_freshness(
    trace_id:          str,
    critical_ids:      List[str],
    max_stale_seconds: int,
    db_url:            str = "",
) -> None:
    """
    Raise RegistryValidationError if any critical indicator has
    freshness_seconds > max_stale_seconds in oe_indicator_snapshots.

    Typical usage: max_stale_seconds=172800 (48h) for EOD Polygon data.
    """
    db_url = db_url or _DB_URL
    try:
        with psycopg2.connect(db_url, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT canonical_id, freshness_seconds
                FROM oe_indicator_snapshots
                WHERE trace_id = %s
                  AND canonical_id = ANY(%s)
                  AND freshness_seconds IS NOT NULL
                  AND freshness_seconds > %s
            """, (trace_id, critical_ids, max_stale_seconds))
            stale_rows = cur.fetchall()
    except Exception as e:
        raise RegistryValidationError(f"DB query failed in assert_data_freshness: {e}")

    if stale_rows:
        stale_desc = ", ".join(
            f"{cid}={int(s)}s (>{max_stale_seconds}s)"
            for cid, s in stale_rows
        )
        raise RegistryValidationError(f"Stale data detected: {stale_desc}")


# ─────────────────────────────────────────────────────────────────────────────
# CONVENIENCE: dump registry for admin / verification use
# ─────────────────────────────────────────────────────────────────────────────

def update_metrics_alert_id(
    trace_id: str,
    alert_id: int,
    db_url:   str = "",
) -> None:
    """
    Back-fill alert_id on oe_options_metrics rows once the alert has been
    persisted in Stage 8 (save_options_alert returns alert_id).
    """
    db_url = db_url or _DB_URL
    try:
        with psycopg2.connect(db_url, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                UPDATE oe_options_metrics
                SET alert_id=%s
                WHERE trace_id=%s AND alert_id IS NULL
            """, (alert_id, trace_id))
            conn.commit()
    except Exception as e:
        print(f"[oe_registries] update_metrics_alert_id tid={trace_id} failed: {e}")


def update_metrics_outcome_by_alert(
    alert_id: int,
    outcome:  str,
    pnl_pct:  Optional[float],
    db_url:   str = "",
) -> None:
    """
    Record WIN/LOSS/EXPIRED_WORTHLESS + pnl_pct on oe_options_metrics rows
    that belong to the given alert_id.  Called by grade_options_outcomes.
    """
    db_url = db_url or _DB_URL
    try:
        with psycopg2.connect(db_url, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                UPDATE oe_options_metrics
                SET outcome=%s, pnl_pct=%s
                WHERE alert_id=%s
            """, (outcome, pnl_pct, alert_id))
            conn.commit()
    except Exception as e:
        print(f"[oe_registries] update_metrics_outcome_by_alert aid={alert_id} failed: {e}")


def get_registry_summary(db_url: str = "") -> dict:
    """Return counts and families from oe_indicator_registry."""
    db_url = db_url or _DB_URL
    try:
        with psycopg2.connect(db_url, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM oe_indicator_registry")
            n_indicators = cur.fetchone()[0]
            cur.execute("SELECT family, COUNT(*) FROM oe_indicator_registry GROUP BY family ORDER BY COUNT(*) DESC")
            families = {r[0]: r[1] for r in cur.fetchall()}
            cur.execute("SELECT COUNT(*) FROM oe_pattern_registry")
            n_patterns = cur.fetchone()[0]
            cur.execute("SELECT COUNT(DISTINCT trace_id) FROM oe_indicator_snapshots")
            n_runs_snapped = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM oe_options_metrics")
            n_metrics = cur.fetchone()[0]
        return {
            "indicators_registered": n_indicators,
            "families": families,
            "patterns_registered": n_patterns,
            "runs_with_indicator_snapshots": n_runs_snapped,
            "options_metrics_rows": n_metrics,
        }
    except Exception as e:
        return {"error": str(e)}


def get_run_snapshot(trace_id: str, db_url: str = "") -> dict:
    """Return all indicator/pattern snapshots for one trace_id. Used by verify_chain.sh."""
    db_url = db_url or _DB_URL
    try:
        with psycopg2.connect(db_url, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT canonical_id, raw_value, raw_value_text, normalized_value,
                       signal_direction, quality_status, freshness_seconds, captured_at
                FROM oe_indicator_snapshots
                WHERE trace_id = %s
                ORDER BY captured_at
            """, (trace_id,))
            indicators = [
                dict(zip(["canonical_id","raw_value","raw_value_text","normalized_value",
                          "signal_direction","quality_status","freshness_seconds","captured_at"],
                         r))
                for r in cur.fetchall()
            ]
            cur.execute("""
                SELECT canonical_id, detection_confidence, actionable, regime, captured_at
                FROM oe_pattern_snapshots
                WHERE trace_id = %s
                ORDER BY captured_at
            """, (trace_id,))
            patterns = [
                dict(zip(["canonical_id","detection_confidence","actionable","regime","captured_at"], r))
                for r in cur.fetchall()
            ]
            cur.execute("""
                SELECT direction, delta, gamma, theta, vega, iv, iv_rank,
                       probability_itm, pop, volume, open_interest,
                       expected_move, slippage_pct, premium_at_risk,
                       data_source, outcome
                FROM oe_options_metrics
                WHERE trace_id = %s
                ORDER BY direction
            """, (trace_id,))
            cols = ["direction","delta","gamma","theta","vega","iv","iv_rank",
                    "probability_itm","pop","volume","open_interest",
                    "expected_move","slippage_pct","premium_at_risk","data_source","outcome"]
            metrics = [dict(zip(cols, r)) for r in cur.fetchall()]
        return {
            "trace_id":   trace_id,
            "indicators": indicators,
            "patterns":   patterns,
            "metrics":    metrics,
            "counts": {
                "indicators": len(indicators),
                "patterns":   len(patterns),
                "metrics":    len(metrics),
            },
        }
    except Exception as e:
        return {"error": str(e)}
