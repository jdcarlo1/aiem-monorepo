"""
Email subscription manager + daily digest sender.
Subscribers stored in PostgreSQL (sm_subscribers table).
Emails sent via SMTP (Gmail or any provider).

Env vars needed:
  SMTP_HOST  – default smtp.gmail.com
  SMTP_PORT  – default 587
  SMTP_USER  – sender Gmail address
  SMTP_PASS  – Gmail app password  (https://myaccount.google.com/apppasswords)
  SMTP_FROM_NAME – display name (default "StockScanner AI")
"""
import os
import smtplib
import secrets
from historical_performance import get_historical_performance
import psycopg2
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime


# ── DB helpers ──────────────────────────────────────────────────────────────

def _conn():
    return psycopg2.connect(os.environ["DATABASE_URL"])


def init_db():
    try:
        with _conn() as con:
            with con.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS sm_subscribers (
                        id         SERIAL PRIMARY KEY,
                        email      VARCHAR(255) UNIQUE NOT NULL,
                        token      VARCHAR(64)  NOT NULL,
                        created_at TIMESTAMP DEFAULT NOW(),
                        active     BOOLEAN DEFAULT TRUE
                    )
                """)
    except Exception as e:
        print(f"[email_alerts] DB init error: {e}")


def subscribe(email: str) -> dict:
    email = email.strip().lower()
    if not email or "@" not in email:
        return {"ok": False, "error": "Invalid email"}
    token = secrets.token_urlsafe(32)
    try:
        with _conn() as con:
            with con.cursor() as cur:
                cur.execute("""
                    INSERT INTO sm_subscribers (email, token, active)
                    VALUES (%s, %s, TRUE)
                    ON CONFLICT (email) DO UPDATE
                        SET active = TRUE, token = EXCLUDED.token
                    RETURNING id
                """, (email, token))
                row = cur.fetchone()
        return {"ok": True, "id": row[0]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def unsubscribe(token: str) -> dict:
    try:
        with _conn() as con:
            with con.cursor() as cur:
                cur.execute("""
                    UPDATE sm_subscribers SET active = FALSE
                    WHERE token = %s RETURNING email
                """, (token,))
                row = cur.fetchone()
        if row:
            return {"ok": True, "email": row[0]}
        return {"ok": False, "error": "Token not found"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_active_subscribers() -> list[dict]:
    try:
        with _conn() as con:
            with con.cursor() as cur:
                cur.execute("""
                    SELECT email, token FROM sm_subscribers WHERE active = TRUE
                """)
                return [{"email": r[0], "token": r[1]} for r in cur.fetchall()]
    except Exception:
        return []


def subscriber_count() -> int:
    try:
        with _conn() as con:
            with con.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM sm_subscribers WHERE active = TRUE")
                return cur.fetchone()[0]
    except Exception:
        return 0


# ── SMTP helpers ────────────────────────────────────────────────────────────

def _smtp_cfg():
    return {
        "host": os.getenv("SMTP_HOST", "smtp.gmail.com"),
        "port": int(os.getenv("SMTP_PORT", "587")),
        "user": os.getenv("SMTP_USER", ""),
        "password": os.getenv("SMTP_PASS", ""),
        "from_name": os.getenv("SMTP_FROM_NAME", "StockScanner AI"),
    }


def smtp_configured() -> bool:
    cfg = _smtp_cfg()
    return bool(cfg["user"] and cfg["password"])


def send_email_raw(to: str, subject: str, html: str) -> bool:
    cfg = _smtp_cfg()
    if not cfg["user"] or not cfg["password"]:
        print("[email_alerts] SMTP not configured — skipping send")
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = f'{cfg["from_name"]} <{cfg["user"]}>'
        msg["To"]      = to
        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP(cfg["host"], cfg["port"]) as server:
            server.starttls()
            server.login(cfg["user"], cfg["password"])
            server.sendmail(cfg["user"], [to], msg.as_string())
        return True
    except Exception as e:
        print(f"[email_alerts] Send error to {to}: {e}")
        return False


# ── Email templates ─────────────────────────────────────────────────────────

def _score_color(score: int) -> str:
    if score >= 75: return "#22c55e"
    if score >= 60: return "#06b6d4"
    if score >= 45: return "#f59e0b"
    return "#94a3b8"


def _bullish_label(score: int) -> tuple[str, str]:
    """Returns (label, color) based on score."""
    if score >= 85: return ("🔥 Extremely Bullish", "#f97316")
    if score >= 70: return ("📈 Very Bullish",      "#22c55e")
    if score >= 60: return ("↗️ Bullish",            "#06b6d4")
    if score >= 45: return ("➡️ Mildly Bullish",     "#f59e0b")
    return ("⬇️ Weak / Neutral", "#64748b")


def _perf_cell(val) -> str:
    """Render a single performance cell — green positive, red negative, gray none."""
    if val is None:
        return '<td style="padding:3px 6px;text-align:center;color:#334155;font-size:10px;">—</td>'
    color = "#22c55e" if val >= 0 else "#ef4444"
    sign  = "+" if val >= 0 else ""
    return (f'<td style="padding:3px 6px;text-align:center;font-size:10px;'
            f'font-weight:700;color:{color};">{sign}{val:.1f}%</td>')


def _perf_html(perf: dict) -> str:
    """Build the historical performance table rows for the email card."""
    count = perf.get("count", 0)
    if count == 0:
        return ""

    day_keys  = [("1d","1D"), ("2d","2D"), ("3d","3D"), ("4d","4D"), ("5d","5D")]
    week_keys = [("1w","1W"), ("2w","2W"), ("3w","3W"), ("4w","4W")]

    def header_cells(pairs):
        return "".join(
            f'<th style="padding:3px 6px;text-align:center;color:#475569;'
            f'font-size:9px;font-weight:600;text-transform:uppercase;">{lbl}</th>'
            for _, lbl in pairs
        )

    def value_cells(pairs):
        return "".join(_perf_cell(perf.get(key)) for key, _ in pairs)

    return f"""
    <tr>
      <td colspan="2" style="padding:10px 16px 14px;border-top:1px solid #1e293b;">
        <div style="font-size:10px;color:#475569;font-weight:600;margin-bottom:6px;text-transform:uppercase;letter-spacing:.06em;">
          📊 Last {count} similar signal{'s' if count != 1 else ''} — avg % return after entry
        </div>
        <table width="100%" cellpadding="0" cellspacing="0" style="background:#0a0f1a;border-radius:6px;">
          <tr style="border-bottom:1px solid #1e293b;">
            <th style="padding:4px 6px;text-align:left;color:#334155;font-size:9px;font-weight:600;white-space:nowrap;">Days →</th>
            {header_cells(day_keys)}
          </tr>
          <tr>
            <td style="padding:3px 6px;color:#475569;font-size:9px;white-space:nowrap;">Return</td>
            {value_cells(day_keys)}
          </tr>
          <tr style="border-top:1px solid #0f172a;">
            <th style="padding:4px 6px;text-align:left;color:#334155;font-size:9px;font-weight:600;white-space:nowrap;">Weeks →</th>
            {header_cells(week_keys)}
          </tr>
          <tr>
            <td style="padding:3px 6px;color:#475569;font-size:9px;white-space:nowrap;">Return</td>
            {value_cells(week_keys)}
          </tr>
        </table>
      </td>
    </tr>"""


def _signal_html(s: dict, rank: int, perf: dict) -> str:
    score       = s.get("smart_money_score", 0)
    score_color = _score_color(score)
    label, label_color = _bullish_label(score)
    opts        = s.get("options_summary") or {}
    ticker      = s.get("ticker", "")
    price       = s.get("price", 0)
    thesis      = s.get("thesis", "")

    # Rank medal for top 3
    medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, f"#{rank}")

    opts_line = ""
    top_vol_block  = ""
    top_prem_block = ""

    if opts:
        opts_line = (
            f"Vol/OI {opts.get('call_vol_oi','—')}&nbsp;&nbsp;·&nbsp;&nbsp;"
            f"C/P {opts.get('call_put_ratio','—')}x&nbsp;&nbsp;·&nbsp;&nbsp;"
            f"ATM IV {opts.get('atm_iv','—')}%"
        )

        today_str = datetime.now().strftime("%Y-%m-%d")

        def _fmt_expiry(d):
            try: return datetime.strptime(d, "%Y-%m-%d").strftime("%b %d")
            except: return d or "—"

        def _days_out(d):
            try:
                from datetime import date
                delta = (datetime.strptime(d, "%Y-%m-%d").date() - date.today()).days
                return delta
            except: return 999

        # Top-volume contract — skip if 0DTE
        tvs = opts.get("top_vol_strike")
        tve = opts.get("top_vol_expiry") or opts.get("expiry", "")
        tvc = opts.get("top_vol_contracts")
        if tvs is not None and tve != today_str:
            days = _days_out(tve)
            contracts_str = f"&nbsp;·&nbsp;{tvc:,} contracts" if tvc else ""
            days_str = f"&nbsp;·&nbsp;{days}d out" if days < 999 else ""
            top_vol_block = (
                f'<div style="margin-top:6px;padding:10px 12px;background:#071a10;'
                f'border-left:4px solid #22c55e;border-radius:0 8px 8px 0;">'
                f'<div style="font-size:9px;color:#22c55e;font-weight:700;text-transform:uppercase;'
                f'letter-spacing:.08em;margin-bottom:4px;">⚡ ACTIONABLE · 🔥 Most Active Strike</div>'
                f'<span style="font-size:18px;font-weight:900;color:#4ade80;">${tvs:g}&nbsp;C</span>'
                f'<span style="font-size:12px;color:#86efac;font-weight:600;">'
                f'&nbsp;&nbsp;Exp&nbsp;<strong>{_fmt_expiry(tve)}</strong>{days_str}{contracts_str}</span>'
                f'</div>'
            )

        # Top-premium contract — skip if 0DTE
        tps = opts.get("top_prem_strike")
        tpe = opts.get("top_prem_expiry") or opts.get("expiry", "")
        tpk = opts.get("top_prem_value_k")
        tpc = opts.get("top_prem_contracts")
        if tps is not None and tpe != today_str:
            prem_str = f"&nbsp;·&nbsp;${tpk/1000:.1f}M premium" if tpk and tpk >= 1000 else (f"&nbsp;·&nbsp;${tpk:,.0f}K premium" if tpk else "")
            contracts_str2 = f"&nbsp;·&nbsp;{tpc:,} contracts" if tpc else ""
            days2 = _days_out(tpe)
            days_str2 = f"&nbsp;·&nbsp;{days2}d out" if days2 < 999 else ""
            top_prem_block = (
                f'<div style="margin-top:6px;padding:10px 12px;background:#1a0d04;'
                f'border-left:4px solid #f97316;border-radius:0 8px 8px 0;">'
                f'<div style="font-size:9px;color:#f97316;font-weight:700;text-transform:uppercase;'
                f'letter-spacing:.08em;margin-bottom:4px;">⚡ ACTIONABLE · 💰 Most Premium Traded</div>'
                f'<span style="font-size:18px;font-weight:900;color:#fb923c;">${tps:g}&nbsp;C</span>'
                f'<span style="font-size:12px;color:#fdba74;font-weight:600;">'
                f'&nbsp;&nbsp;Exp&nbsp;<strong>{_fmt_expiry(tpe)}</strong>{days_str2}{prem_str}{contracts_str2}</span>'
                f'</div>'
            )

    perf_row  = _perf_html(perf)

    return f"""
    <tr>
      <td style="padding:0 0 16px 0;">
        <table width="100%" cellpadding="0" cellspacing="0" style="background:#0f172a;border-radius:10px;border:1px solid #1e293b;overflow:hidden;">
          <tr>
            <!-- Rank + ticker block -->
            <td style="padding:14px 16px;vertical-align:top;width:50%;">
              <div style="font-size:11px;color:#64748b;font-weight:600;margin-bottom:4px;letter-spacing:.05em;">
                {medal} RANK #{rank}
              </div>
              <div style="font-size:26px;font-weight:900;color:#fff;letter-spacing:-1px;line-height:1;">
                {ticker}
              </div>
              <div style="font-size:13px;color:#94a3b8;margin-top:4px;">${price:.2f}</div>
              <div style="margin-top:8px;">
                <span style="background:{label_color}22;color:{label_color};font-size:11px;font-weight:700;padding:3px 10px;border-radius:20px;border:1px solid {label_color}44;">
                  {label}
                </span>
              </div>
            </td>
            <!-- Score block -->
            <td style="padding:14px 16px;vertical-align:top;text-align:right;">
              <div style="font-size:10px;color:#64748b;font-weight:600;text-transform:uppercase;letter-spacing:.08em;margin-bottom:4px;">
                Bullish Score
              </div>
              <div style="font-size:42px;font-weight:900;color:{score_color};line-height:1;">
                {score}
              </div>
              <div style="font-size:10px;color:#64748b;margin-top:2px;">out of 100</div>
            </td>
          </tr>
          <!-- Options data row -->
          {"" if not opts_line else f'''
          <tr>
            <td colspan="2" style="padding:8px 16px 4px;font-size:11px;color:#64748b;border-top:1px solid #1e293b;">
              📡 {opts_line}
            </td>
          </tr>'''}
          <!-- Top-volume + top-premium contract blocks -->
          {"" if not (top_vol_block or top_prem_block) else f'''
          <tr>
            <td colspan="2" style="padding:4px 16px 12px;">
              {top_vol_block}
              {top_prem_block}
            </td>
          </tr>'''}
          <!-- Signal + thesis row -->
          <tr>
            <td colspan="2" style="padding:10px 16px 14px;border-top:1px solid #1e293b;">
              <div style="font-size:12px;font-weight:700;color:#e2e8f0;margin-bottom:4px;">
                {s.get('signal','')}
              </div>
              <div style="font-size:11px;color:#64748b;line-height:1.6;">
                {thesis[:180]}{'…' if len(thesis) > 180 else ''}
              </div>
            </td>
          </tr>
          <!-- Historical performance row -->
          {perf_row}
        </table>
      </td>
    </tr>"""


def build_digest_email(signals: list[dict], date_str: str, unsub_token: str,
                       base_url: str = "", session: str = "eod") -> str:
    # Always rank highest score first
    ranked = sorted(signals, key=lambda s: s.get("smart_money_score", 0), reverse=True)
    rows = ""
    for i, s in enumerate(ranked):
        try:
            perf = get_historical_performance(s.get("ticker", ""), s.get("smart_money_score", 0))
        except Exception:
            perf = {"count": 0}
        rows += _signal_html(s, i + 1, perf)
    unsub_url = f"{base_url}/stock-api/alerts/unsubscribe/{unsub_token}"
    count     = len(ranked)

    is_morning  = session == "morning"
    is_preclose = session == "preclose"

    # Session-specific copy
    is_premarket = session == "premarket"

    if is_premarket:
        emoji     = "🌅"
        title     = "Pre-Market OI Watch — Before the Bell"
        sub_title = "9:00 AM scan — overnight Open Interest from prior day's close"
        bar_note  = "Options haven't opened yet. This is who loaded up positions yesterday."
        bar_color = "#1d4ed8"
        tip_text  = ("💡 <b>Pre-market tip:</b> Open Interest reflects settled positions from yesterday's close — "
                     "it doesn't change until tonight. High call OI means someone bet on upside before today even started. "
                     "Watch these at the open for confirmation.")
    elif is_morning:
        emoji     = "🔔"
        title     = "Opening Bell Unusual Options Activity"
        sub_title = "9:45 AM scan — first real options flow of the day"
        bar_note  = "Opening sweep detected. Options data 15 min after open — act early."
        bar_color = "#7c3aed"
        tip_text  = ("💡 <b>Morning tip:</b> High Vol/OI at open often means smart money entered overnight "
                     "and is confirming the position at the bell. These signals are freshest.")
    elif is_preclose:
        emoji     = "⏰"
        title     = "Pre-Close Alert — 30 Minutes to Act"
        sub_title = "3:30 PM scan — 30 minutes before market close"
        bar_note  = "Market closes at 4:00 PM ET. You still have time to get in."
        bar_color = "#b45309"
        tip_text  = ("💡 <b>Pre-close tip:</b> Signals still showing strength at 3:30 PM have "
                     "held up through the trading day. Entering near close can reduce overnight gap risk "
                     "while still capturing end-of-day momentum.")
    else:
        emoji     = "🏆"
        title     = "End of Day Smart Money Digest"
        sub_title = "4:15 PM scan — full-day final options flow"
        bar_note  = "Full trading day captured. Options data settled after close."
        bar_color = "#0e7490"
        tip_text  = ("💡 <b>EOD tip:</b> High scores after close reflect the full day's options flow. "
                     "Stocks holding strong signal into close often gap up overnight.")

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#0f172a;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <div style="max-width:620px;margin:0 auto;padding:24px 16px;">

    <!-- Header -->
    <div style="text-align:center;margin-bottom:28px;">
      <div style="font-size:28px;font-weight:800;color:#fff;letter-spacing:-1px;">
        📡 StockScanner AI
      </div>
      <div style="color:#64748b;font-size:13px;margin-top:4px;">
        {title} · {date_str}
      </div>
    </div>

    <!-- Session banner -->
    <div style="background:{bar_color};border-radius:12px;padding:16px 20px;margin-bottom:24px;">
      <div style="display:flex;align-items:center;gap:12px;">
        <div style="font-size:32px;">{emoji}</div>
        <div>
          <div style="color:#fff;font-weight:700;font-size:16px;">
            {count} High-Conviction Signal{'s' if count != 1 else ''}
          </div>
          <div style="color:rgba(255,255,255,0.7);font-size:13px;margin-top:2px;">
            {bar_note}
          </div>
        </div>
      </div>
    </div>

    <!-- Ranked signals — most bullish to least bullish -->
    <div style="margin-bottom:24px;">
      <div style="font-size:11px;color:#64748b;font-weight:600;text-transform:uppercase;letter-spacing:.08em;margin-bottom:12px;">
        Ranked Most → Least Bullish · Bullish Score out of 100
      </div>
      <table style="width:100%;border-collapse:collapse;">
        <tbody>
          {rows if rows else '<tr><td style="color:#64748b;padding:20px;text-align:center;background:#1e293b;border-radius:10px;">No high-conviction signals detected</td></tr>'}
        </tbody>
      </table>
    </div>

    <!-- Session tip -->
    <div style="background:#1e293b;border-left:3px solid {bar_color};border-radius:0 12px 12px 0;padding:14px 18px;margin-bottom:24px;">
      <p style="color:#94a3b8;font-size:12px;line-height:1.7;margin:0;">{tip_text}</p>
    </div>

    <!-- Disclaimer -->
    <div style="background:#1e293b;border-radius:12px;padding:14px 18px;margin-bottom:24px;">
      <p style="color:#64748b;font-size:11px;line-height:1.6;margin:0;">
        ⚠️ <b style="color:#94a3b8;">Not financial advice.</b>
        Options data is sourced from yfinance (15-minute delayed).
        Always do your own research before trading.
      </p>
    </div>

    <!-- Footer -->
    <div style="text-align:center;padding:16px 0;">
      <p style="color:#334155;font-size:11px;margin:0;">
        StockScanner AI · {sub_title} &nbsp;·&nbsp;
        <a href="{unsub_url}" style="color:#64748b;">Unsubscribe</a>
      </p>
    </div>
  </div>
</body>
</html>"""


