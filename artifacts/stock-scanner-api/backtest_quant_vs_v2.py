"""
backtest_quant_vs_v2.py
=======================
Standalone head-to-head backtest: cross-sectional quant z-score engine vs
the production V2 nano-cap scoring system.

Test window : Jun 1–18 2026 (13 trading days; Jun 13=Sat, Jun 19=Juneteenth skipped)
Universe    : Finviz nano-cap filter — float<20M, price>$0.50, avgvol>20K (~774 tickers)
Sizing      : $1,000/trade, 5% hard stop (max loss −$50/trade)
Return      : next-day close-to-close (signal on day T, exit close of day T+1)

Usage
-----
    # Stage 1 — build daily OHLCV + float/SI cache (~60 s, saved to /tmp/bt_data.pkl)
    python3 backtest_quant_vs_v2.py --stage build

    # Stage 2 — run all 3 weeks and print comparison table (~90 s total)
    python3 backtest_quant_vs_v2.py --stage run

    # Or both in sequence:
    python3 backtest_quant_vs_v2.py --stage all

Three-way comparison printed
-----------------------------
    V2 alone  | Quant alone  | V2 + Quant combined (both must agree)

RECORDED RESULTS (run Jun 19 2026)
------------------------------------
  Week         │  V2 Alone               │  Quant Alone            │  V2 + Quant Combined
  Jun 1–5      │  23 tr  35% WR  −$76    │   4 tr  25% WR   −$10   │   3 tr   33% WR  +$40
  Jun 8–12     │   2 tr  50% WR  +$24    │   0 tr   —       $0     │   0 tr    —      $0
  Jun 15–18    │  14 tr  57% WR  +$234   │   3 tr 100% WR  +$231   │   4 tr  100% WR  +$263
  ─────────────────────────────────────────────────────────────────────────────────────────
  3-WK TOTAL   │  39 tr  44% WR  +$182   │   7 tr  57% WR  +$221   │   7 tr   71% WR  +$303
               │  0.5% return/capital    │  3.2% return/capital    │  4.3% return/capital

KEY FINDING
  Requiring BOTH systems to agree lifts win rate 44% → 71% (+28 pp) and return
  on deployed capital 0.5% → 4.3% (+3.9 pp). Quant rejected 82% of V2 signals —
  the ones averaging −0.4% return — while confirming the 7 averaging +3.9% each.

  V2 signals Quant REJECTED : 31 trades  39% WR  −$112  (−0.4% return/capital)
  V2 signals Quant CONFIRMED:  7 trades  71% WR  +$303  (+4.3% return/capital)

COMBINED PICKS — all 7 trades
  Ticker   Week        V2sc   Qz-score  gap    mom10   rv    → Ret%     P&L
  OBAI     Jun 1–5      79    +0.559   +5.7%  +14.3%  4.6x  →  +0.0%   $0
  IBG      Jun 1–5      67    +1.003  +11.0%  +13.9%  7.2x  →  +9.0%  +$90
  AEMD     Jun 1–5      64    +1.287   +9.0%  +14.8%  3.2x  →  −8.3%  −$50  (stopped)
  GP       Jun 15–18    87    +0.618   +3.2%  +13.0%  5.1x  →  +1.5%  +$15
  MYSE     Jun 15–18    82    +1.110   +6.3%  +14.1%  5.2x  → +13.9%  +$139
  GP       Jun 15–18    73    +0.618   +5.0%   +8.6%  7.7x  →  +3.2%  +$32
  IQST     Jun 15–18    64    +1.087   +8.3%  +13.0%  3.7x  →  +7.7%  +$77
"""

import sys
import time
import pickle
import warnings
import argparse
from datetime import date, timedelta, datetime
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import numpy as np
import pytz
import requests
import re

warnings.filterwarnings("ignore")

ET               = pytz.timezone("America/New_York")
CACHE_PATH       = "/tmp/bt_data.pkl"
RESULTS_PATH     = "/tmp/bt_results.pkl"

TRADING_DAYS = [
    (date(2026, 6, 1),  date(2026, 6, 2),  "Jun 1–5"),
    (date(2026, 6, 2),  date(2026, 6, 3),  "Jun 1–5"),
    (date(2026, 6, 3),  date(2026, 6, 4),  "Jun 1–5"),
    (date(2026, 6, 4),  date(2026, 6, 5),  "Jun 1–5"),
    (date(2026, 6, 5),  date(2026, 6, 8),  "Jun 1–5"),
    (date(2026, 6, 8),  date(2026, 6, 9),  "Jun 8–12"),
    (date(2026, 6, 9),  date(2026, 6, 10), "Jun 8–12"),
    (date(2026, 6, 10), date(2026, 6, 11), "Jun 8–12"),
    (date(2026, 6, 11), date(2026, 6, 12), "Jun 8–12"),
    (date(2026, 6, 12), date(2026, 6, 15), "Jun 8–12"),
    (date(2026, 6, 15), date(2026, 6, 16), "Jun 15–18"),
    (date(2026, 6, 16), date(2026, 6, 17), "Jun 15–18"),
    (date(2026, 6, 17), date(2026, 6, 18), "Jun 15–18"),
    (date(2026, 6, 18), None,              "Jun 15–18"),  # Jun 19 = Juneteenth
]

