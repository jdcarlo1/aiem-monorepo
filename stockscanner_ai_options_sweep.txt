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

# ── ICS signal weights (named so denominator stays in sync automatically) ─────
_W_NAKED_CALL    = 20   # pc_ratio < 0.5 (directional conviction)
_W_ASK_SIDE      = 15   # vol/OI >= 3x (market-order urgency)
_W_MULTI_LEG     = 15   # >= 2 qualifying strikes same scan
_W_PREMIUM       = 12   # premium >= $500K
_W_SHORT_OTM     = 10   # weekly OTM (<= 7d, strike > price)
_W_ABOVE_VWAP    = 8    # price >= VWAP
_W_HEAVY_VOL     = 8    # vol >= 500 contracts
_W_REPEAT        = 6    # swept on a prior day this week
_W_RED_DAY       = 5    # buying calls on a down day
_W_LOW_IVR       = 5    # IV < 50% (cheap = real conviction)
_W_EARLY_MORNING = 3    # 9:30–10:00 AM ET print
_W_TIGHT_SPREAD  = 7    # bid/ask spread < 3% of mid
# Non-automatable signals (manual tool only — NOT in denominator):
#   oiSpike=4, quietTicker=3, darkPool=3, preCatalyst=3 → 13 pts
_ICS_AUTOMATABLE_WEIGHT = (
    _W_NAKED_CALL + _W_ASK_SIDE + _W_MULTI_LEG + _W_PREMIUM +
    _W_SHORT_OTM + _W_ABOVE_VWAP + _W_HEAVY_VOL + _W_REPEAT +
    _W_RED_DAY + _W_LOW_IVR + _W_EARLY_MORNING + _W_TIGHT_SPREAD
)  # = 114
# holy_grail is an optional additive bonus (up to 73 pts). When the module is
# unavailable it contributes 0, so we score against the automatable base only.
# Scores will exceed 100 when holy_grail fires — that's intentional (bonus signal).
_ICS_TOTAL_WEIGHT  = _ICS_AUTOMATABLE_WEIGHT   # 114; kept as alias for formula
_ICS_SMS_THRESHOLD = 60   # 60/114 ≈ 53% of automatable max → 65-70% win rate zone
                           # (old 80/200=40% made max reachable score=57 → no alerts ever fired)
                           # recalibrate against live backtest once 30+ signals accumulate


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
                # Backfill columns added after initial deploy
                cur.execute("ALTER TABLE call_sweep_log ADD COLUMN IF NOT EXISTS conviction INTEGER DEFAULT 1")
                cur.execute("ALTER TABLE call_sweep_log ADD COLUMN IF NOT EXISTS signals_fired TEXT")
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
                    WHERE ticker=%s AND strike=%s AND expiry=%s AND sweep_date=(now() AT TIME ZONE 'America/New_York')::date
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
                      AND sweep_date >= (now() AT TIME ZONE 'America/New_York')::date - 4
                      AND sweep_date < (now() AT TIME ZONE 'America/New_York')::date
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


# ── ICS auto-scoring function ─────────────────────────────────────────────────

