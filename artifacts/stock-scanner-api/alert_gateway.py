"""
alert_gateway.py — shared Telegram alert ledger + trust lookup.

PURPOSE
Every Telegram alert sent by ANY process in this app (main.py, the standalone
aiem_process.py nano-cap scanner, aiem_telegram_notifier.py's daily briefs,
and specialist modules like aiem_selloff_reversion.py) gets logged here with
a signal_source tag. A separate daily grading job (alert_grading.py) later
computes forward returns for SIGNAL-class alerts and feeds outcomes into
signal_trust_weights / signal_trust_history under context_bucket
'TELEGRAM_ALERTS' — a lineage kept fully separate from 'PAPER_TRADING' /
'AIEM_MICROCAP' / 'AIEM_PREMARKET' so a bad or buggy Telegram-alert trust
computation can NEVER bleed into paper-trading candidate rankings.

SAFETY CONTRACT (do not weaken)
- FAIL-OPEN: log_alert() must NEVER raise and must NEVER be allowed to block
  or suppress an actual Telegram send. Every DB call here is wrapped so a
  DB hiccup degrades to "no ledger row" + a printed warning, not a dropped
  alert and not an unhandled exception in the caller.
- Phase 1 (current): logging + trust lookup only. Nothing in this module
  blocks a send. Hard-gating (suppressing sends below a trust threshold) is
  a separate, explicitly-approved future phase — see alert_grading.py notes.
- alert_class must be one of:
    'SIGNAL'  — ticker-bearing, gradeable (forward-return outcome makes sense)
    'INFO'    — system/health/digest/no-op messages; NEVER graded, NEVER
                counted against any signal_source's trust score
  Callers that omit alert_class get 'INFO' by default, so retrofitting the
  existing 50+ untagged _tg_send() call sites is a strict no-op: they keep
  sending exactly as before and simply gain an audit row.
"""

import os
import psycopg2

# NOTE: meta_learning_signal_trust.py (which writes signal_trust_weights /
# signal_trust_history) connects via AIEM_DATABASE_URL, while this module
# reads the same tables via DATABASE_URL. Confirmed identical in this
# environment. If a future deployment ever points these at different
# databases, trust writes (Phase 3) and trust reads (get_trust_display,
# weekly digest) would silently split-brain — keep them pointed at the
# same database.
_DB_URL = os.environ.get("DATABASE_URL")

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS telegram_alert_ledger (
    id                     BIGSERIAL PRIMARY KEY,
    sent_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    signal_source          TEXT NOT NULL DEFAULT 'unclassified',
    ticker                 TEXT,
    alert_class            TEXT NOT NULL DEFAULT 'INFO',
    alert_text             TEXT,
    audit_trace_id         TEXT,
    trust_weight_at_send   NUMERIC,
    trigger_price          NUMERIC,
    is_test                BOOLEAN NOT NULL DEFAULT FALSE,
    sent_ok                BOOLEAN,
    graded                 BOOLEAN NOT NULL DEFAULT FALSE,
    outcome_d1_pct         NUMERIC,
    outcome_d3_pct         NUMERIC,
    outcome_d5_pct         NUMERIC,
    win_loss               TEXT,
    graded_at              TIMESTAMPTZ
);
"""

_INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_tal_grading_queue ON telegram_alert_ledger (sent_at) "
    "WHERE alert_class = 'SIGNAL' AND graded = FALSE AND is_test = FALSE",
    "CREATE INDEX IF NOT EXISTS idx_tal_source ON telegram_alert_ledger (signal_source, sent_at)",
]

_schema_ready = False


def init_schema() -> None:
    """Idempotent. Safe to call from every process's own startup path."""
    global _schema_ready
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=4) as c, c.cursor() as cu:
            cu.execute(_SCHEMA_SQL)
            for stmt in _INDEX_SQL:
                cu.execute(stmt)
            c.commit()
        _schema_ready = True
    except Exception as e:
        print(f"[alert_gateway] init_schema error (non-fatal): {e}")


def _lookup_trust_weight(cu, signal_source: str):
    try:
        cu.execute(
            "SELECT trust_weight FROM signal_trust_weights "
            "WHERE signal_name = %s AND context_bucket = 'TELEGRAM_ALERTS'",
            (signal_source,),
        )
        row = cu.fetchone()
        return float(row[0]) if row else None
    except Exception as e:
        print(f"[alert_gateway] trust lookup error (non-fatal): {e}")
        return None


def get_trust_display(signal_source: str, min_n: int = 5) -> str:
    """
    Phase 4 (soft gate): return a short suffix line showing this source's
    TELEGRAM_ALERTS track record, e.g.:
        "\n— source trust: 62% WR · weight 1.24 (n=14)"
    or "" (no-op suffix) when there aren't enough graded outcomes yet
    (n_outcomes_observed < min_n) or the source has no row at all. This is
    informational only — it never blocks or alters whether a message is
    sent, only what a human sees when deciding whether to act on it.

    Fail-open: never raises; any error returns "".
    """
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=3) as c, c.cursor() as cu:
            cu.execute(
                "SELECT trust_weight, rolling_win_rate, n_outcomes_observed "
                "FROM signal_trust_weights "
                "WHERE signal_name = %s AND context_bucket = 'TELEGRAM_ALERTS'",
                (signal_source,),
            )
            row = cu.fetchone()
            if not row:
                return ""
            weight, win_rate, n = row
            n = n or 0
            if n < min_n:
                return ""
            wr_pct = float(win_rate or 0.5) * 100
            return f"\n— source trust: {wr_pct:.0f}% WR · weight {float(weight or 1.0):.2f} (n={n})"
    except Exception as e:
        print(f"[alert_gateway] get_trust_display error (non-fatal): {e}")
        return ""


def log_alert(
    text: str,
    *,
    signal_source: str = "unclassified",
    ticker: str = None,
    alert_class: str = "INFO",
    audit_trace_id: str = None,
    trigger_price: float = None,
    is_test: bool = False,
    sent_ok: bool = None,
):
    """
    Fail-open ledger write for one Telegram alert. Returns the current
    trust_weight for (signal_source, 'TELEGRAM_ALERTS') if one exists and
    alert_class == 'SIGNAL', else None. The return value is informational
    only in Phase 1 — callers are not expected to act on it yet.

    This function must never raise. Any internal error is swallowed and
    printed; the caller's Telegram send has already happened (or not) by
    the time this runs and is never affected by it.
    """
    trust_weight = None
    if alert_class not in ("SIGNAL", "INFO"):
        alert_class = "INFO"
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=3) as c, c.cursor() as cu:
            if alert_class == "SIGNAL" and signal_source != "unclassified":
                trust_weight = _lookup_trust_weight(cu, signal_source)
            cu.execute(
                """
                INSERT INTO telegram_alert_ledger
                    (signal_source, ticker, alert_class, alert_text, audit_trace_id,
                     trust_weight_at_send, trigger_price, is_test, sent_ok)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    signal_source,
                    ticker,
                    alert_class,
                    (text or "")[:2000],
                    audit_trace_id,
                    trust_weight,
                    trigger_price,
                    is_test,
                    sent_ok,
                ),
            )
            c.commit()
    except Exception as e:
        print(f"[alert_gateway] log_alert error (non-fatal, send already completed): {e}")
    return trust_weight
