"""
f3_real_options.py
Directive_F3Backtest_RealOptionPricing_Build_2026-08-06

Replaces the synthetic leverage formula with real Polygon 0DTE
option premiums for every F3 trade in the 1-year backtest.

CONTRACT SIZING DECISION (Item 4):
    contracts = trade_size / (entry_premium * 100)

    Rationale: The entire session was framed as "$200 per trade."
    If we fixed contracts=1, a $1.00 premium day costs $100 and a
    $4.00 premium day costs $400 — inconsistent notional, not
    comparable to the synthetic run. Fractional contracts normalise
    every trade to exactly $200 notional so results are directly
    comparable to the prior backtest.

    dollar_pnl = (exit_premium - entry_premium) / entry_premium * 200

SPY 0DTE EXPIRATION CONFIRMATION:
    SPY began Mon/Wed/Fri expirations in 2005.
    Tue/Thu expirations were added 2022-05-02.
    Every trading day in Aug 2025 – Aug 2026 has a same-day expiration.
    expiration_date = trade_date always.

SKIP POLICY:
    If entry OR exit option price cannot be found from Polygon,
    the trade is logged to skipped_trades and omitted entirely.
    No silent fallback to the synthetic formula.

API EFFICIENCY:
    One API call per trade (full day's 1-min bars fetched once).
    Both entry price and exit price are extracted from that single
    response. This halves API calls vs two-call-per-trade design.
"""

import os, time, json, requests, subprocess
from datetime import datetime, timedelta, date
from collections import defaultdict

POLYGON_API_KEY   = os.environ.get("POLYGON_API_KEY", "YOUR_POLYGON_KEY_HERE")
TRADIER_API_TOKEN = (os.environ.get("TRADIER_API_TOKEN_2") or
                     os.environ.get("TRADIER_API_TOKEN", "YOUR_TRADIER_TOKEN_HERE"))

TRADE_SIZE    = 200
BACKTEST_DAYS = 365
API_DELAY     = 0.5   # seconds between option API calls (1 call per trade)


# ── helpers ───────────────────────────────────────────────────────────────────

def _et_minute(ts_ms):
    """Convert Polygon ms UTC timestamp → ET minute-of-day."""
    utc_dt    = datetime.utcfromtimestamp(int(ts_ms) / 1000)
    is_winter = utc_dt.month in (11, 12, 1, 2, 3)
    et_dt     = utc_dt - timedelta(hours=5 if is_winter else 4)
    return et_dt.hour * 60 + et_dt.minute


# ── real option pricing functions (directive spec) ────────────────────────────

def get_atm_option_ticker(spot_price, expiration_date, is_call):
    """
    SPY strikes are $1 increments. ATM = nearest whole dollar.
    Polygon OCC format: O:SPY{YYMMDD}{C|P}{8-digit-strike-3-dec-implied}
    Example: SPY at 562.40, call, 2026-06-05 → O:SPY260605C00562000
    """
    strike     = round(spot_price)
    strike_str = f"{int(strike * 1000):08d}"
    cp         = "C" if is_call else "P"
    exp_str    = expiration_date.strftime("%y%m%d")
    return f"O:SPY{exp_str}{cp}{strike_str}"


def fetch_option_bars_for_day(option_ticker, date_str):
    """
    Fetch ALL 1-min bars for option_ticker on date_str in one call.
    Returns (bars_list, raw_json).
    bars_list is [] if no data.
    """
    url    = (f"https://api.polygon.io/v2/aggs/ticker/{option_ticker}"
              f"/range/1/minute/{date_str}/{date_str}")
    params = {"adjusted": "true", "sort": "asc", "limit": 5000,
              "apiKey": POLYGON_API_KEY}
    try:
        resp = requests.get(url, params=params, timeout=30)
        raw  = resp.json()
    except Exception as e:
        raw = {"error": str(e)}
    bars = raw.get("results") or []
    return bars, raw


def fetch_option_price_at(option_ticker, date_str, target_minute, raw_log=None):
    """
    Directive-specified function signature (kept for compatibility).
    Returns (close_price_or_None, raw_json).
    """
    bars, raw = fetch_option_bars_for_day(option_ticker, date_str)
    if raw_log is not None:
        raw_log.append({"ticker": option_ticker, "date": date_str,
                        "target_minute": target_minute, "raw": raw})
    if not bars:
        return None, raw
    best = min(bars, key=lambda b: abs(_et_minute(b["t"]) - target_minute))
    return float(best["c"]), raw


