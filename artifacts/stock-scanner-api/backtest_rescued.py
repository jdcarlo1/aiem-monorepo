"""
backtest_rescued.py — Can we rescue winners from the filtered buckets?
======================================================================
For each cap tier, looks INSIDE the 3 filtered buckets:
  1. Monday signals
  2. Extreme gain signals
  3. $15-$50 mid/small cap signals

Within each bucket, finds what separates winners from losers.
Candidates: RVOL level, close-range position, SPY direction, gap vs intraday.

Run:  python3 artifacts/stock-scanner-api/backtest_rescued.py
"""

import re, sys, time, statistics, datetime
from collections import defaultdict

try:
    import requests
    import yfinance as yf
except ImportError:
    sys.exit(1)

_FV_HDR = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}

def finviz_universe(filters, max_pages=5, min_vol=100_000):
    tickers, seen = [], set()
    for pg in range(max_pages):
        start = pg * 20 + 1
        url = f"https://finviz.com/screener.ashx?v=111&f={filters}&o=-volume&r={start}"
        try:
            r = requests.get(url, headers=_FV_HDR, timeout=15)
            if not r.ok: break
            new = 0
            for chunk in re.split(r'<tr class="styled-row', r.text)[1:]:
                cells = [re.sub(r"<[^>]+>", "", c).strip()
                         for c in re.findall(r"<td[^>]*>(.*?)</td>", chunk, re.S)]
                if len(cells) < 11: continue
                tk = cells[1].upper().strip()
                if not tk or len(tk) > 5 or "." in tk or tk in seen: continue
                try:
                    vol = int(cells[10].replace(",", ""))
                except Exception:
                    vol = 0
                if vol < min_vol: continue
                seen.add(tk); tickers.append(tk); new += 1
            if new == 0: break
            time.sleep(0.4)
        except Exception as e:
            print(f"  Finviz error pg {pg+1}: {e}"); break
    return tickers


def download_ohlcv(tickers, period="3mo"):
    result = {}
    for i in range(0, len(tickers), 100):
        batch = tickers[i:i+100]
        try:
            raw = yf.download(batch, period=period, interval="1d",
                              auto_adjust=True, progress=False, threads=True)
            if raw.empty: continue
            for tkr in batch:
                try:
                    s = len(batch)
                    o  = raw["Open"][tkr].dropna()   if s > 1 else raw["Open"].dropna()
                    h  = raw["High"][tkr].dropna()   if s > 1 else raw["High"].dropna()
                    lo = raw["Low"][tkr].dropna()    if s > 1 else raw["Low"].dropna()
                    c  = raw["Close"][tkr].dropna()  if s > 1 else raw["Close"].dropna()
                    v  = raw["Volume"][tkr].dropna() if s > 1 else raw["Volume"].dropna()
                    rows = []
                    for idx in c.index:
                        rows.append((idx.date(), float(o.get(idx,0)), float(h.get(idx,0)),
                                     float(lo.get(idx,0)), float(c[idx]), float(v.get(idx,0))))
                    if len(rows) >= 10:
                        result[tkr] = sorted(rows, key=lambda x: x[0])
                except Exception:
                    pass
        except Exception:
            pass
        if i + 100 < len(tickers):
            time.sleep(1)
    return result


