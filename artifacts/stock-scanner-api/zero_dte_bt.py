#!/usr/bin/env python3
"""
zero_dte_bt.py — 0DTE Options Backtest via Polygon 1-min option aggregates.

For each trading day in the lookback window:
  Entry 1: ATM call + put at 10:05 ET (morning scan window)
  Entry 2: ATM call + put at 14:05 ET (afternoon scan window)

Tests all (PT, SL) combinations:
  PT ∈ {25%, 50%, 75%, 100%, 125%, 150%}
  SL ∈ {10%, 20%, 30%, 40%, 50%}

Intrabar exit rule: on a bar where BOTH stop and target are touched,
stop is assumed to trigger first (conservative).
EOD: 15:35 ET — close at last available bar close.

Results stored in three DB tables (resumable):
  zero_dte_bt_progress  — (date, window, side) completed combos
  zero_dte_bt_trades    — one row per (date, window, side, pt_pct, sl_pct)
  zero_dte_bt_reach     — one row per (date, window, side): max gain + reach flags
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
_RATE_SLEEP = 0.22          # ~4.5 req/sec — well under Polygon paid limit

PT_LEVELS   = [0.25, 0.50, 0.75, 1.00, 1.25, 1.50]
SL_LEVELS   = [0.10, 0.20, 0.30, 0.40, 0.50]
WINDOWS_ET  = [(10, 5, "morning"), (14, 5, "afternoon")]
EOD_HH, EOD_MM = 15, 35
DEFAULT_DAYS = 730          # ~2 calendar years

_bt_running = False
_bt_lock    = threading.Lock()


# ── DB ─────────────────────────────────────────────────────────────────────────
def _db():
    return psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=8)


def ensure_bt_tables():
    db = _db(); cur = db.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS zero_dte_bt_progress (
        id           SERIAL PRIMARY KEY,
        trade_date   DATE NOT NULL,
        window_name  TEXT NOT NULL,
        side         TEXT NOT NULL,
        completed_at TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE(trade_date, window_name, side)
    );
    CREATE TABLE IF NOT EXISTS zero_dte_bt_trades (
        id           SERIAL PRIMARY KEY,
        trade_date   DATE    NOT NULL,
        window_name  TEXT    NOT NULL,
        side         TEXT    NOT NULL,
        strike       NUMERIC NOT NULL,
        entry_price  NUMERIC NOT NULL,
        pt_pct       NUMERIC NOT NULL,
        sl_pct       NUMERIC NOT NULL,
        exit_reason  TEXT    NOT NULL,
        exit_price   NUMERIC NOT NULL,
        pnl_pct      NUMERIC NOT NULL,
        bars_elapsed INTEGER,
        UNIQUE(trade_date, window_name, side, pt_pct, sl_pct)
    );
    CREATE TABLE IF NOT EXISTS zero_dte_bt_reach (
        id               SERIAL PRIMARY KEY,
        trade_date       DATE    NOT NULL,
        window_name      TEXT    NOT NULL,
        side             TEXT    NOT NULL,
        entry_price      NUMERIC NOT NULL,
        max_gain_pct     NUMERIC NOT NULL,
        max_drawdown_pct NUMERIC NOT NULL,
        hit_25           BOOLEAN NOT NULL DEFAULT FALSE,
        hit_50           BOOLEAN NOT NULL DEFAULT FALSE,
        hit_75           BOOLEAN NOT NULL DEFAULT FALSE,
        hit_100          BOOLEAN NOT NULL DEFAULT FALSE,
        eod_pct          NUMERIC,
        UNIQUE(trade_date, window_name, side)
    );
    """)
    db.commit(); db.close()


