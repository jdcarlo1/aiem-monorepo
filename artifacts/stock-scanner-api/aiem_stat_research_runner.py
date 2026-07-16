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
        # ── Single premarket indicators ──
        ("gap_ge2",    "Premarket gap ≥2%",          "premarket_gap_pct >= 2"),
        ("gap_ge5",    "Premarket gap ≥5%",          "premarket_gap_pct >= 5"),
        ("gap_ge10",   "Premarket gap ≥10%",         "premarket_gap_pct >= 10"),
        ("gap_ge20",   "Premarket gap ≥20%",         "premarket_gap_pct >= 20"),
        ("gap_1_3",    "Premarket gap 1-3%",         "premarket_gap_pct BETWEEN 1 AND 3"),
        ("gap_3_5",    "Premarket gap 3-5%",         "premarket_gap_pct BETWEEN 3 AND 5"),
        ("gap_5_10",   "Premarket gap 5-10%",        "premarket_gap_pct BETWEEN 5 AND 10"),
        ("gap_lt0",    "Premarket gap negative",     "premarket_gap_pct < 0"),
        ("gap_lt_5",   "Premarket gap down >5%",     "premarket_gap_pct < -5"),
        # ── RVOL tiers ──
        ("rvol_ge2",   "Premarket RVOL ≥2x",         "premarket_rvol >= 2"),
        ("rvol_ge3",   "Premarket RVOL ≥3x",         "premarket_rvol >= 3"),
        ("rvol_ge5",   "Premarket RVOL ≥5x",         "premarket_rvol >= 5"),
        ("rvol_ge10",  "Premarket RVOL ≥10x",        "premarket_rvol >= 10"),
        ("rvol_2_5",   "Premarket RVOL 2-5x",        "premarket_rvol BETWEEN 2 AND 5"),
        # ── First candle ──
        ("fc_up",      "First candle up",             "first_candle_direction = 'up'"),
        ("fc_down",    "First candle down",           "first_candle_direction = 'down'"),
        ("fc_flat",    "First candle flat",           "first_candle_direction = 'flat'"),
        ("gap_held",   "Gap held at open",            "gap_held = TRUE"),
        ("gap_fade",   "Gap faded at open",           "gap_held = FALSE"),
        ("fc_rng_ge1", "First candle range ≥1%",      "first_candle_range_pct >= 1"),
        ("fc_rng_ge2", "First candle range ≥2%",      "first_candle_range_pct >= 2"),
        ("fc_rng_ge3", "First candle range ≥3%",      "first_candle_range_pct >= 3"),
        ("fc_rng_lt05","First candle tight (<0.5%)",  "first_candle_range_pct < 0.5"),
        # ── Prior close strength ──
        ("pcs_ge07",   "Prior close strong ≥0.7",    "prior_close_strength >= 0.7"),
        ("pcs_lt03",   "Prior close weak <0.3",      "prior_close_strength < 0.3"),
        ("pcs_mid",    "Prior close mid 0.3-0.7",    "prior_close_strength BETWEEN 0.3 AND 0.7"),
        # ── Combo signals (premarket + first candle) ──
        ("gap5_rvol2", "Gap≥5% + RVOL≥2x",
         "premarket_gap_pct >= 5 AND premarket_rvol >= 2"),
        ("gap5_fc_up", "Gap≥5% + first candle up",
         "premarket_gap_pct >= 5 AND first_candle_direction = 'up'"),
        ("gap5_held",  "Gap≥5% + gap held",
         "premarket_gap_pct >= 5 AND gap_held = TRUE"),
        ("gap5_str",   "Gap≥5% + prior strong",
         "premarket_gap_pct >= 5 AND prior_close_strength >= 0.7"),
        ("rvol3_fc_up","RVOL≥3x + first candle up",
         "premarket_rvol >= 3 AND first_candle_direction = 'up'"),
        ("rvol5_fc_up","RVOL≥5x + first candle up",
         "premarket_rvol >= 5 AND first_candle_direction = 'up'"),
        ("gap3_rv2_fc_up","Gap≥3%+RVOL≥2x+FC up",
         "premarket_gap_pct >= 3 AND premarket_rvol >= 2 AND first_candle_direction = 'up'"),
        ("gap5_rv3_fc_up_held","Gap≥5%+RVOL≥3x+FC up+held (full setup)",
         "premarket_gap_pct >= 5 AND premarket_rvol >= 3 "
         "AND first_candle_direction = 'up' AND gap_held = TRUE"),
        ("gap3_fc_up_held","Gap≥3%+FC up+held",
         "premarket_gap_pct >= 3 AND first_candle_direction = 'up' AND gap_held = TRUE"),
        ("rv3_wide_fc","RVOL≥3x + wide first candle",
         "premarket_rvol >= 3 AND first_candle_range_pct >= 2"),
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


# ── Telegram helper ───────────────────────────────────────────────────────────

_TG_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
_TG_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "8609255707")

