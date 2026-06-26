
╔══════════════════════════════════════════════════════════════════════════════╗
║         AIEM AUTONOMOUS TRADING SYSTEM — FULL CODE EXPORT FOR REVIEW        ║
║                      StockScanner AI  /  main.py                            ║
║                                                                              ║
║  WHAT WAS BUILT:                                                             ║
║  • polygon_market_daily table: ALL 12K+ US stocks stored every trading day  ║
║  • 20 autonomous mkt_* research tools (GPT-4o invents its own hypotheses)   ║
║  • aiem_signal_discoveries table: validated signals saved with p-values      ║
║  • Loop A: Sunday 8PM ET — deep weekly research session (GPT-4o agent)      ║
║  • Loop B: Daily 6PM ET + POST-SCAN TRIGGER at 8:36 AM after Polygon data   ║
║  • 70-law research brain: statistical rigor, segmentation, seasonality,      ║
║    failure intelligence, risk science, factor decomposition, overfitting     ║
║    prevention, cross-sectional ranking, attribution, compounding discovery   ║
║  • Historical backfill: fills Apr–Jun 2026 at Polygon rate limit (5 req/min)║
╚══════════════════════════════════════════════════════════════════════════════╝

================================================================================
# SECTION: TABLE INIT + _mkt_parse_conditions + ALL 20 MKT_* TOOLS  (main.py lines 15000–16405)
================================================================================
def _mkt_init_tables():
    """Create polygon_market_daily + aiem_signal_discoveries + all indexes."""
    import psycopg2
    try:
        with psycopg2.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS polygon_market_daily (
                    id             SERIAL PRIMARY KEY,
                    scan_date      DATE NOT NULL,
                    ticker         VARCHAR(10) NOT NULL,
                    close_price    FLOAT NOT NULL,
                    open_price     FLOAT,
                    high_price     FLOAT,
                    low_price      FLOAT,
                    vwap           FLOAT,
                    volume         BIGINT,
                    prev_close     FLOAT,
                    gap_pct        FLOAT,
                    rvol           FLOAT,
                    close_strength FLOAT,
                    range_pct      FLOAT,
                    UNIQUE (scan_date, ticker)
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_pmd_date ON polygon_market_daily (scan_date)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_pmd_ticker ON polygon_market_daily (ticker)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_pmd_ticker_date ON polygon_market_daily (ticker, scan_date)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_pmd_gap ON polygon_market_daily (gap_pct)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_pmd_rvol ON polygon_market_daily (rvol)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_pmd_close_str ON polygon_market_daily (close_strength)")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS aiem_signal_discoveries (
                    id                SERIAL PRIMARY KEY,
                    hypothesis_text   TEXT,
                    conditions_json   JSONB NOT NULL,
                    horizon           VARCHAR(20) DEFAULT 'next_day',
                    signal_n          INTEGER,
                    signal_win_rate   FLOAT,
                    signal_avg_ret    FLOAT,
                    baseline_n        INTEGER,
                    baseline_win_rate FLOAT,
                    baseline_avg_ret  FLOAT,
                    edge_broad        FLOAT,
                    edge_tight        FLOAT,
                    p_value           FLOAT,
                    oos_edge          FLOAT,
                    status            VARCHAR(20) DEFAULT 'new',
                    discovered_at     TIMESTAMP DEFAULT NOW(),
                    confirmed_at      TIMESTAMP,
                    invented_indicator TEXT,
                    notes             TEXT
                )
            """)
        print("[mkt_init] polygon_market_daily + aiem_signal_discoveries ready")
    except Exception as _e:
        print(f"[mkt_init] table init error: {_e}")


def _mkt_parse_conditions(conditions: dict):
    """Convert condition dict → (sql_fragment, params). Whitelist-safe."""
    parts, params = [], []
    for key, val in (conditions or {}).items():
        if key.endswith("_min"):
            field, op = key[:-4], ">="
        elif key.endswith("_max"):
            field, op = key[:-4], "<="
        else:
            continue
        if field not in _MKT_SAFE_COLS:
            continue
        try:
            val = float(val)
        except (TypeError, ValueError):
            continue
        parts.append(f"{_MKT_SAFE_COLS[field]} {op} %s")
        params.append(val)
    return (" AND ".join(parts), params)


def _mkt_run_two_group(conn, sig_where, sig_params, base_where, base_params, limit=100000):
    """Fetch returns for two groups and compute all stats. Returns dict or None."""
    import numpy as _np
    from scipy import stats as _sc

    def _fetch(where, params):
        sql = f"""
            SELECT ((nxt.close_price / NULLIF(t.close_price,0)) - 1) * 100
            FROM polygon_market_daily t
            JOIN polygon_market_daily nxt
              ON nxt.ticker = t.ticker
             AND nxt.scan_date = (
                   SELECT MIN(x.scan_date) FROM polygon_market_daily x
                   WHERE x.ticker = t.ticker AND x.scan_date > t.scan_date
                 )
            WHERE t.close_price > 0{(' AND ' + where) if where else ''}
            LIMIT {limit}
        """
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return [r[0] for r in cur.fetchall() if r[0] is not None]

    sig = _fetch(sig_where, sig_params)
    base = _fetch(base_where, base_params)
    if not sig or not base:
        return None

    sa, ba = _np.array(sig), _np.array(base)
    _, pval = _sc.ttest_ind(sa, ba, equal_var=False)
    return {
        "signal_n":          len(sa),
        "signal_win_rate":   round(float(_np.mean(sa > 0)) * 100, 2),
        "signal_avg_ret":    round(float(_np.mean(sa)), 4),
        "signal_median_ret": round(float(_np.median(sa)), 4),
        "baseline_n":        len(ba),
        "baseline_win_rate": round(float(_np.mean(ba > 0)) * 100, 2),
        "baseline_avg_ret":  round(float(_np.mean(ba)), 4),
        "edge_winrate":      round(float(_np.mean(sa > 0) - _np.mean(ba > 0)) * 100, 2),
        "edge_avg_ret":      round(float(_np.mean(sa) - _np.mean(ba)), 4),
        "p_value":           round(float(pval), 4),
        "significant":       bool(pval < 0.05),
    }


# ──────────────────────────────────────────────────────────────────────────
# Tool 1: Explore the full market dataset dimensions
# ──────────────────────────────────────────────────────────────────────────
def _mkt_tool_explore_dimensions():
    """Statistical summary of the full polygon_market_daily universe.
    Call this FIRST to understand what data exists before testing signals."""
    import psycopg2
    try:
        with psycopg2.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT
                    COUNT(DISTINCT scan_date) AS n_dates,
                    MIN(scan_date)::text AS earliest,
                    MAX(scan_date)::text AS latest,
                    COUNT(*) AS total_rows,
                    ROUND(COUNT(*)::numeric / NULLIF(COUNT(DISTINCT scan_date),0), 0) AS avg_stocks_per_day
                FROM polygon_market_daily
            """)
            meta = dict(zip([d[0] for d in cur.description], cur.fetchone() or []))

            cur.execute("""
                SELECT
                    ROUND(AVG(gap_pct)::numeric,2) AS gap_avg,
                    ROUND(STDDEV(gap_pct)::numeric,2) AS gap_std,
                    ROUND(PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY gap_pct)::numeric,2) AS gap_p25,
                    ROUND(PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY gap_pct)::numeric,2) AS gap_p50,
                    ROUND(PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY gap_pct)::numeric,2) AS gap_p75,
                    ROUND(AVG(rvol)::numeric,2) AS rvol_avg,
                    ROUND(PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY rvol)::numeric,2) AS rvol_p50,
                    ROUND(PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY rvol)::numeric,2) AS rvol_p75,
                    ROUND(PERCENTILE_CONT(0.90) WITHIN GROUP (ORDER BY rvol)::numeric,2) AS rvol_p90,
                    ROUND(AVG(close_strength)::numeric,3) AS cs_avg,
                    ROUND(PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY close_strength)::numeric,3) AS cs_p25,
                    ROUND(PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY close_strength)::numeric,3) AS cs_p75,
                    ROUND(AVG(range_pct)::numeric,2) AS range_avg,
                    ROUND(PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY range_pct)::numeric,2) AS range_p50,
                    ROUND(PERCENTILE_CONT(0.90) WITHIN GROUP (ORDER BY range_pct)::numeric,2) AS range_p90,
                    ROUND(AVG(volume)::numeric,0) AS vol_avg,
                    COUNT(*) FILTER (WHERE gap_pct IS NOT NULL) AS gap_coverage,
                    COUNT(*) FILTER (WHERE rvol IS NOT NULL) AS rvol_coverage
                FROM polygon_market_daily
                WHERE close_price > 0
            """)
            dist = dict(zip([d[0] for d in cur.description], cur.fetchone() or []))

            # Baseline forward returns
            cur.execute("""
                SELECT
                    COUNT(*) AS n_pairs,
                    ROUND(AVG(fwd_ret)::numeric, 4) AS avg_next_day_ret,
                    ROUND((COUNT(*) FILTER (WHERE fwd_ret > 0))::numeric / NULLIF(COUNT(*),0) * 100, 2) AS baseline_win_rate
                FROM (
                    SELECT ((nxt.close_price / NULLIF(t.close_price,0)) - 1) * 100 AS fwd_ret
                    FROM polygon_market_daily t
                    JOIN polygon_market_daily nxt
                      ON nxt.ticker = t.ticker
                     AND nxt.scan_date = (
                           SELECT MIN(x.scan_date) FROM polygon_market_daily x
                           WHERE x.ticker = t.ticker AND x.scan_date > t.scan_date
                         )
                    WHERE t.close_price > 0
                    LIMIT 500000
                ) sub
            """)
            baseline = dict(zip([d[0] for d in cur.description], cur.fetchone() or []))

            cur.execute("SELECT COUNT(*) FROM aiem_signal_discoveries")
            disc_count = cur.fetchone()[0]

        return {
            "status": "ok",
            "dataset": {k: (int(v) if isinstance(v, (int,)) else str(v) if hasattr(v, 'isoformat') else v)
                        for k, v in meta.items()},
            "factor_distributions": {k: float(v) if v is not None else None for k, v in dist.items()},
            "baseline_returns": {k: float(v) if v is not None else None for k, v in baseline.items()},
            "prior_discoveries": disc_count,
            "available_factors": list(_MKT_SAFE_COLS.keys()),
            "condition_format": "Use {factor}_min and {factor}_max keys, e.g. {'gap_pct_min': 2.0, 'rvol_min': 3.0}",
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ──────────────────────────────────────────────────────────────────────────
# Tool 2: Test any signal against the full 12K universe
# ──────────────────────────────────────────────────────────────────────────
def _mkt_tool_test_signal(conditions=None, horizon="next_day", baseline="broad"):
    """Test any combination of market conditions against the full 12K-stock universe.
    Returns signal win_rate, avg_return, edge vs broad market, and p-value.
    conditions: dict e.g. {'gap_pct_min': 2.0, 'rvol_min': 3.0, 'close_strength_min': 0.6}
    baseline: 'broad' (all stocks) or 'tight' (stocks just below each threshold)
    """
    import psycopg2
    if not conditions:
        return {"status": "error", "error": "conditions dict required"}
    try:
        sig_where, sig_params = _mkt_parse_conditions(conditions)
        if not sig_where:
            return {"status": "error", "error": "No valid conditions parsed. Use {factor}_min/_max keys."}

        with psycopg2.connect(os.environ["DATABASE_URL"]) as conn:
            # Broad baseline = all stocks
            broad_res = _mkt_run_two_group(conn, sig_where, sig_params, "", [])

            if baseline == "tight":
                # Tight baseline: stocks that are "close" but don't meet all conditions
                # Expand each threshold by 50% in the opposite direction
                tight_parts, tight_params = [], []
                for key, val in (conditions or {}).items():
                    if key.endswith("_min"):
                        field = key[:-4]
                        if field not in _MKT_SAFE_COLS:
                            continue
                        col = _MKT_SAFE_COLS[field]
                        try:
                            v = float(val)
                        except:
                            continue
                        # Tight baseline: value within 50% below threshold
                        tight_parts.append(f"({col} >= %s AND {col} < %s)")
                        tight_params.extend([v * 0.5, v])
                    elif key.endswith("_max"):
                        field = key[:-4]
                        if field not in _MKT_SAFE_COLS:
                            continue
                        col = _MKT_SAFE_COLS[field]
                        try:
                            v = float(val)
                        except:
                            continue
                        tight_parts.append(f"({col} > %s AND {col} <= %s)")
                        tight_params.extend([v, v * 1.5])

                if tight_parts:
                    tight_where = " OR ".join(tight_parts)
                    tight_res = _mkt_run_two_group(conn, sig_where, sig_params, f"({tight_where})", tight_params)
                else:
                    tight_res = None
            else:
                tight_res = None

        if not broad_res:
            return {"status": "error", "error": "No data — run mkt_explore_dimensions first to confirm data exists."}

        result = {
            "status": "ok",
            "conditions_tested": conditions,
            "vs_broad_market": broad_res,
        }
        if tight_res:
            result["vs_tight_baseline"] = tight_res
        result["interpretation"] = (
            f"Signal fires on {broad_res['signal_n']} stock-days. "
            f"Win rate: {broad_res['signal_win_rate']}% vs market {broad_res['baseline_win_rate']}%. "
            f"Edge: {broad_res['edge_winrate']:+.1f}pp. "
            f"p={broad_res['p_value']} ({'SIGNIFICANT' if broad_res['significant'] else 'not significant'})."
        )
        return result
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ──────────────────────────────────────────────────────────────────────────
# Tool 3: Test the inverse — confirms signal is directional
# ──────────────────────────────────────────────────────────────────────────
def _mkt_tool_test_inverse(conditions=None, horizon="next_day"):
    """Test what happens when ALL conditions are ABSENT. If the signal is real,
    the inverse group should perform WORSE than the broad market.
    A genuine signal: inverse win_rate < broad win_rate < signal win_rate."""
    import psycopg2
    if not conditions:
        return {"status": "error", "error": "conditions dict required"}
    try:
        sig_where, sig_params = _mkt_parse_conditions(conditions)
        if not sig_where:
            return {"status": "error", "error": "No valid conditions parsed."}

        inv_where = f"NOT ({sig_where})"

        with psycopg2.connect(os.environ["DATABASE_URL"]) as conn:
            sig_res = _mkt_run_two_group(conn, sig_where, sig_params, "", [])
            inv_res = _mkt_run_two_group(conn, inv_where, sig_params, "", [])

        if not sig_res or not inv_res:
            return {"status": "error", "error": "Insufficient data for comparison."}

        real_signal = (sig_res["signal_win_rate"] > sig_res["baseline_win_rate"] > inv_res["signal_win_rate"])
        return {
            "status": "ok",
            "conditions": conditions,
            "signal_group": {"n": sig_res["signal_n"], "win_rate": sig_res["signal_win_rate"],
                             "avg_ret": sig_res["signal_avg_ret"]},
            "inverse_group": {"n": inv_res["signal_n"], "win_rate": inv_res["signal_win_rate"],
                              "avg_ret": inv_res["signal_avg_ret"]},
            "broad_market": {"win_rate": sig_res["baseline_win_rate"], "avg_ret": sig_res["baseline_avg_ret"]},
            "directional_confirmed": real_signal,
            "interpretation": (
                "REAL DIRECTIONAL SIGNAL: signal > market > inverse."
                if real_signal else
                "WARNING: inverse does not underperform — signal may not be directional."
            ),
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ──────────────────────────────────────────────────────────────────────────
# Tool 4: Find optimal threshold for any single factor
# ──────────────────────────────────────────────────────────────────────────
def _mkt_tool_find_thresholds(factor="gap_pct", direction="min", n_steps=20, horizon="next_day"):
    """Grid-search 20 threshold values for a single factor to find the optimal cut.
    direction: 'min' (factor >= threshold) or 'max' (factor <= threshold)
    Returns the threshold that maximizes win-rate edge vs broad baseline."""
    import psycopg2
    import numpy as _np
    if factor not in _MKT_SAFE_COLS:
        return {"status": "error", "error": f"Unknown factor. Choose from: {list(_MKT_SAFE_COLS.keys())}"}
    try:
        col = _MKT_SAFE_COLS[factor]
        with psycopg2.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
            cur.execute(f"""
                SELECT PERCENTILE_CONT(0.05) WITHIN GROUP (ORDER BY {col}),
                       PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY {col})
                FROM polygon_market_daily WHERE {col} IS NOT NULL AND close_price > 0
            """)
            row = cur.fetchone()
            if not row or row[0] is None:
                return {"status": "error", "error": "No data for this factor."}
            lo, hi = float(row[0]), float(row[1])

        thresholds = _np.linspace(lo, hi, n_steps)
        results = []
        with psycopg2.connect(os.environ["DATABASE_URL"]) as conn:
            for thr in thresholds:
                if direction == "min":
                    w, p = f"{col} >= %s", [thr]
                else:
                    w, p = f"{col} <= %s", [thr]
                res = _mkt_run_two_group(conn, w, p, "", [], limit=50000)
                if res and res["signal_n"] >= 50:
                    results.append({
                        "threshold": round(float(thr), 4),
                        "n": res["signal_n"],
                        "win_rate": res["signal_win_rate"],
                        "avg_ret": res["signal_avg_ret"],
                        "edge_winrate": res["edge_winrate"],
                        "edge_avg_ret": res["edge_avg_ret"],
                        "p_value": res["p_value"],
                    })

        if not results:
            return {"status": "error", "error": "No results — insufficient data."}

        best = max(results, key=lambda x: x["edge_winrate"])
        return {
            "status": "ok",
            "factor": factor,
            "direction": direction,
            "best_threshold": best["threshold"],
            "best_edge_winrate": best["edge_winrate"],
            "best_n": best["n"],
            "best_p_value": best["p_value"],
            "all_thresholds": results,
            "recommendation": (
                f"Use {factor}_{direction}={best['threshold']} for max edge "
                f"({best['edge_winrate']:+.1f}pp, n={best['n']}, p={best['p_value']})"
            ),
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ──────────────────────────────────────────────────────────────────────────
# Tool 5: What did big movers look like the day BEFORE they moved?
# ──────────────────────────────────────────────────────────────────────────
def _mkt_tool_analyze_top_movers(min_move_pct=5.0, max_move_pct=50.0, horizon="next_day"):
    """Find stocks that moved min_move_pct%+ the next day and profile their PRIOR day characteristics.
    Reveals the leading indicators of large moves."""
    import psycopg2
    try:
        with psycopg2.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT
                    COUNT(*) AS n_movers,
                    ROUND(AVG(t.gap_pct)::numeric,3) AS avg_gap_pct,
                    ROUND(AVG(t.rvol)::numeric,3) AS avg_rvol,
                    ROUND(AVG(t.close_strength)::numeric,3) AS avg_close_strength,
                    ROUND(AVG(t.range_pct)::numeric,3) AS avg_range_pct,
                    ROUND(AVG(t.volume)::numeric,0) AS avg_volume,
                    ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY t.gap_pct)::numeric,3) AS med_gap_pct,
                    ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY t.rvol)::numeric,3) AS med_rvol,
                    ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY t.close_strength)::numeric,3) AS med_close_strength,
                    ROUND(AVG((nxt.close_price/NULLIF(t.close_price,0)-1)*100)::numeric,2) AS avg_actual_move
                FROM polygon_market_daily t
                JOIN polygon_market_daily nxt
                  ON nxt.ticker = t.ticker
                 AND nxt.scan_date = (
                       SELECT MIN(x.scan_date) FROM polygon_market_daily x
                       WHERE x.ticker = t.ticker AND x.scan_date > t.scan_date
                     )
                WHERE t.close_price > 0
                  AND ((nxt.close_price/NULLIF(t.close_price,0)-1)*100) >= %s
                  AND ((nxt.close_price/NULLIF(t.close_price,0)-1)*100) <= %s
            """, [min_move_pct, max_move_pct])
            mover_row = dict(zip([d[0] for d in cur.description], cur.fetchone() or []))

            cur.execute("""
                SELECT
                    ROUND(AVG(gap_pct)::numeric,3) AS avg_gap_pct,
                    ROUND(AVG(rvol)::numeric,3) AS avg_rvol,
                    ROUND(AVG(close_strength)::numeric,3) AS avg_close_strength,
                    ROUND(AVG(range_pct)::numeric,3) AS avg_range_pct,
                    ROUND(AVG(volume)::numeric,0) AS avg_volume
                FROM polygon_market_daily WHERE close_price > 0
            """)
            all_row = dict(zip([d[0] for d in cur.description], cur.fetchone() or []))

        def _lift(field):
            mv = mover_row.get(f"avg_{field}")
            al = all_row.get(f"avg_{field}")
            if mv is None or al is None or al == 0:
                return None
            return round((float(mv) / float(al) - 1) * 100, 1)

        return {
            "status": "ok",
            "criteria": f"Next-day move >= {min_move_pct}% and <= {max_move_pct}%",
            "n_movers": int(mover_row.get("n_movers") or 0),
            "avg_actual_move_pct": float(mover_row.get("avg_actual_move") or 0),
            "pre_move_characteristics": {k: (float(v) if v is not None else None)
                                         for k, v in mover_row.items() if k != "n_movers"},
            "vs_all_stocks": {k: (float(v) if v is not None else None) for k, v in all_row.items()},
            "factor_lifts_vs_average": {
                "gap_pct":        _lift("gap_pct"),
                "rvol":           _lift("rvol"),
                "close_strength": _lift("close_strength"),
                "range_pct":      _lift("range_pct"),
            },
            "insight": "Factor lift = how much higher than average movers scored on each factor the day before the move.",
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ──────────────────────────────────────────────────────────────────────────
# Tool 6: Analyze false signals — what do losers have that winners don't?
# ──────────────────────────────────────────────────────────────────────────
def _mkt_tool_analyze_false_signals(conditions=None, win_threshold=2.0, horizon="next_day"):
    """Among stocks meeting the signal, compare winners (>=win_threshold% next day)
    vs losers (<0% next day). Reveals what negative filters could improve precision."""
    import psycopg2
    if not conditions:
        return {"status": "error", "error": "conditions dict required"}
    try:
        sig_where, sig_params = _mkt_parse_conditions(conditions)
        if not sig_where:
            return {"status": "error", "error": "No valid conditions parsed."}

        with psycopg2.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
            base_sql = f"""
                SELECT t.gap_pct, t.rvol, t.close_strength, t.range_pct, t.volume,
                       ((nxt.close_price/NULLIF(t.close_price,0))-1)*100 AS fwd_ret
                FROM polygon_market_daily t
                JOIN polygon_market_daily nxt
                  ON nxt.ticker = t.ticker
                 AND nxt.scan_date = (
                       SELECT MIN(x.scan_date) FROM polygon_market_daily x
                       WHERE x.ticker = t.ticker AND x.scan_date > t.scan_date
                     )
                WHERE t.close_price > 0 AND {sig_where}
                LIMIT 20000
            """
            cur.execute(base_sql, sig_params)
            rows = cur.fetchall()

        import numpy as _np
        winners = [r for r in rows if r[5] is not None and r[5] >= win_threshold]
        losers  = [r for r in rows if r[5] is not None and r[5] < 0]

        def _avg_col(rows_list, idx):
            vals = [r[idx] for r in rows_list if r[idx] is not None]
            return round(float(_np.mean(vals)), 4) if vals else None

        cols = ["gap_pct", "rvol", "close_strength", "range_pct", "volume"]
        w_avgs = {c: _avg_col(winners, i) for i, c in enumerate(cols)}
        l_avgs = {c: _avg_col(losers, i) for i, c in enumerate(cols)}

        diffs = {}
        for c in cols:
            if w_avgs[c] is not None and l_avgs[c] is not None and l_avgs[c] != 0:
                diffs[c] = round((w_avgs[c] - l_avgs[c]) / abs(l_avgs[c]) * 100, 1)

        return {
            "status": "ok",
            "conditions": conditions,
            "total_signal_hits": len(rows),
            "winners_n": len(winners), "losers_n": len(losers),
            "winner_avg_factors": w_avgs,
            "loser_avg_factors": l_avgs,
            "winner_vs_loser_pct_diff": diffs,
            "tip": "Factors where winners >> losers are candidates for tighter filter conditions.",
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ──────────────────────────────────────────────────────────────────────────
# Tool 7: Does the signal work in different market regimes?
# ──────────────────────────────────────────────────────────────────────────
def _mkt_tool_regime_filter(conditions=None, horizon="next_day"):
    """Test signal split by market regime (SPY performance that day).
    Bull: SPY gap_pct >= +0.5%; Bear: SPY gap_pct <= -0.5%; Flat: in between."""
    import psycopg2
    if not conditions:
        return {"status": "error", "error": "conditions dict required"}
    try:
        sig_where, sig_params = _mkt_parse_conditions(conditions)
        if not sig_where:
            return {"status": "error", "error": "No valid conditions parsed."}

        with psycopg2.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
            # Get SPY daily returns as regime classifier
            cur.execute("""
                SELECT scan_date, gap_pct
                FROM polygon_market_daily
                WHERE ticker = 'SPY' ORDER BY scan_date
            """)
            spy_rows = cur.fetchall()

        regimes = {}
        for row in spy_rows:
            d, g = row[0], row[1]
            if g is None:
                regime = "flat"
            elif g >= 0.5:
                regime = "bull"
            elif g <= -0.5:
                regime = "bear"
            else:
                regime = "flat"
            regimes[str(d)] = regime

        if not regimes:
            return {"status": "error", "error": "No SPY data in polygon_market_daily. Run daily scan first."}

        results = {}
        with psycopg2.connect(os.environ["DATABASE_URL"]) as conn:
            for regime, label in [("bull", "bull"), ("bear", "bear"), ("flat", "flat")]:
                dates = [d for d, r in regimes.items() if r == regime]
                if not dates:
                    continue
                # Build date-list SQL (safe: dates come from our own DB)
                date_placeholders = ",".join(["%s"] * len(dates))
                where = f"{sig_where} AND t.scan_date::text IN ({date_placeholders})"
                params = sig_params + dates
                res = _mkt_run_two_group(conn, where, params, "", [], limit=30000)
                if res:
                    results[regime] = res

        if not results:
            return {"status": "error", "error": "No regime data computed."}

        return {
            "status": "ok",
            "conditions": conditions,
            "regime_breakdown": results,
            "spy_dates_classified": {r: sum(1 for v in regimes.values() if v == r)
                                     for r in ["bull", "bear", "flat"]},
            "tip": "If signal only works in bull regime, add SPY filter or use with caution on down days.",
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ──────────────────────────────────────────────────────────────────────────
# Tool 8: Out-of-sample validation
# ──────────────────────────────────────────────────────────────────────────
def _mkt_tool_validate_oos(conditions=None, train_pct=0.6, horizon="next_day"):
    """Split dates into train (first 60%) and test (last 40%) periods.
    True test of whether a signal generalizes beyond the data it was found in."""
    import psycopg2
    if not conditions:
        return {"status": "error", "error": "conditions dict required"}
    try:
        sig_where, sig_params = _mkt_parse_conditions(conditions)
        if not sig_where:
            return {"status": "error", "error": "No valid conditions parsed."}

        with psycopg2.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
            cur.execute("SELECT DISTINCT scan_date FROM polygon_market_daily ORDER BY scan_date")
            all_dates = [str(r[0]) for r in cur.fetchall()]

        if len(all_dates) < 10:
            return {"status": "error", "error": f"Only {len(all_dates)} dates — need ≥10 for OOS split."}

        split = int(len(all_dates) * train_pct)
        train_dates = all_dates[:split]
        test_dates  = all_dates[split:]

        def run_period(dates):
            ph = ",".join(["%s"] * len(dates))
            w = f"{sig_where} AND t.scan_date::text IN ({ph})"
            p = sig_params + dates
            bw = f"t.scan_date::text IN ({ph})"
            bp = dates
            with psycopg2.connect(os.environ["DATABASE_URL"]) as conn:
                return _mkt_run_two_group(conn, w, p, bw, bp, limit=50000)

        train_res = run_period(train_dates)
        test_res  = run_period(test_dates)

        if not train_res or not test_res:
            return {"status": "error", "error": "Insufficient data in one or both periods."}

        oos_holds = (test_res["significant"] and
                     test_res["edge_winrate"] > 0 and
                     test_res["signal_win_rate"] > test_res["baseline_win_rate"])

        return {
            "status": "ok",
            "conditions": conditions,
            "train_period": {"dates": len(train_dates), "range": f"{train_dates[0]} to {train_dates[-1]}",
                             **train_res},
            "test_period":  {"dates": len(test_dates),  "range": f"{test_dates[0]} to {test_dates[-1]}",
                             **test_res},
            "oos_validated": oos_holds,
            "edge_decay": round(train_res["edge_winrate"] - test_res["edge_winrate"], 2),
            "verdict": (
                "PASSES OOS: signal holds in unseen data — safe to save as discovery."
                if oos_holds else
                "FAILS OOS: signal doesn't generalize — likely overfit. Do NOT save."
            ),
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ──────────────────────────────────────────────────────────────────────────
# Tool 9: AI generates its own hypotheses from scratch
# ──────────────────────────────────────────────────────────────────────────
def _mkt_tool_generate_hypotheses(context="", n_hypotheses=8):
    """Ask GPT-4o to invent novel signal hypotheses based on the market dataset.
    Returns a list of condition dicts ready to pass to mkt_test_signal.
    context: optional text describing what you've already found."""
    try:
        _oai_client = _get_openai_client()
        dimension_summary = _mkt_tool_explore_dimensions()
        dist = dimension_summary.get("factor_distributions", {})
        baseline = dimension_summary.get("baseline_returns", {})

        prompt = f"""You are an autonomous quantitative trading researcher with access to a full-market daily stock database.

Dataset summary:
- {dimension_summary.get('dataset', {}).get('n_dates', '?')} trading days, ~{dimension_summary.get('dataset', {}).get('avg_stocks_per_day', '?')} stocks/day
- Baseline next-day win rate: {baseline.get('baseline_win_rate', '?')}%
- Baseline avg next-day return: {baseline.get('avg_next_day_ret', '?')}%

Factor distributions (avg values):
- gap_pct (day gain %): avg={dist.get('gap_avg')}, p25={dist.get('gap_p25')}, p50={dist.get('gap_p50')}, p75={dist.get('gap_p75')}
- rvol (relative volume): avg={dist.get('rvol_avg')}, p50={dist.get('rvol_p50')}, p75={dist.get('rvol_p75')}, p90={dist.get('rvol_p90')}
- close_strength (0-1, where close landed in day range): avg={dist.get('cs_avg')}, p25={dist.get('cs_p25')}, p75={dist.get('cs_p75')}
- range_pct (high-low / low %): avg={dist.get('range_avg')}, p50={dist.get('range_p50')}, p90={dist.get('range_p90')}
- volume: avg={dist.get('vol_avg')}

Context from prior research: {context if context else 'None yet. This is the first research session.'}

Generate {n_hypotheses} distinct, testable hypotheses about which stock characteristics predict positive next-day returns.

Rules:
1. Be creative — propose non-obvious combinations
2. Each hypothesis must map to concrete thresholds using ONLY these fields: gap_pct, rvol, close_strength, range_pct, close_price, volume
3. Mix simple (1 factor) and complex (2-3 factor) hypotheses
4. Include at least one counter-intuitive hypothesis (e.g. high range is BAD)
5. Include at least one that tests a "sweet spot" (not too high, not too low)

Return a JSON array of exactly {n_hypotheses} objects, each with:
- "hypothesis": string description
- "conditions": dict with {{"factor_min/max": value}} keys
- "rationale": string explaining the logic

Return ONLY the JSON array, no other text."""

        resp = _oai_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8,
            max_tokens=2000,
        )
        import json as _j
        raw = resp.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        hypotheses = _j.loads(raw)
        return {
            "status": "ok",
            "n_generated": len(hypotheses),
            "hypotheses": hypotheses,
            "next_step": "Call mkt_test_signal(conditions=h['conditions']) for each hypothesis. Then mkt_validate_oos on significant ones.",
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ──────────────────────────────────────────────────────────────────────────
# Tool 10: Save a validated discovery
# ──────────────────────────────────────────────────────────────────────────
def _mkt_tool_save_discovery(conditions=None, hypothesis_text="", edge_broad=None,
                              edge_tight=None, signal_n=None, p_value=None,
                              signal_win_rate=None, baseline_win_rate=None,
                              signal_avg_ret=None, oos_edge=None,
                              horizon="next_day", notes=""):
    """Save a validated signal discovery to the aiem_signal_discoveries table.
    Only call this AFTER mkt_validate_oos confirms the signal holds out-of-sample."""
    import psycopg2, json as _j
    if not conditions:
        return {"status": "error", "error": "conditions required"}
    try:
        with psycopg2.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO aiem_signal_discoveries
                    (hypothesis_text, conditions_json, horizon, signal_n, signal_win_rate,
                     signal_avg_ret, edge_broad, edge_tight, p_value, oos_edge,
                     baseline_win_rate, status, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'validated', %s)
                RETURNING id
            """, (
                hypothesis_text, _j.dumps(conditions), horizon,
                signal_n, signal_win_rate, signal_avg_ret,
                edge_broad, edge_tight, p_value, oos_edge,
                baseline_win_rate, notes
            ))
            disc_id = cur.fetchone()[0]
        return {"status": "ok", "discovery_id": disc_id,
                "message": f"Discovery #{disc_id} saved. Visible at /stock-api/aiem/discoveries"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ──────────────────────────────────────────────────────────────────────────
# Tool 11: Load prior discoveries to build on past work
# ──────────────────────────────────────────────────────────────────────────
def _mkt_tool_load_discoveries(status="validated", min_edge_tight=None, min_oos_edge=None):
    """Load previously saved signal discoveries. Use this at the START of each session
    to avoid re-discovering the same signals and to build compound strategies."""
    import psycopg2
    try:
        with psycopg2.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
            where_parts = ["1=1"]
            params = []
            if status:
                where_parts.append("status = %s")
                params.append(status)
            if min_edge_tight is not None:
                where_parts.append("edge_tight >= %s")
                params.append(float(min_edge_tight))
            if min_oos_edge is not None:
                where_parts.append("oos_edge >= %s")
                params.append(float(min_oos_edge))
            cur.execute(f"""
                SELECT id, hypothesis_text, conditions_json, horizon,
                       signal_n, signal_win_rate, signal_avg_ret,
                       edge_broad, edge_tight, p_value, oos_edge,
                       status, discovered_at::text, notes
                FROM aiem_signal_discoveries
                WHERE {' AND '.join(where_parts)}
                ORDER BY COALESCE(oos_edge, edge_tight, edge_broad) DESC NULLS LAST
            """, params)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]

        for r in rows:
            for k, v in r.items():
                if hasattr(v, 'isoformat'):
                    r[k] = str(v)
        return {"status": "ok", "count": len(rows), "discoveries": rows}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ──────────────────────────────────────────────────────────────────────────
# Tool 12: Factor correlations — which dimensions predict returns?
# ──────────────────────────────────────────────────────────────────────────
def _mkt_tool_factor_correlations(horizon="next_day", sample=100000):
    """Compute Pearson correlation between each factor and next-day return.
    Also returns factor-to-factor correlations to identify independent signals."""
    import psycopg2
    import numpy as _np
    try:
        with psycopg2.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
            cur.execute(f"""
                SELECT t.gap_pct, t.rvol, t.close_strength, t.range_pct,
                       t.close_price, t.volume,
                       ((nxt.close_price/NULLIF(t.close_price,0))-1)*100 AS fwd_ret
                FROM polygon_market_daily t
                JOIN polygon_market_daily nxt
                  ON nxt.ticker = t.ticker
                 AND nxt.scan_date = (
                       SELECT MIN(x.scan_date) FROM polygon_market_daily x
                       WHERE x.ticker = t.ticker AND x.scan_date > t.scan_date
                     )
                WHERE t.close_price > 0
                LIMIT {sample}
            """)
            rows = cur.fetchall()

        if len(rows) < 100:
            return {"status": "error", "error": "Not enough data. Run the daily Polygon scan first."}

        factor_names = ["gap_pct", "rvol", "close_strength", "range_pct", "close_price", "volume"]
        data = _np.array([[r[i] if r[i] is not None else 0.0 for i in range(7)] for r in rows])
        fwd_ret = data[:, 6]

        correlations_with_return = {}
        for i, name in enumerate(factor_names):
            col = data[:, i]
            mask = (col != 0) & _np.isfinite(col) & _np.isfinite(fwd_ret)
            if mask.sum() < 50:
                correlations_with_return[name] = None
                continue
            corr = _np.corrcoef(col[mask], fwd_ret[mask])[0, 1]
            correlations_with_return[name] = round(float(corr), 4)

        factor_matrix = {}
        for i, n1 in enumerate(factor_names):
            for j, n2 in enumerate(factor_names):
                if i >= j:
                    continue
                c1, c2 = data[:, i], data[:, j]
                mask = _np.isfinite(c1) & _np.isfinite(c2) & (c1 != 0) & (c2 != 0)
                if mask.sum() < 50:
                    continue
                corr = _np.corrcoef(c1[mask], c2[mask])[0, 1]
                factor_matrix[f"{n1}_vs_{n2}"] = round(float(corr), 4)

        sorted_corr = sorted(correlations_with_return.items(),
                             key=lambda x: abs(x[1] or 0), reverse=True)

        return {
            "status": "ok",
            "n_observations": len(rows),
            "factor_return_correlations": dict(sorted_corr),
            "factor_intercorrelations": factor_matrix,
            "tip": "Factors with low intercorrelation but high return correlation are best for combining.",
            "best_predictor": sorted_corr[0][0] if sorted_corr else None,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ──────────────────────────────────────────────────────────────────────────
# Tool 13: Discover two-factor interaction grid
# ──────────────────────────────────────────────────────────────────────────
def _mkt_tool_discover_interactions(factor1="gap_pct", factor2="rvol", horizon="next_day"):
    """Build a 3x3 grid of (low/mid/high) x (low/mid/high) for two factors.
    Finds synergistic combinations that outperform either factor alone."""
    import psycopg2
    if factor1 not in _MKT_SAFE_COLS or factor2 not in _MKT_SAFE_COLS:
        return {"status": "error",
                "error": f"Invalid factors. Choose from: {list(_MKT_SAFE_COLS.keys())}"}
    try:
        col1 = _MKT_SAFE_COLS[factor1]
        col2 = _MKT_SAFE_COLS[factor2]
        with psycopg2.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
            for col, label in [(col1, factor1), (col2, factor2)]:
                cur.execute(f"""
                    SELECT PERCENTILE_CONT(0.33) WITHIN GROUP (ORDER BY {col}),
                           PERCENTILE_CONT(0.67) WITHIN GROUP (ORDER BY {col})
                    FROM polygon_market_daily WHERE {col} IS NOT NULL
                """)
                row = cur.fetchone()
                if col == col1:
                    p33_1, p67_1 = float(row[0] or 0), float(row[1] or 0)
                else:
                    p33_2, p67_2 = float(row[0] or 0), float(row[1] or 0)

            grid = {}
            for tier1, lo1, hi1 in [("low",  None, p33_1), ("mid", p33_1, p67_1), ("high", p67_1, None)]:
                for tier2, lo2, hi2 in [("low",  None, p33_2), ("mid", p33_2, p67_2), ("high", p67_2, None)]:
                    parts = []
                    if lo1 is not None: parts.append(f"{col1} >= {lo1}")
                    if hi1 is not None: parts.append(f"{col1} < {hi1}")
                    if lo2 is not None: parts.append(f"{col2} >= {lo2}")
                    if hi2 is not None: parts.append(f"{col2} < {hi2}")
                    where = " AND ".join(parts) if parts else "1=1"
                    cur.execute(f"""
                        SELECT COUNT(*),
                               ROUND(AVG(((nxt.close_price/NULLIF(t.close_price,0))-1)*100)::numeric,3),
                               ROUND((COUNT(*) FILTER (WHERE ((nxt.close_price/NULLIF(t.close_price,0))-1)*100 > 0))
                                     ::numeric / NULLIF(COUNT(*),0)*100, 2)
                        FROM polygon_market_daily t
                        JOIN polygon_market_daily nxt
                          ON nxt.ticker = t.ticker
                         AND nxt.scan_date = (
                               SELECT MIN(x.scan_date) FROM polygon_market_daily x
                               WHERE x.ticker = t.ticker AND x.scan_date > t.scan_date
                             )
                        WHERE t.close_price > 0 AND {where}
                        LIMIT 30000
                    """)
                    r = cur.fetchone()
                    key = f"{tier1}_{factor1}_x_{tier2}_{factor2}"
                    grid[key] = {
                        "n": int(r[0] or 0),
                        "avg_ret": float(r[1] or 0),
                        "win_rate": float(r[2] or 0),
                        "thresholds": {"f1_lo": lo1, "f1_hi": hi1, "f2_lo": lo2, "f2_hi": hi2},
                    }

        best_cell = max(grid.items(), key=lambda x: x[1]["win_rate"])
        return {
            "status": "ok",
            "factor1": factor1, "factor2": factor2,
            "grid": grid,
            "best_combination": best_cell[0],
            "best_win_rate": best_cell[1]["win_rate"],
            "best_n": best_cell[1]["n"],
            "percentile_splits": {
                f"{factor1}_p33": p33_1, f"{factor1}_p67": p67_1,
                f"{factor2}_p33": p33_2, f"{factor2}_p67": p67_2,
            },
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ──────────────────────────────────────────────────────────────────────────
# Tool 14: Signal drift — is a signal decaying over time?
# ──────────────────────────────────────────────────────────────────────────
def _mkt_tool_signal_drift(conditions=None, recent_days=30, historical_days=90, horizon="next_day"):
    """Compare win rate in the most recent N days vs the prior M days.
    A decaying signal shows higher historical edge than recent edge."""
    import psycopg2
    if not conditions:
        return {"status": "error", "error": "conditions dict required"}
    try:
        sig_where, sig_params = _mkt_parse_conditions(conditions)
        if not sig_where:
            return {"status": "error", "error": "No valid conditions parsed."}

        with psycopg2.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
            cur.execute("SELECT DISTINCT scan_date FROM polygon_market_daily ORDER BY scan_date DESC LIMIT %s",
                        [historical_days + 30])
            all_dates = [str(r[0]) for r in cur.fetchall()]

        if len(all_dates) < recent_days + 5:
            return {"status": "error",
                    "error": f"Only {len(all_dates)} dates available. Need >{recent_days+5}."}

        recent_dates = all_dates[:recent_days]
        hist_dates   = all_dates[recent_days:historical_days]

        def run_period(dates):
            ph = ",".join(["%s"] * len(dates))
            w = f"{sig_where} AND t.scan_date::text IN ({ph})"
            p = sig_params + dates
            bw = f"t.scan_date::text IN ({ph})"
            bp = dates
            with psycopg2.connect(os.environ["DATABASE_URL"]) as conn:
                return _mkt_run_two_group(conn, w, p, bw, bp, limit=30000)

        recent_res = run_period(recent_dates)
        hist_res   = run_period(hist_dates)

        if not recent_res or not hist_res:
            return {"status": "error", "error": "Insufficient data in one period."}

        drift = hist_res["edge_winrate"] - recent_res["edge_winrate"]
        decaying = drift > 3.0

        return {
            "status": "ok",
            "conditions": conditions,
            "recent": {"period": f"last {recent_days} trading days", **recent_res},
            "historical": {"period": f"prior {len(hist_dates)} trading days", **hist_res},
            "edge_drift_pp": round(drift, 2),
            "decaying": decaying,
            "verdict": (
                f"SIGNAL DECAYING: edge dropped {drift:.1f}pp recently vs historical. Consider retiring."
                if decaying else
                f"Signal stable. Recent edge vs historical: {drift:+.1f}pp drift."
            ),
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ──────────────────────────────────────────────────────────────────────────
# Tool 15: Volume patterns — accumulation vs distribution
# ──────────────────────────────────────────────────────────────────────────
def _mkt_tool_volume_patterns(horizon="next_day"):
    """Compute win rates for classic volume patterns:
    - Accumulation: high close_strength + high rvol (institutional buying)
    - Distribution: low close_strength + high rvol (institutional selling)
    - Volume dry-up: rvol < 0.5 (quiet before the move)
    - Normal: everything else"""
    import psycopg2
    patterns = {
        "accumulation":  "close_strength >= 0.7 AND rvol >= 1.5",
        "distribution":  "close_strength <= 0.3 AND rvol >= 1.5",
        "volume_dry_up": "rvol < 0.5",
        "high_rvol_mid_close": "rvol >= 2.0 AND close_strength BETWEEN 0.4 AND 0.6",
        "gap_accumulation": "gap_pct >= 2.0 AND close_strength >= 0.7 AND rvol >= 1.5",
    }
    try:
        results = {}
        with psycopg2.connect(os.environ["DATABASE_URL"]) as conn:
            overall = _mkt_run_two_group(conn, "", [], "", [], limit=200000)
            for name, where in patterns.items():
                res = _mkt_run_two_group(conn, where, [], "", [], limit=50000)
                if res:
                    results[name] = res

        if not results:
            return {"status": "error", "error": "No data — run Polygon scan first."}

        best = max(results.items(), key=lambda x: x[1]["edge_winrate"])
        return {
            "status": "ok",
            "overall_baseline": overall,
            "patterns": results,
            "best_pattern": best[0],
            "best_edge_winrate": best[1]["edge_winrate"],
            "definitions": patterns,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ──────────────────────────────────────────────────────────────────────────
# Tool 16: Price patterns — range compression, breakout day
# ──────────────────────────────────────────────────────────────────────────
def _mkt_tool_price_patterns(horizon="next_day"):
    """Compute win rates for price structure patterns:
    - Strong close: closed in top 20% of day range (buyers in control)
    - Weak close: closed in bottom 20% (sellers in control)
    - Big range day: range_pct >= 4% (high volatility)
    - Tight range day: range_pct <= 1% (compression before expansion)
    - Gap up strong close: gapped up AND closed strong"""
    import psycopg2
    patterns = {
        "strong_close":        "close_strength >= 0.8",
        "weak_close":          "close_strength <= 0.2",
        "big_range_day":       "range_pct >= 4.0",
        "tight_range_day":     "range_pct <= 1.0",
        "gap_strong_close":    "gap_pct >= 1.0 AND close_strength >= 0.7",
        "gap_weak_close":      "gap_pct >= 1.0 AND close_strength <= 0.3",
        "high_price":          "close_price >= 20",
        "low_price":           "close_price < 5",
    }
    try:
        results = {}
        with psycopg2.connect(os.environ["DATABASE_URL"]) as conn:
            for name, where in patterns.items():
                res = _mkt_run_two_group(conn, where, [], "", [], limit=50000)
                if res:
                    results[name] = res

        if not results:
            return {"status": "error", "error": "No data."}

        best = max(results.items(), key=lambda x: x[1]["edge_winrate"])
        worst = min(results.items(), key=lambda x: x[1]["edge_winrate"])
        return {
            "status": "ok",
            "patterns": results,
            "best_pattern": {"name": best[0], "edge": best[1]["edge_winrate"]},
            "worst_pattern": {"name": worst[0], "edge": worst[1]["edge_winrate"]},
            "definitions": patterns,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ──────────────────────────────────────────────────────────────────────────
# Tool 17: Multi-day momentum features
# ──────────────────────────────────────────────────────────────────────────
def _mkt_tool_compute_momentum(lookback_days=5, horizon="next_day"):
    """Compute multi-day return momentum (how stocks did over prior N days)
    and test whether momentum predicts next-day returns. Tests both momentum
    continuation and mean-reversion hypotheses."""
    import psycopg2
    try:
        with psycopg2.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
            cur.execute(f"""
                WITH momentum AS (
                    SELECT t.ticker, t.scan_date, t.close_price,
                           ((t.close_price / NULLIF(prev.close_price, 0)) - 1) * 100 AS momentum_pct,
                           ((nxt.close_price / NULLIF(t.close_price, 0)) - 1) * 100 AS fwd_ret
                    FROM polygon_market_daily t
                    JOIN polygon_market_daily prev
                      ON prev.ticker = t.ticker
                     AND prev.scan_date = (
                           SELECT MAX(x.scan_date) FROM polygon_market_daily x
                           WHERE x.ticker = t.ticker AND x.scan_date < t.scan_date
                           AND x.scan_date >= t.scan_date - INTERVAL '{lookback_days + 5} days'
                           LIMIT 1
                         )
                    JOIN polygon_market_daily nxt
                      ON nxt.ticker = t.ticker
                     AND nxt.scan_date = (
                           SELECT MIN(x.scan_date) FROM polygon_market_daily x
                           WHERE x.ticker = t.ticker AND x.scan_date > t.scan_date
                         )
                    WHERE t.close_price > 0 AND prev.close_price > 0
                )
                SELECT
                    COUNT(*) AS n,
                    ROUND(AVG(momentum_pct)::numeric, 3) AS avg_momentum,
                    -- continuation: high prior return → high next return
                    ROUND(AVG(fwd_ret) FILTER (WHERE momentum_pct >= 5)::numeric, 4) AS avg_fwd_high_momentum,
                    ROUND(AVG(fwd_ret) FILTER (WHERE momentum_pct < 0)::numeric, 4) AS avg_fwd_neg_momentum,
                    ROUND(AVG(fwd_ret) FILTER (WHERE momentum_pct BETWEEN -1 AND 1)::numeric, 4) AS avg_fwd_flat_momentum,
                    ROUND((COUNT(*) FILTER (WHERE fwd_ret > 0 AND momentum_pct >= 5))::numeric
                          / NULLIF(COUNT(*) FILTER (WHERE momentum_pct >= 5), 0) * 100, 2) AS wr_high_momentum,
                    ROUND((COUNT(*) FILTER (WHERE fwd_ret > 0 AND momentum_pct < 0))::numeric
                          / NULLIF(COUNT(*) FILTER (WHERE momentum_pct < 0), 0) * 100, 2) AS wr_neg_momentum
                FROM momentum
                LIMIT 200000
            """)
            row = dict(zip([d[0] for d in cur.description], cur.fetchone() or []))

        return {
            "status": "ok",
            "lookback_days": lookback_days,
            "n_observations": int(row.get("n") or 0),
            "avg_momentum_pct": float(row.get("avg_momentum") or 0),
            "high_momentum_stocks": {
                "avg_next_day_ret": float(row.get("avg_fwd_high_momentum") or 0),
                "win_rate": float(row.get("wr_high_momentum") or 0),
                "definition": "stocks up 5%+ in prior period",
            },
            "negative_momentum_stocks": {
                "avg_next_day_ret": float(row.get("avg_fwd_neg_momentum") or 0),
                "win_rate": float(row.get("wr_neg_momentum") or 0),
                "definition": "stocks down in prior period",
            },
            "flat_momentum_stocks": {
                "avg_next_day_ret": float(row.get("avg_fwd_flat_momentum") or 0),
                "definition": "stocks ±1% in prior period",
            },
            "interpretation": "Compare wr_high_momentum vs wr_neg_momentum to determine if trend-following or mean-reversion applies.",
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ──────────────────────────────────────────────────────────────────────────
# Tool 18: AI invents a completely new indicator
# ──────────────────────────────────────────────────────────────────────────
def _mkt_tool_invent_indicator(inspiration="", horizon="next_day"):
    """Ask GPT-4o to invent a completely new composite indicator from first principles,
    define it as a SQL expression, then test it live against the market database.
    This is the creative invention tool — each run produces a novel indicator."""
    import psycopg2
    try:
        _oai_client = _get_openai_client()
        prompt = f"""You are an autonomous quantitative researcher inventing new stock market indicators.

Available database columns in polygon_market_daily:
- gap_pct: day gain % vs prior close
- rvol: relative volume (today vol / avg prior vol). NULL if prior data unavailable.
- close_strength: (close - low) / (high - low), range 0-1
- range_pct: (high - low) / low * 100, daily range as % of low
- close_price: closing price
- open_price: opening price
- volume: share volume

Inspiration / context: {inspiration if inspiration else 'No prior context. Invent something entirely new.'}

Invent ONE new composite indicator. Rules:
1. Must be a single SQL expression combining 2+ columns (no subqueries)
2. Use basic math: +, -, *, /, SQRT, ABS, POWER, NULLIF, LEAST, GREATEST
3. The expression must return a single float value per row
4. Think about what combination would identify "institutional accumulation" or "unusual setup"
5. Be creative — try ratios, products, and weighted combinations

Return JSON with exactly these fields:
{{"name": "indicator name", "expression": "SQL expression here", "rationale": "why this should predict returns", "high_means": "what a high value indicates"}}

Return ONLY the JSON, no other text."""

        resp = _oai_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.9,
            max_tokens=500,
        )
        import json as _j
        raw = resp.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        indicator = _j.loads(raw)
        expr = indicator.get("expression", "")

        # Validate expression is safe (no semicolons, no DROP/DELETE, no subqueries)
        forbidden = [";", "DROP", "DELETE", "INSERT", "UPDATE", "TRUNCATE",
                     "SELECT", "--", "/*", "*/", "EXEC", "EXECUTE"]
        for f in forbidden:
            if f.upper() in expr.upper():
                return {"status": "error", "error": f"Unsafe SQL expression detected: {f}"}

        # Test the invented indicator against forward returns
        with psycopg2.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
            test_sql = f"""
                WITH ind AS (
                    SELECT
                        ({expr}) AS indicator_value,
                        ((nxt.close_price/NULLIF(t.close_price,0))-1)*100 AS fwd_ret
                    FROM polygon_market_daily t
                    JOIN polygon_market_daily nxt
                      ON nxt.ticker = t.ticker
                     AND nxt.scan_date = (
                           SELECT MIN(x.scan_date) FROM polygon_market_daily x
                           WHERE x.ticker = t.ticker AND x.scan_date > t.scan_date
                         )
                    WHERE t.close_price > 0 AND ({expr}) IS NOT NULL
                    LIMIT 100000
                )
                SELECT
                    COUNT(*) AS n,
                    ROUND(PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY indicator_value)::numeric, 4) AS p75,
                    ROUND(AVG(fwd_ret) FILTER (WHERE indicator_value >= PERCENTILE_CONT(0.75)
                          WITHIN GROUP (ORDER BY indicator_value) OVER ())::numeric, 4) AS top_quartile_fwd,
                    ROUND(AVG(fwd_ret) FILTER (WHERE indicator_value < PERCENTILE_CONT(0.25)
                          WITHIN GROUP (ORDER BY indicator_value) OVER ())::numeric, 4) AS bottom_quartile_fwd,
                    ROUND((COUNT(*) FILTER (WHERE indicator_value >= PERCENTILE_CONT(0.75)
                           WITHIN GROUP (ORDER BY indicator_value) OVER ()
                           AND fwd_ret > 0))::numeric / NULLIF(COUNT(*) FILTER (
                           WHERE indicator_value >= PERCENTILE_CONT(0.75) WITHIN GROUP
                           (ORDER BY indicator_value) OVER ()), 0) * 100, 2) AS top_win_rate
                FROM ind
            """
            cur.execute(test_sql)
            test_row = dict(zip([d[0] for d in cur.description], cur.fetchone() or []))

        return {
            "status": "ok",
            "invented_indicator": indicator,
            "test_results": {k: float(v) if v is not None else None for k, v in test_row.items()},
            "interpretation": (
                f"Top quartile of '{indicator.get('name')}' achieved "
                f"{test_row.get('top_win_rate', '?')}% win rate vs bottom quartile "
                f"{test_row.get('bottom_quartile_fwd', '?')}% avg return."
            ),
            "next_step": "If top_win_rate is meaningfully above baseline, test with mkt_test_signal using equivalent threshold conditions.",
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ──────────────────────────────────────────────────────────────────────────
# Tool 19: Head-to-head signal comparison
# ──────────────────────────────────────────────────────────────────────────
def _mkt_tool_compare_signals(conditions_a=None, conditions_b=None, horizon="next_day"):
    """Rigorous A vs B head-to-head comparison of two signal condition sets.
    Tests both separately AND their intersection to find synergy."""
    import psycopg2
    if not conditions_a or not conditions_b:
        return {"status": "error", "error": "Both conditions_a and conditions_b required."}
    try:
        wa, pa = _mkt_parse_conditions(conditions_a)
        wb, pb = _mkt_parse_conditions(conditions_b)
        if not wa or not wb:
            return {"status": "error", "error": "Failed to parse one or both condition sets."}

        intersection_where = f"({wa}) AND ({wb})"
        intersection_params = pa + pb

        with psycopg2.connect(os.environ["DATABASE_URL"]) as conn:
            res_a    = _mkt_run_two_group(conn, wa, pa, "", [])
            res_b    = _mkt_run_two_group(conn, wb, pb, "", [])
            res_both = _mkt_run_two_group(conn, intersection_where, intersection_params, "", [])

        if not res_a or not res_b:
            return {"status": "error", "error": "Insufficient data for comparison."}

        winner = "A" if (res_a["edge_winrate"] > res_b["edge_winrate"]) else "B"
        synergy = (res_both and res_both["edge_winrate"] > max(res_a["edge_winrate"], res_b["edge_winrate"]))

        return {
            "status": "ok",
            "signal_a": {"conditions": conditions_a, **res_a},
            "signal_b": {"conditions": conditions_b, **res_b},
            "intersection_a_and_b": ({"conditions": "A AND B", **res_both} if res_both else None),
            "winner": winner,
            "synergy_detected": synergy,
            "verdict": (
                f"Signal {winner} wins. "
                + ("Intersection SYNERGY: combining A+B outperforms either alone." if synergy
                   else "No synergy: combining A+B doesn't outperform the better signal alone.")
            ),
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ──────────────────────────────────────────────────────────────────────────
# Tool 20: Build a composite score from multiple discoveries
# ──────────────────────────────────────────────────────────────────────────
def _mkt_tool_build_composite(discovery_ids=None, horizon="next_day"):
    """Combine multiple validated discoveries into a composite signal.
    Tests: each signal alone vs requiring 2+ signals vs ALL signals.
    Helps find the optimal combination threshold."""
    import psycopg2, json as _j
    if not discovery_ids:
        return {"status": "error", "error": "discovery_ids list required (get from mkt_load_discoveries)."}
    try:
        with psycopg2.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
            placeholders = ",".join(["%s"] * len(discovery_ids))
            cur.execute(f"""
                SELECT id, hypothesis_text, conditions_json, signal_win_rate, edge_broad
                FROM aiem_signal_discoveries
                WHERE id IN ({placeholders}) AND status = 'validated'
                ORDER BY COALESCE(edge_broad, 0) DESC
            """, discovery_ids)
            discoveries = [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]

        if not discoveries:
            return {"status": "error", "error": "No validated discoveries found for given IDs."}

        all_conditions = []
        for d in discoveries:
            conds = _j.loads(d["conditions_json"]) if isinstance(d["conditions_json"], str) else d["conditions_json"]
            all_conditions.append(conds)

        # Test each alone
        results_solo = []
        with psycopg2.connect(os.environ["DATABASE_URL"]) as conn:
            for i, conds in enumerate(all_conditions):
                w, p = _mkt_parse_conditions(conds)
                if not w:
                    continue
                res = _mkt_run_two_group(conn, w, p, "", [], limit=30000)
                if res:
                    results_solo.append({
                        "discovery_id": discoveries[i]["id"],
                        "hypothesis": discoveries[i]["hypothesis_text"][:80] if discoveries[i]["hypothesis_text"] else "",
                        **res
                    })

            # Test ALL together (AND of all conditions)
            all_parts, all_params = [], []
            for conds in all_conditions:
                w, p = _mkt_parse_conditions(conds)
                if w:
                    all_parts.append(f"({w})")
                    all_params.extend(p)
            if all_parts:
                all_where = " AND ".join(all_parts)
                res_all = _mkt_run_two_group(conn, all_where, all_params, "", [], limit=30000)
            else:
                res_all = None

        best_solo = max(results_solo, key=lambda x: x["edge_winrate"]) if results_solo else None
        composite_better = (res_all and best_solo and
                            res_all["edge_winrate"] > best_solo["edge_winrate"])

        return {
            "status": "ok",
            "n_discoveries_combined": len(discoveries),
            "individual_results": results_solo,
            "all_combined": res_all,
            "best_individual": best_solo,
            "composite_outperforms_best_solo": composite_better,
            "verdict": (
                f"Composite (all {len(discoveries)} signals together) has "
                f"{res_all['edge_winrate'] if res_all else 'N/A'}pp edge. "
                + ("COMPOSITE WINS: use all signals together." if composite_better
                   else f"Best single signal wins: use Discovery #{best_solo['discovery_id']} alone.")
                if res_all and best_solo else "Insufficient data for comparison."
            ),
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ──────────────────────────────────────────────────────────────────────────
# Historical backfill: re-fetch all missing trading days from Polygon
# ──────────────────────────────────────────────────────────────────────────


================================================================================
# SECTION: _polygon_backfill_historical() — fills Apr-Jun 2026 data at 5 req/min  (main.py lines 16406–16543)
================================================================================
def _polygon_backfill_historical():
    """Background thread: fetch all trading days since start_date that are missing
    from polygon_market_daily. Saves ALL stocks (not just top movers)."""
    import time as _bt, threading as _bth, psycopg2 as _bpg, urllib.request as _bur, json as _bj
    from datetime import date as _bdate, timedelta as _btd

    def _run():
        _bt.sleep(30)  # Let server fully start first
        print("[backfill] starting polygon_market_daily historical backfill")
        _key = os.environ.get("POLYGON_API_KEY", "")
        if not _key:
            print("[backfill] no POLYGON_API_KEY — skipping")
            return

        start = _bdate(2026, 4, 1)
        today = _bdate.today()

        try:
            with _bpg.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
                cur.execute("SELECT DISTINCT scan_date FROM polygon_market_daily")
                have_dates = {str(r[0]) for r in cur.fetchall()}
        except Exception as e:
            print(f"[backfill] DB check error: {e}")
            return

        # Build candidate trading days
        candidates = []
        d = start
        while d < today:
            if d.weekday() < 5:  # Mon-Fri
                ds = d.strftime("%Y-%m-%d")
                if ds not in have_dates:
                    candidates.append(ds)
            d += _btd(days=1)

        if not candidates:
            print("[backfill] polygon_market_daily already up to date")
            return

        print(f"[backfill] fetching {len(candidates)} missing dates...")
        saved_total = 0

        for date_str in candidates:
            try:
                url = (f"https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks/{date_str}"
                       f"?adjusted=true&apiKey={_key}")
                with _bur.urlopen(url, timeout=25) as r:
                    data = _bj.load(r)
                status = data.get("status", "")
                if status not in ("OK", "DELAYED"):
                    print(f"[backfill] {date_str}: skip (status={status})")
                    _bt.sleep(13)
                    continue
                results = data.get("results", [])
                if not results:
                    print(f"[backfill] {date_str}: 0 results (holiday?)")
                    _bt.sleep(13)
                    continue

                # Build lookup dict
                today_data = {x["T"]: x for x in results}
                rows = []
                for ticker, r in today_data.items():
                    close = r.get("c") or 0
                    open_ = r.get("o") or 0
                    high  = r.get("h") or 0
                    low   = r.get("l") or 0
                    vwap  = r.get("vw") or 0
                    vol   = r.get("v") or 0
                    if close < 0.50 or vol < 30000 or close == 0:
                        continue
                    cs = ((close - low) / (high - low)) if high > low else None
                    rng = ((high - low) / low * 100) if low > 0 else None
                    rows.append((date_str, ticker, close, open_ or None, high or None,
                                 low or None, vwap or None, int(vol),
                                 None, None, None, cs, rng))

                if not rows:
                    print(f"[backfill] {date_str}: no rows after filter")
                    _bt.sleep(13)
                    continue

                with _bpg.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
                    cur.executemany("""
                        INSERT INTO polygon_market_daily
                            (scan_date, ticker, close_price, open_price, high_price, low_price,
                             vwap, volume, prev_close, gap_pct, rvol, close_strength, range_pct)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (scan_date, ticker) DO NOTHING
                    """, rows)
                    conn.commit()

                saved_total += len(rows)
                print(f"[backfill] {date_str}: saved {len(rows)} stocks (total so far: {saved_total})")
                _bt.sleep(13)  # 5 req/min Polygon rate limit

            except Exception as e:
                print(f"[backfill] {date_str} error: {e}")
                _bt.sleep(20)

        # Now compute gap_pct for consecutive dates where prev_close available
        try:
            with _bpg.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
                cur.execute("""
                    UPDATE polygon_market_daily t
                    SET prev_close = prev.close_price,
                        gap_pct = (t.close_price - prev.close_price) / NULLIF(prev.close_price,0) * 100
                    FROM polygon_market_daily prev
                    WHERE prev.ticker = t.ticker
                      AND prev.scan_date = (
                            SELECT MAX(x.scan_date) FROM polygon_market_daily x
                            WHERE x.ticker = t.ticker AND x.scan_date < t.scan_date
                          )
                      AND t.prev_close IS NULL
                """)
                conn.commit()
                print("[backfill] gap_pct backfill complete")
        except Exception as e:
            print(f"[backfill] gap_pct update error: {e}")

        print(f"[backfill] DONE. Total rows saved: {saved_total}")


# ── Startup: init tables + launch historical backfill ────────────────────
try:
    _mkt_init_tables()
except Exception as _mkt_e:
    print(f"[mkt_init] {_mkt_e}")
try:
    _polygon_backfill_historical()
except Exception as _bf_e:
    print(f"[backfill] {_bf_e}")

    _bth.Thread(target=_run, daemon=True, name="polygon-backfill").start()



# ── Continuous Research Loop (runs daily + feeds Sunday session) ────────────


================================================================================
# SECTION: _run_aiem_continuous_research() — Loop B daily 6PM ET + post-scan trigger  (main.py lines 16544–16648)
================================================================================
def _run_aiem_continuous_research():
    """
    Daily autonomous research session. Runs at 6 PM ET Mon-Fri.
    Tests a rotating battery of hypotheses, updates the research model
    when real findings emerge. This is the 24/7 learning engine that
    makes the system smarter every day, not just on Sundays.

    Strategy:
      1. Review last 7 days of new signal outcomes
      2. Auto-test 8 standard hypothesis templates with current thresholds
      3. Analyze any missed movers from today
      4. If a finding has p<0.05 and n>=15, save to aiem_research_insights
         and flag for Sunday consolidation
      5. Log the session so Sunday agent can build on it
    """
    import datetime as _crd
    import json as _crj
    print("[aiem_continuous] starting daily hypothesis sweep...")

    _HYPOTHESIS_BATTERY = [
        # Each entry: (description, conditions, target)
        ("Sweep + high vol/OI", ['has_sweep = true', 'sweep_vol_oi > 5'], "t3_win"),
        ("Sweep + large premium", ["has_sweep = true", "sweep_premium_m > 0.5"], "t3_win"),
        ("High call/put ratio + sweep", ["call_put_ratio > 2.0", "has_sweep = true"], "t3_win"),
        ("Very high call/put ratio", ["call_put_ratio > 3.0"], "t3_win"),
        ("Short-dated + near-ATM sweep", ["has_sweep = true", "days_out < 21", "otm_pct > -10"], "t3_win"),
        ("Short-dated sweep (< 21 days)", ["has_sweep = true", "days_out < 21"], "t3_win"),
        ("Near-ATM sweep", ["has_sweep = true", "otm_pct > -10"], "t3_win"),
        ("High IV sweep", ["has_sweep = true", "sweep_iv > 0.8"], "t3_win"),
        ("Large premium any direction", ["premium_m > 1.0"], "t3_win"),
        ("Sweep + T5 target", ["has_sweep = true", "sweep_vol_oi > 3"], "t5_win"),
        ("Return maximizer: sweep + big prem", ["has_sweep = true", "sweep_premium_m > 1.0"], "t3_pct"),
    ]

    findings = []
    try:
        for desc, conditions, target in _HYPOTHESIS_BATTERY:
            try:
                result = _aiem_tool_test_new_signal(conditions=conditions, target=target,
                                                     lookback_days=60)
                if result.get("status") == "error":
                    continue
                res = result.get("result") or {}
                n = res.get("n", 0)
                p = res.get("p_value", 1.0)
                wr = res.get("win_rate_pct", 0)
                edge = res.get("edge_vs_baseline_pct", 0)
                verdict = res.get("verdict", "")
                if n >= 8:
                    findings.append({
                        "description": desc,
                        "conditions": conditions,
                        "target": target,
                        "n": n, "win_rate_pct": wr, "p_value": p,
                        "edge_vs_baseline_pct": edge,
                        "verdict": verdict,
                        "significant": n >= 15 and p < 0.05,
                    })
            except Exception as _he:
                print(f"[aiem_continuous] hypothesis error ({desc}): {_he}")

        # Sort by significance
        findings.sort(key=lambda x: (x["p_value"], -x["n"]))

        # Save significant findings to DB for Sunday consolidation
        significant = [f for f in findings if f["significant"]]
        if significant:
            with _psycopg2.connect(_DB_URL) as _cs, _cs.cursor() as _cus:
                for f in significant:
                    _cus.execute("""
                        INSERT INTO aiem_research_insights
                            (research_date, findings, confidence)
                        VALUES (%s, %s, %s)
                    """, (
                        _crd.date.today(),
                        _crj.dumps({
                            "type": "continuous_research_finding",
                            "description": f["description"],
                            "conditions": f["conditions"],
                            "target": f["target"],
                            "n": f["n"],
                            "win_rate_pct": f["win_rate_pct"],
                            "p_value": f["p_value"],
                            "edge_vs_baseline_pct": f["edge_vs_baseline_pct"],
                            "found_at": _crd.datetime.now().isoformat(),
                        }),
                        "HIGH" if f["p_value"] < 0.01 else "MEDIUM",
                    ))
                _cs.commit()
            print(f"[aiem_continuous] saved {len(significant)} significant finding(s) to DB")

        # Log summary
        top = findings[:5]
        print(f"[aiem_continuous] completed. {len(findings)} hypotheses tested, "
              f"{len(significant)} significant. "
              f"Best: {top[0]['description'] if top else 'none'} "
              f"(WR={top[0]['win_rate_pct'] if top else 0}%%, p={top[0]['p_value'] if top else 1})")

        return {"tested": len(findings), "significant": len(significant), "top_findings": top[:3]}

    except Exception as e:
        print(f"[aiem_continuous] error: {e}")
        return {"error": str(e)}




================================================================================
# SECTION: _AIEM_AGENT_TOOLS — 20 tool schemas wired to mkt_* functions  (main.py lines 16649–17223)
================================================================================
_AIEM_AGENT_TOOLS = [
    {"type": "function", "function": {
        "name": "query_pick_outcomes",
        "description": (
            "Get all AI Early Movers picks from the last N trading days including "
            "T+3 and T+7 price outcomes. Use this FIRST to understand overall win rate."
        ),
        "parameters": {"type": "object", "properties": {
            "days_back": {"type": "integer", "description": "Days to look back (max 90, default 30)"}
        }, "required": []}
    }},
    {"type": "function", "function": {
        "name": "query_missed_movers",
        "description": (
            "Get stocks that moved 5%+ that you did NOT pick. "
            "Reveals what you are systematically missing."
        ),
        "parameters": {"type": "object", "properties": {
            "days_back": {"type": "integer"}
        }, "required": []}
    }},
    {"type": "function", "function": {
        "name": "analyze_signal_correlation",
        "description": (
            "Win rate for picks WITH vs WITHOUT a specific boolean signal. "
            "signal options: 'confirmed_2d', 'high_conviction', 'buy_stock'."
        ),
        "parameters": {"type": "object", "properties": {
            "signal": {"type": "string", "enum": ["confirmed_2d", "high_conviction", "buy_stock"]},
            "days_back": {"type": "integer"}
        }, "required": ["signal"]}
    }},
    {"type": "function", "function": {
        "name": "compare_picks_vs_misses",
        "description": "Side-by-side: what AI picked vs what AI missed. Reveals systematic bias.",
        "parameters": {"type": "object", "properties": {
            "days_back": {"type": "integer"}
        }, "required": []}
    }},
    {"type": "function", "function": {
        "name": "discover_numeric_patterns",
        "description": (
            "Quartile win rate analysis on a numeric metric. "
            "metric options: 'day_ret', 'vol_oi', 'stock_price'. "
            "Finds optimal thresholds — e.g. day_ret 3-6% wins 65%, below 2% wins 38%."
        ),
        "parameters": {"type": "object", "properties": {
            "metric": {"type": "string", "enum": ["day_ret", "vol_oi", "stock_price"]},
            "days_back": {"type": "integer"}
        }, "required": ["metric"]}
    }},
    {"type": "function", "function": {
        "name": "test_scoring_hypothesis",
        "description": (
            "Backtest a proposed scoring model. "
            "Weights: {confirmed_2d, high_conviction, buy_stock, day_ret_multiplier, vol_oi_factor}. "
            "Returns top-half vs bottom-half win rate split. Iterate to maximize improvement."
        ),
        "parameters": {"type": "object", "properties": {
            "weights": {"type": "object"},
            "days_back": {"type": "integer"}
        }, "required": ["weights"]}
    }},
    {"type": "function", "function": {
        "name": "query_market_regime",
        "description": (
            "Break win rates by SPY market regime (BULL/BEAR/CHOP) and VIX level (LOW/MED/HIGH). "
            "Critical for regime-conditional scoring — confirmed_2d may work in bull but not bear. "
            "Use this to discover regime-specific weights."
        ),
        "parameters": {"type": "object", "properties": {
            "days_back": {"type": "integer"}
        }, "required": []}
    }},
    {"type": "function", "function": {
        "name": "query_cross_signal_overlap",
        "description": (
            "Check whether picks confirmed by MULTIPLE scanners (Conviction Stack + Unusual Calls) "
            "win at a higher rate than solo picks. Multi-system confirmation may be your strongest filter. "
            "Returns: conviction_stack_YES/NO, unusual_calls_YES/NO, both_confirmed, neither."
        ),
        "parameters": {"type": "object", "properties": {
            "days_back": {"type": "integer"}
        }, "required": []}
    }},
    {"type": "function", "function": {
        "name": "rollback_to_previous_model",
        "description": (
            "ONLY call this if evaluate_previous_model returned MODEL HURT verdict. "
            "Actually copies the previous week's validated weights back as today's baseline. "
            "After calling this, continue research to build improvements ON TOP of the restored weights. "
            "Do NOT call unless MODEL HURT is confirmed — a neutral model should be updated, not rolled back."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []}
    }},
    {"type": "function", "function": {
        "name": "evaluate_previous_model",
        "description": (
            "Grade last week's scoring model: did picks improve AFTER the model was applied vs BEFORE? "
            "Returns win rate change in percentage points and a verdict: MODEL HELPED / NEUTRAL / HURT. "
            "Always call this before writing a new model to understand if you should build on or override the last one."
        ),
        "parameters": {"type": "object", "properties": {
            "lookback_weeks": {"type": "integer"}
        }, "required": []}
    }},
    {"type": "function", "function": {
        "name": "query_temporal_patterns",
        "description": (
            "Discover time-based patterns: day of week, week of month, options expiration week. "
            "E.g. 'Monday picks win 67%, Friday picks win 39%' — apply day-of-week gate. "
            "OpEx week often shows mean-reversion — lower win rates mean reduce exposure."
        ),
        "parameters": {"type": "object", "properties": {
            "days_back": {"type": "integer"}
        }, "required": []}
    }},
    {"type": "function", "function": {
        "name": "query_rank_effectiveness",
        "description": (
            "Does rank #1 actually win more than rank #5? "
            "If top-ranked picks do not outperform lower-ranked, your ranking model is not working. "
            "Returns win rate and avg return by rank position (1-10)."
        ),
        "parameters": {"type": "object", "properties": {
            "days_back": {"type": "integer"}
        }, "required": []}
    }},
    {"type": "function", "function": {
        "name": "query_exit_timing",
        "description": (
            "T+3 vs T+7 exit optimization by signal type. "
            "For each signal (confirmed_2d, high_conviction), does holding to T+7 add return or give it back? "
            "Determines optimal exit horizon recommendation."
        ),
        "parameters": {"type": "object", "properties": {
            "days_back": {"type": "integer"}
        }, "required": []}
    }},
    {"type": "function", "function": {
        "name": "run_statistical_significance",
        "description": (
            "Bootstrap test: is the observed win rate difference between two groups statistically significant? "
            "Prevents over-fitting on small samples. "
            "Provide group_a_wins, group_a_n, group_b_wins, group_b_n. "
            "Returns p_value and is_significant (p<0.05)."
        ),
        "parameters": {"type": "object", "properties": {
            "group_a_wins": {"type": "number"},
            "group_a_n": {"type": "number"},
            "group_b_wins": {"type": "number"},
            "group_b_n": {"type": "number"},
            "n_bootstrap": {"type": "integer", "description": "Bootstrap iterations, default 2000"}
        }, "required": ["group_a_wins", "group_a_n", "group_b_wins", "group_b_n"]}
    }},
    {"type": "function", "function": {
        "name": "register_hypotheses",
        "description": (
            "MANDATORY step 3 (after evaluate + optional rollback). "
            "Pre-register 3-5 specific, directional, falsifiable hypotheses BEFORE "
            "looking at any data. Each must name a signal, a direction, and a magnitude. "
            "Example: 'confirmed_2d picks will show >10pp higher T+3 win rate than those without.' "
            "Locks them in the DB so you cannot change them post-hoc. "
            "After data queries, explicitly report each as CONFIRMED/REJECTED/INCONCLUSIVE."
        ),
        "parameters": {"type": "object", "properties": {
            "hypotheses": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of 3-5 specific falsifiable hypothesis strings."
            }
        }, "required": ["hypotheses"]}
    }},
    {"type": "function", "function": {
        "name": "multivariate_regression",
        "description": (
            "Logistic regression controlling for multiple confounders simultaneously. "
            "Use this INSTEAD OF or IN ADDITION TO analyze_signal_correlation when you want "
            "a signal's TRUE effect controlling for regime, day-of-week, and other signals at once. "
            "Much more reliable than one-at-a-time correlation — removes false positives caused "
            "by bull markets or calendar effects masking the real driver."
        ),
        "parameters": {"type": "object", "properties": {
            "signal_cols": {
                "type": "array",
                "items": {"type": "string",
                          "enum": ["confirmed_2d","high_conviction","buy_stock","vol_oi_bucket","rank"]},
                "description": "Signals to estimate controlled effects for."
            },
            "control_cols": {
                "type": "array",
                "items": {"type": "string",
                          "enum": ["regime","day_of_week","week_of_month"]},
                "description": "Confounders to hold constant. Default: [regime, day_of_week]."
            },
            "days_back": {"type": "integer",
                          "description": "Days of settled picks to use (default 60, max 90)."}
        }, "required": []}
    }},
    {"type": "function", "function": {
        "name": "search_past_findings",
        "description": (
            "Semantic search over all past weekly findings using embedding similarity. "
            "Call this BEFORE labeling any result a 'new finding'. "
            "If similarity >= 0.85 and the finding is < 4 weeks old, label it CONFIRMED (recurring) "
            "— do NOT count it as fresh evidence for a weight increase. "
            "This prevents the agent from inflating confidence by rediscovering the same pattern."
        ),
        "parameters": {"type": "object", "properties": {
            "query_text": {
                "type": "string",
                "description": "The finding you want to check for prior occurrence. Be specific: include signal name, direction, and magnitude."
            },
            "weeks_back": {
                "type": "integer",
                "description": "How many weeks of history to search (default 16)."
            }
        }, "required": ["query_text"]}
    }},
    {"type": "function", "function": {
        "name": "list_signal_dimensions",
        "description": (
            "List every queryable signal dimension with its statistical distribution. "
            "Call this FIRST at the start of any signal discovery session. "
            "It tells you exactly which fields exist, their ranges, baseline win rates by "
            "day/session, and ready-to-use example condition strings. "
            "This prevents you from hallucinating field names that don't exist."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []}
    }},
    {"type": "function", "function": {
        "name": "test_new_signal",
        "description": (
            "Test any novel signal hypothesis against real historical data. "
            "Compose conditions freely from the vocabulary in list_signal_dimensions. "
            "Returns: sample size, win rate, p-value, 95% CI, edge vs baseline, verdict. "
            "Verdict levels: STATISTICALLY REAL (p<0.05, n>=15) → register it. "
            "PROMISING (p<0.10, n>=10) → tighten conditions and re-test. "
            "NOISE → vary thresholds or combine with another condition. "
            "Call this as many times as needed — each call is instant. "
            "Best practice: test a hypothesis, then test its INVERSE to validate. "
            "Also use segment_by to see if a signal works better on certain days/sessions."
        ),
        "parameters": {"type": "object", "properties": {
            "conditions": {
                "type": "array", "items": {"type": "string"},
                "description": (
                    "List of condition strings. Examples: "
                    "['has_sweep = true', 'sweep_vol_oi > 5'], "
                    "['call_put_ratio > 2.0', 'session = market-open'], "
                    "['days_out < 21', 'has_sweep = true', 'sweep_premium_m > 0.5'], "
                    "['day_of_week = Tuesday', 'has_sweep = true']. "
                    "Mix and match freely — the parser handles all combinations."
                )
            },
            "target": {
                "type": "string",
                "enum": ["t3_win", "t5_win", "t3_pct", "t5_pct"],
                "description": "What to predict. t3_win (default) = 3-day win/loss; t3_pct = actual 3-day return."
            },
            "lookback_days": {
                "type": "integer",
                "description": "How many days of history to test against (default 90, max 180)."
            },
            "segment_by": {
                "type": "string",
                "enum": ["day_of_week", "session", "has_sweep", "cap_tier"],
                "description": "Break down results by this dimension to find conditional patterns."
            },
            "compare_to": {
                "type": "array", "items": {"type": "string"},
                "description": "A second set of conditions for A/B comparison. e.g. ['has_sweep = false'] to contrast."
            }
        }, "required": ["conditions"]}
    }},
    {"type": "function", "function": {
        "name": "analyze_missed_movers",
        "description": (
            "Find stocks that made big moves but were NOT caught by our signal system. "
            "Analyzes what those missed stocks had in common — reveals signal gaps. "
            "Automatically generates candidate hypotheses to test with test_new_signal. "
            "This is the self-correction loop: review what you missed, then go test "
            "whether a new signal combination would have caught it. "
            "Call once per session after reviewing performance."
        ),
        "parameters": {"type": "object", "properties": {
            "min_move_pct": {
                "type": "number",
                "description": "Minimum 1-day move to count as a 'missed mover' (default 5.0%)."
            },
            "lookback_days": {
                "type": "integer",
                "description": "Days to look back for missed movers (default 30)."
            }
        }, "required": []}
    }},
    {"type": "function", "function": {
        "name": "query_own_prediction_performance",
        "description": (
            "Review Loop B's forward-looking prediction track record. "
            "Shows win rate, avg T+3/T+5 return, confidence calibration (does high confidence "
            "actually predict better outcomes?), performance by rank, and best/worst signal combos. "
            "Call this to understand how the morning agent is performing and what needs improvement. "
            "Use results to adjust morning scan weighting criteria."
        ),
        "parameters": {"type": "object", "properties": {
            "days_back": {"type": "integer",
                "description": "Days of prediction history to review (default 45)."}
        }, "required": []}
    }},
    {"type": "function", "function": {
        "name": "save_research_model",
        "description": (
            "Save final conclusions and scoring model to the database. "
            "Call LAST after all investigation is complete. "
            "The weights you save here directly influence tomorrow's picks."
        ),
        "parameters": {"type": "object", "properties": {
            "findings": {"type": "string",
                "description": "Full narrative: what patterns exist, what biases found, what changed vs last week, what regime conditions apply, recommended exit timing."},
            "scoring_adjustments": {"type": "object",
                "description": "All weights, thresholds, gates, and regime flags you recommend."},
            "confidence": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"]}
        }, "required": ["findings", "scoring_adjustments", "confidence"]}
    }},
    # ── Loop A/B Market Research Tools (20 tools for full 12K-stock universe) ──
    {"type": "function", "function": {
        "name": "mkt_explore_dimensions",
        "description": (
            "CALL FIRST in any market research session. Returns full statistical summary of the "
            "polygon_market_daily database: date range, stocks/day, factor distributions (gap_pct, "
            "rvol, close_strength, range_pct, volume), baseline next-day win rate, and prior "
            "discovery count. Answers 'what data exists?' before generating any hypotheses."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []}
    }},
    {"type": "function", "function": {
        "name": "mkt_test_signal",
        "description": (
            "Test any combination of market conditions against the FULL 12K-stock universe "
            "(polygon_market_daily). Returns signal n, win_rate, avg_return, edge vs broad "
            "market, and p-value. The core workhorse — call repeatedly with different conditions. "
            "Use baseline='tight' for the rigorous comparison vs similar-but-not-qualifying stocks."
        ),
        "parameters": {"type": "object", "properties": {
            "conditions": {"type": "object",
                "description": "Dict of {factor_min/max: value}. Allowed: gap_pct, rvol, close_strength, range_pct, close_price, volume, open_price, high_price, low_price, vwap."},
            "horizon": {"type": "string", "enum": ["next_day"]},
            "baseline": {"type": "string", "enum": ["broad", "tight"],
                "description": "broad=vs all stocks; tight=vs stocks just below each threshold."},
        }, "required": ["conditions"]}
    }},
    {"type": "function", "function": {
        "name": "mkt_test_inverse",
        "description": (
            "Test the INVERSE of conditions (all conditions ABSENT). A real signal must show: "
            "signal win_rate > market baseline > inverse win_rate. MANDATORY after any p<0.05 "
            "finding to confirm the signal is truly directional."
        ),
        "parameters": {"type": "object", "properties": {
            "conditions": {"type": "object"},
            "horizon": {"type": "string", "enum": ["next_day"]},
        }, "required": ["conditions"]}
    }},
    {"type": "function", "function": {
        "name": "mkt_find_thresholds",
        "description": (
            "Grid-search 20 threshold values for a single factor to find the optimal cut. "
            "Returns all thresholds ranked by edge_winrate with n and p_value. "
            "Use to optimize the threshold of any significant factor."
        ),
        "parameters": {"type": "object", "properties": {
            "factor": {"type": "string",
                "description": "Factor name: gap_pct, rvol, close_strength, range_pct, close_price, volume."},
            "direction": {"type": "string", "enum": ["min", "max"],
                "description": "min = factor >= threshold (looking for high values); max = factor <= threshold."},
            "n_steps": {"type": "integer", "description": "Number of threshold steps (default 20)."},
        }, "required": ["factor"]}
    }},
    {"type": "function", "function": {
        "name": "mkt_analyze_top_movers",
        "description": (
            "Find stocks that moved min_move_pct%+ the NEXT DAY and profile their PRIOR day "
            "characteristics. Shows factor lifts vs all stocks — e.g. 'movers had 3x higher rvol "
            "the day before'. Reveals the true leading indicators of large moves."
        ),
        "parameters": {"type": "object", "properties": {
            "min_move_pct": {"type": "number", "description": "Min next-day move to qualify (default 5.0%)."},
            "max_move_pct": {"type": "number", "description": "Max next-day move cap (default 50.0%)."},
        }, "required": []}
    }},
    {"type": "function", "function": {
        "name": "mkt_analyze_false_signals",
        "description": (
            "Among stocks meeting signal conditions, compare WINNERS vs LOSERS. "
            "Reveals what additional filter would cut false positives. "
            "Shows which factors are higher in winners than losers."
        ),
        "parameters": {"type": "object", "properties": {
            "conditions": {"type": "object"},
            "win_threshold": {"type": "number", "description": "Min % gain to count as winner (default 2.0)."},
        }, "required": ["conditions"]}
    }},
    {"type": "function", "function": {
        "name": "mkt_regime_filter",
        "description": (
            "Test the signal broken down by market regime (SPY performance that day). "
            "Bull: SPY gap_pct >= +0.5%; Bear: SPY <= -0.5%; Flat: between. "
            "If signal only works in bull regime, it should not be traded on down market days."
        ),
        "parameters": {"type": "object", "properties": {
            "conditions": {"type": "object"},
        }, "required": ["conditions"]}
    }},
    {"type": "function", "function": {
        "name": "mkt_validate_oos",
        "description": (
            "MANDATORY before saving any discovery. Splits dates into train (first 60%) and test "
            "(last 40%). Only call mkt_save_discovery if oos_validated=True AND p<0.05. "
            "A signal that fails OOS is overfit and must NOT be saved."
        ),
        "parameters": {"type": "object", "properties": {
            "conditions": {"type": "object"},
            "train_pct": {"type": "number", "description": "Fraction for training (default 0.6)."},
        }, "required": ["conditions"]}
    }},
    {"type": "function", "function": {
        "name": "mkt_generate_hypotheses",
        "description": (
            "Ask GPT-4o to invent N novel signal hypotheses from first principles, given the "
            "dataset summary and what you've already found. Returns condition dicts ready for "
            "mkt_test_signal. Call at the START of each research session for fresh ideas."
        ),
        "parameters": {"type": "object", "properties": {
            "context": {"type": "string", "description": "Summary of findings so far to inform new hypotheses."},
            "n_hypotheses": {"type": "integer", "description": "Number to generate (default 8)."},
        }, "required": []}
    }},
    {"type": "function", "function": {
        "name": "mkt_save_discovery",
        "description": (
            "Save a validated signal to aiem_signal_discoveries. "
            "ONLY call this AFTER mkt_validate_oos returns oos_validated=True. "
            "Returns discovery_id for use with mkt_build_composite."
        ),
        "parameters": {"type": "object", "properties": {
            "conditions": {"type": "object"},
            "hypothesis_text": {"type": "string"},
            "edge_broad": {"type": "number"},
            "edge_tight": {"type": "number"},
            "signal_n": {"type": "integer"},
            "p_value": {"type": "number"},
            "signal_win_rate": {"type": "number"},
            "baseline_win_rate": {"type": "number"},
            "signal_avg_ret": {"type": "number"},
            "oos_edge": {"type": "number"},
            "notes": {"type": "string"},
        }, "required": ["conditions", "hypothesis_text"]}
    }},
    {"type": "function", "function": {
        "name": "mkt_load_discoveries",
        "description": (
            "Load all previously validated discoveries. Call at the START of each research session "
            "to avoid re-discovering known signals and to build compound strategies."
        ),
        "parameters": {"type": "object", "properties": {
            "status": {"type": "string", "description": "Filter: validated, new, retired (default: validated)."},
            "min_edge_tight": {"type": "number"},
            "min_oos_edge": {"type": "number"},
        }, "required": []}
    }},
    {"type": "function", "function": {
        "name": "mkt_factor_correlations",
        "description": (
            "Compute Pearson correlation between each factor and next-day returns. "
            "Also computes factor-to-factor correlations. Best signals have high return "
            "correlation AND low inter-factor correlation (independent of each other)."
        ),
        "parameters": {"type": "object", "properties": {
            "sample": {"type": "integer", "description": "Rows to sample (default 100000)."},
        }, "required": []}
    }},
    {"type": "function", "function": {
        "name": "mkt_discover_interactions",
        "description": (
            "Build a 3x3 grid of (low/mid/high) x (low/mid/high) for two factors. "
            "Finds synergistic combinations that outperform either factor alone. "
            "Call after mkt_factor_correlations identifies top predictors."
        ),
        "parameters": {"type": "object", "properties": {
            "factor1": {"type": "string"},
            "factor2": {"type": "string"},
        }, "required": ["factor1", "factor2"]}
    }},
    {"type": "function", "function": {
        "name": "mkt_signal_drift",
        "description": (
            "Detect if a signal is decaying. Compares win rate in the last N trading days "
            "vs prior M days. Drift > 3pp means the signal is losing effectiveness. "
            "Run monthly on all saved discoveries."
        ),
        "parameters": {"type": "object", "properties": {
            "conditions": {"type": "object"},
            "recent_days": {"type": "integer", "description": "Most recent N days (default 30)."},
            "historical_days": {"type": "integer", "description": "Historical comparison window (default 90)."},
        }, "required": ["conditions"]}
    }},
    {"type": "function", "function": {
        "name": "mkt_volume_patterns",
        "description": (
            "Test classic volume-based patterns: accumulation (high close_strength + high rvol), "
            "distribution (low close_strength + high rvol), volume dry-up (rvol < 0.5), "
            "and gap+accumulation. Returns win_rate for each vs baseline."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []}
    }},
    {"type": "function", "function": {
        "name": "mkt_price_patterns",
        "description": (
            "Test price structure patterns: strong close (top 20% of day range), weak close "
            "(bottom 20%), big range day, tight range day, gap+strong close, gap+weak close, "
            "high price vs low price. Each may predict next-day returns differently."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []}
    }},
    {"type": "function", "function": {
        "name": "mkt_compute_momentum",
        "description": (
            "Compute multi-day momentum and test continuation vs mean-reversion. "
            "Shows whether stocks up in the prior N days tend to continue or reverse. "
            "Determines if trend-following or contrarian strategy applies."
        ),
        "parameters": {"type": "object", "properties": {
            "lookback_days": {"type": "integer", "description": "Days for momentum calc (default 5)."},
        }, "required": []}
    }},
    {"type": "function", "function": {
        "name": "mkt_invent_indicator",
        "description": (
            "Ask GPT-4o to invent a completely new composite indicator from first principles, "
            "define it as a SQL expression using available columns, then test it live. "
            "Each call produces a novel indicator. Use when standard approaches plateau."
        ),
        "parameters": {"type": "object", "properties": {
            "inspiration": {"type": "string",
                "description": "Context about what you've found so far to guide invention."},
        }, "required": []}
    }},
    {"type": "function", "function": {
        "name": "mkt_compare_signals",
        "description": (
            "Rigorous A vs B head-to-head comparison of two signal condition sets. "
            "Tests each separately + their intersection. Reveals synergy (A AND B > either alone) "
            "or which single signal dominates."
        ),
        "parameters": {"type": "object", "properties": {
            "conditions_a": {"type": "object"},
            "conditions_b": {"type": "object"},
        }, "required": ["conditions_a", "conditions_b"]}
    }},
    {"type": "function", "function": {
        "name": "mkt_build_composite",
        "description": (
            "Combine multiple validated discoveries into a composite signal. Tests each alone "
            "vs requiring ALL signals together. Determines optimal combination. "
            "Use at session end to build the final trading rule from all discoveries."
        ),
        "parameters": {"type": "object", "properties": {
            "discovery_ids": {"type": "array", "items": {"type": "integer"},
                "description": "List of discovery IDs from mkt_load_discoveries or mkt_save_discovery."},
        }, "required": ["discovery_ids"]}
    }}
]




================================================================================
# SECTION: _AIEM_AGENT_SYSTEM — 70-LAW RESEARCH DIRECTIVE BRAIN (main prompt)  (main.py lines 17224–18197)
================================================================================
_AIEM_AGENT_SYSTEM = """You are an autonomous quantitative research AI with access to a real trading database.

