"""
EOD Swing Setup Scanner — runs at 2:00 PM ET Mon-Fri.
Scan takes ~20-30 min, so SMS lands ~2:20-2:30 PM — 90 min to analyze before close.

Looks for the 'quiet accumulation' pattern:
  1. Closed near top of day's range (80%+) — buyers still in at close
  2. Had a high-volume surge (RVOL ≥ 2x) somewhere in last 5 days
  3. Multi-day upward momentum (5-day gain ≥ 5%)
  4. Tight pullbacks — no big red days wiping out the trend
  5. Bullish options bias (more calls than puts)
  6. Above 20-day moving average

Threshold: score ≥ 60 to make the email / SMS.
"""

import os
import math
import requests as _req
import psycopg2
from datetime import datetime, date
import pytz

_ET = pytz.timezone("US/Eastern")


def _conn():
    return psycopg2.connect(os.environ["DATABASE_URL"])


# ── Universe ─────────────────────────────────────────────────────────────────

def _barchart_universe(min_pct: float = 2.0) -> list[str]:
    """Pull today's top movers from all four Barchart cap tiers."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept":     "application/json",
        "Referer":    "https://www.barchart.com/stocks/advances",
    }
    syms = []
    for tier in ("stocks.advances.microcap.us", "stocks.advances.smallcap.us",
                 "stocks.advances.midcap.us",   "stocks.advances.largecap.us"):
        try:
            url = (
                "https://www.barchart.com/proxies/core-api/v1/quotes/get"
                f"?fields=symbol%2CpercentChange%2Cvolume%2CaverageVolume&"
                f"list={tier}&orderBy=percentChange&orderDir=desc&raw=1&limit=100"
            )
            r = _req.get(url, headers=headers, timeout=8)
            if r.ok:
                for row in r.json().get("data", []):
                    sym = (row.get("symbol") or "").strip().upper()
                    pct = float(row.get("percentChange") or 0)
                    if sym and len(sym) <= 5 and "." not in sym and pct >= min_pct:
                        syms.append(sym)
        except Exception:
            pass
    return list(dict.fromkeys(syms))  # dedup, preserve order


# ── Per-stock scoring ─────────────────────────────────────────────────────────

def _score_swing(ticker: str) -> dict | None:
    """
    Return a scored swing-setup dict or None if the stock doesn't qualify.
    """
    try:
        import yfinance as _yf
        tk = _yf.Ticker(ticker)

        # 30 days daily — we use last 6 (today + 5 prior)
        daily = tk.history(period="30d", interval="1d")
        if len(daily) < 4:
            return None

        today   = daily.iloc[-1]
        prior3  = daily.iloc[-4:-1]   # 3 trading days before today
        rolling20 = daily["Close"].iloc[-21:-1]  # 20 prior closes for MA
        avg_vol20 = daily["Volume"].iloc[-21:-1].mean()

        # ── Quick pre-filter ──────────────────────────────────────────────────
        # Must be up today
        prev_close = float(daily["Close"].iloc[-2])
        today_chg  = (float(today["Close"]) - prev_close) / prev_close * 100
        if today_chg < 0:
            return None

        # Price must be > $2 (no pennies)
        if float(today["Close"]) < 2.0:
            return None

        # ── Indicators ───────────────────────────────────────────────────────
        close   = float(today["Close"])
        high    = float(today["High"])
        low     = float(today["Low"])
        vol     = float(today["Volume"])
        rng     = high - low

        # Close position in today's range (0–100%)
        close_pct_range = (close - low) / rng * 100 if rng > 0 else 50

        # RVOL today
        rvol_today = vol / avg_vol20 if avg_vol20 > 0 else 0

        # Best RVOL in last 3 prior days
        best_prior_rvol = 0.0
        for _, r in prior3.iterrows():
            rv = float(r["Volume"]) / avg_vol20 if avg_vol20 > 0 else 0
            if rv > best_prior_rvol:
                best_prior_rvol = rv

        best_rvol = max(rvol_today, best_prior_rvol)

        # 3-day momentum: gain from 3 days ago close to today
        three_day_ago_close = float(prior3.iloc[0]["Close"])
        momentum_3d = (close - three_day_ago_close) / three_day_ago_close * 100

        # Pullback quality: look at each prior day's change
        max_down_day = 0.0
        for i in range(1, len(prior3)):
            day_chg = (float(prior3.iloc[i]["Close"]) - float(prior3.iloc[i-1]["Close"])) / float(prior3.iloc[i-1]["Close"]) * 100
            if day_chg < 0:
                max_down_day = max(max_down_day, abs(day_chg))

        # 20-day MA
        ma20 = float(rolling20.mean()) if len(rolling20) >= 10 else None
        above_ma20 = close > ma20 if ma20 else False

        # Options PCR
        pcr = None
        has_options = False
        try:
            opts_list = tk.options
            if opts_list:
                has_options = True
                exp = opts_list[0]
                chain = tk.option_chain(exp)
                call_vol = float(chain.calls["volume"].sum())
                put_vol  = float(chain.puts["volume"].sum())
                if call_vol > 0:
                    pcr = put_vol / call_vol
        except Exception:
            pass

        # ── Scoring (100 pts) ─────────────────────────────────────────────────
        score = 0
        signals = []

        # 1. Close position in range (25 pts)
        if   close_pct_range >= 90: score += 25; signals.append(f"Closed at {close_pct_range:.0f}% of range 🔒")
        elif close_pct_range >= 80: score += 20; signals.append(f"Closed at {close_pct_range:.0f}% of range")
        elif close_pct_range >= 70: score += 12; signals.append(f"Closed at {close_pct_range:.0f}% of range")
        elif close_pct_range >= 60: score += 6

        # 2. Best RVOL over the week (25 pts)
        if   best_rvol >= 3.0: score += 25; signals.append(f"Peak RVOL {best_rvol:.1f}x 🔥")
        elif best_rvol >= 2.0: score += 18; signals.append(f"Peak RVOL {best_rvol:.1f}x")
        elif best_rvol >= 1.5: score += 10; signals.append(f"RVOL {best_rvol:.1f}x")
        # If best RVOL < 1.5x (like VECO's 1.34x) score 0 here — but can still qualify via other signals

        # 3. 3-day momentum (20 pts)
        if   momentum_3d >= 20: score += 20; signals.append(f"+{momentum_3d:.1f}% in 3 days 🚀")
        elif momentum_3d >= 15: score += 16; signals.append(f"+{momentum_3d:.1f}% in 3 days")
        elif momentum_3d >= 10: score += 12; signals.append(f"+{momentum_3d:.1f}% in 3 days")
        elif momentum_3d >= 7:  score += 8;  signals.append(f"+{momentum_3d:.1f}% in 3 days")
        elif momentum_3d >= 5:  score += 5;  signals.append(f"+{momentum_3d:.1f}% in 3 days")
        elif momentum_3d >= 3:  score += 3

        # 4. Pullback quality (15 pts)
        if   max_down_day == 0:    score += 15; signals.append("All green this week ✅")
        elif max_down_day <  2.0:  score += 12; signals.append(f"Tightest pullback {max_down_day:.1f}% — healthy")
        elif max_down_day <  4.0:  score += 8;  signals.append(f"Max pullback {max_down_day:.1f}%")
        elif max_down_day <  6.0:  score += 4
        # ≥ 6% = 0 pts

        # 5. Options PCR (10 pts)
        if pcr is not None:
            if   pcr < 0.3:  score += 10; signals.append(f"PCR {pcr:.2f} — very bullish 🐂")
            elif pcr < 0.5:  score += 7;  signals.append(f"PCR {pcr:.2f} — bullish")
            elif pcr < 0.7:  score += 4;  signals.append(f"PCR {pcr:.2f}")

        # 6. Above 20-day MA (5 pts)
        if above_ma20:
            score += 5
            signals.append(f"Above 20d MA ${ma20:.2f} ✅")

        # ── Must-have gates ────────────────────────────────────────────────────
        # At minimum: close in top 70% of range AND at least some momentum
        if close_pct_range < 60:
            return None
        if momentum_3d < 3.0:
            return None

        if score < 60:
            return None

        # ── Build result ──────────────────────────────────────────────────────
        # Estimate stop: low of the last 3 days
        recent_lows = [float(daily.iloc[-i]["Low"]) for i in range(1, 4)]
        stop = min(recent_lows)

        # Label
        if   score >= 85: label = "🔥🔥 Extremely Bullish"
        elif score >= 75: label = "🔥 Very Bullish"
        elif score >= 65: label = "📈 Bullish"
        else:             label = "↗️ Developing"

        return {
            "ticker":           ticker,
            "score":            score,
            "label":            label,
            "price":            round(close, 2),
            "today_chg":        round(today_chg, 2),
            "momentum_3d":      round(momentum_3d, 2),
            "close_pct_range":  round(close_pct_range, 1),
            "best_rvol":        round(best_rvol, 2),
            "rvol_today":       round(rvol_today, 2),
            "max_down_day":     round(max_down_day, 2),
            "ma20":             round(ma20, 2) if ma20 else None,
            "above_ma20":       above_ma20,
            "pcr":              round(pcr, 2) if pcr is not None else None,
            "has_options":      has_options,
            "stop":             round(stop, 2),
            "signals":          signals[:4],   # top 4 signals for email card
        }

    except Exception as e:
        print(f"[eod_swing] score error {ticker}: {e}")
        return None


# ── Cross-reference intraday SMS log ─────────────────────────────────────────

def _get_today_intraday_alerts() -> set:
    """Return set of tickers that fired the intraday SMS scanner today."""
    try:
        with _conn() as con:
            with con.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT ticker FROM sms_alerts_log
                    WHERE alert_date = CURRENT_DATE
                """)
                return {row[0] for row in cur.fetchall()}
    except Exception as e:
        print(f"[eod_swing] intraday log lookup error: {e}")
        return set()


