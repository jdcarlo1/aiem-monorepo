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

from .config import TRADIER_TOKEN, TRADIER_BASE, CHAIN_CACHE_TTL

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
