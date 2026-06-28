"""
backtest_deeper.py — Find NEW indicators in the filtered-out losers
====================================================================
Goes beyond the 7 factors already tested. For every signal that passed
our v2 filters, splits winners vs losers and compares 6 NEW metrics:

  1. ATR multiple      — D1 move / 5-day ATR (is this move "genuine"?)
  2. Prior-day gain    — was the stock ALREADY running the day before?
  3. Consecutive up    — how many days in a row going into D1?
  4. Body ratio        — strong-body candle vs wick-heavy / doji
  5. Volume trend      — was volume building the 3 days before D1?
  6. Recent-high dist  — breaking out to 10-day high vs ceiling play?

Prints side-by-side winner vs loser averages + WR-by-bucket tables.
"""

import re, sys, time, statistics
import datetime
from collections import defaultdict

try:
    import requests
    import yfinance as yf
except ImportError:
    sys.exit(1)

_FV_HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36"}

# ── Finviz ──────────────────────────────────────────────────────────────────

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
                              auto_adjust=False, progress=False, threads=True)
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
                    if len(rows) >= 15:
                        result[tkr] = sorted(rows, key=lambda x: x[0])
                except Exception:
                    pass
        except Exception:
            pass
        if i+100 < len(tickers): time.sleep(1)
    return result


# ── Signal builder ────────────────────────────────────────────────────────────

def build_deep_signals(ohlcv, thr, extreme_cap, wpz, tier_key):
    D5 = 5
    signals = []

    for tkr, rows in ohlcv.items():
        for i in range(5, len(rows) - D5):   # need 5 days of history
            d0 = rows[i-1]; d1 = rows[i]; d5 = rows[i+D5-1]
            d0c = d0[4]; d1o = d1[1]; d1h = d1[2]; d1lo = d1[3]; d1c = d1[4]; d1v = d1[5]
            d5c = d5[4]; d1_date = d1[0]

            if d0c <= 0 or d1c <= 0 or d5c <= 0: continue

            d1_gain = (d1c - d0c) / d0c * 100
            if d1_gain < thr: continue

            gap_pct = (d1o - d0c) / d0c * 100
            dow     = d1_date.weekday()

            # ── Apply v2 filter logic ─────────────────────────────────────
            is_monday  = (dow == 0)
            is_extreme = (d1_gain > extreme_cap)
            in_wpz     = (wpz and wpz[0] <= d1c < wpz[1])

            mon_rescued = (is_monday and gap_pct < 0 and tier_key in ("large","small"))
            ext_rescued = False  # small-cap extreme needs RVOL; computed below

            if in_wpz: continue   # hard filter, no rescue
            if is_monday and not mon_rescued: continue

            # RVOL
            prev_vols = [rows[j][5] for j in range(i-5, i) if rows[j][5] > 0]
            avg_vol   = statistics.mean(prev_vols) if prev_vols else d1v
            rvol      = d1v / avg_vol if avg_vol > 0 else 1.0

            if is_extreme:
                ext_rescued = (tier_key == "small" and 0 <= gap_pct < 2 and rvol >= 4.0)
                if not ext_rescued: continue

            # ── Passed — now compute new deep indicators ──────────────────

            # 1. ATR multiple: D1 gain / 5-day ATR
            atrs = []
            for j in range(i-5, i):
                ph = rows[j][2]; pl = rows[j][3]; pc = rows[j-1][4] if j > 0 else rows[j][4]
                atrs.append(max(ph - pl, abs(ph - pc), abs(pl - pc)))
            atr5     = statistics.mean(atrs) if atrs else 0
            d1_range_abs = d1h - d1lo
            atr_mult = d1_range_abs / atr5 if atr5 > 0 else 1.0

            # 2. Prior-day gain (D0 close vs D-1 close)
            dm1 = rows[i-2] if i >= 2 else None
            prior_gain = ((d0c - dm1[4]) / dm1[4] * 100) if dm1 and dm1[4] > 0 else 0.0

            # 3. Consecutive up days going into D1
            consec = 0
            for j in range(i-1, max(i-6, 0), -1):
                if rows[j][4] > rows[j-1][4]:
                    consec += 1
                else:
                    break

            # 4. Body ratio: |close - open| / (high - low)
            body     = abs(d1c - d1o)
            d1_range = d1h - d1lo
            body_ratio = body / d1_range if d1_range > 0 else 0.5
            # positive body = closed higher than opened (green candle)
            green_body = (d1c >= d1o)

            # 5. Volume trend: was volume increasing the 3 days before D1?
            vol3 = [rows[j][5] for j in range(i-3, i) if rows[j][5] > 0]
            if len(vol3) >= 3:
                vol_trend = (vol3[-1] - vol3[0]) / vol3[0] * 100  # % change d-3 to d-1
            else:
                vol_trend = 0.0

            # 6. Distance from 10-day high: close / max(high of last 10 days)
            recent_highs = [rows[j][2] for j in range(max(0, i-10), i)]
            max_10d      = max(recent_highs) if recent_highs else d1c
            near_high    = d1c / max_10d  # 1.0 = new 10-day high; <1 = below ceiling

            d5_ret = (d5c - d1c) / d1c * 100
            won    = d5_ret > 0

            signals.append({
                "ticker":     tkr,
                "date":       d1_date,
                "won":        won,
                "d5_ret":     d5_ret,
                "d1_gain":    d1_gain,
                "atr_mult":   atr_mult,
                "prior_gain": prior_gain,
                "consec":     consec,
                "body_ratio": body_ratio,
                "green_body": green_body,
                "vol_trend":  vol_trend,
                "near_high":  near_high,
                "rvol":       rvol,
            })
    return signals