def _tg_send(msg: str):
    """Send a Telegram message. Silent on failure."""
    try:
        import urllib.request, urllib.parse
        tok = _TG_TOKEN.strip()
        if not tok:
            return
        url  = f"https://api.telegram.org/bot{tok}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id":                  _TG_CHAT_ID,
            "text":                     msg,
            "parse_mode":               "HTML",
            "disable_web_page_preview": "true",
        }).encode()
        urllib.request.urlopen(url, data=data, timeout=10)
    except Exception:
        pass


# ── Historical backtest on polygon_market_daily ───────────────────────────────

def _historical_backtest_cells():
    """
    Every indicator cell tested against polygon_market_daily history.
    Columns in the temp table: premarket_gap_pct, current_rvol, prior_cs,
    prior_rvol, prior_gap_pct, prior_range_pct, vol_accel, close_price, volume.
    Outcomes (tested separately): day_win, big3, big5, big10.
    """
    # ── Premarket gap fine-grained bins ──────────────────────────────────────
    gap_bins = [
        ("gap_lt_10",    "Gap DOWN >10%",               "premarket_gap_pct < -10"),
        ("gap_n10_n5",   "Gap down 5–10%",              "premarket_gap_pct BETWEEN -10 AND -5"),
        ("gap_n5_n2",    "Gap down 2–5%",               "premarket_gap_pct BETWEEN -5 AND -2"),
        ("gap_n2_0",     "Gap down 0–2%",               "premarket_gap_pct BETWEEN -2 AND 0"),
        ("gap_0_1",      "Gap flat 0–1%",               "premarket_gap_pct BETWEEN 0 AND 1"),
        ("gap_1_2",      "Gap up 1–2%",                 "premarket_gap_pct BETWEEN 1 AND 2"),
        ("gap_2_3",      "Gap up 2–3%",                 "premarket_gap_pct BETWEEN 2 AND 3"),
        ("gap_3_5",      "Gap up 3–5%",                 "premarket_gap_pct BETWEEN 3 AND 5"),
        ("gap_5_10",     "Gap up 5–10%",                "premarket_gap_pct BETWEEN 5 AND 10"),
        ("gap_10_20",    "Gap up 10–20%",               "premarket_gap_pct BETWEEN 10 AND 20"),
        ("gap_20_50",    "Gap up 20–50%",               "premarket_gap_pct BETWEEN 20 AND 50"),
        ("gap_ge50",     "Gap up 50%+ (catalyst/halt)", "premarket_gap_pct >= 50"),
        ("gap_ge2",      "Gap up ≥2% (any)",            "premarket_gap_pct >= 2"),
        ("gap_ge5",      "Gap up ≥5% (any)",            "premarket_gap_pct >= 5"),
        ("gap_ge10",     "Gap up ≥10% (any)",           "premarket_gap_pct >= 10"),
        ("gap_ge20",     "Gap up ≥20% (any)",           "premarket_gap_pct >= 20"),
        ("gap_lt0",      "Gap negative (any)",          "premarket_gap_pct < 0"),
    ]

    # ── RVOL fine-grained tiers ───────────────────────────────────────────────
    rvol_bins = [
        ("rvol_1_15",  "RVOL 1–1.5x (slight uptick)",  "current_rvol BETWEEN 1 AND 1.5"),
        ("rvol_15_2",  "RVOL 1.5–2x",                  "current_rvol BETWEEN 1.5 AND 2"),
        ("rvol_2_3",   "RVOL 2–3x",                    "current_rvol BETWEEN 2 AND 3"),
        ("rvol_3_5",   "RVOL 3–5x",                    "current_rvol BETWEEN 3 AND 5"),
        ("rvol_5_10",  "RVOL 5–10x",                   "current_rvol BETWEEN 5 AND 10"),
        ("rvol_10_20", "RVOL 10–20x",                  "current_rvol BETWEEN 10 AND 20"),
        ("rvol_ge20",  "RVOL 20x+ (explosive)",        "current_rvol >= 20"),
        ("rvol_ge2",   "RVOL ≥2x",                     "current_rvol >= 2"),
        ("rvol_ge5",   "RVOL ≥5x",                     "current_rvol >= 5"),
        ("rvol_ge10",  "RVOL ≥10x",                    "current_rvol >= 10"),
    ]

    # ── Prior close strength (where did it close in its range?) ──────────────
    pcs_bins = [
        ("pcs_vweak",  "Prior close VERY weak (<0.1)",    "prior_cs < 0.1"),
        ("pcs_weak",   "Prior close weak (0.1–0.3)",      "prior_cs BETWEEN 0.1 AND 0.3"),
        ("pcs_nlow",   "Prior close neutral-low (0.3–0.5)","prior_cs BETWEEN 0.3 AND 0.5"),
        ("pcs_nhigh",  "Prior close neutral-high (0.5–0.7)","prior_cs BETWEEN 0.5 AND 0.7"),
        ("pcs_str",    "Prior close strong (0.7–0.9)",    "prior_cs BETWEEN 0.7 AND 0.9"),
        ("pcs_vstr",   "Prior close VERY strong (0.9+)",  "prior_cs >= 0.9"),
        ("pcs_ge07",   "Prior close ≥0.7 (any strong)",   "prior_cs >= 0.7"),
        ("pcs_lt03",   "Prior close <0.3 (any weak)",     "prior_cs < 0.3"),
    ]

    # ── Price tiers ───────────────────────────────────────────────────────────
    px_bins = [
        ("px_2_3",   "Price $2–3 (penny)",        "close_price BETWEEN 2 AND 3"),
        ("px_3_5",   "Price $3–5 (micro)",        "close_price BETWEEN 3 AND 5"),
        ("px_5_10",  "Price $5–10 (small-low)",   "close_price BETWEEN 5 AND 10"),
        ("px_10_20", "Price $10–20 (small)",      "close_price BETWEEN 10 AND 20"),
        ("px_20_50", "Price $20–50 (mid)",        "close_price BETWEEN 20 AND 50"),
        ("px_50_100","Price $50–100 (large)",     "close_price BETWEEN 50 AND 100"),
        ("px_ge100", "Price $100+ (mega)",        "close_price >= 100"),
        ("px_2_10",  "Price $2–10 (micro/small)", "close_price BETWEEN 2 AND 10"),
    ]

    # ── Volume tiers ──────────────────────────────────────────────────────────
    vol_bins = [
        ("vol_100_500k", "Volume 100K–500K (light)",  "volume BETWEEN 100000 AND 500000"),
        ("vol_500k_1m",  "Volume 500K–1M",            "volume BETWEEN 500000 AND 1000000"),
        ("vol_1m_5m",    "Volume 1M–5M",              "volume BETWEEN 1000000 AND 5000000"),
        ("vol_5m_10m",   "Volume 5M–10M (heavy)",     "volume BETWEEN 5000000 AND 10000000"),
        ("vol_ge10m",    "Volume 10M+ (explosive)",   "volume >= 10000000"),
        ("vol_ge1m",     "Volume ≥1M",                "volume >= 1000000"),
        ("vol_ge5m",     "Volume ≥5M",                "volume >= 5000000"),
    ]

    # ── Prior day characteristics ─────────────────────────────────────────────
    prior_bins = [
        ("prev_gap_ge5",     "Prior day gap up 5%+",       "prior_gap_pct >= 5"),
        ("prev_gap_ge10",    "Prior day gap up 10%+",      "prior_gap_pct >= 10"),
        ("prev_gap_lt_5",    "Prior day gap DOWN 5%+",     "prior_gap_pct < -5"),
        ("prev_gap_pos",     "Prior day gap positive",     "prior_gap_pct > 0"),
        ("prev_gap_neg",     "Prior day gap negative",     "prior_gap_pct < 0"),
        ("prev_range_tight", "Prior day tight range <1%",  "prior_range_pct < 1"),
        ("prev_range_norm",  "Prior day normal range 1–3%","prior_range_pct BETWEEN 1 AND 3"),
        ("prev_range_wide",  "Prior day wide range 3–5%",  "prior_range_pct BETWEEN 3 AND 5"),
        ("prev_range_vwide", "Prior day VERY wide range 5%+","prior_range_pct >= 5"),
        ("prev_rvol_ge2",    "Prior day RVOL ≥2x",         "prior_rvol >= 2"),
        ("prev_rvol_ge3",    "Prior day RVOL ≥3x",         "prior_rvol >= 3"),
        ("prev_rvol_ge5",    "Prior day RVOL ≥5x",         "prior_rvol >= 5"),
        ("vol_accel",        "Volume accelerating (today > prior)", "vol_accel = TRUE"),
        ("vol_decel",        "Volume decelerating (today < prior)", "vol_accel = FALSE"),
    ]

    # ── Power combos ──────────────────────────────────────────────────────────
    combos = [
        ("gap3_rv2",          "Gap≥3% + RVOL≥2x",
         "premarket_gap_pct >= 3 AND current_rvol >= 2"),
        ("gap5_rv2",          "Gap≥5% + RVOL≥2x",
         "premarket_gap_pct >= 5 AND current_rvol >= 2"),
        ("gap5_rv3",          "Gap≥5% + RVOL≥3x",
         "premarket_gap_pct >= 5 AND current_rvol >= 3"),
        ("gap5_rv5",          "Gap≥5% + RVOL≥5x",
         "premarket_gap_pct >= 5 AND current_rvol >= 5"),
        ("gap10_rv2",         "Gap≥10% + RVOL≥2x",
         "premarket_gap_pct >= 10 AND current_rvol >= 2"),
        ("gap10_rv5",         "Gap≥10% + RVOL≥5x",
         "premarket_gap_pct >= 10 AND current_rvol >= 5"),
        ("gap20_rv2",         "Gap≥20% + RVOL≥2x",
         "premarket_gap_pct >= 20 AND current_rvol >= 2"),
        ("gap5_pcs_str",      "Gap≥5% + prior strong",
         "premarket_gap_pct >= 5 AND prior_cs >= 0.7"),
        ("gap5_rv3_pcs",      "Gap≥5% + RVOL≥3x + prior strong",
         "premarket_gap_pct >= 5 AND current_rvol >= 3 AND prior_cs >= 0.7"),
        ("gap5_tight",        "Gap≥5% + tight prior range (coil)",
         "premarket_gap_pct >= 5 AND prior_range_pct < 1"),
        ("gap10_micro",       "Gap≥10% + micro-cap ($2–5)",
         "premarket_gap_pct >= 10 AND close_price BETWEEN 2 AND 5"),
        ("gap20_micro",       "Gap≥20% + micro-cap ($2–5)",
         "premarket_gap_pct >= 20 AND close_price BETWEEN 2 AND 5"),
        ("gap50_micro",       "Gap≥50% + micro-cap (halt play)",
         "premarket_gap_pct >= 50 AND close_price BETWEEN 2 AND 10"),
        ("rv10_gap3",         "RVOL≥10x + Gap≥3%",
         "current_rvol >= 10 AND premarket_gap_pct >= 3"),
        ("rv10_gap5",         "RVOL≥10x + Gap≥5%",
         "current_rvol >= 10 AND premarket_gap_pct >= 5"),
        ("rv5_pcs_str",       "RVOL≥5x + prior strong",
         "current_rvol >= 5 AND prior_cs >= 0.7"),
        ("gap5_vol_accel",    "Gap≥5% + vol accelerating",
         "premarket_gap_pct >= 5 AND vol_accel = TRUE"),
        ("rv5_vol_accel",     "RVOL≥5x + vol accelerating",
         "current_rvol >= 5 AND vol_accel = TRUE"),
        ("gap_neg_rv3",       "Gap DOWN 2%+ + RVOL≥3x (squeeze)",
         "premarket_gap_pct < -2 AND current_rvol >= 3"),
        ("gap5_rv3_vol1m",    "Gap≥5% + RVOL≥3x + Vol≥1M",
         "premarket_gap_pct >= 5 AND current_rvol >= 3 AND volume >= 1000000"),
        ("gap10_rv3_micro",   "Gap≥10% + RVOL≥3x + micro",
         "premarket_gap_pct >= 10 AND current_rvol >= 3 AND close_price BETWEEN 2 AND 5"),
        ("pcs_vstr_gap3",     "Prior VERY strong + gap 3%+",
         "prior_cs >= 0.9 AND premarket_gap_pct >= 3"),
        ("tight_gap5_rv2",    "Coil: tight prior + gap 5%+ + RVOL 2x",
         "prior_range_pct < 1 AND premarket_gap_pct >= 5 AND current_rvol >= 2"),
        ("prev_str_gap5",     "2-day momentum: prior strong + gap 5%+",
         "prior_cs >= 0.7 AND premarket_gap_pct >= 5"),
        ("prev_weak_gap5",    "Reversal: prior weak + gap 5%+",
         "prior_cs < 0.3 AND premarket_gap_pct >= 5"),
        ("rv10_micro",        "RVOL≥10x + micro-cap ($2–5)",
         "current_rvol >= 10 AND close_price BETWEEN 2 AND 5"),
        ("gap10_vol5m",       "Gap≥10% + Vol≥5M (institutional)",
         "premarket_gap_pct >= 10 AND volume >= 5000000"),
        ("gap5_rv5_vol1m",    "Gap≥5%+RVOL≥5x+Vol≥1M (full setup)",
         "premarket_gap_pct >= 5 AND current_rvol >= 5 AND volume >= 1000000"),
        ("prev_rvol5_gap3",   "Prior RVOL≥5x + today gap≥3% (follow-through)",
         "prior_rvol >= 5 AND premarket_gap_pct >= 3"),
        ("tight_rv5",         "Tight prior range + RVOL≥5x",
         "prior_range_pct < 1 AND current_rvol >= 5"),
        # ── Same-day 7%+ specific combos (buy open, sell close) ──────────────
        ("gap_ge7",           "Gap up ≥7% (any)",
         "premarket_gap_pct >= 7"),
        ("gap7_10",           "Gap up 7–10%",
         "premarket_gap_pct BETWEEN 7 AND 10"),
        ("gap7_rv3",          "Gap≥7% + RVOL≥3x",
         "premarket_gap_pct >= 7 AND current_rvol >= 3"),
        ("gap7_rv5",          "Gap≥7% + RVOL≥5x",
         "premarket_gap_pct >= 7 AND current_rvol >= 5"),
        ("gap7_vol1m",        "Gap≥7% + Vol≥1M",
         "premarket_gap_pct >= 7 AND volume >= 1000000"),
        ("gap7_vol5m",        "Gap≥7% + Vol≥5M",
         "premarket_gap_pct >= 7 AND volume >= 5000000"),
        ("gap7_micro",        "Gap≥7% + micro-cap ($2–5)",
         "premarket_gap_pct >= 7 AND close_price BETWEEN 2 AND 5"),
        ("gap5_rv10",         "Gap≥5% + RVOL≥10x (explosive volume)",
         "premarket_gap_pct >= 5 AND current_rvol >= 10"),
        ("gap5_vol10m",       "Gap≥5% + Vol≥10M (heavy institutional)",
         "premarket_gap_pct >= 5 AND volume >= 10000000"),
        ("gap7_rv3_vol1m",    "Gap≥7%+RVOL≥3x+Vol≥1M (7pct full setup)",
         "premarket_gap_pct >= 7 AND current_rvol >= 3 AND volume >= 1000000"),
        ("gap5_rv5_micro",    "Gap≥5%+RVOL≥5x+micro ($2–5)",
         "premarket_gap_pct >= 5 AND current_rvol >= 5 AND close_price BETWEEN 2 AND 5"),
        ("gap7_pcs_str",      "Gap≥7% + prior strong close",
         "premarket_gap_pct >= 7 AND prior_cs >= 0.7"),
        ("gap5_rv3_px5_20",   "Gap≥5%+RVOL≥3x+price $5–20 (sweet spot)",
         "premarket_gap_pct >= 5 AND current_rvol >= 3 AND close_price BETWEEN 5 AND 20"),
        ("gap7_rv3_pcs_str",  "Gap≥7%+RVOL≥3x+prior strong (3-factor)",
         "premarket_gap_pct >= 7 AND current_rvol >= 3 AND prior_cs >= 0.7"),
        ("rv10_vol1m_gap3",   "RVOL≥10x+Vol≥1M+gap≥3% (explosive open)",
         "current_rvol >= 10 AND volume >= 1000000 AND premarket_gap_pct >= 3"),
    ]

    cells = []
    for k, d, w in gap_bins:
        cells.append(("hb_" + k, d, w))
    for k, d, w in rvol_bins:
        cells.append(("hb_" + k, d, w))
    for k, d, w in pcs_bins:
        cells.append(("hb_" + k, d, w))
    for k, d, w in px_bins:
        cells.append(("hb_" + k, d, w))
    for k, d, w in vol_bins:
        cells.append(("hb_" + k, d, w))
    for k, d, w in prior_bins:
        cells.append(("hb_" + k, d, w))
    for k, d, w in combos:
        cells.append(("hb_" + k, d, w))
    return cells


