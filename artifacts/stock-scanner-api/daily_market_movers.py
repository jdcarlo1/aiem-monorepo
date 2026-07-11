"""
daily_market_movers.py — Daily Top-100 Winners/Losers per Market Cap Tier

Captures the trading day's biggest movers within each of four market cap tiers
(Nano <$300M, Small $300M–$2B, Mid $2B–$10B, Large $10B+), enriches each
ticker with every available DB-resident indicator, and stores the result as
labeled data for the DiscoveryEngine's per-tier statistical learning loop.

Schedule  : Mon–Fri 17:10 ET (20 min before _discovery_cycle_job at 17:30 ET).
Market cap: refreshed nightly at 23:00 ET from Polygon /v3/reference/tickers.
Enrichment: 100% DB-only — no live API calls at enrichment time.
Candlestick: computed from polygon_market_daily OHLCV via candlestick_patterns.detect_patterns.
Options    : EXCLUDED for Nano Cap tier (no listed options market for sub-$300M stocks).

Tier breakpoints (matching existing system convention in sms_alerts.py / main.py):
  large : market_cap >= $10B
  mid   : market_cap >= $2B
  small : market_cap >= $300M
  nano  : market_cap <  $300M  (or unknown — treated as nano/smallest)
"""
import json
import logging
import os
import time
import urllib.request
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
import psycopg2.extras

_DB_URL  = os.environ.get("DATABASE_URL", "")
_POLYGON = "https://api.polygon.io"
logger   = logging.getLogger(__name__)

# ─── Tier constants (kept in sync with sms_alerts.py:295-297) ────────────────
_TIER_LARGE = 10_000_000_000
_TIER_MID   =  2_000_000_000
_TIER_SMALL =    300_000_000
_TIERS      = ("nano", "small", "mid", "large")

# Options-dependent feature keys — excluded from Nano Cap feature_snapshot
_OPTIONS_KEYS = frozenset({
    "prem_7d", "vol_oi_avg", "iv_avg",
    "gex_m", "gex_regime", "pc_skew_pp", "term_ratio", "front_iv", "back_iv",
    "aisc_vol_oi", "aisc_prem", "aisc_otm_pct", "aisc_conviction",
})

# ─────────────────────────────────────────────────────────────────────────────
# 1. Schema init
# ─────────────────────────────────────────────────────────────────────────────

def init_tables() -> None:
    """Create supporting tables. Idempotent."""
    ddl = """
    CREATE TABLE IF NOT EXISTS ticker_market_cap_cache (
        ticker      TEXT PRIMARY KEY,
        market_cap  BIGINT,
        fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_tmcc_market_cap
        ON ticker_market_cap_cache (market_cap);

    CREATE TABLE IF NOT EXISTS daily_market_movers (
        id               BIGSERIAL PRIMARY KEY,
        scan_date        DATE    NOT NULL,
        ticker           TEXT    NOT NULL,
        pct_change       NUMERIC(10,4),
        close_price      NUMERIC(12,4),
        rank             INTEGER,
        direction        TEXT    NOT NULL CHECK (direction IN ('winner','loser')),
        market_cap_tier  TEXT    NOT NULL CHECK (market_cap_tier IN ('nano','small','mid','large')),
        feature_snapshot JSONB,
        created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (scan_date, ticker, direction)
    );
    CREATE INDEX IF NOT EXISTS idx_dmm_scan_date
        ON daily_market_movers (scan_date DESC);
    CREATE INDEX IF NOT EXISTS idx_dmm_tier
        ON daily_market_movers (market_cap_tier, scan_date DESC);
    CREATE INDEX IF NOT EXISTS idx_dmm_direction
        ON daily_market_movers (direction, scan_date DESC);
    """
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=5) as c, c.cursor() as cur:
            cur.execute(ddl)
        logger.info("[market_movers] tables OK")
    except Exception as e:
        logger.error(f"[market_movers] init_tables error: {e}")
        raise


# ─────────────────────────────────────────────────────────────────────────────
# 2. Market cap cache — Polygon /v3/reference/tickers
# ─────────────────────────────────────────────────────────────────────────────

