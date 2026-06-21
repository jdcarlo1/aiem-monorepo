"""
backtest_losers.py — Find what the losing signals have in common
================================================================
For each cap tier, downloads 3 months of daily OHLCV + SPY data,
fires every signal at the current threshold, then splits winners
vs losers and compares 6 factors.

Run:  python3 artifacts/stock-scanner-api/backtest_losers.py
"""

import re, sys, time, statistics, datetime
from collections import defaultdict

try:
    import requests
    import yfinance as yf
except ImportError:
    print("requests / yfinance not installed"); sys.exit(1)


# ── Finviz ─────────────────────────────────────────────────────────────────────

_FV_HDR = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
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


# ── Price download (OHLCV) ─────────────────────────────────────────────────────

def download_ohlcv(tickers, period="3mo"):
    """Return {ticker: [(date, open, high, low, close, volume), ...]}"""
    print(f"  Downloading {len(tickers)} tickers...", flush=True)
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
                    o = raw["Open"][tkr].dropna()    if s > 1 else raw["Open"].dropna()
                    h = raw["High"][tkr].dropna()    if s > 1 else raw["High"].dropna()
                    lo= raw["Low"][tkr].dropna()     if s > 1 else raw["Low"].dropna()
                    c = raw["Close"][tkr].dropna()   if s > 1 else raw["Close"].dropna()
                    v = raw["Volume"][tkr].dropna()  if s > 1 else raw["Volume"].dropna()
                    rows = []
                    for idx in c.index:
                        rows.append((idx.date(), float(o.get(idx,0)), float(h.get(idx,0)),
                                     float(lo.get(idx,0)), float(c[idx]), float(v.get(idx,0))))
                    if len(rows) >= 10:
                        result[tkr] = sorted(rows, key=lambda x: x[0])
                except Exception:
                    pass
        except Exception as e:
            print(f"  Batch error: {e}")
        if i + 100 < len(tickers):
            time.sleep(1)
    print(f"  Got {len(result)}/{len(tickers)} tickers", flush=True)
    return result


# ── Analysis engine ────────────────────────────────────────────────────────────

D5 = 5

def analyze(ohlcv: dict, spy_closes: dict[datetime.date, float], threshold: float) -> list[dict]:
    """
    Return a list of signal dicts, one per event, with all factor values + outcome.
    """
    signals = []
    spy_dates = sorted(spy_closes.keys())

    def spy_return_d1_to_d5(d1_date):
        """SPY return from d1_date close to d5 trading days later."""
        try:
            idx = spy_dates.index(d1_date)
            d5_date = spy_dates[idx + D5] if idx + D5 < len(spy_dates) else None
            if d5_date is None: return None
            return (spy_closes[d5_date] - spy_closes[d1_date]) / spy_closes[d1_date] * 100
        except (ValueError, IndexError):
            return None

    def spy_on_day(d1_date):
        """Was SPY up or down on D1?"""
        try:
            idx = spy_dates.index(d1_date)
            if idx < 1: return None
            d0 = spy_dates[idx - 1]
            return (spy_closes[d1_date] - spy_closes[d0]) / spy_closes[d0] * 100
        except (ValueError, IndexError):
            return None

    for tkr, rows in ohlcv.items():
        for i in range(1, len(rows) - D5):
            d0 = rows[i - 1]
            d1 = rows[i]
            d5 = rows[i + D5 - 1]

            d0_close = d0[4]
            d1_open, d1_high, d1_low, d1_close = d1[1], d1[2], d1[3], d1[4]
            d1_vol   = d1[5]
            d5_close = d5[4]
            d1_date  = d1[0]

            if d0_close <= 0 or d1_close <= 0 or d5_close <= 0: continue

            d1_gain = (d1_close - d0_close) / d0_close * 100
            if d1_gain < threshold: continue

            d5_ret = (d5_close - d1_close) / d1_close * 100
            won    = d5_ret > 0

            # ── Factor 1: gap vs intraday contribution ──────────────────────
            gap_pct      = (d1_open - d0_close) / d0_close * 100   # pre-market gap
            intraday_pct = (d1_close - d1_open) / d0_close * 100   # session move

            # ── Factor 2: close position in day's range ─────────────────────
            d1_range = d1_high - d1_low
            close_range_pos = ((d1_close - d1_low) / d1_range) if d1_range > 0 else 0.5
            # 1.0 = closed at high, 0.0 = closed at low

            # ── Factor 3: day of week ───────────────────────────────────────
            dow = d1_date.weekday()   # 0=Mon, 4=Fri

            # ── Factor 4: SPY direction on D1 ──────────────────────────────
            spy_d1 = spy_on_day(d1_date)

            # ── Factor 5: price level ───────────────────────────────────────
            price_level = d1_close

            # ── Factor 6: move was gap-led vs intraday-led ──────────────────
            gap_led = gap_pct > intraday_pct

            signals.append({
                "ticker":    tkr,
                "date":      d1_date,
                "d1_gain":   d1_gain,
                "d5_ret":    d5_ret,
                "won":       won,
                "gap_pct":   gap_pct,
                "intraday_pct": intraday_pct,
                "close_range_pos": close_range_pos,
                "dow":       dow,
                "spy_d1":    spy_d1,
                "price":     price_level,
                "gap_led":   gap_led,
            })
    return signals