def build_signals(ohlcv, spy_closes, threshold, extreme_cap, weak_price_lo, weak_price_hi):
    """
    Return (passing_signals, monday_signals, extreme_signals, price_zone_signals)
    Each signal dict includes full factor breakdown + outcome.
    """
    spy_dates = sorted(spy_closes.keys())

    def spy_ret(d1_date, n=5):
        try:
            idx = spy_dates.index(d1_date)
            d5 = spy_dates[idx + n] if idx + n < len(spy_dates) else None
            if d5 is None: return None
            return (spy_closes[d5] - spy_closes[d1_date]) / spy_closes[d1_date] * 100
        except (ValueError, IndexError):
            return None

    def spy_d1_move(d1_date):
        try:
            idx = spy_dates.index(d1_date)
            if idx < 1: return None
            d0 = spy_dates[idx - 1]
            return (spy_closes[d1_date] - spy_closes[d0]) / spy_closes[d0] * 100
        except (ValueError, IndexError):
            return None

    D5 = 5
    passing, mondays, extremes, price_zone = [], [], [], []

    for tkr, rows in ohlcv.items():
        for i in range(1, len(rows) - D5):
            d0, d1, d5 = rows[i-1], rows[i], rows[i+D5-1]
            d0c = d0[4]
            d1o, d1h, d1lo, d1c = d1[1], d1[2], d1[3], d1[4]
            d1v = d1[5]
            d5c = d5[4]
            d1_date = d1[0]
            if d0c <= 0 or d1c <= 0 or d5c <= 0: continue

            d1_gain = (d1c - d0c) / d0c * 100
            if d1_gain < threshold: continue

            d5_ret = (d5c - d1c) / d1c * 100
            won = d5_ret > 0

            gap_pct = (d1o - d0c) / d0c * 100
            intraday_pct = (d1c - d1o) / d0c * 100
            d1_range = d1h - d1lo
            crp = (d1c - d1lo) / d1_range if d1_range > 0 else 0.5
            dow = d1_date.weekday()
            spy_move = spy_d1_move(d1_date)

            # RVOL: compare current vol to prior 5-day average
            all_vols = [rows[j][5] for j in range(max(0, i-5), i) if rows[j][5] > 0]
            avg_vol = statistics.mean(all_vols) if all_vols else d1v
            rvol = d1v / avg_vol if avg_vol > 0 else 1.0

            sig = {
                "ticker": tkr, "date": d1_date, "d1_gain": d1_gain,
                "d5_ret": d5_ret, "won": won,
                "gap_pct": gap_pct, "intraday_pct": intraday_pct,
                "crp": crp, "dow": dow, "spy": spy_move, "price": d1c, "rvol": rvol,
            }

            # Route into filtered buckets
            is_monday = (dow == 0)
            is_extreme = (d1_gain > extreme_cap)
            is_price_zone = (weak_price_lo is not None and weak_price_lo <= d1c < weak_price_hi)

            if is_monday:
                mondays.append(sig)
            elif is_extreme:
                extremes.append(sig)
            elif is_price_zone:
                price_zone.append(sig)
            else:
                passing.append(sig)

    return passing, mondays, extremes, price_zone


def wr_avg(sigs):
    if not sigs: return 0, 0, 0
    n = len(sigs)
    w = sum(1 for s in sigs if s["won"])
    avg = statistics.mean(s["d5_ret"] for s in sigs)
    return round(w/n*100, 1), round(avg, 2), n


def print_rescue_analysis(bucket_name, sigs, color=""):
    if len(sigs) < 10:
        print(f"    (too few signals: {len(sigs)})")
        return

    base_wr, base_avg, n = wr_avg(sigs)
    print(f"\n  ── {bucket_name} (n={n}, baseline WR={base_wr}%, avg={base_avg:+.2f}%)")
    print(f"     Question: what do the WINNERS in this filtered bucket have in common?")
    print()

    factors = [
        ("RVOL level", "rvol",
         [(0, 1.5), (1.5, 2.5), (2.5, 4.0), (4.0, 99)],
         ["RVOL < 1.5x", "RVOL 1.5–2.5x", "RVOL 2.5–4x", "RVOL > 4x"]),

        ("Close-range position", "crp",
         [(0, 0.5), (0.5, 0.7), (0.7, 0.85), (0.85, 1.01)],
         ["Gave back >50% of range", "Middle (50–70%)", "Upper (70–85%)", "Closed near HIGH (85%+)"]),

        ("SPY direction on D1", "spy",
         [(-99, -0.5), (-0.5, 0.5), (0.5, 2), (2, 99)],
         ["SPY down >0.5%", "SPY flat", "SPY up 0.5–2%", "SPY up >2%"]),

        ("Gap vs intraday", "gap_pct",
         [(-99, 0), (0, 2), (2, 5), (5, 99)],
         ["Gap-down open", "Small gap (0–2%)", "Large gap (2–5%)", "Massive gap >5%"]),
    ]

    best_rescue = []

    for fname, fkey, bins, labels in factors:
        print(f"     {fname}:")
        for i, (lo, hi) in enumerate(bins):
            grp = [s for s in sigs if s.get(fkey) is not None and lo <= s[fkey] < hi]
            if len(grp) < 8: continue
            gwr, gavg, gn = wr_avg(grp)
            delta = gwr - base_wr
            bar = "█" * int(gwr/5) + "░" * (20 - int(gwr/5))
            flag = ""
            if gwr >= base_wr + 10:
                flag = "  🟢 RESCUE SIGNAL"
                best_rescue.append((fname, labels[i], gwr, gavg, gn, delta))
            elif gwr <= base_wr - 8:
                flag = "  ❌ DOUBLE-FILTER"
            print(f"       {labels[i]:<32}  n={gn:>4}  WR={gwr:>5.1f}%  avg={gavg:>+6.2f}%  {bar}{flag}")
        print()

    if best_rescue:
        print(f"     ✅ RESCUE CANDIDATES (add these as secondary filter to UN-skip this bucket):")
        for fname, label, gwr, gavg, gn, delta in sorted(best_rescue, key=lambda x: -x[2]):
            print(f"       → {fname}: [{label}]  WR={gwr:.1f}% (+{delta:.1f}pp vs bucket)  avg={gavg:+.2f}%  n={gn}")
    else:
        print(f"     ℹ️  No clear rescue signal found — bucket filter is justified as-is")


