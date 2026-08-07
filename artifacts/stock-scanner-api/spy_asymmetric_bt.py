#!/usr/bin/env python3
"""
SPY Asymmetric Strategies — 2y real Polygon options backtest
Directive_SPY_Asymmetric_Strategies_BT_2026-08-07

Compares 23 defined-risk-leaning SPY option structures under IDENTICAL rules:
  - Underlying: SPY
  - Risk budget: $100 max debit / defined risk per trade (1+ contracts floored)
  - Entry: each Monday ~ open (first RTH bar day), ~21–45 DTE when available
  - Exit grid (NO STOP LOSS): take-profit when P&L >= entry_debit * pct
        50%, 75%, 100%, 125%, 150%, 200%
  - Else flatten at 15:30 ET on expiry Friday (or last available bar before expiry)

Pricing: real Polygon daily option aggregates (O:SPY…). Synthetic BS is NOT used.

Archives full ledgers + ranking under docs/verification/spy-asymmetric-bt/
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
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except ImportError:
    _ET = None

POLYGON_BASE = "https://api.polygon.io"
RATE_SLEEP = float(os.environ.get("ASYM_BT_RATE_SLEEP", "0.25"))
CACHE_DIR = Path(os.environ.get("ASYM_BT_CACHE", "/tmp/spy_asym_bt_cache"))
ARCHIVE_DIR_NAME = "spy-asymmetric-bt"
RISK_USD = float(os.environ.get("ASYM_BT_RISK_USD", "500"))
TP_PCTS = [50, 75, 100, 125, 150, 200]


def _api_key() -> str:
    k = os.environ.get("POLYGON_API_KEY") or os.environ.get("POLYGON_KEY") or ""
    if not k:
        raise SystemExit("POLYGON_API_KEY not set")
    return k


def _poly_get(path: str, params: Optional[dict] = None) -> dict:
    params = dict(params or {})
    params["apiKey"] = _api_key()
    url = f"{POLYGON_BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "aiem-spy-asym-bt/1.0"})
    for attempt in range(8):
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")[:240]
            except Exception:
                pass
            if e.code == 429:
                wait = min(60, 2 ** attempt + 1)
                print(f"  [poly] 429 — sleep {wait}s")
                time.sleep(wait)
                continue
            return {"status": "ERROR", "error": f"HTTP {e.code}: {body}", "results": []}
        except Exception as e:
            if attempt < 3:
                time.sleep(1.2 * (attempt + 1))
                continue
            return {"status": "ERROR", "error": str(e), "results": []}
    return {"status": "ERROR", "error": "rate_limited", "results": []}


def archive_root() -> Path:
    root = Path(__file__).resolve().parents[2]
    p = root / "docs" / "verification" / ARCHIVE_DIR_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


# ── market data ───────────────────────────────────────────────────────────────

def fetch_spy_daily(start: date, end: date) -> pd.DataFrame:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"SPY_1d_{start}_{end}.pkl"
    if path.exists():
        return pd.read_pickle(path)
    data = _poly_get(
        f"/v2/aggs/ticker/SPY/range/1/day/{start.isoformat()}/{end.isoformat()}",
        {"adjusted": "true", "sort": "asc", "limit": 50000},
    )
    time.sleep(RATE_SLEEP)
    rows = data.get("results") or []
    if not rows:
        raise SystemExit(f"No SPY daily bars {start}→{end}: {data.get('error')}")
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["t"], unit="ms", utc=True).dt.tz_convert("America/New_York").dt.date
    df = df.set_index("date").rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    df.to_pickle(path)
    print(f"[asym] cached SPY daily {len(df)} bars → {path}")
    return df


def _occ(day: date, strike: float, right: str, exp: date) -> str:
    """OCC option ticker O:SPYYYMMDD[C|P]########"""
    cp = "C" if right.upper().startswith("C") else "P"
    sk8 = f"{int(round(strike * 1000)):08d}"
    return f"O:SPY{exp.strftime('%y%m%d')}{cp}{sk8}"


