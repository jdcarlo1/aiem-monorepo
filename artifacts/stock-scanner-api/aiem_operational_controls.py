"""
aiem_operational_controls.py
Institutional-style operational controls for the EXISTING AIEM options engine.

PAPER TRADING ONLY.

Adds:
- Fail-closed portfolio-gate wrapper
- Global/ticker kill switches
- NYSE options-session enforcement (exchange_calendars required)
- Transactional duplicate-execution locks
- Pending paper-order lifecycle
- Daily realized/unrealized loss blocking
- Heartbeat escalation
- Restart recovery and reconciliation
- Immutable operational event hash chain

Integration:
1) Put this file beside aiem_strat_scheduler.py.
2) Call install_schema() once at startup/migration.
3) Replace the scheduler's portfolio-gate try/except + direct paper insert with
   execute_selected_paper_trade_fail_closed(...) shown at the bottom.
4) Call recover_and_reconcile() at startup and every minute.
5) Call guarded_heartbeat() instead of swallowing heartbeat errors.

This module does not replace the existing strategy, portfolio, or paper-trader modules.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import socket
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, Optional

import psycopg2
import psycopg2.extras

log = logging.getLogger("aiem.operational_controls")
UTC = timezone.utc
DB_URL = os.environ.get("DATABASE_URL", "")
ENGINE_NAME = os.environ.get("AIEM_ENGINE_NAME", "aiem-options")
MAX_DAILY_LOSS = Decimal(os.environ.get("AIEM_MAX_DAILY_LOSS", "2500"))
HEARTBEAT_MAX_AGE_SECONDS = int(os.environ.get("AIEM_HEARTBEAT_MAX_AGE_SECONDS", "180"))
PENDING_ORDER_MAX_AGE_SECONDS = int(os.environ.get("AIEM_PENDING_ORDER_MAX_AGE_SECONDS", "300"))
ALLOW_PAPER_EXECUTION = os.environ.get("AIEM_ALLOW_PAPER_EXECUTION", "true").lower() == "true"


DDL = """
CREATE TABLE IF NOT EXISTS aiem_kill_switches (
    scope TEXT PRIMARY KEY,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    reason TEXT,
    changed_by TEXT NOT NULL DEFAULT 'system',
    changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO aiem_kill_switches(scope, enabled, reason)
VALUES ('GLOBAL', FALSE, 'initial state')
ON CONFLICT (scope) DO NOTHING;

CREATE TABLE IF NOT EXISTS aiem_execution_locks (
    lock_key TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    expiration_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK
      (status IN ('ACQUIRED','PENDING','PAPER_OPEN','PAPER_CLOSED','CANCELLED','FAILED')),
    acquired_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS aiem_paper_orders (
    paper_order_id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    execution_lock_key TEXT NOT NULL REFERENCES aiem_execution_locks(lock_key),
    status TEXT NOT NULL CHECK
      (status IN ('CREATED','VALIDATING','READY','SUBMITTED','ACKNOWLEDGED',
                  'PARTIALLY_FILLED','FILLED','REJECTED','CANCELLED',
                  'RECONCILE_FAILED','RECOVERED')),
    requested_qty INTEGER NOT NULL CHECK (requested_qty > 0),
    filled_qty INTEGER NOT NULL DEFAULT 0 CHECK (filled_qty >= 0),
    requested_price NUMERIC,
    average_fill_price NUMERIC,
    reject_reason TEXT,
    broker_order_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_aiem_active_paper_order
ON aiem_paper_orders(execution_lock_key)
WHERE status IN ('CREATED','VALIDATING','READY','SUBMITTED','ACKNOWLEDGED',
                 'PARTIALLY_FILLED','FILLED','RECOVERED');

CREATE TABLE IF NOT EXISTS aiem_operational_events (
    event_id BIGSERIAL PRIMARY KEY,
    trace_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('INFO','WARN','ERROR','CRITICAL')),
    payload JSONB NOT NULL,
    prev_hash CHAR(64),
    event_hash CHAR(64) NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS aiem_engine_health (
    engine_name TEXT PRIMARY KEY,
    host TEXT NOT NULL,
    pid INTEGER NOT NULL,
    status TEXT NOT NULL,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    last_heartbeat TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


@dataclass(frozen=True)
class ControlDecision:
    allowed: bool
    reason: str
    trace_id: str
    event_hash: Optional[str] = None


def _conn():
    if not DB_URL:
        raise RuntimeError("DATABASE_URL is required")
    return psycopg2.connect(DB_URL, connect_timeout=10)


def _canonical(data: Dict[str, Any]) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)


def _sha(data: Dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(data).encode("utf-8")).hexdigest()


def install_schema() -> None:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(DDL)
    log.info("Operational-control schema installed")


def record_event(
    trace_id: str,
    event_type: str,
    severity: str,
    payload: Dict[str, Any],
) -> str:
    """Append one tamper-evident event under a transaction-level advisory lock."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("AIEM_EVENT_CHAIN",))
        cur.execute(
            "SELECT event_hash FROM aiem_operational_events ORDER BY event_id DESC LIMIT 1"
        )
        row = cur.fetchone()
        prev_hash = row[0] if row else None
        body = {
            "trace_id": trace_id,
            "event_type": event_type,
            "severity": severity,
            "payload": payload,
            "prev_hash": prev_hash,
        }
        event_hash = _sha(body)
        cur.execute(
            """
            INSERT INTO aiem_operational_events
              (trace_id,event_type,severity,payload,prev_hash,event_hash)
            VALUES (%s,%s,%s,%s::jsonb,%s,%s)
            """,
            (trace_id, event_type, severity, _canonical(payload), prev_hash, event_hash),
        )
        return event_hash


def set_kill_switch(scope: str, enabled: bool, reason: str, changed_by: str) -> None:
    scope = scope.upper().strip()
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO aiem_kill_switches(scope,enabled,reason,changed_by,changed_at)
            VALUES (%s,%s,%s,%s,NOW())
            ON CONFLICT(scope) DO UPDATE SET
              enabled=EXCLUDED.enabled, reason=EXCLUDED.reason,
              changed_by=EXCLUDED.changed_by, changed_at=NOW()
            """,
            (scope, enabled, reason, changed_by),
        )
    record_event(
        trace_id=f"control-{uuid.uuid4().hex[:12]}",
        event_type="KILL_SWITCH_CHANGED",
        severity="CRITICAL" if enabled else "WARN",
        payload={"scope": scope, "enabled": enabled, "reason": reason, "changed_by": changed_by},
    )


def kill_switch_reason(ticker: Optional[str] = None) -> Optional[str]:
    scopes = ["GLOBAL"]
    if ticker:
        scopes.append(f"TICKER:{ticker.upper()}")
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT scope,reason FROM aiem_kill_switches
            WHERE enabled=TRUE AND scope = ANY(%s)
            ORDER BY CASE WHEN scope='GLOBAL' THEN 0 ELSE 1 END
            LIMIT 1
            """,
            (scopes,),
        )
        row = cur.fetchone()
    return f"{row[0]} kill switch: {row[1]}" if row else None


def options_session_open(now_utc: Optional[datetime] = None) -> bool:
    """
    Fail closed unless the official XNYS calendar confirms an active session.
    Install dependency: pip install exchange-calendars pandas
    """
    now_utc = now_utc or datetime.now(UTC)
    try:
        import exchange_calendars as xcals
        import pandas as pd
        cal = xcals.get_calendar("XNYS")
        ts = pd.Timestamp(now_utc)
        return bool(cal.is_open_on_minute(ts, ignore_breaks=True))
    except Exception as exc:
        log.error("Exchange-calendar validation failed: %s", exc)
        return False


def _daily_pnl() -> Decimal:
    """
    Realized: SUM(net_pnl) on ase_paper_trades for trades closed today (America/New_York).
    Unrealized: most recent total_unrealized_pnl from ape_portfolio_snapshots
    (the actual persisted portfolio-engine snapshot table — there is no
    per-row unrealized_pnl column on ase_paper_trades).
    Fail closed if P&L cannot be reconciled.
    """
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT COALESCE(SUM(net_pnl),0)
            FROM ase_paper_trades
            WHERE status='CLOSED'
              AND (close_time AT TIME ZONE 'America/New_York')::date
                  = (NOW() AT TIME ZONE 'America/New_York')::date
            """
        )
        (realized,) = cur.fetchone()

        cur.execute(
            """
            SELECT total_unrealized_pnl
            FROM ape_portfolio_snapshots
            ORDER BY snapshot_ts DESC
            LIMIT 1
            """
        )
        row = cur.fetchone()
        unrealized = row[0] if row else 0
    return Decimal(str(realized or 0)) + Decimal(str(unrealized or 0))


def pre_execution_controls(
    *,
    trace_id: str,
    ticker: str,
    strategy_name: str,
    max_loss: float,
) -> ControlDecision:
    if not ALLOW_PAPER_EXECUTION:
        reason = "AIEM_ALLOW_PAPER_EXECUTION is disabled"
        h = record_event(trace_id, "EXECUTION_BLOCKED", "CRITICAL", {"reason": reason})
        return ControlDecision(False, reason, trace_id, h)

    reason = kill_switch_reason(ticker)
    if reason:
        h = record_event(trace_id, "EXECUTION_BLOCKED", "CRITICAL", {"reason": reason})
        return ControlDecision(False, reason, trace_id, h)

    if not options_session_open():
        reason = "Official XNYS options session is not open or calendar validation failed"
        h = record_event(trace_id, "EXECUTION_BLOCKED", "WARN", {"reason": reason})
        return ControlDecision(False, reason, trace_id, h)

    if not max_loss or max_loss <= 0:
        reason = "Invalid/undefined max_loss"
        h = record_event(trace_id, "EXECUTION_BLOCKED", "CRITICAL", {"reason": reason})
        return ControlDecision(False, reason, trace_id, h)

    try:
        pnl = _daily_pnl()
    except Exception as exc:
        reason = f"Daily P&L reconciliation failed: {type(exc).__name__}: {exc}"
        h = record_event(trace_id, "EXECUTION_BLOCKED", "CRITICAL", {"reason": reason})
        return ControlDecision(False, reason, trace_id, h)

    projected = pnl - Decimal(str(max_loss))
    if pnl <= -MAX_DAILY_LOSS or projected <= -MAX_DAILY_LOSS:
        reason = f"Daily-loss control blocked trade: pnl={pnl}, projected={projected}, limit={MAX_DAILY_LOSS}"
        set_kill_switch("GLOBAL", True, reason, "daily_loss_control")
        h = record_event(trace_id, "DAILY_LOSS_BLOCK", "CRITICAL", {"reason": reason})
        return ControlDecision(False, reason, trace_id, h)

    h = record_event(
        trace_id,
        "PRE_EXECUTION_CONTROLS_PASS",
        "INFO",
        {"ticker": ticker, "strategy": strategy_name, "daily_pnl": str(pnl), "max_loss": max_loss},
    )
    return ControlDecision(True, "PASS", trace_id, h)


def _expiration_key(evaluation: Any) -> str:
    expirations = sorted(
        {
            str(getattr(leg, "expiration", None) or getattr(leg, "expiry", None) or "")
            for leg in getattr(evaluation, "legs", [])
        }
    )
    return ",".join(expirations) or "NO_EXPIRY"


def acquire_execution_lock(
    *,
    trace_id: str,
    ticker: str,
    strategy_name: str,
    expiration_key: str,
) -> Optional[str]:
    """One active trade per ticker/strategy/expiration key."""
    lock_key = f"{ticker.upper()}|{strategy_name}|{expiration_key}"
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO aiem_execution_locks
                  (lock_key,trace_id,ticker,strategy_name,expiration_key,status,metadata)
                VALUES (%s,%s,%s,%s,%s,'ACQUIRED','{}'::jsonb)
                """,
                (lock_key, trace_id, ticker.upper(), strategy_name, expiration_key),
            )
        record_event(trace_id, "EXECUTION_LOCK_ACQUIRED", "INFO", {"lock_key": lock_key})
        return lock_key
    except psycopg2.errors.UniqueViolation:
        record_event(trace_id, "DUPLICATE_EXECUTION_BLOCKED", "CRITICAL", {"lock_key": lock_key})
        return None


def update_lock(lock_key: str, status: str, metadata: Optional[Dict[str, Any]] = None) -> None:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE aiem_execution_locks
            SET status=%s, updated_at=NOW(), metadata=metadata || %s::jsonb
            WHERE lock_key=%s
            """,
            (status, _canonical(metadata or {}), lock_key),
        )


