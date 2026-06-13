"""
Real-time SMS alerts via Twilio REST API.
Scans all Barchart feeds + morning inflows cache every 15 min during market hours.
Fires a text the moment any stock crosses the indicators threshold — no waiting for email.

Env vars needed:
  TWILIO_ACCOUNT_SID  — from console.twilio.com
  TWILIO_AUTH_TOKEN   — from console.twilio.com
  TWILIO_FROM_NUMBER  — your Twilio phone number  e.g. +15551234567
  TWILIO_TO_NUMBER    — your cell number          e.g. +15559876543
"""
import os
import psycopg2
import requests as _req
from datetime import datetime, date
import pytz


# ── Config ───────────────────────────────────────────────────────────────────

_ET = pytz.timezone("US/Eastern")

_DEFAULT_TO = "+14013185787"

def sms_configured() -> bool:
    return all([
        os.getenv("TWILIO_ACCOUNT_SID"),
        os.getenv("TWILIO_AUTH_TOKEN"),
        os.getenv("TWILIO_FROM_NUMBER"),
    ])


def _conn():
    return psycopg2.connect(os.environ["DATABASE_URL"])


# ── DB table ─────────────────────────────────────────────────────────────────

def init_sms_log_table():
    try:
        with _conn() as con:
            with con.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS sms_alerts_log (
                        id          SERIAL PRIMARY KEY,
                        ticker      TEXT NOT NULL,
                        alert_date  DATE NOT NULL DEFAULT CURRENT_DATE,
                        price       NUMERIC,
                        chg_pct     NUMERIC,
                        rel_vol     NUMERIC,
                        score       NUMERIC,
                        reason      TEXT,
                        sent_at     TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                        UNIQUE (ticker, alert_date)
                    )
                """)
        print("[sms_alerts] log table ready")
    except Exception as e:
        print(f"[sms_alerts] table init error: {e}")


def _already_alerted_today(ticker: str) -> bool:
    try:
        with _conn() as con:
            with con.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM sms_alerts_log WHERE ticker=%s AND alert_date=CURRENT_DATE",
                    (ticker,)
                )
                return cur.fetchone() is not None
    except Exception:
        return False


def _log_alert(ticker, price, chg_pct, rel_vol, score, reason):
    try:
        with _conn() as con:
            with con.cursor() as cur:
                cur.execute("""
                    INSERT INTO sms_alerts_log (ticker, price, chg_pct, rel_vol, score, reason)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (ticker, alert_date) DO NOTHING
                """, (ticker, price, chg_pct, rel_vol, score, reason))
    except Exception as e:
        print(f"[sms_alerts] log error {ticker}: {e}")


# ── Twilio sender ─────────────────────────────────────────────────────────────

def send_sms(message: str) -> bool:
    sid   = os.getenv("TWILIO_ACCOUNT_SID", "")
    token = os.getenv("TWILIO_AUTH_TOKEN", "")
    frm   = os.getenv("TWILIO_FROM_NUMBER", "")
    to    = os.getenv("TWILIO_TO_NUMBER", "").strip() or _DEFAULT_TO
    if not all([sid, token, frm, to]):
        print("[sms_alerts] Twilio not configured — skipping send")
        return False
    try:
        url  = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
        resp = _req.post(url, auth=(sid, token), data={"From": frm, "To": to, "Body": message}, timeout=10)
        if resp.status_code in (200, 201):
            print(f"[sms_alerts] SMS sent: {message[:60]}…")
            return True
        print(f"[sms_alerts] Twilio error {resp.status_code}: {resp.text[:200]}")
        return False
    except Exception as e:
        print(f"[sms_alerts] send error: {e}")
        return False


# ── Core scan ─────────────────────────────────────────────────────────────────

def run_sms_alert_scan():
    """
    Runs every 15 min during market hours.
    Checks morning_inflows_cache + fresh Barchart feeds.
    Texts when a stock hits your indicators threshold for the first time today.
    """
    if not sms_configured():
        return

    now_et = datetime.now(_ET)
    # Only run Mon-Fri 9:30 AM – 3:45 PM ET
    if now_et.weekday() >= 5:
        return
    market_open  = now_et.replace(hour=9,  minute=30, second=0, microsecond=0)
    market_close = now_et.replace(hour=15, minute=45, second=0, microsecond=0)
    if now_et < market_open or now_et > market_close:
        return

    candidates = {}  # ticker -> {price, chg_pct, rel_vol, score, reason}

    # ── 1. Pull standouts from morning_inflows_cache ──────────────────────────
    try:
        import json
        with _conn() as con:
            with con.cursor() as cur:
                cur.execute("""
                    SELECT payload FROM morning_inflows_cache
                    WHERE scan_date = CURRENT_DATE
                    ORDER BY saved_at DESC LIMIT 1
                """)
                row = cur.fetchone()
        if row:
            payload = json.loads(row[0]) if isinstance(row[0], str) else row[0]
            for s in payload.get("standouts", []):
                t      = s.get("ticker", "")
                chg    = float(s.get("price_chg_pct") or 0)
                rv     = float(s.get("rel_vol") or 0)
                score  = float(s.get("score") or 0)
                price  = float(s.get("price") or 0)
                min_rv_cache = 1.5 if chg >= 20 else 2.0 if chg >= 10 else 3.0 if chg >= 7 else 4.0 if chg >= 3 else 5.0
                if chg >= 1 and rv >= min_rv_cache:
                    candidates[t] = {"price": price, "chg_pct": chg, "rel_vol": rv, "score": score, "reason": "standout"}
    except Exception as e:
        print(f"[sms_alerts] cache read error: {e}")

    # ── 2. Fresh live scan of all Barchart feeds ──────────────────────────────
    try:
        import yfinance as _yf
        import math as _math

        mins_elapsed = max((now_et - market_open).total_seconds() / 60.0, 1.0)
        day_frac     = min(mins_elapsed / 390.0, 1.0)

        bc_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept":     "application/json",
            "Referer":    "https://www.barchart.com/stocks/advances",
        }
        bc_syms = []
        for bc_list in ("stocks.advances.microcap.us", "stocks.advances.smallcap.us",
                        "stocks.advances.midcap.us",   "stocks.advances.largecap.us"):
            try:
                url = (
                    "https://www.barchart.com/proxies/core-api/v1/quotes/get"
                    f"?fields=symbol%2CpercentChange%2Cvolume%2CaverageVolume&"
                    f"list={bc_list}&orderBy=percentChange&orderDir=desc&raw=1&limit=100"
                )
                r = _req.get(url, headers=bc_headers, timeout=8)
                if r.ok:
                    for row in r.json().get("data", []):
                        sym = (row.get("symbol") or "").strip().upper()
                        pct = float(row.get("percentChange") or 0)
                        if sym and len(sym) <= 5 and "." not in sym and pct >= 1:
                            bc_syms.append(sym)
            except Exception:
                pass

        # Score any Barchart ticker not already in candidates
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _score(ticker):
            try:
                tk   = _yf.Ticker(ticker)
                fi   = tk.fast_info
                prev = float(getattr(fi, "previous_close", 0) or 0)
                avg  = float(getattr(fi, "three_month_average_volume", 1) or 1)
                if prev <= 0 or avg <= 0:
                    return None
                hist = tk.history(period="1d", interval="1m")
                if hist.empty:
                    return None
                hist.index = hist.index.tz_convert(_ET)
                cum_vol   = float(hist["Volume"].sum())
                price     = float(hist["Close"].iloc[-1])
                if price <= 0:
                    return None
                chg_pct   = (price - prev) / prev * 100
                if chg_pct < 1:
                    return None
                proj_vol  = cum_vol / day_frac
                rel_vol   = proj_vol / avg
                # Volume bar scales inversely with move — tiny move needs huge vol to confirm
                min_rv    = 1.5 if chg_pct >= 20 else 2.0 if chg_pct >= 10 else 3.0 if chg_pct >= 7 else 4.0 if chg_pct >= 3 else 5.0
                if rel_vol < min_rv:
                    return None
                score     = rel_vol * (chg_pct / 10)
                return {"ticker": ticker, "price": price, "chg_pct": chg_pct,
                        "rel_vol": rel_vol, "score": score, "reason": "barchart_live"}
            except Exception:
                return None

        new_syms = [s for s in bc_syms if s not in candidates]
        with ThreadPoolExecutor(max_workers=12) as pool:
            futs = {pool.submit(_score, t): t for t in new_syms}
            for fut in as_completed(futs):
                res = fut.result()
                if res:
                    t = res.pop("ticker")
                    candidates[t] = res
    except Exception as e:
        print(f"[sms_alerts] barchart live scan error: {e}")

    # ── 3. Fire texts for new qualifiers ────────────────────────────────────
    sent = 0
    for ticker, d in sorted(candidates.items(), key=lambda x: -x[1].get("score", 0)):
        if _already_alerted_today(ticker):
            continue
        chg   = d["chg_pct"]
        rv    = d["rel_vol"]
        price = d["price"]
        score = d["score"]
        reason = d["reason"]

        # Classify signal
        if chg >= 30:
            emoji = "🔥🔥"
        elif chg >= 20:
            emoji = "🔥"
        elif chg >= 15:
            emoji = "🚨"
        else:
            emoji = "📈"

        msg = (
            f"{emoji} SIGNAL: {ticker} +{chg:.1f}% | {rv:.1f}x vol | ${price:.2f}\n"
            f"Score {score:.0f} | {now_et.strftime('%I:%M %p ET')}\n"
            f"nclexai.org/stock-scanner/"
        )
        if send_sms(msg):
            _log_alert(ticker, price, chg, rv, score, reason)
            sent += 1

    print(f"[sms_alerts] scan complete — {len(candidates)} candidates, {sent} texts sent")