WEEK_LABELS = ["Jun 1–5", "Jun 8–12", "Jun 15–18"]


# ── Data helpers ──────────────────────────────────────────────────────────────

def fetch_finviz_universe() -> list:
    """Fetch nano-cap tickers from Finviz screener."""
    url = ("https://finviz.com/screener.ashx?"
           "v=111&f=cap_nano,sh_float_u20,sh_price_o0.5,sh_avgvol_o20&r=")
    headers = {"User-Agent": "Mozilla/5.0"}
    tickers = []
    for page_start in range(1, 2000, 20):
        resp = requests.get(url + str(page_start), headers=headers, timeout=15)
        found = re.findall(r'stock\?t=([A-Z]{1,6})&', resp.text)
        if not found:
            break
        tickers.extend(found)
        time.sleep(0.3)
    return list(dict.fromkeys(tickers))  # dedup, preserve order


def build_cache():
    """Stage 1: fetch universe + daily OHLCV + IWM + float/SI for candidates."""
    import yfinance as yf

    print("[build] Fetching Finviz universe …", flush=True)
    syms = fetch_finviz_universe()
    print(f"[build] {len(syms)} tickers", flush=True)

    print("[build] Downloading daily OHLCV (Apr 1 – today) …", flush=True)
    raw = yf.download(syms, start="2026-04-01", end="2026-06-21",
                      interval="1d", auto_adjust=True,
                      progress=False, threads=True)
    all_hist = {}
    for t in syms:
        try:
            cs = raw["Close"][t].dropna()
            vs = raw["Volume"][t].dropna()
            if len(cs) >= 12:
                all_hist[t] = {"close": cs, "vol": vs}
        except Exception:
            pass
    print(f"[build] {len(all_hist)} tickers with history", flush=True)

    print("[build] Downloading IWM …", flush=True)
    iwm_raw = yf.download("IWM", start="2026-04-01", end="2026-06-21",
                          interval="1d", auto_adjust=True,
                          progress=False)
    iwm_c = iwm_raw["Close"].squeeze().dropna()

    # Identify candidate tickers (any day has gap 1-20%, mom10 3-17%)
    candidates = set()
    for bt_date, _, _ in TRADING_DAYS:
        for ticker, hist in all_hist.items():
            cs = hist["close"]
            cu = cs[cs.index.date <= bt_date]
            if len(cu) < 12:
                continue
            cl = cu.tolist()
            gap   = (cl[-1] / cl[-2] - 1) * 100
            mom10 = (cl[-1] / cl[-11] - 1) * 100
            if 1 <= gap < 20 and 3 <= mom10 < 17 and cl[-1] > 0.50:
                candidates.add(ticker)
    print(f"[build] {len(candidates)} candidate tickers — fetching float/SI …", flush=True)

    def _fetch_fi(ticker):
        """Fetch float/short data from Finviz quote page (not yfinance .info)."""
        try:
            url = f"https://finviz.com/quote.ashx?t={ticker}"
            hdrs = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            resp = requests.get(url, headers=hdrs, timeout=10)
            txt  = resp.text

            def _fv(label):
                m = re.search(re.escape(label) + r"</td><td[^>]*>([^<]+)</td>", txt)
                return m.group(1).strip() if m else None

            def _num(s):
                if not s or s == "-":
                    return None
                s = s.replace(",", "")
                if s.endswith("B"): return float(s[:-1]) * 1e9
                if s.endswith("M"): return float(s[:-1]) * 1e6
                if s.endswith("K"): return float(s[:-1]) * 1e3
                try: return float(s)
                except Exception: return None

            fs_str  = _fv("Shs Float")
            sf_str  = _fv("Short Float")
            sr_str  = _fv("Short Ratio")
            float_shares = _num(fs_str)
            short_pct    = (_num(sf_str.rstrip("%")) / 100.0
                            if sf_str and "%" in sf_str else None)
            short_ratio  = _num(sr_str)
            return ticker, {"float_shares": float_shares,
                            "short_pct": short_pct, "short_ratio": short_ratio}
        except Exception:
            return ticker, {}

    fi_cache = {}
    with ThreadPoolExecutor(max_workers=12) as ex:
        for ticker, fi in ex.map(_fetch_fi, list(candidates)):
            fi_cache[ticker] = fi

    sq = sum(1 for v in fi_cache.values() if v.get("short_pct") and v.get("short_ratio"))
    print(f"[build] float/SI: {len(fi_cache)} fetched, {sq} with full squeeze data", flush=True)

    pickle.dump({"syms": syms, "all_hist": all_hist, "iwm_c": iwm_c,
                 "fi_cache": fi_cache}, open(CACHE_PATH, "wb"))
    print(f"[build] Saved to {CACHE_PATH}", flush=True)