def create_pending_order(
    *,
    trace_id: str,
    ticker: str,
    strategy_name: str,
    lock_key: str,
    requested_qty: int,
    requested_price: Optional[float],
    metadata: Dict[str, Any],
) -> str:
    order_id = f"apo_{uuid.uuid4().hex[:18]}"
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO aiem_paper_orders
              (paper_order_id,trace_id,ticker,strategy_name,execution_lock_key,
               status,requested_qty,requested_price,metadata)
            VALUES (%s,%s,%s,%s,%s,'VALIDATING',%s,%s,%s::jsonb)
            """,
            (
                order_id, trace_id, ticker.upper(), strategy_name, lock_key,
                requested_qty, requested_price, _canonical(metadata),
            ),
        )
    update_lock(lock_key, "PENDING", {"paper_order_id": order_id})
    record_event(trace_id, "PAPER_ORDER_CREATED", "INFO", {"paper_order_id": order_id})
    return order_id


def update_order(
    order_id: str,
    status: str,
    *,
    filled_qty: Optional[int] = None,
    average_fill_price: Optional[float] = None,
    reason: Optional[str] = None,
    broker_order_id: Optional[str] = None,
) -> None:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE aiem_paper_orders SET
              status=%s,
              filled_qty=COALESCE(%s,filled_qty),
              average_fill_price=COALESCE(%s,average_fill_price),
              reject_reason=COALESCE(%s,reject_reason),
              broker_order_id=COALESCE(%s,broker_order_id),
              updated_at=NOW()
            WHERE paper_order_id=%s
            """,
            (status, filled_qty, average_fill_price, reason, broker_order_id, order_id),
        )


