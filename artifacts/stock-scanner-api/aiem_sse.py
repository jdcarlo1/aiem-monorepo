"""
AIEM Real-Time SSE Module — Phase 3 Remediation
================================================
Provides a unified Server-Sent Events endpoint for all critical event categories:
  decisions, candidates, paper_trade, paper_order, fill, reject,
  portfolio_risk, system_health, audit, alert,
  scheduler_failure, provider_failure, evidence_chain_failure

Architecture:
  - aiem_sse_event_log table acts as durable event bus (monotonic id = sequence).
  - Background DB poller reads source tables every 2s, publishes to event_log.
  - Per-connection SSE generator polls event_log from Last-Event-ID.
  - Role-based category filtering enforced server-side.
  - Heartbeat every 30s when no events.
  - Max 100 concurrent SSE connections.
  - Schema version "1.0" in every event payload.
"""

import json
import os
import threading
import time
from datetime import datetime, timezone

import psycopg2
from flask import Blueprint, Response, jsonify, request

# Category → minimum role required to receive it
CATEGORY_MIN_ROLE = {
    "alert":                  "viewer",
    "system_health":          "viewer",
    "candidates":             "trader",
    "paper_trade":            "trader",
    "paper_order":            "trader",
    "fill":                   "trader",
    "reject":                 "trader",
    "decisions":              "analyst",
    "portfolio_risk":         "analyst",
    "scheduler_failure":      "risk_manager",
    "provider_failure":       "risk_manager",
    "audit":                  "auditor",
    "evidence_chain_failure": "auditor",
}

ALL_CATEGORIES = list(CATEGORY_MIN_ROLE.keys())

ROLE_LEVELS = {
    "viewer": 1, "institutional_ddv": 1, "trader": 2, "analyst": 3,
    "risk_manager": 4, "auditor": 5, "administrator": 10,
}
MAX_CONNECTIONS = 100
HEARTBEAT_INTERVAL = 30       # seconds
POLL_INTERVAL     = 0.5       # SSE generator poll cycle
POLLER_INTERVAL   = 2.0       # background source-table poll cycle
EVENT_RETENTION   = 72        # hours — events older than this are purged by maintenance job
SCHEMA_VERSION    = "1.0"

_DB_URL: str = ""

# ── Connection limiter ────────────────────────────────────────────────────────
_conn_lock  = threading.Lock()
_conn_count = 0


def _conn_acquire() -> bool:
    global _conn_count
    with _conn_lock:
        if _conn_count >= MAX_CONNECTIONS:
            return False
        _conn_count += 1
        return True


def _conn_release() -> None:
    global _conn_count
    with _conn_lock:
        _conn_count = max(0, _conn_count - 1)


# ── DB helpers ────────────────────────────────────────────────────────────────

def _db():
    return psycopg2.connect(_DB_URL, connect_timeout=5,
                            options="-c statement_timeout=5000")


# ── Schema bootstrap ──────────────────────────────────────────────────────────

