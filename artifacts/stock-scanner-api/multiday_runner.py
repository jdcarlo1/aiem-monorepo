"""
multiday_runner.py — Multi-Day Large-Cap Runner Scanner
========================================================
Detects 5-day continuation patterns on S&P 500 / large-cap stocks.

Backtest results (60 days, 130 large-cap tickers):
  • D1 ≥3% + D2 confirmed  → 59.7% win rate, +2.2% EV/trade
  • D1 ≥5% + D2 confirmed  → 69.6% win rate, +4.1% avg gain D2→D5

Two daily scans:
  • 4:05 PM ET  — Day 1 watch: find today's ≥3% ignitions, save to DB, email owner
  • 2:45 PM ET  — Day 2 confirm: check yesterday's watch list live,
                  flag confirmed entries (D2 price > D1 close + top-half of range),
                  email owner with BUY SIGNAL
"""

import os, math, traceback
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

_ET_TZ = ZoneInfo("America/New_York")

# ── Large-cap universe (S&P 500 / Nasdaq 100 focus) ──────────────────────────
LARGE_CAP_UNIVERSE = [
    # Mega-cap Tech
    "AAPL","MSFT","NVDA","AMZN","META","GOOGL","GOOG","TSLA","AMD","NFLX",
    "CRM","ORCL","ADBE","INTC","QCOM","TXN","MU","AVGO","AMAT","LRCX","KLAC",
    "MRVL","SNPS","CDNS","FTNT","ANSS","KEYS","JNPR","HPE","DELL","WDC","STX",
    "NTAP","PSTG","ANET","CSCO","AKAM","NOW","WDAY","INTU","PANW","CRWD",
    "ZS","DDOG","NET","SNOW","MDB","PLTR","APP","COIN","SOFI","AFRM","UPST",
    # Financials
    "JPM","BAC","GS","MS","WFC","C","AXP","BLK","SCHW","V","MA","PYPL",
    "COF","DFS","SYF","AIG","PRU","MET","AFL","ALL","TRV","CB","ICE","CME",
    "NDAQ","MSCI","SPGI","MCO","IBKR","BR","FITB","HBAN","RF","KEY","CFG",
    # Healthcare
    "JNJ","PFE","ABBV","LLY","BMY","MRK","AMGN","GILD","BIIB","REGN","VRTX",
    "ISRG","MDT","ABT","TMO","DHR","SYK","BSX","ZBH","EW","HCA","UNH","CVS",
    "CI","HUM","ELV","MOH","CNC","MCK","ABC","CAH","MRNA","IDXX","ILMN","IQV",
    "CRL","A","AVTR","SRPT","VKTX","RYTM","TDOC","TGTX","AGIO","NVCT",
    # Energy
    "XOM","CVX","COP","SLB","HAL","OXY","MPC","VLO","PSX","EOG","DVN",
    "FANG","APA","MRO","BKR","NEE","DUK","SO","AEP","EXC","PCG","SRE",
    "XEL","D","PPL","ETR","TLN","VST","NRG","AES","EIX","PEG","BE","GEV",
    # Consumer Discretionary
    "HD","LOW","TGT","COST","WMT","EBAY","ETSY","MCD","SBUX","YUM","CMG",
    "NKE","LULU","F","GM","RIVN","DIS","CMCSA","FOXA","REAL","CTRN","MOVE",
    # Consumer Staples
    "PG","KO","PEP","MO","PM","MDLZ","GIS","HRL","CL","CHD","EL","KVHI",
    # Industrials
    "BA","LMT","RTX","NOC","GD","TDG","HWM","GE","CAT","DE","PCAR","CMI",
    "ITW","EMR","ETN","ROK","AME","FTV","MMM","HON","JCI","TT","CARR","OTIS",
    "PH","IR","GWW","UPS","FDX","XPO","UNP","CSX","NSC","CP","WAB","AGX",
    "HUBB","MTRN","VSEC","ASTE","KNF","RELY","AVAH","BW","TH","TGTX","CXT",
    # Materials
    "APD","LIN","DOW","LYB","PPG","SHW","ECL","ALB","FCX","NEM","NUE",
    "STLD","RS","AA","ATI",
    # Real Estate / Misc
    "EQIX","AMT","PLD","SPG","PSA","WY","CBRE",
    # Additional from 5D gainer lists
    "WDC","MRNA","STX","GEV","TLN","COF","CAT","NRG","APH","EMR","NVST",
    "BBVA","BDL","CIFR","NBIS","RZLT","TRVI","PRCH","FBYD","MNPR","URGN",
    "CYTK","VSEC","ODTX","MIR","VST","RLAY","TGTX","COF","RYTM","TDOC",
    "BW","DB","IBKR","CXW","GE","RIOT","VKTX","SRPT","AGIO","AGX","VLRS",
]
LARGE_CAP_UNIVERSE = list(dict.fromkeys(LARGE_CAP_UNIVERSE))


