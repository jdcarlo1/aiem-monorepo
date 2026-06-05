"""
C-Suite Insider Trades via SEC EDGAR Form 4 filings.
"""
import requests
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

HEADERS = {
    "User-Agent": "StockScannerAI research@nclexai.org",
    "Accept-Encoding": "gzip, deflate",
}

_CIK_CACHE: dict = {}
_CIK_TS: float = 0.0


def _get_cik_map() -> dict:
    global _CIK_CACHE, _CIK_TS
    now = time.time()
    if _CIK_CACHE and now - _CIK_TS < 3600:
        return _CIK_CACHE
    try:
        r = requests.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers=HEADERS, timeout=10
        )
        data = r.json()
        _CIK_CACHE = {v["ticker"]: str(v["cik_str"]).zfill(10) for v in data.values()}
        _CIK_TS = now
    except Exception:
        pass
    return _CIK_CACHE


def _parse_form4(xml_text: str, ticker: str, filing_date: str) -> list:
    trades = []
    try:
        root = ET.fromstring(xml_text)

        insider_name = ""
        insider_title = ""
        owner = root.find(".//reportingOwner")
        if owner is not None:
            n = owner.find(".//rptOwnerName")
            if n is not None:
                insider_name = (n.text or "").strip()
            rel = owner.find(".//reportingOwnerRelationship")
            if rel is not None:
                titles = []
                off = rel.find("isOfficer")
                if off is not None and off.text == "1":
                    ot = rel.find("officerTitle")
                    titles.append(ot.text.strip() if ot is not None and ot.text else "Officer")
                if rel.findtext("isDirector") == "1":
                    titles.append("Director")
                if rel.findtext("isTenPercentOwner") == "1":
                    titles.append("10% Owner")
                insider_title = ", ".join(titles) if titles else "Insider"

        for txn in root.findall(".//nonDerivativeTransaction"):
            try:
                code = (txn.findtext(".//transactionCode") or "").strip()
                if code not in ("P", "S"):
                    continue
                shares_v = txn.findtext(".//transactionShares/value")
                price_v  = txn.findtext(".//transactionPricePerShare/value")
                date_v   = txn.findtext(".//transactionDate/value") or filing_date
                shares = float(shares_v) if shares_v else 0
                price  = float(price_v)  if price_v  else 0
                if shares <= 0:
                    continue
                trades.append({
                    "ticker":       ticker,
                    "insider_name": insider_name,
                    "title":        insider_title,
                    "trade_type":   "Buy" if code == "P" else "Sell",
                    "shares":       int(shares),
                    "price":        round(price, 2),
                    "value":        round(shares * price),
                    "date":         date_v,
                })
            except Exception:
                continue
    except Exception:
        pass
    return trades


def _fetch_for_ticker(ticker: str, cik_map: dict, start_date: str) -> list:
    trades = []
    cik = cik_map.get(ticker.upper())
    if not cik:
        return trades
    try:
        url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        r = requests.get(url, headers=HEADERS, timeout=8)
        if r.status_code != 200:
            return trades
        data   = r.json()
        recent = data.get("filings", {}).get("recent", {})
        forms  = recent.get("form", [])
        dates  = recent.get("filingDate", [])
        accnos = recent.get("accessionNumber", [])
        docs   = recent.get("primaryDocument", [])
        cik_int = int(cik)
        for form, fdate, accno, doc in zip(forms, dates, accnos, docs):
            if form != "4" or fdate < start_date:
                continue
            accno_clean = accno.replace("-", "")
            xml_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{accno_clean}/{doc}"
            try:
                xr = requests.get(xml_url, headers=HEADERS, timeout=6)
                if xr.status_code == 200:
                    trades.extend(_parse_form4(xr.text, ticker, fdate))
            except Exception:
                pass
            time.sleep(0.06)
    except Exception:
        pass
    return trades


def fetch_insider_trades(tickers: list, days: int = 30) -> list:
    cik_map    = _get_cik_map()
    start_date = (date.today() - timedelta(days=days)).isoformat()
    all_trades: list = []
    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = {ex.submit(_fetch_for_ticker, t, cik_map, start_date): t for t in tickers[:18]}
        for fut in as_completed(futures):
            all_trades.extend(fut.result())
    all_trades.sort(key=lambda x: (x["date"], x["value"]), reverse=True)
    return all_trades
