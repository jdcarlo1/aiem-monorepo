"""
Module 5 — Pattern Discovery Engine
=====================================
Systematic batch scan of condition × horizon combinations using polygon_market_daily.
Tests each (condition, horizon) pair with Fisher's exact test and applies BH-FDR
correction across all tests in the batch.

Qualifying discoveries are inserted as status='hypothesis' into aiem_signal_discoveries
for Module 3 to evaluate over time and Module 4 to gate into validated status.

This module NEVER auto-validates any signal.
Every discovery starts as 'hypothesis'.

Qualification criteria (ALL required after BH-FDR):
  • cond_n >= 50         (minimum fires in condition group)
  • cond_wr >= 55.0%     (meaningfully above random)
  • p_adj < 0.05         (BH-FDR adjusted p-value)
  • delta_wr >= 3.0pp    (condition group meaningfully better than control)

Deduplication: condition_name+horizon must not already have a live discovery
(status IN ('validated','hypothesis')) with matching conditions_json key set.
"""

import datetime as _dt
import json as _json
import math as _math

import aiem_stat_tests as _stat_tests


# ---------------------------------------------------------------------------
# Thresholds

_HORIZONS         = [1, 3, 5]     # trading-day forward windows
_MIN_COND_N       = 50            # minimum fires in condition group
_MIN_COND_WR      = 55.0          # minimum condition win rate %
_FDR_ALPHA        = 0.05          # BH-FDR target false discovery rate
_MIN_DELTA_WR     = 3.0           # pp above control group win rate
_SCAN_START       = "2024-07-08"  # earliest polygon_market_daily date available


# ---------------------------------------------------------------------------
# Discovery grid
# sql_filter uses aliases: pm = polygon_market_daily
# change_pct is computed inline as (pm.close_price - pm.prev_close) / pm.prev_close * 100
# close_strength is already a column in polygon_market_daily

