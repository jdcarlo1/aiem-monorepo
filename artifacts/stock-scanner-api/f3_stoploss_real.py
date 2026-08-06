"""
f3_stoploss_real.py
Directive_F3Backtest_StopLoss_RealBars_2026-08-06

Tests stop losses of 30%, 35%, 40%, 45%, 50% against real Polygon
1-min option bars for every F3 trade.

Stop logic:
  For each bar AFTER entry, if bar low <= entry_px * (1 - stop_pct):
      exit at stop price = entry_px * (1 - stop_pct)
      dollar_pnl = -stop_pct * 200  (exact, because sizing = 200/(entry*100))
  If stop never hit: exit at EOD bar close (same as no-stop backtest)

Compares 6 scenarios side by side:
  No stop | 30% | 35% | 40% | 45% | 50%
"""

import os, time, requests
from datetime import datetime, timedelta, date
from collections import defaultdict

POLYGON_API_KEY = os.environ.get("POLYGON_API_KEY", "")
TRADIER_TOKEN   = (os.environ.get("TRADIER_API_TOKEN_2") or
                   os.environ.get("TRADIER_API_TOKEN", ""))
TRADE_SIZE      = 200
BACKTEST_DAYS   = 365
API_DELAY       = 0.5
STOP_LEVELS     = [0.30, 0.35, 0.40, 0.45, 0.50]


# ── helpers ───────────────────────────────────────────────────────────────────

def _et_minute(ts_ms):
    utc_dt    = datetime.utcfromtimestamp(int(ts_ms) / 1000)
    is_winter = utc_dt.month in (11, 12, 1, 2, 3)
    et_dt     = utc_dt - timedelta(hours=5 if is_winter else 4)
    return et_dt.hour * 60 + et_dt.minute


def get_atm_ticker(spot, exp_date, is_call):
    s  = f"{int(round(spot) * 1000):08d}"
    cp = "C" if is_call else "P"
    return f"O:SPY{exp_date.strftime('%y%m%d')}{cp}{s}"


# ── data fetch ────────────────────────────────────────────────────────────────

def fetch_daily(start_date, end_date):
    print("[1] Fetching SPY daily data...")
    h = {"Authorization": f"Bearer {TRADIER_TOKEN}", "Accept": "application/json"}
    p = {"symbol": "SPY", "interval": "daily",
         "start": start_date.strftime("%Y-%m-%d"),
         "end":   end_date.strftime("%Y-%m-%d")}
    r    = requests.get("https://api.tradier.com/v1/markets/history",
                        headers=h, params=p, timeout=20)
    days = r.json().get("history", {}).get("day", [])
    if not isinstance(days, list): days = [days]
    days = sorted(days, key=lambda x: x["date"])
    dm   = {}
    for i, d in enumerate(days):
        pc = float(days[i-1]["close"]) if i > 0 else None
        dm[d["date"]] = {"open": float(d["open"]), "close": float(d["close"]),
                         "prev_close": pc}
    print(f"    → {len(dm)} days")
    return dm


def fetch_intraday(start_date, end_date):
    print("[2] Fetching 5-min SPY bars from Polygon...")
    url = (f"https://api.polygon.io/v2/aggs/ticker/SPY/range/5/minute/"
           f"{start_date.strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}")
    p    = {"adjusted": "true", "sort": "asc", "limit": 50000,
            "apiKey": POLYGON_API_KEY}
    r    = requests.get(url, params=p, timeout=60)
    data = r.json()
    bars = data.get("results") or []
    print(f"    chunk 1: {len(bars)}")
    while data.get("next_url"):
        time.sleep(3)
        r    = requests.get(data["next_url"] + f"&apiKey={POLYGON_API_KEY}", timeout=60)
        data = r.json()
        more = data.get("results") or []
        bars.extend(more)
        print(f"    +{len(more)}  total={len(bars)}")
    print(f"    → {len(bars)} bars")
    return bars


def organize(raw_bars):
    print("[3] Organizing by day...")
    reg = defaultdict(list); pm = defaultdict(list)
    for b in raw_bars:
        try:
            utc_dt    = datetime.utcfromtimestamp(int(b["t"]) / 1000)
            is_winter = utc_dt.month in (11, 12, 1, 2, 3)
            et_dt     = utc_dt - timedelta(hours=5 if is_winter else 4)
            ds = et_dt.strftime("%Y-%m-%d")
            mn = et_dt.hour * 60 + et_dt.minute
            bd = {"minute": mn, "time_str": et_dt.strftime("%H:%M"),
                  "open": float(b["o"]), "high": float(b["h"]),
                  "low":  float(b["l"]), "close": float(b["c"])}
            if 570 <= mn < 960:   reg[ds].append(bd)
            elif 240 <= mn < 570: pm[ds].append(bd)
        except: continue
    for d in reg: reg[d].sort(key=lambda x: x["minute"])
    for d in pm:  pm[d].sort(key=lambda x: x["minute"])
    print(f"    → {len(reg)} regular-hours days")
    return reg, pm


