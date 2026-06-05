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


def _signal_html(s: dict) -> str:
    score = s.get("smart_money_score", 0)
    color = _score_color(score)
    opts  = s.get("options_summary") or {}
    opts_row = ""
    if opts:
        opts_row = f"""
        <tr>
          <td colspan="4" style="padding:4px 0 8px;font-size:12px;color:#94a3b8;">
            📡 <b>Real options:</b>
            Vol/OI&nbsp;{opts.get('call_vol_oi','—')} &nbsp;|&nbsp;
            C/P&nbsp;{opts.get('call_put_ratio','—')}x &nbsp;|&nbsp;
            ATM&nbsp;IV&nbsp;{opts.get('atm_iv','—')}% &nbsp;|&nbsp;
            Expiry&nbsp;{opts.get('expiry','—')}
          </td>
        </tr>"""
    return f"""
    <tr style="border-bottom:1px solid #1e293b;">
      <td style="padding:12px 8px;font-weight:700;font-size:16px;color:#fff;">
        {s['ticker']}
        <div style="font-size:11px;color:#64748b;font-weight:400;">${s.get('price',0):.2f}</div>
      </td>
      <td style="padding:12px 8px;text-align:center;">
        <span style="background:{color};color:#000;padding:4px 10px;border-radius:20px;font-weight:700;font-size:14px;">
          {score}
        </span>
      </td>
      <td style="padding:12px 8px;font-size:13px;color:#e2e8f0;">{s.get('signal','')}</td>
      <td style="padding:12px 8px;font-size:12px;color:#94a3b8;">
        WR {s.get('win_rate',0):.0f}% · {s.get('expected_move_low',0)}–{s.get('expected_move_high',0)}% move
      </td>
    </tr>
    {opts_row}
    <tr>
      <td colspan="4" style="padding:4px 0 12px;font-size:12px;color:#94a3b8;font-style:italic;">
        {s.get('thesis','')[:200]}{'…' if len(s.get('thesis',''))>200 else ''}
      </td>
    </tr>"""


def build_digest_email(signals: list[dict], date_str: str, unsub_token: str,
                       base_url: str = "", session: str = "eod") -> str:
    rows      = "".join(_signal_html(s) for s in signals)
    unsub_url = f"{base_url}/stock-api/alerts/unsubscribe/{unsub_token}"
    count     = len(signals)

    is_morning = session == "morning"

    # Session-specific copy
    emoji      = "🔔" if is_morning else "🏆"
    title      = "Opening Bell Unusual Options Activity" if is_morning else "End of Day Smart Money Digest"
    sub_title  = ("9:45 AM scan — first real options flow of the day" if is_morning
                  else "4:15 PM scan — full-day final options flow")
    bar_note   = ("Opening sweep detected. Options data 15 min after open — act early." if is_morning
                  else "Full trading day captured. Options data settled after close.")
    bar_color  = "#7c3aed" if is_morning else "#0e7490"   # purple morning, teal EOD
    tip_text   = ("💡 <b>Morning tip:</b> High Vol/OI at open often means smart money entered overnight "
                  "and is confirming the position at the bell. These signals are freshest."
                  if is_morning else
                  "💡 <b>EOD tip:</b> High scores after close reflect the full day's options flow. "
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

    <!-- Signals table -->
    <div style="background:#1e293b;border-radius:12px;padding:20px;margin-bottom:24px;">
      <table style="width:100%;border-collapse:collapse;">
        <thead>
          <tr style="border-bottom:1px solid #334155;">
            <th style="text-align:left;padding:0 8px 12px;color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:.05em;">Ticker</th>
            <th style="text-align:center;padding:0 8px 12px;color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:.05em;">Score</th>
            <th style="text-align:left;padding:0 8px 12px;color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:.05em;">Signal</th>
            <th style="text-align:left;padding:0 8px 12px;color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:.05em;">Stats</th>
          </tr>
        </thead>
        <tbody>
          {rows if rows else '<tr><td colspan="4" style="color:#64748b;padding:20px;text-align:center;">No high-conviction signals detected</td></tr>'}
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
