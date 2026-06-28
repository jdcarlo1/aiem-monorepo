"""
backtest_combo60.py — Systematic multi-factor combination search for 60%+ WR
=============================================================================
Tests EVERY 2-factor and 3-factor combination of 11 indicators (including 4 never
tested before) to find which stacked gates push win rate ≥ 60%.

NEW indicators (never tested):
  A. SPY relative strength  — stock up on a day SPY is DOWN (institutional buying)
  B. 52-week high proximity — within 5-15% of 52wk high (breakout accumulation zone)
  C. Close range position   — closed in top 30% of day's range (buyers in control)
  D. 10/20-day trend align  — D1 move aligned with medium-term trend direction

Plus all previously discovered factors:
  E. ATR multiple           — 0.8–2.0x (normal range, not panic spike)
  F. Prior-day quiet (D0)   — D0 gain < 1.5% (fresh ignition, not extended run)
  G. Pre-D1 vol calm        — vol trend < 20% before D1
  H. Consecutive days       — ≤ 1 prior up day (first day of move)
  I. Gap controlled         — gap ≤ 3% (no huge overnight gap)
  J. Body strength          — body ratio > 0.45 and green close
  K. Near recent high       — 0.83–0.975 of 10-day high

Prints top-20 combinations by WR for each tier, highlights anything ≥ 60%.
"""

import re, sys, time, statistics, datetime, itertools
from collections import defaultdict

try:
    import requests
    import yfinance as yf
except ImportError:
    sys.exit(1)

_FV_HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36"}


# ── Data helpers ──────────────────────────────────────────────────────────────

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
                except Exception:
                    pass
        except Exception:
            pass
        if i+80 < len(tickers): time.sleep(1.5)
    return result


def download_spy(period="1y"):
    """Returns dict date → daily_return_pct for SPY."""
    spy = {}
    try:
        raw = yf.download("SPY", period=period, interval="1d",
                          auto_adjust=False, progress=False)
        if raw.empty: return spy
        closes = raw["Close"].squeeze()
        dates  = list(closes.index)
        for i in range(1, len(dates)):
            d  = dates[i].date()
            c0 = float(closes.iloc[i-1])
            c1 = float(closes.iloc[i])
            spy[d] = (c1 - c0) / c0 * 100 if c0 > 0 else 0.0
    except Exception as e:
        print(f"  SPY download failed: {e}")
    return spy


# ── Signal builder — computes ALL 11 indicator flags ─────────────────────────