def classify_tier(market_cap: Optional[int]) -> str:
    """Map a raw market cap integer to nano/small/mid/large. Unknown → 'nano'."""
    if market_cap is None:
        return "nano"
    if market_cap >= _TIER_LARGE:
        return "large"
    if market_cap >= _TIER_MID:
        return "mid"
    if market_cap >= _TIER_SMALL:
        return "small"
    return "nano"


def _fetch_single_ticker_mktcap(ticker: str, api_key: str) -> Optional[int]:
    """
    Fetch market_cap from /v3/reference/tickers/{ticker} (single-ticker endpoint).
    The batch list endpoint does NOT return market_cap at our subscription tier;
    only the single-ticker endpoint has it. Returns int or None on failure/no data.
    """
    try:
        url = f"{_POLYGON}/v3/reference/tickers/{ticker}?apiKey={api_key}"
        with urllib.request.urlopen(url, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        mc = data.get("results", {}).get("market_cap")
        return int(mc) if mc else None
    except Exception:
        return None


def _upsert_market_cap_batch(rows: List[Tuple[str, Optional[int]]]) -> int:
    """Upsert (ticker, market_cap) rows into ticker_market_cap_cache. Returns count."""
    if not rows:
        return 0
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=10) as conn, conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO ticker_market_cap_cache (ticker, market_cap)
                VALUES %s
                ON CONFLICT (ticker) DO UPDATE SET
                    market_cap = EXCLUDED.market_cap,
                    fetched_at = NOW()
                """,
                rows,
                page_size=500,
            )
        return len(rows)
    except Exception as e:
        logger.error(f"[market_movers] upsert_market_cap_batch error: {e}")
        return 0


def fetch_market_caps_for_tickers(
    tickers: List[str],
    api_key: str,
    num_workers: int = 20,
    sleep_per_worker: float = 0.05,
) -> Dict[str, Optional[int]]:
    """
    Fetch market caps for a list of tickers from /v3/reference/tickers/{ticker}.
    Uses threading for speed. With 20 workers each sleeping 0.05s between calls,
    effective rate ≈ 20–40 req/sec depending on network latency.
    Returns {ticker: market_cap_int_or_None}.
    Also upserts results into ticker_market_cap_cache.
    """
    import threading
    import queue as _queue

    if not api_key or not tickers:
        return {}

    results: Dict[str, Optional[int]] = {}
    lock     = threading.Lock()
    q: _queue.Queue = _queue.Queue()
    for t in tickers:
        q.put(t)

    def worker():
        while True:
            try:
                ticker = q.get_nowait()
            except _queue.Empty:
                return
            try:
                mc = _fetch_single_ticker_mktcap(ticker, api_key)
                with lock:
                    results[ticker] = mc
                time.sleep(sleep_per_worker)
            except Exception:
                with lock:
                    results[ticker] = None
            finally:
                q.task_done()

    threads = [
        threading.Thread(target=worker, daemon=True)
        for _ in range(min(num_workers, len(tickers)))
    ]
    for th in threads:
        th.start()
    q.join()  # blocks until all tasks done

    # Upsert to cache
    _upsert_market_cap_batch(list(results.items()))
    return results


def refresh_market_cap_cache(api_key: str, num_workers: int = 20) -> Dict[str, Any]:
    """
    Nightly (23:00 ET) full refresh of ticker_market_cap_cache.
    Covers every ticker in polygon_market_daily from the last 3 trading days.
    Uses /v3/reference/tickers/{ticker} (single-ticker endpoint — the only Polygon
    endpoint that returns market_cap at standard subscription tiers).
    With 20 workers: ~2–4 minutes for 7,000 tickers. Safe for a nightly job.
    """
    if not api_key:
        return {"error": "POLYGON_API_KEY not set"}

    # Get the universe from polygon_market_daily (our authoritative ticker list)
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=10) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT ticker FROM polygon_market_daily
                WHERE scan_date >= CURRENT_DATE - INTERVAL '3 days'
                ORDER BY ticker
            """)
            tickers = [r[0] for r in cur.fetchall()]
    except Exception as e:
        return {"error": f"DB error fetching universe: {e}"}

    if not tickers:
        return {"error": "polygon_market_daily is empty — no tickers to refresh"}

    t0      = time.time()
    results = fetch_market_caps_for_tickers(tickers, api_key, num_workers=num_workers)
    elapsed = round(time.time() - t0, 1)

    n_ok  = sum(1 for v in results.values() if v is not None)
    n_null = len(results) - n_ok

    summary = {
        "tickers_attempted": len(tickers),
        "with_market_cap":   n_ok,
        "returned_null":     n_null,
        "upserted":          len(results),
        "elapsed_s":         elapsed,
    }
    logger.info(f"[market_movers] nightly cap refresh: {summary}")
    return summary