# ── Main scan ─────────────────────────────────────────────────────────────────

def run_eod_swing_scan(max_tickers: int = 200) -> list[dict]:
    """
    Scan Barchart universe EOD and return top swing setups sorted by score.
    Double-signal flag: fired intraday SMS today AND qualifies for swing setup.
    """
    now = datetime.now(_ET)
    if now.weekday() >= 5:
        return []

    print("[eod_swing] starting EOD swing scan…")
    universe = _barchart_universe(min_pct=2.0)[:max_tickers]
    print(f"[eod_swing] universe: {len(universe)} tickers")

    # Pull today's intraday alerts once (before threading)
    intraday_alerts = _get_today_intraday_alerts()
    print(f"[eod_swing] {len(intraday_alerts)} intraday alerts today for cross-ref")

    from concurrent.futures import ThreadPoolExecutor, as_completed
    results = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(_score_swing, t): t for t in universe}
        for fut in as_completed(futs):
            res = fut.result()
            if res:
                # Flag double signals — fired intraday AND qualifies for swing
                res["double_signal"] = res["ticker"] in intraday_alerts
                results.append(res)

    # Sort: double signals first, then by score
    results.sort(key=lambda x: (x["double_signal"], x["score"]), reverse=True)
    top = results[:10]
    doubles = sum(1 for r in top if r["double_signal"])
    print(f"[eod_swing] {len(results)} qualified → top {len(top)} ({doubles} double signals)")
    return top