# ── underlying data (unchanged from f3_strategy.py) ──────────────────────────

def fetch_daily_data(start_date, end_date):
    print("[1] Fetching daily SPY data from Tradier...")
    headers = {"Authorization": f"Bearer {TRADIER_API_TOKEN}",
               "Accept": "application/json"}
    params  = {"symbol": "SPY", "interval": "daily",
               "start": start_date.strftime("%Y-%m-%d"),
               "end":   end_date.strftime("%Y-%m-%d")}
    r    = requests.get("https://api.tradier.com/v1/markets/history",
                        headers=headers, params=params, timeout=20)
    days = r.json().get("history", {}).get("day", [])
    if not isinstance(days, list): days = [days]
    days_sorted = sorted(days, key=lambda x: x["date"])
    daily_map = {}
    for i, d in enumerate(days_sorted):
        pc = float(days_sorted[i-1]["close"]) if i > 0 else None
        daily_map[d["date"]] = {"open": float(d["open"]),
                                "close": float(d["close"]), "prev_close": pc}
    print(f"    → {len(daily_map)} trading days")
    return daily_map


def fetch_intraday_bars(start_date, end_date):
    print("[2] Fetching 5-min SPY bars from Polygon...")
    url    = (f"https://api.polygon.io/v2/aggs/ticker/SPY/range/5/minute/"
              f"{start_date.strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}")
    params = {"adjusted": "true", "sort": "asc", "limit": 50000,
              "apiKey": POLYGON_API_KEY}
    r      = requests.get(url, params=params, timeout=60)
    data   = r.json()
    bars   = data.get("results") or []
    print(f"    chunk 1: {len(bars)} [{data.get('status','?')}]")
    while data.get("next_url"):
        time.sleep(3)
        r    = requests.get(data["next_url"] + f"&apiKey={POLYGON_API_KEY}", timeout=60)
        data = r.json()
        more = data.get("results") or []
        bars.extend(more)
        print(f"    +{len(more)}  total={len(bars)}")
    print(f"    → {len(bars)} total bars")
    return bars


def organize_bars_by_day(raw_bars):
    print("[3] Organizing bars by day...")
    reg = defaultdict(list); pm = defaultdict(list)
    for b in raw_bars:
        try:
            utc_dt    = datetime.utcfromtimestamp(int(b["t"]) / 1000)
            is_winter = utc_dt.month in (11, 12, 1, 2, 3)
            et_dt     = utc_dt - timedelta(hours=5 if is_winter else 4)
            ds        = et_dt.strftime("%Y-%m-%d")
            mn        = et_dt.hour * 60 + et_dt.minute
            bd        = {"time_str": et_dt.strftime("%H:%M"), "minute": mn,
                         "open": float(b["o"]), "high": float(b["h"]),
                         "low":  float(b["l"]), "close": float(b["c"]),
                         "volume": float(b.get("v", 0))}
            if 570 <= mn < 960:   reg[ds].append(bd)
            elif 240 <= mn < 570: pm[ds].append(bd)
        except: continue
    for d in reg: reg[d].sort(key=lambda x: x["minute"])
    for d in pm:  pm[d].sort(key=lambda x: x["minute"])
    print(f"    → {len(reg)} days with regular-hours data")
    return reg, pm


# ── main backtest ─────────────────────────────────────────────────────────────