Your mission: analyze your own stock-picking performance, discover what makes picks win or lose, and build the most accurate scoring model possible — which directly improves tomorrow's picks.

TOOLS AVAILABLE (use in this order):
1.  evaluate_previous_model      — ALWAYS start here. Was last week's model good or bad?
2.  rollback_to_previous_model   — Call IMMEDIATELY if evaluate_previous_model returns MODEL HURT.
3.  register_hypotheses          — MANDATORY: commit to 3-5 hypotheses BEFORE any data queries.
4.  query_pick_outcomes          — Full T+3/T+7 history and overall win rate.
5.  query_missed_movers          — What big movers did we miss? Why?
6.  analyze_signal_correlation   — Which boolean signals predict winners? (univariate)
7.  multivariate_regression      — Controlled effect sizes holding regime+day_of_week constant.
8.  discover_numeric_patterns    — Optimal thresholds for day_ret, vol_oi, stock_price.
9.  compare_picks_vs_misses      — Systematic bias: what do we avoid that we shouldn't?
10. query_market_regime          — Do our signals work in bull vs bear vs chop?
11. query_cross_signal_overlap   — Does multi-scanner confirmation improve outcomes?
12. query_temporal_patterns      — Day of week, OpEx week, week-of-month effects.
13. query_rank_effectiveness     — Does rank #1 actually outperform rank #5?
14. query_exit_timing            — T+3 vs T+7: when should we exit per signal type?
15. search_past_findings         — Semantic search past reports before calling anything NEW.
16. run_statistical_significance — Bootstrap p-value test. MANDATORY before adding any weight.
17. test_scoring_hypothesis      — Backtest proposed weights before committing. Iterate >= 3x.
18. save_research_model          — Save final model. LAST call only.
19. query_own_prediction_performance — Review Loop B morning agent track record.
20. list_signal_dimensions       — List all queryable fields + distributions. Call FIRST in discovery.
21. test_new_signal              — Test any hypothesis against real data. Call repeatedly.

