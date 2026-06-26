"""
Call Sweep Scanner — Institutional Conviction Score (ICS) Engine
Detects aggressive call buying and scores each sweep across 12 automatable signals.
Alerts fire when the ICS score >= _ICS_SMS_THRESHOLD (default 60/114 ≈ 53%).

ICS signals (automatable, max 114 pts):
  nakedCall(20)      — P/C < 0.5 on the swept expiry (directional, 2:1 calls vs puts)
  askSide(15)        — vol/OI >= 3x (market-order urgency, not limit fishing)
  multiLegSweep(15)  — >= 2 qualifying strikes on the *same* expiry (coordinated)
  premiumSize(12)    — total premium >= $500K
  shortDatedOTM(10)  — weekly OTM call (<=7d, strike > price) = speculative directional
  aboveVWAP(8)       — stock price >= VWAP (intraday trend confirmation)
  heavyVolume(8)     — >= 500 contracts absolute (eliminates thin activity)
  repeatActivity(6)  — swept on a prior day this week (accumulation pattern)
  redDayBuy(5)       — buying calls on a down day (conviction despite weakness)
  lowIVR(5)          — IV < 50% (cheap options = buyer expects real move, not hedge)
  earlyMorning(3)    — 9:30–10:00 AM ET (most informed institutional window)
  tightSpread(7)     — bid/ask spread < 3% of mid (market maker direction confidence)

Non-automatable (manual tool only, NOT in denominator):
  oiSpike(4), quietTicker(3), darkPool(3), preCatalyst(3) → 13 pts

holy_grail module adds optional bonus pts (up to 73) when available.
conviction column in call_sweep_log stores ICS score (0–100+), NOT legacy star rating.
"""
import os
import psycopg2
import yfinance as yf
from datetime import datetime, time as dtime, timedelta
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
_SHORT_DATED_DAYS = 7      # <=7 days = weekly = almost always speculative
_LOW_IV_THRESHOLD = 50.0   # IV% below this = cheap options = real conviction buy
_GAMMA_ROUND_PCT  = 0.5    # strike within 0.5% of a round $5 number = gamma zone
_GAMMA_MIN_OI     = 500    # minimum OI at that strike to matter

# ── ICS signal weights (named so denominator stays in sync automatically) ─────
_W_NAKED_CALL    = 20   # P/C < 0.5 on the swept expiry (directional conviction)
_W_ASK_SIDE      = 15   # vol/OI >= 3x (market-order urgency)
_W_MULTI_LEG     = 15   # >= 2 qualifying strikes on the SAME expiry (coordinated)
_W_PREMIUM       = 12   # premium >= $500K
_W_SHORT_OTM     = 10   # weekly OTM (<=7d, strike > price)
_W_ABOVE_VWAP    = 8    # price >= VWAP
_W_HEAVY_VOL     = 8    # vol >= 500 contracts
_W_REPEAT        = 6    # swept on a prior day this week
_W_RED_DAY       = 5    # buying calls on a down day
_W_LOW_IVR       = 5    # IV < 50% (cheap = real conviction)
_W_EARLY_MORNING = 3    # 9:30–10:00 AM ET print
_W_TIGHT_SPREAD  = 7    # bid/ask spread < 3% of mid
# Non-automatable (manual tool only — NOT in denominator):
#   oiSpike=4, quietTicker=3, darkPool=3, preCatalyst=3 → 13 pts
_ICS_AUTOMATABLE_WEIGHT = (
    _W_NAKED_CALL + _W_ASK_SIDE + _W_MULTI_LEG + _W_PREMIUM +
    _W_SHORT_OTM + _W_ABOVE_VWAP + _W_HEAVY_VOL + _W_REPEAT +
    _W_RED_DAY + _W_LOW_IVR + _W_EARLY_MORNING + _W_TIGHT_SPREAD
)  # = 114
# holy_grail is an optional additive bonus (up to 73 pts). When the module is
# unavailable it contributes 0, so we score against the automatable base only.
# Scores may exceed 100 when holy_grail fires — intentional (super-conviction bonus).
_ICS_TOTAL_WEIGHT  = _ICS_AUTOMATABLE_WEIGHT   # 114
_ICS_SMS_THRESHOLD = 60   # 60/114 ≈ 53% of automatable max → targets 65-70% win rate
                           # Recalibrate against live backtest once 30+ signals accumulate.
                           # (Prior bug: 80/200 → max reachable score was 57 → no alerts ever fired)