def _get_market_cap_tiers_raw_set(conn) -> set:
    """
    Return set of tickers in ticker_market_cap_cache that have a NON-NULL market_cap.
    Tickers present in cache but with market_cap=NULL are treated as uncached
    (they need a live fetch via the single-ticker Polygon endpoint).
    """
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT ticker FROM ticker_market_cap_cache WHERE market_cap IS NOT NULL"
            )
            return {r[0] for r in cur.fetchall()}
    except Exception:
        return set()


def _get_market_cap_tiers(conn, tickers: List[str]) -> Dict[str, str]:
    """
    Look up market cap tiers for a batch of tickers from ticker_market_cap_cache.
    Tickers missing from cache get tier 'nano' (unknown → smallest).
    Returns {ticker: tier}.
    """
    if not tickers:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ticker, market_cap FROM ticker_market_cap_cache WHERE ticker = ANY(%s)",
            (tickers,),
        )
        rows = cur.fetchall()
    result = {t: "nano" for t in tickers}  # default
    for ticker, mc in rows:
        result[ticker] = classify_tier(mc)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# 3. Daily return computation
# ─────────────────────────────────────────────────────────────────────────────

def _get_most_recent_trading_date(conn) -> Optional[date]:
    with conn.cursor() as cur:
        cur.execute("SELECT MAX(scan_date) FROM polygon_market_daily")
        row = cur.fetchone()
        return row[0] if row else None


def _compute_daily_returns(conn, scan_date: date) -> Dict[str, Dict]:
    """
    Use a LAG window to compute pct_change for every ticker on scan_date.
    (polygon_market_daily.prev_close is always NULL — confirmed; LAG is the
     only reliable source.)
    Filter: close_price >= $2 and prev bar exists in the last 5 calendar days.
    Returns {ticker: {pct_change, close_price, open_price, high_price,
                       low_price, volume, close_strength, range_pct}}.
    """
    sql = """
    WITH lagged AS (
        SELECT
            ticker,
            scan_date,
            close_price,
            open_price,
            high_price,
            low_price,
            volume,
            close_strength,
            range_pct,
            LAG(close_price) OVER (PARTITION BY ticker ORDER BY scan_date) AS prev_close_lag
        FROM polygon_market_daily
        WHERE scan_date BETWEEN %s - INTERVAL '7 days' AND %s
          AND close_price >= 2.0
    )
    SELECT
        ticker,
        close_price,
        open_price,
        high_price,
        low_price,
        volume,
        COALESCE(close_strength, 0.5) AS close_strength,
        COALESCE(range_pct,      0.0) AS range_pct,
        ROUND(((close_price - prev_close_lag) / prev_close_lag * 100.0)::numeric, 4) AS pct_change
    FROM lagged
    WHERE scan_date = %s
      AND prev_close_lag IS NOT NULL
      AND prev_close_lag > 0
    """
    out = {}
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute("SET LOCAL statement_timeout = '45000'")
            cur.execute(sql, (scan_date, scan_date, scan_date))
            for r in cur.fetchall():
                out[r["ticker"]] = {
                    "pct_change":    float(r["pct_change"]    or 0.0),
                    "close_price":   float(r["close_price"]   or 0.0),
                    "open_price":    float(r["open_price"]    or 0.0),
                    "high_price":    float(r["high_price"]    or 0.0),
                    "low_price":     float(r["low_price"]     or 0.0),
                    "volume":        int(r["volume"]          or 0),
                    "close_strength":float(r["close_strength"]or 0.5),
                    "range_pct":     float(r["range_pct"]     or 0.0),
                }
    except Exception as e:
        logger.error(f"[market_movers] compute_daily_returns error: {e}")
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 4. Per-tier winner / loser selection
# ─────────────────────────────────────────────────────────────────────────────