def fetch_option_daily(symbol: str, start: date, end: date) -> pd.Series:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    safe = symbol.replace(":", "_")
    path = CACHE_DIR / f"opt1d_{safe}_{start}_{end}.pkl"
    if path.exists():
        try:
            return pd.read_pickle(path)
        except Exception:
            pass
    sym = urllib.parse.quote(symbol)
    data = _poly_get(
        f"/v2/aggs/ticker/{sym}/range/1/day/{start.isoformat()}/{end.isoformat()}",
        {"adjusted": "false", "sort": "asc", "limit": 50000},
    )
    time.sleep(RATE_SLEEP)
    rows = data.get("results") or []
    if not rows:
        s = pd.Series(dtype=float)
        s.to_pickle(path)
        return s
    df = pd.DataFrame(rows)
    idx = pd.to_datetime(df["t"], unit="ms", utc=True).dt.tz_convert("America/New_York").dt.date
    s = pd.Series(df["c"].astype(float).values, index=idx)
    s = s[~s.index.duplicated(keep="last")].sort_index()
    s.to_pickle(path)
    return s


def next_friday(d: date, weeks_ahead: int = 0) -> date:
    """Friday on or after d, plus weeks_ahead extra weeks."""
    # weekday: Mon=0 … Fri=4
    add = (4 - d.weekday()) % 7
    fri = d + timedelta(days=add)
    if fri <= d and weeks_ahead == 0:
        # if today is Friday, use next week's Friday for entry same-day expiry avoidance
        fri = fri + timedelta(days=7)
    fri = fri + timedelta(weeks=weeks_ahead)
    return fri


def mondays_between(start: date, end: date) -> list:
    d = start
    while d.weekday() != 0:
        d += timedelta(days=1)
    out = []
    while d <= end:
        out.append(d)
        d += timedelta(days=7)
    return out


# ── strategy leg builders ─────────────────────────────────────────────────────
# Each builder returns list of (qty, right, strike, exp) where qty>0 long, qty<0 short.
# Spot = underlying price; d0 = entry date.

StrikeSpec = tuple  # (qty, right, strike, exp)


def _round_strike(x: float, step: float = 1.0) -> float:
    return float(round(x / step) * step)


def build_long_call(spot, d0, exp_near, exp_far) -> list:
    k = _round_strike(spot)
    return [(1, "C", k, exp_near)]


def build_long_put(spot, d0, exp_near, exp_far) -> list:
    k = _round_strike(spot)
    return [(1, "P", k, exp_near)]


def build_call_debit_spread(spot, d0, exp_near, exp_far) -> list:
    k = _round_strike(spot)
    return [(1, "C", k, exp_near), (-1, "C", k + 5, exp_near)]


def build_put_debit_spread(spot, d0, exp_near, exp_far) -> list:
    k = _round_strike(spot)
    return [(1, "P", k, exp_near), (-1, "P", k - 5, exp_near)]


def build_debit_bwb_call(spot, d0, exp_near, exp_far) -> list:
    # Debit broken-wing call butterfly: long low, short 2 mid, long further high (asymmetric wing)
    k = _round_strike(spot)
    return [(1, "C", k, exp_near), (-2, "C", k + 5, exp_near), (1, "C", k + 15, exp_near)]


def build_long_call_fly(spot, d0, exp_near, exp_far) -> list:
    k = _round_strike(spot)
    return [(1, "C", k - 5, exp_near), (-2, "C", k, exp_near), (1, "C", k + 5, exp_near)]


def build_long_put_fly(spot, d0, exp_near, exp_far) -> list:
    k = _round_strike(spot)
    return [(1, "P", k + 5, exp_near), (-2, "P", k, exp_near), (1, "P", k - 5, exp_near)]


def build_long_call_condor(spot, d0, exp_near, exp_far) -> list:
    k = _round_strike(spot)
    return [
        (1, "C", k - 10, exp_near),
        (-1, "C", k - 5, exp_near),
        (-1, "C", k + 5, exp_near),
        (1, "C", k + 10, exp_near),
    ]


