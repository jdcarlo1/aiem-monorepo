"""
Congress trades fetcher.
Primary: Quiver Quantitative public API (no auth required)
Cached in memory for 6 hours.
"""
import time
import requests

_cache: dict = {"data": [], "ts": 0}
_CACHE_TTL = 21600  # 6 hours

QUIVER_URL = "https://api.quiverquant.com/beta/live/congresstrading"

_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (compatible; StockScannerAI/1.0)",
}


def _normalize_party(rep: str, bio_id: str) -> str:
    """Best-effort party guess from name or bio guide ID."""
    r = (rep or "").lower()
    if "(d)" in r or " d-" in r:  return "D"
    if "(r)" in r or " r-" in r:  return "R"
    if "(i)" in r:                 return "I"
    return "?"


def _fetch_quiver() -> list:
    try:
        r = requests.get(QUIVER_URL, timeout=20, headers=_HEADERS)
        r.raise_for_status()
        raw = r.json()
        out = []
        for t in raw:
            ticker = (t.get("Ticker") or "").strip().upper()
            if not ticker or ticker in ("N/A", "--", ""):
                continue
            rep    = t.get("Representative") or ""
            party  = _normalize_party(rep, t.get("BioGuideID", ""))
            txn    = t.get("Transaction") or ""
            out.append({
                "member":  rep,
                "party":   party,
                "chamber": "House",
                "ticker":  ticker,
                "type":    txn,
                "amount":  t.get("Range") or t.get("Amount") or "",
                "date":    t.get("TransactionDate") or t.get("ReportDate") or "",
                "asset":   t.get("Asset") or ticker,
            })
        out.sort(key=lambda x: x["date"], reverse=True)
        return out[:300]
    except Exception as e:
        print(f"[congress] fetch error: {e}")
        return []


def get_congress_trades(force: bool = False) -> list:
    now = time.time()
    if not force and now - _cache["ts"] < _CACHE_TTL and _cache["data"]:
        return _cache["data"]
    data = _fetch_quiver()
    if data:
        _cache["data"] = data
        _cache["ts"] = now
    return _cache["data"]