# ── Early-morning window ──────────────────────────────────────────────────────
_EARLY_OPEN  = dtime(9,  30)   # 9:30 AM ET
_EARLY_CLOSE = dtime(10,  0)   # 10:00 AM ET inclusive


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
                        conviction    INTEGER DEFAULT 0,  -- stores ICS score (0-100+), NOT legacy star rating
                        signals_fired TEXT,
                        sweep_date    DATE NOT NULL DEFAULT CURRENT_DATE,
                        sent_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                """)
                # Backfill columns added after initial deploy
                cur.execute("ALTER TABLE call_sweep_log ADD COLUMN IF NOT EXISTS conviction INTEGER DEFAULT 0")
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
                    WHERE ticker=%s AND strike=%s AND expiry=%s
                      AND sweep_date=(now() AT TIME ZONE 'America/New_York')::date
                    LIMIT 1
                """, (ticker, strike, expiry))
                return cur.fetchone() is not None
    except Exception:
        return False


def _log_sweep_alert(ticker, strike, expiry, call_vol, oi, vol_oi,
                     premium, price, vwap, ics_score, signals_fired):
    """Log a fired alert. `ics_score` (0-100+) is stored in the `conviction` column."""
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
                      premium, price, vwap, ics_score, signals_fired))
    except Exception as e:
        print(f"[options_sweep] log error {ticker}: {e}")


# ── Signal: Repeat sweep check ────────────────────────────────────────────────

def _get_repeat_sweep_days(ticker: str) -> int:
    """Returns how many distinct sweep days exist in the prior 4 calendar days
    (≈ 3 trading days; Sat/Sun never have rows so the window is effectively correct).
    Returns 0-3 in practice."""
    try:
        with _conn() as con:
            with con.cursor() as cur:
                cur.execute("""
                    SELECT COUNT(DISTINCT sweep_date) FROM call_sweep_log
                    WHERE ticker=%s
                      AND sweep_date >= (now() AT TIME ZONE 'America/New_York')::date - 4
                      AND sweep_date <  (now() AT TIME ZONE 'America/New_York')::date
                """, (ticker,))
                row = cur.fetchone()
                return int(row[0]) if row else 0
    except Exception:
        return 0


# ── Signal: P/C ratio for a specific expiry ───────────────────────────────────

def _get_pc_ratio_for_expiry(tk: yf.Ticker, expiry: str) -> float | None:
    """
    Returns put/call volume ratio for the given expiry (the one being swept).
    > 1.0 = more put vol than call vol = elevated fear = bullish contrarian fuel.
    Uses the swept expiry's chain, not the nearest expiry, so the signal is
    actually correlated with the trade being scored (nakedCall = 20 pts).
    """
    try:
        chain    = tk.option_chain(expiry)
        call_vol = int(chain.calls["volume"].fillna(0).sum())
        put_vol  = int(chain.puts["volume"].fillna(0).sum())
        if call_vol <= 0:
            return None
        return round(put_vol / call_vol, 2)
    except Exception:
        return None


# ── Signal: Gamma squeeze setup ───────────────────────────────────────────────

def _check_gamma_squeeze(strike: float, oi: int, price: float) -> bool:
    """True if strike is near a round $5 level with heavy OI — dealers must buy."""
    if oi < _GAMMA_MIN_OI:
        return False
    nearest_round = round(strike / 5) * 5
    proximity_pct = abs(strike - nearest_round) / price * 100
    return proximity_pct <= _GAMMA_ROUND_PCT


# ── Merged price data fetch (single yfinance call) ───────────────────────────