_DISCOVERY_GRID = [
    # ── Single-condition: volume ──────────────────────────────────────────
    {
        "name":             "rvol_gte_1_5",
        "sql_filter":       "pm.rvol >= 1.5",
        "conditions_json":  {"rvol_min": 1.5},
        "hypothesis_text":  (
            "Stocks with relative volume >= 1.5x 20-day average on a given day "
            "tend to have elevated forward returns, suggesting institutional presence."
        ),
        "alternative":      "greater",
    },
    {
        "name":             "rvol_gte_2",
        "sql_filter":       "pm.rvol >= 2.0",
        "conditions_json":  {"rvol_min": 2.0},
        "hypothesis_text":  (
            "Stocks with relative volume >= 2x 20-day average tend to have "
            "elevated forward returns, indicating strong institutional or retail interest."
        ),
        "alternative":      "greater",
    },
    {
        "name":             "rvol_gte_3",
        "sql_filter":       "pm.rvol >= 3.0",
        "conditions_json":  {"rvol_min": 3.0},
        "hypothesis_text":  (
            "Stocks with relative volume >= 3x average show unusual activity "
            "and tend to have elevated N-day forward returns."
        ),
        "alternative":      "greater",
    },
    {
        "name":             "rvol_gte_5",
        "sql_filter":       "pm.rvol >= 5.0",
        "conditions_json":  {"rvol_min": 5.0},
        "hypothesis_text":  (
            "Stocks with relative volume >= 5x average show extreme unusual activity "
            "and tend to have elevated forward returns."
        ),
        "alternative":      "greater",
    },
    # ── Single-condition: price change ───────────────────────────────────
    {
        "name":             "change_pct_gte_2",
        "sql_filter":       "(pm.close_price - pm.prev_close) / NULLIF(pm.prev_close, 0) * 100 >= 2.0",
        "conditions_json":  {"change_pct_min": 2.0},
        "hypothesis_text":  (
            "Stocks that gain >= 2% on the day tend to continue higher in the following days."
        ),
        "alternative":      "greater",
    },
    {
        "name":             "change_pct_gte_5",
        "sql_filter":       "(pm.close_price - pm.prev_close) / NULLIF(pm.prev_close, 0) * 100 >= 5.0",
        "conditions_json":  {"change_pct_min": 5.0},
        "hypothesis_text":  (
            "Stocks that gain >= 5% on the day tend to continue higher in the following days."
        ),
        "alternative":      "greater",
    },
    {
        "name":             "change_pct_gte_10",
        "sql_filter":       "(pm.close_price - pm.prev_close) / NULLIF(pm.prev_close, 0) * 100 >= 10.0",
        "conditions_json":  {"change_pct_min": 10.0},
        "hypothesis_text":  (
            "Stocks that gain >= 10% on the day tend to continue higher in the following days."
        ),
        "alternative":      "greater",
    },
    # ── Single-condition: gap ────────────────────────────────────────────
    {
        "name":             "gap_pct_gte_1",
        "sql_filter":       "pm.gap_pct >= 1.0",
        "conditions_json":  {"gap_pct_min": 1.0},
        "hypothesis_text":  (
            "Stocks that gap up >= 1% from prior close tend to continue higher."
        ),
        "alternative":      "greater",
    },
    {
        "name":             "gap_pct_gte_3",
        "sql_filter":       "pm.gap_pct >= 3.0",
        "conditions_json":  {"gap_pct_min": 3.0},
        "hypothesis_text":  (
            "Stocks that gap up >= 3% from prior close tend to continue higher."
        ),
        "alternative":      "greater",
    },
    {
        "name":             "gap_pct_gte_5",
        "sql_filter":       "pm.gap_pct >= 5.0",
        "conditions_json":  {"gap_pct_min": 5.0},
        "hypothesis_text":  (
            "Stocks that gap up >= 5% from prior close tend to continue higher."
        ),
        "alternative":      "greater",
    },
    # ── Single-condition: close position (close_strength) ────────────────
    {
        "name":             "close_strength_gte_0_80",
        "sql_filter":       "pm.close_strength >= 0.80",
        "conditions_json":  {"close_strength_min": 0.80},
        "hypothesis_text":  (
            "Stocks closing in the top 20% of their daily range (close_strength >= 0.80) "
            "tend to have bullish continuation."
        ),
        "alternative":      "greater",
    },
    {
        "name":             "close_strength_gte_0_90",
        "sql_filter":       "pm.close_strength >= 0.90",
        "conditions_json":  {"close_strength_min": 0.90},
        "hypothesis_text":  (
            "Stocks closing in the top 10% of their daily range (close_strength >= 0.90) "
            "tend to have bullish continuation."
        ),
        "alternative":      "greater",
    },
    # ── Single-condition: gap down (reversal hypothesis) ─────────────────
    {
        "name":             "gap_down_gte_5",
        "sql_filter":       "(pm.close_price - pm.prev_close) / NULLIF(pm.prev_close, 0) * 100 <= -5.0",
        "conditions_json":  {"change_pct_max": -5.0},
        "hypothesis_text":  (
            "Stocks that drop >= 5% on the day tend to reverse and close higher "
            "in the following N days."
        ),
        "alternative":      "greater",
    },
    {
        "name":             "gap_down_gte_10",
        "sql_filter":       "(pm.close_price - pm.prev_close) / NULLIF(pm.prev_close, 0) * 100 <= -10.0",
        "conditions_json":  {"change_pct_max": -10.0},
        "hypothesis_text":  (
            "Stocks that drop >= 10% on the day tend to reverse and close higher "
            "in the following N days."
        ),
        "alternative":      "greater",
    },
    # ── Two-condition: rvol + change ──────────────────────────────────────
    {
        "name":             "rvol_gte_2_change_gte_3",
        "sql_filter":       "pm.rvol >= 2.0 AND (pm.close_price - pm.prev_close) / NULLIF(pm.prev_close, 0) * 100 >= 3.0",
        "conditions_json":  {"rvol_min": 2.0, "change_pct_min": 3.0},
        "hypothesis_text":  (
            "Stocks with rvol >= 2x AND day gain >= 3% tend to continue higher. "
            "Combines volume confirmation with price momentum."
        ),
        "alternative":      "greater",
    },
    {
        "name":             "rvol_gte_2_change_gte_5",
        "sql_filter":       "pm.rvol >= 2.0 AND (pm.close_price - pm.prev_close) / NULLIF(pm.prev_close, 0) * 100 >= 5.0",
        "conditions_json":  {"rvol_min": 2.0, "change_pct_min": 5.0},
        "hypothesis_text":  (
            "Stocks with rvol >= 2x AND day gain >= 5% tend to continue higher."
        ),
        "alternative":      "greater",
    },
    {
        "name":             "rvol_gte_3_change_gte_5",
        "sql_filter":       "pm.rvol >= 3.0 AND (pm.close_price - pm.prev_close) / NULLIF(pm.prev_close, 0) * 100 >= 5.0",
        "conditions_json":  {"rvol_min": 3.0, "change_pct_min": 5.0},
        "hypothesis_text":  (
            "Stocks with rvol >= 3x AND day gain >= 5% show extreme combined "
            "volume and price momentum — tend to continue higher."
        ),
        "alternative":      "greater",
    },
    # ── Two-condition: rvol + close_strength ─────────────────────────────
    {
        "name":             "rvol_gte_2_close_strength_gte_0_80",
        "sql_filter":       "pm.rvol >= 2.0 AND pm.close_strength >= 0.80",
        "conditions_json":  {"rvol_min": 2.0, "close_strength_min": 0.80},
        "hypothesis_text":  (
            "Stocks with rvol >= 2x AND closing in top 20% of range — "
            "volume + close position combination suggests institutional accumulation."
        ),
        "alternative":      "greater",
    },
    {
        "name":             "rvol_gte_3_close_strength_gte_0_80",
        "sql_filter":       "pm.rvol >= 3.0 AND pm.close_strength >= 0.80",
        "conditions_json":  {"rvol_min": 3.0, "close_strength_min": 0.80},
        "hypothesis_text":  (
            "Stocks with rvol >= 3x AND closing in top 20% of range — "
            "extreme volume with strong close position."
        ),
        "alternative":      "greater",
    },
    {
        "name":             "rvol_gte_2_close_strength_gte_0_90",
        "sql_filter":       "pm.rvol >= 2.0 AND pm.close_strength >= 0.90",
        "conditions_json":  {"rvol_min": 2.0, "close_strength_min": 0.90},
        "hypothesis_text":  (
            "Stocks with rvol >= 2x AND closing in top 10% of range."
        ),
        "alternative":      "greater",
    },
    # ── Two-condition: gap + rvol ─────────────────────────────────────────
    {
        "name":             "gap_pct_gte_3_rvol_gte_2",
        "sql_filter":       "pm.gap_pct >= 3.0 AND pm.rvol >= 2.0",
        "conditions_json":  {"gap_pct_min": 3.0, "rvol_min": 2.0},
        "hypothesis_text":  (
            "Stocks that gap up >= 3% AND have rvol >= 2x — "
            "gap with volume confirmation tends to continue higher."
        ),
        "alternative":      "greater",
    },
    {
        "name":             "gap_pct_gte_5_rvol_gte_3",
        "sql_filter":       "pm.gap_pct >= 5.0 AND pm.rvol >= 3.0",
        "conditions_json":  {"gap_pct_min": 5.0, "rvol_min": 3.0},
        "hypothesis_text":  (
            "Stocks that gap up >= 5% AND have rvol >= 3x — "
            "large gap with extreme volume."
        ),
        "alternative":      "greater",
    },
    {
        "name":             "gap_pct_gte_1_change_gte_3",
        "sql_filter":       "pm.gap_pct >= 1.0 AND (pm.close_price - pm.prev_close) / NULLIF(pm.prev_close, 0) * 100 >= 3.0",
        "conditions_json":  {"gap_pct_min": 1.0, "change_pct_min": 3.0},
        "hypothesis_text":  (
            "Stocks that gap up >= 1% AND close up >= 3% — gap that holds and extends."
        ),
        "alternative":      "greater",
    },
    # ── Two-condition: gap down + rvol (reversal) ─────────────────────────
    {
        "name":             "gap_down_5_rvol_gte_2",
        "sql_filter":       "(pm.close_price - pm.prev_close) / NULLIF(pm.prev_close, 0) * 100 <= -5.0 AND pm.rvol >= 2.0",
        "conditions_json":  {"change_pct_max": -5.0, "rvol_min": 2.0},
        "hypothesis_text":  (
            "Stocks down >= 5% with rvol >= 2x — high-volume selloff with "
            "reversal potential in following days."
        ),
        "alternative":      "greater",
    },
    {
        "name":             "gap_down_10_rvol_gte_2",
        "sql_filter":       "(pm.close_price - pm.prev_close) / NULLIF(pm.prev_close, 0) * 100 <= -10.0 AND pm.rvol >= 2.0",
        "conditions_json":  {"change_pct_max": -10.0, "rvol_min": 2.0},
        "hypothesis_text":  (
            "Stocks down >= 10% with rvol >= 2x — extreme selloff with "
            "reversal potential."
        ),
        "alternative":      "greater",
    },
]