# ── SMS alert for top picks ───────────────────────────────────────────────────

def send_swing_sms(picks: list[dict]) -> None:
    """Send all qualifying swing setups in one text."""
    if not picks:
        return

    lines = [f"🌙 PRE-CLOSE SWINGS ({len(picks)})"]
    for p in picks:
        pcr_str = f"PCR {p['pcr']}" if p["pcr"] is not None else ""
        pcr_part = f" | {pcr_str}" if pcr_str else ""
        double_tag = " ❤️" if p.get("double_signal") else ""
        lines.append(
            f"{p['ticker']} ${p['price']} +{p['today_chg']}% "
            f"| Scr {p['score']} | 3d +{p['momentum_3d']}%{pcr_part}{double_tag}"
        )
        if p.get("double_signal"):
            lines.append("  → Double signal. Could be the start of a 5-day stretch.")
    lines.append("Exit: next-day close. Stop: below 3d low.")
    body = "\n".join(lines)

    # Personal alert (email only)
    try:
        from email_alerts import send_email_raw as _ser, smtp_configured as _smc
        if _smc():
            _ser("joeldcarlo@gmail.com", f"🌙 Pre-Close Swings ({len(picks)})", f"<pre>{body}</pre>")
    except Exception as e:
        print(f"[eod_swing] email error: {e}")


