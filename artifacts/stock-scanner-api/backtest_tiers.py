"""
backtest_tiers.py
=================
4-week backtest for Multi-Day Runner mid-cap and small-cap thresholds.
Downloads daily price history, finds every D1 gain event, measures D+5 return.
Compares multiple threshold levels to find the optimal cutoff.

Run:  python3 artifacts/stock-scanner-api/backtest_tiers.py
"""

import sys, math, statistics
from datetime import date, timedelta
from collections import defaultdict

try:
    import yfinance as yf
except ImportError:
    print("yfinance not installed"); sys.exit(1)

# ── Universes (representative subsets — liquid, optionable) ──────────────────

MIDCAP_SAMPLE = [
    # Tech
    "BILL","ZI","DOCN","GTLB","BRZE","PCOR","FSLY","PUBM","POWI","COHU",
    # Financials
    "ESNT","RDN","MTG","PNFP","BOKF","UMBF","FFIN","INDB","TBK","TCBI",
    # Healthcare
    "ACAD","ITCI","KRTX","ARWR","ALNY","IONS","EXEL","FATE","RVMD","ROIV",
    # Energy
    "CIVI","SM","MTDR","NOG","VTLE","FLNC","KOS","SBOW","MNRL",
    # Consumer
    "CROX","DECK","SKX","YETI","TXRH","SHAK","WING","PTON","BOOT","BJRI",
    # Industrials
    "GNRC","TREX","AZEK","BLDR","UFPI","BECN","IBP","GMS",
    # Additional liquid
    "VIRT","HUBB","WBS","IHG","AER","MTRN",
]

SMALLCAP_SAMPLE = [
    # Well-known optionable small caps
    "KODK","LAZR","LFMD","LGIH","MOMO","RCII","SDRL","SMCI","WOLF","WOOF",
    # Biotech (optionable)
    "ADMA","AKRO","ARVN","AXSM","BEAM","BPMC","BHVN","BCRX","IMVT","ARQT",
    # Energy small
    "AMPY","CDEV","DNOW","ENPH","GPRE","FLNG","GATO","NOG","SBOW",
    # Consumer
    "BLNK","BOOT","RCII","LOOP","LOVE","JACK","RUTH","CAKE","FAT","NATH",
    # Tech small
    "INPX","OOMA","RBBN","CRNT","EVLV","LIQT","DMRC","CLFD",
    # Industrial small
    "DXPE","FWRD","GLDD","GRBK","HEES","JJSF","LCII","LDOS",
    # Additional
    "AAON","ACNB","ADTN","ZION","ZEUS","ZETA","SMBC","MSTR",
]

LARGE_CAP_SAMPLE = [
    "AAPL","MSFT","NVDA","AMZN","META","GOOGL","TSLA","AMD","NFLX","CRM",
    "ORCL","ADBE","QCOM","TXN","MU","AVGO","AMAT","MRVL","PANW","CRWD",
    "NET","SNOW","PLTR","COIN","JPM","BAC","GS","MS","V","MA",
    "JNJ","PFE","ABBV","LLY","AMGN","ISRG","UNH","XOM","CVX","COP",
    "HD","LOW","COST","WMT","MCD","SBUX","NKE","DIS","F","GM",
]

# ── Config ───────────────────────────────────────────────────────────────────

LOOKBACK_DAYS   = 40    # calendar days (~28 trading days)
D5_HOLD         = 5     # hold through D+5 close
THRESHOLDS      = [2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0]
DOWNLOAD_PERIOD = "3mo"  # yfinance period string

# ── Download ─────────────────────────────────────────────────────────────────

def download_prices(tickers: list[str]) -> dict[str, list[tuple]]:
    """Return {ticker: [(date, close), ...]} sorted by date, last 40 days."""
    print(f"  Downloading {len(tickers)} tickers...", flush=True)
    raw = yf.download(
        tickers, period=DOWNLOAD_PERIOD, interval="1d",
        auto_adjust=True, progress=False, threads=True,
    )
    if raw.empty:
        return {}

    cutoff = date.today() - timedelta(days=LOOKBACK_DAYS)
    result: dict[str, list[tuple]] = {}

    for tkr in tickers:
        try:
            if len(tickers) == 1:
                closes = raw["Close"].dropna()
            else:
                closes = raw["Close"][tkr].dropna()
            rows = [(pd_idx.date(), float(v)) for pd_idx, v in closes.items()
                    if pd_idx.date() >= cutoff]
            if len(rows) >= 6:
                result[tkr] = sorted(rows, key=lambda x: x[0])
        except Exception:
            pass
    print(f"  Got data for {len(result)}/{len(tickers)} tickers", flush=True)
    return result