def _build_tier_movers(
    returns: Dict[str, Dict],
    tiers:   Dict[str, str],
    top_n:   int = 100,
) -> Dict[str, Dict[str, List]]:
    """
    For each tier, rank tickers by pct_change and take top_n (winners)
    and bottom_n (losers). A ticker may appear in both lists if it
    belongs to a tier — deduplicated at upsert time via the UNIQUE constraint.
    Returns {tier: {"winners": [...], "losers": [...]}}.
    Each element: {ticker, pct_change, close_price, rank, direction, ...ohlcv}.
    """
    tier_buckets: Dict[str, List] = {t: [] for t in _TIERS}
    for ticker, ret in returns.items():
        tier = tiers.get(ticker, "nano")
        tier_buckets[tier].append({"ticker": ticker, **ret})

    result = {}
    for tier, rows in tier_buckets.items():
        sorted_rows = sorted(rows, key=lambda r: r["pct_change"], reverse=True)
        n = len(sorted_rows)

        # Winners: top_n by pct_change
        winner_rows = sorted_rows[:top_n]
        winner_set  = {r["ticker"] for r in winner_rows}

        # Losers: bottom_n by pct_change, EXCLUDING any ticker already in winners.
        # This prevents the same ticker appearing in both lists when tier_size < 2×top_n.
        loser_candidates = [r for r in reversed(sorted_rows) if r["ticker"] not in winner_set]
        loser_rows = loser_candidates[:top_n]

        winners = [
            {**r, "rank": i + 1, "direction": "winner"}
            for i, r in enumerate(winner_rows)
        ]
        losers = [
            {**r, "rank": i + 1, "direction": "loser"}
            for i, r in enumerate(loser_rows)
        ]
        result[tier] = {"winners": winners, "losers": losers}
    return result


# ─────────────────────────────────────────────────────────────────────────────
# 5. Candlestick pattern detection (batch)
# ─────────────────────────────────────────────────────────────────────────────

def _detect_candlestick_batch(
    conn, tickers: List[str], scan_date: date
) -> Dict[str, Dict]:
    """
    Fetch the 5 most recent OHLCV bars for each ticker from polygon_market_daily
    and run candlestick_patterns.detect_patterns on each. Returns:
      {ticker: {"patterns": [...], "has_bullish_candle": 0/1,
                "has_bearish_candle": 0/1, "has_doji": 0/1, ...}}
    DB-only. No external API calls.
    """
    _BULLISH = frozenset({"hammer", "bullish_engulfing", "morning_star"})
    _BEARISH = frozenset({"shooting_star", "hanging_man", "bearish_engulfing", "evening_star"})

    sql = """
    SELECT ticker, scan_date, open_price, high_price, low_price, close_price
    FROM polygon_market_daily
    WHERE ticker = ANY(%s::text[])
      AND scan_date BETWEEN %s - INTERVAL '10 days' AND %s
      AND open_price IS NOT NULL
      AND close_price IS NOT NULL
    ORDER BY ticker, scan_date
    """
    bars_by_ticker: Dict[str, List] = {}
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute("SET LOCAL statement_timeout = '20000'")
            cur.execute(sql, (tickers, scan_date, scan_date))
            for r in cur.fetchall():
                t = r["ticker"]
                bars_by_ticker.setdefault(t, []).append({
                    "open":  float(r["open_price"]),
                    "high":  float(r["high_price"]),
                    "low":   float(r["low_price"]),
                    "close": float(r["close_price"]),
                })
    except Exception as e:
        logger.error(f"[market_movers] candlestick fetch error: {e}")
        return {}

    try:
        from candlestick_patterns import detect_patterns
    except ImportError:
        logger.error("[market_movers] candlestick_patterns module not found")
        return {}

    out = {}
    for ticker, bars in bars_by_ticker.items():
        if len(bars) < 2:
            continue
        result = detect_patterns(bars)
        patterns = result.get("patterns", [])
        pat_set  = set(patterns)
        out[ticker] = {
            "candlestick_patterns": patterns,
            "has_bullish_candle":   int(bool(pat_set & _BULLISH)),
            "has_bearish_candle":   int(bool(pat_set & _BEARISH)),
            "has_doji":             int("doji"             in pat_set),
            "has_hammer":           int("hammer"           in pat_set),
            "has_shooting_star":    int("shooting_star"    in pat_set),
            "has_hanging_man":      int("hanging_man"      in pat_set),
            "has_bullish_engulfing":int("bullish_engulfing" in pat_set),
            "has_bearish_engulfing":int("bearish_engulfing" in pat_set),
            "has_morning_star":     int("morning_star"     in pat_set),
            "has_evening_star":     int("evening_star"     in pat_set),
        }
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 6. Feature enrichment — all indicators available in the DB
# ─────────────────────────────────────────────────────────────────────────────

