"""
chain_data.py — Standalone Tradier client for the Advanced Strategy Engine.
Completely independent of aiem_options_structure.py / aiem_options_intel.py.
Uses TRADIER_API_TOKEN_2 (brokerage, real-time data) with TOKEN fallback.
"""
from __future__ import annotations
import os, time, math, threading
from datetime import date, datetime
from typing import Optional, List, Dict, Any
import urllib.request, urllib.parse, json

from .config import (
    TRADIER_TOKEN, TRADIER_BASE, CHAIN_CACHE_TTL,
    MAX_BID_ASK_WIDTH, MIN_OPEN_INTEREST, MIN_VOLUME,
    PREFER_MIN_OI, PREFER_MIN_VOLUME, PREFER_MAX_SPREAD_PCT,
    QUOTE_STALE_SECONDS, POLYGON_CHAIN_FALLBACK_ENABLED,
)

# ── Internal caches ─────────────────────────────────────────────────────────
_chain_cache:   Dict[tuple, tuple] = {}   # (ticker, expiry) → (data, ts)
_expiry_cache:  Dict[str, tuple]   = {}   # ticker → (data, ts)
_quote_cache:   Dict[str, tuple]   = {}   # ticker → (data, ts)
_lock = threading.Lock()

_HEADERS = {
    "Authorization": f"Bearer {TRADIER_TOKEN}",
    "Accept": "application/json",
}

def _get(path: str, params: dict, timeout: int = 8) -> Optional[dict]:
    """HTTP GET against Tradier API. Returns parsed JSON or None on error."""
    if not TRADIER_TOKEN:
        return None
    url = f"{TRADIER_BASE}/{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers=_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            return json.loads(resp.read())
    except Exception:
        return None


def get_spot(ticker: str) -> Optional[float]:
    """Fetch last trade price for underlying."""
    now = time.time()
    with _lock:
        cached = _quote_cache.get(ticker)
        if cached and now - cached[1] < 60:  # 1-min cache for spot
            return cached[0]
    data = _get("quotes", {"symbols": ticker.upper(), "greeks": "false"})
    if not data:
        return None
    try:
        q = data.get("quotes", {}).get("quote", {})
        if isinstance(q, list):
            q = q[0]
        price = float(q.get("last") or q.get("close") or 0)
        if price > 0:
            with _lock:
                _quote_cache[ticker] = (price, now)
            return price
    except Exception:
        pass
    return None


def get_expirations(ticker: str) -> List[str]:
    """Fetch available option expiration dates (YYYY-MM-DD strings)."""
    now = time.time()
    with _lock:
        cached = _expiry_cache.get(ticker)
        if cached and now - cached[1] < CHAIN_CACHE_TTL:
            return cached[0]
    data = _get("options/expirations", {"symbol": ticker.upper(), "includeAllRoots": "true"})
    if not data:
        return []
    try:
        exps = data.get("expirations", {}).get("date", [])
        if isinstance(exps, str):
            exps = [exps]
        exps = [e for e in (exps or []) if e]
        with _lock:
            _expiry_cache[ticker] = (exps, now)
        return exps
    except Exception:
        return []


def get_chain(ticker: str, expiry: str) -> List[Dict[str, Any]]:
    """
    Fetch full option chain with greeks for a specific expiration.
    Returns list of option dicts with normalized keys.
    Caches for CHAIN_CACHE_TTL seconds.
    """
    key = (ticker.upper(), expiry)
    now = time.time()
    with _lock:
        cached = _chain_cache.get(key)
        if cached and now - cached[1] < CHAIN_CACHE_TTL:
            return cached[0]
    data = _get("options/chains", {"symbol": ticker.upper(), "expiration": expiry, "greeks": "true"})
    if not data:
        return []
    try:
        opts = (data.get("options") or {}).get("option") or []
        if isinstance(opts, dict):
            opts = [opts]
        normalized = [_normalize_option(o, ticker, expiry) for o in (opts or [])]
        normalized = [o for o in normalized if o]
        with _lock:
            _chain_cache[key] = (normalized, now)
        return normalized
    except Exception:
        return []


