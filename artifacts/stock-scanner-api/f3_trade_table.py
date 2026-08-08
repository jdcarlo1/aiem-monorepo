"""
f3_trade_table.py
Directive_F3Backtest_RealOptionPricing_TradeLevelTable_2026-08-06

For each of the 178 F3 trades, compute BOTH the synthetic dollar P&L
(original f3_strategy.py formula) AND the real dollar P&L (Polygon 1-min
option bars) side by side, then answer the four directive questions.

Synthetic formula (verbatim from f3_strategy.py):
    atm_premium_est = max(orb_range / 2.0, spy_price * 0.0015)
    leverage        = clip((0.50 * spy_price) / atm_premium_est, 50, 250)
    option_ret_pct  = spy_move_pct * signal_dir * leverage
    option_ret_pct  = clip(option_ret_pct, -100, 2000)
    synth_dollar    = 200 * (option_ret_pct / 100)

Real formula (f3_real_options.py):
    contracts       = 200 / (entry_premium * 100)
    real_dollar     = (exit_premium - entry_premium) * 100 * contracts
                    = (exit_premium - entry_premium) / entry_premium * 200

delta = real_dollar - synth_dollar
"""


from aiem_broker.tradier_config import TRADIER_API_BASE

import os, time, json, requests, csv, io
from datetime import datetime, timedelta, date
from collections import defaultdict

POLYGON_API_KEY   = os.environ.get("POLYGON_API_KEY", "")
TRADIER_TOKEN     = (os.environ.get("TRADIER_API_TOKEN_2") or
                     os.environ.get("TRADIER_API_TOKEN", ""))
TRADE_SIZE        = 200
BACKTEST_DAYS     = 365
API_DELAY         = 0.5


# ── time helpers ──────────────────────────────────────────────────────────────

def _et_minute(ts_ms):
    utc_dt    = datetime.utcfromtimestamp(int(ts_ms) / 1000)
    is_winter = utc_dt.month in (11, 12, 1, 2, 3)
    et_dt     = utc_dt - timedelta(hours=5 if is_winter else 4)
    return et_dt.hour * 60 + et_dt.minute


# ── option ticker ─────────────────────────────────────────────────────────────

def get_atm_ticker(spot_price, exp_date, is_call):
    strike = round(spot_price)
    s_str  = f"{int(strike * 1000):08d}"
    cp     = "C" if is_call else "P"
    return f"O:SPY{exp_date.strftime('%y%m%d')}{cp}{s_str}"


# ── data fetch (identical to f3_real_options.py) ─────────────────────────────

def fetch_daily_data(start_date, end_date):
    print("[1] Fetching SPY daily data from Tradier...")
    h = {"Authorization": f"Bearer {TRADIER_TOKEN}", "Accept": "application/json"}
    p = {"symbol": "SPY", "interval": "daily",
         "start": start_date.strftime("%Y-%m-%d"),
         "end":   end_date.strftime("%Y-%m-%d")}
    r    = requests.get(f"{TRADIER_API_BASE}/v1/markets/history",
                        headers=h, params=p, timeout=20)
    days = r.json().get("history", {}).get("day", [])
    if not isinstance(days, list): days = [days]
    days = sorted(days, key=lambda x: x["date"])
    dm   = {}
    for i, d in enumerate(days):
        pc = float(days[i-1]["close"]) if i > 0 else None
        dm[d["date"]] = {"open": float(d["open"]),
                         "close": float(d["close"]), "prev_close": pc}
    print(f"    → {len(dm)} days")
    return dm


