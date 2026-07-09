"""
aiem_telegram_notifier.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AIEM TELEGRAM NOTIFIER — READ-ONLY, always-on production process (FIX B).

PURPOSE
  Sends AIEM's own INDEPENDENT picks (Workstream D) to Telegram by READING
  the aiem_independent_picks table that main.py's independent scans already
  wrote. These are picks AIEM reasoned to on its own from RAW Polygon /
  raw sweep-tape data only (no pre-computed conviction/composite score is
  ever handed to AIEM for this run). This process NEVER scans and NEVER
  writes to aiem_independent_picks (or any other table) — it is a pure
  notifier, so it cannot race or collide with main.py, which remains the
  single canonical writer.

  As of 2026-07-01 this is TWO separate briefs, not one combined message,
  because stocks and call options are decided by two separate AIEM scans
  at two separate times of day (see SCHEDULE below) - each brief only
  contains its own pick_type, sent as soon as that type's scan has had
  time to finish.

  Previously (through 2026-06-30) this process sent a single 5-pick brief
  sourced from aiem_predictions (the website-scored candidates handed to
  AIEM), then briefly a single combined stock+options brief at 9:30 AM.
  Both were replaced per explicit user direction. If no rows exist in
  aiem_independent_picks for a given pick_type on a given day, we fail
  closed: send a "data not ready" message and stop. We do not scan.

SCHEDULE (Eastern Time)
  09:30  Mon-Fri   Stock picks brief — reads today's aiem_independent_picks
                   pick_type='stock' rows (written by main.py's 9:20 AM
                   stock scan). 9:30 leaves a 10-minute buffer after the
                   9:20 canonical write so the read never races the write.
  10:30  Mon-Fri   Options picks brief — reads today's aiem_independent_picks
                   pick_type='call_option' rows (written by main.py's
                   10:20 AM options scan, which itself runs deliberately
                   after the site's own 9:36 AM and 10:05 AM unusual-calls
                   sweep scans so AIEM has two real intraday passes of
                   sweep data instead of just the noisy first few minutes
                   after the open). 10:30 leaves the same 10-minute buffer
                   after the 10:20 canonical write.
  15:00  Mon-Fri   RVOL/Gap/Close-Strength combo brief — reads
                   polygon_market_daily for the most recently COMPLETED
                   session (rvol>2.5, gap_pct>0.5, close_strength>0.6 — the
                   backtested combo, up to 87.65% historical win rate) and
                   pairs each hit with a LIVE Tradier quote so the owner can
                   see today's follow-through in progress. NOTE: close_strength
                   can only be finalized once a session fully closes, so this
                   brief always reports on the last completed day's hits, not
                   an intraday-only computation for today — there is no
                   full-market live/today snapshot available from the current
                   Polygon plan (grouped-daily for "today" 403s until the
                   session closes).

IDEMPOTENCY (no duplicate sends across restarts/redeploys)
  Owns one dedicated table it created itself, `aiem_notifier_log`
  (PRIMARY KEY (send_date, brief_type) — brief_type is 'stock' or 'options',
  so the two daily briefs claim and record independently and can never
  block or overwrite each other's status). Before sending, it does an
  atomic INSERT ... ON CONFLICT claim for today's ET date + that brief's
  type. Only the process that wins the claim sends. If a redeploy overlap
  causes two instances to be alive at send time simultaneously, only one
  will successfully claim the row and send; the other logs "already sent
  today, skipping" and does nothing. This table is owned solely by this
  script — it is NOT aiem_independent_picks, so this does not reintroduce
  a two-writer collision.

FAILURE VISIBILITY
  If the Telegram send fails (bad token, network error) or the DB read
  fails, this is recorded in `aiem_notifier_log` (status column, per
  brief_type) and in /api/health's `last_run`. This process has no
  secondary delivery channel of its own — the existing uptime-monitor.py
  (SMTP-based) has been extended to check this service's /api/health
  after 9:35 AM ET (stock brief) and again after 10:35 AM ET (options
  brief) on weekdays, emailing the owner if that day's send did not
  succeed, since a healthy HTTP 200 from this service does not by itself
  prove today's message actually reached Telegram.

HEALTH CHECK
  GET /api/health  → {"status","scheduler","db","last_run","mode",
                       "today_status_stock","today_status_options"}
  Bound to AIEM_HEALTH_PORT (default 5051).

REQUIRED ENV VARS
  DATABASE_URL                          — postgres connection string
  TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID — Telegram delivery

MANUAL TEST
  python3 aiem_telegram_notifier.py --once   # sends both briefs immediately
                                              # (stock then options), does not
                                              # start the scheduler, still
                                              # goes through the same
                                              # claim-before-send idempotency
                                              # gate as the real jobs
"""

import os
import sys
import json
import logging
import threading
import urllib.request
from datetime import date, datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

import psycopg2
import pytz
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [AIEM-NOTIFIER] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger('AIEM-NOTIFIER')

ET = pytz.timezone('America/New_York')
DATABASE_URL = os.environ.get('DATABASE_URL', '')
_HEALTH_PORT = int(os.environ.get('AIEM_HEALTH_PORT', '5052'))

