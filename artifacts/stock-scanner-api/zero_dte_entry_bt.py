#!/usr/bin/env python3
"""
zero_dte_entry_bt.py — 0DTE Entry Criteria Backtest

Tests which combination of entry gate thresholds produces the highest win rate.

DATA COLLECTION (1-year window, 3 strikes each side):
  For each trading day, at 10:05 ET and 14:05 ET:
    - Pulls ATM-1, ATM, ATM+1 strikes for calls and puts
    - Records: entry_price, 5-min sweep_$ proxy, moneyness, window
    - Simulates exit: PT=+50%, SL=−10% (current live config)

ANALYSIS — tests every combination of:
    sweep_thresh : $0 / $25k / $50k / $100k / $250k / $500k / $1M / $2M
    moneyness    : any (±2) / near-ATM (±1) / exact-ATM only
    window       : both / morning only / afternoon only
    min_price    : $0 / $0.30 / $0.50 / $0.75 / $1.00

Outputs: top 20 combos ranked by win rate (minimum 10 trades for significance).
"""

import os, time, json, datetime, urllib.request, urllib.parse, threading
import psycopg2, psycopg2.extras, decimal

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except ImportError:
    _ET = None

_POLY_KEY   = os.environ.get("POLYGON_API_KEY", "")
_POLY_BASE  = "https://api.polygon.io"
_RATE_SLEEP = 0.22

PT_FIXED = 0.50
SL_FIXED = 0.10

WINDOWS_ET     = [(10, 5, "morning"), (14, 5, "afternoon")]
EOD_HH, EOD_MM = 15, 35
DEFAULT_DAYS   = 365

# Strikes relative to ATM round-dollar (negative = ITM for calls)
STRIKE_OFFSETS = [-1, 0, 1]

_running = False
_lock    = threading.Lock()


# ── DB ──────────────────────────────────────────────────────────────────────
def _db():
    return psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=8)


