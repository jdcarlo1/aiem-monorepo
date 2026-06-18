"""
News Catalyst Scanner — completely parallel track alongside the ICS momentum scanner.
Targets biotech / news-driven gap plays with blowout opening volume.
Does NOT touch any existing ICS scoring logic.

Scoring (100 pts total):
  Signal 1 — Blowout opening RVOL >15x in first 3 min   30 pts
  Signal 2 — News catalyst keyword detected               20 pts
  Signal 3 — Recovery confirmation (reclaims open/VWAP)  25 pts
  Signal 4 — Sustained volume 3x+ in min 3-10            25 pts

SMS threshold: 75+.  Text is labeled '📰 NEWS CATALYST' so you always
know which scanner fired it.
"""

import os
import threading
import datetime as dt

import numpy  as np
import pandas as pd
import yfinance as yf

try:
    import psycopg2
    _DB_URL = os.getenv("DATABASE_URL", "")
except ImportError:
    psycopg2 = None
    _DB_URL  = ""

try:
    import pytz
    _ET = pytz.timezone("America/New_York")
except ImportError:
    _ET = dt.timezone.utc

from sms_alerts import send_sms

_NC_THRESHOLD = 75
_NC_LOCK      = threading.Lock()

_CATALYST_KEYWORDS = [
    "phase", "fda", "trial", "approval", "approved", "data", "clinical",
    "efficacy", "results", "beats", "earnings", "revenue", "guidance",
    "upgrade", "raised", "target", "acquisition", "merger", "buyout",
    "partnership", "contract", "deal", "ipo", "offering", "buyback",
    "patent", "breakthrough", "milestone", "granted", "cleared",
]


# ── DB helpers ────────────────────────────────────────────────────────────────

def init_news_catalyst_log() -> None:
    if not psycopg2 or not _DB_URL:
        return
    try:
        with psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS news_catalyst_log (
                    id          SERIAL PRIMARY KEY,
                    ticker      TEXT    NOT NULL,
                    alert_date  DATE    NOT NULL DEFAULT CURRENT_DATE,
                    price       NUMERIC,
                    score       NUMERIC,
                    catalyst    TEXT,
                    alerted_at  TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE (ticker, alert_date)
                )
            """)
            conn.commit()
        print("[news_catalyst] log table ready")
    except Exception as e:
        print(f"[news_catalyst] init error: {e}")


def _already_alerted(ticker: str) -> bool:
    if not psycopg2 or not _DB_URL:
        return False
    try:
        with psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM news_catalyst_log WHERE ticker=%s AND alert_date=(now() AT TIME ZONE 'America/New_York')::date LIMIT 1",
                (ticker,),
            )
            return cur.fetchone() is not None
    except:
        return False


def _log_alert(ticker: str, price: float, score: float, catalyst: str) -> None:
    if not psycopg2 or not _DB_URL:
        return
    try:
        with psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO news_catalyst_log (ticker, price, score, catalyst)
                   VALUES (%s,%s,%s,%s)
                   ON CONFLICT (ticker, alert_date) DO NOTHING""",
                (ticker, price, score, catalyst[:200]),
            )
            conn.commit()
    except Exception as e:
        print(f"[news_catalyst] log error {ticker}: {e}")


# ── Signal computations ───────────────────────────────────────────────────────

def _blowout_rvol(df_open: pd.DataFrame, avg_daily_vol: float) -> tuple:
    """Signal 1 (30 pts): First 3-minute projected RVOL > 15x."""
    if len(df_open) < 1 or avg_daily_vol <= 0:
        return False, ""
    mins      = min(3, len(df_open))
    vol_3min  = float(df_open["Volume"].iloc[:mins].sum())
    rvol_proj = (vol_3min / avg_daily_vol) * (390 / mins)
    if rvol_proj >= 15:
        return True, f"🚨 Blowout RVOL {rvol_proj:.0f}x in first {mins} min"
    return False, f"RVOL {rvol_proj:.1f}x (need 15x)"


def _news_catalyst_check(ticker: str) -> tuple:
    """Signal 2 (20 pts): Recent news headline contains a hard catalyst keyword."""
    try:
        news_items = yf.Ticker(ticker).news or []
        for item in news_items[:8]:
            content = item.get("content", {}) or {}
            title   = (
                content.get("title", "") or
                item.get("title", "") or
                ""
            ).lower()
            for kw in _CATALYST_KEYWORDS:
                if kw in title:
                    return True, f"📰 {title[:90].title()}"
    except Exception as e:
        print(f"[news_catalyst] news fetch {ticker}: {e}")
    return False, "No catalyst keyword found"


