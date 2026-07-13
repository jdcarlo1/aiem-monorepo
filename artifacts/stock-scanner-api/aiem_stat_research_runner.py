"""
aiem_stat_research_runner.py — Standalone AIEM statistical research engine.

Runs the EOD indicator grid battery every 2 hours, 24/7. Pure statistics:
scipy.stats.ttest_ind on forward returns. Zero OpenAI tokens. Zero Flask.

Tables written:
  aiem_grid_test_state      — EOD multi-day indicator signals
  aiem_intraday_grid_state  — Same-day premarket/first-candle signals

Run as:  python3 aiem_stat_research_runner.py
Restart: automatic (loops forever with sleep between batches)
"""

import os
import sys
import time
import json
import traceback
import datetime
import logging

import psycopg2
import numpy as np
from scipy import stats as sc

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s [stat_research] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("stat_research")

DB_URL = os.environ["DATABASE_URL"]

# ── Schema ──────────────────────────────────────────────────────────────────

_SCHEMA_EOD = """
CREATE TABLE IF NOT EXISTS aiem_grid_test_state (
    cell_key       TEXT PRIMARY KEY,
    description    TEXT,
    conditions     JSONB,
    horizon        VARCHAR(10),
    last_tested_at TIMESTAMP,
    last_n         INTEGER,
    last_p_value   FLOAT,
    last_win_rate  FLOAT
)
"""

_SCHEMA_INTRADAY = """
CREATE TABLE IF NOT EXISTS aiem_intraday_grid_state (
    cell_key        TEXT PRIMARY KEY,
    description     TEXT,
    conditions      JSONB,
    last_tested_at  TIMESTAMPTZ,
    last_n          INTEGER,
    last_p_value    NUMERIC(12,8),
    last_win_rate   NUMERIC(8,4),
    last_baseline   NUMERIC(8,4)
)
"""


def ensure_schema():
    with psycopg2.connect(DB_URL) as conn, conn.cursor() as cur:
        cur.execute(_SCHEMA_EOD)
        cur.execute(_SCHEMA_INTRADAY)
        conn.commit()
    log.info("schema ready")


# ── EOD grid hypothesis cells ────────────────────────────────────────────────

