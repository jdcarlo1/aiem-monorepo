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

# ── Crash-log ring buffer (notifier Gap 3) ────────────────────────────────────
# Mirrors stdout/stderr to a 200-line in-process ring buffer, flushed to
# crash_log_buffer_notifier every 30 s by a background thread.  After a crash
# the last 200 log lines survive the restart and are readable via:
#   SELECT content FROM crash_log_buffer_notifier ORDER BY line_no;
#
# The tee is installed HERE — before any other module-level code — so every
# log line from startup onward is captured (including import-time errors).
# The flush THREAD is started from _start_notifier_crash_log_flush_thread()
# called at the bottom of module init (after scheduler and DB are set up).
import collections as _clbn_coll
import threading   as _clbn_thr

_NOTIFIER_CRASH_LOG_DEQUE: _clbn_coll.deque = _clbn_coll.deque(maxlen=200)


class _NotifierCrashLogTee:
    """Wraps stdout/stderr; mirrors every completed line to the ring buffer."""
    def __init__(self, orig):
        self._orig = orig
        self._buf  = ""
        self._lock = _clbn_thr.Lock()

    def write(self, s):
        self._orig.write(s)
        with self._lock:
            self._buf += s
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                if line.strip():
                    _NOTIFIER_CRASH_LOG_DEQUE.append(line)

    def flush(self):
        self._orig.flush()

    def fileno(self):
        return self._orig.fileno()

    def isatty(self):
        try:
            return self._orig.isatty()
        except Exception:
            return False

    def __getattr__(self, name):
        return getattr(self._orig, name)


if not isinstance(sys.stdout, _NotifierCrashLogTee):
    sys.stdout = _NotifierCrashLogTee(sys.stdout)
if not isinstance(sys.stderr, _NotifierCrashLogTee):
    sys.stderr = _NotifierCrashLogTee(sys.stderr)


def _flush_notifier_crash_log_to_db() -> None:
    """Flush ring buffer to crash_log_buffer_notifier.  Must never raise."""
    try:
        import psycopg2 as _fclbn_pg
        import os        as _fclbn_os
        import datetime  as _fclbn_dt
        _url = _fclbn_os.environ.get("DATABASE_URL")
        if not _url:
            return
        _snapshot = list(_NOTIFIER_CRASH_LOG_DEQUE)
        if not _snapshot:
            return
        _now = _fclbn_dt.datetime.utcnow()
        with _fclbn_pg.connect(_url, connect_timeout=5) as _c:
            with _c.cursor() as _cur:
                _cur.execute("""
                    CREATE TABLE IF NOT EXISTS crash_log_buffer_notifier (
                        id        BIGSERIAL    PRIMARY KEY,
                        logged_at TIMESTAMPTZ,
                        line_no   INT,
                        content   TEXT
                    )
                """)
                _cur.execute("DELETE FROM crash_log_buffer_notifier")
                _cur.executemany(
                    "INSERT INTO crash_log_buffer_notifier (logged_at, line_no, content) "
                    "VALUES (%s, %s, %s)",
                    [(_now, i, line) for i, line in enumerate(_snapshot, 1)],
                )
            _c.commit()
    except Exception as _fclbn_e:
        try:
            import sys as _fclbn_sys
            _fclbn_sys.__stdout__.write(f"[crash_log_buffer_notifier] flush error: {_fclbn_e}\n")
        except Exception:
            pass


def _start_notifier_crash_log_flush_thread() -> None:
    """Start the 30-second flush daemon.  Call once from main startup block."""
    def _loop():
        import time as _lt
        while True:
            _lt.sleep(30)
            _flush_notifier_crash_log_to_db()
    _clbn_thr.Thread(target=_loop, daemon=True, name="notifier-crash-log-flush").start()

# ── End crash-log ring buffer ─────────────────────────────────────────────────

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

# ── Socket-liveness default for every psycopg2.connect() in this process ────
# connect_timeout alone only bounds the initial TCP/SSL handshake, not a
# recv()/send() on an already-established connection. If the DB's TCP path
# dies silently (no clean FIN/RST), a raw connect() with no keepalives can
# block this process's single-threaded scheduler loop forever. See
# .agents/memory/db-pool-liveness-watchdog.md for the full incident history —
# this mirrors the fix applied to main.py's global psycopg2.connect patch.
def _make_safe_pg_connect(_orig_connect):
    def _safe(*_pa, **_pk):
        _pk.setdefault("connect_timeout", 10)
        _pk.setdefault("keepalives", 1)
        _pk.setdefault("keepalives_idle", 10)
        _pk.setdefault("keepalives_interval", 5)
        _pk.setdefault("keepalives_count", 3)
        _pk.setdefault("tcp_user_timeout", 30000)
        return _orig_connect(*_pa, **_pk)
    return _safe
psycopg2.connect = _make_safe_pg_connect(psycopg2.connect)

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
def _tg_send(text: str, *, signal_source: str = "aiem_telegram_notifier",
             ticker: str = None, alert_class: str = "INFO",
             audit_trace_id: str = None, trigger_price: float = None,
             is_test: bool = False) -> bool:
    """Send a message to the Telegram owner chat. Silent no-op when not configured.

    Also logged (fail-open, never blocks the send) to telegram_alert_ledger via
    alert_gateway.log_alert(). This process's briefs are mostly INFO-class
    (morning previews, digests); callers can pass alert_class='SIGNAL' plus a
    ticker for any individual pick line that should build a trust track record
    under the 'TELEGRAM_ALERTS' bucket."""
    token = "".join(os.environ.get("TELEGRAM_BOT_TOKEN", "").split())
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    send_text = text
    if alert_class == "SIGNAL" and signal_source != "unclassified":
        try:
            _stock_scanner_api_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                   "artifacts", "stock-scanner-api")
            if _stock_scanner_api_dir not in sys.path:
                sys.path.insert(0, _stock_scanner_api_dir)
            import alert_gateway as _ag_trust
            send_text = text + _ag_trust.get_trust_display(signal_source)
        except Exception as _te:
            log.warning(f"[telegram] trust display error (non-fatal): {_te}")
    ok = False
    if not token or not chat_id:
        log.warning("[telegram] TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not configured - skipping send")
        ok = False
    else:
        try:
            payload = json.dumps({"chat_id": chat_id, "text": send_text}).encode()
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data=payload, headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=8) as r:
                ok = json.loads(r.read()).get("ok", False)
                if not ok:
                    log.warning("[telegram] API responded without ok=true")
        except Exception as e:
            log.warning(f"[telegram] send failed: {e}")
            ok = False
    try:
        _stock_scanner_api_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                               "artifacts", "stock-scanner-api")
        if _stock_scanner_api_dir not in sys.path:
            sys.path.insert(0, _stock_scanner_api_dir)
        import alert_gateway as _ag
        _ag.log_alert(text, signal_source=signal_source, ticker=ticker,
                       alert_class=alert_class, audit_trace_id=audit_trace_id,
                       trigger_price=trigger_price, is_test=is_test, sent_ok=ok)
    except Exception as _ge:
        log.warning(f"[telegram] alert_gateway logging error (non-fatal): {_ge}")
    return ok


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
        try:
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
        except Exception as _tbl_err:
            log.warning(f"aiem_independent_picks query failed ({_tbl_err}) — going straight to fallback")
            rows = []
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
# 9:37 AM  TRIFECTA AIEM SIGNALS
# Gap-down >10% scanner — three backtested tiers by volume:
#   Tier 1: gap dn >10% + Vol >5M  →  +9.1% avg (buy open, sell close)
#   Tier 2: gap dn >10% + Vol >1M  →  +6.1% avg
#   Tier 3: gap dn >10% (any vol)  →  +4.2% avg
# Source: aiem_first_candle_data (written by first-candle module at 9:36 AM)
#   premarket_gap_pct   — gap at open vs prior close
#   first_candle_volume — 9:30-9:35 volume, used to estimate daily volume tier
# Volume tier thresholds (first-candle ≈ 8-12% of daily volume):
#   Tier 1 proxy: first_candle_volume >= 400,000  (→ ~5M+ full-day)
#   Tier 2 proxy: first_candle_volume >= 80,000   (→ ~1M+ full-day)
#   Tier 3: everything else that gapped down >10%
# Fires only when ≥1 hit exists. Idempotent via aiem_notifier_log 'trifecta'.
# ─────────────────────────────────────────────────────────────
def send_trifecta_signal_alert():
    """9:37 AM ET Mon-Fri — scan for Gap-Down >10% Trifecta AIEM Signals."""
    today = date.today()
    conn  = None

    # ── Idempotency claim ─────────────────────────────────────────────────
    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
        cur  = conn.cursor()
        cur.execute("""
            INSERT INTO aiem_notifier_log (send_date, brief_type, claimed_at, status)
            VALUES (%s, 'trifecta', NOW(), 'claimed')
            ON CONFLICT (send_date, brief_type) DO NOTHING
        """, (today,))
        conn.commit()
        cur.execute("""
            SELECT status FROM aiem_notifier_log
            WHERE send_date = %s AND brief_type = 'trifecta'
        """, (today,))
        row = cur.fetchone()
        if row and row[0] != "claimed":
            log.info(f"[trifecta] already sent today ({row[0]}), skipping")
            return
    except Exception as e:
        log.warning(f"[trifecta] DB claim error: {e}")
        return
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

    # ── Query first-candle data ───────────────────────────────────────────
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
        cur  = conn.cursor()
        cur.execute("""
            SELECT ticker, premarket_gap_pct, first_candle_volume,
                   open_price, first_candle_high, first_candle_low,
                   first_candle_close, prior_rvol
            FROM aiem_first_candle_data
            WHERE scan_date = %s
              AND premarket_gap_pct <= -10
            ORDER BY premarket_gap_pct ASC
        """, (today,))
        hits = cur.fetchall()
    except Exception as e:
        log.warning(f"[trifecta] DB query error: {e}")
        _update_status("failed", f"db_error={e}")
        return
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

    if not hits:
        log.info("[trifecta] No gap-down >10% stocks found today — no alert sent")
        _update_trifecta_status(today, "sent_empty ok=True (no hits)")
        return

    # ── Classify into three tiers ─────────────────────────────────────────
    TIER1_VOL = 400_000   # proxy for >5M full-day volume
    TIER2_VOL = 80_000    # proxy for >1M full-day volume

    tier1, tier2, tier3 = [], [], []
    for (ticker, gap_pct, vol, open_px, hi, lo, cl, prior_rvol) in hits:
        vol = vol or 0
        entry = {
            "ticker":     ticker,
            "gap_pct":    float(gap_pct or 0),
            "vol":        int(vol),
            "open_px":    float(open_px or 0),
            "hi":         float(hi or 0),
            "lo":         float(lo or 0),
            "cl":         float(cl or 0),
            "prior_rvol": float(prior_rvol or 0),
        }
        if vol >= TIER1_VOL:
            tier1.append(entry)
        elif vol >= TIER2_VOL:
            tier2.append(entry)
        else:
            tier3.append(entry)

    # ── Build message ─────────────────────────────────────────────────────
    def _fmt_row(e):
        vol_str = f"{e['vol']/1_000_000:.1f}M" if e['vol'] >= 1_000_000 else f"{e['vol']/1_000:.0f}K"
        return (f"  • {e['ticker']:<6}  gap {e['gap_pct']:+.1f}%  "
                f"vol {vol_str}  open ${e['open_px']:.2f}")

    lines = [
        f"🎯 TRIFECTA AIEM SIGNALS — {today.strftime('%a %b %-d')}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "Gap Down >10%  |  Buy at open, sell at close",
        "Backtest: buy 9:30 AM open price",
        "",
    ]

    if tier1:
        lines.append(f"🔴 TIER 1 — Gap >10% + Vol >5M  [+9.1% avg backtest]")
        for e in tier1[:6]:
            lines.append(_fmt_row(e))
        lines.append("")

    if tier2:
        lines.append(f"🟠 TIER 2 — Gap >10% + Vol >1M  [+6.1% avg backtest]")
        for e in tier2[:6]:
            lines.append(_fmt_row(e))
        lines.append("")

    if tier3:
        lines.append(f"🟡 TIER 3 — Gap >10% (any vol)  [+4.2% avg backtest]")
        for e in tier3[:6]:
            lines.append(_fmt_row(e))
        lines.append("")

    total = len(tier1) + len(tier2) + len(tier3)
    lines.append(f"Total hits: {total}  |  T1={len(tier1)} T2={len(tier2)} T3={len(tier3)}")
    lines.append("⚠️ Rare signal — trade with 3% stop-loss")

    msg = "\n".join(lines)

    ok = _tg_send(msg, signal_source="trifecta_aiem_signal",
                  alert_class="SIGNAL")
    log.info(f"[trifecta] Sent alert for {total} hits — ok={ok}")
    _update_trifecta_status(today, f"sent_ok={ok} hits={total} "
                                   f"t1={len(tier1)} t2={len(tier2)} t3={len(tier3)}")


def _update_trifecta_status(today, status: str):
    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=5)
        cur  = conn.cursor()
        cur.execute("""
            UPDATE aiem_notifier_log SET status=%s, updated_at=NOW()
            WHERE send_date=%s AND brief_type='trifecta'
        """, (status, today))
        conn.commit()
        conn.close()
    except Exception as e:
        log.warning(f"[trifecta] status update error: {e}")


def _update_notifier_status(send_date, brief_type: str, status: str):
    """Generic notifier log status updater."""
    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=5)
        cur  = conn.cursor()
        cur.execute("""
            UPDATE aiem_notifier_log SET status=%s, updated_at=NOW()
            WHERE send_date=%s AND brief_type=%s
        """, (status, send_date, brief_type))
        conn.commit()
        conn.close()
    except Exception as e:
        log.warning(f"[notifier-log] status update error ({brief_type}): {e}")