def _normalize_option(raw: dict, ticker: str, expiry: str) -> Optional[dict]:
    """Normalize a raw Tradier option dict into a clean internal structure."""
    try:
        g = raw.get("greeks") or {}
        bid = float(raw.get("bid") or 0)
        ask = float(raw.get("ask") or 0)
        mid = round((bid + ask) / 2, 4)
        iv  = float(g.get("smv_vol") or raw.get("iv") or 0) or None
        return {
            "ticker":         ticker.upper(),
            "expiration":     expiry,
            "option_symbol":  raw.get("symbol", ""),
            "call_or_put":    raw.get("option_type", "").upper()[:1],  # C or P
            "strike":         float(raw.get("strike") or 0),
            "bid":            bid,
            "ask":            ask,
            "mid":            mid,
            "iv":             iv,
            "delta":          float(g.get("delta") or 0) or None,
            "gamma":          float(g.get("gamma") or 0) or None,
            "theta":          float(g.get("theta") or 0) or None,
            "vega":           float(g.get("vega") or 0) or None,
            "rho":            float(g.get("rho") or 0) or None,
            "volume":         int(raw.get("volume") or 0),
            "open_interest":  int(raw.get("open_interest") or 0),
            "quote_timestamp": raw.get("trade_date") or raw.get("last_volume_date"),
        }
    except Exception:
        return None


def find_option_by_delta(
    chain: List[dict],
    call_or_put: str,   # "C" or "P"
    target_delta: float,
    side: str = "LONG",
) -> Optional[dict]:
    """
    Find the option in the chain closest to target_delta (absolute).
    For puts, delta is negative; target_delta should be positive (abs value).
    """
    cp = call_or_put.upper()[:1]
    candidates = [o for o in chain if o.get("call_or_put") == cp and o.get("delta") is not None]
    if not candidates:
        return None
    def dist(o):
        d = abs(o["delta"])
        return abs(d - target_delta)
    return min(candidates, key=dist)


def find_option_by_strike(
    chain: List[dict],
    call_or_put: str,
    strike: float,
) -> Optional[dict]:
    """Find option at or nearest to given strike."""
    cp = call_or_put.upper()[:1]
    candidates = [o for o in chain if o.get("call_or_put") == cp]
    if not candidates:
        return None
    return min(candidates, key=lambda o: abs((o.get("strike") or 0) - strike))


def get_atm_iv(chain: List[dict], spot: float) -> Optional[float]:
    """
    Return the average of ATM call and ATM put IV (a.k.a. ATM straddle IV).
    """
    atm_call = find_option_by_delta(chain, "C", 0.50)
    atm_put  = find_option_by_delta(chain, "P", 0.50)
    ivs = [o["iv"] for o in [atm_call, atm_put] if o and o.get("iv")]
    return sum(ivs)/len(ivs) if ivs else None


def get_skew(chain: List[dict]) -> Optional[float]:
    """
    25-delta put IV minus 25-delta call IV (in decimal).
    Positive = put skew (fear premium), Negative = call skew.
    """
    c25 = find_option_by_delta(chain, "C", 0.25)
    p25 = find_option_by_delta(chain, "P", 0.25)
    if c25 and p25 and c25.get("iv") and p25.get("iv"):
        return round(p25["iv"] - c25["iv"], 4)
    return None


def get_strikes_near_atm(chain: List[dict], spot: float, n: int = 10) -> List[float]:
    """Return n nearest strikes to spot (both calls and puts deduplicated)."""
    strikes = sorted({o.get("strike") for o in chain if o.get("strike")})
    strikes.sort(key=lambda k: abs(k - spot))
    return strikes[:n]


def get_dte(expiry: str) -> int:
    """Days to expiration from today."""
    try:
        exp_date = datetime.strptime(expiry, "%Y-%m-%d").date()
        return max(0, (exp_date - date.today()).days)
    except Exception:
        return 0


# ─────────────────────────────────────────────────────────────────────────────
# POLYGON FALLBACK (Phase 4 §6 — secondary source only)
# ─────────────────────────────────────────────────────────────────────────────

