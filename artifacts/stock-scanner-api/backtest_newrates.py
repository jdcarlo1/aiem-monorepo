"""
backtest_newrates.py — Project win rates under new combined filter logic
========================================================================
Simulates the exact filter + rescue logic now in multiday_runner.py:
  - Monday skip + gap-DOWN exception (per tier where it helps)
  - Extreme gain cap + small-cap rescue (gap 0-2% + RVOL > 4x)
  - Price zone filter ($15-$50 mid/small)

Prints before/after table for every tier.
"""

import re, sys, time, statistics, datetime
from collections import defaultdict

try:
    import requests
    import yfinance as yf
except ImportError:
    sys.exit(1)

_FV_HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36"}

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
                cells = [re.sub(r"<[^>]+>","",c).strip()
                         for c in re.findall(r"<td[^>]*>(.*?)</td>",chunk,re.S)]
                if len(cells) < 11: continue
                tk = cells[1].upper().strip()
                if not tk or len(tk)>5 or "." in tk or tk in seen: continue
                try: vol = int(cells[10].replace(",",""))
                except: vol = 0
                if vol < min_vol: continue
                seen.add(tk); tickers.append(tk); new += 1
            if new == 0: break
            time.sleep(0.4)
        except Exception as e:
            print(f"  Finviz pg {pg+1}: {e}"); break
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
                    o  = raw["Open"][tkr].dropna()   if s>1 else raw["Open"].dropna()
                    h  = raw["High"][tkr].dropna()   if s>1 else raw["High"].dropna()
                    lo = raw["Low"][tkr].dropna()    if s>1 else raw["Low"].dropna()
                    c  = raw["Close"][tkr].dropna()  if s>1 else raw["Close"].dropna()
                    v  = raw["Volume"][tkr].dropna() if s>1 else raw["Volume"].dropna()
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
        if i+100 < len(tickers): time.sleep(1)
    return result


def simulate(ohlcv, spy_closes, thr, extreme_cap, wpz, tier_key):
    """
    Returns dict with signal counts and WRs under three scenarios:
      - raw:     no filters (baseline threshold only)
      - v1:      original filters (Monday block, extreme cap, price zone) — no rescues
      - v2:      new filters (v1 + Monday gap-down exception + small extreme rescue)
    """
    spy_dates = sorted(spy_closes.keys())
    D5 = 5

    raw, v1, v2 = [], [], []

    for tkr, rows in ohlcv.items():
        for i in range(1, len(rows) - D5):
            d0, d1, d5 = rows[i-1], rows[i], rows[i+D5-1]
            d0c = d0[4]
            d1o = d1[1]; d1c = d1[4]; d1v = d1[5]
            d5c = d5[4]
            d1_date = d1[0]
            if d0c <= 0 or d1c <= 0 or d5c <= 0: continue

            d1_gain = (d1c - d0c) / d0c * 100
            if d1_gain < thr: continue

            d5_ret = (d5c - d1c) / d1c * 100
            won    = d5_ret > 0
            gap_pct = (d1o - d0c) / d0c * 100
            dow     = d1_date.weekday()

            # RVOL
            all_vols = [rows[j][5] for j in range(max(0, i-5), i) if rows[j][5] > 0]
            avg_vol  = statistics.mean(all_vols) if all_vols else d1v
            rvol     = d1v / avg_vol if avg_vol > 0 else 1.0

            sig = {"won": won, "d5_ret": d5_ret}
            raw.append(sig)

            # ─── v1: original filters ────────────────────────────────────
            is_monday   = (dow == 0)
            is_extreme  = (d1_gain > extreme_cap)
            in_wpz      = (wpz and wpz[0] <= d1c < wpz[1])

            if not is_monday and not is_extreme and not in_wpz:
                v1.append(sig)

            # ─── v2: rescues applied ──────────────────────────────────────
            # Monday exception: gap-down on Monday = genuine intraday move
            # Data shows this works for large (+15pp) and small (+13pp).
            # For mid cap it only gives +8pp so we don't apply it for mid.
            monday_rescued = (
                is_monday and gap_pct < 0
                and tier_key in ("large", "small")
            )

            # Small-cap extreme rescue: gap 0-2% + RVOL > 4x
            extreme_rescued = (
                is_extreme
                and tier_key == "small"
                and 0 <= gap_pct < 2
                and rvol >= 4.0
            )

            passes_v2 = (
                (not is_monday or monday_rescued)
                and (not is_extreme or extreme_rescued)
                and not in_wpz
            )
            if passes_v2:
                v2.append(sig)

    def stats(sigs):
        if not sigs: return 0, 0, 0
        n   = len(sigs)
        wr  = sum(1 for s in sigs if s["won"]) / n * 100
        avg = statistics.mean(s["d5_ret"] for s in sigs)
        return round(wr, 1), round(avg, 2), n

    return {
        "raw": stats(raw),
        "v1":  stats(v1),
        "v2":  stats(v2),
    }


