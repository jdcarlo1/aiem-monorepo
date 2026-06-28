"""
Post-signal performance for the 5 grinder hits found last week.
Entry = 11:30 AM price.  Exit = 3:45 PM price.
"""
import yfinance as yf
import pandas as pd
import warnings
warnings.filterwarnings("ignore")

# Correct dates from backtest output:
# Wed Jun 10 → CVX, Thu Jun 11 → AMGN, Fri Jun 12 → AMKR / INTC / GOOGL
SIGNALS = [
    ("CVX",   "2026-06-10"),
    ("AMGN",  "2026-06-11"),
    ("AMKR",  "2026-06-12"),
    ("INTC",  "2026-06-12"),
    ("GOOGL", "2026-06-12"),
]

ET = "America/New_York"

def scalar(val):
    """Safely convert a pandas scalar or 1-element Series to float."""
    if hasattr(val, "iloc"):
        val = val.iloc[0]
    return float(val)

print(f"\n{'='*65}")
print(f"  GRINDER BACKTEST — Post-Signal Performance")
print(f"  Entry: 11:30 AM price  →  Exit: 3:45 PM price")
print(f"{'='*65}\n")

results = []
for ticker, dt in SIGNALS:
    try:
        from datetime import date, timedelta
        dt_obj = date.fromisoformat(dt)
        raw = yf.download(
            ticker,
            start=str(dt_obj),
            end=str(dt_obj + timedelta(days=1)),
            interval="1m",
            auto_adjust=False,
            progress=False,
        )
        if raw.empty:
            print(f"  {ticker} {dt}: no data returned")
            continue

        # Flatten MultiIndex columns if present (yfinance v0.2+)
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)

        raw.index = raw.index.tz_convert(ET)

        snap_ts = pd.Timestamp(f"{dt} 10:30:00").tz_localize(ET)
        eod_ts  = pd.Timestamp(f"{dt} 15:45:00").tz_localize(ET)

        snap_bar = raw[raw.index <= snap_ts]
        eod_bar  = raw[raw.index <= eod_ts]

        if snap_bar.empty or eod_bar.empty:
            print(f"  {ticker} {dt}: not enough bars")
            continue

        entry = scalar(snap_bar["Close"].iloc[-1])
        exit_ = scalar(eod_bar["Close"].iloc[-1])
        move  = (exit_ - entry) / entry * 100
        win   = "✅" if move > 0 else "❌"

        print(
            f"  {win} {ticker:6s} {dt}  "
            f"entry ${entry:.2f}  →  exit ${exit_:.2f}  "
            f"({'+'if move>=0 else ''}{move:.2f}% from signal)"
        )
        results.append((ticker, move))
    except Exception as e:
        print(f"  {ticker} {dt}: error — {e}")

print()
if results:
    import statistics
    moves  = [x[1] for x in results]
    wins   = [x for x in moves if x > 0]
    losses = [x for x in moves if x <= 0]
    print(f"  Signals:   {len(moves)}")
    print(f"  Win rate:  {len(wins)}/{len(moves)} = {len(wins)/len(moves)*100:.0f}%")
    print(f"  Avg move:  {statistics.mean(moves):+.2f}%")
    if wins:   print(f"  Avg win:   {statistics.mean(wins):+.2f}%")
    if losses: print(f"  Avg loss:  {statistics.mean(losses):+.2f}%")
print(f"{'='*65}\n")