def _get_chain_polygon_fallback(ticker: str, expiry: str) -> List[Dict[str, Any]]:
    """
    Polygon options chain fallback.  Enabled only when
    POLYGON_CHAIN_FALLBACK_ENABLED=True.  Logs source and quote age on
    every use so callers can distinguish primary vs fallback data.

    Returns normalized option list in the same shape as get_chain().
    An empty list is returned when Polygon is disabled, unavailable, or
    returns no data — callers must not silently treat that as a PASS.
    """
    import logging as _log_module
    _fb_log = _log_module.getLogger("aiem_strat_engine.chain_data.polygon_fallback")
    if not POLYGON_CHAIN_FALLBACK_ENABLED:
        _fb_log.debug("Polygon fallback disabled (POLYGON_CHAIN_FALLBACK_ENABLED=False)")
        return []

    polygon_token = os.environ.get("POLYGON_API_KEY", "")
    if not polygon_token:
        _fb_log.warning("Polygon fallback enabled but POLYGON_API_KEY not set — returning empty")
        return []

    fetch_ts = datetime.utcnow().isoformat() + "Z"
    _fb_log.warning(
        "CHAIN_SOURCE=polygon_fallback ticker=%s expiry=%s fetched_at=%s",
        ticker, expiry, fetch_ts,
    )

    url = (
        f"https://api.polygon.io/v3/snapshot/options/{ticker.upper()}"
        f"?expiration_date={expiry}&limit=250&apiKey={polygon_token}"
    )
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status != 200:
                _fb_log.error("Polygon returned HTTP %s for %s %s", resp.status, ticker, expiry)
                return []
            raw = json.loads(resp.read())
    except Exception as exc:
        _fb_log.error("Polygon fetch failed: %s", exc)
        return []

    results = raw.get("results") or []
    normalized = []
    for item in results:
        details = item.get("details") or {}
        day     = item.get("day") or {}
        greeks  = item.get("greeks") or {}
        iv      = item.get("implied_volatility")
        bid     = float(day.get("close") or 0)     # Polygon snapshot: no live bid/ask
        ask     = bid                               # same — mark age as stale
        mid     = bid
        norm = {
            "ticker":         ticker.upper(),
            "expiration":     expiry,
            "option_symbol":  details.get("ticker", ""),
            "call_or_put":    (details.get("contract_type") or "")[:1].upper(),
            "strike":         float(details.get("strike_price") or 0),
            "bid":            bid,
            "ask":            ask,
            "mid":            mid,
            "iv":             float(iv) if iv else None,
            "delta":          float(greeks.get("delta") or 0) or None,
            "gamma":          float(greeks.get("gamma") or 0) or None,
            "theta":          float(greeks.get("theta") or 0) or None,
            "vega":           float(greeks.get("vega") or 0) or None,
            "rho":            None,
            "volume":         int(day.get("volume") or 0),
            "open_interest":  int(item.get("open_interest") or 0),
            "quote_timestamp": fetch_ts,
            "data_source":    "polygon_fallback",
        }
        normalized.append(norm)

    _fb_log.warning(
        "CHAIN_SOURCE=polygon_fallback ticker=%s expiry=%s legs_returned=%d "
        "NOTE=bid_ask_spread_zero_polygon_snapshot",
        ticker, expiry, len(normalized),
    )
    return normalized


# ─────────────────────────────────────────────────────────────────────────────
# CHAIN QUALITY METRICS (Phase 4 §6)
# ─────────────────────────────────────────────────────────────────────────────

def _bootstrap_chain_quality_columns(conn) -> None:
    """Idempotent — adds Phase 4 quality columns to oe_options_metrics if absent."""
    stmts = [
        "ALTER TABLE oe_options_metrics ADD COLUMN IF NOT EXISTS liquidity_score NUMERIC",
        "ALTER TABLE oe_options_metrics ADD COLUMN IF NOT EXISTS exit_liquidity NUMERIC",
        "ALTER TABLE oe_options_metrics ADD COLUMN IF NOT EXISTS quote_age_seconds INTEGER",
        "ALTER TABLE oe_options_metrics ADD COLUMN IF NOT EXISTS chain_completeness NUMERIC",
        "ALTER TABLE oe_options_metrics ADD COLUMN IF NOT EXISTS chain_quality_gate_passed BOOLEAN",
    ]
    with conn.cursor() as cur:
        for s in stmts:
            cur.execute(s)


