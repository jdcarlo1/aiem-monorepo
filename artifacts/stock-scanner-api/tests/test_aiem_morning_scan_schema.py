"""
Regression: aiem_morning_scan failed since ~2026-07-30 with
  column "score" does not exist
because _aiem_tool_scan_market_for_setups queried phantom columns on
conviction_stack_watchlist / call_sweep_log that do not exist in Neon.

Real Neon DDL (conviction_stack_watchlist):
  total_pts, conviction_pct, label, layers, meta, ...
Real unusual CALL flow table with data: unusual_calls_log (vol_oi, prem, first_seen).
"""
import ast
import os
from pathlib import Path

MAIN = Path(__file__).resolve().parents[1] / "main.py"


def _scan_fn_source() -> str:
    src = MAIN.read_text()
    start = src.find("def _aiem_tool_scan_market_for_setups")
    assert start > 0, "_aiem_tool_scan_market_for_setups missing"
    end = src.find("\ndef _aiem_tool_save_daily_predictions", start)
    assert end > start
    return src[start:end]


def test_morning_scan_does_not_query_phantom_score_columns():
    chunk = _scan_fn_source()
    # Strip docstring so historical bug names in comments don't false-fail
    body = chunk.split('"""', 2)[-1] if chunk.count('"""') >= 2 else chunk
    # Exact failing SELECT fragment from job_heartbeats last_error
    assert "SELECT ticker, score, confirmed_2d" not in body
    assert "AND score >= 4" not in body
    assert "ORDER BY score DESC" not in body
    assert "premium_usd" not in body
    assert "detected_at" not in body
    # Must not query empty/wrong-schema call_sweep_log
    assert "FROM call_sweep_log" not in body


def test_morning_scan_uses_neon_real_columns():
    chunk = _scan_fn_source()
    assert "total_pts" in chunk
    assert "conviction_pct" in chunk
    assert "unusual_calls_log" in chunk
    assert "FROM polygon_rvol_scan" in chunk
    # Parses as valid Python
    ast.parse(MAIN.read_text())


def test_morning_scan_isolates_sources_and_has_polygon_fallback():
    """2026-08-06 harden: one bad source must not abort Loop B."""
    chunk = _scan_fn_source()
    body = chunk.split('"""', 2)[-1] if chunk.count('"""') >= 2 else chunk
    assert "source_errors" in body
    assert "_aiem_indep_tool_stock_universe" in body
    assert "fallback_used" in body
    # Each source should be in its own try (at least 3 try blocks in body)
    assert body.count("try:") >= 3


def test_morning_scan_catchup_window_extends_to_1600_et():
    """Redeploy mid-afternoon must still be able to heal an empty day."""
    src = MAIN.read_text()
    # Catchup gate uses 16*60 (4:00 PM ET), not the old noon cutoff.
    assert "9 * 60 + 7 <= _hour_min_et < 16 * 60" in src
    assert "09:07–16:00 ET" in src or "09:07-16:00 ET" in src


def test_fixed_sql_runs_on_neon_when_available():
    """Live DB check — skip if DATABASE_URL / neon url unavailable."""
    url = os.environ.get("DATABASE_URL") or ""
    neon_path = "/tmp/neon_db_url"
    if not url and os.path.exists(neon_path):
        url = open(neon_path).read().strip()
    if not url:
        return  # CI without Neon — structural tests above still guard

    import psycopg2

    with psycopg2.connect(url, connect_timeout=15) as conn, conn.cursor() as cur:
        # Old phantom query must still fail (documents the bug)
        try:
            cur.execute(
                """
                SELECT ticker, score, confirmed_2d, high_conviction, scanner_count,
                       sweep_premium_m, float_m
                FROM conviction_stack_watchlist
                WHERE snap_date >= CURRENT_DATE - INTERVAL '2 days' AND score >= 4
                """
            )
            raise AssertionError("phantom score query should fail on Neon")
        except psycopg2.errors.UndefinedColumn:
            conn.rollback()

        # Fixed conviction query
        cur.execute(
            """
            SELECT ticker, total_pts,
                   (COALESCE(conviction_pct, 0) >= 70 OR COALESCE(total_pts, 0) >= 6),
                   (SELECT COUNT(*) FROM jsonb_each(COALESCE(layers, '{}'::jsonb))),
                   COALESCE((meta->>'sweep_prem')::float, 0) / 1e6,
                   NULLIF((meta->>'float_m')::float, 0)
            FROM conviction_stack_watchlist
            WHERE snap_date >= CURRENT_DATE - INTERVAL '5 days' AND total_pts >= 4
            ORDER BY snap_date DESC, total_pts DESC LIMIT 60
            """
        )
        cur.fetchall()

        # Fixed sweep query
        cur.execute(
            """
            SELECT ticker, MAX(vol_oi)::float, MAX(prem)::float / 1e3, COUNT(*)::int
            FROM unusual_calls_log
            WHERE first_seen >= NOW() - INTERVAL '2 days'
              AND vol_oi >= 2 AND prem >= 100000
            GROUP BY ticker ORDER BY MAX(vol_oi) DESC LIMIT 60
            """
        )
        sweeps = cur.fetchall()
        assert isinstance(sweeps, list)