# ── Regime filter ─────────────────────────────────────────────────────────────

def iwm_stats(iwm_c: pd.Series, sig_date: date):
    """Return (day%, 5d%, 20d%) for IWM as of sig_date."""
    sub = iwm_c[iwm_c.index.date <= sig_date]
    if len(sub) < 2:
        return 0.0, 0.0, 0.0
    d1  = (sub.iloc[-1] / sub.iloc[-2] - 1) * 100
    d5  = (sub.iloc[-1] / sub.iloc[-min(6,  len(sub))] - 1) * 100 if len(sub) >=  6 else 0.0
    d20 = (sub.iloc[-1] / sub.iloc[-min(21, len(sub))] - 1) * 100 if len(sub) >= 21 else 0.0
    return float(d1), float(d5), float(d20)


def regime_gate(d1: float, d5: float, d20: float):
    """Return a gate reason string if we should sit out, else None."""
    if d1  <= -1.0: return f"IWM bear {d1:+.2f}%"
    if d5  <=  0.0: return f"IWM 5d {d5:+.2f}% (regime)"
    if d20 <=  0.0: return f"IWM 20d {d20:+.2f}% (regime)"
    return None


# ── Z-score helpers ───────────────────────────────────────────────────────────

def _zscore(vals):
    a = np.array(vals, dtype=float)
    mu, sd = np.nanmean(a), np.nanstd(a, ddof=1)
    return list((a - mu) / sd) if sd > 1e-9 else [0.0] * len(vals)


def _pct_z(vals):
    """Percentile-based z (robust to outliers); output clipped to ±3."""
    a = np.array(vals, dtype=float)
    n = len(a)
    if n < 3:
        return [0.0] * n
    ranks = a.argsort().argsort().astype(float)
    return list(np.clip((ranks / (n - 1) - 0.5) / 0.2887, -3, 3))


def _sharpe10(cl):
    """10-day daily-returns Sharpe for momentum quality factor."""
    if len(cl) < 12:
        return 0.0
    rets = [cl[i] / cl[i - 1] - 1 for i in range(-10, 0)]
    mu, sd = np.mean(rets), np.std(rets, ddof=1)
    return float(mu / sd) if sd > 1e-9 else 0.0


# ── V2 scorer (read-only copy of production logic) ───────────────────────────

def score_v2(cl: list, rvol_open: float) -> dict | None:
    """
    Production V2 nano-cap score.  Returns dict with keys:
        score, grade (STRONG/WATCH/SKIP), gap, mom10, rvol
    Returns None if RVOL gate fails or insufficient history.
    """
    if len(cl) < 11:
        return None
    if rvol_open < 3.0 or rvol_open > 60.0:
        return None

    gap   = (cl[-1] / cl[-2]  - 1) * 100
    mom10 = (cl[-1] / cl[-11] - 1) * 100

    gp = (35 if  2 <= gap < 5  else
          30 if  5 <= gap < 8  else
          15 if  8 <= gap < 12 else
           5 if 12 <= gap < 20 else
          10 if  0 <= gap < 2  else
           5 if gap < 0        else 0)

    mp = (22 if 10 <= mom10 < 20 else
          15 if 20 <= mom10 < 30 else
          12 if  5 <= mom10 < 10 else
           5 if 30 <= mom10 < 50 else 0)

    vp = (18 if  5 <= rvol_open < 15 else
          15 if  3 <= rvol_open <  5 else
          12 if 15 <= rvol_open < 30 else 5)

    m10_pts = (12 if 10 <= mom10 < 20 else
                8 if  5 <= mom10 < 10 else
                5 if 20 <= mom10 < 30 else 0)

    rsk = ((15 if gap >= 20 else 8 if gap >= 12 else 0) +
           (10 if mom10 >= 50 else 5 if mom10 >= 30 else 0))

    sc = max(0, gp + mp + vp + m10_pts - rsk)
    return {
        "score": sc,
        "grade": "STRONG" if sc >= 60 else "WATCH" if sc >= 40 else "SKIP",
        "gap":   round(gap,   1),
        "mom10": round(mom10, 1),
        "rvol":  round(rvol_open, 1),
    }


# ── Quant z-score engine ──────────────────────────────────────────────────────