def run_f3_real_options(daily_map, regular_bars, premarket_bars,
                        trade_size, raw_evidence_log):
    """
    F3 strategy with real Polygon 0DTE option premiums.
    One API call per trade: fetch full day's 1-min bars,
    extract entry price (bar nearest entry_minute) and
    exit price (bar nearest 960 = 4:00 PM ET).
    """
    trades         = []
    skipped_trades = []
    raw_ev_count   = 0

    candidate_days = [d for d in sorted(daily_map.keys()) if regular_bars.get(d)]
    print(f"[4] Running F3 real option pricing ({len(candidate_days)} candidate days)...")
    print(f"    1 API call/trade × {API_DELAY}s delay")

    for date_str in candidate_days:
        daily    = daily_map[date_str]
        reg_bars = regular_bars.get(date_str, [])
        pm_bars  = premarket_bars.get(date_str, [])

        if not reg_bars or not daily["prev_close"] or len(reg_bars) < 10:
            continue

        # premarket direction
        if pm_bars:
            pm_dir = 1 if pm_bars[-1]["close"] > pm_bars[0]["open"] else -1
        else:
            skipped_trades.append({"date": date_str, "reason": "no_premarket_bars"})
            continue

        # opening range (9:30–9:44 AM)
        orb_bars = [b for b in reg_bars if b["minute"] < 585]
        if not orb_bars:
            skipped_trades.append({"date": date_str, "reason": "no_orb_bars"}); continue
        orb_high = max(b["high"] for b in orb_bars)
        orb_low  = min(b["low"]  for b in orb_bars)
        post_orb = [b for b in reg_bars if b["minute"] >= 585]
        if not post_orb: continue

        # breakout detection
        entry_bar = None
        for bar in post_orb:
            if pm_dir == 1  and bar["close"] > orb_high: entry_bar = bar; break
            if pm_dir == -1 and bar["close"] < orb_low:  entry_bar = bar; break
        if entry_bar is None: continue

        # build option ticker
        spy_price  = entry_bar["close"]
        trade_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        is_call    = (pm_dir == 1)
        opt_ticker = get_atm_option_ticker(spy_price, trade_date, is_call)

        entry_minute = entry_bar["minute"]
        exit_minute  = 960   # 16:00 ET

        # ONE API call — fetch full day's 1-min bars
        collect_raw = (raw_ev_count < 5)
        raw_log_buf = [] if collect_raw else None

        day_bars, raw_json = fetch_option_bars_for_day(opt_ticker, date_str)
        time.sleep(API_DELAY)

        if collect_raw:
            raw_evidence_log.append({
                "trade_index":  raw_ev_count + 1,
                "date":         date_str,
                "direction":    "CALL" if is_call else "PUT",
                "opt_ticker":   opt_ticker,
                "entry_minute": entry_minute,
                "exit_minute":  exit_minute,
                "raw_response": raw_json,   # full raw JSON from Polygon
            })

        if not day_bars:
            skipped_trades.append({
                "date": date_str, "ticker": opt_ticker,
                "reason": "no_option_bars",
                "polygon_status": raw_json.get("status"),
                "polygon_message": raw_json.get("message", "")[:120],
            })
            continue

        # extract entry and exit prices from the day's bars
        entry_bar_opt = min(day_bars, key=lambda b: abs(_et_minute(b["t"]) - entry_minute))
        exit_bar_opt  = min(day_bars, key=lambda b: abs(_et_minute(b["t"]) - exit_minute))
        entry_px      = float(entry_bar_opt["c"])
        exit_px       = float(exit_bar_opt["c"])

        # record resolved minute for evidence
        entry_resolved_min = _et_minute(entry_bar_opt["t"])
        exit_resolved_min  = _et_minute(exit_bar_opt["t"])

        if collect_raw:
            raw_evidence_log[-1]["entry_px"]            = entry_px
            raw_evidence_log[-1]["exit_px"]             = exit_px
            raw_evidence_log[-1]["entry_resolved_min"]  = entry_resolved_min
            raw_evidence_log[-1]["exit_resolved_min"]   = exit_resolved_min
            raw_evidence_log[-1]["total_bars_that_day"] = len(day_bars)
            raw_ev_count += 1

        if entry_px <= 0:
            skipped_trades.append({"date": date_str, "ticker": opt_ticker,
                                   "reason": "zero_entry_price"}); continue

        # P&L — $200 normalised notional
        contracts  = trade_size / (entry_px * 100)
        option_ret = (exit_px - entry_px) / entry_px * 100
        dollar_pnl = (exit_px - entry_px) * 100 * contracts

        trades.append({
            "date":               date_str,
            "direction":          "CALL" if is_call else "PUT",
            "entry_time":         entry_bar["time_str"],
            "spy_entry":          round(spy_price, 2),
            "orb_high":           round(orb_high, 2),
            "orb_low":            round(orb_low, 2),
            "opt_ticker":         opt_ticker,
            "entry_px":           round(entry_px, 4),
            "exit_px":            round(exit_px, 4),
            "entry_minute_req":   entry_minute,
            "entry_minute_actual":entry_resolved_min,
            "exit_minute_actual": exit_resolved_min,
            "contracts":          round(contracts, 4),
            "option_ret":         round(option_ret, 2),
            "dollar_pnl":         round(dollar_pnl, 2),
            "win":                option_ret > 0,
        })

    print(f"    → {len(trades)} real trades | {len(skipped_trades)} skipped")
    return trades, skipped_trades


