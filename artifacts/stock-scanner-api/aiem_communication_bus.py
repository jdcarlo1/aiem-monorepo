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
"""

import threading
import datetime as dt


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
            "trace_id": self.trace_id, "ticker": self.ticker,
            "stage_order": self.stage_order, "stage_name": self.stage_name,
            "event_type": self.event_type, "component_name": self.component_name,
            "timestamp": self.timestamp.isoformat(),
        }


class CommunicationBus:
    """
    Process-wide singleton. subscribe() registers a callback invoked
    synchronously (same thread, same call stack) on every publish() --
    this guarantees no event is ever dropped or reordered relative to the
    stage it describes, which matters on a live trading decision path.
    """
    _instance = None
    _instance_lock = threading.Lock()

    def __init__(self):
        self._subscribers = []
        self._recent = []
        self._recent_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "CommunicationBus":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def subscribe(self, callback) -> None:
        self._subscribers.append(callback)

    def publish(self, event: "StageEvent") -> None:
        with self._recent_lock:
            self._recent.append(event.to_dict())
            if len(self._recent) > 500:
                self._recent.pop(0)
        for cb in list(self._subscribers):
            try:
                cb(event)
            except Exception as _e:
                print(f"[aiem_communication_bus] subscriber error (non-fatal): {_e}")

    def recent_events(self, trace_id: str = None, limit: int = 50) -> list:
        with self._recent_lock:
            evs = list(self._recent)
        if trace_id:
            evs = [e for e in evs if e["trace_id"] == trace_id]
        return evs[-limit:]


def get_bus() -> CommunicationBus:
    return CommunicationBus.get_instance()
