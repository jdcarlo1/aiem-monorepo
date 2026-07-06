"""
AIEM Nano-Cap Premarket Scanner — 1-Year Backtest
===================================================
Tests the exact same criteria as aiem_process.py against the last 13 months
of daily data in polygon_market_daily.

Entry assumptions tested in parallel:
  ENTRY A: open_price  (9:30 AM open print — best-case, catches the spike)
  ENTRY B: vwap        (mid-morning average — approximates 9:45–10:30 entry)

Returns measured:
  T+1  = next market-day close  (day-trade / overnight)
  T+3  = 3 trading days later
  T+5  = 5 trading days later

Criteria mirroring aiem_process.py funnel:
  price     $1–$20           (open_price)
  gap       > 2%             (gap_pct)
  volume    > 50,000         (daily proxy; premarket vol not in daily data)
  rvol      > 1.5×           (proxy for elevated premarket interest)
  NO float filter            (only 151 tickers in reference cache — noted)
"""

import os
import sys
import json
import psycopg2
from datetime import date, timedelta
from collections import defaultdict

DB_URL = os.environ.get("DATABASE_URL", "")

def db():
    return psycopg2.connect(DB_URL, connect_timeout=10)

# ── Signal scoring (mirrors aiem_score_ticker logic) ────────────────────────

def score_candidate(row):
    """
    Returns (confidence 0-100, signal_list, tier_label).
    Uses the same signal weights as aiem_process.py's aiem_score_ticker().
    """
    gap       = float(row["gap_pct"]   or 0)
    rvol      = float(row["rvol"]      or 1)
    price     = float(row["open_price"]or 0)
    vol       = int(  row["volume"]    or 0)
    rng       = float(row["range_pct"] or 0)
    cs        = float(row["close_strength"] or 0.5)

    signals = []
    score   = 0.0

    # ── Gap strength ─────────────────────────────────────────────────────────
    if gap >= 15:
        score += 25; signals.append("gap_extreme")
    elif gap >= 8:
        score += 18; signals.append("gap_strong")
    elif gap >= 4:
        score += 12; signals.append("gap_moderate")
    elif gap >= 2:
        score += 6;  signals.append("gap_mild")

    # ── Relative volume ──────────────────────────────────────────────────────
    if rvol >= 5:
        score += 20; signals.append("rvol_extreme")
    elif rvol >= 3:
        score += 14; signals.append("rvol_high")
    elif rvol >= 2:
        score += 9;  signals.append("rvol_elevated")
    elif rvol >= 1.5:
        score += 4;  signals.append("rvol_above_avg")

    # ── Price zone (sweet spot $3–$12 for low-float gappers) ─────────────────
    if 3 <= price <= 12:
        score += 12; signals.append("price_sweet_spot")
    elif 1 <= price < 3:
        score += 6;  signals.append("price_sub3")
    elif 12 < price <= 20:
        score += 6;  signals.append("price_mid")

    # ── Volume confirmation ──────────────────────────────────────────────────
    if vol >= 2_000_000:
        score += 15; signals.append("vol_massive")
    elif vol >= 500_000:
        score += 10; signals.append("vol_high")
    elif vol >= 200_000:
        score += 6;  signals.append("vol_elevated")
    elif vol >= 50_000:
        score += 2;  signals.append("vol_min")

    # ── Range expansion ──────────────────────────────────────────────────────
    if rng >= 20:
        score += 10; signals.append("range_wide")
    elif rng >= 10:
        score += 6;  signals.append("range_moderate")

    # ── Close strength (did it hold gains through the day?) ──────────────────
    if cs >= 0.75:
        score += 8;  signals.append("close_strong")
    elif cs >= 0.5:
        score += 3;  signals.append("close_mid")
    elif cs < 0.25:
        score -= 5;  signals.append("close_weak")

    # Cap at 100
    confidence = min(100, max(0, round(score)))

    if confidence >= 72:
        tier = "HIGH (alert would fire)"
    elif confidence >= 55:
        tier = "MEDIUM"
    else:
        tier = "LOW"

    return confidence, signals, tier


