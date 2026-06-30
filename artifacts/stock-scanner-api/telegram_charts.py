"""
telegram_charts.py
------------------
Shared, dependency-light helper for attaching small stock-chart images to
Telegram alerts across BOTH independent StockScanner AI processes:
  - aiem_autonomous.py  (root, 24/7 BlockingScheduler)
  - artifacts/stock-scanner-api/main.py  (Flask web process)

Design constraints (intentional):
  * Chart data comes ONLY from the `polygon_market_daily` DB table (daily
    OHLCV, already populated, ~2 years history). NEVER issues a live
    yfinance/Polygon/Tradier API call. This avoids re-triggering the
    9:30-9:45 AM market-open burst-saturation bug that was fixed earlier.
  * At most one Telegram `sendPhoto` per alert call, with up to
    DEFAULT_MAX_TICKERS panels in a single grid image (never one photo per
    ticker, never a media-group album) to respect Telegram rate limits.
  * Every public function swallows its own exceptions and returns a falsy
    value on failure. Callers' existing text-alert behavior must never be
    affected by a chart failure.
  * matplotlib is imported lazily (inside functions) with the headless
    'Agg' backend so a missing/broken charting dependency can never break
    process startup or any non-chart code path.
"""

import os
import re
import io
import json
import urllib.request

DEFAULT_MAX_TICKERS = 6
DEFAULT_LOOKBACK_DAYS = 45

_TICKER_TOKEN_RE = re.compile(r'\$?\b([A-Z]{1,5})\b')

_STOPWORDS = {
    "THE", "FOR", "AND", "NOW", "NEW", "TOP", "ALL", "ARE", "WAS", "WIN",
    "BUY", "SELL", "ETF", "USD", "AI", "ID", "OK", "NO", "UP", "ON", "IN",
    "AT", "TO", "OF", "IS", "IT", "BE", "AM", "PM", "ET", "EOD", "WR", "EV",
    "RVOL", "VWAP", "OI", "CI", "WATCH", "PICK", "PICKS", "STRONG", "ALERT",
    "AIEM", "SMS", "TG", "DB", "API", "USA", "GO", "VS", "PER", "OFF",
}


def extract_tickers(text_or_items, max_n=DEFAULT_MAX_TICKERS):
    """Pull a clean, de-duplicated, uppercase ticker list out of either a
    free-text string (regex scan, stopword-filtered) or an iterable of
    already-known ticker strings. Always returns a plain list[str]."""
    try:
        out = []
        if isinstance(text_or_items, (list, tuple, set)):
            for raw in text_or_items:
                t = str(raw).strip().upper().lstrip("$")
                if t and t not in _STOPWORDS and re.fullmatch(r"[A-Z]{1,5}", t):
                    if t not in out:
                        out.append(t)
        else:
            for m in _TICKER_TOKEN_RE.finditer(str(text_or_items or "")):
                t = m.group(1)
                if t in _STOPWORDS:
                    continue
                if t not in out:
                    out.append(t)
        return out[:max_n]
    except Exception as e:
        print(f"[telegram_charts] extract_tickers error: {e}")
        return []