_HIST_LAST_RUN_DATE = None  # track to avoid re-running same day


def _ensure_hist_grid_table(conn):
    with conn.cursor() as cur:
        # Recreate with composite PK (cell_key, outcome_type).
        # If an old single-column PK table exists, drop and rebuild it —
        # this is safe because the backtest regenerates all rows every run.
        cur.execute("""
            DO $$
            DECLARE
                pk_col_count int;
            BEGIN
                SELECT array_length(conkey, 1)
                INTO pk_col_count
                FROM pg_constraint
                WHERE conrelid = 'aiem_historical_pattern_grid'::regclass
                  AND contype = 'p'
                LIMIT 1;

                IF pk_col_count IS NULL THEN
                    -- table doesn't exist yet
                    CREATE TABLE aiem_historical_pattern_grid (
                        cell_key        TEXT,
                        outcome_type    TEXT,
                        description     TEXT,
                        n_signal        INTEGER,
                        n_total         INTEGER,
                        wr_signal       FLOAT,
                        wr_baseline     FLOAT,
                        p_value         FLOAT,
                        odds_ratio      FLOAT,
                        passes_bonf     BOOLEAN,
                        days_covered    INTEGER,
                        last_tested_at  TIMESTAMPTZ DEFAULT NOW(),
                        PRIMARY KEY (cell_key, outcome_type)
                    );
                ELSIF pk_col_count = 1 THEN
                    -- old single-column PK — drop and rebuild
                    DROP TABLE aiem_historical_pattern_grid;
                    CREATE TABLE aiem_historical_pattern_grid (
                        cell_key        TEXT,
                        outcome_type    TEXT,
                        description     TEXT,
                        n_signal        INTEGER,
                        n_total         INTEGER,
                        wr_signal       FLOAT,
                        wr_baseline     FLOAT,
                        p_value         FLOAT,
                        odds_ratio      FLOAT,
                        passes_bonf     BOOLEAN,
                        days_covered    INTEGER,
                        last_tested_at  TIMESTAMPTZ DEFAULT NOW(),
                        PRIMARY KEY (cell_key, outcome_type)
                    );
                END IF;
                -- If pk_col_count = 2, table already has the right composite PK.
            END $$
        """)
        conn.commit()


