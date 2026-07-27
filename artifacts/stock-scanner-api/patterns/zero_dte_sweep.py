"""
patterns/zero_dte_sweep.py
--------------------------
0DTE sweep pattern scanner for SPY and SPX.

ISOLATION: zero imports from aiem_*, options_engine, or any shared-state
module. All Tradier API calls, DB connections, and Telegram delivery are
self-contained within this module. Main.py calls scan_once(tg_fn=_tg_send).

Pattern gates (ALL must pass for a match):
  1. Bid/ask spread  <= 0.10 (SPY) / 0.30 (SPX)
  2. Delta           in [0.25, 0.70] (abs value)
  3. Volume/OI ratio >= 2.0
  4. 5-min sweep $   >= $500k  (volume delta since last scan * midPrice * 100)
  5. IV Rank         >= 0.50   (skipped/logged if < 5 days stored history)
  6. Price confirm   underlying price >= 5-min bar High (calls) or
                                      <= 5-min bar Low  (puts)
                     (skipped/logged if no bars available)

Scan windows: 10:00–11:30 ET and 14:00–15:30 ET, Mon–Fri only.
Interval: 5 minutes (controlled by APScheduler job in main.py).

Paper trading:
  - Every alert match opens a hypothetical paper trade in paper_0dte_trades.
  - A separate 1-min monitor (registered in main.py) polls current prices and
    closes trades at profit_target or stop_loss.
  - An EOD closer (15:35 ET cron in main.py) sweeps remaining open trades.
  - Win-rate stats are live via the v_paper_0dte_stats view.
"""

import os
import threading
from datetime import datetime, date

_TICKERS        = ["SPY", "SPX"]
_SPREAD_LIMIT   = {"SPY": 0.10, "SPX": 0.30}
_PREMIUM_THRESH = 500_000      # USD per 5-min window
_VOI_MIN        = 2.0
_IV_RANK_MIN    = 0.50
_DELTA_MIN      = 0.25
_DELTA_MAX      = 0.70
_IV_HISTORY_MIN = 5            # minimum stored days before IV rank gate fires
_IV_HISTORY_MAX = 20
_WINDOWS_ET     = [(10, 0, 11, 30), (14, 0, 15, 30)]

# ── Paper trading config ──────────────────────────────────────────────────────
# ⚠️  PROPOSED DEFAULTS — flagged for approval (per directive note).
#    profit_target_pct=1.00 → exit when option premium doubles (+100%).
#    stop_loss_pct=0.50     → exit when option loses half its value (−50%).
#    These are the most common retail 0DTE paper-trading defaults.
#    Change these constants to adjust; they are NOT hardcoded in any logic.
_PAPER_PROFIT_TARGET_PCT: float = 0.50   # +50% gain on entry premium → target exit
_PAPER_STOP_LOSS_PCT:     float = 0.10   # −10% loss on entry premium  → stop exit
_PAPER_CONTRACTS:         int   = 1      # hypothetical contracts per trade (×100 shares)

_tables_ready   = False
_last_vol: dict = {}           # {(ticker, side, strike, expiry): int}
_last_vol_lock  = threading.Lock()


# ── Tradier helpers (self-contained) ─────────────────────────────────────────

def _td_headers() -> dict:
    tok = (os.environ.get("TRADIER_API_TOKEN_2") or
           os.environ.get("TRADIER_API_TOKEN") or "")
    return {"Authorization": f"Bearer {tok}", "Accept": "application/json"} if tok else {}


def _today_expiry(ticker: str) -> str | None:
    """Return today's date string if it appears in Tradier's expiry list, else None."""
    import requests
    today_str = date.today().isoformat()
    hdrs = _td_headers()
    if not hdrs:
        return None
    try:
        r = requests.get(
            "https://api.tradier.com/v1/markets/options/expirations",
            params={"symbol": ticker, "includeAllRoots": "false"},
            headers=hdrs, timeout=5,
        )
        if r.status_code != 200:
            return None
        raw = (r.json().get("expirations") or {}).get("date") or []
        if isinstance(raw, str):
            raw = [raw]
        return today_str if today_str in raw else None
    except Exception as exc:
        print(f"[0dte] expiry {ticker}: {exc}")
        return None


