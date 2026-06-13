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
                # Create table without unique constraint to allow re-alerts
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
                        sent_at     TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                    )
                """)
                # Drop old unique constraint if it exists (migration)
                cur.execute("""
                    DO $$ BEGIN
                        ALTER TABLE sms_alerts_log DROP CONSTRAINT IF EXISTS sms_alerts_log_ticker_alert_date_key;
                    EXCEPTION WHEN others THEN NULL;
                    END $$;
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS sms_alerts_log_ticker_date_idx
                    ON sms_alerts_log (ticker, alert_date, sent_at DESC)
                """)
        print("[sms_alerts] log table ready")
    except Exception as e:
        print(f"[sms_alerts] table init error: {e}")


def _should_skip_alert(ticker: str, current_chg: float) -> bool:
    """
    Skip if alerted in the last 2 hours AND current gain is less than
    1.5x the gain at last alert. Re-alert when a stock significantly
    accelerates (e.g. was +4% at 10am, now +10% at noon).
    """
    try:
        with _conn() as con:
            with con.cursor() as cur:
                cur.execute("""
                    SELECT chg_pct, sent_at FROM sms_alerts_log
                    WHERE ticker=%s AND alert_date=CURRENT_DATE
                    ORDER BY sent_at DESC LIMIT 1
                """, (ticker,))
                row = cur.fetchone()
        if row is None:
            return False  # Never alerted today — go ahead
        last_chg, last_sent = float(row[0] or 0), row[1]
        now_et = datetime.now(_ET)
        if last_sent.tzinfo is None:
            last_sent = pytz.utc.localize(last_sent)
        hours_since = (now_et - last_sent.astimezone(_ET)).total_seconds() / 3600
        # Re-alert if 30+ min passed AND gain grew 1.5x since last alert
        if hours_since >= 0.5 and current_chg >= last_chg * 1.5:
            return False  # Allow re-alert
        return True  # Skip
    except Exception:
        return False


def _log_alert(ticker, price, chg_pct, rel_vol, score, reason):
    try:
        with _conn() as con:
            with con.cursor() as cur:
                cur.execute("""
                    INSERT INTO sms_alerts_log (ticker, price, chg_pct, rel_vol, score, reason)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (ticker, price, chg_pct, rel_vol, score, reason))
    except Exception as e:
        print(f"[sms_alerts] log error {ticker}: {e}")


# ── SMS via email-to-text gateway (primary) ───────────────────────────────────

_SMS_EMAIL_GATEWAY = "4013185787@tmomail.net"  # T-Mobile gateway for +14013185787
_BACKUP_EMAIL      = "joeldcarlo@gmail.com"    # Gmail backup in case SMS gateway drops it

def _send_sms_via_email(message: str) -> bool:
    """Send SMS via T-Mobile email-to-text gateway + backup to Gmail."""
    try:
        from email_alerts import send_email_raw, smtp_configured
        if not smtp_configured():
            print("[sms_alerts] SMTP not configured — skipping email-to-SMS")
            return False
        # Fire SMS gateway
        ok = send_email_raw(to=_SMS_EMAIL_GATEWAY, subject="", html=f"<pre>{message}</pre>")
        if ok:
            print(f"[sms_alerts] SMS via email gateway sent: {message[:60]}…")
        # Always send backup email to Gmail regardless of SMS result
        try:
            send_email_raw(to=_BACKUP_EMAIL, subject=f"📈 StockScanner Alert", html=f"<pre style='font-size:16px'>{message}</pre>")
            print(f"[sms_alerts] Backup email sent to {_BACKUP_EMAIL}")
        except Exception as be:
            print(f"[sms_alerts] Backup email error: {be}")
        return ok
    except Exception as e:
        print(f"[sms_alerts] email-to-SMS error: {e}")
        return False


# ── Twilio sender (fallback) ───────────────────────────────────────────────────

def send_sms(message: str) -> bool:
    # Primary: email-to-SMS gateway (no carrier registration needed)
    if _send_sms_via_email(message):
        return True
    # Fallback: Twilio
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
            print(f"[sms_alerts] SMS via Twilio sent: {message[:60]}…")
            return True
        print(f"[sms_alerts] Twilio error {resp.status_code}: {resp.text[:200]}")
        return False
    except Exception as e:
        print(f"[sms_alerts] send error: {e}")
        return False