def score_quant_batch(cands: list) -> list:
    """
    Cross-sectional quant z-score for a list of candidates on one trading day.

    Five factors (all normalised to z-scores within the day's candidate pool):
        gap_z    : gap% vs peers — who gapped hardest relative to the field
        mom_z    : weighted average of pct-rank z-scores for mom10, mom5, mom3
        qual_z   : 10-day return Sharpe — sustained up-trend vs one-day spike
        ft_z     : float turnover — (opening 15-min abs vol) / float_shares × 100%
        sq_z     : squeeze pressure — short_pct × (1 / days_to_cover)

    Weights: gap 20%, mom 30%, qual 20%, ft 15%, sq 15%.
    ft_z and sq_z are dropped (weight redistributed) when data is unavailable.

    Grade thresholds (cross-sectional, per-day — both conditions must hold):
        STRONG : top 15% AND composite_z ≥ 0.5
        WATCH  : top 30% AND composite_z > 0
        SKIP   : below either threshold
    """
    n = len(cands)
    if n < 3:
        return []

    gaps    = [c["gap"]   for c in cands]
    mom10s  = [c["mom10"] for c in cands]
    mom5s   = [c["mom5"]  for c in cands]
    mom3s   = [c["mom3"]  for c in cands]
    sharpes = [_sharpe10(c["cl"]) for c in cands]

    # Float turnover: approximate opening 15-min absolute volume / float_shares
    ft_raw = []
    for c in cands:
        fs = c.get("float_shares")
        if fs and fs > 0:
            vol15m = c["rvol_open"] * (c["vol20"] / 26.0)  # est. abs opening vol
            ft_raw.append(vol15m / fs * 100.0)
        else:
            ft_raw.append(np.nan)

    # Squeeze pressure: short_pct × (1 / days_to_cover)
    sq_raw = []
    for c in cands:
        sp = c.get("short_pct")
        sr = c.get("short_ratio")
        if sp and sr and sr > 0:
            sq_raw.append(float(sp) * (1.0 / float(sr)))
        else:
            sq_raw.append(np.nan)

    gap_z  = _zscore(gaps)
    mom_z  = [np.mean([a, b, c]) for a, b, c in
              zip(_pct_z(mom10s), _pct_z(mom5s), _pct_z(mom3s))]
    qual_z = _zscore(sharpes)

    ft_arr = np.array(ft_raw, dtype=float)
    ft_ok  = ~np.isnan(ft_arr)
    ft_z   = np.zeros(n)
    if ft_ok.sum() >= 3:
        v = ft_arr[ft_ok]
        mu, sd = np.nanmean(v), np.nanstd(v, ddof=1)
        if sd > 1e-9:
            ft_z[ft_ok] = (v - mu) / sd

    sq_arr = np.array(sq_raw, dtype=float)
    sq_ok  = ~np.isnan(sq_arr)
    sq_z   = np.zeros(n)
    if sq_ok.sum() >= 3:
        v = sq_arr[sq_ok]
        mu, sd = np.nanmean(v), np.nanstd(v, ddof=1)
        if sd > 1e-9:
            sq_z[sq_ok] = (v - mu) / sd

    results = []
    for i, c in enumerate(cands):
        factors = {"gap": gap_z[i], "mom": mom_z[i], "qual": qual_z[i]}
        weights = {"gap": 0.20,     "mom": 0.30,     "qual": 0.20}
        if ft_ok[i]:
            factors["ft"] = float(ft_z[i])
            weights["ft"] = 0.15
        if sq_ok[i]:
            factors["sq"] = float(sq_z[i])
            weights["sq"] = 0.15
        ws   = sum(weights.values())
        comp = sum(factors[k] * weights[k] / ws for k in factors)
        results.append({
            **c,
            "composite_z": round(float(comp), 3),
            "gap_z":        round(gap_z[i], 2),
            "mom_z":        round(mom_z[i], 2),
            "qual_z":       round(qual_z[i], 2),
            "ft_z":         round(float(ft_z[i]), 2),
            "sq_z":         round(float(sq_z[i]), 2),
        })

    results.sort(key=lambda x: x["composite_z"], reverse=True)
    n15 = max(1, int(n * 0.15))
    n30 = max(2, int(n * 0.30))
    for rank, r in enumerate(results):
        if rank < n15 and r["composite_z"] >= 0.5:
            r["grade"] = "STRONG"
        elif rank < n30 and r["composite_z"] > 0:
            r["grade"] = "WATCH"
        else:
            r["grade"] = "SKIP"
        r["rank"] = rank + 1
    return results


# ── Intraday RVOL ─────────────────────────────────────────────────────────────

