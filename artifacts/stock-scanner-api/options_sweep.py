"""
Call Sweep Scanner — Tradier API
Detects aggressive call buying (sweeps) above VWAP on heavy volume.
These are the strongest bullish signals: institutional money buying calls urgently.

Env vars needed:
  TRADIER_TOKEN  — from tradier.com → Settings → API Access

Signal criteria:
  - Call volume > 2x open interest  (aggressive sweep, not normal flow)
  - Volume >= 50 contracts minimum  (filters noise)
  - Strike within -5% to +10% of current price  (near-the-money)
  - Expiry within 1–30 days  (near-term conviction)
  - Premium > $5,000  (real money behind it)
  - Underlying stock >= VWAP  (institutional bid holding)
"""
import os
import psycopg2
import requests
from datetime import datetime, timedelta
import pytz
import yfinance as yf

_ET = pytz.timezone("US/Eastern")

_TRADIER_TOKEN = lambda: os.getenv("TRADIER_TOKEN", "")
_TRADIER_BASE  = "https://api.tradier.com/v1"

_MIN_VOL_OI_RATIO = 2.0    # volume must be 2x open interest to qualify as a sweep
_MIN_CONTRACTS    = 50     # minimum contracts to filter noise
_MIN_PREMIUM      = 5_000  # minimum dollar premium ($5K)
_MAX_DAYS_OUT     = 30     # only look at options expiring within 30 days
_MAX_OTM_PCT      = 10.0   # strike no more than 10% above current price
_MAX_ITM_PCT      = 5.0    # strike no more than 5% below current price (don't chase deep ITM)

# ── Database ──────────────────────────────────────────────────────────────────

def _conn():
    return psycopg2.connect(os.environ["DATABASE_URL"])


def init_call_sweep_log_table():
    try:
        with _conn() as con:
            with con.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS call_sweep_log (
                        id           SERIAL PRIMARY KEY,
                        ticker       TEXT NOT NULL,
                        strike       NUMERIC(10,2),
                        expiry       TEXT,
                        call_volume  INTEGER,
                        open_interest INTEGER,
                        vol_oi_ratio NUMERIC(6,2),
                        premium      INTEGER,
                        stock_price  NUMERIC(10,2),
                        vwap         NUMERIC(10,2),
                        sweep_date   DATE NOT NULL DEFAULT CURRENT_DATE,
                        sent_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                """)
                cur.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS call_sweep_log_ticker_strike_expiry_date
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
                      (ticker, strike, expiry, call_volume, open_interest, vol_oi_ratio,
                       premium, stock_price, vwap)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (ticker, strike, expiry, sweep_date) DO NOTHING
                """, (ticker, strike, expiry, call_vol, oi, vol_oi, premium, price, vwap))
    except Exception as e:
        print(f"[options_sweep] log error {ticker}: {e}")


# ── Tradier API ───────────────────────────────────────────────────────────────

def _tradier_get(path: str, params: dict) -> dict:
    token = _TRADIER_TOKEN()
    if not token:
        return {}
    try:
        r = requests.get(
            f"{_TRADIER_BASE}{path}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            params=params,
            timeout=8,
        )
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"[options_sweep] Tradier request error {path}: {e}")
    return {}


def _get_expirations(ticker: str) -> list[str]:
    data = _tradier_get("/markets/options/expirations", {"symbol": ticker, "includeAllRoots": "true"})
    exps = (data.get("expirations") or {}).get("date") or []
    if isinstance(exps, str):
        exps = [exps]
    now = datetime.now(_ET).date()
    cutoff = now + timedelta(days=_MAX_DAYS_OUT)
    return [e for e in exps if now <= datetime.strptime(e, "%Y-%m-%d").date() <= cutoff]


def _get_calls_chain(ticker: str, expiry: str) -> list[dict]:
    data = _tradier_get("/markets/options/chains", {
        "symbol": ticker,
        "expiration": expiry,
        "greeks": "false",
    })
    options = (data.get("options") or {}).get("option") or []
    if isinstance(options, dict):
        options = [options]
    return [o for o in options if o.get("option_type") == "call"]


def _get_vwap(ticker: str) -> tuple[float, float]:
    """Returns (current_price, vwap) using 1-min intraday data."""
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


