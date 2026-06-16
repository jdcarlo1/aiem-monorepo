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
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed

import psycopg2
import yfinance as yf

from scanner import fetch_stock_data
from indicators import compute_indicators
from scoring import compute_score
from smart_money import DEFAULT_LEADERBOARD

_DB = os.getenv("DATABASE_URL", "")
_ET = ZoneInfo("America/New_York")
_MAX_WORKERS = 6
_LOCK = threading.Lock()
_SNAP_LOCK = threading.Lock()
_SNAP_STATUS = {"running": False, "phase": "idle", "snap_date": None,
                "logged": 0, "error": None, "finished_at": None}

# Asset types that are NOT single-name stocks — excluded from the 8+ stock list.
FUND_TYPES = ("ETF", "MUTUALFUND", "INDEX", "CURRENCY", "CRYPTOCURRENCY", "ETN")

# Seed denylist of common ETFs/leveraged funds in the optionable universe so the
# list is clean even before yfinance classification fills in.
ETF_DENYLIST = {
    "SPY", "QQQ", "IWM", "DIA", "MDY", "VTI", "VOO", "VXX", "UVXY", "SVXY",
    "XLF", "XLE", "XLK", "XLY", "XLI", "XLV", "XLB", "XLP", "XLU", "XLRE", "XLC",
    "SMH", "SOXX", "XBI", "IBB", "KRE", "XRT", "ITB", "JETS", "KWEB", "XME", "XOP", "XHB",
    "TQQQ", "SPXL", "SOXL", "UDOW", "LABU", "FNGU", "TECL", "UPRO", "TNA", "FAS", "ERX", "BULZ",
    "SQQQ", "SPXS", "SOXS", "SDOW", "TZA", "FAZ", "ERY", "SARK",
    "GLD", "IAU", "SLV", "USO", "UNG", "GDX", "GDXJ", "OIH", "SLX", "URA", "COPX",
    "TLT", "HYG", "LQD", "TBT", "TMF", "SHY", "IEF", "JNK", "BND", "AGG", "TMV",
    "EEM", "EFA", "FXI", "EWJ", "EWZ", "EWY", "IEMG", "ARKK", "ARKG", "IBIT", "FBTC", "BITO",
    "IPO", "CEW", "EDIV", "CEMB", "SVAL", "AIVI", "IHE", "CWI", "FXU", "NQP", "NOM", "UST",
    "AMZO", "DVXV", "SCHD", "DVY", "VIG", "VYM", "NOBL", "HDV", "SPHD", "JEPI", "JEPQ",
}

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


def get_leaderboard(min_score=6.0, scan_date=None, limit=3000, exclude_etf=False):
    day = scan_date or datetime.now(_ET).date()
    with psycopg2.connect(_DB) as c, c.cursor() as cur:
        cur.execute(
            """SELECT h.ticker, h.score, h.rating, h.price, h.rsi,
                      h.volume_ratio, h.price_change_pct, m.quote_type
               FROM composite_score_history h
               LEFT JOIN ticker_meta m ON m.ticker = h.ticker
               WHERE h.scan_date=%s AND h.score >= %s
                 AND (NOT %s OR COALESCE(m.quote_type,'EQUITY') <> ALL(%s))
               ORDER BY h.score DESC,
                        h.volume_ratio DESC NULLS LAST,
                        h.price_change_pct DESC NULLS LAST,
                        h.ticker
               LIMIT %s""",
            (day, min_score, exclude_etf, list(FUND_TYPES), limit),
        )
        rows = cur.fetchall()

    def f(v):
        return float(v) if v is not None else None

    return {
        "scan_date": str(day),
        "min_score": min_score,
        "exclude_etf": bool(exclude_etf),
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
                "quote_type": r[7] or "UNKNOWN",
            }
            for r in rows
        ],
    }