def init_multiday_runner_tables():
    import psycopg2 as pg
    with pg.connect(os.environ["DATABASE_URL"]) as c, c.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS multiday_runner_watch (
                id           SERIAL PRIMARY KEY,
                d1_date      DATE NOT NULL,
                ticker       VARCHAR(10) NOT NULL,
                d1_pct       FLOAT,
                d1_close     FLOAT,
                d1_high      FLOAT,
                d1_low       FLOAT,
                d1_rvol      FLOAT,
                d1_vol       BIGINT,
                d1_strong    BOOLEAN DEFAULT FALSE,
                status       VARCHAR(16) DEFAULT 'watch',
                d2_date      DATE,
                d2_pct       FLOAT,
                d2_close     FLOAT,
                d2_close_pos FLOAT,
                d2_above_d1  BOOLEAN,
                confirmed    BOOLEAN DEFAULT FALSE,
                entry_price  FLOAT,
                stop_price   FLOAT,
                exit_price   FLOAT,
                exit_date    DATE,
                exit_pct     FLOAT,
                hold_days    INT,
                exit_reason  VARCHAR(24),
                captured_at  TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE (d1_date, ticker)
            )
        """)
        c.commit()
    print("[multiday_runner] tables ready")


def _today_et() -> date:
    return datetime.now(_ET_TZ).date()


def run_day1_scan() -> list:
    """
    4:05 PM ET: scan LARGE_CAP_UNIVERSE for today's ≥3% ignition.
    Saves new entries to multiday_runner_watch with status='watch'.
    Returns list of row dicts.
    """
    import yfinance as yf
    import pandas as pd
    import psycopg2 as pg

    today = _today_et()
    rows_saved = []

    try:
        data = yf.download(
            LARGE_CAP_UNIVERSE,
            period="6d", interval="1d",
            group_by="ticker", auto_adjust=True, progress=False,
        )
    except Exception as e:
        print(f"[multiday_runner] day1 download error: {e}")
        return []

    for ticker in LARGE_CAP_UNIVERSE:
        try:
            df = data[ticker].dropna() if len(LARGE_CAP_UNIVERSE) > 1 else data
            if len(df) < 2:
                continue

            closes  = df["Close"].values.astype(float)
            volumes = df["Volume"].values.astype(float)
            highs   = df["High"].values.astype(float)
            lows    = df["Low"].values.astype(float)
            dates   = df.index

            d0c = closes[-2]
            d1c = closes[-1]
            d1_pct = (d1c - d0c) / d0c * 100
            if d1_pct < 3.0:
                continue

            avg_vol = float(pd.Series(volumes[:-1]).mean()) if len(volumes) > 1 else float(volumes[-1])
            d1_rvol = float(volumes[-1]) / avg_vol if avg_vol > 0 else 1.0
            d1_strong = d1_pct >= 5.0

            row = {
                "d1_date":   today,
                "ticker":    ticker,
                "d1_pct":    round(d1_pct, 2),
                "d1_close":  round(d1c, 4),
                "d1_high":   round(float(highs[-1]), 4),
                "d1_low":    round(float(lows[-1]), 4),
                "d1_rvol":   round(d1_rvol, 2),
                "d1_vol":    int(volumes[-1]),
                "d1_strong": d1_strong,
                "status":    "watch",
            }

            with pg.connect(os.environ["DATABASE_URL"]) as c, c.cursor() as cur:
                cur.execute("""
                    INSERT INTO multiday_runner_watch
                      (d1_date, ticker, d1_pct, d1_close, d1_high, d1_low,
                       d1_rvol, d1_vol, d1_strong, status)
                    VALUES (%(d1_date)s, %(ticker)s, %(d1_pct)s, %(d1_close)s,
                            %(d1_high)s, %(d1_low)s, %(d1_rvol)s, %(d1_vol)s,
                            %(d1_strong)s, %(status)s)
                    ON CONFLICT (d1_date, ticker) DO UPDATE SET
                      d1_pct    = EXCLUDED.d1_pct,
                      d1_close  = EXCLUDED.d1_close,
                      d1_high   = EXCLUDED.d1_high,
                      d1_low    = EXCLUDED.d1_low,
                      d1_rvol   = EXCLUDED.d1_rvol,
                      d1_vol    = EXCLUDED.d1_vol,
                      d1_strong = EXCLUDED.d1_strong
                """, row)
                c.commit()

            rows_saved.append(row)
        except Exception as e:
            print(f"[multiday_runner] day1 ticker {ticker} error: {e}")

    print(f"[multiday_runner] day1 scan → {len(rows_saved)} ignitions saved for {today}")
    return sorted(rows_saved, key=lambda r: r["d1_pct"], reverse=True)


def run_day2_confirm_scan() -> list:
    """
    2:45 PM ET: load yesterday's watch list, check live intraday prices.
    Confirm if: current price > D1 close  AND  close position >= 50% of day's range.
    Updates DB status='confirmed'. Returns confirmed list.
    """
    import yfinance as yf
    import psycopg2 as pg

    today = _today_et()
    yesterday = today - timedelta(days=1)
    # Walk back through weekends
    for _ in range(5):
        if yesterday.weekday() < 5:
            break
        yesterday -= timedelta(days=1)

    confirmed = []

    with pg.connect(os.environ["DATABASE_URL"]) as c, c.cursor() as cur:
        cur.execute("""
            SELECT id, ticker, d1_close, d1_pct, d1_strong
            FROM multiday_runner_watch
            WHERE d1_date = %s AND status = 'watch'
        """, (yesterday,))
        rows = cur.fetchall()

    if not rows:
        print(f"[multiday_runner] day2 confirm: no watch entries from {yesterday}")
        return []

    tickers = [r[1] for r in rows]
    print(f"[multiday_runner] day2 checking {len(tickers)} watch entries from {yesterday}")

    try:
        live = yf.download(
            tickers, period="1d", interval="5m",
            group_by="ticker", auto_adjust=True, progress=False,
        )
    except Exception as e:
        print(f"[multiday_runner] day2 download error: {e}")
        return []

    for (row_id, ticker, d1_close, d1_pct, d1_strong) in rows:
        try:
            df = live[ticker].dropna() if len(tickers) > 1 else live
            if df is None or len(df) == 0:
                continue

            current  = float(df["Close"].iloc[-1])
            day_high = float(df["High"].max())
            day_low  = float(df["Low"].min())

            above_d1   = current > d1_close
            day_range  = day_high - day_low
            close_pos  = (current - day_low) / day_range if day_range > 0 else 0.5
            top_half   = close_pos >= 0.5
            is_confirm = above_d1 and top_half

            d2_pct = (current - d1_close) / d1_close * 100

            with pg.connect(os.environ["DATABASE_URL"]) as c, c.cursor() as cur:
                cur.execute("""
                    UPDATE multiday_runner_watch SET
                      d2_date      = %s,
                      d2_pct       = %s,
                      d2_close     = %s,
                      d2_close_pos = %s,
                      d2_above_d1  = %s,
                      confirmed    = %s,
                      status       = %s,
                      entry_price  = %s,
                      stop_price   = %s
                    WHERE id = %s
                """, (
                    today,
                    round(d2_pct, 2),
                    round(current, 4),
                    round(close_pos, 3),
                    above_d1,
                    is_confirm,
                    "confirmed" if is_confirm else "rejected",
                    round(current, 4) if is_confirm else None,
                    round(d1_close * 0.98, 4) if is_confirm else None,  # stop = 2% below D1 close
                    row_id,
                ))
                c.commit()

            if is_confirm:
                confirmed.append({
                    "ticker":      ticker,
                    "d1_date":     str(yesterday),
                    "d1_pct":      round(d1_pct, 2),
                    "d1_strong":   d1_strong,
                    "d2_pct":      round(d2_pct, 2),
                    "current":     round(current, 2),
                    "entry_price": round(current, 2),
                    "stop_price":  round(d1_close * 0.98, 2),
                    "close_pos":   round(close_pos * 100, 0),
                })

        except Exception as e:
            print(f"[multiday_runner] day2 confirm {ticker} error: {e}")

    print(f"[multiday_runner] day2 confirm → {len(confirmed)}/{len(rows)} confirmed")
    return sorted(confirmed, key=lambda r: r.get("d1_pct", 0), reverse=True)


def get_multiday_runners_data() -> dict:
    """API endpoint: returns watch / confirmed / active / stats."""
    import psycopg2 as pg
    import psycopg2.extras

    today = _today_et()

    with pg.connect(os.environ["DATABASE_URL"]) as c:
        with c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Today's Day 1 watch list
            cur.execute("""
                SELECT ticker, d1_date, d1_pct, d1_close, d1_rvol, d1_strong, status
                FROM multiday_runner_watch
                WHERE d1_date = %s AND status = 'watch'
                ORDER BY d1_pct DESC
            """, (today,))
            watch = [dict(r) for r in cur.fetchall()]

            # Confirmed entries (last 2 days)
            cur.execute("""
                SELECT ticker, d1_date, d2_date, d1_pct, d2_pct, d1_strong,
                       entry_price, stop_price, d2_close_pos, status
                FROM multiday_runner_watch
                WHERE confirmed = TRUE
                  AND d2_date >= %s
                ORDER BY d1_pct DESC
            """, (today - timedelta(days=2),))
            confirmed = [dict(r) for r in cur.fetchall()]

            # Active holds (confirmed, no exit yet, within last 7 days)
            cur.execute("""
                SELECT ticker, d1_date, d2_date, d1_pct, d2_pct, entry_price,
                       stop_price, status, confirmed
                FROM multiday_runner_watch
                WHERE confirmed = TRUE
                  AND exit_date IS NULL
                  AND status NOT IN ('exited', 'rejected', 'watch')
                  AND d2_date >= %s
                ORDER BY d2_date DESC, d1_pct DESC
            """, (today - timedelta(days=7),))
            active = [dict(r) for r in cur.fetchall()]

            # Track record: last 60 days of exited positions
            cur.execute("""
                SELECT
                    COUNT(*) FILTER (WHERE confirmed = TRUE)             AS total_confirmed,
                    COUNT(*) FILTER (WHERE confirmed AND exit_pct > 0)   AS wins,
                    COUNT(*) FILTER (WHERE confirmed AND exit_pct <= 0)  AS losses,
                    ROUND(AVG(exit_pct) FILTER (WHERE confirmed)::numeric, 2) AS avg_gain,
                    ROUND(MAX(exit_pct) FILTER (WHERE confirmed)::numeric, 2) AS best_gain,
                    ROUND(MIN(exit_pct) FILTER (WHERE confirmed)::numeric, 2) AS worst_loss
                FROM multiday_runner_watch
                WHERE d1_date >= %s
            """, (today - timedelta(days=60),))
            row = cur.fetchone()
            stats = dict(row) if row else {}

    # Serialize dates
    def _ser(lst):
        out = []
        for r in lst:
            d = {}
            for k, v in r.items():
                d[k] = str(v) if isinstance(v, date) else v
            out.append(d)
        return out

    return {
        "watch":     _ser(watch),
        "confirmed": _ser(confirmed),
        "active":    _ser(active),
        "stats":     stats,
        "as_of":     datetime.now(_ET_TZ).strftime("%Y-%m-%d %H:%M ET"),
    }


def build_day1_email_html(rows: list) -> str:
    """Email HTML for the 4:05 PM Day 1 watch list."""
    if not rows:
        return ""

    strong = [r for r in rows if r.get("d1_strong")]
    normal = [r for r in rows if not r.get("d1_strong")]

    def _row(r):
        badge = '<span style="background:#f59e0b;color:#000;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:700;">STRONG ≥5%</span>' if r.get("d1_strong") else ''
        return f"""
        <tr style="border-bottom:1px solid rgba(255,255,255,0.06)">
          <td style="padding:10px 8px;font-weight:800;font-size:18px;color:#fff">{r['ticker']}</td>
          <td style="padding:10px 8px;color:#22c55e;font-weight:700;font-size:16px">+{r['d1_pct']:.1f}%</td>
          <td style="padding:10px 8px;color:#94a3b8">${r['d1_close']:.2f}</td>
          <td style="padding:10px 8px;color:#64748b">{r['d1_rvol']:.1f}x</td>
          <td style="padding:10px 8px">{badge}</td>
        </tr>"""

    rows_html = "".join(_row(r) for r in rows[:20])

    return f"""<!DOCTYPE html><html><body style="background:#0f1117;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#fff;margin:0;padding:0">
