#!/usr/bin/env python3
"""
SPY multi-strategy options backtest (1 year) — theoretical $100 max risk / trade.

DATA
----
Underlying: Neon `polygon_market_daily` for SPY (Polygon-ingested bars).
Options: Black–Scholes mid prices using rolling realized vol × 1.10 as IV proxy.
  Live Polygon Options API key in this environment returns "Unknown API Key",
  so historical option quotes are synthetic. Documented in verification.

EXIT RULES (industry-standard mechanical — same for all strategies in family)
----------------------------------------------------------------------------
CREDIT structures (credit spreads, iron condor/butterfly, 0DTE credit, BWB):
  • Take profit:  buy back at 50% of initial credit
  • Stop loss:    buy back at 2.0× initial credit
  • Time stop:    close at 21 DTE (0DTE: hold to cash settlement at close)

DEBIT verticals (bull call / bear put):
  • Take profit:  50% of max profit (= 50% of (width − debit))
  • Stop loss:    lose 50% of debit paid
  • Time stop:    21 DTE

LONG vol (straddle / strangle / reverse IC):
  • Take profit:  +50% of premium paid
  • Stop loss:    −50% of premium paid
  • Time stop:    14 DTE

CALENDAR / DIAGONAL / DOUBLE*:
  • Take profit:  50% of debit paid (as MTM credit)
  • Stop loss:    −100% of debit (full debit lost)
  • Time stop:    front-month 7 DTE

RATIO spreads (undefined risk):
  • Take profit:  50% of net credit (or 50% of debit if net debit)
  • Stop loss:    2× |net premium| or short strike breached by 1%
  • Time stop:    21 DTE

SIZING
------
Each entry risks at most $100 of *defined* max loss (fractional contracts allowed
for theoretical comparison). Undefined-risk ratios capped at $100 notional stop.

ENTRY CADENCE
-------------
Weekly (every Monday or first session of week). 0DTE: every session, short
~16-delta credit put OR call based on prior-day return sign (fade).
"""
from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import psycopg2

# ── Black–Scholes ─────────────────────────────────────────────────────────────

