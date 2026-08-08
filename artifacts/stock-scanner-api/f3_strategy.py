"""
============================================================
  F3 SPY 0DTE STRATEGY — COMPLETE BACKTEST
============================================================

RULES (plain English):
  1. Before open: check if SPY premarket trended UP or DOWN
  2. 9:30–9:44 AM: record the Opening Range High and Low
  3. 9:45 AM onward: wait for SPY to break out in the
     SAME direction as premarket
  4. When breakout fires: buy the ATM call (UP) or put (DOWN)
     for $200
  5. Sell at exactly 4:00 PM — no stop loss, no profit target

REQUIREMENTS:
  pip install requests

API KEYS NEEDED:
  - Polygon.io  → set POLYGON_API_KEY below
  - Tradier     → set TRADIER_API_TOKEN below

============================================================
"""


from aiem_broker.tradier_config import TRADIER_API_BASE

import os
import time
import requests
from datetime import datetime, timedelta, date
from collections import defaultdict


# ============================================================
#  SETTINGS — edit these
# ============================================================

POLYGON_API_KEY  = os.environ.get("POLYGON_API_KEY", "YOUR_POLYGON_KEY_HERE")
TRADIER_API_TOKEN = os.environ.get("TRADIER_API_TOKEN_2") or \
                    os.environ.get("TRADIER_API_TOKEN", "YOUR_TRADIER_TOKEN_HERE")

TRADE_SIZE   = 200          # dollars per trade
BACKTEST_DAYS = 365         # how many calendar days to look back


# ============================================================
#  STEP 1 — FETCH DAILY SPY DATA (gap, open, close)
# ============================================================

def fetch_daily_data(start_date, end_date):
    """
    Gets SPY daily open/close from Tradier.
    We use this to know the official opening price each day.
    """
    print("[1] Fetching daily SPY data from Tradier...")

    headers = {
        "Authorization": f"Bearer {TRADIER_API_TOKEN}",
        "Accept": "application/json"
    }
    params = {
        "symbol":   "SPY",
        "interval": "daily",
        "start":    start_date.strftime("%Y-%m-%d"),
        "end":      end_date.strftime("%Y-%m-%d"),
    }
    response = requests.get(
        f"{TRADIER_API_BASE}/v1/markets/history",
        headers=headers,
        params=params,
        timeout=20
    )
    days = response.json().get("history", {}).get("day", [])
    if not isinstance(days, list):
        days = [days]

    # Build a lookup: date string → { open, close, prev_close }
    days_sorted = sorted(days, key=lambda x: x["date"])
    daily_map = {}
    for i, day in enumerate(days_sorted):
        prev_close = float(days_sorted[i-1]["close"]) if i > 0 else None
        daily_map[day["date"]] = {
            "open":       float(day["open"]),
            "close":      float(day["close"]),
            "prev_close": prev_close,
        }

    print(f"    → {len(daily_map)} trading days loaded")
    return daily_map


# ============================================================
#  STEP 2 — FETCH 5-MINUTE BAR DATA (intraday)
# ============================================================

def fetch_intraday_bars(start_date, end_date):
    """
    Gets SPY 5-minute bars from Polygon for the full date range.
    Handles pagination automatically if data exceeds 50,000 bars.

    Premarket bars  = 4:00 AM – 9:29 AM  (used for direction)
    Regular bars    = 9:30 AM – 4:00 PM  (used for ORB and entry)
    """
    print("[2] Fetching 5-minute SPY bars from Polygon...")

    url = (
        f"https://api.polygon.io/v2/aggs/ticker/SPY/range/5/minute/"
        f"{start_date.strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}"
    )
    params = {
        "adjusted": "true",
        "sort":     "asc",
        "limit":    50000,
        "apiKey":   POLYGON_API_KEY,
    }
    response = requests.get(url, params=params, timeout=60)
    data     = response.json()
    all_bars = data.get("results") or []
    print(f"    → Chunk 1: {len(all_bars)} bars  [{data.get('status','?')}]")

    # Polygon paginates — keep fetching if there is a next page
    while data.get("next_url"):
        time.sleep(3)
        response = requests.get(
            data["next_url"] + f"&apiKey={POLYGON_API_KEY}",
            timeout=60
        )
        data      = response.json()
        more_bars = data.get("results") or []
        all_bars.extend(more_bars)
        print(f"    → +{len(more_bars)} bars  total={len(all_bars)}")

    print(f"    → {len(all_bars)} total bars fetched")
    return all_bars