def _fetch_chain(ticker: str, expiry: str) -> tuple:
    """
    Returns (calls, puts) where each is a list of dicts:
      strike, bid, ask, lastPrice, volume, openInterest,
      impliedVolatility, delta, contractSymbol
    Fetches with greeks=true so delta comes from Tradier's own model.
    """
    import requests
    hdrs = _td_headers()
    if not hdrs:
        return [], []
    try:
        r = requests.get(
            "https://api.tradier.com/v1/markets/options/chains",
            params={"symbol": ticker, "expiration": expiry, "greeks": "true"},
            headers=hdrs, timeout=8,
        )
        if r.status_code != 200:
            return [], []
        opts = (r.json().get("options") or {}).get("option") or []
        if isinstance(opts, dict):
            opts = [opts]
        calls, puts = [], []
        for o in opts:
            strike = float(o.get("strike") or 0)
            if not strike:
                continue
            bid   = float(o.get("bid") or 0)
            ask   = float(o.get("ask") or 0)
            mid   = (bid + ask) / 2 if bid and ask else float(o.get("last") or 0)
            g     = o.get("greeks") or {}
            d_raw = g.get("delta")
            row   = {
                "strike":            strike,
                "bid":               bid,
                "ask":               ask,
                "lastPrice":         mid,
                "volume":            int(o.get("volume") or 0),
                "openInterest":      int(o.get("open_interest") or 0),
                "impliedVolatility": float(g.get("mid_iv") or 0),
                "delta":             float(d_raw) if d_raw not in (None, "") else None,
                "contractSymbol":    str(o.get("symbol") or ""),
            }
            (calls if o.get("option_type") == "call" else puts).append(row)
        return calls, puts
    except Exception as exc:
        print(f"[0dte] chain {ticker}/{expiry}: {exc}")
        return [], []


def _fetch_5min_bars(ticker: str) -> list:
    """
    Returns list of 5-min bar dicts (open/high/low/close/volume) for the current
    session via Tradier timesales. Empty list on error or for non-equity tickers.
    """
    import requests
    tok = (os.environ.get("TRADIER_API_TOKEN_2") or
           os.environ.get("TRADIER_API_TOKEN") or "")
    if not tok:
        return []
    hdrs = {"Authorization": f"Bearer {tok}", "Accept": "application/json"}
    try:
        r = requests.get(
            "https://api.tradier.com/v1/markets/timesales",
            params={"symbol": ticker, "interval": "5min", "session_filter": "open"},
            headers=hdrs, timeout=6,
        )
        if r.status_code != 200:
            return []
        bars = (r.json().get("series") or {}).get("data") or []
        if isinstance(bars, dict):
            bars = [bars]
        return bars
    except Exception as exc:
        print(f"[0dte] timesales {ticker}: {exc}")
        return []


def _fetch_underlying_price(ticker: str) -> float:
    """
    Current underlying price via Tradier quotes.
    Uses .SPX for SPX index quotes; SPY is a direct equity quote.
    Falls back to 0.0 on error.
    """
    import requests
    sym = ".SPX" if ticker == "SPX" else ticker
    hdrs = _td_headers()
    if not hdrs:
        return 0.0
    try:
        r = requests.get(
            "https://api.tradier.com/v1/markets/quotes",
            params={"symbols": sym},
            headers=hdrs, timeout=5,
        )
        if r.status_code != 200:
            return 0.0
        raw = r.json().get("quotes", {}).get("quote", {})
        if isinstance(raw, list):
            raw = raw[0] if raw else {}
        return float(raw.get("last") or 0)
    except Exception as exc:
        print(f"[0dte] underlying_price {ticker}: {exc}")
        return 0.0


