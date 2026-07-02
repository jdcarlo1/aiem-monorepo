"""
reports.py - shadow-mode prediction logging for the AIEM Probability Engine.

Owns exactly ONE table: aiem_probability_engine_predictions. This table is
created/read/written ONLY by this file - no other module in this codebase
(and per the isolation contract, nothing in main.py or the live scheduler)
touches it. It is a permanent, append-mostly log of what this package
predicted, so predictions can be checked against real outcomes later
without ever feeding a live trade.

Idempotency: UNIQUE (signal_date, ticker, model_version). Re-running
generate_and_log_predictions() for a date/ticker/model_version already
logged is a safe no-op (ON CONFLICT DO NOTHING) - the first prediction of
the day is what gets graded, matching "log predictions vs later outcomes
DAILY" from the spec.

CREATE TABLE IF NOT EXISTS is used here deliberately: this table is not
part of the main app's ORM-managed schema (there is none for this raw-SQL
Python package), so it is not subject to the Drizzle publish-diff flow.
This mirrors the existing convention elsewhere in this codebase (see
quant_agent_sessions' reconcile_orphaned_sessions()), where package-owned,
non-ORM tables self-create on first use.

PIT FIX (2026-07-02): generate_and_log_predictions() used to score EVERY
unlogged row - backlog included - with whatever model_horizon_{h}d.pkl
happened to be on disk at call time, no matter how old that row's
signal_date was. Since train.py always trains on the full dataset
available at run time, a backfilled row's signal_date could (and did, for
213/225 existing rows) predate data the model was actually trained on -
the model had, in effect, already seen the future relative to that row.

This is now fixed structurally: predictions are grouped by signal_date and
each group is scored ONLY with models that model_registry.get_as_of()
confirms could not have absorbed outcome information from that date or
later (see model_registry.py + predict.load_models_as_of()). A date with
no PIT-eligible model is skipped and counted, never silently scored with
"whatever's currently on disk." Every row logged going forward carries an
honest pit_status ('pit_safe'), and every row logged BEFORE this fix is
labeled 'leaked' by the one-time migration in ensure_table() below - see
pit_correction.py for the separate, disclosed re-scoring of those 213 rows
and pit_metrics.py for the honest before/after accuracy comparison.

Run directly for a demo (creates the table if missing, logs today's new
predictions, and prints them back):
    python reports.py
Run with --backfill to fill in outcomes for past predictions whose horizon
has now elapsed:
    python reports.py --backfill
"""
import json
import os
import sys

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DB_URL, HORIZONS

TABLE = "aiem_probability_engine_predictions"

_CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    signal_date DATE NOT NULL,
    ticker TEXT NOT NULL,
    model_version TEXT NOT NULL,
    probability_source_json JSONB,
    prob_up_1d DOUBLE PRECISION,
    prob_up_2d DOUBLE PRECISION,
    prob_up_3d DOUBLE PRECISION,
    prob_up_4d DOUBLE PRECISION,
    confidence DOUBLE PRECISION,
    regime_tag TEXT,
    edge_after_cost_prob_pts DOUBLE PRECISION,
    top_contributing_layers_json JSONB,
    overlays_json JSONB,
    warnings_json JSONB,
    feature_snapshot_json JSONB,
    outcome_ret_1d DOUBLE PRECISION,
    outcome_ret_2d DOUBLE PRECISION,
    outcome_ret_3d DOUBLE PRECISION,
    outcome_ret_4d DOUBLE PRECISION,
    outcome_label_1d SMALLINT,
    outcome_label_2d SMALLINT,
    outcome_label_3d SMALLINT,
    outcome_label_4d SMALLINT,
    outcome_last_checked_at TIMESTAMPTZ,
    UNIQUE (signal_date, ticker, model_version)
)
"""


def ensure_table() -> None:
    with psycopg2.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(_CREATE_SQL)
            # Migration for tables created before regime_tag / edge_after_cost_prob_pts
            # existed as their own columns (they used to only live inside
            # overlays_json / be discarded entirely). Additive + idempotent.
            cur.execute(f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS regime_tag TEXT")
            cur.execute(f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS edge_after_cost_prob_pts DOUBLE PRECISION")
            # Backfill regime_tag for already-logged rows from overlays_json - it was
            # always computed, just not persisted as its own column until now.
            # edge_after_cost_prob_pts has no such backfill source: it was never
            # written anywhere in the old rows, so old rows stay NULL there (honest
            # gap) and only newly-logged rows going forward will have it populated.
            cur.execute(f"""
                UPDATE {TABLE}
                SET regime_tag = overlays_json -> 'regime' ->> 'regime_tag'
                WHERE regime_tag IS NULL AND overlays_json IS NOT NULL
            """)
            # PIT FIX migration: label every existing row honestly. created_at
            # is when the row was actually scored+logged (this table has no
            # other timestamp).
            #
            # CORRECTED 2026-07-03 (see .agents/memory/probability-engine-shadow-log-leakage.md):
            # this used to mark a row 'pit_safe' whenever created_at::date <=
            # signal_date ("logged same-day"). That heuristic is WRONG and was
            # proven wrong on real data: 12 rows scored via the OLD unversioned
            # load_models() path (same run, same model_version, same DB
            # transaction timestamp down to the microsecond as 184 rows
            # correctly labeled 'leaked' moments earlier) happened to have
            # signal_date == created_at::date and were incorrectly stamped
            # 'pit_safe' even though they were produced by the exact same
            # pre-fix, non-registry-versioned scoring pass. "Logged same-day"
            # does not imply "scored by a registry-eligible, date-gated
            # model" - only model_registry.get_as_of() can prove that, and it
            # cannot be evaluated retroactively for old rows (registry.json is
            # overwritten in place, not append-only, so the registry state at
            # the time an old row was logged is unrecoverable).
            #
            # Correct, conservative rule: ANY pre-existing row with no
            # pit_status yet was, by definition, logged before this fix
            # existed, and therefore was NEVER scored via load_models_as_of()/
            # get_as_of() - it cannot be proven safe, so it is marked 'leaked'
            # unconditionally. Only rows inserted AFTER the fix, explicitly
            # stamped at insert time by log_predictions() (see
            # generate_and_log_predictions(), which only calls it when
            # load_models_as_of() returned a real registry-gated model), are
            # ever allowed to be 'pit_safe'.
            cur.execute(f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS pit_status TEXT")
            cur.execute(f"""
                UPDATE {TABLE}
                SET pit_status = 'leaked'
                WHERE pit_status IS NULL
            """)
        conn.commit()


def get_logged_keys(model_version: str = None) -> set:
    """
    {(signal_date_iso, ticker)} already logged for model_version (or across
    ALL model_versions if model_version is None) - used to skip re-scoring
    rows that were already predicted (overlay computation is ~2-3s/row, so
    this matters for batch cost, not just log cleanliness).
    """
    ensure_table()
    sql = f"SELECT DISTINCT signal_date, ticker FROM {TABLE}"
    params = None
    if model_version is not None:
        sql += " WHERE model_version = %s"
        params = (model_version,)
    with psycopg2.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return {(row[0].isoformat(), row[1]) for row in cur.fetchall()}


def filter_unlogged(std_df: pd.DataFrame, model_version: str) -> pd.DataFrame:
    if std_df.empty:
        return std_df
    logged = get_logged_keys(model_version)
    mask = std_df.apply(
        lambda r: (pd.Timestamp(r["trade_date"]).date().isoformat(), r["ticker"]) not in logged,
        axis=1,
    )
    return std_df[mask]


def _json_default(o):
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, (np.bool_,)):
        return bool(o)
    return str(o)


def log_predictions(reports: list, model_version: str, pit_status: str = "pit_safe") -> int:
    """
    Upserts each ProbabilityReport into the shadow log. Returns the number
    of NEW rows actually inserted (ON CONFLICT DO NOTHING means re-running
    on an already-logged date/ticker/model_version is a safe no-op, so this
    can be called from a daily cron-equivalent without double-logging).

    pit_status is stamped explicitly by the caller, never inferred here -
    generate_and_log_predictions() always passes 'pit_safe' (it only ever
    scores a date with a model_registry-confirmed-eligible model). Nothing
    in this file should ever pass 'leaked' - that value only exists on rows
    written before this fix, via ensure_table()'s one-time migration.
    """
    if not reports:
        return 0
    ensure_table()

    insert_sql = f"""
        INSERT INTO {TABLE} (
            signal_date, ticker, model_version, probability_source_json,
            prob_up_1d, prob_up_2d, prob_up_3d, prob_up_4d, confidence,
            regime_tag, edge_after_cost_prob_pts,
            top_contributing_layers_json, overlays_json, warnings_json,
            feature_snapshot_json, pit_status
        ) VALUES (
            %(signal_date)s, %(ticker)s, %(model_version)s, %(probability_source_json)s,
            %(prob_up_1d)s, %(prob_up_2d)s, %(prob_up_3d)s, %(prob_up_4d)s, %(confidence)s,
            %(regime_tag)s, %(edge_after_cost_prob_pts)s,
            %(top_contributing_layers_json)s, %(overlays_json)s, %(warnings_json)s,
            %(feature_snapshot_json)s, %(pit_status)s
        )
        ON CONFLICT (signal_date, ticker, model_version) DO NOTHING
    """

    rows = []
    for r in reports:
        d = r.to_dict()
        rows.append({
            "signal_date": d["signal_date"],
            "ticker": d["ticker"],
            "model_version": model_version,
            "probability_source_json": json.dumps(getattr(r, "_probability_sources", {}), default=_json_default),
            "prob_up_1d": d["prob_up_1d"],
            "prob_up_2d": d["prob_up_2d"],
            "prob_up_3d": d["prob_up_3d"],
            "prob_up_4d": d["prob_up_4d"],
            "confidence": d["confidence"],
            "regime_tag": d.get("regime_tag"),
            "edge_after_cost_prob_pts": d.get("edge_after_cost_prob_pts"),
            "top_contributing_layers_json": json.dumps(d["top_contributing_layers"]),
            "overlays_json": json.dumps(getattr(r, "_overlays", {}), default=_json_default),
            "warnings_json": json.dumps(d["warnings"]),
            "feature_snapshot_json": json.dumps(d.get("_horizon_detail", {}), default=_json_default),
            "pit_status": pit_status,
        })

    with psycopg2.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM {TABLE}")
            before_count = cur.fetchone()[0]
            psycopg2.extras.execute_batch(cur, insert_sql, rows)
            cur.execute(f"SELECT count(*) FROM {TABLE}")
            after_count = cur.fetchone()[0]
        conn.commit()

    # before/after total row count (not len(rows)) because ON CONFLICT
    # silently collapses duplicate (signal_date, ticker) keys WITHIN this
    # same batch (e.g. two separate picks for the same ticker on the same
    # day) - the shadow log's grain is one row per ticker per day, so that
    # collapse is correct behavior, but len(rows) would overcount it.
    return after_count - before_count


def generate_and_log_predictions(only_new: bool = True, limit: int = None,
                                  commit_every: int = 5) -> list:
    """
    Top-level daily entry point: build features for all known picks,
    optionally skip already-logged ones, predict, and log. Returns the
    list of ProbabilityReport objects actually scored this run.

    PIT FIX: rows are grouped by signal_date and each date-group is scored
    ONLY with model_registry-confirmed-eligible models for that date (see
    predict.load_models_as_of() + module docstring above). A date with no
    eligible model for any horizon is skipped entirely and counted in
    `total_skipped_no_model` - never scored with "whatever's on disk," which
    is exactly the bug that produced 213 leaked rows before this fix. This
    means a from-scratch backlog is now WORKED THROUGH SLOWER than before
    (skipped dates simply never get scored until a model with an early
    enough cutoff_date exists) - that is the correct, disclosed tradeoff:
    a smaller PIT-safe log beats a larger contaminated one.

    `limit` caps how many rows are scored in this call (across all
    date-groups combined), oldest signal_date first - overlay computation
    (layer9 in particular) costs ~2-3s/row, so a large backlog must be
    worked through in bounded batches, not one synchronous call.

    `commit_every` controls how often predictions are logged DURING this
    run (every N scored rows within a date-group), not just once at the
    end, so a killed/timed-out run is interruption-safe: already-committed
    sub-batches survive and a subsequent only_new=True call resumes from
    exactly where it left off (idempotent via ON CONFLICT DO NOTHING keyed
    on signal_date+ticker+model_version).
    """
    from data_snapshot import build_dataset
    from features import add_standardized_features
    from predict import generate_predictions, load_models_as_of
    from model_registry import version_string_for_entries

    raw = build_dataset()
    if raw.empty:
        print("[reports] no dataset available")
        return []
    std_df = add_standardized_features(raw)
    std_df = std_df.sort_values("trade_date")

    all_reports, total_inserted, total_skipped_no_model, scored_count = [], 0, 0, 0
    unique_dates = sorted(pd.Timestamp(d).date() for d in std_df["trade_date"].unique())

    for as_of in unique_dates:
        if limit is not None and scored_count >= limit:
            break

        date_df = std_df[std_df["trade_date"] == pd.Timestamp(as_of)]

        models, entries = load_models_as_of(as_of)
        if not models:
            total_skipped_no_model += len(date_df)
            print(f"[reports] SKIPPED {as_of}: no PIT-eligible model yet "
                  f"(model_registry.get_as_of found nothing with "
                  f"label_settled_through < {as_of} for any horizon) - "
                  f"scoring this date now would leak, n={len(date_df)} rows not scored")
            continue

        model_version = version_string_for_entries(entries)

        if only_new:
            date_df = filter_unlogged(date_df, model_version)
        if date_df.empty:
            continue

        if limit is not None:
            remaining_budget = limit - scored_count
            if remaining_budget <= 0:
                break
            date_df = date_df.head(remaining_budget)

        rows = list(date_df.iterrows())
        for start in range(0, len(rows), commit_every):
            chunk_idx = [idx for idx, _ in rows[start:start + commit_every]]
            chunk_df = date_df.loc[chunk_idx]
            chunk_reports = generate_predictions(chunk_df, models=models)
            n_inserted = log_predictions(chunk_reports, model_version, pit_status="pit_safe")
            total_inserted += n_inserted
            all_reports.extend(chunk_reports)
            scored_count += len(chunk_reports)

        print(f"[reports] {as_of}: scored {len(rows)} rows with model_version="
              f"{model_version} (horizons={sorted(entries.keys())})", flush=True)

    print(f"[reports] DONE: scored {len(all_reports)} rows across "
          f"{len(unique_dates)} distinct signal_dates, logged {total_inserted} new "
          f"rows, ALL pit_status='pit_safe'; {total_skipped_no_model} rows skipped "
          f"(no PIT-eligible model for their date yet)")
    return all_reports


def backfill_outcomes(batch_limit: int = 500) -> int:
    """
    Fills outcome_ret_Nd / outcome_label_Nd for logged predictions whose
    horizon has now elapsed and whose outcome is still NULL, using the same
    "exact forward trading-day close" logic as data_snapshot._forward_labels
    (computed fresh here, post-hoc, from polygon_market_daily). Never
    touches probability/confidence columns - shadow log entries are
    immutable once logged, only their outcome columns get backfilled.
    Returns the number of rows updated.
    """
    ensure_table()
    with psycopg2.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT id, signal_date, ticker FROM {TABLE}
                WHERE outcome_label_4d IS NULL
                ORDER BY signal_date ASC
                LIMIT %s
            """, (batch_limit,))
            pending = cur.fetchall()

        if not pending:
            print("[reports] no pending outcome backfills")
            return 0

        tickers = sorted({t for _, _, t in pending})
        with conn.cursor() as cur:
            cur.execute("""
                SELECT ticker, scan_date, close_price FROM polygon_market_daily
                WHERE ticker = ANY(%s) ORDER BY ticker ASC, scan_date ASC
            """, (tickers,))
            hist_rows = cur.fetchall()

        hist_by_ticker = {}
        for ticker, scan_date, close_price in hist_rows:
            hist_by_ticker.setdefault(ticker, []).append((scan_date, close_price))

        updated = 0
        with conn.cursor() as cur:
            for pred_id, signal_date, ticker in pending:
                hist = hist_by_ticker.get(ticker, [])
                dates = [h[0] for h in hist]
                if signal_date not in dates:
                    continue
                pos = dates.index(signal_date)
                base_price = hist[pos][1]
                if base_price in (None, 0):
                    continue

                update_fields, params = [], []
                any_new = False
                for h in HORIZONS:
                    fut_pos = pos + h
                    if fut_pos >= len(hist):
                        continue
                    fut_price = hist[fut_pos][1]
                    if fut_price is None:
                        continue
                    ret_pct = (fut_price - base_price) / base_price * 100.0
                    label = 1 if fut_price > base_price else 0
                    update_fields += [f"outcome_ret_{h}d = %s", f"outcome_label_{h}d = %s"]
                    params += [ret_pct, label]
                    any_new = True

                if not any_new:
                    continue
                update_fields.append("outcome_last_checked_at = now()")
                cur.execute(
                    f"UPDATE {TABLE} SET {', '.join(update_fields)} WHERE id = %s",
                    params + [pred_id],
                )
                updated += 1
        conn.commit()

    print(f"[reports] backfilled outcomes for {updated}/{len(pending)} pending rows")
    return updated


if __name__ == "__main__":
    if "--backfill" in sys.argv:
        backfill_outcomes()
    else:
        limit = None
        if "--limit" in sys.argv:
            limit = int(sys.argv[sys.argv.index("--limit") + 1])
        generate_and_log_predictions(only_new=True, limit=limit)
