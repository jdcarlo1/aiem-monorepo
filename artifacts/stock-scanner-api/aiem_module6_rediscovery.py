"""
aiem_module6_rediscovery.py — Module 6: Rediscovery Engine (Retirement Feedback Loop)

When a signal is retired via Module 4, this module tests a small, pre-registered
set of statistical neighbors of the retired condition using the same Fisher + BH-FDR
harness as Module 5. The expected output is "nothing found."

Design principles:
  - Fixed variation set (max 6 per retired signal) — no open-ended search
  - Batch-level BH-FDR (all variations in one combined test batch)
  - Effect size floor: delta >= +2.5pp AND p < 0.05 post-correction
  - Lineage: every descendant carries parent_signal_id + generation
  - Generation cap: only gen-0 (original) signals spawn children; gen-1 is the last
"""

import logging
import time
from datetime import datetime, timezone
from typing import Optional

import psycopg2
import psycopg2.extras
import aiem_stat_tests as _stat_tests

log = logging.getLogger("module6_rediscovery")

# ---------------------------------------------------------------------------
# Configuration

_SCAN_START           = "2024-07-08"   # earliest polygon_market_daily date
_FDR_ALPHA            = 0.05
_MIN_DELTA            = 2.5            # pp — minimum effect size (post-BH)
_MIN_WR               = 52.0           # % — minimum win rate
_MIN_N                = 50             # minimum condition-group rows
_MAX_PARENT_GENERATION = 0             # only gen-0 originals can spawn gen-1 children

_DEFAULT_HORIZONS = [1, 3, 5]

_HORIZON_MAP = {
    "next_day": 1, "1d": 1, "3d": 3, "5d": 5, "20d": 20,
}

# ---------------------------------------------------------------------------
# Testable condition key catalog
# Each entry maps a conditions_json key to a SQL column expression, comparison
# operator, discrete step size, and number of steps to try in each direction.

_CHANGE_PCT = "(pm.close_price - pm.prev_close) / NULLIF(pm.prev_close, 0) * 100"

_TESTABLE = {
    "rvol_min":            {"col": "pm.rvol",           "op": ">=", "step": 0.5,  "n_steps": 2},
    "vol_ratio_min":       {"col": "pm.rvol",           "op": ">=", "step": 0.1,  "n_steps": 2},
    "close_strength_min":  {"col": "pm.close_strength", "op": ">=", "step": 0.05, "n_steps": 2},
    "close_strength_max":  {"col": "pm.close_strength", "op": "<=", "step": 0.05, "n_steps": 2},
    "price_range_max_pct": {"col": "pm.range_pct",      "op": "<=", "step": 1.0,  "n_steps": 2},
    "range_pct_max":       {"col": "pm.range_pct",      "op": "<=", "step": 1.0,  "n_steps": 2},
    "price_range_min_pct": {"col": "pm.range_pct",      "op": ">=", "step": 1.0,  "n_steps": 2},
    "gap_pct_min":         {"col": "pm.gap_pct",        "op": ">=", "step": 0.5,  "n_steps": 2},
    "gap_pct_max":         {"col": "pm.gap_pct",        "op": "<=", "step": 0.5,  "n_steps": 2},
    "change_pct_min":      {"col": _CHANGE_PCT,         "op": ">=", "step": 1.0,  "n_steps": 2},
}

# ---------------------------------------------------------------------------
# Schema

_SCHEMA_SQL = """
ALTER TABLE aiem_signal_discoveries
    ADD COLUMN IF NOT EXISTS parent_signal_id  INTEGER REFERENCES aiem_signal_discoveries(id),
    ADD COLUMN IF NOT EXISTS generation        INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS variation_note    TEXT;

CREATE TABLE IF NOT EXISTS aiem_rediscovery_runs (
    run_id            BIGSERIAL PRIMARY KEY,
    batch_id          TEXT             NOT NULL,
    parent_signal_id  INTEGER          NOT NULL REFERENCES aiem_signal_discoveries(id),
    variations_tested INTEGER          NOT NULL DEFAULT 0,
    variations_passed INTEGER          NOT NULL DEFAULT 0,
    non_testable      BOOLEAN          NOT NULL DEFAULT FALSE,
    run_timestamp     TIMESTAMPTZ      NOT NULL DEFAULT NOW()
);
"""