def compare_factor(signals, factor_key, bins, bin_labels=None):
    """Print a factor breakdown: win rate by bucket."""
    buckets = defaultdict(lambda: [0, 0])  # bucket -> [wins, total]
    for s in signals:
        v = s.get(factor_key)
        if v is None: continue
        for i, (lo, hi) in enumerate(bins):
            if lo <= v < hi:
                label = bin_labels[i] if bin_labels else f"{lo}–{hi}"
                buckets[label][1] += 1
                if s["won"]: buckets[label][0] += 1
                break

    rows = []
    for label in (bin_labels or [f"{lo}–{hi}" for lo, hi in bins]):
        w, n = buckets[label]
        if n < 5: continue
        wr  = w / n * 100
        avg = statistics.mean([s["d5_ret"] for s in signals
                                if bin_labels and _in_bucket(s[factor_key], bins, bin_labels, label)])
        rows.append((label, n, wr, avg))

    if not rows: return
    for label, n, wr, avg in rows:
        bar_len = int(wr / 5)
        bar = "█" * bar_len + "░" * (20 - bar_len)
        flag = "  ⚠️  LOW WR" if wr < 47 else ("  🟢 HIGH WR" if wr > 58 else "")
        print(f"    {label:<28}  n={n:>5}  WR={wr:>5.1f}%  avg={avg:>+6.2f}%  {bar}{flag}")


def _in_bucket(v, bins, bin_labels, target_label):
    if v is None: return False
    for i, (lo, hi) in enumerate(bins):
        if lo <= v < hi:
            return (bin_labels[i] if bin_labels else f"{lo}–{hi}") == target_label
    return False


def compare_bool_factor(signals, factor_key, true_label, false_label):
    groups = {true_label: [s for s in signals if s.get(factor_key) is True],
              false_label: [s for s in signals if s.get(factor_key) is False]}
    for label, grp in groups.items():
        if len(grp) < 5: continue
        wr  = sum(1 for s in grp if s["won"]) / len(grp) * 100
        avg = statistics.mean(s["d5_ret"] for s in grp)
        bar_len = int(wr / 5)
        bar = "█" * bar_len + "░" * (20 - bar_len)
        flag = "  ⚠️  LOW WR" if wr < 47 else ("  🟢 HIGH WR" if wr > 58 else "")
        print(f"    {label:<28}  n={len(grp):>5}  WR={wr:>5.1f}%  avg={avg:>+6.2f}%  {bar}{flag}")


# ── Tier configs ───────────────────────────────────────────────────────────────

TIERS = [
    {
        "name":    "LARGE CAP ($10B+)",
        "filter":  "cap_large,sh_opt_option",
        "min_vol": 500_000,
        "pages":   6,
        "thr":     3.0,
    },
    {
        "name":    "MID CAP ($2B–$10B)",
        "filter":  "cap_mid,sh_opt_option",
        "min_vol": 200_000,
        "pages":   6,
        "thr":     5.0,   # updated threshold
    },
    {
        "name":    "SMALL CAP ($300M–$2B)",
        "filter":  "cap_small,sh_opt_option",
        "min_vol": 100_000,
        "pages":   6,
        "thr":     7.0,   # updated threshold
    },
]


# ── Main ───────────────────────────────────────────────────────────────────────

DOW_LABELS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]