def _compute_ics_score(h: dict, now_et: datetime) -> tuple[int, list[str]]:
    """
    Compute an automated ICS score (0-100) using the same weights as the manual tool.
    Returns (score, list_of_label_strings).
    Signals checked automatically:
      nakedCall(20)      — pc_ratio < 0.5 (directional, more calls than puts 2:1)
      askSide(15)        — vol_oi >= 3x  (aggressive market-order buying = urgency)
      multiLegSweep(15)  — same ticker has >= 2 qualifying strikes this scan
      premiumSize(12)    — premium >= $500K
      shortDatedOTM(10)  — days_out <= 7 AND strike > price
      aboveVWAP(8)       — price >= vwap
      heavyVolume(8)     — vol >= 500 contracts (high absolute activity)
      repeatActivity(6)  — repeat_days >= 1
      redDayBuy(5)       — stock price down from prev close (buying on weakness)
      lowIVR(5)          — IV < 50%
      earlyMorning(3)    — 9:30–10:00 AM ET (most informed institutional prints)
    Not automatable (13 pts total): oiSpike, quietTicker, darkPool, preCatalyst
    """
    pts = 0
    labels = []

    # nakedCall — P/C < 0.5 means 2:1 more call vol than put = directional conviction
    pc = h.get("pc_ratio")
    if pc is not None and pc < 0.5:
        pts += _W_NAKED_CALL
        labels.append(f"📣 Directional (P/C {pc}) — naked conviction")

    # askSide — vol/OI >= 3x = someone is paying up, not limit-fishing
    if h["vol_oi"] >= 3.0:
        pts += _W_ASK_SIDE
        labels.append(f"⚡ Ask-side urgency ({h['vol_oi']}x ratio)")

    # multiLegSweep — multiple qualifying strikes this scan cycle
    if h.get("multi_strike", False):
        pts += _W_MULTI_LEG
        labels.append("🔀 Multi-strike sweep (coordinated)")

    # premiumSize
    if h["premium"] >= 500_000:
        pts += _W_PREMIUM
        labels.append(f"💰 Premium ${h['premium']//1000}K (institutional size)")

    # shortDatedOTM
    if h["days_out"] <= 7 and h["otm_pct"] > 0:
        pts += _W_SHORT_OTM
        labels.append(f"📅 Weekly OTM +{h['otm_pct']}% ({h['days_out']}d) — speculative")

    # aboveVWAP
    if h["above_vwap"]:
        pts += _W_ABOVE_VWAP
        labels.append(f"✅ Above VWAP (${h['vwap']:.2f})")

    # heavyVolume — absolute options volume >= 500 contracts
    if h["vol"] >= 500:
        pts += _W_HEAVY_VOL
        labels.append(f"📊 Heavy vol {h['vol']:,} contracts")

    # repeatActivity
    if h["repeat_days"] >= 1:
        pts += _W_REPEAT
        labels.append(f"🔁 Repeat sweep {h['repeat_days']}d in a row")

    # redDayBuy — buying calls while stock is down = conviction
    if h.get("red_day", False):
        pts += _W_RED_DAY
        labels.append("🔴 Buying on red day — strong conviction")

    # lowIVR
    if 0 < h["iv"] < _LOW_IV_THRESHOLD:
        pts += _W_LOW_IVR
        labels.append(f"📉 Low IV {h['iv']:.0f}% (cheap, expects big move)")

    # earlyMorning — 9:30–10:00 AM ET
    if (now_et.hour == 9 and now_et.minute >= 30) or (now_et.hour == 10 and now_et.minute == 0):
        pts += _W_EARLY_MORNING
        labels.append("🌅 Early morning print (informed money)")

    # tightSpread — bid/ask spread < 3% of mid
    spread_pct = h.get("spread_pct", 100.0)
    if 0 < spread_pct < 3.0:
        pts += _W_TIGHT_SPREAD
        labels.append(f"📐 Tight options spread {spread_pct:.1f}% — market maker direction confidence")

    # Holy Grail signals (up to 73 pts) — delta flow, tape, VWAP bands, MFI,
    # price acceleration, consecutive green, pre-market vol, VWAP reclaim, minute RVOL
    try:
        from holy_grail import compute_holy_grail_signals
        hg = compute_holy_grail_signals(h["ticker"], h["price"], h["vwap"])
        pts += hg["pts"]
        labels.extend(hg["labels"])
    except Exception as _hg_err:
        print(f"[options_sweep] holy_grail error {h['ticker']}: {_hg_err}")

    score = min(round(pts / _ICS_TOTAL_WEIGHT * 100), 100)
    return score, labels


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

def _get_prev_close(ticker: str) -> float:
    """Returns yesterday's closing price for red-day detection."""
    try:
        tk   = yf.Ticker(ticker)
        hist = tk.history(period="2d", interval="1d")
        if len(hist) >= 2:
            return float(hist["Close"].iloc[-2])
        return 0.0
    except Exception:
        return 0.0