def guarded_heartbeat(status: str = "alive", details: Optional[Dict[str, Any]] = None) -> None:
    """
    Heartbeat failures are raised and recorded; they are never debug-only.
    Caller should let supervisor restart the process after repeated failure.
    """
    trace_id = f"heartbeat-{ENGINE_NAME}"
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO aiem_engine_health
                  (engine_name,host,pid,status,details,last_heartbeat)
                VALUES (%s,%s,%s,%s,%s::jsonb,NOW())
                ON CONFLICT(engine_name) DO UPDATE SET
                  host=EXCLUDED.host,pid=EXCLUDED.pid,status=EXCLUDED.status,
                  details=EXCLUDED.details,last_heartbeat=NOW()
                """,
                (ENGINE_NAME, socket.gethostname(), os.getpid(), status, _canonical(details or {})),
            )
    except Exception as exc:
        log.exception("CRITICAL heartbeat failure")
        raise RuntimeError(f"Heartbeat persistence failed: {exc}") from exc


def health_watchdog() -> None:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT engine_name,last_heartbeat
            FROM aiem_engine_health
            WHERE last_heartbeat < NOW() - (%s * INTERVAL '1 second')
            """,
            (HEARTBEAT_MAX_AGE_SECONDS,),
        )
        stale = cur.fetchall()
    for engine_name, last_heartbeat in stale:
        set_kill_switch(
            "GLOBAL",
            True,
            f"Stale heartbeat: {engine_name}, last={last_heartbeat}",
            "health_watchdog",
        )