def build_long_put_condor(spot, d0, exp_near, exp_far) -> list:
    k = _round_strike(spot)
    return [
        (1, "P", k + 10, exp_near),
        (-1, "P", k + 5, exp_near),
        (-1, "P", k - 5, exp_near),
        (1, "P", k - 10, exp_near),
    ]


def build_call_ratio_backspread(spot, d0, exp_near, exp_far) -> list:
    # Defined-risk lean: short 1 ATM, long 2 OTM (debit or small credit)
    k = _round_strike(spot)
    return [(-1, "C", k, exp_near), (2, "C", k + 5, exp_near)]


def build_put_ratio_backspread(spot, d0, exp_near, exp_far) -> list:
    k = _round_strike(spot)
    return [(-1, "P", k, exp_near), (2, "P", k - 5, exp_near)]


def build_long_straddle(spot, d0, exp_near, exp_far) -> list:
    k = _round_strike(spot)
    return [(1, "C", k, exp_near), (1, "P", k, exp_near)]


def build_long_strangle(spot, d0, exp_near, exp_far) -> list:
    k = _round_strike(spot)
    return [(1, "C", k + 5, exp_near), (1, "P", k - 5, exp_near)]


def build_call_calendar(spot, d0, exp_near, exp_far) -> list:
    k = _round_strike(spot)
    return [(-1, "C", k, exp_near), (1, "C", k, exp_far)]


def build_put_calendar(spot, d0, exp_near, exp_far) -> list:
    k = _round_strike(spot)
    return [(-1, "P", k, exp_near), (1, "P", k, exp_far)]


def build_call_diagonal(spot, d0, exp_near, exp_far) -> list:
    k = _round_strike(spot)
    return [(-1, "C", k + 5, exp_near), (1, "C", k, exp_far)]


def build_put_diagonal(spot, d0, exp_near, exp_far) -> list:
    k = _round_strike(spot)
    return [(-1, "P", k - 5, exp_near), (1, "P", k, exp_far)]


def build_unbalanced_fly_call(spot, d0, exp_near, exp_far) -> list:
    k = _round_strike(spot)
    return [(1, "C", k - 5, exp_near), (-2, "C", k, exp_near), (1, "C", k + 10, exp_near)]


def build_unbalanced_condor_call(spot, d0, exp_near, exp_far) -> list:
    k = _round_strike(spot)
    return [
        (1, "C", k - 10, exp_near),
        (-1, "C", k - 5, exp_near),
        (-1, "C", k + 5, exp_near),
        (1, "C", k + 15, exp_near),
    ]


def build_skip_strike_fly_call(spot, d0, exp_near, exp_far) -> list:
    k = _round_strike(spot)
    return [(1, "C", k - 5, exp_near), (-2, "C", k, exp_near), (1, "C", k + 10, exp_near)]


def build_christmas_tree_call(spot, d0, exp_near, exp_far) -> list:
    # 1 long ATM, short 2 further OTM, short 1 even further (tree) — often credit;
    # defined-risk variant: long 1 lower wing
    k = _round_strike(spot)
    return [
        (1, "C", k - 5, exp_near),
        (-1, "C", k, exp_near),
        (-1, "C", k + 5, exp_near),
        (-1, "C", k + 10, exp_near),
        (2, "C", k + 15, exp_near),
    ]


def build_call_ladder_defined(spot, d0, exp_near, exp_far) -> list:
    # Defined-risk call ladder: long lower, short mid, short higher → buy further wing
    k = _round_strike(spot)
    return [(1, "C", k, exp_near), (-1, "C", k + 5, exp_near), (-1, "C", k + 10, exp_near), (1, "C", k + 15, exp_near)]


def build_put_ladder_defined(spot, d0, exp_near, exp_far) -> list:
    k = _round_strike(spot)
    return [(1, "P", k, exp_near), (-1, "P", k - 5, exp_near), (-1, "P", k - 10, exp_near), (1, "P", k - 15, exp_near)]