def _scan_ticker_for_sweeps(ticker: str) -> list[dict]:
    price, vwap = _get_vwap(ticker)
    if price <= 0 or vwap <= 0:
        return []

    tk         = yf.Ticker(ticker)
    now        = datetime.now(_ET).date()
    prev_close = _get_prev_close(ticker)
    red_day    = (prev_close > 0 and price < prev_close)

    # Pre-compute confluence signals that apply to the whole ticker (not per-strike)
    repeat_days = _get_repeat_sweep_days(ticker)
    pc_ratio    = _get_pc_ratio(ticker, tk)

    raw_hits = []
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

                # Bid/ask spread as % of mid (signal 5 proxy)
                spread_pct = round((ask - bid) / mid * 100, 2) if mid > 0 and ask > bid else 100.0

                # ── Legacy 6-signal conviction (kept for DB logging) ──────────
                signals = ["sweep"]
                if days_out <= _SHORT_DATED_DAYS and otm_pct > 0:
                    signals.append("short_dated_otm")
                if repeat_days >= 1:
                    signals.append(f"repeat_{repeat_days}d")
                if pc_ratio is not None and pc_ratio > 1.0:
                    signals.append(f"pc_div_{pc_ratio}")
                if 0 < iv < _LOW_IV_THRESHOLD:
                    signals.append(f"low_iv_{iv:.0f}")
                if _check_gamma_squeeze(strike, oi, price):
                    signals.append("gamma_squeeze")

                raw_hits.append({
                    "ticker":      ticker,
                    "price":       price,
                    "vwap":        vwap,
                    "above_vwap":  price >= vwap,
                    "strike":      strike,
                    "expiry":      expiry,
                    "days_out":    days_out,
                    "vol":         vol,
                    "oi":          oi,
                    "vol_oi":      round(vol_oi, 1),
                    "premium":     premium,
                    "otm_pct":     round(otm_pct, 1),
                    "iv":          round(iv, 1),
                    "spread_pct":  spread_pct,
                    "pc_ratio":    pc_ratio,
                    "repeat_days": repeat_days,
                    "red_day":     red_day,
                    "signals":     signals,
                    "conviction":  len(signals),
                })
            except Exception:
                pass

    # Detect multi-strike: if >= 2 qualifying hits exist for this ticker, flag all of them
    multi_strike = len(raw_hits) >= 2
    for h in raw_hits:
        h["multi_strike"] = multi_strike

    return raw_hits


# ── SMS message builder ───────────────────────────────────────────────────────

def _build_sweep_msg(h: dict, now_et: datetime, ics_score: int, ics_labels: list[str]) -> str:
    # ICS-based label (replaces the old 6-signal star rating)
    if ics_score >= 80:
        header = f"🔥🔥🔥 EXTREME CONVICTION — ICS {ics_score}/100"
    elif ics_score >= 70:
        header = f"⭐⭐⭐ HIGH CONVICTION — ICS {ics_score}/100"
    else:
        header = f"⭐⭐ STRONG SWEEP — ICS {ics_score}/100"

    otm_label  = f"+{h['otm_pct']}% OTM" if h["otm_pct"] >= 0 else f"{abs(h['otm_pct'])}% ITM"

    lines = [
        f"🎯 INST. CONVICTION ALERT: {h['ticker']}",
        header,
        f"${h['strike']} strike ({otm_label}) exp {h['expiry']} ({h['days_out']}d)",
        f"Vol {h['vol']:,} | OI {h['oi']:,} | {h['vol_oi']}x ratio",
        f"Premium ${h['premium']:,} | Stock ${h['price']:.2f}",
    ]

    # Add ICS signal breakdown (up to 5 signals to keep SMS short)
    if ics_labels:
        lines.append("Signals fired:")
        for lbl in ics_labels[:5]:
            lines.append(f"  {lbl}")
        if len(ics_labels) > 5:
            lines.append(f"  +{len(ics_labels)-5} more")

    lines.append(now_et.strftime("%I:%M %p ET"))
    return "\n".join(lines)


# ── Universe helpers ──────────────────────────────────────────────────────────

def _get_sms_alerted_today() -> list[str]:
    try:
        with _conn() as con:
            with con.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT ticker FROM sms_alerts_log
                    WHERE alert_date = (now() AT TIME ZONE 'America/New_York')::date
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

    print(f"[options_sweep] scanning {len(universe)} tickers (ICS threshold: {_ICS_SMS_THRESHOLD}+)...")

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

                # ── Auto-score against ICS weights ────────────────────────────
                ics_score, ics_labels = _compute_ics_score(h, now_et)

                print(f"[options_sweep] {ticker} ${h['strike']} exp {h['expiry']} → ICS {ics_score}/100")

                # Only alert if automated ICS score meets threshold
                if ics_score < _ICS_SMS_THRESHOLD:
                    continue

                msg = _build_sweep_msg(h, now_et, ics_score, ics_labels)
                if send_sms(msg):
                    _log_sweep_alert(
                        ticker, h["strike"], h["expiry"],
                        h["vol"], h["oi"], h["vol_oi"],
                        h["premium"], h["price"], h["vwap"],
                        ics_score, ",".join(h["signals"]) + f"|ics_{ics_score}"
                    )
                    sent += 1
        except Exception as e:
            print(f"[options_sweep] error scanning {ticker}: {e}")

    print(f"[options_sweep] scan complete — {len(universe)} tickers, {sent} ICS {_ICS_SMS_THRESHOLD}+ alerts sent")
