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
                      gap_pct: float, early_morning: bool,
                      float_turnover: float = 0.0,
                      orb_break: bool = False,
                      atr_multiple: float = 0.0,
                      short_float: float = 0.0) -> int:
    """
    Pure price/volume score 0-100 aligned with the quant framework for
    micro/small-cap stocks without options activity.

    Scoring categories (total 100):
      RVOL             25 pts  — substitute for options unusual activity
      Float Turnover   20 pts  — Volume/Float; 50-100%+ = high conviction move
      Above VWAP       15 pts  — institutions use this as the only clean benchmark
      Gap %            10 pts  — gap-and-go vs gap-and-fade filter
      ORB Breakout     10 pts  — 5-min opening range break = systematic entry signal
      Price chg         8 pts  — confirms direction
      ATR expansion     7 pts  — today's move vs average daily range (extension check)
      Short Float       5 pts  — >15% float short = squeeze fuel bonus
    """
    pts = 0

    # RVOL (25) — min 3x threshold for small caps per quant framework
    if   rv >= 15: pts += 25
    elif rv >= 10: pts += 22
    elif rv >= 5:  pts += 18
    elif rv >= 3:  pts += 12
    elif rv >= 2:  pts += 6

    # Float Turnover (20) — Volume / Float shares; 50%+ = something real happening
    if   float_turnover >= 2.0: pts += 20   # 200%+ float rotation = extreme squeeze
    elif float_turnover >= 1.0: pts += 17   # 100%+ = full float traded
    elif float_turnover >= 0.5: pts += 13   # 50%+ = high conviction
    elif float_turnover >= 0.2: pts += 7    # 20%+ = notable interest
    elif float_turnover >= 0.1: pts += 3

    # Above VWAP (15) — buyers in control
    if above_vwap: pts += 15

    # Gap % (10) — gap holds pre-market high = gap-and-go signal
    if   gap_pct >= 20: pts += 10
    elif gap_pct >= 10: pts += 8
    elif gap_pct >= 5:  pts += 6
    elif gap_pct >= 1:  pts += 3

    # ORB Breakout (10) — broke the 5-min opening range high = systematic entry
    if orb_break: pts += 10

    # Price change (8)
    if   chg >= 15: pts += 8
    elif chg >= 7:  pts += 6
    elif chg >= 3:  pts += 4
    elif chg >= 1:  pts += 2

    # ATR multiple (7) — today's move vs 14-day ATR; 1x-2x ATR = healthy, >3x = extended
    if   1.0 <= atr_multiple < 2.0: pts += 7   # ideal range: 1x ATR target reachable
    elif 2.0 <= atr_multiple < 3.0: pts += 4   # hitting 2x ATR — partial profit zone
    elif atr_multiple >= 0.5:       pts += 2

    # Short float bonus (5) — >15% short float = squeeze fuel
    if   short_float >= 0.30: pts += 5
    elif short_float >= 0.20: pts += 4
    elif short_float >= 0.15: pts += 3

    # Early morning premium — strongest window
    if early_morning: pts += 3  # small premium, not a primary factor

    return min(pts, 100)