def _compute_leg_quality(leg: Any) -> Dict[str, Any]:
    """
    Compute per-leg Phase 4 chain quality metrics.

    liquidity_score  — weighted composite [0,1]
      0.50 × spread_component  (0=at hard-reject, 1=at 0%)
      0.25 × oi_component      (0=0 OI, 1=at PREFER_MIN_OI)
      0.25 × vol_component     (0=0 vol, 1=at PREFER_MIN_VOLUME)

    expected_slippage  — half-spread in dollars (ask-bid)/2
    fill_probability   — model: volume-weighted + spread-penalised [0,1]
    exit_liquidity     — 10%-haircut of liquidity_score (exit fills worse than entry)
    quote_age_seconds  — seconds since quote_timestamp; None → None (treated as stale)
    chain_completeness — 1.0 if all required fields present, else 0.0
    """
    bid  = getattr(leg, "bid", None)  or 0.0
    ask  = getattr(leg, "ask", None)  or 0.0
    mid  = getattr(leg, "mid", None)  or 0.0
    vol  = getattr(leg, "volume", None) or 0
    oi   = getattr(leg, "open_interest", None) or 0
    iv   = getattr(leg, "iv", None)
    delta = getattr(leg, "delta", None)
    ts   = getattr(leg, "quote_timestamp", None)

    spread_pct = ((ask - bid) / mid) if mid > 0 else 1.0

    # Spread component: 1.0 at 0% spread, 0.0 at MAX_BID_ASK_WIDTH (hard-reject)
    spread_component = max(0.0, 1.0 - spread_pct / MAX_BID_ASK_WIDTH)
    # OI component: 1.0 at PREFER_MIN_OI
    oi_component  = min(1.0, oi  / PREFER_MIN_OI)
    # Volume component: 1.0 at PREFER_MIN_VOLUME
    vol_component = min(1.0, vol / PREFER_MIN_VOLUME)

    liquidity_score = round(
        0.50 * spread_component + 0.25 * oi_component + 0.25 * vol_component, 4
    )
    expected_slippage = round((ask - bid) / 2.0, 4)

    # Fill probability: combines volume signal with spread penalty
    vol_fill  = min(1.0, vol / (PREFER_MIN_VOLUME * 2))  # full at 2× preferred
    fill_probability = round(0.70 * vol_fill + 0.30 * spread_component, 4)

    exit_liquidity = round(liquidity_score * 0.90, 4)

    # Quote age
    quote_age_seconds: Optional[int] = None
    if ts:
        from datetime import timezone as _tz
        now = datetime.now(_tz.utc)
        _parsed = None
        for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                _parsed = datetime.strptime(str(ts)[:19].rstrip("Z"), fmt.rstrip("Z"))
                _parsed = _parsed.replace(tzinfo=_tz.utc)
                break
            except ValueError:
                continue
        # Unix timestamp fallback (Tradier trade_date is milliseconds)
        if _parsed is None:
            try:
                val = float(str(ts))
                if val > 1_000_000_000_000:
                    val /= 1000.0
                _parsed = datetime.fromtimestamp(val, tz=_tz.utc)
            except (ValueError, OSError):
                pass
        if _parsed is not None:
            quote_age_seconds = int((now - _parsed).total_seconds())

    # Chain completeness: 1.0 if all critical fields present, else 0.0
    required = [bid, ask, mid, iv, delta]
    chain_completeness = 1.0 if all(v is not None and v != 0 for v in required) else 0.0

    return {
        "liquidity_score":   liquidity_score,
        "expected_slippage": expected_slippage,
        "fill_probability":  fill_probability,
        "exit_liquidity":    exit_liquidity,
        "quote_age_seconds": quote_age_seconds,
        "chain_completeness": chain_completeness,
        "spread_pct":        round(spread_pct, 4),
    }