# ── Sweep detection ───────────────────────────────────────────────────────────

def _scan_ticker_for_sweeps(ticker: str) -> list[dict]:
    """Returns a list of qualifying call sweep hits for one ticker."""
    price, vwap = _get_vwap(ticker)
    if price <= 0 or vwap <= 0:
        return []

    hits = []
    for expiry in _get_expirations(ticker):
        calls = _get_calls_chain(ticker, expiry)
        days_out = (datetime.strptime(expiry, "%Y-%m-%d").date() - datetime.now(_ET).date()).days + 1

        for opt in calls:
            try:
                vol = int(opt.get("volume") or 0)
                oi  = int(opt.get("open_interest") or 0)
                if vol < _MIN_CONTRACTS or oi < 10:
                    continue
                vol_oi = vol / oi
                if vol_oi < _MIN_VOL_OI_RATIO:
                    continue
                strike = float(opt.get("strike") or 0)
                if not strike:
                    continue
                otm_pct = (strike - price) / price * 100
                if otm_pct > _MAX_OTM_PCT or otm_pct < -_MAX_ITM_PCT:
                    continue
                bid     = float(opt.get("bid") or 0)
                ask     = float(opt.get("ask") or 0)
                mid     = (bid + ask) / 2 if (bid and ask) else float(opt.get("last") or 0)
                premium = int(mid * vol * 100)
                if premium < _MIN_PREMIUM:
                    continue

                hits.append({
                    "ticker":   ticker,
                    "price":    price,
                    "vwap":     vwap,
                    "above_vwap": price >= vwap,
                    "strike":   strike,
                    "expiry":   expiry,
                    "days_out": days_out,
                    "vol":      vol,
                    "oi":       oi,
                    "vol_oi":   round(vol_oi, 1),
                    "premium":  premium,
                    "otm_pct":  round(otm_pct, 1),
                })
            except Exception:
                pass
    return hits


# ── Universe to scan ──────────────────────────────────────────────────────────

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


# ── SMS helpers ───────────────────────────────────────────────────────────────

def _send_sweep_sms(msg: str):
    """Reuse the send_sms function from sms_alerts."""
    try:
        from sms_alerts import send_sms
        return send_sms(msg)
    except Exception as e:
        print(f"[options_sweep] sms send error: {e}")
        return False


# ── Main public function ──────────────────────────────────────────────────────

def run_call_sweep_scan(extra_tickers: list[str] | None = None):
    """
    Runs every 15 min during market hours.
    Scans today's alerted tickers + any extras for bullish call sweeps above VWAP.
    Fires an SMS for each qualifying sweep not yet alerted today.
    """
    token = _TRADIER_TOKEN()
    if not token:
        print("[options_sweep] TRADIER_TOKEN not set — skipping call sweep scan")
        return

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
    sent = 0

    for ticker in universe:
        try:
            hits = _scan_ticker_for_sweeps(ticker)
            for h in hits:
                if _already_sweep_alerted(ticker, h["strike"], h["expiry"]):
                    continue

                vwap_status = "✅ above VWAP" if h["above_vwap"] else "⚠️ below VWAP"
                otm_label   = f"+{h['otm_pct']}% OTM" if h["otm_pct"] >= 0 else f"{abs(h['otm_pct'])}% ITM"
                prem_str    = f"${h['premium']:,}"
                msg = (
                    f"📣 CALL SWEEP: {ticker}\n"
                    f"${h['strike']} strike ({otm_label}) exp {h['expiry']} ({h['days_out']}d)\n"
                    f"Vol {h['vol']:,} | OI {h['oi']:,} | {h['vol_oi']}x ratio\n"
                    f"Premium {prem_str} | Stock ${h['price']:.2f} {vwap_status}\n"
                    f"{now_et.strftime('%I:%M %p ET')}"
                )
                if _send_sweep_sms(msg):
                    _log_sweep_alert(
                        ticker, h["strike"], h["expiry"],
                        h["vol"], h["oi"], h["vol_oi"],
                        h["premium"], h["price"], h["vwap"]
                    )
                    sent += 1
        except Exception as e:
            print(f"[options_sweep] error scanning {ticker}: {e}")

    print(f"[options_sweep] scan complete — {len(universe)} tickers, {sent} sweep alerts sent")