def init_schema(conn):
    with conn.cursor() as cur:
        cur.execute(_SCHEMA_SQL)
    conn.commit()
    log.info("[module6] schema ready (parent_signal_id/generation/variation_note + aiem_rediscovery_runs)")


# ---------------------------------------------------------------------------
# BH-FDR — delegates to the canonical aiem_stat_tests.bh_fdr_reject (Diagram 2
# remediation spec P1-1 / C2: one authoritative implementation, shared with
# Module 5). Module 6 already imports aiem_stat_tests for run_fisher_test, so
# this adds zero new cross-module coupling beyond what already exists. See
# tests/test_bh_fdr_equivalence.py for proof this is a pure behavior-preserving
# refactor of the previous local implementation.

def _bh_fdr_reject(p_values: list, alpha: float = _FDR_ALPHA) -> list:
    return _stat_tests.bh_fdr_reject(p_values, alpha)


# ---------------------------------------------------------------------------
# Variation generator

def _generate_variations(signal: dict) -> list:
    """
    Return at most 6 pre-registered statistical neighbors for a retired signal.
    Returns an empty list if the signal's conditions are not testable on
    polygon_market_daily (prior-day lookback, CMF, RSI, funnel signals, etc.).
    """
    cond = signal.get("conditions_json") or {}
    if not cond:
        return []

    matching = [
        (k, float(v))
        for k, v in cond.items()
        if k in _TESTABLE and isinstance(v, (int, float))
    ]
    if not matching:
        return []

    orig_horizon_str = signal.get("horizon", "1d")
    orig_horizon     = _HORIZON_MAP.get(orig_horizon_str, 1)

    def _build_filter(override_key=None, override_val=None):
        parts = []
        for k, v in matching:
            cfg = _TESTABLE[k]
            val = override_val if k == override_key else v
            parts.append(f"{cfg['col']} {cfg['op']} {val}")
        return " AND ".join(parts)

    variations = []

    primary_key, primary_val = matching[0]
    cfg  = _TESTABLE[primary_key]
    step = cfg["step"]
    n_steps = cfg["n_steps"]

    deltas = [-(n_steps * step), -step, step, n_steps * step]
    for delta in deltas:
        new_val = round(primary_val + delta, 6)
        if new_val <= 0:
            continue
        variations.append({
            "name":            f"var_{primary_key}_{new_val}",
            "sql_filter":      _build_filter(override_key=primary_key, override_val=new_val),
            "conditions_json": {**cond, primary_key: new_val},
            "variation_note":  f"{primary_key}: {primary_val} → {new_val}",
            "horizon":         orig_horizon,
        })
        if len(variations) >= 4:
            break

    base_filter = _build_filter()
    for h in _DEFAULT_HORIZONS:
        if h == orig_horizon:
            continue
        variations.append({
            "name":            f"var_horizon_{h}d",
            "sql_filter":      base_filter,
            "conditions_json": dict(cond),
            "variation_note":  f"horizon: {orig_horizon_str} → {h}d",
            "horizon":         h,
        })
        if len(variations) >= 6:
            break

    return variations[:6]


# ---------------------------------------------------------------------------
# One-variation test (mirrors Module 5's _run_one_test)

def _run_variation_test(cur, var: dict, horizon: int) -> dict:
    """
    Non-overlapping (bucketed) Fisher's exact test for one variation.
    Delegates to aiem_stat_tests.run_fisher_test — same harness as Module 5.
    """
    return _stat_tests.run_fisher_test(
        cur,
        sql_filter  = var["sql_filter"],
        horizon     = horizon,
        scan_start  = _SCAN_START,
        alternative = "greater",
    )


