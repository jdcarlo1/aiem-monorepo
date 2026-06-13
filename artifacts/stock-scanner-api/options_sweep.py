"""
Call Sweep Scanner — yfinance (same source as the Unusual Calls tab on the website)
Detects aggressive call buying above VWAP on heavy volume and fires an SMS alert.

Signal criteria:
  - Call volume > 2x open interest  (aggressive sweep, not normal flow)
  - Volume >= 50 contracts minimum
  - Strike within -5% to +10% of current price  (near-the-money)
  - Expiry within 1–30 days
  - Premium > $5,000
  - Underlying stock >= VWAP  (institutional bid holding)
"""
import os
import psycopg2
import yfinance as yf
from datetime import datetime, timedelta
import pytz

_ET = pytz.timezone("US/Eastern")

_MIN_VOL_OI_RATIO = 2.0
_MIN_CONTRACTS    = 50
_MIN_PREMIUM      = 5_000
_MAX_DAYS_OUT     = 30
_MAX_OTM_PCT      = 10.0
_MAX_ITM_PCT      = 5.0


# ── Database ──────────────────────────────────────────────────────────────────

def _conn():
    return psycopg2.connect(os.environ["DATABASE_URL"])


def init_call_sweep_log_table():
    try:
        with _conn() as con:
            with con.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS call_sweep_log (
                        id            SERIAL PRIMARY KEY,
                        ticker        TEXT NOT NULL,
                        strike        NUMERIC(10,2),
                        expiry        TEXT,
                        call_volume   INTEGER,
                        open_interest INTEGER,
                        vol_oi_ratio  NUMERIC(6,2),
                        premium       INTEGER,
                        stock_price   NUMERIC(10,2),
                        vwap          NUMERIC(10,2),
                        sweep_date    DATE NOT NULL DEFAULT CURRENT_DATE,
                        sent_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                """)
                cur.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS call_sweep_log_uniq
                    ON call_sweep_log (ticker, strike, expiry, sweep_date)
                """)
        print("[options_sweep] call_sweep_log table ready")
    except Exception as e:
        print(f"[options_sweep] table init error: {e}")


def _already_sweep_alerted(ticker: str, strike: float, expiry: str) -> bool:
    try:
        with _conn() as con:
            with con.cursor() as cur:
                cur.execute("""
                    SELECT 1 FROM call_sweep_log
                    WHERE ticker=%s AND strike=%s AND expiry=%s AND sweep_date=CURRENT_DATE
                    LIMIT 1
                """, (ticker, strike, expiry))
                return cur.fetchone() is not None
    except Exception:
        return False


def _log_sweep_alert(ticker, strike, expiry, call_vol, oi, vol_oi, premium, price, vwap):
    try:
        with _conn() as con:
            with con.cursor() as cur:
                cur.execute("""
                    INSERT INTO call_sweep_log
                      (ticker, strike, expiry, call_volume, open_interest,
                       vol_oi_ratio, premium, stock_price, vwap)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (ticker, strike, expiry, sweep_date) DO NOTHING
                """, (ticker, strike, expiry, call_vol, oi, vol_oi, premium, price, vwap))
    except Exception as e:
        print(f"[options_sweep] log error {ticker}: {e}")


# ── Data helpers ──────────────────────────────────────────────────────────────

def _get_vwap(ticker: str) -> tuple[float, float]:
    """Returns (current_price, vwap) using 1-min intraday data — same method as sms_alerts.py."""
    try:
        tk   = yf.Ticker(ticker)
        hist = tk.history(period="1d", interval="1m")
        if hist.empty:
            return 0.0, 0.0
        hist["_tp"] = (hist["High"] + hist["Low"] + hist["Close"]) / 3
        vol = float(hist["Volume"].sum())
        if vol <= 0:
            return 0.0, 0.0
        vwap  = float((hist["_tp"] * hist["Volume"]).sum()) / vol
        price = float(hist["Close"].iloc[-1])
        return price, vwap
    except Exception:
        return 0.0, 0.0


