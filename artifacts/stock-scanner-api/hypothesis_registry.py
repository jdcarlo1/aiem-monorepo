"""
hypothesis_registry.py
-----------------------
Pre-registration system for AIEM's hypothesis testing.
Prevents multiple-comparisons inflation by forcing every hypothesis to be
registered BEFORE testing and permanently locking results once recorded.
"""

import os
import json
import hashlib
import datetime as dt
from dataclasses import dataclass
from typing import Optional, List, Dict, Any

import psycopg2
import psycopg2.extras


DDL = """
CREATE TABLE IF NOT EXISTS hypothesis_registry (
    id SERIAL PRIMARY KEY,
    hypothesis_hash TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    parameters JSONB NOT NULL,
    train_start DATE NOT NULL,
    train_end DATE NOT NULL,
    test_start DATE NOT NULL,
    test_end DATE NOT NULL,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    locked BOOLEAN NOT NULL DEFAULT FALSE,
    result JSONB,
    result_recorded_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS hypothesis_counter (
    id INT PRIMARY KEY DEFAULT 1,
    total_registered INT NOT NULL DEFAULT 0,
    CHECK (id = 1)
);
INSERT INTO hypothesis_counter (id, total_registered)
VALUES (1, 0) ON CONFLICT (id) DO NOTHING;
"""

LOCK_TRIGGER_SQL = """
CREATE OR REPLACE FUNCTION prevent_locked_update() RETURNS TRIGGER AS $$
BEGIN
    IF OLD.locked = TRUE THEN
        RAISE EXCEPTION 'Cannot modify a locked hypothesis (id=%)', OLD.id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS lock_enforcement ON hypothesis_registry;
CREATE TRIGGER lock_enforcement
    BEFORE UPDATE ON hypothesis_registry
    FOR EACH ROW
    EXECUTE FUNCTION prevent_locked_update();
"""


def _connect():
    url = os.environ.get("AIEM_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("No database URL found (set AIEM_DATABASE_URL or DATABASE_URL).")
    return psycopg2.connect(url)


def init_schema():
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(DDL)
            try:
                cur.execute(LOCK_TRIGGER_SQL)
            except Exception:
                pass
        conn.commit()
    print("[hypothesis_registry] schema ready")


@dataclass
class Hypothesis:
    name: str
    description: str
    parameters: Dict[str, Any]
    train_start: str
    train_end: str
    test_start: str
    test_end: str

    def hash(self) -> str:
        payload = json.dumps(
            {
                "name": self.name,
                "parameters": self.parameters,
                "train_start": self.train_start,
                "train_end": self.train_end,
                "test_start": self.test_start,
                "test_end": self.test_end,
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()


def register_hypothesis(h: Hypothesis) -> int:
    test_start_date = dt.date.fromisoformat(h.test_start)
    train_end_date  = dt.date.fromisoformat(h.train_end)
    if test_start_date <= train_end_date:
        raise ValueError("test_start must be strictly after train_end")

    h_hash = h.hash()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, locked FROM hypothesis_registry WHERE hypothesis_hash = %s",
                (h_hash,),
            )
            existing = cur.fetchone()
            if existing:
                raise ValueError(
                    f"This exact hypothesis is already registered (id={existing[0]}, "
                    f"locked={existing[1]}). Register a genuinely new variant instead."
                )
            cur.execute(
                """
                INSERT INTO hypothesis_registry
                    (hypothesis_hash, name, description, parameters,
                     train_start, train_end, test_start, test_end)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    h_hash, h.name, h.description, json.dumps(h.parameters),
                    h.train_start, h.train_end, h.test_start, h.test_end,
                ),
            )
            new_id = cur.fetchone()[0]
            cur.execute(
                "UPDATE hypothesis_counter SET total_registered = total_registered + 1 WHERE id = 1"
            )
        conn.commit()
    return new_id


def record_result(hypothesis_id: int, result: Dict[str, Any]):
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT locked FROM hypothesis_registry WHERE id = %s", (hypothesis_id,)
            )
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"No hypothesis with id={hypothesis_id}")
            if row[0]:
                raise PermissionError(
                    f"Hypothesis {hypothesis_id} is already locked. Results cannot "
                    f"be overwritten. Register a new hypothesis if you need to retest."
                )
            cur.execute(
                """
                UPDATE hypothesis_registry
                SET result = %s, result_recorded_at = now(), locked = TRUE
                WHERE id = %s
                """,
                (json.dumps(result), hypothesis_id),
            )
        conn.commit()


def get_total_registered() -> int:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT total_registered FROM hypothesis_counter WHERE id = 1")
            return cur.fetchone()[0]


def bonferroni_adjusted_alpha(alpha: float = 0.05) -> float:
    n = max(get_total_registered(), 1)
    return alpha / n


def list_locked_results() -> List[Dict[str, Any]]:
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, name, parameters, train_start, train_end,
                       test_start, test_end, registered_at,
                       result, result_recorded_at
                FROM hypothesis_registry
                WHERE locked = TRUE
                ORDER BY registered_at ASC
                """
            )
            rows = []
            for r in cur.fetchall():
                d = dict(r)
                for k in ("train_start", "train_end", "test_start", "test_end"):
                    if d.get(k):
                        d[k] = str(d[k])
                if d.get("registered_at"):
                    d["registered_at"] = d["registered_at"].isoformat()
                if d.get("result_recorded_at"):
                    d["result_recorded_at"] = d["result_recorded_at"].isoformat()
                rows.append(d)
            return rows


def list_all_hypotheses(limit: int = 50) -> List[Dict[str, Any]]:
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, name, description, train_start, train_end,
                       test_start, test_end, registered_at, locked,
                       result_recorded_at
                FROM hypothesis_registry
                ORDER BY registered_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = []
            for r in cur.fetchall():
                d = dict(r)
                for k in ("train_start", "train_end", "test_start", "test_end"):
                    if d.get(k):
                        d[k] = str(d[k])
                if d.get("registered_at"):
                    d["registered_at"] = d["registered_at"].isoformat()
                if d.get("result_recorded_at"):
                    d["result_recorded_at"] = d["result_recorded_at"].isoformat()
                rows.append(d)
            return rows


if __name__ == "__main__":
    init_schema()
    print(f"Total hypotheses registered so far: {get_total_registered()}")