# ---------------------------------------------------------------------------
# Dedup: don't insert a variation whose conditions_json key-set matches an
# already-live signal (validated or hypothesis).

def _live_key_sets(cur) -> list:
    cur.execute("""
        SELECT conditions_json FROM aiem_signal_discoveries
        WHERE status IN ('validated', 'hypothesis')
    """)
    return [
        frozenset((cj or {}).keys())
        for (cj,) in cur.fetchall()
        if cj
    ]


# ---------------------------------------------------------------------------
# Main entry point

def run_rediscovery_batch(conn, batch_id: Optional[str] = None) -> dict:
    """
    Test variations of all signals retired since the last Module 6 run.
    All variations are corrected as one combined BH-FDR batch.
    Expected output: "nothing found" most weeks.
    """
    t0 = time.time()
    if batch_id is None:
        batch_id = datetime.now(timezone.utc).strftime("m6_%Y%m%d_%H%M%S")

    with conn.cursor() as cur:
        cur.execute("SELECT MAX(run_timestamp) FROM aiem_rediscovery_runs")
        row = cur.fetchone()
        last_run_ts = row[0] if (row and row[0]) else datetime(2000, 1, 1, tzinfo=timezone.utc)

        cur.execute("""
            SELECT DISTINCT ON (sa.discovery_id)
                sd.id,
                sd.conditions_json,
                sd.horizon,
                COALESCE(sd.generation, 0),
                sd.hypothesis_text
            FROM aiem_signal_actions sa
            JOIN aiem_signal_discoveries sd ON sd.id = sa.discovery_id
            WHERE sa.action      = 'retire'
              AND sa.approved_at > %s
              AND sd.status      = 'retired'
              AND COALESCE(sd.generation, 0) <= %s
            ORDER BY sa.discovery_id, sa.approved_at DESC
        """, (last_run_ts, _MAX_PARENT_GENERATION))
        retired = cur.fetchall()

    if not retired:
        log.info("[module6] no new retirements since last run — nothing to test")
        return {
            "batch_id": batch_id,
            "retired_signals_scanned": 0,
            "non_testable_count": 0,
            "total_variations_tested": 0,
            "bh_fdr_threshold": 0.0,
            "discoveries_inserted": 0,
            "elapsed_seconds": round(time.time() - t0, 1),
            "summary": [],
        }

    log.info(f"[module6] {len(retired)} retired signal(s) in scope")

    all_vars       = []   # (parent_id, parent_gen, variation_dict)
    non_testable   = []   # parent_ids with non-testable conditions

    for sig_id, cond_json, horizon, generation, hyp_text in retired:
        signal = {
            "id": sig_id, "conditions_json": cond_json,
            "horizon": horizon, "generation": generation,
        }
        vars_ = _generate_variations(signal)
        if not vars_:
            non_testable.append(sig_id)
            log.info(f"[module6] id={sig_id} — non-testable, logging and skipping")
        else:
            log.info(f"[module6] id={sig_id} — {len(vars_)} variation(s) queued")
            for v in vars_:
                all_vars.append((sig_id, generation, v))

    with conn.cursor() as cur:
        for sid in non_testable:
            cur.execute("""
                INSERT INTO aiem_rediscovery_runs
                    (batch_id, parent_signal_id, variations_tested, variations_passed, non_testable)
                VALUES (%s, %s, 0, 0, TRUE)
            """, (batch_id, sid))
        conn.commit()

    if not all_vars:
        log.info("[module6] no testable variations — done")
        return {
            "batch_id": batch_id,
            "retired_signals_scanned": len(retired),
            "non_testable_count": len(non_testable),
            "total_variations_tested": 0,
            "bh_fdr_threshold": 0.0,
            "discoveries_inserted": 0,
            "elapsed_seconds": round(time.time() - t0, 1),
            "summary": [{"parent_id": s, "result": "non_testable"} for s in non_testable],
        }

    log.info(f"[module6] running {len(all_vars)} variation test(s) as one BH-FDR batch …")

    raw_results = []
    with conn.cursor() as cur:
        live_ks = _live_key_sets(cur)
        for parent_id, parent_gen, var in all_vars:
            try:
                res = _run_variation_test(cur, var, var["horizon"])
            except Exception as exc:
                log.warning(f"  {var['name']} — error: {exc}")
                res = {"cond_n": 0, "ctrl_n": 0, "cond_wr": None,
                       "ctrl_wr": None, "delta_wr": None, "p_raw": 1.0}
            raw_results.append(res)
            log.debug(
                f"  {var['name']} h={var['horizon']}d "
                f"n={res['cond_n']} wr={res['cond_wr']}% "
                f"delta={res['delta_wr']}pp p={res['p_raw']:.3e}"
            )

    p_values = [r["p_raw"] for r in raw_results]
    rejected = _bh_fdr_reject(p_values, alpha=_FDR_ALPHA)

    bh_threshold = 0.0
    sorted_pairs = sorted(zip(p_values, range(len(p_values))), key=lambda x: x[0])
    for rank, (pv, _) in enumerate(sorted_pairs, 1):
        if pv <= rank / len(p_values) * _FDR_ALPHA:
            bh_threshold = pv

    discoveries_inserted = 0
    per_parent: dict = {}

    for i, (parent_id, parent_gen, var) in enumerate(all_vars):
        ps = per_parent.setdefault(parent_id, {
            "tested": 0, "passed": 0, "gen": parent_gen, "discoveries": [],
        })
        ps["tested"] += 1

        res = raw_results[i]
        bh_ok    = rejected[i]
        delta_ok = (res["delta_wr"] or 0.0) >= _MIN_DELTA
        wr_ok    = (res["cond_wr"]  or 0.0) >= _MIN_WR
        n_ok     = (res["cond_n"]   or 0)   >= _MIN_N
        cj_keys  = frozenset(var["conditions_json"].keys())
        dup_ok   = cj_keys not in live_ks
        qualifies = bh_ok and delta_ok and wr_ok and n_ok and dup_ok

        if not qualifies:
            reasons = []
            if not bh_ok:    reasons.append(f"BH-FDR not rejected (p={res['p_raw']:.3e})")
            if not delta_ok: reasons.append(f"delta={res['delta_wr']}pp < {_MIN_DELTA}pp floor")
            if not wr_ok:    reasons.append(f"wr={res['cond_wr']}% < {_MIN_WR}% floor")
            if not n_ok:     reasons.append(f"n={res['cond_n']} < {_MIN_N} floor")
            if not dup_ok:   reasons.append("key-set matches existing live signal")
            log.debug(f"  {var['name']} — skip: {'; '.join(reasons)}")
            continue

        child_gen = parent_gen + 1
        child_hyp = (
            f"[Descendant of id={parent_id}, generation {child_gen}] "
            f"Variation: {var['variation_note']}. "
            f"Conditions refined from retired parent signal."
        )

        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO aiem_signal_discoveries
                    (hypothesis_text, conditions_json, horizon, status,
                     signal_win_rate, signal_n,
                     baseline_win_rate, baseline_n,
                     edge_broad, p_value, invented_indicator,
                     parent_signal_id, generation, variation_note,
                     discovered_at)
                VALUES
                    (%s, %s, %s, 'hypothesis',
                     %s, %s,
                     %s, %s,
                     %s, %s, 'module6_rediscovery',
                     %s, %s, %s,
                     NOW())
                RETURNING id
            """, (
                child_hyp,
                psycopg2.extras.Json(var["conditions_json"]),
                f"{var['horizon']}d",
                res["cond_wr"], res["cond_n"],
                res["ctrl_wr"], res["ctrl_n"],
                res["delta_wr"], res["p_raw"],
                parent_id, child_gen, var["variation_note"],
            ))
            new_id = cur.fetchone()[0]
            conn.commit()

        live_ks.append(cj_keys)
        discoveries_inserted += 1
        ps["passed"] += 1
        ps["discoveries"].append({
            "new_id": new_id, "variation_note": var["variation_note"],
            "cond_wr": res["cond_wr"], "delta_wr": res["delta_wr"],
            "p_raw": res["p_raw"], "generation": child_gen,
        })
        log.info(
            f"  ✅ NEW descendant id={new_id} (gen {child_gen}) from parent id={parent_id}"
            f" — {var['variation_note']}, WR={res['cond_wr']}%, delta={res['delta_wr']}pp"
        )

    with conn.cursor() as cur:
        for parent_id, stats in per_parent.items():
            cur.execute("""
                INSERT INTO aiem_rediscovery_runs
                    (batch_id, parent_signal_id, variations_tested, variations_passed, non_testable)
                VALUES (%s, %s, %s, %s, FALSE)
            """, (batch_id, parent_id, stats["tested"], stats["passed"]))
        conn.commit()

    summary = []
    for parent_id, stats in per_parent.items():
        if stats["discoveries"]:
            summary.append({
                "parent_id": parent_id, "variations_tested": stats["tested"],
                "passed": stats["passed"], "discoveries": stats["discoveries"],
            })
        else:
            summary.append({
                "parent_id": parent_id, "variations_tested": stats["tested"],
                "passed": 0, "result": "nothing_found",
            })
    for sid in non_testable:
        summary.append({"parent_id": sid, "result": "non_testable"})

    elapsed = round(time.time() - t0, 1)
    log.info(
        f"[module6] complete: {len(all_vars)} variations tested, "
        f"{discoveries_inserted} inserted ({elapsed}s)"
    )
    return {
        "batch_id":                batch_id,
        "retired_signals_scanned": len(retired),
        "non_testable_count":      len(non_testable),
        "total_variations_tested": len(all_vars),
        "bh_fdr_threshold":        bh_threshold,
        "discoveries_inserted":    discoveries_inserted,
        "elapsed_seconds":         elapsed,
        "summary":                 summary,
    }


# ---------------------------------------------------------------------------
# Status report (for GET /aiem/module6-status)

def get_module6_status(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                r.run_id, r.batch_id, r.parent_signal_id,
                r.variations_tested, r.variations_passed,
                r.non_testable, r.run_timestamp,
                sd.conditions_json, sd.status AS parent_status
            FROM aiem_rediscovery_runs r
            JOIN aiem_signal_discoveries sd ON sd.id = r.parent_signal_id
            ORDER BY r.run_timestamp DESC
            LIMIT 20
        """)
        rows = cur.fetchall()

        cur.execute("""
            SELECT id, status, conditions_json, horizon,
                   signal_win_rate, signal_n, edge_broad, p_value,
                   parent_signal_id, generation, variation_note, discovered_at
            FROM aiem_signal_discoveries
            WHERE parent_signal_id IS NOT NULL
            ORDER BY discovered_at DESC
        """)
        descendants = cur.fetchall()

    return {
        "recent_runs": [
            {
                "run_id":            r[0],
                "batch_id":          r[1],
                "parent_signal_id":  r[2],
                "variations_tested": r[3],
                "variations_passed": r[4],
                "non_testable":      r[5],
                "run_timestamp":     str(r[6]),
                "parent_cond":       r[7],
                "parent_status":     r[8],
            }
            for r in rows
        ],
        "descendant_hypotheses": [
            {
                "id":               d[0],
                "status":           d[1],
                "conditions_json":  d[2],
                "horizon":          d[3],
                "signal_win_rate":  d[4],
                "signal_n":         d[5],
                "edge_broad":       d[6],
                "p_value":          d[7],
                "parent_signal_id": d[8],
                "generation":       d[9],
                "variation_note":   d[10],
                "discovered_at":    str(d[11]),
            }
            for d in descendants
        ],
        "total_descendants": len(descendants),
    }