_scheduler_ref = None
_last_run = {"status": "not_run_yet", "timestamp": None}


# ─────────────────────────────────────────────────────────────
# TELEGRAM SEND (no DB write side effects)
# ─────────────────────────────────────────────────────────────
def _tg_send(text: str) -> bool:
    """Send a message to the Telegram owner chat. Silent no-op when not configured."""
    token = "".join(os.environ.get("TELEGRAM_BOT_TOKEN", "").split())
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        log.warning("[telegram] TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not configured - skipping send")
        return False
    try:
        payload = json.dumps({"chat_id": chat_id, "text": text}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            ok = json.loads(r.read()).get("ok", False)
            if not ok:
                log.warning("[telegram] API responded without ok=true")
            return ok
    except Exception as e:
        log.warning(f"[telegram] send failed: {e}")
        return False


# ─────────────────────────────────────────────────────────────
# SKIP COMMAND LISTENER — polls getUpdates for owner SKIP replies
# ─────────────────────────────────────────────────────────────
def _tg_get_skip_commands(within_minutes: int = 90) -> set:
    """Poll Telegram getUpdates for recent SKIP commands from the owner.
    Returns a set of skipped categories: 'nano', 'picks', 'options'.
    Non-blocking (timeout=0). Safe to call even without webhooks configured."""
    import time as _t
    token   = "".join(os.environ.get("TELEGRAM_BOT_TOKEN", "").split())
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return set()
    cutoff  = _t.time() - within_minutes * 60
    skipped = set()
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/getUpdates?timeout=0&limit=50",
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        for upd in data.get("result", []):
            msg = upd.get("message", {})
            if str(msg.get("chat", {}).get("id", "")) != chat_id:
                continue
            if msg.get("date", 0) < cutoff:
                continue
            text = (msg.get("text") or "").upper().strip()
            for cmd, cat in [
                ("SKIP NANO",    "nano"),
                ("SKIP PICKS",   "picks"),
                ("SKIP OPTIONS", "options"),
                ("SKIP ALL",     "all"),
            ]:
                if cmd in text:
                    skipped.add(cat)
    except Exception as e:
        log.warning(f"[skip-check] getUpdates error: {e}")
    if "all" in skipped:
        return {"nano", "picks", "options"}
    return skipped


# ─────────────────────────────────────────────────────────────
# MORNING PREVIEW — 9:00 AM daily briefing with skip instructions
# ─────────────────────────────────────────────────────────────
def send_morning_preview():
    """9:00 AM ET Mon-Fri: send one consolidated preview of every alert
    planned for today. Owner can reply SKIP NANO / SKIP PICKS / SKIP OPTIONS
    within 30 minutes to block that category before it fires."""
    today = date.today()
    conn  = None
    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
        cur  = conn.cursor()

        # Nano picks (S1b/S1c/S1d gap signals from aiem_process)
        cur.execute("""
            SELECT ticker, confidence_score, signal_basis
            FROM aiem_process_predictions
            WHERE prediction_date = %s AND confidence_score >= 50
            ORDER BY confidence_score DESC LIMIT 10
        """, (today,))
        nano_rows = cur.fetchall()

        # Stock picks (AIEM independent, threshold 7.5)
        cur.execute("""
            SELECT ticker, confidence_score
            FROM aiem_independent_picks
            WHERE pick_date = %s AND pick_type = 'stock' AND confidence_score >= 7.5
            ORDER BY confidence_score DESC LIMIT 10
        """, (today,))
        stock_rows = cur.fetchall()

        # Options picks (AIEM independent, threshold 7.5)
        cur.execute("""
            SELECT ticker, confidence_score
            FROM aiem_independent_picks
            WHERE pick_date = %s AND pick_type = 'call_option' AND confidence_score >= 7.5
            ORDER BY confidence_score DESC LIMIT 10
        """, (today,))
        opts_rows = cur.fetchall()

        lines = [
            f"📅 AIEM MORNING PREVIEW — {today.strftime('%a %b %-d')}",
            "Reply SKIP to block any category (30 min window)",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        ]

        if nano_rows:
            lines.append(f"🟢 NANO PICKS — {len(nano_rows)} above threshold (fires 9:30 AM):")
            for ticker, conf, sig in nano_rows[:5]:
                tier = "S1c" if "momentum_carry" in (sig or "") else "S1d" if "soft_carry" in (sig or "") else "S1b"
                lines.append(f"  • {ticker}  {float(conf):.0f}/100  [{tier}]")
            if len(nano_rows) > 5:
                lines.append(f"  ...+{len(nano_rows)-5} more")
            lines.append("  ➤ Reply SKIP NANO to block")
        else:
            lines.append("⚪ NANO PICKS: none above 50 threshold today")

        lines.append("")

        if stock_rows:
            lines.append(f"📈 STOCK PICKS — {len(stock_rows)} ready (fires 9:30 AM):")
            for ticker, conf in stock_rows[:4]:
                lines.append(f"  • {ticker}  {float(conf):.1f}/10")
            lines.append("  ➤ Reply SKIP PICKS to block")
        else:
            lines.append("⚪ STOCK PICKS: none above 7.5 today")

        if opts_rows:
            lines.append(f"🎯 OPTIONS PICKS — {len(opts_rows)} ready (fires 10:30 AM):")
            for ticker, conf in opts_rows[:4]:
                lines.append(f"  • {ticker}  {float(conf):.1f}/10")
            lines.append("  ➤ Reply SKIP OPTIONS to block")
        else:
            lines.append("⚪ OPTIONS PICKS: none above 7.5 today")

        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        _tg_send("\n".join(lines))
        log.info(f"[preview] sent — {len(nano_rows)} nano, {len(stock_rows)} stock, {len(opts_rows)} options")
    except Exception as e:
        log.error(f"[preview] error: {e}")
    finally:
        if conn:
            conn.close()


# ─────────────────────────────────────────────────────────────
# DB READ — SELECT ONLY. This process must never INSERT/UPDATE/DELETE
# aiem_predictions; main.py is the single canonical writer.
# ─────────────────────────────────────────────────────────────
def _tier_label(sig_basis: str) -> str:
    """Map aiem_process_predictions signal_basis to a human-readable tier."""
    sigs = [s.strip() for s in (sig_basis or "").split(",")]
    if "momentum_carry" in sigs: return "S1c ✅ Full Carry"
    if "soft_carry"     in sigs: return "S1d 🔵 Soft Carry"
    if "gap_sweet_spot" in sigs: return "S1b 🟡 Gap Zone"
    return "Gap"


def _fetch_todays_picks(pick_type: str):
    """Read-only: AIEM's own independent picks (Workstream D) for today,
    filtered to ONE pick_type ('stock' or 'call_option'), ordered by AIEM's
    own confidence score so the message shows its highest-conviction ideas
    first.

    Fallback for 'stock' type: if aiem_independent_picks is empty today
    (Workstream D scan did not run), reads aiem_process_predictions instead
    — these are the S1b/S1c/S1d gap+momentum picks generated at 3 AM ET."""
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
        cur = conn.cursor()
        # Threshold on the 0–10 scale used by aiem_independent_picks.
        # Stock floor = 6.5 (avg historical = 7.06; excludes bottom-tier speculative picks).
        # Options floor = 6.5 (all historical options ≥ 6.2; keeps only genuine flow signals).
        _INDEP_THRESH = 7.5
        cur.execute("""
            SELECT pick_type, ticker, confidence_score, rationale,
                   option_strike, option_expiry, hold_days_max
            FROM aiem_independent_picks
            WHERE pick_date = %s AND pick_type = %s
              AND confidence_score >= 7.5
            ORDER BY confidence_score DESC NULLS LAST
            LIMIT 20
        """, (date.today(), pick_type))
        rows = cur.fetchall()
        if rows:
            return rows

        # Fallback for stocks: use today's AIEM Process predictions (S1b/S1c/S1d)
        # Only send picks that passed the confidence threshold (50). Picks below
        # this have no validated edge and must never appear in the alert.
        if pick_type == "stock":
            log.info("aiem_independent_picks empty today — falling back to aiem_process_predictions (S1b/S1c/S1d)")
            cur.execute("""
                SELECT ticker, confidence_score, signal_basis
                FROM aiem_process_predictions
                WHERE prediction_date = %s
                  AND confidence_score >= 50
                ORDER BY rank ASC
                LIMIT 10
            """, (date.today(),))
            proc_rows = cur.fetchall()
            if proc_rows:
                enriched = []
                for ticker, conf, sig_basis in proc_rows:
                    tier = _tier_label(sig_basis or "")
                    # aiem_process_predictions uses a 0–100 scale; aiem_independent_picks
                    # uses 0–10. Normalize here so the message formatter (which assumes /10)
                    # displays correctly: e.g. conf=55.7 → 5.6/10
                    conf_normalized = round(float(conf) / 10.0, 1) if conf is not None else None
                    enriched.append(("stock", ticker, conf_normalized, tier, None, None, 5))
                return enriched
            log.info("aiem_process_predictions: 0 picks above confidence threshold 50 today — no alert sent")

        return []
    finally:
        if conn:
            conn.close()


# ─────────────────────────────────────────────────────────────
# IDEMPOTENCY — this notifier owns this ONE small table itself.
# It is NOT aiem_predictions, so writing here does not reintroduce
# the two-writer collision this whole notifier exists to avoid.
# Keyed on (send_date, brief_type) so the stock brief (9:30 AM) and the
# options brief (10:30 AM) claim and record fully independently.
# ─────────────────────────────────────────────────────────────
def _ensure_notifier_log_table(conn):
    """Creates aiem_notifier_log fresh with the new (send_date, brief_type)
    composite key, OR migrates an existing pre-2026-07-01 table (which had
    send_date alone as PRIMARY KEY, one row per day total) up to the new
    schema in place. Migration path is required: this table already exists
    in production with real rows (today's already-sent combined-format
    brief), and a bare CREATE TABLE IF NOT EXISTS would silently do nothing
    on that table, leaving brief_type missing and the new options-brief
    claim would fail with 'column brief_type does not exist'."""
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS aiem_notifier_log (
            send_date  DATE NOT NULL,
            brief_type TEXT NOT NULL DEFAULT 'stock',
            claimed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            status     TEXT,
            updated_at TIMESTAMPTZ,
            PRIMARY KEY (send_date, brief_type)
        )
    """)
    # Migration for a table that already existed before this column existed.
    cur.execute("ALTER TABLE aiem_notifier_log ADD COLUMN IF NOT EXISTS brief_type TEXT NOT NULL DEFAULT 'stock'")
    cur.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON tc.constraint_name = kcu.constraint_name
                WHERE tc.table_name = 'aiem_notifier_log'
                  AND tc.constraint_type = 'PRIMARY KEY'
                  AND kcu.column_name = 'brief_type'
            ) THEN
                IF EXISTS (
                    SELECT 1 FROM information_schema.table_constraints
                    WHERE table_name = 'aiem_notifier_log' AND constraint_type = 'PRIMARY KEY'
                ) THEN
                    EXECUTE (
                        SELECT 'ALTER TABLE aiem_notifier_log DROP CONSTRAINT ' || quote_ident(constraint_name)
                        FROM information_schema.table_constraints
                        WHERE table_name = 'aiem_notifier_log' AND constraint_type = 'PRIMARY KEY'
                        LIMIT 1
                    );
                END IF;
                ALTER TABLE aiem_notifier_log ADD PRIMARY KEY (send_date, brief_type);
            END IF;
        END $$;
    """)
    conn.commit()