# ----------------------------------------------------------------------------
# ETF / fund classification (ticker_meta). Runs IN-PROCESS only — yfinance is
# warmed inside the stock-api process; fresh processes get rate-limited.
# Unknowns are cached as status='unknown' and retried on later runs, never
# silently treated as funds (so ADRs / REITs like MAC / dual-class stay in).
# ----------------------------------------------------------------------------
def init_meta_table():
    with psycopg2.connect(_DB) as c, c.cursor() as cur:
        cur.execute(
            """CREATE TABLE IF NOT EXISTS ticker_meta (
                   ticker        TEXT PRIMARY KEY,
                   quote_type    TEXT,
                   status        TEXT DEFAULT 'ok',
                   classified_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
               );"""
        )
        cur.executemany(
            """INSERT INTO ticker_meta (ticker, quote_type, status)
               VALUES (%s,'ETF','ok')
               ON CONFLICT (ticker) DO NOTHING""",
            [(t,) for t in sorted(ETF_DENYLIST)],
        )
        c.commit()


def _detect_quote_type(t):
    try:
        fi = yf.Ticker(t).fast_info
        for k in ("quote_type", "quoteType"):
            try:
                v = fi[k]
            except Exception:
                v = getattr(fi, k, None)
            if v:
                return str(v).upper()
    except Exception:
        pass
    try:
        info = yf.Ticker(t).get_info()
        v = info.get("quoteType") or info.get("typeDisp")
        if v:
            return str(v).upper()
    except Exception:
        pass
    return None


def classify_missing(tickers):
    tickers = list(dict.fromkeys(tickers))
    if not tickers:
        return 0
    with psycopg2.connect(_DB) as c, c.cursor() as cur:
        cur.execute(
            "SELECT ticker, status FROM ticker_meta WHERE ticker = ANY(%s)",
            (tickers,),
        )
        meta = {r[0]: r[1] for r in cur.fetchall()}
    todo = [t for t in tickers if meta.get(t) != "ok"]
    if not todo:
        return 0
    results = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(_detect_quote_type, t): t for t in todo}
        for fut in as_completed(futs):
            t = futs[fut]
            qt = fut.result()
            results.append((t, qt, "ok" if qt else "unknown"))
            time.sleep(0)
    done = sum(1 for _, qt, _s in results if qt)
    with psycopg2.connect(_DB) as c, c.cursor() as cur:
        cur.executemany(
            """INSERT INTO ticker_meta (ticker, quote_type, status, classified_at)
               VALUES (%s,%s,%s,NOW())
               ON CONFLICT (ticker) DO UPDATE SET
                   quote_type=EXCLUDED.quote_type,
                   status=EXCLUDED.status,
                   classified_at=NOW()""",
            results,
        )
        c.commit()
    return done


# ----------------------------------------------------------------------------
# Daily watchlist (track-record log): the actionable cohort =
# score>=8 AND volume_ratio>=1.5 AND non-fund. Entry = NEXT session open.
# ----------------------------------------------------------------------------
def init_watchlist_table():
    with psycopg2.connect(_DB) as c, c.cursor() as cur:
        cur.execute(
            """CREATE TABLE IF NOT EXISTS composite_watchlist (
                   id                 SERIAL PRIMARY KEY,
                   snap_date          DATE NOT NULL,
                   ticker             TEXT NOT NULL,
                   score              NUMERIC(4,1),
                   rating             TEXT,
                   scan_price         NUMERIC(12,4),
                   volume_ratio       NUMERIC(8,2),
                   rsi                NUMERIC(8,2),
                   price_change_pct   NUMERIC(8,2),
                   entry_date         DATE,
                   entry_open         NUMERIC(12,4),
                   entry_price_source TEXT DEFAULT 'next_open',
                   w1_pct             NUMERIC(8,2),
                   w2_pct             NUMERIC(8,2),
                   w3_pct             NUMERIC(8,2),
                   w4_pct             NUMERIC(8,2),
                   captured_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                   updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                   UNIQUE (snap_date, ticker)
               );
               CREATE INDEX IF NOT EXISTS idx_cw_snap
                   ON composite_watchlist (snap_date DESC);"""
        )
        c.commit()