def fetch_option_bars(ticker, date_str):
    url = (f"https://api.polygon.io/v2/aggs/ticker/{ticker}"
           f"/range/1/minute/{date_str}/{date_str}")
    p   = {"adjusted": "true", "sort": "asc", "limit": 5000,
           "apiKey": POLYGON_API_KEY}
    try:
        r   = requests.get(url, params=p, timeout=30)
        raw = r.json()
    except Exception as e:
        raw = {"error": str(e)}
    return raw.get("results") or []


# ── stop loss simulator ───────────────────────────────────────────────────────

def apply_stop(day_bars, entry_minute, entry_px, stop_pct):
    """
    Scan bars AFTER entry minute. Return (exit_px, stopped, exit_minute).
    stopped=True  → stop was hit; exit_px = entry_px * (1 - stop_pct)
    stopped=False → held to EOD; exit_px = last bar close
    """
    stop_price = entry_px * (1.0 - stop_pct)
    post = [b for b in day_bars if _et_minute(b["t"]) > entry_minute]
    if not post:
        return entry_px, False, entry_minute  # no bars after entry

    for bar in post:
        if float(bar["l"]) <= stop_price:
            return stop_price, True, _et_minute(bar["t"])

    # stop never hit — EOD exit
    eod_bar = min(post, key=lambda b: abs(_et_minute(b["t"]) - 960))
    return float(eod_bar["c"]), False, _et_minute(eod_bar["t"])


def dollar_pnl(entry_px, exit_px):
    contracts = TRADE_SIZE / (entry_px * 100)
    return round((exit_px - entry_px) * 100 * contracts, 2)


# ── main ──────────────────────────────────────────────────────────────────────

def run(daily_map, regular_bars, premarket_bars):
    print(f"[4] Running F3 with real option bars + stop loss sweep...")
    results  = []
    skipped  = []

    for date_str in sorted(daily_map.keys()):
        daily  = daily_map[date_str]
        reg_bs = regular_bars.get(date_str, [])
        pm_bs  = premarket_bars.get(date_str, [])

        if not reg_bs or not daily["prev_close"] or len(reg_bs) < 10:
            continue

        if pm_bs:
            pm_dir = 1 if pm_bs[-1]["close"] > pm_bs[0]["open"] else -1
        else:
            skipped.append({"date": date_str, "reason": "no_pm"}); continue

        orb_bs = [b for b in reg_bs if b["minute"] < 585]
        if not orb_bs: continue
        orb_high = max(b["high"] for b in orb_bs)
        orb_low  = min(b["low"]  for b in orb_bs)
        post_orb = [b for b in reg_bs if b["minute"] >= 585]
        if not post_orb: continue

        entry_bar = None
        for bar in post_orb:
            if pm_dir == 1  and bar["close"] > orb_high: entry_bar = bar; break
            if pm_dir == -1 and bar["close"] < orb_low:  entry_bar = bar; break
        if entry_bar is None: continue

        spy_price    = entry_bar["close"]
        is_call      = (pm_dir == 1)
        trade_date   = datetime.strptime(date_str, "%Y-%m-%d").date()
        opt_ticker   = get_atm_ticker(spy_price, trade_date, is_call)
        entry_minute = entry_bar["minute"]

        # fetch 1-min option bars once
        day_bars = fetch_option_bars(opt_ticker, date_str)
        time.sleep(API_DELAY)

        if not day_bars:
            skipped.append({"date": date_str, "reason": "no_option_bars"}); continue

        # find entry price
        entry_bar_opt = min(day_bars, key=lambda b: abs(_et_minute(b["t"]) - entry_minute))
        entry_px      = float(entry_bar_opt["c"])
        if entry_px <= 0:
            skipped.append({"date": date_str, "reason": "zero_entry"}); continue

        # no-stop exit (EOD)
        post_opt = [b for b in day_bars if _et_minute(b["t"]) > entry_minute]
        if post_opt:
            eod_bar = min(post_opt, key=lambda b: abs(_et_minute(b["t"]) - 960))
            nostop_exit = float(eod_bar["c"])
        else:
            nostop_exit = entry_px
        nostop_pnl = dollar_pnl(entry_px, nostop_exit)

        # apply each stop level
        stop_pnls = {}
        stop_hits = {}
        for sl in STOP_LEVELS:
            exit_px, stopped, _ = apply_stop(day_bars, entry_minute, entry_px, sl)
            stop_pnls[sl] = dollar_pnl(entry_px, exit_px)
            stop_hits[sl] = stopped

        results.append({
            "date":       date_str,
            "direction":  "CALL" if is_call else "PUT",
            "entry_px":   round(entry_px, 4),
            "nostop_exit":round(nostop_exit, 4),
            "nostop_pnl": nostop_pnl,
            "stop_pnls":  stop_pnls,
            "stop_hits":  stop_hits,
        })

        n_done = len(results) + len(skipped)
        if n_done % 20 == 0:
            print(f"    {n_done} processed  ({len(results)} trades  {len(skipped)} skipped)")

    print(f"    → {len(results)} trades | {len(skipped)} skipped")
    return results, skipped


