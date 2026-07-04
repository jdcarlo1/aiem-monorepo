"""
aiem_options_structure.py — GEX, Put/Call Skew, Options Term Structure
Computes institutional dealer-positioning signals from Tradier options chains.

• GEX  (Gamma Exposure): net dealer gamma in $M — positive=suppressive, negative=amplifying
• Skew (Put/Call Skew) : OTM 25-delta put IV minus call IV in pp; FEAR_PREMIUM | NORMAL | CALL_SKEW
• Term (Term Structure) : front-month ATM IV / next-month ATM IV ratio; INVERTED | NORMAL | CONTANGO
"""
import os, math, time, threading, requests as _req_os

_HEADERS_CACHE: dict = {}
_HEADERS_LOCK = threading.Lock()
_TDC_TTL = 300  # 5-minute chain cache


def _td_auth_headers() -> dict:
    """Return Tradier auth headers from env, prefer primary token."""
    tok = os.environ.get("TRADIER_API_TOKEN") or os.environ.get("TRADIER_API_TOKEN_2", "")
    if not tok:
        return {}
    return {"Authorization": f"Bearer {tok}", "Accept": "application/json"}


_chain_cache: dict = {}
_chain_lock = threading.Lock()


def _fetch_chain_with_greeks(ticker: str, expiry: str) -> list:
    """
    Pull raw option chain from Tradier with full greeks (including gamma).
    Returns list of option dicts or [].
    Caches for _TDC_TTL seconds.
    """
    key = (ticker.upper(), expiry)
    now = time.time()
    with _chain_lock:
        cached = _chain_cache.get(key)
        if cached and now - cached[1] < _TDC_TTL:
            return cached[0]

    hdrs = _td_auth_headers()
    if not hdrs:
        return []
    try:
        r = _req_os.get(
            "https://api.tradier.com/v1/markets/options/chains",
            params={"symbol": ticker.upper(), "expiration": expiry, "greeks": "true"},
            headers=hdrs, timeout=6,
        )
        if r.status_code != 200:
            return []
        opts = (r.json().get("options") or {}).get("option") or []
        if isinstance(opts, dict):
            opts = [opts]
        with _chain_lock:
            _chain_cache[key] = (opts, now)
        return opts
    except Exception:
        return []


def _fetch_expiries(ticker: str) -> list:
    """Return list of expiry date strings from Tradier."""
    hdrs = _td_auth_headers()
    if not hdrs:
        return []
    try:
        r = _req_os.get(
            "https://api.tradier.com/v1/markets/options/expirations",
            params={"symbol": ticker.upper(), "includeAllRoots": "true"},
            headers=hdrs, timeout=4,
        )
        if r.status_code != 200:
            return []
        dates = (r.json().get("expirations") or {}).get("date") or []
        if isinstance(dates, str):
            dates = [dates]
        return [str(d) for d in dates if d]
    except Exception:
        return []


def _spot_from_tradier(ticker: str) -> float | None:
    """Get current price from Tradier quotes."""
    hdrs = _td_auth_headers()
    if not hdrs:
        return None
    try:
        r = _req_os.get(
            "https://api.tradier.com/v1/markets/quotes",
            params={"symbols": ticker.upper(), "greeks": "false"},
            headers=hdrs, timeout=4,
        )
        if r.status_code != 200:
            return None
        q = (r.json().get("quotes") or {}).get("quote") or {}
        if isinstance(q, list):
            q = q[0] if q else {}
        v = q.get("last") or q.get("bid")
        return float(v) if v else None
    except Exception:
        return None


# ── GEX ───────────────────────────────────────────────────────────────────────