def _enrich_features_batch(
    conn, tickers: List[str], scan_date: date
) -> Dict[str, Dict]:
    """
    One SQL batch query for ALL DB-resident indicators across ALL tickers.
    No external API calls. NULL means the ticker is outside the scanned
    watchlist for that indicator — documented, not a bug.

    Indicators pulled (all DB-resident, no external API calls):
      Layer 9  : statistical_score, hurst_raw, vpin_raw, jump_detected,
                 entropy_score, tail_score, vrp_score, amihud_score,
                 cs_spread_raw, rnd_skew, pca_factor1_var
      Conviction: total_pts, conviction_pct, label
      Options   : prem_7d, vol_oi_avg, iv_avg (unusual_calls_log 7-day agg)
                  gex_m, gex_regime, pc_skew_pp, term_ratio, front_iv, back_iv
                  (options_structure_scan)
      AI calls  : aisc_vol_oi, aisc_prem, aisc_otm_pct, aisc_conviction
                  (ai_short_calls_log — actual columns, not hypothetical score cols)
      Stat arb  : coint_pvalue (only for configured pairs; no current_zscore col)
    """
    if not tickers:
        return {}

    sql = """
    SELECT
        t.ticker,
        -- Layer 9 Statistical Edge
        l9.statistical_score,
        l9.hurst_raw,
        l9.vpin_raw,
        l9.jump_detected,
        l9.entropy_score,
        l9.tail_score,
        l9.vrp_score,
        l9.amihud_score,
        l9.cs_spread_raw,
        l9.rnd_skew,
        l9.pca_factor1_var,
        -- Conviction Stack
        csw.total_pts   AS conviction_pts,
        csw.conviction_pct,
        csw.label       AS conviction_label,
        -- Options flow — unusual calls (7-day aggregate)
        ucl.prem_7d,
        ucl.vol_oi_avg,
        ucl.iv_avg,
        -- Options structure
        oss.gex_m,
        oss.gex_regime,
        oss.pc_skew_pp,
        oss.term_ratio,
        oss.front_iv,
        oss.back_iv,
        -- AI short calls (actual columns: vol_oi, prem, otm_pct, conviction)
        aisc.vol_oi     AS aisc_vol_oi,
        aisc.prem       AS aisc_prem,
        aisc.otm_pct    AS aisc_otm_pct,
        aisc.conviction AS aisc_conviction,
        -- Stat arb (fires only for configured pairs; stat_arb_pairs has no current_zscore)
        sap.coint_pvalue
    FROM UNNEST(%s::text[]) AS t(ticker)
    -- Layer 9: most recent scan_date up to scan_date param
    LEFT JOIN (
        SELECT DISTINCT ON (ticker)
            ticker, statistical_score, hurst_raw, vpin_raw, jump_detected,
            entropy_score, tail_score, vrp_score, amihud_score,
            cs_spread_raw, rnd_skew, pca_factor1_var
        FROM layer9_scores
        WHERE scan_date <= %s
        ORDER BY ticker, scan_date DESC
    ) l9 ON l9.ticker = t.ticker
    -- Conviction stack: most recent snap_date up to today
    LEFT JOIN (
        SELECT DISTINCT ON (ticker)
            ticker, total_pts, conviction_pct, label
        FROM conviction_stack_watchlist
        WHERE snap_date <= %s
        ORDER BY ticker, snap_date DESC
    ) csw ON csw.ticker = t.ticker
    -- Unusual calls: 7-day aggregate premium + avg vol_oi + avg iv
    LEFT JOIN (
        SELECT
            ticker,
            SUM(prem)   AS prem_7d,
            AVG(vol_oi) AS vol_oi_avg,
            AVG(iv)     AS iv_avg
        FROM unusual_calls_log
        WHERE last_seen >= NOW() - INTERVAL '7 days'
        GROUP BY ticker
    ) ucl ON ucl.ticker = t.ticker
    -- Options structure: most recent scan_date up to today
    LEFT JOIN (
        SELECT DISTINCT ON (ticker)
            ticker, gex_m, gex_regime, pc_skew_pp, term_ratio, front_iv, back_iv
        FROM options_structure_scan
        WHERE scan_date <= %s
        ORDER BY ticker, scan_date DESC
    ) oss ON oss.ticker = t.ticker
    -- AI short calls: most recent pick (actual columns only)
    LEFT JOIN (
        SELECT DISTINCT ON (ticker)
            ticker, vol_oi, prem, otm_pct, conviction
        FROM ai_short_calls_log
        WHERE trade_date >= CURRENT_DATE - INTERVAL '14 days'
        ORDER BY ticker, trade_date DESC
    ) aisc ON aisc.ticker = t.ticker
    -- Stat arb: fires when ticker is one side of an active pair
    LEFT JOIN (
        SELECT ticker_a AS ticker, coint_pvalue FROM stat_arb_pairs WHERE is_active = TRUE
        UNION ALL
        SELECT ticker_b AS ticker, coint_pvalue FROM stat_arb_pairs WHERE is_active = TRUE
    ) sap ON sap.ticker = t.ticker
    """

    def _f(v):
        return float(v) if v is not None else None

    def _b(v):
        return bool(v) if v is not None else None

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute("SET LOCAL statement_timeout = '30000'")
            cur.execute(sql, (tickers, scan_date, scan_date, scan_date))
            rows = cur.fetchall()
        return {r["ticker"]: {
            "statistical_score":   _f(r["statistical_score"]),
            "hurst_raw":           _f(r["hurst_raw"]),
            "vpin_raw":            _f(r["vpin_raw"]),
            "jump_detected":       _b(r["jump_detected"]),
            "entropy_score":       _f(r["entropy_score"]),
            "tail_score":          _f(r["tail_score"]),
            "vrp_score":           _f(r["vrp_score"]),
            "amihud_score":        _f(r["amihud_score"]),
            "cs_spread_raw":       _f(r["cs_spread_raw"]),
            "rnd_skew":            _f(r["rnd_skew"]),
            "pca_factor1_var":     _f(r["pca_factor1_var"]),
            "conviction_pts":      _f(r["conviction_pts"]),
            "conviction_pct":      _f(r["conviction_pct"]),
            "conviction_label":    r["conviction_label"],
            "prem_7d":             _f(r["prem_7d"]),
            "vol_oi_avg":          _f(r["vol_oi_avg"]),
            "iv_avg":              _f(r["iv_avg"]),
            "gex_m":               _f(r["gex_m"]),
            "gex_regime":          r["gex_regime"],
            "pc_skew_pp":          _f(r["pc_skew_pp"]),
            "term_ratio":          _f(r["term_ratio"]),
            "front_iv":            _f(r["front_iv"]),
            "back_iv":             _f(r["back_iv"]),
            "aisc_vol_oi":         _f(r["aisc_vol_oi"]),
            "aisc_prem":           _f(r["aisc_prem"]),
            "aisc_otm_pct":        _f(r["aisc_otm_pct"]),
            "aisc_conviction":     r["aisc_conviction"],
            "coint_pvalue":        _f(r["coint_pvalue"]),
        } for r in rows}
    except Exception as e:
        logger.error(f"[market_movers] enrich_features_batch error: {e}")
        return {}


