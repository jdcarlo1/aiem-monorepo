"""
aiem_optprob.py  —  Standalone deep-ITM options-probability scanner for AIEM.

Zero dependency on main.py / Flask / the website backend.
Owns its own Tradier API calls, Black-Scholes math, universe pre-filter,
DB writes, and Telegram digest.  main.py's Quant-tab calculator is a
completely separate copy that lives only in that process.

Public API (called from aiem_process.py):
  init_optprob_table(db_url)
  get_optprob_universe(db_url)            -> list[str]
  compute_options_probability_matrix(ticker, hold_days, max_dte) -> dict
  run_optprob_deep_itm_scan(db_url, tg_send, cursor_state)
  run_optprob_daily_digest(db_url, tg_send)
"""

import math
import os
import threading
from datetime import datetime, date, timedelta
from types import SimpleNamespace

import pytz
import requests

ET = pytz.timezone("America/New_York")

# ── Tradier auth ──────────────────────────────────────────────────────────────

def _tradier_token() -> str:
    return (os.environ.get("TRADIER_API_TOKEN_2") or
            os.environ.get("TRADIER_API_TOKEN") or "")

def _tradier_headers() -> dict:
    tok = _tradier_token()
    if not tok:
        return {}
    return {"Authorization": f"Bearer {tok}", "Accept": "application/json"}


# ── Simple per-process caches (no sharing with main.py) ──────────────────────

_tde_cache: dict = {}   # expiry dates:  {ticker: (exps, ts)}
_tdc_cache: dict = {}   # option chains: {(ticker,expiry): (df_c, df_p, ts)}
_cache_lock = threading.Lock()
_CACHE_TTL  = 300       # 5-min TTL — chains don't move fast intraday


# ── Tradier helpers ───────────────────────────────────────────────────────────

def _td_quotes(symbols: list) -> dict:
    """Batch real-time quotes.  {SYM: {last, prevclose, bid, ask, …}} or {}."""
    hdrs = _tradier_headers()
    if not hdrs or not symbols:
        return {}
    try:
        r = requests.get(
            "https://api.tradier.com/v1/markets/quotes",
            params={"symbols": ",".join(str(s) for s in symbols[:200])},
            headers=hdrs, timeout=6,
        )
        if r.status_code != 200:
            return {}
        raw = r.json().get("quotes", {}).get("quote", [])
        if isinstance(raw, dict):
            raw = [raw]
        return {
            q["symbol"]: {
                "last":       float(q.get("last") or 0),
                "prevclose":  float(q.get("prevclose") or 0),
                "bid":        float(q.get("bid") or 0),
                "ask":        float(q.get("ask") or 0),
            }
            for q in raw if q.get("symbol")
        }
    except Exception as exc:
        print(f"[aiem_optprob] td_quotes error: {exc}")
        return {}


def _td_expiries(ticker: str, max_days: int = 10) -> list:
    """Nearest option expiry dates within max_days.  Cached 5 min."""
    hdrs = _tradier_headers()
    if not hdrs:
        return []
    ticker = ticker.upper()
    import time as _t
    now = _t.time()
    with _cache_lock:
        cached = _tde_cache.get(ticker)
        if cached and now - cached[1] < _CACHE_TTL:
            return cached[0]
    try:
        r = requests.get(
            "https://api.tradier.com/v1/markets/options/expirations",
            params={"symbol": ticker, "includeAllRoots": "false"},
            headers=hdrs, timeout=5,
        )
        if r.status_code != 200:
            return []
        raw = (r.json().get("expirations") or {}).get("date") or []
        if isinstance(raw, str):
            raw = [raw]
        today = datetime.now()
        exps = [e for e in raw
                if 0 < (datetime.strptime(e, "%Y-%m-%d") - today).days <= max_days]
        with _cache_lock:
            _tde_cache[ticker] = (exps, now)
        return exps
    except Exception as exc:
        print(f"[aiem_optprob] td_expiries {ticker}: {exc}")
        return []