def _compute_gex(opts: list, spot: float, weight: float = 1.0) -> tuple:
    """
    Compute net dealer Gamma Exposure from one expiry's chain.

    Standard Black-Scholes GEX:
        Per-contract GEX = gamma × OI × 100 × spot²/100
        Calls: dealer SHORT gamma → negative contribution
        Puts : dealer LONG  gamma → positive contribution
        Net GEX = sum(put GEX) - sum(call GEX)

    Returns (net_gex_millions, n_calls, n_puts).
    Tradier provides gamma in the greeks object when greeks=true.
    """
    gex = 0.0
    n_calls = n_puts = 0
    for o in opts:
        oi = int(o.get("open_interest") or 0)
        if oi <= 0:
            continue
        g_raw = (o.get("greeks") or {}).get("gamma")
        if g_raw is None:
            # Fall back: approximate gamma from IV + moneyness (Bell-curve kernel)
            iv_raw = float((o.get("greeks") or {}).get("mid_iv") or 0)
            if iv_raw <= 0:
                continue
            strike = float(o.get("strike") or 0)
            if strike <= 0 or spot <= 0:
                continue
            moneyness = abs(strike - spot) / spot
            gamma = math.exp(-0.5 * (moneyness / (iv_raw + 0.01)) ** 2) * 0.05
        else:
            gamma = float(g_raw)

        contract_gex = gamma * oi * 100 * (spot ** 2) / 100  # dollars
        opt_type = str(o.get("option_type") or "").lower()
        if opt_type == "call":
            gex -= contract_gex  # dealer short call gamma → suppresses upside moves
            n_calls += 1
        elif opt_type == "put":
            gex += contract_gex  # dealer long put gamma → suppresses downside moves
            n_puts += 1

    gex_m = (gex * weight) / 1_000_000
    return gex_m, n_calls, n_puts


def _gamma_flip_price(opts: list, spot: float) -> float | None:
    """
    Estimate the price level where net GEX crosses zero (gamma flip).
    Method: find the strike where cumulative call OI ≈ cumulative put OI.
    This is where dealer hedging pressure flips from suppressive to amplifying.
    """
    try:
        from collections import defaultdict
        strike_call_oi: dict = defaultdict(float)
        strike_put_oi:  dict = defaultdict(float)
        for o in opts:
            oi = float(o.get("open_interest") or 0)
            strike = float(o.get("strike") or 0)
            if oi <= 0 or strike <= 0:
                continue
            opt_type = str(o.get("option_type") or "").lower()
            if opt_type == "call":
                strike_call_oi[strike] += oi
            elif opt_type == "put":
                strike_put_oi[strike] += oi

        all_strikes = sorted(set(strike_call_oi) | set(strike_put_oi))
        if not all_strikes:
            return None

        min_diff = float("inf")
        flip = None
        for s in all_strikes:
            diff = abs(strike_call_oi[s] - strike_put_oi[s])
            if diff < min_diff:
                min_diff = diff
                flip = s
        return float(flip) if flip else None
    except Exception:
        return None


# ── Put/Call Skew ─────────────────────────────────────────────────────────────

def _compute_skew(opts: list, spot: float) -> tuple:
    """
    Compute 25-delta Put/Call Skew.
    Uses OTM put IV (strike ~25% below ATM proxy) minus OTM call IV.
    Positive skew = FEAR_PREMIUM (market paying up for downside protection).
    Returns (skew_pp, put_iv, call_iv, tag).
    """
    otm_puts  = [(float(o.get("strike") or 0),
                  float((o.get("greeks") or {}).get("mid_iv") or 0) * 100)
                 for o in opts
                 if str(o.get("option_type", "")).lower() == "put"
                 and float(o.get("strike") or 0) < spot * 0.97
                 and float((o.get("greeks") or {}).get("mid_iv") or 0) > 0]

    otm_calls = [(float(o.get("strike") or 0),
                  float((o.get("greeks") or {}).get("mid_iv") or 0) * 100)
                 for o in opts
                 if str(o.get("option_type", "")).lower() == "call"
                 and float(o.get("strike") or 0) > spot * 1.03
                 and float((o.get("greeks") or {}).get("mid_iv") or 0) > 0]

    if not otm_puts or not otm_calls:
        return None, None, None, None

    # Target: ~25% OTM (25-delta proxy)
    target_put_dist  = spot * 0.08   # ~8% OTM put ≈ 25-delta for typical 30-day vol
    target_call_dist = spot * 0.08

    best_put  = min(otm_puts,  key=lambda x: abs(spot - x[0] - target_put_dist))
    best_call = min(otm_calls, key=lambda x: abs(x[0] - spot - target_call_dist))

    put_iv  = best_put[1]
    call_iv = best_call[1]

    if call_iv <= 0:
        return None, None, None, None

    skew_pp = put_iv - call_iv
    if skew_pp > 8:
        tag = "FEAR_PREMIUM"
    elif skew_pp < -3:
        tag = "CALL_SKEW"
    else:
        tag = "NORMAL"

    return round(skew_pp, 2), round(put_iv, 2), round(call_iv, 2), tag


