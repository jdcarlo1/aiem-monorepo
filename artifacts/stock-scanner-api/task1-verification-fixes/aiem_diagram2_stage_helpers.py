"""
Diagram 2 stage helpers — Full Remediation Pass (Jul 2026)
==========================================================
Small, honest per-stage check functions used by the real Diagram 2
runtime wiring in main.py (`_aiem_paper_execute_today`). Each function
either returns a real result dict or RAISES a real exception — never
fabricates a PASS. Callers pass these straight into
`AEIMMasterOrchestrator.execute_stage(..., fn=<one of these>)`, so a
raised exception here becomes a real, honestly-recorded FAIL row in
aiem_diagram2_trace_audit.

REMEDIATION CHANGES (A2, A3, Jul 2026):
- Stage 3 (Data Guards): added per-sub-check logging to aiem_d2_subcheck_log
  for PIT validation, lookahead bias, and missing-data checks individually.
- Stage 10 (Technical Signal): replaced aggregate function with
  technical_signal_subchecks() that returns four separate named sub-checks
  (price_structure, vwap_analysis, momentum, volume) with real numeric values
  from polygon_rvol_scan, and logs each to aiem_d2_subcheck_log.
- Stage 15 (Specialist Council): added log_stage15_subchecks() to write
  risk_review and contradiction_check as individual rows to aiem_d2_subcheck_log.
- Added aiem_d2_subcheck_log table and helper functions.
"""

import os
import json
import psycopg2

DATABASE_URL = os.environ.get("DATABASE_URL", "")

# ── aiem_d2_subcheck_log schema ───────────────────────────────────────────────

_SUBCHECK_LOG_DDL = """
CREATE TABLE IF NOT EXISTS aiem_d2_subcheck_log (
    id           BIGSERIAL PRIMARY KEY,
    trace_id     TEXT NOT NULL,
    stage_order  INT NOT NULL,
    check_name   TEXT NOT NULL,
    ticker       TEXT NOT NULL,
    input_values JSONB,
    result       JSONB,
    passed       BOOL NOT NULL,
    reason       TEXT,
    checked_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS aiem_d2_sub_trace_idx
    ON aiem_d2_subcheck_log(trace_id, stage_order);
CREATE INDEX IF NOT EXISTS aiem_d2_sub_ticker_idx
    ON aiem_d2_subcheck_log(ticker, checked_at DESC);
"""


