"""
spy_historical_backfill.py
==========================
One-time backfill of SPY daily OHLCV into polygon_market_daily
for dates before the Polygon Starter plan's coverage window.

Source : yfinance bulk download (daily bars, unadjusted Close)
Target : polygon_market_daily, ticker='SPY'
Conflict: ON CONFLICT (scan_date, ticker) DO NOTHING — safe to re-run

Computed columns from available data:
  prev_close     = previous trading day's close
  gap_pct        = (open - prev_close) / prev_close * 100
  range_pct      = (high - low) / low * 100
  close_strength = (close - low) / (high - low)  [0..1]

Columns left NULL (not derivable from daily OHLCV):
  vwap, rvol  — require intraday or rolling average context
"""

import os
import sys
import time
import psycopg2
import yfinance as yf

DB_URL     = os.environ.get("DATABASE_URL")
START_DATE = "2000-01-01"
END_DATE   = "2024-07-08"   # exclusive upper bound — stops at 2024-07-07
TICKER     = "SPY"

def run():
    if not DB_URL:
        print("ERROR: DATABASE_URL not set", flush=True)
        sys.exit(1)

    # ── 1. Download from yfinance ──────────────────────────────────────────
    print(f"[backfill] Downloading {TICKER} {START_DATE} → {END_DATE} ...", flush=True)
    t0 = time.time()
    df = yf.download(
        TICKER,
        start=START_DATE,
        end=END_DATE,
        interval="1d",
        progress=False,
        auto_adjust=False,   # raw unadjusted close, consistent with Polygon
    )
    elapsed = time.time() - t0
    if df.empty:
        print("ERROR: yfinance returned empty DataFrame", flush=True)
        sys.exit(1)

    # Flatten multi-level columns (yfinance returns (metric, ticker))
    df.columns = [col[0].lower().replace(" ", "_") for col in df.columns]
    df = df.reset_index()
    df.columns = [c.lower() for c in df.columns]
    # date column is 'date' after reset_index
    df = df.rename(columns={"date": "scan_date"})
    df["scan_date"] = df["scan_date"].dt.date

    print(f"[backfill] Downloaded {len(df)} rows in {elapsed:.2f}s  "
          f"({df['scan_date'].iloc[0]} → {df['scan_date'].iloc[-1]})", flush=True)

    # ── 2. Compute derived fields ──────────────────────────────────────────
    closes = df["close"].tolist()
    opens  = df["open"].tolist()
    highs  = df["high"].tolist()
    lows   = df["low"].tolist()

    prev_closes     = [None] + closes[:-1]
    gap_pcts        = []
    range_pcts      = []
    close_strengths = []

    for i in range(len(df)):
        pc = prev_closes[i]
        o  = opens[i]
        h  = highs[i]
        lo = lows[i]
        c  = closes[i]

        gap_pcts.append(round((o - pc) / pc * 100, 4) if pc and pc > 0 else None)
        range_pcts.append(round((h - lo) / lo * 100, 4) if lo and lo > 0 else None)
        if h and lo and h > lo:
            close_strengths.append(round((c - lo) / (h - lo), 4))
        else:
            close_strengths.append(None)

    df["prev_close"]     = prev_closes
    df["gap_pct"]        = gap_pcts
    df["range_pct"]      = range_pcts
    df["close_strength"] = close_strengths
    df["ticker"]         = TICKER

    # ── 3. Insert into polygon_market_daily ───────────────────────────────
    print(f"[backfill] Connecting to DB and inserting ...", flush=True)
    inserted = skipped = 0

    with psycopg2.connect(DB_URL, options="-c statement_timeout=60000") as conn, \
         conn.cursor() as cur:

        for _, row in df.iterrows():
            cur.execute("""
                INSERT INTO polygon_market_daily
                    (scan_date, ticker, open_price, high_price, low_price,
                     close_price, volume, prev_close, gap_pct,
                     range_pct, close_strength)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (scan_date, ticker) DO NOTHING
            """, (
                row["scan_date"],
                TICKER,
                round(float(row["open"]),  4) if row["open"]  else None,
                round(float(row["high"]),  4) if row["high"]  else None,
                round(float(row["low"]),   4) if row["low"]   else None,
                round(float(row["close"]), 4) if row["close"] else None,
                int(row["volume"])            if row["volume"] else None,
                round(float(row["prev_close"]), 4) if row["prev_close"] else None,
                row["gap_pct"],
                row["range_pct"],
                row["close_strength"],
            ))
            if cur.rowcount == 1:
                inserted += 1
            else:
                skipped += 1

        conn.commit()

    total_elapsed = time.time() - t0
    print(f"[backfill] Done in {total_elapsed:.2f}s: "
          f"{inserted} inserted, {skipped} skipped (ON CONFLICT)", flush=True)


if __name__ == "__main__":
    run()
