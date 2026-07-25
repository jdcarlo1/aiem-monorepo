#!/usr/bin/env python3
"""
One-time backfill: compute gap_pct and rvol for polygon_market_daily rows
since 2024-07-22 where they are currently NULL.

ROOT CAUSE FINDINGS (confirmed before writing this script):
  1. prev_close column is NULL for all rows before 2026-04-07 (old writer
     never stored it), so gap_pct = open_price/prev_close is impossible
     for old data.  Fix: derive previous close via JOIN on previous trading day.
  2. gap_pct/rvol are only non-null from 2026-07-10 onward (7 trading days)
     which is why _TRAIN_START=2026-04-07 yields 0 qualifying rows.
  3. Large CTE window-function approach timed out (full table scan).

APPROACH: per-date JOIN — touches only ~6,500 rows per transaction,
uses idx_pmd_ticker_date, very low lock contention window.

  gap_pct = (open_price / prev_trading_day.close_price) - 1
  rvol    = volume / AVG(volume over previous 30 trading days)

Immutability rule: only UPDATEs NULL rows — never deletes anything.

Usage:
    python3 tools/backfill_gap_rvol.py
"""
import os
import sys
import datetime
import time
import psycopg2

DB_URL         = os.environ["DATABASE_URL"]
BACKFILL_START = datetime.date(2024, 7, 22)
BACKFILL_END   = datetime.date.today() - datetime.timedelta(days=1)  # up to yesterday
STMT_TIMEOUT   = "30000"   # 30s per individual statement
LOCK_TIMEOUT   = "5000"    # 5s — tiny per-date transactions shouldn't need long waits
RETRY_SLEEP    = 10        # seconds between retries on lock contention
MAX_RETRIES    = 3


def connect():
    conn = psycopg2.connect(DB_URL, connect_timeout=10)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(f"SET statement_timeout = '{STMT_TIMEOUT}'")
        cur.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
    return conn


def audit(conn, label: str):
    with conn.cursor() as cur:
        cur.execute("SET statement_timeout = '25000'")
        cur.execute("""
            SELECT
              COUNT(*)               AS total_rows,
              COUNT(gap_pct)         AS gap_pct_nonnull,
              COUNT(rvol)            AS rvol_nonnull,
              COUNT(close_strength)  AS close_str_nonnull,
              COUNT(range_pct)       AS range_pct_nonnull,
              COUNT(*) FILTER (WHERE close_price > 2.0) AS price_ok
            FROM polygon_market_daily
            WHERE scan_date >= %s
        """, (BACKFILL_START.isoformat(),))
        row = cur.fetchone()
    total = row[0]
    print(f"\n=== {label} ===")
    print(f"  total_rows:        {total:>12,}")
    if total:
        for name, val in [
            ("gap_pct_nonnull",   row[1]),
            ("rvol_nonnull",      row[2]),
            ("close_str_nonnull", row[3]),
            ("range_pct_nonnull", row[4]),
            ("price_ok",          row[5]),
        ]:
            print(f"  {name:<20} {val:>12,}  ({val/total*100:.2f}%)")
    return row


def get_trading_dates(conn) -> list:
    """Return all distinct scan_dates in the backfill range (ordered)."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT scan_date
            FROM polygon_market_daily
            WHERE scan_date BETWEEN %s AND %s
            ORDER BY scan_date
        """, (BACKFILL_START.isoformat(), BACKFILL_END.isoformat()))
        return [r[0] for r in cur.fetchall()]


def get_prev_date(conn, date: datetime.date):
    """Return the most recent trading date before 'date', or None."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT MAX(scan_date)
            FROM polygon_market_daily
            WHERE scan_date < %s
        """, (date.isoformat(),))
        row = cur.fetchone()
        return row[0] if row else None