def recover_and_reconcile() -> Dict[str, int]:
    """
    Recover stale pending paper orders and fail closed on ambiguous state.
    Existing FILLED orders must have a matching ase_paper_trades row by run_id.
    """
    stats = {"cancelled_stale": 0, "reconciled": 0, "failed": 0}
    with _conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT * FROM aiem_paper_orders
            WHERE status IN ('CREATED','VALIDATING','READY','SUBMITTED','ACKNOWLEDGED','PARTIALLY_FILLED')
              AND updated_at < NOW() - (%s * INTERVAL '1 second')
            FOR UPDATE SKIP LOCKED
            """,
            (PENDING_ORDER_MAX_AGE_SECONDS,),
        )
        for order in cur.fetchall():
            cur.execute(
                """
                UPDATE aiem_paper_orders
                SET status='CANCELLED',
                    reject_reason='restart/stale pending-order recovery',
                    updated_at=NOW()
                WHERE paper_order_id=%s
                """,
                (order["paper_order_id"],),
            )
            cur.execute(
                "UPDATE aiem_execution_locks SET status='CANCELLED',updated_at=NOW() WHERE lock_key=%s",
                (order["execution_lock_key"],),
            )
            stats["cancelled_stale"] += 1

        cur.execute(
            """
            SELECT o.paper_order_id,o.trace_id,o.execution_lock_key
            FROM aiem_paper_orders o
            LEFT JOIN ase_paper_trades p ON p.run_id=o.trace_id
            WHERE o.status IN ('FILLED','RECOVERED')
              AND p.paper_trade_id IS NULL
            """
        )
        missing = cur.fetchall()
        if missing:
            for row in missing:
                cur.execute(
                    """
                    UPDATE aiem_paper_orders
                    SET status='RECONCILE_FAILED',
                        reject_reason='FILLED order has no matching paper trade',
                        updated_at=NOW()
                    WHERE paper_order_id=%s
                    """,
                    (row["paper_order_id"],),
                )
                stats["failed"] += 1
            set_kill_switch(
                "GLOBAL",
                True,
                f"Paper-order reconciliation failed for {len(missing)} orders",
                "restart_reconciliation",
            )
    return stats


def execute_selected_paper_trade_fail_closed(
    *,
    evaluation: Any,
    selection: Any,
    ticker: str,
    thesis: str,
    market_regime: str,
    volatility_regime: str,
    event_context: Optional[str],
    run_id: str,
    underlying_price: float,
    requested_qty: int = 1,
) -> Optional[str]:
    """
    Single approved gateway from a selected strategy to existing insert_paper_trade().
    Any exception, missing proof, duplicate, risk rejection, or reconciliation failure blocks execution.
    """
    from aiem_portfolio_engine import run_portfolio_gate
    from aiem_strat_engine.paper_trader import insert_paper_trade, safety_check

    strategy_name = evaluation.strategy_name
    block = safety_check(evaluation)
    if block:
        record_event(run_id, "PAPER_EXECUTION_BLOCKED", "CRITICAL", {"reason": block})
        return None

    max_loss = float(evaluation.payoff_info.get("max_loss") or 0)
    controls = pre_execution_controls(
        trace_id=run_id,
        ticker=ticker,
        strategy_name=strategy_name,
        max_loss=max_loss,
    )
    if not controls.allowed:
        return None

    expiration_key = _expiration_key(evaluation)
    lock_key = acquire_execution_lock(
        trace_id=run_id,
        ticker=ticker,
        strategy_name=strategy_name,
        expiration_key=expiration_key,
    )
    if not lock_key:
        return None

    order_id = create_pending_order(
        trace_id=run_id,
        ticker=ticker,
        strategy_name=strategy_name,
        lock_key=lock_key,
        requested_qty=requested_qty,
        requested_price=evaluation.pricing_info.get("net_mid"),
        metadata={"thesis": thesis, "expiration_key": expiration_key},
    )

    try:
        update_order(order_id, "VALIDATING")

        # The gate already returns REJECT on internal exceptions; caller also fails closed.
        pe = run_portfolio_gate(
            evaluation=evaluation,
            selection=selection,
            ticker=ticker,
            run_id=run_id,
            db_url=DB_URL,
        )
        if not pe or not pe.gate_passed():
            reason = "Portfolio gate rejected or returned no decision"
            if pe:
                reason = "; ".join(pe.decision_reasons[:3]) or pe.decision
            update_order(order_id, "REJECTED", reason=reason)
            update_lock(lock_key, "CANCELLED", {"reason": reason})
            record_event(run_id, "PORTFOLIO_GATE_BLOCK", "CRITICAL", {"reason": reason})
            return None

        # Re-run controls immediately before insertion.
        controls2 = pre_execution_controls(
            trace_id=run_id,
            ticker=ticker,
            strategy_name=strategy_name,
            max_loss=max_loss,
        )
        if not controls2.allowed:
            update_order(order_id, "CANCELLED", reason=controls2.reason)
            update_lock(lock_key, "CANCELLED", {"reason": controls2.reason})
            return None

        update_order(order_id, "READY")
        paper_trade_id = insert_paper_trade(
            evaluation=evaluation,
            selection=selection,
            ticker=ticker,
            thesis=thesis,
            market_regime=market_regime,
            volatility_regime=volatility_regime,
            event_context=event_context,
            run_id=run_id,
            underlying_price=underlying_price,
        )
        if not paper_trade_id:
            reason = "Existing paper trader blocked or failed insertion"
            update_order(order_id, "REJECTED", reason=reason)
            update_lock(lock_key, "FAILED", {"reason": reason})
            record_event(run_id, "PAPER_INSERT_FAILED", "CRITICAL", {"reason": reason})
            return None

        # Paper simulator treats atomic DB insertion as acknowledgement/fill.
        fill_price = evaluation.pricing_info.get("net_mid")
        update_order(
            order_id,
            "FILLED",
            filled_qty=requested_qty,
            average_fill_price=fill_price,
            broker_order_id=paper_trade_id,
        )
        update_lock(
            lock_key,
            "PAPER_OPEN",
            {"paper_trade_id": paper_trade_id, "paper_order_id": order_id},
        )
        record_event(
            run_id,
            "PAPER_TRADE_OPENED",
            "INFO",
            {
                "paper_trade_id": paper_trade_id,
                "paper_order_id": order_id,
                "portfolio_gate_hash": pe.evidence_hash,
            },
        )
        return paper_trade_id

    except Exception as exc:
        reason = f"FAIL_CLOSED_EXECUTION_EXCEPTION: {type(exc).__name__}: {exc}"
        log.exception(reason)
        try:
            update_order(order_id, "REJECTED", reason=reason)
            update_lock(lock_key, "FAILED", {"reason": reason})
            record_event(run_id, "PAPER_EXECUTION_EXCEPTION", "CRITICAL", {"reason": reason})
        finally:
            return None


# REQUIRED SCHEDULER PATCH
#
# Replace the existing block beginning with:
#   try:
#       pe_decision = run_portfolio_gate(...)
#   ...
#   pt_id = insert_paper_trade(...)
#
# with:
#
# from aiem_operational_controls import execute_selected_paper_trade_fail_closed
#
# pt_id = execute_selected_paper_trade_fail_closed(
#     evaluation=selection.selected,
#     selection=selection,
#     ticker=ticker,
#     thesis=thesis,
#     market_regime=market_regime,
#     volatility_regime=vol_regime,
#     event_context=None,
#     run_id=run_id,
#     underlying_price=spot,
#     requested_qty=1,
# )
#
# At process startup:
#   from aiem_operational_controls import install_schema, recover_and_reconcile
#   install_schema()
#   recover_and_reconcile()
#
# Every minute:
#   guarded_heartbeat("alive", {"worker": "strategy_scheduler"})
#   health_watchdog()
#   recover_and_reconcile()


# ══════════════════════════════════════════════════════════════════════════
# Directive_Phase1_OperationalControls_Verification_2026-07-25
# Verify this module (standalone — not yet wired into the scheduler).
#
# Standing checklist (required on every item below):
#   - Raw command output only, no narrative/summary substitutes
#   - Raw grep -n / sed -n for any code-location or schema claim
#   - sha256 before/after for any file changed this session
#   - Raw SQL + full result set for any DB claim
#   - verified_run.sh / verify_chain.sh output + sha256 cross-check vs canonical
#   - No deletion/truncation of any row/table/file without explicit approval first
#   - State plainly if any item cannot be evidenced — do not pad
#
# Items to verify:
#
# 1. Schema match for _daily_pnl() — raw \d ase_paper_trades and
#    \d ape_portfolio_snapshots (or equivalent information_schema query)
#    proving net_pnl, close_time, status, total_unrealized_pnl, snapshot_ts
#    columns exist exactly as referenced below. Not a paraphrase — the raw
#    column list.
#
# 2. install_schema() execution — raw output of running it once, then raw
#    query listing the 5 new tables (aiem_kill_switches, aiem_execution_locks,
#    aiem_paper_orders, aiem_operational_events, aiem_engine_health)
#    confirming they exist and are seeded as expected (GLOBAL kill switch
#    row present, enabled=FALSE).
#
# 3. Hash chain integrity — call record_event() twice with test payloads,
#    then raw SQL showing both rows, confirming the second row's prev_hash
#    equals the first row's event_hash. Then a mutation check: manually
#    alter one row's payload after insertion and show that recomputing the
#    hash over the stored row no longer matches event_hash — proves the
#    chain isn't vacuous.
#
# 4. _daily_pnl() live check — run it against current data, then
#    independently run the same two SQL queries manually and confirm the
#    numbers match. If today's data is empty, state that plainly and
#    confirm the function returns 0, not an error.
#
# 5. kill_switch_reason() behavior — set GLOBAL to TRUE with a test reason,
#    call kill_switch_reason(), show it returns non-None; set back to
#    FALSE, show it returns None. Paste both raw calls/outputs.
#
# 6. No unintended footprint — git diff HEAD --stat confirming only this
#    file changed, nothing in the scheduler or existing modules touched
#    this session.
#
# 7. sha256 — before (original upload) and after (this fixed version) hash
#    of this file.
#
# Label the result per standing rule: "PASS" only if nothing outstanding
# and every item above has raw evidence with nothing deferred — otherwise
# use a lesser label ("partial," "cleared to proceed").
# ══════════════════════════════════════════════════════════════════════════