# ============================================================
#  STEP 3 — SORT BARS BY DAY
#           Separate premarket bars from regular-hours bars
# ============================================================

def organize_bars_by_day(raw_bars):
    """
    Converts Polygon timestamps (milliseconds UTC) to Eastern Time,
    then sorts each bar into:
      - regular_bars[date] = bars from 9:30 AM to 4:00 PM ET
      - premarket_bars[date] = bars from 4:00 AM to 9:29 AM ET
    """
    print("[3] Organizing bars by trading day...")

    regular_bars  = defaultdict(list)
    premarket_bars = defaultdict(list)

    for bar in raw_bars:
        try:
            # Convert UTC millisecond timestamp to Eastern Time
            # Nov–Mar = UTC-5 (EST), Apr–Oct = UTC-4 (EDT)
            utc_dt    = datetime.utcfromtimestamp(int(bar["t"]) / 1000)
            is_winter = utc_dt.month in (11, 12, 1, 2, 3)
            et_dt     = utc_dt - timedelta(hours=5 if is_winter else 4)

            date_str  = et_dt.strftime("%Y-%m-%d")
            minute    = et_dt.hour * 60 + et_dt.minute   # minutes since midnight

            bar_data  = {
                "time_str": et_dt.strftime("%H:%M"),
                "minute":   minute,
                "open":     float(bar["o"]),
                "high":     float(bar["h"]),
                "low":      float(bar["l"]),
                "close":    float(bar["c"]),
                "volume":   float(bar.get("v", 0)),
            }

            MARKET_OPEN  = 9 * 60 + 30   # 9:30 AM
            MARKET_CLOSE = 16 * 60        # 4:00 PM
            PREMARKET_START = 4 * 60      # 4:00 AM

            if MARKET_OPEN <= minute < MARKET_CLOSE:
                regular_bars[date_str].append(bar_data)
            elif PREMARKET_START <= minute < MARKET_OPEN:
                premarket_bars[date_str].append(bar_data)

        except Exception:
            continue

    # Sort each day's bars by time
    for d in regular_bars:   regular_bars[d].sort(key=lambda x: x["minute"])
    for d in premarket_bars: premarket_bars[d].sort(key=lambda x: x["minute"])

    days_with_data = len(regular_bars)
    print(f"    → {days_with_data} trading days with intraday data")
    return regular_bars, premarket_bars


# ============================================================
#  STEP 4 — F3 SIGNAL LOGIC
#           Run through each trading day and find the entry
# ============================================================

