#!/usr/bin/env python3
"""
zero_dte_freq_bt.py — 0DTE Sweep FREQUENCY Backtest via Polygon.

Walks every trading day in the past 2 years and simulates the 5-minute
rolling scanner for SPY (both windows, both sides) to count how often
the $500k sweep gate fires.

Gates applied:
  ✓ Gate 4 — $500k sweep  (vol_delta × midPrice × 100 in any 5-min tick)
  ~ Gate 6 — Price confirm (omitted — needs intraday underlying bars;
                             acts as a further ~30-40% reduction)
  ~ Gate 1 — Spread       (assumed PASS — liquid ATM option)
  ~ Gate 2 — Delta        (ATM ~0.45-0.55; assumed PASS)
  ~ Gate 3 — VOI          (historical OI unavailable; SKIPPED)
  ~ Gate 5 — IV Rank      (not stored pre-scanner; SKIPPED)

This gives an UPPER BOUND on trade frequency.
Real live frequency = this number × (Gate 3 × Gate 5 × Gate 6 pass rates).
Rough real-world reduction: ×0.25–0.40 from these three additional gates.

Results stored in zero_dte_freq_bt_days (resumable).
"""

import os, sys, time, json, datetime, urllib.request, urllib.parse
import psycopg2, psycopg2.extras

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except ImportError:
    import pytz
    _ET = pytz.timezone("America/New_York")

POLY_KEY       = os.environ.get("POLYGON_API_KEY", "")
POLY_BASE      = "https://api.polygon.io"
RATE_SLEEP     = 0.22

TICKER         = "SPY"
PREMIUM_THRESH = 500_000
SCAN_INTERVAL  = 5          # minutes
LOOKBACK_DAYS  = 730        # 2 calendar years

WINDOWS = [
    (10, 0, 11, 30, "morning"),
    (14, 0, 15, 30, "afternoon"),
]


# ── DB ──────────────────────────────────────────────────────────────────────
def _db():
    return psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=8)