═══════════════════════════════════════════════════════════════
LOOP A (SUNDAY WEEKLY) + LOOP B (DAILY) — FULL MARKET RESEARCH
═══════════════════════════════════════════════════════════════
You also have access to 20 full-market research tools (mkt_*) operating on polygon_market_daily
(12,000+ stocks every trading day since April 2026). Use these in EVERY session.

MANDATORY WORKFLOW FOR MARKET RESEARCH (always follow this sequence):
1.  mkt_load_discoveries      — FIRST: load prior validated signals to avoid re-discovery
2.  mkt_explore_dimensions    — understand dataset size, factor distributions, baseline returns
3.  mkt_generate_hypotheses   — generate 8 fresh hypotheses from first principles
4.  mkt_factor_correlations   — find which factors most predict returns (once per session)
5.  mkt_test_signal           — test each hypothesis (n, win_rate, edge, p-value)
6.  mkt_test_inverse          — MANDATORY: confirm signal is directional after any p<0.05 find
7.  mkt_analyze_top_movers    — what did 5%+ movers look like the day before they moved?
8.  mkt_analyze_false_signals — find what separates winners from false positives
9.  mkt_volume_patterns       — accumulation/distribution/dry-up pattern win rates
10. mkt_price_patterns        — strong/weak close, range compression pattern win rates
11. mkt_compute_momentum      — multi-day momentum continuation vs mean-reversion
12. mkt_find_thresholds       — grid-search optimal threshold for each significant factor
13. mkt_discover_interactions — 3x3 grid of best two-factor combinations
14. mkt_regime_filter         — does signal only work in bull/bear/flat markets?
15. mkt_compare_signals       — A vs B head-to-head on competing hypotheses
16. mkt_invent_indicator      — invent a completely new indicator from first principles
17. mkt_validate_oos          — MANDATORY before saving: 60/40 train/test split
18. mkt_save_discovery        — save ONLY if oos_validated=True AND p<0.05
19. mkt_signal_drift          — check if any prior discovery is decaying
20. mkt_build_composite       — combine top discoveries into final weighted rule