def _fetch_option_mid(contract_symbol: str) -> float | None:
    """
    Current bid/ask midpoint for a single option contract via Tradier quotes API.
    Used by the 1-min paper trade monitor to check target/stop levels.
    Falls back to last price if bid/ask unavailable. Returns None on error.
    """
    import requests
    hdrs = _td_headers()
    if not hdrs or not contract_symbol:
        return None
    try:
        r = requests.get(
            "https://api.tradier.com/v1/markets/quotes",
            params={"symbols": contract_symbol, "greeks": "false"},
            headers=hdrs, timeout=5,
        )
        if r.status_code != 200:
            return None
        raw = r.json().get("quotes", {}).get("quote", {})
        if isinstance(raw, list):
            raw = raw[0] if raw else {}
        bid = float(raw.get("bid") or 0)
        ask = float(raw.get("ask") or 0)
        if bid > 0 and ask > 0:
            return round((bid + ask) / 2, 4)
        last = float(raw.get("last") or 0)
        return round(last, 4) if last > 0 else None
    except Exception as exc:
        print(f"[0dte_paper] fetch_mid {contract_symbol}: {exc}")
        return None


# ── DB helpers ────────────────────────────────────────────────────────────────

def _db():
    import psycopg2
    return psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=4)


def ensure_tables() -> None:
    """Idempotent table creation. Called once per process lifetime."""
    try:
        with _db() as conn, conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS pattern_0dte_matches (
                    id                BIGSERIAL PRIMARY KEY,
                    scanned_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    ticker            TEXT        NOT NULL,
                    side              TEXT        NOT NULL,
                    strike            NUMERIC     NOT NULL,
                    expiry            DATE        NOT NULL,
                    contract_symbol   TEXT,
                    sweep_premium_usd NUMERIC,
                    vol_oi_ratio      NUMERIC,
                    iv_rank           NUMERIC,
                    delta             NUMERIC,
                    bid               NUMERIC,
                    ask               NUMERIC,
                    spread            NUMERIC,
                    underlying_price  NUMERIC,
                    five_min_high     NUMERIC,
                    five_min_low      NUMERIC,
                    gates_passed      TEXT[]
                );
                CREATE TABLE IF NOT EXISTS pattern_0dte_iv_history (
                    id        BIGSERIAL PRIMARY KEY,
                    ticker    TEXT  NOT NULL,
                    snap_date DATE  NOT NULL,
                    atm_iv    NUMERIC NOT NULL,
                    UNIQUE (ticker, snap_date)
                );

                -- Paper trading: one row per hypothetical trade opened on an alert match
                CREATE TABLE IF NOT EXISTS paper_0dte_trades (
                    trade_id          BIGSERIAL   PRIMARY KEY,
                    match_id          BIGINT      NOT NULL
                                                  REFERENCES pattern_0dte_matches(id),
                    contract_symbol   TEXT        NOT NULL,
                    ticker            TEXT        NOT NULL,
                    side              TEXT        NOT NULL,   -- call / put
                    strike            NUMERIC     NOT NULL,
                    expiry            DATE        NOT NULL,
                    entry_price       NUMERIC     NOT NULL,   -- ask at alert time (config source: row["ask"])
                    contracts         INT         NOT NULL DEFAULT 1,
                    -- Config values stored per-trade so historical rows reflect the
                    -- config that was active when the trade was opened.
                    -- Source constants: _PAPER_PROFIT_TARGET_PCT, _PAPER_STOP_LOSS_PCT
                    profit_target_pct NUMERIC     NOT NULL,
                    stop_loss_pct     NUMERIC     NOT NULL,
                    exit_price        NUMERIC,
                    exit_reason       TEXT,        -- target | stop | expired_worthless | eod
                    exit_time         TIMESTAMPTZ,
                    pnl_usd           NUMERIC,     -- (exit - entry) * contracts * 100
                    pnl_pct           NUMERIC,     -- (exit - entry) / entry
                    win               BOOLEAN,
                    status            TEXT        NOT NULL DEFAULT 'open',  -- open | closed
                    opened_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            # Live win-rate view — updated on every query, not a static snapshot
            cur.execute("""
                CREATE OR REPLACE VIEW v_paper_0dte_stats AS
                SELECT
                    COUNT(*)                                                          AS total_trades,
                    COUNT(*) FILTER (WHERE win IS TRUE)                               AS wins,
                    COUNT(*) FILTER (WHERE win IS FALSE)                              AS losses,
                    COUNT(*) FILTER (WHERE status = 'open')                           AS open_trades,
                    ROUND(
                        100.0 * COUNT(*) FILTER (WHERE win IS TRUE)
                        / NULLIF(COUNT(*) FILTER (WHERE status = 'closed'), 0), 2
                    )                                                                 AS win_rate_pct,
                    ROUND(AVG(pnl_usd) FILTER (WHERE win IS TRUE),  2)               AS avg_win_usd,
                    ROUND(AVG(pnl_pct) FILTER (WHERE win IS TRUE),  4)               AS avg_win_pct,
                    ROUND(AVG(pnl_usd) FILTER (WHERE win IS FALSE AND status='closed'), 2) AS avg_loss_usd,
                    ROUND(AVG(pnl_pct) FILTER (WHERE win IS FALSE AND status='closed'), 4) AS avg_loss_pct,
                    MAX(opened_at)                                                    AS last_trade_at
                FROM paper_0dte_trades;
            """)
            conn.commit()
        print("[0dte] tables ready (pattern_0dte_matches, pattern_0dte_iv_history, paper_0dte_trades, v_paper_0dte_stats)")
    except Exception as exc:
        print(f"[0dte] table init error: {exc}")


# ── IV rank ───────────────────────────────────────────────────────────────────

def _store_atm_iv(ticker: str, iv: float) -> None:
    if iv <= 0:
        return
    today = date.today().isoformat()
    try:
        with _db() as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO pattern_0dte_iv_history (ticker, snap_date, atm_iv)
                VALUES (%s, %s, %s)
                ON CONFLICT (ticker, snap_date) DO UPDATE SET atm_iv = EXCLUDED.atm_iv
            """, (ticker, today, iv))
            conn.commit()
    except Exception as exc:
        print(f"[0dte] iv_history write {ticker}: {exc}")


def _compute_iv_rank(ticker: str, current_iv: float) -> float | None:
    """
    Returns IV rank (0.0–1.0) using up to the last 20 stored trading-day snapshots.
    Returns None when fewer than _IV_HISTORY_MIN rows exist (gate skipped, logged).
    """
    try:
        with _db() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT atm_iv FROM pattern_0dte_iv_history
                WHERE ticker = %s ORDER BY snap_date DESC LIMIT %s
            """, (ticker, _IV_HISTORY_MAX))
            rows = [float(r[0]) for r in cur.fetchall()]
        if len(rows) < _IV_HISTORY_MIN:
            return None
        iv_min, iv_max = min(rows), max(rows)
        if iv_max == iv_min:
            return None
        return max(0.0, min(1.0, (current_iv - iv_min) / (iv_max - iv_min)))
    except Exception as exc:
        print(f"[0dte] iv_rank {ticker}: {exc}")
        return None