STRATEGIES: dict[str, Callable] = {
    "01_long_call": build_long_call,
    "02_long_put": build_long_put,
    "03_call_debit_spread": build_call_debit_spread,
    "04_put_debit_spread": build_put_debit_spread,
    "05_debit_broken_wing_butterfly": build_debit_bwb_call,
    "06_long_call_butterfly": build_long_call_fly,
    "07_long_put_butterfly": build_long_put_fly,
    "08_long_call_condor": build_long_call_condor,
    "09_long_put_condor": build_long_put_condor,
    "10_call_ratio_backspread": build_call_ratio_backspread,
    "11_put_ratio_backspread": build_put_ratio_backspread,
    "12_long_straddle": build_long_straddle,
    "13_long_strangle": build_long_strangle,
    "14_call_calendar": build_call_calendar,
    "15_put_calendar": build_put_calendar,
    "16_call_diagonal": build_call_diagonal,
    "17_put_diagonal": build_put_diagonal,
    "18_unbalanced_butterfly": build_unbalanced_fly_call,
    "19_unbalanced_condor": build_unbalanced_condor_call,
    "20_skip_strike_butterfly": build_skip_strike_fly_call,
    "21_christmas_tree_butterfly": build_christmas_tree_call,
    "22_call_ladder_defined_risk": build_call_ladder_defined,
    "23_put_ladder_defined_risk": build_put_ladder_defined,
}


@dataclass
class LegPos:
    qty: int  # contracts, signed
    symbol: str
    series: pd.Series


def _px_on(series: pd.Series, d: date) -> Optional[float]:
    if series is None or series.empty:
        return None
    if d in series.index:
        px = series.loc[d]
    else:
        px = series.asof(d)
    if px is None or (isinstance(px, float) and np.isnan(px)) or float(px) <= 0:
        return None
    return float(px)


def package_value(legs: list[LegPos], d: date) -> Optional[float]:
    """Mark package in dollars (premium * 100 * qty sum)."""
    total = 0.0
    for leg in legs:
        px = _px_on(leg.series, d)
        if px is None:
            return None
        total += leg.qty * px * 100.0
    return total


def run_strategy(
    name: str,
    builder: Callable,
    spy: pd.DataFrame,
    entry_dates: list,
    tp_pct: int,
    end: date,
    sl_pct: float = 0.0,
) -> list:
    trades = []
    for d0 in entry_dates:
        if d0 not in spy.index:
            later = [x for x in spy.index if x >= d0]
            if not later:
                continue
            d0 = later[0]
        spot = float(spy.loc[d0, "close"])
        exp_near = next_friday(d0, weeks_ahead=3)
        exp_far = next_friday(d0, weeks_ahead=7)
        if exp_near > end + timedelta(days=7):
            continue

        raw_legs = builder(spot, d0, exp_near, exp_far)
        unique = {}
        leg_pos = []
        ok = True
        for qty, right, strike, exp in raw_legs:
            sym = _occ(d0, strike, right, exp)
            if sym not in unique:
                unique[sym] = fetch_option_daily(sym, d0, min(exp, end))
            ser = unique[sym]
            if _px_on(ser, d0) is None:
                ok = False
                break
            leg_pos.append(LegPos(qty=qty, symbol=sym, series=ser))
        if not ok or not leg_pos:
            continue

        entry_val = package_value(leg_pos, d0)
        if entry_val is None:
            continue
        unit_cost = entry_val
        if abs(unit_cost) < 1.0:
            continue
        if unit_cost > 0:
            if unit_cost > RISK_USD:
                continue
            mult = max(int(RISK_USD / unit_cost), 1)
            while mult > 1 and unit_cost * mult > RISK_USD:
                mult -= 1
        else:
            mult = 1
        for lp in leg_pos:
            lp.qty *= mult
        entry_val *= mult
        premium_basis = abs(entry_val)
        tp_dollars = None if tp_pct <= 0 else premium_basis * (tp_pct / 100.0)
        sl_dollars = None if sl_pct <= 0 else premium_basis * (sl_pct / 100.0)
        is_debit = entry_val > 0

        def _pnl(mark: float) -> float:
            return mark - entry_val

        hold_end = min(exp_near, end)
        sessions = [x for x in spy.index if d0 < x <= hold_end]
        exit_d = None
        exit_val = None
        exit_reason = None
        pnl = None

        for d in sessions:
            mark = package_value(leg_pos, d)
            if mark is None:
                continue
            pnl_now = _pnl(mark)
            # Stop first on same bar (conservative)
            if sl_dollars is not None and pnl_now <= -sl_dollars:
                exit_d, exit_val, exit_reason, pnl = d, mark, f"SL_{int(sl_pct)}PCT", pnl_now
                break
            if tp_dollars is not None and pnl_now >= tp_dollars:
                exit_d, exit_val, exit_reason, pnl = d, mark, f"TP_{tp_pct}PCT", pnl_now
                break

        if exit_d is None:
            for d in reversed(sessions):
                mark = package_value(leg_pos, d)
                if mark is None:
                    continue
                pnl = _pnl(mark)
                exit_d, exit_val, exit_reason = d, mark, "EXPIRY_FLATTEN"
                break
            if exit_d is None:
                continue

        trades.append({
            "strategy": name,
            "tp_pct": tp_pct,
            "sl_pct": sl_pct,
            "entry_date": d0.isoformat(),
            "exit_date": exit_d.isoformat(),
            "spot": round(spot, 2),
            "exp_near": exp_near.isoformat(),
            "mult": mult,
            "entry_val": round(entry_val, 2),
            "exit_val": round(exit_val, 2),
            "premium_basis": round(premium_basis, 2),
            "pnl": round(float(pnl), 2),
            "exit_reason": exit_reason,
            "legs": [f"{lp.qty}x{lp.symbol}" for lp in leg_pos],
        })
    return trades