def fetch_daily_bars(tickers, days=DEFAULT_LOOKBACK_DAYS):
    """Pull recent daily OHLCV bars for `tickers` from polygon_market_daily
    ONLY (no live API calls). Returns {ticker: [ {scan_date, open, high,
    low, close, volume}, ... ascending by date ] }. Tickers with no rows
    are simply absent from the result. Never raises."""
    tickers = [str(t).strip().upper() for t in (tickers or []) if t]
    if not tickers:
        return {}
    try:
        import psycopg2
    except Exception as e:
        print(f"[telegram_charts] psycopg2 unavailable: {e}")
        return {}

    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return {}

    # Pull a generous calendar-day buffer so weekends/holidays don't starve
    # the requested number of trading sessions; trimmed to `days` below.
    calendar_buffer_days = int(days) + 25

    out = {}
    try:
        with psycopg2.connect(dsn, connect_timeout=5,
                               options="-c statement_timeout=4000") as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT ticker, scan_date, open_price, high_price, low_price,
                       close_price, volume
                FROM polygon_market_daily
                WHERE ticker = ANY(%s)
                  AND scan_date >= CURRENT_DATE - (%s * INTERVAL '1 day')
                ORDER BY ticker, scan_date ASC
                """,
                (tickers, calendar_buffer_days),
            )
            rows = cur.fetchall()
    except Exception as e:
        print(f"[telegram_charts] fetch_daily_bars query error: {e}")
        return {}

    for ticker, scan_date, o, h, l, c, v in rows:
        if c is None:
            continue
        out.setdefault(ticker, []).append({
            "scan_date": scan_date,
            "open": o, "high": h, "low": l, "close": c, "volume": v,
        })

    # Trim each series to the most recent `days` trading sessions.
    for t in list(out.keys()):
        out[t] = out[t][-int(days):]
        if len(out[t]) < 2:
            del out[t]  # not enough data to draw a meaningful line

    return out


def render_chart_grid(bars_by_ticker, title="", max_tickers=DEFAULT_MAX_TICKERS):
    """Render a compact multi-panel PNG (close-price line + shaded daily
    high/low range, last price & % change annotated, green/red by
    direction) for up to `max_tickers` tickers. Returns PNG bytes, or None
    if there is nothing renderable. Never raises."""
    try:
        tickers = [t for t in bars_by_ticker.keys() if bars_by_ticker.get(t)][:max_tickers]
        if not tickers:
            return None

        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates

        n = len(tickers)
        cols = 3 if n > 3 else n
        rows = 2 if n > 3 else 1
        fig, axes = plt.subplots(rows, cols, figsize=(4.0 * cols, 2.6 * rows), dpi=130)
        if n == 1:
            axes = [axes]
        else:
            axes = list(axes.flat) if hasattr(axes, "flat") else list(axes)

        for i, ticker in enumerate(tickers):
            ax = axes[i]
            series = bars_by_ticker[ticker]
            dates = [r["scan_date"] for r in series]
            closes = [r["close"] for r in series]
            highs = [r["high"] if r["high"] is not None else r["close"] for r in series]
            lows = [r["low"] if r["low"] is not None else r["close"] for r in series]

            first_close = closes[0]
            last_close = closes[-1]
            pct_change = ((last_close - first_close) / first_close * 100.0) if first_close else 0.0
            color = "#1a9e4a" if pct_change >= 0 else "#c0392b"

            ax.fill_between(dates, lows, highs, color=color, alpha=0.15, linewidth=0)
            ax.plot(dates, closes, color=color, linewidth=1.6)
            ax.set_title(f"{ticker}  ${last_close:,.2f}  ({pct_change:+.1f}%)",
                         fontsize=10, color=color, fontweight="bold")
            ax.tick_params(axis="both", labelsize=6)
            ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=2, maxticks=4))
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
            for spine in ("top", "right"):
                ax.spines[spine].set_visible(False)

        # Hide any unused grid cells (e.g. 5 tickers in a 2x3 grid).
        for j in range(len(tickers), len(axes)):
            axes[j].axis("off")

        if title:
            # matplotlib's default font (DejaVu Sans) has no emoji glyphs, which
            # would render as missing-glyph boxes in the PNG; the Telegram
            # caption (set separately, full UTF-8) keeps the emoji intact.
            _ascii_title = title.encode("ascii", "ignore").decode("ascii").strip()
            if _ascii_title:
                fig.suptitle(_ascii_title, fontsize=11, fontweight="bold")
        fig.tight_layout(rect=(0, 0, 1, 0.94 if title else 1))

        buf = io.BytesIO()
        fig.savefig(buf, format="png")
        plt.close(fig)
        buf.seek(0)
        png_bytes = buf.read()
        return png_bytes if png_bytes else None
    except Exception as e:
        print(f"[telegram_charts] render_chart_grid error: {e}")
        try:
            import matplotlib.pyplot as plt
            plt.close("all")
        except Exception:
            pass
        return None


def send_telegram_photo(image_bytes, caption=""):
    """Send a single PNG to the configured Telegram owner chat via
    sendPhoto (multipart/form-data, no extra deps). Returns True on
    confirmed Telegram 'ok'. Never raises."""
    if not image_bytes:
        return False
    token = "".join(os.environ.get("TELEGRAM_BOT_TOKEN", "").split())
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False
    try:
        boundary = "----TGChartBoundary7d8f3a91"
        parts = []

        def field(name, value):
            parts.append(
                (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
                 f'{value}\r\n').encode("utf-8")
            )

        field("chat_id", chat_id)
        if caption:
            field("caption", caption[:1024])
        parts.append(
            (f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; '
             f'filename="chart.png"\r\nContent-Type: image/png\r\n\r\n').encode("utf-8")
        )
        parts.append(image_bytes)
        parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
        payload = b"".join(parts)

        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendPhoto",
            data=payload,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            return bool(json.loads(r.read()).get("ok", False))
    except Exception as e:
        print(f"[telegram_charts] sendPhoto error: {e}")
        return False


def send_ticker_chart_alert(kind, title, tickers, caption=None, max_tickers=DEFAULT_MAX_TICKERS):
    """High-level orchestration used by alert call sites:
    clean tickers -> DB bars -> chart PNG -> Telegram sendPhoto.

    `tickers` should already be a list of ticker symbol strings (callers
    that only have free text should call extract_tickers() first and pass
    the result in). Always returns True/False, never raises, and never
    touches/breaks the caller's existing text-alert flow.
    """
    try:
        clean = extract_tickers(tickers, max_n=max_tickers)
        if not clean:
            return False
        bars = fetch_daily_bars(clean)
        if not bars:
            return False
        png = render_chart_grid(bars, title=title, max_tickers=max_tickers)
        if not png:
            return False
        return send_telegram_photo(png, caption or title or "")
    except Exception as e:
        print(f"[telegram_charts] send_ticker_chart_alert error (kind={kind}): {e}")
        return False