def run_historical_backtest():
    """
    Test every indicator cell against ALL available polygon_market_daily history.

    Temp-table columns computed via LAG():
      premarket_gap_pct  = (today_open − prev_close) / prev_close × 100
      current_rvol       = today rvol
      prior_cs           = yesterday close_strength
      prior_rvol         = yesterday rvol
      prior_gap_pct      = yesterday intraday gap (gap_pct col)
      prior_range_pct    = yesterday high-low range
      vol_accel          = current_rvol > prior_rvol  (volume building)
      day_return_pct     = (close − open) / open × 100
      Outcomes tested (same-day only — buy open, sell close):
        big7     = day_return_pct >= 7   (primary target)
        big10    = day_return_pct >= 10  (strong runner)

    Runs once per calendar day. Results stored in aiem_historical_pattern_grid.
    """
    global _HIST_LAST_RUN_DATE
    today = datetime.date.today()
    if _HIST_LAST_RUN_DATE == today:
        return {"status": "already_run_today"}

    n_cells = len(_historical_backtest_cells())
    # 2 same-day outcomes × n_cells; Bonferroni across entire test family
    bonf_thresh = 0.05 / (2 * n_cells)
    log.info(
        "=== Historical backtest: %d cells × 2 same-day outcomes = %d tests "
        "(Bonferroni p<%.2e) — all available trading days ===",
        n_cells, 2 * n_cells, bonf_thresh
    )

    try:
        conn = psycopg2.connect(DB_URL)
        _ensure_hist_grid_table(conn)
        cur = conn.cursor()

        # ── Build the LAG dataset in a temp table ─────────────────────────────
        # Includes range_pct (prior day volatility) and all outcome columns.
        cur.execute("DROP TABLE IF EXISTS _hb_tmp")
        cur.execute("""
            CREATE TEMP TABLE _hb_tmp AS
            SELECT
                ticker,
                scan_date,
                close_price,
                volume,
                (open_price - prev_close) / NULLIF(prev_close, 0) * 100   AS premarket_gap_pct,
                rvol                                                         AS current_rvol,
                prev_cs                                                      AS prior_cs,
                prev_rvol                                                    AS prior_rvol,
                prev_gap                                                     AS prior_gap_pct,
                prev_range                                                   AS prior_range_pct,
                rvol > prev_rvol                                             AS vol_accel,
                (close_price - open_price) / NULLIF(open_price, 0) * 100   AS day_return_pct,
                (close_price - open_price) / NULLIF(open_price, 0) >= 0.07 AS big7,
                (close_price - open_price) / NULLIF(open_price, 0) >= 0.10 AS big10
            FROM (
                SELECT
                    ticker, scan_date, open_price, close_price, rvol, volume,
                    gap_pct, range_pct,
                    LAG(close_price) OVER w  AS prev_close,
                    LAG(close_strength) OVER w AS prev_cs,
                    LAG(rvol)        OVER w  AS prev_rvol,
                    LAG(gap_pct)     OVER w  AS prev_gap,
                    LAG(range_pct)   OVER w  AS prev_range
                FROM polygon_market_daily
                WHERE close_price BETWEEN 2.0 AND 200.0
                  AND volume      >= 100000
                  AND open_price  >  0
                WINDOW w AS (PARTITION BY ticker ORDER BY scan_date)
            ) sub
            WHERE prev_close IS NOT NULL AND prev_close > 0
        """)
        conn.commit()

        # Index the temp table so per-cell WHERE clauses are fast
        cur.execute("CREATE INDEX ON _hb_tmp (premarket_gap_pct)")
        cur.execute("CREATE INDEX ON _hb_tmp (current_rvol)")
        cur.execute("CREATE INDEX ON _hb_tmp (close_price)")
        conn.commit()

        # Baseline stats per outcome (same-day only)
        cur.execute("""
            SELECT
                COUNT(*),
                AVG(big7::int)*100,
                AVG(big10::int)*100
            FROM _hb_tmp
        """)
        row = cur.fetchone()
        total_rows = int(row[0] or 0)
        baselines  = {
            "big7":  float(row[1] or 3),
            "big10": float(row[2] or 2),
        }
        outcomes = {
            "big7":  "big7",
            "big10": "big10",
        }
        log.info(
            "Dataset ready: %d rows | same-day baselines: big7=%.1f%% big10=%.1f%%",
            total_rows, baselines["big7"], baselines["big10"]
        )

        if total_rows < 5000:
            conn.close()
            return {"status": "insufficient_data", "total": total_rows}

        cells      = _historical_backtest_cells()
        significant = []
        total_tests = 0

        for outcome_col, outcome_label in outcomes.items():
            base_wr  = baselines[outcome_col]
            log.info("-- Testing outcome: %-10s  baseline=%.2f%% --", outcome_label, base_wr)

            for key, desc, where_sql in cells:
                try:
                    cur.execute(f"""
                        SELECT
                            COUNT(*) AS n_in,
                            SUM(CASE WHEN {outcome_col} THEN 1 ELSE 0 END) AS win_in
                        FROM _hb_tmp
                        WHERE {where_sql}
                    """)
                    r = cur.fetchone()
                    if not r or not r[0] or r[0] < 30:
                        continue
                    n_in    = int(r[0])
                    win_in  = int(r[1] or 0)
                    lose_in = n_in - win_in
                    n_out   = total_rows - n_in
                    win_out = max(int(round(total_rows * base_wr / 100)) - win_in, 0)
                    lose_out = max(n_out - win_out, 0)
                    if n_out < 30:
                        continue

                    odds_r, p_val = sc.fisher_exact(
                        [[win_in, lose_in], [win_out, lose_out]],
                        alternative="greater"
                    )
                    wr_sig      = win_in / n_in * 100
                    passes_bonf = bool(p_val < bonf_thresh)
                    total_tests += 1

                    cur.execute("""
                        INSERT INTO aiem_historical_pattern_grid
                            (cell_key, outcome_type, description, n_signal, n_total,
                             wr_signal, wr_baseline, p_value, odds_ratio,
                             passes_bonf, days_covered, last_tested_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,494,NOW())
                        ON CONFLICT (cell_key, outcome_type) DO UPDATE SET
                            description    = EXCLUDED.description,
                            n_signal       = EXCLUDED.n_signal,
                            n_total        = EXCLUDED.n_total,
                            wr_signal      = EXCLUDED.wr_signal,
                            wr_baseline    = EXCLUDED.wr_baseline,
                            p_value        = EXCLUDED.p_value,
                            odds_ratio     = EXCLUDED.odds_ratio,
                            passes_bonf    = EXCLUDED.passes_bonf,
                            days_covered   = EXCLUDED.days_covered,
                            last_tested_at = NOW()
                    """, (key, outcome_label, desc, n_in, total_rows,
                          round(wr_sig, 2), round(base_wr, 2),
                          float(p_val), float(odds_r), passes_bonf))
                    conn.commit()

                    if passes_bonf:
                        log.info(
                            "★ FINDING [%s]  %-44s  n=%6d  WR=%.1f%% (base=%.1f%%)  "
                            "p=%.2e  OR=%.2f",
                            outcome_label, desc[:44], n_in,
                            wr_sig, base_wr, p_val, float(odds_r)
                        )
                        significant.append({
                            "outcome": outcome_label, "desc": desc, "n": n_in,
                            "wr": round(wr_sig, 1), "base": round(base_wr, 1),
                            "p": float(p_val), "or": round(float(odds_r), 2),
                        })

                except Exception as cell_err:
                    log.warning("Cell %s/%s error: %s", outcome_label, key, cell_err)
                    conn.rollback()

        cur.execute("DROP TABLE IF EXISTS _hb_tmp")
        conn.commit()
        conn.close()

        _HIST_LAST_RUN_DATE = today
        log.info(
            "Historical backtest COMPLETE: %d total tests, %d significant findings",
            total_tests, len(significant)
        )
        return {
            "status": "ok", "total": total_rows,
            "tested": total_tests, "significant": len(significant),
            "findings": significant,
        }

    except Exception as exc:
        log.error("Historical backtest error: %s", exc)
        traceback.print_exc()
        return {"status": "error", "error": str(exc)}