TIERS = [
    {"name": "LARGE CAP", "filter": "cap_large,sh_opt_option",
     "min_vol": 500_000, "pages": 6, "thr": 3.0, "extreme_cap": 15.0,
     "wpz": None},
    {"name": "MID CAP",   "filter": "cap_mid,sh_opt_option",
     "min_vol": 200_000, "pages": 6, "thr": 5.0, "extreme_cap": 15.0,
     "wpz": (15.0, 50.0)},
    {"name": "SMALL CAP", "filter": "cap_small,sh_opt_option",
     "min_vol": 100_000, "pages": 6, "thr": 7.0, "extreme_cap": 17.0,
     "wpz": (15.0, 50.0)},
]


def main():
    print("Downloading SPY...")
    spy_raw = yf.download("SPY", period="3mo", interval="1d", auto_adjust=True, progress=False)
    spy_closes = {}
    if not spy_raw.empty:
        close_col = spy_raw["Close"]
        if hasattr(close_col, "squeeze"):
            close_col = close_col.squeeze()
        for idx, v in close_col.items():
            try: spy_closes[idx.date()] = float(v)
            except: pass
    print(f"SPY: {len(spy_closes)} days\n")

    for cfg in TIERS:
        print("═" * 68)
        print(f"  {cfg['name']}  —  threshold ≥{cfg['thr']}%")
        print("═" * 68)
        print(f"  Pulling Finviz universe...")
        tickers = finviz_universe(cfg["filter"], max_pages=cfg["pages"], min_vol=cfg["min_vol"])
        print(f"  {len(tickers)} tickers — downloading...")
        ohlcv = download_ohlcv(tickers)
        wpz = cfg["wpz"]
        wpz_lo = wpz[0] if wpz else None
        wpz_hi = wpz[1] if wpz else None

        passing, mondays, extremes, price_zone = build_signals(
            ohlcv, spy_closes, cfg["thr"], cfg["extreme_cap"], wpz_lo, wpz_hi)

        pwr, pavg, pn = wr_avg(passing)
        print(f"\n  Passing (kept) signals: n={pn}  WR={pwr}%  avg={pavg:+.2f}%")
        print(f"  Filtered out: Monday={len(mondays)}  Extreme={len(extremes)}  PriceZone={len(price_zone)}")

        print(f"\n  ═══ ANALYSIS: Can we rescue winners from each filtered bucket? ═══")

        print_rescue_analysis("MONDAY signals", mondays)
        print_rescue_analysis("EXTREME GAIN signals", extremes)
        if price_zone:
            print_rescue_analysis(f"PRICE ZONE $15–$50 signals", price_zone)

        print()

    print("═" * 68)
    print("  SUMMARY:")
    print("  🟢 RESCUE SIGNAL = a sub-filter within the filtered bucket that")
    print("     achieves WR ≥ (bucket_baseline + 10pp) → worth adding as")
    print("     an exception rule to un-skip those specific signals")
    print("  ❌ DOUBLE-FILTER = sub-bucket so bad it confirms the main filter")
    print("  ℹ️  No rescue = original filter is working fine as-is")
    print("═" * 68)


if __name__ == "__main__":
    main()