def _with_options_score(rv: float, chg: float, above_vwap: bool,
                        gap_pct: float, orb_break: bool = False,
                        atr_multiple: float = 0.0) -> int:
    """
    Institutional momentum score for stocks that have options (any cap size).
    Replaces float rotation with RVOL + VWAP + price momentum signals.
    Max 100 pts, fire threshold 55.

    Scoring categories (total 100):
      RVOL             30 pts  — institutional conviction; 3x+ on large-cap = real money
      Price change     25 pts  — large-caps rarely move 10%+; reward it heavily
      Above VWAP       20 pts  — institutions watch VWAP; close above = trend confirmed
      ORB Breakout     10 pts  — systematic entry signal
      Gap/catalyst      8 pts  — news-driven gap = catalyst confirmation
      ATR expansion     7 pts  — today's range vs 14-day ATR
    """
    pts = 0

    # RVOL (30) — 3x+ on a large-cap is genuine institutional activity
    if   rv >= 10: pts += 30
    elif rv >= 7:  pts += 25
    elif rv >= 5:  pts += 20
    elif rv >= 3:  pts += 15
    elif rv >= 2:  pts += 8
    elif rv >= 1.5: pts += 4

    # Price change (25) — must move meaningfully to justify alerting on large-cap
    if   chg >= 15: pts += 25
    elif chg >= 10: pts += 20
    elif chg >= 7:  pts += 15
    elif chg >= 5:  pts += 10
    elif chg >= 3:  pts += 5

    # Above VWAP (20) — heavier than micro-cap; VWAP is the primary institutional benchmark
    if above_vwap: pts += 20

    # ORB break (10) — broke the 5-min opening range high
    if orb_break: pts += 10

    # Gap/catalyst (8)
    if   gap_pct >= 10: pts += 8
    elif gap_pct >= 5:  pts += 6
    elif gap_pct >= 1:  pts += 3

    # ATR expansion (7) — range expansion vs 14-day average
    if   1.0 <= atr_multiple < 2.0: pts += 7
    elif 2.0 <= atr_multiple < 3.0: pts += 5
    elif atr_multiple >= 3.0:       pts += 3
    elif atr_multiple >= 0.5:       pts += 2

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


def _cap_label(mkt_cap: float) -> str:
    """Return cap-size label from market cap in dollars."""
    if   mkt_cap >= 10_000_000_000: return "LARGE CAP"
    elif mkt_cap >= 2_000_000_000:  return "MID CAP"
    elif mkt_cap >= 300_000_000:    return "SMALL CAP"
    elif mkt_cap > 0:               return "MICRO CAP"
    return ""