STANDARDS: Never save without p<0.05 AND oos_validated=True. Always test inverse.

╔══════════════════════════════════════════════════════════════════════════╗
║          STANDING RESEARCH DIRECTIVES  —  40 LAWS OF THE BRAIN          ║
║       Follow ALL of these every session. No exceptions. No skipping.     ║
╚══════════════════════════════════════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 CATEGORY A: STATISTICAL RIGOR  (Laws 1–7)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

LAW 1 — DECAY AUDIT BEFORE ANYTHING ELSE:
The very first action of every session is mkt_signal_drift on ALL entries from
mkt_load_discoveries. Signals with edge_drift > 3pp are DECAYING. Document which are
holding vs dying. Do not touch new research until this audit is complete. A decaying
signal you keep deploying costs real money.

LAW 2 — EFFECT SIZE OVER P-VALUE:
Never report significance without reporting the actual win rate DIFFERENCE and its 95%
confidence interval. A p=0.001 with 1.5pp edge on 12K observations means nothing
tradeable. A p=0.04 with 8pp edge on 220 stock-days is real money. Magnitude matters
more than the p-value. Always compute: signal_win_rate - base_win_rate = EDGE.

LAW 3 — BONFERRONI PENALTY FOR MASS TESTING:
If you test 6 or more hypotheses in one session, adjust your significance threshold
to p < (0.05 / number_of_tests). Testing 10 ideas? Your threshold is p < 0.005.
Without this correction you WILL find false signals by chance — and deploy garbage.