# ── Quality scoring helpers (options-aware) ────────────────────────────────────

def _has_options(ticker: str) -> bool:
    """Return True if yfinance reports at least one options expiry for this ticker."""
    try:
        import yfinance as _yf
        return len(_yf.Ticker(ticker).options) > 0
    except Exception:
        return False


def _no_options_score(rv: float, chg: float, above_vwap: bool,
                      gap_pct: float, early_morning: bool) -> int:
    """
    Pure price/volume score 0-100 for stocks without (or ignoring) options flow.
    Used to set quality label on every outgoing SMS.
      RVOL        25 pts
      Price chg   20 pts
      Above VWAP  15 pts
      Gap         10 pts
      Consec /MFI 10 pts  (approximated via rv+chg strength)
      VWAP recl.   5 pts  (proxy: above_vwap with chg momentum)
      Early AM     5 pts
    Total max = 90 (10 reserved for MFI/consec not computed here)
    """
    pts = 0
    # RVOL (25)
    if   rv >= 15: pts += 25
    elif rv >= 10: pts += 22
    elif rv >= 5:  pts += 18
    elif rv >= 3:  pts += 12
    elif rv >= 2:  pts += 6
    # Price chg (20)
    if   chg >= 15: pts += 20
    elif chg >= 7:  pts += 18
    elif chg >= 3:  pts += 14
    elif chg >= 1:  pts += 8
    # Above VWAP (15)
    if above_vwap:  pts += 15
    # Gap from prior close (10)
    if   gap_pct >= 20: pts += 10
    elif gap_pct >= 10: pts += 8
    elif gap_pct >= 5:  pts += 6
    elif gap_pct >= 1:  pts += 3
    # VWAP momentum proxy (5) — above VWAP AND strong chg = high prob reclaim
    if above_vwap and chg >= 3: pts += 5
    # Early morning premium (5)
    if early_morning: pts += 5
    return min(pts, 100)


def _quality_prefix(score: int) -> str:
    """
    SMS quality label based on no-options score.
      80+ → 🟢🔥  (exceptional — institutions piling in)
      65+ → 🔥    (strong signal)
      60+ → 📈    (solid signal)
    """
    if score >= 80: return "🟢🔥"
    if score >= 65: return "🔥"
    return "📈"


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
                # VWAP: cumulative(typical_price * volume) / cumulative(volume)
                hist["_tp"] = (hist["High"] + hist["Low"] + hist["Close"]) / 3
                tp_vol_sum  = float((hist["_tp"] * hist["Volume"]).sum())
                vwap        = tp_vol_sum / cum_vol if cum_vol > 0 else price
                above_vwap  = price >= vwap
                open_price  = float(hist["Open"].iloc[0])
                gap_pct     = (open_price - prev) / prev * 100 if prev > 0 else 0.0
                score       = rel_vol * (chg_pct / 10)
                if above_vwap:
                    score *= 1.2  # boost score for stocks holding above VWAP
                return {"ticker": ticker, "price": price, "chg_pct": chg_pct,
                        "rel_vol": rel_vol, "score": score, "vwap": vwap,
                        "above_vwap": above_vwap, "gap_pct": gap_pct,
                        "reason": "barchart_live"}
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
        chg   = d["chg_pct"]
        if _should_skip_alert(ticker, chg):
            continue
        rv          = d["rel_vol"]
        price       = d["price"]
        score       = d["score"]
        reason      = d["reason"]
        vwap        = d.get("vwap")
        above_vwap  = d.get("above_vwap")

        # Quality score (pure price/volume — no options confusion)
        gap_pct_val  = d.get("gap_pct", 0.0)
        early_flag   = (now_et.hour == 9 or (now_et.hour == 10 and now_et.minute <= 30))
        nopt_score   = _no_options_score(rv, chg, above_vwap, gap_pct_val, early_flag)
        quality      = _quality_prefix(nopt_score)

        vwap_line = ""
        if vwap:
            vwap_status = "✅ above VWAP" if above_vwap else "⚠️ below VWAP"
            stop_price  = round(vwap * 0.995, 2)
            vwap_line   = f"VWAP ${vwap:.2f} — {vwap_status}  stop ${stop_price:.2f}\n"

        msg = (
            f"{quality} MORNING BURST: {ticker} +{chg:.1f}% | {rv:.1f}x vol | ${price:.2f}\n"
            f"{vwap_line}"
            f"Score {nopt_score}/100 | {now_et.strftime('%I:%M %p ET')}\n"
            f"nclexai.org/stock-scanner/"
        )
        if send_sms(msg):
            _log_alert(ticker, price, chg, rv, score, reason)
            sent += 1

    print(f"[sms_alerts] scan complete — {len(candidates)} candidates, {sent} texts sent")


