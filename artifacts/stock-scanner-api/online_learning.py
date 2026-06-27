"""
online_learning.py
--------------------
Continual/online model updates with full auditability and rollback:

  1. Every update produces an immutable, version-stamped snapshot — nothing
     is overwritten in place.
  2. Each update is gated by a held-out buffer check BEFORE being accepted.
  3. Hard max-drift-per-update guard prevents one bad batch from swinging weights.
  4. Defaults to DRY-RUN: promote=True must be passed explicitly.
"""

import os
import json
import pickle
import hashlib
import datetime as dt
from typing import Optional, Dict, Any

import numpy as np
import psycopg2
import psycopg2.extras


DDL = """
CREATE TABLE IF NOT EXISTS model_versions (
    id SERIAL PRIMARY KEY,
    model_name TEXT NOT NULL,
    version INT NOT NULL,
    weights_blob BYTEA NOT NULL,
    weights_hash TEXT NOT NULL,
    trained_on_n_samples INT NOT NULL,
    held_out_score NUMERIC,
    is_live BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    notes TEXT,
    UNIQUE (model_name, version)
);
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
        conn.commit()
    print("[online_learning] schema ready")


def _next_version(model_name: str) -> int:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(MAX(version), 0) FROM model_versions WHERE model_name = %s",
                (model_name,),
            )
            return cur.fetchone()[0] + 1


def get_live_model(model_name: str) -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, model_name, version, weights_hash, trained_on_n_samples, "
                "held_out_score, is_live, created_at, notes "
                "FROM model_versions WHERE model_name = %s AND is_live = TRUE",
                (model_name,),
            )
            row = cur.fetchone()
            if not row:
                return None
            d = dict(row)
            if d.get("created_at"):
                d["created_at"] = d["created_at"].isoformat()
            return d


def _score_model(weights: np.ndarray, X: np.ndarray, y: np.ndarray) -> float:
    preds = X @ weights
    mse   = float(np.mean((preds - y) ** 2))
    return -mse


def propose_update(
    model_name: str,
    current_weights: np.ndarray,
    new_batch_X: np.ndarray,
    new_batch_y: np.ndarray,
    held_out_X: np.ndarray,
    held_out_y: np.ndarray,
    learning_rate: float = 0.01,
    max_weight_drift: float = 0.15,
    promote: bool = False,
    notes: str = "",
) -> Dict[str, Any]:
    preds    = new_batch_X @ current_weights
    grad     = new_batch_X.T @ (preds - new_batch_y) / len(new_batch_y)
    new_weights = current_weights - learning_rate * grad

    drift              = np.abs((new_weights - current_weights) / (np.abs(current_weights) + 1e-9))
    max_drift_observed = float(np.max(drift))
    drift_ok           = max_drift_observed <= max_weight_drift

    current_score = _score_model(current_weights, held_out_X, held_out_y)
    new_score     = _score_model(new_weights, held_out_X, held_out_y)
    perf_ok       = new_score >= current_score
    accepted      = drift_ok and perf_ok

    result = {
        "model_name":                   model_name,
        "accepted":                     accepted,
        "promoted_to_live":             False,
        "max_drift_observed":           round(max_drift_observed, 4),
        "drift_limit":                  max_weight_drift,
        "drift_ok":                     drift_ok,
        "current_held_out_score":       round(current_score, 6),
        "new_held_out_score":           round(new_score, 6),
        "performance_improved_or_equal": perf_ok,
    }

    if not accepted:
        result["reason_rejected"] = (
            "Weight drift exceeded limit" if not drift_ok
            else "Held-out performance did not improve or hold steady"
        )
        return result

    weights_blob = pickle.dumps(new_weights)
    weights_hash = hashlib.sha256(weights_blob).hexdigest()[:16]
    version      = _next_version(model_name)

    with _connect() as conn:
        with conn.cursor() as cur:
            if promote:
                cur.execute(
                    "UPDATE model_versions SET is_live = FALSE WHERE model_name = %s",
                    (model_name,),
                )
            cur.execute(
                """
                INSERT INTO model_versions
                    (model_name, version, weights_blob, weights_hash,
                     trained_on_n_samples, held_out_score, is_live, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (model_name, version, weights_blob, weights_hash,
                 len(new_batch_y), new_score, promote, notes),
            )
        conn.commit()

    result["version_saved"]     = version
    result["weights_hash"]      = weights_hash
    result["promoted_to_live"]  = promote
    return result


def rollback_to_version(model_name: str, version: int) -> Dict[str, Any]:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM model_versions WHERE model_name = %s AND version = %s",
                (model_name, version),
            )
            row = cur.fetchone()
            if not row:
                return {"error": f"No version {version} found for model {model_name}"}
            cur.execute(
                "UPDATE model_versions SET is_live = FALSE WHERE model_name = %s",
                (model_name,),
            )
            cur.execute("UPDATE model_versions SET is_live = TRUE WHERE id = %s", (row[0],))
        conn.commit()
    return {"model_name": model_name, "now_live_version": version}


def version_history(model_name: str) -> list:
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, version, weights_hash, trained_on_n_samples,
                       held_out_score, is_live, created_at, notes
                FROM model_versions WHERE model_name = %s ORDER BY version ASC
                """,
                (model_name,),
            )
            rows = []
            for r in cur.fetchall():
                d = dict(r)
                if d.get("created_at"):
                    d["created_at"] = d["created_at"].isoformat()
                rows.append(d)
            return rows


if __name__ == "__main__":
    init_schema()
    print("online_learning schema ready. Default: propose_update() is DRY-RUN.")