<div style="max-width:600px;margin:0 auto;padding:32px 24px">
  <div style="text-align:center;margin-bottom:24px">
    <div style="display:inline-block;background:#22c55e;color:#000;padding:6px 18px;border-radius:6px;font-size:11px;font-weight:800;letter-spacing:.1em">STOCKSCANNER AI</div>
    <h1 style="font-size:26px;font-weight:900;margin:12px 0 4px;letter-spacing:-.02em">Day 1 Watch List</h1>
    <p style="color:#64748b;font-size:13px;margin:0">Large-cap ignitions today · Watch for Day 2 confirmation tomorrow</p>
  </div>

  <div style="background:rgba(34,197,94,0.07);border:1px solid rgba(34,197,94,0.2);border-radius:12px;padding:16px 20px;margin-bottom:20px">
    <p style="margin:0;font-size:14px;color:#94a3b8;line-height:1.6">
      <strong style="color:#22c55e">{len(rows)} large-cap stocks</strong> gained ≥3% today.
      {f'<strong style="color:#f59e0b">{len(strong)} are STRONG ignitions (≥5%)</strong> — historically 69.6% win rate on Day 3–5.' if strong else ''}
      Tomorrow at <strong style="color:#fff">2:45 PM ET</strong> you'll receive the confirmed BUY list.
      <br><br>
      <strong style="color:#fff">The Day 2 rule:</strong> If the stock is still above today's close AND sitting in the top half of tomorrow's range at 2:45 PM — that's the entry.
    </p>
  </div>

  <table style="width:100%;border-collapse:collapse">
    <thead>
      <tr style="border-bottom:1px solid rgba(255,255,255,0.12)">
        <th style="padding:8px;text-align:left;font-size:11px;color:#64748b;letter-spacing:.08em">TICKER</th>
        <th style="padding:8px;text-align:left;font-size:11px;color:#64748b;letter-spacing:.08em">D1 GAIN</th>
        <th style="padding:8px;text-align:left;font-size:11px;color:#64748b;letter-spacing:.08em">CLOSE</th>
        <th style="padding:8px;text-align:left;font-size:11px;color:#64748b;letter-spacing:.08em">RVOL</th>
        <th style="padding:8px;text-align:left;font-size:11px;color:#64748b;letter-spacing:.08em"></th>
      </tr>
    </thead>
    <tbody>{rows_html}</tbody>
  </table>

  <div style="margin-top:24px;padding:16px;background:rgba(255,255,255,0.04);border-radius:8px">
    <p style="margin:0;font-size:12px;color:#475569;line-height:1.7">
      <strong style="color:#94a3b8">Backtest edge (60 days, 130 large-caps):</strong><br>
      D1 ≥3% + Day 2 confirmed → <strong style="color:#22c55e">59.7% win rate, +2.2% EV</strong><br>
      D1 ≥5% + Day 2 confirmed → <strong style="color:#f59e0b">69.6% win rate, +4.1% avg gain D2→D5</strong>
    </p>
  </div>
