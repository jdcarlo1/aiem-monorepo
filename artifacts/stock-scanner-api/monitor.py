"""
Uptime monitor — completely standalone, zero impact on main.py or any tab.

Checks https://nclexai.org/stock-api/ every 30 minutes, 24/7.
Sends email to owner if the site is down for 2 consecutive checks,
and a recovery email when it comes back up.
"""
import os
import time
import urllib.request
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
import pytz

OWNER_EMAIL    = os.getenv("ALERT_EMAIL", "joeldcarlo@gmail.com")
CHECK_URL      = "https://nclexai.org/stock-api/"
CHECK_TIMEOUT  = 15         # seconds per ping
SLEEP_TICK     = 60         # seconds between loop ticks
CHECK_INTERVAL = 30 * 60   # 30 minutes between checks
ET = pytz.timezone("America/New_York")


def _smtp_send(subject: str, body: str):
    host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER", "")
    pwd  = os.getenv("SMTP_PASS", "")
    if not user or not pwd:
        print(f"[monitor] SMTP not configured — cannot send: {subject}")
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"StockScanner AI Monitor <{user}>"
    msg["To"]      = OWNER_EMAIL
    msg.attach(MIMEText(body, "html"))
    try:
        with smtplib.SMTP(host, port, timeout=10) as s:
            s.starttls()
            s.login(user, pwd)
            s.sendmail(user, [OWNER_EMAIL], msg.as_string())
        print(f"[monitor] alert sent: {subject}")
    except Exception as e:
        print(f"[monitor] SMTP error: {e}")


def _ping() -> bool:
    try:
        req = urllib.request.Request(
            CHECK_URL, headers={"User-Agent": "StockScanner-Monitor/1.0"}
        )
        with urllib.request.urlopen(req, timeout=CHECK_TIMEOUT) as r:
            return r.status == 200
    except Exception:
        return False


def run():
    print(f"[monitor] started — checking {CHECK_URL} every 30 min, 24/7")
    consecutive_failures = 0
    site_was_down        = False
    last_check_time      = 0.0

    while True:
        now_ts = time.time()

        if (now_ts - last_check_time) >= CHECK_INTERVAL:
            last_check_time = now_ts
            up      = _ping()
            now_str = datetime.now(ET).strftime("%a %b %d %I:%M %p ET")

            if up:
                print(f"[monitor] {now_str} — UP ✓")
                if site_was_down:
                    _smtp_send(
                        "✅ StockScanner AI is back online",
                        f"<p>Your site is back up as of <strong>{now_str}</strong>.</p>"
                        f"<p>URL: <a href='{CHECK_URL}'>{CHECK_URL}</a></p>"
                    )
                consecutive_failures = 0
                site_was_down        = False
            else:
                consecutive_failures += 1
                print(f"[monitor] {now_str} — DOWN ✗ (consecutive: {consecutive_failures})")
                if consecutive_failures >= 2 and not site_was_down:
                    site_was_down = True
                    _smtp_send(
                        "🚨 StockScanner AI is DOWN",
                        f"<p><strong>Site did not respond at {now_str}.</strong></p>"
                        f"<p>URL checked: <a href='{CHECK_URL}'>{CHECK_URL}</a></p>"
                        f"<p>2 consecutive failed checks (30-min interval). "
                        f"Subscribers may be affected.</p>"
                        f"<p>Fix: go to "
                        f"<a href='https://replit.com'>replit.com</a> → "
                        f"open this project → click <strong>Publish → Redeploy</strong>.</p>"
                    )

        time.sleep(SLEEP_TICK)


if __name__ == "__main__":
    run()