def compute_chain_quality(
    legs: List[Any],
    trace_id: str,
    alert_id: Optional[int] = None,
    ticker: str = "",
    scan_date: Optional[Any] = None,       # datetime.date
    db_url: str = "",
) -> Dict[str, Any]:
    """
    Compute Phase 4 chain quality metrics for a multi-leg strategy and
    persist per-leg rows to oe_options_metrics.

    Returns strategy-level aggregates:
      liquidity_score       — min across legs (weakest-leg governs)
      expected_slippage     — sum across legs (total round-trip friction)
      fill_probability      — min across legs
      exit_liquidity        — min across legs
      quote_age             — max across legs (stalest leg governs)
      chain_completeness    — min across legs
      chain_quality_gate_passed — True when all hard gates would pass
      per_leg               — list of per-leg metric dicts
    """
    if not legs:
        return {"error": "no_legs"}

    per_leg = []
    for lg in legs:
        metrics = _compute_leg_quality(lg)
        metrics["option_symbol"] = getattr(lg, "option_symbol", None) or str(getattr(lg, "strike", "?"))
        metrics["strike"]        = getattr(lg, "strike", None)
        metrics["expiration"]    = getattr(lg, "expiration", None)
        metrics["direction"]     = getattr(lg, "call_or_put", None) or getattr(lg, "asset_type", None)
        metrics["oi"]            = getattr(lg, "open_interest", None)
        metrics["volume"]        = getattr(lg, "volume", None)
        per_leg.append(metrics)

    # Strategy-level aggregates
    liq_scores   = [m["liquidity_score"]   for m in per_leg]
    slippages    = [m["expected_slippage"] for m in per_leg]
    fill_probs   = [m["fill_probability"]  for m in per_leg]
    exit_liqs    = [m["exit_liquidity"]    for m in per_leg]
    ages         = [m["quote_age_seconds"] for m in per_leg if m["quote_age_seconds"] is not None]
    completeness = [m["chain_completeness"] for m in per_leg]

    strat = {
        "liquidity_score":       round(min(liq_scores),    4),
        "expected_slippage":     round(sum(slippages),     4),
        "fill_probability":      round(min(fill_probs),    4),
        "exit_liquidity":        round(min(exit_liqs),     4),
        "quote_age":             max(ages) if ages else None,
        "chain_completeness":    round(min(completeness),  4),
        "chain_quality_gate_passed": all(
            m["chain_completeness"] == 1.0
            and (m["quote_age_seconds"] is None or m["quote_age_seconds"] <= QUOTE_STALE_SECONDS)
            for m in per_leg
        ),
        "per_leg": per_leg,
        "trace_id": trace_id,
    }

    # ── Persist to oe_options_metrics ────────────────────────────────────────
    if db_url:
        try:
            import psycopg2
            import psycopg2.extras
            from datetime import date as _date
            sd = scan_date if isinstance(scan_date, _date) else _date.today()
            with psycopg2.connect(db_url, connect_timeout=5) as conn:
                _bootstrap_chain_quality_columns(conn)
                with conn.cursor() as cur:
                    for m in per_leg:
                        cur.execute("""
                            INSERT INTO oe_options_metrics (
                                trace_id, alert_id, ticker, scan_date,
                                direction, strike, expiry, dte,
                                bid, ask, mid, spread_pct,
                                volume, open_interest,
                                fill_probability, slippage_pct, data_source,
                                liquidity_score, exit_liquidity,
                                quote_age_seconds, chain_completeness,
                                chain_quality_gate_passed, captured_at
                            ) VALUES (
                                %s,%s,%s,%s, %s,%s,%s,%s,
                                %s,%s,%s,%s, %s,%s,
                                %s,%s,%s,
                                %s,%s, %s,%s, %s, NOW()
                            )
                            ON CONFLICT DO NOTHING
                        """, (
                            trace_id, alert_id, ticker or "", sd,
                            m.get("direction"), m.get("strike"),
                            m.get("expiration"), None,
                            None, None, None, m["spread_pct"],
                            m["volume"], m["oi"],
                            m["fill_probability"], m["expected_slippage"],
                            getattr(legs[0], "data_source", "tradier"),
                            m["liquidity_score"], m["exit_liquidity"],
                            m["quote_age_seconds"], m["chain_completeness"],
                            strat["chain_quality_gate_passed"],
                        ))
                conn.commit()
            strat["persisted"] = True
        except Exception as exc:
            strat["persist_error"] = str(exc)

    return strat


def select_expirations_for_dte_slots(
    expirations: List[str],
    slots: Optional[dict] = None,
) -> Dict[str, Optional[str]]:
    """
    Map DTE slot names to the best matching expiration.
    slots: {slot_name: (min_dte, max_dte)} — defaults to config.DTE_SLOTS
    """
    if slots is None:
        from .config import DTE_SLOTS
        slots = DTE_SLOTS
    result: Dict[str, Optional[str]] = {}
    for slot_name, (lo, hi) in slots.items():
        best = None
        best_dist = float("inf")
        target = (lo + hi) // 2
        for exp in expirations:
            d = get_dte(exp)
            if lo <= d <= hi:
                dist = abs(d - target)
                if dist < best_dist:
                    best_dist = dist
                    best = exp
        result[slot_name] = best
    return result