def _td_chain(ticker: str, expiry: str):
    """Option chain for one expiry (calls + puts with greeks).  Cached 5 min."""
    import time as _t, pandas as _pd
    hdrs = _tradier_headers()
    empty = SimpleNamespace(calls=_pd.DataFrame(), puts=_pd.DataFrame())
    if not hdrs:
        return empty
    ticker = ticker.upper()
    key = (ticker, expiry)
    now = _t.time()
    with _cache_lock:
        cached = _tdc_cache.get(key)
        if cached and now - cached[2] < _CACHE_TTL:
            return SimpleNamespace(calls=cached[0], puts=cached[1])
    try:
        r = requests.get(
            "https://api.tradier.com/v1/markets/options/chains",
            params={"symbol": ticker, "expiration": expiry, "greeks": "true"},
            headers=hdrs, timeout=7,
        )
        if r.status_code != 200:
            return empty
        opts = (r.json().get("options") or {}).get("option") or []
        if isinstance(opts, dict):
            opts = [opts]
        if not opts:
            return empty
        rows_c, rows_p = [], []
        for o in opts:
            strike = float(o.get("strike") or 0)
            if not strike:
                continue
            bid = float(o.get("bid") or 0)
            ask = float(o.get("ask") or 0)
            mid = (bid + ask) / 2 if bid and ask else float(o.get("last") or 0)
            _g = o.get("greeks") or {}
            _delta = _g.get("delta")
            row = {
                "strike":            strike,
                "lastPrice":         mid,
                "bid":               bid,
                "ask":               ask,
                "volume":            int(o.get("volume") or 0),
                "openInterest":      int(o.get("open_interest") or 0),
                "impliedVolatility": float(_g.get("mid_iv") or 0),
                "delta":             float(_delta) if _delta not in (None, "") else None,
                "expiration":        expiry,
            }
            (rows_c if o.get("option_type") == "call" else rows_p).append(row)
        df_c = _pd.DataFrame(rows_c) if rows_c else _pd.DataFrame()
        df_p = _pd.DataFrame(rows_p) if rows_p else _pd.DataFrame()
        with _cache_lock:
            _tdc_cache[key] = (df_c, df_p, now)
        return SimpleNamespace(calls=df_c, puts=df_p)
    except Exception as exc:
        print(f"[aiem_optprob] td_chain {ticker} {expiry}: {exc}")
        return empty


def _td_history(ticker: str, days: int = 45) -> "pd.DataFrame":
    """Daily OHLCV from Tradier.  Used only for historical-vol fallback."""
    import pandas as _pd
    hdrs = _tradier_headers()
    if not hdrs:
        return _pd.DataFrame()
    start = (datetime.now() - timedelta(days=days + 14)).strftime("%Y-%m-%d")
    end   = datetime.now().strftime("%Y-%m-%d")
    try:
        r = requests.get(
            "https://api.tradier.com/v1/markets/history",
            params={"symbol": ticker.upper(), "interval": "daily",
                    "start": start, "end": end},
            headers=hdrs, timeout=8,
        )
        if r.status_code != 200:
            return _pd.DataFrame()
        raw = (r.json().get("history") or {}).get("day") or []
        if isinstance(raw, dict):
            raw = [raw]
        if not raw:
            return _pd.DataFrame()
        df = _pd.DataFrame(raw)
        df["date"] = _pd.to_datetime(df["date"])
        df = df.set_index("date").rename(columns={
            "open": "Open", "high": "High", "low": "Low",
            "close": "Close", "volume": "Volume",
        })
        for col in ["Open", "High", "Low", "Close", "Volume"]:
            if col in df.columns:
                df[col] = _pd.to_numeric(df[col], errors="coerce")
        return df.sort_index()
    except Exception as exc:
        print(f"[aiem_optprob] td_history {ticker}: {exc}")
        return __import__("pandas").DataFrame()


# ── Black-Scholes ─────────────────────────────────────────────────────────────

