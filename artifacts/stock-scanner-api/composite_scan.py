"""
Manual / scheduled Composite Score scan across the full optionable universe
(DEFAULT_LEADERBOARD). Runs inside the always-on stock-api process so it
survives across requests, persists results to composite_score_history, and
exposes status + leaderboard helpers.

Concurrency: a single-flight guard (_LOCK + _STATUS["running"]) ensures only one
scan runs at a time, which bounds the work even if the trigger is spammed.
Persistence: per-batch UPSERT keyed on (scan_date, ticker) keeps today's rows
continuously valid and makes re-runs idempotent (no destructive pre-delete).
"""
import os
import time
import random
import threading
from datetime import datetime
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed

import psycopg2

from scanner import fetch_stock_data
from indicators import compute_indicators
from scoring import compute_score
from smart_money import DEFAULT_LEADERBOARD

_DB = os.getenv("DATABASE_URL", "")
_ET = ZoneInfo("America/New_York")
_MAX_WORKERS = 6
_LOCK = threading.Lock()

_STATUS = {
    "running": False,
    "phase": "idle",          # idle | starting | scanning | retrying | done | error
    "scan_date": None,
    "total": 0,
    "done": 0,
    "ok": 0,
    "retry_total": 0,
    "retry_done": 0,
    "started_at": None,
    "finished_at": None,
    "error": None,
}


def init_composite_table():
    with psycopg2.connect(_DB) as c, c.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS composite_score_history (
                id               SERIAL PRIMARY KEY,
                scan_date        DATE         NOT NULL,
                ticker           TEXT         NOT NULL,
                score            NUMERIC(4,1) NOT NULL,
                rating           TEXT,
                price            NUMERIC(12,4),
                rsi              NUMERIC(8,2),
                volume_ratio     NUMERIC(8,2),
                price_change_pct NUMERIC(8,2),
                scanned_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
            );
            """
        )
        # Drop any pre-existing duplicate (scan_date,ticker) rows so the unique
        # index below can be created, then enforce uniqueness for upserts.
        cur.execute(
            """
            DELETE FROM composite_score_history a
            USING composite_score_history b
            WHERE a.scan_date = b.scan_date
              AND a.ticker = b.ticker
              AND a.id < b.id;
            """
        )
        cur.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_csh_date_ticker
                ON composite_score_history (scan_date, ticker);
            CREATE INDEX IF NOT EXISTS idx_csh_date_score
                ON composite_score_history (scan_date, score DESC);
            CREATE INDEX IF NOT EXISTS idx_csh_ticker_date
                ON composite_score_history (ticker, scan_date DESC);
            """
        )
        c.commit()


def _scan_one(t):
    for attempt in range(4):
        try:
            df = fetch_stock_data(t, period="1y")
            if df is None or df.empty:
                return None
            ind = compute_indicators(df)
            if not ind or ind.get("price") is None:
                return None
            sc = compute_score(ind)
            return (
                t,
                float(sc["score"]),
                sc["rating"],
                ind.get("price"),
                ind.get("rsi"),
                ind.get("volume_ratio"),
                ind.get("price_change_pct"),
            )
        except Exception as e:
            if "Rate" in str(e) or "Too Many" in str(e):
                time.sleep(1.5 * (attempt + 1) + random.random())
                continue
            return None
    return None


def _flush(today, rows):
    if not rows:
        return
    with psycopg2.connect(_DB) as c, c.cursor() as cur:
        cur.executemany(
            """INSERT INTO composite_score_history
               (scan_date,ticker,score,rating,price,rsi,volume_ratio,price_change_pct)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (scan_date,ticker) DO UPDATE SET
                   score            = EXCLUDED.score,
                   rating           = EXCLUDED.rating,
                   price            = EXCLUDED.price,
                   rsi              = EXCLUDED.rsi,
                   volume_ratio     = EXCLUDED.volume_ratio,
                   price_change_pct = EXCLUDED.price_change_pct,
                   scanned_at       = NOW()""",
            [(today,) + r for r in rows],
        )
        c.commit()


def run_job():
    today = datetime.now(_ET).date()
    tickers = list(dict.fromkeys(DEFAULT_LEADERBOARD))
    try:
        init_composite_table()
        _STATUS.update(
            phase="scanning", scan_date=str(today), total=len(tickers),
            done=0, ok=0, retry_total=0, retry_done=0,
            started_at=datetime.now(_ET).isoformat(), finished_at=None, error=None,
        )
        buf = []
        seen = set()
        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as ex:
            futs = {ex.submit(_scan_one, t): t for t in tickers}
            for fut in as_completed(futs):
                _STATUS["done"] += 1
                r = fut.result()
                if r:
                    buf.append(r)
                    seen.add(r[0])
                    _STATUS["ok"] += 1
                    if len(buf) >= 400:
                        _flush(today, buf)
                        buf = []
        # flush remaining main-pass results before the (slower) retry phase so
        # the leaderboard is current and not waiting on stragglers.
        _flush(today, buf)
        buf = []

        missing = [t for t in tickers if t not in seen]
        _STATUS.update(phase="retrying", retry_total=len(missing), retry_done=0)
        for t in missing:
            _STATUS["retry_done"] += 1
            r = _scan_one(t)
            if r:
                buf.append(r)
                seen.add(r[0])
                _STATUS["ok"] += 1
                if len(buf) >= 50:
                    _flush(today, buf)
                    buf = []
        _flush(today, buf)
        _STATUS.update(running=False, phase="done", finished_at=datetime.now(_ET).isoformat())
    except Exception as e:
        _STATUS.update(running=False, phase="error", error=str(e)[:200],
                       finished_at=datetime.now(_ET).isoformat())


def start_job():
    with _LOCK:
        if _STATUS["running"]:
            return {"started": False, "reason": "already running", **_STATUS}
        # Mark running under the lock BEFORE spawning so a second concurrent
        # POST cannot launch a duplicate scan.
        _STATUS.update(running=True, phase="starting", error=None)
    threading.Thread(target=run_job, daemon=True).start()
    time.sleep(0.3)
    return {"started": True, **_STATUS}


def get_status():
    return dict(_STATUS)


def get_leaderboard(min_score=6.0, scan_date=None, limit=3000):
    day = scan_date or datetime.now(_ET).date()
    with psycopg2.connect(_DB) as c, c.cursor() as cur:
        cur.execute(
            """SELECT ticker, score, rating, price, rsi, volume_ratio, price_change_pct
               FROM composite_score_history
               WHERE scan_date=%s AND score >= %s
               ORDER BY score DESC, volume_ratio DESC NULLS LAST, ticker
               LIMIT %s""",
            (day, min_score, limit),
        )
        rows = cur.fetchall()

    def f(v):
        return float(v) if v is not None else None

    return {
        "scan_date": str(day),
        "min_score": min_score,
        "count": len(rows),
        "leaderboard": [
            {
                "ticker": r[0],
                "score": float(r[1]),
                "rating": r[2],
                "price": f(r[3]),
                "rsi": f(r[4]),
                "volume_ratio": f(r[5]),
                "price_change_pct": f(r[6]),
            }
            for r in rows
        ],
    }
