"""
backtest_tiers.py  —  Multi-Day Runner threshold backtest
=========================================================
Step 1: Pull liquid, optionable tickers for each cap tier from Finviz.
Step 2: Download 3 months of daily OHLCV from yfinance.
Step 3: Find every D1 gain event at multiple threshold levels.
Step 4: Measure D1-close → D5-close return and report win rates.

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


# ── Finviz helpers (copied from main.py pattern) ──────────────────────────────

_FV_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def _parse_cap(s: str) -> float:
    s = (s or "").strip().upper()
    if not s or s == "-":
        return 0.0
    mult = 1.0
    if s.endswith("B"):
        mult, s = 1e9, s[:-1]
    elif s.endswith("M"):
        mult, s = 1e6, s[:-1]
    elif s.endswith("K"):
        mult, s = 1e3, s[:-1]
    try:
        return float(s.replace(",", "")) * mult
    except Exception:
        return 0.0


def finviz_universe(filters: str, max_pages: int = 5, min_price: float = 2.0,
                    min_vol: int = 100_000) -> list[str]:
    """
    Pull optionable tickers from the Finviz screener for a given filter string.
    Returns a list of ticker symbols, sorted by volume (biggest movers first).
    """
    tickers: list[str] = []
    seen: set = set()

    for pg in range(max_pages):
        start = pg * 20 + 1
        url = (
            f"https://finviz.com/screener.ashx?v=111"
            f"&f={filters}&o=-volume&r={start}"
        )
        try:
            r = requests.get(url, headers=_FV_HEADERS, timeout=15)
            if not r.ok:
                print(f"  Finviz page {pg+1}: HTTP {r.status_code} — stopping")
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
                break   # past last page

            time.sleep(0.4)   # be polite to Finviz

        except Exception as e:
            print(f"  Finviz page {pg+1} error: {e}")
            break

    return tickers


# ── Price download ────────────────────────────────────────────────────────────

def download_prices(tickers: list[str], period: str = "3mo") -> dict[str, list[tuple]]:
    """
    Return {ticker: [(date, open, close), ...]} for each ticker.
    Uses yfinance bulk download for speed.
    """
    print(f"  Downloading {len(tickers)} tickers ({period})...", flush=True)
    if not tickers:
        return {}

    # Batch in groups of 100 to avoid yfinance URL-length limits
    all_prices: dict[str, list[tuple]] = {}
    batch_size = 100

    for i in range(0, len(tickers), batch_size):
        batch = tickers[i:i + batch_size]
        try:
            raw = yf.download(
                batch, period=period, interval="1d",
                auto_adjust=True, progress=False, threads=True,
            )
            if raw.empty:
                continue

            for tkr in batch:
                try:
                    if len(batch) == 1:
                        closes = raw["Close"].dropna()
                        opens  = raw["Open"].dropna()
                    else:
                        closes = raw["Close"][tkr].dropna()
                        opens  = raw["Open"][tkr].dropna()

                    rows = []
                    for idx in closes.index:
                        if idx in opens.index:
                            rows.append((
                                idx.date(),
                                float(opens[idx]),
                                float(closes[idx]),
                            ))
                    if len(rows) >= 10:
                        all_prices[tkr] = sorted(rows, key=lambda x: x[0])
                except Exception:
                    pass
        except Exception as e:
            print(f"  Download batch error: {e}")

        if i + batch_size < len(tickers):
            time.sleep(1)   # pause between batches

    print(f"  Got data for {len(all_prices)}/{len(tickers)} tickers", flush=True)
    return all_prices


# ── Backtest engine ───────────────────────────────────────────────────────────

THRESHOLDS = [2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 6.0, 7.0, 8.0, 10.0, 12.0]
D5_HOLD    = 5   # hold through D+5 close


def run_backtest(prices: dict[str, list[tuple]]) -> dict[float, list[float]]:
    """
    For each ticker, find every day where the close gained >= threshold% vs prev close.
    Measure the next D+5 close return (entry = D1 close, exit = D5 close).
    Returns {threshold: [d5_return, ...]}.
    """
    events: dict[float, list[float]] = defaultdict(list)

    for tkr, rows in prices.items():
        for i in range(1, len(rows) - D5_HOLD):
            d0_close = rows[i - 1][2]
            d1_close = rows[i][2]
            d5_close = rows[i + D5_HOLD - 1][2]

            if d0_close <= 0 or d1_close <= 0 or d5_close <= 0:
                continue

            d1_gain = (d1_close - d0_close) / d0_close * 100
            d5_ret  = (d5_close - d1_close)  / d1_close  * 100   # D1-close entry → D5-close exit

            for thr in THRESHOLDS:
                if d1_gain >= thr:
                    events[thr].append(d5_ret)

    return events


# ── Report ────────────────────────────────────────────────────────────────────

def report(events: dict, tier_label: str, current_thr: float, current_strong: float):
    print(f"\n{'═'*72}")
    print(f"  {tier_label}")
    print(f"  Current scanner config: D1 ≥{current_thr}%  |  STRONG ≥{current_strong}%")
    print(f"{'═'*72}")
    print(f"  {'Threshold':>11}  {'Signals':>8}  {'Win Rate':>9}  {'Avg D5%':>9}  {'Median':>8}  {'Sharpe~':>8}")
    print(f"  {'─'*68}")

    best_avg = -999.0
    best_thr = current_thr

    for thr in THRESHOLDS:
        rets = events.get(thr, [])
        n = len(rets)
        if n < 10:
            print(f"  {f'≥{thr}%':>11}  {n:>8}  {'—':>9}  {'—':>9}  {'—':>8}  {'—':>8}  (too few)")
            continue

        wins     = sum(1 for r in rets if r > 0)
        wr       = wins / n * 100
        avg_ret  = statistics.mean(rets)
        med_ret  = statistics.median(rets)
        try:
            sharpe = avg_ret / statistics.stdev(rets) if len(rets) > 1 else 0
        except Exception:
            sharpe = 0

        marker = ""
        if thr == current_thr:
            marker = "  ← CURRENT"
        if thr == current_strong:
            marker += "  (STRONG)"

        if avg_ret > best_avg and n >= 20:
            best_avg = avg_ret
            best_thr = thr

        print(f"  {f'≥{thr}%':>11}  {n:>8}  {wr:>8.1f}%  {avg_ret:>+8.2f}%  {med_ret:>+7.2f}%  {sharpe:>+7.3f}{marker}")

    print()
    if best_thr != current_thr:
        print(f"  ⚡ DATA SAYS: Optimal threshold is ≥{best_thr}%  (current is ≥{current_thr}%)")
    else:
        print(f"  ✅  Current threshold ≥{current_thr}% holds up — data validates it")
    return best_thr


# ── Main ──────────────────────────────────────────────────────────────────────

TIER_CONFIGS = [
    {
        "label":          "LARGE CAP  ($10B+)",
        "finviz_filter":  "cap_large,sh_opt_option",
        "min_vol":        500_000,
        "pages":          6,
        "current_thr":    3.0,
        "current_strong": 5.0,
    },
    {
        "label":          "MID CAP  ($2B – $10B)",
        "finviz_filter":  "cap_mid,sh_opt_option",
        "min_vol":        200_000,
        "pages":          6,
        "current_thr":    4.0,
        "current_strong": 7.0,
    },
    {
        "label":          "SMALL CAP  ($300M – $2B)",
        "finviz_filter":  "cap_small,sh_opt_option",
        "min_vol":        100_000,
        "pages":          6,
        "current_thr":    5.0,
        "current_strong": 10.0,
    },
]


def main():
    print("\n" + "═"*72)
    print("  MULTI-DAY RUNNER — FINVIZ UNIVERSE × 3-MONTH THRESHOLD BACKTEST")
    print(f"  Strategy: BUY at D1 close  →  EXIT at D{D5_HOLD} close")
    print(f"  Thresholds tested: {THRESHOLDS}")
    print("═"*72)

    recommendations: list[tuple[str, float, float]] = []

    for cfg in TIER_CONFIGS:
        print(f"\n{'─'*72}")
        print(f"  [{cfg['label']}]  Pulling Finviz universe...")

        tickers = finviz_universe(
            cfg["finviz_filter"],
            max_pages=cfg["pages"],
            min_vol=cfg["min_vol"],
        )
        print(f"  Finviz returned {len(tickers)} tickers")

        if len(tickers) < 10:
            print("  ⚠️  Too few tickers — skipping tier")
            continue

        prices = download_prices(tickers, period="3mo")

        if len(prices) < 5:
            print("  ⚠️  Too few price series — skipping tier")
            continue

        events = run_backtest(prices)
        best = report(events, cfg["label"], cfg["current_thr"], cfg["current_strong"])
        recommendations.append((cfg["label"], cfg["current_thr"], best))

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n\n" + "═"*72)
    print("  SUMMARY — RECOMMENDED THRESHOLDS")
    print("═"*72)
    for label, cur, rec in recommendations:
        changed = "✅ keep" if rec == cur else f"⚡ CHANGE  ≥{cur}% → ≥{rec}%"
        print(f"  {label:<32}  {changed}")
    print("═"*72 + "\n")


if __name__ == "__main__":
    main()