def _recovery_confirmation(
    df_open: pd.DataFrame, open_price: float, vwap: float
) -> tuple:
    """
    Signal 3 (25 pts): Price confirms buyers won the opening battle.
    Fires if:
      (a) Price dipped below open then reclaimed it, OR
      (b) Price stayed above VWAP and is trending up from open.
    """
    if len(df_open) < 3:
        return False, ""
    closes      = df_open["Close"].values
    latest      = float(closes[-1])
    min_after   = float(np.min(closes[1:]))

    if min_after < open_price and latest >= open_price:
        recovery = (latest - min_after) / min_after * 100
        return True, f"✅ Dipped then reclaimed open — recovered +{recovery:.1f}%"

    if latest > vwap and latest > float(closes[1]):
        chg = (latest - float(closes[0])) / float(closes[0]) * 100
        return True, f"✅ Above VWAP, trending +{chg:.1f}% from open"

    return False, "No recovery — price not confirmed above open/VWAP"


def _sustained_volume(df_open: pd.DataFrame, avg_daily_vol: float) -> tuple:
    """
    Signal 4 (25 pts): Volume in minutes 3-10 averages 3x+ the per-minute daily avg.
    Ensures the move isn't just a one-minute opening flush with no follow-through.
    """
    if len(df_open) < 4 or avg_daily_vol <= 0:
        return False, ""
    later_vols  = df_open["Volume"].iloc[3:].values
    if len(later_vols) == 0:
        return False, ""
    avg_later    = float(np.mean(later_vols))
    per_min_avg  = avg_daily_vol / 390
    ratio        = avg_later / per_min_avg if per_min_avg > 0 else 0
    if ratio >= 3:
        return True, f"📊 Sustained vol {ratio:.1f}x avg (min 3-10)"
    return False, f"Later vol only {ratio:.1f}x avg (need 3x)"


# ── Master score ──────────────────────────────────────────────────────────────

def compute_news_catalyst_score(
    ticker: str,
    df_open: pd.DataFrame,
    avg_daily_vol: float,
    open_price: float,
) -> tuple:
    """
    Returns (score_0_to_100, breakdown_dict).
    Completely independent of ICS scoring in options_sweep.py.
    """
    vol_sum = float(df_open["Volume"].sum())
    vwap    = (
        float(
            ((df_open["High"] + df_open["Low"] + df_open["Close"]) / 3
             * df_open["Volume"]).sum()
            / vol_sum
        )
        if vol_sum > 0
        else open_price
    )

    s1_fired, s1_lbl = _blowout_rvol(df_open, avg_daily_vol)
    s2_fired, s2_lbl = _news_catalyst_check(ticker)
    s3_fired, s3_lbl = _recovery_confirmation(df_open, open_price, vwap)
    s4_fired, s4_lbl = _sustained_volume(df_open, avg_daily_vol)

    score = (
        (30 if s1_fired else 0) +
        (20 if s2_fired else 0) +
        (25 if s3_fired else 0) +
        (25 if s4_fired else 0)
    )

    breakdown = {
        "Blowout RVOL >15x (30pts)":     (s1_fired, s1_lbl),
        "News Catalyst (20pts)":          (s2_fired, s2_lbl),
        "Recovery Confirmed (25pts)":     (s3_fired, s3_lbl),
        "Sustained Volume 3x+ (25pts)":   (s4_fired, s4_lbl),
    }
    return score, breakdown


# ── Universe fetch ────────────────────────────────────────────────────────────

def _get_scan_universe() -> list:
    tickers = set()
    try:
        result = yf.screen("day_gainers", size=60)
        for q in result.get("quotes", []):
            sym = q.get("symbol", "")
            if sym and "." not in sym:
                tickers.add(sym)
    except Exception as e:
        print(f"[news_catalyst] screener error: {e}")
    return list(tickers)


# ── Main scan entry point ─────────────────────────────────────────────────────

