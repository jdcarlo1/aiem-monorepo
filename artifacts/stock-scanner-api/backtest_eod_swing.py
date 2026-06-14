"""
EOD Swing Backtest — Jun 1–5 + Jun 9–13, 2026 (10 trading days)
─────────────────────────────────────────────────────────────────
Replicates the live eod_swing.py scoring logic on historical data.

Signal day: scanner fires at 3:30 PM, entry = signal day's close price.
Universe: same large/mid-cap tickers as morning burst backtest (since Barchart
top-movers API is live-only; we filter by "up ≥2% on signal day" to mimic it).

Exit scenarios tested:
  • Next-day OPEN   (overnight gap — quickest exit, take if gap ≥ stop)
  • Next-day CLOSE  (hold through full next session)
  • Day+3 CLOSE     (hold 3 sessions — full swing trade)

Side-by-side vs morning burst + grinder from the two-week backtest.
"""
import yfinance as yf
import pandas as pd
import warnings, statistics
from datetime import date, timedelta
warnings.filterwarnings("ignore")

# ── Universe ────────────────────────────────────────────────────────────────
# Same as morning burst backtest — large/mid cap with avg vol ≥500k
UNIVERSE = [
    "AAPL","MSFT","NVDA","AMD","AVGO","QCOM","TXN","MU","AMAT","KLAC",
    "LRCX","ON","MRVL","INTC","SMCI","AMKR","ONTO",
    "JPM","GS","MS","BAC","AXP","V","MA","BLK",
    "XOM","CVX","COP","OXY","FRO","SLB","HAL","GE","HON","CAT",
    "JNJ","LLY","UNH","ABBV","MRK","PFE","AMGN","GILD",
    "AMZN","META","GOOGL","NFLX","TSLA","HD","COST","NKE",
    "ANET","DECK","AXON","CELH","CRWD","FTNT","LULU","MELI","MPWR","NET","PANW",
]

# All 10 trading days in both weeks
WEEK_DATES = [date(2026, 6, d) for d in [1, 2, 3, 4, 5, 9, 10, 11, 12, 13]]
WEEK1 = {date(2026, 6, d) for d in [1, 2, 3, 4, 5]}
WEEK2 = {date(2026, 6, d) for d in [9, 10, 11, 12, 13]}
ET    = "America/New_York"

# Scoring thresholds (from eod_swing.py)
SCORE_THRESHOLD  = 60
MIN_CHG_PCT      = 2.0   # must be up ≥2% today (mimics Barchart universe filter)
MIN_MOMENTUM_3D  = 3.0   # mandatory gate
MIN_CLOSE_RNG    = 60.0  # mandatory gate (% of day's range)


def scalar(v):
    if hasattr(v, "iloc"): v = v.iloc[0]
    return float(v)


def get_col(df, metric, ticker):
    try:
        if isinstance(df.columns, pd.MultiIndex):
            s = df[ticker][metric]
        else:
            s = df[metric]
        return s.dropna()
    except Exception:
        return pd.Series(dtype=float)


print("\n" + "="*72)
print("  EOD SWING BACKTEST  —  Jun 1–5 + Jun 9–13, 2026  (10 trading days)")
print("  Entry = signal-day CLOSE  |  Universe = large/mid-cap up ≥2% today")
print("="*72)

print("\nFetching daily data (May 1 – Jun 18)…")
daily_all = yf.download(
    UNIVERSE, start="2026-05-01", end="2026-06-18",
    interval="1d", group_by="ticker", auto_adjust=True, progress=False
)
print("Data ready.\n")


signals = []   # all qualifying signals with outcomes