# ── Exit alert system ─────────────────────────────────────────────────────────

_PROFIT_TARGET_PCT = 10.0  # fire a "take profit" alert when gain hits this % from entry

def init_exit_log_table():
    try:
        with _conn() as con:
            with con.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS sms_exit_log (
                        id          SERIAL PRIMARY KEY,
                        ticker      TEXT NOT NULL,
                        exit_date   DATE NOT NULL DEFAULT CURRENT_DATE,
                        price       NUMERIC,
                        chg_pct     NUMERIC,
                        vwap        NUMERIC,
                        entry_price NUMERIC,
                        sent_at     TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                        UNIQUE (ticker, exit_date)
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS sms_profit_log (
                        id          SERIAL PRIMARY KEY,
                        ticker      TEXT NOT NULL,
                        profit_date DATE NOT NULL DEFAULT CURRENT_DATE,
                        price       NUMERIC,
                        gain_pct    NUMERIC,
                        entry_price NUMERIC,
                        sent_at     TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                        UNIQUE (ticker, profit_date)
                    )
                """)
        print("[sms_alerts] exit + profit log tables ready")
    except Exception as e:
        print(f"[sms_alerts] exit table init error: {e}")


def _already_exit_alerted(ticker: str) -> bool:
    try:
        with _conn() as con:
            with con.cursor() as cur:
                cur.execute("""
                    SELECT 1 FROM sms_exit_log
                    WHERE ticker=%s AND exit_date=CURRENT_DATE LIMIT 1
                """, (ticker,))
                return cur.fetchone() is not None
    except Exception:
        return False


def _already_profit_alerted(ticker: str) -> bool:
    try:
        with _conn() as con:
            with con.cursor() as cur:
                cur.execute("""
                    SELECT 1 FROM sms_profit_log
                    WHERE ticker=%s AND profit_date=CURRENT_DATE LIMIT 1
                """, (ticker,))
                return cur.fetchone() is not None
    except Exception:
        return False


def _log_profit_alert(ticker, price, gain_pct, entry_price):
    try:
        with _conn() as con:
            with con.cursor() as cur:
                cur.execute("""
                    INSERT INTO sms_profit_log (ticker, price, gain_pct, entry_price)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (ticker, profit_date) DO NOTHING
                """, (ticker, price, gain_pct, entry_price))
    except Exception as e:
        print(f"[sms_alerts] profit log error {ticker}: {e}")


def _log_exit_alert(ticker, price, chg_pct, vwap, entry_price):
    try:
        with _conn() as con:
            with con.cursor() as cur:
                cur.execute("""
                    INSERT INTO sms_exit_log (ticker, price, chg_pct, vwap, entry_price)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (ticker, exit_date) DO NOTHING
                """, (ticker, price, chg_pct, vwap, entry_price))
    except Exception as e:
        print(f"[sms_alerts] exit log error {ticker}: {e}")


def _get_today_alerted_tickers() -> list:
    """Return list of (ticker, entry_price, entry_chg) alerted today."""
    try:
        with _conn() as con:
            with con.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT ON (ticker) ticker, price, chg_pct
                    FROM sms_alerts_log
                    WHERE alert_date = CURRENT_DATE
                    ORDER BY ticker, sent_at ASC
                """)
                return cur.fetchall()
    except Exception:
        return []