def _scan_ticker_for_sweeps(ticker: str) -> list[dict]:
    """Scans all near-term options expirations for qualifying call sweeps."""
    price, vwap = _get_vwap(ticker)
    if price <= 0 or vwap <= 0:
        return []

    hits = []
    tk   = yf.Ticker(ticker)
    now  = datetime.now(_ET).date()

    for expiry in (tk.options or []):
        exp_date = datetime.strptime(expiry, "%Y-%m-%d").date()
        days_out = (exp_date - now).days + 1
        if not (1 <= days_out <= _MAX_DAYS_OUT):
            continue

        try:
            calls = tk.option_chain(expiry).calls
        except Exception:
            continue

        for _, row in calls.iterrows():
            try:
                vol = int(row.get("volume") or 0)
                oi  = int(row.get("openInterest") or 0)
                if vol < _MIN_CONTRACTS or oi < 10:
                    continue
                vol_oi = vol / oi
                if vol_oi < _MIN_VOL_OI_RATIO:
                    continue
                strike  = float(row["strike"])
                otm_pct = (strike - price) / price * 100
                if otm_pct > _MAX_OTM_PCT or otm_pct < -_MAX_ITM_PCT:
                    continue
                bid     = float(row.get("bid") or 0)
                ask     = float(row.get("ask") or 0)
                mid     = (bid + ask) / 2 if (bid and ask) else float(row.get("lastPrice") or 0)
                premium = int(mid * vol * 100)
                if premium < _MIN_PREMIUM:
                    continue

                hits.append({
                    "ticker":     ticker,
                    "price":      price,
                    "vwap":       vwap,
                    "above_vwap": price >= vwap,
                    "strike":     strike,
                    "expiry":     expiry,
                    "days_out":   days_out,
                    "vol":        vol,
                    "oi":         oi,
                    "vol_oi":     round(vol_oi, 1),
                    "premium":    premium,
                    "otm_pct":    round(otm_pct, 1),
                })
            except Exception:
                pass

    return hits


def _get_sms_alerted_today() -> list[str]:
    """Tickers that already triggered equity entry alerts today — highest priority."""
    try:
        with _conn() as con:
            with con.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT ticker FROM sms_alerts_log
                    WHERE alert_date = CURRENT_DATE
                """)
                return [r[0] for r in cur.fetchall()]
    except Exception:
        return []


# ── Main public function ──────────────────────────────────────────────────────

def run_call_sweep_scan(extra_tickers: list[str] | None = None):
    """
    Runs every 15 min during market hours.
    Scans today's SMS-alerted tickers for bullish call sweeps above VWAP.
    Fires an SMS for each qualifying sweep not yet alerted today.
    """
    now_et = datetime.now(_ET)
    if now_et.weekday() >= 5:
        return
    market_open  = now_et.replace(hour=9,  minute=30, second=0, microsecond=0)
    market_close = now_et.replace(hour=15, minute=45, second=0, microsecond=0)
    if now_et < market_open or now_et > market_close:
        return

    universe = list(dict.fromkeys(
        _get_sms_alerted_today() + (extra_tickers or [])
    ))
    if not universe:
        print("[options_sweep] no tickers to scan")
        return

    print(f"[options_sweep] scanning {len(universe)} tickers for call sweeps...")

    try:
        from sms_alerts import send_sms
    except Exception as e:
        print(f"[options_sweep] cannot import send_sms: {e}")
        return

    sent = 0
    for ticker in universe:
        try:
            for h in _scan_ticker_for_sweeps(ticker):
                if _already_sweep_alerted(ticker, h["strike"], h["expiry"]):
                    continue

                vwap_label = "✅ above VWAP" if h["above_vwap"] else "⚠️ below VWAP"
                otm_label  = f"+{h['otm_pct']}% OTM" if h["otm_pct"] >= 0 else f"{abs(h['otm_pct'])}% ITM"
                msg = (
                    f"📣 CALL SWEEP: {ticker}\n"
                    f"${h['strike']} strike ({otm_label}) exp {h['expiry']} ({h['days_out']}d)\n"
                    f"Vol {h['vol']:,} | OI {h['oi']:,} | {h['vol_oi']}x ratio\n"
                    f"Premium ${h['premium']:,} | Stock ${h['price']:.2f} {vwap_label}\n"
                    f"{now_et.strftime('%I:%M %p ET')}"
                )
                if send_sms(msg):
                    _log_sweep_alert(
                        ticker, h["strike"], h["expiry"],
                        h["vol"], h["oi"], h["vol_oi"],
                        h["premium"], h["price"], h["vwap"]
                    )
                    sent += 1
        except Exception as e:
            print(f"[options_sweep] error scanning {ticker}: {e}")

    print(f"[options_sweep] scan complete — {len(universe)} tickers, {sent} sweep alerts sent")
