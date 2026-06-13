"""
Call Sweep Scanner — Multi-Signal Confluence Engine
Detects aggressive call buying above VWAP and scores it across 6 confluence signals.
Fires an SMS with a conviction rating (1–5 stars) so you know how strong each alert is.

Signals stacked:
  1. Sweep ratio        — vol > 2x OI (base requirement)
  2. Short-dated OTM    — weekly calls (≤7 days) are almost always speculative, not hedges
  3. Repeat sweep       — same ticker swept yesterday or 2 days ago = institutional accumulation
  4. P/C divergence     — more put OI than call OI = elevated fear = contrarian fuel when it unwinds
  5. Low IV rank        — cheap options mean the buyer expects a real move, not just a hedge
  6. Gamma squeeze      — heavy call OI at a round-number strike near price forces dealers to buy shares

Conviction labels:
  1 signal  → SWEEP
  2 signals → STRONG SWEEP ⭐
  3 signals → HIGH CONVICTION ⭐⭐
  4+ signals → INSTITUTIONAL SETUP ⭐⭐⭐
"""
import os
import psycopg2
import yfinance as yf
from datetime import datetime, timedelta
import pytz

_ET = pytz.timezone("US/Eastern")

# ── Sweep base criteria ───────────────────────────────────────────────────────
_MIN_VOL_OI_RATIO = 2.0    # vol must be 2x OI to qualify as a sweep
_MIN_CONTRACTS    = 50     # minimum contracts — filters noise
_MIN_PREMIUM      = 5_000  # minimum dollar premium
_MAX_DAYS_OUT     = 30     # only near-term expirations
_MAX_OTM_PCT      = 10.0   # no more than 10% out of the money
_MAX_ITM_PCT      = 5.0    # no more than 5% in the money

# ── Confluence thresholds ─────────────────────────────────────────────────────
_SHORT_DATED_DAYS = 7      # ≤7 days = weekly = almost always speculative
_LOW_IV_THRESHOLD = 50.0   # IV% below this = cheap options = real conviction buy
_GAMMA_ROUND_PCT  = 0.5    # strike within 0.5% of a round $5 number = gamma zone
_GAMMA_MIN_OI     = 500    # minimum OI at that strike to matter


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
                        conviction    INTEGER DEFAULT 1,
                        signals_fired TEXT,
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


def _log_sweep_alert(ticker, strike, expiry, call_vol, oi, vol_oi,
                     premium, price, vwap, conviction, signals_fired):
    try:
        with _conn() as con:
            with con.cursor() as cur:
                cur.execute("""
                    INSERT INTO call_sweep_log
                      (ticker, strike, expiry, call_volume, open_interest,
                       vol_oi_ratio, premium, stock_price, vwap, conviction, signals_fired)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (ticker, strike, expiry, sweep_date) DO NOTHING
                """, (ticker, strike, expiry, call_vol, oi, vol_oi,
                      premium, price, vwap, conviction, signals_fired))
    except Exception as e:
        print(f"[options_sweep] log error {ticker}: {e}")


# ── Signal 3: Repeat sweep check ──────────────────────────────────────────────

def _get_repeat_sweep_days(ticker: str) -> int:
    """Returns how many of the last 3 trading days the ticker was swept (0-3)."""
    try:
        with _conn() as con:
            with con.cursor() as cur:
                cur.execute("""
                    SELECT COUNT(DISTINCT sweep_date) FROM call_sweep_log
                    WHERE ticker=%s
                      AND sweep_date >= CURRENT_DATE - INTERVAL '4 days'
                      AND sweep_date < CURRENT_DATE
                """, (ticker,))
                row = cur.fetchone()
                return int(row[0]) if row else 0
    except Exception:
        return 0


# ── Signal 4: P/C ratio divergence ───────────────────────────────────────────

def _get_pc_ratio(ticker: str, tk: yf.Ticker) -> float | None:
    """
    Returns the put/call volume ratio for the nearest expiry.
    > 1.0 = more put volume than call volume = elevated fear = bullish contrarian fuel
    """
    try:
        exps = tk.options
        if not exps:
            return None
        chain = tk.option_chain(exps[0])
        call_vol = int(chain.calls["volume"].fillna(0).sum())
        put_vol  = int(chain.puts["volume"].fillna(0).sum())
        if call_vol <= 0:
            return None
        return round(put_vol / call_vol, 2)
    except Exception:
        return None


# ── Signal 6: Gamma squeeze setup ─────────────────────────────────────────────

def _check_gamma_squeeze(strike: float, oi: int, price: float) -> bool:
    """
    True if:
    - Strike is near a round $5 number (e.g. $100, $105, $110)
    - OI is large (dealers are short gamma here and must buy stock if price approaches)
    """
    if oi < _GAMMA_MIN_OI:
        return False
    nearest_round = round(strike / 5) * 5
    proximity_pct = abs(strike - nearest_round) / price * 100
    return proximity_pct <= _GAMMA_ROUND_PCT


# ── Core VWAP calc ────────────────────────────────────────────────────────────

def _get_vwap(ticker: str) -> tuple[float, float]:
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