_TOTAL_TESTS = len(_DISCOVERY_GRID) * len(_HORIZONS)


# ---------------------------------------------------------------------------
# Schema init

def init_schema(conn) -> None:
    """Create Module 5 tracking tables if they don't exist."""
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS aiem_module5_runs (
                id            BIGSERIAL PRIMARY KEY,
                started_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                completed_at  TIMESTAMPTZ,
                conditions_tested    INT,
                total_tests          INT,
                bh_fdr_threshold     DOUBLE PRECISION,
                discoveries_inserted INT,
                discoveries_skipped  INT,
                top_results          JSONB,
                error                TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS aiem_module5_test_results (
                id             BIGSERIAL PRIMARY KEY,
                run_id         BIGINT REFERENCES aiem_module5_runs(id) ON DELETE CASCADE,
                condition_name TEXT NOT NULL,
                horizon_days   INT NOT NULL,
                cond_n         INT,
                cond_win       INT,
                cond_lose      INT,
                ctrl_n         INT,
                ctrl_win       INT,
                ctrl_lose      INT,
                cond_wr        DOUBLE PRECISION,
                ctrl_wr        DOUBLE PRECISION,
                delta_wr       DOUBLE PRECISION,
                p_raw          DOUBLE PRECISION,
                p_adj          DOUBLE PRECISION,
                bh_rejected    BOOLEAN,
                qualifies      BOOLEAN,
                skip_reason    TEXT,
                discovery_id   INT REFERENCES aiem_signal_discoveries(id),
                tested_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT uq_m5_cond_horizon_run UNIQUE (run_id, condition_name, horizon_days)
            )
        """)
    conn.commit()
    print("[module5] aiem_module5_runs + aiem_module5_test_results schema ready")


# ---------------------------------------------------------------------------
# BH-FDR correction (no external scipy needed for this step)

def _bh_fdr_reject(p_values: list[float], alpha: float = 0.05) -> list[bool]:
    """
    Benjamini-Hochberg step-up FDR correction.
    Returns a boolean list parallel to p_values: True = rejected (significant).
    """
    n = len(p_values)
    if n == 0:
        return []
    ranked = sorted(range(n), key=lambda i: p_values[i])
    last_rejected = -1
    for rank, orig_idx in enumerate(ranked, 1):
        if p_values[orig_idx] <= rank / n * alpha:
            last_rejected = rank - 1   # 0-indexed position in ranked list
    rejected = [False] * n
    for rank_idx in range(last_rejected + 1):
        rejected[ranked[rank_idx]] = True
    return rejected


# ---------------------------------------------------------------------------
# One-condition, one-horizon test

def _run_one_test(cur, cond: dict, horizon: int) -> dict:
    """
    Execute non-overlapping (bucketed) Fisher's exact test for one condition × horizon pair.
    Delegates to aiem_stat_tests.run_fisher_test — the authoritative shared harness.

    Non-overlapping: each ticker's history is divided into H-day calendar buckets;
    only the earliest row per bucket is kept, so no two retained observations for the
    same ticker share any forward-return days.
    """
    return _stat_tests.run_fisher_test(
        cur,
        sql_filter  = cond["sql_filter"],
        horizon     = horizon,
        scan_start  = _SCAN_START,
        alternative = cond.get("alternative", "greater"),
    )


# ---------------------------------------------------------------------------
# Deduplication check

def _existing_live_condition_key_sets(cur) -> list[frozenset]:
    """Return the key sets of all live (validated/hypothesis) signal conditions_json."""
    cur.execute("""
        SELECT conditions_json
        FROM aiem_signal_discoveries
        WHERE status IN ('validated', 'hypothesis')
          AND conditions_json IS NOT NULL
    """)
    return [frozenset(row[0].keys()) for row in cur.fetchall()]


def _is_duplicate(conditions_json: dict, existing_key_sets: list[frozenset]) -> bool:
    """
    Returns True if conditions_json key set is an exact match of any existing signal.
    Subset/superset are NOT considered duplicates — only identical key sets.
    """
    new_keys = frozenset(conditions_json.keys())
    return new_keys in existing_key_sets


# ---------------------------------------------------------------------------
# Main batch run

def run_discovery_batch(conn) -> dict:
    """
    Run the full discovery grid, apply BH-FDR correction, and insert
    qualifying discoveries into aiem_signal_discoveries.

    Returns a summary dict with run metadata and top results.
    """
    started_at = _dt.datetime.utcnow()

    # Open run row
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO aiem_module5_runs (started_at, conditions_tested, total_tests)
            VALUES (NOW(), %s, %s) RETURNING id
        """, (len(_DISCOVERY_GRID), _TOTAL_TESTS))
        run_id = cur.fetchone()[0]
    conn.commit()

    all_results: list[dict] = []

    with conn.cursor() as cur:
        existing_key_sets = _existing_live_condition_key_sets(cur)

        # Phase 1: run all tests
        print(f"[module5] run {run_id}: testing {len(_DISCOVERY_GRID)} conditions × "
              f"{len(_HORIZONS)} horizons = {_TOTAL_TESTS} total tests ...")

        for cond in _DISCOVERY_GRID:
            for horizon in _HORIZONS:
                try:
                    res = _run_one_test(cur, cond, horizon)
                except Exception as _e:
                    print(f"[module5]  ✗ {cond['name']} h={horizon}: {_e}")
                    res = {
                        "cond_n": 0, "cond_win": 0, "cond_lose": 0,
                        "ctrl_n": 0, "ctrl_win": 0, "ctrl_lose": 0,
                        "cond_wr": None, "ctrl_wr": None, "delta_wr": None,
                        "p_raw": 1.0,
                    }

                all_results.append({
                    "condition_name": cond["name"],
                    "horizon_days":   horizon,
                    "conditions_json": cond["conditions_json"],
                    "hypothesis_text": cond["hypothesis_text"],
                    **res,
                })

    # Phase 2: BH-FDR correction
    p_values = [r["p_raw"] for r in all_results]
    rejected = _bh_fdr_reject(p_values, alpha=_FDR_ALPHA)
    # FDR threshold = largest p-value that was rejected
    threshold = max(
        (p_values[i] for i, rej in enumerate(rejected) if rej),
        default=0.0,
    )

    for i, r in enumerate(all_results):
        r["bh_rejected"] = rejected[i]
        r["p_adj"]       = round(r["p_raw"], 6)   # store raw; threshold is the gate

    # Phase 3: qualify + insert discoveries
    discoveries_inserted = 0
    discoveries_skipped  = 0

    with conn.cursor() as cur:
        # Refresh existing key sets in case prior loop inserted some
        existing_key_sets = _existing_live_condition_key_sets(cur)

        for r in all_results:
            qualifies   = False
            skip_reason = None
            discovery_id = None

            if not r["bh_rejected"]:
                skip_reason = f"BH-FDR not rejected (p={r['p_raw']:.4f}, threshold={threshold:.4f})"
            elif (r["cond_n"] or 0) < _MIN_COND_N:
                skip_reason = f"cond_n={r['cond_n']} < {_MIN_COND_N} minimum"
            elif (r["cond_wr"] or 0) < _MIN_COND_WR:
                skip_reason = f"cond_wr={r['cond_wr']}% < {_MIN_COND_WR}% minimum"
            elif (r["delta_wr"] or 0) < _MIN_DELTA_WR:
                skip_reason = f"delta_wr={r['delta_wr']}pp < {_MIN_DELTA_WR}pp minimum"
            elif _is_duplicate(r["conditions_json"], existing_key_sets):
                skip_reason = "duplicate — conditions_json key set matches an existing live signal"
            else:
                qualifies = True

            r["qualifies"]   = qualifies
            r["skip_reason"] = skip_reason

            if qualifies:
                horizon_label = f"{r['horizon_days']}d"
                cur.execute("""
                    INSERT INTO aiem_signal_discoveries
                        (status, conditions_json, hypothesis_text, horizon,
                         signal_win_rate, signal_n,
                         baseline_win_rate, baseline_n,
                         edge_broad, oos_edge, p_value,
                         invented_indicator, discovered_at)
                    VALUES
                        ('hypothesis', %s::jsonb, %s, %s,
                         %s, %s,
                         %s, %s,
                         %s, %s, %s,
                         'module5_fisher_bh', NOW())
                    RETURNING id
                """, (
                    _json.dumps(r["conditions_json"]),
                    r["hypothesis_text"],
                    horizon_label,
                    r["cond_wr"],
                    r["cond_n"],
                    r["ctrl_wr"],
                    r["ctrl_n"],
                    round(r["cond_wr"] - r["ctrl_wr"], 2),   # edge_broad
                    round(r["cond_wr"] - r["ctrl_wr"], 2),   # oos_edge (same — this IS the OOS test)
                    r["p_raw"],
                ))
                discovery_id = cur.fetchone()[0]
                # Add to existing key sets to prevent same-batch duplicates
                existing_key_sets.append(frozenset(r["conditions_json"].keys()))
                discoveries_inserted += 1
                print(
                    f"[module5]  ✅ NEW DISCOVERY id={discovery_id}: "
                    f"{r['condition_name']} h={r['horizon_days']}d  "
                    f"wr={r['cond_wr']}% n={r['cond_n']} delta={r['delta_wr']}pp"
                )
            else:
                discoveries_skipped += 1
                r["discovery_id"] = None

            r["discovery_id"] = discovery_id

            # Store test result row
            cur.execute("""
                INSERT INTO aiem_module5_test_results (
                    run_id, condition_name, horizon_days,
                    cond_n, cond_win, cond_lose,
                    ctrl_n, ctrl_win, ctrl_lose,
                    cond_wr, ctrl_wr, delta_wr,
                    p_raw, p_adj, bh_rejected,
                    qualifies, skip_reason, discovery_id
                ) VALUES (
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s
                )
                ON CONFLICT (run_id, condition_name, horizon_days) DO NOTHING
            """, (
                run_id,
                r["condition_name"], r["horizon_days"],
                r["cond_n"], r["cond_win"], r["cond_lose"],
                r["ctrl_n"], r["ctrl_win"], r["ctrl_lose"],
                r["cond_wr"], r["ctrl_wr"], r["delta_wr"],
                r["p_raw"], r["p_adj"], r["bh_rejected"],
                r["qualifies"], r["skip_reason"], r["discovery_id"],
            ))

        # Top 10 results by delta_wr for summary
        top_results = sorted(
            [r for r in all_results if r.get("cond_wr") is not None],
            key=lambda x: (x.get("delta_wr") or -999),
            reverse=True,
        )[:10]
        top_results_json = [
            {k: v for k, v in r.items()
             if k in ("condition_name", "horizon_days", "cond_n", "cond_wr",
                      "ctrl_wr", "delta_wr", "p_raw", "bh_rejected", "qualifies",
                      "skip_reason", "discovery_id")}
            for r in top_results
        ]

        # Close run row
        cur.execute("""
            UPDATE aiem_module5_runs SET
                completed_at         = NOW(),
                bh_fdr_threshold     = %s,
                discoveries_inserted = %s,
                discoveries_skipped  = %s,
                top_results          = %s::jsonb
            WHERE id = %s
        """, (
            threshold,
            discoveries_inserted,
            discoveries_skipped,
            _json.dumps(top_results_json),
            run_id,
        ))

    conn.commit()

    elapsed = (_dt.datetime.utcnow() - started_at).total_seconds()
    summary = {
        "run_id":                run_id,
        "started_at":            started_at.isoformat() + "Z",
        "elapsed_seconds":       round(elapsed, 1),
        "conditions_tested":     len(_DISCOVERY_GRID),
        "total_tests":           _TOTAL_TESTS,
        "bh_fdr_threshold":      round(threshold, 6),
        "discoveries_inserted":  discoveries_inserted,
        "discoveries_skipped":   discoveries_skipped,
        "top_10_by_delta_wr":    top_results_json,
    }
    print(
        f"[module5] run {run_id} complete in {elapsed:.1f}s: "
        f"{discoveries_inserted} new discoveries, {discoveries_skipped} skipped"
    )
    return summary