def fetch_rvol_map(tickers: list, bt_date: date) -> dict:
    """
    Fetch 1-min bars for bt_date, return {ticker: opening_15min_volume}.
    Chunked 20 tickers at a time to avoid Yahoo rate limits.
    """
    import yfinance as yf

    bt_str   = bt_date.strftime("%Y-%m-%d")
    end_str  = (bt_date + timedelta(days=1)).strftime("%Y-%m-%d")
    ot_start = ET.localize(datetime(bt_date.year, bt_date.month, bt_date.day, 9, 30))
    ot_end   = ET.localize(datetime(bt_date.year, bt_date.month, bt_date.day, 9, 45))
    rvol_map = {}

    for chunk in [tickers[i:i + 20] for i in range(0, len(tickers), 20)]:
        try:
            iv = yf.download(chunk, start=bt_str, end=end_str,
                             interval="1m", auto_adjust=True,
                             progress=False, threads=True)
            if iv.empty:
                continue
            if isinstance(iv.columns, pd.MultiIndex):
                for t in chunk:
                    try:
                        vs = iv["Volume"][t].copy()
                        vs.index = vs.index.tz_convert(ET)
                        w = vs[(vs.index >= ot_start) & (vs.index < ot_end)].dropna()
                        if len(w) >= 3:
                            rvol_map[t] = float(w.sum())
                    except Exception:
                        pass
            elif len(chunk) == 1:
                vs = iv["Volume"].copy()
                vs.index = vs.index.tz_convert(ET)
                w = vs[(vs.index >= ot_start) & (vs.index < ot_end)].dropna()
                if len(w) >= 3:
                    rvol_map[chunk[0]] = float(w.sum())
        except Exception:
            pass
        time.sleep(0.25)
    return rvol_map


# ── Per-day runner ────────────────────────────────────────────────────────────

def run_day(bt_date: date, next_date, all_hist: dict, iwm_c: pd.Series,
            fi_cache: dict) -> tuple:
    """
    Returns (v2_strong, qt_strong, full_universe) where full_universe is every
    RVOL-gated candidate with its next-day return (None when unavailable).
    This lets print_report find big movers that BOTH systems missed, not just
    ones already captured in v2_strong or qt_strong.
    """
    bt_str = bt_date.strftime("%Y-%m-%d")
    d1, d5, d20 = iwm_stats(iwm_c, bt_date)
    gate = regime_gate(d1, d5, d20)
    print(f"\n── {bt_str}  IWM {d1:+.2f}% | 5d {d5:+.2f}% | 20d {d20:+.2f}%",
          end="", flush=True)

    if gate:
        print(f"  🚫 {gate}")
        return [], [], []

    # Daily pre-filter: gap 1-20%, mom10 3-17%, price > $0.50
    cands = []
    for ticker, hist in all_hist.items():
        cs = hist["close"]
        vs = hist["vol"]
        cu = cs[cs.index.date <= bt_date]
        vu = vs[vs.index.date <= bt_date]
        if len(cu) < 12:
            continue
        cl  = cu.tolist()
        vl  = vu.tolist()
        gap   = (cl[-1] / cl[-2]  - 1) * 100
        mom10 = (cl[-1] / cl[-11] - 1) * 100
        mom5  = (cl[-1] / cl[-6]  - 1) * 100 if len(cl) >= 7 else 0.0
        mom3  = (cl[-1] / cl[-4]  - 1) * 100 if len(cl) >= 5 else 0.0
        vol20 = sum(vl[-20:]) / min(20, len(vl))
        if 1 <= gap < 20 and 3 <= mom10 < 17 and cl[-1] > 0.50 and vol20 > 0:
            fi = fi_cache.get(ticker, {})
            cands.append({
                "ticker":       ticker,
                "cl":           cl,
                "vl":           vl,
                "gap":          round(gap,   1),
                "mom10":        round(mom10, 1),
                "mom5":         round(mom5,  1),
                "mom3":         round(mom3,  1),
                "vol20":        vol20,
                "float_shares": fi.get("float_shares"),
                "short_pct":    fi.get("short_pct"),
                "short_ratio":  fi.get("short_ratio"),
            })

    print(f"  pre-filter {len(cands)}", end="", flush=True)

    # Intraday RVOL gate
    tickers_c = [c["ticker"] for c in cands]
    rvol_map  = fetch_rvol_map(tickers_c, bt_date)

    cands_rv = []
    for c in cands:
        if c["ticker"] not in rvol_map:
            continue
        exp15 = c["vol20"] / 26.0 if c["vol20"] > 0 else 1.0
        rv    = rvol_map[c["ticker"]] / exp15
        if rv < 3.0 or rv > 60.0:
            continue
        cands_rv.append({**c, "rvol_open": round(rv, 1)})

    print(f" → rvol_gated {len(cands_rv)}", end="", flush=True)

    # Next-day return lookup
    def nret(ticker: str, entry: float) -> float | None:
        if next_date is None:
            return None
        cs2 = all_hist.get(ticker, {}).get("close")
        if cs2 is None:
            return None
        nc = cs2[cs2.index.date >= next_date]
        if len(nc) < 1:
            return None
        return round((float(nc.iloc[0]) / entry - 1) * 100, 1)

    # Score V2
    v2_strong = []
    for c in cands_rv:
        r = score_v2(c["cl"], c["rvol_open"])
        if not r or r["grade"] != "STRONG":
            continue
        nr = nret(c["ticker"], c["cl"][-1])
        if nr is None and next_date is not None:
            continue
        v2_strong.append({**r, "ticker": c["ticker"], "next_ret": nr,
                          "bt_date": bt_str})

    # Score Quant (cross-sectional on the RVOL-gated pool)
    qt_scored  = score_quant_batch(cands_rv)
    qt_strong  = []
    for r in qt_scored:
        if r["grade"] != "STRONG":
            continue
        nr = nret(r["ticker"], r["cl"][-1])
        if nr is None and next_date is not None:
            continue
        qt_strong.append({**r, "next_ret": nr, "bt_date": bt_str})

    # Day summary
    def _day_pl(trs):
        graded = [t for t in trs if t["next_ret"] is not None]
        if not graded:
            return 0, 0, 0.0
        wins = sum(1 for t in graded if t["next_ret"] > 0)
        pl   = sum(max(-50.0, 1000 * t["next_ret"] / 100) for t in graded)
        return len(graded), wins, pl

    vn, vw, vpl = _day_pl(v2_strong)
    qn, qw, qpl = _day_pl(qt_strong)
    pending_v   = len(v2_strong) - vn
    pending_q   = len(qt_strong) - qn
    pend_s = f"  ({pending_v}V/{pending_q}Q pending)" if (pending_v or pending_q) else ""
    print(f"  |  V2 {vn}tr {vw}W ${vpl:+.0f}  |  QT {qn}tr {qw}W ${qpl:+.0f}{pend_s}")

    # Full universe: every RVOL-gated candidate with its next-day return.
    # Needed for big-mover analysis to flag missed signals.
    full_universe = []
    for c in cands_rv:
        nr = nret(c["ticker"], c["cl"][-1])
        if nr is not None:
            full_universe.append({"ticker": c["ticker"], "next_ret": nr,
                                  "bt_date": bt_str, "week": ""})

    return (
        [t for t in v2_strong if t["next_ret"] is not None],
        [t for t in qt_strong  if t["next_ret"] is not None],
        full_universe,
    )