def eod_cells():
    """Return list of (key, description, sig_where, horizon_days) tuples.
    sig_where uses table aliases: t. = polygon_market_daily, ind. = polygon_indicators_daily.
    """
    horizons = [("next_day", 1), ("3d", 3), ("5d", 5), ("10d", 10)]

    singles = [
        ("ind.rsi_14 < 30",       "RSI oversold (<30)"),
        ("ind.rsi_14 > 70",       "RSI overbought (>70)"),
        ("ind.stoch_k < 20",      "Stoch %K oversold (<20)"),
        ("ind.stoch_k > 80",      "Stoch %K overbought (>80)"),
        ("ind.macd_hist > 0",     "MACD histogram positive"),
        ("ind.macd_hist < 0",     "MACD histogram negative"),
        ("ind.adx_14 > 25",       "ADX trending (>25)"),
        ("ind.adx_14 > 40",       "ADX strong trend (>40)"),
        ("ind.cmf_20 > 0.1",      "CMF inflow (>0.1)"),
        ("ind.cmf_20 < -0.1",     "CMF outflow (<-0.1)"),
        ("ind.mfi_14 < 20",       "MFI oversold (<20)"),
        ("ind.mfi_14 > 80",       "MFI overbought (>80)"),
        ("ind.cci_20 > 100",      "CCI overbought (>100)"),
        ("ind.cci_20 < -100",     "CCI oversold (<-100)"),
        ("ind.williams_r < -80",  "Williams %R oversold (<-80)"),
        ("ind.williams_r > -20",  "Williams %R overbought (>-20)"),
        ("ind.bb_pct < 0.1",      "Near lower Bollinger Band (<0.1)"),
        ("ind.bb_pct > 0.9",      "Near upper Bollinger Band (>0.9)"),
        ("ind.roc_12 > 10",       "12d ROC strong (>10%)"),
        ("ind.roc_12 < -10",      "12d ROC weak (<-10%)"),
        ("ind.momentum_10 > 0",   "10d momentum positive"),
        ("ind.atr_pct > 3",       "High ATR volatility (>3%)"),
        ("ind.pct_from_sma20 < -5",  "5%+ below SMA20"),
        ("ind.pct_from_sma20 > 5",   "5%+ above SMA20"),
        ("ind.pct_from_sma50 < -10", "10%+ below SMA50"),
        ("ind.pct_from_sma50 > 10",  "10%+ above SMA50"),
        ("ind.pct_from_52w_low < 10",  "Within 10% of 52w low"),
        ("ind.pct_from_52w_high > -10","Within 10% of 52w high"),
        # polygon_market_daily direct columns
        ("t.rvol > 2",            "RVOL > 2x"),
        ("t.rvol > 3",            "RVOL > 3x"),
        ("t.gap_pct > 2",         "Gap up >2%"),
        ("t.gap_pct > 5",         "Gap up >5%"),
        ("t.gap_pct < -2",        "Gap down >2%"),
        ("t.close_strength > 0.7","Closed near high (CS>0.7)"),
        ("t.close_strength < 0.3","Closed near low (CS<0.3)"),
        ("t.range_pct > 5",       "Wide range day (>5%)"),
    ]

    combos = [
        ("ind.rsi_14 < 30 AND ind.atr_pct > 3",           "RSI oversold + high ATR"),
        ("ind.macd_hist > 0 AND ind.adx_14 > 25",         "MACD bullish + ADX trending"),
        ("ind.macd_hist < 0 AND ind.adx_14 > 25",         "MACD bearish + ADX trending"),
        ("ind.bb_pct < 0.1 AND ind.rsi_14 < 30",          "Near lower BB + RSI oversold"),
        ("ind.cmf_20 > 0.1 AND ind.macd_hist > 0",        "CMF inflow + MACD bullish"),
        ("ind.roc_12 > 10 AND ind.rsi_14 < 70",           "ROC strong + RSI not overbought"),
        ("t.rvol > 2 AND t.gap_pct > 2",                  "RVOL>2 + Gap>2%"),
        ("t.rvol > 3 AND t.gap_pct > 3",                  "RVOL>3 + Gap>3%"),
        ("t.close_strength > 0.7 AND ind.adx_14 > 25",    "Closed near high + ADX trending"),
        ("ind.rsi_14 < 30 AND ind.stoch_k < 20 AND ind.williams_r < -80",
         "Triple oversold: RSI+Stoch+Williams"),
        ("ind.macd_hist > 0 AND ind.roc_12 > 10 AND ind.adx_14 > 25",
         "Momentum burst: MACD+ROC+ADX"),
    ]

    result = []
    for where, desc in singles:
        prefix = "ind_" if where.startswith("ind.") else "pmd_"
        field_part = where.replace("ind.", "").replace("t.", "")
        key_base = field_part.replace(" ", "_").replace(".", "_").replace("<", "lt").replace(">", "gt")
        for hlabel, hdays in horizons:
            result.append((f"{prefix}{key_base}|{hlabel}", desc, where, hdays))

    for where, desc in combos:
        key_base = desc.lower().replace(" ", "_").replace("+", "x")[:40]
        for hlabel, hdays in horizons:
            result.append((f"combo_{key_base}|{hlabel}", desc, where, hdays))

    return result


# ── Intraday grid hypothesis cells ───────────────────────────────────────────

def intraday_cells():
    """Cells that test premarket/first-candle conditions vs day_win (same-day).
    Data source: aiem_first_candle_data. Zero rows until first candle capture at 9:36 AM ET.
    """
    return [
        ("gap_ge2",   "Premarket gap ≥2%",    "premarket_gap_pct >= 2"),
        ("gap_ge5",   "Premarket gap ≥5%",    "premarket_gap_pct >= 5"),
        ("gap_ge10",  "Premarket gap ≥10%",   "premarket_gap_pct >= 10"),
        ("gap_lt0",   "Premarket gap neg",    "premarket_gap_pct < 0"),
        ("rvol_ge2",  "Premarket RVOL ≥2x",  "premarket_rvol >= 2"),
        ("rvol_ge3",  "Premarket RVOL ≥3x",  "premarket_rvol >= 3"),
        ("fc_up",     "First candle up",       "first_candle_direction = 'up'"),
        ("fc_down",   "First candle down",     "first_candle_direction = 'down'"),
        ("gap_held",  "Gap held at open",      "gap_held = TRUE"),
        ("gap_fade",  "Gap faded at open",     "gap_held = FALSE"),
        ("fc_rng_ge2","First candle rng ≥2%", "first_candle_range_pct >= 2"),
        ("pcs_ge07",  "Prior close strong ≥0.7", "prior_close_strength >= 0.7"),
        ("pcs_lt03",  "Prior close weak <0.3",   "prior_close_strength < 0.3"),
        ("gap5_rvol2","Gap≥5% + RVOL≥2x",
         "premarket_gap_pct >= 5 AND premarket_rvol >= 2"),
        ("gap5_fc_up","Gap≥5% + first candle up",
         "premarket_gap_pct >= 5 AND first_candle_direction = 'up'"),
        ("gap5_held", "Gap≥5% + gap held",
         "premarket_gap_pct >= 5 AND gap_held = TRUE"),
        ("rvol3_fc_up","RVOL≥3 + first candle up",
         "premarket_rvol >= 3 AND first_candle_direction = 'up'"),
        ("gap3_rv2_fc_up","Gap≥3%+RVOL≥2x+FC up",
         "premarket_gap_pct >= 3 AND premarket_rvol >= 2 AND first_candle_direction = 'up'"),
    ]


