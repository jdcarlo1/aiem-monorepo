"""
Uptime monitor — completely standalone, zero impact on main.py or any tab.

Checks https://nclexai.org/stock-api/ every 30 minutes, 24/7.
Sends email to owner if the site is down for 2 consecutive checks,
and a recovery email when it comes back up.

Also checks the AIEM Telegram notifier's /api/health once per weekday
after 9:20 AM ET. A plain HTTP 200 from that service does not prove
today's Telegram message actually sent (e.g. Telegram API down, bad
token, or the DB read failing) — so this reads the JSON body's
`last_run.status` field and emails the owner if it does not start with
"sent_ok" or "sent_empty" by then. This is the only failure-visibility
channel for that notifier, since it has no delivery channel of its own
besides Telegram.
"""
import os
import time
import json
import urllib.request
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
import pytz

OWNER_EMAIL       = os.getenv("ALERT_EMAIL", "joeldcarlo@gmail.com")
CHECK_URL         = "https://nclexai.org/stock-api/"
AIEM_HEALTH_URL   = "https://nclexai.org/aiem-telegram/api/health"
CHECK_TIMEOUT     = 15         # seconds per ping
SLEEP_TICK        = 60         # seconds between loop ticks
CHECK_INTERVAL    = 30 * 60   # 30 minutes between checks
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


def _check_aiem_notifier():
    """Returns (ok, detail_str). ok=False covers 'unreachable', 'nothing
    recorded yet', and 'recorded but the send itself failed'.

    Reads `today_status`, which the notifier populates from its own DB
    table (shared truth across process instances) — NOT `last_run`,
    which is only that specific process's in-memory state and would be
    stale/wrong if a *different* instance won the idempotency claim and
    actually sent (e.g. during a redeploy overlap)."""
    try:
        req = urllib.request.Request(
            AIEM_HEALTH_URL, headers={"User-Agent": "StockScanner-Monitor/1.0"}
        )
        with urllib.request.urlopen(req, timeout=CHECK_TIMEOUT) as r:
            body = json.loads(r.read())
    except Exception as e:
        return False, f"health endpoint unreachable: {e}"

    today_status = body.get("today_status") or {}
    status = str(today_status.get("status", ""))
    # A real successful send looks like "sent_ok=True" or "sent_empty ok=True".
    # "sent_ok=False" / "sent_empty ok=False" / "in_progress" / "failed_*" /
    # "not_run_yet" / "log_lookup_error" must all be treated as failures.
    if "=True" in status:
        return True, status
    return False, status or "no status recorded for today"


def run():
    print(f"[monitor] started — checking {CHECK_URL} every 30 min, 24/7")
    consecutive_failures  = 0
    site_was_down         = False
    last_check_time       = 0.0
    aiem_alert_sent_date  = None   # ET date string; reset daily to avoid re-alerting every 30 min

    while True:
        now_ts = time.time()

        if (now_ts - last_check_time) >= CHECK_INTERVAL:
            last_check_time = now_ts
            up      = _ping()
            now_et  = datetime.now(ET)
            now_str = now_et.strftime("%a %b %d %I:%M %p ET")

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

            # AIEM Telegram notifier check: once per weekday, after 9:20 AM ET,
            # confirm today's 9:15 AM brief actually went out.
            today_str = now_et.strftime("%Y-%m-%d")
            is_weekday = now_et.weekday() < 5
            past_send_window = (now_et.hour, now_et.minute) >= (9, 20)
            if is_weekday and past_send_window and aiem_alert_sent_date != today_str:
                ok, detail = _check_aiem_notifier()
                if ok:
                    print(f"[monitor] {now_str} — AIEM notifier OK ({detail})")
                    aiem_alert_sent_date = today_str  # don't re-check again today
                else:
                    print(f"[monitor] {now_str} — AIEM notifier FAILED ({detail})")
                    _smtp_send(
                        "🚨 AIEM Telegram morning brief did not send",
                        f"<p><strong>No confirmed send by {now_str}.</strong></p>"
                        f"<p>Health check detail: {detail}</p>"
                        f"<p>Checked: <a href='{AIEM_HEALTH_URL}'>{AIEM_HEALTH_URL}</a></p>"
                        f"<p>You likely did NOT receive today's AIEM picks on Telegram.</p>"
                    )
                    aiem_alert_sent_date = today_str  # one alert per day, not one per 30-min tick

        time.sleep(SLEEP_TICK)


if __name__ == "__main__":
    run()