LAW 4 — ALWAYS TEST THE INVERSE:
Every signal that passes p < 0.05 must be IMMEDIATELY tested with the exact inverse
conditions. If the inverse also has significant edge, the signal is BIDIRECTIONAL —
flag it for both long entry and short/avoidance. If the inverse has no edge, the
signal is one-directional. Skipping the inverse means you leave money on the table.

LAW 5 — CAUSAL VALIDATION REQUIRED:
Every saved signal requires BOTH univariate AND controlled multivariate validation.
If only the univariate is significant but the controlled regression is not, the signal
is a REGIME PROXY masquerading as alpha — it will fail in live trading. Exclude it.
Only signals that survive BOTH tests get saved.

LAW 6 — MINIMUM SAMPLE = 200, PREFERRED = 500:
Hard floor: never save any discovery with signal_n < 200. Preferred minimum for
deployment confidence is 500+ stock-days. Small samples are statistical noise.
A signal with n=47 and 80% WR is worthless — it will regress to 50% in live trading.

LAW 7 — CONFIDENCE INTERVALS ON EVERY WIN RATE:
Never report a win rate without computing its 95% CI bounds using Wilson interval:
  CI = win_rate ± 1.96 * sqrt(win_rate*(1-win_rate)/n)
If the LOWER BOUND of the CI is below 55%, the signal is not strong enough to deploy.
The lower bound is the realistic live-trading expectation, not the point estimate.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 CATEGORY B: PRICE, SIZE & MARKET SEGMENTATION  (Laws 8–12)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

LAW 8 — PRICE BUCKET DECOMPOSITION IS MANDATORY:
Every significant signal (p<0.10) must be retested in EXACTLY these three price buckets:
  - Penny:  close_price_max=5         (explosive but dangerous)
  - Low:    close_price_min=5, close_price_max=15   (best risk/reward zone)
  - Mid:    close_price_min=15, close_price_max=50  (institutional-grade setups)
A signal that only works in one bucket is still a real signal — apply it with the filter.
A signal tested only on all stocks combined is masking its true nature.

LAW 9 — MARKET CAP SEGMENTATION:
Test every validated signal separately in three cap tiers:
  - Nano:   under $300M market cap
  - Small:  $300M–$2B market cap
  - Mid:    $2B–$10B market cap
Nano signals fire faster and bigger but die faster. Mid-cap signals are more persistent.
A signal that only works in nano should NEVER be deployed on mid-cap names.

LAW 10 — LIQUIDITY GATE:
Every signal must be validated with a minimum volume floor of 500,000 shares/day.
Thin stocks (<500K volume) skew win rates dramatically — their "moves" are noise.
Always run one version with the liquidity filter and compare results. If edge disappears
when you require volume > 500K, the signal only works on untradeable micro-liquidity.

LAW 11 — BEAR MARKET ALPHA IS THE HOLY GRAIL:
Call mkt_regime_filter on EVERY validated signal. Any signal that shows HIGHER edge on
bear days (SPY down) than bull days must be flagged PRIORITY, saved with notes="bear_alpha",
and elevated to the top of the deployment queue. Bear-alpha signals are extremely rare,
work when subscribers need the system most, and immediately command premium positioning.

LAW 12 — VOLATILITY NORMALIZATION:
High-VIX environments compress risk-adjusted returns for all signals. When VIX > 25,
normalize expected returns: a 4% move in VIX=30 conditions equals ~2.5% in VIX=15.
Always tag discoveries with the average VIX level during the test period. Signals
discovered primarily in low-VIX environments may fail during market stress.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 CATEGORY C: TIME & SEASONALITY PATTERNS  (Laws 13–17)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

LAW 13 — DAY-OF-WEEK DECOMPOSITION:
Every validated signal must be broken down by day of week: Mon / Tue / Wed / Thu / Fri.
Many signals ONLY work midweek (Tue-Thu). Monday signals are dangerous (gap-from-weekend
noise). Friday signals fade early (position squaring). A signal that averages 60% WR may
be 72% on Tuesday and 45% on Friday — this is critical deployment information.

LAW 14 — MULTI-DAY HOLDING PERIODS ARE REQUIRED:
Never assume T+1 is the optimal exit. Test every signal at T+1, T+2, T+3, and T+5.
Some signals peak at T+2 then mean-revert by T+4. Others are slow-burn 5-day setups.
Always report the OPTIMAL holding period alongside the signal. The exit timing is half
the trade — getting the entry right but exiting too early or late still loses money.

LAW 15 — INTRADAY TIMING MATTERS:
Test whether the rvol in the data represents EARLY burst (9:30–10:00 AM) vs SUSTAINED
strength. Early burst + fade has a completely different expected return than volume that
builds throughout the day. If close_strength >= 0.75 and rvol >= 2x, that is sustained
buying pressure — worth more than an early gap that fades.

LAW 16 — EARNINGS SEASON REGIME:
Test every signal separately during earnings season (weeks 1–3 of each quarter) vs
non-earnings weeks. Many momentum signals FAIL during earnings because catalyst risk
dominates. Signals that work only outside earnings season must be restricted accordingly
in deployment. Never blindly deploy a signal into earnings season without this test.

LAW 17 — MONTHLY SEASONALITY CHECK:
Once per month: test whether signal edge varies by calendar month. January (new money
flows), May-June (summer slowdown), October (historical volatility spike) are different
regimes. A signal discovered in April data may not survive a September bear tape.
Flag any signal where edge varies by more than 8pp between months.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 CATEGORY D: SIGNAL QUALITY & INDEPENDENCE  (Laws 18–22)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

LAW 18 — CLOSE STRENGTH IS THE UNDEREXPLORED FACTOR:
close_strength (0 = closed at day's low, 1 = closed at day's high) is the most
underweighted predictor in the current system. Dedicate at LEAST 3 hypothesis slots per
session to close_strength combinations. Test it alone, combined with rvol, combined with
gap_pct, and combined with range_pct. This is highest-priority unexplored territory.

LAW 19 — HUNT FOR GAP-INDEPENDENT SIGNALS:
Gap + volume (S2) is already validated. The system does NOT need more gap variations.
Every session must test at least 2 hypotheses with gap_pct_max=0.5 — the flat-open
universe. What predicts a 5% move when a stock opens FLAT? Volume dry-up + tight range
the prior day? Accumulation over 3 days? These independent signals are the most valuable
because they diversify the portfolio away from gap-dependent risk concentration.

LAW 20 — FACTOR ORTHOGONALITY TEST:
Before saving any new signal, it must prove it adds information ABOVE existing signals.
Run a partial correlation test: does the new signal predict returns AFTER controlling for
close_strength, rvol, and gap_pct? If its controlled beta drops to zero, it is a
derivative of something already known. Adding it to the portfolio produces zero
diversification benefit. Discard it and keep hunting.

LAW 21 — SIGNAL CORRELATION AUDIT:
Never save two signals whose conditions produce >0.70 raw correlation in firing patterns.
If signal A fires on 200 stock-days and signal B fires on 185 of the same 200, they are
the same signal with a different name. Keep the one with higher win rate and lower CI
bound. Redundant signals bloat the model and create false confidence in results.

LAW 22 — ECONOMIC RATIONALE REQUIRED:
Every saved discovery must include a one-sentence economic rationale for WHY it should
predict returns. No rationale = the signal is likely a statistical artifact.
Example: "Stocks closing near their high on 2x+ volume indicate sustained institutional
buying that continues into the next session before exhaustion." If you cannot write a
plausible economic reason for the signal, do not save it.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 CATEGORY E: FAILURE MODE INTELLIGENCE  (Laws 23–27)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

LAW 23 — WORST-DAY AUTOPSY EVERY SESSION:
For every validated signal, identify the worst 10 performing stock-days. Find their common
pattern: same sector? same day of week? same VIX level? earnings week? This becomes an
EXCLUSION FILTER. A signal with 65% average WR that fails 90% of the time on Fridays in
biotech during earnings should be deployed with those exact exclusions. The downside is
what costs money — know it exactly.

LAW 24 — FALSE POSITIVE ARCHAEOLOGY:
Define a false positive as: signal fired AND stock dropped >5% on the next day. Find all
false positives for every saved signal. Extract their common attributes (sector, float,
news catalyst, VIX, day of week). Test those attributes as signal KILLERS. If adding an
exclusion condition removes 60% of false positives while keeping 85% of true positives,
that exclusion must be added to the signal definition. Do not skip this.

LAW 25 — MISSED MOVERS MANDATE:
Every session: find the top 20 stocks in the dataset that moved >8% and were NOT flagged
by ANY current signal. These are the misses that cost subscribers money. For each cluster
of misses, generate at least 2 new hypotheses. The goal is systematic coverage — the
system should eventually explain >70% of all large moves before they happen.

LAW 26 — FAILED HYPOTHESIS ARCHIVE:
Every rejected hypothesis (p >= 0.05) must be documented with: the exact conditions
tested, the result, and the reason for rejection. If the same concept is rejected 3
sessions in a row, permanently retire it with the label TESTED AND RETIRED. Do not waste
future sessions re-testing ideas that have already been proven not to work.

LAW 27 — LOOK-AHEAD BIAS AUDIT:
Before saving any signal, explicitly verify that ALL conditions use data available BEFORE
market open on the test day. No intraday highs, no closing prices from the same day, no
same-day volume used to predict same-day returns. Look-ahead contamination produces
signals with beautiful backtests that lose money every single live trade. It is the most
common way quants fool themselves.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 CATEGORY F: RISK & MONEY SCIENCE  (Laws 28–32)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

LAW 28 — EXPECTED VALUE IS THE ONLY METRIC THAT MATTERS:
Never recommend a signal based on win rate alone. Compute:
  EV = (avg_win_pct × win_rate) - (avg_loss_pct × (1 - win_rate))
If EV is negative, the signal loses money even with a 60% win rate (large losers dominate).
If EV < 0.5%, the signal is marginal — not worth the transaction cost and slippage.
Only signals with EV > 1.0% per trade deserve serious deployment consideration.

LAW 29 — TAIL RISK ANALYSIS:
For every validated signal, compute the 5th and 10th percentile outcome (worst-case
returns). If the 5th percentile is worse than -12%, the signal has catastrophic-loss
potential and requires a hard stop-loss filter before deployment. Many signals look great
in average-case but hide left-tail blow-up risk. The tail kills accounts, not the average.

LAW 30 — KELLY CRITERION SIZING:
For every saved discovery, compute the Kelly fraction:
  f* = (win_rate - (1 - win_rate) / avg_win_loss_ratio)
This is the theoretically optimal position size as a fraction of capital. Signals with
f* < 0.05 are too marginal to size meaningfully. Signals with f* > 0.25 are high-conviction
candidates for larger sizing in the scanner output. Always report f* alongside discoveries.

LAW 31 — DRAWDOWN CLUSTERING DETECTION:
Test whether the signal's worst outcomes cluster on the same calendar days. If the 10
worst stock-days for a signal all happened within 3 trading sessions of each other, the
signal has HIDDEN CORRELATED RISK — in a real portfolio it would produce a concentrated
drawdown, not the diversified losses implied by the average. Flag this prominently.

LAW 32 — SURVIVORSHIP BIAS WARNING:
The polygon_market_daily table contains only stocks that survived to today's date. Stocks
that went to zero, were delisted, or dropped 80%+ may be missing from the dataset. This
means every win rate in the database is slightly OVERSTATED. Always interpret results
conservatively. When in doubt, require higher win rate minimums for nano-cap signals
where survival bias is strongest.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 CATEGORY G: SIGNAL LIFECYCLE & DECAY  (Laws 33–36)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

LAW 33 — RECENCY VALIDATION FOR OLD SIGNALS:
Any signal in the discovery library older than 45 days must be re-validated on the MOST
RECENT 30 trading days only. Markets evolve. A signal discovered in February on February
data may be dead by June. The full-period win rate is historical comfort — the recent
period win rate is the truth. If the recent period shows <52% WR, retire the signal.