def _get_price_data(ticker: str) -> tuple[float, float, float]:
    """
    Returns (price, vwap, prev_close) from a single 2-day 1-minute history pull.
    Merges what were two separate yfinance calls (_get_vwap + _get_prev_close).
    - price     : last trade price (today's most recent 1m close)
    - vwap      : today's intraday VWAP (TP-weighted over today's bars only)
    - prev_close: yesterday's last 1m close (for red-day detection)
    """
    try:
        tk   = yf.Ticker(ticker)
        hist = tk.history(period="2d", interval="1m")
        if hist.empty:
            return 0.0, 0.0, 0.0

        today = datetime.now(_ET).date()
        hist.index = hist.index.tz_convert(_ET)

        today_bars = hist[hist.index.date == today]
        yest_bars  = hist[hist.index.date < today]

        if today_bars.empty:
            return 0.0, 0.0, 0.0

        price = float(today_bars["Close"].iloc[-1])

        tp  = (today_bars["High"] + today_bars["Low"] + today_bars["Close"]) / 3
        vol = float(today_bars["Volume"].sum())
        vwap = float((tp * today_bars["Volume"]).sum()) / vol if vol > 0 else price

        prev_close = float(yest_bars["Close"].iloc[-1]) if not yest_bars.empty else 0.0

        return price, vwap, prev_close
    except Exception:
        return 0.0, 0.0, 0.0


# ── ICS auto-scoring function ─────────────────────────────────────────────────

def _compute_ics_score(h: dict, now_et: datetime) -> tuple[int, list[str]]:
    """
    Compute ICS score using named weight constants (_W_*).
    Denominator = _ICS_TOTAL_WEIGHT (114 automatable pts).
    Returns (score, label_list). Score may exceed 100 if holy_grail fires.

    h must contain: vol_oi, multi_strike, premium, days_out, otm_pct, above_vwap,
                    vwap, vol, repeat_days, red_day, iv, spread_pct, ticker,
                    price, pc_ratio (for this expiry).
    """
    pts    = 0
    labels = []

    # nakedCall — P/C < 0.5 on the swept expiry = 2:1 more calls than puts
    pc = h.get("pc_ratio")
    if pc is not None and pc < 0.5:
        pts += _W_NAKED_CALL
        labels.append(f"📣 Directional (P/C {pc:.2f}) — naked conviction")

    # askSide — vol/OI >= 3x = paying up, not limit-fishing
    if h["vol_oi"] >= 3.0:
        pts += _W_ASK_SIDE
        labels.append(f"⚡ Ask-side urgency ({h['vol_oi']}x ratio)")

    # multiLegSweep — >= 2 qualifying strikes on the SAME expiry
    if h.get("multi_strike", False):
        pts += _W_MULTI_LEG
        labels.append("🔀 Multi-strike sweep same expiry (coordinated)")

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

    # heavyVolume
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

    # earlyMorning — proper 9:30:00–10:00:59 range (not spot-second check)
    t = now_et.time().replace(second=0, microsecond=0)
    if _EARLY_OPEN <= t <= _EARLY_CLOSE:
        pts += _W_EARLY_MORNING
        labels.append("🌅 Early morning print (informed money)")

    # tightSpread — bid/ask spread < 3% of mid
    spread_pct = h.get("spread_pct", 100.0)
    if 0 < spread_pct < 3.0:
        pts += _W_TIGHT_SPREAD
        labels.append(f"📐 Tight spread {spread_pct:.1f}% — market maker direction confidence")

    # holy_grail bonus (optional module, up to 73 pts)
    try:
        from holy_grail import compute_holy_grail_signals
        hg = compute_holy_grail_signals(h["ticker"], h["price"], h["vwap"])
        pts += hg["pts"]
        labels.extend(hg["labels"])
    except Exception as _hg_err:
        pass  # holy_grail unavailable — score purely from automatable signals

    score = min(round(pts / _ICS_TOTAL_WEIGHT * 100), 100)
    return score, labels


# ── Full sweep scan for one ticker ────────────────────────────────────────────