</div></body></html>"""


def build_day2_email_html(confirmed: list) -> str:
    """Email HTML for the 2:45 PM Day 2 BUY SIGNAL alert."""
    if not confirmed:
        return ""

    def _row(r):
        strong_badge = '<span style="background:#f59e0b;color:#000;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:700;">STRONG</span> ' if r.get("d1_strong") else ''
        return f"""
        <tr style="border-bottom:1px solid rgba(255,255,255,0.06)">
          <td style="padding:12px 8px">
            <div style="font-weight:900;font-size:20px;color:#fff">{r['ticker']}</div>
            <div style="font-size:11px;color:#64748b">D1: +{r['d1_pct']:.1f}%</div>
          </td>
          <td style="padding:12px 8px;text-align:center">
            <div style="color:#22c55e;font-weight:700;font-size:16px">+{r['d2_pct']:.1f}%</div>
            <div style="font-size:11px;color:#64748b">D2 so far</div>
          </td>
          <td style="padding:12px 8px;text-align:center">
            <div style="color:#fff;font-weight:700;font-size:16px">${r['entry_price']:.2f}</div>
            <div style="font-size:11px;color:#64748b">entry</div>
          </td>
          <td style="padding:12px 8px;text-align:center">
            <div style="color:#f87171;font-weight:700;font-size:14px">${r['stop_price']:.2f}</div>
            <div style="font-size:11px;color:#64748b">stop</div>
          </td>
          <td style="padding:12px 8px;text-align:center">
            <div style="color:#94a3b8;font-size:13px">{int(r.get('close_pos', 0))}%</div>
            <div style="font-size:11px;color:#64748b">of range</div>
          </td>
          <td style="padding:12px 8px">{strong_badge}</td>
        </tr>"""

    rows_html = "".join(_row(r) for r in confirmed)

    return f"""<!DOCTYPE html><html><body style="background:#0f1117;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#fff;margin:0;padding:0">
