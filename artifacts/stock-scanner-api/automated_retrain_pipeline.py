"""
automated_retrain_pipeline.py
--------------------------------
Automates the MECHANICAL steps of getting smarter over time:

  1. Pulls all new labeled data accumulated since the last retrain
  2. Retrains the relevant model automatically
  3. Runs the SAME held-out evaluation as before, automatically
  4. Compares new-version performance against the CURRENTLY LIVE version
  5. Writes a retrain report and adds it to a PROMOTION QUEUE

What this does NOT do automatically: flip a new model version live. That
step requires you to call approve_promotion() after reading the retrain
report. If retraining and promoting were both fully automatic, then six
months from now if a model version quietly got worse, you'd have no way to
know WHICH version made WHICH pick or to trace back why the track record
changed. The one-step human gate is what keeps every number in your track
record attributable and explainable — which is exactly what makes a real
track record worth anything later.

Can be run on a schedule (steps 1-4) without manual intervention.
Only step 5 (promotion) needs you.

REQUIRES: AIEM_DATABASE_URL.
"""

import os
import json
import datetime as dt
from typing import Dict, Any, List, Optional, Callable

import psycopg2
import psycopg2.extras


DDL = """
CREATE TABLE IF NOT EXISTS retrain_runs (
    id SERIAL PRIMARY KEY,
    model_name TEXT NOT NULL,
    run_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    n_new_training_examples INT NOT NULL,
    new_version_held_out_metrics JSONB NOT NULL,
    currently_live_held_out_metrics JSONB,
    recommendation TEXT NOT NULL,
    promotion_status TEXT NOT NULL DEFAULT 'pending' CHECK (promotion_status IN ('pending', 'approved', 'rejected')),
    promotion_decided_at TIMESTAMPTZ,
    promotion_decided_by TEXT,
    promotion_notes TEXT,
    serialized_model_blob BYTEA
);

CREATE INDEX IF NOT EXISTS idx_retrain_runs_status ON retrain_runs(model_name, promotion_status);
"""


def _connect():
    url = os.environ.get("AIEM_DATABASE_URL")
    if not url:
        raise RuntimeError("AIEM_DATABASE_URL is not set.")
    return psycopg2.connect(url)


def init_schema():
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(DDL)
        conn.commit()


def run_scheduled_retrain(
    model_name: str,
    fetch_new_data_fn: Callable[[], Any],
    train_fn: Callable[[Any], Dict[str, Any]],
    evaluate_fn: Callable[[Any, Any], Dict[str, Any]],
    get_current_live_metrics_fn: Callable[[], Optional[Dict[str, Any]]],
    serialize_model_fn: Callable[[Any], bytes],
    min_improvement_threshold_pct: float = 5.0,
) -> Dict[str, Any]:
    """Automated entry point — call from a scheduled job (weekly cron, etc.)
    and the mechanical steps run with zero manual intervention:

      fetch_new_data_fn()           -> pulls accumulated labeled data
      train_fn(data)                -> {"model": ..., other training info}
      evaluate_fn(model, data)      -> held-out metrics dict (must include
                                        a numeric 'accuracy' or 'overall_accuracy')
      get_current_live_metrics_fn() -> live version's last known held-out
                                        metrics, or None if nothing is live yet
      serialize_model_fn(model)     -> bytes, for storage in the promotion queue

    Produces a recommendation ('promote' / 'hold' / 'investigate') based on
    comparing new vs. live performance — but NEVER applies it. Always ends
    in the promotion queue, status='pending'.
    """
    new_data     = fetch_new_data_fn()
    train_result = train_fn(new_data)
    new_model    = train_result.get("model")
    n_examples   = train_result.get("n_training_examples", 0)

    new_metrics  = evaluate_fn(new_model, new_data)
    live_metrics = get_current_live_metrics_fn()

    new_accuracy  = new_metrics.get("accuracy", new_metrics.get("overall_accuracy"))
    live_accuracy = (
        live_metrics.get("accuracy", live_metrics.get("overall_accuracy"))
        if live_metrics else None
    )

    if live_accuracy is None:
        recommendation = "promote"
        reasoning = "No currently-live version to compare against — this would be the first deployed version."
    else:
        improvement_pct = (new_accuracy - live_accuracy) / live_accuracy * 100 if live_accuracy > 0 else 0
        if improvement_pct >= min_improvement_threshold_pct:
            recommendation = "promote"
            reasoning = f"New version improves held-out accuracy by {improvement_pct:.1f}% over current live version."
        elif improvement_pct <= -min_improvement_threshold_pct:
            recommendation = "investigate"
            reasoning = (
                f"New version is {abs(improvement_pct):.1f}% WORSE than current live version — "
                "investigate why before doing anything, including before re-running with more data."
            )
        else:
            recommendation = "hold"
            reasoning = (
                f"New version performance ({improvement_pct:+.1f}%) is within noise of current "
                "live version — not enough improvement to justify a change."
            )

    blob = serialize_model_fn(new_model) if new_model is not None else None

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO retrain_runs
                    (model_name, n_new_training_examples, new_version_held_out_metrics,
                     currently_live_held_out_metrics, recommendation, serialized_model_blob)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    model_name, n_examples, json.dumps(new_metrics),
                    json.dumps(live_metrics) if live_metrics else None,
                    recommendation, blob,
                ),
            )
            run_id = cur.fetchone()[0]
        conn.commit()

    return {
        "run_id":                  run_id,
        "model_name":              model_name,
        "n_new_training_examples": n_examples,
        "new_metrics":             new_metrics,
        "live_metrics":            live_metrics,
        "recommendation":          recommendation,
        "reasoning":               reasoning,
        "promotion_status":        "pending",
        "next_step": (
            f"Call get_pending_promotions() to review, then "
            f"approve_promotion({run_id}, ...) or reject_promotion({run_id}, ...)."
        ),
    }