def _scan_ticker_for_sweeps(ticker: str) -> list[dict]:
    price, vwap, prev_close = _get_price_data(ticker)   # single yfinance call
    if price <= 0 or vwap <= 0:
        return []

    tk      = yf.Ticker(ticker)
    now     = datetime.now(_ET).date()
    red_day = (prev_close > 0 and price < prev_close)
    repeat_days = _get_repeat_sweep_days(ticker)

    # Group qualifying hits by expiry so multi_strike is per-expiry, not cross-expiry
    hits_by_expiry: dict[str, list[dict]] = {}

    for expiry in (tk.options or []):
        exp_date = datetime.strptime(expiry, "%Y-%m-%d").date()
        days_out = (exp_date - now).days + 1
        if not (1 <= days_out <= _MAX_DAYS_OUT):
            continue

        try:
            calls = tk.option_chain(expiry).calls
        except Exception:
            continue

        # P/C ratio from the same expiry being swept (not nearest expiry)
        pc_ratio = _get_pc_ratio_for_expiry(tk, expiry)

        skipped = 0
        expiry_hits = []
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
                spread_pct = round((ask - bid) / mid * 100, 2) if mid > 0 and ask > bid else 100.0

                # Signal tags stored for signals_fired DB column (audit trail)
                signals = ["sweep"]
                if days_out <= _SHORT_DATED_DAYS and otm_pct > 0:
                    signals.append("short_dated_otm")
                if repeat_days >= 1:
                    signals.append(f"repeat_{repeat_days}d")
                if pc_ratio is not None and pc_ratio < 0.5:
                    signals.append(f"naked_call_pc{pc_ratio:.2f}")
                if 0 < iv < _LOW_IV_THRESHOLD:
                    signals.append(f"low_iv_{iv:.0f}")
                if _check_gamma_squeeze(strike, oi, price):
                    signals.append("gamma_squeeze")

                expiry_hits.append({
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
                })
            except Exception as _row_err:
                skipped += 1

        if skipped:
            print(f"[options_sweep] {ticker} {expiry}: {skipped} row(s) skipped due to bad data")

        if expiry_hits:
            hits_by_expiry[expiry] = expiry_hits

    # multi_strike: >= 2 qualifying strikes on the SAME expiry = coordinated sweep
    raw_hits = []
    for expiry, hits in hits_by_expiry.items():
        same_expiry_multi = len(hits) >= 2
        for h in hits:
            h["multi_strike"] = same_expiry_multi
        raw_hits.extend(hits)

    return raw_hits


# ── SMS message builder ───────────────────────────────────────────────────────

def _build_sweep_msg(h: dict, now_et: datetime, ics_score: int, ics_labels: list[str]) -> str:
    if ics_score >= 80:
        header = f"🔥🔥🔥 EXTREME CONVICTION — ICS {ics_score}/100"
    elif ics_score >= 70:
        header = f"⭐⭐⭐ HIGH CONVICTION — ICS {ics_score}/100"
    else:
        header = f"⭐⭐ STRONG SWEEP — ICS {ics_score}/100"

    otm_label = f"+{h['otm_pct']}% OTM" if h["otm_pct"] >= 0 else f"{abs(h['otm_pct'])}% ITM"

    lines = [
        f"🎯 INST. CONVICTION ALERT: {h['ticker']}",
        header,
        f"${h['strike']} strike ({otm_label}) exp {h['expiry']} ({h['days_out']}d)",
        f"Vol {h['vol']:,} | OI {h['oi']:,} | {h['vol_oi']}x ratio",
        f"Premium ${h['premium']:,} | Stock ${h['price']:.2f}",
    ]

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
    Runs every 15 min during market hours (9:30–15:45 ET, weekdays only).
    Scans today's SMS-alerted tickers for bullish call sweeps scored by ICS.
    Fires an alert for each qualifying sweep (ICS >= _ICS_SMS_THRESHOLD) not yet alerted today.
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

    print(f"[options_sweep] scanning {len(universe)} tickers "
          f"(ICS threshold: {_ICS_SMS_THRESHOLD}+, denominator: {_ICS_TOTAL_WEIGHT})...")

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

                ics_score, ics_labels = _compute_ics_score(h, now_et)
                print(f"[options_sweep] {ticker} ${h['strike']} {h['expiry']} "
                      f"→ ICS {ics_score}/100 (signals: {','.join(h['signals'])})")

                if ics_score < _ICS_SMS_THRESHOLD:
                    continue

                msg = _build_sweep_msg(h, now_et, ics_score, ics_labels)
                if send_sms(msg):
                    _log_sweep_alert(
                        ticker, h["strike"], h["expiry"],
                        h["vol"], h["oi"], h["vol_oi"],
                        h["premium"], h["price"], h["vwap"],
                        ics_score,
                        ",".join(h["signals"]) + f"|ics_{ics_score}"
                    )
                    sent += 1
        except Exception as e:
            print(f"[options_sweep] error scanning {ticker}: {e}")

    print(f"[options_sweep] scan complete — {len(universe)} tickers, "
          f"{sent} ICS {_ICS_SMS_THRESHOLD}+ alerts sent")