def fetch_intraday_bars(start_date, end_date):
    print("[2] Fetching 5-min SPY bars from Polygon...")
    url = (f"https://api.polygon.io/v2/aggs/ticker/SPY/range/5/minute/"
           f"{start_date.strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}")
    p   = {"adjusted": "true", "sort": "asc", "limit": 50000,
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
    print(f"    → {len(bars)} total bars")
    return bars


def organize_bars(raw_bars):
    print("[3] Organizing bars by day...")
    reg = defaultdict(list); pm = defaultdict(list)
    for b in raw_bars:
        try:
            utc_dt    = datetime.utcfromtimestamp(int(b["t"]) / 1000)
            is_winter = utc_dt.month in (11, 12, 1, 2, 3)
            et_dt     = utc_dt - timedelta(hours=5 if is_winter else 4)
            ds = et_dt.strftime("%Y-%m-%d")
            mn = et_dt.hour * 60 + et_dt.minute
            bd = {"time_str": et_dt.strftime("%H:%M"), "minute": mn,
                  "open":  float(b["o"]), "high": float(b["h"]),
                  "low":   float(b["l"]), "close": float(b["c"])}
            if 570 <= mn < 960:   reg[ds].append(bd)
            elif 240 <= mn < 570: pm[ds].append(bd)
        except: continue
    for d in reg: reg[d].sort(key=lambda x: x["minute"])
    for d in pm:  pm[d].sort(key=lambda x: x["minute"])
    print(f"    → {len(reg)} days with regular-hours data")
    return reg, pm


def fetch_option_bars(opt_ticker, date_str):
    url = (f"https://api.polygon.io/v2/aggs/ticker/{opt_ticker}"
           f"/range/1/minute/{date_str}/{date_str}")
    p   = {"adjusted": "true", "sort": "asc", "limit": 5000,
           "apiKey": POLYGON_API_KEY}
    try:
        r   = requests.get(url, params=p, timeout=30)
        raw = r.json()
    except Exception as e:
        raw = {"error": str(e)}
    return raw.get("results") or [], raw


# ── main loop ─────────────────────────────────────────────────────────────────

def run(daily_map, regular_bars, premarket_bars):
    print(f"[4] Running F3 — computing synthetic + real P&L per trade...")
    trades   = []
    skipped  = []
    n_cands  = 0

    for date_str in sorted(daily_map.keys()):
        daily   = daily_map[date_str]
        reg_bs  = regular_bars.get(date_str, [])
        pm_bs   = premarket_bars.get(date_str, [])

        if not reg_bs or not daily["prev_close"] or len(reg_bs) < 10:
            continue
        n_cands += 1

        # premarket direction
        if pm_bs:
            pm_dir = 1 if pm_bs[-1]["close"] > pm_bs[0]["open"] else -1
        else:
            skipped.append({"date": date_str, "reason": "no_premarket"}); continue

        # opening range 9:30–9:44
        orb_bs = [b for b in reg_bs if b["minute"] < 585]
        if not orb_bs:
            skipped.append({"date": date_str, "reason": "no_orb"}); continue
        orb_high = max(b["high"] for b in orb_bs)
        orb_low  = min(b["low"]  for b in orb_bs)
        post_orb = [b for b in reg_bs if b["minute"] >= 585]
        if not post_orb: continue

        # breakout detection
        entry_bar = None
        for i, bar in enumerate(post_orb):
            if pm_dir == 1  and bar["close"] > orb_high: entry_bar = bar; break
            if pm_dir == -1 and bar["close"] < orb_low:  entry_bar = bar; break
        if entry_bar is None: continue

        spy_price    = entry_bar["close"]
        is_call      = (pm_dir == 1)
        trade_date   = datetime.strptime(date_str, "%Y-%m-%d").date()
        opt_ticker   = get_atm_ticker(spy_price, trade_date, is_call)
        entry_minute = entry_bar["minute"]
        final_bar    = post_orb[-1]

        # ── SYNTHETIC P&L (f3_strategy.py verbatim) ──────────────────
        orb_range       = orb_high - orb_low
        atm_premium_est = max(orb_range / 2.0, spy_price * 0.0015)
        leverage        = min(max((0.50 * spy_price) / atm_premium_est, 50.0), 250.0)
        spy_move_pct    = (final_bar["close"] - spy_price) / spy_price * 100
        synth_ret_pct   = spy_move_pct * pm_dir * leverage
        synth_ret_pct   = min(max(synth_ret_pct, -100.0), 2000.0)
        synth_dollar    = round(TRADE_SIZE * (synth_ret_pct / 100), 2)

        # ── REAL P&L (Polygon 1-min option bars) ─────────────────────
        day_bars, raw_json = fetch_option_bars(opt_ticker, date_str)
        time.sleep(API_DELAY)

        if not day_bars:
            skipped.append({"date": date_str, "ticker": opt_ticker,
                             "reason": "no_option_bars",
                             "status": raw_json.get("status",""),
                             "msg":    raw_json.get("message","")[:80]})
            continue

        entry_bar_opt = min(day_bars, key=lambda b: abs(_et_minute(b["t"]) - entry_minute))
        exit_bar_opt  = min(day_bars, key=lambda b: abs(_et_minute(b["t"]) - 960))
        entry_px      = float(entry_bar_opt["c"])
        exit_px       = float(exit_bar_opt["c"])

        if entry_px <= 0:
            skipped.append({"date": date_str, "reason": "zero_entry_px"}); continue

        contracts  = TRADE_SIZE / (entry_px * 100)
        real_dollar = round((exit_px - entry_px) * 100 * contracts, 2)
        delta       = round(real_dollar - synth_dollar, 2)

        trades.append({
            "date":          date_str,
            "direction":     "CALL" if is_call else "PUT",
            "entry_premium": round(entry_px, 4),
            "exit_premium":  round(exit_px, 4),
            "contracts":     round(contracts, 4),
            "synth_dollar":  synth_dollar,
            "real_dollar":   real_dollar,
            "delta":         delta,
            # extras for Q3 raw evidence
            "opt_ticker":    opt_ticker,
            "entry_minute":  entry_minute,
            "spy_entry":     round(spy_price, 2),
            "spy_exit":      round(final_bar["close"], 2),
            "entry_bar_opt": entry_bar_opt,
            "exit_bar_opt":  exit_bar_opt,
            "n_bars":        len(day_bars),
        })

        n_done = len(trades) + len(skipped)
        if n_done % 20 == 0:
            print(f"    {n_done} processed  ({len(trades)} trades  {len(skipped)} skipped)")

    print(f"    → {len(trades)} real trades | {len(skipped)} skipped")
    return trades, skipped


# ── report ────────────────────────────────────────────────────────────────────

def print_report(trades, skipped):

    if not trades:
        print("No trades found.")
        return

    # sort by delta descending
    by_delta = sorted(trades, key=lambda x: x["delta"], reverse=True)

    total_synth = sum(t["synth_dollar"] for t in trades)
    total_real  = sum(t["real_dollar"]  for t in trades)
    total_delta = sum(t["delta"]        for t in trades)

    # ── Q1: count real > synth vs real < synth ────────────────────────────────
    real_better  = sum(1 for t in trades if t["real_dollar"] > t["synth_dollar"])
    synth_better = sum(1 for t in trades if t["real_dollar"] < t["synth_dollar"])
    equal        = sum(1 for t in trades if t["real_dollar"] == t["synth_dollar"])

    # ── full table (sorted by delta desc) ────────────────────────────────────
    print()
    print("=" * 100)
    print("  FULL 178-TRADE TABLE  —  sorted by delta (real − synthetic) descending")
    print("=" * 100)
    hdr = (f"  {'Date':<12} {'Dir':<5} {'EntPx':>7} {'ExPx':>7} "
           f"{'Cntr':>6} {'Synth$':>9} {'Real$':>9} {'Delta':>9}")
    sep = "  " + "─"*95
    print(hdr)
    print(sep)
    for t in by_delta:
        print(f"  {t['date']:<12} {t['direction']:<5} "
              f"${t['entry_premium']:>6.3f} ${t['exit_premium']:>6.3f} "
              f"{t['contracts']:>6.3f} "
              f"${t['synth_dollar']:>+8.2f} ${t['real_dollar']:>+8.2f} "
              f"${t['delta']:>+8.2f}")
    print(sep)
    print(f"  {'TOTAL':<12} {'':5} {'':7} {'':7} {'':6} "
          f"${total_synth:>+8.2f} ${total_real:>+8.2f} ${total_delta:>+8.2f}")

    # ── Q1 answer ─────────────────────────────────────────────────────────────
    print()
    print("=" * 80)
    print("  QUESTION 1 — Real vs Synthetic: which direction?")
    print("=" * 80)
    print(f"  real_dollar > synth_dollar  : {real_better:>3} trades  "
          f"({real_better/len(trades)*100:.1f}%)")
    print(f"  real_dollar < synth_dollar  : {synth_better:>3} trades  "
          f"({synth_better/len(trades)*100:.1f}%)")
    print(f"  equal                       : {equal:>3} trades")

    # ── Q2: top-10 by delta ───────────────────────────────────────────────────
    print()
    print("=" * 80)
    print("  QUESTION 2 — Top 10 trades by largest positive delta (real > synthetic)")
    print("=" * 80)
    top10 = by_delta[:10]
    top10_delta_sum = sum(t["delta"] for t in top10)
    print(f"  Top-10 combined delta : ${top10_delta_sum:+,.2f}  "
          f"(of ${total_delta:+,.2f} total = {top10_delta_sum/total_delta*100:.1f}%)")
    print()
    print(f"  {'Date':<12} {'Dir':<5} {'EntPx':>7} {'ExPx':>7} "
          f"{'Cntr':>6} {'Synth$':>9} {'Real$':>9} {'Delta':>9}")
    print("  " + "─"*80)
    for t in top10:
        print(f"  {t['date']:<12} {t['direction']:<5} "
              f"${t['entry_premium']:>6.3f} ${t['exit_premium']:>6.3f} "
              f"{t['contracts']:>6.3f} "
              f"${t['synth_dollar']:>+8.2f} ${t['real_dollar']:>+8.2f} "
              f"${t['delta']:>+8.2f}")

    # ── Q3: raw entry/exit bars for the single biggest-delta trade ────────────
    big = by_delta[0]
    print()
    print("=" * 80)
    print("  QUESTION 3 — Raw entry/exit option bars for largest-delta trade")
    print("=" * 80)
    print(f"  Date      : {big['date']}")
    print(f"  Direction : {big['direction']}")
    print(f"  Ticker    : {big['opt_ticker']}")
    print(f"  SPY entry : {big['spy_entry']}  →  SPY exit : {big['spy_exit']}")
    print(f"  Entry minute (ET): {big['entry_minute']} "
          f"= {big['entry_minute']//60}:{big['entry_minute']%60:02d}")
    print(f"  Total 1-min bars returned: {big['n_bars']}")
    print()
    eb = big["entry_bar_opt"]
    xb = big["exit_bar_opt"]
    print("  ENTRY BAR (raw Polygon 1-min):")
    print(f"    t_ms  = {eb['t']}")
    print(f"    ET min= {_et_minute(eb['t'])} = "
          f"{_et_minute(eb['t'])//60}:{_et_minute(eb['t'])%60:02d}")
    print(f"    open  = {eb['o']}")
    print(f"    high  = {eb['h']}")
    print(f"    low   = {eb['l']}")
    print(f"    close = {eb['c']}   ← entry_premium used")
    print(f"    vwap  = {eb.get('vw','n/a')}")
    print(f"    vol   = {eb['v']}")
    print(f"    n_tx  = {eb.get('n','n/a')}")
    print()
    print("  EXIT BAR (raw Polygon 1-min, nearest to 16:00 ET):")
    print(f"    t_ms  = {xb['t']}")
    print(f"    ET min= {_et_minute(xb['t'])} = "
          f"{_et_minute(xb['t'])//60}:{_et_minute(xb['t'])%60:02d}")
    print(f"    open  = {xb['o']}")
    print(f"    high  = {xb['h']}")
    print(f"    low   = {xb['l']}")
    print(f"    close = {xb['c']}   ← exit_premium used")
    print(f"    vwap  = {xb.get('vw','n/a')}")
    print(f"    vol   = {xb['v']}")
    print(f"    n_tx  = {xb.get('n','n/a')}")
    print()
    print(f"  P&L check:")
    print(f"    contracts          = 200 / ({big['entry_premium']} × 100) "
          f"= {big['contracts']:.4f}")
    print(f"    real_dollar        = ({big['exit_premium']} - {big['entry_premium']})"
          f" × 100 × {big['contracts']:.4f} = ${big['real_dollar']:+.2f}")
    print(f"    synth_dollar       = ${big['synth_dollar']:+.2f}")
    print(f"    delta              = ${big['delta']:+.2f}")

    # ── Q4: totals excluding the largest-delta trade ──────────────────────────
    print()
    print("=" * 80)
    print("  QUESTION 4 — Remove single largest-delta trade, recompute totals")
    print("=" * 80)
    rest = [t for t in trades if t["date"] != big["date"] or
            t["direction"] != big["direction"]]
    synth_ex  = sum(t["synth_dollar"] for t in rest)
    real_ex   = sum(t["real_dollar"]  for t in rest)
    delta_ex  = real_ex - synth_ex
    n_ex      = len(rest)
    print(f"  Largest-delta trade removed: {big['date']}  {big['direction']}"
          f"  delta=${big['delta']:+.2f}")
    print()
    print(f"  {'':30} {'With outlier':>15} {'Without outlier':>16}")
    print(f"  {'':30} {'─'*15} {'─'*16}")
    print(f"  {'N trades':<30} {len(trades):>15} {n_ex:>16}")
    print(f"  {'Synthetic total':<30} ${total_synth:>+14,.2f} ${synth_ex:>+15,.2f}")
    print(f"  {'Real total':<30} ${total_real:>+14,.2f} ${real_ex:>+15,.2f}")
    print(f"  {'Delta (real − synthetic)':<30} ${total_delta:>+14,.2f} ${delta_ex:>+15,.2f}")
    print()
    if delta_ex > 0:
        print(f"  CONCLUSION: Even without the outlier, real > synthetic "
              f"by ${delta_ex:+,.2f}.  The edge holds.")
    else:
        print(f"  CONCLUSION: Without the outlier, synthetic leads real "
              f"by ${abs(delta_ex):,.2f}.  The gap is outlier-driven.")

    # ── summary ───────────────────────────────────────────────────────────────
    print()
    print("=" * 80)
    print("  TOTALS SUMMARY")
    print("=" * 80)
    print(f"  Trades with real data   : {len(trades)}")
    print(f"  Skipped                 : {len(skipped)}")
    print(f"  Synthetic total         : ${total_synth:+,.2f}")
    print(f"  Real total              : ${total_real:+,.2f}")
    print(f"  Delta (real − synth)    : ${total_delta:+,.2f}")
    print()

    # ── write CSV for reference ───────────────────────────────────────────────
    csv_path = "f3_trade_comparison.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "date","direction","entry_premium","exit_premium",
            "contracts","synth_dollar","real_dollar","delta"])
        w.writeheader()
        for t in by_delta:
            w.writerow({k: t[k] for k in w.fieldnames})
    print(f"  CSV saved: {csv_path}  ({len(trades)} rows, sorted by delta desc)")


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    end_date   = date.today()
    start_date = end_date - timedelta(days=BACKTEST_DAYS)

    print()
    print("=" * 72)
    print("  F3 TRADE-LEVEL COMPARISON TABLE")
    print(f"  {start_date} → {end_date}")
    print(f"  ${TRADE_SIZE}/trade | No stop | No target | EOD exit")
    print("=" * 72)

    dm            = fetch_daily_data(start_date, end_date)
    raw_bars      = fetch_intraday_bars(start_date, end_date)
    reg_bs, pm_bs = organize_bars(raw_bars)

    trades, skipped = run(dm, reg_bs, pm_bs)
    print_report(trades, skipped)