# ── Core statistical test ────────────────────────────────────────────────────

def run_two_group(conn, sig_where, base_where, horizon_days=1, limit=100_000):
    """Fetch forward returns for signal vs baseline, run Welch t-test.
    Returns dict with n, win_rate, avg_ret, p_value, significant — or None if no data.
    """
    h = max(1, int(horizon_days))
    needs_join = "ind." in (sig_where or "") or "ind." in (base_where or "")
    join_sql = (
        "LEFT JOIN polygon_indicators_daily ind "
        "ON ind.ticker = t.ticker AND ind.scan_date = t.scan_date"
        if needs_join else ""
    )

    def fetch(where):
        sql = f"""
            WITH fwd_all AS (
                SELECT ticker, scan_date, close_price,
                       LEAD(close_price, {h}) OVER (
                           PARTITION BY ticker ORDER BY scan_date
                       ) AS fwd_close
                FROM polygon_market_daily
                WHERE close_price > 0
            )
            SELECT ((fwd.fwd_close / NULLIF(fwd.close_price, 0)) - 1) * 100
            FROM fwd_all fwd
            JOIN polygon_market_daily t
              ON t.ticker = fwd.ticker AND t.scan_date = fwd.scan_date
            {join_sql}
            WHERE fwd.fwd_close IS NOT NULL
              {'AND ' + where if where else ''}
            LIMIT {limit}
        """
        with conn.cursor() as cur:
            cur.execute(sql)
            return [r[0] for r in cur.fetchall() if r[0] is not None]

    sig = fetch(sig_where)
    base = fetch(base_where)
    if len(sig) < 30 or len(base) < 30:
        return None

    sa, ba = np.array(sig), np.array(base)
    _, pval = sc.ttest_ind(sa, ba, equal_var=False)
    return {
        "n":          len(sa),
        "win_rate":   round(float(np.mean(sa > 0)) * 100, 2),
        "avg_ret":    round(float(np.mean(sa)), 4),
        "baseline_n": len(ba),
        "baseline_wr": round(float(np.mean(ba > 0)) * 100, 2),
        "p_value":    round(float(pval), 6),
        "significant": bool(pval < 0.05),
    }


def run_fisher(conn, sig_where, total_rows, limit=50_000):
    """2×2 Fisher's exact test for intraday battery (binary day_win outcome).
    Returns dict with n, win_rate, baseline_wr, p_value — or None.
    """
    from scipy.stats import fisher_exact

    sql = f"""
        SELECT
            SUM(CASE WHEN day_win THEN 1 ELSE 0 END),
            SUM(CASE WHEN NOT day_win THEN 1 ELSE 0 END)
        FROM aiem_first_candle_data
        WHERE day_win IS NOT NULL
          AND {sig_where}
        LIMIT {limit}
    """
    base_sql = """
        SELECT
            SUM(CASE WHEN day_win THEN 1 ELSE 0 END),
            SUM(CASE WHEN NOT day_win THEN 1 ELSE 0 END)
        FROM aiem_first_candle_data
        WHERE day_win IS NOT NULL
        LIMIT 50000
    """
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            r = cur.fetchone()
            wins_s = int(r[0] or 0)
            losses_s = int(r[1] or 0)

            cur.execute(base_sql)
            rb = cur.fetchone()
            wins_b = int(rb[0] or 0)
            losses_b = int(rb[1] or 0)
    except Exception:
        return None

    n_s = wins_s + losses_s
    n_b = wins_b + losses_b
    if n_s < 10 or n_b < 10:
        return None

    _, pval = fisher_exact([[wins_s, losses_s], [wins_b, losses_b]])
    return {
        "n":           n_s,
        "win_rate":    round(wins_s / n_s * 100, 2),
        "baseline_n":  n_b,
        "baseline_wr": round(wins_b / n_b * 100, 2),
        "p_value":     round(float(pval), 8),
        "significant": bool(pval < 0.05),
    }


# ── EOD battery run ──────────────────────────────────────────────────────────