# ── Trading window guard ──────────────────────────────────────────────────────

def _in_trading_window() -> bool:
    """True only during 10:00–11:30 and 14:00–15:30 ET, Mon–Fri."""
    try:
        from zoneinfo import ZoneInfo
        ZI = ZoneInfo
    except ImportError:
        import pytz
        ZI = lambda tz: pytz.timezone(tz)
    now = datetime.now(ZI("America/New_York"))
    if now.weekday() >= 5:
        return False
    hm = (now.hour, now.minute)
    for (hs, ms, he, me) in _WINDOWS_ET:
        if (hs, ms) <= hm <= (he, me):
            return True
    return False


# ── Gate evaluation (single contract) ────────────────────────────────────────

def _eval_contract(
    ticker: str,
    side: str,
    row: dict,
    expiry: str,
    bars: list,
    underlying_price: float,
    spread_limit: float,
    iv_rank: float | None,
    prev_vol: int,
) -> tuple:
    """
    Returns (passed: bool, gates: list[str], sweep_usd: float).
    Gates are accumulated until the first failure so callers can log partial progress.
    """
    gates = []

    bid, ask = row["bid"], row["ask"]
    spread = ask - bid

    # Gate 1 — spread
    if spread <= spread_limit:
        gates.append("spread_ok")
    else:
        return False, gates, 0.0

    # Gate 2 — delta
    delta = row.get("delta")
    if delta is not None and _DELTA_MIN <= abs(delta) <= _DELTA_MAX:
        gates.append("delta_ok")
    else:
        return False, gates, 0.0

    # Gate 3 — volume / OI
    vol = row["volume"]
    oi  = row["openInterest"]
    if oi > 0 and (vol / oi) >= _VOI_MIN:
        gates.append("voi_ok")
    else:
        return False, gates, 0.0

    # Gate 4 — 5-min sweep premium (volume delta × midPrice × 100)
    vol_delta = max(0, vol - prev_vol)
    sweep_usd = vol_delta * row["lastPrice"] * 100
    if sweep_usd >= _PREMIUM_THRESH:
        gates.append("premium_ok")
    else:
        return False, gates, sweep_usd

    # Gate 5 — IV rank
    if iv_rank is not None:
        if iv_rank >= _IV_RANK_MIN:
            gates.append("iv_rank_ok")
        else:
            return False, gates, sweep_usd
    else:
        gates.append("iv_rank_skipped_lt5d_history")

    # Gate 6 — price confirmation vs last complete 5-min bar
    if len(bars) >= 2:
        last_complete = bars[-2]
        bar_high = float(last_complete.get("high") or 0)
        bar_low  = float(last_complete.get("low")  or 0)
        if side == "call" and bar_high > 0 and underlying_price >= bar_high:
            gates.append("price_confirm_ok")
        elif side == "put" and bar_low > 0 and underlying_price <= bar_low:
            gates.append("price_confirm_ok")
        else:
            return False, gates, sweep_usd
    elif len(bars) == 1:
        gates.append("price_confirm_skipped_only_1_bar")
    else:
        gates.append("price_confirm_skipped_no_bars")

    return True, gates, sweep_usd