# ── Term Structure ─────────────────────────────────────────────────────────────

def _atm_iv(opts: list, spot: float) -> float | None:
    """ATM IV = average IV of the 3 strikes nearest to spot (both calls)."""
    calls = [(float(o.get("strike") or 0),
              float((o.get("greeks") or {}).get("mid_iv") or 0) * 100)
             for o in opts
             if str(o.get("option_type", "")).lower() == "call"
             and float((o.get("greeks") or {}).get("mid_iv") or 0) > 0]
    if not calls:
        return None
    calls.sort(key=lambda x: abs(x[0] - spot))
    nearest = calls[:3]
    ivs = [iv for _, iv in nearest if iv > 0]
    return round(sum(ivs) / len(ivs), 2) if ivs else None


# ── Full computation per ticker ───────────────────────────────────────────────

def compute_options_structure(ticker: str, spot: float | None = None) -> dict:
    """
    Compute GEX, Put/Call Skew, and Term Structure for one ticker.
    Fetches its own Tradier data. Returns a result dict.
    """
    result: dict = {
        "ticker":           ticker.upper(),
        "spot":             spot,
        "gex_m":            None,
        "gex_regime":       None,
        "gamma_flip_price": None,
        "pc_skew_pp":       None,
        "pc_skew_tag":      None,
        "term_ratio":       None,
        "term_tag":         None,
        "front_iv":         None,
        "back_iv":          None,
        "calls_analyzed":   0,
        "puts_analyzed":    0,
        "error":            None,
    }

    try:
        if spot is None:
            spot = _spot_from_tradier(ticker)
        if not spot or spot <= 0:
            result["error"] = "no_spot"
            return result
        result["spot"] = spot

        expiries = _fetch_expiries(ticker)
        if not expiries:
            result["error"] = "no_expiries"
            return result

        front_exp = expiries[0]
        back_exp  = expiries[1] if len(expiries) > 1 else None

        front_opts = _fetch_chain_with_greeks(ticker, front_exp)
        if not front_opts:
            result["error"] = "empty_chain"
            return result

        # ── GEX ───────────────────────────────────────────────────────────
        gex, n_c, n_p = _compute_gex(front_opts, spot, weight=1.0)
        result["calls_analyzed"] = n_c
        result["puts_analyzed"]  = n_p

        back_opts = _fetch_chain_with_greeks(ticker, back_exp) if back_exp else []
        if back_opts:
            gex_b, nc_b, np_b = _compute_gex(back_opts, spot, weight=0.5)
            gex += gex_b
            result["calls_analyzed"] += nc_b
            result["puts_analyzed"]  += np_b

        result["gex_m"]       = round(gex, 3)
        result["gex_regime"]  = ("NEAR_FLIP" if abs(gex) < 0.5
                                 else "LONG_GAMMA" if gex > 0
                                 else "SHORT_GAMMA")
        result["gamma_flip_price"] = _gamma_flip_price(front_opts, spot)

        # ── Put/Call Skew ──────────────────────────────────────────────────
        skew_pp, put_iv, call_iv, skew_tag = _compute_skew(front_opts, spot)
        result["pc_skew_pp"]  = skew_pp
        result["pc_skew_tag"] = skew_tag

        # ── Term Structure ─────────────────────────────────────────────────
        if back_opts:
            f_iv = _atm_iv(front_opts, spot)
            b_iv = _atm_iv(back_opts,  spot)
            if f_iv and b_iv and b_iv > 0:
                ratio = f_iv / b_iv
                result["term_ratio"] = round(ratio, 3)
                result["front_iv"]   = f_iv
                result["back_iv"]    = b_iv
                result["term_tag"]   = ("INVERTED"  if ratio > 1.10
                                        else "CONTANGO" if ratio < 0.88
                                        else "NORMAL")

    except Exception as _e:
        result["error"] = str(_e)

    return result