def build_signals(ohlcv, spy_returns, thr, ecap, wpz, tier_key):
    D5 = 5
    signals = []

    for tkr, rows in ohlcv.items():
        # 52-week (≈252 trading days) high for each position
        for i in range(15, len(rows) - D5):
            d0 = rows[i-1]; d1 = rows[i]; d5 = rows[i+D5]
            d0c = d0[4]; d1o = d1[1]; d1h = d1[2]; d1lo = d1[3]
            d1c = d1[4]; d1v = d1[5]; d5c = d5[4]; d1_date = d1[0]

            if d0c <= 0 or d1c <= 0 or d5c <= 0: continue

            d1_gain = (d1c - d0c) / d0c * 100
            if d1_gain < thr: continue

            gap_pct   = (d1o - d0c) / d0c * 100
            dow       = d1_date.weekday()
            d5_ret    = (d5c - d1c) / d1c * 100

            # ── Apply existing v2 filters ─────────────────────────────
            is_monday  = (dow == 0)
            is_extreme = (d1_gain > ecap)
            in_wpz     = (wpz and wpz[0] <= d1c < wpz[1])
            if in_wpz: continue
            if is_monday and not (gap_pct < 0 and tier_key in ("large","small")): continue

            # RVOL
            prev_vols = [rows[j][5] for j in range(i-5, i) if rows[j][5] > 0]
            avg_vol   = statistics.mean(prev_vols) if prev_vols else d1v
            rvol      = d1v / avg_vol if avg_vol > 0 else 1.0

            if is_extreme:
                if not (tier_key=="small" and 0<=gap_pct<2 and rvol>=4.0): continue

            # ── Compute all 11 indicator flags ────────────────────────

            # [E] ATR multiple (5-day ATR before D1)
            atr_vals = []
            for j in range(i-6, i-1):
                _h=rows[j][2]; _lo=rows[j][3]; _pc=rows[j-1][4]
                atr_vals.append(max(_h-_lo, abs(_h-_pc), abs(_lo-_pc)))
            atr5     = statistics.mean(atr_vals) if atr_vals else 0
            d1_range = d1h - d1lo
            atr_mult = d1_range / atr5 if atr5 > 0 else 1.0

            # [F] Prior-day gain (D0)
            prior_gain = (d0c - rows[i-2][4]) / rows[i-2][4] * 100 if rows[i-2][4] > 0 else 0.0

            # [G] Pre-D1 volume trend (3-day)
            vol_trend = 0.0
            if i >= 4 and rows[i-4][5] > 0:
                vol_trend = (rows[i-2][5] - rows[i-4][5]) / rows[i-4][5] * 100

            # [H] Consecutive up days
            consec = 0
            for j in range(i-1, max(i-8, 0), -1):
                if rows[j][4] > rows[j-1][4]: consec += 1
                else: break

            # [I] Gap controlled
            # gap_pct already computed above

            # [J] Body strength: body_ratio + green close
            body_ratio  = abs(d1c - d1o) / d1_range if d1_range > 0 else 0.5
            green_close = (d1c >= d1o)

            # [K] Near 10-day high
            recent_highs = [rows[j][2] for j in range(max(0, i-10), i)]
            max_10d      = max(recent_highs) if recent_highs else d1c
            near_high_10 = d1c / max_10d

            # ── NEW INDICATORS ────────────────────────────────────────

            # [A] SPY relative strength: stock up while SPY is DOWN (best signal)
            spy_ret = spy_returns.get(d1_date, None)
            spy_down_stock_up = (spy_ret is not None and spy_ret < 0)
            spy_up_confirm    = (spy_ret is not None and spy_ret > 0)

            # [B] 52-week high proximity (use up to 252 trading days of history)
            hist_window = rows[max(0, i-252):i]
            high_52w    = max(r[2] for r in hist_window) if hist_window else d1c
            ratio_52w   = d1c / high_52w  # 1.0 = AT 52wk high; 0.9 = 10% below

            # [C] Close range position: (close - low) / (high - low)
            # 1.0 = closed at HOD; 0.0 = closed at LOD
            close_range_pos = (d1c - d1lo) / d1_range if d1_range > 0 else 0.5

            # [D] Medium-term trend alignment: is D1 move in direction of 20-day trend?
            if i >= 20:
                price_20d_ago = rows[i-20][4]
                trend_20d     = (d0c - price_20d_ago) / price_20d_ago * 100
            else:
                trend_20d = 0.0
            uptrend_aligned = trend_20d > 0   # stock was already trending up into D1

            # ── Build signal dict ─────────────────────────────────────
            signals.append({
                # outcomes
                "won":     d5_ret > 0,
                "d5_ret":  d5_ret,
                # raw values
                "atr_mult":       atr_mult,
                "prior_gain":     prior_gain,
                "vol_trend":      vol_trend,
                "consec":         consec,
                "gap_pct":        gap_pct,
                "body_ratio":     body_ratio,
                "green_close":    green_close,
                "near_high_10":   near_high_10,
                "spy_ret":        spy_ret,
                "ratio_52w":      ratio_52w,
                "close_range_pos":close_range_pos,
                "trend_20d":      trend_20d,
                "d1_gain":        d1_gain,
                "rvol":           rvol,
                # ── binary gates (True = POSITIVE / "good zone") ─────
                "A_spy_strength":  spy_down_stock_up,         # up vs down mkt
                "A2_spy_confirm":  spy_up_confirm,            # up with up mkt
                "B_52wk_near":     0.88 <= ratio_52w <= 1.05, # near/above 52wk high
                "B2_52wk_far":     ratio_52w < 0.80,          # deep recovery
                "C_close_top":     close_range_pos >= 0.70,   # closed top 30% of range
                "C2_close_mid":    0.40 <= close_range_pos < 0.70,
                "D_uptrend":       uptrend_aligned,           # 20d trend up
                "D2_downtrend":    not uptrend_aligned,       # bouncing vs downtrend
                "E_atr_sweet":     0.80 <= atr_mult <= 2.0,   # normal ATR range
                "F_d0_quiet":      prior_gain < 1.5,          # not already running
                "G_vol_calm":      vol_trend < 20.0,          # no pre-D1 vol surge
                "H_first_day":     consec <= 1,               # first/second up day
                "I_gap_small":     gap_pct <= 3.0,            # controlled gap
                "J_body_strong":   body_ratio >= 0.50 and green_close,
                "K_near_10d_high": 0.83 <= near_high_10 <= 0.975,
                "K2_at_10d_high":  near_high_10 > 0.975,
            })
    return signals


