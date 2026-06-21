"""
backtest_tiers.py  —  Multi-Day Runner threshold backtest
=========================================================
Pulls liquid, optionable tickers from Finviz for each cap tier,
downloads 3 months of daily prices from yfinance, then compares
OLD vs NEW thresholds directly.

Run:  python3 artifacts/stock-scanner-api/backtest_tiers.py
"""

import re, sys, time, math, statistics
from collections import defaultdict

try:
    import requests
except ImportError:
    print("requests not installed"); sys.exit(1)
try:
    import yfinance as yf
except ImportError:
    print("yfinance not installed"); sys.exit(1)


# ── Finviz scraper ─────────────────────────────────────────────────────────────

_FV_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def finviz_universe(filters: str, max_pages: int = 5,
                    min_price: float = 2.0, min_vol: int = 100_000) -> list[str]:
    tickers: list[str] = []
    seen: set = set()
    for pg in range(max_pages):
        start = pg * 20 + 1
        url = f"https://finviz.com/screener.ashx?v=111&f={filters}&o=-volume&r={start}"
        try:
            r = requests.get(url, headers=_FV_HEADERS, timeout=15)
            if not r.ok:
                break
            new_this_page = 0
            for chunk in re.split(r'<tr class="styled-row', r.text)[1:]:
                cells = [
                    re.sub(r"<[^>]+>", "", c).strip()
                    for c in re.findall(r"<td[^>]*>(.*?)</td>", chunk, re.S)
                ]
                if len(cells) < 11:
                    continue
                tk = cells[1].upper().strip()
                if not tk or len(tk) > 5 or "." in tk or tk in seen:
                    continue
                try:
                    px = float(cells[8].replace(",", ""))
                except Exception:
                    px = 0.0
                try:
                    vol = int(cells[10].replace(",", ""))
                except Exception:
                    vol = 0
                if px < min_price or vol < min_vol:
                    continue
                seen.add(tk)
                tickers.append(tk)
                new_this_page += 1
            if new_this_page == 0:
                break
            time.sleep(0.4)
        except Exception as e:
            print(f"  Finviz page {pg+1} error: {e}")
            break
    return tickers


# ── Price download ─────────────────────────────────────────────────────────────

def download_prices(tickers: list[str], period: str = "3mo") -> dict[str, list[tuple]]:
    print(f"  Downloading {len(tickers)} tickers ({period})...", flush=True)
    if not tickers:
        return {}
    all_prices: dict[str, list[tuple]] = {}
    for i in range(0, len(tickers), 100):
        batch = tickers[i:i + 100]
        try:
            raw = yf.download(
                batch, period=period, interval="1d",
                auto_adjust=True, progress=False, threads=True,
            )
            if raw.empty:
                continue
            for tkr in batch:
                try:
                    closes = raw["Close"][tkr].dropna() if len(batch) > 1 else raw["Close"].dropna()
                    rows = [(idx.date(), float(v)) for idx, v in closes.items()]
                    if len(rows) >= 10:
                        all_prices[tkr] = sorted(rows, key=lambda x: x[0])
                except Exception:
                    pass
        except Exception as e:
            print(f"  Batch error: {e}")
        if i + 100 < len(tickers):
            time.sleep(1)
    print(f"  Got data for {len(all_prices)}/{len(tickers)} tickers", flush=True)
    return all_prices


# ── Backtest engine ────────────────────────────────────────────────────────────

D5_HOLD = 5


def run_backtest(prices: dict[str, list[tuple]],
                 thresholds: list[float]) -> dict[float, list[float]]:
    events: dict[float, list[float]] = defaultdict(list)
    for tkr, rows in prices.items():
        for i in range(1, len(rows) - D5_HOLD):
            d0_close = rows[i - 1][1]
            d1_close = rows[i][1]
            d5_close = rows[i + D5_HOLD - 1][1]
            if d0_close <= 0 or d1_close <= 0 or d5_close <= 0:
                continue
            d1_gain = (d1_close - d0_close) / d0_close * 100
            d5_ret  = (d5_close - d1_close)  / d1_close  * 100
            for thr in thresholds:
                if d1_gain >= thr:
                    events[thr].append(d5_ret)
    return events


def stats(rets: list[float]) -> tuple:
    if not rets:
        return 0, 0.0, 0.0, 0.0
    n    = len(rets)
    wr   = sum(1 for r in rets if r > 0) / n * 100
    avg  = statistics.mean(rets)
    med  = statistics.median(rets)
    sd   = statistics.stdev(rets) if len(rets) > 1 else 1
    shrp = avg / sd if sd > 0 else 0
    return n, wr, avg, med, shrp


# ── Tier configs ───────────────────────────────────────────────────────────────

TIERS = [
    {
        "name":    "LARGE CAP  ($10B+)",
        "filter":  "cap_large,sh_opt_option",
        "min_vol": 500_000,
        "pages":   6,
        "old_thr": 3.0,
        "new_thr": 3.0,   # unchanged — data validated it
        "strong_old": 5.0,
        "strong_new": 5.0,
    },
    {
        "name":    "MID CAP  ($2B – $10B)",
        "filter":  "cap_mid,sh_opt_option",
        "min_vol": 200_000,
        "pages":   6,
        "old_thr": 4.0,
        "new_thr": 5.0,   # raised by data
        "strong_old": 7.0,
        "strong_new": 7.0,
    },
    {
        "name":    "SMALL CAP  ($300M – $2B)",
        "filter":  "cap_small,sh_opt_option",
        "min_vol": 100_000,
        "pages":   6,
        "old_thr": 5.0,
        "new_thr": 7.0,   # raised by data
        "strong_old": 10.0,
        "strong_new": 10.0,
    },
]