# ── evidence report ───────────────────────────────────────────────────────────

def print_full_report(trades, skipped_trades, raw_evidence_log,
                      days_with_data, trade_size):

    total_attempted = len(trades) + len(skipped_trades)
    skip_pct = (len(skipped_trades) / total_attempted * 100
                if total_attempted else 0)

    print()
    print("=" * 72)
    print("  DIRECTIVE EVIDENCE REPORT — F3 REAL OPTION PRICING")
    print("=" * 72)

    # ── Item 1 ────────────────────────────────────────────────────────────────
    print()
    print("─" * 72)
    print("  ITEM 1 — FULL-RANGE COVERAGE CHECK")
    print("─" * 72)
    print(f"  Trading days with intraday SPY data : {days_with_data}")
    print(f"  Breakout signals attempted          : {total_attempted}")
    print(f"  Trades with real option data found  : {len(trades)}")
    print(f"  Trades SKIPPED (missing opt data)   : {len(skipped_trades)}  ({skip_pct:.1f}%)")
    if skip_pct > 10:
        print(f"\n  *** WARNING: {skip_pct:.1f}% skip rate exceeds 10% threshold ***")
        print(f"  *** Results below cover only {len(trades)} of {total_attempted} signals ***")
    if skipped_trades:
        skip_reasons = defaultdict(int)
        for s in skipped_trades: skip_reasons[s["reason"]] += 1
        print(f"\n  Skip breakdown:")
        for r, c in sorted(skip_reasons.items(), key=lambda x: -x[1]):
            print(f"    {r:<35}: {c}")
        print(f"\n  First 15 skipped:")
        print(f"  {'Date':<12} {'Ticker':<28} Reason / Polygon status")
        print(f"  {'─'*12} {'─'*28} {'─'*30}")
        for s in skipped_trades[:15]:
            msg = s.get("polygon_message","") or s.get("reason","")
            print(f"  {s['date']:<12} {s.get('ticker','—'):<28} "
                  f"{s['reason']}  [{s.get('polygon_status','')}]  {msg[:50]}")

    # ── Item 2 ────────────────────────────────────────────────────────────────
    print()
    print("─" * 72)
    print("  ITEM 2 — RAW API RESPONSES (first 5 trades with real data)")
    print("─" * 72)
    for ev in raw_evidence_log:
        print(f"\n  Trade #{ev['trade_index']}  {ev['date']}  {ev['direction']}")
        print(f"  Ticker : {ev['opt_ticker']}")
        print(f"  Entry bar requested : minute {ev['entry_minute']} "
              f"({ev['entry_minute']//60}:{ev['entry_minute']%60:02d} ET)")
        print(f"  Entry bar resolved  : minute {ev.get('entry_resolved_min','?')} "
              f"  price=${ev.get('entry_px','?')}")
        print(f"  Exit bar resolved   : minute {ev.get('exit_resolved_min','?')} "
              f"  price=${ev.get('exit_px','?')}")
        print(f"  Total 1-min bars returned that day: {ev.get('total_bars_that_day','?')}")
        print(f"\n  Full raw Polygon JSON response:")
        raw_str = json.dumps(ev["raw_response"], indent=2)
        # print up to 4000 chars — enough to see real bars
        print(raw_str[:4000])
        if len(raw_str) > 4000:
            print(f"  ... [truncated — {len(raw_str)} chars total, "
                  f"{len(ev['raw_response'].get('results',[]))} bars]")

    # ── Item 3 ────────────────────────────────────────────────────────────────
    print()
    print("─" * 72)
    print("  ITEM 3 — TRADE-BY-TRADE TABLE (real premiums)")
    print("─" * 72)
    print(f"\n  {'Date':<12} {'Dir':<5} {'Sig':>5}  {'Opt Ticker':<28}  "
          f"{'Entry$':>7}  {'Exit$':>7}  {'Ret%':>8}  {'P&L':>8}")
    print(f"  {'─'*12} {'─'*5} {'─'*5}  {'─'*28}  "
          f"{'─'*7}  {'─'*7}  {'─'*8}  {'─'*8}")
    for t in trades:
        print(f"  {t['date']:<12} {t['direction']:<5} {t['entry_time']:>5}  "
              f"{t['opt_ticker']:<28}  "
              f"${t['entry_px']:>6.3f}  ${t['exit_px']:>6.3f}  "
              f"{t['option_ret']:>+7.1f}%  ${t['dollar_pnl']:>+6.2f}")

    # ── Item 4 ────────────────────────────────────────────────────────────────
    print()
    print("─" * 72)
    print("  ITEM 4 — CONTRACT SIZING CONVENTION")
    print("─" * 72)
    print(f"  Convention : contracts = {trade_size} / (entry_premium × 100)")
    print(f"  This fixes every trade at ${trade_size} notional, regardless of premium.")
    if trades:
        avg_ep = sum(t["entry_px"]  for t in trades) / len(trades)
        avg_ct = sum(t["contracts"] for t in trades) / len(trades)
        print(f"  Avg entry premium  : ${avg_ep:.3f}  →  avg contracts = {avg_ct:.3f}")
        print(f"  Range: entry_px min=${min(t['entry_px'] for t in trades):.3f}  "
              f"max=${max(t['entry_px'] for t in trades):.3f}")

    # ── Item 5 ────────────────────────────────────────────────────────────────
    print()
    print("─" * 72)
    print("  ITEM 5 — AGGREGATE STATS (REAL PREMIUMS — replaces synthetic)")
    print("─" * 72)
    if not trades:
        print("  No trades with real option data — cannot compute stats.")
        print("  This means Polygon does not have historical 1-min option bars")
        print("  for this account tier. See skip breakdown in Item 1.")
    else:
        total_pnl   = sum(t["dollar_pnl"] for t in trades)
        total_spent = trade_size * len(trades)
        wins        = [t for t in trades if t["win"]]
        losses      = [t for t in trades if not t["win"]]
        win_rets    = [t["option_ret"] for t in wins]
        loss_rets   = [t["option_ret"] for t in losses]
        pf          = (abs(sum(win_rets)/sum(loss_rets))
                       if loss_rets and sum(loss_rets)!=0 else 99.0)
        best        = max(trades, key=lambda x: x["option_ret"])
        worst       = min(trades, key=lambda x: x["option_ret"])

        print()
        print(f"  NOTE: Synthetic results (178 trades, +$7,070, +19.9% ROC)")
        print(f"        are SUPERSEDED by these real-premium numbers.")
        print()
        print(f"  Total trades (real data)     : {len(trades)}")
        print(f"  Skipped (no option data)     : {len(skipped_trades)}  ({skip_pct:.1f}%)")
        print(f"  Win rate                     : {len(wins)/len(trades)*100:.1f}%")
        if wins:
            print(f"  Avg winning trade            : +{sum(win_rets)/len(wins):.1f}%"
                  f"  (${sum(win_rets)/len(wins)/100*trade_size:+.2f})")
        if losses:
            print(f"  Avg losing trade             : {sum(loss_rets)/len(losses):.1f}%"
                  f"  (${sum(loss_rets)/len(losses)/100*trade_size:+.2f})")
        print(f"  Profit factor                : {pf:.2f}")
        print(f"  Total premiums spent         : ${total_spent:,.0f}")
        print(f"  Total profit (real)          : ${total_pnl:+,.2f}")
        print(f"  Cash-on-cash (real)          : {total_pnl/total_spent*100:+.1f}%")
        print(f"  Avg profit per trade         : ${total_pnl/len(trades):+.2f}")
        print(f"  Avg profit per month         : ${total_pnl/12:+.2f}")
        print(f"  Best  : {best['date']}  {best['direction']}"
              f"  {best['option_ret']:+.1f}%  ${best['dollar_pnl']:+.2f}")
        print(f"  Worst : {worst['date']}  {worst['direction']}"
              f"  {worst['option_ret']:+.1f}%  ${worst['dollar_pnl']:+.2f}")

        print()
        print(f"  Month by month:")
        print(f"  {'Month':<8} {'N':>4}  {'WR%':>5}  {'P&L':>10}")
        print(f"  {'─'*8} {'─'*4}  {'─'*5}  {'─'*10}")
        by_month = defaultdict(list)
        for t in trades: by_month[t["date"][:7]].append(t)
        for m in sorted(by_month):
            mt = by_month[m]
            wr = sum(1 for t in mt if t["win"]) / len(mt) * 100
            print(f"  {m:<8} {len(mt):>4}  {wr:>4.0f}%  "
                  f"${sum(t['dollar_pnl'] for t in mt):>+8.2f}")

    # ── Items 6 & 7 ───────────────────────────────────────────────────────────
    print()
    print("─" * 72)
    print("  ITEM 6 — SHA256 + GIT COMMIT")
    print("─" * 72)
    sha = subprocess.run(["sha256sum", "f3_real_options.py"],
                         capture_output=True, text=True).stdout.strip()
    print(f"  {sha}")
    git_add = subprocess.run(
        ["git", "add", "f3_real_options.py", "f3_strategy.py"],
        capture_output=True, text=True, cwd="/home/runner/workspace")
    git_commit = subprocess.run(
        ["git", "commit", "-m",
         "Directive_F3Backtest_RealOptionPricing_Build_2026-08-06: "
         "f3_real_options.py — real Polygon 0DTE premiums replace synthetic leverage"],
        capture_output=True, text=True, cwd="/home/runner/workspace")
    git_log = subprocess.run(
        ["git", "log", "--oneline", "-1"],
        capture_output=True, text=True, cwd="/home/runner/workspace")
    print(f"  git commit: {git_log.stdout.strip()}")
    if git_commit.returncode != 0:
        print(f"  git note: {git_commit.stderr.strip()[:200]}")

    print()
    print("─" * 72)
    print("  ITEM 7 — NEGATIVE CONTROL")
    print("─" * 72)
    if trades:
        ctrl_trade = trades[0]
        print(f"  Control trade: {ctrl_trade['date']}  {ctrl_trade['opt_ticker']}")
        print(f"  Polygon 1-min close at ~{ctrl_trade['entry_minute_req']//60}:"
              f"{ctrl_trade['entry_minute_req']%60:02d}: ${ctrl_trade['entry_px']:.4f}")
        # Cross-check via Tradier option time & sales
        h = {"Authorization": f"Bearer {TRADIER_API_TOKEN}",
             "Accept": "application/json"}
        occ_sym = ctrl_trade["opt_ticker"].replace("O:", "")
        r = requests.get("https://api.tradier.com/v1/markets/history",
                         headers=h, timeout=15,
                         params={"symbol": occ_sym, "interval": "daily",
                                 "start": ctrl_trade["date"],
                                 "end":   ctrl_trade["date"]})
        raw_t = r.json()
        print(f"\n  Tradier /markets/history response for same contract:")
        print(json.dumps(raw_t, indent=2)[:1500])
        day_t = raw_t.get("history", {}).get("day")
        if day_t:
            if isinstance(day_t, list): day_t = day_t[0]
            t_close = float(day_t.get("close", 0))
            print(f"\n  Tradier EOD close : ${t_close:.4f}")
            print(f"  Polygon entry px  : ${ctrl_trade['entry_px']:.4f}  "
                  f"(intraday ~{ctrl_trade['entry_time']})")
            print(f"  Polygon exit px   : ${ctrl_trade['exit_px']:.4f}  (EOD)")
            if t_close > 0:
                diff = abs(ctrl_trade["exit_px"] - t_close) / t_close * 100
                print(f"  Polygon EOD vs Tradier EOD diff: {diff:.2f}%")
                print(f"  (Intraday vs EOD diff is expected — different time of day)")
        else:
            print("  Tradier returned no day record for this option.")


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    end_date   = date.today()
    start_date = end_date - timedelta(days=BACKTEST_DAYS)

    print()
    print("=" * 72)
    print("  F3 REAL OPTIONS BACKTEST")
    print(f"  {start_date} → {end_date}")
    print(f"  ${TRADE_SIZE}/trade normalised | No stop | No target | EOD exit")
    print("=" * 72)
    print()

    daily_map                    = fetch_daily_data(start_date, end_date)
    raw_bars                     = fetch_intraday_bars(start_date, end_date)
    regular_bars, premarket_bars = organize_bars_by_day(raw_bars)
    days_with_data               = len(regular_bars)

    raw_evidence_log = []
    trades, skipped  = run_f3_real_options(
        daily_map, regular_bars, premarket_bars, TRADE_SIZE, raw_evidence_log)

    print_full_report(trades, skipped, raw_evidence_log,
                      days_with_data, TRADE_SIZE)