def main():
    # Download SPY for market direction context
    print("Downloading SPY for market context...")
    spy_raw = yf.download("SPY", period="3mo", interval="1d",
                          auto_adjust=True, progress=False)
    spy_closes: dict[datetime.date, float] = {}
    if not spy_raw.empty:
        close_col = spy_raw["Close"]
        # yfinance single-ticker sometimes returns a DataFrame column instead of Series
        if hasattr(close_col, "squeeze"):
            close_col = close_col.squeeze()
        for idx, v in close_col.items():
            try:
                spy_closes[idx.date()] = float(v)
            except (TypeError, ValueError):
                pass
    print(f"SPY: {len(spy_closes)} days\n")

    all_filters: list[tuple[str, str, float, float]] = []   # (tier, factor, cutoff, min_wr_to_filter)

    for cfg in TIERS:
        print("═" * 68)
        print(f"  {cfg['name']}  —  threshold ≥{cfg['thr']}%")
        print("═" * 68)

        print(f"\n  Pulling Finviz universe ({cfg['filter']})...")
        tickers = finviz_universe(cfg["filter"], max_pages=cfg["pages"],
                                   min_vol=cfg["min_vol"])
        print(f"  {len(tickers)} tickers")

        ohlcv = download_ohlcv(tickers)
        signals = analyze(ohlcv, spy_closes, cfg["thr"])

        n_total = len(signals)
        n_win   = sum(1 for s in signals if s["won"])
        base_wr = n_win / n_total * 100 if n_total else 0
        base_avg = statistics.mean(s["d5_ret"] for s in signals) if signals else 0

        print(f"\n  Total signals: {n_total}  |  Baseline WR: {base_wr:.1f}%  |  Avg D5: {base_avg:+.2f}%\n")

        # ── Factor 1: SPY direction on D1 ────────────────────────────────────
        print("  FACTOR 1: Was the market (SPY) up or down on D1?")
        compare_factor(signals, "spy_d1",
            bins=[(-99, -2), (-2, -0.5), (-0.5, 0.5), (0.5, 2), (2, 99)],
            bin_labels=["SPY fell >2%", "SPY fell 0.5–2%", "SPY flat ±0.5%",
                        "SPY up 0.5–2%", "SPY up >2%"])

        # ── Factor 2: Close position in day's range ───────────────────────────
        print("\n  FACTOR 2: Where did D1 close in the day's range?")
        print("            (1.0 = closed at high, 0.0 = closed at low)")
        compare_factor(signals, "close_range_pos",
            bins=[(0.0, 0.3), (0.3, 0.5), (0.5, 0.7), (0.7, 0.85), (0.85, 1.01)],
            bin_labels=["Closed near LOW (0–30%)", "Lower half (30–50%)",
                        "Middle (50–70%)", "Upper (70–85%)", "Closed near HIGH (85–100%)"])

        # ── Factor 3: Gap-led vs intraday-led ────────────────────────────────
        print("\n  FACTOR 3: Was the D1 gain a pre-market gap or intraday move?")
        compare_bool_factor(signals, "gap_led",
            true_label="Gap-led (gap > intraday move)",
            false_label="Intraday-led (intraday > gap)")

        # ── Factor 4: Day of week ──────────────────────────────────────────
        print("\n  FACTOR 4: Day of week")
        compare_factor(signals, "dow",
            bins=[(0, 1), (1, 2), (2, 3), (3, 4), (4, 5)],
            bin_labels=DOW_LABELS)

        # ── Factor 5: D1 gain size ────────────────────────────────────────
        print("\n  FACTOR 5: How big was the D1 gain? (continuation vs exhaustion)")
        compare_factor(signals, "d1_gain",
            bins=[(cfg["thr"], cfg["thr"]+2), (cfg["thr"]+2, cfg["thr"]+5),
                  (cfg["thr"]+5, cfg["thr"]+10), (cfg["thr"]+10, 999)],
            bin_labels=[f"Just above (≥{cfg['thr']}% – {cfg['thr']+2:.0f}%)",
                        f"Medium ({cfg['thr']+2:.0f}% – {cfg['thr']+5:.0f}%)",
                        f"Large ({cfg['thr']+5:.0f}% – {cfg['thr']+10:.0f}%)",
                        f"Extreme (>{cfg['thr']+10:.0f}%)"])

        # ── Factor 6: Price level ──────────────────────────────────────────
        print("\n  FACTOR 6: Stock price level")
        compare_factor(signals, "price",
            bins=[(0, 5), (5, 15), (15, 50), (50, 150), (150, 99999)],
            bin_labels=["<$5", "$5–$15", "$15–$50", "$50–$150", ">$150"])

        print()

    print("\n" + "═" * 68)
    print("  HOW TO USE THIS:")
    print("  ⚠️  LOW WR = that bucket drags your win rate down → filter it out")
    print("  🟢 HIGH WR = that bucket is your best signal → prioritise it")
    print("  Look for patterns that are consistent across all 3 tiers.")
    print("═" * 68 + "\n")


if __name__ == "__main__":
    main()