# ─────────────────────────────────────────────────────────────
# 8:50 AM  AIEM PATTERN ENGINE — Multi-day momentum & reversal
#
# Nine backtested patterns discovered by AIEM on polygon_market_daily.
# All computed from last 35 trading days of OHLCV data. Fires premarket
# so user can enter at today's open. Only sends when ≥1 pattern has hits.
# Idempotent via aiem_notifier_log brief_type='pattern_engine'.
#
# Patterns (sorted by win rate):
#   ROC-12 < -10%              — 10d 81.82% / 5d 75.07% / 3d 63.05%
#   High ATR >3% + momentum    — 5d  74.47%
#   High ATR >3%               — 10d 75.0%
#   10-day momentum positive   — 5d  73.87% / 10d 70.85%
#   Williams %R + MFI oversold — 5d  72.01% / 3d 65.22%
#   MACD bearish + ADX >25     — 10d 71.53%
#   Washout RSI+Stoch+Will     — 10d 67.82%
#   Stoch + CCI oversold       — 5d  67.09%
#   CMF outflow + MACD bearish — 5d  66.65%
# ─────────────────────────────────────────────────────────────
def send_pattern_engine_alert():
    """8:50 AM ET Mon-Fri — AIEM Pattern Engine multi-day signals."""
    today = date.today()
    conn  = None

    # ── Idempotency claim ─────────────────────────────────────────────────
    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
        cur  = conn.cursor()
        cur.execute("""
            INSERT INTO aiem_notifier_log (send_date, brief_type, claimed_at, status)
            VALUES (%s, 'pattern_engine', NOW(), 'claimed')
            ON CONFLICT (send_date, brief_type) DO NOTHING
        """, (today,))
        conn.commit()
        cur.execute("""
            SELECT status FROM aiem_notifier_log
            WHERE send_date=%s AND brief_type='pattern_engine'
        """, (today,))
        row = cur.fetchone()
        if row and row[0] != "claimed":
            log.info(f"[pattern-engine] already sent ({row[0]}), skipping")
            return
    except Exception as e:
        log.warning(f"[pattern-engine] DB claim error: {e}")
        return
    finally:
        if conn:
            try: conn.close()
            except: pass

    # ── Pull last 35 trading days of OHLCV ───────────────────────────────
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
        cur  = conn.cursor()
        cur.execute("""
            SELECT DISTINCT scan_date FROM polygon_market_daily
            ORDER BY scan_date DESC LIMIT 35
        """)
        dates = [r[0] for r in cur.fetchall()]
        if not dates:
            log.warning("[pattern-engine] no data in polygon_market_daily")
            _update_notifier_status(today, 'pattern_engine', 'no_data')
            return
        latest_date = dates[0]
        min_date    = dates[-1]

        cur.execute("""
            SELECT ticker, scan_date,
                   close_price, high_price, low_price, volume
            FROM polygon_market_daily
            WHERE scan_date >= %s
              AND close_price > 1.0
              AND volume     > 100000
            ORDER BY ticker, scan_date
        """, (min_date,))
        rows = cur.fetchall()
    except Exception as e:
        log.warning(f"[pattern-engine] DB fetch error: {e}")
        _update_notifier_status(today, 'pattern_engine', f'db_error={e}')
        return
    finally:
        if conn:
            try: conn.close()
            except: pass

    if not rows:
        _update_notifier_status(today, 'pattern_engine', 'no_data')
        return

    # ── Compute indicators with pandas ───────────────────────────────────
    try:
        import pandas as _pd
        import numpy  as _np

        df = _pd.DataFrame(rows, columns=['ticker','scan_date','close','high','low','volume'])
        df = df.sort_values(['ticker','scan_date'])

        # Drop tickers with < 27 rows (need 26 for MACD + 1 diff)
        counts = df.groupby('ticker').size()
        df = df[df['ticker'].isin(counts[counts >= 27].index)].copy()

        def _compute(g):
            g = g.sort_values('scan_date').copy()
            c, h, l, v = g['close'], g['high'], g['low'], g['volume']

            # ROC 12-day
            g['roc12'] = (c - c.shift(12)) / (c.shift(12) + 1e-9) * 100

            # ATR% 14 (H-L range proxy / close)
            g['atr_pct'] = (h - l).rolling(14).mean() / (c + 1e-9) * 100

            # 10-day momentum (signed price change)
            g['mom10'] = c - c.shift(10)

            # Williams %R 14
            h14 = h.rolling(14).max()
            l14 = l.rolling(14).min()
            g['willr'] = (h14 - c) / (h14 - l14 + 1e-9) * -100

            # Typical price
            tp = (h + l + c) / 3

            # MFI 14
            mf      = tp * v
            pos_mf  = mf.where(tp > tp.shift(1), 0.0)
            neg_mf  = mf.where(tp < tp.shift(1), 0.0)
            pmf14   = pos_mf.rolling(14).sum()
            nmf14   = neg_mf.rolling(14).sum()
            g['mfi'] = 100 - (100 / (1 + pmf14 / (nmf14 + 1e-9)))

            # RSI 14
            delta    = c.diff()
            gain     = delta.where(delta > 0, 0.0)
            loss     = (-delta).where(delta < 0, 0.0)
            avg_gain = gain.ewm(com=13, adjust=False).mean()
            avg_loss = loss.ewm(com=13, adjust=False).mean()
            g['rsi'] = 100 - (100 / (1 + avg_gain / (avg_loss + 1e-9)))

            # Stoch %K 14
            g['stoch'] = (c - l14) / (h14 - l14 + 1e-9) * 100

            # MACD (12,26,9) — bearish when macd < signal
            ema12       = c.ewm(span=12, adjust=False).mean()
            ema26       = c.ewm(span=26, adjust=False).mean()
            macd        = ema12 - ema26
            g['macd']   = macd
            g['macd_sig'] = macd.ewm(span=9, adjust=False).mean()

            # ADX 14
            tr   = _pd.concat([h-l, (h-c.shift(1)).abs(), (l-c.shift(1)).abs()], axis=1).max(axis=1)
            h_d  = h - h.shift(1)
            l_d  = l.shift(1) - l
            dm_p = h_d.where((h_d > l_d) & (h_d > 0), 0.0)
            dm_n = l_d.where((l_d > h_d) & (l_d > 0), 0.0)
            atr14    = tr.ewm(alpha=1/14, adjust=False).mean()
            di_p     = dm_p.ewm(alpha=1/14, adjust=False).mean() / (atr14 + 1e-9) * 100
            di_n     = dm_n.ewm(alpha=1/14, adjust=False).mean() / (atr14 + 1e-9) * 100
            dx       = (di_p - di_n).abs() / (di_p + di_n + 1e-9) * 100
            g['adx'] = dx.ewm(alpha=1/14, adjust=False).mean()

            # CCI 20
            tp_m20   = tp.rolling(20).mean()
            tp_mad20 = tp.rolling(20).apply(lambda x: (x - x.mean()).abs().mean(), raw=True)
            g['cci'] = (tp - tp_m20) / (0.015 * tp_mad20 + 1e-9)

            # CMF 20
            mfv      = ((c - l) - (h - c)) / (h - l + 1e-9) * v
            g['cmf'] = mfv.rolling(20).sum() / (v.rolling(20).sum() + 1e-9)

            return g

        df = df.groupby('ticker', group_keys=False).apply(_compute)

        # Only evaluate the most recent date
        latest = df[df['scan_date'] == latest_date].dropna(
            subset=['roc12','atr_pct','mom10','willr','mfi','rsi','stoch',
                    'macd','macd_sig','adx','cci','cmf']
        ).copy()

    except Exception as e:
        log.warning(f"[pattern-engine] indicator compute error: {e}")
        _update_notifier_status(today, 'pattern_engine', f'compute_error={e}')
        return

    if latest.empty:
        log.info(f"[pattern-engine] no valid indicators for {latest_date}")
        _update_notifier_status(today, 'pattern_engine', f'no_latest date={latest_date}')
        return

    # ── Apply pattern conditions ──────────────────────────────────────────
    patterns_found = []

    def _hit(mask, label, horizon, wr, emoji, sort_col, ascending=True, limit=8):
        sub = latest[mask]
        if sub.empty:
            return
        tickers = sub.sort_values(sort_col, ascending=ascending)['ticker'].head(limit).tolist()
        patterns_found.append((emoji, label, horizon, wr, tickers))

    # 1. ROC-12 < -10% (deeply oversold) — 81.82% WR 10d
    _hit(latest['roc12'] < -10,
         "12-Day ROC < -10%  (Deeply Oversold)",
         "5-10d hold", "75-82% WR", "🔴", 'roc12', ascending=True)

    # 2. High ATR >3% + 10d momentum positive — 74.47% WR 5d
    _hit((latest['atr_pct'] > 3) & (latest['mom10'] > 0),
         "High ATR >3% + Momentum Positive",
         "5d hold", "74.5% WR", "🟠", 'atr_pct', ascending=False)

    # 3. High ATR >3% + momentum flat/negative — 75.0% WR 10d
    _hit((latest['atr_pct'] > 3) & (latest['mom10'] <= 0),
         "High ATR >3%  (Momentum Neutral/Negative)",
         "10d hold", "75.0% WR", "🟠", 'atr_pct', ascending=False)

    # 4. 10-day momentum positive, low ATR — 73.87% WR 5d
    _hit((latest['mom10'] > 0) & (latest['atr_pct'] <= 3),
         "10-Day Momentum Positive  (Steady)",
         "5-10d hold", "71-74% WR", "🟢", 'mom10', ascending=False)

    # 5. Williams %R + MFI both oversold — 72.01% WR 5d
    _hit((latest['willr'] < -80) & (latest['mfi'] < 20),
         "Williams %R + MFI Oversold",
         "3-5d hold", "65-72% WR", "🔵", 'willr', ascending=True)

    # 6. MACD bearish + ADX trending >25 — 71.53% WR 10d
    _hit((latest['macd'] < latest['macd_sig']) & (latest['adx'] > 25),
         "MACD Bearish + ADX Trending (>25)",
         "10d hold", "71.5% WR", "🟣", 'adx', ascending=False)

    # 7. Washout — RSI + Stoch + Williams all oversold — 67.82% WR 10d
    _hit((latest['rsi'] < 30) & (latest['stoch'] < 20) & (latest['willr'] < -80),
         "Washout — RSI + Stoch + Williams All Oversold",
         "10d hold", "67.8% WR", "⚫", 'rsi', ascending=True)

    # 8. Stoch + CCI both oversold — 67.09% WR 5d
    _hit((latest['stoch'] < 20) & (latest['cci'] < -100),
         "Stoch + CCI Oversold",
         "5d hold", "67.1% WR", "🟤", 'cci', ascending=True)

    # 9. CMF outflow + MACD bearish — 66.65% WR 5d
    _hit((latest['cmf'] < 0) & (latest['macd'] < latest['macd_sig']),
         "CMF Outflow + MACD Bearish",
         "5d hold", "66.7% WR", "🟤", 'cmf', ascending=True)

    if not patterns_found:
        log.info("[pattern-engine] no pattern hits today — silent skip")
        _update_notifier_status(today, 'pattern_engine', 'sent_empty ok=True (no hits)')
        return

    # ── Build and send Telegram message ───────────────────────────────────
    lines = [
        f"📊 AIEM PATTERN ENGINE — {latest_date.strftime('%a %b %-d')}",
        "Multi-day reversal & momentum signals",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
    ]
    total_stocks = 0
    for emoji, label, horizon, wr, tickers in patterns_found:
        total_stocks += len(tickers)
        ticker_str = "  ".join(tickers[:6])
        extra = f"  +{len(tickers)-6} more" if len(tickers) > 6 else ""
        lines.append(f"{emoji} {label}  |  {wr}  |  {horizon}")
        lines.append(f"   {ticker_str}{extra}")
        lines.append("")

    lines.append(f"Patterns: {len(patterns_found)}  |  Stocks: {total_stocks}")
    lines.append(f"Based on {latest_date.strftime('%b %-d')} close data")
    lines.append("⚠️ Multi-day holds — not same-day trades")

    msg = "\n".join(lines)
    ok = _tg_send(msg, signal_source="aiem_pattern_engine", alert_class="SIGNAL")
    log.info(f"[pattern-engine] alert sent ok={ok} patterns={len(patterns_found)} stocks={total_stocks}")
    _update_notifier_status(today, 'pattern_engine',
                            f"sent_ok={ok} patterns={len(patterns_found)} stocks={total_stocks}")


# ═══════════════════════════════════════════════════════════════════════════
# AIEM INDEPENDENT TAB SCAN ENGINE
# ───────────────────────────────────────────────────────────────────────────
# AIEM scans Polygon directly — no dependency on main.py DB writes.
# One master scan at 9:35 AM populates _TAB_CACHE for all 18 morning alerts.
# Every send function falls back to the DB if cache is empty, and falls back
# to a "no data" message if both are empty — never crashes.
# ═══════════════════════════════════════════════════════════════════════════

import json as _json_mod
import threading as _thr
from concurrent.futures import ThreadPoolExecutor as _TPE
from datetime import timedelta as _td
from collections import Counter as _Counter

_POLY_KEY          = os.environ.get("POLYGON_API_KEY", "")
_TAB_CACHE: dict   = {}
_TAB_CACHE_DATE    = None
_TAB_CACHE_LOCK    = _thr.Lock()
_TAB_SCAN_RUNNING  = False

_ETF_SET = {
    "SPY","QQQ","IWM","DIA","GLD","TLT","XLF","XLK","XLE","XLV","XLI",
    "XLU","XLRE","XLC","XLY","XLP","XLB","SMH","ARKK","SOXX","XBI","IBB",
    "MSTR","SOXL","TQQQ","SQQQ","SPXL","UPRO","IYR","HYG","LQD","EEM",
    "FXI","KWEB","EWZ","USO","GDX","GDXJ","COIN",
}


def _log_polygon_api_call_nt(caller: str, endpoint: str, http_status, error_msg, rows: int, elapsed_ms: int):
    """Best-effort write to polygon_api_calls. Never raises."""
    try:
        import psycopg2 as _lpg
        _db = DATABASE_URL
        if not _db:
            return
        with _lpg.connect(_db, connect_timeout=3) as _lc, _lc.cursor() as _lcu:
            _lcu.execute("""
                CREATE TABLE IF NOT EXISTS polygon_api_calls (
                    id          BIGSERIAL PRIMARY KEY,
                    ts          TIMESTAMPTZ DEFAULT NOW(),
                    caller      TEXT NOT NULL,
                    endpoint    TEXT,
                    http_status INTEGER,
                    error_msg   TEXT,
                    rows_returned INTEGER,
                    elapsed_ms  INTEGER
                )
            """)
            _lcu.execute(
                "INSERT INTO polygon_api_calls (caller,endpoint,http_status,error_msg,rows_returned,elapsed_ms) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (caller, endpoint, http_status, error_msg, rows, elapsed_ms),
            )
            _lc.commit()
    except Exception:
        pass


def _poly_req(path: str, timeout: int = 12) -> dict:
    if not _POLY_KEY:
        return {}
    import urllib.error as _ue_nt, time as _t_nt
    sep = "&" if "?" in path else "?"
    url = f"https://api.polygon.io{path}{sep}apiKey={_POLY_KEY}"
    _t0 = _t_nt.monotonic()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AIEM-Notifier/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            _http_status = r.status
            _body = _json_mod.loads(r.read().decode("utf-8", errors="replace"))
        _rows = len(_body.get("results", [])) if isinstance(_body, dict) else 0
        _log_polygon_api_call_nt("notifier", path.split("?")[0], _http_status, None,
                                  _rows, int((_t_nt.monotonic()-_t0)*1000))
        return _body
    except _ue_nt.HTTPError as _he:
        log.warning(f"[poly_req] {path[:70]} → HTTP {_he.code} {_he.reason}")
        _log_polygon_api_call_nt("notifier", path.split("?")[0], _he.code, str(_he),
                                  0, int((_t_nt.monotonic()-_t0)*1000))
        return {}
    except Exception as e:
        log.debug(f"[poly_req] {path[:70]} → {e}")
        _log_polygon_api_call_nt("notifier", path.split("?")[0], None, str(e),
                                  0, int((_t_nt.monotonic()-_t0)*1000))
        return {}


def _poly_grouped_daily(date_str: str) -> list:
    d = _poly_req(f"/v2/aggs/grouped/locale/us/market/stocks/{date_str}?adjusted=true&include_otc=false")
    return d.get("results", [])


