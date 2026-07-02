"""
aiem_stat_tests.py — Shared statistical testing utilities for AIEM signal discovery.

Implements non-overlapping (bucketed) Fisher's exact test as the authoritative default
for Module 5 (discovery) and Module 6 (rediscovery/variation testing).

Problem with the overlapping method
------------------------------------
The old approach counted every (ticker, scan_date) row that met the condition as an
independent observation. When the same ticker fires on consecutive days, the H-day
forward-return windows of adjacent rows share days, violating the independence assumption
of Fisher's exact test. The resulting p-values are mildly anti-conservative (they
overstate statistical confidence in proportion to how often signals cluster on the same
ticker in consecutive days).

Non-overlapping (bucketed) fix
--------------------------------
Each ticker's full history is divided into non-overlapping H-day calendar buckets using
ROW_NUMBER() within the ticker's scan_date order. Within each bucket only the earliest
observation (by scan_date) is kept. This guarantees that any two retained observations
for the same ticker are always ≥H trading days apart, so their H-day forward-return
windows never share a day. The effective n is approximately n_overlapping / H.

Both methods are available for comparison via `run_fisher_test_overlapping` (legacy)
and `run_fisher_test` (default, non-overlapping). Only the non-overlapping version
should be used for promotion/validation decisions.

LAG-aware extension
--------------------
`run_fisher_test_lag` extends the standard test by computing prior-day context columns
in the CTE (prev_close_strength, prev_move_pct, prev_rvol, prev_gap_pct, prev_range_pct).
sql_filter expressions in the LAG variant reference these without a table-alias prefix.
Use this for multi-day pattern signals (e.g. "big catalyst day → inside day → gap up").

Available columns in the standard harness (pm. prefix in sql_filter):
    pm.close_price, pm.prev_close, pm.rvol, pm.close_strength,
    pm.gap_pct, pm.volume, pm.range_pct

Additional columns in run_fisher_test_lag (no prefix in sql_filter):
    close_price, prev_close, rvol, close_strength, gap_pct, volume, range_pct,
    prev_close_strength, prev_move_pct, prev_rvol, prev_gap_pct, prev_range_pct
"""

import logging
from scipy.stats import fisher_exact as _scipy_fisher

log = logging.getLogger("aiem_stat_tests")

_SCAN_START = "2024-07-08"   # earliest polygon_market_daily date


# ---------------------------------------------------------------------------
# Internal helpers

def _compute_stats(cond_win: int, cond_lose: int, ctrl_win: int, ctrl_lose: int,
                   alternative: str = "greater") -> dict:
    cond_n = cond_win + cond_lose
    ctrl_n = ctrl_win + ctrl_lose
    if cond_n == 0 or ctrl_n == 0:
        return {
            "cond_n":    cond_n,  "cond_win":  cond_win,  "cond_lose": cond_lose,
            "ctrl_n":    ctrl_n,  "ctrl_win":  ctrl_win,  "ctrl_lose": ctrl_lose,
            "cond_wr":   None,    "ctrl_wr":   None,      "delta_wr":  None,
            "p_raw":     1.0,
        }
    cond_wr  = round(cond_win / cond_n * 100, 2)
    ctrl_wr  = round(ctrl_win / ctrl_n * 100, 2)
    delta_wr = round(cond_wr - ctrl_wr, 2)
    _, p_raw = _scipy_fisher([[cond_win, cond_lose], [ctrl_win, ctrl_lose]],
                              alternative=alternative)
    return {
        "cond_n":    cond_n,   "cond_win":  cond_win,  "cond_lose": cond_lose,
        "ctrl_n":    ctrl_n,   "ctrl_win":  ctrl_win,  "ctrl_lose": ctrl_lose,
        "cond_wr":   cond_wr,  "ctrl_wr":   ctrl_wr,   "delta_wr":  delta_wr,
        "p_raw":     float(p_raw),
    }


def _fetch_2x2(cur) -> tuple:
    row = cur.fetchone()
    return tuple(int(v) for v in row)


# ---------------------------------------------------------------------------
# Non-overlapping (bucketed) — DEFAULT

