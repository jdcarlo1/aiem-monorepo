#!/usr/bin/env python3
"""
fetch_si_background.py
Fetches Polygon short-interest data for all tickers in aiem_squeeze_backtest_log.
Runs as a daemon, saves progress to DB, resumes if restarted.
Rate: 1 req / 8s (safe margin under Polygon Starter limit).
Run as: python3 fetch_si_background.py >> /tmp/fetch_si.log 2>&1 &
"""
import os, sys, time, urllib.request, json, psycopg2
from datetime import date

DB  = os.environ.get("DATABASE_URL", "")
KEY = os.environ.get("POLYGON_API_KEY", "")
RATE_SLEEP = 8       # seconds between requests
FROM_DATE  = "2024-01-01"  # fetch settlements from this date onward
LIMIT      = 50      # records per ticker

def fetch_si(ticker):
    url = (f"https://api.polygon.io/stocks/v1/short-interest"
           f"?ticker={ticker}&settlement_date.gte={FROM_DATE}&limit={LIMIT}&apiKey={KEY}")
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            return json.loads(resp.read()).get("results", [])
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code} {ticker}", flush=True)
        if e.code == 429:
            print("  [rate-limit] sleeping 60s", flush=True)
            time.sleep(60)
        return []
    except Exception as e:
        print(f"  ERR {ticker}: {e}", flush=True)
        return []

def main():
    if not DB or not KEY:
        print("DB_URL or POLYGON_API_KEY missing", flush=True)
        sys.exit(1)

    conn = psycopg2.connect(DB, connect_timeout=10)
    conn.autocommit = False
    cur = conn.cursor()

    # Ensure table exists
    cur.execute("""
        CREATE TABLE IF NOT EXISTS polygon_short_interest (
            ticker           TEXT NOT NULL,
            settlement_date  DATE NOT NULL,
            short_interest   BIGINT,
            avg_daily_volume BIGINT,
            days_to_cover    DOUBLE PRECISION,
            fetched_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (ticker, settlement_date)
        )
    """)

    # Track which tickers have already been fetched (any record)
    cur.execute("SELECT DISTINCT ticker FROM polygon_short_interest")
    already_fetched = {r[0] for r in cur.fetchall()}

    # Get all tickers to fetch
    cur.execute("SELECT DISTINCT ticker FROM aiem_squeeze_backtest_log ORDER BY ticker")
    tickers = [r[0] for r in cur.fetchall()]

    todo = [t for t in tickers if t not in already_fetched]
    print(f"[si_fetch] {len(already_fetched)} already fetched, {len(todo)} remaining", flush=True)

    ok = 0; empty = 0; errors = 0; inserted = 0

    for i, ticker in enumerate(todo):
        time.sleep(RATE_SLEEP)
        results = fetch_si(ticker)

        if results:
            ok += 1
            for r in results:
                try:
                    cur.execute("""
                        INSERT INTO polygon_short_interest
                            (ticker, settlement_date, short_interest, avg_daily_volume, days_to_cover)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (ticker, settlement_date) DO NOTHING
                    """, (ticker, r["settlement_date"], r.get("short_interest"),
                          r.get("avg_daily_volume"), r.get("days_to_cover")))
                    inserted += 1
                except Exception as e:
                    print(f"  insert error {ticker}: {e}", flush=True)
            latest = max(results, key=lambda x: x["settlement_date"])
            print(f"  [{i+1}/{len(todo)}] ✅ {ticker}: n={len(results)} "
                  f"latest={latest['settlement_date']} dtc={latest.get('days_to_cover','?')}", flush=True)
            conn.commit()
        else:
            empty += 1
            print(f"  [{i+1}/{len(todo)}] -- {ticker}: no data", flush=True)

        if (i+1) % 10 == 0:
            print(f"--- progress: ok={ok} empty={empty} errors={errors} inserted={inserted} ---",
                  flush=True)

    conn.commit()
    conn.close()
    print(f"\n[si_fetch] DONE: ok={ok} empty={empty} errors={errors} total_inserted={inserted}",
          flush=True)

if __name__ == "__main__":
    main()