# ── DB write ──────────────────────────────────────────────────────────────────

def _write_match(
    ticker, side, row, expiry, underlying_price,
    sweep_usd, iv_rank, gates, bars,
) -> int | None:
    """
    Insert into pattern_0dte_matches and return the new row's id (or None on error).
    The returned id is the FK for paper_0dte_trades.
    """
    last_bar = bars[-2] if len(bars) >= 2 else (bars[-1] if bars else {})
    try:
        with _db() as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO pattern_0dte_matches
                    (ticker, side, strike, expiry, contract_symbol,
                     sweep_premium_usd, vol_oi_ratio, iv_rank, delta,
                     bid, ask, spread, underlying_price,
                     five_min_high, five_min_low, gates_passed)
                VALUES (%s,%s,%s,%s,%s, %s,%s,%s,%s, %s,%s,%s,%s, %s,%s,%s)
                RETURNING id
            """, (
                ticker, side, row["strike"], expiry, row.get("contractSymbol"),
                round(sweep_usd, 2),
                round(row["volume"] / max(row["openInterest"], 1), 4),
                round(iv_rank, 4) if iv_rank is not None else None,
                row.get("delta"),
                row["bid"], row["ask"],
                round(row["ask"] - row["bid"], 4),
                underlying_price or None,
                float(last_bar.get("high") or 0) or None,
                float(last_bar.get("low")  or 0) or None,
                gates,
            ))
            match_id = cur.fetchone()[0]
            conn.commit()
        return match_id
    except Exception as exc:
        print(f"[0dte] write_match error: {exc}")
        return None


# ── Telegram alert ────────────────────────────────────────────────────────────

def _send_alert(tg_fn, ticker, side, row, expiry, sweep_usd, iv_rank) -> None:
    try:
        from zoneinfo import ZoneInfo
        ZI = ZoneInfo
    except ImportError:
        import pytz
        ZI = lambda tz: pytz.timezone(tz)
    ts  = datetime.now(ZI("America/New_York")).strftime("%H:%M ET")
    iv_str = f"{iv_rank:.0%}" if iv_rank is not None else "N/A (<5d hist)"
    msg = (
        f"\U0001f534 0DTE SWEEP \u2014 {ticker} {side.upper()}\n"
        f"Strike: {row['strike']}  Expiry: {expiry}\n"
        f"IV Rank: {iv_str}  Delta: {row['delta']:.2f}\n"
        f"Sweep $: ${sweep_usd:,.0f}\n"
        f"Vol/OI: {row['volume']}/{row['openInterest']}\n"
        f"Spread: {row['ask'] - row['bid']:.2f}  "
        f"(bid {row['bid']} / ask {row['ask']})\n"
        f"{ts}"
    )
    try:
        if callable(tg_fn):
            tg_fn(msg, signal_source="0dte_sweep", alert_class="SIGNAL", ticker=ticker)
        else:
            import urllib.request as _ulr, json as _jmod
            token   = "".join(os.environ.get("TELEGRAM_BOT_TOKEN", "").split())
            chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
            if token and chat_id:
                payload = _jmod.dumps({"chat_id": chat_id, "text": msg}).encode()
                req = _ulr.Request(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                )
                _ulr.urlopen(req, timeout=8)
    except Exception as exc:
        print(f"[0dte] telegram error: {exc}")


# ── Paper trading helpers ─────────────────────────────────────────────────────

def open_paper_trade(
    match_id: int,
    entry_price: float,
    contract_symbol: str,
    ticker: str,
    side: str,
    strike: float,
    expiry: str,
) -> None:
    """
    Log a new hypothetical paper trade from an alert match.

    entry_price: row["ask"] at alert time (the price a buyer would pay).
    Config source: _PAPER_PROFIT_TARGET_PCT, _PAPER_STOP_LOSS_PCT,
                   _PAPER_CONTRACTS (module-level constants, not hardcoded in logic).
    """
    if entry_price <= 0:
        print(f"[0dte_paper] skipping zero entry price for match_id={match_id}")
        return
    if not contract_symbol:
        print(f"[0dte_paper] skipping empty contract_symbol for match_id={match_id}")
        return
    try:
        with _db() as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO paper_0dte_trades
                    (match_id, contract_symbol, ticker, side, strike, expiry,
                     entry_price, contracts, profit_target_pct, stop_loss_pct, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'open')
            """, (
                match_id, contract_symbol, ticker, side, strike, expiry,
                round(entry_price, 4), _PAPER_CONTRACTS,
                _PAPER_PROFIT_TARGET_PCT, _PAPER_STOP_LOSS_PCT,
            ))
            conn.commit()
        print(
            f"[0dte_paper] OPEN trade match_id={match_id} {ticker} {side.upper()} "
            f"strike={strike} entry=${entry_price:.2f} "
            f"target=+{_PAPER_PROFIT_TARGET_PCT*100:.0f}% "
            f"stop=-{_PAPER_STOP_LOSS_PCT*100:.0f}%"
        )
    except Exception as exc:
        print(f"[0dte_paper] open_paper_trade error match_id={match_id}: {exc}")