def _get_regime_context() -> str:
    """
    Fetch current market regime once (market-wide, not per-ticker).
    Returns regime string like 'full_exposure' / 'reduce_exposure' / 'sit_out'.
    Falls back to 'unknown' on any error.
    """
    try:
        from regime_detector import get_current_regime
        regime = get_current_regime()
        return regime.get("recommendation", "unknown") if isinstance(regime, dict) else str(regime)
    except Exception:
        return "unknown"


# ─────────────────────────────────────────────────────────────────────────────
# 7. Main job entry point
# ─────────────────────────────────────────────────────────────────────────────

def run_daily_tiered_movers_job(top_n: int = 100, api_key: str = "") -> Dict[str, Any]:
    """
    Main entry point for the 17:10 ET Mon-Fri scheduler job.

    Steps:
      1. Find most recent trading date in polygon_market_daily.
      2. Compute daily pct_change for all tickers (LAG window).
      3. Assign market cap tiers from ticker_market_cap_cache.
         3a. For tickers not in cache: fetch live from Polygon (api_key required).
             This ensures correct tiering even before the nightly full refresh.
      4. Select top_n winners + bottom_n losers per tier (4×2×100 = 800 rows).
      5. Batch-enrich all 800 tickers with all DB-resident indicators.
      6. Add candlestick pattern flags (DB-only OHLCV math).
      7. Strip options keys for Nano Cap tickers (intentional exclusion).
      8. Upsert into daily_market_movers.

    Runtime: ~15–60s depending on how many tickers need live market cap fetches.
    Pure DB-only path (warm cache): ~10–15 seconds.
    """
    t0 = time.time()
    logger.info("[market_movers] daily tiered movers job started")

    try:
        conn = psycopg2.connect(_DB_URL, connect_timeout=10)
        conn.autocommit = False
    except Exception as e:
        logger.error(f"[market_movers] DB connect failed: {e}")
        return {"error": str(e)}

    try:
        # ── Step 1: Most recent trading date ─────────────────────────────────
        scan_date = _get_most_recent_trading_date(conn)
        if not scan_date:
            return {"error": "polygon_market_daily is empty"}

        # ── Step 2: Daily returns (LAG window) ────────────────────────────────
        returns = _compute_daily_returns(conn, scan_date)
        if not returns:
            return {"error": f"no daily return data for {scan_date}",
                    "scan_date": str(scan_date)}

        all_tickers = list(returns.keys())

        # ── Step 3: Market cap tier assignment ────────────────────────────────
        # First pass: read from cache (instant)
        tiers = _get_market_cap_tiers(conn, all_tickers)

        with conn.cursor() as _cc:
            _cc.execute(
                "SELECT COUNT(*) FROM ticker_market_cap_cache WHERE ticker = ANY(%s)",
                (all_tickers,)
            )
            cache_hits = _cc.fetchone()[0]

        # Step 3a: Live-fetch market caps for tickers NOT in cache WITH a real value.
        # Tickers in cache but with market_cap=NULL (from old broken batch endpoint) are
        # also treated as uncached and re-fetched here.
        # Sort by |pct_change| so the highest-moving tickers (the ones that will be
        # selected as movers) get their market caps fetched first.
        live_fetched = 0
        if api_key:
            cached_set = _get_market_cap_tiers_raw_set(conn)
            uncached = sorted(
                [t for t in all_tickers if t not in cached_set],
                key=lambda t: abs(returns[t].get("pct_change", 0.0)),
                reverse=True,
            )
            if uncached:
                # Cap at 800 per run; nightly refresh handles the full 7K universe.
                to_fetch = uncached[:800]
                logger.info(
                    f"[market_movers] live-fetching market caps for "
                    f"{len(to_fetch)} tickers (of {len(uncached)} uncached)"
                )
                live_caps = fetch_market_caps_for_tickers(to_fetch, api_key, num_workers=20)
                live_fetched = len([v for v in live_caps.values() if v is not None])
                # Re-read tiers now that the cache has real values
                tiers = _get_market_cap_tiers(conn, all_tickers)

        # ── Step 4: Per-tier winner/loser selection ───────────────────────────
        tier_movers = _build_tier_movers(returns, tiers, top_n=top_n)
        all_selected = []
        for tier, mv in tier_movers.items():
            all_selected.extend(mv["winners"])
            all_selected.extend(mv["losers"])

        selected_tickers = list({r["ticker"] for r in all_selected})
        if not selected_tickers:
            return {"error": "no movers selected — all tiers empty"}

        # ── Step 5: Batch feature enrichment (single SQL, all 800 tickers) ────
        enriched = _enrich_features_batch(conn, selected_tickers, scan_date)

        # ── Step 6: Candlestick patterns (OHLCV DB query + pure math) ─────────
        candles = _detect_candlestick_batch(conn, selected_tickers, scan_date)

        # ── Step 7: Regime context (once for all rows) ────────────────────────
        regime = _get_regime_context()

        # ── Step 8: Replace rows for this scan_date then insert ───────────────
        # DELETE first so re-runs of the same-day job don't leave stale rows
        # from a prior broken pass (especially the double-counted winner+loser rows
        # that existed before the disjoint fix in _build_tier_movers).
        insert_sql = """
        INSERT INTO daily_market_movers
            (scan_date, ticker, pct_change, close_price, rank,
             direction, market_cap_tier, feature_snapshot)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (scan_date, ticker, direction) DO UPDATE SET
            pct_change       = EXCLUDED.pct_change,
            close_price      = EXCLUDED.close_price,
            rank             = EXCLUDED.rank,
            market_cap_tier  = EXCLUDED.market_cap_tier,
            feature_snapshot = EXCLUDED.feature_snapshot,
            created_at       = NOW()
        """
        n_upserted = 0
        tier_counts: Dict[str, int] = {}

        with conn.cursor() as cur:
            cur.execute("DELETE FROM daily_market_movers WHERE scan_date = %s", (scan_date,))
            logger.info(f"[market_movers] cleared {cur.rowcount} stale rows for {scan_date}")
            for row in all_selected:
                ticker = row["ticker"]
                tier   = tiers.get(ticker, "nano")

                # Base OHLCV features (available for all tickers)
                snap: Dict[str, Any] = {
                    "pct_change":    row["pct_change"],
                    "close_price":   row["close_price"],
                    "open_price":    row.get("open_price"),
                    "high_price":    row.get("high_price"),
                    "low_price":     row.get("low_price"),
                    "volume":        row.get("volume"),
                    "close_strength":row.get("close_strength"),
                    "range_pct":     row.get("range_pct"),
                    "regime":        regime,
                }

                # Candlestick flags (OHLCV — available for all tickers)
                if ticker in candles:
                    snap.update(candles[ticker])

                # Sparse DB indicators (NULL where ticker not in watchlist)
                ind = enriched.get(ticker, {})
                snap.update(ind)

                # Strip options-dependent keys for Nano Cap (intentional exclusion)
                if tier == "nano":
                    for k in _OPTIONS_KEYS:
                        snap.pop(k, None)

                cur.execute(insert_sql, (
                    scan_date, ticker,
                    row["pct_change"], row["close_price"],
                    row["rank"], row["direction"], tier,
                    json.dumps(snap),
                ))
                n_upserted += 1
                tier_counts[tier] = tier_counts.get(tier, 0) + 1

        conn.commit()

        elapsed = round(time.time() - t0, 1)
        summary = {
            "scan_date":        str(scan_date),
            "universe_size":    len(returns),
            "cache_hits":       cache_hits,
            "live_fetched":     live_fetched,
            "selected":         len(selected_tickers),
            "upserted":         n_upserted,
            "by_tier":          tier_counts,
            "enriched":         len(enriched),
            "candlestick_hits": len(candles),
            "elapsed_s":        elapsed,
        }
        logger.info(f"[market_movers] done: {summary}")
        return summary

    except Exception as e:
        conn.rollback()
        logger.error(f"[market_movers] job error: {e}")
        return {"error": str(e)}
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# 8. Discovery Engine feed
# ─────────────────────────────────────────────────────────────────────────────