# ── Display helpers ───────────────────────────────────────────────────────────

def compare_factor(title, signals, key, bins, labels):
    print(f"\n  {title}:")
    base_wr = sum(1 for s in signals if s["won"]) / len(signals) * 100 if signals else 0
    found_rescue = False
    for i, (lo, hi) in enumerate(bins):
        grp = [s for s in signals if lo <= s.get(key, 0) < hi]
        if len(grp) < 8: continue
        wr  = sum(1 for s in grp if s["won"]) / len(grp) * 100
        avg = statistics.mean(s["d5_ret"] for s in grp)
        delta = wr - base_wr
        bar = "█"*int(wr/5) + "░"*(20-int(wr/5))
        flag = ""
        if delta >= 10:
            flag = "  🟢 NEW INDICATOR — filter IN this zone"
            found_rescue = True
        elif delta <= -8:
            flag = "  🔴 AVOID — filter OUT this zone"
            found_rescue = True
        print(f"    {labels[i]:<36}  n={len(grp):>5}  WR={wr:>5.1f}%  avg={avg:>+6.2f}%  {bar}{flag}")
    return found_rescue


def print_winner_loser_diff(signals):
    winners = [s for s in signals if s["won"]]
    losers  = [s for s in signals if not s["won"]]
    if not winners or not losers: return

    def avg(lst, key): return round(statistics.mean(s[key] for s in lst), 2)

    print(f"\n  WINNER vs LOSER average comparison (n={len(winners)} wins / {len(losers)} losses):")
    print(f"  {'Metric':<28}  {'Winners':>10}  {'Losers':>10}  {'Diff':>8}  Signal?")
    print(f"  {'─'*28}  {'─'*10}  {'─'*10}  {'─'*8}  {'─'*18}")
    rows = [
        ("ATR multiple",       "atr_mult",   ">",  1.0),
        ("Prior-day gain (%)", "prior_gain", "<",  0.0),
        ("Consecutive up days","consec",     "<",  0.0),
        ("Body ratio",         "body_ratio", ">",  0.0),
        ("Volume trend (%)",   "vol_trend",  ">",  0.0),
        ("Near-high ratio",    "near_high",  ">",  0.95),
        ("RVOL",               "rvol",       ">",  2.0),
    ]
    for label, key, direction, threshold in rows:
        w = avg(winners, key)
        l = avg(losers,  key)
        diff = w - l
        # Signal if winners are meaningfully different from losers
        meaningful = abs(diff) > abs(w) * 0.12  # >12% relative difference
        sig = "✅ MEANINGFUL" if meaningful else ""
        print(f"  {label:<28}  {w:>10.3f}  {l:>10.3f}  {diff:>+8.3f}  {sig}")


# ── Tier configs ──────────────────────────────────────────────────────────────

TIERS = [
    {"name": "LARGE CAP", "filter": "cap_large,sh_opt_option",
     "min_vol": 500_000, "pages": 6, "thr": 3.0, "ecap": 15.0, "wpz": None, "key": "large"},
    {"name": "MID CAP",   "filter": "cap_mid,sh_opt_option",
     "min_vol": 200_000, "pages": 6, "thr": 5.0, "ecap": 15.0, "wpz": (15.0, 50.0), "key": "mid"},
    {"name": "SMALL CAP", "filter": "cap_small,sh_opt_option",
     "min_vol": 100_000, "pages": 6, "thr": 7.0, "ecap": 17.0, "wpz": (15.0, 50.0), "key": "small"},
]


