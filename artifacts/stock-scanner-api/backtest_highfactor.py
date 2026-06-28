"""
backtest_highfactor.py — 4 through 7-factor combination search
==============================================================
Tests every 4, 5, 6, and 7-factor combination of the TOP 12 gates (pre-screened
from backtest_combo60.py) to answer: does stacking more factors push WR higher,
or do sample sizes collapse into noise?

STATISTICAL GUARD:
  min_n = 30 for ALL factor levels.  Any combo with fewer signals is excluded.
  Wilson 95% confidence interval is shown for every result.  A 70% WR on n=15
  is just noise — the CI will tell you clearly.

THRESHOLD OPTIMIZER:
  For the 3 dominant pillars (10-day magnet, 20-day downtrend, 52wk proximity)
  we scan every threshold increment to find the EXACT optimal cutoffs, not just
  the arbitrary ones used in backtest_combo60.py.

EXPECTED RESULT (from real quant research):
  4-factor:   WR rises modestly, n still large enough to trust (~150+)
  5-factor:   WR gains 2-4pp but n starts to thin (<80)
  6-factor:   Marginal WR gain, most combos fall below n=30 (excluded)
  7-factor:   Almost all combos excluded — very few survive n≥30
The sweet spot is almost always 3-4 factors in daily equity signals.
"""

import re, sys, time, math, statistics, itertools
from collections import defaultdict

try:
    import requests, yfinance as yf
except ImportError:
    sys.exit(1)

_FV_HDR = {"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0"}

# ── Top 12 gates from backtest_combo60 (appear in top-5 combos across ≥2 tiers) ──
# Using only gates that have demonstrated cross-tier reliability.
# Testing all 16 gates would give C(16,7)=11440 combos — most pure noise.
TOP_GATES = [
    "K_near_10d_high",    # 10d magnet zone  (11 tiers - strongest predictor)
    "D2_downtrend",       # 20d downtrend reversal (9 tiers)
    "B_52wk_near",        # near 52wk high - breakout zone (5 tiers, large/mid)
    "B2_52wk_far",        # deep below 52wk high - recovery (2 tiers, mid cap)
    "C_close_top",        # closed top 30% of range (3 tiers)
    "J_body_strong",      # strong green body (3 tiers)
    "F_d0_quiet",         # D0 quiet < 1.5% (3 tiers)
    "E_atr_sweet",        # ATR normal range (2 tiers)
    "G_vol_calm",         # vol trend calm (2 tiers)
    "H_first_day",        # first/2nd up day (2 tiers)
    "A_spy_strength",     # SPY DOWN, stock UP (2 tiers — small cap powerhouse)
    "A2_spy_confirm",     # SPY UP confirming (2 tiers)
]

GATE_LABELS = {
    "K_near_10d_high":  "10d magnet (83-97.5% of 10d high)",
    "D2_downtrend":     "20d downtrend reversal",
    "B_52wk_near":      "Near 52wk high (88-105%)",
    "B2_52wk_far":      "Deep below 52wk high (<80%)",
    "C_close_top":      "Closed top 30% of range",
    "J_body_strong":    "Strong green body ≥50%",
    "F_d0_quiet":       "D0 gain < 1.5% (fresh move)",
    "E_atr_sweet":      "ATR normal (0.8-2x)",
    "G_vol_calm":       "Vol trend < 20% pre-D1",
    "H_first_day":      "1st or 2nd up day",
    "A_spy_strength":   "SPY DOWN, stock UP (rel. strength)",
    "A2_spy_confirm":   "SPY UP confirming move",
}

MIN_N    = 30
SEP      = "═" * 80


# ── Wilson 95% confidence interval ───────────────────────────────────────────
def wilson_ci(wins, n, z=1.96):
    if n == 0: return (0.0, 0.0)
    p   = wins / n
    denom = 1 + z**2 / n
    center = (p + z**2 / (2*n)) / denom
    margin = (z * math.sqrt(p*(1-p)/n + z**2/(4*n**2))) / denom
    return (max(0, (center - margin)*100), min(100, (center + margin)*100))


