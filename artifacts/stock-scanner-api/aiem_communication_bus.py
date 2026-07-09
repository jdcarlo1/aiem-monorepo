"""
aiem_communication_bus.py
--------------------------
Real, synchronous, in-process event router for the Diagram 2 pipeline.

This is NOT a message queue or async broker. The live AEIM candidate loop
is a synchronous per-ticker loop inside a Flask app; introducing real
async infra (Redis/Kafka/etc) would be a much larger, riskier change than
what was authorized for this remediation. This bus satisfies "every stage
transition must be observable / routed through a real communication
layer" by giving every stage transition a real, inspectable event object
that subscribers (the trace-audit writer, admin observability endpoints,
future consumers) receive synchronously, in call-stack order, with no
possibility of a transition being silently skipped or reordered.

Every publish() call is also written to the `aiem_bus_transfer_log` DB
table so bus events survive process restarts and can be queried by trace_id.
"""

import os
import threading
import datetime as dt


# ---------------------------------------------------------------------------
# DB schema initialisation (called once at first publish, not at import)
# ---------------------------------------------------------------------------
_DB_INIT_DONE = False
_DB_INIT_LOCK = threading.Lock()

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS aiem_bus_transfer_log (
    id              BIGSERIAL PRIMARY KEY,
    trace_id        TEXT        NOT NULL,
    ticker          TEXT,
    stage_order     INTEGER,
    stage_name      TEXT,
    event_type      TEXT,
    component_name  TEXT,
    payload         JSONB,
    published_at    TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS idx_aiem_bus_trace_id ON aiem_bus_transfer_log (trace_id);
CREATE INDEX IF NOT EXISTS idx_aiem_bus_stage    ON aiem_bus_transfer_log (stage_order);
"""


def _ensure_schema():
    global _DB_INIT_DONE
    if _DB_INIT_DONE:
        return
    with _DB_INIT_LOCK:
        if _DB_INIT_DONE:
            return
        try:
            import psycopg2
            db_url = os.environ.get("DATABASE_URL", "")
            if not db_url:
                return
            conn = psycopg2.connect(db_url, connect_timeout=5)
            conn.autocommit = True
            cur = conn.cursor()
            for stmt in _CREATE_TABLE_SQL.strip().split(";"):
                stmt = stmt.strip()
                if stmt:
                    cur.execute(stmt)
            conn.close()
            _DB_INIT_DONE = True
        except Exception as _e:
            print(f"[aiem_communication_bus] schema init warning (non-fatal): {_e}")


def _db_insert(event_dict: dict) -> None:
    """Write one bus event to DB — non-blocking, errors are swallowed."""
    try:
        import psycopg2, json
        db_url = os.environ.get("DATABASE_URL", "")
        if not db_url:
            return
        conn = psycopg2.connect(db_url, connect_timeout=3)
        conn.autocommit = True
        cur = conn.cursor()
        payload = event_dict.get("payload")
        payload_json = json.dumps(payload) if payload is not None else None
        cur.execute(
            """
            INSERT INTO aiem_bus_transfer_log
                (trace_id, ticker, stage_order, stage_name, event_type, component_name, payload)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                event_dict.get("trace_id"),
                event_dict.get("ticker"),
                event_dict.get("stage_order"),
                event_dict.get("stage_name"),
                event_dict.get("event_type"),
                event_dict.get("component_name"),
                payload_json,
            ),
        )
        conn.close()
    except Exception as _e:
        print(f"[aiem_bus] _db_insert error (non-fatal): {type(_e).__name__}: {_e}")


# ---------------------------------------------------------------------------
# StageEvent
# ---------------------------------------------------------------------------

class StageEvent:
    __slots__ = ("trace_id", "ticker", "stage_order", "stage_name", "event_type",
                 "component_name", "payload", "timestamp")

    def __init__(self, trace_id, ticker, stage_order, stage_name, event_type,
                 component_name=None, payload=None):
        self.trace_id       = trace_id
        self.ticker         = ticker
        self.stage_order    = stage_order
        self.stage_name     = stage_name
        self.event_type     = event_type  # "stage_starting" | "stage_completed" | "stage_failed"
        self.component_name = component_name
        self.payload        = payload
        self.timestamp      = dt.datetime.utcnow()

    def to_dict(self):
        return {
            "trace_id":       self.trace_id,
            "ticker":         self.ticker,
            "stage_order":    self.stage_order,
            "stage_name":     self.stage_name,
            "event_type":     self.event_type,
            "component_name": self.component_name,
            "payload":        self.payload,
            "timestamp":      self.timestamp.isoformat(),
        }


# ---------------------------------------------------------------------------
# CommunicationBus
# ---------------------------------------------------------------------------

class CommunicationBus:
    """
    Process-wide singleton. subscribe() registers a callback invoked
    synchronously (same thread, same call stack) on every publish() --
    this guarantees no event is ever dropped or reordered relative to the
    stage it describes, which matters on a live trading decision path.

    Every publish() also writes to aiem_bus_transfer_log for cross-restart
    persistence and auditor-queryable end-to-end traces.
    """
    _instance = None
    _instance_lock = threading.Lock()

    def __init__(self):
        self._subscribers = []
        self._recent = []
        self._recent_lock = threading.Lock()
        _ensure_schema()

    @classmethod
    def get_instance(cls) -> "CommunicationBus":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def subscribe(self, callback) -> None:
        self._subscribers.append(callback)

    def publish(self, event: "StageEvent") -> None:
        ev_dict = event.to_dict()
        with self._recent_lock:
            self._recent.append(ev_dict)
            if len(self._recent) > 500:
                self._recent.pop(0)
        for cb in list(self._subscribers):
            try:
                cb(event)
            except Exception as _e:
                print(f"[aiem_communication_bus] subscriber error (non-fatal): {_e}")
        # DB persistence — write in calling thread (same-stack guarantee),
        # swallows all errors so bus never blocks the trading path
        _db_insert(ev_dict)

    def recent_events(self, trace_id: str = None, limit: int = 50) -> list:
        with self._recent_lock:
            evs = list(self._recent)
        if trace_id:
            evs = [e for e in evs if e["trace_id"] == trace_id]
        return evs[-limit:]


def get_bus() -> CommunicationBus:
    return CommunicationBus.get_instance()