# ── Main ───────────────────────────────────────────────────────────────────────

def bar(val: float, ref: float, width: int = 20) -> str:
    """Simple ASCII bar showing val vs ref."""
    if ref == 0:
        return ""
    ratio = val / ref
    filled = max(0, min(width, round(ratio * width)))
    return "█" * filled + "░" * (width - filled)


def main():
    print("\n" + "═" * 70)
    print("  MULTI-DAY RUNNER — OLD vs NEW THRESHOLD COMPARISON")
    print(f"  Finviz universe  ×  3-month daily history  ×  D1→D{D5_HOLD} hold")
    print("═" * 70)

    summary_rows = []

    for cfg in TIERS:
        print(f"\n{'─'*70}")
        print(f"  {cfg['name']}  —  pulling Finviz universe...")
        tickers = finviz_universe(cfg["filter"], max_pages=cfg["pages"],
                                  min_vol=cfg["min_vol"])
        print(f"  {len(tickers)} tickers from Finviz")
        if len(tickers) < 10:
            print("  ⚠️  Too few — skipping")
            continue

        prices = download_prices(tickers)
        if len(prices) < 5:
            print("  ⚠️  Too few price series — skipping")
            continue

        all_thresholds = list({cfg["old_thr"], cfg["new_thr"],
                                cfg["strong_old"], cfg["strong_new"]})
        events = run_backtest(prices, sorted(set(all_thresholds)))

        old_n, old_wr, old_avg, old_med, old_shrp = stats(events.get(cfg["old_thr"], []))
        new_n, new_wr, new_avg, new_med, new_shrp = stats(events.get(cfg["new_thr"], []))
        sold_n, sold_wr, sold_avg, *_ = stats(events.get(cfg["strong_old"], []))
        snew_n, snew_wr, snew_avg, *_ = stats(events.get(cfg["strong_new"], []))

        print(f"\n  {'':30}  {'Signals':>8}  {'Win Rate':>9}  {'Avg D5':>8}  {'Median':>8}  {'Sharpe':>7}")
        print(f"  {'─'*68}")

        changed = cfg["old_thr"] != cfg["new_thr"]
        tag_old = "  ← OLD" if changed else "  ← UNCHANGED"
        tag_new = "  ← NEW ✓" if changed else ""

        print(f"  {'D1 threshold  OLD  ≥'+str(cfg['old_thr'])+'%':30}  {old_n:>8}  {old_wr:>8.1f}%  {old_avg:>+7.2f}%  {old_med:>+7.2f}%  {old_shrp:>+6.3f}{tag_old}")
        if changed:
            wr_delta  = new_wr  - old_wr
            avg_delta = new_avg - old_avg
            shrp_delta= new_shrp - old_shrp
            wr_sign   = "+" if wr_delta  >= 0 else ""
            avg_sign  = "+" if avg_delta >= 0 else ""
            print(f"  {'D1 threshold  NEW  ≥'+str(cfg['new_thr'])+'%':30}  {new_n:>8}  {new_wr:>8.1f}%  {new_avg:>+7.2f}%  {new_med:>+7.2f}%  {new_shrp:>+6.3f}{tag_new}")
            print(f"  {'  Change':30}  {new_n-old_n:>+8}  {wr_sign}{wr_delta:>7.1f}pp  {avg_sign}{avg_delta:>6.2f}pp  {'':>8}  {'+' if shrp_delta>=0 else ''}{shrp_delta:>.3f}")

        print(f"\n  {'STRONG  OLD  ≥'+str(cfg['strong_old'])+'%':30}  {sold_n:>8}  {sold_wr:>8.1f}%  {sold_avg:>+7.2f}%")
        if cfg["strong_old"] != cfg["strong_new"]:
            print(f"  {'STRONG  NEW  ≥'+str(cfg['strong_new'])+'%':30}  {snew_n:>8}  {snew_wr:>8.1f}%  {snew_avg:>+7.2f}%")

        summary_rows.append({
            "name":       cfg["name"],
            "changed":    changed,
            "old_thr":    cfg["old_thr"],
            "new_thr":    cfg["new_thr"],
            "old_wr":     old_wr,
            "new_wr":     new_wr,
            "old_avg":    old_avg,
            "new_avg":    new_avg,
            "old_signals":old_n,
            "new_signals":new_n,
        })

    # ── Final summary ──────────────────────────────────────────────────────────
    print("\n\n" + "═" * 70)
    print("  FINAL VERDICT")
    print("═" * 70)
    print(f"  {'Tier':<30}  {'Old':>14}  {'New':>14}  {'Δ WR':>7}  {'Δ Avg':>7}")
    print(f"  {'─'*66}")
    for r in summary_rows:
        if r["changed"]:
            delta_wr  = r["new_wr"]  - r["old_wr"]
            delta_avg = r["new_avg"] - r["old_avg"]
            print(f"  {r['name']:<30}  "
                  f"≥{r['old_thr']}%: {r['old_wr']:>4.1f}% WR  "
                  f"≥{r['new_thr']}%: {r['new_wr']:>4.1f}% WR  "
                  f"{'+' if delta_wr>=0 else ''}{delta_wr:.1f}pp  "
                  f"{'+' if delta_avg>=0 else ''}{delta_avg:.2f}%")
        else:
            print(f"  {r['name']:<30}  ≥{r['old_thr']}%: {r['old_wr']:>4.1f}% WR  (unchanged)")
    print("═" * 70 + "\n")


if __name__ == "__main__":
    main()