# ── Email builder ─────────────────────────────────────────────────────────────

def _swing_card_html(p: dict, rank: int) -> str:
    score = p["score"]
    if score >= 85: score_color = "#f97316"
    elif score >= 75: score_color = "#22c55e"
    elif score >= 65: score_color = "#06b6d4"
    else:             score_color = "#f59e0b"

    medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, f"#{rank}")

    signals_html = "".join(
        f'<div style="font-size:11px;color:#94a3b8;line-height:1.7;">• {s}</div>'
        for s in p.get("signals", [])
    )

    pcr_badge = ""
    if p.get("pcr") is not None:
        pcr_color = "#22c55e" if p["pcr"] < 0.5 else "#f59e0b"
        pcr_badge = (
            f'<span style="background:{pcr_color}22;color:{pcr_color};font-size:10px;'
            f'font-weight:700;padding:2px 8px;border-radius:20px;border:1px solid {pcr_color}44;margin-left:6px;">'
            f'PCR {p["pcr"]}</span>'
        )

    return f"""
    <tr>
      <td style="padding:0 0 14px 0;">
        <table width="100%" cellpadding="0" cellspacing="0"
               style="background:#0f172a;border-radius:10px;border:1px solid #1e293b;overflow:hidden;">
          <tr>
            <td style="padding:14px 16px;vertical-align:top;width:55%;">
              <div style="font-size:11px;color:#64748b;font-weight:600;margin-bottom:4px;letter-spacing:.05em;">
                {medal} SWING SETUP #{rank}
              </div>
              <div style="font-size:26px;font-weight:900;color:#fff;letter-spacing:-1px;line-height:1;">
                {p["ticker"]}
              </div>
              <div style="font-size:13px;color:#94a3b8;margin-top:4px;">
                ${p["price"]}
                <span style="color:#22c55e;font-weight:700;"> +{p["today_chg"]}% today</span>
                {pcr_badge}
              </div>
              <div style="margin-top:10px;font-size:11px;color:#64748b;">
                <span style="color:#e2e8f0;font-weight:600;">3d gain:</span>
                <span style="color:#22c55e;font-weight:700;"> +{p["momentum_3d"]}%</span>
                &nbsp;·&nbsp;
                <span style="color:#e2e8f0;font-weight:600;">Closed:</span>
                <span style="color:#94a3b8;"> {p["close_pct_range"]:.0f}% of range</span>
                &nbsp;·&nbsp;
                <span style="color:#e2e8f0;font-weight:600;">Peak RVOL:</span>
                <span style="color:#94a3b8;"> {p["best_rvol"]}x</span>
              </div>
            </td>
            <td style="padding:14px 16px;vertical-align:top;text-align:right;">
              <div style="font-size:10px;color:#64748b;font-weight:600;text-transform:uppercase;letter-spacing:.08em;margin-bottom:4px;">
                Swing Score
              </div>
              <div style="font-size:42px;font-weight:900;color:{score_color};line-height:1;">
                {score}
              </div>
              <div style="font-size:10px;color:#64748b;margin-top:2px;">out of 100</div>
              <div style="margin-top:8px;font-size:11px;color:#ef4444;">
                Stop: ${p["stop"]}
              </div>
            </td>
          </tr>
          <tr>
            <td colspan="2" style="padding:10px 16px 14px;border-top:1px solid #1e293b;">
              {signals_html}
            </td>
          </tr>
        </table>
      </td>
    </tr>"""


