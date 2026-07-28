"""
orb_backtest.py
===================================================================
Opening Range Breakout (ORB) Long — 2-Year Backtest
===================================================================

Strategy (mirrors Pine Script exactly):
  - Opening range: First 30 min (9:30–10:00 AM ET, bars where bar OPEN >= 9:30
    and bar CLOSE <= 10:00)
  - Entry:        First 5-min bar that CLOSES above ORB High, after 10:00 AM ET
  - Hard stop:    entry_price × 0.95  (5% fixed floor)
  - Trail stop:   highest_price × 0.90  (10% trailing from peak)
  - Effective:    max(hard_stop, trail_stop)
  - Exit trigger: low of any bar < effective_stop  → fill at effective_stop
  - EOD close:    force-exit at 15:55 bar close if still open
  - One trade per ticker per day

Universe: top-150 liquid tickers from polygon_market_daily
  (2024-07-28→2026-07-25, price≥$5, vol≥500k daily, appeared 400+ days)

Data: Polygon /v2/aggs/ticker/{t}/range/5/minute/{from}/{to}
      2024-07-28 → 2026-07-25

Run: python3 artifacts/stock-scanner-api/orb_backtest.py
"""

import os
import sys
import time
import json
import datetime as dt
import urllib.request
import urllib.parse
import psycopg2
import psycopg2.extras
from collections import defaultdict

# ── Config ────────────────────────────────────────────────────────────────────
POLYGON_API_KEY = os.environ.get("POLYGON_API_KEY", "")
DATABASE_URL    = os.environ.get("DATABASE_URL", "")

BACKTEST_START  = "2024-07-28"
BACKTEST_END    = "2026-07-25"
MAX_TICKERS     = 40   # full universe; batched by --batch argument
MIN_PRICE       = 5.0
MIN_VOLUME      = 500_000
RATE_LIMIT_SEC  = 12.0          # 1 call / 12s  → never hits Starter rate cap
RETRY_MAX       = 3             # fallback retries; shouldn't be needed
RETRY_BASE_SEC  = 60.0

# Strategy params (mirrors Pine Script)
HARD_STOP_PCT   = 0.95          # 5% hard stop  (entry × 0.95)
TRAIL_PCT       = 0.90          # 10% trailing  (highest × 0.90)

# Session times (ET = UTC-4 in summer, UTC-5 in winter; we use offset per bar)
_ORB_START   = dt.time(9,  30)
_ORB_END     = dt.time(10,  0)   # exclusive — bars with bar_open >= 9:30 & bar_close <= 10:00
_ENTRY_AFTER = dt.time(10,  0)   # entry only on bars that START at or after 10:00
_EOD_CUTOFF  = dt.time(15, 55)   # last bar we allow as an exit; force-close here


