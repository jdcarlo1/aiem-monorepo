"""
daily_picks.py - "top N stocks today" selector for the AIEM Probability
Engine.

Owns exactly ONE table: aiem_probability_engine_daily_picks. Per the
isolation contract, nothing in main.py or the live scheduler writes to
this table - only this file does, via run_daily_job() below.

IMPORTANT framing: this does NOT independently scan the market. It reads
today's candidates that the EXISTING scanners already surfaced into
ai_short_calls_log (the same source data_snapshot.py trains on), scores
each one through the trained probability models, and reports the top N by
calibrated probability + edge. It is a RE-RANKING layer on top of the
existing conviction-stack signals, not a new stock-discovery engine.

Ranking formula (documented here so it's auditable, not a black box):
    score = mean(prob_up_2d, prob_up_3d) + 0.10 * confidence
  - 2d/3d chosen as the "sweet spot" horizon: 1d is the noisiest single
    day, 4d drifts furthest from the as-of feature snapshot's freshness.
  - confidence (0-1, lowered by model disagreement per schemas.py) is a
    tie-breaker/nudge, not a gate - a low-confidence 90% prob_up should
    still outrank a high-confidence 55% prob_up, hence the small 0.10
    weight rather than a hard filter.
  - edge_after_cost_prob_pts is NOT in the primary score (it's a rough
    equity-spread proxy per context.py's own docstring, not real options
    slippage) but IS surfaced in the output for a human to sanity-check.

Run directly for a manual/demo run:
    python daily_picks.py
"""
import datetime
import json
import os
import sys

import psycopg2
import psycopg2.extras

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DB_URL

TABLE = "aiem_probability_engine_daily_picks"
SOURCE_TABLE = "aiem_probability_engine_predictions"
_ET = None  # set lazily below to avoid a hard pytz/zoneinfo dependency at import time

_CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    pick_date DATE NOT NULL,
    rank SMALLINT NOT NULL,
    ticker TEXT NOT NULL,
    model_version TEXT NOT NULL,
    score DOUBLE PRECISION,
    prob_up_1d DOUBLE PRECISION,
    prob_up_2d DOUBLE PRECISION,
    prob_up_3d DOUBLE PRECISION,
    prob_up_4d DOUBLE PRECISION,
    confidence DOUBLE PRECISION,
    edge_after_cost_prob_pts DOUBLE PRECISION,
    regime_tag TEXT,
    top_contributing_layers_json JSONB,
    warnings_json JSONB,
    UNIQUE (pick_date, ticker)
)
"""


def _et_today():
    global _ET
    if _ET is None:
        try:
            from zoneinfo import ZoneInfo
            _ET = ZoneInfo("America/New_York")
        except Exception:
            _ET = datetime.timezone.utc
    return datetime.datetime.now(_ET).date()


def ensure_table() -> None:
    with psycopg2.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(_CREATE_SQL)
        conn.commit()


def _fetch_todays_scored_rows(model_version: str, pick_date) -> list:
    sql = f"""
        SELECT ticker, prob_up_1d, prob_up_2d, prob_up_3d, prob_up_4d,
               confidence, regime_tag, edge_after_cost_prob_pts,
               top_contributing_layers_json, warnings_json
        FROM {SOURCE_TABLE}
        WHERE signal_date = %s AND model_version = %s
    """
    with psycopg2.connect(DB_URL) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (pick_date, model_version))
            return cur.fetchall()


def _score_and_rank(rows: list, n: int) -> list:
    ranked = []
    for r in rows:
        p2, p3 = r.get("prob_up_2d"), r.get("prob_up_3d")
        if p2 is None or p3 is None:
            continue
        conf = r.get("confidence") or 0.0
        score = ((p2 + p3) / 2.0) + 0.10 * conf
        r["_score"] = score
        r["_regime_tag"] = r.get("regime_tag")
        r["_edge"] = r.get("edge_after_cost_prob_pts")
        ranked.append(r)
    ranked.sort(key=lambda r: r["_score"], reverse=True)
    return ranked[:n]


def run_daily_job(n: int = 10, model_version: str = None) -> list:
    """
    Top-level entry point for the daily scheduler: scores any new picks
    (via reports.generate_and_log_predictions), then ranks today's scored
    rows and writes the top `n` to aiem_probability_engine_daily_picks.

    Idempotent per pick_date: re-running the same day replaces that day's
    rows (DELETE + INSERT), so a manual re-run or a retry after a crash
    can't create duplicate/stale ranks for the same date.
    """
    from predict import load_models_as_of
    from model_registry import version_string_for_entries
    from reports import generate_and_log_predictions, ensure_table as ensure_predictions_table

    ensure_predictions_table()
    ensure_table()

    pick_date = _et_today()

    generate_and_log_predictions(only_new=True, limit=200)

    if model_version is None:
        # PIT FIX: resolve the version the SAME way reports.py just did for
        # today's date-group (load_models_as_of + version_string_for_entries)
        # instead of the old load_models()+compute_model_version() path -
        # those two could silently diverge (e.g. one horizon's newest model
        # isn't PIT-eligible for "today" yet while another's is), which would
        # make this lookup find zero rows even though reports.py just logged
        # some. Re-deriving it here (a pure function of the registry state,
        # which scoring doesn't mutate) always matches exactly.
        _, entries = load_models_as_of(pick_date)
        if not entries:
            print(f"[daily_picks] no PIT-eligible trained models for {pick_date} yet "
                  f"- run train.py first (or wait for label_settled_through to pass)")
            return []
        model_version = version_string_for_entries(entries)
    rows = _fetch_todays_scored_rows(model_version, pick_date)
    if not rows:
        print(f"[daily_picks] no scored rows for {pick_date} under model_version={model_version} "
              f"- nothing to rank (today's candidates may not exist yet in ai_short_calls_log)")
        return []

    top = _score_and_rank(rows, n)
    if not top:
        print(f"[daily_picks] {len(rows)} scored rows for {pick_date} but none had both "
              f"prob_up_2d and prob_up_3d - nothing rankable")
        return []

    with psycopg2.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM {TABLE} WHERE pick_date = %s", (pick_date,))
            for i, r in enumerate(top, start=1):
                cur.execute(f"""
                    INSERT INTO {TABLE}
                        (pick_date, rank, ticker, model_version, score,
                         prob_up_1d, prob_up_2d, prob_up_3d, prob_up_4d,
                         confidence, edge_after_cost_prob_pts, regime_tag,
                         top_contributing_layers_json, warnings_json)
                    VALUES (%s,%s,%s,%s,%s, %s,%s,%s,%s, %s,%s,%s, %s,%s)
                """, (
                    pick_date, i, r["ticker"], model_version, r["_score"],
                    r.get("prob_up_1d"), r.get("prob_up_2d"), r.get("prob_up_3d"), r.get("prob_up_4d"),
                    r.get("confidence"), r.get("_edge"), r.get("_regime_tag"),
                    json.dumps(r.get("top_contributing_layers_json")),
                    json.dumps(r.get("warnings_json")),
                ))
        conn.commit()

    print(f"[daily_picks] wrote top {len(top)} picks for {pick_date} (model_version={model_version})")
    return top


if __name__ == "__main__":
    picks = run_daily_job()
    for r in picks:
        print(f"  #{r['_score']:.3f}  {r['ticker']:6s}  2d={r.get('prob_up_2d')}  3d={r.get('prob_up_3d')}  conf={r.get('confidence')}")
