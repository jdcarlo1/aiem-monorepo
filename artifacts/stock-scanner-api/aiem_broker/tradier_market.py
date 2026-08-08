"""Thin Tradier market-data client for paper brokerage fills.

Never places orders. Prefer TRADIER_API_TOKEN_2, fall back to TRADIER_API_TOKEN.
Uses production api.tradier.com (brokerage market-data token).
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import requests

TRADIER_API_BASE = os.environ.get("TRADIER_API_BASE", "https://api.tradier.com").rstrip("/")


def tradier_token() -> str:
    return (
        os.environ.get("TRADIER_API_TOKEN_2")
        or os.environ.get("TRADIER_API_TOKEN")
        or ""
    ).strip()


def tradier_account_id() -> str:
    return (os.environ.get("TRADIER_ACCOUNT_ID") or "").strip()


def _headers(token: Optional[str] = None) -> Dict[str, str]:
    tok = (token or tradier_token()).strip()
    return {"Authorization": f"Bearer {tok}", "Accept": "application/json"}


def fetch_quotes(symbols: List[str], token: Optional[str] = None) -> Dict[str, dict]:
    """Return {SYM: {last, bid, ask, ...}} for up to 200 symbols."""
    tok = token or tradier_token()
    if not tok or not symbols:
        return {}
    cleaned = [str(s).upper().strip() for s in symbols if s][:200]
    try:
        r = requests.get(
            f"{TRADIER_API_BASE}/v1/markets/quotes",
            params={"symbols": ",".join(cleaned), "greeks": "false"},
            headers=_headers(tok),
            timeout=6,
        )
        if r.status_code != 200:
            return {}
        raw = (r.json().get("quotes") or {}).get("quote") or []
        if isinstance(raw, dict):
            raw = [raw]
        out: Dict[str, dict] = {}
        for q in raw:
            sym = (q.get("symbol") or "").upper()
            if not sym:
                continue
            out[sym] = {
                "ticker": sym,
                "last": float(q.get("last") or 0) or None,
                "bid": float(q.get("bid") or 0) or None,
                "ask": float(q.get("ask") or 0) or None,
                "prevclose": float(q.get("prevclose") or 0) or None,
                "volume": int(q.get("volume") or 0),
                "avg_volume": int(q.get("average_volume") or 0),
                "description": q.get("description"),
                "exch": q.get("exch"),
                "type": q.get("type"),
                "source": "tradier",
                "raw": q,
            }
        return out
    except Exception as e:
        print(f"[tradier_market] quotes error: {e}")
        return {}


def fetch_quote(symbol: str, token: Optional[str] = None) -> Optional[dict]:
    return fetch_quotes([symbol], token=token).get((symbol or "").upper())


def fetch_option_quote(
    underlying: str,
    strike: float,
    expiry: str,
    right: str = "call",
    token: Optional[str] = None,
) -> Optional[dict]:
    """Look up one option contract on the Tradier chain (greeks=true)."""
    tok = token or tradier_token()
    und = (underlying or "").upper().strip()
    exp = str(expiry or "")[:10]
    side = (right or "call").lower()
    if side in ("c", "call"):
        side = "call"
    elif side in ("p", "put"):
        side = "put"
    else:
        side = "call"
    if not tok or not und or not exp or strike is None:
        return None
    try:
        r = requests.get(
            f"{TRADIER_API_BASE}/v1/markets/options/chains",
            params={"symbol": und, "expiration": exp, "greeks": "true"},
            headers=_headers(tok),
            timeout=8,
        )
        if r.status_code != 200:
            return None
        opts = (r.json().get("options") or {}).get("option") or []
        if isinstance(opts, dict):
            opts = [opts]
        sk = float(strike)
        want = [o for o in opts if str(o.get("option_type") or "").lower() == side]
        if not want:
            return None
        best = min(want, key=lambda o: abs(float(o.get("strike") or 0) - sk))
        bid = float(best.get("bid") or 0) or None
        ask = float(best.get("ask") or 0) or None
        last = float(best.get("last") or 0) or None
        mid = None
        if bid and ask:
            mid = (bid + ask) / 2.0
        elif ask:
            mid = ask
        elif bid:
            mid = bid
        elif last:
            mid = last
        greeks = best.get("greeks") or {}
        return {
            "ticker": und,
            "option_symbol": best.get("symbol"),
            "strike": float(best.get("strike") or sk),
            "expiry": exp,
            "option_right": side,
            "bid": bid,
            "ask": ask,
            "last": last,
            "mid": mid,
            "volume": int(best.get("volume") or 0),
            "open_interest": int(best.get("open_interest") or 0),
            "implied_volatility": (greeks.get("mid_iv") if isinstance(greeks, dict) else None),
            "source": "tradier_chain",
            "raw": best,
        }
    except Exception as e:
        print(f"[tradier_market] option quote error {und} {strike} {exp}: {e}")
        return None


def fetch_profile(token: Optional[str] = None) -> Optional[dict]:
    """User profile / account metadata from Tradier (read-only)."""
    tok = token or tradier_token()
    if not tok:
        return None
    try:
        r = requests.get(
            f"{TRADIER_API_BASE}/v1/user/profile",
            headers=_headers(tok),
            timeout=6,
        )
        if r.status_code != 200:
            return {"ok": False, "status_code": r.status_code, "body": r.text[:300]}
        return r.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}


def connection_probe() -> dict:
    """Quick readiness check used by Sales Readiness / broker status."""
    tok = tradier_token()
    acct = tradier_account_id()
    out: Dict[str, Any] = {
        "token_present": bool(tok),
        "account_id_env": acct or None,
        "api_base": TRADIER_API_BASE,
        "quotes_ok": False,
        "profile_ok": False,
        "option_level": None,
        "account_number": None,
        "account_type": None,
    }
    if not tok:
        out["error"] = "TRADIER_API_TOKEN(_2) not set"
        return out
    q = fetch_quote("SPY", token=tok)
    out["quotes_ok"] = bool(q and (q.get("last") or q.get("bid") or q.get("ask")))
    out["spy_quote"] = {
        k: q.get(k) for k in ("last", "bid", "ask")
    } if q else None
    prof = fetch_profile(token=tok)
    if prof and isinstance(prof.get("profile"), dict):
        out["profile_ok"] = True
        acc = prof["profile"].get("account")
        if isinstance(acc, list) and acc:
            acc = acc[0]
        if isinstance(acc, dict):
            out["account_number"] = acc.get("account_number")
            out["account_type"] = acc.get("type")
            out["option_level"] = acc.get("option_level")
            out["day_trader"] = acc.get("day_trader")
            out["status"] = acc.get("status")
    else:
        out["profile"] = prof
    return out