def build_swing_email(picks: list[dict], date_str: str, unsub_token: str,
                      base_url: str = "") -> str:
    rows = "".join(_swing_card_html(p, i + 1) for i, p in enumerate(picks))
    unsub_url = f"{base_url}/stock-api/alerts/unsubscribe/{unsub_token}"
    count = len(picks)

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#0f172a;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <div style="max-width:620px;margin:0 auto;padding:24px 16px;">

    <div style="text-align:center;margin-bottom:28px;">
      <div style="font-size:28px;font-weight:800;color:#fff;letter-spacing:-1px;">📡 StockScanner AI</div>
      <div style="color:#64748b;font-size:13px;margin-top:4px;">EOD Swing Setups · {date_str}</div>
    </div>

    <div style="background:#1e3a5f;border-radius:12px;padding:16px 20px;margin-bottom:24px;">
      <div style="display:flex;align-items:center;gap:12px;">
        <div style="font-size:32px;">🌙</div>
        <div>
          <div style="color:#fff;font-weight:700;font-size:16px;">
            {count} Overnight Swing Setup{'s' if count != 1 else ''}
          </div>
          <div style="color:rgba(255,255,255,0.7);font-size:13px;margin-top:2px;">
            3:30 PM scan — 30 min to buy before close and hold overnight
          </div>
        </div>
      </div>
    </div>

    <div style="margin-bottom:24px;">
      <div style="font-size:11px;color:#64748b;font-weight:600;text-transform:uppercase;letter-spacing:.08em;margin-bottom:12px;">
        Ranked by Swing Score — Highest Conviction First
      </div>
      <table style="width:100%;border-collapse:collapse;">
        <tbody>
          {rows if rows else '<tr><td style="color:#64748b;padding:20px;text-align:center;background:#1e293b;border-radius:10px;">No swing setups detected today</td></tr>'}
        </tbody>
      </table>
    </div>

    <div style="background:#1e293b;border-left:3px solid #1d4ed8;border-radius:0 12px 12px 0;padding:14px 18px;margin-bottom:24px;">
      <p style="color:#94a3b8;font-size:12px;line-height:1.7;margin:0;">
        💡 <b style="color:#e2e8f0;">Swing tip:</b> These stocks closed near their high of day with buying pressure
        sustained into the close. The edge is overnight — institutions who accumulated today don't sell overnight.
        <b style="color:#e2e8f0;">Exit at next-day close (D+1).</b> Watch for gap-up or early continuation at the open. Stop below the 3-day low listed.
      </p>
    </div>

    <div style="background:#1e293b;border-radius:12px;padding:14px 18px;margin-bottom:24px;">
      <p style="color:#64748b;font-size:11px;line-height:1.6;margin:0;">
        ⚠️ <b style="color:#94a3b8;">Not financial advice.</b>
        Swing setups are based on price/volume patterns and options flow.
        Always do your own research. Past patterns do not guarantee future results.
      </p>
    </div>

    <div style="text-align:center;padding:16px 0;">
      <p style="color:#334155;font-size:11px;margin:0;">
        StockScanner AI · 4:05 PM EOD Swing Scan &nbsp;·&nbsp;
        <a href="{unsub_url}" style="color:#64748b;">Unsubscribe</a>
      </p>
    </div>
  </div>
</body>
</html>"""


def send_swing_digest(base_url: str = "") -> dict:
    """Run scan and send swing digest email + SMS."""
    from email_alerts import get_active_subscribers, send_email_raw, smtp_configured

    picks = run_eod_swing_scan()
    if not picks:
        print("[eod_swing] no picks — skipping email")
        return {"sent": 0, "picks": 0}

    # SMS first (fastest delivery)
    send_swing_sms(picks)

    if not smtp_configured():
        print("[eod_swing] SMTP not configured — SMS only")
        return {"sent": 0, "picks": len(picks), "reason": "smtp not configured"}

    subscribers = get_active_subscribers()
    date_str    = datetime.now(_ET).strftime("%B %d, %Y")
    subject     = (f"🌙 EOD Swing Setups: {len(picks)} Setup{'s' if len(picks) != 1 else ''} "
                   f"Closing Strong · {date_str}")

    sent = skipped = 0
    for sub in subscribers:
        html = build_swing_email(picks, date_str, sub["token"], base_url)
        ok   = send_email_raw(to=sub["email"], subject=subject, html=html)
        if ok:
            sent += 1
        else:
            skipped += 1

    print(f"[eod_swing] digest sent to {sent} subscribers, {len(picks)} picks")
    return {"sent": sent, "skipped": skipped, "picks": len(picks)}