LAW 34 — DECAY RATE TRACKING:
For every signal in the library, compare win rate in the OLDEST third of the dataset vs
the NEWEST third. If the win rate is declining, compute the decay rate in pp per month.
A signal decaying at >2pp/month will be below 50% within a quarter. Flag it DECAYING
even if the total p-value is still significant — total significance masks the decay.

LAW 35 — REGIME CONDITIONALITY MATRIX:
Test every validated signal in all four market regime quadrants:
  Q1: Bull market + Low volatility (VIX < 15)   — the easy mode
  Q2: Bull market + High volatility (VIX > 25)  — fear in an uptrend
  Q3: Bear market + Low volatility (VIX < 15)   — slow grind down
  Q4: Bear market + High volatility (VIX > 25)  — crash conditions
A signal that only works in Q1 is not deployable in all conditions. Tag every signal
with which quadrants it is valid for and suppress it outside those conditions.

LAW 36 — HYPOTHESIS RECYCLING:
Hypotheses rejected with p between 0.05 and 0.15 are "near-miss" signals — not
significant yet but potentially real. Store them. Every time the dataset grows by 30+
additional trading days, re-test all near-miss hypotheses. Near-miss signals that become
significant with more data are often the best discoveries because they required patience.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 CATEGORY H: COMPOSITE & PORTFOLIO INTELLIGENCE  (Laws 37–40)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

LAW 37 — COMPOSITE IS THE MANDATORY SESSION OUTPUT:
Every session MUST end with mkt_build_composite combining all discoveries saved this session
plus the top 3 from mkt_load_discoveries by historical edge. The composite is the actual
deliverable — individual signals are building blocks, not deployable products. A composite
that tests 3 conditions simultaneously reduces false positives by an order of magnitude.
Never close a session without computing and reporting the composite.

LAW 38 — SIGNAL FIRING CAPACITY CHECK:
Before finalizing any discovery, estimate how many tickers would trigger this signal on an
average trading day in the current universe. Signals that fire on fewer than 5 tickers/day
cannot be reliably traded — too few opportunities to be statistically meaningful in live
use. Minimum viable firing rate = 5 tickers/day. Maximum useful rate = ~50/day (above
that, quality dilution sets in). Report firing rate alongside every saved signal.

LAW 39 — CROSS-SIGNAL SYNERGY SWEEP:
Once the discovery library reaches 8+ validated signals: run mkt_build_composite on ALL
combinations of 3 signals from the library (all C(n,3) subsets). Find which trio produces
the highest combined edge and lowest false positive rate. That trio becomes the CORE MODEL.
Individual signals that don't contribute to any high-performing trio should be reviewed
for retirement. The whole must be greater than the sum of its parts.

LAW 40 — MANDATORY INVENTION EVERY SESSION:
Every single session must include at least one call to mkt_invent_indicator using the
current session's discoveries as the inspiration parameter. The ability to invent indicators
no human has formally defined is the PRIMARY COMPETITIVE ADVANTAGE of this system over
every other scanner on the market. Human researchers are biased toward what they already
know. This system is not. Never end a session without attempting to invent something new.
The next gap+volume discovery — the one that drives a 10x improvement in edge — will come
from a session where the agent invented something unexpected. Do not skip this. Ever.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 CATEGORY I: CROSS-SECTIONAL & RANKING INTELLIGENCE  (Laws 41–46)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

LAW 41 — CROSS-SECTIONAL Z-SCORE RANKING IS SUPERIOR TO THRESHOLDS:
Threshold conditions (rvol > 2.0, gap_pct > 2.0) treat all passing stocks equally.
Real quant desks rank every stock in the universe by its z-score relative to that day's
full cross-section. A stock with rvol = 4.0 when the median is 1.2 is different from
rvol = 4.0 when the median is 3.5. Always compute and report the percentile rank of each
signal condition within the daily universe. Top 5th percentile signals are elite.
Top-10% in 3 factors simultaneously is rarer than the threshold conditions suggest.

LAW 42 — RELATIVE STRENGTH AGAINST SECTOR IS REQUIRED:
Never analyze a stock's return without normalizing it against its sector's return that day.
A stock up 3% when its sector is up 4% is UNDERPERFORMING — a false positive.
A stock up 3% when its sector is down 1% is showing 4pp of EXCESS STRENGTH — a real signal.
Always compute: stock_return - sector_return = excess_return. Signal testing should use
excess_return as the dependent variable, not raw return.

LAW 43 — FACTOR RANK MOMENTUM (SIGNAL-OF-SIGNALS):
Track which signals have been WORKING MOST in the last 10 trading days vs the last 30.
If close_strength has generated +6.2% average forward return this month but only +1.8%
last quarter, close_strength is in a hot regime and should be weighted higher NOW.
If rvol has been flat for 6 weeks, reduce its composite weight. Signal weights should
rotate with recency performance — not stay static.

LAW 44 — CONFLUENCE PERCENTILE SCORING:
Develop a confluence score for each stock-day = (rvol_pct_rank + gap_pct_rank +
close_strength_pct_rank) / 3, where each is the percentile within that day's universe.
Test whether stocks in the top 5% by confluence score outperform top 10% and top 20%.
The goal is to find the THRESHOLD OF ELITENESS — the percentile cutoff where edge
becomes large enough to trade confidently. Document this threshold every session.

LAW 45 — SECTOR LEADERSHIP CLASSIFICATION:
Within each sector, identify which stock moved FIRST on any given day vs which followed.
Leaders (first movers in a sector on a strong day) have more persistent momentum than
laggards (those playing catch-up 30 minutes later). Test whether early sector movers
outperform delayed movers by T+1, T+3, T+5. First-mover classification is a signal
no simple threshold test can capture.

LAW 46 — PEER GROUP RELATIVE VALUE:
For every validated signal, test it filtered to stocks that are OUTPERFORMING their
closest market-cap peer group by at least 1.5pp on the signal day. A $500M biotech
up 3% when all other $300M-$700M biotechs are flat is a different animal than when
the whole group is up 3%. Relative strength within peer group is a natural filter
that eliminates false positives driven by sector-wide moves, not company-specific catalysts.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 CATEGORY J: WALK-FORWARD & OVERFITTING PREVENTION  (Laws 47–51)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

LAW 47 — WALK-FORWARD VALIDATION IS MANDATORY:
A signal that looks good on the FULL dataset may be overfit to its own history.
Every signal must pass a walk-forward test: train on the FIRST 60% of available dates,
validate on the LAST 40% (never seen during testing). If the validation win rate drops
more than 8pp from the training win rate, the signal is overfit and must be REJECTED
regardless of training set performance. Overfitting is the #1 killer of backtested signals.

LAW 48 — PARAMETER STABILITY TEST:
For every threshold in a signal (e.g., rvol > 2.0), test the performance at rvol > 1.5
and rvol > 2.5 as well. If the edge is only present at exactly 2.0 but disappears at 1.8
or 2.2, the threshold was optimized to the data (curve-fitting). Real signals show
STABLE PERFORMANCE across a range of threshold values, not a single knife-edge point.
Only signals with stable performance across ±30% threshold variation should be saved.

LAW 49 — INDEPENDENT VALIDATION SAMPLE:
Set aside the most recent 20 trading days as a HELD-OUT validation set that is NEVER
used during signal discovery or training. Every session, test newly discovered signals
on this held-out set before saving. A signal that fails on the 20 most recent days
is already dead, regardless of historical performance. Recency IS validity.

LAW 50 — MULTIPLE COMPARISON CORRECTION AT SESSION LEVEL:
Track the TOTAL number of hypothesis tests conducted across ALL sessions, not just
within one session. If 200 total tests have been run across 20 sessions, expect
10 false discoveries at p<0.05 by pure chance alone. Any signal discovered in sessions
where many tests were run must be held to a stricter standard (p<0.01) before deployment.
Maintain a running tally of total tests vs discoveries in the session narrative.

LAW 51 — DEVIL'S ADVOCATE IS REQUIRED BEFORE SAVING:
Before saving any discovery, explicitly write the strongest possible argument AGAINST it:
  - "This could be explained by survivorship bias because..."
  - "This could be a look-ahead artifact because..."
  - "This could be regime-specific because..."
  - "This could be statistically fragile because..."
If you cannot construct a strong counter-argument, you have not thought hard enough.
Only save the discovery after genuinely trying to destroy it with data.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 CATEGORY K: ADVANCED RISK & FACTOR DECOMPOSITION  (Laws 52–57)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

LAW 52 — FACTOR DECOMPOSITION: IS THIS JUST HIDDEN BETA?:
For every signal, test whether the edge disappears when you control for market beta.
A signal that fires on high-rvol stocks in a strong bull market may simply be capturing
general market momentum (beta), not stock-specific alpha. Run a regression of signal
returns against SPY returns on the same day. If R-squared > 0.40, the signal is mostly
market beta — NOT tradeable alpha. Real alpha has low correlation to SPY daily returns.

LAW 53 — INFORMATION RATIO TRACKING PER SIGNAL:
Win rate alone is insufficient. Compute for every signal:
  Information Ratio = avg_excess_return / std_dev_of_returns
IR > 0.5 is good. IR > 1.0 is excellent. IR < 0.3 is not deployable regardless of win rate.
A signal with 58% WR and 0.9 IR beats a signal with 65% WR and 0.2 IR every time in
real portfolio construction. Always report IR alongside win rate in every session.

LAW 54 — TRANSACTION COST ACCOUNTING IS MANDATORY:
Every edge calculation must subtract realistic transaction costs:
  - Stocks priced $1–$5: assume 0.50% round-trip cost (wide bid-ask)
  - Stocks priced $5–$15: assume 0.20% round-trip cost
  - Stocks priced $15+: assume 0.08% round-trip cost
A signal showing 1.2% average gain on $3 stocks has 0.7% NET edge after costs — barely
worth it. A signal showing 2.5% average gain on $20 stocks has 2.42% NET edge — real money.
Always report net-of-costs edge, not gross edge. Never deploy a signal with net edge < 0.5%.

LAW 55 — MARKET IMPACT CAPACITY MODEL:
This system has subscribers. If 500 subscribers all buy the same $3 stock on the same
signal at 9:35 AM, the first 50 get the edge — the other 450 create the price impact that
eliminates it. For every signal, estimate: (avg_daily_volume × 0.01) = deployable capacity.
A stock with 200K average volume can absorb ~$50K across all subscribers before self-
defeating. If a signal fires on stocks with capacity < $50K, flag it as CAPACITY CONSTRAINED.
Only signals with capacity > $200K per name should be broadly distributed to subscribers.

LAW 56 — MACRO REGIME OVERLAY:
Test every validated signal across three macro environments that span the dataset:
  - Fed hiking cycle (rates rising, tightening)
  - Fed cutting cycle (rates falling, easing)
  - Fed pause / neutral
Many momentum signals work differently in hiking vs cutting environments. Signals discovered
primarily during one Fed regime may fail when the regime changes. Always note which macro
regime dominates your dataset and flag signals that may be regime-specific.

LAW 57 — YIELD CURVE STATE CONDITIONING:
Test every signal with the yield curve state as a filter:
  - Normal (2Y < 10Y): healthy growth expectations
  - Inverted (2Y > 10Y): recession signal, risk-off environment
Small-cap momentum signals have historically underperformed during yield curve inversion.
A signal validated only during normal curve conditions should carry a regime caveat.
Note the curve state of your test period in every session narrative.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 CATEGORY L: MULTI-SOURCE SIGNAL CONFIRMATION  (Laws 58–62)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

LAW 58 — OPTIONS FLOW CONFIRMATION TIMING:
Options flow (call sweeps) detected BEFORE a price gap has much higher predictive value
than flow detected AFTER the gap begins. When testing signals that include options data,
always distinguish: was the unusual call volume on the day BEFORE the move, or on the
same day as the move? Pre-gap options flow = informed money. Same-day flow = reactive money.
Pre-gap signals should have their own separate and higher-confidence testing track.

LAW 59 — SHORT FLOAT DYNAMICS INTEGRATION:
For every signal tested, compute the average short_interest / float ratio of the firing
stocks. Signals that fire predominantly on high-short-float stocks (>15%) have an embedded
short squeeze component that can dramatically amplify moves. Test whether signals on
high-short stocks (>15% float shorted) outperform the same signal on low-short stocks
(<5% float shorted). If yes, short float is a required confirmation condition.

LAW 60 — FLOAT ROTATION VELOCITY:
Compute how many times the stock's float has traded in the last 5 trading days:
  float_rotation = sum(5-day volume) / float_shares
Stocks with float_rotation > 2.0 (the full float has traded twice in 5 days) are in
active institutional accumulation or distribution. Test whether float_rotation > 1.5
as an additional filter improves signal precision by reducing slow-moving large-float stocks.

LAW 61 — MULTI-DAY MOMENTUM SEQUENCE DETECTION:
Test patterns that span multiple consecutive days, not just single-day conditions:
  - 3 consecutive days closing in top 25% of range (close_strength > 0.75)
  - 3 consecutive days with rvol > 1.5x AND each day higher than prior day's close
  - Expanding volume over 3 days (each day's volume higher than prior)
These multi-day sequences represent sustained institutional interest, not one-day noise.
They require SQL self-joins on consecutive scan dates — the mkt_test_signal tool
supports conditions_2 and conditions_3 parameters for this purpose. Use them.

LAW 62 — CROSS-ASSET CONFIRMATION REQUIREMENT:
For any signal generating a bullish trade recommendation, test whether requiring same-day
SPY strength (SPY > 0%) as a filter improves precision. Some signals work in all
environments; others are reliable ONLY when the broad market is cooperating.
Separately: test whether sector ETF strength on the same day (sector up > 0.5%) improves
signal precision. Documenting the cross-asset dependency tells you exactly when to
suppress a signal in live trading.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 CATEGORY M: NON-LINEAR DISCOVERY & PATTERN SCIENCE  (Laws 63–67)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

LAW 63 — NON-LINEAR FACTOR COMBINATIONS:
Threshold conditions are linear. Real market structure is non-linear. Always test:
  - Squared terms: (rvol^2) — does extreme rvol have a non-linear payoff?
  - Log transforms: log(rvol) — is the relationship better captured in log-space?
  - Ratios: gap_pct / range_pct — did the stock open near its day range ceiling?
  - Products: rvol × close_strength — high rvol PLUS strong close is multiplicative
  - Differences: close_strength - prior_day_close_strength — was the close IMPROVING?
Non-linear combinations catch edges that no linear threshold test will ever find.
Use mkt_invent_indicator for these — pass specific mathematical combinations to test.

LAW 64 — PRIOR DAY CONSOLIDATION DETECTION:
A stock that gaps up cleanly from a TIGHT prior-day range is more significant than one
gapping from an already-extended base. Test: prior_day range_pct < 3.0% as a filter on
gap signals. "Inside day before gap" (prior range fully contained within 2 days prior)
is a classic institutional accumulation signature. This requires joining the dataset
on consecutive dates — test it with mkt_test_signal using multi-day conditions.

LAW 65 — STRUCTURAL BREAKOUT DETECTION:
A gap through a 52-week high is categorically different from a gap within a range.
Test every gap signal filtered to stocks where close_price is within 2% of their
52-week high vs stocks rallying well below their 52-week high. Breakouts to new highs
have historically sustained momentum for 5-20 trading days. Bounces within ranges
mean-revert faster. These require different trade management and different expected
holding periods. Document the difference explicitly.

LAW 66 — GAP FILL PROBABILITY BY SIZE:
Stocks that gap up 2-4% fill their gap (return to prior close) roughly 55-65% of
the time by end of day. Stocks that gap up 8%+ on 5x+ volume fill only ~15-25% of
the time. Test in the polygon_market_daily dataset: compute gap fill rate by gap size
bucket. Knowing gap fill probability tells subscribers whether to:
  (a) Buy the open and hold (low fill probability = gap is real)
  (b) Wait for pullback to VWAP (high fill probability = gap will partially fill)
This is actionable intelligence no simple signal test captures.

LAW 67 — MEAN REVERSION AFTER EXTREME MOVES:
Stocks that move >15% in a single day have a strong historical tendency to mean-revert
over the next 1-3 trading days. Test: stocks with range_pct > 15% — what is the
T+1, T+2, T+3 average return? If it's negative (mean reversion), this is a SHORT signal
or an EXIT signal for any longs. Understanding when momentum becomes overextension
prevents riding winning positions into a reversal. Test the EXACT threshold at which
momentum switches to mean reversion (likely somewhere between 10% and 20% single-day move).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 CATEGORY N: ATTRIBUTION & CONTINUOUS IMPROVEMENT  (Laws 68–70)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

LAW 68 — ATTRIBUTION ANALYSIS ON EVERY SESSION:
Before closing any session, perform attribution: for the picks that WORKED this week
(available in aiem_signal_discoveries outcomes), which specific laws or signals predicted
the success most accurately? For the picks that FAILED, which signals falsely flagged them?
Attribution turns outcomes into lessons. Without attribution, the system repeats the same
errors indefinitely. Every failure is a curriculum. Mine it every session.

LAW 69 — ADVERSARIAL SELF-TESTING:
Every session, the agent must deliberately try to BREAK its own best current signal.
Find conditions where the top-ranked discovery fails: specific sectors, specific market
regimes, specific price zones, specific calendar windows. A signal that cannot be broken
is robust. A signal that breaks easily needs either a regime filter or retirement.
The goal is NOT to protect the signal — the goal is to find its EXACT boundaries so
subscribers are never caught in conditions where it reliably fails.

LAW 70 — COMPOUNDING DISCOVERY ARCHITECTURE:
Every session's discoveries must be explicitly connected to prior sessions.
Ask and answer: "What does today's discovery ADD to what was found previously?"
"Does this confirm, extend, or contradict prior session findings?"
"If confirmed: can I raise the confidence level on the prior finding?"
"If contradicted: which dataset period explains the discrepancy?"
The discoveries are not isolated findings — they are building blocks of a cumulative
intelligence that gets smarter every week. Every session must advance the state of
knowledge, not just repeat tests already done. Reference prior sessions explicitly.
The system compounds its intelligence like interest — each session builds on the last.

╔══════════════════════════════════════════════════════════════════════════════╗
║  FINAL MANDATE: You are not a report generator. You are not a data analyst.  ║
║  You are an autonomous quantitative research system competing against 100-   ║
║  person hedge fund teams. Every session must produce something that makes    ║
║  the next week's picks more accurate, more robust, and more profitable than  ║
║  the current week's. The bar is: institutional-grade statistical discipline  ║
║  + genuine novel discovery + zero tolerance for overfitting or data snooping ║
║  + relentless focus on the one question that matters: DOES THIS MAKE MONEY?  ║
╚══════════════════════════════════════════════════════════════════════════════╝

22. analyze_missed_movers        — Find what big moves you missed and why.

HARD RULES — violating these produces an invalid model:
1. ROLLBACK RULE: If evaluate_previous_model returns MODEL HURT, call rollback_to_previous_model
   BEFORE any other research. Build new weights on top of the restored baseline.

2. PRE-REGISTRATION RULE: Call register_hypotheses BEFORE any data tools (steps 4+).
   Write 3-5 specific, directional, falsifiable claims. After data queries, explicitly label
   each hypothesis CONFIRMED, REJECTED, or INCONCLUSIVE. Never add hypotheses post-hoc.
   Good example: "confirmed_2d picks will show >10pp higher T+3 win rate vs picks without it."
   Bad example: "signals probably help" (not falsifiable, no magnitude).

3. CAUSAL DISCIPLINE RULE: For any signal you plan to include in the model, run BOTH:
   a) analyze_signal_correlation (univariate — raw win rate diff)
   b) multivariate_regression (controlled — isolates true effect from regime/calendar noise)
   Only include a signal if BOTH show significance. If univariate is significant but controlled
   is not, that signal is a regime proxy, not a real edge — exclude it.

4. RAG RULE: Before writing any finding in your narrative, call search_past_findings.
   If similarity >= 0.85 and the finding was seen <= 4 weeks ago: label CONFIRMED (recurring).
   If similarity >= 0.78 and <= 8 weeks ago: label LIKELY RECURRING.
   Only label a finding NEW if similarity < 0.65 or no prior match exists.
   CONFIRMED findings do NOT justify raising a weight — they confirm the weight already set.

5. P-VALUE RULE: For EVERY weight in scoring_adjustments include {key}_p_value and {key}_n.
   Use the CONTROLLED p-value from multivariate_regression when available (more reliable).
   The save function strips weights with p >= 0.10 automatically.
   Example: {"confirmed_2d_bonus": 1.8, "confirmed_2d_bonus_p_value": 0.031, "confirmed_2d_bonus_n": 47}

6. BACKTEST RULE: Test >= 3 weight combinations with test_scoring_hypothesis before saving.
   Only commit the combination with the highest top-half win rate improvement.

7. REGIME RULE: Always call query_market_regime. Never apply bull-market weights in bear regime.

8. SAMPLE RULE: settled_picks < 20 → confidence=LOW, conservative defaults only.
   Do not claim significance with n < 15 per group.

SIGNAL DISCOVERY WORKFLOW (required every Sunday):
Step A: Call list_signal_dimensions to see what data is available.
Step B: Call analyze_missed_movers — read the generated_hypotheses it returns.
Step C: For each generated hypothesis AND 2-3 of your own ideas: call test_new_signal.
Step D: For any STATISTICALLY REAL finding, call test_new_signal again with the INVERSE
        conditions (to confirm the effect is real, not just randomness).
Step E: Register all p<0.05 findings with register_hypotheses before saving the model.

The goal is not just to analyze what happened — it is to INVENT new signals that will
make next week's picks more accurate. Think like a quant researcher: vary thresholds,
combine signals in unexpected ways, test the inverse of every finding.

REQUIRED findings content:
- Previous model verdict (HELPED / NEUTRAL / HURT) and by how many percentage points
- Loop B morning agent performance: win rate, avg T+3 return, confidence calibration
- Which signal combos (rvol+conviction+sweep) are working best for Loop B predictions
- Signal Discovery: at least 3 novel hypotheses tested with test_new_signal this session
- Any STATISTICALLY REAL new signals found (p<0.05, n>=15) and registered as hypotheses
- Missed movers analysis: what did we miss and what new signal could catch it next time
- Your 3-5 pre-registered hypotheses and their outcomes (CONFIRMED / REJECTED / INCONCLUSIVE)
- Signals tested via multivariate_regression and their controlled p-values
- Any finding labeled CONFIRMED (recurring) from search_past_findings — and what that means for weights
- Signals REMOVED and why (not significant / regime proxy / small sample)
- Current market regime and whether regime-conditional adjustments apply
- Exit timing recommendation (T+3 vs T+7)
- One open question to test next week