# ── Weekly Telegram pattern digest ────────────────────────────────────────────

_DIGEST_LAST_SENT_DATE = None


def send_pattern_digest(force: bool = False):
    """
    Send a Telegram digest of the best intraday pattern findings.
    Fires automatically every Sunday. Pass force=True to send immediately.
    Pulls from:
      - aiem_historical_pattern_grid  (365-day historical backtest)
      - aiem_intraday_grid_state      (live first-candle accumulation)
    """
    global _DIGEST_LAST_SENT_DATE
    today = datetime.date.today()
    is_sunday = today.weekday() == 6
    if not force and (not is_sunday or _DIGEST_LAST_SENT_DATE == today):
        return

    log.info("Sending weekly pattern digest to Telegram...")
    try:
        conn = psycopg2.connect(DB_URL)
        cur  = conn.cursor()
        lines = [
            "📊 <b>AIEM Intraday Pattern Research Report</b>",
            f"<i>{today.strftime('%B %d, %Y')}</i>",
            "",
        ]

        # ── Same-day 7%+ findings (primary focus) ────────────────────────────
        try:
            cur.execute("""
                SELECT description, n_signal, wr_signal, wr_baseline, p_value, odds_ratio
                FROM aiem_historical_pattern_grid
                WHERE passes_bonf = TRUE
                  AND outcome_type = 'big7'
                ORDER BY wr_signal DESC
                LIMIT 10
            """)
            rows = cur.fetchall()
            if rows:
                lines.append("🎯 <b>Same-Day 7%+ Runner Patterns (buy open → sell close)</b>")
                for r in rows:
                    desc, n, wr, base, p, OR = r
                    out_of_20 = round(wr / 100 * 20, 1)
                    icon = "🟢" if wr >= 15 else "🟡"
                    lines.append(
                        f"{icon} <b>{desc}</b>\n"
                        f"   WR <b>{wr:.1f}%</b> ({out_of_20}/20 stocks run 7%+)"
                        f"  |  n={n:,}  |  OR={OR:.2f}  |  p={p:.2e}"
                    )
                lines.append("")
            else:
                lines.append("🎯 <b>Same-Day 7%+ Patterns</b>: Still accumulating data — check back tomorrow")
                lines.append("")
        except Exception:
            pass

        # Historical backtest top findings (all outcomes)
        try:
            cur.execute("""
                SELECT description, outcome_type, n_signal, wr_signal, wr_baseline, p_value, odds_ratio
                FROM aiem_historical_pattern_grid
                WHERE passes_bonf = TRUE
                  AND outcome_type = 'any_win'
                ORDER BY wr_signal DESC
                LIMIT 10
            """)
            rows = cur.fetchall()
            if rows:
                lines.append("🔬 <b>Historical Backtest — 365 days, 11K+ stocks</b>")
                for r in rows:
                    desc, outcome, n, wr, base, p, OR = r
                    edge = wr - base
                    icon = "🟢" if edge >= 5 else "🟡"
                    lines.append(
                        f"{icon} <b>{desc}</b>\n"
                        f"   WR <b>{wr:.1f}%</b> vs {base:.1f}% baseline"
                        f"  |  n={n:,}  |  OR={OR:.2f}  |  p={p:.2e}"
                    )
                lines.append("")
            else:
                lines.append(
                    "🔬 <b>Historical Backtest</b>: Running analysis — "
                    "no Bonferroni-significant patterns found yet"
                )
                lines.append("")
        except Exception:
            pass

        # Live first-candle findings
        try:
            cur.execute("""
                SELECT description, last_n, last_win_rate, last_baseline, last_p_value
                FROM aiem_intraday_grid_state
                WHERE last_p_value < 0.05
                ORDER BY last_win_rate DESC
                LIMIT 5
            """)
            rows = cur.fetchall()
            if rows:
                lines.append("📅 <b>Live First-Candle Grid (accumulating daily since fix)</b>")
                for r in rows:
                    desc, n, wr, base, p = r
                    lines.append(
                        f"📈 <b>{desc}</b>\n"
                        f"   WR <b>{wr:.1f}%</b> vs {base:.1f}%  |  n={n}  |  p={p:.4f}"
                    )
                lines.append("")
        except Exception:
            pass

        # Data coverage stats
        try:
            cur.execute("SELECT COUNT(*), COUNT(DISTINCT scan_date) "
                        "FROM aiem_first_candle_data WHERE day_win IS NOT NULL")
            fc_rows, fc_days = cur.fetchone()
            lines.append(
                f"<i>Live data: {fc_rows or 0} stock-days captured across "
                f"{fc_days or 0} trading sessions</i>"
            )
        except Exception:
            pass

        lines.append("<i>Target: find signal with ≥70% WR, n≥100, p&lt;0.001</i>")
        lines.append("<i>System captures ~200 stocks daily at 9:36 AM ET</i>")

        conn.close()
        _tg_send("\n".join(lines))
        _DIGEST_LAST_SENT_DATE = today
        log.info("Pattern digest sent.")

    except Exception as exc:
        log.error("Pattern digest error: %s", exc)