def run_backtest():
    conn = db()
    cur  = conn.cursor()

    print("=" * 65)
    print("AIEM NANO-CAP BACKTEST — Last 13 Months")
    print("=" * 65)

    # ── Step 1: Pull all qualifying daily bars ────────────────────────────────
    print("\n[1/4] Pulling qualifying days from polygon_market_daily…")
    cur.execute("""
        SELECT
            d.scan_date,
            d.ticker,
            d.open_price,
            d.close_price,
            d.high_price,
            d.low_price,
            d.vwap,
            d.volume,
            d.gap_pct,
            d.rvol,
            d.close_strength,
            d.range_pct
        FROM polygon_market_daily d
        WHERE d.scan_date >= (CURRENT_DATE - INTERVAL '13 months')
          AND d.scan_date <  CURRENT_DATE          -- exclude today (no forward data)
          AND d.open_price  BETWEEN 1.0 AND 20.0   -- price filter
          AND d.gap_pct     > 2.0                  -- gap > 2%
          AND d.volume      > 50000                -- volume proxy
          AND d.rvol        > 1.5                  -- elevated premarket interest
        ORDER BY d.scan_date, d.ticker
    """)
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    print(f"    Qualifying bar-days found: {len(rows):,}")

    if not rows:
        print("No data — check polygon_market_daily population.")
        conn.close()
        return

    # ── Step 2: Build forward-return lookup ───────────────────────────────────
    print("[2/4] Building forward-return price map…")
    all_tickers = list({r["ticker"] for r in rows})
    all_dates   = list({r["scan_date"] for r in rows})
    min_date    = min(all_dates)
    max_date    = max(all_dates) + timedelta(days=10)

    # Load close prices for all relevant tickers (for forward return calc)
    cur.execute("""
        SELECT ticker, scan_date, close_price
        FROM polygon_market_daily
        WHERE scan_date BETWEEN %s AND %s
          AND ticker = ANY(%s)
        ORDER BY ticker, scan_date
    """, (min_date, max_date, all_tickers))
    price_rows = cur.fetchall()

    # Build: {ticker: [(date, close), ...]}
    ticker_prices = defaultdict(list)
    for ticker, d, close in price_rows:
        ticker_prices[ticker].append((d, float(close or 0)))

    # Sort each ticker's dates
    for t in ticker_prices:
        ticker_prices[t].sort(key=lambda x: x[0])

    def get_forward_close(ticker, entry_date, n_days):
        prices = ticker_prices.get(ticker, [])
        # Find index of entry_date
        for i, (d, _) in enumerate(prices):
            if d == entry_date:
                target_i = i + n_days
                if target_i < len(prices):
                    return prices[target_i][1]
                return None
        return None

    # ── Step 3: Score every candidate + compute returns ───────────────────────
    print("[3/4] Scoring candidates and computing forward returns…")

    results = []
    for row in rows:
        conf, signals, tier = score_candidate(row)
        entry_date = row["scan_date"]
        ticker     = row["ticker"]
        open_px    = float(row["open_price"] or 0)
        vwap_px    = float(row["vwap"]       or open_px)

        if open_px <= 0:
            continue

        # Forward closes
        t1_close = get_forward_close(ticker, entry_date, 1)
        t3_close = get_forward_close(ticker, entry_date, 3)
        t5_close = get_forward_close(ticker, entry_date, 5)

        def ret(entry, fwd):
            if fwd and entry and entry > 0:
                return round((fwd - entry) / entry * 100, 2)
            return None

        results.append({
            "date":       entry_date,
            "ticker":     ticker,
            "confidence": conf,
            "tier":       tier,
            "signals":    signals,
            "gap_pct":    float(row["gap_pct"] or 0),
            "rvol":       float(row["rvol"] or 0),
            "open_px":    open_px,
            "vwap_px":    vwap_px,
            # Entry A: open price (9:30 AM)
            "a_t1": ret(open_px, t1_close),
            "a_t3": ret(open_px, t3_close),
            "a_t5": ret(open_px, t5_close),
            # Entry B: vwap (mid-morning ~9:45-10:30)
            "b_t1": ret(vwap_px, t1_close),
            "b_t3": ret(vwap_px, t3_close),
            "b_t5": ret(vwap_px, t5_close),
        })

    print(f"    Scored candidates: {len(results):,}")

    # ── Step 4: Report ────────────────────────────────────────────────────────
    print("[4/4] Generating report…\n")

    def stats(rets):
        valid = [r for r in rets if r is not None]
        if not valid:
            return {"n": 0}
        wins  = [r for r in valid if r > 0]
        losses= [r for r in valid if r <= 0]
        avg_w = sum(wins) / len(wins) if wins else 0
        avg_l = sum(losses) / len(losses) if losses else 0
        ev    = (len(wins)/len(valid)) * avg_w + (1-len(wins)/len(valid)) * avg_l
        return {
            "n":       len(valid),
            "win_n":   len(wins),
            "wr":      round(len(wins)/len(valid)*100, 1),
            "avg_ret": round(sum(valid)/len(valid), 2),
            "avg_win": round(avg_w, 2),
            "avg_loss":round(avg_l, 2),
            "ev":      round(ev, 2),
            "best":    round(max(valid), 1),
            "worst":   round(min(valid), 1),
        }

    # ── Section A: Full universe (all qualifying bars) ────────────────────────
    print("─" * 65)
    print("FULL UNIVERSE  (all qualifying days, any confidence score)")
    print(f"  Total qualifying bar-days: {len(results):,}")
    print()

    for label, entry_key, t_keys in [
        ("ENTRY A — 9:30 Open Price", "open_px", ("a_t1","a_t3","a_t5")),
        ("ENTRY B — VWAP (mid-morning ~9:45-10:30)", "vwap_px", ("b_t1","b_t3","b_t5")),
    ]:
        print(f"  {label}")
        for horizon, key in zip(["T+1 (next day)", "T+3 (3 days)  ", "T+5 (5 days)  "], t_keys):
            s = stats([r[key] for r in results])
            if s["n"] > 0:
                print(f"    {horizon}  n={s['n']:5,}  WR={s['wr']:5.1f}%  "
                      f"AvgRet={s['avg_ret']:+6.2f}%  EV={s['ev']:+6.2f}%  "
                      f"AvgW={s['avg_win']:+5.1f}%  AvgL={s['avg_loss']:+5.1f}%")
        print()

    # ── Section B: HIGH confidence only (≥72, the alert-fire threshold) ───────
    high_conf = [r for r in results if r["confidence"] >= 72]
    print("─" * 65)
    print(f"HIGH CONFIDENCE ONLY  (score ≥ 72 — alert would fire)")
    print(f"  Days in this tier: {len(high_conf):,}  "
          f"({100*len(high_conf)/max(len(results),1):.1f}% of qualifying days)")
    print()

    for label, entry_key, t_keys in [
        ("ENTRY A — 9:30 Open Price", "open_px", ("a_t1","a_t3","a_t5")),
        ("ENTRY B — VWAP (~9:45-10:30)", "vwap_px", ("b_t1","b_t3","b_t5")),
    ]:
        print(f"  {label}")
        for horizon, key in zip(["T+1 (next day)", "T+3 (3 days)  ", "T+5 (5 days)  "], t_keys):
            s = stats([r[key] for r in high_conf])
            if s["n"] > 0:
                print(f"    {horizon}  n={s['n']:5,}  WR={s['wr']:5.1f}%  "
                      f"AvgRet={s['avg_ret']:+6.2f}%  EV={s['ev']:+6.2f}%  "
                      f"AvgW={s['avg_win']:+5.1f}%  AvgL={s['avg_loss']:+5.1f}%")
        print()

    # ── Section C: By confidence tier ────────────────────────────────────────
    print("─" * 65)
    print("WIN RATE BY CONFIDENCE TIER  (T+1, Entry A: open price)")
    tiers = [
        ("≥80 (very high)", [r for r in results if r["confidence"] >= 80]),
        ("72–79 (alert zone)", [r for r in results if 72 <= r["confidence"] < 80]),
        ("55–71 (medium)",  [r for r in results if 55 <= r["confidence"] < 72]),
        ("<55  (low)",      [r for r in results if r["confidence"] < 55]),
    ]
    for tier_name, tier_rows in tiers:
        s = stats([r["a_t1"] for r in tier_rows])
        if s["n"] > 0:
            print(f"  {tier_name:28s}  n={s['n']:5,}  WR={s['wr']:5.1f}%  "
                  f"AvgRet={s['avg_ret']:+5.1f}%  EV={s['ev']:+5.1f}%")

    # ── Section D: By gap tier ────────────────────────────────────────────────
    print()
    print("─" * 65)
    print("WIN RATE BY GAP SIZE  (T+1, Entry A: open price, HIGH conf only)")
    gap_tiers = [
        ("gap ≥ 25%", [r for r in high_conf if r["gap_pct"] >= 25]),
        ("gap 15–25%",[r for r in high_conf if 15 <= r["gap_pct"] < 25]),
        ("gap  8–15%",[r for r in high_conf if  8 <= r["gap_pct"] < 15]),
        ("gap  4–8% ",[r for r in high_conf if  4 <= r["gap_pct"] < 8]),
        ("gap  2–4% ",[r for r in high_conf if  2 <= r["gap_pct"] < 4]),
    ]
    for tier_name, tier_rows in gap_tiers:
        s = stats([r["a_t1"] for r in tier_rows])
        if s["n"] > 0:
            print(f"  {tier_name:18s}  n={s['n']:4,}  WR={s['wr']:5.1f}%  "
                  f"AvgRet={s['avg_ret']:+5.1f}%  Best={s['best']:+5.1f}%  Worst={s['worst']:+5.1f}%")
        else:
            print(f"  {tier_name:18s}  n=   0  (no data)")

    # ── Section E: Signal effectiveness ──────────────────────────────────────
    print()
    print("─" * 65)
    print("TOP SIGNALS BY WIN RATE  (T+1, Entry A, HIGH conf only, n≥20)")
    sig_stats = defaultdict(lambda: {"rets": []})
    for r in high_conf:
        for sig in r["signals"]:
            if r["a_t1"] is not None:
                sig_stats[sig]["rets"].append(r["a_t1"])

    sig_rows = []
    for sig, data in sig_stats.items():
        s = stats(data["rets"])
        if s["n"] >= 20:
            sig_rows.append((sig, s))
    sig_rows.sort(key=lambda x: x[1]["wr"], reverse=True)

    for sig, s in sig_rows:
        print(f"  {sig:25s}  n={s['n']:4,}  WR={s['wr']:5.1f}%  "
              f"AvgRet={s['avg_ret']:+5.1f}%  EV={s['ev']:+5.1f}%")

    # ── Section F: Entry A vs B comparison (headline) ─────────────────────────
    print()
    print("─" * 65)
    print("9:30 vs MID-MORNING ENTRY COMPARISON  (T+1, HIGH conf only)")
    for label, key in [("9:30 open (Entry A)", "a_t1"), ("VWAP mid-morning (Entry B)", "b_t1")]:
        s = stats([r[key] for r in high_conf])
        if s["n"] > 0:
            print(f"  {label:30s}  WR={s['wr']:5.1f}%  AvgRet={s['avg_ret']:+5.2f}%  EV={s['ev']:+5.2f}%")

    # ── Summary ───────────────────────────────────────────────────────────────
    print()
    print("=" * 65)
    print("NOTES")
    print("  • Volume filter uses daily volume (50K proxy for premarket vol)")
    print("  • Float filter NOT applied — float data only available for 151 tickers")
    print("  • Entry A = open price (best-case 9:30 fill)")
    print("  • Entry B = VWAP (mid-morning weighted average, approx 9:45-10:30)")
    print("  • T+1/T+3/T+5 = calendar days from next trading day's close")
    print("  • High confidence ≥72 = the threshold at which live alerts fire")
    print("=" * 65)

    conn.close()
    return results


if __name__ == "__main__":
    results = run_backtest()