def main():
    print("Downloading data — running deeper indicator analysis...\n")
    all_findings = []

    for cfg in TIERS:
        print("═" * 72)
        print(f"  {cfg['name']}  (≥{cfg['thr']}% D1 gain, v2 filter pool)")
        print("═" * 72)
        tickers = finviz_universe(cfg["filter"], max_pages=cfg["pages"], min_vol=cfg["min_vol"])
        print(f"  {len(tickers)} tickers — downloading...")
        ohlcv = download_ohlcv(tickers)
        sigs  = build_deep_signals(ohlcv, cfg["thr"], cfg["ecap"], cfg["wpz"], cfg["key"])

        base_wr = sum(1 for s in sigs if s["won"]) / len(sigs) * 100 if sigs else 0
        print(f"\n  {len(sigs)} signals in v2 pool  |  Baseline WR={base_wr:.1f}%\n")

        print_winner_loser_diff(sigs)

        # ── Factor breakdown tables ───────────────────────────────────────
        findings = []

        f1 = compare_factor(
            "Factor 1: ATR Multiple (D1 range / 5-day ATR) — is this move 'real'?",
            sigs, "atr_mult",
            [(0, 0.8), (0.8, 1.2), (1.2, 2.0), (2.0, 3.0), (3.0, 99)],
            ["Below ATR (<0.8x — weak move)",
             "Near ATR (0.8–1.2x — normal)",
             "1.2–2x ATR (strong)",
             "2–3x ATR (very strong)",
             "Above 3x ATR (extreme / spike)"])
        findings.append(("ATR Multiple", f1))

        f2 = compare_factor(
            "Factor 2: Prior-day gain (D0) — was the stock ALREADY running?",
            sigs, "prior_gain",
            [(-99, -2), (-2, 0), (0, 2), (2, 5), (5, 99)],
            ["D0 was down >2% (dip before rip)",
             "D0 down 0–2%",
             "D0 flat (0–2%)",
             "D0 up 2–5%",
             "D0 already up >5% (back-to-back)"])
        findings.append(("Prior-Day Gain", f2))

        f3 = compare_factor(
            "Factor 3: Consecutive up days going into D1 — exhaustion signal?",
            sigs, "consec",
            [(0, 1), (1, 2), (2, 3), (3, 4), (4, 10)],
            ["First up day (fresh move)",
             "2nd up day",
             "3rd up day",
             "4th up day",
             "5th+ up day (extended run)"])
        findings.append(("Consecutive Days", f3))

        f4 = compare_factor(
            "Factor 4: Body ratio (candle body / total range) — conviction in the close?",
            sigs, "body_ratio",
            [(0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01)],
            ["Doji / near-doji (<20% body)",
             "Small body (20–40%)",
             "Medium body (40–60%)",
             "Large body (60–80%)",
             "Very strong body (>80%)"])
        findings.append(("Body Ratio", f4))

        f5 = compare_factor(
            "Factor 5: Pre-D1 volume trend (3-day vol trend into D1) — building interest?",
            sigs, "vol_trend",
            [(-99, -30), (-30, -10), (-10, 10), (10, 30), (30, 99)],
            ["Vol falling fast (>30% drop)",
             "Vol declining (10–30% drop)",
             "Vol flat (±10%)",
             "Vol building (10–30% rise)",
             "Vol surging (>30% rise before D1)"])
        findings.append(("Volume Trend", f5))

        f6 = compare_factor(
            "Factor 6: Distance from 10-day high — breaking out vs hitting ceiling?",
            sigs, "near_high",
            [(0, 0.85), (0.85, 0.93), (0.93, 0.97), (0.97, 0.995), (0.995, 1.001)],
            ["Far from high (<85%) — deep ceiling",
             "Below (85–93%)",
             "Near (93–97%)",
             "Just below (97–99.5%)",
             "At/above 10-day high — breakout"])
        findings.append(("Near-High Breakout", f6))

        tier_finds = [(n, found) for n, found in findings if found]
        all_findings.append((cfg["name"], tier_finds, base_wr))
        print()

    # ── Cross-tier summary ─────────────────────────────────────────────────
    print("\n" + "═" * 72)
    print("  CROSS-TIER SUMMARY — New indicators worth adding")
    print("═" * 72)
    print("  If a factor appears consistently across 2-3 tiers → add it to the scanner.\n")
    counter = defaultdict(int)
    for tier_name, finds, bwr in all_findings:
        for fname, _ in finds:
            counter[fname] += 1
        print(f"  {tier_name} (base WR={bwr:.1f}%): {', '.join(n for n,_ in finds) or 'none found'}")
    print()
    consistent = [n for n, cnt in counter.items() if cnt >= 2]
    if consistent:
        print(f"  ✅ CONSISTENT ACROSS ≥2 TIERS → worth implementing:")
        for n in consistent:
            print(f"     → {n}")
    else:
        print("  ℹ️  No single factor dominates across all tiers — signals are noisy.")
    print("═" * 72)


if __name__ == "__main__":
    main()