# ── Stats helpers ─────────────────────────────────────────────────────────────

def stats(trs: list) -> dict:
    if not trs:
        return dict(n=0, wins=0, pl=0.0, dep=0, wr=0.0, ret=0.0, avg_ret=0.0)
    rets = [t["next_ret"] for t in trs]
    pls  = [max(-50.0, 1000 * r / 100) for r in rets]
    wins = sum(1 for r in rets if r > 0)
    pl   = sum(pls)
    dep  = len(trs) * 1000
    return dict(n=len(trs), wins=wins, pl=pl, dep=dep,
                wr=wins / len(trs) * 100,
                ret=pl / dep * 100,
                avg_ret=float(np.mean(rets)))


# ── Master report ─────────────────────────────────────────────────────────────

def print_report(stored: dict):
    print("""
══════════════════════════════════════════════════════════════════════════════
  QUANT Z-SCORE vs V2  ─  3-WEEK HEAD-TO-HEAD BACKTEST
  Universe : Finviz nano-cap  (float<20M, price>$0.50, avgvol>20K)
  Shared gates: mom10>17% blocked | RVOL<3x or >60x blocked
  Regime:  IWM day≤-1% OR 5d≤0% OR 20d≤0%  → sit out entirely
  Sizing : $1,000/trade | 5% hard stop (−$50 max)
  Return : next-day close-to-close
══════════════════════════════════════════════════════════════════════════════""")

    hdr = (f"  {'Week':<12} │ {'V2 Alone':^28} │ {'Quant Alone':^28} │ "
           f"{'V2 + Quant':^28}")
    sub = (f"  {'':12} │ {'N':>3} {'WR%':>5} {'P&L':>8} {'Ret%':>6} │ "
           f"{'N':>3} {'WR%':>5} {'P&L':>8} {'Ret%':>6} │ "
           f"{'N':>3} {'WR%':>5} {'P&L':>8} {'Ret%':>6}")
    sep = "  " + "─" * 90
    print(hdr); print(sub); print(sep)

    tot_v, tot_q, tot_c, tot_full = [], [], [], []
    for lbl in WEEK_LABELS:
        if lbl not in stored:
            continue
        entry = stored[lbl]
        v2t, qtt = entry[0], entry[1]
        full_u   = entry[2] if len(entry) == 3 else []
        qt_set   = {(t["ticker"], t.get("bt_date","")) for t in qtt}
        combined = [t for t in v2t if (t["ticker"], t.get("bt_date","")) in qt_set]
        tot_v.extend(v2t); tot_q.extend(qtt); tot_c.extend(combined); tot_full.extend(full_u)
        vs = stats(v2t); qs = stats(qtt); cs = stats(combined)
        print(f"  {lbl:<12} │ {vs['n']:>3} {vs['wr']:>4.0f}% ${vs['pl']:>+7.0f} "
              f"{vs['ret']:>+5.1f}% │ "
              f"{qs['n']:>3} {qs['wr']:>4.0f}% ${qs['pl']:>+7.0f} {qs['ret']:>+5.1f}% │ "
              f"{cs['n']:>3} {cs['wr']:>4.0f}% ${cs['pl']:>+7.0f} {cs['ret']:>+5.1f}%")

    vs = stats(tot_v); qs = stats(tot_q); cs = stats(tot_c)
    print(sep)
    print(f"  {'3-WK TOTAL':<12} │ {vs['n']:>3} {vs['wr']:>4.0f}% ${vs['pl']:>+7.0f} "
          f"{vs['ret']:>+5.1f}% │ "
          f"{qs['n']:>3} {qs['wr']:>4.0f}% ${qs['pl']:>+7.0f} {qs['ret']:>+5.1f}% │ "
          f"{cs['n']:>3} {cs['wr']:>4.0f}% ${cs['pl']:>+7.0f} {cs['ret']:>+5.1f}%")

    # Quant-as-filter breakdown
    qt_set   = {(t["ticker"], t.get("bt_date","")) for t in tot_c}
    rejected = [t for t in tot_v if (t["ticker"], t.get("bt_date","")) not in qt_set]
    rs = stats(rejected)
    print(
        f"\n  QUANT AS FILTER ON V2 SIGNALS\n"
        f"  V2 signals Quant REJECTED : {rs['n']:>3}  {rs['wr']:>4.0f}% WR"
        f"  ${rs['pl']:>+7.0f}  {rs['ret']:>+5.1f}% return/capital\n"
        f"  V2 signals Quant CONFIRMED: {cs['n']:>3}  {cs['wr']:>4.0f}% WR"
        f"  ${cs['pl']:>+7.0f}  {cs['ret']:>+5.1f}% return/capital\n"
        f"  Win-rate lift: {vs['wr']:.0f}% → {cs['wr']:.0f}%"
        f" ({cs['wr']-vs['wr']:>+.0f} pp)\n"
        f"  Return lift  : {vs['ret']:.1f}% → {cs['ret']:.1f}%"
        f" ({cs['ret']-vs['ret']:>+.1f} pp)\n"
        f"  Trade filter : {vs['n']} → {cs['n']}"
        f" ({(vs['n']-cs['n'])/max(vs['n'],1)*100:.0f}% of V2 signals filtered out)"
    )

    # Quant-only per-trade breakdown (all quant STRONG trades, independent of V2)
    print(f"\n  QUANT ALONE — all {qs['n']} STRONG trades")
    print(f"  {'Ticker':<7} {'Week':<12} {'Qz':>7} {'Rnk':>4}  gap    m10    rv    → Ret%    P&L")
    for lbl in WEEK_LABELS:
        if lbl not in stored:
            continue
        entry = stored[lbl]
        qtt = entry[1]
        for t in sorted(qtt, key=lambda x: x.get("composite_z", 0), reverse=True):
            nr  = t["next_ret"]
            fla = "✓" if nr > 0 else "✗"
            print(f"  {t['ticker']:<7} {lbl:<12} "
                  f"{t.get('composite_z', 0):>+7.3f} {t.get('rank', 0):>4}  "
                  f"{t.get('gap', 0):>+5.1f}%  {t.get('mom10', 0):>+5.1f}%  "
                  f"{t.get('rvol', 0):>4.1f}x  → {nr:>+5.1f}%  {fla}  "
                  f"${max(-50, 1000 * nr / 100):>+5.0f}")

    # Combined pick detail
    print(f"\n  COMBINED PICKS — full detail ({cs['n']} trades where BOTH systems agree)")
    print(f"  {'Ticker':<7} {'Week':<12} {'V2sc':>5} {'Qz':>7}  gap    m10    rv    → Ret%    P&L")
    for lbl in WEEK_LABELS:
        if lbl not in stored:
            continue
        entry = stored[lbl]
        v2t, qtt = entry[0], entry[1]
        qt_map = {t["ticker"]: t for t in qtt}
        for t in sorted(v2t, key=lambda x: x.get("score", 0), reverse=True):
            if t["ticker"] not in qt_map:
                continue
            qt  = qt_map[t["ticker"]]
            nr  = t["next_ret"]
            fla = "✓" if nr > 0 else "✗"
            print(f"  {t['ticker']:<7} {lbl:<12} {t.get('score',0):>5} "
                  f"{qt['composite_z']:>+7.3f}  "
                  f"{t.get('gap',0):>+5.1f}%  {t.get('mom10',0):>+5.1f}%  "
                  f"{t.get('rvol',0):>4.1f}x  → {nr:>+5.1f}%  {fla}  "
                  f"${max(-50, 1000*nr/100):>+5.0f}")

    # ── >15% next-day mover analysis ─────────────────────────────────────────
    # Sources from the full RVOL-gated universe (not just picked signals) so
    # we can correctly report big movers that BOTH systems missed.
    # Use (ticker, bt_date) as unique trade key so same ticker on different
    # days is counted separately in the caught/missed analysis.
    v2_keys = {(t["ticker"], t.get("bt_date","")) for t in tot_v}
    qt_keys = {(t["ticker"], t.get("bt_date","")) for t in tot_q}
    cm_keys = {(t["ticker"], t.get("bt_date","")) for t in tot_c}

    # Big-mover table: one row per (ticker, bt_date) event ≥15%
    big_movers = {}
    for u in tot_full:
        nr = u.get("next_ret")
        if nr is not None and abs(nr) >= 15:
            key = (u["ticker"], u.get("bt_date",""))
            if key not in big_movers or abs(nr) > abs(big_movers[key]["ret"]):
                big_movers[key] = {"ret": nr, "week": u.get("week", ""),
                                   "ticker": u["ticker"]}

    if big_movers:
        print(f"\n  >15% NEXT-DAY MOVERS from full RVOL-gated universe — caught vs missed")
        print(f"  {'Ticker':<7} {'Date':<12} {'Week':<12} {'Ret%':>6}  V2?   QT?   Both?  Miss?")
        for key, info in sorted(big_movers.items(), key=lambda x: abs(x[1]["ret"]), reverse=True):
            v2c = "✓" if key in v2_keys else "–"
            qtc = "✓" if key in qt_keys else "–"
            cmc = "✓" if key in cm_keys else "–"
            missed = "MISS" if (v2c == "–" and qtc == "–") else ""
            print(f"  {info['ticker']:<7} {key[1]:<12} {info['week']:<12}"
                  f" {info['ret']:>+5.1f}%  {v2c:^5} {qtc:^5} {cmc:^5}  {missed}")
        total_big   = len(big_movers)
        v2_caught   = sum(1 for k in big_movers if k in v2_keys)
        qt_caught   = sum(1 for k in big_movers if k in qt_keys)
        cm_caught   = sum(1 for k in big_movers if k in cm_keys)
        both_missed = sum(1 for k in big_movers if k not in v2_keys and k not in qt_keys)
        print(f"\n  V2 caught {v2_caught}/{total_big} ({v2_caught/total_big*100:.0f}%)")
        print(f"  QT caught {qt_caught}/{total_big} ({qt_caught/total_big*100:.0f}%)")
        print(f"  Combined  {cm_caught}/{total_big} ({cm_caught/total_big*100:.0f}%)")
        print(f"  Both missed: {both_missed}/{total_big} ({both_missed/total_big*100:.0f}%)")