def backfill_gap_pct_for_date(conn, date: datetime.date, prev_date: datetime.date) -> int:
    """
    gap_pct = (today.open_price / yesterday.close_price) - 1
    Uses a JOIN on (ticker, scan_date) — hits idx_pmd_ticker_date.
    Only updates rows where gap_pct IS NULL.
    """
    sql = """
        UPDATE polygon_market_daily a
        SET gap_pct = (a.open_price / NULLIF(b.close_price, 0)) - 1.0
        FROM polygon_market_daily b
        WHERE a.scan_date   = %s
          AND b.scan_date   = %s
          AND a.ticker      = b.ticker
          AND a.gap_pct     IS NULL
          AND a.open_price  IS NOT NULL
          AND b.close_price IS NOT NULL
          AND b.close_price > 0
    """
    with conn.cursor() as cur:
        cur.execute(sql, (date.isoformat(), prev_date.isoformat()))
        return cur.rowcount


def backfill_rvol_for_date(conn, date: datetime.date) -> int:
    """
    rvol = today.volume / AVG(volume) over the 30 preceding trading days.
    Groups by ticker over [date - 45 calendar days, date - 1 day] for the avg.
    Only updates rows where rvol IS NULL.
    """
    buf_start = (date - datetime.timedelta(days=45)).isoformat()
    prev_day  = (date - datetime.timedelta(days=1)).isoformat()
    sql = """
        UPDATE polygon_market_daily a
        SET rvol = a.volume::numeric / NULLIF(avg_data.avg_vol, 0)
        FROM (
            SELECT ticker, AVG(volume) AS avg_vol
            FROM polygon_market_daily
            WHERE scan_date BETWEEN %s AND %s
              AND volume IS NOT NULL
            GROUP BY ticker
        ) avg_data
        WHERE a.scan_date   = %s
          AND a.ticker      = avg_data.ticker
          AND a.rvol        IS NULL
          AND a.volume      IS NOT NULL
          AND avg_data.avg_vol > 0
    """
    with conn.cursor() as cur:
        cur.execute(sql, (buf_start, prev_day, date.isoformat()))
        return cur.rowcount


def run_with_retry(fn, *args, label=""):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fn(*args)
        except psycopg2.errors.LockNotAvailable:
            if attempt < MAX_RETRIES:
                print(f"      {label} lock contention (attempt {attempt}) — retry in {RETRY_SLEEP}s", flush=True)
                time.sleep(RETRY_SLEEP)
            else:
                print(f"      {label} lock contention exhausted after {MAX_RETRIES} attempts — skipping")
                return 0
        except psycopg2.errors.QueryCanceled as e:
            print(f"      {label} statement timeout: {e}")
            return 0
        except Exception as e:
            print(f"      {label} error: {e}")
            return 0


def main():
    print(f"Backfill started: {datetime.datetime.utcnow().isoformat()}Z")
    print(f"Approach: per-date JOIN (not CTE window) — uses idx_pmd_ticker_date")

    conn = connect()
    audit(conn, "BEFORE BACKFILL")

    print("\nFetching trading dates...", flush=True)
    dates = get_trading_dates(conn)
    print(f"Trading dates in range: {len(dates)}")

    # Cache prev_date per date (avoid repeated DB calls)
    prev_dates = {}
    for i, d in enumerate(dates):
        prev_dates[d] = dates[i-1] if i > 0 else None

    total_gap  = 0
    total_rvol = 0
    skipped    = 0

    for i, date in enumerate(dates):
        prev_date = prev_dates[date]
        if prev_date is None:
            skipped += 1
            continue  # first date ever — no previous close

        g = run_with_retry(backfill_gap_pct_for_date, conn, date, prev_date, label="gap_pct")
        r = run_with_retry(backfill_rvol_for_date,    conn, date,            label="rvol")
        total_gap  += g
        total_rvol += r

        if (i + 1) % 50 == 0 or g > 0 or r > 0:
            print(f"  [{date}] gap={g:,} rvol={r:,}  (cumulative gap={total_gap:,} rvol={total_rvol:,})", flush=True)

    print(f"\nTotal gap_pct rows updated: {total_gap:,}")
    print(f"Total rvol rows updated:    {total_rvol:,}")
    print(f"Skipped (no prev date):     {skipped}")

    audit(conn, "AFTER BACKFILL")
    conn.close()
    print(f"\nBackfill complete: {datetime.datetime.utcnow().isoformat()}Z")


if __name__ == "__main__":
    main()