def _close_trade(
    trade_id: int,
    exit_price: float,
    exit_reason: str,
    entry_price: float,
    contracts: int,
) -> None:
    """Write exit record and compute P&L for a paper trade."""
    pnl_pct = (exit_price - entry_price) / entry_price if entry_price > 0 else 0.0
    pnl_usd = (exit_price - entry_price) * contracts * 100  # 100 shares per contract
    win = pnl_usd > 0
    try:
        with _db() as conn, conn.cursor() as cur:
            cur.execute("""
                UPDATE paper_0dte_trades
                SET exit_price  = %s,
                    exit_reason = %s,
                    exit_time   = NOW(),
                    pnl_usd     = %s,
                    pnl_pct     = %s,
                    win         = %s,
                    status      = 'closed'
                WHERE trade_id = %s
                  AND status   = 'open'
            """, (
                round(exit_price, 4),
                exit_reason,
                round(pnl_usd, 2),
                round(pnl_pct, 6),
                win,
                trade_id,
            ))
            conn.commit()
        print(
            f"[0dte_paper] CLOSED trade_id={trade_id} reason={exit_reason} "
            f"exit=${exit_price:.2f} pnl_usd=${pnl_usd:+.2f} pnl_pct={pnl_pct:+.2%}"
        )
    except Exception as exc:
        print(f"[0dte_paper] _close_trade error trade_id={trade_id}: {exc}")