def run_eod_battery(batch_size=20):
    """Run one pass of the EOD indicator grid battery. Skips recently tested cells."""
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(hours=20)
    cells = eod_cells()

    tested = 0
    findings = 0
    try:
        conn = psycopg2.connect(DB_URL)
        conn.autocommit = False

        cur = conn.cursor()
        cur.execute("SELECT cell_key, last_tested_at FROM aiem_grid_test_state")
        state = {r[0]: r[1] for r in cur.fetchall()}

        for key, desc, sig_where, hdays in cells:
            # Skip if tested recently
            last = state.get(key)
            if last and last > cutoff:
                continue

            result = run_two_group(conn, sig_where, None, horizon_days=hdays)
            if result is None:
                continue

            cur.execute("""
                INSERT INTO aiem_grid_test_state
                    (cell_key, description, conditions, horizon, last_tested_at,
                     last_n, last_p_value, last_win_rate)
                VALUES (%s, %s, %s, %s, NOW(), %s, %s, %s)
                ON CONFLICT (cell_key) DO UPDATE SET
                    last_tested_at = NOW(),
                    last_n         = EXCLUDED.last_n,
                    last_p_value   = EXCLUDED.last_p_value,
                    last_win_rate  = EXCLUDED.last_win_rate
            """, (
                key, desc,
                json.dumps({"where": sig_where}),
                str(hdays),
                result["n"],
                result["p_value"],
                result["win_rate"],
            ))
            conn.commit()
            tested += 1
            if result["significant"]:
                findings += 1
                log.info(
                    "EOD FINDING: %-60s  n=%d  WR=%.1f%%  p=%.4f  avg=%.2f%%",
                    desc[:60], result["n"], result["win_rate"],
                    result["p_value"], result["avg_ret"]
                )

            if tested >= batch_size:
                break

        conn.close()
    except Exception as exc:
        log.error("EOD battery error: %s", exc)
        traceback.print_exc()
        return {"status": "error", "error": str(exc)}

    log.info("EOD batch done: tested=%d  findings=%d", tested, findings)
    return {"status": "ok", "tested": tested, "findings": findings}


# ── Intraday battery run ─────────────────────────────────────────────────────

def run_intraday_battery():
    """Run the intraday (same-day) Fisher battery. Returns early if <10 settled rows."""
    try:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM aiem_first_candle_data WHERE day_win IS NOT NULL")
        total_rows = cur.fetchone()[0]
        if total_rows < 10:
            conn.close()
            return {"status": "no_data", "total_rows": total_rows}

        cells = intraday_cells()
        tested = 0
        findings = 0
        for key, desc, sig_where in cells:
            result = run_fisher(conn, sig_where, total_rows)
            if result is None:
                continue
            cur.execute("""
                INSERT INTO aiem_intraday_grid_state
                    (cell_key, description, conditions, last_tested_at,
                     last_n, last_p_value, last_win_rate, last_baseline)
                VALUES (%s, %s, %s, NOW(), %s, %s, %s, %s)
                ON CONFLICT (cell_key) DO UPDATE SET
                    last_tested_at = NOW(),
                    last_n         = EXCLUDED.last_n,
                    last_p_value   = EXCLUDED.last_p_value,
                    last_win_rate  = EXCLUDED.last_win_rate,
                    last_baseline  = EXCLUDED.last_baseline
            """, (
                key, desc,
                json.dumps({"where": sig_where}),
                result["n"],
                result["p_value"],
                result["win_rate"],
                result["baseline_wr"],
            ))
            conn.commit()
            tested += 1
            if result["significant"]:
                findings += 1
                log.info(
                    "INTRADAY FINDING: %-50s  n=%d  WR=%.1f%%  base=%.1f%%  p=%.6f",
                    desc[:50], result["n"], result["win_rate"],
                    result["baseline_wr"], result["p_value"]
                )

        conn.close()
        log.info("Intraday batch done: total_rows=%d  tested=%d  findings=%d",
                 total_rows, tested, findings)
        return {"status": "ok", "total_rows": total_rows, "tested": tested, "findings": findings}

    except Exception as exc:
        log.error("Intraday battery error: %s", exc)
        traceback.print_exc()
        return {"status": "error", "error": str(exc)}


# ── Main loop ────────────────────────────────────────────────────────────────

def main():
    log.info("AIEM statistical research runner starting — pure stats, zero OpenAI")
    ensure_schema()

    while True:
        try:
            log.info("=== EOD battery cycle start ===")
            eod_result = run_eod_battery(batch_size=50)

            log.info("=== Intraday battery cycle start ===")
            intra_result = run_intraday_battery()
            if intra_result.get("status") == "no_data":
                log.info("Intraday: no settled rows yet (first candle capture at 9:36 AM ET)")

            # Sleep 2 hours between full cycles on weekdays, 30 min on weekends
            now = datetime.datetime.now()
            sleep_s = 1800 if now.weekday() > 4 else 7200
            log.info("Cycle complete. Sleeping %.0f min until next cycle.", sleep_s / 60)
            time.sleep(sleep_s)

        except KeyboardInterrupt:
            log.info("Shutdown requested — exiting")
            sys.exit(0)
        except Exception as exc:
            log.error("Main loop error: %s — retrying in 5 min", exc)
            traceback.print_exc()
            time.sleep(300)


if __name__ == "__main__":
    main()