def run_f3_backtest(daily_map, regular_bars, premarket_bars, trade_size):
    """
    For each trading day:

      F3 RULE:
        - Determine premarket direction (UP or DOWN)
        - Find the Opening Range High and Low (9:30–9:44 AM)
        - Wait for price to break out in the premarket direction
        - If breakout confirmed: enter the trade
        - Exit at 4:00 PM close, whatever the price is

    Returns a list of trade records.
    """
    print("[4] Running F3 backtest...")

    ORB_END_MINUTE = 9 * 60 + 45   # Opening Range ends at 9:45 AM

    trades = []

    for date_str in sorted(daily_map.keys()):
        daily       = daily_map[date_str]
        reg_bars    = regular_bars.get(date_str, [])
        pm_bars     = premarket_bars.get(date_str, [])

        # Skip days with no intraday data or no previous close
        if not reg_bars or not daily["prev_close"] or len(reg_bars) < 10:
            continue

        # ── PREMARKET DIRECTION ─────────────────────────────────────
        # Compare first premarket bar (open) to last premarket bar (close)
        # UP   = premarket trended higher → look for a CALL
        # DOWN = premarket trended lower  → look for a PUT
        if pm_bars:
            pm_open  = pm_bars[0]["open"]
            pm_close = pm_bars[-1]["close"]
            premarket_direction = 1 if pm_close > pm_open else -1
        else:
            # No premarket data — skip this day
            continue

        # ── OPENING RANGE (9:30–9:44 AM) ───────────────────────────
        orb_bars = [b for b in reg_bars if b["minute"] < ORB_END_MINUTE]
        if not orb_bars:
            continue

        orb_high = max(b["high"]  for b in orb_bars)
        orb_low  = min(b["low"]   for b in orb_bars)

        # Bars available after the opening range closes
        post_orb_bars = [b for b in reg_bars if b["minute"] >= ORB_END_MINUTE]
        if not post_orb_bars:
            continue

        # ── BREAKOUT DETECTION ──────────────────────────────────────
        # Scan post-ORB bars for the FIRST breakout in premarket direction
        entry_bar   = None
        entry_index = None

        for i, bar in enumerate(post_orb_bars):
            if premarket_direction == 1 and bar["close"] > orb_high:
                # Price broke ABOVE the opening range → CALL signal
                entry_bar   = bar
                entry_index = i
                break
            elif premarket_direction == -1 and bar["close"] < orb_low:
                # Price broke BELOW the opening range → PUT signal
                entry_bar   = bar
                entry_index = i
                break

        # No breakout today — skip (no trade)
        if entry_bar is None:
            continue

        # ── F3 FILTER ───────────────────────────────────────────────
        # Only take the trade if the breakout direction matches premarket
        # (This is already guaranteed by the logic above, but stated clearly)
        signal_direction = premarket_direction   # 1 = call, -1 = put

        # ── OPTION PRICE ESTIMATE ───────────────────────────────────
        # Estimate the ATM 0DTE premium at entry
        # We use half the ORB range as a proxy for the option premium
        spy_price = entry_bar["close"]
        orb_range = orb_high - orb_low
        atm_premium_est = max(orb_range / 2.0, spy_price * 0.0015)

        # Leverage: how much the option moves per 1% move in SPY
        # ATM 0DTE delta ≈ 0.50, so option moves ~$0.50 per $1 SPY move
        # Leverage = (0.50 × SPY price) / option premium
        leverage = min(max((0.50 * spy_price) / atm_premium_est, 50.0), 250.0)

        # ── HOLD TO EOD ─────────────────────────────────────────────
        # No stop loss. No profit target.
        # Sell at whatever price the last bar of the day closes at.
        final_bar  = post_orb_bars[-1]
        spy_move_pct = (final_bar["close"] - spy_price) / spy_price * 100
        option_return_pct = spy_move_pct * signal_direction * leverage

        # Floor at -100% (option can't lose more than you paid)
        # Cap at 2000% (10x, extremely generous ceiling for monster days)
        option_return_pct = min(max(option_return_pct, -100.0), 2000.0)

        dollar_pnl = trade_size * (option_return_pct / 100)

        # ── RECORD THE TRADE ────────────────────────────────────────
        trades.append({
            "date":              date_str,
            "direction":         "CALL" if signal_direction == 1 else "PUT",
            "entry_time":        entry_bar["time_str"],
            "spy_entry":         round(spy_price, 2),
            "orb_high":          round(orb_high, 2),
            "orb_low":           round(orb_low, 2),
            "spy_exit":          round(final_bar["close"], 2),
            "option_return_pct": round(option_return_pct, 1),
            "dollar_pnl":        round(dollar_pnl, 2),
            "premium_spent":     trade_size,
            "win":               option_return_pct > 0,
        })

    print(f"    → {len(trades)} trades generated")
    return trades


# ============================================================
#  STEP 5 — PRINT RESULTS
# ============================================================