def get_pending_promotions(model_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """The queue you actually check periodically — this is the ONE thing
    that still needs a human, by design. Everything mechanical already ran;
    this is just you reading a short report and saying yes or no."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if model_name:
                cur.execute(
                    "SELECT id, model_name, run_at, n_new_training_examples, "
                    "new_version_held_out_metrics, currently_live_held_out_metrics, "
                    "recommendation FROM retrain_runs "
                    "WHERE model_name = %s AND promotion_status = 'pending' "
                    "ORDER BY run_at DESC",
                    (model_name,),
                )
            else:
                cur.execute(
                    "SELECT id, model_name, run_at, n_new_training_examples, "
                    "new_version_held_out_metrics, currently_live_held_out_metrics, "
                    "recommendation FROM retrain_runs "
                    "WHERE promotion_status = 'pending' ORDER BY run_at DESC"
                )
            return [dict(r) for r in cur.fetchall()]


def approve_promotion(run_id: int, decided_by: str, notes: str = "") -> Dict[str, Any]:
    """The one deliberate action that actually changes what's live. Returns
    the serialized model blob so your application code can write it into
    whatever 'is_live' table the specific model type uses."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM retrain_runs WHERE id = %s", (run_id,))
            run = cur.fetchone()
            if not run:
                return {"error": f"No retrain run with id={run_id}"}
            if run["promotion_status"] != "pending":
                return {"error": f"Run {run_id} already has status '{run['promotion_status']}'"}
            cur.execute(
                """
                UPDATE retrain_runs
                SET promotion_status = 'approved', promotion_decided_at = now(),
                    promotion_decided_by = %s, promotion_notes = %s
                WHERE id = %s
                """,
                (decided_by, notes, run_id),
            )
        conn.commit()

    return {
        "run_id":                 run_id,
        "status":                 "approved",
        "model_name":             run["model_name"],
        "serialized_model_blob":  run["serialized_model_blob"],
        "next_step":              "Write this blob into the relevant module's is_live model storage.",
    }


def reject_promotion(run_id: int, decided_by: str, notes: str = "") -> Dict[str, Any]:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE retrain_runs
                SET promotion_status = 'rejected', promotion_decided_at = now(),
                    promotion_decided_by = %s, promotion_notes = %s
                WHERE id = %s
                """,
                (decided_by, notes, run_id),
            )
        conn.commit()
    return {"run_id": run_id, "status": "rejected"}


def get_retrain_history(model_name: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Full history of retrain attempts — including ones you rejected or
    that recommended 'investigate.' If a model keeps recommending
    'investigate' every retrain cycle, that is a sign the underlying
    signal may not be stable, regardless of how good any single retrain's
    numbers look."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, model_name, run_at, n_new_training_examples, recommendation, "
                "promotion_status, promotion_decided_at FROM retrain_runs "
                "WHERE model_name = %s ORDER BY run_at DESC LIMIT %s",
                (model_name, limit),
            )
            return [dict(r) for r in cur.fetchall()]


if __name__ == "__main__":
    init_schema()
    print("automated_retrain_pipeline schema ready.")
    print("Schedule run_scheduled_retrain() weekly/monthly.")
    print("Check get_pending_promotions() periodically.")
    print("The mechanical work is automatic. The one decision that matters stays yours.")