# ── Time helpers ───────────────────────────────────────────────────────────────
def _et_to_ms(date_str: str, hour: int, minute: int) -> int:
    """Convert ET clock time on date_str to UTC milliseconds. Respects DST."""
    parts = [int(x) for x in date_str.split("-")]
    if _ET:
        dt = datetime.datetime(*parts, hour, minute, 0, tzinfo=_ET)
        return int(dt.timestamp() * 1000)
    # Fallback: crude DST rule (EDT Mar 2nd Sun - Nov 1st Sun ≈ UTC-4 else UTC-5)
    month = parts[1]
    offset = -4 if 3 <= month <= 10 else -5
    dt = datetime.datetime(*parts, hour, minute, 0,
                           tzinfo=datetime.timezone(datetime.timedelta(hours=offset)))
    return int(dt.timestamp() * 1000)


# ── Polygon helpers ────────────────────────────────────────────────────────────
def _poly_get(path: str) -> dict:
    if "?" in path:
        url = f"{_POLY_BASE}{path}&limit=1000"
    else:
        url = f"{_POLY_BASE}{path}?limit=1000"
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
    d = _poly_get(f"/v2/aggs/ticker/SPY/range/1/minute/{date_str}/{date_str}?adjusted=false")
    bars = d.get("results", [])
    _spy_cache[date_str] = bars
    time.sleep(_RATE_SLEEP)
    return bars


def _option_bars(date_str: str, side: str, strike: float) -> list:
    dt   = datetime.date.fromisoformat(date_str)
    yy   = dt.strftime("%y"); mm = dt.strftime("%m"); dd = dt.strftime("%d")
    cp   = "C" if side == "call" else "P"
    sk8  = f"{int(round(strike * 1000)):08d}"
    sym  = urllib.parse.quote(f"O:SPY{yy}{mm}{dd}{cp}{sk8}")
    d    = _poly_get(f"/v2/aggs/ticker/{sym}/range/1/minute/{date_str}/{date_str}?adjusted=false")
    time.sleep(_RATE_SLEEP)
    return d.get("results", [])


def _entry_price(bars: list, entry_ms: int) -> float | None:
    """Open of the first bar at or after entry_ms. This is our fill price."""
    for b in bars:
        if b["t"] >= entry_ms:
            v = float(b["o"])
            return v if v >= 0.01 else None
    return None


def _eod_price(bars: list, eod_ms: int, fallback: float) -> float:
    """Close of the last bar at or before eod_ms."""
    price = fallback
    for b in bars:
        if b["t"] <= eod_ms:
            price = float(b["c"])
    return price


# ── Trade simulation ───────────────────────────────────────────────────────────
def simulate_exit(bars: list, entry_ms: int, entry_price: float,
                  pt_pct: float, sl_pct: float, eod_ms: int) -> dict:
    """
    Walk bars from entry_ms → eod_ms; return first exit event.
    Conservative rule: stop fires before target on ambiguous intrabar.
    """
    target  = entry_price * (1 + pt_pct)
    stop    = entry_price * (1 - sl_pct)
    found   = False
    elapsed = 0

    for b in bars:
        ts = b["t"]
        if ts > eod_ms:
            break
        if not found:
            if ts >= entry_ms:
                found = True
                # Entry at open — check the entry bar itself (h/l happen after open)
            else:
                continue

        lo = float(b["l"]); hi = float(b["h"])
        elapsed += 1

        if lo <= stop:                            # stop first (conservative)
            return {"exit_reason": "stop",   "exit_price": round(stop, 4),
                    "pnl_pct": round(-sl_pct, 6), "bars_elapsed": elapsed}
        if hi >= target:
            return {"exit_reason": "target", "exit_price": round(target, 4),
                    "pnl_pct": round(pt_pct,  6), "bars_elapsed": elapsed}

    ep   = _eod_price(bars, eod_ms, entry_price)
    pnl  = (ep - entry_price) / entry_price if entry_price > 0 else 0.0
    return {"exit_reason": "eod", "exit_price": round(ep, 4),
            "pnl_pct": round(pnl, 6), "bars_elapsed": elapsed}