def run_news_catalyst_scan(force: bool = False) -> list:
    """
    Runs in parallel to the ICS scan during the morning window (9:31–10:30 ET).
    Sends a DIFFERENT SMS labeled '📰 NEWS CATALYST' so the user always knows
    which scanner fired.  Never modifies ICS state.

    force=True bypasses the 9:31–10:30 time gate so the wake-up backup can still
    catch a monster mover later in the day (e.g. the server was asleep all morning
    and the owner opens the app at 10:44). Safe because the per-ticker dedup log
    still blocks duplicate alerts and the intraday math below keys off `now_et`, so
    a later scan simply measures 9:30 → now. Weekends are always skipped.
    """
    now_et = dt.datetime.now(dt.timezone.utc).astimezone(_ET)

    if now_et.weekday() >= 5:
        return []
    total_min = now_et.hour * 60 + now_et.minute
    if not force and not (9 * 60 + 31 <= total_min <= 10 * 60 + 30):
        return []

    with _NC_LOCK:
        universe = _get_scan_universe()
        if not universe:
            print("[news_catalyst] empty universe — skipping")
            return []

        print(f"[news_catalyst] scanning {len(universe)} tickers")
        hits = []

        day_start = now_et.replace(hour=9, minute=29, second=0, microsecond=0)
        day_end   = now_et + dt.timedelta(minutes=2)

        for ticker in universe:
            try:
                if _already_alerted(ticker):
                    continue

                raw = yf.download(
                    ticker,
                    start=day_start,
                    end=day_end,
                    interval="1m",
                    progress=False,
                    auto_adjust=True,
                )
                if raw.empty or len(raw) < 3:
                    continue
                raw.columns = [
                    c[0] if isinstance(c, tuple) else c for c in raw.columns
                ]
                df_open = raw.between_time("09:30", now_et.strftime("%H:%M"))
                if len(df_open) < 3:
                    continue

                dfd = yf.download(
                    ticker, period="20d", interval="1d",
                    progress=False, auto_adjust=True,
                )
                if dfd.empty:
                    continue
                dfd.columns = [
                    c[0] if isinstance(c, tuple) else c for c in dfd.columns
                ]
                avg_vol    = float(dfd["Volume"].iloc[:-1].mean())
                if avg_vol <= 0:
                    continue

                open_price = float(df_open["Open"].iloc[0])
                price      = float(df_open["Close"].iloc[-1])
                chg_pct    = (price - open_price) / open_price * 100

                if chg_pct < 1.0:
                    continue

                score, breakdown = compute_news_catalyst_score(
                    ticker, df_open, avg_vol, open_price
                )
                if score < _NC_THRESHOLD:
                    continue

                catalyst_lbl = next(
                    (lbl for _, (fired, lbl) in breakdown.items() if fired and "📰" in lbl),
                    "Catalyst detected",
                )
                strength = (
                    "🔥🔥 EXTREME" if score >= 90 else
                    ("⭐⭐ HIGH"    if score >= 80 else "⭐ GOOD")
                )
                msg = (
                    f"📰 NEWS CATALYST: {ticker}\n"
                    f"Score: {score}/100 {strength}\n"
                    f"Price: ${price:.2f} ({chg_pct:+.1f}% from open)\n"
                    f"{catalyst_lbl[:80]}\n"
                    f"Signal: High-vol news gap — watch VWAP hold"
                )

                print(f"[news_catalyst] {ticker} score={score}/100")
                for sig_name, (fired, sig_lbl) in breakdown.items():
                    icon = "✅" if fired else "⬜"
                    print(f"[news_catalyst]   {icon} {sig_name}: {sig_lbl[:60]}")

                if send_sms(msg):
                    _log_alert(ticker, price, score, catalyst_lbl)
                    print(f"[news_catalyst] 📰 SMS sent → {ticker}")
                    try:
                        import requests as _rq_nc
                        _rq_nc.post(
                            "https://ntfy.sh/stockscanner-joel-9x7k2",
                            data=msg.encode("utf-8"),
                            headers={
                                "Title": f"📰 News Catalyst: {ticker} — {score}/100",
                                "Priority": "urgent",
                                "Tags": "newspaper,chart_with_upwards_trend",
                            },
                            timeout=8,
                        )
                    except Exception as _ne_nc:
                        pass
                    hits.append({
                        "ticker": ticker, "score": score,
                        "price": price,   "chg":   chg_pct,
                    })

            except Exception as e:
                print(f"[news_catalyst] {ticker} error: {e}")

        print(f"[news_catalyst] done — {len(hits)} alerts fired")
        return hits