for dt in WEEK_DATES:
    # We need signal-day data + 3 sessions ahead
    dt_ts = pd.Timestamp(dt)

    print(f"── {dt.strftime('%a %b %d')}")

    for tkr in UNIVERSE:
        try:
            dc = get_col(daily_all, "Close",  tkr)
            dh = get_col(daily_all, "High",   tkr)
            dl = get_col(daily_all, "Low",    tkr)
            dv = get_col(daily_all, "Volume", tkr)
            do = get_col(daily_all, "Open",   tkr)

            # Localize index if needed
            for s in [dc, dh, dl, dv, do]:
                if s.index.tzinfo is not None:
                    s.index = s.index.tz_localize(None)

            # Find signal day in data
            sig_mask = dc.index == dt_ts
            if not sig_mask.any():
                continue

            sig_idx = dc.index.get_loc(dt_ts)
            if sig_idx < 21:
                continue

            # Today's data
            close    = scalar(dc.iloc[sig_idx])
            high_d   = scalar(dh.iloc[sig_idx])
            low_d    = scalar(dl.iloc[sig_idx])
            vol_d    = scalar(dv.iloc[sig_idx])
            prev_close = scalar(dc.iloc[sig_idx - 1])

            if prev_close <= 0 or close < 2.0:
                continue

            today_chg = (close - prev_close) / prev_close * 100
            if today_chg < MIN_CHG_PCT:
                continue

            # avg vol (20 prior days)
            avg_vol20 = float(dv.iloc[sig_idx-20:sig_idx].mean())
            if avg_vol20 < 200_000:
                continue

            # Close position in range
            rng = high_d - low_d
            close_pct_range = (close - low_d) / rng * 100 if rng > 0 else 50.0
            if close_pct_range < MIN_CLOSE_RNG:
                continue

            # RVOL today
            rvol_today = vol_d / avg_vol20

            # Best RVOL in last 3 prior days
            best_prior_rvol = 0.0
            for i in range(1, 4):
                if sig_idx - i >= 0:
                    rv = scalar(dv.iloc[sig_idx-i]) / avg_vol20
                    best_prior_rvol = max(best_prior_rvol, rv)
            best_rvol = max(rvol_today, best_prior_rvol)

            # 3-day momentum (close 3 sessions ago → today)
            three_day_ago_close = scalar(dc.iloc[sig_idx - 3])
            momentum_3d = (close - three_day_ago_close) / three_day_ago_close * 100
            if momentum_3d < MIN_MOMENTUM_3D:
                continue

            # Pullback quality (max down day in last 2 prior sessions)
            max_down_day = 0.0
            for i in range(1, 3):
                if sig_idx - i >= 1:
                    day_chg = (scalar(dc.iloc[sig_idx-i]) - scalar(dc.iloc[sig_idx-i-1])) \
                              / scalar(dc.iloc[sig_idx-i-1]) * 100
                    if day_chg < 0:
                        max_down_day = max(max_down_day, abs(day_chg))

            # 20-day MA
            ma20 = float(dc.iloc[sig_idx-20:sig_idx].mean())
            above_ma20 = close > ma20

            # ── Scoring (replicating eod_swing.py logic) ──────────────────
            score    = 0
            sig_list = []

            # 1. Close position in range (25 pts)
            if   close_pct_range >= 90: score += 25; sig_list.append(f"Closed {close_pct_range:.0f}% of range 🔒")
            elif close_pct_range >= 80: score += 20; sig_list.append(f"Closed {close_pct_range:.0f}% of range")
            elif close_pct_range >= 70: score += 12; sig_list.append(f"Closed {close_pct_range:.0f}% of range")
            elif close_pct_range >= 60: score += 6

            # 2. Best RVOL (25 pts)
            if   best_rvol >= 3.0: score += 25; sig_list.append(f"Peak RVOL {best_rvol:.1f}x 🔥")
            elif best_rvol >= 2.0: score += 18; sig_list.append(f"Peak RVOL {best_rvol:.1f}x")
            elif best_rvol >= 1.5: score += 10; sig_list.append(f"RVOL {best_rvol:.1f}x")

            # 3. 3-day momentum (20 pts)
            if   momentum_3d >= 20: score += 20; sig_list.append(f"+{momentum_3d:.1f}% in 3d 🚀")
            elif momentum_3d >= 15: score += 16; sig_list.append(f"+{momentum_3d:.1f}% in 3d")
            elif momentum_3d >= 10: score += 12; sig_list.append(f"+{momentum_3d:.1f}% in 3d")
            elif momentum_3d >= 7:  score += 8;  sig_list.append(f"+{momentum_3d:.1f}% in 3d")
            elif momentum_3d >= 5:  score += 5;  sig_list.append(f"+{momentum_3d:.1f}% in 3d")
            elif momentum_3d >= 3:  score += 3

            # 4. Pullback quality (15 pts)
            if   max_down_day == 0:   score += 15; sig_list.append("All green ✅")
            elif max_down_day < 2.0:  score += 12; sig_list.append(f"Tight pullback {max_down_day:.1f}%")
            elif max_down_day < 4.0:  score += 8
            elif max_down_day < 6.0:  score += 4

            # 5. PCR — skip historical fetch (no live options chain), treat as neutral
            #    This slightly understates real scores but keeps it objective

            # 6. Above 20-day MA (5 pts)
            if above_ma20:
                score += 5; sig_list.append(f"Above 20d MA")

            if score < SCORE_THRESHOLD:
                continue

            # ── Exit prices ────────────────────────────────────────────────
            total_days = len(dc)

            # Next-day open (D+1)
            nd_open = None
            nd_close = None
            d3_close = None

            if sig_idx + 1 < total_days:
                nd_open  = scalar(do.iloc[sig_idx + 1])
                nd_close = scalar(dc.iloc[sig_idx + 1])
            if sig_idx + 3 < total_days:
                d3_close = scalar(dc.iloc[sig_idx + 3])

            # 3-day stop: min low of signal day + 2 prior days
            stop = min(low_d,
                       scalar(dl.iloc[sig_idx-1]) if sig_idx >= 1 else low_d,
                       scalar(dl.iloc[sig_idx-2]) if sig_idx >= 2 else low_d)

            gap_pct    = (nd_open  - close) / close * 100 if nd_open  else None
            nd_close_p = (nd_close - close) / close * 100 if nd_close else None
            d3_pct     = (d3_close - close) / close * 100 if d3_close else None

            label_str = "🔥🔥" if score >= 85 else "🔥" if score >= 75 else "📈"

            win_nd = "✅" if (nd_close_p or -99) > 0 else "❌"
            if gap_pct is not None and nd_close_p is not None and d3_pct is not None:
                print(f"  {label_str} {tkr:5s}  score={score:3d}  +{today_chg:.1f}%today  "
                      f"3d={momentum_3d:+.1f}%  RVOL {best_rvol:.1f}x  rng={close_pct_range:.0f}%  "
                      f"gap={gap_pct:+.2f}%  D1={nd_close_p:+.2f}% {win_nd}  D3={d3_pct:+.2f}%")
            else:
                print(f"  {label_str} {tkr:5s}  score={score:3d}  +{today_chg:.1f}%today  "
                      f"3d={momentum_3d:+.1f}%  RVOL {best_rvol:.1f}x  (incomplete future data)")

            signals.append({
                "ticker":         tkr,
                "date":           dt,
                "score":          score,
                "today_chg":      round(today_chg, 2),
                "momentum_3d":    round(momentum_3d, 2),
                "close_pct_range":round(close_pct_range, 1),
                "best_rvol":      round(best_rvol, 2),
                "entry_p":        round(close, 2),
                "stop":           round(stop, 2),
                "gap_pct":        round(gap_pct, 2) if gap_pct is not None else None,
                "nd_close_pct":   round(nd_close_p, 2) if nd_close_p is not None else None,
                "d3_close_pct":   round(d3_pct, 2) if d3_pct is not None else None,
                "above_ma20":     above_ma20,
                "week":           1 if dt in WEEK1 else 2,
            })

        except Exception:
            continue

    print()