def run_fisher_test(
    cur,
    sql_filter:  str,
    horizon:     int,
    scan_start:  str = _SCAN_START,
    alternative: str = "greater",
) -> dict:
    """
    Non-overlapping bucketed Fisher's exact test.

    Divides each ticker's history into non-overlapping H-day calendar buckets
    (via ROW_NUMBER integer division). Only the earliest row per (ticker, bucket_id)
    is kept, ensuring consecutive retained observations are ≥H days apart and their
    forward-return windows never overlap.

    Use this for all Module 5 and Module 6 statistical decisions.

    Parameters
    ----------
    sql_filter  : SQL CASE condition string referencing 'pm' alias columns:
                  pm.close_price, pm.prev_close, pm.rvol, pm.close_strength,
                  pm.gap_pct, pm.volume, pm.range_pct
    horizon     : Forward-return window in trading days (also the bucket size)
    scan_start  : Earliest scan_date to include (default: polygon_market_daily start)
    alternative : Fisher's exact test tail ('greater', 'less', 'two-sided')
    """
    sql = f"""
        WITH raw AS (
            SELECT
                pm.ticker,
                pm.scan_date,
                pm.close_price,
                pm.prev_close,
                pm.rvol,
                pm.close_strength,
                pm.gap_pct,
                pm.volume,
                pm.range_pct,
                LEAD(pm.close_price, %s)
                    OVER (PARTITION BY pm.ticker ORDER BY pm.scan_date) AS fwd_close,
                CASE WHEN {sql_filter} THEN TRUE ELSE FALSE END AS cond_met,
                ROW_NUMBER() OVER (PARTITION BY pm.ticker ORDER BY pm.scan_date) AS rn
            FROM polygon_market_daily pm
            WHERE pm.scan_date >= %s
              AND pm.close_price > 0
              AND pm.prev_close  > 0
              AND pm.rvol        IS NOT NULL
        ),
        bucketed AS (
            SELECT *,
                (rn - 1) / %s AS bucket_id    -- non-overlapping H-day blocks
            FROM raw
            WHERE fwd_close IS NOT NULL
        ),
        deduped AS (
            SELECT DISTINCT ON (ticker, bucket_id)
                close_price, fwd_close, cond_met
            FROM bucketed
            ORDER BY ticker, bucket_id, scan_date   -- keep earliest in each block
        )
        SELECT
            COUNT(*) FILTER (WHERE     cond_met AND fwd_close >  close_price) AS cond_win,
            COUNT(*) FILTER (WHERE     cond_met AND fwd_close <= close_price) AS cond_lose,
            COUNT(*) FILTER (WHERE NOT cond_met AND fwd_close >  close_price) AS ctrl_win,
            COUNT(*) FILTER (WHERE NOT cond_met AND fwd_close <= close_price) AS ctrl_lose
        FROM deduped
    """
    cur.execute(sql, (horizon, scan_start, horizon))
    cond_win, cond_lose, ctrl_win, ctrl_lose = _fetch_2x2(cur)
    return _compute_stats(cond_win, cond_lose, ctrl_win, ctrl_lose, alternative)


# ---------------------------------------------------------------------------
# LAG-aware non-overlapping — for multi-day pattern signals