# ---------------------------------------------------------------------------
# Last run report (for GET endpoint)

def get_last_run_report(conn) -> dict:
    """Return the most recent Module 5 run summary and top results."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, started_at, completed_at, conditions_tested, total_tests,
                   bh_fdr_threshold, discoveries_inserted, discoveries_skipped,
                   top_results, error
            FROM aiem_module5_runs
            ORDER BY id DESC
            LIMIT 1
        """)
        row = cur.fetchone()
        if not row:
            return {"error": "no runs yet", "hint": "POST /admin/run-module5-discovery to start"}

        cols = ["run_id", "started_at", "completed_at", "conditions_tested", "total_tests",
                "bh_fdr_threshold", "discoveries_inserted", "discoveries_skipped",
                "top_results", "error"]
        report = dict(zip(cols, row))

        # Serialize datetimes
        for k in ("started_at", "completed_at"):
            if report.get(k):
                report[k] = report[k].isoformat()

        # Also return discoveries this run generated
        cur.execute("""
            SELECT t.condition_name, t.horizon_days, t.cond_n, t.cond_wr,
                   t.ctrl_wr, t.delta_wr, t.p_raw, t.discovery_id,
                   d.status AS current_status
            FROM aiem_module5_test_results t
            LEFT JOIN aiem_signal_discoveries d ON d.id = t.discovery_id
            WHERE t.run_id = %s AND t.qualifies = TRUE
            ORDER BY t.delta_wr DESC
        """, (report["run_id"],))
        disc_cols = ["condition_name", "horizon_days", "cond_n", "cond_wr",
                     "ctrl_wr", "delta_wr", "p_raw", "discovery_id", "current_status"]
        report["discoveries"] = [dict(zip(disc_cols, r)) for r in cur.fetchall()]

    return report
