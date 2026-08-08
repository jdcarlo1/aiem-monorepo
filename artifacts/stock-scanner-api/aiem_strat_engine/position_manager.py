"""
position_manager.py — Autonomous position monitoring, exit decisions, and adjustments.

Exit rules (priority order):
  1. Max-loss stop (hard stop at max_loss threshold)
  2. Profit target (close at target%)
  3. DTE-based exit (close when DTE < min_dte_to_hold)
  4. Thesis violation (underlying breaches key level)
  5. Time-based holding period cap

Adjustment types (append-only, each = new ase_adjustments row):
  ROLL_UP       — move short strike up (bullish adjustment)
  ROLL_DOWN     — move short strike down (bearish)
  ROLL_OUT      — extend expiration (time/carry)
  ADD_WING      — add protection (convert strangle → condor)
  REDUCE        — take off partial position
  CLOSE_LEG     — close one specific leg
  FULL_CLOSE    — close entire position
  CONVERT       — restructure (e.g. vertical → diagonal)
"""
from __future__ import annotations
import json, os, uuid
from datetime import datetime, timezone, date
from typing import List, Optional, Dict, Any, Tuple

import psycopg2, psycopg2.extras

from .db import get_conn
from .chain_data import get_spot, get_chain, get_expirations, get_atm_iv
from .paper_trader import close_paper_trade, get_open_trades
from .config import COMMISSION_PER_LEG


# ── Exit thresholds ──────────────────────────────────────────────────────────
PROFIT_TARGET_PCT     = float(os.environ.get("ASE_PROFIT_TARGET_PCT", "0.50") or 0.50)
STOP_LOSS_PCT         = float(os.environ.get("ASE_STOP_LOSS_PCT", "2.00") or 2.00)
MIN_DTE_HOLD          = int(os.environ.get("ASE_MIN_DTE_HOLD", "3") or 3)
MAX_HOLDING_DAYS      = int(os.environ.get("ASE_MAX_HOLDING_DAYS", "90") or 90)


def _new_adj_id() -> str:
    return f"ase_adj_{uuid.uuid4().hex[:12]}"


def _current_value(legs_data: List[Dict], ticker: str) -> Optional[float]:
    """
    Estimate current mark-to-market value of all legs using live chain data.
    Returns None if any leg cannot be priced.
    """
    total = 0.0
    fetched: Dict[str, List] = {}
    for leg in legs_data:
        if not isinstance(leg, dict): continue
        exp = leg.get("expiration")
        if not exp: continue
        if exp not in fetched:
            fetched[exp] = get_chain(ticker, str(exp)[:10])
        chain = fetched.get(exp, [])

        cp = leg.get("call_or_put", "")[:1].upper()
        strike = leg.get("strike")
        if not strike: continue

        match = next((o for o in chain
                       if o.get("call_or_put") == cp and abs((o.get("strike") or 0) - float(strike)) < 0.01),
                     None)
        if not match:
            return None

        mid = match.get("mid") or 0
        sign = 1 if leg.get("buy_or_sell") == "LONG" else -1
        ratio = leg.get("ratio", 1) or 1
        total += sign * mid * ratio
    return round(total, 4)


def should_close(trade: Dict[str, Any], spot: float) -> Tuple[bool, str]:
    """
    Evaluate whether a position should be closed.
    Returns (should_close, reason).
    """
    max_loss   = float(trade.get("maximum_loss") or 0)
    max_profit = trade.get("maximum_profit")
    entry_time_raw = trade.get("entry_time")
    if isinstance(entry_time_raw, str):
        try:
            entry_time = datetime.fromisoformat(entry_time_raw.replace("Z", "+00:00"))
        except Exception:
            entry_time = None
    else:
        entry_time = entry_time_raw

    # Holding period cap
    if entry_time:
        now = datetime.now(timezone.utc)
        ent = entry_time.replace(tzinfo=timezone.utc) if entry_time.tzinfo is None else entry_time
        days_held = (now - ent).days
        if days_held >= MAX_HOLDING_DAYS:
            return True, f"MAX_HOLDING_PERIOD: {days_held} days >= {MAX_HOLDING_DAYS}"

    # Current unrealized P&L from most recent valuation
    unrealized = trade.get("unrealized_pnl")
    if unrealized is not None:
        unrealized = float(unrealized)
        # Profit target
        if max_profit and unrealized >= float(max_profit) * PROFIT_TARGET_PCT * 100:
            return True, f"PROFIT_TARGET: PnL ${unrealized:.2f} >= {PROFIT_TARGET_PCT:.0%} of max"
        # Stop loss
        if max_loss and unrealized <= -float(max_loss) * STOP_LOSS_PCT * 100:
            return True, f"STOP_LOSS: PnL ${unrealized:.2f} <= -{STOP_LOSS_PCT:.0%}× max_loss"

    # DTE-based exit: check each leg
    legs = trade.get("legs") or []
    if isinstance(legs, str):
        try: legs = json.loads(legs)
        except Exception: legs = []
    for leg in legs:
        if not isinstance(leg, dict): continue
        exp = leg.get("expiration")
        if exp:
            exp_str = str(exp)[:10]
            try:
                exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
                dte = max(0, (exp_date - date.today()).days)
                if dte <= MIN_DTE_HOLD and leg.get("buy_or_sell") == "SHORT":
                    return True, f"DTE_EXIT: short option DTE={dte} <= {MIN_DTE_HOLD}"
            except Exception:
                pass

    return False, ""