# ── Stats helper ─────────────────────────────────────────────────────────────
def stats(group, key):
    valid = [s for s in group if s.get(key) is not None]
    if not valid:
        return 0, 0, 0, 0, 0
    wins   = [s for s in valid if s[key] > 0]
    losses = [s for s in valid if s[key] <= 0]
    wr     = len(wins) / len(valid) * 100
    avg_w  = statistics.mean(s[key] for s in wins)  if wins   else 0.0
    avg_l  = statistics.mean(s[key] for s in losses) if losses else 0.0
    ev     = wr/100 * avg_w + (1 - wr/100) * avg_l
    return len(valid), wr, avg_w, avg_l, ev


def row(label, group, key):
    n, wr, avg_w, avg_l, ev = stats(group, key)
    print(f"  {label:<28} n={n:3d}  WR={wr:5.1f}%  "
          f"win={avg_w:+.2f}%  loss={avg_l:+.2f}%  EV={ev:+.3f}%/trade")


print("\n" + "="*72)
print("  EOD SWING RESULTS — 10 TRADING DAYS")
print("="*72)
print("\n  ── All signals ──")
row("Next-day OPEN  (gap)",       signals, "gap_pct")
row("Next-day CLOSE (D+1 hold)",  signals, "nd_close_pct")
row("Day+3  CLOSE  (full swing)", signals, "d3_close_pct")

w1 = [s for s in signals if s["week"] == 1]
w2 = [s for s in signals if s["week"] == 2]
print(f"\n  ── Week 1 (Jun 1–5) — {len(w1)} signals ──")
row("D+1 close", w1, "nd_close_pct")
row("D+3 close", w1, "d3_close_pct")
print(f"\n  ── Week 2 (Jun 9–13) — {len(w2)} signals ──")
row("D+1 close", w2, "nd_close_pct")
row("D+3 close", w2, "d3_close_pct")