# ── Finviz + data helpers (same as backtest_combo60) ─────────────────────────
def finviz_universe(filters, max_pages=6, min_vol=100_000):
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

def download_ohlcv(tickers, period="1y"):
    result = {}
    for i in range(0, len(tickers), 80):
        batch = tickers[i:i+80]
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
                    if len(rows) >= 60:
                        result[tkr] = sorted(rows, key=lambda x: x[0])
                except Exception: pass
        except Exception: pass
        if i+80 < len(tickers): time.sleep(1.5)
    return result

def download_spy(period="1y"):
    spy = {}
    try:
        raw = yf.download("SPY", period=period, interval="1d",
                          auto_adjust=False, progress=False)
        closes = raw["Close"].squeeze()
        dates  = list(closes.index)
        for i in range(1, len(dates)):
            d  = dates[i].date()
            c0 = float(closes.iloc[i-1])
            c1 = float(closes.iloc[i])
            spy[d] = (c1 - c0) / c0 * 100 if c0 > 0 else 0.0
    except Exception as e:
        print(f"  SPY download: {e}")
    return spy


# ── Signal builder ────────────────────────────────────────────────────────────
def build_signals(ohlcv, spy_returns, thr, ecap, wpz, tier_key):
    D5, signals = 5, []
    for tkr, rows in ohlcv.items():
        for i in range(15, len(rows) - D5):
            d0 = rows[i-1]; d1 = rows[i]; d5 = rows[i+D5]
            d0c=d0[4]; d1o=d1[1]; d1h=d1[2]; d1lo=d1[3]; d1c=d1[4]; d1v=d1[5]; d5c=d5[4]
            d1_date = d1[0]
            if d0c<=0 or d1c<=0 or d5c<=0: continue
            d1_gain = (d1c-d0c)/d0c*100
            if d1_gain < thr: continue
            gap_pct = (d1o-d0c)/d0c*100
            dow = d1_date.weekday()
            d5_ret = (d5c-d1c)/d1c*100
            is_monday  = (dow==0)
            is_extreme = (d1_gain>ecap)
            in_wpz = (wpz and wpz[0]<=d1c<wpz[1])
            if in_wpz: continue
            if is_monday and not (gap_pct<0 and tier_key in ("large","small")): continue
            prev_vols = [rows[j][5] for j in range(i-5,i) if rows[j][5]>0]
            avg_vol = statistics.mean(prev_vols) if prev_vols else d1v
            rvol = d1v/avg_vol if avg_vol>0 else 1.0
            if is_extreme and not (tier_key=="small" and 0<=gap_pct<2 and rvol>=4.0): continue
            # ATR
            atr_vals=[]
            for j in range(i-6,i-1):
                _h=rows[j][2];_lo=rows[j][3];_pc=rows[j-1][4]
                atr_vals.append(max(_h-_lo,abs(_h-_pc),abs(_lo-_pc)))
            atr5 = statistics.mean(atr_vals) if atr_vals else 0
            d1_range = d1h-d1lo
            atr_mult = d1_range/atr5 if atr5>0 else 1.0
            prior_gain = (d0c-rows[i-2][4])/rows[i-2][4]*100 if rows[i-2][4]>0 else 0.0
            vol_trend=0.0
            if i>=4 and rows[i-4][5]>0:
                vol_trend = (rows[i-2][5]-rows[i-4][5])/rows[i-4][5]*100
            consec=0
            for j in range(i-1,max(i-8,0),-1):
                if rows[j][4]>rows[j-1][4]: consec+=1
                else: break
            body_ratio = abs(d1c-d1o)/d1_range if d1_range>0 else 0.5
            green_close= (d1c>=d1o)
            recent_highs=[rows[j][2] for j in range(max(0,i-10),i)]
            max_10d=max(recent_highs) if recent_highs else d1c
            near_high_10=d1c/max_10d
            spy_ret=spy_returns.get(d1_date,None)
            hist_window=rows[max(0,i-252):i]
            high_52w=max(r[2] for r in hist_window) if hist_window else d1c
            ratio_52w=d1c/high_52w
            close_range_pos=(d1c-d1lo)/d1_range if d1_range>0 else 0.5
            trend_20d = (d0c-rows[i-20][4])/rows[i-20][4]*100 if i>=20 and rows[i-20][4]>0 else 0.0

            signals.append({
                "won":     d5_ret>0,
                "d5_ret":  d5_ret,
                "K_near_10d_high":  0.83<=near_high_10<=0.975,
                "D2_downtrend":     trend_20d<0,
                "B_52wk_near":      0.88<=ratio_52w<=1.05,
                "B2_52wk_far":      ratio_52w<0.80,
                "C_close_top":      close_range_pos>=0.70,
                "J_body_strong":    body_ratio>=0.50 and green_close,
                "F_d0_quiet":       prior_gain<1.5,
                "E_atr_sweet":      0.80<=atr_mult<=2.0,
                "G_vol_calm":       vol_trend<20.0,
                "H_first_day":      consec<=1,
                "A_spy_strength":   spy_ret is not None and spy_ret<0,
                "A2_spy_confirm":   spy_ret is not None and spy_ret>0,
                # raw values for threshold optimizer
                "_near_high_10":    near_high_10,
                "_trend_20d":       trend_20d,
                "_ratio_52w":       ratio_52w,
                "_close_pos":       close_range_pos,
                "_atr_mult":        atr_mult,
                "_prior_gain":      prior_gain,
            })
    return signals