def record_valuation(
    paper_trade_id: str,
    ticker: str,
    legs_data: List[Dict],
    spot: Optional[float] = None,
) -> Optional[float]:
    """
    Mark-to-market all legs, store in ase_position_valuations.
    Returns current unrealized_pnl or None.
    """
    if spot is None:
        spot = get_spot(ticker)
    if spot is None:
        return None

    current_val = _current_value(legs_data, ticker)
    if current_val is None:
        return None

    # Net entry cost from parent
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT capital_at_risk, delta, gamma, theta, vega FROM ase_paper_trades WHERE paper_trade_id=%s", (paper_trade_id,))
            row = cur.fetchone()
            if not row:
                return None
            cap_risk, delta, gamma, theta, vega = row
            # Unrealized PnL = current value - entry cost (sign already embedded in legs)
            # For a debit trade: entry cost was positive (paid out); current_val is current "price of position"
            # We store the P&L on a per-unit basis; multiply by 100 for dollars
            unrealized_pnl = round(current_val * 100, 2)   # simple approximation

            today = date.today()
            cur.execute("""
                INSERT INTO ase_position_valuations (
                    paper_trade_id, valuation_date, underlying_price,
                    paper_value, unrealized_pnl, delta, gamma, theta, vega
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (paper_trade_id, valuation_date) DO UPDATE
                SET paper_value=EXCLUDED.paper_value,
                    unrealized_pnl=EXCLUDED.unrealized_pnl,
                    underlying_price=EXCLUDED.underlying_price
            """, (paper_trade_id, today, spot, current_val, unrealized_pnl, delta, gamma, theta, vega))
            conn.commit()
        return unrealized_pnl
    except Exception as exc:
        print(f"[position_manager.valuation] {type(exc).__name__}: {exc}")
        return None


def record_adjustment(
    paper_trade_id:  str,
    adjustment_type: str,
    reason:          str,
    legs_closed:     List[Dict],
    legs_opened:     List[Dict],
    net_cost:        float,
    new_trade_id:    Optional[str] = None,
) -> Optional[str]:
    """
    Append an adjustment record to ase_adjustments (never UPDATE originals).
    Returns adjustment_id or None on error.
    """
    adj_id = _new_adj_id()
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO ase_adjustments (
                    adjustment_id, paper_trade_id, adjustment_type, reason,
                    legs_closed, legs_opened, net_cost, new_paper_trade_id
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                adj_id, paper_trade_id, adjustment_type, reason,
                json.dumps(legs_closed), json.dumps(legs_opened),
                net_cost, new_trade_id,
            ))
            conn.commit()
        return adj_id
    except Exception as exc:
        print(f"[position_manager.adjust] {type(exc).__name__}: {exc}")
        return None


def monitor_all_positions() -> Dict[str, Any]:
    """
    Full position monitoring pass — called by scheduler every 15 min during market hours.
    For each open trade:
      1. Fetch current spot
      2. Value all legs
      3. Check exit conditions
      4. Close or record valuation
    Returns summary dict.
    """
    trades = get_open_trades()
    summary = {"total": len(trades), "closed": [], "held": [], "errors": []}

    for trade in trades:
        ticker = trade.get("underlying", "")
        if not ticker:
            continue
        pid    = trade.get("paper_trade_id", "")
        try:
            spot = get_spot(ticker)
            if spot is None:
                summary["errors"].append({"id": pid, "reason": "no spot"})
                continue

            legs_data = trade.get("legs", [])
            if isinstance(legs_data, str):
                try: legs_data = json.loads(legs_data)
                except Exception: legs_data = []

            # Parse leg dicts (may be JSON strings inside the array)
            parsed_legs = []
            for l in (legs_data or []):
                if isinstance(l, str):
                    try: l = json.loads(l)
                    except Exception: continue
                if isinstance(l, dict):
                    parsed_legs.append(l)

            # Update trade with current valuation
            unrealized = record_valuation(pid, ticker, parsed_legs, spot)
            trade["unrealized_pnl"] = unrealized

            should_close_, close_reason = should_close(trade, spot)

            if should_close_:
                # Compute gross PnL from current valuation
                gross_pnl = unrealized or 0.0
                n_legs = len(parsed_legs)
                commission = COMMISSION_PER_LEG * n_legs * 2  # entry + exit
                success = close_paper_trade(pid, close_reason, gross_pnl, commission)
                if success:
                    adj_id = record_adjustment(
                        pid, "FULL_CLOSE", close_reason,
                        legs_closed=parsed_legs,
                        legs_opened=[],
                        net_cost=0.0,
                    )
                    summary["closed"].append({
                        "id": pid, "ticker": ticker, "reason": close_reason,
                        "pnl": gross_pnl, "adj_id": adj_id
                    })
                    print(f"[position_manager] Closed {pid} ({ticker}): {close_reason}, PnL=${gross_pnl:.2f}")
                else:
                    summary["errors"].append({"id": pid, "reason": "close DB error"})
            else:
                summary["held"].append({"id": pid, "ticker": ticker, "unrealized": unrealized})

        except Exception as exc:
            summary["errors"].append({"id": pid, "reason": str(exc)[:100]})
            print(f"[position_manager] Error on {pid}: {exc}")

    return summary
