#!/usr/bin/env python3
"""
Gamma Blast Strategy — AIEM / Polygon backtest handoff
Directive_GammaBlast_Backtest_2026-08-07

HOW THIS IS "TOLD" TO AIEM (no chat inbox):
  Place/run this script under artifacts/stock-scanner-api/ on the stock-api
  host (Replit) where POLYGON_API_KEY is set:
      python gamma_blast_backtest.py --years 2 --mode synthetic --sweep-tp 1.5,2,3 --sweep-sl 0.60,0.65,0.75

Strategy (from user paste, fixes retained):
  - SPY 0DTE directional option after range compression + straddle expansion
  - Risk $100/trade, TP/SL configurable, time stop 45m
  - Entry window 09:30–14:30 ET

Pricing modes:
  - synthetic : Black-Scholes from underlying bars only — LOGIC sanity check.
                Do NOT report as real P&L.
  - real      : Polygon 1-min option aggregates for ATM 0DTE call/put.
                2-year real sweeps are extremely API-heavy; prefer synthetic
                for ranking knobs, then spot-check real on a shorter window.

Does NOT place live broker orders. Does NOT touch D1/D2/D3 or Pattern Lab ledgers.
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
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

try:
    from scipy.stats import norm
except ImportError:
    print("ERROR: scipy required (Black-Scholes synthetic mode).", file=sys.stderr)
    sys.exit(2)

try:
    from zoneinfo import ZoneInfo

    _ET = ZoneInfo("America/New_York")
except ImportError:
    _ET = None

POLYGON_BASE = "https://api.polygon.io"
# Free/basic Polygon tiers are ~5 req/min — keep well under that for long pulls.
RATE_SLEEP = float(os.environ.get("GAMMA_BLAST_RATE_SLEEP", "13"))
CACHE_DIR = Path(os.environ.get("GAMMA_BLAST_CACHE", "/tmp/gamma_blast_cache"))

# Baseline knobs — keep every run's full config + trade log so we can
# compare variable sweeps later (do not discard results).
DEFAULT_CONFIG = {
    "ticker": "SPY",
    "risk_per_trade": 100.0,
    "take_profit_multiplier": 3.0,
    "stop_loss_pct": 0.50,
    "range_threshold": 0.01,
    "breakout_threshold": 0.05,
    "time_stop_minutes": 45,
    "entry_start": "09:30",
    "entry_end": "14:30",
    "risk_free_rate": 0.05,
    "iv_estimate": 0.20,
}

CONFIG = dict(DEFAULT_CONFIG)
ARCHIVE_DIR_NAME = "gamma-blast"


def _api_key() -> str:
    k = os.environ.get("POLYGON_API_KEY") or os.environ.get("POLYGON_KEY") or ""
    if not k:
        raise SystemExit(
            "POLYGON_API_KEY not set — run on the stock-api / AIEM host that has the key."
        )
    return k


def _poly_get(path: str, params: Optional[dict] = None) -> dict:
    params = dict(params or {})
    params["apiKey"] = _api_key()
    url = f"{POLYGON_BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "aiem-gamma-blast/1.0"})
    for attempt in range(8):
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                pass
            if e.code == 429:
                wait = min(60, 2 ** attempt + 1)
                print(f"  [poly] 429 rate limit — sleep {wait}s (attempt {attempt+1}/8)")
                time.sleep(wait)
                continue
            return {
                "status": "ERROR",
                "error": f"HTTP {e.code}: {body or e.reason}",
                "results": [],
            }
        except Exception as e:
            if attempt < 3:
                time.sleep(1.5 * (attempt + 1))
                continue
            return {"status": "ERROR", "error": str(e), "results": []}
    return {"status": "ERROR", "error": "rate_limited_exhausted", "results": []}


def _bars_from_results(rows: list) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["t"], unit="ms", utc=True).dt.tz_convert(
        "America/New_York"
    )
    df = df.set_index("timestamp").rename(
        columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"}
    )
    df = df.between_time("09:30", "16:00")
    return df[["Open", "High", "Low", "Close", "Volume"]]


def fetch_spy_1m(day: date) -> pd.DataFrame:
    """Polygon SPY 1-min bars for one session (ET calendar date)."""
    d = day.isoformat()
    data = _poly_get(
        f"/v2/aggs/ticker/SPY/range/1/minute/{d}/{d}",
        {"adjusted": "true", "sort": "asc", "limit": 50000},
    )
    time.sleep(RATE_SLEEP)
    if data.get("status") not in ("OK", "DELAYED", None) and not data.get("results"):
        err = data.get("error") or data.get("status")
        print(f"  [poly] {d}: {err}")
    return _bars_from_results(data.get("results") or [])


def fetch_spy_range_cached(start: date, end: date) -> pd.DataFrame:
    """
    Pull SPY 1-min bars for [start, end] in month chunks; disk-cache to avoid
    re-downloading across sweep variants.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"SPY_1m_{start.isoformat()}_{end.isoformat()}.pkl"
    if cache_path.exists():
        print(f"[gamma_blast] loading cached SPY bars: {cache_path}")
        return pd.read_pickle(cache_path)

    print(f"[gamma_blast] downloading SPY 1m {start} → {end} (month chunks)…")
    edges = pd.date_range(start=start, end=end, freq="MS").date.tolist()
    if not edges or edges[0] != start:
        edges = [start] + edges
    if edges[-1] != end:
        edges.append(end)
    # unique sorted
    edges = sorted(set(edges))
    if edges[-1] < end:
        edges.append(end)

    all_rows = []
    for i in range(len(edges) - 1):
        a, b = edges[i], edges[i + 1]
        if a >= b:
            continue
        print(f"  chunk {a} → {b}…")
        path = f"/v2/aggs/ticker/SPY/range/1/minute/{a.isoformat()}/{b.isoformat()}"
        params = {"adjusted": "true", "sort": "asc", "limit": 50000}
        url_path = path
        first = True
        while url_path:
            if first:
                data = _poly_get(url_path, params)
                first = False
            else:
                # next_url already includes query; call raw
                full = url_path
                if "apiKey=" not in full:
                    sep = "&" if "?" in full else "?"
                    full = f"{full}{sep}apiKey={_api_key()}"
                req = urllib.request.Request(
                    full, headers={"User-Agent": "aiem-gamma-blast/1.0"}
                )
                try:
                    with urllib.request.urlopen(req, timeout=45) as r:
                        data = json.loads(r.read())
                except Exception as e:
                    data = {"status": "ERROR", "error": str(e), "results": []}
            rows = data.get("results") or []
            all_rows.extend(rows)
            status = data.get("status")
            if status not in ("OK", "DELAYED") and not rows:
                print(f"    status={status} error={data.get('error')}")
                break
            next_url = data.get("next_url")
            url_path = next_url
            time.sleep(RATE_SLEEP)
        time.sleep(0.35)

    df = _bars_from_results(all_rows)
    if df.empty:
        print("[gamma_blast] WARNING: zero SPY bars returned for range")
        return df
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df.to_pickle(cache_path)
    print(f"[gamma_blast] cached {len(df)} bars → {cache_path}")
    return df


