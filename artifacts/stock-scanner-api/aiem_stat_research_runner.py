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
    Indicator cells for the historical polygon_market_daily backtest.
    Dataset columns available: premarket_gap_pct, current_rvol, prior_cs,
    prior_rvol, prior_gap_pct, close_price, volume, day_win.
    """
    return [
        # Premarket gap size
        ("hb_gap_ge2",          "Gap up ≥2%",                   "premarket_gap_pct >= 2"),
        ("hb_gap_ge5",          "Gap up ≥5%",                   "premarket_gap_pct >= 5"),
        ("hb_gap_ge10",         "Gap up ≥10%",                  "premarket_gap_pct >= 10"),
        ("hb_gap_ge20",         "Gap up ≥20%",                  "premarket_gap_pct >= 20"),
        ("hb_gap_1_3",          "Gap 1–3%",                     "premarket_gap_pct BETWEEN 1 AND 3"),
        ("hb_gap_3_5",          "Gap 3–5%",                     "premarket_gap_pct BETWEEN 3 AND 5"),
        ("hb_gap_5_10",         "Gap 5–10%",                    "premarket_gap_pct BETWEEN 5 AND 10"),
        ("hb_gap_lt0",          "Gap negative",                 "premarket_gap_pct < 0"),
        ("hb_gap_lt_5",         "Gap down >5%",                 "premarket_gap_pct < -5"),
        # RVOL tiers
        ("hb_rvol_ge2",         "RVOL ≥2x",                     "current_rvol >= 2"),
        ("hb_rvol_ge3",         "RVOL ≥3x",                     "current_rvol >= 3"),
        ("hb_rvol_ge5",         "RVOL ≥5x",                     "current_rvol >= 5"),
        ("hb_rvol_ge10",        "RVOL ≥10x",                    "current_rvol >= 10"),
        ("hb_rvol_2_5",         "RVOL 2–5x",                    "current_rvol BETWEEN 2 AND 5"),
        ("hb_rvol_5_10",        "RVOL 5–10x",                   "current_rvol BETWEEN 5 AND 10"),
        # Prior close strength
        ("hb_pcs_str",          "Prior day closed strong ≥0.7", "prior_cs >= 0.7"),
        ("hb_pcs_weak",         "Prior day closed weak <0.3",   "prior_cs < 0.3"),
        ("hb_pcs_mid",          "Prior day closed mid 0.3–0.7", "prior_cs BETWEEN 0.3 AND 0.7"),
        # Price tier
        ("hb_px_2_5",           "Price $2–5 (micro-cap)",       "close_price BETWEEN 2 AND 5"),
        ("hb_px_5_15",          "Price $5–15 (small)",          "close_price BETWEEN 5 AND 15"),
        ("hb_px_15_50",         "Price $15–50 (mid)",           "close_price BETWEEN 15 AND 50"),
        ("hb_px_ge50",          "Price ≥$50 (large)",           "close_price >= 50"),
        # Volume tiers
        ("hb_vol_1m",           "Volume ≥1M shares",            "volume >= 1000000"),
        ("hb_vol_5m",           "Volume ≥5M shares",            "volume >= 5000000"),
        # Prior day indicators
        ("hb_prev_rvol_ge3",    "Prior day RVOL ≥3x",           "prior_rvol >= 3"),
        ("hb_prev_gap_ge5",     "Prior day gap ≥5%",            "prior_gap_pct >= 5"),
        ("hb_prev_gap_neg",     "Prior day gap negative",       "prior_gap_pct < 0"),
        # Power combos
        ("hb_gap5_rv3",         "Gap≥5% + RVOL≥3x",
         "premarket_gap_pct >= 5 AND current_rvol >= 3"),
        ("hb_gap10_rv2",        "Gap≥10% + RVOL≥2x",
         "premarket_gap_pct >= 10 AND current_rvol >= 2"),
        ("hb_gap5_str",         "Gap≥5% + prior strong",
         "premarket_gap_pct >= 5 AND prior_cs >= 0.7"),
        ("hb_gap5_rv3_str",     "Gap≥5% + RVOL≥3x + prior strong",
         "premarket_gap_pct >= 5 AND current_rvol >= 3 AND prior_cs >= 0.7"),
        ("hb_gap3_rv2",         "Gap≥3% + RVOL≥2x",
         "premarket_gap_pct >= 3 AND current_rvol >= 2"),
        ("hb_rv5_gap3",         "RVOL≥5x + Gap≥3%",
         "current_rvol >= 5 AND premarket_gap_pct >= 3"),
        ("hb_rv10_any",         "RVOL≥10x (any gap)",           "current_rvol >= 10"),
        ("hb_gap_neg_rv3",      "Gap down + RVOL≥3x (fade)",
         "premarket_gap_pct < -2 AND current_rvol >= 3"),
        ("hb_prev_str_gap5",    "Prior strong + today gap≥5%",
         "prior_cs >= 0.7 AND premarket_gap_pct >= 5"),
        ("hb_gap20_micro",      "Gap≥20% micro-cap ($2–10)",
         "premarket_gap_pct >= 20 AND close_price BETWEEN 2 AND 10"),
    ]


_HIST_LAST_RUN_DATE = None  # track to avoid re-running same day


def _ensure_hist_grid_table(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS aiem_historical_pattern_grid (
                cell_key        TEXT PRIMARY KEY,
                description     TEXT,
                n_signal        INTEGER,
                n_total         INTEGER,
                wr_signal       FLOAT,
                wr_baseline     FLOAT,
                p_value         FLOAT,
                odds_ratio      FLOAT,
                passes_bonf     BOOLEAN,
                days_covered    INTEGER,
                last_tested_at  TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        conn.commit()


def run_historical_backtest():
    """
    Backtest every indicator cell against 365 days of polygon_market_daily.

    Uses LAG() window function to compute per-stock per-day:
      premarket_gap_pct = (today_open − yesterday_close) / yesterday_close × 100
      current_rvol      = today's rvol
      prior_cs          = yesterday's close_strength
      day_win           = today_close > today_open   (buy-at-open / sell-at-close)

    Runs once per calendar day. Results stored in aiem_historical_pattern_grid.
    """
    global _HIST_LAST_RUN_DATE
    today = datetime.date.today()
    if _HIST_LAST_RUN_DATE == today:
        return {"status": "already_run_today"}

    log.info("=== Historical backtest starting (polygon_market_daily, last 365d) ===")
    try:
        conn = psycopg2.connect(DB_URL)
        _ensure_hist_grid_table(conn)
        cur = conn.cursor()

        # Build LAG dataset once in a temp table for efficient multi-cell queries.
        cur.execute("DROP TABLE IF EXISTS _hb_tmp")
        cur.execute("""
            CREATE TEMP TABLE _hb_tmp AS
            SELECT
                ticker, scan_date, close_price, volume,
                (open_price - prev_close) / NULLIF(prev_close, 0) * 100  AS premarket_gap_pct,
                rvol                                                       AS current_rvol,
                prev_cs                                                    AS prior_cs,
                prev_rvol                                                  AS prior_rvol,
                prev_gap                                                   AS prior_gap_pct,
                close_price > open_price                                   AS day_win
            FROM (
                SELECT
                    ticker, scan_date, open_price, close_price, rvol, volume, gap_pct,
                    LAG(close_price)    OVER w AS prev_close,
                    LAG(close_strength) OVER w AS prev_cs,
                    LAG(rvol)           OVER w AS prev_rvol,
                    LAG(gap_pct)        OVER w AS prev_gap
                FROM polygon_market_daily
                WHERE scan_date >= CURRENT_DATE - INTERVAL '365 days'
                  AND close_price BETWEEN 2.0 AND 200.0
                  AND volume      >= 100000
                  AND open_price  >  0
                WINDOW w AS (PARTITION BY ticker ORDER BY scan_date)
            ) sub
            WHERE prev_close IS NOT NULL AND prev_close > 0
        """)
        conn.commit()

        cur.execute("SELECT COUNT(*), AVG(day_win::int) * 100 FROM _hb_tmp")
        total_rows, baseline_wr = cur.fetchone()
        total_rows  = int(total_rows or 0)
        baseline_wr = float(baseline_wr or 50.0)
        log.info("Historical dataset built: %d rows, baseline WR=%.1f%%", total_rows, baseline_wr)

        if total_rows < 5000:
            conn.close()
            return {"status": "insufficient_data", "total": total_rows}

        cells       = _historical_backtest_cells()
        bonf_thresh = 0.05 / len(cells)
        significant = []

        for key, desc, where_sql in cells:
            try:
                cur.execute(f"""
                    SELECT
                        COUNT(*)                                   AS n_in,
                        SUM(CASE WHEN day_win THEN 1 ELSE 0 END)  AS win_in
                    FROM _hb_tmp
                    WHERE {where_sql}
                """)
                row = cur.fetchone()
                if not row or not row[0] or row[0] < 30:
                    continue
                n_in  = int(row[0])
                win_in = int(row[1] or 0)
                lose_in = n_in - win_in

                n_out   = total_rows - n_in
                win_out = int(round(total_rows * baseline_wr / 100)) - win_in
                win_out = max(win_out, 0)
                lose_out = max(n_out - win_out, 0)

                if n_out < 30:
                    continue

                odds_r, p_val = sc.fisher_exact(
                    [[win_in, lose_in], [win_out, lose_out]],
                    alternative="greater"
                )
                wr_sig      = win_in / n_in * 100
                passes_bonf = bool(p_val < bonf_thresh)

                cur.execute("""
                    INSERT INTO aiem_historical_pattern_grid
                        (cell_key, description, n_signal, n_total,
                         wr_signal, wr_baseline, p_value, odds_ratio,
                         passes_bonf, days_covered, last_tested_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,365,NOW())
                    ON CONFLICT (cell_key) DO UPDATE SET
                        n_signal       = EXCLUDED.n_signal,
                        n_total        = EXCLUDED.n_total,
                        wr_signal      = EXCLUDED.wr_signal,
                        wr_baseline    = EXCLUDED.wr_baseline,
                        p_value        = EXCLUDED.p_value,
                        odds_ratio     = EXCLUDED.odds_ratio,
                        passes_bonf    = EXCLUDED.passes_bonf,
                        days_covered   = EXCLUDED.days_covered,
                        last_tested_at = NOW()
                """, (key, desc, n_in, total_rows,
                      round(wr_sig, 2), round(baseline_wr, 2),
                      float(p_val), float(odds_r), passes_bonf))
                conn.commit()

                if passes_bonf:
                    log.info(
                        "HIST FINDING ★  %-48s  n=%6d  WR=%.1f%% (base=%.1f%%)  p=%.2e  OR=%.2f",
                        desc[:48], n_in, wr_sig, baseline_wr, p_val, float(odds_r)
                    )
                    significant.append({
                        "desc": desc, "n": n_in,
                        "wr": round(wr_sig, 1), "base": round(baseline_wr, 1),
                        "p": float(p_val), "or": round(float(odds_r), 2),
                    })

            except Exception as cell_err:
                log.warning("Historical cell %s error: %s", key, cell_err)
                conn.rollback()

        cur.execute("DROP TABLE IF EXISTS _hb_tmp")
        conn.commit()
        conn.close()

        _HIST_LAST_RUN_DATE = today
        log.info("Historical backtest complete: %d cells, %d significant (Bonferroni p<%.4f)",
                 len(cells), len(significant), bonf_thresh)
        return {"status": "ok", "total": total_rows, "tested": len(cells),
                "significant": len(significant), "findings": significant}

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

        # Historical backtest top findings
        try:
            cur.execute("""
                SELECT description, n_signal, wr_signal, wr_baseline, p_value, odds_ratio
                FROM aiem_historical_pattern_grid
                WHERE passes_bonf = TRUE
                ORDER BY wr_signal DESC
                LIMIT 10
            """)
            rows = cur.fetchall()
            if rows:
                lines.append("🔬 <b>Historical Backtest — 365 days, 11K+ stocks</b>")
                for r in rows:
                    desc, n, wr, base, p, OR = r
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