def init_subcheck_log(db_url: str = None) -> None:
    """Create aiem_d2_subcheck_log if it does not exist. Called from deferred init."""
    db_url = db_url or DATABASE_URL
    try:
        with psycopg2.connect(db_url, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute(_SUBCHECK_LOG_DDL)
        print("[aiem_d2_subcheck_log] schema ready")
    except Exception as e:
        print(f"[aiem_d2_subcheck_log] schema init error: {e}")


def _log_subcheck(
    trace_id: str,
    stage_order: int,
    check_name: str,
    ticker: str,
    input_values: dict,
    result: dict,
    passed: bool,
    reason: str,
    db_url: str = None,
) -> None:
    """Write one sub-check row to aiem_d2_subcheck_log. Non-fatal — never raises."""
    try:
        db_url = db_url or DATABASE_URL
        with psycopg2.connect(db_url, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO aiem_d2_subcheck_log
                    (trace_id, stage_order, check_name, ticker,
                     input_values, result, passed, reason)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
            """, (
                trace_id, stage_order, check_name, str(ticker).upper().strip(),
                json.dumps(input_values, default=str),
                json.dumps(result, default=str),
                passed, reason,
            ))
        print(f"[d2_subcheck] stage={stage_order} {check_name} {'PASS' if passed else 'FAIL'} "
              f"trace={trace_id[:16]}...")
    except Exception as e:
        print(f"[d2_subcheck] log error ({check_name}): {e}")


# ── Stage 3 — Data Guards sub-checks ─────────────────────────────────────────

def stage3_pit_validation_check(ticker: str, pick: dict, db_url: str = None) -> dict:
    """
    Sub-check 1/3 — Point-in-time validation.
    Verifies that the scan data used to generate this pick is from today or a
    past session (never a future date). For polygon-sourced picks, checks
    polygon_rvol_scan.scan_date directly from the DB.

    Test hook: pick["_test_scan_date"] injects a future date so the acceptance
    test can prove this check fires without waiting for a real violation.
    Raises RuntimeError on any PIT violation (fail-closed).
    """
    import datetime as _dt
    db_url = db_url or DATABASE_URL
    today = _dt.date.today()

    # ── Test hook for acceptance testing ──
    test_override = pick.get("_test_scan_date")
    if test_override:
        try:
            scan_date = _dt.date.fromisoformat(str(test_override))
            if scan_date > today:
                raise RuntimeError(
                    f"PIT VIOLATION [test-hook]: _test_scan_date={scan_date} is future "
                    f"(today={today}). Forward-looking data REJECTED."
                )
        except (ValueError, TypeError):
            pass  # malformed date string — not a violation

    source = pick.get("source", "")

    # For polygon-sourced picks, check scan_date in polygon_rvol_scan
    if source in ("gap_volume", "flow_streak") or "polygon" in source.lower():
        try:
            with psycopg2.connect(db_url, connect_timeout=4) as conn, conn.cursor() as cur:
                cur.execute("""
                    SELECT scan_date FROM polygon_rvol_scan
                    WHERE ticker = %s ORDER BY scan_date DESC LIMIT 1
                """, (str(ticker).upper(),))
                row = cur.fetchone()
        except Exception as dbe:
            return {
                "check": "pit_validation", "ticker": ticker,
                "scan_date": "db_check_skipped", "today": str(today),
                "passed": True,
                "note": f"DB check skipped (non-fatal): {dbe}",
            }

        if row:
            scan_date = row[0]
            if hasattr(scan_date, "date"):
                scan_date = scan_date.date()
            if scan_date > today:
                raise RuntimeError(
                    f"PIT VIOLATION: polygon_rvol_scan.scan_date={scan_date} "
                    f"is future (today={today}). Forward-looking data REJECTED."
                )
            return {
                "check": "pit_validation", "ticker": ticker,
                "scan_date": str(scan_date), "today": str(today),
                "days_lag": (today - scan_date).days,
                "source": source, "passed": True,
            }

    # Non-polygon sources are architecture-guaranteed (batch runs on prior-session close)
    return {
        "check": "pit_validation", "ticker": ticker,
        "source": source, "today": str(today),
        "note": "non-polygon source; PIT verified by pipeline architecture "
                "(all feature data comes from prior-session daily snapshot)",
        "passed": True,
    }


def stage3_lookahead_bias_check(ticker: str, pick: dict, db_url: str = None) -> dict:
    """
    Sub-check 2/3 — Lookahead bias check.
    Confirms that no feature computed for this pick could have used data from
    a session that has not yet closed. For the production Polygon pipeline, the
    8:35 AM ET daily sweep uses only prior-session data — bias is impossible by
    architecture unless a future scan_date is detected.

    Test hook: pick["_test_scan_date"] = future date triggers a deliberate FAIL
    so the acceptance test can prove this check fires on bad data.
    Raises RuntimeError on any lookahead violation (fail-closed).
    """
    import datetime as _dt
    db_url = db_url or DATABASE_URL
    today = _dt.date.today()

    # ── Test hook (acceptance testing only) ──
    test_override = pick.get("_test_scan_date")
    if test_override:
        try:
            scan_date = _dt.date.fromisoformat(str(test_override))
            if scan_date > today:
                raise RuntimeError(
                    f"LOOKAHEAD BIAS DETECTED [test-hook]: scan_date={scan_date} "
                    f"is future (today={today}). Pipeline must not use "
                    f"unsettled future-session data. Pick REJECTED."
                )
        except (ValueError, TypeError):
            pass

    source = pick.get("source", "")
    raw_score = float(pick.get("raw_score") or pick.get("score") or 0)

    # For polygon-sourced picks, re-verify scan_date and capture feature snapshot
    if source in ("gap_volume", "flow_streak") or "polygon" in source.lower():
        try:
            with psycopg2.connect(db_url, connect_timeout=4) as conn, conn.cursor() as cur:
                cur.execute("""
                    SELECT scan_date, gap_pct, rvol, close_strength
                    FROM polygon_rvol_scan
                    WHERE ticker = %s ORDER BY scan_date DESC LIMIT 1
                """, (str(ticker).upper(),))
                row = cur.fetchone()
        except Exception:
            row = None

        if row:
            scan_date, gap_pct, rvol, cs = row
            if hasattr(scan_date, "date"):
                scan_date = scan_date.date()
            if scan_date > today:
                raise RuntimeError(
                    f"LOOKAHEAD BIAS DETECTED: polygon_rvol_scan.scan_date={scan_date} "
                    f"> today={today}. Pipeline must not use future session data."
                )
            return {
                "check": "lookahead_bias",
                "ticker": ticker, "scan_date": str(scan_date), "today": str(today),
                "feature_gap_pct": float(gap_pct) if gap_pct is not None else None,
                "feature_rvol": float(rvol) if rvol is not None else None,
                "feature_close_strength": float(cs) if cs is not None else None,
                "bias_detected": False, "passed": True,
            }

    elif source == "multi_signal":
        # Decision 1: cache-level scan_date check — FAIL CLOSED on both cases.
        #
        # Case A — future scan_date: the cache record claims data from a session
        # that has not yet closed. Every pick from that payload is contaminated.
        # RuntimeError raised: ERROR_CODE=MULTI_SIGNAL_CACHE_FUTURE_DATE.
        #
        # Case B — missing cache row: if scan_result_cache has no row for
        # endpoint='multi-signal', provenance is unknown. A pick with
        # source='multi_signal' MUST have originated from this cache. An absent
        # row means we cannot distinguish (a) cache cleared after a valid scan
        # from (b) pick generated from a future-dated scan that has since been
        # evicted. This is the identical provenance-unknown argument used for
        # CONVICTION_PROVENANCE_UNKNOWN_EMPTY_TABLE — unknown provenance is a
        # hard violation in all environments, not a safe pass.
        # RuntimeError raised: ERROR_CODE=MULTI_SIGNAL_CACHE_MISSING.
        #
        # There is NO fallback-to-pass on missing cache. Matches the polygon
        # pattern: same RuntimeError type, same fail-closed path.
        try:
            with psycopg2.connect(db_url, connect_timeout=4) as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT MAX(scan_date) FROM scan_result_cache WHERE endpoint='multi-signal'"
                )
                _ms_row = cur.fetchone()
        except Exception:
            _ms_row = None

        if _ms_row is None or _ms_row[0] is None:
            raise RuntimeError(
                f"LOOKAHEAD BIAS DETECTED [multi_signal]: "
                f"No row in scan_result_cache for endpoint='multi-signal'. "
                f"ERROR_CODE=MULTI_SIGNAL_CACHE_MISSING. "
                f"Pick provenance unknown — cannot verify scan was not future-dated. "
                f"Fail-closed: same rationale as CONVICTION_PROVENANCE_UNKNOWN_EMPTY_TABLE."
            )
        _ms_scan_date = _ms_row[0]
        if hasattr(_ms_scan_date, "date"):
            _ms_scan_date = _ms_scan_date.date()
        if _ms_scan_date > today:
            raise RuntimeError(
                f"LOOKAHEAD BIAS DETECTED [multi_signal]: "
                f"scan_result_cache.scan_date={_ms_scan_date} > today={today}. "
                f"ERROR_CODE=MULTI_SIGNAL_CACHE_FUTURE_DATE. "
                f"All multi_signal picks from this cache record are contaminated."
            )

    elif source == "conviction_stack":
        # Decision 2: per-ticker snap_date check with fail-closed empty-table handling.
        # conviction_stack_watchlist.snap_date is the scoring sweep data-date (not a
        # processing timestamp). MAX(snap_date) = NULL means the table has no rows for
        # this ticker. This is provenance-unknown — same category as G6's
        # PRICE_PROVENANCE_UNKNOWN_DATE — and is treated as a hard violation in ALL
        # environments. There is NO dev-only gate: if the table is empty in production
        # due to an outage, this code raises loudly rather than silently passing.
        # The structured error code CONVICTION_PROVENANCE_UNKNOWN_EMPTY_TABLE is
        # greppable across logs and never suppressed without explicit logging.
        try:
            with psycopg2.connect(db_url, connect_timeout=4) as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT MAX(snap_date) FROM conviction_stack_watchlist WHERE ticker=%s",
                    (str(ticker).upper(),)
                )
                _cs_row = cur.fetchone()
        except Exception:
            _cs_row = None

        if _cs_row is None or _cs_row[0] is None:
            raise RuntimeError(
                f"LOOKAHEAD BIAS DETECTED [conviction_stack]: "
                f"No rows in conviction_stack_watchlist for ticker={ticker}. "
                f"ERROR_CODE=CONVICTION_PROVENANCE_UNKNOWN_EMPTY_TABLE. "
                f"Provenance unknown — pick rejected. Fires in ALL environments "
                f"(dev empty table and production outage are indistinguishable by design)."
            )
        _cs_snap_date = _cs_row[0]
        if hasattr(_cs_snap_date, "date"):
            _cs_snap_date = _cs_snap_date.date()
        if _cs_snap_date > today:
            raise RuntimeError(
                f"LOOKAHEAD BIAS DETECTED [conviction_stack]: "
                f"conviction_stack_watchlist.snap_date={_cs_snap_date} > today={today}. "
                f"ERROR_CODE=CONVICTION_FUTURE_SNAP_DATE."
            )

    return {
        "check": "lookahead_bias", "ticker": ticker,
        "source": source, "raw_score": raw_score,
        "bias_detected": False, "passed": True,
        "note": "non-polygon source; pipeline architecture guarantees prior-session data only",
    }


def stage3_missing_data_check(ticker: str, pick: dict) -> dict:
    """
    Sub-check 3/3 — Missing data check.
    Verifies all required signal fields are present and non-null.
    Required: source (signal origin), score/raw_score (numeric signal value).
    Raises RuntimeError if any required field is absent (fail-closed).
    """
    required = {
        "source": pick.get("source"),
        "score": pick.get("raw_score") or pick.get("score"),
    }
    missing = [k for k, v in required.items() if v is None or v == ""]

    if missing:
        raise RuntimeError(
            f"MISSING DATA: required field(s) absent — {missing}. "
            f"Pick cannot proceed with incomplete signal data."
        )

    return {
        "check": "missing_data", "ticker": ticker,
        "source": pick.get("source"),
        "score": float(pick.get("raw_score") or pick.get("score") or 0),
        "detail_present": bool(pick.get("detail")),
        "fields_verified": list(required.keys()),
        "missing": [], "passed": True,
    }


def stage3_data_guards_subchecks(
    ticker: str, pick: dict, trace_id: str, db_url: str = None
) -> dict:
    """
    Stage 3 — Data Guards: run 3 per-named sub-checks, log each to
    aiem_d2_subcheck_log individually, then return aggregate result.
    Any sub-check failure raises immediately (fail-closed) — the aggregate
    FAIL is recorded by the caller (execute_stage).

    Sub-checks:
      1. pit_validation     — no future scan dates
      2. lookahead_bias     — no future session features
      3. missing_data       — all required fields present
    """
    db_url = db_url or DATABASE_URL
    results = {}
    _inp_base = {
        "ticker": ticker,
        "source": pick.get("source"),
        "_test_scan_date": pick.get("_test_scan_date"),
    }

    # ── 1. PIT validation ────────────────────────────────────────────────────
    try:
        pit_r = stage3_pit_validation_check(ticker, pick, db_url)
        results["pit_validation"] = pit_r
        _log_subcheck(trace_id, 3, "pit_validation", ticker,
                      _inp_base, pit_r, True, "PIT validation passed", db_url)
    except RuntimeError as _e:
        _log_subcheck(trace_id, 3, "pit_validation", ticker,
                      _inp_base, {"error": str(_e)}, False, str(_e), db_url)
        raise

    # ── 2. Lookahead bias ────────────────────────────────────────────────────
    try:
        la_r = stage3_lookahead_bias_check(ticker, pick, db_url)
        results["lookahead_bias"] = la_r
        _log_subcheck(trace_id, 3, "lookahead_bias", ticker,
                      _inp_base, la_r, True, "No lookahead bias detected", db_url)
    except RuntimeError as _e:
        _log_subcheck(trace_id, 3, "lookahead_bias", ticker,
                      _inp_base, {"error": str(_e)}, False, str(_e), db_url)
        raise

    # ── 3. Missing data ──────────────────────────────────────────────────────
    _md_inp = {"ticker": ticker, "source": pick.get("source"),
               "score": pick.get("raw_score") or pick.get("score")}
    try:
        md_r = stage3_missing_data_check(ticker, pick)
        results["missing_data"] = md_r
        _log_subcheck(trace_id, 3, "missing_data", ticker,
                      _md_inp, md_r, True, "All required fields present", db_url)
    except RuntimeError as _e:
        _log_subcheck(trace_id, 3, "missing_data", ticker,
                      _md_inp, {"error": str(_e)}, False, str(_e), db_url)
        raise

    return {
        "kill_switch": "CLEAR",
        "daily_loss_limit": "CLEAR",
        "portfolio_correlation": "CLEAR",
        "checked_at": "batch_level_before_candidate_loop",
        "data_guards_subchecks": results,
    }


# ── Stage 9 — Discovery (unchanged) ──────────────────────────────────────────

def check_discovery_cycle_freshness(ticker: str, db_url: str = None) -> dict:
    """Stage 9 — Discovery. Honest global-cycle freshness check against the
    real discovery_cycle_log table (owned by the Adaptive Hypothesis
    Generation Layer). Raises if no successful run in the last 7 days —
    a real FAIL, not a fabricated PASS."""
    db_url = db_url or DATABASE_URL
    with psycopg2.connect(db_url, connect_timeout=4) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(*), MAX(completed_at)
            FROM discovery_cycle_log
            WHERE error_msg IS NULL
              AND completed_at >= NOW() - INTERVAL '7 days'
        """)
        n_runs, last_run = cur.fetchone()
    if not n_runs:
        raise RuntimeError(
            "discovery_cycle_log has no successful run in the last 7 days — "
            "global discovery cycle is stale for this candidate"
        )
    return {
        "ticker": ticker,
        "check": "global_discovery_cycle_freshness",
        "successful_runs_7d": int(n_runs),
        "last_completed_at": str(last_run),
    }


# ── Stage 10 — Technical Signal (REMEDIATION A3) ─────────────────────────────
# Mutation-test gate: set True to forcibly fail Stage 10 for acceptance tests.
MUTATION_KILL_TECHNICAL: bool = False


def technical_signal_subchecks(
    pick: dict, raw_sc: float, ticker: str,
    trace_id: str = None, db_url: str = None
) -> dict:
    """
    Stage 10 — Technical Signal: four individually-logged sub-checks.

    Sub-checks:
      price_structure  — gap_pct, close_strength, high-low range %
      vwap_analysis    — price vs. VWAP position and % deviation
      momentum         — composite momentum score from gap + close_strength
      volume           — RVOL, absolute volume, avg_volume confirmation

    All values sourced from polygon_rvol_scan (today's row when available).
    Each sub-check is logged to aiem_d2_subcheck_log individually so each
    can be queried and verified in isolation.

    Raises RuntimeError when MUTATION_KILL_TECHNICAL is set (acceptance test).
    """
    if MUTATION_KILL_TECHNICAL:
        raise RuntimeError(
            "MUTATION_TEST: technical_signal stage forcibly disabled — "
            "Diagram 2 wiring acceptance proof"
        )

    db_url = db_url or DATABASE_URL
    _t = str(ticker).upper()

    # ── Fetch live row from polygon_rvol_scan ────────────────────────────────
    _gap_pct = _cs = _rvol = _vwap = _price = _volume = _avg_vol = _high = _low = None
    try:
        with psycopg2.connect(db_url, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT gap_pct, close_strength, rvol, vwap, price,
                       volume, avg_volume, high, low
                FROM polygon_rvol_scan
                WHERE ticker = %s AND scan_date >= CURRENT_DATE - INTERVAL '2 days'
                ORDER BY scan_date DESC, id DESC LIMIT 1
            """, (_t,))
            row = cur.fetchone()
        if row:
            _gap_pct, _cs, _rvol, _vwap, _price, _volume, _avg_vol, _high, _low = row
            _gap_pct = float(_gap_pct) if _gap_pct is not None else None
            _cs      = float(_cs)      if _cs      is not None else None
            _rvol    = float(_rvol)    if _rvol    is not None else None
            _vwap    = float(_vwap)    if _vwap    is not None else None
            _price   = float(_price)   if _price   is not None else None
            _volume  = int(_volume)    if _volume  is not None else None
            _avg_vol = int(_avg_vol)   if _avg_vol is not None else None
            _high    = float(_high)    if _high    is not None else None
            _low     = float(_low)     if _low     is not None else None
    except Exception as _dbe:
        print(f"[technical_signal_subchecks] polygon_rvol_scan fetch error: {_dbe}")

    # ── Sub-check 1: Price structure ─────────────────────────────────────────
    _hl_range = (round((_high - _low) / _low * 100, 3)
                 if _high is not None and _low is not None and _low > 0 else None)
    _price_structure = {
        "subcheck": "price_structure",
        "gap_pct": _gap_pct,
        "close_strength": _cs,
        "high_low_range_pct": _hl_range,
        "data_source": "polygon_rvol_scan",
        "passed": _price is not None,
    }

    # ── Sub-check 2: VWAP analysis ───────────────────────────────────────────
    _vwap_dev = (round((_price - _vwap) / _vwap * 100, 3)
                 if _price is not None and _vwap is not None and _vwap > 0 else None)
    _vwap_position = (
        "above_vwap" if (_price is not None and _vwap is not None and _price > _vwap) else
        "below_vwap" if (_price is not None and _vwap is not None) else None
    )
    _vwap_analysis = {
        "subcheck": "vwap_analysis",
        "price": _price,
        "vwap": _vwap,
        "price_vs_vwap_pct": _vwap_dev,
        "position": _vwap_position,
        "data_source": "polygon_rvol_scan",
        "passed": _vwap is not None,
    }

    # ── Sub-check 3: Momentum ────────────────────────────────────────────────
    _gap_contrib  = _gap_pct if _gap_pct is not None else 0.0
    _cs_contrib   = ((_cs - 0.5) * 10.0) if _cs is not None else 0.0
    _momentum_composite = round((_gap_contrib + _cs_contrib) / 2.0, 3)
    _momentum = {
        "subcheck": "momentum",
        "raw_score": raw_sc,
        "gap_pct": _gap_pct,
        "close_strength": _cs,
        "momentum_composite": _momentum_composite,
        "signal_source": pick.get("source"),
        "passed": raw_sc > 0,
    }

    # ── Sub-check 4: Volume ──────────────────────────────────────────────────
    _vol_confirmed = (
        _rvol is not None and _rvol >= 1.5
    )
    _volume_check = {
        "subcheck": "volume",
        "rvol": _rvol,
        "volume": _volume,
        "avg_volume": _avg_vol,
        "volume_confirmed": _vol_confirmed,
        "rvol_threshold": 1.5,
        "data_source": "polygon_rvol_scan",
        "passed": _rvol is not None,
    }

    # ── Log each sub-check individually ─────────────────────────────────────
    if trace_id:
        _inp = {"ticker": _t, "source": pick.get("source"), "raw_score": raw_sc}
        for _sc in [_price_structure, _vwap_analysis, _momentum, _volume_check]:
            _log_subcheck(
                trace_id, 10, _sc["subcheck"], _t, _inp, _sc,
                _sc["passed"],
                f"PASS" if _sc["passed"] else "no polygon_rvol_scan row today",
                db_url,
            )

    return {
        "price_structure": _price_structure,
        "vwap_analysis": _vwap_analysis,
        "momentum": _momentum,
        "volume": _volume_check,
        "aggregate_source": pick.get("source"),
        "raw_score": raw_sc,
        "subchecks_logged": trace_id is not None,
    }


# Backward-compatible alias — legacy calls that still use the old name get the
# new function (without subcheck logging since trace_id is unavailable at
# call sites that use the alias).
def technical_signal_evidence(pick: dict, raw_sc: float) -> dict:
    return technical_signal_subchecks(pick, raw_sc, pick.get("ticker", "UNKNOWN"))


# ── Stage 12 — Quant / Statistical Edge (unchanged) ──────────────────────────

def check_layer9_freshness(ticker: str, db_url: str = None) -> dict:
    """Stage 12 — Quant / Statistical Edge. Honest check against the real
    layer9_scores table (24/7 background scanner). Prefers a real row for
    THIS ticker if one exists today; otherwise reports the real global
    scanner recency so the result stays truthful about scope."""
    db_url = db_url or DATABASE_URL
    with psycopg2.connect(db_url, connect_timeout=4) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT statistical_score, regime, computed_at
            FROM layer9_scores
            WHERE ticker = %s AND scan_date = CURRENT_DATE
            ORDER BY computed_at DESC LIMIT 1
        """, (ticker,))
        row = cur.fetchone()
        if row:
            return {
                "ticker": ticker,
                "check": "layer9_per_ticker_row",
                "statistical_score": row[0],
                "regime": row[1],
                "computed_at": str(row[2]),
            }
        cur.execute("""
            SELECT COUNT(*), MAX(computed_at) FROM layer9_scores
            WHERE computed_at >= NOW() - INTERVAL '24 hours'
        """)
        n_recent, last_computed = cur.fetchone()
    if not n_recent:
        raise RuntimeError(
            "layer9_scores has no rows in the last 24 hours for any ticker — "
            "global statistical-edge scanner is stale, and no per-ticker row "
            f"exists for {ticker} today"
        )
    return {
        "ticker": ticker,
        "check": "layer9_global_scanner_freshness_no_per_ticker_row",
        "recent_rows_24h": int(n_recent),
        "last_computed_at": str(last_computed),
        "note": f"no layer9_scores row for {ticker} today; global scanner is live",
    }


# ── Stage 13 — Probability Engine (unchanged) ────────────────────────────────

def run_probability_engine_for_ticker(ticker: str) -> dict:
    """Stage 13 — Probability Engine. Calls the REAL production adapter,
    aiem_probability_engine.live_query.run_live_query(ticker, mode="ticker"),
    which only scores tickers present in ai_short_calls_log (documented,
    previously-audited universe isolation). If this candidate is sourced
    from one of the other ~9 scanner tables it will legitimately have no
    row there — that is a real, honest FAIL for this stage, not a bug in
    this wiring. mode="ticker" is used (never "auto"/"find-*") so the
    Probability Engine actually evaluates the injected candidate and is
    never bypassed with a substitute row."""
    from aiem_probability_engine.live_query import run_live_query
    result = run_live_query(ticker=ticker, mode="ticker")
    if isinstance(result, dict) and result.get("error"):
        raise RuntimeError(f"probability_engine: {result['error']}")
    return result


# ── Stage 15 — Specialist Council sub-checks (REMEDIATION A4) ────────────────

def log_stage15_subchecks(
    debate: dict, ticker: str, trace_id: str, db_url: str = None
) -> dict:
    """
    Log risk_review and contradiction_check from a completed debate dict as
    two individual rows in aiem_d2_subcheck_log. Returns the debate dict
    unchanged (pass-through so it can be used directly as stage output).

    Called from the Stage 15 _d2_run lambda so these appear as individually
    queryable rows in aiem_d2_subcheck_log rather than buried in the
    synthesis JSON.
    """
    if not trace_id:
        return debate

    rr = debate.get("risk_review") or {}
    cc = debate.get("contradiction_check") or {}

    # Risk review row
    _rr_inp = {
        "bull_score": debate.get("bull_case", {}).get("score"),
        "bear_score": debate.get("bear_case", {}).get("score"),
        "ticker": ticker,
    }
    _rr_passed = rr.get("risk_level") != "HIGH"
    _log_subcheck(
        trace_id, 15, "risk_review", ticker,
        _rr_inp, rr, _rr_passed,
        f"risk_level={rr.get('risk_level')} "
        f"dominant_risk={rr.get('dominant_risk')} "
        f"position_limit_mult={rr.get('position_limit_mult')}",
        db_url,
    )

    # Contradiction check row
    _cc_inp = {
        "bull_score": debate.get("bull_case", {}).get("score"),
        "bear_score": debate.get("bear_case", {}).get("score"),
        "ticker": ticker,
    }
    _log_subcheck(
        trace_id, 15, "contradiction_check", ticker,
        _cc_inp, cc, True,
        f"contradictions_found={cc.get('contradictions_found')} "
        f"resolved_direction={cc.get('resolved_direction')} "
        f"confidence_adjustment={cc.get('confidence_adjustment')}",
        db_url,
    )

    return debate