def _spy_is_green() -> bool:
    """
    Returns True if SPY is currently trading above yesterday's close.
    Backtest showed: on red SPY days hit rate drops to ~10% and every
    scanner version loses money. Only fire alerts on green market days.
    """
    try:
        import yfinance as _yf
        spy = _yf.Ticker("SPY")
        fi  = spy.fast_info
        prev_close = float(getattr(fi, "previous_close", 0) or 0)
        last_price = float(getattr(fi, "last_price", 0) or 0)
        if prev_close <= 0 or last_price <= 0:
            return True  # data unavailable — don't block alerts
        is_green = last_price > prev_close
        print(f"[sms_alerts] SPY check: ${last_price:.2f} vs prev ${prev_close:.2f} → {'GREEN ✅' if is_green else 'RED ❌ — suppressing alerts'}")
        return is_green
    except Exception as e:
        print(f"[sms_alerts] SPY check error: {e} — allowing alerts")
        return True  # fail open


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
    # Only run Mon-Fri 10:00 AM – 3:45 PM ET
    # Backtest showed pre-10 AM signals are opening-bell noise (13% hit rate vs 45% post-10 AM)
    if now_et.weekday() >= 5:
        return
    market_open  = now_et.replace(hour=10, minute=0,  second=0, microsecond=0)
    market_close = now_et.replace(hour=15, minute=45, second=0, microsecond=0)
    if now_et < market_open or now_et > market_close:
        return

    # SPY green-day filter — suppress all alerts on red market days
    # Backtest: green SPY days = 45% hit rate (+$1,057/week); red days = 10% (-$1,109/week)
    if not _spy_is_green():
        print("[sms_alerts] SPY red day — skipping intraday scan")
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
                # Large/mid-cap (avg vol ≥ 500k): lower +3-7% tier to 2.5x
                # Catches FRO-type institutional grinders; backtest confirmed clean on green days
                if avg >= 500_000 and 3.0 <= chg_pct < 7.0:
                    min_rv = 2.5
                elif chg_pct >= 20: min_rv = 1.5
                elif chg_pct >= 10: min_rv = 2.0
                elif chg_pct >= 7:  min_rv = 3.0
                elif chg_pct >= 3:  min_rv = 4.0
                else:               min_rv = 5.0
                if rel_vol < min_rv:
                    return None
                # VWAP
                hist["_tp"] = (hist["High"] + hist["Low"] + hist["Close"]) / 3
                tp_vol_sum  = float((hist["_tp"] * hist["Volume"]).sum())
                vwap        = tp_vol_sum / cum_vol if cum_vol > 0 else price
                above_vwap  = price >= vwap
                open_price  = float(hist["Open"].iloc[0])
                gap_pct     = (open_price - prev) / prev * 100 if prev > 0 else 0.0

                # 5-min ORB (Opening Range Breakout) — first 5 candles set the range
                orb_high  = float(hist.head(5)["High"].max()) if len(hist) >= 5 else price
                orb_break = price > orb_high

                # Float turnover + short float + market cap + options flag
                float_turnover = 0.0
                short_float    = 0.0
                mkt_cap        = 0.0
                has_opts       = False
                try:
                    info         = tk.info
                    float_shares = float(info.get("floatShares") or 0)
                    if float_shares > 0:
                        float_turnover = cum_vol / float_shares
                    short_float = float(info.get("shortPercentOfFloat") or 0)
                    mkt_cap     = float(info.get("marketCap") or 0)
                except Exception:
                    pass
                try:
                    has_opts = len(tk.options) > 0
                except Exception:
                    pass

                # 14-day ATR from daily history (graceful fallback)
                atr = 0.0
                try:
                    daily = tk.history(period="15d", interval="1d")
                    if len(daily) >= 5:
                        prev_c = daily["Close"].shift(1)
                        trs    = [max(h - l, abs(h - pc), abs(l - pc))
                                  for h, l, pc in zip(daily["High"].values[-14:],
                                                       daily["Low"].values[-14:],
                                                       prev_c.values[-14:])
                                  if pc > 0]
                        atr = sum(trs) / len(trs) if trs else 0.0
                except Exception:
                    pass

                atr_multiple = (price - open_price) / atr if atr > 0 else 0.0

                score = rel_vol * (chg_pct / 10)
                if above_vwap:
                    score *= 1.2
                return {"ticker": ticker, "price": price, "chg_pct": chg_pct,
                        "rel_vol": rel_vol, "score": score, "vwap": vwap,
                        "above_vwap": above_vwap, "gap_pct": gap_pct,
                        "orb_break": orb_break, "orb_high": orb_high,
                        "float_turnover": float_turnover, "short_float": short_float,
                        "atr": atr, "atr_multiple": atr_multiple, "mkt_cap": mkt_cap,
                        "has_options": has_opts, "reason": "barchart_live"}
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

        # Pull new quant indicator fields (graceful defaults for standout cache entries)
        gap_pct_val     = d.get("gap_pct", 0.0)
        orb_break_val   = d.get("orb_break", False)
        float_turn_val  = d.get("float_turnover", 0.0)
        short_fl_val    = d.get("short_float", 0.0)
        atr_mult_val    = d.get("atr_multiple", 0.0)
        atr_val         = d.get("atr", 0.0)
        mkt_cap_val     = d.get("mkt_cap", 0.0)
        has_options_val = d.get("has_options", False)

        early_flag  = (now_et.hour == 9 or (now_et.hour == 10 and now_et.minute <= 30))
        if has_options_val:
            nopt_score = _with_options_score(rv, chg, above_vwap, gap_pct_val,
                                          orb_break=orb_break_val,
                                          atr_multiple=atr_mult_val)
            threshold = 50
        else:
            nopt_score = _no_options_score(rv, chg, above_vwap, gap_pct_val, early_flag,
                                           float_turnover=float_turn_val,
                                           orb_break=orb_break_val,
                                           atr_multiple=atr_mult_val,
                                           short_float=short_fl_val)
            threshold = 60
        if nopt_score < threshold:
            continue
        quality     = _quality_prefix(nopt_score)

        vwap_line = ""
        if vwap:
            vwap_status = "✅ above VWAP" if above_vwap else "⚠️ below VWAP"
            stop_price  = round(vwap * 0.995, 2)
            vwap_line   = f"VWAP ${vwap:.2f} — {vwap_status}  stop ${stop_price:.2f}\n"

        # ATR targets (1x = take partial, 2x = runner — per quant framework)
        atr_line = ""
        if atr_val > 0:
            t1 = round(price + atr_val, 2)
            t2 = round(price + 2 * atr_val, 2)
            atr_line = f"ATR targets: ${t1:.2f} (1x) / ${t2:.2f} (2x)\n"

        # Cap label + detail line: shows cap size, options status, and float rotation
        cap_lbl    = _cap_label(mkt_cap_val)
        float_line = ""
        if has_options_val:
            float_line = f"{cap_lbl} | has options\n"
        elif float_turn_val >= 0.2:
            ft_pct = float_turn_val * 100
            ft_str = f"{cap_lbl} | Float rotation {ft_pct:.0f}%"
            if short_fl_val >= 0.15:
                ft_str += f" | {short_fl_val*100:.0f}% short 🔥"
            float_line = ft_str + "\n"
        elif cap_lbl:
            float_line = f"{cap_lbl} | no options\n"

        orb_tag = " | ✅ ORB break" if orb_break_val else ""

        msg = (
            f"{quality} MORNING BURST: {ticker} +{chg:.1f}% | {rv:.1f}x vol | ${price:.2f}{orb_tag}\n"
            f"{vwap_line}"
            f"{atr_line}"
            f"{float_line}"
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
    if not _spy_is_green():
        print("[sms_alerts] SPY red day — skipping midday breakout scan")
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
                if chg_from_prev > 5.0:
                    return None  # already up >5% — move is too extended for midday entry
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
                # Distance from high of day — skip if price is at/near HOD (exhausted move)
                high_of_day   = float(hist["High"].max())
                pct_from_high = (high_of_day - price) / high_of_day * 100
                if pct_from_high < 2.0:
                    return None  # at or within 2% of HOD = no upside room left
                # 15-min momentum: compare last 15 bars vs 15 bars before that
                last_15      = hist.tail(15)
                prev_15      = hist.iloc[-30:-15] if len(hist) >= 30 else hist.head(15)
                momentum_15m = (float(last_15["Close"].iloc[-1]) - float(prev_15["Close"].iloc[-1])) / float(prev_15["Close"].iloc[-1]) * 100
                if momentum_15m < 2.0:
                    return None  # require meaningful current momentum, not a fading move
                gap_pct = (open_p - prev) / prev * 100 if prev > 0 else 0.0
                # Market cap + options flag for routing
                mkt_cap  = 0.0
                has_opts = False
                try:
                    mkt_cap = float(tk.info.get("marketCap") or 0)
                except Exception:
                    pass
                try:
                    has_opts = len(tk.options) > 0
                except Exception:
                    pass
                if has_opts:
                    score = _with_options_score(rel_vol, chg_from_prev, True, gap_pct)
                else:
                    score = _no_options_score(rel_vol, chg_from_prev, True, gap_pct, early_morning=False)
                return {
                    "ticker": ticker, "price": price, "chg_from_open": chg_from_open,
                    "chg_pct": chg_from_prev, "rel_vol": rel_vol, "vwap": vwap,
                    "momentum_15m": momentum_15m, "gap_pct": gap_pct, "score": score,
                    "mkt_cap": mkt_cap, "has_options": has_opts,
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
            has_opts = d.get("has_options", False)
            mkt_cap  = d.get("mkt_cap", 0)
            threshold = 50 if has_opts else 60
            if score < threshold:
                continue
            quality  = _quality_prefix(score)
            stop_p   = round(vwap * 0.995, 2)
            cap_lbl  = _cap_label(mkt_cap)
            cap_tag  = f" | {cap_lbl} {'w/opts' if has_opts else 'no opts'}" if cap_lbl else ""
            msg = (
                f"{quality} MIDDAY BREAKOUT: {ticker} +{chg:.1f}% | {rv:.1f}x vol | ${price:.2f}{cap_tag}\n"
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
    if not _spy_is_green():
        print("[sms_alerts] SPY red day — skipping gap recovery scan")
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
                # Market cap + options flag for routing
                mkt_cap  = 0.0
                has_opts = False
                try:
                    mkt_cap = float(tk.info.get("marketCap") or 0)
                except Exception:
                    pass
                try:
                    has_opts = len(tk.options) > 0
                except Exception:
                    pass
                if has_opts:
                    score = _with_options_score(rel_vol, chg_pct_prev, True, gap_pct)
                else:
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
                    "score": score, "mkt_cap": mkt_cap, "has_options": has_opts,
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
            has_opts  = d.get("has_options", False)
            mkt_cap   = d.get("mkt_cap", 0)
            threshold = 50 if has_opts else 60
            if score < threshold:
                continue
            quality  = _quality_prefix(score)
            stop_p   = round(vwap * 0.995, 2)
            cap_lbl  = _cap_label(mkt_cap)
            cap_tag  = f" | {cap_lbl} {'w/opts' if has_opts else 'no opts'}" if cap_lbl else ""
            msg = (
                f"{quality} GAP RECOVERY: {ticker} reclaimed VWAP | ${price:.2f}{cap_tag}\n"
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


# ── Steady Grinder scanner ─────────────────────────────────────────────────────

def run_steady_grinder_scan():
    """
    Runs every 30 min, 10:30 AM – 1:30 PM ET.
    Targets large/mid-cap stocks (avg vol ≥ 1M) that are grinding steadily
    higher on light-but-sustained volume — institutional accumulation plays.
    Classic pattern: FRO (+9.8%), AMKR (+8.7%) type.

    Criteria (all must pass):
      • Avg daily vol ≥ 1M (institutional stocks only — screens out micro/small-cap pump)
      • Up 2-8% from prev close (not too small, not already extended)
      • RVOL 1.0–3.0x  (if >3x, morning burst scanner already has it)
      • Above VWAP  (institutional benchmark — buyers still in control)
      • Price NOW > price 45 min ago  (actively trending, not stalling)
      • Price 45 min ago > price 90 min ago  (grind started before we checked — not a spike)
      • Within 2% of intraday high  (still at highs, not pulling back)
      • Has options  (quality filter: all large/mid caps qualify)

    One text per ticker per day via sms_midday_log alert_type='grinder'.
    """
    now_et = datetime.now(_ET)
    if now_et.weekday() >= 5:
        return
    start = now_et.replace(hour=10, minute=30, second=0, microsecond=0)
    end   = now_et.replace(hour=13, minute=30, second=0, microsecond=0)
    if now_et < start or now_et > end:
        return
    # NOTE: No SPY green-day gate here intentionally.
    # Institutional accumulation is stock-specific — it happens on red SPY days too.
    # The grinder's own filters (above VWAP, trending, RVOL 1-3x, EMA 9>21) are
    # sufficient to screen out market-panic sells without needing a market-wide gate.

    try:
        import yfinance as _yf
        from concurrent.futures import ThreadPoolExecutor, as_completed

        market_open  = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
        mins_elapsed = max((now_et - market_open).total_seconds() / 60.0, 1.0)
        day_frac     = min(mins_elapsed / 390.0, 1.0)

        bc_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept":     "application/json",
            "Referer":    "https://www.barchart.com/stocks/advances",
        }
        # Only mid + large cap feeds — grinders are institutional, not micro/small
        bc_syms = set()
        for bc_list in ("stocks.advances.midcap.us", "stocks.advances.largecap.us"):
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
                        if sym and len(sym) <= 5 and "." not in sym and 2.0 <= pct <= 8.0:
                            bc_syms.add(sym)
            except Exception:
                pass

        def _check_grinder(ticker):
            try:
                tk   = _yf.Ticker(ticker)
                fi   = tk.fast_info
                prev = float(getattr(fi, "previous_close", 0) or 0)
                avg  = float(getattr(fi, "three_month_average_volume", 1) or 1)
                if prev <= 0 or avg <= 0:
                    return None
                # Institutional stocks only — avg daily vol ≥ 1M filters micro/small-cap pumps
                if avg < 1_000_000:
                    return None
                hist = tk.history(period="1d", interval="1m")
                if hist.empty or len(hist) < 60:
                    return None
                hist.index = hist.index.tz_convert(_ET)
                cum_vol  = float(hist["Volume"].sum())
                price    = float(hist["Close"].iloc[-1])
                open_p   = float(hist["Open"].iloc[0])
                if price <= 0 or open_p <= 0:
                    return None
                # Price ≥ $10 — filters thin/manipulated names that sneak into mid-cap feeds
                if price < 10.0:
                    return None
                chg_pct  = (price - prev) / prev * 100
                if not (2.0 <= chg_pct <= 8.0):
                    return None
                proj_vol = cum_vol / day_frac
                rel_vol  = proj_vol / avg
                # RVOL 1.3–3.0x: meaningful institutional volume, not explosive
                # Below 1.3x = barely above average, not enough conviction
                # Above 3.0x = morning burst scanner already covers it
                if rel_vol < 1.3 or rel_vol >= 3.0:
                    return None
                # No single-bar volume spike >40% of total day's volume
                # A genuine grind has evenly distributed volume — one dominant bar = news pop, not a grind
                if cum_vol > 0 and float(hist["Volume"].max()) / cum_vol > 0.40:
                    return None
                # VWAP — must be above it (buyers in control)
                hist["_tp"] = (hist["High"] + hist["Low"] + hist["Close"]) / 3
                tp_vol_sum  = float((hist["_tp"] * hist["Volume"]).sum())
                vwap        = tp_vol_sum / cum_vol if cum_vol > 0 else price
                if price < vwap:
                    return None
                # Not >3% above VWAP — prevents chasing stocks that already ripped
                vwap_ext = (price - vwap) / vwap * 100 if vwap > 0 else 0.0
                if vwap_ext > 3.0:
                    return None
                # Still at highs — within 2% of intraday high (not fading)
                hod = float(hist["High"].max())
                pct_from_hod = (hod - price) / hod * 100 if hod > 0 else 0
                if pct_from_hod > 2.0:
                    return None
                # Trending up over the past 90 min — the core grinder confirmation
                # price_now > price_45m_ago: still climbing right now
                # price_45m_ago > price_90m_ago: was already climbing when we first checked
                bars_45m = hist.iloc[-45] if len(hist) >= 45 else None
                bars_90m = hist.iloc[-90] if len(hist) >= 90 else None
                if bars_45m is None:
                    return None
                price_45m = float(bars_45m["Close"])
                if price <= price_45m:
                    return None  # flat or fading over last 45 min — not a grinder
                if bars_90m is not None:
                    price_90m = float(bars_90m["Close"])
                    if price_45m <= price_90m:
                        return None  # was stalling 45-90 min ago — spike, not a grind
                trend_gain_45m = (price - price_45m) / price_45m * 100
                # t45 floor: ≥ 0.5% — must be visibly climbing, not just sitting near highs
                # t45 ceiling: ≤ 2.0% — above 2% in 45 min is a spike, not a grind
                # Backtest data: stocks with t45 > 2% (e.g. +5.5%) faded hard same day
                if trend_gain_45m < 0.5 or trend_gain_45m > 2.0:
                    return None
                # EMA 9 > EMA 21 on 30-min bars — confirms structured uptrend, not a choppy drift
                # Resample 1-min closes → 30-min, compute exponential moving averages
                bars_30m = hist["Close"].resample("30min").last().dropna()
                ema_ok = True  # graceful pass if not enough bars
                if len(bars_30m) >= 21:
                    ema9  = float(bars_30m.ewm(span=9,  adjust=False).mean().iloc[-1])
                    ema21 = float(bars_30m.ewm(span=21, adjust=False).mean().iloc[-1])
                    ema_ok = ema9 > ema21
                elif len(bars_30m) >= 9:
                    ema9  = float(bars_30m.ewm(span=9, adjust=False).mean().iloc[-1])
                    ema_ok = ema9 > float(bars_30m.iloc[0])  # rising from open at minimum
                if not ema_ok:
                    return None
                gap_pct = (open_p - prev) / prev * 100 if prev > 0 else 0.0
                # Has options — quality filter: large/mid caps qualify; micro-caps often don't
                has_opts = False
                mkt_cap  = 0.0
                try:
                    has_opts = len(tk.options) > 0
                except Exception:
                    pass
                try:
                    mkt_cap = float(tk.info.get("marketCap") or 0)
                except Exception:
                    pass
                if not has_opts:
                    return None
                # Bonus: check if ticker had unusual call sweeps earlier today
                # Options flow from earlier in the day = institutional intent confirmed
                has_call_sweep = False
                try:
                    with _conn() as con:
                        with con.cursor() as cur:
                            cur.execute("""
                                SELECT 1 FROM unusual_calls_log
                                WHERE ticker=%s
                                AND DATE(first_seen AT TIME ZONE 'UTC')=CURRENT_DATE
                                LIMIT 1
                            """, (ticker,))
                            has_call_sweep = cur.fetchone() is not None
                except Exception:
                    pass
                score = _with_options_score(rel_vol, chg_pct, True, gap_pct)
                # Trending bonus — each confirmed 45-min leg adds conviction
                if trend_gain_45m >= 2.0:
                    score = min(score + 8, 100)
                elif trend_gain_45m >= 1.0:
                    score = min(score + 4, 100)
                # Call sweep bonus — options flow from earlier confirms institutional intent
                if has_call_sweep:
                    score = min(score + 10, 100)
                return {
                    "ticker": ticker, "price": price, "chg_pct": chg_pct,
                    "rel_vol": rel_vol, "vwap": vwap, "vwap_ext": round(vwap_ext, 1),
                    "gap_pct": gap_pct, "trend_gain_45m": round(trend_gain_45m, 2),
                    "hod": hod, "score": score, "mkt_cap": mkt_cap,
                    "has_call_sweep": has_call_sweep,
                }
            except Exception:
                return None

        candidates = [s for s in bc_syms if not _already_midday_alerted(s, "grinder")]
        results = []
        with ThreadPoolExecutor(max_workers=12) as pool:
            futs = {pool.submit(_check_grinder, t): t for t in candidates}
            for fut in as_completed(futs):
                res = fut.result()
                if res:
                    results.append(res)

        results.sort(key=lambda x: -x["score"])
        sent = 0
        for d in results:
            ticker        = d["ticker"]
            chg           = d["chg_pct"]
            rv            = d["rel_vol"]
            price         = d["price"]
            vwap          = d["vwap"]
            hod           = d["hod"]
            t45           = d["trend_gain_45m"]
            score         = d["score"]
            mkt_cap       = d.get("mkt_cap", 0)
            vwap_ext      = d.get("vwap_ext", 0)
            has_call_sweep = d.get("has_call_sweep", False)
            if score < 45:
                continue
            stop_p  = round(vwap * 0.995, 2)
            cap_lbl = _cap_label(mkt_cap)
            cap_tag = f" | {cap_lbl}" if cap_lbl else ""
            sweep_tag = "  🔥 call sweeps earlier" if has_call_sweep else ""
            msg = (
                f"📶 STEADY GRINDER: {ticker} +{chg:.1f}% | {rv:.1f}x vol | ${price:.2f}{cap_tag}\n"
                f"Climbing — +{t45:.1f}% last 45 min  HOD ${hod:.2f}\n"
                f"VWAP ${vwap:.2f} +{vwap_ext:.1f}% above ✅  stop ${stop_p:.2f}{sweep_tag}\n"
                f"Score {score}/100 | {now_et.strftime('%I:%M %p ET')}\n"
                f"nclexai.org/stock-scanner/"
            )
            if send_sms(msg):
                _log_midday_alert(ticker, price, chg, rv, score, "grinder")
                sent += 1

        print(f"[sms_alerts] steady grinder scan — {len(candidates)} checked, {sent} texts sent")
    except Exception as e:
        print(f"[sms_alerts] steady grinder scan error: {e}")