def _poly_options_for_ticker(ticker: str, spot: float, today_date) -> list:
    d = _poly_req(
        f"/v3/snapshot/options/{ticker}?contract_type=call&limit=250&sort=volume&order=desc",
        timeout=12,
    )
    out = []
    for r in d.get("results", []):
        detail  = r.get("details", {})
        day_d   = r.get("day", {})
        strike  = float(detail.get("strike_price", 0) or 0)
        expiry  = detail.get("expiration_date", "") or ""
        vol     = int(day_d.get("volume", 0) or 0)
        oi      = int(r.get("open_interest", 0) or 0)
        iv      = float(r.get("implied_volatility", 0) or 0)
        last_px = float(day_d.get("close", 0) or day_d.get("vwap", 0) or 0)
        if vol < 10 or strike <= 0 or not expiry:
            continue
        vol_oi  = round(vol / oi, 1) if oi > 0 else 0.0
        otm_pct = round((strike - spot) / spot * 100, 1) if spot > 0 else 0.0
        prem_m  = (round(vol * last_px / 1_000_000, 3)
                   if last_px > 0
                   else round(vol * max(strike - spot, 0.5) / 1_000_000, 3))
        try:
            from datetime import date as _dt_d
            days_out = max(0, (_dt_d.fromisoformat(expiry) - today_date).days)
        except Exception:
            days_out = 0
        if days_out < 0:
            continue
        if   days_out <= 3:    urgency = "EXPIRING"
        elif days_out <= 14:   urgency = "NEAR"
        elif days_out <= 45:   urgency = "SHORT"
        else:                  urgency = "LONG"
        if otm_pct > 40 and vol_oi >= 5 and prem_m >= 0.2:
            urgency = "FAR"
        out.append({
            "ticker": ticker, "price": spot, "strike": strike,
            "expiry": expiry, "days_out": days_out,
            "volume": vol, "oi": oi, "vol_oi": vol_oi,
            "prem_m": prem_m, "otm_pct": otm_pct,
            "iv_pct": round(iv * 100, 1), "urgency": urgency,
            "is_etf": ticker in _ETF_SET,
        })
    return out


def _prior_td(d):
    dd = d - _td(days=1)
    while dd.weekday() >= 5:
        dd -= _td(days=1)
    return dd


def _cs(b: dict) -> float:
    c, l, h = b.get("c", 0), b.get("l", 0), b.get("h", 0)
    if h <= l:
        return 0.5
    return round((c - l) / (h - l), 3)


def _fmt_call(c: dict) -> str:
    return (f"${c['ticker']} ${c['strike']:.0f}C {c['expiry']} "
            f"| {c['vol_oi']:.1f}x | ${c['prem_m']:.2f}M | {c['otm_pct']:+.0f}% | {c['urgency']}")


def _claim_tab(today, brief_type: str) -> bool:
    try:
        with psycopg2.connect(DATABASE_URL) as _conn:
            cur = _conn.cursor()
            cur.execute(
                "INSERT INTO aiem_notifier_log (send_date, brief_type, claimed_at, status) "
                "VALUES (%s, %s, NOW(), 'claimed') ON CONFLICT (send_date, brief_type) DO NOTHING",
                (today, brief_type),
            )
            _conn.commit()
            return cur.rowcount > 0
    except Exception as e:
        log.error(f"[claim_tab] {brief_type}: {e}")
        return False


def _aiem_tab_scan(force: bool = False):
    """
    AIEM independent full-market Polygon scan.
    Pulls grouped daily bars + top 400 tickers' call chains.
    Populates _TAB_CACHE with structured data for all 18 tab alerts.
    Runs once per trading day at 9:35 AM; idempotent.
    """
    global _TAB_CACHE, _TAB_CACHE_DATE, _TAB_SCAN_RUNNING

    today = datetime.now(ET).date()
    with _TAB_CACHE_LOCK:
        if not force and _TAB_CACHE_DATE == today and _TAB_CACHE:
            return
        if _TAB_SCAN_RUNNING:
            return
        _TAB_SCAN_RUNNING = True

    try:
        log.info("[tab-scan] ▶ AIEM independent Polygon market scan starting …")

        # ── Grouped daily bars ────────────────────────────────────────────────
        date_str = today.strftime("%Y-%m-%d")
        bars = _poly_grouped_daily(date_str)
        if len(bars) < 500:
            prev = _prior_td(today)
            date_str = prev.strftime("%Y-%m-%d")
            bars = _poly_grouped_daily(date_str)
            log.info(f"[tab-scan] today sparse, fell back to {date_str} ({len(bars)} bars)")

        prev2_str  = _prior_td(_prior_td(today)).strftime("%Y-%m-%d")
        bars_prev  = _poly_grouped_daily(prev2_str)
        prev_v_map = {b.get("T", ""): int(b.get("v", 0) or 0) for b in bars_prev if b.get("T")}

        bar_map: dict = {}
        for b in bars:
            t = b.get("T", "")
            if not t or "." in t or len(t) > 5:
                continue
            price = float(b.get("c", 0) or 0)
            if price < 1.0:
                continue
            vol   = int(b.get("v", 0) or 0)
            pv    = prev_v_map.get(t, vol or 1)
            rvol  = round(vol / pv, 2) if pv > 0 else 1.0
            b_enr = dict(b)
            b_enr.update({
                "ticker": t, "price": price, "volume": vol, "rvol": rvol,
                "close_strength": _cs(b), "is_etf": t in _ETF_SET,
            })
            bar_map[t] = b_enr

        log.info(f"[tab-scan] {len(bar_map)} stocks loaded from {date_str}")

        # ── Options scan — top 400 by volume ─────────────────────────────────
        top400 = sorted(bar_map.values(), key=lambda x: x["volume"], reverse=True)[:400]
        log.info(f"[tab-scan] querying options for {len(top400)} tickers …")
        all_calls: list = []

        def _scan_one(stock):
            return _poly_options_for_ticker(stock["ticker"], stock["price"], today)

        with _TPE(max_workers=10) as pool:
            for contracts in pool.map(_scan_one, top400, timeout=60):
                if contracts:
                    all_calls.extend(contracts)

        log.info(f"[tab-scan] {len(all_calls)} call contracts collected")

        cache: dict = {
            "scan_date": today, "bar_date": date_str,
            "total_tickers": len(bar_map), "total_calls": len(all_calls),
        }

        # ── Tab buckets ───────────────────────────────────────────────────────

        cache["unusual_calls"] = sorted(
            [c for c in all_calls if c["vol_oi"] >= 2.0 and c["prem_m"] >= 0.1 and not c["is_etf"]],
            key=lambda x: x["vol_oi"], reverse=True)[:15]

        cache["hc_calls"] = sorted(
            [c for c in all_calls if c["vol_oi"] >= 5.0 and c["prem_m"] >= 0.5 and not c["is_etf"]],
            key=lambda x: x["vol_oi"] * x["prem_m"], reverse=True)[:12]

        cache["whale"] = sorted(
            [c for c in all_calls if c["prem_m"] >= 1.0 and c["vol_oi"] >= 3.0],
            key=lambda x: x["prem_m"], reverse=True)[:12]

        cache["hc_etfs"] = sorted(
            [c for c in all_calls if c["is_etf"] and c["prem_m"] >= 0.5 and c["vol_oi"] >= 3.0],
            key=lambda x: x["prem_m"], reverse=True)[:12]

        cache["sweep_radar"] = sorted(
            [c for c in all_calls if c["urgency"] == "FAR"],
            key=lambda x: x["vol_oi"], reverse=True)[:12]

        cache["microcap"] = sorted(
            [c for c in all_calls if c["price"] <= 20 and c["vol_oi"] >= 3.0 and not c["is_etf"]],
            key=lambda x: x["vol_oi"], reverse=True)[:12]

        cache["bull_flow"] = sorted(
            [c for c in all_calls if c["vol_oi"] >= 2.0 and c["volume"] >= 300 and not c["is_etf"]],
            key=lambda x: x["volume"], reverse=True)[:12]

        # Persistence — tickers with 3+ active call strikes (broad positioning)
        tcounts = _Counter(c["ticker"] for c in all_calls if c["vol_oi"] >= 2.0)
        persist_rows = []
        for t, cnt in tcounts.most_common(20):
            if cnt < 3:
                break
            best = dict(max((c for c in all_calls if c["ticker"] == t), key=lambda x: x["vol_oi"]))
            best["strike_count"] = cnt
            persist_rows.append(best)
        cache["persistence"] = persist_rows[:12]

        # Insider radar — high OTM, large prem, quiet ticker, not ETF
        def _ins_score(c):
            s = 0
            if c["otm_pct"] >= 20:  s += 3
            if c["prem_m"]  >= 0.5: s += 2
            if c["vol_oi"]  >= 10:  s += 2
            if c["volume"]  >= 1000: s += 1
            if c["days_out"] <= 30:  s += 1
            return s
        ins_cands = [c for c in all_calls if c["otm_pct"] >= 15 and c["prem_m"] >= 0.1 and not c["is_etf"]]
        for c in ins_cands:
            c["ins_score"] = _ins_score(c)
        cache["insider_radar"] = sorted(ins_cands, key=lambda x: x["ins_score"], reverse=True)[:12]

        cache["gamma_squeeze"] = sorted(
            [c for c in all_calls if c["vol_oi"] >= 5.0 and c["volume"] >= 500
             and c["price"] >= 5 and not c["is_etf"]],
            key=lambda x: x["vol_oi"], reverse=True)[:12]

        cache["oi_buildup"] = sorted(
            [c for c in all_calls if c["oi"] >= 1000 and c["vol_oi"] >= 3.0],
            key=lambda x: x["oi"], reverse=True)[:15]

        # Flow streak — high close-strength + high RVOL
        cache["flow_streak"] = sorted(
            [b for b in bar_map.values()
             if b["close_strength"] >= 0.65 and b.get("rvol", 0) >= 1.5 and b["volume"] >= 300_000],
            key=lambda x: x["close_strength"] * x.get("rvol", 1), reverse=True)[:15]

        # Steady grinders — sweep-confirmed + high close strength
        call_tk_set = {c["ticker"] for c in all_calls if c["vol_oi"] >= 2.0}
        grinders = []
        for b in cache["flow_streak"]:
            t = b["ticker"]
            if t in call_tk_set:
                best_c = max((c for c in all_calls if c["ticker"] == t), key=lambda x: x["vol_oi"], default=None)
                if best_c:
                    row = dict(b)
                    row.update({"vol_oi": best_c["vol_oi"], "prem_m": best_c["prem_m"],
                                "strike": best_c["strike"], "expiry": best_c["expiry"]})
                    grinders.append(row)
        cache["steady_grinders"] = grinders[:12]

        # 8-Layer conviction stack — multi-signal score per ticker
        tsig: dict = {}
        for c in all_calls:
            t = c["ticker"]
            if t not in tsig:
                tsig[t] = {"ticker": t, "price": c["price"], "pts": 0, "layers": []}
            info = tsig[t]
            b    = bar_map.get(t, {})
            if c["vol_oi"] >= 5 and "L1_OI" not in info["layers"]:
                info["pts"] += 2; info["layers"].append("L1_OI")
            if c["vol_oi"] >= 3 and c["prem_m"] >= 0.2 and "L2_SWEEP" not in info["layers"]:
                info["pts"] += 2; info["layers"].append("L2_SWEEP")
            if c["urgency"] == "FAR" and "L7_FAR" not in info["layers"]:
                info["pts"] += 2; info["layers"].append("L7_FAR")
            if b.get("close_strength", 0) >= 0.7 and "CS" not in info["layers"]:
                info["pts"] += 2; info["layers"].append("CS")
            if b.get("rvol", 0) >= 2.0 and "RVOL" not in info["layers"]:
                info["pts"] += 2; info["layers"].append("RVOL")
        stk = sorted(tsig.values(), key=lambda x: x["pts"], reverse=True)
        cache["conviction_stack"]     = stk[:15]
        cache["smart_money_pressure"] = [x for x in stk if x["pts"] >= 6][:10]

        # Today's picks — probability engine DB
        picks = []
        try:
            with psycopg2.connect(DATABASE_URL) as _pc:
                cur = _pc.cursor()
                cur.execute(
                    "SELECT rank, ticker, score, prob_up_1d, prob_up_2d, prob_up_3d, confidence, regime_tag "
                    "FROM aiem_probability_engine_daily_picks WHERE pick_date=%s ORDER BY rank LIMIT 10",
                    (today,))
                picks = [dict(zip(["rank","ticker","score","p1d","p2d","p3d","conf","regime"], r))
                         for r in cur.fetchall()]
        except Exception as e:
            log.debug(f"[tab-scan] todays_picks DB: {e}")
        cache["todays_picks"] = picks

        cache["eod_sweep"]  = []
        cache["dark_pool"]  = []

        with _TAB_CACHE_LOCK:
            _TAB_CACHE      = cache
            _TAB_CACHE_DATE = today

        log.info(
            f"[tab-scan] ✅ Cache ready — unusual={len(cache['unusual_calls'])} "
            f"hc={len(cache['hc_calls'])} whale={len(cache['whale'])} "
            f"streak={len(cache['flow_streak'])} grinders={len(cache['steady_grinders'])}"
        )

    except Exception as e:
        log.error(f"[tab-scan] FATAL: {e}", exc_info=True)
    finally:
        with _TAB_CACHE_LOCK:
            _TAB_SCAN_RUNNING = False


def _get_tab(key: str) -> list:
    """Return tab data from cache, triggering scan if stale/missing."""
    today = datetime.now(ET).date()
    with _TAB_CACHE_LOCK:
        ready = (_TAB_CACHE_DATE == today and bool(_TAB_CACHE))
    if not ready:
        t = _thr.Thread(target=_aiem_tab_scan, daemon=True)
        t.start()
        t.join(timeout=300)
    return _TAB_CACHE.get(key, [])


def run_aiem_tab_scan_job():
    """9:35 AM — pre-warm cache for all morning tab briefs."""
    log.info("[tab-scan-job] ▶ scheduler triggered")
    _thr.Thread(target=_aiem_tab_scan, daemon=True).start()