<div style="max-width:620px;margin:0 auto;padding:32px 24px">
  <div style="text-align:center;margin-bottom:24px">
    <div style="display:inline-block;background:#22c55e;color:#000;padding:6px 18px;border-radius:6px;font-size:11px;font-weight:800;letter-spacing:.1em">🟢 BUY SIGNAL</div>
    <h1 style="font-size:28px;font-weight:900;margin:12px 0 4px;letter-spacing:-.02em">Day 2 Confirmed</h1>
    <p style="color:#64748b;font-size:13px;margin:0">Enter before 3:45 PM ET · Hold through Day 5</p>
  </div>

  <div style="background:rgba(34,197,94,0.1);border:1.5px solid rgba(34,197,94,0.3);border-radius:12px;padding:16px 20px;margin-bottom:24px">
    <p style="margin:0;font-size:14px;color:#94a3b8;line-height:1.6">
      <strong style="color:#22c55e">{len(confirmed)} stock{'s' if len(confirmed)!=1 else ''}</strong> confirmed the Day 2 pattern right now:
      trading <strong style="color:#fff">above yesterday's close</strong> and sitting in the
      <strong style="color:#fff">top half of today's range</strong> at 2:45 PM.
      <br><br>
      <strong style="color:#fff">Enter at market before 3:45 PM ET.</strong>
      Stop = 2% below yesterday's close. Historical target: hold through Day 5.
    </p>
  </div>

  <table style="width:100%;border-collapse:collapse">
    <thead>
      <tr style="border-bottom:1px solid rgba(255,255,255,0.12)">
        <th style="padding:8px;text-align:left;font-size:11px;color:#64748b;letter-spacing:.08em">TICKER</th>
        <th style="padding:8px;text-align:center;font-size:11px;color:#64748b;letter-spacing:.08em">D2 GAIN</th>
        <th style="padding:8px;text-align:center;font-size:11px;color:#64748b;letter-spacing:.08em">ENTRY</th>
        <th style="padding:8px;text-align:center;font-size:11px;color:#64748b;letter-spacing:.08em">STOP</th>
        <th style="padding:8px;text-align:center;font-size:11px;color:#64748b;letter-spacing:.08em">RANGE</th>
        <th style="padding:8px;font-size:11px;color:#64748b"></th>
      </tr>
    </thead>
    <tbody>{rows_html}</tbody>
  </table>

  <div style="margin-top:24px;padding:16px;background:rgba(255,255,255,0.04);border-radius:8px">
    <p style="margin:0;font-size:12px;color:#475569;line-height:1.7">
      <strong style="color:#94a3b8">60-day large-cap backtest:</strong><br>
      D1 ≥3% + D2 confirmed → <strong style="color:#22c55e">59.7% win, +2.2% EV/trade (D2→D5)</strong><br>
      D1 ≥5% + D2 confirmed → <strong style="color:#f59e0b">69.6% win, +4.1% avg gain</strong><br>
      This is NOT a guarantee. Use the stop and size accordingly.
    </p>
  </div>
</div></body></html>"""