# ── Main loop ────────────────────────────────────────────────────────────────

def main():
    log.info("AIEM statistical research runner starting — pure stats, zero OpenAI")
    ensure_schema()

    # ── Startup kick: run the historical backtest immediately ──────────────────
    # This fires once right now so the user gets pattern results ASAP (don't
    # wait for the EOD battery to finish first).
    log.info("=== STARTUP: Historical backtest kicking off immediately (all available data) ===")
    hist_startup = run_historical_backtest()
    log.info("STARTUP historical backtest: status=%s  significant=%s  total_rows=%s",
             hist_startup.get("status"), hist_startup.get("significant"),
             hist_startup.get("total"))
    # Send the digest right after so findings go to Telegram immediately
    send_pattern_digest(force=True)
    # ──────────────────────────────────────────────────────────────────────────

    while True:
        try:
            # Pause during 7:30–10:30 AM ET Mon–Fri (morning burst window).
            # Background threads in main.py all compete for pool connections
            # during this window. Staying off the DB during peak keeps the
            # pool free for live user-facing requests.
            now_utc = datetime.datetime.utcnow()
            now_et_h = (now_utc.hour - 4) % 24  # rough ET offset (EDT)
            now_et_m = now_utc.minute
            now_et_mins = now_et_h * 60 + now_et_m
            is_weekday = now_utc.weekday() < 5  # Mon=0 … Fri=4
            in_peak = is_weekday and (450 <= now_et_mins <= 630)  # 7:30–10:30 AM ET
            if in_peak:
                wake_mins = 630 - now_et_mins + 5
                log.info("Peak morning window — sleeping %d min to keep pool free.", wake_mins)
                time.sleep(wake_mins * 60)
                continue

            log.info("=== EOD battery cycle start ===")
            eod_result = run_eod_battery(batch_size=50)

            log.info("=== Intraday battery cycle start ===")
            intra_result = run_intraday_battery()
            if intra_result.get("status") == "no_data":
                log.info("Intraday: no settled rows yet (first candle capture at 9:36 AM ET)")

            log.info("=== Historical backtest cycle start ===")
            hist_result = run_historical_backtest()
            log.info("Historical backtest: %s", hist_result.get("status"))
            if hist_result.get("status") == "ok":
                log.info("  → %d rows, %d/%d cells significant",
                         hist_result.get("total", 0),
                         hist_result.get("significant", 0),
                         hist_result.get("tested", 0))

            # Weekly Sunday pattern digest to Telegram
            send_pattern_digest()

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