def _claim_todays_send(send_date, brief_type: str) -> bool:
    """Atomic claim: returns True only if THIS call won the right to send
    today's brief of this type.

    Prevents duplicate sends if two process instances are alive at once
    (e.g. during a redeploy overlap) - via the 'in_progress' exclusion below,
    which blocks a second claimant for as long as a first attempt could
    plausibly still be in flight.

    Also allows a RETRY the same day after a definitive failure (bad token,
    Telegram API error, etc.) - a transient failure must not permanently
    lock out the rest of the day. Only a *confirmed success*
    ('sent_ok=True...' or 'sent_empty ok=True...') is treated as terminal."""
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
        _ensure_notifier_log_table(conn)
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO aiem_notifier_log (send_date, brief_type, status, claimed_at)
            VALUES (%s, %s, 'in_progress', now())
            ON CONFLICT (send_date, brief_type) DO UPDATE
                SET status = 'in_progress', claimed_at = now()
                WHERE aiem_notifier_log.status NOT LIKE 'sent_ok=True%%'
                  AND aiem_notifier_log.status NOT LIKE 'sent_empty ok=True%%'
                  AND (
                        aiem_notifier_log.status <> 'in_progress'
                        OR aiem_notifier_log.claimed_at < now() - interval '10 minutes'
                      )
            RETURNING send_date
            """,
            (send_date, brief_type)
        )
        row = cur.fetchone()
        conn.commit()
        return row is not None
    finally:
        if conn:
            conn.close()


def _record_send_result(send_date, brief_type: str, status: str):
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
        cur = conn.cursor()
        cur.execute(
            "UPDATE aiem_notifier_log SET status = %s, updated_at = now() WHERE send_date = %s AND brief_type = %s",
            (status, send_date, brief_type)
        )
        conn.commit()
    except Exception as e:
        log.warning(f"_record_send_result failed (non-fatal, claim already held): {e}")
    finally:
        if conn:
            conn.close()


def _send_picks_brief(brief_type: str, pick_type: str, send_hour_label: str):
    """Shared implementation for both the 9:30 AM stock brief and the
    10:30 AM options brief. brief_type/pick_type are always in lockstep
    ('stock'/'stock' or 'options'/'call_option') - kept as two separate
    params only because the DB pick_type value and the human-readable
    brief label differ."""
    today = date.today()
    log_prefix = f"{brief_type}_picks_brief"

    try:
        won_claim = _claim_todays_send(today, brief_type)
    except Exception as e:
        log.error(f"{log_prefix}: idempotency claim failed (DB unreachable): {e}")
        _last_run.update(status=f"claim_db_error: {e}", timestamp=datetime.utcnow().isoformat())
        return

    if not won_claim:
        log.info(f"{log_prefix}: {today} already sent (or in progress) by another instance - skipping duplicate")
        _last_run.update(status="skipped_duplicate", timestamp=datetime.utcnow().isoformat())
        return

    # Skip-command check — did the owner reply SKIP within the last 90 min?
    skipped = _tg_get_skip_commands(within_minutes=90)
    skip_cat = "picks" if brief_type in ("stock", "options") else brief_type
    if skip_cat in skipped:
        log.info(f"{log_prefix}: owner replied SKIP {skip_cat.upper()} — suppressing today's {brief_type} brief")
        _record_send_result(today, brief_type, f"skipped_by_owner_command")
        return

    try:
        picks = _fetch_todays_picks(pick_type)
    except Exception as e:
        log.error(f"{log_prefix}: DB read failed: {e}")
        _last_run.update(status=f"db_error: {e}", timestamp=datetime.utcnow().isoformat())
        _record_send_result(today, brief_type, f"failed_db_error: {e}")
        return

    if not picks:
        ok = _tg_send(
            f"AIEM {send_hour_label}: No independent {brief_type} picks found for "
            f"{today.strftime('%a %b %d')} - data not ready, or AIEM found nothing "
            f"genuinely convincing today. (Read-only notifier - did not run a scan.)"
        )
        log.warning(f"{log_prefix}: no picks found in aiem_independent_picks for today (telegram sent={ok})")
        status = f"sent_empty ok={ok}"
        _last_run.update(status=status, timestamp=datetime.utcnow().isoformat())
        _record_send_result(today, brief_type, status)
        return

    label = "Stock" if brief_type == "stock" else "Options"
    header = f"AIEM Independent {label} Picks - {today.strftime('%a %b %d')} ({len(picks)})"
    sub = "AIEM's own reasoning on raw data - no pre-scored input"
    lines = []
    for i, (p_type, ticker, conf, rationale, strike, expiry, hold_days) in enumerate(picks, start=1):
        conf_txt = f"{float(conf):.1f}/10" if conf is not None else "?/10"
        short_reason = (rationale or "")[:50]
        hold_txt = f"hold ~{int(hold_days)}d" if hold_days is not None else "hold ?d"
        if p_type == "call_option":
            strike_txt = f"${float(strike):.2f}" if strike is not None else "?"
            exp_txt = expiry.strftime("%m/%d") if hasattr(expiry, "strftime") else (str(expiry) if expiry else "?")
            lines.append(f"#{i} ${ticker} CALL {strike_txt} exp {exp_txt} - {conf_txt} - {hold_txt} - {short_reason}")
        else:
            lines.append(f"#{i} ${ticker} STOCK - {conf_txt} - {hold_txt} - {short_reason}")

    # Telegram caps a single message at 4096 chars - chunk defensively so a
    # 20-pick list never silently truncates or fails to send.
    chunks, cur_chunk = [], [header, sub, "----------------------"]
    cur_len = sum(len(l) + 1 for l in cur_chunk)
    for line in lines:
        if cur_len + len(line) + 1 > 3500:
            chunks.append(cur_chunk)
            cur_chunk, cur_len = [], 0
        cur_chunk.append(line)
        cur_len += len(line) + 1
    if cur_chunk:
        chunks.append(cur_chunk)

    all_ok = True
    for idx, chunk in enumerate(chunks, start=1):
        text = "\n".join(chunk)
        if len(chunks) > 1:
            text = f"(part {idx}/{len(chunks)})\n" + text
        all_ok = _tg_send(text) and all_ok

    log.info(f"{log_prefix}: sent={all_ok} picks={len(picks)} parts={len(chunks)}")
    status = f"sent_ok={all_ok}"
    _last_run.update(status=status, timestamp=datetime.utcnow().isoformat())
    _record_send_result(today, brief_type, status)


def send_independent_stock_picks_brief():
    """09:30 AM ET Mon-Fri. Read-only w.r.t. aiem_independent_picks; sends
    AIEM's OWN independently-reasoned STOCK picks (Workstream D, stock leg) -
    up to 20, ranked by AIEM's own confidence score. Claim-before-send
    guarantees at most one send per ET calendar date even if two instances
    are alive at once."""
    _send_picks_brief("stock", "stock", "9:30 AM")


def send_independent_options_picks_brief():
    """10:30 AM ET Mon-Fri. Read-only w.r.t. aiem_independent_picks; sends
    AIEM's OWN independently-reasoned CALL OPTION picks (Workstream D,
    options leg) - up to 20, ranked by AIEM's own confidence score.
    Claim-before-send guarantees at most one send per ET calendar date even
    if two instances are alive at once."""
    _send_picks_brief("options", "call_option", "10:30 AM")


# ─────────────────────────────────────────────────────────────
# 3:00 PM RVOL / GAP / CLOSE-STRENGTH COMBO BRIEF
# Read-only w.r.t. polygon_market_daily (written by main.py's 8:35 AM
# Polygon grouped-daily scan). This process only SELECTs from it and never
# writes, same non-collision guarantee as the picks briefs above.
# ─────────────────────────────────────────────────────────────
def _td_quotes_lite(symbols: list) -> dict:
    """Minimal batch real-time quote fetch from Tradier via urllib (no
    'requests' dependency needed in this process). Returns
    {SYM: {last, prevclose}} or {} on any failure — callers must treat a
    missing/empty result as 'live price unavailable', never as zero."""
    token = os.environ.get("TRADIER_API_TOKEN_2", "") or os.environ.get("TRADIER_API_TOKEN", "")
    if not token or not symbols:
        return {}
    try:
        import urllib.parse as _uparse
        qs = _uparse.urlencode({"symbols": ",".join(str(s) for s in symbols[:200])})
        req = urllib.request.Request(
            f"https://api.tradier.com/v1/markets/quotes?{qs}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            raw = json.loads(r.read()).get("quotes", {}).get("quote", [])
        if isinstance(raw, dict):
            raw = [raw]
        return {
            q["symbol"]: {
                "last": float(q.get("last") or 0),
                "prevclose": float(q.get("prevclose") or 0),
            }
            for q in raw if q.get("symbol")
        }
    except Exception as e:
        log.warning(f"[td_quotes_lite] error: {e}")
        return {}


def _classify_combo_tier(rvol: float, gap_pct: float, close_price: float) -> str:
    """AIEM's own 2026-07-09 research finding (see verify link recorded that
    day): within the RVOL/Gap/CloseStrength combo, losers had materially
    HIGHER average RVOL than winners (11.97x vs 8.54x) while close_strength
    was nearly identical between the two groups - i.e. once volume/gap gets
    extreme the setup looks more like a one-day blow-off/exhaustion move than
    a clean continuation, regardless of how strong the close looks. AIEM's
    recommended split: 'core' = gap_pct<8, rvol<15, close_price>=$5; anything
    outside that is flagged 'exhaustion_risk' - not excluded, just labeled
    separately so the owner isn't treating a blow-off name the same as a
    clean setup. Thresholds may be refined once the fuller last-month
    per-trigger backtest AIEM ran on 2026-07-09 comes back with harder
    threshold-search results."""
    if gap_pct >= 8 or rvol >= 15 or close_price < 5:
        return "exhaustion_risk"
    return "core"


def _fetch_rvol_combo_hits(limit: int = 20):
    """Backtested combo: rvol>2.5, gap_pct>0.5, close_strength>0.6. AIEM's own
    2026-07-09 full-universe re-test (signed research session, see
    .agents/memory/rvol-gap-closestrength-3pm-alert.md) found 76.41% WR at
    next-day horizon (n=1,619) and 87.35% WR at 3-day horizon (n=166) — the
    owner's original 87.65% figure lines up with a multi-day hold, not a
    same-session/next-day grade. Reads the most recently COMPLETED session in
    polygon_market_daily — that table only ever holds finished trading days,
    so 'today' only appears here starting the following morning. Basic
    quality floor (price>=$1, no dotted warrant/rights tickers) keeps sub-$1
    noise out of a daily alert; a handful of leveraged ETFs/ETNs can still
    pass this combo since their intraday range also produces a
    close_strength value — flagged in the message body, not filtered, since
    the backtest itself did not exclude them.
    Returns (scan_date, [(ticker, rvol, gap_pct, close_strength, close_price), ...])."""
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
        cur = conn.cursor()
        cur.execute("SELECT MAX(scan_date) FROM polygon_market_daily")
        row = cur.fetchone()
        scan_date = row[0] if row else None
        if not scan_date:
            return None, []
        cur.execute(
            """
            SELECT ticker, rvol, gap_pct, close_strength, close_price
            FROM polygon_market_daily
            WHERE scan_date = %s
              AND rvol > 2.5 AND gap_pct > 0.5 AND close_strength > 0.6
              AND close_price >= 1.00
              AND ticker NOT LIKE '%%.%%'
            ORDER BY rvol DESC
            LIMIT %s
            """,
            (scan_date, limit),
        )
        return scan_date, cur.fetchall()
    finally:
        if conn:
            conn.close()


def send_rvol_combo_alert():
    """3:00 PM ET Mon-Fri. Reports the RVOL>2.5 + Gap>0.5% + Close-Strength>0.6
    combo hits from the most recently completed session, each paired with a
    LIVE Tradier quote so the owner can see today's real-time follow-through.
    Claim-before-send guarantees at most one send per ET calendar date even
    if two instances are alive at once."""
    today = date.today()
    log_prefix = "rvol_combo_alert"

    try:
        won_claim = _claim_todays_send(today, "rvol_combo")
    except Exception as e:
        log.error(f"{log_prefix}: idempotency claim failed (DB unreachable): {e}")
        _last_run.update(status=f"claim_db_error: {e}", timestamp=datetime.utcnow().isoformat())
        return

    if not won_claim:
        log.info(f"{log_prefix}: {today} already sent (or in progress) by another instance - skipping duplicate")
        _last_run.update(status="skipped_duplicate", timestamp=datetime.utcnow().isoformat())
        return

    try:
        scan_date, hits = _fetch_rvol_combo_hits(limit=20)
    except Exception as e:
        log.error(f"{log_prefix}: DB read failed: {e}")
        _last_run.update(status=f"db_error: {e}", timestamp=datetime.utcnow().isoformat())
        _record_send_result(today, "rvol_combo", f"failed_db_error: {e}")
        return

    if not scan_date or not hits:
        date_txt = f" ({scan_date.strftime('%a %b %d')})" if scan_date else ""
        ok = _tg_send(
            f"AIEM 3:00 PM Combo Alert - no RVOL>2.5/Gap>0.5%/CloseStrength>0.6 "
            f"hits found in the most recently completed session{date_txt}."
        )
        log.warning(f"{log_prefix}: no combo hits found (telegram sent={ok})")
        status = f"sent_empty ok={ok}"
        _last_run.update(status=status, timestamp=datetime.utcnow().isoformat())
        _record_send_result(today, "rvol_combo", status)
        return

    quotes = _td_quotes_lite([h[0] for h in hits])

    header = (
        f"AIEM RVOL Combo Alert - hits from {scan_date.strftime('%a %b %d')} "
        f"close ({len(hits)})"
    )
    sub = (
        "RVOL>2.5 + Gap>0.5% + CloseStrength>0.6 - AIEM re-test: 76.4% WR "
        "next-day, 87.3% WR at 3-day hold. Tiered below per AIEM's "
        "exhaustion-risk finding (high RVOL/gap can mean blow-off, not "
        "continuation)."
    )

    def _fmt_line(i, ticker, rvol, gap_pct, close_strength, prior_close):
        q = quotes.get(ticker)
        if q and q.get("last"):
            chg = (q["last"] - prior_close) / prior_close * 100 if prior_close else 0
            live_txt = f"now ${q['last']:.2f} ({chg:+.1f}% since {scan_date.strftime('%m/%d')} close)"
        else:
            live_txt = "live price unavailable"
        return (
            f"#{i} ${ticker}  RVOL {rvol:.1f}x  Gap {gap_pct:+.1f}%  "
            f"CloseStr {close_strength:.2f}  |  {live_txt}"
        )

    core_lines, exhaustion_lines = [], []
    for i, (ticker, rvol, gap_pct, close_strength, prior_close) in enumerate(hits, start=1):
        line = _fmt_line(i, ticker, rvol, gap_pct, close_strength, prior_close)
        tier = _classify_combo_tier(rvol, gap_pct, prior_close)
        (core_lines if tier == "core" else exhaustion_lines).append(line)

    lines = [header, sub, "----------------------"]
    lines.append(f"CORE ({len(core_lines)}) - moderate gap/RVOL, price>=$5:")
    lines.extend(core_lines if core_lines else ["  (none today)"])
    lines.append("")
    lines.append(
        f"⚠ EXHAUSTION-RISK ({len(exhaustion_lines)}) - extreme gap/RVOL or "
        f"sub-$5, higher blow-off odds per AIEM's research:"
    )
    lines.extend(exhaustion_lines if exhaustion_lines else ["  (none today)"])

    chunks, cur_chunk = [], []
    cur_len = 0
    for line in lines:
        if cur_len + len(line) + 1 > 3500:
            chunks.append(cur_chunk)
            cur_chunk, cur_len = [], 0
        cur_chunk.append(line)
        cur_len += len(line) + 1
    if cur_chunk:
        chunks.append(cur_chunk)

    all_ok = True
    for idx, chunk in enumerate(chunks, start=1):
        text = "\n".join(chunk)
        if len(chunks) > 1:
            text = f"(part {idx}/{len(chunks)})\n" + text
        all_ok = _tg_send(text) and all_ok

    log.info(f"{log_prefix}: sent={all_ok} hits={len(hits)} parts={len(chunks)}")
    status = f"sent_ok={all_ok}"
    _last_run.update(status=status, timestamp=datetime.utcnow().isoformat())
    _record_send_result(today, "rvol_combo", status)


# ─────────────────────────────────────────────────────────────
# HEALTH SERVER — stdlib HTTPServer, GET-only, read-only DB probe
# ─────────────────────────────────────────────────────────────
def _fetch_today_notifier_log_status(brief_type: str):
    """Cross-instance source of truth for 'did today's send actually happen',
    read from the shared DB row rather than this process's own in-memory
    _last_run (which would be wrong if a *different* instance won the claim
    and sent, e.g. during a redeploy overlap). brief_type is 'stock' or
    'options' - each brief has its own independent row."""
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=5)
        cur = conn.cursor()
        cur.execute(
            "SELECT status, updated_at FROM aiem_notifier_log WHERE send_date = %s AND brief_type = %s",
            (date.today(), brief_type)
        )
        row = cur.fetchone()
        if not row:
            return {"status": "not_run_yet", "updated_at": None}
        return {"status": row[0], "updated_at": row[1].isoformat() if row[1] else None}
    except Exception as e:
        return {"status": f"log_lookup_error: {e}", "updated_at": None}
    finally:
        if conn:
            conn.close()


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        _p = self.path.rstrip('/')
        if not (_p.endswith('/api/health') or _p.endswith('/aiem-telegram') or _p == ''):
            self.send_response(404)
            self.end_headers()
            return

        health = {
            "status":              "ok",
            "timestamp":           datetime.utcnow().isoformat(),
            "scheduler":           "unknown",
            "db":                  "unknown",
            "last_run":            _last_run,                                 # this process's own memory (whichever brief last ran)
            "today_status_stock":  _fetch_today_notifier_log_status("stock"),  # shared DB truth - use this for monitoring
            "today_status_options": _fetch_today_notifier_log_status("options"),
            "today_status_rvol_combo": _fetch_today_notifier_log_status("rvol_combo"),
            "mode":                "read_only_notifier",
        }
        try:
            health["scheduler"] = "running" if (_scheduler_ref and _scheduler_ref.running) else "stopped"
        except Exception:
            health["scheduler"] = "error"
        try:
            conn = psycopg2.connect(DATABASE_URL, connect_timeout=5)
            conn.close()
            health["db"] = "connected"
        except Exception as e:
            health["db"] = f"error: {e}"
            health["status"] = "degraded"

        body = json.dumps(health).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # suppress per-request noise from stdlib logging


def _start_health_server():
    srv = HTTPServer(("0.0.0.0", _HEALTH_PORT), _HealthHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True, name="aiem-notifier-health")
    t.start()
    log.info(f"Health endpoint: http://0.0.0.0:{_HEALTH_PORT}/api/health")


def main():
    global _scheduler_ref
    if not DATABASE_URL:
        log.error("DATABASE_URL not set - exiting")
        return

    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
        _ensure_notifier_log_table(conn)
        log.info("aiem_notifier_log table ready")
    except Exception as e:
        log.error(f"could not ensure aiem_notifier_log table at startup: {e}")
    finally:
        if conn:
            conn.close()

    _start_health_server()

    # ── Startup catch-up ────────────────────────────────────────────────────
    # If this process restarts after the scheduled send time (e.g. due to a
    # redeploy during the auditor or any other restart), fire missed briefs
    # immediately rather than waiting until tomorrow.
    #   Stock brief  → catch up if now is 9:30 AM – 4:00 PM ET on a weekday
    #   Options brief → catch up if now is 10:30 AM – 4:00 PM ET on a weekday
    def _catchup():
        import time as _time
        _time.sleep(5)  # let health server bind first
        now_et = datetime.now(ET)
        if now_et.weekday() >= 5:
            return
        now_mins = now_et.hour * 60 + now_et.minute
        close_mins = 16 * 60  # 4:00 PM ET

        def _already_sent(brief_type):
            try:
                conn = psycopg2.connect(DATABASE_URL, connect_timeout=5)
                cur = conn.cursor()
                cur.execute(
                    "SELECT status FROM aiem_notifier_log WHERE send_date=%s AND brief_type=%s",
                    (date.today(), brief_type)
                )
                row = cur.fetchone()
                conn.close()
                return row and ("sent_ok=True" in (row[0] or "") or "sent_empty ok=True" in (row[0] or ""))
            except Exception:
                return False

        # Stock brief: window 9:30 AM (570 min) → 4:00 PM (960 min)
        if 570 <= now_mins < close_mins and not _already_sent("stock"):
            log.info(f"[catchup] Missed 9:30 AM stock brief (now {now_et.strftime('%H:%M')} ET) — sending now")
            send_independent_stock_picks_brief()

        # Options brief: window 10:30 AM (630 min) → 4:00 PM (960 min)
        if 630 <= now_mins < close_mins and not _already_sent("options"):
            log.info(f"[catchup] Missed 10:30 AM options brief (now {now_et.strftime('%H:%M')} ET) — sending now")
            send_independent_options_picks_brief()

        # RVOL combo brief: window 3:00 PM (900 min) → 4:00 PM (960 min)
        if 900 <= now_mins < close_mins and not _already_sent("rvol_combo"):
            log.info(f"[catchup] Missed 3:00 PM RVOL combo alert (now {now_et.strftime('%H:%M')} ET) — sending now")
            send_rvol_combo_alert()

    threading.Thread(target=_catchup, daemon=True, name="startup-catchup").start()
    # ────────────────────────────────────────────────────────────────────────

    scheduler = BlockingScheduler(timezone=ET)
    _scheduler_ref = scheduler

    scheduler.add_job(
        send_morning_preview,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=0, timezone=ET),
        id="aiem_morning_preview",
        replace_existing=True,
    )
    scheduler.add_job(
        send_independent_stock_picks_brief,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=30, timezone=ET),
        id="aiem_independent_stock_picks_notifier",
        replace_existing=True,
    )
    scheduler.add_job(
        send_independent_options_picks_brief,
        CronTrigger(day_of_week="mon-fri", hour=10, minute=30, timezone=ET),
        id="aiem_independent_options_picks_notifier",
        replace_existing=True,
    )
    scheduler.add_job(
        send_rvol_combo_alert,
        CronTrigger(day_of_week="mon-fri", hour=15, minute=0, timezone=ET),
        id="aiem_rvol_combo_alert",
        replace_existing=True,
    )

    log.info("AIEM Telegram Notifier started — 9:00 AM preview + 9:30 AM stock + 10:30 AM options + 3:00 PM RVOL combo, Mon-Fri")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    if "--once" in sys.argv:
        log.info("Manual test mode: sending both briefs now (stock then options), scheduler NOT started")
        send_independent_stock_picks_brief()
        send_independent_options_picks_brief()
    elif "--once-rvol" in sys.argv:
        log.info("Manual test mode: sending RVOL combo alert now, scheduler NOT started")
        send_rvol_combo_alert()
    else:
        main()