def split_by_session(df: pd.DataFrame) -> dict:
    """Map date → that day's RTH bars."""
    out = {}
    if df.empty:
        return out
    for d, g in df.groupby(df.index.date):
        out[d] = g
    return out


def _occ_symbol(day: date, strike: float, option_type: str) -> str:
    yy, mm, dd = day.strftime("%y"), day.strftime("%m"), day.strftime("%d")
    cp = "C" if option_type == "CALL" else "P"
    sk8 = f"{int(round(strike * 1000)):08d}"
    return f"O:SPY{yy}{mm}{dd}{cp}{sk8}"


def fetch_option_1m(day: date, strike: float, option_type: str) -> pd.Series:
    """Polygon 1-min option marks; disk-cached so TP/SL sweeps reuse the same bars."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    occ = _occ_symbol(day, strike, option_type)
    safe = occ.replace(":", "_")
    cache_path = CACHE_DIR / f"opt_{safe}.pkl"
    if cache_path.exists():
        try:
            return pd.read_pickle(cache_path)
        except Exception:
            pass

    d = day.isoformat()
    sym = urllib.parse.quote(occ)
    data = _poly_get(
        f"/v2/aggs/ticker/{sym}/range/1/minute/{d}/{d}",
        {"adjusted": "false", "sort": "asc", "limit": 50000},
    )
    time.sleep(RATE_SLEEP)
    rows = data.get("results") or []
    if not rows:
        # Cache empty series too — avoid re-hitting 404/empty contracts.
        empty = pd.Series(dtype=float)
        empty.to_pickle(cache_path)
        err = data.get("error") or data.get("status")
        if err and err not in ("OK", "DELAYED"):
            print(f"  [opt] {occ}: {err}")
        return empty
    df = pd.DataFrame(rows)
    ts = pd.to_datetime(df["t"], unit="ms", utc=True).dt.tz_convert("America/New_York")
    s = pd.Series(df["c"].astype(float).values, index=ts)
    s.to_pickle(cache_path)
    return s


def black_scholes_price(S, K, T_years, r, sigma, option_type: str) -> float:
    if T_years <= 0:
        return max(S - K, 0.0) if option_type == "CALL" else max(K - S, 0.0)
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T_years) / (sigma * np.sqrt(T_years))
    d2 = d1 - sigma * np.sqrt(T_years)
    if option_type == "CALL":
        return float(S * norm.cdf(d1) - K * np.exp(-r * T_years) * norm.cdf(d2))
    return float(K * np.exp(-r * T_years) * norm.cdf(-d2) - S * norm.cdf(-d1))


def get_atm_straddle(
    now_ts,
    underlying_price: float,
    expiry_ts,
    mode: str,
    day: date,
    opt_cache: dict,
    iv_estimate: float,
):
    strike = float(round(underlying_price))
    if mode == "real":
        key_c = (day, strike, "CALL")
        key_p = (day, strike, "PUT")
        if key_c not in opt_cache:
            opt_cache[key_c] = fetch_option_1m(day, strike, "CALL")
        if key_p not in opt_cache:
            opt_cache[key_p] = fetch_option_1m(day, strike, "PUT")
        call_s, put_s = opt_cache[key_c], opt_cache[key_p]
        if call_s.empty or put_s.empty:
            return None
        call_px = call_s.asof(now_ts)
        put_px = put_s.asof(now_ts)
        if pd.isna(call_px) or pd.isna(put_px) or call_px <= 0 or put_px <= 0:
            return None
        call_price, put_price = float(call_px), float(put_px)
    else:
        T_years = max((expiry_ts - now_ts).total_seconds(), 0) / (3600 * 6.5 * 252)
        call_price = black_scholes_price(
            underlying_price, strike, T_years, CONFIG["risk_free_rate"], iv_estimate, "CALL"
        )
        put_price = black_scholes_price(
            underlying_price, strike, T_years, CONFIG["risk_free_rate"], iv_estimate, "PUT"
        )

    return {
        "strike": strike,
        "call_price": call_price,
        "put_price": put_price,
        "straddle_total": call_price + put_price,
    }


def run_backtest_day(underlying_bars: pd.DataFrame, mode: str, day: date) -> list:
    trades = []
    open_trade = None
    straddle_history = []
    opt_cache: dict = {}

    if underlying_bars.empty:
        return trades

    expiry_ts = pd.Timestamp(
        datetime.combine(day, datetime.strptime("16:00", "%H:%M").time())
    )
    if _ET is not None:
        expiry_ts = expiry_ts.tz_localize(_ET)
    else:
        expiry_ts = expiry_ts.tz_localize("America/New_York")

    entry_start = pd.Timestamp(
        datetime.combine(day, datetime.strptime(CONFIG["entry_start"], "%H:%M").time()),
        tz=_ET or "America/New_York",
    )
    entry_end = pd.Timestamp(
        datetime.combine(day, datetime.strptime(CONFIG["entry_end"], "%H:%M").time()),
        tz=_ET or "America/New_York",
    )

    for ts, bar in underlying_bars.iterrows():
        price = float(bar["Close"])

        if open_trade is not None:
            elapsed_min = (ts - open_trade["entry_time"]).total_seconds() / 60
            straddle = get_atm_straddle(
                ts, price, expiry_ts, mode, day, opt_cache, CONFIG["iv_estimate"]
            )
            if straddle is None:
                continue
            leg = straddle["call_price"] if open_trade["type"] == "CALL" else straddle["put_price"]
            current_value = leg * open_trade["contracts"] * 100

            exit_reason = None
            if current_value >= open_trade["take_profit_price"]:
                exit_reason = "TAKE_PROFIT"
            elif current_value <= open_trade["stop_loss_price"]:
                exit_reason = "STOP_LOSS"
            elif elapsed_min > CONFIG["time_stop_minutes"]:
                exit_reason = "TIME_STOP"
            elif ts >= expiry_ts:
                exit_reason = "EXPIRY"

            if exit_reason:
                open_trade.update(
                    {
                        "exit_time": ts,
                        "exit_value": current_value,
                        "exit_reason": exit_reason,
                        "pnl": current_value - open_trade["premium"],
                        "pricing_mode": mode,
                    }
                )
                trades.append(open_trade)
                open_trade = None
            continue

        if not (entry_start <= ts <= entry_end):
            continue

        window = underlying_bars.loc[:ts].tail(30)
        if len(window) < 5:
            continue
        range_pct = (window["High"].max() - window["Low"].min()) / price
        if range_pct >= CONFIG["range_threshold"]:
            continue

        straddle = get_atm_straddle(
            ts, price, expiry_ts, mode, day, opt_cache, CONFIG["iv_estimate"]
        )
        if straddle is None:
            continue
        straddle_history.append((ts, straddle["straddle_total"]))
        if len(straddle_history) < 2:
            continue

        prev_total = straddle_history[-2][1]
        curr_total = straddle_history[-1][1]
        if prev_total <= 0:
            continue
        expansion = (curr_total - prev_total) / prev_total
        if expansion <= CONFIG["breakout_threshold"]:
            continue

        trend_window = underlying_bars.loc[:ts].tail(5)
        price_change = (
            trend_window["Close"].iloc[-1] - trend_window["Close"].iloc[0]
        ) / trend_window["Close"].iloc[0]
        if price_change > 0.002:
            direction = "CALL"
            entry_price = straddle["call_price"]
        elif price_change < -0.002:
            direction = "PUT"
            entry_price = straddle["put_price"]
        else:
            continue

        if entry_price <= 0:
            continue
        contracts = max(int(CONFIG["risk_per_trade"] / (entry_price * 100)), 1)
        premium = entry_price * contracts * 100

        open_trade = {
            "trade_date": day.isoformat(),
            "type": direction,
            "strike": straddle["strike"],
            "contracts": contracts,
            "entry_time": ts,
            "entry_underlying": price,
            "entry_option_price": entry_price,
            "premium": premium,
            "take_profit_price": premium * CONFIG["take_profit_multiplier"],
            "stop_loss_price": premium * (1 - CONFIG["stop_loss_pct"]),
        }

    if open_trade is not None:
        last_ts = underlying_bars.index[-1]
        last_price = float(underlying_bars["Close"].iloc[-1])
        straddle = get_atm_straddle(
            last_ts, last_price, expiry_ts, mode, day, opt_cache, CONFIG["iv_estimate"]
        )
        if straddle is not None:
            leg = (
                straddle["call_price"]
                if open_trade["type"] == "CALL"
                else straddle["put_price"]
            )
            current_value = leg * open_trade["contracts"] * 100
            open_trade.update(
                {
                    "exit_time": last_ts,
                    "exit_value": current_value,
                    "exit_reason": "FORCED_EOD",
                    "pnl": current_value - open_trade["premium"],
                    "pricing_mode": mode,
                }
            )
            trades.append(open_trade)

    return trades


def trading_days_back(n: int, end: Optional[date] = None) -> list:
    end = end or date.today()
    out = []
    d = end
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return list(reversed(out))


def _jsonable_trades(trades: list) -> list:
    out = []
    for t in trades:
        row = dict(t)
        for k, v in list(row.items()):
            if hasattr(v, "isoformat"):
                row[k] = v.isoformat()
            elif isinstance(v, (np.floating, np.integer)):
                row[k] = float(v) if isinstance(v, np.floating) else int(v)
        out.append(row)
    return out


def summarize(trades: list, mode: str) -> dict:
    banner = (
        "=== SYNTHETIC MODE — logic check only; NOT real P&L ==="
        if mode == "synthetic"
        else "=== REAL MODE — Polygon 1-min option aggregates ==="
    )
    print(banner)
    if not trades:
        print("No trades generated.")
        return {
            "trades": 0,
            "mode": mode,
            "total_pnl": 0.0,
            "win_rate": None,
            "avg_pnl": None,
        }

    df = pd.DataFrame(trades)
    total_pnl = float(df["pnl"].sum())
    win_rate = float((df["pnl"] > 0).mean())
    print(f"Trades: {len(df)}")
    print(f"Win rate: {win_rate:.1%}")
    print(f"Total P&L: ${total_pnl:.2f}")
    print(f"Avg P&L/trade: ${df['pnl'].mean():.2f}")
    cols = [
        c
        for c in [
            "trade_date",
            "entry_time",
            "type",
            "premium",
            "exit_reason",
            "pnl",
            "pricing_mode",
        ]
        if c in df.columns
    ]
    # keep console readable on multi-year runs
    print(df[cols].tail(30).to_string(index=False))
    if len(df) > 30:
        print(f"... ({len(df) - 30} earlier trades omitted from console; full log in archive)")
    return {
        "trades": int(len(df)),
        "mode": mode,
        "total_pnl": round(total_pnl, 2),
        "win_rate": round(win_rate, 4),
        "avg_pnl": round(float(df["pnl"].mean()), 2),
    }


def archive_root() -> Path:
    root = Path(__file__).resolve().parents[2]
    ver = root / "docs" / "verification" / ARCHIVE_DIR_NAME
    ver.mkdir(parents=True, exist_ok=True)
    return ver


def save_run_archive(
    *,
    mode: str,
    days: list,
    days_with_bars: int,
    trades: list,
    summary: dict,
    label: str,
    out_override: str = "",
) -> Path:
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    end = days[-1].isoformat() if days else "na"
    base = f"gamma-blast-{label}-{mode}-{end}-{stamp}"
    if out_override:
        path = Path(out_override)
        path.parent.mkdir(parents=True, exist_ok=True)
    else:
        path = archive_root() / f"{base}.json"

    payload = {
        "strategy": "GAMMA_BLAST",
        "label": label,
        "saved_utc": stamp,
        "pricing_mode": mode,
        "disclaimer": (
            "synthetic = logic check only, not real P&L"
            if mode == "synthetic"
            else "real = Polygon 1-min option aggregates"
        ),
        "days_requested": len(days),
        "days_with_bars": days_with_bars,
        "start": days[0].isoformat() if days else None,
        "end": end,
        "config": dict(CONFIG),
        "default_config": dict(DEFAULT_CONFIG),
        "summary": summary,
        "trades": _jsonable_trades(trades),
        "trade_count": len(trades),
    }
    path.write_text(json.dumps(payload, indent=2, default=str))

    latest = archive_root() / f"LATEST-{mode}.json"
    latest.write_text(json.dumps({"path": str(path), **payload}, indent=2, default=str))

    index_path = archive_root() / "RUN_INDEX.jsonl"
    with index_path.open("a") as f:
        f.write(
            json.dumps(
                {
                    "saved_utc": stamp,
                    "path": str(path),
                    "label": label,
                    "mode": mode,
                    "trades": summary.get("trades"),
                    "total_pnl": summary.get("total_pnl"),
                    "win_rate": summary.get("win_rate"),
                    "avg_pnl": summary.get("avg_pnl"),
                    "config": dict(CONFIG),
                },
                default=str,
            )
            + "\n"
        )
    print(f"[gamma_blast] ARCHIVED full ledger → {path}")
    return path


def apply_config_overrides(args) -> str:
    global CONFIG
    CONFIG = dict(DEFAULT_CONFIG)
    overrides = {}
    if args.risk_per_trade is not None:
        CONFIG["risk_per_trade"] = float(args.risk_per_trade)
        overrides["risk"] = CONFIG["risk_per_trade"]
    if args.take_profit is not None:
        CONFIG["take_profit_multiplier"] = float(args.take_profit)
        overrides["tp"] = CONFIG["take_profit_multiplier"]
    if args.stop_loss is not None:
        CONFIG["stop_loss_pct"] = float(args.stop_loss)
        overrides["sl"] = CONFIG["stop_loss_pct"]
    if args.range_threshold is not None:
        CONFIG["range_threshold"] = float(args.range_threshold)
        overrides["range"] = CONFIG["range_threshold"]
    if args.breakout_threshold is not None:
        CONFIG["breakout_threshold"] = float(args.breakout_threshold)
        overrides["brk"] = CONFIG["breakout_threshold"]
    if args.time_stop is not None:
        CONFIG["time_stop_minutes"] = int(args.time_stop)
        overrides["tstop"] = CONFIG["time_stop_minutes"]
    if not overrides:
        return args.label or "baseline"
    tag = args.label or "custom"
    bits = "-".join(f"{k}{v}" for k, v in overrides.items())
    return f"{tag}-{bits}"


def run_window_preloaded(mode: str, sessions: dict, day_list: list) -> tuple[list, int]:
    all_trades = []
    days_with_bars = 0
    for d in day_list:
        bars = sessions.get(d)
        if bars is None or bars.empty:
            continue
        days_with_bars += 1
        all_trades.extend(run_backtest_day(bars, mode, d))
    return all_trades, days_with_bars


def run_window(mode: str, day_list: list) -> tuple[list, int]:
    """Fallback path: fetch day-by-day (no cache). Prefer preloaded sessions."""
    all_trades = []
    days_with_bars = 0
    for d in day_list:
        bars = fetch_spy_1m(d)
        if bars.empty:
            print(f"  {d}: no SPY bars (skip)")
            continue
        days_with_bars += 1
        day_trades = run_backtest_day(bars, mode, d)
        print(f"  {d}: bars={len(bars)} trades={len(day_trades)}")
        all_trades.extend(day_trades)
    return all_trades, days_with_bars


def _parse_float_list(s: str) -> list:
    return [float(x.strip()) for x in s.split(",") if x.strip()]


def write_sweep_ranking(rows: list, mode: str, days: list) -> Path:
    """Rank variants by total_pnl — kept for Joel's variable comparison."""
    ranked = sorted(rows, key=lambda r: (r.get("total_pnl") is None, -(r.get("total_pnl") or 0)))
    path = archive_root() / f"SWEEP_RANKING-{mode}-{days[-1].isoformat()}.json"
    payload = {
        "strategy": "GAMMA_BLAST",
        "pricing_mode": mode,
        "disclaimer": (
            "synthetic rankings are logic-only — not real dollar performance"
            if mode == "synthetic"
            else "real = Polygon option aggregates"
        ),
        "risk_per_trade_usd": CONFIG.get("risk_per_trade", DEFAULT_CONFIG["risk_per_trade"]),
        "window": {"start": days[0].isoformat(), "end": days[-1].isoformat(), "n_days": len(days)},
        "profit_multiplier_map": {
            "1.5": "sell at +50% profit (1.5x premium)",
            "2.0": "sell at +100% profit (2x premium)",
            "3.0": "sell at +200% profit (3x premium)",
        },
        "ranked_best_first": ranked,
        "winner": ranked[0] if ranked else None,
    }
    path.write_text(json.dumps(payload, indent=2, default=str))
    print("\n========== SWEEP RANKING (best total P&L first) ==========")
    for i, r in enumerate(ranked, 1):
        print(
            f"{i}. {r['label']}: trades={r['trades']} win_rate={r.get('win_rate')} "
            f"total_pnl=${r.get('total_pnl')} avg=${r.get('avg_pnl')}"
        )
    print(f"[gamma_blast] wrote ranking → {path}")
    return path