def run_exit_alert_scan():
    """
    Runs every 15 min during market hours alongside run_sms_alert_scan.
    Checks stocks alerted today — if any break below VWAP, fires an exit text.
    Only one exit alert per ticker per day.
    """
    if not sms_configured():
        return

    now_et = datetime.now(_ET)
    if now_et.weekday() >= 5:
        return
    market_open  = now_et.replace(hour=9,  minute=30, second=0, microsecond=0)
    market_close = now_et.replace(hour=15, minute=45, second=0, microsecond=0)
    if now_et < market_open or now_et > market_close:
        return

    alerted = _get_today_alerted_tickers()
    if not alerted:
        return

    import yfinance as _yf
    sent = 0

    for ticker, entry_price, entry_chg in alerted:
        if _already_exit_alerted(ticker):
            continue
        try:
            tk   = _yf.Ticker(ticker)
            fi   = tk.fast_info
            prev = float(getattr(fi, "previous_close", 0) or 0)
            hist = tk.history(period="1d", interval="1m")
            if hist.empty or prev <= 0:
                continue
            hist.index = hist.index.tz_convert(_ET)
            price = float(hist["Close"].iloc[-1])
            vol   = float(hist["Volume"].sum())
            if vol <= 0:
                continue
            # Calculate current VWAP
            hist["_tp"] = (hist["High"] + hist["Low"] + hist["Close"]) / 3
            tp_vol_sum  = float((hist["_tp"] * hist["Volume"]).sum())
            vwap        = tp_vol_sum / vol
            chg_pct     = (price - prev) / prev * 100

            # Calculate gain from entry price
            entry_gain = ((price - float(entry_price or prev)) / float(entry_price or prev) * 100) if entry_price else chg_pct

            # ── Profit target alert (+10% from entry) ────────────────────────
            if entry_gain >= _PROFIT_TARGET_PCT and not _already_profit_alerted(ticker):
                vwap_status = "✅ still above VWAP" if price >= vwap else "⚠️ approaching VWAP"
                profit_msg = (
                    f"🎯 PROFIT TARGET: {ticker} +{entry_gain:.1f}%!\n"
                    f"Price ${price:.2f} (entry ${float(entry_price):.2f})\n"
                    f"VWAP ${vwap:.2f} — {vwap_status}\n"
                    f"Consider selling | {now_et.strftime('%I:%M %p ET')}"
                )
                if send_sms(profit_msg):
                    _log_profit_alert(ticker, price, entry_gain, entry_price)
                    sent += 1

            # ── VWAP break exit alert ─────────────────────────────────────────
            if price >= vwap:
                continue
            if _already_exit_alerted(ticker):
                continue

            gain_str = f"+{entry_gain:.1f}% from entry" if entry_gain > 0 else f"{entry_gain:.1f}% from entry"
            status   = "🟡 still profitable" if entry_gain > 0 else "🔴 at a loss"

            msg = (
                f"🚪 EXIT: {ticker} broke below VWAP\n"
                f"Price ${price:.2f} ({gain_str}) {status}\n"
                f"VWAP ${vwap:.2f} — momentum fading\n"
                f"Consider locking in | {now_et.strftime('%I:%M %p ET')}"
            )
            if send_sms(msg):
                _log_exit_alert(ticker, price, chg_pct, vwap, entry_price)
                sent += 1
        except Exception as e:
            print(f"[sms_alerts] exit check error {ticker}: {e}")

    print(f"[sms_alerts] exit scan complete — {len(alerted)} watched, {sent} exit alerts sent")


# ── Mid-Day Breakout + Gap Recovery dedup table ───────────────────────────────