def _N(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(S: float, K: float, T: float, r: float, sigma: float, call: bool) -> float:
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return max(0.0, (S - K) if call else (K - S))
    vol = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / vol
    d2 = d1 - vol
    if call:
        return S * _N(d1) - K * math.exp(-r * T) * _N(d2)
    return K * math.exp(-r * T) * _N(-d2) - S * _N(-d1)


def bs_delta(S: float, K: float, T: float, r: float, sigma: float, call: bool) -> float:
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        if call:
            return 1.0 if S > K else 0.0
        return -1.0 if S < K else 0.0
    vol = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / vol
    return _N(d1) if call else (_N(d1) - 1.0)


# ── Market data ───────────────────────────────────────────────────────────────

def load_spy(start: str = "2025-08-01", end: str = "2026-08-04") -> pd.DataFrame:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL required")
    with psycopg2.connect(url, connect_timeout=15) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT scan_date, open_price, high_price, low_price, close_price, volume
            FROM polygon_market_daily
            WHERE ticker = 'SPY' AND scan_date >= %s AND scan_date <= %s
            ORDER BY scan_date
            """,
            (start, end),
        )
        rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = df[c].astype(float)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["ret"] = df["close"].pct_change()
    # 20d realized vol annualized
    df["rv20"] = df["ret"].rolling(20).std() * math.sqrt(252)
    df["iv"] = (df["rv20"] * 1.10).clip(lower=0.08, upper=0.80).bfill().ffill()
    return df.reset_index(drop=True)


def strike_for_delta(S, T, r, sigma, target_delta: float, call: bool) -> float:
    """Binary search strike for approx target delta (puts: negative target e.g. -0.16)."""
    lo, hi = S * 0.5, S * 1.5
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        d = bs_delta(S, mid, T, r, sigma, call)
        if call:
            # Higher strike → lower call delta. Too much delta → raise strike.
            if d > target_delta:
                lo = mid
            else:
                hi = mid
        else:
            # Put delta ∈ [-1, 0]. Higher strike → more ITM → more negative delta.
            # Too ITM (d < target, e.g. -0.5 vs -0.16) → lower the strike.
            if d < target_delta:
                hi = mid
            else:
                lo = mid
    k = 0.5 * (lo + hi)
    # SPY typically $1 strikes near ATM
    return round(k)


# ── Position / trade ──────────────────────────────────────────────────────────

@dataclass
class Leg:
    call: bool
    strike: float
    qty: float  # +1 long, -1 short (per unit structure)
    dte_at_entry: int


@dataclass
class Trade:
    strategy: str
    entry_i: int
    entry_date: date
    exit_i: Optional[int] = None
    exit_date: Optional[date] = None
    exit_reason: str = ""
    legs: Tuple[Leg, ...] = ()
    entry_premium: float = 0.0  # net credit >0 credit, <0 debit (per share)
    exit_premium: float = 0.0
    contracts: float = 1.0
    pnl: float = 0.0
    max_risk: float = 100.0
    family: str = "credit"


def mtm_structure(legs: Tuple[Leg, ...], S: float, T_years: float, r: float, sigma: float) -> float:
    """Net value to CLOSE (pay to buy back shorts / sell longs) — positive = cost to close."""
    total = 0.0
    for leg in legs:
        # Remaining time for this leg: scale by dte_at_entry fraction if multi-expiry
        # Caller passes per-leg T via encoding: we store dte_at_entry and recompute outside.
        px = bs_price(S, leg.strike, max(T_years, 1e-6), r, sigma, leg.call)
        # qty>0 long: closing = sell = -px; qty<0 short: closing = buy = +px
        # Net close cost = -qty * px  (short qty=-1 → +px)
        total += (-leg.qty) * px
    return total


def structure_mtm_with_dtes(
    legs: Tuple[Leg, ...],
    S: float,
    calendar_days_elapsed: int,
    r: float,
    sigma: float,
) -> float:
    total = 0.0
    for leg in legs:
        rem_days = max(leg.dte_at_entry - calendar_days_elapsed, 0)
        T = rem_days / 365.0
        px = bs_price(S, leg.strike, T, r, sigma, leg.call) if rem_days > 0 else max(
            0.0, (S - leg.strike) if leg.call else (leg.strike - S)
        )
        total += (-leg.qty) * px
    return total


# ── Builders ──────────────────────────────────────────────────────────────────

R = 0.045
WIDTH = 5.0  # $5 SPY spreads


def _size_from_risk(max_loss_per_share: float, risk_budget: float = 100.0) -> float:
    if max_loss_per_share <= 0:
        return 0.0
    # dollars = max_loss_per_share * 100 * contracts
    return risk_budget / (max_loss_per_share * 100.0)


def build_bull_put(S, iv, dte=45) -> Tuple[Tuple[Leg, ...], float, float, str]:
    T = dte / 365.0
    short_k = strike_for_delta(S, T, R, iv, -0.16, call=False)
    long_k = short_k - WIDTH
    legs = (Leg(False, short_k, -1, dte), Leg(False, long_k, +1, dte))
    credit = bs_price(S, short_k, T, R, iv, False) - bs_price(S, long_k, T, R, iv, False)
    max_loss = WIDTH - credit
    return legs, credit, max_loss, "credit"


def build_bear_call(S, iv, dte=45) -> Tuple[Tuple[Leg, ...], float, float, str]:
    T = dte / 365.0
    short_k = strike_for_delta(S, T, R, iv, 0.16, call=True)
    long_k = short_k + WIDTH
    legs = (Leg(True, short_k, -1, dte), Leg(True, long_k, +1, dte))
    credit = bs_price(S, short_k, T, R, iv, True) - bs_price(S, long_k, T, R, iv, True)
    max_loss = WIDTH - credit
    return legs, credit, max_loss, "credit"


def build_iron_condor(S, iv, dte=45):
    T = dte / 365.0
    sp = strike_for_delta(S, T, R, iv, -0.16, False)
    lp = sp - WIDTH
    sc = strike_for_delta(S, T, R, iv, 0.16, True)
    lc = sc + WIDTH
    legs = (
        Leg(False, sp, -1, dte), Leg(False, lp, +1, dte),
        Leg(True, sc, -1, dte), Leg(True, lc, +1, dte),
    )
    credit = (
        bs_price(S, sp, T, R, iv, False) - bs_price(S, lp, T, R, iv, False)
        + bs_price(S, sc, T, R, iv, True) - bs_price(S, lc, T, R, iv, True)
    )
    max_loss = WIDTH - credit
    return legs, credit, max_loss, "credit"


def build_iron_butterfly(S, iv, dte=45):
    T = dte / 365.0
    atm = round(S)
    legs = (
        Leg(False, atm, -1, dte), Leg(False, atm - WIDTH, +1, dte),
        Leg(True, atm, -1, dte), Leg(True, atm + WIDTH, +1, dte),
    )
    credit = (
        bs_price(S, atm, T, R, iv, False) - bs_price(S, atm - WIDTH, T, R, iv, False)
        + bs_price(S, atm, T, R, iv, True) - bs_price(S, atm + WIDTH, T, R, iv, True)
    )
    max_loss = WIDTH - credit
    return legs, credit, max_loss, "credit"


def build_bwb(S, iv, dte=45):
    """Broken-wing put butterfly: +1 / −2 / +1 with asymmetric wings (wider ITM wing).

    Strikes: K−10 / K / K+5 relative to ATM−5 body — net credit common; max loss on
    the broken (narrow) side ≈ |wing_diff − credit|.
    """
    T = dte / 365.0
    body = round(S) - 5
    k_low = body - 10   # wide wing
    k_mid = body
    k_high = body + 5   # broken (narrow) wing
    legs = (
        Leg(False, k_low, +1, dte),
        Leg(False, k_mid, -2, dte),
        Leg(False, k_high, +1, dte),
    )
    net = (
        -bs_price(S, k_low, T, R, iv, False)
        + 2 * bs_price(S, k_mid, T, R, iv, False)
        - bs_price(S, k_high, T, R, iv, False)
    )
    # net > 0 ⇒ credit. Risk on broken wing ≈ 5 − credit when credit structure.
    if net >= 0:
        max_loss = max(5.0 - net, 0.25)
        return legs, net, max_loss, "credit"
    max_loss = abs(net) + 5.0
    return legs, net, max_loss, "debit"


def build_bull_call(S, iv, dte=45):
    T = dte / 365.0
    long_k = strike_for_delta(S, T, R, iv, 0.45, True)
    short_k = long_k + WIDTH
    legs = (Leg(True, long_k, +1, dte), Leg(True, short_k, -1, dte))
    debit = bs_price(S, long_k, T, R, iv, True) - bs_price(S, short_k, T, R, iv, True)
    max_loss = debit
    return legs, -debit, max_loss, "debit"


def build_bear_put(S, iv, dte=45):
    T = dte / 365.0
    long_k = strike_for_delta(S, T, R, iv, -0.45, False)
    short_k = long_k - WIDTH
    legs = (Leg(False, long_k, +1, dte), Leg(False, short_k, -1, dte))
    debit = bs_price(S, long_k, T, R, iv, False) - bs_price(S, short_k, T, R, iv, False)
    max_loss = debit
    return legs, -debit, max_loss, "debit"


def build_call_ratio(S, iv, dte=45):
    """1×2 call ratio: long ATM, short 2× OTM. Undefined upside risk — size to $100 stop."""
    T = dte / 365.0
    long_k = round(S)
    short_k = long_k + WIDTH
    legs = (Leg(True, long_k, +1, dte), Leg(True, short_k, -2, dte))
    credit = -bs_price(S, long_k, T, R, iv, True) + 2 * bs_price(S, short_k, T, R, iv, True)
    # Stop budget: 2× |premium| per share (matches exit rule); floor so sizing stays sane
    max_loss = max(2.0 * abs(credit), 1.0)
    return legs, credit, max_loss, "ratio"


def build_put_ratio(S, iv, dte=45):
    """1×2 put ratio: long ATM, short 2× OTM. Undefined downside risk — size to $100 stop."""
    T = dte / 365.0
    long_k = round(S)
    short_k = long_k - WIDTH
    legs = (Leg(False, long_k, +1, dte), Leg(False, short_k, -2, dte))
    credit = -bs_price(S, long_k, T, R, iv, False) + 2 * bs_price(S, short_k, T, R, iv, False)
    max_loss = max(2.0 * abs(credit), 1.0)
    return legs, credit, max_loss, "ratio"


def build_calendar(S, iv, front=30, back=60):
    # sell front ATM call, buy back ATM call
    k = round(S)
    Tf, Tb = front / 365.0, back / 365.0
    legs = (Leg(True, k, -1, front), Leg(True, k, +1, back))
    debit = -bs_price(S, k, Tf, R, iv, True) + bs_price(S, k, Tb, R, iv * 0.98, True)
    return legs, -debit, abs(debit), "calendar"


def build_diagonal(S, iv, front=30, back=60):
    k_short = round(S + 5)
    k_long = round(S)
    Tf, Tb = front / 365.0, back / 365.0
    legs = (Leg(True, k_short, -1, front), Leg(True, k_long, +1, back))
    debit = -bs_price(S, k_short, Tf, R, iv, True) + bs_price(S, k_long, Tb, R, iv * 0.98, True)
    return legs, -debit, abs(debit) + 5, "calendar"


def build_double_calendar(S, iv, front=30, back=60):
    kc = round(S + 5)
    kp = round(S - 5)
    Tf, Tb = front / 365.0, back / 365.0
    legs = (
        Leg(True, kc, -1, front), Leg(True, kc, +1, back),
        Leg(False, kp, -1, front), Leg(False, kp, +1, back),
    )
    debit = (
        -bs_price(S, kc, Tf, R, iv, True) + bs_price(S, kc, Tb, R, iv * 0.98, True)
        - bs_price(S, kp, Tf, R, iv, False) + bs_price(S, kp, Tb, R, iv * 0.98, False)
    )
    return legs, -debit, abs(debit), "calendar"


def build_double_diagonal(S, iv, front=30, back=60):
    sc, lc = round(S + 5), round(S + 10)
    sp, lp = round(S - 5), round(S - 10)
    Tf, Tb = front / 365.0, back / 365.0
    legs = (
        Leg(True, sc, -1, front), Leg(True, lc, +1, back),
        Leg(False, sp, -1, front), Leg(False, lp, +1, back),
    )
    debit = (
        -bs_price(S, sc, Tf, R, iv, True) + bs_price(S, lc, Tb, R, iv * 0.98, True)
        - bs_price(S, sp, Tf, R, iv, False) + bs_price(S, lp, Tb, R, iv * 0.98, False)
    )
    return legs, -debit, abs(debit) + 5, "calendar"


def build_long_butterfly(S, iv, dte=45):
    T = dte / 365.0
    k2 = round(S)
    k1, k3 = k2 - WIDTH, k2 + WIDTH
    legs = (Leg(True, k1, +1, dte), Leg(True, k2, -2, dte), Leg(True, k3, +1, dte))
    debit = (
        bs_price(S, k1, T, R, iv, True)
        - 2 * bs_price(S, k2, T, R, iv, True)
        + bs_price(S, k3, T, R, iv, True)
    )
    return legs, -debit, abs(debit), "debit"


def build_reverse_ic(S, iv, dte=45):
    """Long strangle wings / short ATM — reverse iron condor (long vol)."""
    T = dte / 365.0
    lp = strike_for_delta(S, T, R, iv, -0.25, False)
    lc = strike_for_delta(S, T, R, iv, 0.25, True)
    sp = round(S) - 2
    sc = round(S) + 2
    legs = (
        Leg(False, lp, +1, dte), Leg(False, sp, -1, dte),
        Leg(True, sc, -1, dte), Leg(True, lc, +1, dte),
    )
    debit = (
        bs_price(S, lp, T, R, iv, False) - bs_price(S, sp, T, R, iv, False)
        - bs_price(S, sc, T, R, iv, True) + bs_price(S, lc, T, R, iv, True)
    )
    return legs, -debit, abs(debit), "debit"


def build_straddle(S, iv, dte=45):
    T = dte / 365.0
    k = round(S)
    legs = (Leg(True, k, +1, dte), Leg(False, k, +1, dte))
    debit = bs_price(S, k, T, R, iv, True) + bs_price(S, k, T, R, iv, False)
    return legs, -debit, debit, "long_vol"


def build_strangle(S, iv, dte=45):
    T = dte / 365.0
    kc = strike_for_delta(S, T, R, iv, 0.25, True)
    kp = strike_for_delta(S, T, R, iv, -0.25, False)
    legs = (Leg(True, kc, +1, dte), Leg(False, kp, +1, dte))
    debit = bs_price(S, kc, T, R, iv, True) + bs_price(S, kp, T, R, iv, False)
    return legs, -debit, debit, "long_vol"


def build_0dte_credit(S, iv, prior_ret: float):
    """Fade: after up day sell call credit; after down day sell put credit. 0–1 DTE."""
    dte = 1
    if prior_ret >= 0:
        return build_bear_call(S, max(iv, 0.12), dte=dte)
    return build_bull_put(S, max(iv, 0.12), dte=dte)


STRATEGIES: Dict[str, Callable] = {
    "0DTE Credit Spreads": build_0dte_credit,
    "Bull Put Credit Spread": lambda S, iv, **kw: build_bull_put(S, iv),
    "Bear Call Credit Spread": lambda S, iv, **kw: build_bear_call(S, iv),
    "Broken-Wing Butterfly": lambda S, iv, **kw: build_bwb(S, iv),
    "Iron Condor": lambda S, iv, **kw: build_iron_condor(S, iv),
    "Bull Call Debit Spread": lambda S, iv, **kw: build_bull_call(S, iv),
    "Bear Put Debit Spread": lambda S, iv, **kw: build_bear_put(S, iv),
    "Iron Butterfly": lambda S, iv, **kw: build_iron_butterfly(S, iv),
    "Call Ratio Spread": lambda S, iv, **kw: build_call_ratio(S, iv),
    "Put Ratio Spread": lambda S, iv, **kw: build_put_ratio(S, iv),
    "Calendar Spread": lambda S, iv, **kw: build_calendar(S, iv),
    "Diagonal Spread": lambda S, iv, **kw: build_diagonal(S, iv),
    "Double Calendar": lambda S, iv, **kw: build_double_calendar(S, iv),
    "Double Diagonal": lambda S, iv, **kw: build_double_diagonal(S, iv),
    "Long Butterfly": lambda S, iv, **kw: build_long_butterfly(S, iv),
    "Reverse Iron Condor": lambda S, iv, **kw: build_reverse_ic(S, iv),
    "Long Straddle": lambda S, iv, **kw: build_straddle(S, iv),
    "Long Strangle": lambda S, iv, **kw: build_strangle(S, iv),
}


def should_exit(family: str, entry_prem: float, close_cost: float, rem_dte: int, is_0dte: bool) -> Optional[str]:
    """
    entry_prem: credit >0, debit stored as negative.
    close_cost: mark-to-market cost to close (same sign convention as -entry for flat).
    PnL per share ≈ entry_prem - close_cost for credits (sold for C, buy back for B → C-B).
    For debit entry_prem=-D, pnl = -D - close_cost where close_cost is negative when long has value...
    We define entry_cashflow = +credit or -debit (cash in at open).
    exit_cashflow = -close_cost (cash when closing; close_cost is price paid to flatten).
    pnl_share = entry_cashflow + exit_cashflow = entry_prem - close_cost.
    """
    pnl = entry_prem - close_cost
    credit = entry_prem if entry_prem > 0 else None
    debit = -entry_prem if entry_prem < 0 else None

    if is_0dte:
        if rem_dte <= 0:
            return "expiry"
        # same-day: 50% / 2x still apply
    if family == "credit" and credit:
        if pnl >= 0.50 * credit:
            return "take_profit_50pct"
        if pnl <= -2.0 * credit:
            return "stop_2x_credit"
        if rem_dte <= 21 and not is_0dte:
            return "time_21dte"
        if rem_dte <= 0:
            return "expiry"
    elif family == "debit" and debit:
        # max profit approx unknown; use 50% of debit as TP proxy for verticals
        if pnl >= 0.50 * debit:
            return "take_profit_50pct_debit"
        if pnl <= -0.50 * debit:
            return "stop_50pct_debit"
        if rem_dte <= 21:
            return "time_21dte"
        if rem_dte <= 0:
            return "expiry"
    elif family == "long_vol" and debit:
        if pnl >= 0.50 * debit:
            return "take_profit_50pct"
        if pnl <= -0.50 * debit:
            return "stop_50pct"
        if rem_dte <= 14:
            return "time_14dte"
        if rem_dte <= 0:
            return "expiry"
    elif family == "calendar" and debit:
        if pnl >= 0.50 * debit:
            return "take_profit_50pct"
        if pnl <= -1.0 * debit:
            return "stop_full_debit"
        if rem_dte <= 7:
            return "time_front_7dte"
        if rem_dte <= 0:
            return "expiry"
    elif family == "ratio":
        abs_p = abs(entry_prem) or 0.5
        if pnl >= 0.50 * abs_p:
            return "take_profit_50pct"
        if pnl <= -2.0 * abs_p:
            return "stop_2x"
        if rem_dte <= 21:
            return "time_21dte"
        if rem_dte <= 0:
            return "expiry"
    else:
        if rem_dte <= 0:
            return "expiry"
    return None


def run_strategy(name: str, df: pd.DataFrame, risk: float = 100.0) -> List[Trade]:
    trades: List[Trade] = []
    open_tr: Optional[Trade] = None
    builder = STRATEGIES[name]
    is_0dte = name.startswith("0DTE")

    for i in range(25, len(df)):
        row = df.iloc[i]
        S, iv = float(row["close"]), float(row["iv"])
        d = row["date"]

        # Manage open trade
        if open_tr is not None:
            elapsed = (d - open_tr.entry_date).days
            min_dte = min(leg.dte_at_entry for leg in open_tr.legs)
            rem = min_dte - elapsed
            close_cost = structure_mtm_with_dtes(open_tr.legs, S, elapsed, R, iv)
            reason = should_exit(open_tr.family, open_tr.entry_premium, close_cost, rem, is_0dte)
            # 0DTE always close same day at EOD if not already
            if is_0dte and rem <= 0 and not reason:
                reason = "expiry"
            if reason:
                open_tr.exit_i = i
                open_tr.exit_date = d
                open_tr.exit_reason = reason
                open_tr.exit_premium = close_cost
                pnl_share = open_tr.entry_premium - close_cost
                open_tr.pnl = pnl_share * 100.0 * open_tr.contracts
                trades.append(open_tr)
                open_tr = None

        if open_tr is not None:
            continue

        # Entry cadence
        if is_0dte:
            prior = float(df.iloc[i - 1]["ret"] or 0)
            legs, prem, max_loss, family = builder(S, iv, prior_ret=prior)
        else:
            # weekly: Monday=0
            if d.weekday() != 0:
                continue
            legs, prem, max_loss, family = builder(S, iv)

        # Skip pathological / zero-edge structures (credit ≈ full width, negative risk, etc.)
        if max_loss <= 0.10 or abs(prem) < 0.05:
            continue
        if family == "credit" and prem > 0 and max_loss > WIDTH * 1.5:
            continue
        contracts = _size_from_risk(max_loss, risk)
        if contracts <= 0:
            continue
        open_tr = Trade(
            strategy=name,
            entry_i=i,
            entry_date=d,
            legs=legs,
            entry_premium=prem,
            contracts=contracts,
            max_risk=risk,
            family=family,
        )

    # Force close last open
    if open_tr is not None:
        i = len(df) - 1
        row = df.iloc[i]
        S, iv = float(row["close"]), float(row["iv"])
        elapsed = (row["date"] - open_tr.entry_date).days
        close_cost = structure_mtm_with_dtes(open_tr.legs, S, elapsed, R, iv)
        open_tr.exit_i = i
        open_tr.exit_date = row["date"]
        open_tr.exit_reason = "end_of_sample"
        open_tr.exit_premium = close_cost
        open_tr.pnl = (open_tr.entry_premium - close_cost) * 100.0 * open_tr.contracts
        trades.append(open_tr)

    return trades


def summarize(trades: List[Trade]) -> dict:
    if not trades:
        return {
            "n": 0, "total_pnl": 0.0, "avg_pnl": 0.0, "win_rate": None,
            "profit_factor": None, "max_dd": 0.0, "expectancy": 0.0,
        }
    pnls = [t.pnl for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    eq = np.cumsum(pnls)
    peak = np.maximum.accumulate(eq)
    dd = float((eq - peak).min()) if len(eq) else 0.0
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "n": len(trades),
        "total_pnl": round(sum(pnls), 2),
        "avg_pnl": round(float(np.mean(pnls)), 2),
        "win_rate": round(100.0 * len(wins) / len(pnls), 1),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "max_dd": round(dd, 2),
        "expectancy": round(float(np.mean(pnls)), 2),
        "exit_reasons": {
            k: sum(1 for t in trades if t.exit_reason == k)
            for k in sorted({t.exit_reason for t in trades})
        },
    }


def verification_checks(df: pd.DataFrame) -> List[dict]:
    """Accuracy / sanity checks — not optional marketing."""
    checks = []
    S = float(df.iloc[-1]["close"])
    iv = float(df.iloc[-1]["iv"])
    T = 45 / 365.0
    # Put-call parity approx at ATM
    k = round(S)
    c = bs_price(S, k, T, R, iv, True)
    p = bs_price(S, k, T, R, iv, False)
    lhs = c - p
    rhs = S - k * math.exp(-R * T)
    parity_err = abs(lhs - rhs)
    checks.append({
        "name": "put_call_parity_atm",
        "pass": parity_err < 0.05,
        "detail": f"|C-P - (S-Ke^-rT)|={parity_err:.4f} (tol 0.05)",
    })
    # Intrinsic bounds
    call_ok = c >= max(0, S - k) - 1e-6
    put_ok = p >= max(0, k - S) - 1e-6
    checks.append({"name": "intrinsic_bounds", "pass": call_ok and put_ok, "detail": f"C={c:.3f} P={p:.3f}"})
    # Credit spread max loss identity + OTM 16Δ short put
    legs, credit, max_loss, _ = build_bull_put(S, iv, 45)
    short_put_k = legs[0].strike
    put_delta = bs_delta(S, short_put_k, T, R, iv, False)
    checks.append({
        "name": "bull_put_short_otm_16delta",
        "pass": short_put_k < S and -0.22 <= put_delta <= -0.10,
        "detail": f"S={S:.2f} short_k={short_put_k} delta={put_delta:.3f} credit={credit:.3f}",
    })
    checks.append({
        "name": "bull_put_max_loss_identity",
        "pass": abs(max_loss - (WIDTH - credit)) < 1e-9 and 0.15 < credit < WIDTH - 0.10,
        "detail": f"credit={credit:.3f} max_loss={max_loss:.3f} width={WIDTH}",
    })
    # Expiry settlement: deep ITM put credit spread loss ≈ width - credit
    S2 = legs[0].strike - 10  # below short put
    settle = 0.0
    for leg in legs:
        intrinsic = max(0.0, (leg.strike - S2) if not leg.call else (S2 - leg.strike))
        settle += (-leg.qty) * intrinsic  # cost to close at expiry
    pnl = credit - settle
    checks.append({
        "name": "bull_put_max_loss_at_expiry_breach",
        "pass": abs(pnl - (-max_loss)) < 0.15,
        "detail": f"pnl_at_breach={pnl:.3f} expected={-max_loss:.3f}",
    })
    _, ic_credit, ic_ml, _ = build_iron_condor(S, iv, 45)
    checks.append({
        "name": "iron_condor_defined_risk",
        "pass": ic_credit > 0.15 and ic_ml > 0.10 and abs(ic_ml - (WIDTH - ic_credit)) < 1e-9,
        "detail": f"credit={ic_credit:.3f} max_loss={ic_ml:.3f}",
    })
    # Data coverage
    checks.append({
        "name": "spy_bars_1y",
        "pass": len(df) >= 240,
        "detail": f"n={len(df)} from {df.iloc[0]['date']} to {df.iloc[-1]['date']}",
    })
    return checks


def main():
    out_dir = Path("artifacts/backtests")
    out_dir.mkdir(parents=True, exist_ok=True)

    df = load_spy()
    print(f"[data] SPY bars={len(df)} {df.iloc[0]['date']} → {df.iloc[-1]['date']} "
          f"close {df.iloc[0]['close']:.2f}→{df.iloc[-1]['close']:.2f}")

    checks = verification_checks(df)
    for c in checks:
        print(f"[verify] {'PASS' if c['pass'] else 'FAIL'} {c['name']}: {c['detail']}")

    results = []
    all_trades = {}
    for name in STRATEGIES:
        print(f"[run] {name} …", flush=True)
        trades = run_strategy(name, df, risk=100.0)
        summ = summarize(trades)
        summ["strategy"] = name
        results.append(summ)
        all_trades[name] = [
            {
                "entry": str(t.entry_date),
                "exit": str(t.exit_date),
                "reason": t.exit_reason,
                "pnl": round(t.pnl, 2),
                "contracts": round(t.contracts, 4),
                "entry_prem": round(t.entry_premium, 4),
                "family": t.family,
            }
            for t in trades
        ]
        print(f"       n={summ['n']} total=${summ['total_pnl']} wr={summ['win_rate']}%")

    results.sort(key=lambda r: r["total_pnl"], reverse=True)
    payload = {
        "as_of": datetime.now().isoformat() + "Z",
        "underlying": "SPY",
        "window": {"start": str(df.iloc[0]["date"]), "end": str(df.iloc[-1]["date"]), "bars": len(df)},
        "risk_per_trade_usd": 100,
        "data_notes": [
            "Underlying from Neon polygon_market_daily (Polygon-ingested).",
            "Live Polygon API key returned Unknown API Key — option prices are Black–Scholes "
            "with IV = 1.10 × 20d realized vol (not live option NBBO).",
            "Theoretical fractional contracts sized to $100 max defined risk.",
        ],
        "exit_rules": {
            "credit": "TP 50% of credit; SL 2× credit; time 21 DTE (0DTE → EOD)",
            "debit_vertical": "TP 50% of debit; SL 50% of debit; time 21 DTE",
            "long_vol": "TP +50% premium; SL −50%; time 14 DTE",
            "calendar_family": "TP 50% debit; SL −100% debit; front 7 DTE",
            "ratio": "TP 50% |premium|; SL 2× |premium|; time 21 DTE",
        },
        "verification": checks,
        "ranking": results,
        "trades_sample": {k: v[:5] for k, v in all_trades.items()},
        "winner": results[0]["strategy"] if results else None,
    }
    out_json = out_dir / "spy_options_strategies_1y.json"
    out_json.write_text(json.dumps(payload, indent=2))
    # markdown table
    lines = [
        "# SPY Options Strategies — 1 Year Backtest ($100 risk / trade)",
        "",
        f"Window: **{payload['window']['start']} → {payload['window']['end']}** ({payload['window']['bars']} sessions).",
        "",
        "## Ranking by total P&L",
        "",
        "| Rank | Strategy | Trades | Total P&L | Win% | Avg/trade | Max DD | Profit factor |",
        "|-----:|----------|-------:|----------:|-----:|----------:|-------:|--------------:|",
    ]
    for i, r in enumerate(results, 1):
        lines.append(
            f"| {i} | {r['strategy']} | {r['n']} | ${r['total_pnl']:.2f} | "
            f"{r['win_rate'] if r['win_rate'] is not None else '—'} | "
            f"${r['avg_pnl']:.2f} | ${r['max_dd']:.2f} | "
            f"{r['profit_factor'] if r['profit_factor'] is not None else '—'} |"
        )
    lines += [
        "",
        f"**Most profitable (this window):** {payload['winner']}",
        "",
        "## Exit rules used",
        "",
        "- **Credit structures:** take profit at **50% of credit**, stop at **2× credit**, time stop **21 DTE** (0DTE closed at EOD).",
        "- **Debit verticals:** TP **50% of debit**, SL **50% of debit**, time **21 DTE**.",
        "- **Long straddle/strangle:** TP **+50%**, SL **−50%**, time **14 DTE**.",
        "- **Calendars/diagonals:** TP **50% of debit**, SL **full debit**, front **7 DTE**.",
        "",
        "## Data / accuracy caveats",
        "",
        "- Underlying = Neon `polygon_market_daily` SPY (Polygon-ingested).",
        "- **Live Polygon options API key is invalid** in this environment → premiums are **Black–Scholes** with IV = 1.10×20d RV, not exchange NBBO.",
        "- No slippage, dividends, early assignment, or borrow. Strong SPY uptrend in sample favors bullish credit/debit.",
        "",
        "## Verification checks",
        "",
    ]
    for c in checks:
        lines.append(f"- {'✅' if c['pass'] else '❌'} **{c['name']}**: {c['detail']}")
    out_md = Path("docs/verification/spy-options-strategies-backtest-1y-2026-08-06.md")
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines) + "\n")
    print(f"[wrote] {out_json}")
    print(f"[wrote] {out_md}")
    print(f"[winner] {payload['winner']} ${results[0]['total_pnl']}")
    if not all(c["pass"] for c in checks):
        sys.exit(2)


if __name__ == "__main__":
    main()