def snapshot_today(min_score=8.0, min_vol=1.5):
    today = datetime.now(_ET).date()
    with psycopg2.connect(_DB) as c, c.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM composite_score_history WHERE scan_date=%s", (today,)
        )
        n = cur.fetchone()[0]
        if n < 1000:
            return {"ok": False, "reason": f"scan not ready ({n} rows today)",
                    "snap_date": str(today)}
        cur.execute(
            """SELECT ticker, score, rating, price, volume_ratio, rsi, price_change_pct
               FROM composite_score_history
               WHERE scan_date=%s AND score >= %s""",
            (today, min_score),
        )
        all_rows = cur.fetchall()

    # classify the FULL 8+ set in-process so the displayed list is clean, then
    # log only the actionable cohort (volume_ratio >= min_vol) for the track record.
    classify_missing([r[0] for r in all_rows])
    rows = [r for r in all_rows if r[4] is not None and float(r[4]) >= min_vol]

    with psycopg2.connect(_DB) as c, c.cursor() as cur:
        cur.execute(
            "SELECT ticker, quote_type FROM ticker_meta WHERE ticker = ANY(%s)",
            ([r[0] for r in rows],),
        )
        meta = {t: qt for t, qt in cur.fetchall()}
        keep = [r for r in rows if (meta.get(r[0]) or "EQUITY") not in FUND_TYPES]
        # Same-day idempotency: drop rows logged earlier today that no longer
        # qualify (reclassified as a fund, or dropped out of the cohort) so a
        # manual rerun can never leave a stale cohort for the day.
        cur.execute(
            "DELETE FROM composite_watchlist WHERE snap_date=%s AND NOT (ticker = ANY(%s))",
            (today, [r[0] for r in keep]),
        )
        cur.executemany(
            """INSERT INTO composite_watchlist
                   (snap_date,ticker,score,rating,scan_price,volume_ratio,rsi,price_change_pct)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (snap_date,ticker) DO UPDATE SET
                   score=EXCLUDED.score, rating=EXCLUDED.rating,
                   scan_price=EXCLUDED.scan_price, volume_ratio=EXCLUDED.volume_ratio,
                   rsi=EXCLUDED.rsi, price_change_pct=EXCLUDED.price_change_pct,
                   updated_at=NOW()""",
            [(today, r[0], r[1], r[2], r[3], r[4], r[5], r[6]) for r in keep],
        )
        c.commit()
    return {"ok": True, "snap_date": str(today), "candidates": len(rows), "logged": len(keep)}


def run_snapshot():
    with _SNAP_LOCK:
        if _SNAP_STATUS["running"]:
            return {"started": False, "reason": "already running", **_SNAP_STATUS}
        _SNAP_STATUS.update(running=True, phase="classifying", error=None, finished_at=None)

    def _bg():
        try:
            res = snapshot_today()
            _SNAP_STATUS.update(
                running=False, phase="done" if res.get("ok") else "skipped",
                snap_date=res.get("snap_date"), logged=res.get("logged", 0),
                finished_at=datetime.now(_ET).isoformat(),
                error=None if res.get("ok") else res.get("reason"))
        except Exception as e:
            _SNAP_STATUS.update(running=False, phase="error", error=str(e)[:200],
                                finished_at=datetime.now(_ET).isoformat())

    threading.Thread(target=_bg, daemon=True).start()
    return {"started": True}


def get_snapshot_status():
    return dict(_SNAP_STATUS)