# ── N-factor search ───────────────────────────────────────────────────────────
def run_factor_search(signals, factor_count, min_n=MIN_N, top_k=15):
    results = []
    for combo in itertools.combinations(TOP_GATES, factor_count):
        subset = [s for s in signals if all(s.get(g,False) for g in combo)]
        n = len(subset)
        if n < min_n: continue
        wins = sum(1 for s in subset if s["won"])
        wr   = wins/n*100
        avg  = statistics.mean(s["d5_ret"] for s in subset)
        lo, hi = wilson_ci(wins, n)
        results.append((wr, lo, hi, avg, n, combo))
    results.sort(key=lambda x: (-x[0], -x[4]))
    return results[:top_k]


# ── Threshold optimizer for the 3 dominant pillars ───────────────────────────
def threshold_optimizer(signals, tier_name):
    base_wr = sum(1 for s in signals if s["won"])/len(signals)*100

    print(f"\n  ── Threshold optimizer: {tier_name} ────────────────────────────")

    # Pillar 1: 10-day magnet zone  (_near_high_10)
    print(f"\n  [10d high ratio]  (currently 0.83–0.975)  baseline={base_wr:.1f}%")
    print(f"  {'lo':>6}  {'hi':>6}  {'n':>6}  {'WR':>7}  {'CI_lo':>7}  {'CI_hi':>7}")
    best_wr_k = 0
    for lo_t in [0.70, 0.75, 0.80, 0.82, 0.83, 0.84, 0.85, 0.86, 0.87, 0.88]:
        for hi_t in [0.92, 0.94, 0.95, 0.96, 0.97, 0.975, 0.98, 0.99, 1.00]:
            if hi_t <= lo_t: continue
            sub = [s for s in signals if lo_t <= s["_near_high_10"] < hi_t]
            if len(sub) < MIN_N: continue
            wins = sum(1 for s in sub if s["won"])
            wr   = wins/len(sub)*100
            lo_ci, hi_ci = wilson_ci(wins, len(sub))
            if wr > best_wr_k:
                best_wr_k = wr
                print(f"  {lo_t:>6.2f}  {hi_t:>6.3f}  {len(sub):>6}  {wr:>6.1f}%  {lo_ci:>6.1f}%  {hi_ci:>6.1f}%  ◄ NEW BEST")

    # Pillar 2: 20-day trend threshold (_trend_20d)
    print(f"\n  [20d trend going into D1]  (currently < 0 = any downtrend)  baseline={base_wr:.1f}%")
    print(f"  {'threshold':>10}  {'n':>6}  {'WR':>7}  {'CI_lo':>7}  {'CI_hi':>7}")
    for thr in [0, -1, -2, -3, -4, -5, -6, -7, -8, -10, -12, -15, -20]:
        sub = [s for s in signals if s["_trend_20d"] < thr]
        if len(sub) < MIN_N: continue
        wins = sum(1 for s in sub if s["won"])
        wr   = wins/len(sub)*100
        lo_ci, hi_ci = wilson_ci(wins, len(sub))
        diff = wr - base_wr
        flag = " ◄ BETTER" if diff >= 3 else ""
        print(f"  trend < {thr:>4}%  {len(sub):>6}  {wr:>6.1f}%  {lo_ci:>6.1f}%  {hi_ci:>6.1f}%  ({diff:+.1f}pp){flag}")

    # Pillar 3: 52-week high proximity (_ratio_52w)
    print(f"\n  [52-week high ratio]  (near=0.88–1.05, far=<0.80)  baseline={base_wr:.1f}%")
    print(f"  {'range':>20}  {'n':>6}  {'WR':>7}  {'CI_lo':>7}  {'CI_hi':>7}")
    ranges = [
        ("< 60%",      lambda r: r<0.60),
        ("60–70%",     lambda r: 0.60<=r<0.70),
        ("70–80%",     lambda r: 0.70<=r<0.80),
        ("80–88%",     lambda r: 0.80<=r<0.88),
        ("88–95%",     lambda r: 0.88<=r<0.95),
        ("95–100%",    lambda r: 0.95<=r<1.00),
        ("100–105%",   lambda r: 1.00<=r<1.05),
        ("> 105%",     lambda r: r>=1.05),
    ]
    for label, cond in ranges:
        sub = [s for s in signals if cond(s["_ratio_52w"])]
        if len(sub) < MIN_N: continue
        wins = sum(1 for s in sub if s["won"])
        wr   = wins/len(sub)*100
        lo_ci, hi_ci = wilson_ci(wins, len(sub))
        diff = wr - base_wr
        flag = " ◄" if diff >= 2 else ""
        print(f"  {label:>20}  {len(sub):>6}  {wr:>6.1f}%  {lo_ci:>6.1f}%  {hi_ci:>6.1f}%  ({diff:+.1f}pp){flag}")


