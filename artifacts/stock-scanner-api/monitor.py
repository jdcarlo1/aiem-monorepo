"""
Uptime monitor — completely standalone, zero impact on main.py or any tab.

Checks https://nclexai.org/stock-api/market/overview every 30 minutes
Mon-Fri 9:30 AM – 4:00 PM ET. Sends email to owner if the site is down
for 2 consecutive checks, and a recovery email when it comes back up.
"""
import os
import sys
import time
import urllib.request
import urllib.error
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
import pytz

OWNER_EMAIL   = os.getenv("ALERT_EMAIL", "joeldcarlo@gmail.com")
CHECK_URL     = "https://nclexai.org/stock-api/market/overview"
CHECK_TIMEOUT = 15          # seconds per ping
SLEEP_TICK    = 60          # seconds between loop ticks (short so shutdown is fast)
MARKET_INTERVAL = 30 * 60  # 30 minutes in seconds
ET = pytz.timezone("America/New_York")


def _smtp_send(subject: str, body: str):
    host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER", "")
    pwd  = os.getenv("SMTP_PASS", "")
    if not user or not pwd:
        print(f"[monitor] SMTP not configured — cannot send alert: {subject}")
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


def _is_market_hours() -> bool:
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return False
    h, m = now.hour, now.minute
    after_open  = (h > 9) or (h == 9 and m >= 30)
    before_close = h < 16
    return after_open and before_close


def _ping() -> bool:
    try:
        req = urllib.request.Request(CHECK_URL, headers={"User-Agent": "StockScanner-Monitor/1.0"})
        with urllib.request.urlopen(req, timeout=CHECK_TIMEOUT) as r:
            return r.status == 200
    except Exception:
        return False


def run():
    print(f"[monitor] started — checking {CHECK_URL} every 30 min during market hours")
    consecutive_failures = 0
    site_was_down        = False
    last_check_time      = 0.0

    while True:
        now_ts = time.time()
        in_market = _is_market_hours()

        if in_market and (now_ts - last_check_time) >= MARKET_INTERVAL:
            last_check_time = now_ts
            up = _ping()
            now_str = datetime.now(ET).strftime("%I:%M %p ET")

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
                        f"<p><strong>Your site did not respond at {now_str}.</strong></p>"
                        f"<p>URL checked: <a href='{CHECK_URL}'>{CHECK_URL}</a></p>"
                        f"<p>This is the 2nd consecutive failed check (30-min interval). "
                        f"Subscribers may be affected.</p>"
                        f"<p>Go to <a href='https://replit.com'>replit.com</a> and click "
                        f"<strong>Publish</strong> to redeploy if needed.</p>"
                    )

        time.sleep(SLEEP_TICK)


if __name__ == "__main__":
    run()