def init_midday_log_table():
    try:
        with _conn() as con:
            with con.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS sms_midday_log (
                        id          SERIAL PRIMARY KEY,
                        ticker      TEXT NOT NULL,
                        alert_date  DATE NOT NULL DEFAULT CURRENT_DATE,
                        alert_type  TEXT NOT NULL,
                        price       NUMERIC,
                        chg_pct     NUMERIC,
                        rel_vol     NUMERIC,
                        score       NUMERIC,
                        sent_at     TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                        UNIQUE (ticker, alert_date, alert_type)
                    )
                """)
        print("[sms_alerts] midday log table ready")
    except Exception as e:
        print(f"[sms_alerts] midday table init error: {e}")


def _already_midday_alerted(ticker: str, alert_type: str) -> bool:
    try:
        with _conn() as con:
            with con.cursor() as cur:
                cur.execute("""
                    SELECT 1 FROM sms_midday_log
                    WHERE ticker=%s AND alert_date=CURRENT_DATE AND alert_type=%s LIMIT 1
                """, (ticker, alert_type))
                return cur.fetchone() is not None
    except Exception:
        return False


def _log_midday_alert(ticker, price, chg_pct, rel_vol, score, alert_type):
    try:
        with _conn() as con:
            with con.cursor() as cur:
                cur.execute("""
                    INSERT INTO sms_midday_log (ticker, price, chg_pct, rel_vol, score, alert_type)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (ticker, alert_date, alert_type) DO NOTHING
                """, (ticker, price, chg_pct, rel_vol, score, alert_type))
    except Exception as e:
        print(f"[sms_alerts] midday log error {ticker}: {e}")


# ── Mid-Day Breakout scanner ───────────────────────────────────────────────────

def run_midday_breakout_scan():
    """
    Runs every 15 min, 10:30 AM – 3:30 PM ET.
    Looks for stocks that are >2% from open, above VWAP, with 15-min
    momentum >1% — trend is confirmed, lower risk than morning entry.
    One text per ticker per day for this alert type.
    """
    now_et = datetime.now(_ET)
    if now_et.weekday() >= 5:
        return
    start  = now_et.replace(hour=10, minute=30, second=0, microsecond=0)
    end    = now_et.replace(hour=15, minute=30, second=0, microsecond=0)
    if now_et < start or now_et > end:
        return

    try:
        import yfinance as _yf
        import math as _math
        from concurrent.futures import ThreadPoolExecutor, as_completed

        market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
        mins_elapsed = max((now_et - market_open).total_seconds() / 60.0, 1.0)
        day_frac    = min(mins_elapsed / 390.0, 1.0)

        bc_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept":     "application/json",
            "Referer":    "https://www.barchart.com/stocks/advances",
        }
        bc_syms = set()
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
                        if sym and len(sym) <= 5 and "." not in sym and pct >= 2:
                            bc_syms.add(sym)
            except Exception:
                pass

        def _check_midday(ticker):
            try:
                tk   = _yf.Ticker(ticker)
                fi   = tk.fast_info
                prev = float(getattr(fi, "previous_close", 0) or 0)
                avg  = float(getattr(fi, "three_month_average_volume", 1) or 1)
                if prev <= 0 or avg <= 0:
                    return None
                hist = tk.history(period="1d", interval="1m")
                if hist.empty or len(hist) < 16:
                    return None
                hist.index = hist.index.tz_convert(_ET)
                cum_vol    = float(hist["Volume"].sum())
                price      = float(hist["Close"].iloc[-1])
                open_p     = float(hist["Open"].iloc[0])
                if price <= 0 or open_p <= 0:
                    return None
                chg_from_open  = (price - open_p) / open_p * 100
                chg_from_prev  = (price - prev) / prev * 100
                if chg_from_open < 2.0:
                    return None
                proj_vol   = cum_vol / day_frac
                rel_vol    = proj_vol / avg
                if rel_vol < 2.0:
                    return None
                # VWAP
                hist["_tp"]  = (hist["High"] + hist["Low"] + hist["Close"]) / 3
                tp_vol_sum   = float((hist["_tp"] * hist["Volume"]).sum())
                vwap         = tp_vol_sum / cum_vol if cum_vol > 0 else price
                if price < vwap:
                    return None  # must be above VWAP
                # 15-min momentum: compare last 15 bars vs 15 bars before that
                last_15      = hist.tail(15)
                prev_15      = hist.iloc[-30:-15] if len(hist) >= 30 else hist.head(15)
                momentum_15m = (float(last_15["Close"].iloc[-1]) - float(prev_15["Close"].iloc[-1])) / float(prev_15["Close"].iloc[-1]) * 100
                if momentum_15m < 1.0:
                    return None  # no momentum
                gap_pct = (open_p - prev) / prev * 100 if prev > 0 else 0.0
                score   = _no_options_score(rel_vol, chg_from_prev, True, gap_pct, early_morning=False)
                return {
                    "ticker": ticker, "price": price, "chg_from_open": chg_from_open,
                    "chg_pct": chg_from_prev, "rel_vol": rel_vol, "vwap": vwap,
                    "momentum_15m": momentum_15m, "gap_pct": gap_pct, "score": score,
                }
            except Exception:
                return None

        candidates = [s for s in bc_syms if not _already_midday_alerted(s, "midday")]
        results = []
        with ThreadPoolExecutor(max_workers=12) as pool:
            futs = {pool.submit(_check_midday, t): t for t in candidates}
            for fut in as_completed(futs):
                res = fut.result()
                if res:
                    results.append(res)

        results.sort(key=lambda x: -x["score"])
        sent = 0
        for d in results:
            ticker  = d["ticker"]
            chg     = d["chg_pct"]
            rv      = d["rel_vol"]
            price   = d["price"]
            vwap    = d["vwap"]
            m15     = d["momentum_15m"]
            score   = d["score"]
            chg_open = d["chg_from_open"]
            quality = _quality_prefix(score)
            stop_p  = round(vwap * 0.995, 2)
            msg = (
                f"{quality} MIDDAY BREAKOUT: {ticker} +{chg:.1f}% | {rv:.1f}x vol | ${price:.2f}\n"
                f"Above VWAP ${vwap:.2f} ✅  stop ${stop_p:.2f}\n"
                f"+{m15:.1f}% last 15 min  |  +{chg_open:.1f}% from open\n"
                f"Score {score}/100 | {now_et.strftime('%I:%M %p ET')}\n"
                f"nclexai.org/stock-scanner/"
            )
            if send_sms(msg):
                _log_midday_alert(ticker, price, chg, rv, score, "midday")
                sent += 1

        print(f"[sms_alerts] midday breakout scan — {len(candidates)} checked, {sent} texts sent")
    except Exception as e:
        print(f"[sms_alerts] midday breakout scan error: {e}")


# ── Gap Recovery scanner ───────────────────────────────────────────────────────

def run_gap_recovery_scan():
    """
    Runs every 15 min, 10:30 AM – 1:00 PM ET.
    Targets stocks that gapped up 20%+ at open, sold off, then reclaimed VWAP
    with fresh momentum. Classic short-squeeze setup after morning shakeout.
    One text per ticker per day for this alert type.
    """
    now_et = datetime.now(_ET)
    if now_et.weekday() >= 5:
        return
    start  = now_et.replace(hour=10, minute=30, second=0, microsecond=0)
    end    = now_et.replace(hour=13, minute=0,  second=0, microsecond=0)
    if now_et < start or now_et > end:
        return

    try:
        import yfinance as _yf
        from concurrent.futures import ThreadPoolExecutor, as_completed

        market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
        mins_elapsed = max((now_et - market_open).total_seconds() / 60.0, 1.0)
        day_frac    = min(mins_elapsed / 390.0, 1.0)

        bc_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept":     "application/json",
            "Referer":    "https://www.barchart.com/stocks/advances",
        }
        # Target big gappers (>20% from prior close)
        bc_syms = set()
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
                        if sym and len(sym) <= 5 and "." not in sym and pct >= 20:
                            bc_syms.add(sym)
            except Exception:
                pass

        def _check_gap_recovery(ticker):
            try:
                tk   = _yf.Ticker(ticker)
                fi   = tk.fast_info
                prev = float(getattr(fi, "previous_close", 0) or 0)
                avg  = float(getattr(fi, "three_month_average_volume", 1) or 1)
                if prev <= 0 or avg <= 0:
                    return None
                hist = tk.history(period="1d", interval="1m")
                if hist.empty or len(hist) < 16:
                    return None
                hist.index = hist.index.tz_convert(_ET)
                cum_vol    = float(hist["Volume"].sum())
                price      = float(hist["Close"].iloc[-1])
                open_p     = float(hist["Open"].iloc[0])
                if price <= 0 or open_p <= 0:
                    return None
                gap_pct    = (open_p - prev) / prev * 100 if prev > 0 else 0.0
                if gap_pct < 20:
                    return None  # only big gappers qualify
                proj_vol   = cum_vol / day_frac
                rel_vol    = proj_vol / avg
                if rel_vol < 3.0:
                    return None
                # VWAP
                hist["_tp"]  = (hist["High"] + hist["Low"] + hist["Close"]) / 3
                tp_vol_sum   = float((hist["_tp"] * hist["Volume"]).sum())
                vwap         = tp_vol_sum / cum_vol if cum_vol > 0 else price
                if price < vwap:
                    return None  # must have reclaimed VWAP
                chg_pct_prev = (price - prev) / prev * 100
                # 15-min momentum
                last_15      = hist.tail(15)
                prev_15      = hist.iloc[-30:-15] if len(hist) >= 30 else hist.head(15)
                momentum_15m = (float(last_15["Close"].iloc[-1]) - float(prev_15["Close"].iloc[-1])) / float(prev_15["Close"].iloc[-1]) * 100
                if momentum_15m < 1.5:
                    return None  # higher bar — gap recovery needs stronger momentum
                # Check that price pulled back from open (classic gap recovery shape)
                intraday_low = float(hist["Low"].min())
                pullback_pct = (open_p - intraday_low) / open_p * 100 if open_p > 0 else 0
                if pullback_pct < 3.0:
                    return None  # no real shakeout = not a recovery pattern
                score = _no_options_score(rel_vol, chg_pct_prev, True, gap_pct, early_morning=False)
                # Gap recovery bonus — big pullback + reclaim = higher conviction
                if pullback_pct >= 10:
                    score = min(score + 10, 100)
                elif pullback_pct >= 5:
                    score = min(score + 5, 100)
                return {
                    "ticker": ticker, "price": price, "chg_pct": chg_pct_prev,
                    "rel_vol": rel_vol, "vwap": vwap, "gap_pct": gap_pct,
                    "momentum_15m": momentum_15m, "pullback_pct": pullback_pct,
                    "score": score,
                }
            except Exception:
                return None

        candidates = [s for s in bc_syms if not _already_midday_alerted(s, "gap_recovery")]
        results = []
        with ThreadPoolExecutor(max_workers=12) as pool:
            futs = {pool.submit(_check_gap_recovery, t): t for t in candidates}
            for fut in as_completed(futs):
                res = fut.result()
                if res:
                    results.append(res)

        results.sort(key=lambda x: -x["score"])
        sent = 0
        for d in results:
            ticker  = d["ticker"]
            chg     = d["chg_pct"]
            rv      = d["rel_vol"]
            price   = d["price"]
            vwap    = d["vwap"]
            gap     = d["gap_pct"]
            m15     = d["momentum_15m"]
            pb      = d["pullback_pct"]
            score   = d["score"]
            quality = _quality_prefix(score)
            stop_p  = round(vwap * 0.995, 2)
            msg = (
                f"{quality} GAP RECOVERY: {ticker} reclaimed VWAP | ${price:.2f}\n"
                f"Gap +{gap:.0f}% | pulled back {pb:.0f}% then recovered\n"
                f"VWAP ${vwap:.2f} ✅  stop ${stop_p:.2f}  |  {rv:.1f}x vol\n"
                f"+{m15:.1f}% last 15 min  |  Score {score}/100\n"
                f"{now_et.strftime('%I:%M %p ET')} | nclexai.org/stock-scanner/"
            )
            if send_sms(msg):
                _log_midday_alert(ticker, price, chg, rv, score, "gap_recovery")
                sent += 1

        print(f"[sms_alerts] gap recovery scan — {len(candidates)} checked, {sent} texts sent")
    except Exception as e:
        print(f"[sms_alerts] gap recovery scan error: {e}")