# ── Backtest engine ──────────────────────────────────────────────────────────

def run_backtest(prices: dict[str, list[tuple]], label: str) -> dict:
    """
    For each ticker, find every day with gain >= each threshold.
    Measure the D+5 close return from that entry.
    """
    events: dict[float, list[float]] = defaultdict(list)   # threshold -> list of D5 returns

    for tkr, rows in prices.items():
        for i in range(1, len(rows) - D5_HOLD):
            d0_date, d0_close = rows[i - 1]
            d1_date, d1_close = rows[i]
            d5_date, d5_close = rows[i + D5_HOLD - 1] if i + D5_HOLD - 1 < len(rows) else (None, None)

            if d5_close is None or d0_close <= 0:
                continue

            d1_gain = (d1_close - d0_close) / d0_close * 100
            d5_ret  = (d5_close - d1_close) / d1_close * 100   # entry = D1 close, exit = D5 close

            for thr in THRESHOLDS:
                if d1_gain >= thr:
                    events[thr].append(d5_ret)

    return events

# ── Report ───────────────────────────────────────────────────────────────────

def report(events: dict, tier_label: str, current_threshold: float, current_strong: float):
    print(f"\n{'='*64}")
    print(f"  {tier_label}")
    print(f"  Current config: D1 ≥{current_threshold}% | STRONG ≥{current_strong}%")
    print(f"{'='*64}")
    print(f"  {'Threshold':>10}  {'Signals':>8}  {'Win Rate':>9}  {'Avg D5%':>9}  {'EV':>9}  Verdict")
    print(f"  {'-'*60}")

    best_ev = -999
    best_thr = current_threshold

    for thr in THRESHOLDS:
        rets = events.get(thr, [])
        n = len(rets)
        if n < 5:
            print(f"  {f'≥{thr}%':>10}  {n:>8}  {'—':>9}  {'—':>9}  {'—':>9}  (too few)")
            continue
        wins     = sum(1 for r in rets if r > 0)
        win_rate = wins / n * 100
        avg_ret  = statistics.mean(rets)
        ev       = win_rate / 100 * avg_ret + (1 - win_rate / 100) * statistics.mean([r for r in rets if r <= 0] or [0])

        marker = ""
        if thr == current_threshold:
            marker = " ← CURRENT"
        elif thr == best_thr:
            marker = " ← BEST EV"

        if avg_ret > best_ev:
            best_ev = avg_ret
            best_thr = thr

        print(f"  {f'≥{thr}%':>10}  {n:>8}  {win_rate:>8.1f}%  {avg_ret:>+8.1f}%  {ev:>+8.1f}%{marker}")

    print()
    if best_thr != current_threshold:
        print(f"  ⚠️  RECOMMENDATION: Change threshold from ≥{current_threshold}% → ≥{best_thr}%")
    else:
        print(f"  ✅  Current threshold ≥{current_threshold}% is optimal in this dataset")

# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "="*64)
    print("  MULTI-DAY RUNNER — 4-WEEK THRESHOLD BACKTEST")
    print(f"  Period: last {LOOKBACK_DAYS} calendar days | Hold: D1 entry → D{D5_HOLD} close")
    print("="*64)

    # Large cap (control group — we know ≥3% works)
    print("\n[1/3] Large Cap (control group)...")
    lg_prices = download_prices(LARGE_CAP_SAMPLE)
    lg_events = run_backtest(lg_prices, "large")
    report(lg_events, "LARGE CAP ($10B+) — control", 3.0, 5.0)

    # Mid cap
    print("\n[2/3] Mid Cap...")
    mid_prices = download_prices(MIDCAP_SAMPLE)
    mid_events = run_backtest(mid_prices, "mid")
    report(mid_events, "MID CAP ($2B–$10B)", 4.0, 7.0)

    # Small cap
    print("\n[3/3] Small Cap...")
    sm_prices = download_prices(SMALLCAP_SAMPLE)
    sm_events = run_backtest(sm_prices, "small")
    report(sm_events, "SMALL CAP ($300M–$2B)", 5.0, 10.0)

    print("\n" + "="*64)
    print("  BACKTEST COMPLETE")
    print("="*64 + "\n")

if __name__ == "__main__":
    main()