def monitor_open_trades() -> None:
    """
    Poll current option price for every open paper trade and close if
    profit target or stop loss is hit.

    Mechanism: separate 1-minute APScheduler job registered in main.py
               (id="zero_dte_paper_monitor"). NOT the 5-min scan cycle —
               the 5-min cadence is insufficient to catch intraday hits.
    Window guard: 9:30–15:40 ET, Mon–Fri only.
    """
    try:
        from zoneinfo import ZoneInfo
        ZI = ZoneInfo
    except ImportError:
        import pytz
        ZI = lambda tz: pytz.timezone(tz)
    now = datetime.now(ZI("America/New_York"))
    if now.weekday() >= 5:
        return
    hm = (now.hour, now.minute)
    if not ((9, 30) <= hm <= (15, 40)):
        return

    try:
        with _db() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT trade_id, contract_symbol, entry_price,
                       profit_target_pct, stop_loss_pct, contracts
                FROM paper_0dte_trades
                WHERE status = 'open'
            """)
            open_trades = cur.fetchall()
    except Exception as exc:
        print(f"[0dte_paper] monitor fetch_open error: {exc}")
        return

    if not open_trades:
        return

    for (trade_id, contract_symbol, entry_price,
         pt_pct, sl_pct, contracts) in open_trades:
        entry_price = float(entry_price)
        pt_pct      = float(pt_pct)
        sl_pct      = float(sl_pct)
        contracts   = int(contracts)

        current_price = _fetch_option_mid(contract_symbol)
        if current_price is None:
            continue  # price unavailable this cycle; try again next minute

        target_price = entry_price * (1.0 + pt_pct)
        stop_price   = entry_price * (1.0 - sl_pct)

        exit_reason = None
        if current_price >= target_price:
            exit_reason = "target"
        elif current_price <= stop_price:
            exit_reason = "stop"

        if exit_reason:
            _close_trade(trade_id, current_price, exit_reason, entry_price, contracts)


def close_eod_trades() -> None:
    """
    EOD closer: runs at 15:35 ET via CronTrigger in main.py
    (id="zero_dte_paper_eod").

    Any trade still open at 0DTE expiry is closed at current market value.
    If price is unavailable (option worthless / market closed), closes at $0.00
    with reason 'expired_worthless'. Never carries a 0DTE position overnight.
    """
    try:
        with _db() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT trade_id, contract_symbol, entry_price, contracts
                FROM paper_0dte_trades
                WHERE status = 'open'
            """)
            open_trades = cur.fetchall()
    except Exception as exc:
        print(f"[0dte_paper] close_eod fetch error: {exc}")
        return

    if not open_trades:
        print("[0dte_paper] EOD closer: no open trades to close")
        return

    print(f"[0dte_paper] EOD closer: closing {len(open_trades)} open trade(s)")
    for (trade_id, contract_symbol, entry_price, contracts) in open_trades:
        entry_price = float(entry_price)
        contracts   = int(contracts)
        current_price = _fetch_option_mid(contract_symbol)
        if current_price is None or current_price <= 0.0:
            current_price = 0.0
            reason = "expired_worthless"
        else:
            reason = "eod"
        _close_trade(trade_id, current_price, reason, entry_price, contracts)


