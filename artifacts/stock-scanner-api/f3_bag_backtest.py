#!/usr/bin/env python3
"""
F3 SPY 0DTE — another bag/back test (Polygon-only).

Matches live Pattern Lab / aim_f3_spy_0dte rules:
  PM direction → ORB 09:30–09:44 → breakout with PM → ATM long call/put
  $200 notional, −65% premium stop, else exit 16:00. No profit target.

Real Polygon 1-min option aggregates only (no synthetic P&L).
No Tradier required — SPY daily/intraday also from Polygon.

HOW THIS IS "TOLD" TO AIEM (no chat inbox):
  Place/run under artifacts/stock-scanner-api/ on the stock-api host
  where POLYGON_API_KEY is set:
      python f3_bag_backtest.py --days 63 --stop 0.65

Default window ~3 months (63 calendar days), same cadence as the last
Gamma Blast real run after that pattern was rejected.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

POLYGON_BASE = "https://api.polygon.io"
RATE_SLEEP = float(os.environ.get("F3_BAG_RATE_SLEEP", "0.35"))
CACHE_DIR = Path(os.environ.get("F3_BAG_CACHE", "/tmp/f3_bag_cache"))
ARCHIVE_DIR_NAME = "f3-bag-backtest"
TRADE_SIZE = 200.0


def _api_key() -> str:
    k = os.environ.get("POLYGON_API_KEY") or os.environ.get("POLYGON_KEY") or ""
    if not k or k.startswith("YOUR_"):
        print(
            "POLYGON_API_KEY not set — paste/export it, or run on the AIEM host that has it.",
            file=sys.stderr,
        )
        sys.exit(2)
    return k


def _poly_get(path: str, params: dict, retries: int = 8) -> dict:
    params = dict(params)
    params["apiKey"] = _api_key()
    url = f"{POLYGON_BASE}{path}?{urllib.parse.urlencode(params)}"
    delay = max(RATE_SLEEP, 1.0)
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:400]
            if e.code == 429 and attempt < retries - 1:
                wait = min(90.0, delay * (2 ** attempt))
                print(f"  [f3_bag] 429 — sleep {wait:.0f}s then retry ({attempt+1}/{retries})")
                time.sleep(wait)
                continue
            return {"status": "ERROR", "error": f"HTTP {e.code}: {body}", "results": []}
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(delay * (attempt + 1))
                continue
            return {"status": "ERROR", "error": str(e), "results": []}
    return {"status": "ERROR", "error": "retries exhausted", "results": []}

def _et_minute(ts_ms: int) -> int:
    utc_dt = datetime.utcfromtimestamp(int(ts_ms) / 1000)
    # Rough EST/EDT: Nov–Mar winter. Good enough for RTH bucketing;
    # Polygon bars are already session-aligned for SPY.
    is_winter = utc_dt.month in (11, 12, 1, 2, 3)
    et_dt = utc_dt - timedelta(hours=5 if is_winter else 4)
    return et_dt.hour * 60 + et_dt.minute


def _et_date_str(ts_ms: int) -> str:
    utc_dt = datetime.utcfromtimestamp(int(ts_ms) / 1000)
    is_winter = utc_dt.month in (11, 12, 1, 2, 3)
    et_dt = utc_dt - timedelta(hours=5 if is_winter else 4)
    return et_dt.strftime("%Y-%m-%d")


def get_atm_ticker(spot: float, exp_date: date, is_call: bool) -> str:
    s = f"{int(round(spot) * 1000):08d}"
    cp = "C" if is_call else "P"
    return f"O:SPY{exp_date.strftime('%y%m%d')}{cp}{s}"


def _month_edges(start: date, end: date) -> list[date]:
    """Inclusive chunk edges for month-sized Polygon pulls (minute bars need this)."""
    edges = [start]
    y, m = start.year, start.month
    while True:
        m += 1
        if m > 12:
            m = 1
            y += 1
        nxt = date(y, m, 1)
        if nxt >= end:
            break
        if nxt > start:
            edges.append(nxt)
    if edges[-1] != end:
        edges.append(end)
    return edges


def _fetch_aggs_range(start: date, end: date, multiplier: int, timespan: str) -> list:
    """One Polygon range pull with next_url pagination."""
    all_rows: list = []
    path = (
        f"/v2/aggs/ticker/SPY/range/{multiplier}/{timespan}/"
        f"{start.isoformat()}/{end.isoformat()}"
    )
    data = _poly_get(path, {"adjusted": "true", "sort": "asc", "limit": 50000})
    rows = data.get("results") or []
    all_rows.extend(rows)
    if not rows and data.get("error"):
        print(f"  [f3_bag] {start}→{end} error={data.get('error')}")
    next_url = data.get("next_url")
    while next_url:
        time.sleep(RATE_SLEEP)
        sep = "&" if "?" in next_url else "?"
        url = f"{next_url}{sep}apiKey={urllib.parse.quote(_api_key())}"
        data = None
        for attempt in range(8):
            try:
                with urllib.request.urlopen(url, timeout=60) as resp:
                    data = json.loads(resp.read().decode())
                break
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 7:
                    wait = min(90.0, max(RATE_SLEEP, 1.0) * (2 ** attempt))
                    print(f"  [f3_bag] next_url 429 — sleep {wait:.0f}s")
                    time.sleep(wait)
                    continue
                print(f"  [f3_bag] next_url failed: HTTP {e.code}")
                data = {"results": []}
                break
            except Exception as e:
                print(f"  [f3_bag] next_url failed: {e}")
                data = {"results": []}
                break
        more = (data or {}).get("results") or []
        all_rows.extend(more)
        print(f"  +{len(more)} total={len(all_rows)}")
        next_url = (data or {}).get("next_url")
    return all_rows


def fetch_spy_aggs(start: date, end: date, multiplier: int, timespan: str) -> list:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"SPY_{multiplier}{timespan}_{start}_{end}.json"
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text())
            # Ignore empty caches from failed wide-range pulls.
            if cached:
                return cached
        except Exception:
            pass

    print(f"[f3_bag] fetching SPY {multiplier}/{timespan} {start}→{end} …")
    all_rows: list = []
    # Day bars can be one shot; minute bars must be month-chunked or Polygon
    # returns empty / errors on multi-year windows.
    if timespan == "day":
        chunks = [(start, end)]
    else:
        edges = _month_edges(start, end)
        chunks = [(edges[i], edges[i + 1]) for i in range(len(edges) - 1)]

    empty_chunks = 0
    for a, b in chunks:
        if a >= b:
            continue
        print(f"  chunk {a} → {b}…")
        rows = _fetch_aggs_range(a, b, multiplier, timespan)
        if not rows:
            empty_chunks += 1
        all_rows.extend(rows)
        print(f"    → {len(rows)} (running {len(all_rows)})")
        time.sleep(RATE_SLEEP)

    # Only persist a complete-looking cache (avoid locking in partial 429 pulls).
    if empty_chunks == 0 or (timespan == "day" and all_rows):
        cache_path.write_text(json.dumps(all_rows))
        print(f"[f3_bag] cached {len(all_rows)} bars → {cache_path}")
    else:
        print(
            f"[f3_bag] NOT caching incomplete pull "
            f"({empty_chunks} empty chunks, {len(all_rows)} bars)"
        )
    return all_rows

def organize_intraday(raw_bars: list):
    reg: dict[str, list] = defaultdict(list)
    pm: dict[str, list] = defaultdict(list)
    for b in raw_bars:
        try:
            ds = _et_date_str(b["t"])
            mn = _et_minute(b["t"])
            bd = {
                "minute": mn,
                "open": float(b["o"]),
                "high": float(b["h"]),
                "low": float(b["l"]),
                "close": float(b["c"]),
            }
            if 570 <= mn < 960:  # 09:30–16:00
                reg[ds].append(bd)
            elif 240 <= mn < 570:  # 04:00–09:30
                pm[ds].append(bd)
        except Exception:
            continue
    for d in reg:
        reg[d].sort(key=lambda x: x["minute"])
    for d in pm:
        pm[d].sort(key=lambda x: x["minute"])
    print(f"[f3_bag] organized {len(reg)} RTH days, {len(pm)} PM days")
    return reg, pm


def build_daily_map(daily_bars: list) -> dict:
    days = sorted(daily_bars, key=lambda b: b["t"])
    dm = {}
    prev_close = None
    for b in days:
        ds = _et_date_str(b["t"])
        dm[ds] = {
            "open": float(b["o"]),
            "close": float(b["c"]),
            "prev_close": prev_close,
        }
        prev_close = float(b["c"])
    print(f"[f3_bag] daily map: {len(dm)} days")
    return dm


def fetch_option_bars(ticker: str, date_str: str) -> list:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    safe = ticker.replace(":", "_")
    cache_path = CACHE_DIR / f"opt_{safe}_{date_str}.json"
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text())
        except Exception:
            pass

    sym = urllib.parse.quote(ticker)
    data = _poly_get(
        f"/v2/aggs/ticker/{sym}/range/1/minute/{date_str}/{date_str}",
        {"adjusted": "true", "sort": "asc", "limit": 5000},
    )
    time.sleep(RATE_SLEEP)
    rows = data.get("results") or []
    cache_path.write_text(json.dumps(rows))
    return rows


def apply_stop(day_bars: list, entry_minute: int, entry_px: float, stop_pct: float):
    stop_price = entry_px * (1.0 - stop_pct)
    post = [b for b in day_bars if _et_minute(b["t"]) > entry_minute]
    if not post:
        return entry_px, False, entry_minute
    for bar in post:
        if float(bar["l"]) <= stop_price:
            return stop_price, True, _et_minute(bar["t"])
    eod = min(post, key=lambda b: abs(_et_minute(b["t"]) - 960))
    return float(eod["c"]), False, _et_minute(eod["t"])


def dollar_pnl(entry_px: float, exit_px: float) -> float:
    contracts = TRADE_SIZE / (entry_px * 100.0)
    return round((exit_px - entry_px) * 100.0 * contracts, 2)


def run(daily_map: dict, regular_bars: dict, premarket_bars: dict, stop_levels: list[float]):
    results = []
    skipped = []

    for date_str in sorted(daily_map.keys()):
        daily = daily_map[date_str]
        reg_bs = regular_bars.get(date_str, [])
        pm_bs = premarket_bars.get(date_str, [])

        if not reg_bs or daily.get("prev_close") is None or len(reg_bs) < 10:
            continue
        if not pm_bs:
            skipped.append({"date": date_str, "reason": "no_pm"})
            continue

        pm_dir = 1 if pm_bs[-1]["close"] > pm_bs[0]["open"] else -1
        orb_bs = [b for b in reg_bs if b["minute"] < 585]  # before 09:45
        if not orb_bs:
            continue
        orb_high = max(b["high"] for b in orb_bs)
        orb_low = min(b["low"] for b in orb_bs)
        post_orb = [b for b in reg_bs if b["minute"] >= 585]
        if not post_orb:
            continue

        entry_bar = None
        for bar in post_orb:
            if pm_dir == 1 and bar["close"] > orb_high:
                entry_bar = bar
                break
            if pm_dir == -1 and bar["close"] < orb_low:
                entry_bar = bar
                break
        if entry_bar is None:
            continue

        spy_price = entry_bar["close"]
        is_call = pm_dir == 1
        trade_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        opt_ticker = get_atm_ticker(spy_price, trade_date, is_call)
        entry_minute = entry_bar["minute"]

        day_bars = fetch_option_bars(opt_ticker, date_str)
        if not day_bars:
            skipped.append({"date": date_str, "reason": "no_option_bars", "ticker": opt_ticker})
            continue

        entry_opt = min(day_bars, key=lambda b: abs(_et_minute(b["t"]) - entry_minute))
        entry_px = float(entry_opt["c"])
        if entry_px <= 0:
            skipped.append({"date": date_str, "reason": "zero_entry"})
            continue

        post_opt = [b for b in day_bars if _et_minute(b["t"]) > entry_minute]
        if post_opt:
            eod_bar = min(post_opt, key=lambda b: abs(_et_minute(b["t"]) - 960))
            nostop_exit = float(eod_bar["c"])
        else:
            nostop_exit = entry_px
        nostop_pnl = dollar_pnl(entry_px, nostop_exit)

        stop_pnls = {}
        stop_hits = {}
        stop_exits = {}
        for sl in stop_levels:
            exit_px, stopped, exit_mn = apply_stop(day_bars, entry_minute, entry_px, sl)
            stop_pnls[str(sl)] = dollar_pnl(entry_px, exit_px)
            stop_hits[str(sl)] = stopped
            stop_exits[str(sl)] = round(exit_px, 4)

        results.append(
            {
                "date": date_str,
                "direction": "CALL" if is_call else "PUT",
                "ticker": opt_ticker,
                "spy_entry": round(spy_price, 2),
                "entry_px": round(entry_px, 4),
                "entry_minute": entry_minute,
                "nostop_exit": round(nostop_exit, 4),
                "nostop_pnl": nostop_pnl,
                "stop_pnls": stop_pnls,
                "stop_hits": stop_hits,
                "stop_exits": stop_exits,
            }
        )
        n_done = len(results) + len(skipped)
        if n_done % 10 == 0:
            print(f"  … {n_done} processed ({len(results)} trades, {len(skipped)} skipped)")

    print(f"[f3_bag] → {len(results)} trades | {len(skipped)} skipped")
    return results, skipped


def summarize(results: list, stop_levels: list[float]) -> dict:
    n = len(results)
    if n == 0:
        return {"trades": 0}

    def tot(sl: Optional[float]) -> float:
        if sl is None:
            return sum(r["nostop_pnl"] for r in results)
        return sum(r["stop_pnls"][str(sl)] for r in results)

    def wr(sl: Optional[float]) -> float:
        if sl is None:
            wins = sum(1 for r in results if r["nostop_pnl"] > 0)
        else:
            wins = sum(1 for r in results if r["stop_pnls"][str(sl)] > 0)
        return wins / n * 100.0

    out: dict[str, Any] = {
        "trades": n,
        "notional_per_trade": TRADE_SIZE,
        "no_stop": {
            "total_pnl": round(tot(None), 2),
            "win_rate_pct": round(wr(None), 1),
            "cash_on_cash_pct": round(tot(None) / (TRADE_SIZE * n) * 100.0, 1),
        },
        "stops": {},
    }
    for sl in stop_levels:
        hits = sum(1 for r in results if r["stop_hits"][str(sl)])
        out["stops"][f"{int(sl * 100)}pct"] = {
            "stop_pct": sl,
            "total_pnl": round(tot(sl), 2),
            "win_rate_pct": round(wr(sl), 1),
            "cash_on_cash_pct": round(tot(sl) / (TRADE_SIZE * n) * 100.0, 1),
            "stops_triggered": hits,
            "vs_nostop": round(tot(sl) - tot(None), 2),
        }
    return out


def print_report(results: list, stop_levels: list[float], summary: dict):
    print()
    print("=" * 72)
    print("  F3 BAG/BACK TEST — REAL POLYGON 0DTE OPTION BARS")
    print(f"  {summary.get('trades', 0)} trades | ${TRADE_SIZE:.0f}/trade")
    print("=" * 72)
    if not results:
        print("  No trades.")
        return

    ns = summary["no_stop"]
    print(
        f"  No stop:   P&L ${ns['total_pnl']:+,.2f}  "
        f"WR {ns['win_rate_pct']:.1f}%  CoC {ns['cash_on_cash_pct']:+.1f}%"
    )
    for key, st in summary["stops"].items():
        print(
            f"  Stop {int(st['stop_pct']*100)}%:  P&L ${st['total_pnl']:+,.2f}  "
            f"WR {st['win_rate_pct']:.1f}%  CoC {st['cash_on_cash_pct']:+.1f}%  "
            f"hits={st['stops_triggered']}  vs_nostop ${st['vs_nostop']:+,.2f}"
        )

    primary = stop_levels[0] if stop_levels else None
    print()
    print(f"  Per-trade (primary stop {int((primary or 0)*100)}%):")
    print(
        f"  {'Date':<12} {'Dir':<5} {'EntPx':>7} {'NoStop$':>9} "
        f"{'Stop$':>9} {'Hit?':>5}"
    )
    print("  " + "─" * 55)
    for r in results:
        hit = "Y" if primary is not None and r["stop_hits"][str(primary)] else "—"
        stop_pnl = r["stop_pnls"][str(primary)] if primary is not None else 0.0
        print(
            f"  {r['date']:<12} {r['direction']:<5} "
            f"${r['entry_px']:>6.3f} ${r['nostop_pnl']:>+8.2f} "
            f"${stop_pnl:>+8.2f} {hit:>5}"
        )


def archive_root() -> Path:
    root = Path(__file__).resolve().parents[2] / "docs" / "verification" / ARCHIVE_DIR_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def save_archive(payload: dict, tag: str) -> Path:
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    path = archive_root() / f"f3-bag-{tag}-{ts}.json"
    path.write_text(json.dumps(payload, indent=2))
    latest = archive_root() / "LATEST.json"
    latest.write_text(json.dumps(payload, indent=2))
    (archive_root() / "RUN_INDEX.jsonl").open("a").write(
        json.dumps({"ts": ts, "tag": tag, "path": path.name, "summary": payload.get("summary")})
        + "\n"
    )
    print(f"[f3_bag] archived → {path}")
    return path


def main():
    ap = argparse.ArgumentParser(description="F3 SPY 0DTE bag/back test (Polygon real options)")
    ap.add_argument("--days", type=int, default=63, help="Calendar days lookback (default 63 ≈ 3m)")
    ap.add_argument(
        "--stop",
        default="0.65",
        help="Comma-separated stop fractions (default 0.65 = live Pattern Lab rule)",
    )
    ap.add_argument("--start", default="", help="Optional YYYY-MM-DD start (overrides --days)")
    ap.add_argument("--end", default="", help="Optional YYYY-MM-DD end (default today)")
    args = ap.parse_args()

    end_date = date.fromisoformat(args.end) if args.end else date.today()
    start_date = (
        date.fromisoformat(args.start)
        if args.start
        else end_date - timedelta(days=args.days)
    )
    stop_levels = [float(x) for x in args.stop.split(",") if x.strip()]

    # Force key check early
    _api_key()

    print()
    print("=" * 72)
    print("  F3 BAG/BACK TEST — REAL POLYGON OPTION BARS")
    print(f"  {start_date} → {end_date}")
    print(f"  Stops: {[f'{int(s*100)}%' for s in stop_levels]}")
    print("=" * 72)

    daily_raw = fetch_spy_aggs(start_date - timedelta(days=5), end_date, 1, "day")
    daily_map = build_daily_map(daily_raw)
    # Prefer 1-min for PM+ORB fidelity; fall back message if empty
    intra = fetch_spy_aggs(start_date, end_date, 1, "minute")
    if len(intra) < 100:
        print("[f3_bag] WARNING: few 1-min bars; trying 5-min …")
        intra = fetch_spy_aggs(start_date, end_date, 5, "minute")
    reg, pm = organize_intraday(intra)

    # Clip daily_map to window
    daily_map = {k: v for k, v in daily_map.items() if start_date.isoformat() <= k <= end_date.isoformat()}

    results, skipped = run(daily_map, reg, pm, stop_levels)
    summary = summarize(results, stop_levels)
    print_report(results, stop_levels, summary)

    payload = {
        "strategy": "F3_SPY_0DTE",
        "pricing": "polygon_real_option_aggs",
        "window": {"start": start_date.isoformat(), "end": end_date.isoformat()},
        "stops": stop_levels,
        "rules": {
            "notional": TRADE_SIZE,
            "orb": "09:30-09:44",
            "entry_from": "09:45",
            "exit": "16:00 or stop",
            "primary_stop": 0.65,
        },
        "summary": summary,
        "trades": results,
        "skipped": skipped,
    }
    save_archive(payload, tag=f"{args.days}d-sl{'-'.join(str(int(s*100)) for s in stop_levels)}")
    print()
    if summary.get("trades", 0) == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