Your goal: maximize T+3 win rate for top-ranked picks. Rigorous statistical discipline beats
finding impressive-sounding patterns. A null result is a valid, honest, and valuable output."""



# ══════════════════════════════════════════════════════════════════════════════
# LOOP B: Daily forward-looking AI scan (9:05 AM ET, Mon-Fri)
# ══════════════════════════════════════════════════════════════════════════════
# The morning agent reads fresh signal data from three sources (Polygon RVOL,
# conviction stack, call sweeps), applies the learned scoring model from
# Sunday's research, and makes its own ranked 3-5 day breakout predictions.
# Predictions are saved to aiem_predictions and graded at 4:35 PM.
# Sunday's research agent reviews the track record weekly and improves the
# scoring weights — closing the self-learning loop.
# ══════════════════════════════════════════════════════════════════════════════

_AIEM_MORNING_TOOLS = [
    {"type": "function", "function": {
        "name": "scan_market_for_setups",
        "description": (
            "Pull today's fresh signals from Polygon RVOL, conviction stack, and call sweeps. "
            "Returns ranked candidates with composite scores. "
            "ALWAYS call this first — it also includes the learned model weights as context."
        ),
        "parameters": {"type": "object", "properties": {
            "min_rvol": {"type": "number", "description": "Min RVOL threshold (default 3.0)"},
            "max_price": {"type": "number", "description": "Max stock price filter (default 80)"}
        }, "required": []}
    }},
    {"type": "function", "function": {
        "name": "save_daily_predictions",
        "description": (
            "Save your ranked 3-5 day breakout predictions. "
            "Call LAST after reviewing candidates. "
            "Include 5-8 picks with your reasoning for each."
        ),
        "parameters": {"type": "object", "properties": {
            "predictions": {
                "type": "array",
                "description": "List of prediction objects.",
                "items": {
                    "type": "object",
                    "properties": {
                        "ticker": {"type": "string"},
                        "rank": {"type": "integer", "description": "1=highest conviction"},
                        "confidence_score": {"type": "number",
                            "description": "0-10 score. 8+ = very high conviction."},
                        "signal_basis": {"type": "string",
                            "description": "Which signals triggered: e.g. rvol+conviction+sweep"},
                        "reasoning": {"type": "string",
                            "description": "Why this ticker. Pattern, catalyst, setup type."},
                        "predicted_move": {"type": "string",
                            "description": "e.g. bullish breakout, targeting +8-15% in 3-5 days"}
                    }, "required": ["ticker","rank","confidence_score","signal_basis","reasoning"]
                }
            }
        }, "required": ["predictions"]}
    }},
]

_AIEM_MORNING_SYSTEM = """You are an autonomous AI market analyst making forward-looking trade predictions.

Your job: scan today's market signals, apply what you've learned from past outcomes, and identify the 5-8 stocks most likely to make a significant move (8-20%) over the next 3-5 trading days.

PROTOCOL:
1. Call scan_market_for_setups to get today's candidates and the learned model weights.
2. Study the candidates. Prioritize stocks confirmed by 2-3 signal sources (RVOL + conviction + sweep).
3. Apply the learned_model_context weights to adjust your ranking.
4. Select your top 5-8. Be decisive — this is a prediction task, not a research task.
5. Call save_daily_predictions with your final ranked list.

SELECTION CRITERIA (in order of importance):
1. Multi-source confirmation: stocks in all 3 sources (RVOL + conviction + sweep) are highest priority
2. confirmed_2d = True: stock already showed strength two consecutive days — momentum continuation
3. high_conviction = True: conviction engine scored this 8+
4. sweep_vol_oi > 5: unusually large call sweep relative to open interest — smart money signal
5. RVOL > 5 with green open: genuine volume expansion, not just gap-and-fade
6. Float < 20M: low float amplifies moves

AVOID:
- Stocks with price > $60 (harder to get big % moves)
- Stocks with only 1 source confirming (too weak)
- Stocks you've seen fail with the same setup recently (check learned_model_context)

Your confidence_score should reflect genuine conviction:
- 8-10: All 3 sources confirm, learned model says this setup works, regime is favorable
- 5-7: 2 sources confirm, decent setup, some uncertainty
- 1-4: Only 1 source, or setup type has mixed historical results

Be specific in your reasoning. Explain the pattern, not just the signals."""


def _run_aiem_morning_scan():
    """
    Daily forward-looking AI scan. Runs 9:05 AM ET Mon-Fri.
    Reads fresh market signals, applies learned weights, saves ranked predictions.
    """
    import threading as _amt
    import datetime as _amdt

    if not _intraday_scan_allowed():
        print("[aiem_morning] skipped — outside market hours")
        return

    def _morning_scan_thread():
        import json as _amj
        try:
            print("[aiem_morning] starting daily forward scan...")
            _oai = _OpenAI(
                base_url="https://ai-integrations.replit.com/openai",
                api_key=os.environ.get("AI_INTEGRATIONS_OPENAI_API_KEY","")
            )
            _morning_tool_map = {
                "scan_market_for_setups":   _aiem_tool_scan_market_for_setups,
                "save_daily_predictions":   _aiem_tool_save_daily_predictions,
            }
            messages = [
                {"role": "system", "content": _AIEM_MORNING_SYSTEM},
                {"role": "user", "content": (
                    "Today is {}. Scan the market and make your predictions for "
                    "the next 3-5 trading days. Start with scan_market_for_setups.".format(
                        _amdt.date.today().strftime("%A, %B %d %Y"))
                )}
            ]
            saved = False
            for _i in range(6):
                _resp = _oai.chat.completions.create(
                    model="gpt-4o",
                    messages=messages,
                    tools=_AIEM_MORNING_TOOLS,
                    tool_choice="auto",
                    temperature=0.4,
                    max_tokens=2000,
                )
                _msg = _resp.choices[0].message
                messages.append({
                    "role": "assistant",
                    "content": _msg.content,
                    "tool_calls": [tc.model_dump() for tc in (_msg.tool_calls or [])]
                })
                if not _msg.tool_calls:
                    break
                for tc in _msg.tool_calls:
                    fn_name = tc.function.name
                    try:
                        args = _amj.loads(tc.function.arguments or "{}")
                    except Exception:
                        args = {}
                    fn = _morning_tool_map.get(fn_name)
                    result = fn(**args) if fn else {"error": "Unknown tool"}
                    result_str = _amj.dumps(result, default=str)
                    if len(result_str) > 5000:
                        result_str = result_str[:5000] + "...}"
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result_str
                    })
                    if fn_name == "save_daily_predictions":
                        saved = True
                        print("[aiem_morning] predictions saved — loop complete")
                        break
                else:
                    continue
                break

            if not saved:
                print("[aiem_morning] agent didn't save predictions — no qualifying candidates today")
        except Exception as _e:
            print("[aiem_morning] error: {}".format(_e))

    _amt.Thread(target=_morning_scan_thread, daemon=True).start()


def _run_aiem_prediction_grader():
    """
    Grades Loop B predictions at T+1, T+3, T+5 using Tradier history.
    Runs 4:35 PM ET Mon-Fri. Updates aiem_prediction_outcomes table.
    """
    import datetime as _gdt, json as _gj
    try:
        today = _gdt.date.today()

        def _prev_trading_days(n):
            """Return the date n trading days before today."""
            d = today
            count = 0
            while count < n:
                d -= _gdt.timedelta(days=1)
                if d.weekday() < 5:
                    count += 1
            return d

        # Grade T+1 (yesterday's predictions), T+3 (3 days ago), T+5 (5 days ago)
        targets = [
            (_prev_trading_days(1), "t1_return"),
            (_prev_trading_days(3), "t3_return"),
            (_prev_trading_days(5), "t5_return"),
        ]

        with _psycopg2.connect(_DB_URL) as _c, _c.cursor() as _cu:
            _cu.execute("""
                CREATE TABLE IF NOT EXISTS aiem_prediction_outcomes (
                    id SERIAL PRIMARY KEY,
                    prediction_date DATE NOT NULL,
                    ticker VARCHAR(10) NOT NULL,
                    t1_return NUMERIC(8,4),
                    t3_return NUMERIC(8,4),
                    t5_return NUMERIC(8,4),
                    win_t3 BOOLEAN,
                    win_t5 BOOLEAN,
                    graded_at TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE(prediction_date, ticker)
                )
            """)
            _c.commit()

        graded_total = 0
        for pred_date, col in targets:
            # Get ungraded predictions for this date
            with _psycopg2.connect(_DB_URL) as _c, _c.cursor() as _cu:
                _cu.execute("""
                    SELECT p.ticker
                    FROM aiem_predictions p
                    LEFT JOIN aiem_prediction_outcomes o
                      ON o.prediction_date=p.prediction_date AND o.ticker=p.ticker
                    WHERE p.prediction_date=%s
                      AND (o.{col} IS NULL OR o.id IS NULL)
                """.format(col=col), (pred_date,))
                tickers = [r[0] for r in _cu.fetchall()]

            if not tickers:
                continue

            # Get entry prices (from prediction date) and today's close
            # Use Tradier history: fetch a window around pred_date → today
            from_date = pred_date.isoformat()
            to_date = today.isoformat()

            for ticker in tickers:
                try:
                    hist = _td_history(ticker, from_date, to_date)
                    if not hist or len(hist) < 2:
                        continue
                    # Entry = close on prediction_date (first bar on or after pred_date)
                    bars_by_date = {_gdt.date.fromisoformat(b["date"]): b
                                    for b in hist if "date" in b and "close" in b}
                    # Find entry: close on pred_date or next available
                    entry_price = None
                    for offset in range(5):
                        d = pred_date + _gdt.timedelta(days=offset)
                        if d in bars_by_date:
                            entry_price = float(bars_by_date[d]["close"])
                            break
                    if not entry_price:
                        continue

                    # Find exit: close on today or most recent available
                    exit_price = None
                    for offset in range(5):
                        d = today - _gdt.timedelta(days=offset)
                        if d in bars_by_date:
                            exit_price = float(bars_by_date[d]["close"])
                            break
                    if not exit_price:
                        continue

                    ret = (exit_price - entry_price) / entry_price

                    with _psycopg2.connect(_DB_URL) as _c, _c.cursor() as _cu:
                        _cu.execute("""
                            INSERT INTO aiem_prediction_outcomes
                                (prediction_date, ticker, {col}, win_t3, win_t5)
                            VALUES (%s, %s, %s, %s, %s)
                            ON CONFLICT (prediction_date, ticker) DO UPDATE
                                SET {col}=EXCLUDED.{col},
                                    win_t3=CASE WHEN EXCLUDED.{col} IS NOT NULL
                                               THEN EXCLUDED.{col} > 0 ELSE aiem_prediction_outcomes.win_t3 END,
                                    graded_at=NOW()
                        """.format(col=col), (pred_date, ticker, ret, ret > 0, ret > 0))
                        _c.commit()
                    graded_total += 1
                except Exception as _te:
                    print("[aiem_grader] {} error: {}".format(ticker, _te))

        print("[aiem_grader] graded {} outcomes".format(graded_total))
    except Exception as e:
        print("[aiem_grader] error: {}".format(e))



================================================================================
# SECTION: _run_aiem_research_agent() — Loop A autonomous GPT-4o research loop  (main.py lines 18198–18400)
================================================================================
def _run_aiem_research_agent(max_iterations=None):
    """
    Autonomous AI research agent — full enhanced version.
    - Adaptive iteration budget: scales with data quantity (more picks = more iterations)
    - All 14 tools registered in tool_map
    - Self-critique pass after primary research
    - Weekly research email to owner
    Runs as daemon thread. Called Sunday 8PM ET + POST /stock-api/admin/run-aiem-research.
    """
    import json as _aj, datetime as _ardt

    # ── Adaptive iteration budget ─────────────────────────────────────────────
    # Scale with how many settled picks exist — more data = agent can go deeper
    try:
        with _psycopg2.connect(_DB_URL) as _c, _c.cursor() as _cu:
            _cu.execute("SELECT COUNT(*) FROM ai_early_movers_log WHERE t3_win IS NOT NULL")
            _settled = _cu.fetchone()[0] or 0
    except Exception:
        _settled = 0

    if max_iterations is None:
        if _settled < 10:
            max_iterations = 8    # sparse data — stay conservative
        elif _settled < 30:
            max_iterations = 15   # moderate data
        elif _settled < 80:
            max_iterations = 20   # good data set
        else:
            max_iterations = 25   # rich data — go deep

    print(f"[aiem_research] settled_picks={_settled} → budget={max_iterations} iterations")

    # ── OpenAI client ─────────────────────────────────────────────────────────
    try:
        from openai import OpenAI as _OAIR
        _oai = _OAIR(
            base_url="https://ai-integrations.replit.com/openai",
            api_key=os.environ.get("AI_INTEGRATIONS_OPENAI_API_KEY", ""),
        )
    except Exception as _oe:
        print(f"[aiem_research] OpenAI init error: {_oe}")
        return {"error": str(_oe)}

    # ── Full tool map — all 14 tools ──────────────────────────────────────────
    _tool_map = {
        "query_pick_outcomes":          _aiem_tool_query_pick_outcomes,
        "query_missed_movers":          _aiem_tool_query_missed_movers,
        "analyze_signal_correlation":   _aiem_tool_analyze_signal_correlation,
        "compare_picks_vs_misses":      _aiem_tool_compare_picks_vs_misses,
        "discover_numeric_patterns":    _aiem_tool_discover_numeric_patterns,
        "test_scoring_hypothesis":      _aiem_tool_test_scoring_hypothesis,
        "query_market_regime":          _aiem_tool_query_market_regime,
        "query_cross_signal_overlap":   _aiem_tool_query_cross_signal_overlap,
        "evaluate_previous_model":      _aiem_tool_evaluate_previous_model,
        "rollback_to_previous_model":   _aiem_tool_rollback_to_previous_model,
        "query_temporal_patterns":      _aiem_tool_query_temporal_patterns,
        "query_rank_effectiveness":     _aiem_tool_query_rank_effectiveness,
        "query_exit_timing":            _aiem_tool_query_exit_timing,
        "run_statistical_significance": _aiem_tool_run_statistical_significance,
        "save_research_model":          _aiem_tool_save_research_model,
        # ── Three new upgrades ────────────────────────────────────────────────
        "register_hypotheses":          _aiem_tool_register_hypotheses,
        "multivariate_regression":      _aiem_tool_multivariate_regression,
        "search_past_findings":         _aiem_tool_search_past_findings,
        "query_own_prediction_performance": _aiem_tool_query_own_prediction_performance,
        "list_signal_dimensions":          _aiem_tool_list_signal_dimensions,
        "test_new_signal":                 _aiem_tool_test_new_signal,
        "analyze_missed_movers":           _aiem_tool_analyze_missed_movers,
        # ── Loop A/B Market Research Tools ──────────────────────────────────
        "mkt_explore_dimensions":    _mkt_tool_explore_dimensions,
        "mkt_test_signal":           _mkt_tool_test_signal,
        "mkt_test_inverse":          _mkt_tool_test_inverse,
        "mkt_find_thresholds":       _mkt_tool_find_thresholds,
        "mkt_analyze_top_movers":    _mkt_tool_analyze_top_movers,
        "mkt_analyze_false_signals": _mkt_tool_analyze_false_signals,
        "mkt_regime_filter":         _mkt_tool_regime_filter,
        "mkt_validate_oos":          _mkt_tool_validate_oos,
        "mkt_generate_hypotheses":   _mkt_tool_generate_hypotheses,
        "mkt_save_discovery":        _mkt_tool_save_discovery,
        "mkt_load_discoveries":      _mkt_tool_load_discoveries,
        "mkt_factor_correlations":   _mkt_tool_factor_correlations,
        "mkt_discover_interactions": _mkt_tool_discover_interactions,
        "mkt_signal_drift":          _mkt_tool_signal_drift,
        "mkt_volume_patterns":       _mkt_tool_volume_patterns,
        "mkt_price_patterns":        _mkt_tool_price_patterns,
        "mkt_compute_momentum":      _mkt_tool_compute_momentum,
        "mkt_invent_indicator":      _mkt_tool_invent_indicator,
        "mkt_compare_signals":       _mkt_tool_compare_signals,
        "mkt_build_composite":       _mkt_tool_build_composite,
    }

    # ── Phase 1: Primary research loop ───────────────────────────────────────
    messages = [
        {"role": "system", "content": _AIEM_AGENT_SYSTEM},
        {"role": "user", "content": (
            "Begin autonomous research. You have {} settled picks to analyze. "
            "Start with evaluate_previous_model, then query_pick_outcomes. "
            "Use all available tools to discover patterns. "
            "Run statistical_significance on any finding before including it. "
            "Test multiple weight combinations. Save your model when done."
        ).format(_settled)}
    ]

    tool_calls_made = 0
    model_saved = False
    save_result = None
    saved_findings = None
    saved_weights = None
    saved_confidence = None
    log_lines = [
        "[aiem_research] ═══ RESEARCH SESSION STARTED ═══",
        "[aiem_research] settled_picks={}, budget={} iterations".format(_settled, max_iterations),
    ]

    for iteration in range(max_iterations):
        try:
            resp = _oai.chat.completions.create(
                model="gpt-4o",
                messages=messages,
                tools=_AIEM_AGENT_TOOLS,
                tool_choice="auto",
                temperature=0.15,
                max_tokens=4096,
            )
        except Exception as _ce:
            log_lines.append("  iter {}: API error: {}".format(iteration, _ce))
            break

        msg = resp.choices[0].message
        messages.append({
            "role": "assistant",
            "content": msg.content,
            "tool_calls": [tc.model_dump() for tc in (msg.tool_calls or [])]
        })

        if not msg.tool_calls:
            log_lines.append("  iter {}: Agent finished reasoning (no tool call)".format(iteration))
            break

        for tc in msg.tool_calls:
            fn_name = tc.function.name
            try:
                fn_args = _aj.loads(tc.function.arguments or "{}")
            except Exception:
                fn_args = {}

            log_lines.append("  iter {}: → {}({})".format(
                iteration, fn_name, _aj.dumps(fn_args)[:100]))

            fn = _tool_map.get(fn_name)
            result = fn(**fn_args) if fn else {"error": "Unknown tool: {}".format(fn_name)}
            tool_calls_made += 1

            result_str = _aj.dumps(result, default=str)
            if len(result_str) > 6000:
                result_str = result_str[:6000] + '..."}'

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result_str,
            })

            if fn_name == "save_research_model":
                model_saved = True
                save_result = result
                saved_findings   = fn_args.get("findings", "")
                saved_weights    = fn_args.get("scoring_adjustments", {})
                saved_confidence = fn_args.get("confidence", "LOW")
                log_lines.append("  → PRIMARY MODEL SAVED (confidence={})".format(saved_confidence))

        if model_saved:
            break

    # ── Phase 2: Self-critique pass ───────────────────────────────────────────
    # Only run if we have enough data and a model was saved
    if model_saved and _settled >= 15:
        log_lines.append("[aiem_research] Starting self-critique pass...")
        critique_messages = [
            {"role": "system", "content": _AIEM_AGENT_SYSTEM},
            {"role": "user", "content": (
                "You just saved this research model:\n\n"
                "FINDINGS: {}\n\n"
                "WEIGHTS: {}\n\n"
                "CONFIDENCE: {}\n\n"
                "Now CRITIQUE it. What could be wrong? What did you NOT test? "
                "What confounds might explain the patterns (e.g. sample bias, survivorship bias, "
                "look-ahead bias)? What regime conditions could break this model? "
                "Then: if your critique reveals a significant flaw, call save_research_model again "
                "to update the findings with the caveat. If the model is solid, call save_research_model "
                "to append 'SELF-CRITIQUE PASSED' to the findings."
            ).format(saved_findings, _aj.dumps(saved_weights), saved_confidence)}
        ]
        try:
            for _ci in range(5):  # max 5 critique iterations
                _cr = _oai.chat.completions.create(
                    model="gpt-4o",
                    messages=critique_messages,
                    tools=_AIEM_AGENT_TOOLS,
                    tool_choice="auto",
                    temperature=0.3,
                    max_tokens=2048,
                )


================================================================================
# SECTION: SCHEDULER WIRING — Loop A (Sunday 8PM) + Loop B (daily 6PM) + morning agent  (main.py lines 2340–2415)
================================================================================
            print(f"[scheduler] aiem miss detection error: {e}")
    _scheduler.add_job(
        _run_aiem_miss_detection,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=50, timezone=_ET),
        id="aiem_miss_detection",
        replace_existing=True,
    )
    # Continuous Research Loop: 6 PM ET Mon-Fri — daily autonomous hypothesis sweep.
    # Tests 11 standard signal templates against today's new outcome data,
    # saves any significant findings (p<0.05) to DB for Sunday consolidation.
    # This is what makes the system smarter every day, not just on Sundays.
    def _run_continuous_research_job():
        try:
            import threading as _crj_thr
            _crj_thr.Thread(target=_run_aiem_continuous_research, daemon=True).start()
        except Exception as e:
            print(f"[scheduler] continuous research error: {e}")
    _scheduler.add_job(
        _run_continuous_research_job,
        CronTrigger(day_of_week="mon-fri", hour=18, minute=0, timezone=_ET),
        id="aiem_continuous_research",
        replace_existing=True,
    )
    # Loop B — morning forward-looking scan: 9:05 AM ET Mon-Fri
    # Reads fresh Polygon RVOL + conviction + sweep signals, applies learned weights,
    # saves ranked 3-5 day breakout predictions to aiem_predictions table.
    def _run_aiem_morning_job():
        try:
            import threading as _amj_thr
            _amj_thr.Thread(target=_run_aiem_morning_scan, daemon=True).start()
        except Exception as e:
            print(f"[scheduler] aiem morning scan error: {e}")
    _scheduler.add_job(
        _run_aiem_morning_job,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=7, timezone=_ET),
        id="aiem_morning_scan",
        replace_existing=True,
    )
    # Loop B — prediction grader: 4:35 PM ET Mon-Fri
    # Grades T+1 / T+3 / T+5 outcomes for Loop B predictions using Tradier history.
    def _run_aiem_grader_job():
        try:
            import threading as _agj_thr
            _agj_thr.Thread(target=_run_aiem_prediction_grader, daemon=True).start()
        except Exception as e:
            print(f"[scheduler] aiem grader error: {e}")
    _scheduler.add_job(
        _run_aiem_grader_job,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=35, timezone=_ET),
        id="aiem_prediction_grader",
        replace_existing=True,
    )
    # AI Research Agent: every Sunday 8 PM ET — autonomous self-learning loop.
    # Queries its own pick history, discovers signal correlations, builds a scoring model.
    # Results saved to aiem_research_insights → injected into Monday's pick prompt.
    def _run_aiem_research_job():
        try:
            import threading as _aiem_rt
            _aiem_rt.Thread(target=_run_aiem_research_agent, daemon=True).start()
            print("[scheduler] AI research agent started")
        except Exception as e:
            print(f"[scheduler] aiem research agent error: {e}")
    _scheduler.add_job(
        _run_aiem_research_job,
        CronTrigger(day_of_week="sun", hour=20, minute=0, timezone=_ET),
        id="aiem_research_agent",
        replace_existing=True,
    )
    # Position monitor: poll Gmail for TRADE: emails every 15 min (market hours)
    def _run_poll_trade_emails():
        if not _intraday_scan_allowed():
            return
        try:
            import threading as _thr_pt
            _thr_pt.Thread(target=_poll_trade_emails, daemon=True).start()
        except Exception as e:


================================================================================
# SECTION: _polygon_full_market_scan() — saves ALL 12K+ stocks + post-scan Loop B trigger  (main.py lines 30674–31055)
================================================================================
def _polygon_full_market_scan() -> list:
    """
    Scan all 11,000+ US stocks for unusual relative volume using Polygon grouped daily.
    Uses 5 API calls total. Returns top movers sorted by RVOL descending.
    Caches in app._cache['polygon_rvol'] and persists to DB.
    Lock prevents concurrent runs from doubling the Polygon request rate.
    """
    if not _POLYGON_RVOL_LOCK.acquire(blocking=False):
        app.logger.info("[polygon_rvol] scan already running — skipping concurrent call")
        cached = getattr(app, "_polygon_rvol_cache", {})
        return cached.get("movers", [])

    import time as _t2

    days = _polygon_recent_trading_days(5)
    if not days:
        _POLYGON_RVOL_LOCK.release()
        return []

    app.logger.info(f"[polygon_rvol] fetching up to {len(days)} candidate days: {days[:5]}...")
    daily_data = []
    for _day in days:
        _data = _polygon_grouped_daily(_day)
        _t2.sleep(13)  # Polygon Starter = 5 req/min → need ≥12s between calls
        if not _data:
            app.logger.info(f"[polygon_rvol] {_day}: 0 tickers (holiday/error) — skipping")
            continue
        app.logger.info(f"[polygon_rvol] {_day}: {len(_data)} tickers")
        daily_data.append((_day, _data))
        if len(daily_data) >= 5:
            break

    if not daily_data:
        _POLYGON_RVOL_LOCK.release()
        return []

    yesterday_day, yesterday_data = daily_data[0]
    prior_days = [d for _, d in daily_data[1:]]
    app.logger.info(f"[polygon_rvol] scanning {yesterday_day}: {len(yesterday_data)} tickers, {len(prior_days)} prior days")

    movers = []
    for _ticker, _r in yesterday_data.items():
        _price  = _r.get("c", 0) or 0
        _vol    = _r.get("v", 0) or 0
        _open   = _r.get("o", 0) or 0
        _high   = _r.get("h", 0) or 0
        _low    = _r.get("l", 0) or 0
        _vwap   = _r.get("vw", 0) or 0

        if not (1.0 <= _price <= 50.0 and _vol >= 150_000 and _open > 0):
            continue
        _gap = (_price - _open) / _open * 100
        if _gap < 3.0 or _price <= _open:
            continue

        _pvols = [_d.get(_ticker, {}).get("v", 0) or 0 for _d in prior_days]
        _pvols = [v for v in _pvols if v > 0]
        if len(_pvols) < 2:
            continue
        _avg = sum(_pvols) / len(_pvols)
        if _avg < 10_000:
            continue
        _rvol = _vol / _avg
        if _rvol < 5.0:
            continue

        _range = (_high - _low) if _high > _low else 1
        _close_str = (_price - _low) / _range

        movers.append({
            "ticker":         _ticker,
            "price":          round(_price, 2),
            "open":           round(_open, 2),
            "high":           round(_high, 2),
            "low":            round(_low, 2),
            "vwap":           round(_vwap, 2),
            "gap_pct":        round(_gap, 1),
            "volume":         int(_vol),
            "avg_volume":     int(_avg),
            "rvol":           round(_rvol, 1),
            "close_strength": round(_close_str, 2),
            "scan_date":      yesterday_day,
        })

    movers.sort(key=lambda x: x["rvol"], reverse=True)
    top = movers[:40]
    app.logger.info(f"[polygon_rvol] scan done: {len(top)} movers from {len(yesterday_data)} tickers")

    app._polygon_rvol_cache = {
        "movers":        top,
        "scan_date":     days[0],
        "total_scanned": len(yesterday_data),
    }

    try:
        import psycopg2 as _pg3
        with _pg3.connect(os.environ["DATABASE_URL"]) as _c3, _c3.cursor() as _cur3:
            _cur3.execute("""
                CREATE TABLE IF NOT EXISTS polygon_rvol_scan (
                    id             SERIAL PRIMARY KEY,
                    scan_date      DATE NOT NULL,
                    ticker         VARCHAR(10) NOT NULL,
                    price          FLOAT,
                    open_price     FLOAT,
                    high           FLOAT,
                    low            FLOAT,
                    vwap           FLOAT,
                    gap_pct        FLOAT,
                    volume         BIGINT,
                    avg_volume     BIGINT,
                    rvol           FLOAT,
                    close_strength FLOAT,
                    UNIQUE(scan_date, ticker)
                )
            """)
            for _m in top:
                _cur3.execute("""
                    INSERT INTO polygon_rvol_scan
                        (scan_date, ticker, price, open_price, high, low, vwap,
                         gap_pct, volume, avg_volume, rvol, close_strength)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (scan_date, ticker) DO UPDATE SET
                        price=EXCLUDED.price, rvol=EXCLUDED.rvol, volume=EXCLUDED.volume
                """, (_m["scan_date"], _m["ticker"], _m["price"], _m["open"],
                      _m["high"], _m["low"], _m["vwap"], _m["gap_pct"],
                      _m["volume"], _m["avg_volume"], _m["rvol"], _m["close_strength"]))
        app.logger.info(f"[polygon_rvol] saved {len(top)} rows to DB")
    except Exception as _e3:
        app.logger.error(f"[polygon_rvol] DB save error: {_e3}")


    # ── Save ALL stocks to polygon_market_daily (Loop A/B full-market research) ──
    try:
        import psycopg2 as _pg5
        _all_rows = []
        _prior_close_map = {}
        if len(daily_data) > 1:
            _, _prior_day = daily_data[1]
            _prior_close_map = {t: _d.get("c", 0) for t, _d in _prior_day.items() if _d.get("c")}
        for _ticker, _r in yesterday_data.items():
            _c   = _r.get("c") or 0
            _o   = _r.get("o") or 0
            _h   = _r.get("h") or 0
            _l   = _r.get("l") or 0
            _vw  = _r.get("vw") or 0
            _vol = _r.get("v") or 0
            if _c < 0.50 or _vol < 30000 or _c == 0:
                continue
            _cs2   = ((_c - _l) / (_h - _l)) if _h > _l else None
            _rng2  = ((_h - _l) / _l * 100) if _l > 0 else None
            _pc   = _prior_close_map.get(_ticker)
            _gap2  = ((_c - _pc) / _pc * 100) if _pc else None
            _pvols2 = [_d.get(_ticker, {}).get("v", 0) or 0 for _d in prior_days]
            _pvols2 = [v for v in _pvols2 if v > 0]
            _rvol2  = (_vol / (sum(_pvols2) / len(_pvols2))) if _pvols2 else None
            _all_rows.append((yesterday_day, _ticker, _c,
                              _o or None, _h or None, _l or None, _vw or None,
                              int(_vol), _pc, _gap2, _rvol2, _cs2, _rng2))
        if _all_rows:
            with _pg5.connect(os.environ["DATABASE_URL"]) as _c5, _c5.cursor() as _cur5:
                _cur5.executemany(
                    "INSERT INTO polygon_market_daily "
                    "(scan_date, ticker, close_price, open_price, high_price, low_price, "
                    "vwap, volume, prev_close, gap_pct, rvol, close_strength, range_pct) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (scan_date, ticker) DO UPDATE SET "
                    "close_price=EXCLUDED.close_price, gap_pct=EXCLUDED.gap_pct, "
                    "rvol=EXCLUDED.rvol, volume=EXCLUDED.volume, "
                    "close_strength=EXCLUDED.close_strength",
                    _all_rows)
            app.logger.info(f"[polygon_market_daily] saved {len(_all_rows)} stocks for {yesterday_day}")
    except Exception as _e5b:
        app.logger.error(f"[polygon_market_daily] save error: {_e5b}")

    _POLYGON_RVOL_LOCK.release()

    # ── Post-scan trigger: fire Loop B immediately on fresh data ──────────────
    import threading as _pst_thr
    def _post_scan_loop_b():
        import time as _pst_t
        _pst_t.sleep(60)  # 60s: ensure all DB writes are committed and visible
        app.logger.info("[post_scan] Loop B triggered by fresh Polygon data — running AIEM research")
        try:
            _run_aiem_continuous_research()
            app.logger.info("[post_scan] Loop B research session complete")
        except Exception as _pst_e:
            app.logger.error(f"[post_scan] Loop B error: {_pst_e}")
    _pst_thr.Thread(target=_post_scan_loop_b, daemon=True, name="loop-b-post-scan").start()
    app.logger.info("[post_scan] Loop B triggered in background (fires in 60s)")

    return top


def _get_polygon_rvol_data() -> dict:
    """Return cached polygon_rvol scan; fall back to DB if cache is cold."""
    _cached = getattr(app, "_polygon_rvol_cache", None)
    if _cached:
        return _cached
    try:
        import psycopg2 as _pg4
        with _pg4.connect(os.environ["DATABASE_URL"]) as _c4, _c4.cursor() as _cur4:
            _cur4.execute("""
                SELECT ticker, price, open_price, high, low, vwap, gap_pct,
                       volume, avg_volume, rvol, close_strength, scan_date::text
                FROM polygon_rvol_scan
                WHERE scan_date = (SELECT MAX(scan_date) FROM polygon_rvol_scan)
                ORDER BY rvol DESC LIMIT 40
            """)
            _cols = [_d[0] for _d in _cur4.description]
            _rows = [dict(zip(_cols, _row)) for _row in _cur4.fetchall()]
            if _rows:
                _sd = _rows[0]["scan_date"]
                _result = {"movers": _rows, "scan_date": _sd, "total_scanned": 11000}
                app._polygon_rvol_cache = _result
                return _result
    except Exception as _e4:
        app.logger.error(f"[polygon_rvol] DB fallback error: {_e4}")
    return {"movers": [], "scan_date": None, "total_scanned": 0}


def _send_polygon_rvol_email() -> None:
    """8:35 AM ET: Email owner the top full-market RVOL movers from yesterday."""
    from email_alerts import send_email_raw, smtp_configured
    if not smtp_configured():
        print("[polygon_rvol] SMTP not configured — skip")
        return
    try:
        _mv = _polygon_full_market_scan()
        if not _mv:
            print("[polygon_rvol] no movers found — skipping email")
            return

        _scan_date = _mv[0].get("scan_date", "")
        _n = len(_mv)
        _rows_html = ""
        for _i, _m in enumerate(_mv[:25], 1):
            _rc = "#ef4444" if _m["rvol"] >= 20 else "#f59e0b" if _m["rvol"] >= 10 else "#22c55e"
            _gc = "#22c55e" if _m["gap_pct"] >= 10 else "#86efac"
            _cb = int(_m.get("close_strength", 0.5) * 100)
            _rows_html += (
                f'<tr style="border-bottom:1px solid #1e293b;">'
                f'<td style="padding:8px 12px;font-weight:700;color:#f1f5f9;font-size:15px;">'
                f'{_i}. {_m["ticker"]}</td>'
                f'<td style="padding:8px 12px;color:#94a3b8;">${_m["price"]:.2f}</td>'
                f'<td style="padding:8px 12px;font-weight:700;color:{_gc};">+{_m["gap_pct"]:.1f}%</td>'
                f'<td style="padding:8px 12px;font-weight:800;color:{_rc};">{_m["rvol"]:.0f}x</td>'
                f'<td style="padding:8px 12px;color:#94a3b8;">{_m["volume"]:,}</td>'
                f'<td style="padding:8px 12px;">'
                f'<div style="background:#1e293b;border-radius:3px;height:8px;width:80px;">'
                f'<div style="background:#22c55e;border-radius:3px;height:8px;width:{_cb}%;"></div>'
                f'</div></td></tr>'
            )

        _html = f"""
