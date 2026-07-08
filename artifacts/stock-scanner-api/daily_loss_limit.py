"""
daily_loss_limit.py
====================================================================
Pure-math circuit breaker: checks today's realized + unrealized P&L
against a configured threshold. No broker dependency — works purely
off your own DB. Wire `check_daily_loss_limit()` into
pre_decision_risk_gate.py as one of the checks run before any new
decision is acted on.
====================================================================
"""

import datetime as dt
import os
from typing import Dict, Any, Optional

import psycopg2
import psycopg2.extras


# Set this via env var so it can be changed without a code deploy.
# Example: DAILY_LOSS_LIMIT_PCT=2.0 means halt at -2% of account value for the day.
DEFAULT_LOSS_LIMIT_PCT = float(os.environ.get("DAILY_LOSS_LIMIT_PCT", "2.0"))


def get_account_value(db_url: str) -> Optional[float]:
    """
    STATUS (as of 2026-07-01): there is NO real broker/account integration
    anywhere in this codebase (see position_reconciler.py's STATUS block —
    Tradier tokens here are market-data-only, no account/positions access).
    This function therefore has no real account equity to query.

    It returns the value of ACCOUNT_VALUE_BASELINE ONLY if that env var is
    explicitly set (e.g. for deliberately testing this module against a
    manually-chosen paper-trading baseline). If it is NOT set, this returns
    None — it does NOT fall back to a hardcoded number like 10000.

    Why this matters: check_daily_loss_limit() divides today's P&L by this
    value to get a real-looking loss percentage. A silent fake baseline
    (e.g. "10000") produces a loss_pct that LOOKS like a real, trustworthy
    computed number but is actually meaningless — and worse, it can mask a
    real breach or report a false one. Returning None forces the caller to
    fail closed (halt) instead of gating live decisions on a fabricated
    number. Do not reintroduce a hardcoded fallback here.
    """
    raw = os.environ.get("ACCOUNT_VALUE_BASELINE")
    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def get_todays_realized_pnl(db_url: str) -> float:
    """
    Sums the `pnl` column for aiem_paper_trades positions closed today.

    NOTE (fixed 2026-07-08, Joel sign-off Part 1 addendum item 6): this used
    to query `ai_stock_picks`, which is a dead table (0 rows) for the AIEM
    paper-trading domain. The real live table is `aiem_paper_trades`
    (status='CLOSED_AIEM' when a position is closed, `pnl` already computed
    at close time, `exit_date` is the date it closed).
    """
    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COALESCE(SUM(pnl), 0)
                FROM aiem_paper_trades
                WHERE status = 'CLOSED_AIEM'
                  AND exit_date = CURRENT_DATE
            """)
            return float(cur.fetchone()[0])
    finally:
        conn.close()


def check_daily_loss_limit(
    db_url: str,
    loss_limit_pct: float = DEFAULT_LOSS_LIMIT_PCT,
) -> Dict[str, Any]:
    """
    Returns a result dict. `halt_trading` is True if today's loss has
    breached the configured percentage threshold.

    Call this FIRST, before any order-placement logic runs, the same
    way assert_simulation_mode() is called first in simulation_lock.py.

    Fail-closed: if ACCOUNT_VALUE_BASELINE has not been explicitly
    configured (get_account_value() returns None), there is no real or
    intentionally-set number to compute a percentage against. Rather than
    silently skip this check or divide against a hardcoded placeholder,
    this treats the check as breached (halt_trading=True) so trading is
    blocked until a real account value is configured — mirroring the
    "fail closed, not open" DATABASE_URL handling in
    pre_decision_risk_gate.py's run_risk_gate().
    """
    account_value = get_account_value(db_url)

    if account_value is None:
        result = {
            "checked_at": dt.datetime.utcnow().isoformat(),
            "account_value": None,
            "todays_realized_pnl": None,
            "loss_pct": None,
            "loss_limit_pct": loss_limit_pct,
            "halt_trading": True,
            "reason": (
                "ACCOUNT_VALUE_BASELINE is not configured (or is invalid) — "
                "cannot compute a meaningful daily loss percentage without a "
                "real account value. Failing closed: trading halted until a "
                "real account value is configured."
            ),
        }
        _log_breach(db_url, result)
        return result

    realized_pnl = get_todays_realized_pnl(db_url)
    loss_pct = (realized_pnl / account_value) * 100
    halt = loss_pct <= -abs(loss_limit_pct)

    result = {
        "checked_at": dt.datetime.utcnow().isoformat(),
        "account_value": account_value,
        "todays_realized_pnl": realized_pnl,
        "loss_pct": round(loss_pct, 3),
        "loss_limit_pct": loss_limit_pct,
        "halt_trading": halt,
        "reason": None,
    }

    if halt:
        _log_breach(db_url, result)

    return result


def _log_breach(db_url: str, result: Dict[str, Any]) -> None:
    """
    Logs a breach event. Create this table once:

        CREATE TABLE IF NOT EXISTS daily_loss_breach_log (
            id SERIAL PRIMARY KEY,
            checked_at TIMESTAMPTZ NOT NULL,
            account_value DOUBLE PRECISION,
            realized_pnl DOUBLE PRECISION,
            loss_pct DOUBLE PRECISION,
            loss_limit_pct DOUBLE PRECISION,
            resolved BOOLEAN DEFAULT FALSE
        );
    """
    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO daily_loss_breach_log
                    (checked_at, account_value, realized_pnl, loss_pct, loss_limit_pct)
                VALUES (%s, %s, %s, %s, %s)
            """, (
                result["checked_at"],
                result["account_value"],
                result["todays_realized_pnl"],
                result["loss_pct"],
                result["loss_limit_pct"],
            ))
        conn.commit()
    finally:
        conn.close()


def is_daily_loss_breached_today(db_url: str) -> bool:
    """
    Quick check for use as a gate elsewhere: has today already had an
    unresolved breach logged? Use this in addition to (not instead of)
    calling check_daily_loss_limit() live, since this only reflects
    past checks, not the current live P&L.
    """
    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) FROM daily_loss_breach_log
                WHERE checked_at::date = CURRENT_DATE AND resolved = FALSE
            """)
            return cur.fetchone()[0] > 0
    finally:
        conn.close()


if __name__ == "__main__":
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("Set DATABASE_URL to test this module.")
    else:
        print(check_daily_loss_limit(db_url))