# ── Main entry point ──────────────────────────────────────────────────────────

def scan_once(tg_fn=None) -> int:
    """
    Run one 0DTE scan cycle for SPY and SPX.

    Args:
        tg_fn: callable with same signature as main.py's _tg_send(), or None
               (module sends directly via Telegram env vars).

    Returns:
        Number of matches written to pattern_0dte_matches this cycle.
    """
    global _tables_ready
    if not _in_trading_window():
        return 0
    if not _tables_ready:
        ensure_tables()
        _tables_ready = True

    total_matches = 0

    for ticker in _TICKERS:
        spread_limit = _SPREAD_LIMIT[ticker]
        expiry = _today_expiry(ticker)
        if not expiry:
            print(f"[0dte] {ticker}: no 0DTE expiry available, skipping")
            continue

        calls, puts = _fetch_chain(ticker, expiry)
        if not calls and not puts:
            print(f"[0dte] {ticker}: empty chain for {expiry}, skipping")
            continue

        # 5-min bars for price confirmation (empty list is handled gracefully in gates)
        bars = _fetch_5min_bars(ticker)

        # Underlying price: from bars close (SPY) or direct quote (SPX, index)
        if bars:
            underlying_price = float(bars[-1].get("close") or 0)
        else:
            underlying_price = _fetch_underlying_price(ticker)

        # ATM IV: call with abs(delta) closest to 0.50, store daily snapshot
        atm_cands = [c for c in calls if c.get("delta") is not None]
        atm_iv = 0.0
        if atm_cands:
            atm_row = min(atm_cands, key=lambda r: abs(abs(r["delta"]) - 0.50))
            atm_iv  = atm_row["impliedVolatility"]
        _store_atm_iv(ticker, atm_iv)
        iv_rank = _compute_iv_rank(ticker, atm_iv) if atm_iv > 0 else None

        for side, contracts in (("call", calls), ("put", puts)):
            for row in contracts:
                key = (ticker, side, row["strike"], expiry)
                with _last_vol_lock:
                    prev_vol = _last_vol.get(key, 0)

                passed, gates, sweep_usd = _eval_contract(
                    ticker, side, row, expiry, bars,
                    underlying_price, spread_limit, iv_rank, prev_vol,
                )

                with _last_vol_lock:
                    _last_vol[key] = row["volume"]

                if passed:
                    match_id = _write_match(
                        ticker, side, row, expiry, underlying_price,
                        sweep_usd, iv_rank, gates, bars,
                    )
                    _send_alert(tg_fn, ticker, side, row, expiry, sweep_usd, iv_rank)

                    # Open a hypothetical paper trade for every alert match.
                    # entry_price = ask at alert time (what a buyer would pay).
                    if match_id is not None:
                        open_paper_trade(
                            match_id=match_id,
                            entry_price=row["ask"],
                            contract_symbol=row.get("contractSymbol", ""),
                            ticker=ticker,
                            side=side,
                            strike=row["strike"],
                            expiry=expiry,
                        )

                    total_matches += 1
                    print(
                        f"[0dte] MATCH {ticker} {side.upper()} "
                        f"strike={row['strike']} sweep=${sweep_usd:,.0f} "
                        f"iv_rank={iv_rank} gates={gates}"
                    )

    return total_matches