# ── Main ──────────────────────────────────────────────────────────────────────

def run_backtest():
    d        = pickle.load(open(CACHE_PATH, "rb"))
    all_hist = d["all_hist"]
    iwm_c    = d["iwm_c"]
    fi_cache = d["fi_cache"]

    stored: dict[str, tuple] = {}
    try:
        stored = pickle.load(open(RESULTS_PATH, "rb"))
    except Exception:
        pass

    weeks_done = set(stored.keys())

    for bt_date, next_date, week_lbl in TRADING_DAYS:
        if week_lbl in weeks_done:
            continue
        v2s, qts, full = run_day(bt_date, next_date, all_hist, iwm_c, fi_cache)
        # Tag each universe entry with its week label for mover reporting
        for u in full:
            u["week"] = week_lbl
        if week_lbl not in stored:
            stored[week_lbl] = ([], [], [])
        old_v2, old_qt, old_full = stored[week_lbl] if len(stored[week_lbl]) == 3 else (*stored[week_lbl], [])
        stored[week_lbl] = (old_v2 + v2s, old_qt + qts, old_full + full)
        pickle.dump(stored, open(RESULTS_PATH, "wb"))

    print_report(stored)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Quant z-score vs V2 backtest")
    parser.add_argument("--stage", choices=["build", "run", "all"],
                        default="run", help="build=fetch data, run=score, all=both")
    args = parser.parse_args()

    if args.stage in ("build", "all"):
        build_cache()
    if args.stage in ("run", "all"):
        run_backtest()