# ── Send digest to all subscribers ──────────────────────────────────────────

def send_daily_digest(signals: list[dict], base_url: str = "",
                      session: str = "eod") -> dict:
    subscribers = get_active_subscribers()
    if not subscribers:
        return {"sent": 0, "skipped": 0, "reason": "no subscribers"}
    if not smtp_configured():
        return {"sent": 0, "skipped": len(subscribers), "reason": "SMTP not configured"}

    date_str = datetime.now().strftime("%B %d, %Y")
    top      = [s for s in signals if s.get("smart_money_score", 0) >= 60][:8]

    is_morning = session == "morning"
    if is_morning:
        subject = (f"🔔 Opening Bell Alert: {len(top)} Unusual Options Signal"
                   f"{'s' if len(top) != 1 else ''} · {date_str}")
    else:
        subject = (f"🏆 EOD Smart Money: {len(top)} High-Conviction Signal"
                   f"{'s' if len(top) != 1 else ''} · {date_str}")

    sent = skipped = 0
    for sub in subscribers:
        html = build_digest_email(top, date_str, sub["token"], base_url, session)
        ok   = send_email_raw(to=sub["email"], subject=subject, html=html)
        if ok:
            sent += 1
        else:
            skipped += 1

    return {"sent": sent, "skipped": skipped, "signals": len(top), "session": session}