# ── Combination engine ────────────────────────────────────────────────────────

GATES = [
    "A_spy_strength", "A2_spy_confirm",
    "B_52wk_near", "B2_52wk_far",
    "C_close_top", "C2_close_mid",
    "D_uptrend", "D2_downtrend",
    "E_atr_sweet",
    "F_d0_quiet",
    "G_vol_calm",
    "H_first_day",
    "I_gap_small",
    "J_body_strong",
    "K_near_10d_high", "K2_at_10d_high",
]

GATE_LABELS = {
    "A_spy_strength":  "SPY was DOWN on D1 (relative strength)",
    "A2_spy_confirm":  "SPY was UP on D1 (market confirmation)",
    "B_52wk_near":     "Within 5-12% of 52-week high (breakout zone)",
    "B2_52wk_far":     "More than 20% below 52-week high (deep recovery)",
    "C_close_top":     "Closed in top 30% of day's range (buyers in control)",
    "C2_close_mid":    "Closed in middle 40-70% of range",
    "D_uptrend":       "20-day trend is UP going into D1 (aligned)",
    "D2_downtrend":    "20-day trend is DOWN going into D1 (reversal)",
    "E_atr_sweet":     "ATR multiple 0.8–2x (normal range, not panic spike)",
    "F_d0_quiet":      "D0 gain < 1.5% (stock wasn't already running)",
    "G_vol_calm":      "Pre-D1 volume trend < 20% (quiet accumulation)",
    "H_first_day":     "First or 2nd consecutive up day (fresh move)",
    "I_gap_small":     "Opening gap ≤ 3% (controlled, not news spike)",
    "J_body_strong":   "Strong green body ≥ 50% of range (conviction close)",
    "K_near_10d_high": "10-day high in 83–97.5% zone (approaching ceiling)",
    "K2_at_10d_high":  "AT or above 10-day high (fresh breakout)",
}

def find_combos(signals, min_n=30, top_n=25):
    base_wr = sum(1 for s in signals if s["won"]) / len(signals) * 100 if signals else 0
    results = []

    for r in (1, 2, 3):
        for combo in itertools.combinations(GATES, r):
            subset = [s for s in signals if all(s.get(g, False) for g in combo)]
            n = len(subset)
            if n < min_n: continue
            wr  = sum(1 for s in subset if s["won"]) / n * 100
            avg = statistics.mean(s["d5_ret"] for s in subset)
            results.append((wr, avg, n, combo))

    results.sort(key=lambda x: (-x[0], -x[2]))
    return base_wr, results[:top_n]


# ── Per-indicator standalone stats ───────────────────────────────────────────

def single_gate_table(signals):
    base_wr  = sum(1 for s in signals if s["won"]) / len(signals) * 100
    base_avg = statistics.mean(s["d5_ret"] for s in signals)
    print(f"\n  {'Gate':<40}  {'n':>6}  {'WR':>7}  {'AvgD5':>7}  {'vs base':>8}")
    print(f"  {'─'*40}  {'─'*6}  {'─'*7}  {'─'*7}  {'─'*8}")
    rows = []
    for g in GATES:
        sub = [s for s in signals if s.get(g, False)]
        if len(sub) < 15: continue
        wr  = sum(1 for s in sub if s["won"]) / len(sub) * 100
        avg = statistics.mean(s["d5_ret"] for s in sub)
        rows.append((wr, avg, len(sub), g))
    rows.sort(key=lambda x: -x[0])
    for wr, avg, n, g in rows:
        delta = wr - base_wr
        flag  = " ◄ NEW BEST" if wr >= 60 else (" ◄" if wr >= base_wr + 3 else "")
        print(f"  {GATE_LABELS.get(g, g):<40}  {n:>6}  {wr:>6.1f}%  {avg:>+6.2f}%  {delta:>+7.1f}pp{flag}")


TIERS = [
    {"name": "LARGE CAP", "filter": "cap_large,sh_opt_option",
     "min_vol": 500_000, "pages": 6, "thr": 3.0, "ecap": 15.0, "wpz": None, "key": "large"},
    {"name": "MID CAP",   "filter": "cap_mid,sh_opt_option",
     "min_vol": 200_000, "pages": 6, "thr": 5.0, "ecap": 15.0, "wpz": (15.0, 50.0), "key": "mid"},
    {"name": "SMALL CAP", "filter": "cap_small,sh_opt_option",
     "min_vol": 100_000, "pages": 6, "thr": 7.0, "ecap": 17.0, "wpz": (15.0, 50.0), "key": "small"},
]