SSE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS aiem_sse_event_log (
    id          BIGSERIAL PRIMARY KEY,
    category    TEXT NOT NULL,
    payload     JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_sse_event_log_cat_id  ON aiem_sse_event_log (category, id);
CREATE INDEX IF NOT EXISTS ix_sse_event_log_created ON aiem_sse_event_log (created_at);

CREATE TABLE IF NOT EXISTS aiem_sse_poller_state (
    source_table TEXT PRIMARY KEY,
    last_seen_id BIGINT  NOT NULL DEFAULT 0,
    last_seen_ts TIMESTAMPTZ,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def init_sse_module(db_url: str) -> None:
    global _DB_URL
    _DB_URL = db_url
    try:
        with _db() as c, c.cursor() as cu:
            for stmt in SSE_SCHEMA_SQL.strip().split(";"):
                s = stmt.strip()
                if s:
                    cu.execute(s)
            c.commit()
        print("[aiem_sse] Schema ready", flush=True)
        _start_poller()
        _start_maintenance()
    except Exception as exc:
        print(f"[aiem_sse] init error: {exc}", flush=True)


# ── Event publishing ──────────────────────────────────────────────────────────

def publish_event(category: str, payload: dict) -> int | None:
    """Insert one event into the log. Returns the assigned id."""
    try:
        full_payload = {
            "schema_version": SCHEMA_VERSION,
            "category": category,
            "ts": datetime.now(timezone.utc).isoformat(),
            **payload,
        }
        with _db() as c, c.cursor() as cu:
            cu.execute(
                "INSERT INTO aiem_sse_event_log (category, payload) VALUES (%s,%s) RETURNING id",
                (category, json.dumps(full_payload)),
            )
            eid = cu.fetchone()[0]
            c.commit()
        return eid
    except Exception as exc:
        print(f"[aiem_sse] publish error: {exc}", flush=True)
        return None


def _poll_new_events(min_id: int, allowed_categories: list[str]) -> list[dict]:
    """Return events with id > min_id in allowed_categories."""
    if not allowed_categories:
        return []
    try:
        with _db() as c, c.cursor() as cu:
            cu.execute(
                "SELECT id, category, payload, created_at FROM aiem_sse_event_log "
                "WHERE id>%s AND category=ANY(%s) ORDER BY id LIMIT 50",
                (min_id, allowed_categories),
            )
            return [
                {"id": r[0], "category": r[1], "payload": r[2], "created_at": r[3]}
                for r in cu.fetchall()
            ]
    except Exception:
        return []


# ── Background source-table poller ────────────────────────────────────────────

def _get_poller_state(cu, source: str) -> int:
    cu.execute("SELECT last_seen_id FROM aiem_sse_poller_state WHERE source_table=%s", (source,))
    row = cu.fetchone()
    return row[0] if row else 0


def _set_poller_state(cu, source: str, last_id: int) -> None:
    cu.execute(
        "INSERT INTO aiem_sse_poller_state (source_table, last_seen_id, updated_at) "
        "VALUES (%s,%s,now()) ON CONFLICT (source_table) DO UPDATE "
        "SET last_seen_id=%s, updated_at=now()",
        (source, last_id, last_id),
    )


def _safe_poll_table(poll_fn) -> None:
    """Run a poller function, swallowing exceptions."""
    try:
        poll_fn()
    except Exception as exc:
        print(f"[aiem_sse] poller error: {exc}", flush=True)


def _poll_decisions():
    # oe_decision_audit uses TEXT pk (decision_id); track by created_at
    with _db() as c, c.cursor() as cu:
        cu.execute("SELECT last_seen_ts FROM aiem_sse_poller_state WHERE source_table='oe_decision_audit'")
        row = cu.fetchone()
        last_ts = row[0] if row else None
        if last_ts:
            cu.execute(
                "SELECT decision_id, identity_json, created_at FROM oe_decision_audit "
                "WHERE created_at>%s AND is_test_record=FALSE ORDER BY created_at LIMIT 20",
                (last_ts,),
            )
        else:
            cu.execute(
                "SELECT decision_id, identity_json, created_at FROM oe_decision_audit "
                "WHERE is_test_record=FALSE ORDER BY created_at DESC LIMIT 1"
            )
        rows = cu.fetchall()
        max_ts = last_ts
        for r in rows:
            ident = r[1] if isinstance(r[1], dict) else {}
            publish_event("decisions", {
                "decision_id": r[0],
                "ticker": ident.get("ticker"),
                "decision_type": ident.get("decision_type"),
                "ts": r[2].isoformat() if r[2] else None,
            })
            if r[2] and (max_ts is None or r[2] > max_ts):
                max_ts = r[2]
        if rows and max_ts:
            cu.execute(
                "INSERT INTO aiem_sse_poller_state (source_table, last_seen_id, last_seen_ts, updated_at) "
                "VALUES ('oe_decision_audit',0,%s,now()) ON CONFLICT (source_table) DO UPDATE "
                "SET last_seen_ts=%s, updated_at=now()",
                (max_ts, max_ts),
            )
            c.commit()


def _poll_candidates():
    with _db() as c, c.cursor() as cu:
        last_id = _get_poller_state(cu, "aiem_options_alerts")
        cu.execute(
            "SELECT id, ticker, direction, strike, expiry, selected_score, created_at "
            "FROM aiem_options_alerts WHERE id>%s ORDER BY id LIMIT 20",
            (last_id,),
        )
        rows = cu.fetchall()
        for row in rows:
            payload = {
                "ticker": row[1], "direction": row[2],
                "strike": float(row[3] or 0), "expiry": str(row[4] or ""),
                "selected_score": float(row[5] or 0),
                "ts": row[6].isoformat() if row[6] else None,
            }
            publish_event("candidates", payload)
            publish_event("alert", payload)
            last_id = max(last_id, row[0])
        if rows:
            _set_poller_state(cu, "aiem_options_alerts", last_id)
            c.commit()


def _poll_paper_trades():
    with _db() as c, c.cursor() as cu:
        last_id = _get_poller_state(cu, "aiem_paper_trades")
        cu.execute(
            "SELECT id, ticker, direction, entry_price, notional, status, pnl_pct, created_at "
            "FROM aiem_paper_trades WHERE id>%s ORDER BY id LIMIT 20",
            (last_id,),
        )
        rows = cu.fetchall()
        for row in rows:
            payload = {
                "ticker": row[1], "direction": row[2],
                "entry_price": float(row[3] or 0), "notional": float(row[4] or 0),
                "status": row[5], "pnl_pct": float(row[6] or 0) if row[6] else None,
                "ts": row[7].isoformat() if row[7] else None,
            }
            publish_event("paper_trade", payload)
            if row[5] in ("CLOSED", "CLOSED_AIEM"):
                publish_event("fill", payload)
            last_id = max(last_id, row[0])
        if rows:
            _set_poller_state(cu, "aiem_paper_trades", last_id)
            c.commit()


def _poll_paper_orders():
    with _db() as c, c.cursor() as cu:
        last_id = _get_poller_state(cu, "aiem_paper_execution_log")
        cu.execute(
            "SELECT id, status, trades_inserted, error_msg, trigger_source, started_at "
            "FROM aiem_paper_execution_log WHERE id>%s ORDER BY id LIMIT 20",
            (last_id,),
        )
        rows = cu.fetchall()
        _REJECT_STATUSES = ("NO_CANDIDATES", "BLOCKED_G0", "SKIPPED")
        for row in rows:
            payload = {
                "status": row[1], "trades_inserted": row[2],
                "error_msg": row[3], "trigger_source": row[4],
                "ts": row[5].isoformat() if row[5] else None,
            }
            publish_event("paper_order", payload)
            if row[1] in _REJECT_STATUSES:
                publish_event("reject", payload)
            last_id = max(last_id, row[0])
        if rows:
            _set_poller_state(cu, "aiem_paper_execution_log", last_id)
            c.commit()


def _poll_system_health():
    """Emit system_health + scheduler_failure events from job_heartbeats.
    job_heartbeats has no integer PK; track by last_attempt timestamp."""
    with _db() as c, c.cursor() as cu:
        cu.execute("SELECT last_seen_ts FROM aiem_sse_poller_state WHERE source_table='job_heartbeats'")
        row = cu.fetchone()
        last_ts = row[0] if row else None
        if last_ts:
            cu.execute(
                "SELECT job_name, last_success, last_attempt, last_error, consecutive_failures "
                "FROM job_heartbeats WHERE last_attempt > %s ORDER BY last_attempt LIMIT 20",
                (last_ts,),
            )
        else:
            cu.execute(
                "SELECT job_name, last_success, last_attempt, last_error, consecutive_failures "
                "FROM job_heartbeats ORDER BY last_attempt DESC NULLS LAST LIMIT 20"
            )
        rows = cu.fetchall()
        max_ts = last_ts
        for row in rows:
            # job_heartbeats.last_success / last_attempt are TIMESTAMP WITHOUT TIME ZONE
            # (naive). aiem_sse_poller_state.last_seen_ts is TIMESTAMPTZ (aware).
            # Normalize naive timestamps to UTC-aware before comparison to avoid
            # "can't compare offset-naive and offset-aware datetimes".
            r_success = row[1].replace(tzinfo=timezone.utc) if row[1] and row[1].tzinfo is None else row[1]
            r_attempt = row[2].replace(tzinfo=timezone.utc) if row[2] and row[2].tzinfo is None else row[2]
            payload = {
                "job_name": row[0],
                "last_success": r_success.isoformat() if r_success else None,
                "ts": r_attempt.isoformat() if r_attempt else None,
                "consecutive_failures": int(row[4] or 0),
            }
            publish_event("system_health", payload)
            if int(row[4] or 0) > 0:
                publish_event("scheduler_failure", {**payload, "last_error": row[3]})
            if r_attempt and (max_ts is None or r_attempt > max_ts):
                max_ts = r_attempt
        if rows and max_ts:
            cu.execute(
                "INSERT INTO aiem_sse_poller_state (source_table, last_seen_id, last_seen_ts, updated_at) "
                "VALUES ('job_heartbeats',0,%s,now()) ON CONFLICT (source_table) DO UPDATE "
                "SET last_seen_ts=%s, updated_at=now()",
                (max_ts, max_ts),
            )
            c.commit()


def _poll_audit():
    with _db() as c, c.cursor() as cu:
        last_id = _get_poller_state(cu, "d3_governance_decisions")
        cu.execute(
            "SELECT id, checkpoint, decision, trace_id, response_timestamp_utc "
            "FROM d3_governance_decisions WHERE id>%s AND is_test_record=FALSE ORDER BY id LIMIT 20",
            (last_id,),
        )
        rows = cu.fetchall()
        for row in rows:
            publish_event("audit", {
                "checkpoint": row[1], "decision": row[2],
                "trace_id": row[3],
                "ts": row[4].isoformat() if row[4] else None,
            })
            last_id = max(last_id, row[0])
        if rows:
            _set_poller_state(cu, "d3_governance_decisions", last_id)
            c.commit()


def _poll_portfolio_risk():
    """Publish a portfolio-risk snapshot every cycle."""
    try:
        with _db() as c, c.cursor() as cu:
            cu.execute(
                "SELECT COUNT(*) as open_count, SUM(notional) as total_notional, "
                "COUNT(CASE WHEN direction IN ('PUT','SHORT') THEN 1 END) as short_count "
                "FROM aiem_paper_trades WHERE status='OPEN'"
            )
            row = cu.fetchone()
            if row:
                publish_event("portfolio_risk", {
                    "open_positions": int(row[0] or 0),
                    "total_notional": float(row[1] or 0),
                    "short_count": int(row[2] or 0),
                    "ts": datetime.now(timezone.utc).isoformat(),
                })
    except Exception:
        pass


def _poll_evidence_chain():
    """Check for recent evidence chain anomalies."""
    try:
        with _db() as c, c.cursor() as cu:
            cu.execute(
                "SELECT id, seq, created_at FROM aiem_evidence_chain "
                "ORDER BY id DESC LIMIT 1"
            )
            row = cu.fetchone()
            if row:
                last_id = _get_poller_state(cu, "aiem_evidence_chain")
                if row[0] > last_id:
                    publish_event("evidence_chain_failure", {
                        "seq": int(row[1] or 0),
                        "ts": row[2].isoformat() if row[2] else None,
                        "detail": "new evidence chain entry detected",
                    })
                    _set_poller_state(cu, "aiem_evidence_chain", row[0])
                    c.commit()
    except Exception:
        pass


_POLLERS = [
    ("decisions",       _poll_decisions),
    ("candidates",      _poll_candidates),
    ("paper_trades",    _poll_paper_trades),
    ("paper_orders",    _poll_paper_orders),
    ("system_health",   _poll_system_health),
    ("audit",           _poll_audit),
    ("portfolio_risk",  _poll_portfolio_risk),
    ("evidence_chain",  _poll_evidence_chain),
]

_poller_running = False


def _poller_loop():
    time.sleep(5)  # Let startup settle
    while True:
        for name, fn in _POLLERS:
            _safe_poll_table(fn)
        time.sleep(POLLER_INTERVAL)


def _start_poller():
    global _poller_running
    if _poller_running:
        return
    _poller_running = True
    t = threading.Thread(target=_poller_loop, daemon=True, name="sse-poller")
    t.start()
    print("[aiem_sse] Background poller started", flush=True)


def _maintenance_loop():
    """Purge old events periodically."""
    while True:
        time.sleep(3600)
        try:
            with _db() as c, c.cursor() as cu:
                cu.execute(
                    "DELETE FROM aiem_sse_event_log "
                    "WHERE created_at < now() - interval '%s hours'",
                    (EVENT_RETENTION,),
                )
                c.commit()
        except Exception:
            pass


def _start_maintenance():
    t = threading.Thread(target=_maintenance_loop, daemon=True, name="sse-maintenance")
    t.start()


# ── SSE generator ─────────────────────────────────────────────────────────────

def _allowed_categories(role: str, requested: list[str]) -> list[str]:
    """Filter requested categories to those the role is permitted to receive."""
    role_level = ROLE_LEVELS.get(role, 0)
    return [
        cat for cat in requested
        if cat in CATEGORY_MIN_ROLE
        and role_level >= ROLE_LEVELS.get(CATEGORY_MIN_ROLE[cat], 999)
    ]


def _sse_generator(start_seq: int, allowed_cats: list[str]):
    last_seq = start_seq
    last_event_time = time.time()
    try:
        while True:
            events = _poll_new_events(last_seq, allowed_cats)
            for ev in events:
                last_seq = ev["id"]
                last_event_time = time.time()
                payload = ev["payload"]
                if isinstance(payload, str):
                    payload = json.loads(payload)
                payload["seq"] = ev["id"]
                payload["schema_version"] = SCHEMA_VERSION
                data = json.dumps(payload, default=str)
                yield f"id: {ev['id']}\nevent: {ev['category']}\ndata: {data}\n\n"

            now = time.time()
            if now - last_event_time >= HEARTBEAT_INTERVAL:
                last_event_time = now
                yield f"data: {json.dumps({'type': 'heartbeat', 'seq': last_seq, 'schema_version': SCHEMA_VERSION})}\n\n"

            time.sleep(POLL_INTERVAL)
    except GeneratorExit:
        pass
    finally:
        _conn_release()


# ── Blueprint ─────────────────────────────────────────────────────────────────

sse_bp = Blueprint("aiem_sse", __name__)


@sse_bp.route("/stock-api/events/stream", methods=["GET"])
def events_stream():
    """
    GET /stock-api/events/stream?categories=cat1,cat2
    Headers: Cookie: aiem_session=... (or X-Admin-Token for backward compat)
    Opt headers: Last-Event-ID: <seq>
    Returns: text/event-stream

    Emits events with:
      id: <seq>           — monotonic; client sends back as Last-Event-ID on reconnect
      event: <category>   — one of the 13 defined categories
      data: <JSON>        — {schema_version:"1.0", seq, category, ts, ...payload}

    Heartbeat every 30s: data: {"type":"heartbeat", "seq":<last>, "schema_version":"1.0"}

    Auth required (viewer+). Categories filtered by role.
    Max 100 concurrent connections; returns 429 when limit reached.
    """
    # Auth — import lazily to avoid circular import
    try:
        from aiem_auth import get_current_user
        user = get_current_user()
    except Exception:
        user = None
    if user is None:
        return jsonify({"error": "unauthorized", "detail": "authentication required"}), 401

    role = user.get("role", "viewer")

    # Connection limit
    if not _conn_acquire():
        return jsonify({"error": "connection limit reached"}), 429

    # Requested categories
    cats_param = request.args.get("categories", ",".join(ALL_CATEGORIES))
    requested  = [c.strip() for c in cats_param.split(",") if c.strip()]
    allowed    = _allowed_categories(role, requested)
    if not allowed:
        _conn_release()
        return jsonify({"error": "no authorized categories"}), 403

    # Last-Event-ID for missed-event recovery
    start_seq = 0
    lei_header = request.headers.get("Last-Event-ID", "")
    if lei_header:
        try:
            start_seq = int(lei_header)
        except ValueError:
            pass

    headers = {
        "Cache-Control": "no-cache, no-store",
        "X-Accel-Buffering": "no",
        "Access-Control-Allow-Origin": request.headers.get("Origin", "*"),
        "Access-Control-Allow-Credentials": "true",
    }
    return Response(
        _sse_generator(start_seq, allowed),
        content_type="text/event-stream",
        headers=headers,
    )


@sse_bp.route("/stock-api/events/publish", methods=["POST"])
def events_publish():
    """
    POST /stock-api/events/publish — internal/admin-only event injection.
    Body: {"category": "...", "payload": {...}}
    """
    try:
        from aiem_auth import get_current_user, ROLE_LEVELS
        user = get_current_user()
        if user is None:
            return jsonify({"error": "unauthorized"}), 401
        if ROLE_LEVELS.get(user.get("role", ""), 0) < ROLE_LEVELS["administrator"]:
            return jsonify({"error": "forbidden"}), 403
    except Exception:
        return jsonify({"error": "unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    category = body.get("category", "")
    payload  = body.get("payload", {})

    if category not in ALL_CATEGORIES:
        return jsonify({"error": f"unknown category {category!r}", "valid": ALL_CATEGORIES}), 400

    eid = publish_event(category, payload)
    if eid is None:
        return jsonify({"error": "publish failed"}), 503
    return jsonify({"status": "ok", "id": eid, "category": category})


@sse_bp.route("/stock-api/events/status", methods=["GET"])
def events_status():
    """GET /stock-api/events/status — SSE infrastructure health."""
    try:
        from aiem_auth import get_current_user
        user = get_current_user()
    except Exception:
        user = None
    if user is None:
        return jsonify({"error": "unauthorized"}), 401

    try:
        with _db() as c, c.cursor() as cu:
            cu.execute("SELECT COUNT(*), MAX(id), MAX(created_at) FROM aiem_sse_event_log")
            row = cu.fetchone()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 503

    with _conn_lock:
        conns = _conn_count

    return jsonify({
        "active_connections": conns,
        "max_connections": MAX_CONNECTIONS,
        "total_events": int(row[0] or 0),
        "max_seq": int(row[1] or 0),
        "latest_event_at": row[2].isoformat() if row[2] else None,
        "categories": ALL_CATEGORIES,
        "category_roles": CATEGORY_MIN_ROLE,
        "retention_hours": EVENT_RETENTION,
        "schema_version": SCHEMA_VERSION,
        "poller_interval_sec": POLLER_INTERVAL,
        "heartbeat_interval_sec": HEARTBEAT_INTERVAL,
    })