# ─────────────────────────────────────────────────────────────────────────────
# OI BUILDUP  ·  8:55 AM
# ─────────────────────────────────────────────────────────────────────────────
def send_oi_buildup_brief():
    today = datetime.now(ET).date()
    if not _claim_tab(today, "oi_buildup"):
        return
    try:
        with psycopg2.connect(DATABASE_URL) as _c:
            cur = _c.cursor()
            cur.execute(
                "SELECT ticker, price, strike, expiry, oi, otm_pct, days_out, iv "
                "FROM oi_daily_snapshot WHERE snapshot_date=%s ORDER BY oi DESC LIMIT 12",
                (_prior_td(today),))
            db_rows = cur.fetchall()
    except Exception:
        db_rows = []
    rows = _get_tab("oi_buildup")
    lines = [f"📈 OI BUILDUP — {today.strftime('%b %-d, %Y')}",
             "Smart money pre-loading OTM calls 1-3 days before the move",
             "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
    if db_rows:
        for r in db_rows[:12]:
            lines.append(f"${r[0]} ${r[2]:.0f}C {r[3]} | OI={r[4]:,} | {r[5]:+.0f}% OTM | IV={r[7]:.0f}%")
    elif rows:
        for i, c in enumerate(rows[:12], 1):
            lines.append(f"{i}. ${c['ticker']} ${c['strike']:.0f}C {c['expiry']} | OI={c['oi']:,} | {c['vol_oi']:.1f}x | {c['otm_pct']:+.0f}%")
    else:
        lines.append("⚠️ No OI buildup signals — snapshot captures at 4:30 PM daily")
    ok = _tg_send("\n".join(lines), signal_source="oi_buildup_tab", alert_class="SIGNAL")
    _update_notifier_status(today, "oi_buildup", f"sent_ok={ok}")


# ─────────────────────────────────────────────────────────────────────────────
# TODAY'S PICKS (S1B · S1C · S1D)  ·  9:40 AM
# ─────────────────────────────────────────────────────────────────────────────
def send_todays_picks_brief():
    today = datetime.now(ET).date()
    if not _claim_tab(today, "todays_picks"):
        return
    picks = _get_tab("todays_picks")
    lines = [f"⚡ TODAY'S PICKS (S1B·S1C·S1D) — {today.strftime('%b %-d, %Y')}",
             "AIEM Probability Engine · Buy at 9:30 AM open · results at close",
             "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
    for p in picks[:10]:
        lines.append(
            f"#{p['rank']} ${p['ticker']} | Score={p['score']:.1f} "
            f"| P1d={p['p1d']:.0%} P2d={p['p2d']:.0%} P3d={p['p3d']:.0%} | {p.get('regime','—')}"
        )
    if not picks:
        lines.append("⚠️ No picks yet — probability scan runs 7:00–9:15 AM ET")
    ok = _tg_send("\n".join(lines), signal_source="todays_picks_tab", alert_class="SIGNAL")
    _update_notifier_status(today, "todays_picks", f"sent_ok={ok} picks={len(picks)}")


# ─────────────────────────────────────────────────────────────────────────────
# GAMMA SQUEEZE  ·  9:50 AM
# ─────────────────────────────────────────────────────────────────────────────
def send_gamma_squeeze_brief():
    today = datetime.now(ET).date()
    if not _claim_tab(today, "gamma_squeeze"):
        return
    db_rows = []
    try:
        with psycopg2.connect(DATABASE_URL) as _c:
            cur = _c.cursor()
            cur.execute(
                "SELECT ticker, price, fir, fsd, call_volume, vol_oi, top_strike, top_strike_expiry, score "
                "FROM gamma_pressure_alerts WHERE alert_date=%s ORDER BY fir DESC LIMIT 12", (today,))
            db_rows = cur.fetchall()
    except Exception:
        pass
    lines = [f"⚡ GAMMA SQUEEZE — {today.strftime('%b %-d, %Y')}",
             "FIR>2% = market makers legally forced to buy float shares",
             "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
    if db_rows:
        for r in db_rows:
            lines.append(f"${r[0]} ${r[1]:.2f} | FIR={r[2]:.1f}% | FSD={r[3]:.2f} | {r[4]:,} call vol | Score={r[8]:.0f}")
    else:
        rows = _get_tab("gamma_squeeze")
        for i, c in enumerate(rows[:12], 1):
            lines.append(f"{i}. {_fmt_call(c)}")
        if not rows:
            lines.append("⚠️ No gamma squeeze setups today (8:45 AM text covers yesterday's)")
    ok = _tg_send("\n".join(lines), signal_source="gamma_squeeze_tab", alert_class="SIGNAL")
    _update_notifier_status(today, "gamma_squeeze", f"sent_ok={ok}")


# ─────────────────────────────────────────────────────────────────────────────
# HIGH CONVICTION CALLS  ·  9:45 AM
# ─────────────────────────────────────────────────────────────────────────────
def send_hc_calls_brief():
    today = datetime.now(ET).date()
    if not _claim_tab(today, "hc_calls"):
        return
    db_rows = []
    try:
        with psycopg2.connect(DATABASE_URL) as _c:
            cur = _c.cursor()
            cur.execute(
                "SELECT ticker, price, score, conviction, total_prem_m, max_vol_oi, avg_iv, rank "
                "FROM conviction_calls_snapshot WHERE snap_date=%s ORDER BY rank LIMIT 12", (today,))
            db_rows = cur.fetchall()
    except Exception:
        pass
    lines = [f"🎯 HIGH CONVICTION CALLS — {today.strftime('%b %-d, %Y')}",
             "Vol/OI≥5 · Prem≥$500K · Multi-strike institutional positioning",
             "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
    if db_rows:
        for r in db_rows:
            lines.append(f"#{r[7]} ${r[0]} @ ${r[1]:.2f} | {r[4]:.2f}M prem | {r[5]:.1f}x VOI | IV={r[6]:.0f}% | {r[3]}")
    else:
        rows = _get_tab("hc_calls")
        for i, c in enumerate(rows[:12], 1):
            lines.append(f"{i}. {_fmt_call(c)}")
        if not rows:
            lines.append("⚠️ No high conviction signals today")
    ok = _tg_send("\n".join(lines), signal_source="hc_calls_tab", alert_class="SIGNAL")
    _update_notifier_status(today, "hc_calls", f"sent_ok={ok}")


# ─────────────────────────────────────────────────────────────────────────────
# UNUSUAL CALLS  ·  10:00 AM
# ─────────────────────────────────────────────────────────────────────────────
def send_unusual_calls_brief():
    today = datetime.now(ET).date()
    if not _claim_tab(today, "unusual_calls"):
        return
    db_rows = []
    try:
        with psycopg2.connect(DATABASE_URL) as _c:
            cur = _c.cursor()
            cur.execute(
                "SELECT ticker, price, strike, expiry, days_out, vol_oi, prem, otm_pct, urgency "
                "FROM unusual_calls_log WHERE last_seen::date=%s ORDER BY vol_oi DESC LIMIT 12", (today,))
            db_rows = cur.fetchall()
    except Exception:
        pass
    lines = [f"📊 UNUSUAL CALLS — {today.strftime('%b %-d, %Y')}",
             "Vol/OI≥2 · Prem≥$100K · Sorted by conviction",
             "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
    if db_rows:
        for r in db_rows:
            lines.append(f"${r[0]} @ ${r[1]:.2f} | ${r[2]:.0f}C {r[3]} | {r[5]:.1f}x | ${r[6]/1e6:.2f}M | {r[7]:+.0f}% | {r[8]}")
    else:
        rows = _get_tab("unusual_calls")
        for i, c in enumerate(rows[:12], 1):
            lines.append(f"{i}. {_fmt_call(c)}")
        if not rows:
            lines.append("⚠️ No unusual call signals today")
    ok = _tg_send("\n".join(lines), signal_source="unusual_calls_tab", alert_class="SIGNAL")
    _update_notifier_status(today, "unusual_calls", f"sent_ok={ok}")


# ─────────────────────────────────────────────────────────────────────────────
# HIGH CONVICTION ETFs  ·  10:05 AM
# ─────────────────────────────────────────────────────────────────────────────
def send_hc_etfs_brief():
    today = datetime.now(ET).date()
    if not _claim_tab(today, "hc_etfs"):
        return
    rows = _get_tab("hc_etfs")
    lines = [f"🔥 HC ETFs — {today.strftime('%b %-d, %Y')}",
             f"ETF-only bullish call activity · Sorted by premium · {len(rows)} signals",
             "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
    for i, c in enumerate(rows[:12], 1):
        lines.append(f"{i}. {_fmt_call(c)}")
    if not rows:
        lines.append("⚠️ No ETF signals today")
    ok = _tg_send("\n".join(lines), signal_source="hc_etfs_tab", alert_class="SIGNAL")
    _update_notifier_status(today, "hc_etfs", f"sent_ok={ok} rows={len(rows)}")


# ─────────────────────────────────────────────────────────────────────────────
# FAR-OTM SWEEP RADAR  ·  10:10 AM
# ─────────────────────────────────────────────────────────────────────────────
def send_sweep_radar_brief():
    today = datetime.now(ET).date()
    if not _claim_tab(today, "sweep_radar"):
        return
    rows = _get_tab("sweep_radar")
    lines = [f"🔭 FAR-OTM SWEEP RADAR — {today.strftime('%b %-d, %Y')}",
             ">40% OTM · Vol/OI≥5 · Prem≥$200K · Directional conviction bets (prob of innocence <3%)",
             "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
    for i, c in enumerate(rows[:12], 1):
        lines.append(f"{i}. ${c['ticker']} ${c['strike']:.0f}C {c['expiry']} | {c['vol_oi']:.1f}x | ${c['prem_m']:.2f}M | {c['otm_pct']:+.0f}% OTM")
    if not rows:
        lines.append("⚠️ No far-OTM sweeps today")
    ok = _tg_send("\n".join(lines), signal_source="sweep_radar_tab", alert_class="SIGNAL")
    _update_notifier_status(today, "sweep_radar", f"sent_ok={ok} rows={len(rows)}")


# ─────────────────────────────────────────────────────────────────────────────
# MICRO / SMALL CAP CALLS  ·  10:20 AM
# ─────────────────────────────────────────────────────────────────────────────
def send_microcap_calls_brief():
    today = datetime.now(ET).date()
    if not _claim_tab(today, "microcap_calls"):
        return
    db_rows = []
    try:
        with psycopg2.connect(DATABASE_URL) as _c:
            cur = _c.cursor()
            cur.execute(
                "SELECT ticker, price, strike, expiry, days_out, vol_oi, prem, otm_pct, urgency, cap_tier "
                "FROM unusual_calls_microcap_log WHERE last_seen::date=%s ORDER BY vol_oi DESC LIMIT 12",
                (today,))
            db_rows = cur.fetchall()
    except Exception:
        pass
    lines = [f"🔬 MICRO/SMALL CAP CALLS — {today.strftime('%b %-d, %Y')}",
             "≤$2B mkt cap universe · Lower thresholds · Leverage plays",
             "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
    if db_rows:
        for r in db_rows:
            lines.append(f"${r[0]} @ ${r[1]:.2f} | ${r[2]:.0f}C {r[3]} | {r[5]:.1f}x | ${r[6]/1e6:.2f}M | {r[7]:+.0f}% | {r[8]} | {r[9]}")
    else:
        rows = _get_tab("microcap")
        for i, c in enumerate(rows[:12], 1):
            lines.append(f"{i}. {_fmt_call(c)}")
        if not rows:
            lines.append("⚠️ No micro/small cap signals today")
    ok = _tg_send("\n".join(lines), signal_source="microcap_tab", alert_class="SIGNAL")
    _update_notifier_status(today, "microcap_calls", f"sent_ok={ok}")


# ─────────────────────────────────────────────────────────────────────────────
# 8-LAYER CONVICTION STACK  ·  10:25 AM
# ─────────────────────────────────────────────────────────────────────────────
def send_conviction_stack_brief():
    today = datetime.now(ET).date()
    if not _claim_tab(today, "conviction_stack"):
        return
    db_rows = []
    try:
        with psycopg2.connect(DATABASE_URL) as _c:
            cur = _c.cursor()
            cur.execute(
                "SELECT ticker, price, total_pts, label, layers "
                "FROM conviction_stack_watchlist WHERE snap_date=%s ORDER BY total_pts DESC LIMIT 12",
                (today,))
            db_rows = cur.fetchall()
    except Exception:
        pass
    lines = [f"🎯 8-LAYER CONVICTION — {today.strftime('%b %-d, %Y')}",
             "8+/10 pts = ~90% probability of explosive move",
             "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
    if db_rows:
        for r in db_rows:
            lines.append(f"${r[0]} @ ${r[1]:.2f} | {r[2]}/10 pts | {r[3]}")
    else:
        rows = _get_tab("conviction_stack")
        for i, c in enumerate(rows[:12], 1):
            layers = " + ".join(c.get("layers", []))
            lines.append(f"{i}. ${c['ticker']} @ ${c['price']:.2f} | {c['pts']}/10 | {layers}")
        if not rows:
            lines.append("⚠️ No conviction stack setups today")
    ok = _tg_send("\n".join(lines), signal_source="conviction_stack_tab", alert_class="SIGNAL")
    _update_notifier_status(today, "conviction_stack", f"sent_ok={ok}")


# ─────────────────────────────────────────────────────────────────────────────
# SMART MONEY PRESSURE  ·  10:35 AM
# ─────────────────────────────────────────────────────────────────────────────
def send_smart_money_pressure_brief():
    today = datetime.now(ET).date()
    if not _claim_tab(today, "smart_money_pressure"):
        return
    rows = _get_tab("smart_money_pressure")
    lines = [f"🔥 SMART MONEY PRESSURE — {today.strftime('%b %-d, %Y')}",
             f"4+ independent layers converging · {len(rows)} extreme setups",
             "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
    for i, c in enumerate(rows[:10], 1):
        layers = " + ".join(c.get("layers", []))
        lines.append(f"{i}. ${c['ticker']} @ ${c['price']:.2f} | {c['pts']}/10 | {layers}")
    if not rows:
        lines.append("⚠️ No extreme smart money pressure today")
    ok = _tg_send("\n".join(lines), signal_source="smart_money_tab", alert_class="SIGNAL")
    _update_notifier_status(today, "smart_money_pressure", f"sent_ok={ok} rows={len(rows)}")


# ─────────────────────────────────────────────────────────────────────────────
# INSIDER RADAR  ·  10:40 AM
# ─────────────────────────────────────────────────────────────────────────────
def send_insider_radar_brief():
    today = datetime.now(ET).date()
    if not _claim_tab(today, "insider_radar"):
        return
    db_rows = []
    try:
        with psycopg2.connect(DATABASE_URL) as _c:
            cur = _c.cursor()
            cur.execute(
                "SELECT ticker, suspicion_score, prem, strike, expiry, vol_oi, days_to_earnings, verdict "
                "FROM insider_alerts WHERE detected_at::date=%s ORDER BY suspicion_score DESC LIMIT 12",
                (today,))
            db_rows = cur.fetchall()
    except Exception:
        pass
    lines = [f"🕵️ INSIDER RADAR — {today.strftime('%b %-d, %Y')}",
             "SEC-style detection · Rarity + Size + Vol/OI + Earnings proximity",
             "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
    if db_rows:
        for r in db_rows:
            dte = f" | {r[6]}d→earnings" if r[6] and r[6] <= 90 else ""
            lines.append(f"${r[0]} | Score={r[1]} | ${r[2]/1e6:.2f}M | ${r[3]:.0f}C {r[4]} | {r[5]:.1f}x{dte} | {r[7]}")
    else:
        rows = _get_tab("insider_radar")
        for i, c in enumerate(rows[:12], 1):
            lines.append(f"{i}. ${c['ticker']} @ ${c['price']:.2f} | Score={c.get('ins_score',0)} | {_fmt_call(c)}")
        if not rows:
            lines.append("⚠️ No suspicious insider-like activity today")
    ok = _tg_send("\n".join(lines), signal_source="insider_radar_tab", alert_class="SIGNAL")
    _update_notifier_status(today, "insider_radar", f"sent_ok={ok}")


# ─────────────────────────────────────────────────────────────────────────────
# DARK POOL  ·  10:50 AM
# ─────────────────────────────────────────────────────────────────────────────
def send_dark_pool_brief():
    today = datetime.now(ET).date()
    if not _claim_tab(today, "dark_pool"):
        return
    rows = []
    try:
        with psycopg2.connect(DATABASE_URL) as _c:
            cur = _c.cursor()
            cur.execute(
                "SELECT ticker, price, dark_pool_pct, dark_pool_signal, score "
                "FROM steady_grinder_scan WHERE scan_date >= %s "
                "AND dark_pool_pct IS NOT NULL ORDER BY dark_pool_pct DESC LIMIT 15",
                (_prior_td(today),))
            rows = [dict(zip(["ticker","price","dp_pct","dp_signal","score"], r))
                    for r in cur.fetchall()]
    except Exception as e:
        log.debug(f"[dark_pool] DB: {e}")
    lines = [f"🌑 DARK POOL RADAR — {today.strftime('%b %-d, %Y')}",
             "Off-exchange short volume (FINRA) · High DP% = institutional accumulation in the dark",
             "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
    for i, r in enumerate(rows[:12], 1):
        lines.append(f"{i}. ${r['ticker']} @ ${r['price']:.2f} | DP={r['dp_pct']:.1f}% | {r.get('dp_signal','—')} | Score={r.get('score',0):.0f}")
    if not rows:
        lines.append("⚠️ Dark pool data populated by EOD scan — check website tab for live data")
    ok = _tg_send("\n".join(lines), signal_source="dark_pool_tab", alert_class="SIGNAL")
    _update_notifier_status(today, "dark_pool", f"sent_ok={ok} rows={len(rows)}")


# ─────────────────────────────────────────────────────────────────────────────
# BULL FLOW  ·  11:00 AM
# ─────────────────────────────────────────────────────────────────────────────
def send_bull_flow_brief():
    today = datetime.now(ET).date()
    if not _claim_tab(today, "bull_flow"):
        return
    db_rows = []
    try:
        with psycopg2.connect(DATABASE_URL) as _c:
            cur = _c.cursor()
            cur.execute(
                "SELECT ticker, strike, expiry, call_volume, vol_oi_ratio, premium, stock_price, conviction "
                "FROM call_sweep_log WHERE sweep_date=%s ORDER BY vol_oi_ratio DESC LIMIT 12", (today,))
            db_rows = cur.fetchall()
    except Exception:
        pass
    lines = [f"🐂 BULL FLOW — {today.strftime('%b %-d, %Y')}",
             "Call sweeps · High Vol/OI · Directional bullish conviction",
             "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
    if db_rows:
        for r in db_rows:
            lines.append(f"${r[0]} ${r[1]:.0f}C {r[2]} | {r[3]:,} vol | {r[4]:.1f}x | ${r[5]/1e6:.2f}M | @ ${r[6]:.2f} | {r[7]}")
    else:
        rows = _get_tab("bull_flow")
        for i, c in enumerate(rows[:12], 1):
            lines.append(f"{i}. {_fmt_call(c)}")
        if not rows:
            lines.append("⚠️ No bull flow signals today")
    ok = _tg_send("\n".join(lines), signal_source="bull_flow_tab", alert_class="SIGNAL")
    _update_notifier_status(today, "bull_flow", f"sent_ok={ok}")


# ─────────────────────────────────────────────────────────────────────────────
# PERSISTENCE  ·  11:05 AM
# ─────────────────────────────────────────────────────────────────────────────
def send_persistence_brief():
    today = datetime.now(ET).date()
    if not _claim_tab(today, "persistence"):
        return
    rows = _get_tab("persistence")
    lines = [f"📌 PERSISTENCE — {today.strftime('%b %-d, %Y')}",
             "Tickers with 3+ active call strikes today = broad institutional positioning",
             "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
    for i, c in enumerate(rows[:12], 1):
        lines.append(
            f"{i}. ${c['ticker']} @ ${c['price']:.2f} | {c.get('strike_count',0)} strikes "
            f"| best {c['vol_oi']:.1f}x | ${c['prem_m']:.2f}M"
        )
    if not rows:
        lines.append("⚠️ No persistent positioning detected today")
    ok = _tg_send("\n".join(lines), signal_source="persistence_tab", alert_class="SIGNAL")
    _update_notifier_status(today, "persistence", f"sent_ok={ok} rows={len(rows)}")


# ─────────────────────────────────────────────────────────────────────────────
# FLOW STREAK (Accumulation Streak)  ·  11:10 AM
# ─────────────────────────────────────────────────────────────────────────────
def send_flow_streak_brief():
    today = datetime.now(ET).date()
    if not _claim_tab(today, "flow_streak"):
        return
    rows = _get_tab("flow_streak")
    lines = [f"📊 FLOW STREAK — {today.strftime('%b %-d, %Y')}",
             "Consecutive days of net institutional buying · CS≥65% + RVOL≥1.5x",
             "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
    for i, b in enumerate(rows[:12], 1):
        lines.append(
            f"{i}. ${b['ticker']} @ ${b['price']:.2f} "
            f"| CS={b['close_strength']:.0%} | RVOL={b.get('rvol',0):.1f}x | {b['volume']:,.0f} vol"
        )
    if not rows:
        lines.append("⚠️ No accumulation streak signals today")
    ok = _tg_send("\n".join(lines), signal_source="flow_streak_tab", alert_class="SIGNAL")
    _update_notifier_status(today, "flow_streak", f"sent_ok={ok} rows={len(rows)}")


# ─────────────────────────────────────────────────────────────────────────────
# ACCUMULATION LEADERS / STEADY GRINDERS  ·  11:15 AM
# ─────────────────────────────────────────────────────────────────────────────
def send_steady_grinders_brief():
    today = datetime.now(ET).date()
    if not _claim_tab(today, "steady_grinders"):
        return
    db_rows = []
    try:
        with psycopg2.connect(DATABASE_URL) as _c:
            cur = _c.cursor()
            cur.execute(
                "SELECT ticker, price, d1_close_pos, d2_close_pos, higher_low, score "
                "FROM steady_grinder_scan WHERE scan_date=%s ORDER BY score DESC LIMIT 12", (today,))
            db_rows = cur.fetchall()
    except Exception:
        pass
    lines = [f"⚙️ ACCUMULATION LEADERS — {today.strftime('%b %-d, %Y')}",
             "Institutional shakeout → reentry · ⚡ Sweep confirms the run · 76% WR",
             "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
    if db_rows:
        for r in db_rows:
            lines.append(
                f"${r[0]} @ ${r[1]:.2f} | CS1={r[2]:.0%} CS2={r[3]:.0%} "
                f"| HL={'✓' if r[4] else '✗'} | Score={r[5]:.0f}"
            )
    else:
        rows = _get_tab("steady_grinders")
        for i, c in enumerate(rows[:12], 1):
            lines.append(
                f"{i}. ${c['ticker']} @ ${c['price']:.2f} "
                f"| CS={c['close_strength']:.0%} | RVOL={c.get('rvol',0):.1f}x "
                f"| {c.get('vol_oi',0):.1f}x VOI"
            )
        if not rows:
            lines.append("⚠️ No accumulation leaders today")
    ok = _tg_send("\n".join(lines), signal_source="steady_grinders_tab", alert_class="SIGNAL")
    _update_notifier_status(today, "steady_grinders", f"sent_ok={ok}")


# ─────────────────────────────────────────────────────────────────────────────
# WHALE BLOCKS  ·  11:20 AM
# ─────────────────────────────────────────────────────────────────────────────
def send_whale_brief():
    today = datetime.now(ET).date()
    if not _claim_tab(today, "whale"):
        return
    db_rows = []
    try:
        with psycopg2.connect(DATABASE_URL) as _c:
            cur = _c.cursor()
            cur.execute(
                "SELECT ticker, direction, strike, expiry, days_out, prem_m, volume, otm_pct, tier "
                "FROM whale_blocks WHERE first_seen::date=%s ORDER BY prem_m DESC LIMIT 12", (today,))
            db_rows = cur.fetchall()
    except Exception:
        pass
    lines = [f"🐳 WHALE BLOCKS — {today.strftime('%b %-d, %Y')}",
             "Prem≥$1M · Vol/OI≥3 · Institutional-scale directional bets",
             "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
    if db_rows:
        for r in db_rows:
            lines.append(f"${r[0]} {r[1]} ${r[2]:.0f}C {r[3]} | ${r[5]:.1f}M | {r[6]:,} vol | {r[7]:+.0f}% OTM | {r[8]}")
    else:
        rows = _get_tab("whale")
        for i, c in enumerate(rows[:12], 1):
            lines.append(f"{i}. ${c['ticker']} ${c['strike']:.0f}C {c['expiry']} | ${c['prem_m']:.2f}M | {c['vol_oi']:.1f}x | {c['otm_pct']:+.0f}% OTM")
        if not rows:
            lines.append("⚠️ No whale blocks today")
    ok = _tg_send("\n".join(lines), signal_source="whale_tab", alert_class="SIGNAL")
    _update_notifier_status(today, "whale", f"sent_ok={ok}")


# ─────────────────────────────────────────────────────────────────────────────
# EOD CALL SWEEP  ·  4:35 PM
# ─────────────────────────────────────────────────────────────────────────────
def send_eod_sweep_brief():
    today = datetime.now(ET).date()
    if not _claim_tab(today, "eod_sweep"):
        return
    db_rows = []
    try:
        with psycopg2.connect(DATABASE_URL) as _c:
            cur = _c.cursor()
            cur.execute(
                "SELECT ticker, score, grade, num_strikes, total_prem_m, max_vol_oi, avg_iv, price_at_signal "
                "FROM eod_sweep_log WHERE signal_date=%s ORDER BY score DESC LIMIT 12", (today,))
            db_rows = cur.fetchall()
        if not db_rows:
            cur2 = _c.cursor()
            cur2.execute(
                "SELECT ticker, price, strike, expiry, vol_oi, prem, otm_pct, urgency "
                "FROM unusual_calls_log WHERE last_seen::date=%s "
                "AND last_seen::time > '14:00:00' ORDER BY vol_oi DESC LIMIT 12", (today,))
            db_rows = [("*"+r[0], None, "EOD", 1, r[5]/1e6 if r[5] else 0, r[4], 0, r[1])
                       for r in cur2.fetchall()]
    except Exception:
        pass
    lines = [f"🌆 EOD CALL SWEEP — {today.strftime('%b %-d, %Y')}",
             "Late-session smart money · Signals detected after 2:00 PM ET",
             "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
    if db_rows:
        for r in db_rows:
            score_str = f"Score={r[1]:.0f} {r[2]}" if r[1] is not None else r[2]
            lines.append(f"${r[0]} @ ${r[7]:.2f} | {score_str} | {r[3]} strikes | ${r[4]:.2f}M | {r[5]:.1f}x VOI")
    else:
        lines.append("⚠️ No EOD sweep signals detected today")
    ok = _tg_send("\n".join(lines), signal_source="eod_sweep_tab", alert_class="SIGNAL")
    _update_notifier_status(today, "eod_sweep", f"sent_ok={ok}")


# ─────────────────────────────────────────────────────────────────────────────
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
        # Ensure aiem_independent_picks table always exists so queries never
        # crash with UndefinedTable before the fallback code can run.
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS aiem_independent_picks (
                id SERIAL PRIMARY KEY,
                pick_date DATE NOT NULL,
                pick_type VARCHAR(20) NOT NULL,
                ticker VARCHAR(10) NOT NULL,
                rank INTEGER,
                confidence_score NUMERIC(5,2),
                rationale TEXT,
                features JSONB,
                entry_price NUMERIC(12,4),
                option_strike NUMERIC(10,2),
                option_expiry DATE,
                hold_days_max INTEGER DEFAULT 5,
                status VARCHAR(20) DEFAULT 'open',
                exit_price NUMERIC(12,4),
                exit_date DATE,
                pnl_pct NUMERIC(8,4),
                direction_correct BOOLEAN,
                source VARCHAR(40) DEFAULT 'aiem_independent_polygon',
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_aiem_indep_picks_date_type
            ON aiem_independent_picks (pick_date, pick_type)
        """)
        conn.commit()
        log.info("aiem_independent_picks table ready")
    except Exception as e:
        log.error(f"could not ensure tables at startup: {e}")
    finally:
        if conn:
            conn.close()

    _start_health_server()

    # ── aiem-process watchdog ────────────────────────────────────────────────
    # Polls every 2 min to verify aiem_process.py is alive.  If it has been
    # dead for 2 consecutive checks (≥4 min) OUTSIDE the 3:00-3:10 AM ET
    # nightly reset window, fires a Telegram alert and spawns it directly.
    # This guards against the platform failing to auto-restart after the
    # nightly os._exit(0) at 3:02 AM.
    def _aiem_process_watchdog():
        import subprocess as _sp, time as _wtime, sys as _wsys
        _AIEM_SCRIPT   = "/home/runner/workspace/artifacts/stock-scanner-api/aiem_process.py"
        _CHECK_SECS    = 120        # poll interval
        _MISS_THRESHOLD = 2         # consecutive misses before action
        _ALERT_COOLDOWN = 1800      # 30 min between repeated alerts
        _GRACE_START   = (3, 0)     # ET hour, minute — start of reset window
        _GRACE_END     = (3, 10)    # ET hour, minute — end of reset window

        def _alive():
            try:
                r = _sp.run(["pgrep", "-f", "aiem_process.py"],
                             capture_output=True, text=True)
                return bool(r.stdout.strip())
            except Exception:
                return True   # fail-open on pgrep error

        def _in_grace():
            now = datetime.now(ET)
            cur  = now.hour * 60 + now.minute
            return (_GRACE_START[0]*60 + _GRACE_START[1]) <= cur <= (_GRACE_END[0]*60 + _GRACE_END[1])

        def _spawn():
            try:
                p = _sp.Popen(
                    [_wsys.executable, _AIEM_SCRIPT],
                    stdout=open("/tmp/aiem_process_watchdog_spawn.log", "a"),
                    stderr=_sp.STDOUT,
                    close_fds=True,
                    start_new_session=True,
                )
                log.warning(f"[aiem-watchdog] Spawned aiem_process.py PID={p.pid}")
                return p.pid
            except Exception as _e:
                log.error(f"[aiem-watchdog] spawn failed: {_e}")
                return None

        misses     = 0
        last_alert = 0.0
        _wtime.sleep(30)   # let the notifier fully boot before first check
        log.info("[aiem-watchdog] thread started")

        while True:
            try:
                if _alive():
                    if misses:
                        log.info(f"[aiem-watchdog] aiem-process back alive after {misses} miss(es)")
                    misses = 0
                elif _in_grace():
                    log.info("[aiem-watchdog] aiem-process absent — inside nightly reset window, skipping")
                    misses = 0
                else:
                    misses += 1
                    log.warning(f"[aiem-watchdog] aiem-process NOT found (miss {misses}/{_MISS_THRESHOLD})")
                    if misses >= _MISS_THRESHOLD:
                        import time as _t2
                        now_ts = _t2.time()
                        if now_ts - last_alert >= _ALERT_COOLDOWN:
                            _tg_send(
                                "⚠️ <b>AIEM-PROCESS IS DOWN</b>\n"
                                "━━━━━━━━━━━━━━━━━━━━\n"
                                f"Detected at {datetime.now(ET).strftime('%I:%M %p ET on %a %b %d')}\n"
                                f"Down ≥{misses * _CHECK_SECS // 60} min outside nightly reset window.\n\n"
                                "Attempting automatic restart now…\n"
                                "Stock pick scans and Telegram alerts may have been delayed."
                            )
                            last_alert = now_ts
                        pid = _spawn()
                        misses = 0
                        _wtime.sleep(15)
                        if pid and _alive():
                            _tg_send(f"✅ <b>aiem-process restarted</b> (PID {pid}) — scans resuming.")
                        elif pid:
                            _tg_send("❌ <b>aiem-process restart FAILED</b> — manual intervention needed.\nRestart the aiem-process workflow in Replit.")
            except Exception as _we:
                log.error(f"[aiem-watchdog] loop error: {_we}")
            _wtime.sleep(_CHECK_SECS)

    threading.Thread(target=_aiem_process_watchdog, daemon=True,
                     name="aiem-process-watchdog").start()

    # ── HTTP heartbeat monitor (Component 1/2 of infra directive) ─────────────
    # Pings :5055/health every 2 min via HTTP (separate from pgrep watchdog).
    # N=3 consecutive failures (6 min) → Telegram alert.
    # N=3 chosen: 1 miss can be transient network; 2 = unlikely coincidence;
    # 3 × 2 min = 6 min confirms dead process with high confidence.
    # Alert-only — no spawn (pgrep watchdog handles restart).
    def _aiem_http_heartbeat_monitor():
        import time as _hbt, urllib.request as _hbur
        _HB_INTERVAL  = 120    # 2-min poll
        _HB_THRESHOLD = 3      # consecutive HTTP failures before alert
        _HB_COOLDOWN  = 1800   # 30-min between repeated alerts
        _HB_URL       = "http://127.0.0.1:5055/health"
        misses     = 0
        last_alert = 0.0
        _hbt.sleep(45)   # stagger from pgrep watchdog
        log.info("[hb-monitor] HTTP heartbeat monitor started -- "
                 "pinging :5055/health every 2 min, alert on 3 consecutive misses")
        while True:
            try:
                try:
                    with _hbur.urlopen(_HB_URL, timeout=8) as _r:
                        _raw = _r.read()
                        _data = json.loads(_raw) if _raw else {}
                        if misses > 0:
                            log.info("[hb-monitor] aiem-process OK after %d miss(es) uptime=%ss",
                                     misses, _data.get("uptime_s"))
                        misses = 0
                except Exception as _he:
                    misses += 1
                    log.warning("[hb-monitor] :5055/health unreachable (miss %d/%d): %s",
                                misses, _HB_THRESHOLD, _he)
                    if misses >= _HB_THRESHOLD:
                        _now_ts = _hbt.time()
                        if _now_ts - last_alert >= _HB_COOLDOWN:
                            _det = datetime.now(ET).strftime("%I:%M %p ET %a %b %d")
                            _dur = misses * _HB_INTERVAL // 60
                            _msg = (
                                "\U0001f534 <b>AIEM-PROCESS HTTP HEALTH MONITOR: DOWN</b>\n"
                                "--------------------\n"
                                "Detected: " + _det + "\n"
                                + str(misses) + " consecutive HTTP /health pings missed "
                                "(" + str(_dur) + " min without response).\n\n"
                                "Liveness check (HTTP, separate from pgrep watchdog).\n"
                                "pgrep watchdog will attempt auto-restart if process is absent."
                            )
                            _tg_send(_msg)
                            last_alert = _now_ts
            except Exception as _le:
                log.error("[hb-monitor] loop error: %s", _le)
            _hbt.sleep(_HB_INTERVAL)

        threading.Thread(target=_aiem_http_heartbeat_monitor, daemon=True,
                     name="aiem-http-heartbeat-monitor").start()

    # ── Synthetic heartbeat trail (Component 4 of infra directive) ────────────
    # Every 5 min Mon-Fri 6:50-10:00 AM ET: ping :5055/health, log result to DB.
    # Produces a continuous alive/dead trail across the risk window so any gap
    # is datestamped precisely rather than inferred from absent trigger activity.
    def _synthetic_heartbeat_trail():
        import time as _sht, urllib.request as _shur
        _SH_TABLE = """
            CREATE TABLE IF NOT EXISTS aiem_process_heartbeat_trail (
                id            BIGSERIAL PRIMARY KEY,
                ts            TIMESTAMPTZ DEFAULT NOW(),
                scan_date     DATE NOT NULL,
                alive         BOOLEAN NOT NULL,
                uptime_s      INT,
                response_json JSONB
            )
        """
        _SH_INS = (
            "INSERT INTO aiem_process_heartbeat_trail "
            "(ts, scan_date, alive, uptime_s, response_json) "
            "VALUES (NOW(), %s, %s, %s, %s::jsonb)"
        )
        try:
            import psycopg2 as _shpg
            with _shpg.connect(DATABASE_URL, connect_timeout=5) as _sc, _sc.cursor() as _sk:
                _sk.execute(_SH_TABLE)
                _sc.commit()
            log.info("[heartbeat-trail] aiem_process_heartbeat_trail table ready")
        except Exception as _te:
            log.error(f"[heartbeat-trail] table init: {_te}")
        _sht.sleep(60)
        log.info("[heartbeat-trail] synthetic trail started — "
                 "6:50–10:00 AM ET Mon-Fri, 5-min intervals, writing to DB")
        while True:
            try:
                _now_et = datetime.now(ET)
                _mins   = _now_et.hour * 60 + _now_et.minute
                if _now_et.weekday() < 5 and 6 * 60 + 50 <= _mins < 10 * 60:
                    _alive, _uptime_s, _resp_json = False, None, None
                    try:
                        with _shur.urlopen("http://127.0.0.1:5055/health",
                                           timeout=5) as _r:
                            _rd = json.loads(_r.read())
                            _alive    = True
                            _uptime_s = _rd.get("uptime_s")
                            _resp_json = json.dumps(_rd)
                    except Exception as _he:
                        _resp_json = json.dumps({"error": str(_he)})
                    _label = (f"uptime={_uptime_s}s" if _alive else "UNREACHABLE")
                    log.info(f"[heartbeat-trail] {_now_et.strftime('%H:%M ET')} "
                             f"aiem-process alive={_alive} {_label}")
                    try:
                        import psycopg2 as _shpg2
                        with _shpg2.connect(DATABASE_URL, connect_timeout=5) as _sc2,                                 _sc2.cursor() as _sk2:
                            _sk2.execute(_SH_INS,
                                (_now_et.date(), _alive, _uptime_s, _resp_json))
                            _sc2.commit()
                    except Exception as _dbe:
                        log.error(f"[heartbeat-trail] DB write: {_dbe}")
            except Exception as _le:
                log.error(f"[heartbeat-trail] loop: {_le}")
            _sht.sleep(300)   # 5-min interval

    threading.Thread(target=_synthetic_heartbeat_trail, daemon=True,
                     name="aiem-heartbeat-trail").start()
    # ────────────────────────────────────────────────────────────────────────

    # ── VM resource monitor — Crash Forensics Gap 2 ──────────────────────────
    # Every 60 s: reads RSS, thread count, and CPU% for aiem-process,
    # stock-api, and this notifier via /proc/{pid}/status + /proc/{pid}/stat;
    # reads total VM pressure from /proc/meminfo.  Written by THIS process
    # (the notifier) so rows survive a complete crash of either monitored
    # process — covering the gap right up to and including the crash instant.
    # Retention: 7 days rolling, pruned on every insert cycle.
    #
    # Residual gap (per directive — noted, not fixed here): the notifier
    # itself has no crash-log buffer or lifecycle wrapper of its own.
    def _vm_resource_monitor():
        import time       as _vrmt
        import subprocess as _vrmsp

        _VRM_CREATE = """
            CREATE TABLE IF NOT EXISTS vm_resource_log (
                id              BIGSERIAL    PRIMARY KEY,
                ts              TIMESTAMPTZ  DEFAULT NOW(),
                process_name    TEXT         NOT NULL,
                pid             INT,
                rss_mb          NUMERIC(10,1),
                vm_pressure_pct NUMERIC(5,1),
                cpu_pct         NUMERIC(5,1),
                thread_count    INT
            )
        """
        _VRM_INS = (
            "INSERT INTO vm_resource_log "
            "(ts, process_name, pid, rss_mb, vm_pressure_pct, cpu_pct, thread_count) "
            "VALUES (NOW(), %s, %s, %s, %s, %s, %s)"
        )
        _VRM_CLEANUP = (
            "DELETE FROM vm_resource_log WHERE ts < NOW() - INTERVAL '7 days'"
        )

        # Create table at startup
        try:
            import psycopg2 as _vrmpg
            with _vrmpg.connect(DATABASE_URL, connect_timeout=5) as _c, \
                    _c.cursor() as _cur:
                _cur.execute(_VRM_CREATE)
                _c.commit()
            log.info("[vm-resource-monitor] vm_resource_log table ready")
        except Exception as _te:
            log.error(f"[vm-resource-monitor] table init: {_te}")

        # Process name → pgrep pattern
        # stock-api pattern matches the full path so it doesn't collide with
        # aiem_process_wrapper.sh (which also runs python + a .py file)
        _PROCS = [
            ("aiem-process", "aiem_process.py"),
            ("stock-api",    "stock-scanner-api/main.py"),
            ("notifier",     "aiem_telegram_notifier.py"),
        ]

        # CPU tick tracking: {process_name: (prev_ticks, prev_monotonic)}
        _cpu_prev: dict = {}
        _LINUX_HZ = 100   # standard CONFIG_HZ on Linux (used to convert jiffies → seconds)

        def _get_pid(pattern: str):
            """First PID matching `pgrep -f pattern`, or None."""
            try:
                r = _vrmsp.run(["pgrep", "-f", pattern],
                               capture_output=True, text=True)
                pids = [int(p) for p in r.stdout.strip().split() if p.strip()]
                return pids[0] if pids else None
            except Exception:
                return None

        def _read_proc_status(pid: int):
            """(rss_kb, thread_count) from /proc/{pid}/status."""
            rss_kb = threads = None
            try:
                with open(f"/proc/{pid}/status") as _f:
                    for _line in _f:
                        if _line.startswith("VmRSS:"):
                            rss_kb = int(_line.split()[1])
                        elif _line.startswith("Threads:"):
                            threads = int(_line.split()[1])
                        if rss_kb is not None and threads is not None:
                            break
            except Exception:
                pass
            return rss_kb, threads

        def _read_proc_stat_ticks(pid: int):
            """utime+stime (CPU jiffies) from /proc/{pid}/stat fields 14+15."""
            try:
                with open(f"/proc/{pid}/stat") as _f:
                    fields = _f.read().split()
                return int(fields[13]) + int(fields[14])
            except Exception:
                return None

        def _read_vm_pressure():
            """vm_pressure_pct = (MemTotal-MemAvailable)/MemTotal × 100."""
            total_kb = avail_kb = None
            try:
                with open("/proc/meminfo") as _f:
                    for _line in _f:
                        if _line.startswith("MemTotal:"):
                            total_kb = int(_line.split()[1])
                        elif _line.startswith("MemAvailable:"):
                            avail_kb = int(_line.split()[1])
                        if total_kb is not None and avail_kb is not None:
                            break
                if total_kb and avail_kb is not None:
                    return round((total_kb - avail_kb) / total_kb * 100, 1)
            except Exception:
                pass
            return None

        _vrmt.sleep(30)   # stagger from other startup threads
        log.info("[vm-resource-monitor] started — sampling every 60 s → vm_resource_log")

        while True:
            try:
                vm_pressure = _read_vm_pressure()
                now_t       = _vrmt.monotonic()
                rows        = []

                for proc_name, script_pat in _PROCS:
                    pid            = _get_pid(script_pat)
                    rss_mb         = None
                    cpu_pct        = None
                    thread_count   = None

                    if pid:
                        rss_kb, thread_count = _read_proc_status(pid)
                        rss_mb = round(rss_kb / 1024, 1) if rss_kb else None

                        ticks = _read_proc_stat_ticks(pid)
                        if ticks is not None and proc_name in _cpu_prev:
                            prev_ticks, prev_t = _cpu_prev[proc_name]
                            elapsed = now_t - prev_t
                            if elapsed > 0:
                                cpu_pct = round(
                                    (ticks - prev_ticks) / (elapsed * _LINUX_HZ) * 100, 1
                                )
                        if ticks is not None:
                            _cpu_prev[proc_name] = (ticks, now_t)

                    rows.append((proc_name, pid, rss_mb, vm_pressure, cpu_pct, thread_count))

                try:
                    import psycopg2 as _vrmpg2
                    with _vrmpg2.connect(DATABASE_URL, connect_timeout=5) as _c2, \
                            _c2.cursor() as _cur2:
                        _cur2.executemany(_VRM_INS, rows)
                        _cur2.execute(_VRM_CLEANUP)
                        _c2.commit()
                except Exception as _dbe:
                    log.error(f"[vm-resource-monitor] DB write: {_dbe}")

            except Exception as _le:
                log.error(f"[vm-resource-monitor] loop error: {_le}")

            _vrmt.sleep(60)

    threading.Thread(target=_vm_resource_monitor, daemon=True,
                     name="vm-resource-monitor").start()
    # ────────────────────────────────────────────────────────────────────────

    # ── Protection #6: independent morning scan watchdog ─────────────────────
    # Runs in THIS process (aiem-telegram, separate from aiem-process).
    # Every 5 min from 6:50–10:00 AM ET on trading days:
    #   1. Checks morning_scan_runs for a SUCCEEDED slot in the last 25 min.
    #   2. If missing and time >= 7:00 AM: triggers localhost:5055/run-scan.
    #   3. Retries up to 3 times (90s wait between).
    #   4. Sends Telegram only after 3 consecutive failures (15-min cooldown).
    # This satisfies PI item 6: "watchdog cannot run inside the same process
    # it monitors."  Provides automatic recovery without manual intervention.
    def _morning_scan_watchdog():
        import time as _mwt, urllib.request as _mw_req, urllib.error as _mw_err
        import sys as _mwsys, os as _mwos, uuid as _mwuuid, json as _mwjson
        _SCHED_DIR = _mwos.path.join(_mwos.path.dirname(_mwos.path.abspath(__file__)),
                                      "artifacts", "stock-scanner-api")
        if _SCHED_DIR not in _mwsys.path:
            _mwsys.path.insert(0, _SCHED_DIR)
        _mwchkp_local = None
        try:
            import aiem_pipeline_checkpoints as _mwchkp_local
        except Exception as _mwce:
            log.warning(f"[morning-watchdog] checkpoint module unavailable: {_mwce}")

        def _mw_chk(tid, stage, payload):
            """Safe checkpoint write — no-op if module unavailable or trace_id missing."""
            if _mwchkp_local and tid:
                _mwchkp_local.chk(tid, stage, payload, DATABASE_URL)
        _MW_INTERVAL  = 300    # 5-min polling interval
        _MW_SCAN_URL  = "http://localhost:5055/run-scan"
        _MW_MAX_TRIES = 3
        _MW_WAIT_S    = 90     # seconds to wait after trigger before re-checking DB
        _MW_COOLDOWN  = 900    # 15-min between repeated failure alerts
        _MW_MAX_TRIGGERS_PER_DAY = 5   # hard daily cap on recovery cycles fired

        _last_alert_ts = 0.0
        _mwt.sleep(90)   # let notifier fully boot before first check
        log.info("[morning-watchdog] thread started — polling every 5 min from 6:50–10:00 AM ET")

        while True:
            try:
                now_et   = datetime.now(ET)
                now_mins = now_et.hour * 60 + now_et.minute
                if now_et.weekday() < 5 and (6*60 + 50 <= now_mins <= 10*60):
                    today_dt  = now_et.date()
                    _mw_succeeded = 0
                    _mw_preds     = 0
                    _mconn = None
                    try:
                        _mconn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
                        _mcur  = _mconn.cursor()
                        try:
                            _mcur.execute("""
                                SELECT COUNT(*) FROM morning_scan_runs
                                WHERE job_name='premarket_scan' AND market_date=%s
                                  AND status='SUCCEEDED'
                                  AND completed_at > NOW() - INTERVAL '25 minutes'
                            """, (today_dt,))
                            _mw_succeeded = _mcur.fetchone()[0]
                        except Exception:
                            pass  # table may not exist yet on first boot
                        _mcur.execute(
                            "SELECT COUNT(*) FROM aiem_process_predictions WHERE prediction_date=%s",
                            (today_dt,)
                        )
                        _mw_preds = _mcur.fetchone()[0]
                        _mconn.close()
                    except Exception as _mdb:
                        log.warning(f"[morning-watchdog] DB check: {_mdb}")
                        if _mconn:
                            try: _mconn.close()
                            except: pass
                        _mwt.sleep(_MW_INTERVAL)
                        continue

                    # ── Stage 1: WATCHDOG_POLL — write-before-work, before gate checks ──
                    _mw_trace_id = None
                    try:
                        if _mwchkp_local:
                            _mw_trace_id = _mwchkp_local.get_or_set_trace_id(
                                today_dt, DATABASE_URL,
                                new_trace_id=str(_mwuuid.uuid4()))
                            _mw_chk(_mw_trace_id, "WATCHDOG_POLL", {
                                "time_et": now_et.isoformat(),
                                "succeeded_recent": int(_mw_succeeded),
                                "preds": int(_mw_preds),
                            })
                    except Exception as _mwt1e:
                        log.warning(f"[morning-watchdog] checkpoint WATCHDOG_POLL: {_mwt1e}")

                    # ── Gates 1-3: kill switch / daily cap / verification ─────────
                    # All gates must pass before any trigger attempt.
                    # Fail closed: if gate DB is unreachable, no trigger fires.
                    _should_trigger, _gate_reason = True, "all_gates_pass"
                    try:
                        _gconn = psycopg2.connect(DATABASE_URL, connect_timeout=5)
                        _gc    = _gconn.cursor()
                        _gc.execute("""
                            CREATE TABLE IF NOT EXISTS aiem_watchdog_flags (
                                flag_name  TEXT PRIMARY KEY,
                                flag_value TEXT NOT NULL,
                                updated_at TIMESTAMPTZ DEFAULT NOW()
                            )""")
                        _gc.execute("""
                            INSERT INTO aiem_watchdog_flags (flag_name, flag_value)
                            VALUES ('morning_watchdog_trigger_enabled', 'true')
                            ON CONFLICT DO NOTHING""")
                        _gc.execute("""
                            CREATE TABLE IF NOT EXISTS morning_watchdog_audit (
                                audit_date     DATE PRIMARY KEY,
                                triggers_fired INT  NOT NULL DEFAULT 0,
                                updated_at     TIMESTAMPTZ DEFAULT NOW()
                            )""")
                        _gconn.commit()
                        # Gate 1 — kill switch (Joel sets flag_value='false' to disable;
                        #   watchdog reads only, never clears)
                        _gc.execute(
                            "SELECT flag_value FROM aiem_watchdog_flags "
                            "WHERE flag_name='morning_watchdog_trigger_enabled'")
                        _krow = _gc.fetchone()
                        if _krow and _krow[0].strip().lower() == 'false':
                            _should_trigger, _gate_reason = False, "kill_switch"
                        # Gate 2 — daily trigger cap (_MW_MAX_TRIGGERS_PER_DAY per day)
                        if _should_trigger:
                            _gc.execute(
                                "SELECT COALESCE(triggers_fired,0) FROM morning_watchdog_audit "
                                "WHERE audit_date=%s", (today_dt,))
                            _crow = _gc.fetchone()
                            _fired_today = _crow[0] if _crow else 0
                            if _fired_today >= _MW_MAX_TRIGGERS_PER_DAY:
                                _should_trigger, _gate_reason = (
                                    False,
                                    f"daily_cap:{_fired_today}/{_MW_MAX_TRIGGERS_PER_DAY}")
                        # Gate 3 — verification: morning_scan_runs accessible, not in crash
                        #           loop, no unexpired RUNNING lease
                        if _should_trigger:
                            try:
                                _gc.execute(
                                    "SELECT COUNT(*) FROM morning_scan_runs "
                                    "WHERE market_date=%s AND status='FAILED'", (today_dt,))
                                _fail_ct = _gc.fetchone()[0]
                                if _fail_ct >= 5:
                                    _should_trigger, _gate_reason = (
                                        False,
                                        f"verification_gate:failed_slots={_fail_ct}")
                                if _should_trigger:
                                    _gc.execute(
                                        "SELECT COUNT(*) FROM morning_scan_runs "
                                        "WHERE market_date=%s AND status='RUNNING' "
                                        "  AND lease_expires_at > NOW()", (today_dt,))
                                    if _gc.fetchone()[0] > 0:
                                        _should_trigger, _gate_reason = (
                                            False,
                                            "verification_gate:active_running_lease")
                            except Exception:
                                _should_trigger, _gate_reason = (
                                    False,
                                    "verification_gate:morning_scan_runs_unqueryable")
                        _gconn.close()
                    except Exception as _gate_err:
                        _should_trigger, _gate_reason = False, "gate_db_unreachable"
                    if not _should_trigger:
                        log.info(f"[morning-watchdog] trigger blocked — gate={_gate_reason}")
                        _mwt.sleep(_MW_INTERVAL)
                        continue
                    # ─────────────────────────────────────────────────────────────

                    if _mw_succeeded > 0:
                        log.debug("[morning-watchdog] recent SUCCEEDED slot — all good")
                    elif now_mins < 7*60:
                        log.debug("[morning-watchdog] pre-7 AM, no scan expected yet")
                    else:
                        log.warning(
                            f"[morning-watchdog] {now_et.strftime('%H:%M ET')}: "
                            f"0 recent SUCCEEDED slots, {_mw_preds} predictions — "
                            f"triggering scan recovery"
                        )
                        # NOTE: daily trigger count is incremented atomically by
                        # _rs_gate_check() inside /run-scan — not here.
                        # The watchdog's Gate 2 reads that count as advisory pre-check only.
                        _confirmed = False
                        for _try in range(1, _MW_MAX_TRIES + 1):
                            _mw_http_status = None
                            # Stage 2: RUN_SCAN_CALLED — write before HTTP call
                            _mw_chk(_mw_trace_id, "RUN_SCAN_CALLED",
                                    {"attempt": _try, "url": _MW_SCAN_URL})
                            try:
                                _mw_post_body = (
                                    _mwjson.dumps({"trace_id": _mw_trace_id}).encode()
                                    if _mw_trace_id else b""
                                )
                                _rreq  = _mw_req.Request(
                                    _MW_SCAN_URL, data=_mw_post_body, method="POST")
                                _rreq.add_header("Content-Type", "application/json")
                                _rresp = _mw_req.urlopen(_rreq, timeout=15)
                                _mw_http_status = _rresp.status
                                log.info(
                                    f"[morning-watchdog] scan triggered "
                                    f"(attempt {_try}/{_MW_MAX_TRIES}), HTTP {_rresp.status}"
                                )
                            except Exception as _te:
                                _mw_http_status = getattr(_te, 'code', -1)
                                log.warning(
                                    f"[morning-watchdog] trigger attempt {_try}: {_te}"
                                )
                            # Stage 3: RUN_SCAN_RESPONSE — write after HTTP response
                            _mw_chk(_mw_trace_id, "RUN_SCAN_RESPONSE",
                                    {"attempt": _try, "http_status": _mw_http_status})
                            _mwt.sleep(_MW_WAIT_S)
                            try:
                                _vconn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
                                _vcur  = _vconn.cursor()
                                _vcur.execute(
                                    "SELECT COUNT(*) FROM aiem_process_predictions "
                                    "WHERE prediction_date=%s", (today_dt,)
                                )
                                _new_n = _vcur.fetchone()[0]
                                _vconn.close()
                                if _new_n > 0:
                                    log.info(
                                        f"[morning-watchdog] confirmed: {_new_n} predictions "
                                        f"after attempt {_try}"
                                    )
                                    _confirmed = True
                                    break
                            except Exception as _ve:
                                log.warning(f"[morning-watchdog] verify DB: {_ve}")
                            if _try < _MW_MAX_TRIES:
                                _mwt.sleep(30)
                        if not _confirmed:
                            import time as _mwts
                            _now_ts = _mwts.time()
                            if _now_ts - _last_alert_ts >= _MW_COOLDOWN:
                                _tg_send(
                                    f"🚨 <b>MORNING SCAN WATCHDOG — FINAL FAILURE</b>\n"
                                    f"━━━━━━━━━━━━━━━━━━━━\n"
                                    f"Time: {now_et.strftime('%I:%M %p ET on %a %b %d')}\n"
                                    f"{_MW_MAX_TRIES} trigger attempts exhausted — "
                                    f"0 predictions written.\n\n"
                                    f"GH Actions premarket-backup.yml (every 5 min) is "
                                    f"providing independent recovery. Check aiem-process logs."
                                )
                                _last_alert_ts = _now_ts
                            else:
                                log.warning("[morning-watchdog] final failure — alert in cooldown")
            except Exception as _mwe:
                log.error(f"[morning-watchdog] loop error: {_mwe}")
            _mwt.sleep(_MW_INTERVAL)

    threading.Thread(target=_morning_scan_watchdog, daemon=True,
                     name="morning-scan-watchdog").start()
    # ────────────────────────────────────────────────────────────────────────

    # ── Protection #5 (renumbered from #5 → same code): external paper trade watchdog ──
    # Runs in THIS process (aiem-telegram, a completely different process from
    # stock-api), satisfying the "external watchdog" protection requirement.
    # Polls the DB ledger every 2 min. After 9:46 AM ET, if no terminal status,
    # POSTs to the stock-api admin endpoint to trigger recovery execution.
    def _paper_trade_watchdog():
        import time as _ptw_t, json as _ptw_j, urllib.request as _ptw_req
        import urllib.error as _ptw_err, datetime as _ptw_dt
        _PTW_LOG = "/home/runner/workspace/.local/paper_watchdog.log"
        _PTW_ADMIN = os.getenv("ADMIN_TOKEN", "")
        _PTW_PORT  = os.getenv("PORT", "5050")

        def _ptw_log(ev):
            ev.setdefault("ts", _ptw_dt.datetime.utcnow().isoformat() + "Z")
            ev.setdefault("pid", os.getpid())
            msg = _ptw_j.dumps(ev)
            try:
                os.makedirs(os.path.dirname(_PTW_LOG), exist_ok=True)
                with open(_PTW_LOG, "a") as _f:
                    _f.write(msg + "\n")
            except Exception:
                pass

        def _ptw_ledger(ds):
            try:
                _ptw_conn = psycopg2.connect(DATABASE_URL, connect_timeout=5)
                _ptw_cur  = _ptw_conn.cursor()
                _ptw_cur.execute("SELECT status FROM paper_trade_job_ledger "
                                 "WHERE business_date=%s", (ds,))
                _r = _ptw_cur.fetchone()
                _ptw_cur.close(); _ptw_conn.close()
                return _r[0] if _r else "PENDING"
            except Exception as _e2:
                return "DB_ERROR"

        def _ptw_trigger(note):
            try:
                url = f"http://localhost:{_PTW_PORT}/stock-api/admin/run-paper-today"
                req = _ptw_req.Request(
                    url,
                    data=_ptw_j.dumps({"trigger_source": "external_watchdog",
                                        "note": note}).encode(),
                    headers={"Content-Type": "application/json",
                             "X-Admin-Token": _PTW_ADMIN,
                             "X-Trigger-Source": "external_watchdog"},
                    method="POST",
                )
                with _ptw_req.urlopen(req, timeout=60) as _resp:
                    _ptw_log({"event": "TRIGGER_SENT", "http": _resp.status, "note": note})
                return True
            except _ptw_err.HTTPError as _he:
                _ptw_log({"event": "TRIGGER_HTTP_ERROR", "code": _he.code, "note": note})
            except Exception as _te:
                _ptw_log({"event": "TRIGGER_ERROR", "error": str(_te), "note": note})
            return False

        _ptw_t.sleep(90)
        _ptw_et = _ptw_dt.timezone(_ptw_dt.timedelta(hours=-4))  # ET (EDT)
        log.info("[paper-watchdog] external paper trade watchdog started (2-min poll)")
        _ptw_log({"event": "EXTERNAL_WATCHDOG_STARTED", "pid": os.getpid()})
        while True:
            try:
                import pytz as _ptw_pytz
                _ptw_et = _ptw_pytz.timezone("America/New_York")
                _now = _ptw_dt.datetime.now(_ptw_et)
                _ds  = str(_now.date())
                _h, _m = _now.hour, _now.minute
                is_wday  = _now.weekday() < 5
                past_946 = (_h > 9) or (_h == 9 and _m >= 46)
                before_4 = _h < 16
                if is_wday and past_946 and before_4:
                    _status = _ptw_ledger(_ds)
                    terminal = {"COMPLETED", "SKIPPED"}
                    if _status not in terminal and _status != "DB_ERROR":
                        _note = f"{_ds} ledger={_status} at {_h:02d}:{_m:02d} ET"
                        log.warning(f"[paper-watchdog] RECOVERY TRIGGER: {_note}")
                        _ptw_log({"event": "WATCHDOG_RECOVERY_TRIGGERED",
                                  "date": _ds, "ledger_status": _status,
                                  "time_et": f"{_h:02d}:{_m:02d}"})
                        _ptw_trigger(_note)
                    else:
                        _ptw_log({"event": "WATCHDOG_CHECK_OK", "date": _ds,
                                  "ledger_status": _status,
                                  "time_et": f"{_h:02d}:{_m:02d}"})
            except Exception as _pe:
                log.error(f"[paper-watchdog] loop error: {_pe}")
            _ptw_t.sleep(120)

    threading.Thread(target=_paper_trade_watchdog, daemon=True,
                     name="paper-trade-watchdog").start()
    # ────────────────────────────────────────────────────────────────────────

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

        # RVOL combo brief: PAUSED 2026-07-09. A bug in AIEM's core backtest
        # tool (_mkt_run_two_group) was found and fixed - it had been computing
        # forward returns over an already-filtered rowset, which silently
        # dropped single-fire tickers and inflated the apparent win rate
        # (76-87%). After the fix, AIEM re-ran the exact combo on the last
        # ~30 days and found NO real edge at next_day/2d/3d for the overall
        # combo OR the core tier (all edges negative-to-flat, p>0.34
        # everywhere). Do not re-enable until a fresh AIEM backtest on the
        # fixed tool shows a genuine, significant edge.
        # if 900 <= now_mins < close_mins and not _already_sent("rvol_combo"):
        #     log.info(f"[catchup] Missed 3:00 PM RVOL combo alert (now {now_et.strftime('%H:%M')} ET) — sending now")
        #     send_rvol_combo_alert()

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
        send_trifecta_signal_alert,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=37, timezone=ET),
        id="aiem_trifecta_signal_alert",
        replace_existing=True,
    )
    scheduler.add_job(
        send_pattern_engine_alert,
        CronTrigger(day_of_week="mon-fri", hour=8, minute=50, timezone=ET),
        id="aiem_pattern_engine_alert",
        replace_existing=True,
    )
    # PAUSED 2026-07-09 — see comment above _catchup's rvol_combo block for why.
    # scheduler.add_job(
    #     send_rvol_combo_alert,
    #     CronTrigger(day_of_week="mon-fri", hour=15, minute=0, timezone=ET),
    #     id="aiem_rvol_combo_alert",
    #     replace_existing=True,
    # )

    # ── TAB SCAN ENGINE: master scan at 9:35 AM warms cache for all 18 alerts ──
    scheduler.add_job(
        run_aiem_tab_scan_job,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=35, timezone=ET),
        id="aiem_tab_scan_job", replace_existing=True,
    )
    # ── OI Buildup  8:55 AM ─────────────────────────────────────────────────
    scheduler.add_job(
        send_oi_buildup_brief,
        CronTrigger(day_of_week="mon-fri", hour=8, minute=55, timezone=ET),
        id="aiem_oi_buildup_brief", replace_existing=True,
    )
    # ── Today's Picks (S1B·S1C·S1D)  9:40 AM ───────────────────────────────
    scheduler.add_job(
        send_todays_picks_brief,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=40, timezone=ET),
        id="aiem_todays_picks_brief", replace_existing=True,
    )
    # ── HC Calls  9:45 AM ───────────────────────────────────────────────────
    scheduler.add_job(
        send_hc_calls_brief,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=45, timezone=ET),
        id="aiem_hc_calls_brief", replace_existing=True,
    )
    # ── Gamma Squeeze  9:50 AM ──────────────────────────────────────────────
    scheduler.add_job(
        send_gamma_squeeze_brief,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=50, timezone=ET),
        id="aiem_gamma_squeeze_brief", replace_existing=True,
    )
    # ── Unusual Calls  10:00 AM ─────────────────────────────────────────────
    scheduler.add_job(
        send_unusual_calls_brief,
        CronTrigger(day_of_week="mon-fri", hour=10, minute=0, timezone=ET),
        id="aiem_unusual_calls_brief", replace_existing=True,
    )
    # ── HC ETFs  10:05 AM ───────────────────────────────────────────────────
    scheduler.add_job(
        send_hc_etfs_brief,
        CronTrigger(day_of_week="mon-fri", hour=10, minute=5, timezone=ET),
        id="aiem_hc_etfs_brief", replace_existing=True,
    )
    # ── Far-OTM Sweep Radar  10:10 AM ───────────────────────────────────────
    scheduler.add_job(
        send_sweep_radar_brief,
        CronTrigger(day_of_week="mon-fri", hour=10, minute=10, timezone=ET),
        id="aiem_sweep_radar_brief", replace_existing=True,
    )
    # ── Micro/Small Cap Calls  10:20 AM ─────────────────────────────────────
    scheduler.add_job(
        send_microcap_calls_brief,
        CronTrigger(day_of_week="mon-fri", hour=10, minute=20, timezone=ET),
        id="aiem_microcap_calls_brief", replace_existing=True,
    )
    # ── 8-Layer Conviction Stack  10:25 AM ──────────────────────────────────
    scheduler.add_job(
        send_conviction_stack_brief,
        CronTrigger(day_of_week="mon-fri", hour=10, minute=25, timezone=ET),
        id="aiem_conviction_stack_brief", replace_existing=True,
    )
    # ── Smart Money Pressure  10:35 AM ──────────────────────────────────────
    scheduler.add_job(
        send_smart_money_pressure_brief,
        CronTrigger(day_of_week="mon-fri", hour=10, minute=35, timezone=ET),
        id="aiem_smart_money_brief", replace_existing=True,
    )
    # ── Insider Radar  10:40 AM ─────────────────────────────────────────────
    scheduler.add_job(
        send_insider_radar_brief,
        CronTrigger(day_of_week="mon-fri", hour=10, minute=40, timezone=ET),
        id="aiem_insider_radar_brief", replace_existing=True,
    )
    # ── Dark Pool  10:50 AM ─────────────────────────────────────────────────
    scheduler.add_job(
        send_dark_pool_brief,
        CronTrigger(day_of_week="mon-fri", hour=10, minute=50, timezone=ET),
        id="aiem_dark_pool_brief", replace_existing=True,
    )
    # ── Bull Flow  11:00 AM ─────────────────────────────────────────────────
    scheduler.add_job(
        send_bull_flow_brief,
        CronTrigger(day_of_week="mon-fri", hour=11, minute=0, timezone=ET),
        id="aiem_bull_flow_brief", replace_existing=True,
    )
    # ── Persistence  11:05 AM ───────────────────────────────────────────────
    scheduler.add_job(
        send_persistence_brief,
        CronTrigger(day_of_week="mon-fri", hour=11, minute=5, timezone=ET),
        id="aiem_persistence_brief", replace_existing=True,
    )
    # ── Flow Streak  11:10 AM ───────────────────────────────────────────────
    scheduler.add_job(
        send_flow_streak_brief,
        CronTrigger(day_of_week="mon-fri", hour=11, minute=10, timezone=ET),
        id="aiem_flow_streak_brief", replace_existing=True,
    )
    # ── Accumulation Leaders / Steady Grinders  11:15 AM ────────────────────
    scheduler.add_job(
        send_steady_grinders_brief,
        CronTrigger(day_of_week="mon-fri", hour=11, minute=15, timezone=ET),
        id="aiem_steady_grinders_brief", replace_existing=True,
    )
    # ── Whale Blocks  11:20 AM ──────────────────────────────────────────────
    scheduler.add_job(
        send_whale_brief,
        CronTrigger(day_of_week="mon-fri", hour=11, minute=20, timezone=ET),
        id="aiem_whale_brief", replace_existing=True,
    )
    # ── EOD Call Sweep  4:35 PM ─────────────────────────────────────────────
    scheduler.add_job(
        send_eod_sweep_brief,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=35, timezone=ET),
        id="aiem_eod_sweep_brief", replace_existing=True,
    )

    def _nightly_db_backup():
        """2:58 AM — pg_dump the entire database to a compressed file.
        Keeps the last 7 daily backups. Sends a Telegram summary.
        Runs BEFORE the 3:00 AM os._exit() resets so data is safe first."""
        import subprocess as _sp, glob as _gl, gzip as _gz, shutil as _sh
        _backup_dir = os.path.join(os.path.dirname(__file__), ".local", "backups")
        os.makedirs(_backup_dir, exist_ok=True)
        _db_url = os.environ.get("DATABASE_URL", "")
        if not _db_url:
            log.error("[nightly-backup] DATABASE_URL not set — skipping backup")
            _tg_send("⚠️ AIEM DB Backup FAILED — DATABASE_URL not set",
                     signal_source="db_backup", alert_class="ERROR")
            return
        import datetime as _dtb
        _ts   = _dtb.now(ET).strftime("%Y%m%d_%H%M")
        _out  = os.path.join(_backup_dir, f"aiem_db_{_ts}.sql.gz")
        _pg   = "/nix/store/bgwr5i8jf8jpg75rr53rz3fqv5k8yrwp-postgresql-16.10/bin/pg_dump"
        if not os.path.exists(_pg):
            # fallback: whatever is on PATH
            _pg = "pg_dump"
        try:
            # Stream pg_dump → gzip → file (no huge temp .sql on disk)
            log.info(f"[nightly-backup] starting pg_dump → {_out}")
            with open(_out, "wb") as _fout:
                _dump = _sp.Popen(
                    [_pg, "--no-password", _db_url],
                    stdout=_sp.PIPE, stderr=_sp.PIPE
                )
                with _gz.open(_fout, "wb") as _gz_out:
                    _sh.copyfileobj(_dump.stdout, _gz_out)
                _dump.wait(timeout=300)
            _sz_mb = os.path.getsize(_out) / 1_048_576
            if _dump.returncode != 0:
                _err = _dump.stderr.read().decode("utf-8", errors="replace")[:300]
                raise RuntimeError(f"pg_dump exit {_dump.returncode}: {_err}")
            log.info(f"[nightly-backup] done — {_sz_mb:.1f} MB → {_out}")

            # Rotate — keep only the 7 most-recent backup files
            _all = sorted(_gl.glob(os.path.join(_backup_dir, "aiem_db_*.sql.gz")))
            for _old in _all[:-7]:
                try:
                    os.remove(_old)
                    log.info(f"[nightly-backup] rotated old backup: {os.path.basename(_old)}")
                except Exception:
                    pass

            _tg_send(
                f"✅ AIEM Nightly DB Backup complete\n"
                f"File: aiem_db_{_ts}.sql.gz ({_sz_mb:.1f} MB)\n"
                f"Backups kept: {min(len(_all), 7)} (last 7 days)\n"
                f"All data safe before 3 AM reset.",
                signal_source="db_backup", alert_class="INFO"
            )
        except Exception as _e:
            log.error(f"[nightly-backup] FAILED: {_e}")
            _tg_send(
                f"🚨 AIEM DB Backup FAILED at {_ts}\nError: {str(_e)[:200]}\n"
                "Manual backup may be needed — check /home/runner/workspace/.local/backups/",
                signal_source="db_backup", alert_class="ERROR"
            )

    scheduler.add_job(
        _nightly_db_backup,
        CronTrigger(hour=2, minute=58, timezone=ET),
        id="nightly_db_backup",
        replace_existing=True,
    )

    def _nightly_notifier_reset():
        # FIX-2026-07-28: always gc.collect(), never os._exit(0).
        # os._exit(0) in dev mode killed the daemon vm_resource_monitor thread,
        # producing a ~5.9-hour gap (3:04–8:58 AM ET) in vm_resource_log on
        # every trading day.  Production already used gc.collect() only; dev
        # now matches.  Memory relief comes from gc.collect(); the platform
        # does not need to restart this process to get a fresh heap.
        import gc as _gc
        log.info("[NIGHTLY-RESET] 3:04 AM ET — gc.collect() (vm_resource_monitor stays alive)")
        _gc.collect()

    scheduler.add_job(
        _nightly_notifier_reset,
        CronTrigger(hour=3, minute=4, timezone=ET),
        id="nightly_notifier_reset",
        replace_existing=True,
    )

    # ── aiem-process morning heartbeat check — 7:05 AM ET Mon-Fri ────────────
    # If aiem_process_heartbeat has no row in the last 10 min at 7:05 AM ET,
    # the process missed the 6:55 AM warmup window. Fire a Telegram alert so
    # manual intervention can happen before the 7:00–9:15 AM premarket window.
    def _aiem_morning_heartbeat_check():
        if datetime.now(ET).weekday() >= 5:
            return
        try:
            import psycopg2 as _mhb_pg
            conn = _mhb_pg.connect(os.environ["DATABASE_URL"], connect_timeout=4)
            cur  = conn.cursor()
            cur.execute("""
                SELECT MAX(ts) FROM aiem_process_heartbeat
                WHERE ts > NOW() - INTERVAL '10 minutes'
            """)
            row = cur.fetchone()
            conn.close()
            last_hb = row[0] if row else None
            if last_hb is None:
                _tg_send(
                    "🚨 <b>AIEM-PROCESS HEARTBEAT MISSING @ 7:05 AM</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "No heartbeat in the last 10 min — process may have missed the\n"
                    "6:55 AM warmup. Premarket scans (7:00–9:15 AM) are at risk.\n\n"
                    "• GitHub Actions premarket-backup.yml is firing every 10 min\n"
                    "  and will retry automatically — check its run log.\n"
                    "• If the workflow also fails, restart the aiem-process workflow\n"
                    "  in Replit manually."
                )
                log.warning("[morning-hb-check] 7:05 AM: aiem_process_heartbeat MISSING — alert sent")
            else:
                log.info(f"[morning-hb-check] 7:05 AM: heartbeat OK (last={last_hb.isoformat()})")
        except Exception as _mhbe:
            log.warning(f"[morning-hb-check] DB query failed: {_mhbe}")

    scheduler.add_job(
        _aiem_morning_heartbeat_check,
        CronTrigger(day_of_week="mon-fri", hour=7, minute=5, timezone=ET),
        id="aiem_morning_heartbeat_check",
        replace_existing=True,
    )

    # ── Options pipeline scheduler external watchdog ─────────────────────────
    # Runs in THIS process (aiem-telegram) — completely separate from
    # aiem_options_scheduler.py — satisfying the "external watchdog" requirement.
    # Checks every 5 min:
    #   (a) job_heartbeats freshness  — alerts if no heartbeat in 15 min
    #   (b) stuck EXECUTING rows      — alerts if any job stuck > 10 min
    def _options_pipeline_watchdog():
        import time as _opw_t
        _OPW_INTERVAL   = 300      # 5 min
        _HB_STALE_SECS  = 900      # 15 min — heartbeat too old
        _EXEC_STALE_SEC = 600      # 10 min — job stuck executing
        _ALERT_COOL     = 1800     # 30 min between repeated alerts
        _last_alert     = 0.0
        _opw_t.sleep(60)           # let notifier fully boot first
        log.info("[opts-watchdog] options pipeline watchdog started")
        while True:
            try:
                import psycopg2 as _opw_pg
                conn = _opw_pg.connect(os.environ["DATABASE_URL"], connect_timeout=4)
                cur  = conn.cursor()

                # (a) heartbeat freshness
                cur.execute("""
                    SELECT last_success, consecutive_failures
                    FROM job_heartbeats
                    WHERE job_name = 'options_pipeline_scheduler'
                """)
                hb = cur.fetchone()
                hb_stale = False
                if hb is None:
                    hb_stale = True
                    hb_msg = "No heartbeat row found — scheduler may never have started."
                else:
                    import datetime as _opw_dt
                    last_ok = hb[0]
                    failures = hb[1] or 0
                    if last_ok is None:
                        hb_stale = True
                        hb_msg = f"last_success=None  consecutive_failures={failures}"
                    else:
                        age = (_opw_dt.datetime.utcnow() - last_ok).total_seconds()
                        if age > _HB_STALE_SECS:
                            hb_stale = True
                            hb_msg = (f"last_success={last_ok.isoformat()}  "
                                      f"age={int(age)}s > {_HB_STALE_SECS}s threshold  "
                                      f"consecutive_failures={failures}")

                # (b) stuck EXECUTING rows
                cur.execute("""
                    SELECT id, ticker, scan_date, executing_at
                    FROM options_pipeline_jobs
                    WHERE status = 'EXECUTING'
                      AND executing_at < NOW() - INTERVAL '10 minutes'
                """)
                stuck = cur.fetchall()
                conn.close()

                issues = []
                if hb_stale:
                    issues.append(f"HEARTBEAT STALE: {hb_msg}")
                if stuck:
                    issues.append(
                        f"STUCK JOBS ({len(stuck)}): " +
                        ", ".join(f"id={r[0]} {r[1]} {r[2]}" for r in stuck)
                    )

                if issues:
                    import time as _t2
                    now_ts = _t2.time()
                    if now_ts - _last_alert >= _ALERT_COOL:
                        _tg_send(
                            "⚠️ <b>OPTIONS PIPELINE WATCHDOG ALERT</b>\n"
                            "━━━━━━━━━━━━━━━━━━━━\n" +
                            "\n".join(f"• {i}" for i in issues) +
                            "\n\nCheck aiem_options_scheduler workflow in Replit.\n"
                            "Stale recovery runs automatically every 5 min."
                        )
                        _last_alert = now_ts
                        log.warning(f"[opts-watchdog] alert sent: {issues}")
                else:
                    log.debug("[opts-watchdog] options scheduler healthy")

            except Exception as _opw_e:
                log.warning(f"[opts-watchdog] check error: {_opw_e}")
            _opw_t.sleep(_OPW_INTERVAL)

    threading.Thread(target=_options_pipeline_watchdog, daemon=True,
                     name="options-pipeline-watchdog").start()
    # ────────────────────────────────────────────────────────────────────────

    # ── Crash-log flush thread (Gap 3) ───────────────────────────────────────
    _start_notifier_crash_log_flush_thread()
    log.info("[crash_log_notifier] 30-s flush thread started → crash_log_buffer_notifier")
    # ─────────────────────────────────────────────────────────────────────────

    log.info("AIEM Telegram Notifier started — 2:58 AM DB BACKUP + 8:50 AM PATTERN ENGINE + 9:00 AM preview + 9:30 AM stock + 9:37 AM TRIFECTA + 10:30 AM options, Mon-Fri | aiem-process watchdog active (2-min poll) | options-pipeline-watchdog active (5-min poll)")
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