# ── Full sweep scan for one ticker ────────────────────────────────────────────

def _scan_ticker_for_sweeps(ticker: str) -> list[dict]:
    price, vwap = _get_vwap(ticker)
    if price <= 0 or vwap <= 0:
        return []

    tk   = yf.Ticker(ticker)
    now  = datetime.now(_ET).date()

    # Pre-compute confluence signals that apply to the whole ticker (not per-strike)
    repeat_days = _get_repeat_sweep_days(ticker)
    pc_ratio    = _get_pc_ratio(ticker, tk)

    hits = []
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

                iv = float(row.get("impliedVolatility") or 0) * 100

                # ── Score confluence signals ──────────────────────────────────
                signals = []

                # Signal 1 always true (base sweep requirement already met)
                signals.append("sweep")

                # Signal 2: short-dated OTM weekly
                if days_out <= _SHORT_DATED_DAYS and otm_pct > 0:
                    signals.append("short_dated_otm")

                # Signal 3: repeat sweep
                if repeat_days >= 1:
                    signals.append(f"repeat_{repeat_days}d")

                # Signal 4: P/C divergence (elevated put buying = contrarian fuel)
                if pc_ratio is not None and pc_ratio > 1.0:
                    signals.append(f"pc_div_{pc_ratio}")

                # Signal 5: low IV rank (cheap options = buyer expects real move)
                if 0 < iv < _LOW_IV_THRESHOLD:
                    signals.append(f"low_iv_{iv:.0f}")

                # Signal 6: gamma squeeze setup
                if _check_gamma_squeeze(strike, oi, price):
                    signals.append("gamma_squeeze")

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
                    "iv":         round(iv, 1),
                    "pc_ratio":   pc_ratio,
                    "repeat_days": repeat_days,
                    "signals":    signals,
                    "conviction": len(signals),
                })
            except Exception:
                pass

    return hits


# ── SMS message builder ───────────────────────────────────────────────────────

def _build_sweep_msg(h: dict, now_et: datetime) -> str:
    conviction = h["conviction"]

    # Label and stars
    if conviction >= 4:
        label = "INSTITUTIONAL SETUP ⭐⭐⭐"
    elif conviction == 3:
        label = "HIGH CONVICTION ⭐⭐"
    elif conviction == 2:
        label = "STRONG SWEEP ⭐"
    else:
        label = "SWEEP"

    vwap_label = "✅ above VWAP" if h["above_vwap"] else "⚠️ below VWAP"
    otm_label  = f"+{h['otm_pct']}% OTM" if h["otm_pct"] >= 0 else f"{abs(h['otm_pct'])}% ITM"

    # Build signal detail lines
    detail_lines = []

    if "short_dated_otm" in " ".join(h["signals"]):
        detail_lines.append("📅 Weekly OTM — almost never a hedge")

    for s in h["signals"]:
        if s.startswith("repeat_"):
            days = s.split("_")[1].replace("d","")
            detail_lines.append(f"🔁 Repeat sweep: {days} day(s) in a row — accumulation")

    for s in h["signals"]:
        if s.startswith("pc_div_"):
            ratio = s.split("_")[2]
            detail_lines.append(f"📊 P/C ratio {ratio} — elevated puts = contrarian fuel")

    for s in h["signals"]:
        if s.startswith("low_iv_"):
            iv_val = s.split("_")[2]
            detail_lines.append(f"📉 Low IV {iv_val}% — options cheap, buyer expects big move")

    if "gamma_squeeze" in " ".join(h["signals"]):
        detail_lines.append(f"⚡ Gamma squeeze zone — dealers must buy shares above ${h['strike']:.0f}")

    lines = [
        f"📣 CALL SWEEP: {h['ticker']} — {label}",
        f"${h['strike']} strike ({otm_label}) exp {h['expiry']} ({h['days_out']}d)",
        f"Vol {h['vol']:,} | OI {h['oi']:,} | {h['vol_oi']}x ratio",
        f"Premium ${h['premium']:,} | Stock ${h['price']:.2f} {vwap_label}",
    ]
    lines.extend(detail_lines)
    lines.append(now_et.strftime("%I:%M %p ET"))
    return "\n".join(lines)


# ── Universe helpers ──────────────────────────────────────────────────────────

def _get_sms_alerted_today() -> list[str]:
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
    Scans today's SMS-alerted tickers for bullish call sweeps with confluence scoring.
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

    print(f"[options_sweep] scanning {len(universe)} tickers across 6 confluence signals...")

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

                msg = _build_sweep_msg(h, now_et)
                if send_sms(msg):
                    _log_sweep_alert(
                        ticker, h["strike"], h["expiry"],
                        h["vol"], h["oi"], h["vol_oi"],
                        h["premium"], h["price"], h["vwap"],
                        h["conviction"], ",".join(h["signals"])
                    )
                    sent += 1
        except Exception as e:
            print(f"[options_sweep] error scanning {ticker}: {e}")

    print(f"[options_sweep] scan complete — {len(universe)} tickers, {sent} sweep alerts sent")