# ── report ────────────────────────────────────────────────────────────────────

def print_report(results):
    if not results:
        print("No results."); return

    n         = len(results)
    scenarios = [None] + STOP_LEVELS   # None = no stop

    def total_pnl(sl):
        if sl is None:
            return sum(r["nostop_pnl"] for r in results)
        return sum(r["stop_pnls"][sl] for r in results)

    def wins(sl):
        if sl is None:
            return sum(1 for r in results if r["nostop_pnl"] > 0)
        return sum(1 for r in results if r["stop_pnls"][sl] > 0)

    def stops_triggered(sl):
        if sl is None: return 0
        return sum(1 for r in results if r["stop_hits"][sl])

    def avg_loss(sl):
        if sl is None:
            losers = [r["nostop_pnl"] for r in results if r["nostop_pnl"] < 0]
        else:
            losers = [r["stop_pnls"][sl] for r in results if r["stop_pnls"][sl] < 0]
        return sum(losers) / len(losers) if losers else 0

    def avg_win(sl):
        if sl is None:
            winners = [r["nostop_pnl"] for r in results if r["nostop_pnl"] > 0]
        else:
            winners = [r["stop_pnls"][sl] for r in results if r["stop_pnls"][sl] > 0]
        return sum(winners) / len(winners) if winners else 0

    def stopped_that_recovered(sl):
        """Trades where stop fired but nostop_pnl would have been positive."""
        return sum(1 for r in results
                   if r["stop_hits"][sl] and r["nostop_pnl"] > 0)

    def saved_on_real_losers(sl):
        """Extra $ saved vs no-stop on trades that were losers either way."""
        return sum(
            r["stop_pnls"][sl] - r["nostop_pnl"]
            for r in results
            if r["stop_hits"][sl] and r["nostop_pnl"] < 0
        )

    def cost_of_false_stops(sl):
        """$ forfeited on trades stop fired but nostop would have been positive."""
        return sum(
            r["stop_pnls"][sl] - r["nostop_pnl"]
            for r in results
            if r["stop_hits"][sl] and r["nostop_pnl"] > 0
        )

    # ── main comparison table ─────────────────────────────────────────────────
    print()
    print("=" * 80)
    print("  F3 STOP LOSS SWEEP — REAL POLYGON 1-MIN OPTION BARS")
    print(f"  {n} trades | $200/trade normalised | Aug 2025 – Aug 2026")
    print("=" * 80)
    print()

    labels = ["No stop", "Stop 30%", "Stop 35%", "Stop 40%", "Stop 45%", "Stop 50%"]
    col_w  = 13

    def row(label, fn):
        vals = "".join(f"{fn(sl):>{col_w}}" for sl in scenarios)
        print(f"  {label:<28}{vals}")

    header = "".join(f"{l:>{col_w}}" for l in labels)
    print(f"  {'':28}{header}")
    print("  " + "─" * (28 + col_w * 6))

    row("Total P&L ($)",
        lambda sl: f"${total_pnl(sl):+,.2f}")
    row("Cash-on-cash (%)",
        lambda sl: f"{total_pnl(sl)/(TRADE_SIZE*n)*100:+.1f}%")
    row("Win rate (%)",
        lambda sl: f"{wins(sl)/n*100:.1f}%")
    row("Avg winning trade ($)",
        lambda sl: f"${avg_win(sl):+.2f}")
    row("Avg losing trade ($)",
        lambda sl: f"${avg_loss(sl):+.2f}")
    row("Stops triggered",
        lambda sl: f"{stops_triggered(sl)}" if sl else "—")
    row("  → saved on real losers ($)",
        lambda sl: f"${saved_on_real_losers(sl):+.2f}" if sl else "—")
    row("  → cost: stopped winners ($)",
        lambda sl: f"${cost_of_false_stops(sl):+.2f}" if sl else "—")
    row("  → net stop effect ($)",
        lambda sl: f"${total_pnl(sl)-total_pnl(None):+.2f}" if sl else "—")
    row("Stopped that recovered",
        lambda sl: f"{stopped_that_recovered(sl)}" if sl else "—")

    print()
    print("  Note: 'stopped that recovered' = stop fired, but holding to EOD")
    print("  would have been profitable. These are the false-stop false negatives.")

    # ── per-trade detail for any stop that improves total ─────────────────────
    best_sl   = max(STOP_LEVELS, key=lambda sl: total_pnl(sl))
    best_total = total_pnl(best_sl)
    nostop_tot = total_pnl(None)

    print()
    print("=" * 80)
    print(f"  BEST STOP LEVEL: {int(best_sl*100)}%  "
          f"(total ${best_total:+,.2f} vs no-stop ${nostop_tot:+,.2f}  "
          f"net ${best_total-nostop_tot:+,.2f})")
    print("=" * 80)

    # trades where stop changed the outcome most (largest abs delta)
    best_deltas = sorted(
        results,
        key=lambda r: abs(r["stop_pnls"][best_sl] - r["nostop_pnl"]),
        reverse=True
    )[:20]

    print()
    print(f"  Top 20 trades where {int(best_sl*100)}% stop had the biggest impact:")
    print(f"  {'Date':<12} {'Dir':<5} {'EntPx':>7} {'EOD exit':>9} "
          f"{'NoStop$':>9} {'Stop$':>9} {'Delta':>9} {'Stopped?':>9}")
    print("  " + "─" * 77)
    for r in best_deltas:
        stopped_str = f"YES ({int(best_sl*100)}%)" if r["stop_hits"][best_sl] else "no"
        delta = r["stop_pnls"][best_sl] - r["nostop_pnl"]
        print(f"  {r['date']:<12} {r['direction']:<5} "
              f"${r['entry_px']:>6.3f} ${r['nostop_exit']:>8.3f} "
              f"${r['nostop_pnl']:>+8.2f} ${r['stop_pnls'][best_sl]:>+8.2f} "
              f"${delta:>+8.2f} {stopped_str:>9}")

    # ── full trade-by-trade for all 6 scenarios ───────────────────────────────
    print()
    print("=" * 80)
    print("  FULL PER-TRADE TABLE — all 6 scenarios")
    print("=" * 80)
    print(f"  {'Date':<12} {'Dir':<5} {'EntPx':>7}  "
          f"{'NoStop':>8} {'SL30':>8} {'SL35':>8} "
          f"{'SL40':>8} {'SL45':>8} {'SL50':>8}")
    print("  " + "─" * 82)
    for r in sorted(results, key=lambda x: x["date"]):
        stop_cols = " ".join(f"${r['stop_pnls'][sl]:>+7.2f}" for sl in STOP_LEVELS)
        print(f"  {r['date']:<12} {r['direction']:<5} "
              f"${r['entry_px']:>6.3f}  "
              f"${r['nostop_pnl']:>+7.2f} {stop_cols}")

    # ── totals row ────────────────────────────────────────────────────────────
    print("  " + "─" * 82)
    tot_stop_cols = " ".join(
        f"${sum(r['stop_pnls'][sl] for r in results):>+7.2f}"
        for sl in STOP_LEVELS
    )
    print(f"  {'TOTAL':<12} {'':5} {'':7}  "
          f"${total_pnl(None):>+7.2f} {tot_stop_cols}")


# ── entry ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    end_date   = date.today()
    start_date = end_date - timedelta(days=BACKTEST_DAYS)

    print()
    print("=" * 72)
    print("  F3 STOP LOSS SWEEP — REAL POLYGON 1-MIN OPTION BARS")
    print(f"  {start_date} → {end_date}")
    print(f"  Stop levels tested: {[f'{int(s*100)}%' for s in STOP_LEVELS]}")
    print("=" * 72)

    dm       = fetch_daily(start_date, end_date)
    raw      = fetch_intraday(start_date, end_date)
    reg, pm  = organize(raw)
    results, skipped = run(dm, reg, pm)
    print_report(results)