def main():
    ap = argparse.ArgumentParser(description="Gamma Blast Polygon backtest (AIEM handoff)")
    ap.add_argument("--days", type=int, default=None, help="Trading days lookback")
    ap.add_argument("--years", type=float, default=None, help="Lookback in years (~252*years days)")
    ap.add_argument(
        "--mode",
        choices=("synthetic", "real"),
        default="synthetic",
        help="synthetic=BS logic check; real=Polygon option 1m aggs",
    )
    ap.add_argument("--out", default="", help="Optional explicit archive path")
    ap.add_argument("--label", default="", help="Archive label")
    ap.add_argument("--risk-per-trade", type=float, default=None)
    ap.add_argument("--take-profit", type=float, default=None, help="Premium multiple, e.g. 2.0 = +100%")
    ap.add_argument("--stop-loss", type=float, default=None, help="Fraction of premium lost, e.g. 0.65")
    ap.add_argument("--range-threshold", type=float, default=None)
    ap.add_argument("--breakout-threshold", type=float, default=None)
    ap.add_argument("--time-stop", type=int, default=None, help="Minutes")
    ap.add_argument(
        "--sweep-quick",
        action="store_true",
        help="Legacy small TP/SL grid",
    )
    ap.add_argument(
        "--sweep-tp",
        default="",
        help="Comma list of take-profit multipliers (1.5=+50%, 2=+100%, 3=+200%)",
    )
    ap.add_argument(
        "--sweep-sl",
        default="",
        help="Comma list of stop-loss fractions (0.60,0.65,0.75)",
    )
    args = ap.parse_args()

    if args.years is not None:
        n_days = max(int(round(args.years * 252)), 1)
    elif args.days is not None:
        n_days = args.days
    else:
        n_days = 20

    days = trading_days_back(n_days)
    start, end = days[0], days[-1]

    # Preload SPY once for all variants
    spy_df = fetch_spy_range_cached(start, end)
    sessions = split_by_session(spy_df)
    print(f"[gamma_blast] sessions with bars: {len(sessions)} / {len(days)} requested")

    sweep_tps = _parse_float_list(args.sweep_tp) if args.sweep_tp else []
    sweep_sls = _parse_float_list(args.sweep_sl) if args.sweep_sl else []

    if args.sweep_quick and not (sweep_tps and sweep_sls):
        sweep_tps = [2.0, 3.0, 4.0]
        sweep_sls = [0.35, 0.50, 0.65]

    if sweep_tps and sweep_sls:
        grid = [
            {"take_profit_multiplier": tp, "stop_loss_pct": sl}
            for tp in sweep_tps
            for sl in sweep_sls
        ]
        print(
            f"[gamma_blast] SWEEP mode={args.mode} days={n_days} "
            f"variants={len(grid)} range={start}→{end} risk=$100"
        )
        if args.mode == "synthetic":
            print("[gamma_blast] WARNING: synthetic — compare ranks, not dollar P&L.")
        if args.mode == "real":
            print(
                "[gamma_blast] WARNING: real 2y sweeps are very API-heavy; "
                "expect long runtime / rate limits."
            )

        ranking_rows = []
        for i, knobs in enumerate(grid, 1):
            global CONFIG
            CONFIG = dict(DEFAULT_CONFIG)
            if args.risk_per_trade is not None:
                CONFIG["risk_per_trade"] = float(args.risk_per_trade)
            CONFIG.update(knobs)
            # Label: tp1.5 = +50% profit, tp2 = +100%, tp3 = +200%
            profit_pct = int(round((knobs["take_profit_multiplier"] - 1.0) * 100))
            sl_pct = int(round(knobs["stop_loss_pct"] * 100))
            label = f"sweep-tp{knobs['take_profit_multiplier']}-profit{profit_pct}pct-sl{sl_pct}pct"
            print(f"\n--- variant {i}/{len(grid)} {label} ---")
            trades, n_bars = run_window_preloaded(args.mode, sessions, days)
            summary = summarize(trades, args.mode)
            path = save_run_archive(
                mode=args.mode,
                days=days,
                days_with_bars=n_bars,
                trades=trades,
                summary=summary,
                label=label,
            )
            ranking_rows.append(
                {
                    "label": label,
                    "path": str(path),
                    "take_profit_multiplier": knobs["take_profit_multiplier"],
                    "profit_pct_target": profit_pct,
                    "stop_loss_pct": knobs["stop_loss_pct"],
                    "trades": summary.get("trades"),
                    "win_rate": summary.get("win_rate"),
                    "total_pnl": summary.get("total_pnl"),
                    "avg_pnl": summary.get("avg_pnl"),
                    "days_with_bars": n_bars,
                }
            )
        write_sweep_ranking(ranking_rows, args.mode, days)
        print(f"\n[gamma_blast] sweep complete — see {archive_root()}/")
        return 0 if sessions else 1

    label = apply_config_overrides(args)
    print(
        f"[gamma_blast] mode={args.mode} days={n_days} label={label} "
        f"range={start}→{end} ticker={CONFIG['ticker']} "
        f"risk=${CONFIG['risk_per_trade']}"
    )
    if args.mode == "synthetic":
        print(
            "[gamma_blast] WARNING: synthetic Black-Scholes — report as logic check only."
        )

    all_trades, days_with_bars = run_window_preloaded(args.mode, sessions, days)
    summary = summarize(all_trades, args.mode)
    save_run_archive(
        mode=args.mode,
        days=days,
        days_with_bars=days_with_bars,
        trades=all_trades,
        summary=summary,
        label=label,
        out_override=args.out,
    )
    return 0 if days_with_bars else 1


if __name__ == "__main__":
    sys.exit(main())