def get_wl_rows_for_engine(
    tier: str,
    min_days: int = 5,
) -> List[Dict]:
    """
    Load all daily_market_movers rows for a given tier with feature_snapshot
    populated. Returns flat list of dicts with feature fields + is_winner bool.
    Returns [] when fewer than min_days of data exist for this tier.
    """
    sql = """
    SELECT ticker, scan_date, direction, pct_change, feature_snapshot
    FROM daily_market_movers
    WHERE market_cap_tier = %s
      AND feature_snapshot IS NOT NULL
    ORDER BY scan_date, ticker
    """
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=5) as conn, \
             conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(sql, (tier,))
            rows = cur.fetchall()

        if not rows:
            return []

        days = len(set(str(r["scan_date"]) for r in rows))
        if days < min_days:
            logger.info(
                f"[market_movers] tier={tier}: only {days} days of data "
                f"(need {min_days}) — WL cycle skipped for this tier"
            )
            return []

        result = []
        for r in rows:
            snap = r["feature_snapshot"] or {}
            flat = {
                "ticker":    r["ticker"],
                "scan_date": str(r["scan_date"]),
                "direction": r["direction"],
                "pct_change":float(r["pct_change"] or 0.0),
                "is_winner": r["direction"] == "winner",
                **snap,
            }
            result.append(flat)
        return result

    except Exception as e:
        logger.error(f"[market_movers] get_wl_rows error (tier={tier}): {e}")
        return []