SEP = "═" * 76


def main():
    print("Downloading SPY (1 year)...")
    spy_returns = download_spy(period="1y")
    print(f"SPY: {len(spy_returns)} trading days loaded\n")

    all_tier_results = []

    for cfg in TIERS:
        print(SEP)
        print(f"  {cfg['name']}  (v2 filter pool, ≥{cfg['thr']}% D1 gain)")
        print(SEP)
        tickers = finviz_universe(cfg["filter"], max_pages=cfg["pages"], min_vol=cfg["min_vol"])
        print(f"  {len(tickers)} tickers — downloading 1y history...")
        ohlcv   = download_ohlcv(tickers, period="1y")
        signals = build_signals(ohlcv, spy_returns, cfg["thr"], cfg["ecap"], cfg["wpz"], cfg["key"])

        base_n = len(signals)
        base_wr = sum(1 for s in signals if s["won"]) / base_n * 100 if base_n else 0
        print(f"\n  {base_n} signals in v2 pool  |  Baseline WR = {base_wr:.1f}%")

        # ── Single gate analysis ─────────────────────────────────────
        print(f"\n  ── Single-gate standings (sorted by WR) ────────────────────")
        single_gate_table(signals)

        # ── Best combinations ────────────────────────────────────────
        print(f"\n  ── Top combinations (2- and 3-factor) ──────────────────────")
        base_wr2, combos = find_combos(signals, min_n=30, top_n=25)
        print(f"\n  {'Rank':<5}  {'n':>5}  {'WR':>7}  {'AvgD5':>7}  Gates")
        print(f"  {'─'*5}  {'─'*5}  {'─'*7}  {'─'*7}  {'─'*50}")
        hit60 = []
        for rank, (wr, avg, n, combo) in enumerate(combos, 1):
            gates_str = " + ".join(GATE_LABELS.get(g, g) for g in combo)
            flag = "  ★ 60%+ ACHIEVED" if wr >= 60 else ""
            print(f"  #{rank:<4}  {n:>5}  {wr:>6.1f}%  {avg:>+6.2f}%  {gates_str}{flag}")
            if wr >= 60:
                hit60.append((wr, n, combo))

        if hit60:
            print(f"\n  ✅ COMBINATIONS THAT HIT 60%+ in {cfg['name']}:")
            for wr, n, combo in hit60:
                print(f"    WR={wr:.1f}% (n={n}):")
                for g in combo:
                    print(f"      • {GATE_LABELS.get(g, g)}")
        else:
            best_wr, best_avg, best_n, best_combo = combos[0] if combos else (0, 0, 0, ())
            print(f"\n  Best found: {best_wr:.1f}% — {best_n} signals")
            print(f"  Gap to 60%: {60 - best_wr:.1f}pp — need tighter gates or more factors")

        all_tier_results.append((cfg["name"], base_wr, combos[:5]))
        print()

    # ── Cross-tier summary ────────────────────────────────────────────────────
    print(SEP)
    print("  CROSS-TIER BEST COMBOS")
    print(SEP)
    for tier_name, bwr, top5 in all_tier_results:
        print(f"\n  {tier_name} (baseline {bwr:.1f}%):")
        for wr, avg, n, combo in top5:
            gates_str = " + ".join(GATE_LABELS.get(g, g) for g in combo)
            star = "★" if wr >= 60 else " "
            print(f"  {star} WR={wr:.1f}% (n={n}): {gates_str}")

    # ── Consistency check: gates that appear in top-5 across ≥2 tiers ────────
    print(f"\n  Gates appearing in top-5 combos across multiple tiers:")
    gate_count = defaultdict(int)
    for _, _, top5 in all_tier_results:
        for _, _, _, combo in top5:
            for g in combo:
                gate_count[g] += 1
    for g, cnt in sorted(gate_count.items(), key=lambda x: -x[1]):
        if cnt >= 2:
            print(f"    ({cnt} tiers) {GATE_LABELS.get(g, g)}")

    print(f"\n{SEP}")
    print("  HOW TO USE THIS:")
    print("  1. Any combo hitting 60%+ = add as 'STRONG SIGNAL' tier in scanner")
    print("  2. Gates in top-5 across ≥2 tiers = universal score boosters")
    print("  3. Single gates with ≥+5pp vs baseline = worth adding to v2 filters")
    print(SEP)


if __name__ == "__main__":
    main()