def ensure_tables():
    db = _db(); cur = db.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS zero_dte_entry_bt_progress (
        trade_date  DATE NOT NULL,
        window_name TEXT NOT NULL,
        UNIQUE(trade_date, window_name)
    );
    CREATE TABLE IF NOT EXISTS zero_dte_entry_bt_raw (
        id              SERIAL PRIMARY KEY,
        trade_date      DATE    NOT NULL,
        window_name     TEXT    NOT NULL,
        side            TEXT    NOT NULL,
        strike          NUMERIC NOT NULL,
        spy_price       NUMERIC NOT NULL,
        moneyness       NUMERIC NOT NULL,
        entry_price     NUMERIC NOT NULL,
        sweep_dollar    NUMERIC NOT NULL,
        entry_bar_vol   INTEGER NOT NULL,
        exit_reason     TEXT    NOT NULL,
        pnl_pct         NUMERIC NOT NULL,
        bars_elapsed    INTEGER,
        UNIQUE(trade_date, window_name, side, strike)
    );
    """)
    db.commit(); db.close()


# ── Time helpers ─────────────────────────────────────────────────────────────
def _et_to_ms(date_str: str, hour: int, minute: int) -> int:
    parts = [int(x) for x in date_str.split("-")]
    if _ET:
        dt = datetime.datetime(*parts, hour, minute, 0, tzinfo=_ET)
        return int(dt.timestamp() * 1000)
    month  = parts[1]
    offset = -4 if 3 <= month <= 10 else -5
    dt = datetime.datetime(*parts, hour, minute, 0,
                           tzinfo=datetime.timezone(datetime.timedelta(hours=offset)))
    return int(dt.timestamp() * 1000)


# ── Polygon helpers ───────────────────────────────────────────────────────────
def _poly_get(path: str) -> dict:
    sep = "&" if "?" in path else "?"
    url = f"{_POLY_BASE}{path}{sep}limit=1000"
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {_POLY_KEY}"})
    try:
        with urllib.request.urlopen(req, timeout=14) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"status": "ERROR", "error": str(e), "results": []}


_spy_cache: dict[str, list] = {}


def _spy_bars(date_str: str) -> list:
    if date_str in _spy_cache:
        return _spy_cache[date_str]
    d    = _poly_get(f"/v2/aggs/ticker/SPY/range/1/minute/{date_str}/{date_str}?adjusted=false")
    bars = d.get("results", [])
    _spy_cache[date_str] = bars
    time.sleep(_RATE_SLEEP)
    return bars


def _option_bars(date_str: str, side: str, strike: float) -> list:
    dt  = datetime.date.fromisoformat(date_str)
    yy  = dt.strftime("%y"); mm = dt.strftime("%m"); dd = dt.strftime("%d")
    cp  = "C" if side == "call" else "P"
    sk8 = f"{int(round(strike * 1000)):08d}"
    sym = urllib.parse.quote(f"O:SPY{yy}{mm}{dd}{cp}{sk8}")
    d   = _poly_get(f"/v2/aggs/ticker/{sym}/range/1/minute/{date_str}/{date_str}?adjusted=false")
    time.sleep(_RATE_SLEEP)
    return d.get("results", [])


def _entry_price(bars: list, entry_ms: int):
    for b in bars:
        if b["t"] >= entry_ms:
            v = float(b["o"])
            return v if v >= 0.01 else None
    return None


def _sweep_dollar(bars: list, entry_ms: int, entry_price: float) -> tuple[float, int]:
    """
    5-minute sweep volume proxy: sum bar volumes in [entry_ms-5min, entry_ms].
    Returns (sweep_dollar, total_vol).
    """
    window_start = entry_ms - 5 * 60 * 1000
    total_vol = 0
    for b in bars:
        if window_start <= b["t"] <= entry_ms:
            total_vol += int(b.get("v", 0))
    sweep = total_vol * entry_price * 100
    return round(sweep, 2), total_vol


def _eod_price(bars: list, eod_ms: int, fallback: float) -> float:
    price = fallback
    for b in bars:
        if b["t"] <= eod_ms:
            price = float(b["c"])
    return price


# ── Exit simulation ───────────────────────────────────────────────────────────
def _simulate(bars: list, entry_ms: int, entry_price: float, eod_ms: int) -> dict:
    target  = entry_price * (1 + PT_FIXED)
    stop    = entry_price * (1 - SL_FIXED)
    found   = False
    elapsed = 0
    for b in bars:
        ts = b["t"]
        if ts > eod_ms:
            break
        if not found:
            if ts >= entry_ms:
                found = True
            else:
                continue
        lo = float(b["l"]); hi = float(b["h"])
        elapsed += 1
        if lo <= stop:
            return {"exit_reason": "stop",   "pnl_pct": round(-SL_FIXED, 6),
                    "bars_elapsed": elapsed}
        if hi >= target:
            return {"exit_reason": "target", "pnl_pct": round(PT_FIXED, 6),
                    "bars_elapsed": elapsed}
    ep  = _eod_price(bars, eod_ms, entry_price)
    pnl = (ep - entry_price) / entry_price if entry_price > 0 else 0.0
    return {"exit_reason": "eod", "pnl_pct": round(pnl, 6), "bars_elapsed": elapsed}


# ── Main backtest ─────────────────────────────────────────────────────────────
def run_backtest(lookback_days: int = DEFAULT_DAYS) -> str:
    ensure_tables()
    db = _db(); cur = db.cursor()

    today = datetime.date.today()
    start = today - datetime.timedelta(days=lookback_days)

    all_dates = []
    d = start
    while d < today:
        if d.weekday() < 5:
            all_dates.append(d.isoformat())
        d += datetime.timedelta(days=1)

    cur.execute("SELECT trade_date::text, window_name FROM zero_dte_entry_bt_progress")
    done: set[tuple] = set(tuple(r) for r in cur.fetchall())

    print(f"[entry_bt] {len(all_dates)} trading days, {len(done)} already done", flush=True)

    inserted = 0
    for i, date_str in enumerate(all_dates):
        eod_ms   = _et_to_ms(date_str, EOD_HH, EOD_MM)
        spy_bars = _spy_bars(date_str)
        if len(spy_bars) < 10:
            for (_, _, wname) in WINDOWS_ET:
                k = (date_str, wname)
                if k not in done:
                    cur.execute("INSERT INTO zero_dte_entry_bt_progress VALUES(%s,%s) "
                                "ON CONFLICT DO NOTHING", (date_str, wname))
                    done.add(k)
            db.commit()
            continue

        for (wh, wm, wname) in WINDOWS_ET:
            k = (date_str, wname)
            if k in done:
                continue

            entry_ms  = _et_to_ms(date_str, wh, wm)

            spy_price = None
            for b in spy_bars:
                if b["t"] >= entry_ms:
                    spy_price = float(b["o"]); break
            if spy_price is None:
                cur.execute("INSERT INTO zero_dte_entry_bt_progress VALUES(%s,%s) "
                            "ON CONFLICT DO NOTHING", (date_str, wname))
                db.commit(); done.add(k); continue

            atm = round(spy_price)

            for offset in STRIKE_OFFSETS:
                strike = float(atm + offset)
                for side in ("call", "put"):
                    opt_bars = _option_bars(date_str, side, strike)
                    if len(opt_bars) < 5:
                        continue

                    ep = _entry_price(opt_bars, entry_ms)
                    if ep is None or ep < 0.01:
                        continue

                    sweep_usd, entry_vol = _sweep_dollar(opt_bars, entry_ms, ep)

                    # moneyness: negative = ITM for calls, positive = OTM for calls
                    # flip sign for puts so negative = ITM in both cases
                    if side == "call":
                        moneyness = round(strike - spy_price, 2)
                    else:
                        moneyness = round(spy_price - strike, 2)

                    res = _simulate(opt_bars, entry_ms, ep, eod_ms)

                    cur.execute("""
                        INSERT INTO zero_dte_entry_bt_raw
                            (trade_date, window_name, side, strike, spy_price,
                             moneyness, entry_price, sweep_dollar, entry_bar_vol,
                             exit_reason, pnl_pct, bars_elapsed)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT(trade_date,window_name,side,strike) DO NOTHING
                    """, (date_str, wname, side, strike, round(spy_price, 2),
                          moneyness, ep, sweep_usd, entry_vol,
                          res["exit_reason"], res["pnl_pct"], res["bars_elapsed"]))
                    inserted += 1

            cur.execute("INSERT INTO zero_dte_entry_bt_progress VALUES(%s,%s) "
                        "ON CONFLICT DO NOTHING", (date_str, wname))
            db.commit()
            done.add(k)

            if i % 10 == 0:
                print(f"[entry_bt] day {i+1}/{len(all_dates)} | rows so far: {inserted}", flush=True)

    db.close()
    print(f"[entry_bt] collection done — {inserted} rows total", flush=True)
    return analyze()


# ── Analysis ─────────────────────────────────────────────────────────────────
SWEEP_THRESHOLDS = [0, 25_000, 50_000, 100_000, 250_000, 500_000, 1_000_000, 2_000_000]
MONEYNESS_FILTERS = [
    ("any",      None,   None),    # all strikes
    ("near_atm", None,  1.5),      # moneyness within $1.5 of ATM
    ("atm_only", None,  0.5),      # moneyness within $0.5 (exact ATM)
]
WINDOW_FILTERS = ["both", "morning", "afternoon"]
MIN_PRICES = [0.0, 0.30, 0.50, 0.75, 1.00]


def analyze() -> dict:
    db  = _db()
    cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("""
        SELECT window_name, side, moneyness, entry_price,
               sweep_dollar, exit_reason, pnl_pct
        FROM zero_dte_entry_bt_raw
    """)
    rows = cur.fetchall()
    db.close()

    if not rows:
        return {"combos": [], "total_rows": 0, "status": "no_data"}

    results = []

    for sweep_thresh in SWEEP_THRESHOLDS:
        for (mname, _, mmax) in MONEYNESS_FILTERS:
            for window in WINDOW_FILTERS:
                for min_price in MIN_PRICES:
                    subset = []
                    for r in rows:
                        # window filter
                        if window != "both" and r["window_name"] != window:
                            continue
                        # moneyness filter (abs because ITM is negative)
                        if mmax is not None and abs(float(r["moneyness"])) > mmax:
                            continue
                        # sweep filter
                        if float(r["sweep_dollar"]) < sweep_thresh:
                            continue
                        # min price filter
                        if float(r["entry_price"]) < min_price:
                            continue
                        subset.append(r)

                    if len(subset) < 10:
                        continue

                    wins   = sum(1 for r in subset if r["exit_reason"] == "target")
                    losses = sum(1 for r in subset if r["exit_reason"] == "stop")
                    eods   = sum(1 for r in subset if r["exit_reason"] == "eod")
                    decisive = wins + losses
                    wr     = round(100.0 * wins / decisive, 1) if decisive > 0 else None
                    avg_pnl= round(sum(float(r["pnl_pct"]) for r in subset) / len(subset) * 100, 2)

                    results.append({
                        "sweep_thresh":  sweep_thresh,
                        "moneyness":     mname,
                        "window":        window,
                        "min_price":     min_price,
                        "n":             len(subset),
                        "wins":          wins,
                        "losses":        losses,
                        "eods":          eods,
                        "win_rate_pct":  wr,
                        "avg_pnl_pct":   avg_pnl,
                    })

    # Sort by win rate descending (then avg_pnl as tiebreak)
    results.sort(key=lambda x: (x["win_rate_pct"] or 0, x["avg_pnl_pct"]), reverse=True)

    return {
        "top_by_win_rate":   results[:20],
        "top_by_ev":         sorted(results, key=lambda x: x["avg_pnl_pct"], reverse=True)[:20],
        "total_rows":        len(rows),
        "total_combos_tested": len(results),
        "status": "complete",
    }


def get_status() -> dict:
    global _running
    try:
        db  = _db(); cur = db.cursor()
        cur.execute("SELECT COUNT(*) FROM zero_dte_entry_bt_progress")
        done = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM zero_dte_entry_bt_raw")
        rows = cur.fetchone()[0]
        db.close()
        status = "running" if _running else ("complete" if done > 0 else "not_started")
        return {"status": status, "days_processed": int(done), "raw_rows": int(rows)}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def start_background(lookback_days: int = DEFAULT_DAYS) -> dict:
    global _running
    with _lock:
        if _running:
            return {"started": False, "reason": "already_running"}
        _running = True

    def _run():
        global _running
        try:
            run_backtest(lookback_days)
        except Exception as e:
            print(f"[entry_bt] ERROR: {e}", flush=True)
            import traceback; traceback.print_exc()
        finally:
            _running = False

    threading.Thread(target=_run, daemon=True, name="zero_dte_entry_bt").start()
    return {"started": True, "lookback_days": lookback_days}