def print_results(trades, trade_size, days_with_data):
    if not trades:
        print("No trades found.")
        return

    total_pnl      = sum(t["dollar_pnl"]    for t in trades)
    total_spent    = sum(t["premium_spent"]  for t in trades)
    wins           = [t for t in trades if t["win"]]
    losses         = [t for t in trades if not t["win"]]
    win_returns    = [t["option_return_pct"] for t in wins]
    loss_returns   = [t["option_return_pct"] for t in losses]
    best_trade     = max(trades, key=lambda x: x["option_return_pct"])
    worst_trade    = min(trades, key=lambda x: x["option_return_pct"])

    avg_win  = sum(win_returns)  / len(wins)   if wins   else 0
    avg_loss = sum(loss_returns) / len(losses) if losses else 0
    pf       = abs(sum(win_returns) / sum(loss_returns)) if sum(loss_returns) != 0 else 99.0

    print()
    print("=" * 60)
    print("  F3 STRATEGY RESULTS — 12 MONTHS")
    print("  No stop loss | No profit target | Sell at 4PM")
    print("  $200 per trade")
    print("=" * 60)
    print()
    print(f"  TRADE SUMMARY")
    print(f"  ─────────────────────────────────")
    print(f"  Trading days in dataset   : {days_with_data}")
    print(f"  Total trades taken        : {len(trades)}")
    print(f"  Trades per week (avg)     : {len(trades) / 52:.1f}")
    print(f"  Calls taken               : {sum(1 for t in trades if t['direction']=='CALL')}")
    print(f"  Puts taken                : {sum(1 for t in trades if t['direction']=='PUT')}")
    print()
    print(f"  PERFORMANCE")
    print(f"  ─────────────────────────────────")
    print(f"  Win rate                  : {len(wins)/len(trades)*100:.1f}%")
    print(f"  Avg winning trade         : +{avg_win:.1f}%  (${avg_win/100*trade_size:+.2f})")
    print(f"  Avg losing trade          : {avg_loss:.1f}%  (${avg_loss/100*trade_size:+.2f})")
    print(f"  Profit factor             : {pf:.2f}")
    print()
    print(f"  MONEY")
    print(f"  ─────────────────────────────────")
    print(f"  Total premiums spent      : ${total_spent:,.0f}")
    print(f"  Total profit              : ${total_pnl:+,.2f}")
    print(f"  Cash-on-cash return       : {total_pnl/total_spent*100:+.1f}%")
    print(f"  Avg profit per trade      : ${total_pnl/len(trades):+.2f}")
    print(f"  Avg profit per month      : ${total_pnl/12:+.2f}")
    print()
    print(f"  BEST & WORST TRADES")
    print(f"  ─────────────────────────────────")
    print(f"  Best  : {best_trade['date']}  {best_trade['direction']:<4}  "
          f"{best_trade['option_return_pct']:>+6.0f}%  "
          f"${best_trade['dollar_pnl']:>+8.2f}")
    print(f"  Worst : {worst_trade['date']}  {worst_trade['direction']:<4}  "
          f"{worst_trade['option_return_pct']:>+6.0f}%  "
          f"${worst_trade['dollar_pnl']:>+8.2f}")
    print()

    # Top 10 trades
    print(f"  TOP 10 TRADES")
    print(f"  ─────────────────────────────────")
    print(f"  {'Date':<12} {'Dir':<5} {'Entry':>6}  {'Return':>7}  {'P&L on $200':>12}")
    print(f"  {'─'*12} {'─'*5} {'─'*6}  {'─'*7}  {'─'*12}")
    top10 = sorted(trades, key=lambda x: x["option_return_pct"], reverse=True)[:10]
    for t in top10:
        print(f"  {t['date']:<12} {t['direction']:<5} {t['entry_time']:>6}  "
              f"{t['option_return_pct']:>+6.0f}%  ${t['dollar_pnl']:>+10.2f}")

    # Month-by-month breakdown
    print()
    print(f"  MONTH BY MONTH")
    print(f"  ─────────────────────────────────")
    print(f"  {'Month':<8} {'Trades':>6}  {'Won':>4}  {'WR%':>5}  {'P&L':>10}")
    print(f"  {'─'*8} {'─'*6}  {'─'*4}  {'─'*5}  {'─'*10}")
    by_month = defaultdict(list)
    for t in trades:
        by_month[t["date"][:7]].append(t)
    for month in sorted(by_month):
        mt      = by_month[month]
        m_pnl   = sum(t["dollar_pnl"] for t in mt)
        m_wins  = sum(1 for t in mt if t["win"])
        m_wr    = m_wins / len(mt) * 100
        print(f"  {month:<8} {len(mt):>6}  {m_wins:>4}  {m_wr:>4.0f}%  ${m_pnl:>+8.2f}")

    print()
    print("=" * 60)
    print("  END OF REPORT")
    print("=" * 60)


# ============================================================
#  MAIN — runs everything in order
# ============================================================

if __name__ == "__main__":

    end_date   = date.today()
    start_date = end_date - timedelta(days=BACKTEST_DAYS)

    print()
    print("=" * 60)
    print("  F3 SPY 0DTE BACKTEST")
    print(f"  {start_date}  →  {end_date}")
    print(f"  ${TRADE_SIZE} per trade | No stop | No target | Sell at 4PM")
    print("=" * 60)
    print()

    # Step 1 — daily data
    daily_map = fetch_daily_data(start_date, end_date)

    # Step 2 — intraday bars
    raw_bars = fetch_intraday_bars(start_date, end_date)

    # Step 3 — organize by day
    regular_bars, premarket_bars = organize_bars_by_day(raw_bars)

    days_with_data = len(regular_bars)

    # Step 4 — run the strategy
    trades = run_f3_backtest(daily_map, regular_bars, premarket_bars, TRADE_SIZE)

    # Step 5 — print results
    print_results(trades, TRADE_SIZE, days_with_data)