def run_fisher_test_lag(
    cur,
    sql_filter:  str,
    horizon:     int,
    scan_start:  str = _SCAN_START,
    alternative: str = "greater",
) -> dict:
    """
    Non-overlapping bucketed Fisher's exact test with prior-day LAG context.

    Extends run_fisher_test() by computing LAG(1)-derived columns in the CTE,
    enabling sql_filter expressions that reference yesterday's values. This is
    needed for multi-day pattern signals such as:
        "big catalyst day (prior) → tight inside day (today) → gap-up (forward)"

    LAG columns available in sql_filter (reference WITHOUT 'pm.' prefix):
        prev_close_strength  — yesterday's close_strength
        prev_move_pct        — ABS(yesterday close − prev_close) / prev_close × 100
        prev_rvol            — yesterday's rvol
        prev_gap_pct         — yesterday's gap_pct
        prev_range_pct       — yesterday's range_pct

    Single-row columns also available (no prefix needed):
        close_price, prev_close, rvol, close_strength, gap_pct, volume, range_pct

    Example sql_filter:
        "volume >= 50000 AND range_pct <= 1.5 AND prev_move_pct >= 5.0
         AND prev_close_strength >= 0.7"

    Parameters
    ----------
    sql_filter  : SQL condition string referencing column names WITHOUT 'pm.' prefix
    horizon     : Forward-return window in trading days (also the bucket size)
    scan_start  : Earliest scan_date to include (default: polygon_market_daily start)
    alternative : Fisher's exact test tail ('greater', 'less', 'two-sided')

    Note on the "win" condition
    ---------------------------
    The win condition is fwd_close > close_price (positive H-day return). For signals
    that specify a minimum gap-up threshold on the forward day (e.g., gap_up_pct >= 3%),
    the current harness does not enforce that threshold on the outcome — it tests whether
    the H-day return is positive. This is a known approximation; document it in the
    signal's notes field.
    """
    sql = f"""
        WITH raw AS (
            SELECT
                pm.ticker,
                pm.scan_date,
                pm.close_price,
                pm.prev_close,
                pm.rvol,
                pm.close_strength,
                pm.gap_pct,
                pm.volume,
                pm.range_pct,
                LAG(pm.close_strength, 1)
                    OVER (PARTITION BY pm.ticker ORDER BY pm.scan_date) AS prev_close_strength,
                LAG(pm.rvol, 1)
                    OVER (PARTITION BY pm.ticker ORDER BY pm.scan_date) AS prev_rvol,
                LAG(pm.gap_pct, 1)
                    OVER (PARTITION BY pm.ticker ORDER BY pm.scan_date) AS prev_gap_pct,
                LAG(pm.range_pct, 1)
                    OVER (PARTITION BY pm.ticker ORDER BY pm.scan_date) AS prev_range_pct,
                LAG(ABS(pm.close_price - pm.prev_close) / NULLIF(pm.prev_close, 0) * 100, 1)
                    OVER (PARTITION BY pm.ticker ORDER BY pm.scan_date) AS prev_move_pct,
                LEAD(pm.close_price, %s)
                    OVER (PARTITION BY pm.ticker ORDER BY pm.scan_date) AS fwd_close,
                ROW_NUMBER()
                    OVER (PARTITION BY pm.ticker ORDER BY pm.scan_date) AS rn
            FROM polygon_market_daily pm
            WHERE pm.scan_date >= %s
              AND pm.close_price > 0
              AND pm.prev_close  > 0
              AND pm.rvol        IS NOT NULL
        ),
        annotated AS (
            SELECT *,
                (rn - 1) / %s AS bucket_id,
                CASE WHEN {sql_filter} THEN TRUE ELSE FALSE END AS cond_met
            FROM raw
            WHERE fwd_close IS NOT NULL
        ),
        deduped AS (
            SELECT DISTINCT ON (ticker, bucket_id)
                close_price, fwd_close, cond_met
            FROM annotated
            ORDER BY ticker, bucket_id, scan_date
        )
        SELECT
            COUNT(*) FILTER (WHERE     cond_met AND fwd_close >  close_price) AS cond_win,
            COUNT(*) FILTER (WHERE     cond_met AND fwd_close <= close_price) AS cond_lose,
            COUNT(*) FILTER (WHERE NOT cond_met AND fwd_close >  close_price) AS ctrl_win,
            COUNT(*) FILTER (WHERE NOT cond_met AND fwd_close <= close_price) AS ctrl_lose
        FROM deduped
    """
    cur.execute(sql, (horizon, scan_start, horizon))
    cond_win, cond_lose, ctrl_win, ctrl_lose = _fetch_2x2(cur)
    return _compute_stats(cond_win, cond_lose, ctrl_win, ctrl_lose, alternative)


# ---------------------------------------------------------------------------
# Overlapping (legacy) — for retroactive comparison only

def run_fisher_test_overlapping(
    cur,
    sql_filter:  str,
    horizon:     int,
    scan_start:  str = _SCAN_START,
    alternative: str = "greater",
) -> dict:
    """
    Legacy overlapping method. Retained solely for retroactive before/after comparison.

    DO NOT use for new discovery or validation decisions.
    Anti-conservative p-values when same ticker fires on adjacent days.
    """
    sql = f"""
        WITH ranked AS (
            SELECT
                pm.close_price,
                pm.prev_close,
                pm.rvol,
                pm.close_strength,
                pm.gap_pct,
                pm.volume,
                pm.range_pct,
                LEAD(pm.close_price, %s)
                    OVER (PARTITION BY pm.ticker ORDER BY pm.scan_date) AS fwd_close,
                CASE WHEN {sql_filter} THEN TRUE ELSE FALSE END AS cond_met
            FROM polygon_market_daily pm
            WHERE pm.scan_date >= %s
              AND pm.close_price > 0
              AND pm.prev_close  > 0
              AND pm.rvol        IS NOT NULL
        )
        SELECT
            COUNT(*) FILTER (WHERE     cond_met AND fwd_close >  close_price) AS cond_win,
            COUNT(*) FILTER (WHERE     cond_met AND fwd_close <= close_price) AS cond_lose,
            COUNT(*) FILTER (WHERE NOT cond_met AND fwd_close >  close_price) AS ctrl_win,
            COUNT(*) FILTER (WHERE NOT cond_met AND fwd_close <= close_price) AS ctrl_lose
        FROM ranked
        WHERE fwd_close IS NOT NULL
    """
    cur.execute(sql, (horizon, scan_start))
    cond_win, cond_lose, ctrl_win, ctrl_lose = _fetch_2x2(cur)
    return _compute_stats(cond_win, cond_lose, ctrl_win, ctrl_lose, alternative)
