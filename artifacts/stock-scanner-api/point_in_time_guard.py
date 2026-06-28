"""
point_in_time_guard.py

Point-in-time data integrity layer for StockScanner AI / AIEM.

PROBLEM THIS SOLVES
--------------------
scanner.fetch_stock_data() and scanner.analyze_ticker() call yfinance directly
with no concept of "as of what date." Two concrete leaks follow from this:

1. auto_adjust=True price history is RETROACTIVELY adjusted for splits/dividends
   that happen AFTER the historical date in question. A backtest row dated
   "2024-03-03" can contain a price that was adjusted using a stock split that
   happened in 2025 - information that did not exist on 2024-03-03.

2. yf.Ticker(ticker).info (market_cap, sector, pe_ratio, etc.) is always
   CURRENT-DAY data. There is no historical .info call in yfinance. Any
   backtest or AIEM training row that attaches info fields to a historical
   signal_date is attaching today's company facts to a past date - a hard
   lookahead leak that can inflate backtest accuracy.

This module does NOT change live scanning behavior (today's scans don't need
historical fidelity - today's data IS today's data). It adds a guard
specifically for code paths that compute features for PAST dates: AIEM's
backtests, walk-forward splits, and training pipelines.

REPLIT INTEGRATION NOTES
------------------------
- Drop this file in alongside scanner.py / data_prep.py.
- In every backtest_*.py and *_retrain*.py / *_training*.py module, replace
  calls to scanner.fetch_stock_data(ticker) with
  fetch_point_in_time_prices(ticker, as_of_date=<that row's signal_date>).
- Call assert_no_future_leakage(...) at the end of any feature-computation
  function used inside a backtest loop, right before the result is returned.
- Wire snapshot_daily_fundamentals() as a new scheduled job (same pattern as
  the other jobs already registered in main.py's scheduler block) so that,
  going forward, fundamentals have a real point-in-time source instead of
  always-current .info calls. This does not retroactively fix old backtests -
  that data is genuinely unrecoverable from yfinance - but it stops the leak
  from continuing into new data.
"""

import warnings
from datetime import datetime, date

import pandas as pd


class LookaheadViolation(Exception):
    """Raised when a point-in-time computation uses data beyond its as_of_date."""
    pass


def fetch_point_in_time_prices(ticker: str, as_of_date, period: str = "2y") -> pd.DataFrame:
    """
    Drop-in replacement for scanner.fetch_stock_data() for any code path
    computing a feature/signal for a HISTORICAL date (backtests, training,
    walk-forward validation, AIEM hypothesis testing on past data).

    Fetches with auto_adjust=False (raw, unadjusted prices) and trims
    everything strictly after as_of_date. Raw prices avoid the "future split
    bleeds into the past" leak. If you need split/dividend-adjusted returns,
    adjust manually within the trimmed window using only corporate actions
    that were already public as of as_of_date.

    as_of_date: str ('YYYY-MM-DD') or datetime.date
    """
    import yfinance as yf

    if isinstance(as_of_date, str):
        as_of_date = datetime.strptime(as_of_date, "%Y-%m-%d").date()

    data = yf.download(ticker, period=period, progress=False, auto_adjust=False)
    if data is None or data.empty:
        return None
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)

    data = data[data.index.date <= as_of_date]

    if data.empty:
        raise LookaheadViolation(
            f"No data exists for {ticker} on or before {as_of_date} - "
            f"check that as_of_date isn't before the ticker's listing date."
        )
    return data


def assert_no_future_leakage(df: pd.DataFrame, as_of_date, date_col: str = None) -> None:
    """
    Hard guard to drop into any backtest/training function right before it
    returns computed features. Raises LookaheadViolation if any row's date
    (index or date_col) is after as_of_date.

    Usage:
        feats = compute_features(df)
        assert_no_future_leakage(feats, as_of_date="2024-03-03")
        return feats
    """
    if isinstance(as_of_date, str):
        as_of_date = datetime.strptime(as_of_date, "%Y-%m-%d").date()

    if date_col:
        dates = pd.to_datetime(df[date_col]).dt.date
    else:
        dates = pd.to_datetime(df.index).date

    violations = [d for d in dates if d > as_of_date]
    if violations:
        raise LookaheadViolation(
            f"{len(violations)} row(s) dated after as_of_date={as_of_date}. "
            f"First violation: {min(violations)}. This is a lookahead leak."
        )


def get_static_company_facts_warning(ticker: str) -> dict:
    """
    yfinance's .info has NO historical mode - it is always "as of right now."
    There is no safe way to retroactively know what market_cap/sector/pe_ratio
    WAS on a past date using yfinance alone.

    Call this from any backtest/training feature function that currently
    uses stock.info fields, so the leak is at minimum logged loudly instead
    of silent. Long-term fix: pull historical fundamentals from a vendor that
    actually supports point-in-time fundamentals, or use
    snapshot_daily_fundamentals() below to start building your own
    point-in-time table going forward.
    """
    warnings.warn(
        f"[LOOKAHEAD RISK] {ticker}: static info fields (market_cap, sector, "
        f"pe_ratio, etc.) are CURRENT-DAY only in yfinance. If this value is "
        f"being attached to a historical signal_date, it is a lookahead leak.",
        stacklevel=2,
    )
    return {
        "market_cap": None, "sector": None, "pe_ratio": None,
        "_warning": "static info has no point-in-time source - see warning log",
    }


def snapshot_daily_fundamentals(tickers: list, db_conn) -> None:
    """
    The real long-term fix for the .info leak: starting TODAY, snapshot
    fundamentals daily into a table keyed by (ticker, snapshot_date). Going
    forward, AIEM's backtests can join on snapshot_date <= signal_date
    instead of calling yfinance's always-current .info.

    This does not fix HISTORICAL backtests retroactively - that data is
    simply not recoverable from yfinance - but it prevents the leak from
    continuing into new data from this point forward.

    Replit: wire this as a new daily scheduled job (same pattern as the
    other scheduled jobs in main.py's scheduler block) that runs once per
    trading day after market close and inserts one row per ticker.
    """
    import yfinance as yf

    today = date.today().isoformat()
    rows = []
    for ticker in tickers:
        try:
            info = yf.Ticker(ticker).info or {}
            rows.append({
                "ticker": ticker,
                "snapshot_date": today,
                "market_cap": info.get("marketCap"),
                "sector": info.get("sector"),
                "pe_ratio": info.get("trailingPE"),
                "forward_pe": info.get("forwardPE"),
                "beta": info.get("beta"),
            })
        except Exception as e:
            print(f"[fundamentals_snapshot] {ticker} failed: {e}")

    if not rows:
        return

    with db_conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS daily_fundamentals_snapshot (
                ticker TEXT NOT NULL,
                snapshot_date DATE NOT NULL,
                market_cap BIGINT,
                sector TEXT,
                pe_ratio DOUBLE PRECISION,
                forward_pe DOUBLE PRECISION,
                beta DOUBLE PRECISION,
                PRIMARY KEY (ticker, snapshot_date)
            )
        """)
        for r in rows:
            cur.execute("""
                INSERT INTO daily_fundamentals_snapshot
                    (ticker, snapshot_date, market_cap, sector, pe_ratio, forward_pe, beta)
                VALUES (%(ticker)s, %(snapshot_date)s, %(market_cap)s, %(sector)s,
                        %(pe_ratio)s, %(forward_pe)s, %(beta)s)
                ON CONFLICT (ticker, snapshot_date) DO NOTHING
            """, r)
    db_conn.commit()