# ----------------------------------------------------------------------------
# Outcomes: entry = next trading day OPEN (what the user can actually buy);
# returns measured over 1/2/3/4 weeks held = 5/10/15/20 trading sessions of
# exposure from that entry. This tracks STOCK returns, not option P&L.
# ----------------------------------------------------------------------------
def fill_outcomes():
    today = datetime.now(_ET).date()
    with psycopg2.connect(_DB) as c, c.cursor() as cur:
        cur.execute(
            """SELECT snap_date, ticker FROM composite_watchlist
               WHERE snap_date < %s
                 AND (entry_open IS NULL OR w1_pct IS NULL OR w2_pct IS NULL
                      OR w3_pct IS NULL OR w4_pct IS NULL)
               ORDER BY snap_date""",
            (today,),
        )
        rows = cur.fetchall()
    if not rows:
        return {"filled": 0}

    from collections import defaultdict
    by_ticker = defaultdict(list)
    for snap_date, ticker in rows:
        by_ticker[ticker].append(snap_date)

    updates = []
    for ticker, snaps in by_ticker.items():
        try:
            start = min(snaps) - timedelta(days=6)
            hist = yf.Ticker(ticker).history(
                start=start.isoformat(),
                end=(today + timedelta(days=1)).isoformat(),
                interval="1d",
            )
            if hist.empty:
                continue
            bars = []
            for row in hist.itertuples():
                d = row.Index.date() if hasattr(row.Index, "date") else row.Index
                bars.append((d, float(row.Open), float(row.Close)))
            bars.sort(key=lambda x: x[0])
            for snap_date in snaps:
                future = [b for b in bars if b[0] > snap_date]
                if not future:
                    continue
                entry_date, entry_open, _ = future[0]
                if not entry_open:
                    continue

                def pct(h):
                    return (round((future[h][2] - entry_open) / entry_open * 100, 2)
                            if len(future) > h else None)

                # 1/2/3/4 weeks held from the next-open entry = 5/10/15/20 trading
                # sessions of exposure. future[0] is the entry session itself, so
                # the exit is the close of session index 4/9/14/19.
                updates.append((entry_date, entry_open, pct(4), pct(9), pct(14), pct(19),
                                snap_date, ticker))
        except Exception:
            continue

    if updates:
        with psycopg2.connect(_DB) as c, c.cursor() as cur:
            cur.executemany(
                """UPDATE composite_watchlist SET
                       entry_date=%s, entry_open=%s,
                       w1_pct=%s, w2_pct=%s, w3_pct=%s, w4_pct=%s, updated_at=NOW()
                   WHERE snap_date=%s AND ticker=%s""",
                updates,
            )
            c.commit()
    return {"filled": len(updates)}


def get_track_record(days=90):
    cutoff = datetime.now(_ET).date() - timedelta(days=days)
    today = datetime.now(_ET).date()
    with psycopg2.connect(_DB) as c, c.cursor() as cur:
        cur.execute(
            """SELECT snap_date,ticker,score,rating,scan_price,volume_ratio,
                      price_change_pct,entry_date,entry_open,w1_pct,w2_pct,w3_pct,w4_pct
               FROM composite_watchlist
               WHERE snap_date >= %s
               ORDER BY snap_date DESC, score DESC, volume_ratio DESC NULLS LAST""",
            (cutoff,),
        )
        rows = cur.fetchall()

    def f(v):
        return float(v) if v is not None else None

    picks = [{
        "snap_date": str(r[0]), "ticker": r[1], "score": f(r[2]), "rating": r[3],
        "scan_price": f(r[4]), "volume_ratio": f(r[5]), "price_change_pct": f(r[6]),
        "entry_date": str(r[7]) if r[7] else None, "entry_open": f(r[8]),
        "w1_pct": f(r[9]), "w2_pct": f(r[10]), "w3_pct": f(r[11]), "w4_pct": f(r[12]),
    } for r in rows]

    def stat(key):
        vals = [p[key] for p in picks if p[key] is not None]
        wins = sum(1 for v in vals if v > 0)
        losses = sum(1 for v in vals if v <= 0)
        return {
            "count": len(vals), "wins": wins, "losses": losses,
            "win_rate": round(wins / len(vals) * 100, 1) if vals else None,
            "avg_pct": round(sum(vals) / len(vals), 2) if vals else None,
        }

    return {
        "picks": picks,
        "stats": {"w1": stat("w1_pct"), "w2": stat("w2_pct"),
                  "w3": stat("w3_pct"), "w4": stat("w4_pct")},
        "today_count": sum(1 for p in picks if p["snap_date"] == str(today)),
    }