# Score tier breakdown
high   = [s for s in signals if s["score"] >= 75]
mid    = [s for s in signals if 60 <= s["score"] < 75]
print(f"\n  ── By score tier ──")
row(f"High (score 75+, n={len(high)})",  high, "d3_close_pct")
row(f"Mid  (score 60-74, n={len(mid)})", mid,  "d3_close_pct")


# ── Head-to-head comparison ───────────────────────────────────────────────────
print("\n" + "="*72)
print("  HEAD-TO-HEAD: All 3 Scanners — Jun 1–13, 2026")
print("="*72)
print("""
  MORNING BURST (9:35-9:45 AM) — from prior backtest (10 trading days)
    n=40  WR=62%  win=+2.43%  loss=-3.45%  EV=+0.23%/trade  [same-day exit]
    With VWAP gate: n=30  WR=67%  win=+2.65%  loss=-2.79%  EV=+0.83%/trade

  STEADY GRINDER (10:30 AM) — from prior backtest (10 trading days)
    n=28  WR=50%  win=+1.50%  loss=-1.65%  EV=-0.08%/trade  [same-day exit]
""")
print("  EOD SWING — this backtest:")
n_d1, wr_d1, aw_d1, al_d1, ev_d1 = stats(signals, "nd_close_pct")
n_d3, wr_d3, aw_d3, al_d3, ev_d3 = stats(signals, "d3_close_pct")
print(f"    D+1 close:  n={n_d1}  WR={wr_d1:.0f}%  win={aw_d1:+.2f}%  "
      f"loss={al_d1:+.2f}%  EV={ev_d1:+.3f}%/trade")
print(f"    D+3 close:  n={n_d3}  WR={wr_d3:.0f}%  win={aw_d3:+.2f}%  "
      f"loss={al_d3:+.2f}%  EV={ev_d3:+.3f}%/trade")

# Gap stats
n_g, wr_g, aw_g, al_g, ev_g = stats(signals, "gap_pct")
print(f"    Next-day gap: n={n_g}  WR={wr_g:.0f}%  avg gap={aw_g:+.2f}% win / {al_g:+.2f}% loss")

print("\n" + "="*72)
print("  VERDICT")
print("="*72)
print(f"""
  ┌──────────────────────────────────────────────────────────────┐
  │ Scanner         │ Hold     │ WR   │ Avg Win │ Avg Loss │ EV  │
  ├──────────────────────────────────────────────────────────────│
  │ Morning Burst   │ same-day │  62% │  +2.43% │   -3.45% │+0.23│
  │ Morning (VWAP)  │ same-day │  67% │  +2.65% │   -2.79% │+0.83│
  │ Grinder         │ same-day │  50% │  +1.50% │   -1.65% │-0.08│
  │ EOD Swing D+1   │ overnight│ {wr_d1:3.0f}% │  {aw_d1:+.2f}% │   {al_d1:+.2f}% │{ev_d1:+.2f}│
  │ EOD Swing D+3   │ 3 days  │ {wr_d3:3.0f}% │  {aw_d3:+.2f}% │   {al_d3:+.2f}% │{ev_d3:+.2f}│
  └──────────────────────────────────────────────────────────────┘
""")

# Best and worst signals
valid_d3 = [s for s in signals if s.get("d3_close_pct") is not None]
valid_d3.sort(key=lambda x: x["d3_close_pct"], reverse=True)
if valid_d3:
    top_n = min(5, len(valid_d3))
    print(f"  Top {top_n} winners (D+3):")
    for s in valid_d3[:top_n]:
        print(f"    {s['ticker']:5s} {s['date']}  score={s['score']}  "
              f"+{s['today_chg']:.1f}%today  D+3={s['d3_close_pct']:+.2f}%")
    print(f"\n  Top {top_n} losers (D+3):")
    for s in valid_d3[-top_n:]:
        print(f"    {s['ticker']:5s} {s['date']}  score={s['score']}  "
              f"+{s['today_chg']:.1f}%today  D+3={s['d3_close_pct']:+.2f}%")
else:
    print("  No signals with complete D+3 data.")
print()

# All signals summary
if signals:
    print("  All signals fired (including incomplete future data):")
    for s in sorted(signals, key=lambda x: (x["date"], x["score"]), reverse=True):
        nd  = f"D1={s['nd_close_pct']:+.2f}%" if s.get("nd_close_pct") is not None else "D1=?"
        d3  = f"D3={s['d3_close_pct']:+.2f}%" if s.get("d3_close_pct") is not None else "D3=?"
        print(f"    {s['ticker']:5s} {s['date']}  score={s['score']:3d}  "
              f"+{s['today_chg']:.1f}%today  3d={s['momentum_3d']:+.1f}%  {nd}  {d3}")
print()