# ── Polygon helpers ───────────────────────────────────────────────────────────
def _polygon_get(url: str, extra_params: dict) -> dict:
    """GET with retry-after support for 429."""
    import urllib.error
    params = {**extra_params, "apiKey": POLYGON_API_KEY}
    qs  = urllib.parse.urlencode(params)
    full_url = f"{url}?{qs}"
    for attempt in range(RETRY_MAX):
        try:
            req = urllib.request.urlopen(full_url, timeout=25)
            return json.loads(req.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = float(e.headers.get("Retry-After", RETRY_BASE_SEC))
                wait = max(wait, RETRY_BASE_SEC)
                print(f"\n      [429] rate limited — waiting {wait:.0f}s (attempt {attempt+1}/{RETRY_MAX})", flush=True)
                time.sleep(wait)
            else:
                raise
    raise RuntimeError(f"max retries exceeded for {url}")


_CHUNK_PAIRS = [
    ("2024-07-28", "2025-07-25"),   # year 1
    ("2025-07-25", "2026-07-25"),   # year 2
]


def fetch_5min_bars(ticker: str, from_date: str, to_date: str) -> list:
    """
    Fetch all 5-min bars for ticker over the full window by splitting into
    1-year chunks. Each chunk fits under Polygon's 50,000-bar page limit
    (~250 trading days × ~190 bars/day ≈ 47,500 bars), avoiding pagination
    and the cascade of 429s it triggers.
    """
    all_bars = []
    for chunk_from, chunk_to in _CHUNK_PAIRS:
        # Respect the caller's date range
        if chunk_to <= from_date or chunk_from >= to_date:
            continue
        c_from = max(chunk_from, from_date)
        c_to   = min(chunk_to,   to_date)

        url    = (f"https://api.polygon.io/v2/aggs/ticker/{ticker}"
                  f"/range/5/minute/{c_from}/{c_to}")
        params = {"adjusted": "true", "sort": "asc", "limit": "50000"}

        time.sleep(RATE_LIMIT_SEC)
        try:
            data = _polygon_get(url, params)
        except Exception as e:
            print(f"\n      ERR({ticker} {c_from}→{c_to}): {e}", flush=True)
            continue

        chunk_bars = data.get("results") or []
        all_bars.extend(chunk_bars)

        # If Polygon still wants to paginate (edge case), follow once
        next_url = data.get("next_url")
        if next_url:
            time.sleep(RATE_LIMIT_SEC)
            try:
                data2 = _polygon_get(next_url.split("?")[0], {})
                all_bars.extend(data2.get("results") or [])
            except Exception as e:
                print(f"\n      ERR({ticker} page2): {e}", flush=True)

    return all_bars


def bars_to_days(bars: list) -> dict:
    """
    Group raw Polygon bars by ET trading date.
    Polygon 't' is bar-open timestamp in ms UTC.
    We bucket by the ET date of the bar open.
    Returns {date_str: [bar_dict_with_et_time, ...]} sorted by time.
    """
    UTC = dt.timezone.utc
    days = defaultdict(list)
    for b in bars:
        utc_dt = dt.datetime.fromtimestamp(b["t"] / 1000, tz=UTC)
        # Approximate ET: UTC-5 winter / UTC-4 summer
        # Good enough for 9:30/10:00/15:55 buckets (30-min slack)
        et_dt  = utc_dt.astimezone(dt.timezone(dt.timedelta(hours=-5)))
        # If this looks like after midnight ET in winter, re-check with -4
        # Simple heuristic: if hour < 5, something's wrong; use -4
        if et_dt.hour < 5:
            et_dt = utc_dt.astimezone(dt.timezone(dt.timedelta(hours=-4)))
        days[et_dt.date().isoformat()].append({
            **b,
            "_et": et_dt,
            "_t":  et_dt.time().replace(second=0, microsecond=0),
        })
    return {k: sorted(v, key=lambda x: x["_et"]) for k, v in days.items()}


# ── Universe ──────────────────────────────────────────────────────────────────
def get_universe(db_url: str) -> list:
    conn = psycopg2.connect(db_url, connect_timeout=10)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT ticker,
                       COUNT(*)               AS days,
                       AVG(open_price)        AS avg_price,
                       AVG(volume)            AS avg_vol
                FROM polygon_market_daily
                WHERE scan_date BETWEEN %s AND %s
                  AND open_price >= %s
                  AND volume     >= %s
                GROUP BY ticker
                HAVING COUNT(*) >= 400
                ORDER BY AVG(volume) DESC
                LIMIT %s
            """, (BACKTEST_START, BACKTEST_END, MIN_PRICE, MIN_VOLUME, MAX_TICKERS))
            return [r["ticker"] for r in cur.fetchall()]
    finally:
        conn.close()


# ── ORB simulation (one ticker, full history) ─────────────────────────────────
def simulate_orb(ticker: str, day_bars: dict) -> list:
    trades = []

    for day, bars in sorted(day_bars.items()):
        # Restrict to regular session only
        session = [b for b in bars
                   if _ORB_START <= b["_t"] < dt.time(16, 0)]
        if len(session) < 8:
            continue

        # ── Build ORB ────────────────────────────────────────────────────────
        # ORB bars: bars whose bar_open time is in [9:30, 10:00)
        orb_bars = [b for b in session if _ORB_START <= b["_t"] < _ORB_END]
        if len(orb_bars) < 2:          # need at least 2 bars for a valid range
            continue

        orb_high = max(b["h"] for b in orb_bars)
        orb_low  = min(b["l"] for b in orb_bars)

        # ── Post-10:00 bars (possible entry + management) ─────────────────────
        post = [b for b in session if b["_t"] >= _ENTRY_AFTER]
        if not post:
            continue

        # ── Entry: first bar closing above ORB High ───────────────────────────
        entry_idx = None
        entry_price = None
        for i, bar in enumerate(post):
            if bar["c"] > orb_high:
                entry_idx   = i
                entry_price = bar["c"]
                break

        if entry_price is None:
            continue    # no breakout today

        # ── Initialise stops (Pine Script lines 32-34) ────────────────────────
        hard_stop     = entry_price * HARD_STOP_PCT    # 5% fixed floor
        highest_price = entry_price
        trail_stop    = entry_price * TRAIL_PCT         # 10% initial trail

        exit_price  = None
        exit_reason = None

        # Management starts on the bar AFTER entry
        mgmt = post[entry_idx + 1:]

        for bar in mgmt:
            # Pine: highestPrice := math.max(highestPrice, high)
            highest_price = max(highest_price, bar["h"])
            trail_stop    = highest_price * TRAIL_PCT
            eff_stop      = max(hard_stop, trail_stop)   # Pine line 46

            if bar["l"] < eff_stop:
                # Fill at effective stop (conservative; real fill may be worse)
                exit_price  = eff_stop
                exit_reason = "stop"
                break

            # EOD force-close
            if bar["_t"] >= _EOD_CUTOFF:
                exit_price  = bar["c"]
                exit_reason = "eod_close"
                break

        # Still open if we exhausted bars without hitting stop or EOD cutoff
        if exit_price is None:
            exit_price  = post[-1]["c"]
            exit_reason = "eod_close"

        pnl_pct = (exit_price - entry_price) / entry_price * 100

        trades.append({
            "ticker":        ticker,
            "date":          day,
            "entry_price":   round(entry_price,   4),
            "exit_price":    round(exit_price,    4),
            "orb_high":      round(orb_high,      4),
            "orb_low":       round(orb_low,       4),
            "highest_price": round(highest_price, 4),
            "pnl_pct":       round(pnl_pct,       4),
            "exit_reason":   exit_reason,
        })

    return trades


# ── Reporting helpers ─────────────────────────────────────────────────────────
def _pct_row(label, vals, width=16):
    if not vals:
        return f"  {label:<{width}} n=0"
    wr  = sum(1 for v in vals if v > 0) / len(vals) * 100
    avg = sum(vals) / len(vals)
    med = sorted(vals)[len(vals)//2]
    return (f"  {label:<{width}} n={len(vals):5,}  "
            f"WR={wr:4.0f}%  avg={avg:+5.2f}%  med={med:+5.2f}%")


# ── Merge partial batch files into final summary ───────────────────────────────
def merge_batches(out_dir: str):
    import glob
    SEP = "=" * 72
    files = sorted(glob.glob(os.path.join(out_dir, "orb_backtest_partial_*.json")))
    if not files:
        print("No partial files found to merge.")
        return

    all_trades = []
    for f in files:
        with open(f) as fh:
            all_trades.extend(json.load(fh))

    if not all_trades:
        print("No trades in partial files.")
        return

    total  = len(all_trades)
    pnls   = [t["pnl_pct"] for t in all_trades]
    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    wr     = len(wins) / total * 100
    avg_r  = sum(pnls) / total
    avg_w  = sum(wins) / len(wins) if wins else 0
    avg_l  = sum(losses) / len(losses) if losses else 0
    ev     = (wr/100)*avg_w + (1 - wr/100)*avg_l
    wl_ratio = abs(avg_w / avg_l) if avg_l else float("inf")
    med_r  = sorted(pnls)[total // 2]

    sorted_trades = sorted(all_trades, key=lambda x: (x["date"], x["ticker"]))
    equity = peak = max_dd = 0.0
    for t in sorted_trades:
        equity += t["pnl_pct"]
        peak    = max(peak, equity)
        max_dd  = max(max_dd, peak - equity)

    by_exit = defaultdict(list)
    by_year = defaultdict(list)
    for t in all_trades:
        by_exit[t["exit_reason"]].append(t["pnl_pct"])
        by_year[t["date"][:4]].append(t["pnl_pct"])

    tickers_seen = list({t["ticker"] for t in all_trades})

    print(SEP)
    print("  ORB LONG  |  5% Hard Stop + 10% Trailing  |  FINAL RESULTS")
    print(f"  Window : {BACKTEST_START} → {BACKTEST_END}")
    print(SEP)
    print(f"\n  {'Universe':<24} {len(tickers_seen)} tickers")
    print(f"  {'Total trades':<24} {total:,}")
    print(f"  {'Win rate':<24} {wr:.1f}%")
    print(f"  {'Avg return / trade':<24} {avg_r:+.2f}%")
    print(f"  {'Median return':<24} {med_r:+.2f}%")
    print(f"  {'Avg win':<24} {avg_w:+.2f}%")
    print(f"  {'Avg loss':<24} {avg_l:+.2f}%")
    print(f"  {'EV per trade':<24} {ev:+.3f}%")
    print(f"  {'Win/Loss ratio':<24} {wl_ratio:.2f}×")
    print(f"  {'Max drawdown (equal-wt)':<24} {max_dd:.1f}% cumulative pts")

    print(f"\n  Exit breakdown:")
    print(f"  {'Exit type':<16} {'n':>6}  {'WR':>5}  {'Avg ret':>8}  {'Med ret':>8}")
    print(f"  {'-'*52}")
    for reason in ("stop", "eod_close"):
        vals = by_exit.get(reason, [])
        if not vals:
            continue
        r_wr  = sum(1 for v in vals if v > 0) / len(vals) * 100
        r_avg = sum(vals) / len(vals)
        r_med = sorted(vals)[len(vals)//2]
        print(f"  {reason:<16} {len(vals):6,}  {r_wr:4.0f}%  {r_avg:+7.2f}%  {r_med:+7.2f}%")

    print(f"\n  By year:")
    print(f"  {'Year':<8} {'n':>6}  {'WR':>5}  {'Avg ret':>8}  {'Med ret':>8}")
    print(f"  {'-'*44}")
    for yr in sorted(by_year):
        vals  = by_year[yr]
        y_wr  = sum(1 for v in vals if v > 0) / len(vals) * 100
        y_avg = sum(vals) / len(vals)
        y_med = sorted(vals)[len(vals)//2]
        print(f"  {yr:<8} {len(vals):6,}  {y_wr:4.0f}%  {y_avg:+7.2f}%  {y_med:+7.2f}%")

    top10 = sorted(all_trades, key=lambda x: x["pnl_pct"], reverse=True)[:10]
    bot10 = sorted(all_trades, key=lambda x: x["pnl_pct"])[:10]

    print(f"\n  Top 10 trades:")
    print(f"  {'Ticker':<7} {'Date':<12} {'Entry':>7} {'Exit':>7} {'P&L%':>7}  Exit")
    print(f"  {'-'*56}")
    for t in top10:
        print(f"  {t['ticker']:<7} {t['date']:<12} "
              f"{t['entry_price']:7.2f} {t['exit_price']:7.2f} "
              f"{t['pnl_pct']:+6.1f}%  {t['exit_reason']}")

    print(f"\n  Bottom 10 trades:")
    print(f"  {'Ticker':<7} {'Date':<12} {'Entry':>7} {'Exit':>7} {'P&L%':>7}  Exit")
    print(f"  {'-'*56}")
    for t in bot10:
        print(f"  {t['ticker']:<7} {t['date']:<12} "
              f"{t['entry_price']:7.2f} {t['exit_price']:7.2f} "
              f"{t['pnl_pct']:+6.1f}%  {t['exit_reason']}")

    out = os.path.join(out_dir, "orb_backtest_results.json")
    with open(out, "w") as f:
        json.dump({
            "summary": {
                "strategy": "ORB Long 5% Hard + 10% Trail",
                "window":   f"{BACKTEST_START} to {BACKTEST_END}",
                "universe": len(tickers_seen),
                "total_trades":        total,
                "win_rate_pct":        round(wr, 2),
                "avg_return_pct":      round(avg_r, 4),
                "median_return_pct":   round(med_r, 4),
                "avg_win_pct":         round(avg_w, 4),
                "avg_loss_pct":        round(avg_l, 4),
                "ev_per_trade_pct":    round(ev,    4),
                "win_loss_ratio":      round(wl_ratio, 3),
                "max_drawdown_pct":    round(max_dd, 4),
            },
            "by_year":  {yr: {
                "n": len(v), "wr": round(sum(1 for x in v if x>0)/len(v)*100, 1),
                "avg": round(sum(v)/len(v), 4)
            } for yr, v in by_year.items()},
            "by_exit": {reason: {
                "n": len(v), "wr": round(sum(1 for x in v if x>0)/len(v)*100, 1),
                "avg": round(sum(v)/len(v), 4)
            } for reason, v in by_exit.items()},
            "trades": sorted_trades,
        }, f, indent=2)
    print(f"\n  Full trade log → {out}")
    print(SEP)


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    """
    Batch mode: python3 orb_backtest.py --batch 0 --batch-size 4
    Merge mode: python3 orb_backtest.py --merge
    """
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch",      type=int, default=0)
    ap.add_argument("--batch-size", type=int, default=4)
    ap.add_argument("--merge",      action="store_true")
    args = ap.parse_args()

    out_dir = os.path.dirname(os.path.abspath(__file__))

    if args.merge:
        merge_batches(out_dir)
        return

    if not POLYGON_API_KEY:
        sys.exit("[ERROR] POLYGON_API_KEY not set.")
    if not DATABASE_URL:
        sys.exit("[ERROR] DATABASE_URL not set.")

    # 1. Universe
    tickers = get_universe(DATABASE_URL)
    if not tickers:
        sys.exit("[ERROR] No tickers returned from universe query.")

    batch_tickers = tickers[args.batch * args.batch_size :
                            (args.batch + 1) * args.batch_size]
    if not batch_tickers:
        print(f"Batch {args.batch} is empty — all tickers processed.")
        return

    print(f"\n[batch {args.batch}] tickers: {batch_tickers}")
    print(f"  Window: {BACKTEST_START} → {BACKTEST_END}\n")

    all_trades = []
    for idx, ticker in enumerate(batch_tickers, 1):
        print(f"  [{idx}/{len(batch_tickers)}] {ticker:<6}", end="  ", flush=True)
        bars = fetch_5min_bars(ticker, BACKTEST_START, BACKTEST_END)
        if not bars:
            print("→ no data")
            continue
        day_bars = bars_to_days(bars)
        trades   = simulate_orb(ticker, day_bars)
        all_trades.extend(trades)
        print(f"→ {len(bars):6,} bars  {len(day_bars):3d} days  {len(trades):3d} trades")

    # Save partial results for this batch
    partial_file = os.path.join(out_dir, f"orb_backtest_partial_{args.batch:02d}.json")
    with open(partial_file, "w") as f:
        json.dump(all_trades, f)
    print(f"\n  Batch {args.batch} done — {len(all_trades)} trades → {partial_file}")


if __name__ == "__main__":
    main()