def scan_options_structure(tickers: list, max_workers: int = 4) -> list:
    """Scan a list of tickers concurrently. Returns list of result dicts."""
    import concurrent.futures as _cf
    results = []
    with _cf.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(compute_options_structure, t): t for t in tickers}
        for fut in _cf.as_completed(futs, timeout=120):
            try:
                r = fut.result(timeout=10)
                if r and not r.get("error"):
                    results.append(r)
            except Exception:
                pass
    return results


def init_db(conn):
    """Create options_structure_scan table."""
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS options_structure_scan (
                ticker            TEXT NOT NULL,
                scan_date         DATE NOT NULL,
                spot              NUMERIC,
                gex_m             NUMERIC,
                gex_regime        TEXT,
                gamma_flip_price  NUMERIC,
                pc_skew_pp        NUMERIC,
                pc_skew_tag       TEXT,
                term_ratio        NUMERIC,
                term_tag          TEXT,
                front_iv          NUMERIC,
                back_iv           NUMERIC,
                calls_analyzed    INT DEFAULT 0,
                puts_analyzed     INT DEFAULT 0,
                updated_at        TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (ticker, scan_date)
            )
        """)
        conn.commit()


def save_to_db(results: list, conn) -> int:
    """Upsert options structure scan results."""
    from datetime import date as _d
    today = _d.today().isoformat()
    n = 0
    with conn.cursor() as cur:
        for r in results:
            try:
                cur.execute("""
                    INSERT INTO options_structure_scan
                        (ticker, scan_date, spot, gex_m, gex_regime, gamma_flip_price,
                         pc_skew_pp, pc_skew_tag, term_ratio, term_tag,
                         front_iv, back_iv, calls_analyzed, puts_analyzed, updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                    ON CONFLICT (ticker, scan_date) DO UPDATE SET
                        spot=EXCLUDED.spot, gex_m=EXCLUDED.gex_m,
                        gex_regime=EXCLUDED.gex_regime,
                        gamma_flip_price=EXCLUDED.gamma_flip_price,
                        pc_skew_pp=EXCLUDED.pc_skew_pp, pc_skew_tag=EXCLUDED.pc_skew_tag,
                        term_ratio=EXCLUDED.term_ratio, term_tag=EXCLUDED.term_tag,
                        front_iv=EXCLUDED.front_iv, back_iv=EXCLUDED.back_iv,
                        calls_analyzed=EXCLUDED.calls_analyzed,
                        puts_analyzed=EXCLUDED.puts_analyzed,
                        updated_at=NOW()
                """, (
                    r["ticker"], today,
                    r.get("spot"), r.get("gex_m"), r.get("gex_regime"),
                    r.get("gamma_flip_price"), r.get("pc_skew_pp"), r.get("pc_skew_tag"),
                    r.get("term_ratio"), r.get("term_tag"),
                    r.get("front_iv"), r.get("back_iv"),
                    r.get("calls_analyzed", 0), r.get("puts_analyzed", 0),
                ))
                n += 1
            except Exception as _e:
                conn.rollback()
    conn.commit()
    return n
