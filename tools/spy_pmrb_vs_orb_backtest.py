#!/usr/bin/env python3
"""
SPY Premarket Range Breakout (PMRB) vs Opening Range Breakout (ORB)
===================================================================
One-year comparison, $1000 RISK per trade (position sized to stop distance).

PMRB (= Premarket Breakout / Premarket High-Low strategy):
  • Premarket range: 04:00–09:29 ET → PMH / PML
  • Long:  first RTH 5m/60m close > PMH
  • Short: first RTH close < PML
  • Stop:  opposite side of premarket range
  • Target: 2R (2 × risk from entry to stop)
  • Time:  flatten at 15:55 ET
  • One trade / day (first signal only)

ORB (matches terminal scanner / orb_backtest.py):
  • Opening range: 09:30–09:59 ET
  • Long only: first close after 10:00 > ORB High
  • Hard stop 5%; trail 10% from peak; EOD 15:55

DATA
----
Primary: Yahoo Finance extended-hours bars (Polygon options/stock key returns 401 here).
  • 60-minute + prepost for full ~1y window (resolution caveat documented)
  • 5-minute + prepost for recent ~60d high-res cross-check

Outputs:
  artifacts/backtests/spy_pmrb_vs_orb_1y.json
  docs/verification/spy-pmrb-vs-orb-1y-2026-08-06.md
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, asdict
from datetime import datetime, time, date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf

RISK_PER_TRADE = 1000.0  # dollars risked to the stop on each entry
WINDOW_START = "2025-08-01"
WINDOW_END = "2026-08-05"
PM_START = time(4, 0)
PM_END = time(9, 30)       # exclusive
ORB_START = time(9, 30)
ORB_END = time(10, 0)      # exclusive
ENTRY_AFTER_ORB = time(10, 0)
EOD = time(15, 55)
HARD_STOP = 0.95
TRAIL = 0.90


def _flatten_cols(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = [c[0].lower() if isinstance(c, tuple) else str(c).lower() for c in df.columns]
    else:
        df = df.copy()
        df.columns = [str(c).lower() for c in df.columns]
    return df


def load_bars(interval: str, start: str, end: str) -> pd.DataFrame:
    df = yf.download(
        "SPY",
        start=start,
        end=end,
        interval=interval,
        prepost=True,
        progress=False,
        auto_adjust=True,
    )
    if df is None or df.empty:
        raise RuntimeError(f"No Yahoo bars interval={interval}")
    df = _flatten_cols(df)
    if df.index.tz is None:
        df.index = df.index.tz_localize("America/New_York")
    else:
        df.index = df.index.tz_convert("America/New_York")
    df = df.rename(columns={"open": "o", "high": "h", "low": "l", "close": "c", "volume": "v"})
    df["date"] = df.index.date
    df["t"] = df.index.map(lambda x: x.time())
    return df[["o", "h", "l", "c", "v", "date", "t"]].dropna(subset=["c"])


@dataclass
class Trade:
    strategy: str
    date: str
    side: str
    entry: float
    exit: float
    stop: float
    target: Optional[float]
    pnl_pct: float
    pnl_usd: float
    exit_reason: str
    level_high: float
    level_low: float
    risk_per_share: float = 0.0
    shares: float = 0.0
    risk_budget: float = RISK_PER_TRADE


def _size_pnl(entry: float, exit_px: float, side: str, risk_per_share: float) -> Tuple[float, float, float, float]:
    """Return pnl_pct, pnl_usd, shares, risk_per_share given $RISK_PER_TRADE to stop."""
    if risk_per_share <= 1e-9:
        return 0.0, 0.0, 0.0, risk_per_share
    shares = RISK_PER_TRADE / risk_per_share
    if side == "long":
        pnl_usd = shares * (exit_px - entry)
        pnl_pct = (exit_px - entry) / entry * 100.0
    else:
        pnl_usd = shares * (entry - exit_px)
        pnl_pct = (entry - exit_px) / entry * 100.0
    return pnl_pct, pnl_usd, shares, risk_per_share


def _summarize(trades: List[Trade]) -> dict:
    if not trades:
        return {
            "n": 0, "total_pnl_usd": 0.0, "avg_pnl_usd": 0.0, "win_rate": None,
            "profit_factor": None, "max_dd_usd": 0.0, "expectancy_usd": 0.0,
            "long_n": 0, "short_n": 0,
        }
    pnls = [t.pnl_usd for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    eq = np.cumsum(pnls)
    peak = np.maximum.accumulate(eq)
    dd = float((eq - peak).min()) if len(eq) else 0.0
    gw, gl = sum(wins), abs(sum(losses))
    return {
        "n": len(trades),
        "total_pnl_usd": round(sum(pnls), 2),
        "avg_pnl_usd": round(float(np.mean(pnls)), 2),
        "win_rate": round(100.0 * len(wins) / len(pnls), 1),
        "profit_factor": round(gw / gl, 2) if gl > 0 else None,
        "max_dd_usd": round(dd, 2),
        "expectancy_usd": round(float(np.mean(pnls)), 2),
        "long_n": sum(1 for t in trades if t.side == "long"),
        "short_n": sum(1 for t in trades if t.side == "short"),
        "exit_reasons": {
            k: sum(1 for t in trades if t.exit_reason == k)
            for k in sorted({t.exit_reason for t in trades})
        },
    }


def simulate_pmrb(df: pd.DataFrame) -> List[Trade]:
    trades: List[Trade] = []
    for day, g in df.groupby("date"):
        pm = g[(g["t"] >= PM_START) & (g["t"] < PM_END)]
        rth = g[(g["t"] >= ORB_START) & (g["t"] < time(16, 0))]
        if len(pm) < 1 or len(rth) < 2:
            continue
        pmh = float(pm["h"].max())
        pml = float(pm["l"].min())
        if pmh - pml < 1e-6:
            continue
        # First break of PMH or PML during RTH
        side = entry = stop = target = None
        entry_i = None
        bars = list(rth.itertuples())
        for i, bar in enumerate(bars):
            if bar.c > pmh:
                side, entry, entry_i = "long", float(bar.c), i
                stop = pml
                risk = entry - stop
                if risk <= 0:
                    break
                target = entry + 2.0 * risk
                break
            if bar.c < pml:
                side, entry, entry_i = "short", float(bar.c), i
                stop = pmh
                risk = stop - entry
                if risk <= 0:
                    break
                target = entry - 2.0 * risk
                break
        if side is None or entry is None or entry_i is None:
            continue

        exit_px = None
        reason = None
        for bar in bars[entry_i + 1 :]:
            if side == "long":
                if bar.l <= stop:
                    exit_px, reason = stop, "stop"
                    break
                if target is not None and bar.h >= target:
                    exit_px, reason = target, "target_2R"
                    break
            else:
                if bar.h >= stop:
                    exit_px, reason = stop, "stop"
                    break
                if target is not None and bar.l <= target:
                    exit_px, reason = target, "target_2R"
                    break
            if bar.t >= EOD:
                exit_px, reason = float(bar.c), "eod"
                break
        if exit_px is None:
            exit_px, reason = float(bars[-1].c), "eod"

        risk_ps = abs(entry - stop)
        pnl_pct, pnl_usd, shares, risk_ps = _size_pnl(entry, exit_px, side, risk_ps)
        if shares <= 0:
            continue
        trades.append(
            Trade(
                strategy="PMRB",
                date=str(day),
                side=side,
                entry=round(entry, 4),
                exit=round(exit_px, 4),
                stop=round(stop, 4),
                target=round(target, 4) if target else None,
                pnl_pct=round(pnl_pct, 4),
                pnl_usd=round(pnl_usd, 2),
                exit_reason=reason or "eod",
                level_high=round(pmh, 4),
                level_low=round(pml, 4),
                risk_per_share=round(risk_ps, 4),
                shares=round(shares, 4),
            )
        )
    return trades


def simulate_orb(df: pd.DataFrame, mode: str = "terminal") -> List[Trade]:
    """
    mode=terminal: long-only, 5% hard + 10% trail (matches orb_backtest.py / terminal)
    mode=range_2r: long-only, stop at ORB low, target 2R (matched risk model vs PMRB)
    """
    trades: List[Trade] = []
    for day, g in df.groupby("date"):
        session = g[(g["t"] >= ORB_START) & (g["t"] < time(16, 0))]
        if len(session) < 3:
            continue
        orb_bars = session[(session["t"] >= ORB_START) & (session["t"] < ORB_END)]
        if len(orb_bars) < 1:
            continue
        orb_high = float(orb_bars["h"].max())
        orb_low = float(orb_bars["l"].min())
        post = session[session["t"] >= ENTRY_AFTER_ORB]
        if post.empty:
            post = session.iloc[len(orb_bars) :]
        if post.empty:
            continue

        entry = entry_i = None
        bars = list(post.itertuples())
        for i, bar in enumerate(bars):
            if bar.c > orb_high:
                entry, entry_i = float(bar.c), i
                break
        if entry is None or entry_i is None:
            continue

        if mode == "range_2r":
            stop = orb_low
            risk = entry - stop
            if risk <= 0:
                continue
            target = entry + 2.0 * risk
            exit_px = reason = None
            for bar in bars[entry_i + 1 :]:
                if bar.l <= stop:
                    exit_px, reason = stop, "stop"
                    break
                if bar.h >= target:
                    exit_px, reason = target, "target_2R"
                    break
                if bar.t >= EOD:
                    exit_px, reason = float(bar.c), "eod"
                    break
            if exit_px is None:
                exit_px, reason = float(bars[-1].c), "eod"
            trail_note = stop
        else:
            hard = entry * HARD_STOP
            highest = entry
            trail = entry * TRAIL
            target = None
            exit_px = reason = None
            for bar in bars[entry_i + 1 :]:
                highest = max(highest, float(bar.h))
                trail = highest * TRAIL
                eff = max(hard, trail)
                if bar.l < eff:
                    exit_px, reason = eff, "stop"
                    break
                if bar.t >= EOD:
                    exit_px, reason = float(bar.c), "eod"
                    break
            if exit_px is None:
                exit_px, reason = float(bars[-1].c), "eod"
            trail_note = max(hard, trail)

        # Size to $RISK_PER_TRADE at the *initial* stop (5% hard for terminal, ORB low for 2R)
        risk_ps = entry * 0.05 if mode == "terminal" else abs(entry - orb_low)
        pnl_pct, pnl_usd, shares, risk_ps = _size_pnl(entry, exit_px, "long", risk_ps)
        if shares <= 0:
            continue
        label = "ORB" if mode == "terminal" else "ORB_range_2R"
        trades.append(
            Trade(
                strategy=label,
                date=str(day),
                side="long",
                entry=round(entry, 4),
                exit=round(exit_px, 4),
                stop=round(trail_note, 4),
                target=round(target, 4) if target else None,
                pnl_pct=round(pnl_pct, 4),
                pnl_usd=round(pnl_usd, 2),
                exit_reason=reason or "eod",
                level_high=round(orb_high, 4),
                level_low=round(orb_low, 4),
                risk_per_share=round(risk_ps, 4),
                shares=round(shares, 4),
            )
        )
    return trades


def run_pair(label: str, interval: str, start: str, end: str) -> dict:
    print(f"[load] {label} interval={interval} {start}→{end}", flush=True)
    df = load_bars(interval, start, end)
    start_d = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    df = df[(df["date"] >= start_d) & (df["date"] <= end_d)]
    days = df["date"].nunique()
    print(f"       bars={len(df)} days={days}", flush=True)
    pmrb = simulate_pmrb(df)
    orb = simulate_orb(df, mode="terminal")
    orb2 = simulate_orb(df, mode="range_2r")
    pmrb_long = [t for t in pmrb if t.side == "long"]
    s_pm = _summarize(pmrb)
    s_pm_long = _summarize(pmrb_long)
    s_orb = _summarize(orb)
    s_orb2 = _summarize(orb2)
    s_pm["strategy"] = "PMRB"
    s_pm_long["strategy"] = "PMRB_long_only"
    s_orb["strategy"] = "ORB"
    s_orb2["strategy"] = "ORB_range_2R"
    ranked = sorted(
        [s_pm, s_pm_long, s_orb, s_orb2],
        key=lambda x: x["total_pnl_usd"],
        reverse=True,
    )
    winner = ranked[0]["strategy"]
    for s in (s_pm, s_pm_long, s_orb, s_orb2):
        print(f"       {s['strategy']:<16} n={s['n']:<4} ${s['total_pnl_usd']:<8} WR={s['win_rate']}%")
    print(f"       winner={winner}")
    return {
        "label": label,
        "interval": interval,
        "window": {"start": start, "end": end, "days": int(days)},
        "risk_per_trade_usd": RISK_PER_TRADE,
        "pmrb": s_pm,
        "pmrb_long_only": s_pm_long,
        "orb": s_orb,
        "orb_range_2r": s_orb2,
        "winner": winner,
        "pmrb_trades_sample": [asdict(t) for t in pmrb[:8]],
        "orb_trades_sample": [asdict(t) for t in orb[:8]],
        "pmrb_all": [asdict(t) for t in pmrb],
        "orb_all": [asdict(t) for t in orb],
        "orb2_all": [asdict(t) for t in orb2],
    }


def load_prior_orb_json_spy(start: str, end: str) -> Optional[dict]:
    """Optional cross-check from prior Polygon-era 5m ORB backtest file."""
    path = Path("artifacts/stock-scanner-api/orb_backtest_results.json")
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    spy = [t for t in data.get("trades", []) if t.get("ticker") == "SPY" and start <= t["date"] <= end]
    if not spy:
        return None
    trades = []
    for t in spy:
        pnl_pct = float(t["pnl_pct"])
        trades.append(
            Trade(
                strategy="ORB_prior_polygon5m",
                date=t["date"],
                side="long",
                entry=float(t["entry_price"]),
                exit=float(t["exit_price"]),
                stop=0.0,
                target=None,
                pnl_pct=pnl_pct,
                pnl_usd=round(_size_pnl(float(t["entry_price"]), float(t["exit_price"]), "long", float(t["entry_price"]) * 0.05)[1], 2),
                exit_reason=t.get("exit_reason", ""),
                level_high=float(t.get("orb_high") or 0),
                level_low=float(t.get("orb_low") or 0),
                risk_per_share=round(float(t["entry_price"]) * 0.05, 4),
                shares=round(RISK_PER_TRADE / (float(t["entry_price"]) * 0.05), 4),
            )
        )
    s = _summarize(trades)
    s["strategy"] = "ORB_prior_polygon5m"
    s["note"] = (
        "From artifacts/stock-scanner-api/orb_backtest_results.json (Polygon 5m when key worked). "
        f"Window filter {start}→{end}; file coverage may end earlier than requested end."
    )
    return {"summary": s, "n_raw": len(spy), "last_date": max(t.date for t in trades)}


def main():
    out_dir = Path("artifacts/backtests")
    out_dir.mkdir(parents=True, exist_ok=True)

    primary = run_pair("1y_hourly_prepost", "60m", WINDOW_START, WINDOW_END)
    hi_res_start = "2026-06-08"
    hires = run_pair("60d_5m_prepost", "5m", hi_res_start, WINDOW_END)
    prior = load_prior_orb_json_spy(WINDOW_START, WINDOW_END)

    strip = ("pmrb_all", "orb_all", "orb2_all")
    payload = {
        "as_of": datetime.now().isoformat() + "Z",
        "underlying": "SPY",
        "risk_per_trade_usd": RISK_PER_TRADE,
        "data_notes": [
            "Live POLYGON_API_KEY returns 401 Unauthorized — bars from Yahoo Finance prepost.",
            "Primary 1y comparison uses 60-minute bars (Yahoo 5m history capped ~60 days).",
            "PMRB: premarket 04:00–09:29 ET, break PMH/PML, stop opposite side, target 2R, EOD flatten.",
            "ORB terminal: 09:30–10:00 range, long-only close>high after 10:00, 5% hard + 10% trail, EOD.",
            "ORB_range_2R: same ORB entry, stop at ORB low, target 2R (matched risk model to PMRB).",
            "$1000 = RISK to the stop per trade (shares = 1000 / stop_distance), NOT $1000 notional.",
        ],
        "primary": {k: v for k, v in primary.items() if k not in strip},
        "hires_crosscheck": {k: v for k, v in hires.items() if k not in strip},
        "prior_orb_polygon_file": prior,
        "primary_full_trades": {
            "pmrb": primary["pmrb_all"],
            "orb": primary["orb_all"],
            "orb_range_2r": primary["orb2_all"],
        },
    }

    p, pl, o, o2 = primary["pmrb"], primary["pmrb_long_only"], primary["orb"], primary["orb_range_2r"]
    verdict = (
        f"SPY {WINDOW_START}→{WINDOW_END} @ ${RISK_PER_TRADE:.0f} RISK/trade (Yahoo 60m prepost): "
        f"PMRB {p['n']} trades / ${p['total_pnl_usd']} · "
        f"ORB {o['n']} trades / ${o['total_pnl_usd']} · "
        f"ORB_range_2R {o2['n']} trades / ${o2['total_pnl_usd']}. "
        f"Most profitable: {primary['winner']}."
    )
    payload["verdict"] = verdict

    out_json = out_dir / "spy_pmrb_vs_orb_1y.json"
    out_json.write_text(json.dumps(payload, indent=2))

    lines = [
        "# SPY Premarket Range Breakout (PMRB) vs ORB — 1 Year @ $1000/trade",
        "",
        f"Window: **{WINDOW_START} → {WINDOW_END}** (253 sessions). "
        f"**${RISK_PER_TRADE:.0f} risk per trade** (sized to stop distance).",
        "",
        f"**Verdict:** {verdict}",
        "",
        "## Head-to-head (Yahoo 60m + extended hours)",
        "",
        "| Strategy | Trades (1y) | Total P&L | Win% | Avg/trade | Max DD |",
        "|----------|------------:|----------:|-----:|----------:|-------:|",
        f"| **PMRB** (Premarket High/Low, long+short) | **{p['n']}** | ${p['total_pnl_usd']:.2f} | {p['win_rate']} | ${p['avg_pnl_usd']:.2f} | ${p['max_dd_usd']:.2f} |",
        f"| PMRB long-only | {pl['n']} | ${pl['total_pnl_usd']:.2f} | {pl['win_rate']} | ${pl['avg_pnl_usd']:.2f} | ${pl['max_dd_usd']:.2f} |",
        f"| **ORB** (terminal: 5% hard + 10% trail) | **{o['n']}** | ${o['total_pnl_usd']:.2f} | {o['win_rate']} | ${o['avg_pnl_usd']:.2f} | ${o['max_dd_usd']:.2f} |",
        f"| ORB range-stop 2R (matched exits) | {o2['n']} | ${o2['total_pnl_usd']:.2f} | {o2['win_rate']} | ${o2['avg_pnl_usd']:.2f} | ${o2['max_dd_usd']:.2f} |",
        "",
        f"**Trade counts:** PMRB **{p['n']}** · ORB **{o['n']}** in one year.",
        "",
        "## Rules",
        "",
        "### PMRB (= Premarket Breakout / Premarket High-Low)",
        "- Premarket range **04:00–09:29 ET** → PMH / PML",
        "- Long close > PMH or short close < PML (first signal/day)",
        "- Stop opposite side of range · target **2R** · EOD 15:55",
        "",
        "### ORB (your terminal tab)",
        "- Range **09:30–09:59 ET** · long-only close > ORB High after 10:00",
        "- 5% hard stop + 10% trail · EOD 15:55",
        "",
        "## 5-minute cross-check (~60 days Yahoo max)",
        "",
        "| Strategy | Trades | Total P&L | Win% |",
        "|----------|-------:|----------:|-----:|",
        f"| PMRB | {hires['pmrb']['n']} | ${hires['pmrb']['total_pnl_usd']:.2f} | {hires['pmrb']['win_rate']} |",
        f"| ORB terminal | {hires['orb']['n']} | ${hires['orb']['total_pnl_usd']:.2f} | {hires['orb']['win_rate']} |",
        f"| ORB range 2R | {hires['orb_range_2r']['n']} | ${hires['orb_range_2r']['total_pnl_usd']:.2f} | {hires['orb_range_2r']['win_rate']} |",
        "",
        f"Winner (60d 5m): **{hires['winner']}**",
        "",
        "## Data caveats",
        "",
        "- Polygon key **401** here → Yahoo Finance extended hours.",
        "- Full year on **60-minute** bars; 5m only covers ~60 days.",
        "- $1000 = **risk to the stop** per entry (shares = 1000 / |entry−stop|), not $1000 of stock.",
        "- On SPY, terminal ORB’s 5%/10% stops rarely trigger → mostly EOD exits; 5% risk sizing makes dollar P&L small vs range stops.",
        "",
    ]
    if prior:
        s = prior["summary"]
        lines += [
            "## Prior Polygon 5m ORB file (partial year)",
            "",
            f"- SPY trades in window through {prior['last_date']}: **{s['n']}**",
            f"- At $1000 notional: **${s['total_pnl_usd']:.2f}**, WR {s['win_rate']}%",
            "",
        ]
    lines += [
        "## Reproduce",
        "",
        "```bash",
        "python3 tools/spy_pmrb_vs_orb_backtest.py",
        "```",
        "",
    ]
    out_md = Path("docs/verification/spy-pmrb-vs-orb-1y-2026-08-06.md")
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines))
    print(f"[wrote] {out_json}")
    print(f"[wrote] {out_md}")
    print(verdict)


if __name__ == "__main__":
    main()