def _bs_call_price(S, K, T, sigma, r=0.045):
    from scipy.stats import norm as _norm
    if T <= 0 or sigma <= 0:
        return max(S - K, 0.0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return S * _norm.cdf(d1) - K * math.exp(-r * T) * _norm.cdf(d2)


def _win_prob(S, K, sigma_pct, days, r=0.045) -> float:
    """P(spot > strike after hold_days) via Black-Scholes d2.  Returns 0-100."""
    from scipy.stats import norm as _norm
    sigma = sigma_pct / 100.0
    T = days / 365.0
    if T <= 0 or sigma <= 0:
        return 100.0 if S > K else 0.0
    d2 = (math.log(S / K) + (r - 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    return round(float(_norm.cdf(d2)) * 100, 2)


def _holding_bep(K, T_remaining, sigma_pct, premium, spot_px):
    """Numerical break-even: spot at which residual value = premium paid."""
    from scipy.optimize import brentq as _brentq
    sigma = sigma_pct / 100.0
    if sigma <= 0 or T_remaining <= 0:
        return None
    def f(S_):
        return _bs_call_price(S_, K, T_remaining, sigma) - premium
    lo, hi = 0.01, spot_px * 6
    try:
        if f(lo) > 0 or f(hi) < 0:
            return None
        return round(_brentq(f, lo, hi), 2)
    except Exception:
        return None


# ── Core computation ──────────────────────────────────────────────────────────

def compute_options_probability_matrix(
    ticker: str,
    hold_days: int = 2,
    max_dte:   int = 7,
    pre_verified_avg_vol: int = None,
) -> dict:
    """
    Real-data options probability matrix.  Returns {"error": str} on failure —
    never returns fabricated data.

    pre_verified_avg_vol: if the caller already confirmed avg_vol_30d via a
    DB pre-filter (e.g. get_optprob_universe), pass it here so the liquidity
    gate skips the "underlying volume" re-check.  Pass None to re-check.
    """
    import numpy as _np

    ticker = (ticker or "").upper().strip()
    if not ticker:
        return {"error": "No ticker provided."}

    quotes = _td_quotes([ticker])
    q = quotes.get(ticker) or {}
    spot = q.get("last") or q.get("prevclose") or 0.0
    if not spot:
        return {"error": f"No live price for {ticker} from Tradier."}

    expiries = _td_expiries(ticker, max_days=max_dte)
    if not expiries:
        return {"error": f"No option expiry within {max_dte} days for {ticker}."}
    expiry = expiries[0]
    dte = (datetime.strptime(expiry, "%Y-%m-%d").date() - date.today()).days
    hold_days = max(1, min(hold_days, dte)) if dte > 0 else 1

    chain = _td_chain(ticker, expiry)
    calls = chain.calls.copy() if chain.calls is not None else None
    if calls is None or calls.empty:
        return {"error": f"No call chain for {ticker} {expiry}."}

    # Historical vol fallback (used only when chain IV is missing/zero)
    hist_iv = None
    try:
        hist = _td_history(ticker, days=45)
        if hist is not None and not hist.empty and "Close" in hist.columns:
            closes = hist["Close"].dropna().to_numpy(dtype=float)
            if len(closes) >= 2:
                log_ret = _np.log(closes[1:] / closes[:-1])
                hist_iv = float(_np.std(log_ret) * _np.sqrt(252) * 100)
    except Exception as exc:
        print(f"[aiem_optprob] hist vol fallback {ticker}: {exc}")

    rows = []
    iv_source = "chain"

    def _build_row(row_s, depth_pct, extra=None):
        nonlocal iv_source
        strike  = float(row_s["strike"])
        bid     = float(row_s.get("bid") or 0)
        ask     = float(row_s.get("ask") or 0)
        premium = float(row_s.get("lastPrice") or 0)
        if not premium:
            premium = (bid + ask) / 2 if bid and ask else 0.0

        chain_iv = float(row_s.get("impliedVolatility") or 0) * 100
        if chain_iv > 1:
            iv_used = chain_iv
        else:
            iv_used    = hist_iv or 0.0
            iv_source  = "historical"

        base = {"depth_pct": depth_pct, "strike": strike}
        if extra:
            base.update(extra)

        if premium <= 0:
            base.update({
                "premium": None, "expiration_bep": None, "holding_bep": None,
                "win_probability": None, "iv_used": round(iv_used, 1),
                "note": "No live quote for this strike.",
                "liquidity": None, "verdict": "NO_QUOTE", "suggested_limit": None,
            })
            return base

        expiration_bep   = round(strike + premium, 2)
        T_remaining_days = max(dte - hold_days, 0)
        if T_remaining_days > 0 and iv_used > 0:
            h_bep = _holding_bep(strike, T_remaining_days / 365.0, iv_used, premium, spot)
        else:
            h_bep = expiration_bep

        open_interest = int(row_s.get("openInterest") or 0)
        opt_volume    = int(row_s.get("volume") or 0)
        mid           = (bid + ask) / 2 if bid and ask else None
        spread_pct    = round((ask - bid) / mid * 100, 2) if (mid and ask >= bid) else None

        reject_reasons = []
        if pre_verified_avg_vol is None or pre_verified_avg_vol < 2_000_000:
            reject_reasons.append("Underlying 30d avg volume below 2,000,000 shares")
        if open_interest < 500:
            reject_reasons.append("Option open interest below 500 contracts")
        if spread_pct is None or spread_pct > 1.5:
            reject_reasons.append("Bid-ask spread above 1.5% of midpoint")

        liquidity = {
            "avg_vol_30d":          pre_verified_avg_vol,
            "option_open_interest": open_interest,
            "spread_pct":           spread_pct,
            "passed":               len(reject_reasons) == 0,
            "reject_reasons":       reject_reasons,
        }
        verdict        = "TRADEABLE" if liquidity["passed"] else "REJECTED_ILLIQUID"
        suggested_limit = round(mid, 2) if (liquidity["passed"] and mid) else None

        base.update({
            "premium":         round(premium, 2),
            "bid":             round(bid, 2),
            "ask":             round(ask, 2),
            "expiration_bep":  expiration_bep,
            "holding_bep":     h_bep,
            "win_probability": _win_prob(spot, strike, iv_used, hold_days),
            "iv_used":         round(iv_used, 1),
            "volume":          opt_volume,
            "open_interest":   open_interest,
            "liquidity":       liquidity,
            "verdict":         verdict,
            "suggested_limit": suggested_limit,
        })
        return base

    # Depth rows (5 / 10 / 15 % ITM)
    for pct in (5, 10, 15):
        target = round(spot * (1 - pct / 100.0), 2)
        calls["_dist"] = (calls["strike"] - target).abs()
        sel = calls.loc[calls["_dist"].idxmin()]
        rows.append(_build_row(sel, pct))

    # Deep-ITM delta-target row (0.80 delta) — omitted honestly if no delta data
    target_delta = 0.80
    if "delta" in calls.columns:
        valid = calls[calls["delta"].notna()]
        if not valid.empty:
            valid = valid.copy()
            valid["_ddist"] = (valid["delta"].astype(float) - target_delta).abs()
            dsel = valid.loc[valid["_ddist"].idxmin()]
            actual_delta  = float(dsel["delta"])
            deep_itm_pct  = round((1 - float(dsel["strike"]) / spot) * 100, 1)
            rows.append(_build_row(dsel, deep_itm_pct, extra={
                "strategy":     "deep_itm_delta_target",
                "target_delta": target_delta,
                "delta":        round(actual_delta, 3),
            }))

    return {
        "ticker":         ticker,
        "spot_price":     round(float(spot), 2),
        "expiry":         expiry,
        "days_to_expiry": dte,
        "hold_days":      hold_days,
        "iv_source":      iv_source,
        "rows":           rows,
        "generated_at":   datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


# ── Universe pre-filter ───────────────────────────────────────────────────────

_UNIVERSE_CACHE: dict = {"date": None, "tickers": []}
_UNIVERSE_LOCK = threading.Lock()


def get_optprob_universe(db_url: str) -> list:
    """
    Full scan universe: tickers in DEFAULT_LEADERBOARD (~6,635 liquid,
    options-active names) that also have 30d avg volume >= 2,000,000 in
    polygon_market_daily.  Single DB query — zero live API calls.
    Cached once per ET calendar day.
    """
    import psycopg2

    today = datetime.now(ET).strftime("%Y-%m-%d")
    with _UNIVERSE_LOCK:
        if _UNIVERSE_CACHE["date"] == today and _UNIVERSE_CACHE["tickers"]:
            return _UNIVERSE_CACHE["tickers"]

    try:
        from smart_money import DEFAULT_LEADERBOARD
    except Exception as exc:
        print(f"[aiem_optprob] could not import DEFAULT_LEADERBOARD: {exc}")
        return []

    try:
        with psycopg2.connect(db_url) as conn, conn.cursor() as cur:
            cur.execute("""
                WITH latest AS (SELECT MAX(scan_date) AS d FROM polygon_market_daily),
                avg30 AS (
                    SELECT ticker, AVG(volume) AS avg_vol_30d
                    FROM polygon_market_daily, latest
                    WHERE scan_date > latest.d - INTERVAL '45 days'
                    GROUP BY ticker
                    HAVING COUNT(*) >= 15
                )
                SELECT ticker FROM avg30 WHERE avg_vol_30d >= 2000000
            """)
            liquid = {row[0] for row in cur.fetchall()}
        universe = sorted(liquid & set(DEFAULT_LEADERBOARD))
        with _UNIVERSE_LOCK:
            _UNIVERSE_CACHE["date"]    = today
            _UNIVERSE_CACHE["tickers"] = universe
        print(f"[aiem_optprob] universe: {len(universe)} tickers "
              f"(of {len(DEFAULT_LEADERBOARD)} leaderboard names pass volume gate)")
        return universe
    except Exception as exc:
        print(f"[aiem_optprob] get_optprob_universe error: {exc}")
        return []


# ── DB table ──────────────────────────────────────────────────────────────────

def init_optprob_table(db_url: str) -> None:
    """Create options_prob_deep_itm_daily table if it doesn't exist (idempotent)."""
    import psycopg2
    try:
        with psycopg2.connect(db_url) as conn, conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS options_prob_deep_itm_daily (
                    id              SERIAL PRIMARY KEY,
                    scan_date       DATE        NOT NULL,
                    ticker          TEXT        NOT NULL,
                    spot_price      NUMERIC,
                    strike          NUMERIC,
                    delta           NUMERIC,
                    win_probability NUMERIC,
                    premium         NUMERIC,
                    suggested_limit NUMERIC,
                    expiry          TEXT,
                    days_to_expiry  INTEGER,
                    open_interest   INTEGER,
                    spread_pct      NUMERIC,
                    edge_points     INTEGER,
                    edge_max_points INTEGER,
                    first_seen      TIMESTAMPTZ DEFAULT NOW(),
                    last_seen       TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE (scan_date, ticker)
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_optprob_deep_itm_date
                ON options_prob_deep_itm_daily (scan_date)
            """)
        print("[aiem_optprob] options_prob_deep_itm_daily table ready")
    except Exception as exc:
        print(f"[aiem_optprob] table init error: {exc}")


# ── Scan job ──────────────────────────────────────────────────────────────────

def run_optprob_deep_itm_scan(
    db_url:       str,
    tg_send,
    cursor_state: dict,
    label:        str = "segment",
) -> None:
    """
    Scan one rotating segment of the universe for deep-ITM call opportunities.
    cursor_state is a mutable dict {"cursor": int} owned by the caller
    (aiem_process.py) so state persists across the 6 daily runs.

    Gates:
      • win_probability >= 75%
      • liquidity.passed (OI >= 500, spread <= 1.5%, vol pre-verified)
      • delta is not None (no fabrication)

    Results are upserted into options_prob_deep_itm_daily (keep-higher-win-prob
    on conflict) so any ticker hit across multiple segment runs keeps its best
    reading for the day's digest.
    """
    import psycopg2
    from concurrent.futures import ThreadPoolExecutor, as_completed

    job_name = "aiem_optprob_deep_itm_scan"
    now_et = datetime.now(ET)
    if now_et.weekday() >= 5:
        print(f"[{job_name}] {label} skipped (weekend)")
        return

    universe = get_optprob_universe(db_url)
    if not universe:
        print(f"[{job_name}] {label} empty universe - skipping")
        return

    n      = len(universe)
    seg_sz = max(1, -(-n // 6))             # ceil(n/6) - full coverage in 6 runs
    start  = cursor_state.get("cursor", 0) % n
    end    = min(start + seg_sz, n)
    cursor_state["cursor"] = end % n        # advance for next run
    segment = universe[start:end]

    print(f"[{job_name}] {label} scanning {len(segment)} of {n} tickers "
          f"(segment [{start}:{end}])")

    def _scan_one(ticker: str):
        try:
            result = compute_options_probability_matrix(
                ticker, hold_days=2, max_dte=7,
                pre_verified_avg_vol=2_000_001,  # confirmed by universe pre-filter
            )
        except Exception as exc:
            print(f"[{job_name}] {ticker} error: {exc}")
            return None
        if "error" in result:
            return None
        for row in result.get("rows", []):
            if row.get("strategy") != "deep_itm_delta_target":
                continue
            if row.get("delta") is None:
                return None  # honestly skip — no delta data
            wp  = row.get("win_probability")
            liq = row.get("liquidity")
            if wp is None or wp < 75 or not liq or not liq.get("passed"):
                return None
            return {
                "ticker":          result["ticker"],
                "spot_price":      result["spot_price"],
                "strike":          row["strike"],
                "delta":           row.get("delta"),
                "win_probability": wp,
                "premium":         row.get("premium"),
                "suggested_limit": row.get("suggested_limit"),
                "expiry":          result["expiry"],
                "days_to_expiry":  result["days_to_expiry"],
                "open_interest":   (liq.get("option_open_interest") or 0),
                "spread_pct":      liq.get("spread_pct"),
            }
        return None

    candidates = []
    with ThreadPoolExecutor(max_workers=2) as ex:
        futs = {ex.submit(_scan_one, t): t for t in segment}
        done = 0
        try:
            for fut in as_completed(futs, timeout=600):
                try:
                    r = fut.result()
                    if r:
                        candidates.append(r)
                    done += 1
                except Exception as exc:
                    print(f"[{job_name}] worker error: {exc}")
        except TimeoutError:
            for f in futs:
                f.cancel()
            print(f"[{job_name}] {label} 600s timeout — "
                  f"{done}/{len(segment)} tickers done, {len(candidates)} partial")

    if candidates:
        et_today = datetime.now(ET).strftime("%Y-%m-%d")
        try:
            with psycopg2.connect(db_url) as conn, conn.cursor() as cur:
                for c in candidates:
                    cur.execute("""
                        INSERT INTO options_prob_deep_itm_daily
                            (scan_date, ticker, spot_price, strike, delta,
                             win_probability, premium, suggested_limit, expiry,
                             days_to_expiry, open_interest, spread_pct,
                             edge_points, edge_max_points, last_seen)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, NOW())
                        ON CONFLICT (scan_date, ticker) DO UPDATE SET
                            win_probability = GREATEST(
                                options_prob_deep_itm_daily.win_probability,
                                EXCLUDED.win_probability),
                            spot_price      = EXCLUDED.spot_price,
                            strike          = EXCLUDED.strike,
                            delta           = EXCLUDED.delta,
                            premium         = EXCLUDED.premium,
                            suggested_limit = EXCLUDED.suggested_limit,
                            expiry          = EXCLUDED.expiry,
                            days_to_expiry  = EXCLUDED.days_to_expiry,
                            open_interest   = EXCLUDED.open_interest,
                            spread_pct      = EXCLUDED.spread_pct,
                            last_seen       = NOW()
                    """, (
                        et_today, c["ticker"], c["spot_price"], c["strike"],
                        c["delta"], c["win_probability"], c["premium"],
                        c["suggested_limit"], c["expiry"], c["days_to_expiry"],
                        c["open_interest"], c["spread_pct"], None, None,
                    ))
        except Exception as exc:
            print(f"[{job_name}] DB write error: {exc}")

    print(f"[{job_name}] {label} done — "
          f"{len(candidates)} candidate(s) >=75% win-prob + liquidity PASS")


# ── Daily digest ──────────────────────────────────────────────────────────────

def run_optprob_daily_digest(db_url: str, tg_send) -> None:
    """
    Pull today's top-20 deep-ITM candidates from DB and send ONE Telegram
    message.  Called once at 4:10 PM ET from aiem_process.py's scheduler.
    """
    import psycopg2

    job_name = "aiem_optprob_daily_digest"
    try:
        et_today = datetime.now(ET).strftime("%Y-%m-%d")
        with psycopg2.connect(db_url) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT ticker, spot_price, strike, delta, win_probability,
                       premium, suggested_limit, expiry, days_to_expiry,
                       open_interest, spread_pct
                FROM options_prob_deep_itm_daily
                WHERE scan_date = %s
                ORDER BY win_probability DESC
                LIMIT 20
            """, (et_today,))
            rows = cur.fetchall()

        if not rows:
            tg_send(
                "\U0001F4CA AIEM Options Digest\n"
                "No deep-ITM candidates cleared the 75% win-probability + "
                "liquidity bar today across the full scanned universe.",
            )
            print(f"[{job_name}] 0 candidates — sent empty-day note")
            return

        lines = [
            f"\U0001F4CA AIEM Deep-ITM Options Digest — "
            f"Top {len(rows)} (>=75% win prob, liquidity PASS)"
        ]
        for (tkr, spot, strike, delta, wp, prem, sug, expiry, dte, oi, spread) in rows:
            line = (f"• {tkr}: {float(wp):.1f}% win "
                    f"· \u03B4{float(delta):.2f} "
                    f"· Strike ${float(strike):.2f} (spot ${float(spot):.2f}) "
                    f"· Exp {expiry} ({dte}d)")
            if sug is not None:
                line += f" · Limit ${float(sug):.2f}"
            if oi:
                line += f" · OI {int(oi):,}"
            lines.append(line)
        lines.append("Not financial advice — informational only.")

        tg_send("\n".join(lines))
        print(f"[{job_name}] sent digest with {len(rows)} candidate(s)")
    except Exception as exc:
        import traceback
        print(f"[{job_name}] error: {exc}\n{traceback.format_exc()}")