TIERS = [
    {"name":"LARGE","filter":"cap_large,sh_opt_option","min_vol":500_000,"pages":6,"thr":3.0,"ecap":15.0,"wpz":None,"key":"large"},
    {"name":"MID",  "filter":"cap_mid,sh_opt_option",  "min_vol":200_000,"pages":6,"thr":5.0,"ecap":15.0,"wpz":(15,50),"key":"mid"},
    {"name":"SMALL","filter":"cap_small,sh_opt_option","min_vol":100_000,"pages":6,"thr":7.0,"ecap":17.0,"wpz":(15,50),"key":"small"},
]


def main():
    print("Downloading SPY...")
    spy = download_spy()
    print(f"SPY: {len(spy)} days\n")

    all_summary = []

    for cfg in TIERS:
        print(SEP)
        print(f"  {cfg['name']} CAP  |  v2 pool  |  ≥{cfg['thr']}% D1  |  testing 4–7 factors")
        print(SEP)
        tickers = finviz_universe(cfg["filter"], max_pages=cfg["pages"], min_vol=cfg["min_vol"])
        print(f"  {len(tickers)} tickers...")
        ohlcv   = download_ohlcv(tickers)
        signals = build_signals(ohlcv, spy, cfg["thr"], cfg["ecap"], cfg["wpz"], cfg["key"])

        base_n  = len(signals)
        base_wr = sum(1 for s in signals if s["won"])/base_n*100 if base_n else 0
        print(f"\n  Baseline: {base_n} signals, WR = {base_wr:.1f}%")

        # ── Factor-count sweep ───────────────────────────────────────
        tier_best = {}
        for fc in (4, 5, 6, 7):
            results = run_factor_search(signals, fc, min_n=MIN_N)
            surviving = len([r for r in results if r[4] >= MIN_N])
            tier_best[fc] = results[0] if results else None

            print(f"\n  ── {fc}-FACTOR combinations  (n≥{MIN_N})  ─────────────────────────────")
            if not results:
                print(f"     None survive n≥{MIN_N} — all combos too sparse (overfitting territory)")
                continue

            print(f"  {'Rank':<5}  {'n':>5}  {'WR':>7}  {'95% CI':>14}  {'AvgD5':>7}  Gates")
            print(f"  {'─'*5}  {'─'*5}  {'─'*7}  {'─'*14}  {'─'*7}  {'─'*50}")
            for rank, (wr, lo_ci, hi_ci, avg, n, combo) in enumerate(results, 1):
                star = " ★ 60%+" if wr >= 60 else ("  ★ 65%+" if wr >= 65 else "")
                gates_str = " + ".join(GATE_LABELS.get(g,g) for g in combo)
                print(f"  #{rank:<4}  {n:>5}  {wr:>6.1f}%  [{lo_ci:>5.1f}–{hi_ci:<5.1f}]  {avg:>+6.2f}%  {gates_str}{star}")

        # ── Show the WR-vs-factor-count progression ─────────────────
        print(f"\n  ── WR progression by factor count (best surviving combo) ──────────────")
        print(f"  {'Factors':<10}  {'Best WR':>8}  {'n':>6}  {'95% CI low':>12}  Reliable?")
        print(f"  {'─'*10}  {'─'*8}  {'─'*6}  {'─'*12}  {'─'*10}")
        # Add 3-factor reference point from backtest_combo60 results
        print(f"  3-factor    {'~62–65%':>8}  {'100+':>6}  {'~57%':>12}  ✅ Yes (from combo60)")
        for fc in (4, 5, 6, 7):
            best = tier_best[fc]
            if best is None:
                print(f"  {fc}-factor    {'—':>8}  {'<30':>6}  {'—':>12}  ❌ No (n too small)")
            else:
                wr, lo_ci, hi_ci, avg, n, combo = best
                reliable = "✅ Yes" if n >= 50 else ("⚠️  Marginal" if n >= 30 else "❌ No")
                print(f"  {fc}-factor    {wr:>7.1f}%  {n:>6}  {lo_ci:>11.1f}%  {reliable}")

        # ── Threshold optimizer ──────────────────────────────────────
        threshold_optimizer(signals, cfg["name"])

        # Store for cross-tier summary
        all_summary.append((cfg["name"], base_wr, tier_best))
        print()

    # ── Cross-tier verdict ────────────────────────────────────────────────────
    print(SEP)
    print("  CROSS-TIER VERDICT: HOW MANY FACTORS IS OPTIMAL?")
    print(SEP)
    print(f"\n  {'Tier':<8}  {'Base':>6}  {'3-fac':>8}  {'4-fac':>8}  {'5-fac':>8}  {'6-fac':>8}  {'7-fac':>8}")
    print(f"  {'─'*8}  {'─'*6}  {'─'*8}  {'─'*8}  {'─'*8}  {'─'*8}  {'─'*8}")
    for name, bwr, bests in all_summary:
        row = f"  {name:<8}  {bwr:>5.1f}%  {'~62-65%':>8}"
        for fc in (4, 5, 6, 7):
            b = bests[fc]
            row += f"  {b[0]:>6.1f}% " if b else f"  {'—':>7} "
        print(row)

    print(f"""
  KEY TAKEAWAYS:
  1. 4-factor combos: likely +2–3pp over 3-factor, still statistically solid (n≥50)
  2. 5-factor:        WR may rise but n thins — check the CI lower bound, not the WR
  3. 6-7 factor:      WR looks great but n collapses below 30 = NOISE, not signal
  4. The CI low bound is what matters — a 70% WR with CI low of 52% is worthless
  5. SWEET SPOT: 3–4 factors in a daily equity scanner (confirmed by quant research)
  6. Use threshold optimizer to tighten the existing 3-factor combos FIRST
     — that's more reliable than stacking extra gates on noisy data
{SEP}""")


if __name__ == "__main__":
    main()