TIERS = [
    {"name": "LARGE CAP", "filter": "cap_large,sh_opt_option",
     "min_vol": 500_000, "pages": 6, "thr": 3.0, "ecap": 15.0, "wpz": None, "key": "large"},
    {"name": "MID CAP",   "filter": "cap_mid,sh_opt_option",
     "min_vol": 200_000, "pages": 6, "thr": 5.0, "ecap": 15.0, "wpz": (15.0, 50.0), "key": "mid"},
    {"name": "SMALL CAP", "filter": "cap_small,sh_opt_option",
     "min_vol": 100_000, "pages": 6, "thr": 7.0, "ecap": 17.0, "wpz": (15.0, 50.0), "key": "small"},
]

SEP = "─" * 72

def main():
    print("Downloading SPY...")
    spy_raw = yf.download("SPY", period="3mo", interval="1d", auto_adjust=True, progress=False)
    spy_closes = {}
    if not spy_raw.empty:
        cc = spy_raw["Close"].squeeze()
        for idx, v in cc.items():
            try: spy_closes[idx.date()] = float(v)
            except: pass
    print(f"SPY: {len(spy_closes)} trading days\n")

    summary_rows = []

    for cfg in TIERS:
        print(f"{SEP}")
        print(f"  {cfg['name']}  (threshold ≥{cfg['thr']}%)")
        print(f"{SEP}")
        tickers = finviz_universe(cfg["filter"], max_pages=cfg["pages"], min_vol=cfg["min_vol"])
        print(f"  {len(tickers)} tickers — downloading...")
        ohlcv = download_ohlcv(tickers)
        result = simulate(ohlcv, spy_closes, cfg["thr"], cfg["ecap"], cfg["wpz"], cfg["key"])

        raw_wr, raw_avg, raw_n = result["raw"]
        v1_wr,  v1_avg,  v1_n  = result["v1"]
        v2_wr,  v2_avg,  v2_n  = result["v2"]

        def bar(wr): return "█"*int(wr/4) + "░"*(25-int(wr/4))

        print(f"\n  {'Scenario':<38}  {'n':>6}  {'Win Rate':>9}  {'Avg D5':>8}  Chart")
        print(f"  {'─'*38}  {'─'*6}  {'─'*9}  {'─'*8}  {'─'*25}")
        print(f"  {'Raw (no filters)':<38}  {raw_n:>6}  {raw_wr:>8.1f}%  {raw_avg:>+7.2f}%  {bar(raw_wr)}")
        print(f"  {'v1 — original filters':<38}  {v1_n:>6}  {v1_wr:>8.1f}%  {v1_avg:>+7.2f}%  {bar(v1_wr)}")
        print(f"  {'v2 — + rescue exceptions  ◄ LIVE':<38}  {v2_n:>6}  {v2_wr:>8.1f}%  {v2_avg:>+7.2f}%  {bar(v2_wr)}")

        delta_n  = v2_n - v1_n
        delta_wr = v2_wr - v1_wr
        rescued  = v2_n - v1_n
        print(f"\n  Signals rescued back in: +{rescued}  |  WR change: {delta_wr:+.1f}pp")
        print()
        summary_rows.append((cfg["name"], raw_wr, v1_wr, v2_wr, v1_n, v2_n))

    print(f"\n{'═'*72}")
    print(f"  FINAL SUMMARY — ALL TIERS")
    print(f"{'═'*72}")
    print(f"  {'Tier':<16}  {'Raw WR':>8}  {'v1 WR':>8}  {'v2 WR (live)':>13}  {'Change':>8}")
    print(f"  {'─'*16}  {'─'*8}  {'─'*8}  {'─'*13}  {'─'*8}")
    total_r, total_v1, total_v2 = [], [], []
    for name, rwr, v1wr, v2wr, v1n, v2n in summary_rows:
        delta = v2wr - v1wr
        arrow = "⬆" if delta > 0 else ("⬇" if delta < 0 else "─")
        print(f"  {name:<16}  {rwr:>7.1f}%  {v1wr:>7.1f}%  {v2wr:>12.1f}%  {arrow} {abs(delta):.1f}pp")
    print(f"\n  v1 = Monday skip + extreme cap + price zone (last session)")
    print(f"  v2 = v1 + Monday gap-down rescue + small-cap extreme rescue")
    print(f"{'═'*72}\n")


if __name__ == "__main__":
    main()