def compute_reach(bars: list, entry_ms: int, entry_price: float, eod_ms: int) -> dict:
    """Max intraday gain/drawdown from entry, hit flags, EOD P&L."""
    max_g = 0.0; max_d = 0.0; eod_p = 0.0; found = False
    for b in bars:
        ts = b["t"]
        if ts > eod_ms:
            break
        if not found:
            if ts >= entry_ms:
                found = True
            else:
                continue
        hi_p = (float(b["h"]) - entry_price) / entry_price
        lo_p = (float(b["l"]) - entry_price) / entry_price
        max_g = max(max_g, hi_p)
        max_d = min(max_d, lo_p)
        eod_p = (float(b["c"]) - entry_price) / entry_price
    return {
        "max_gain_pct":     round(max_g, 6),
        "max_drawdown_pct": round(max_d, 6),
        "hit_25":  max_g >= 0.25,
        "hit_50":  max_g >= 0.50,
        "hit_75":  max_g >= 0.75,
        "hit_100": max_g >= 1.00,
        "eod_pct": round(eod_p, 6),
    }


# ── Main backtest ──────────────────────────────────────────────────────────────
def run_backtest(lookback_days: int = DEFAULT_DAYS, status_fn=None) -> dict:
    """
    Run or resume the 0DTE options backtest.
    Saves progress to DB after every (date, window, side) triplet.
    """
    ensure_bt_tables()
    db = _db(); cur = db.cursor()

    today = datetime.date.today()
    start = today - datetime.timedelta(days=lookback_days)

    # All Mon–Fri dates in range (excluding today — 0DTE not yet expired)
    all_dates = []
    d = start
    while d < today:
        if d.weekday() < 5:
            all_dates.append(d.isoformat())
        d += datetime.timedelta(days=1)

    # Load already-completed keys (resume support)
    cur.execute(
        "SELECT trade_date::text, window_name, side FROM zero_dte_bt_progress")
    done: set[tuple] = set(tuple(r) for r in cur.fetchall())

    total = len(all_dates)
    if status_fn:
        status_fn(f"start|{total}|{lookback_days}")

    for i, date_str in enumerate(all_dates):
        if status_fn and i % 20 == 0:
            pct = round(100 * i / total)
            status_fn(f"progress|{i}|{total}|{pct}")

        eod_ms   = _et_to_ms(date_str, EOD_HH, EOD_MM)
        spy_bars = _spy_bars(date_str)
        if len(spy_bars) < 10:
            # Holiday or closed day — mark all combos done
            for (_, _, wname) in WINDOWS_ET:
                for side in ("call", "put"):
                    k = (date_str, wname, side)
                    if k not in done:
                        cur.execute(
                            "INSERT INTO zero_dte_bt_progress(trade_date,window_name,side) "
                            "VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                            (date_str, wname, side))
                        done.add(k)
            db.commit()
            continue

        for (wh, wm, wname) in WINDOWS_ET:
            entry_ms = _et_to_ms(date_str, wh, wm)

            # SPY underlying price at entry bar → ATM strike
            spy_price = None
            for b in spy_bars:
                if b["t"] >= entry_ms:
                    spy_price = float(b["o"])
                    break
            if spy_price is None:
                continue
            strike = round(spy_price)

            for side in ("call", "put"):
                k = (date_str, wname, side)
                if k in done:
                    continue

                opt_bars = _option_bars(date_str, side, float(strike))

                # Mark done if no data
                if len(opt_bars) < 5:
                    cur.execute(
                        "INSERT INTO zero_dte_bt_progress(trade_date,window_name,side) "
                        "VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                        (date_str, wname, side))
                    db.commit()
                    done.add(k)
                    continue

                ep = _entry_price(opt_bars, entry_ms)
                if ep is None:
                    cur.execute(
                        "INSERT INTO zero_dte_bt_progress(trade_date,window_name,side) "
                        "VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                        (date_str, wname, side))
                    db.commit()
                    done.add(k)
                    continue

                # Reach stats (once per date/window/side)
                reach = compute_reach(opt_bars, entry_ms, ep, eod_ms)
                cur.execute("""
                    INSERT INTO zero_dte_bt_reach
                        (trade_date,window_name,side,entry_price,
                         max_gain_pct,max_drawdown_pct,
                         hit_25,hit_50,hit_75,hit_100,eod_pct)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(trade_date,window_name,side) DO NOTHING
                """, (date_str, wname, side, ep,
                      reach["max_gain_pct"], reach["max_drawdown_pct"],
                      reach["hit_25"], reach["hit_50"],
                      reach["hit_75"], reach["hit_100"],
                      reach["eod_pct"]))

                # Simulate all (PT, SL) combos
                for pt in PT_LEVELS:
                    for sl in SL_LEVELS:
                        res = simulate_exit(opt_bars, entry_ms, ep, pt, sl, eod_ms)
                        cur.execute("""
                            INSERT INTO zero_dte_bt_trades
                                (trade_date,window_name,side,strike,entry_price,
                                 pt_pct,sl_pct,exit_reason,exit_price,pnl_pct,bars_elapsed)
                            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                            ON CONFLICT(trade_date,window_name,side,pt_pct,sl_pct) DO NOTHING
                        """, (date_str, wname, side, strike, ep,
                              pt, sl,
                              res["exit_reason"], res["exit_price"],
                              res["pnl_pct"],     res["bars_elapsed"]))

                cur.execute(
                    "INSERT INTO zero_dte_bt_progress(trade_date,window_name,side) "
                    "VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                    (date_str, wname, side))
                db.commit()
                done.add(k)

    db.close()
    if status_fn:
        status_fn("done")
    return get_results()


# ── Results aggregation ────────────────────────────────────────────────────────
def _dec(v):
    return float(v) if isinstance(v, decimal.Decimal) else v


def get_results() -> dict:
    try:
        db  = _db()
        cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # ── PT/SL combination stats ──────────────────────────────────────────
        cur.execute("""
            SELECT
                pt_pct, sl_pct, side,
                COUNT(*)                                                  AS total,
                COUNT(*) FILTER (WHERE exit_reason='target')              AS wins,
                COUNT(*) FILTER (WHERE exit_reason='stop')                AS losses,
                COUNT(*) FILTER (WHERE exit_reason='eod')                 AS eod_exits,
                ROUND(
                  100.0 * COUNT(*) FILTER (WHERE exit_reason='target')
                  / NULLIF(COUNT(*) FILTER (WHERE exit_reason IN ('target','stop')),0)
                , 1)                                                       AS win_rate_pct,
                ROUND(AVG(pnl_pct)*100, 2)                                AS avg_pnl_pct,
                ROUND(AVG(pnl_pct) FILTER (WHERE exit_reason='target')*100,2) AS avg_win_pct,
                ROUND(AVG(pnl_pct) FILTER (WHERE exit_reason='stop')*100, 2)  AS avg_loss_pct,
                ROUND(AVG(pnl_pct) FILTER (WHERE exit_reason='eod')*100,  2)  AS avg_eod_pct,
                ROUND(AVG(bars_elapsed),1)                                AS avg_bars
            FROM zero_dte_bt_trades
            GROUP BY pt_pct, sl_pct, side
            ORDER BY side, pt_pct, sl_pct
        """)
        combos = [{k: _dec(v) for k, v in dict(r).items()} for r in cur.fetchall()]

        # ── Reach rates ──────────────────────────────────────────────────────
        cur.execute("""
            SELECT
                side,
                COUNT(*)                                                   AS total_obs,
                ROUND(AVG(max_gain_pct)*100, 1)                            AS avg_max_gain_pct,
                ROUND(AVG(max_drawdown_pct)*100, 1)                        AS avg_max_drawdown_pct,
                ROUND(100.0*COUNT(*) FILTER(WHERE hit_25) /NULLIF(COUNT(*),0),1) AS pct_reaching_25,
                ROUND(100.0*COUNT(*) FILTER(WHERE hit_50) /NULLIF(COUNT(*),0),1) AS pct_reaching_50,
                ROUND(100.0*COUNT(*) FILTER(WHERE hit_75) /NULLIF(COUNT(*),0),1) AS pct_reaching_75,
                ROUND(100.0*COUNT(*) FILTER(WHERE hit_100)/NULLIF(COUNT(*),0),1) AS pct_reaching_100,
                ROUND(AVG(eod_pct)*100, 1)                                 AS avg_eod_pct
            FROM zero_dte_bt_reach
            GROUP BY side
            ORDER BY side
        """)
        reach = [{k: _dec(v) for k, v in dict(r).items()} for r in cur.fetchall()]

        # ── Progress ─────────────────────────────────────────────────────────
        cur.execute("SELECT COUNT(*) AS n FROM zero_dte_bt_progress")
        done_n = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM zero_dte_bt_trades")
        trade_n = cur.fetchone()["n"]

        db.close()
        return {
            "combos":      combos,
            "reach_rates": reach,
            "progress": {
                "completed_combinations": int(done_n),
                "total_trade_rows":       int(trade_n),
            },
        }
    except Exception as e:
        return {"combos": [], "reach_rates": [], "progress": {}, "error": str(e)}


def get_status() -> dict:
    global _bt_running
    try:
        db  = _db(); cur = db.cursor()
        cur.execute("SELECT COUNT(*) FROM zero_dte_bt_progress")
        done = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM zero_dte_bt_trades")
        trades = cur.fetchone()[0]
        db.close()
        status = "running" if _bt_running else ("complete" if done > 0 else "not_started")
        return {
            "status": status,
            "completed_combinations": int(done),
            "total_trade_rows": int(trades),
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


def start_background(lookback_days: int = DEFAULT_DAYS) -> dict:
    global _bt_running
    with _bt_lock:
        if _bt_running:
            return {"started": False, "reason": "already_running"}
        _bt_running = True

    def _run():
        global _bt_running
        try:
            run_backtest(lookback_days, status_fn=lambda m: print(f"[0dte_bt] {m}"))
        except Exception as e:
            print(f"[0dte_bt] ERROR: {e}")
            import traceback; traceback.print_exc()
        finally:
            _bt_running = False

    t = threading.Thread(target=_run, daemon=True, name="zero_dte_bt")
    t.start()
    return {"started": True, "lookback_days": lookback_days}


if __name__ == "__main__":
    import sys
    days = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DAYS
    print(f"[0dte_bt] Standalone run — {days}-day lookback")
    ensure_bt_tables()
    res = run_backtest(days, status_fn=lambda m: print(f"  {m}"))

    print("\n=== REACH RATES ===")
    for r in res.get("reach_rates", []):
        print(f"  {r['side']:4s}  obs={r['total_obs']}  "
              f"+25%={r['pct_reaching_25']}%  +50%={r['pct_reaching_50']}%  "
              f"+75%={r['pct_reaching_75']}%  +100%={r['pct_reaching_100']}%  "
              f"avg_eod={r['avg_eod_pct']}%")

    print("\n=== BEST PT/SL BY WIN RATE (calls, decisive exits only) ===")
    calls = [c for c in res.get("combos", []) if c["side"] == "call"]
    top   = sorted(calls, key=lambda x: x.get("win_rate_pct") or 0, reverse=True)[:8]
    for r in top:
        print(f"  PT={r['pt_pct']*100:.0f}%  SL={r['sl_pct']*100:.0f}%  "
              f"WR={r['win_rate_pct']}%  avg_pnl={r['avg_pnl_pct']}%  "
              f"n={r['total']}")