def summarize(trades: list) -> dict:
    if not trades:
        return {
            "trades": 0, "total_pnl": 0.0, "win_rate": None, "avg_pnl": None,
            "tp_hits": 0, "sl_hits": 0, "expiry_exits": 0,
        }
    df = pd.DataFrame(trades)
    wins = (df["pnl"] > 0).sum()
    return {
        "trades": int(len(df)),
        "total_pnl": round(float(df["pnl"].sum()), 2),
        "win_rate": round(float(wins / len(df)), 4),
        "avg_pnl": round(float(df["pnl"].mean()), 2),
        "tp_hits": int(df["exit_reason"].astype(str).str.startswith("TP_").sum()),
        "sl_hits": int(df["exit_reason"].astype(str).str.startswith("SL_").sum()),
        "expiry_exits": int((df["exit_reason"] == "EXPIRY_FLATTEN").sum()),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=float, default=2.0)
    ap.add_argument("--strategies", default="all", help="comma names or 'all'")
    ap.add_argument("--tp", default=",".join(str(x) for x in TP_PCTS), help="profit exit percents")
    ap.add_argument(
        "--sl",
        default="0",
        help="stop-loss percents of premium (comma). 0 = no stop. e.g. 20,25,30",
    )
    ap.add_argument("--max-entries", type=int, default=0, help="cap Mondays (0=all) for smoke tests")
    args = ap.parse_args()

    if args.tp.strip().lower() in ("none", "0", "ride", "expiry"):
        tp_list = [0]
    else:
        tp_list = [int(x) for x in args.tp.split(",") if x.strip()]
    sl_list = [float(x) for x in args.sl.split(",") if x.strip()]
    if not sl_list:
        sl_list = [0.0]

    end = date.today()
    start = end - timedelta(days=int(args.years * 365.25))

    print(
        f"[asym] SPY asymmetric BT {start}→{end} risk=${RISK_USD} "
        f"TPs={tp_list} SLs={sl_list}"
    )
    spy = fetch_spy_daily(start, end)
    entries = mondays_between(start, end)
    entries = [d for d in entries if d in spy.index or any(x >= d for x in spy.index)]
    if args.max_entries:
        entries = entries[: args.max_entries]
    print(f"[asym] entry Mondays: {len(entries)}")

    if args.strategies == "all":
        strat_items = list(STRATEGIES.items())
    else:
        want = set(args.strategies.split(","))
        strat_items = [(k, v) for k, v in STRATEGIES.items() if k in want]

    ranking = []

    for sname, builder in strat_items:
        for tp in tp_list:
            for sl in sl_list:
                if tp > 0 and sl > 0:
                    label = f"{sname}__tp{tp}__sl{int(sl)}"
                elif tp > 0:
                    label = f"{sname}__tp{tp}"
                elif sl > 0:
                    label = f"{sname}__RIDE__sl{int(sl)}"
                else:
                    label = f"{sname}__RIDE"
                print(f"\n=== {label} ===")
                trades = run_strategy(sname, builder, spy, entries, tp, end, sl_pct=sl)
                summary = summarize(trades)
                print(
                    f"  trades={summary['trades']} pnl=${summary['total_pnl']} "
                    f"wr={summary['win_rate']} sl_hits={summary.get('sl_hits')}"
                )
                payload = {
                    "strategy": sname,
                    "tp_pct": tp,
                    "sl_pct": sl,
                    "ride_to_expiry": tp <= 0,
                    "no_stop_loss": sl <= 0,
                    "risk_usd": RISK_USD,
                    "window": {"start": start.isoformat(), "end": end.isoformat()},
                    "summary": summary,
                    "trades": trades,
                }
                out = archive_root() / f"{label}.json"
                out.write_text(json.dumps(payload, indent=2, default=str))
                ranking.append({
                    "label": label,
                    "strategy": sname,
                    "tp_pct": tp,
                    "sl_pct": sl,
                    "ride_to_expiry": tp <= 0,
                    **summary,
                    "path": str(out),
                })

    ranking_sorted = sorted(
        ranking, key=lambda r: (r.get("total_pnl") is None, -(r.get("total_pnl") or 0))
    )
    if tp_list == [0] and any(s > 0 for s in sl_list):
        rank_name = f"RANKING_RIDE_SLGRID_{end.isoformat()}.json"
        exit_rule = f"NO take-profit; stop-loss grid {sl_list}% of premium; else expiry flatten"
    elif tp_list == [0]:
        rank_name = f"RANKING_RIDE_{end.isoformat()}.json"
        exit_rule = "NO take-profit — ride to near-expiry flatten only; NO STOP LOSS"
    else:
        rank_name = f"RANKING_NOSTOP_TPGRID_{end.isoformat()}.json"
        exit_rule = f"TP grid {tp_list}% — stops={sl_list}; else flatten near expiry"

    rank_path = archive_root() / rank_name
    rank_payload = {
        "rules": {
            "underlying": "SPY",
            "risk_usd": RISK_USD,
            "entry": "weekly Monday",
            "exit": exit_rule,
            "pricing": "Polygon daily option aggregates",
            "strategies_n": len(strat_items),
            "tp_list": tp_list,
            "sl_list": sl_list,
        },
        "ranked_best_first": ranking_sorted,
        "winner": ranking_sorted[0] if ranking_sorted else None,
        "top10": ranking_sorted[:10],
        "best_tp_per_strategy": {},
    }
    best = {}
    for row in ranking_sorted:
        s = row["strategy"]
        if s not in best:
            best[s] = row
    rank_payload["best_tp_per_strategy"] = best
    rank_path.write_text(json.dumps(rank_payload, indent=2, default=str))

    idx = archive_root() / "RUN_INDEX.jsonl"
    with idx.open("a") as f:
        f.write(json.dumps({
            "saved_utc": datetime.utcnow().isoformat() + "Z",
            "ranking": str(rank_path),
            "winner": rank_payload.get("winner"),
        }, default=str) + "\n")

    print("\n========== TOP 10 (best total P&L) ==========")
    for i, r in enumerate(ranking_sorted[:10], 1):
        print(
            f"{i}. {r['label']}: trades={r['trades']} WR={r['win_rate']} "
            f"PnL=${r['total_pnl']} SL_hits={r.get('sl_hits')}"
        )
    print(f"[asym] wrote {rank_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
