"""
prediction_logger.py

Logs every ML model prediction at signal time and resolves it when the
pick settles. This is what feeds both the retraining dataset and the
audit dashboard.

Table: aiem_ml_predictions
  id, ticker, trade_date, predicted_prob, features_json,
  outcome (NULL until resolved), return_pct (NULL until resolved),
  model_version, created_at, resolved_at
"""

import json
import os
import psycopg2
from datetime import datetime, timezone

_DB_URL = os.environ.get("DATABASE_URL", "")

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS aiem_ml_predictions (
    id              SERIAL PRIMARY KEY,
    ticker          TEXT NOT NULL,
    trade_date      DATE NOT NULL,
    predicted_prob  FLOAT,
    features_json   JSONB,
    outcome         INTEGER,
    return_pct      FLOAT,
    model_version   TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ,
    UNIQUE (ticker, trade_date)
);
"""


def _init_table():
    if not _DB_URL:
        return
    try:
        with psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute(_INIT_SQL)
            conn.commit()
    except Exception as e:
        print(f"[prediction_logger] init error: {e}")


_init_table()


def log_prediction(
    ticker: str,
    trade_date,
    predicted_prob: float,
    features: dict = None,
    model_version: str = "unknown",
):
    """
    Call this when a pick is generated. Records the model's predicted
    probability so we can later compare it against the actual outcome.
    """
    if not _DB_URL:
        return
    try:
        features_json = json.dumps({
            k: (float(v) if v is not None and str(v) != "nan" else None)
            for k, v in (features or {}).items()
        })
        with psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO aiem_ml_predictions
                    (ticker, trade_date, predicted_prob, features_json, model_version)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (ticker, trade_date) DO UPDATE SET
                    predicted_prob = EXCLUDED.predicted_prob,
                    features_json  = EXCLUDED.features_json,
                    model_version  = EXCLUDED.model_version
            """, (str(ticker), str(trade_date), predicted_prob, features_json, model_version))
            conn.commit()
    except Exception as e:
        print(f"[prediction_logger] log_prediction error ({ticker} {trade_date}): {e}")


def resolve_prediction(ticker: str, trade_date, outcome: int, return_pct: float = None):
    """
    Call this when a pick settles. outcome=1 means WIN, outcome=0 means LOSS.
    return_pct is the actual percentage return (positive or negative).
    """
    if not _DB_URL:
        return
    try:
        with psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("""
                UPDATE aiem_ml_predictions
                SET outcome     = %s,
                    return_pct  = %s,
                    resolved_at = NOW()
                WHERE ticker     = %s
                  AND trade_date = %s
                  AND outcome IS NULL
            """, (int(outcome), return_pct, str(ticker), str(trade_date)))
            conn.commit()
    except Exception as e:
        print(f"[prediction_logger] resolve_prediction error ({ticker} {trade_date}): {e}")


def get_audit_stats() -> dict:
    """
    Returns rolling accuracy stats for the audit dashboard.
    """
    if not _DB_URL:
        return {}
    try:
        with psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT
                    COUNT(*) AS total_logged,
                    SUM(CASE WHEN outcome IS NOT NULL THEN 1 ELSE 0 END) AS resolved,
                    ROUND(AVG(CASE WHEN outcome IS NOT NULL THEN outcome END)::numeric, 3) AS win_rate,
                    ROUND(AVG(CASE WHEN predicted_prob IS NOT NULL AND outcome IS NOT NULL
                                   THEN predicted_prob END)::numeric, 3) AS avg_confidence,
                    ROUND(AVG(CASE WHEN return_pct IS NOT NULL THEN return_pct END)::numeric, 2) AS avg_return_pct
                FROM aiem_ml_predictions
            """)
            row = cur.fetchone()
            if row:
                return {
                    "total_logged":   row[0],
                    "resolved":       row[1],
                    "win_rate":       row[2],
                    "avg_confidence": row[3],
                    "avg_return_pct": row[4],
                }
    except Exception as e:
        print(f"[prediction_logger] get_audit_stats error: {e}")
    return {}