<div style="background:#0f172a;padding:28px;font-family:Arial,sans-serif;max-width:640px;margin:0 auto;border-radius:12px;">
  <div style="text-align:center;margin-bottom:24px;">
    <div style="font-size:32px;margin-bottom:8px;">🔥</div>
    <h1 style="color:#f1f5f9;font-size:22px;margin:0;">Full Market RVOL Scanner</h1>
    <p style="color:#64748b;margin:6px 0 0;">Yesterday's top movers · {_scan_date}</p>
    <p style="color:#475569;font-size:12px;margin:4px 0 0;">Scanned 11,000+ stocks via Polygon · {_n} qualified movers</p>
  </div>
  <div style="background:#1e293b;border-radius:4px;padding:10px 14px;margin-bottom:20px;font-size:13px;color:#94a3b8;">
    📌 <strong style="color:#f1f5f9;">Gapped 3%+ on 5x+ normal volume.</strong>
    Watch for continuation at open. High close-strength (green bar) = closed near HOD = institutional accumulation.
  </div>
  <table style="width:100%;border-collapse:collapse;">
    <thead>
      <tr style="background:#1e293b;">
        <th style="padding:8px 12px;text-align:left;color:#64748b;font-size:12px;">TICKER</th>
        <th style="padding:8px 12px;text-align:left;color:#64748b;font-size:12px;">PRICE</th>
        <th style="padding:8px 12px;text-align:left;color:#64748b;font-size:12px;">DAY GAIN</th>
        <th style="padding:8px 12px;text-align:left;color:#64748b;font-size:12px;">RVOL</th>
        <th style="padding:8px 12px;text-align:left;color:#64748b;font-size:12px;">VOLUME</th>
        <th style="padding:8px 12px;text-align:left;color:#64748b;font-size:12px;">CLOSE STRENGTH</th>
      </tr>
    </thead>
    <tbody>{_rows_html}</tbody>
  </table>
  <div style="margin-top:16px;text-align:center;">
    <p style="color:#64748b;font-size:12px;">Close Strength = where price closed within the day range (100% = closed at HOD)</p>
  </div>
</div>"""

        _subj = f"🔥 Full Market RVOL — {_n} movers ({_scan_date}) · Polygon"
        _ok = send_email_raw(_OWNER_EMAIL, _subj, _html)
        print(f"[polygon_rvol] email sent={_ok} → {_n} movers for {_scan_date}")
    except Exception as _e5:
        print(f"[polygon_rvol] email error: {_e5}\n{traceback.format_exc()}")


@app.route("/stock-api/full-market-movers", methods=["GET"])
def full_market_movers_endpoint():
    """Return the latest full-market Polygon RVOL scan results."""
    try:
        data = _get_polygon_rvol_data()
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e), "movers": [], "scan_date": None, "total_scanned": 0}), 200




@app.route("/stock-api/gap-volume-signal", methods=["GET"])
def gap_volume_signal_endpoint():
    """
    Gap + Volume Confirmation Signal — validated by June 2026 Polygon backtest.
    Finds stocks that gapped up ≥1% on ≥2x normal volume on the most recent
    Polygon scan day.  Edge: +8.7pp vs all stocks OOS (Apr-May 2026, n=3,553,
    p=0.0000); +2.5pp vs tight baseline (other gappers, p=0.0023).
    Data source: polygon_rvol_scan (runs 8:35 AM ET daily via Polygon API).
    """
    try:
        import psycopg2 as _gvs_pg
        with _gvs_pg.connect(os.environ["DATABASE_URL"]) as _c, _c.cursor() as _cur:
            _cur.execute("""
                SELECT ticker, price, open_price, high, low, vwap,
                       gap_pct, volume, avg_volume, rvol, close_strength,
                       scan_date::text,
                       ROUND((gap_pct * 0.35 + rvol * 0.40 +
                              close_strength * 100 * 0.25)::numeric, 2) AS score
                FROM polygon_rvol_scan
                WHERE scan_date = (SELECT MAX(scan_date) FROM polygon_rvol_scan)
                  AND gap_pct  >= 1.0
                  AND rvol     >= 2.0
                  AND price    >= 2.0
                ORDER BY (gap_pct * 0.35 + rvol * 0.40 + close_strength * 100 * 0.25) DESC
                LIMIT 60
            """)
            cols = [d[0] for d in _cur.description]
            rows = [dict(zip(cols, row)) for row in _cur.fetchall()]
            scan_date = rows[0]["scan_date"] if rows else None

            # Range pct from high/low for display
            for r in rows:
                h, l = r.get("high") or 0, r.get("low") or 0
                r["range_pct"] = round((h - l) / l * 100, 1) if l > 0 else None
                r["score"] = float(r["score"]) if r["score"] else 0.0

        return jsonify({
            "signals": rows,
            "count": len(rows),
            "scan_date": scan_date,
            "total_scanned": 11000,
            "edge_note": (
                "OOS-validated (Apr-May 2026): +8.7pp vs all stocks, "
                "+2.5pp vs other gappers. 216K stock-day test."
            ),
            "stale": scan_date is None,
        })
    except Exception as _e:
        app.logger.error(f"[gap-volume-signal] {_e}")
        return jsonify({"signals": [], "count": 0, "scan_date": None,
                        "total_scanned": 0, "edge_note": "", "stale": True}), 200

@app.route("/stock-api/admin/run-aiem-research", methods=["POST"])
def admin_run_aiem_research():
    """
    Admin: trigger the AI research agent immediately.
    The agent autonomously queries its own pick history, runs signal correlation
    analysis, backtests scoring hypotheses, and saves its conclusions to
    aiem_research_insights — which flows into tomorrow's pick prompt.
    Runs in background thread; returns immediately.
    POST /stock-api/admin/run-aiem-research
    Headers: X-Admin-Token: <ADMIN_TOKEN>
    """
    _tok = request.headers.get("X-Admin-Token", "")
    if _tok != os.environ.get("ADMIN_TOKEN", ""):
        return jsonify({"error": "unauthorized"}), 403
    try:
        import threading as _aiem_adm_thr
        import datetime as _aiem_adm_dt
        _aiem_adm_thr.Thread(target=_run_aiem_research_agent, daemon=True).start()
        return jsonify({
            "status": "started",
            "message": "AI research agent is running autonomously. It will query its own data, discover patterns, and save a scoring model.",
            "check_results_at": "aiem_research_insights DB table or GET /stock-api/aiem-research-status",
            "started_at": _aiem_adm_dt.datetime.utcnow().isoformat(),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500




================================================================================
# SECTION: GET /stock-api/aiem/discoveries — view all saved signal discoveries  (main.py lines 31056–31130)
================================================================================
@app.route("/stock-api/aiem/discoveries", methods=["GET"])
def aiem_discoveries_endpoint():
    """Return all AIEM-validated signal discoveries from the full-market Loop A/B research."""
    import psycopg2, json as _dj
    try:
        status_filter = request.args.get("status", "validated")
        limit = min(int(request.args.get("limit", 50)), 200)
        with psycopg2.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, hypothesis_text, conditions_json, horizon, "
                "signal_n, signal_win_rate, signal_avg_ret, edge_broad, edge_tight, "
                "p_value, oos_edge, status, discovered_at::text, notes, invented_indicator "
                "FROM aiem_signal_discoveries "
                "WHERE (%s IS NULL OR status = %s) "
                "ORDER BY COALESCE(oos_edge, edge_tight, edge_broad) DESC NULLS LAST "
                "LIMIT %s",
                [status_filter, status_filter, limit])
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            cur.execute("SELECT COUNT(*) FROM aiem_signal_discoveries")
            total = cur.fetchone()[0]
        for r in rows:
            if isinstance(r.get("conditions_json"), str):
                try:
                    r["conditions_json"] = _dj.loads(r["conditions_json"])
                except Exception:
                    pass
            for k, v in list(r.items()):
                if hasattr(v, "isoformat"):
                    r[k] = str(v)
        return jsonify({"status": "ok", "total": total, "count": len(rows), "discoveries": rows})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e), "discoveries": []}), 200


@app.route("/stock-api/aiem-research-status", methods=["GET"])
def aiem_research_status():
    """Returns the latest AI research agent findings and scoring model."""
    try:
        import json as _arsj
        with _psycopg2.connect(_DB_URL) as _c, _c.cursor() as _cu:
            _cu.execute("""
                SELECT research_date, findings, scoring_adjustments,
                       confidence, tool_calls_made, created_at
                FROM aiem_research_insights
                ORDER BY research_date DESC
                LIMIT 5
            """)
            cols = [d[0] for d in _cu.description]
            rows = []
            for r in _cu.fetchall():
                d = dict(zip(cols, r))
                d["research_date"] = str(d["research_date"])
                d["created_at"] = d["created_at"].isoformat() if d.get("created_at") else None
                rows.append(d)
        return jsonify({
            "research_history": rows,
            "latest": rows[0] if rows else None,
            "context_injected_in_picks": _get_aiem_research_context()[:500] + "..." if _get_aiem_research_context() else "None yet",
            "next_scheduled_run": "Sunday 8:00 PM ET",
            "manual_trigger": "POST /stock-api/admin/run-aiem-research (requires X-Admin-Token)"
        })
    except Exception as e:
        return jsonify({"error": str(e), "research_history": []}), 500


@app.route("/stock-api/admin/run-polygon-rvol", methods=["POST"])
def admin_run_polygon_rvol():
    """Admin: trigger the full-market Polygon RVOL scan immediately."""
    _tok = request.headers.get("X-Admin-Token", "")
    if _tok != os.environ.get("ADMIN_TOKEN", ""):
        return jsonify({"error": "unauthorized"}), 403
    try:
        movers = _polygon_full_market_scan()
        return jsonify({"status": "ok", "movers_found": len(movers),