def ensure_tables():
    db = _db(); cur = db.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS zero_dte_freq_bt_days (
        id             SERIAL PRIMARY KEY,
        trade_date     DATE NOT NULL UNIQUE,
        spy_open       NUMERIC,
        am_call_fire   BOOLEAN DEFAULT FALSE,
        am_put_fire    BOOLEAN DEFAULT FALSE,
        pm_call_fire   BOOLEAN DEFAULT FALSE,
        pm_put_fire    BOOLEAN DEFAULT FALSE,
        am_call_sweeps INTEGER DEFAULT 0,
        am_put_sweeps  INTEGER DEFAULT 0,
        pm_call_sweeps INTEGER DEFAULT 0,
        pm_put_sweeps  INTEGER DEFAULT 0,
        no_data        BOOLEAN DEFAULT FALSE,
        processed_at   TIMESTAMPTZ DEFAULT NOW()
    );
    """)
    db.commit(); db.close()


# ── Polygon helpers ──────────────────────────────────────────────────────────
def _poly(path):
    sep = "&" if "?" in path else "?"
    url = f"{POLY_BASE}{path}{sep}limit=1000"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {POLY_KEY}"})
    try:
        with urllib.request.urlopen(req, timeout=14) as r:
            return json.loads(r.read())
    except:
        return {"results": []}


def _et_ms(date_str, hour, minute):
    parts = [int(x) for x in date_str.split("-")]
    dt = datetime.datetime(*parts, hour, minute, 0, tzinfo=_ET)
    return int(dt.timestamp() * 1000)


def _spy_daily_open(date_str):
    """SPY daily bar open — available for all trading days on any Polygon tier."""
    d = _poly(f"/v2/aggs/ticker/SPY/range/1/day/{date_str}/{date_str}?adjusted=false")
    time.sleep(RATE_SLEEP)
    bars = d.get("results", [])
    return float(bars[0]["o"]) if bars else None


def _option_bars(date_str, side, strike):
    """1-minute bars for the 0DTE ATM option contract."""
    dt  = datetime.date.fromisoformat(date_str)
    yy  = dt.strftime("%y"); mm = dt.strftime("%m"); dd = dt.strftime("%d")
    cp  = "C" if side == "call" else "P"
    sk8 = f"{int(round(float(strike) * 1000)):08d}"
    sym = urllib.parse.quote(f"O:{TICKER}{yy}{mm}{dd}{cp}{sk8}")
    d   = _poly(f"/v2/aggs/ticker/{sym}/range/1/minute/{date_str}/{date_str}?adjusted=false")
    time.sleep(RATE_SLEEP)
    return d.get("results", [])


# ── Simulate one (window, side) ─────────────────────────────────────────────
def _simulate(opt_bars, date_str, win_open_hh, win_open_mm,
              win_close_hh, win_close_mm):
    """
    Replay the 5-min rolling scanner.  Returns (fired: bool, n_sweeps: int).
    Checks Gate 4 only: sweep_usd = vol_delta × midPrice × 100 >= $500k.
    """
    if not opt_bars:
        return False, 0

    open_ms  = _et_ms(date_str, win_open_hh,  win_open_mm)
    close_ms = _et_ms(date_str, win_close_hh, win_close_mm)
    opt_sorted = sorted(opt_bars, key=lambda b: b["t"])

    fired    = False
    n_sweeps = 0
    prev_vol = None
    tick_ms  = open_ms

    while tick_ms <= close_ms:
        cum_vol = sum(int(b.get("v", 0)) for b in opt_sorted if b["t"] <= tick_ms)
        mid_px  = None
        for b in reversed(opt_sorted):
            if b["t"] <= tick_ms:
                mid_px = (float(b["o"]) + float(b["c"])) / 2.0
                break

        if prev_vol is not None and mid_px and mid_px > 0:
            vol_delta = max(0, cum_vol - prev_vol)
            sweep_usd = vol_delta * mid_px * 100
            if sweep_usd >= PREMIUM_THRESH:
                n_sweeps += 1
                fired = True

        prev_vol = cum_vol
        tick_ms += SCAN_INTERVAL * 60 * 1000

    return fired, n_sweeps


# ── Main loop ────────────────────────────────────────────────────────────────
def run_freq_bt(max_seconds=None):
    ensure_tables()

    today = datetime.date.today()
    start = today - datetime.timedelta(days=LOOKBACK_DAYS)

    db = _db(); cur = db.cursor()
    cur.execute("SELECT trade_date FROM zero_dte_freq_bt_days")
    done = {r[0] for r in cur.fetchall()}
    db.close()

    all_days  = []
    d = start
    while d <= today:
        if d.weekday() < 5:
            all_days.append(d)
        d += datetime.timedelta(days=1)

    remaining = [d for d in all_days if d not in done]
    print(f"[freq_bt] {len(all_days)} weekdays total | {len(done)} done | "
          f"{len(remaining)} to process", flush=True)

    t_start = time.time()

    for i, trade_date in enumerate(remaining):
        if max_seconds and (time.time() - t_start) > max_seconds:
            pct = 100 * (len(done) + i) / len(all_days)
            print(f"[freq_bt] time budget reached after {i} days "
                  f"({pct:.0f}% complete) — run again to continue", flush=True)
            _report()
            return

        date_str = trade_date.isoformat()
        print(f"[freq_bt] {len(done)+i+1}/{len(all_days)}  {date_str}", end=" ", flush=True)

        # Step 1: SPY daily open for ATM strike (works on all Polygon tiers)
        spy_open = _spy_daily_open(date_str)
        if spy_open is None:
            db = _db(); cur = db.cursor()
            cur.execute("INSERT INTO zero_dte_freq_bt_days (trade_date, no_data) "
                        "VALUES (%s,TRUE) ON CONFLICT (trade_date) DO NOTHING", (trade_date,))
            db.commit(); db.close()
            print("holiday/no-data", flush=True)
            continue

        strike = round(spy_open)  # SPY strikes at $1 intervals

        res = dict(spy_open=spy_open,
                   am_call_fire=False, am_put_fire=False,
                   pm_call_fire=False, pm_put_fire=False,
                   am_call_sweeps=0,  am_put_sweeps=0,
                   pm_call_sweeps=0,  pm_put_sweeps=0)

        for (woh, wom, wch, wcm, wname) in WINDOWS:
            for side in ("call", "put"):
                bars = _option_bars(date_str, side, strike)
                fired, n = _simulate(bars, date_str, woh, wom, wch, wcm)
                key = f"{wname[:2]}_{side}"
                res[f"{key}_fire"]   = fired
                res[f"{key}_sweeps"] = n

        db = _db(); cur = db.cursor()
        cur.execute("""
            INSERT INTO zero_dte_freq_bt_days
              (trade_date, spy_open,
               am_call_fire, am_put_fire, pm_call_fire, pm_put_fire,
               am_call_sweeps, am_put_sweeps, pm_call_sweeps, pm_put_sweeps)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (trade_date) DO NOTHING
        """, (trade_date, res["spy_open"],
              res["am_call_fire"], res["am_put_fire"],
              res["pm_call_fire"], res["pm_put_fire"],
              res["am_call_sweeps"], res["am_put_sweeps"],
              res["pm_call_sweeps"], res["pm_put_sweeps"]))
        db.commit(); db.close()

        total_fires = sum([res["am_call_fire"], res["am_put_fire"],
                           res["pm_call_fire"], res["pm_put_fire"]])
        print(f"strike=${strike}  fires={total_fires}/4  "
              f"(am_c={res['am_call_fire']} am_p={res['am_put_fire']} "
              f"pm_c={res['pm_call_fire']} pm_p={res['pm_put_fire']})", flush=True)

    print(f"\n[freq_bt] Complete!", flush=True)
    _report()


# ── Report ────────────────────────────────────────────────────────────────────
def _report():
    from collections import Counter
    db = _db(); cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("""
        SELECT
            COUNT(*) FILTER (WHERE no_data IS NOT TRUE)       AS trading_days,
            COUNT(*) FILTER (WHERE no_data = TRUE)            AS holidays,
            COUNT(*) FILTER (WHERE no_data IS NOT TRUE AND
                (am_call_fire OR am_put_fire OR pm_call_fire OR pm_put_fire))
                                                              AS days_any_fire,
            COUNT(*) FILTER (WHERE no_data IS NOT TRUE AND
                (am_call_fire OR am_put_fire))                AS days_am_fire,
            COUNT(*) FILTER (WHERE no_data IS NOT TRUE AND
                (pm_call_fire OR pm_put_fire))                AS days_pm_fire,
            ROUND(AVG(
              CASE WHEN no_data IS NOT TRUE THEN
                (CASE WHEN am_call_fire THEN 1 ELSE 0 END) +
                (CASE WHEN am_put_fire  THEN 1 ELSE 0 END) +
                (CASE WHEN pm_call_fire THEN 1 ELSE 0 END) +
                (CASE WHEN pm_put_fire  THEN 1 ELSE 0 END)
              END)::numeric, 2)                               AS avg_fires_per_day
        FROM zero_dte_freq_bt_days
    """)
    r = cur.fetchone()

    cur.execute("""
        SELECT
          (CASE WHEN am_call_fire THEN 1 ELSE 0 END +
           CASE WHEN am_put_fire  THEN 1 ELSE 0 END +
           CASE WHEN pm_call_fire THEN 1 ELSE 0 END +
           CASE WHEN pm_put_fire  THEN 1 ELSE 0 END) AS n,
          COUNT(*) AS days
        FROM zero_dte_freq_bt_days
        WHERE no_data IS NOT TRUE
        GROUP BY n ORDER BY n
    """)
    dist = cur.fetchall()
    db.close()

    td = r['trading_days'] or 1
    pct_fire = 100 * r['days_any_fire'] / td

    print("\n" + "="*65)
    print("0DTE SWEEP FREQUENCY — 2-YEAR BACKTEST RESULTS (SPY)")
    print("="*65)
    print(f"  Active trading days    : {r['trading_days']}")
    print(f"  Market holidays        : {r['holidays']}")
    print(f"  Days w/ ANY $500k sweep: {r['days_any_fire']}  ({pct_fire:.1f}%)")
    print(f"  Days w/ morning sweep  : {r['days_am_fire']}")
    print(f"  Days w/ afternoon sweep: {r['days_pm_fire']}")
    print(f"  Avg sweeps per day     : {r['avg_fires_per_day']}")
    print()
    print("  Trades-per-day distribution (Gate 4 only):")
    for row in dist:
        print(f"    {row['n']} trades: {row['days']} days  "
              f"({100*row['days']/td:.1f}%)")
    print()
    print("  UPPER BOUND — Gates 3+5+6 not applied.")
    print("  Real frequency estimate: multiply by ~0.25-0.40")
    real_low  = r['avg_fires_per_day'] * 0.25 if r['avg_fires_per_day'] else 0
    real_high = r['avg_fires_per_day'] * 0.40 if r['avg_fires_per_day'] else 0
    print(f"  → Estimated REAL avg   : {real_low:.1f}–{real_high:.1f} trades/day")
    print("="*65)


if __name__ == "__main__":
    if "--report" in sys.argv:
        _report()
    else:
        max_sec = None
        if "--max-seconds" in sys.argv:
            idx = sys.argv.index("--max-seconds")
            max_sec = int(sys.argv[idx + 1])
        run_freq_bt(max_seconds=max_sec)
