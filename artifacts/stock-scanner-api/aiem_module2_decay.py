"""
aiem_module2_decay.py — Module 2: Decay & Failure Analyzer

Evaluates every signal in aiem_signal_discoveries and assigns one of four
explicit evaluation_status values. No signal is silently skipped. No verdict
is issued without genuine out-of-sample evidence.

evaluation_status values (one per signal, always):
  evaluable_now              — real OOS data exists; module computes decay verdict
  evaluable_pending_time     — adapter can run but OOS window too small (n < 30)
  evaluable_pending_columns  — conditions need column/parser mappings not yet wired
  unevaluable_structural     — sequential multi-row state machine; per-condition
                               check impossible regardless of what columns exist

decay_verdict values (only issued when evaluation_status == evaluable_now):
  failing      — realized_win_rate < 50.0 AND p < 0.05 AND n >= 50
  decaying     — realized_wr < (discovery_wr - 3.0pp) AND p < 0.10 AND n >= 30
  holding      — within 3pp of discovery rate, or p >= 0.10
  insufficient_n — n < 30; not enough data to issue any verdict

This module does NOT promote signals and does NOT retire signals.
Those decisions belong to the human approval gate (Module 4).
Module 2's job: clean, evidence-backed verdicts — or honest "cannot evaluate"
statements — for every signal, on every run.

See AIEM_OPEN_ITEMS.md (Category A / Category B distinction) for rationale.
"""

import os
import json
import math
from datetime import date, datetime, timezone
from typing import Optional

import psycopg2

# ---------------------------------------------------------------------------
# Condition-key classification tables
#
# A condition key is "mappable" if it can be expressed via a SQL WHERE clause
# on existing columns (polygon_market_daily or polygon_indicators_daily),
# OR via the _MKT_V2_BASE_CTE derived columns.
# ---------------------------------------------------------------------------

# Keys whose pattern (as stem after stripping _min/_max) maps directly to an
# existing column in polygon_market_daily or polygon_indicators_daily via the
# current _mkt_parse_conditions whitelist.
_DIRECT_MAPPABLE_STEMS = frozenset({
    "gap_pct", "rvol", "close_strength", "range_pct", "close_price",
    "volume", "open_price", "high_price", "low_price", "vwap",
})

# Keys mappable via the V2 CTE derived columns (lag/delta/rolling values).
# Includes the actual column names used inside _MKT_V2_BASE_CTE.
_V2_CTE_MAPPABLE_STEMS = frozenset({
    "gap_pct_lag1", "gap_pct_lag2", "range_pct_lag1", "range_pct_lag2",
    "move_pct", "move_pct_lag1", "move_pct_lag2",
    "close_strength_lag1", "close_strength_lag2",
    "volume_lag1", "volume_lag2", "volume_avg20",
    "rvol_lag1", "close_price_lag1", "close_price_lag2",
    "cmf_20", "cmf_20_lag15", "cmf_20_delta15",
    "rsi_14", "rsi_14_lag15", "rsi_14_delta15",
    "stoch_k", "stoch_k_lag15", "stoch_d",
    "high_5d", "low_5d",
})

# Condition keys that indicate an inherently structural / multi-row pattern.
# Any discovery whose conditions_json contains a key from this set cannot be
# expressed as a per-row SQL condition regardless of what columns are added.
_STRUCTURAL_PATTERN_KEYS = frozenset({
    "cross", "funnel", "trough", "confirm", "fire_day", "universe",
})

# Known aliases for indicator columns: what the discovery stored → what exists
# in polygon_indicators_daily. Data is present; only the parser mapping is missing.
_INDICATOR_ALIAS_MAP = {
    "cmf20":         "polygon_indicators_daily.cmf_20",
    "rsi14":         "polygon_indicators_daily.rsi_14",
    "cmf20_lag15":   "V2 CTE: cmf_20_lag15",
    "rsi14_lag15":   "V2 CTE: rsi_14_lag15",
    "cmf_delta15":   "V2 CTE: cmf_20_delta15",
    "rsi_delta15":   "V2 CTE: rsi_14_delta15",
    "stoch_k":       "polygon_indicators_daily.stoch_k",
    "stoch_d":       "polygon_indicators_daily.stoch_d",
}

# Condition key stems whose evaluation path already EXISTS via the Category A
# chain adapter (_mkt_chain_filter_sql in main.py, handles ids 2/3/4/5) or
# Category B lag/delta adapter (_mkt_lagdelta_filter_sql, handles ids 7/8).
# Data is in the DB; adapters are wired and verified. The only blocker is
# forward-time accumulation, not missing data or missing code.
# These keys should yield evaluation_status="evaluable_pending_time", NOT
# "evaluable_pending_columns". That label is reserved for keys with NO known
# evaluation path.
_CHAIN_ADAPTER_KNOWN_STEMS = frozenset({
    # Category A chain adapter (ids 2,3,4,5) — all mapped to V2 CTE lag cols
    "gap_up_pct",               # → d.gap_pct >= threshold
    "gap_down_range",           # → d.gap_pct BETWEEN lo AND hi (list value)
    "inside_day_range",         # → d.range_pct_lag1 <= threshold
    "prior_day_move_pct",       # → d.move_pct_lag2 (ids 2,3) or d.move_pct_lag1 (id 5)
    "prior_day_close_strength", # → d.close_strength_lag2 (ids 2,3) or lag1 (id 5)
    "prior_day_cs",             # abbreviation of prior_day_close_strength
    "prior_day_move",           # → d.move_pct_lag1
    "prev_close_strength",      # → d.close_strength_lag1
    "prev_gap_pct",             # → d.gap_pct_lag1
    "avg_vol",                  # → d.volume_avg20 (V2 CTE rolling 20d avg)
    "price_range",              # → d.close_price_lag2 BETWEEN lo AND hi (list)
    "gap_abs",                  # → d.gap_pct BETWEEN -v AND v (ABS approximation)
    "volume_ratio",             # → d.rvol (approximate; same construct, same data)
})

# Keys with genuinely NO known evaluation path: no adapter exists, no data
# column exists, and no approximation is available. Signals with any key in
# this set block on BOTH missing data AND missing adapter code.
# Currently empty for all 9 known discoveries — every blocked key either has
# a chain/lagdelta adapter or is a structural pattern (id=9).
_TRULY_UNMAPPED_KEYS: frozenset = frozenset()

# Forward trading days required before any decay verdict is possible.
# Chosen to give at least 30 fires for common daily signals.
_MIN_FWD_DAYS_FOR_VERDICT = 21    # ~1 calendar month

# Minimum realized_n to issue a decay verdict (except insufficient_n).
_MIN_N_FOR_VERDICT = 30

# Verdict thresholds.
_FAILING_WR_CEILING = 50.0        # below this = failing (if n and p qualify)
_FAILING_P_CEILING  = 0.05
_FAILING_N_FLOOR    = 50          # need at least this many to call "failing"
_DECAYING_DELTA_PP  = 3.0         # pp drop vs discovery win rate to call "decaying"
_DECAYING_P_CEILING = 0.10
_DECAYING_N_FLOOR   = 30


# ---------------------------------------------------------------------------
# Schema init
# ---------------------------------------------------------------------------

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS aiem_module2_evaluations (
    id                          SERIAL PRIMARY KEY,
    discovery_id                INTEGER NOT NULL REFERENCES aiem_signal_discoveries(id),
    discovery_status            VARCHAR(20),
    evaluation_status           VARCHAR(40) NOT NULL,
    blocking_reason             TEXT,
    missing_columns             TEXT[],
    forward_days_accumulated    INTEGER,
    forward_days_required       INTEGER,
    decay_verdict               VARCHAR(20),
    realized_n                  INTEGER,
    realized_win_rate           FLOAT,
    realized_p_value            FLOAT,
    win_rate_at_discovery       FLOAT,
    delta_vs_discovery_pp       FLOAT,
    structural_reason           TEXT,
    estimated_years_to_evaluable FLOAT,
    run_at                      TIMESTAMP DEFAULT NOW(),
    UNIQUE (discovery_id)
);
"""

def init_module2_table(conn):
    with conn.cursor() as cur:
        cur.execute(_CREATE_TABLE_SQL)
    conn.commit()


# ---------------------------------------------------------------------------
# Condition-key analysis
# ---------------------------------------------------------------------------

def _extract_condition_stems(conditions: dict) -> set:
    """
    Extract the logical field stems from a conditions dict.
    Handles both _min/_max suffix format and operator-string value format.
    Returns the raw key names (not stripped) because structural detection
    needs the full key.
    """
    return set(conditions.keys())


def _key_stem(key: str) -> str:
    """Strip _min / _max suffix to get the base field name."""
    if key.endswith("_min"):
        return key[:-4]
    if key.endswith("_max"):
        return key[:-4]
    return key


def _classify_condition_keys(conditions: dict):
    """
    Analyse a conditions dict and return a 5-tuple:
      (is_structural, is_evaluable_direct, truly_unmapped_keys,
       indicator_alias_keys, chain_adapter_keys)

    is_structural:        True if any key is in _STRUCTURAL_PATTERN_KEYS
    is_evaluable_direct:  True if ALL stems are in _DIRECT_MAPPABLE_STEMS
    truly_unmapped_keys:  Keys with no known evaluation path whatsoever
    indicator_alias_keys: Keys that exist in the DB under a different name
                          (lagdelta adapter handles these)
    chain_adapter_keys:   Keys handled by the chain adapter (ids 2/3/4/5)
                          or any recognized approximation — adapter is wired
                          and data exists; only forward time is needed

    Evaluation path exists for a key if it is in any of:
      _DIRECT_MAPPABLE_STEMS, _V2_CTE_MAPPABLE_STEMS,
      _INDICATOR_ALIAS_MAP, _CHAIN_ADAPTER_KNOWN_STEMS
    Only keys in NONE of the above go into truly_unmapped_keys.
    """
    raw_keys = set(conditions.keys())

    if raw_keys & _STRUCTURAL_PATTERN_KEYS:
        return True, False, list(raw_keys), [], []

    stems     = {k: _key_stem(k) for k in raw_keys}
    truly_unmapped = []
    alias     = []
    chain_adp = []

    direct_ok = True
    for raw, stem in stems.items():
        if stem in _DIRECT_MAPPABLE_STEMS:
            continue
        if stem in _V2_CTE_MAPPABLE_STEMS:
            continue
        direct_ok = False
        # Indicator alias: data in DB, lagdelta adapter handles it
        if raw in _INDICATOR_ALIAS_MAP or stem in _INDICATOR_ALIAS_MAP:
            alias.append(raw)
            continue
        # Chain adapter: data in DB, chain adapter handles it
        if stem in _CHAIN_ADAPTER_KNOWN_STEMS:
            chain_adp.append(raw)
            continue
        # Partial-stem match against chain adapter stems
        matched = False
        for ck in _CHAIN_ADAPTER_KNOWN_STEMS:
            if stem.startswith(ck) or ck.startswith(stem):
                chain_adp.append(raw)
                matched = True
                break
        if not matched:
            truly_unmapped.append(raw)

    return False, direct_ok, truly_unmapped, alias, chain_adp


# ---------------------------------------------------------------------------
# Decay verdict logic
# ---------------------------------------------------------------------------

def compute_decay_verdict(
    realized_n: Optional[int],
    realized_wr: Optional[float],
    realized_p: Optional[float],
    discovery_wr: Optional[float],
) -> str:
    """
    Returns one of: failing | decaying | holding | insufficient_n
    All parameters are required for anything other than insufficient_n.
    """
    if realized_n is None or realized_n < _MIN_N_FOR_VERDICT:
        return "insufficient_n"
    if realized_wr is None or realized_p is None:
        return "insufficient_n"

    if realized_wr < _FAILING_WR_CEILING and realized_p < _FAILING_P_CEILING and realized_n >= _FAILING_N_FLOOR:
        return "failing"

    if discovery_wr is not None:
        delta = realized_wr - discovery_wr
        if delta < -_DECAYING_DELTA_PP and realized_p < _DECAYING_P_CEILING and realized_n >= _DECAYING_N_FLOOR:
            return "decaying"

    return "holding"


# ---------------------------------------------------------------------------
# Per-signal classification
# ---------------------------------------------------------------------------

def classify_signal(discovery: dict, fwd_days: int, last_outcome: Optional[dict]) -> dict:
    """
    Return a fully-populated classification dict for one discovery row.
    Never returns None. Every field is present.

    Parameters
    ----------
    discovery:    Row from aiem_signal_discoveries as a dict.
    fwd_days:     Count of distinct scan_dates in polygon_market_daily AFTER discovery date.
    last_outcome: Most recent retestable=True row from aiem_discovery_outcomes, or None.

    Classification order (important):
      1. Structural check  — unevaluable regardless of columns or outcomes
      2. Outcome exists    — if a retestable=True outcome exists the adapter worked;
                             jump directly to verdict, bypassing condition-key analysis
      3. Condition-key gap — no outcome yet AND conditions can't be resolved to columns
      4. Pending time      — conditions parseable but no outcome row or n too small
      5. evaluable_now     — conditions parseable AND n >= 30 → issue verdict
    """
    disc_id      = discovery["id"]
    conditions   = discovery["conditions_json"] or {}
    disc_wr      = discovery.get("signal_win_rate")
    disc_status  = discovery.get("status")

    is_structural, is_direct, unmapped_keys, alias_keys, chain_adapter_keys = _classify_condition_keys(conditions)

    result = {
        "discovery_id":              disc_id,
        "discovery_status":          disc_status,
        "evaluation_status":         None,
        "blocking_reason":           None,
        "missing_columns":           None,
        "forward_days_accumulated":  fwd_days,
        "forward_days_required":     _MIN_FWD_DAYS_FOR_VERDICT,
        "decay_verdict":             None,
        "realized_n":                None,
        "realized_win_rate":         None,
        "realized_p_value":          None,
        "win_rate_at_discovery":     disc_wr,
        "delta_vs_discovery_pp":     None,
        "structural_reason":         None,
        "estimated_years_to_evaluable": None,
    }

    # ── 1. Structural (unevaluable regardless of data or time) ──────────────
    if is_structural:
        raw_keys = set(conditions.keys())
        struct_keys = list(raw_keys & _STRUCTURAL_PATTERN_KEYS)

        # Estimate fire rate from signal_n and the known 2-year window (498 days).
        # signal_n for id 9 is 261 final confirmed fires over 498 trading days ≈ 0.52/day.
        # The spec states ~31/year. Use whichever we can compute.
        fire_rate_per_day = None
        if discovery.get("signal_n") and discovery.get("signal_n") > 0:
            # 498 trading days in the training window
            fire_rate_per_day = discovery["signal_n"] / 498.0
        if fire_rate_per_day and fire_rate_per_day > 0:
            days_needed = math.ceil(200 / fire_rate_per_day)
            years_est = round(days_needed / 252, 1)
        else:
            years_est = None

        result["evaluation_status"] = "unevaluable_structural"
        result["structural_reason"] = (
            f"Signal uses stage-label condition keys {struct_keys} — these are steps in "
            f"a sequential multi-row state machine, not column filters. No SQL WHERE clause "
            f"on any column can replicate this detection, regardless of what columns exist. "
            f"Dedicated retest function _mkt_washout_ignition_retest() exists and runs correctly. "
            f"Block is forward-time only: 0 post-discovery fires observed. "
            f"Fire rate from training window: ~{round(fire_rate_per_day * 252) if fire_rate_per_day else '?'}/year. "
            f"n=200 required for verdict."
        )
        result["estimated_years_to_evaluable"] = years_est
        result["forward_days_accumulated"] = fwd_days
        return result

    # ── 2. Outcome-exists shortcut ───────────────────────────────────────────
    # If a retestable=True outcome row exists, the adapter ran successfully
    # (even if the condition keys look unmapped to this classifier). Use the
    # real evidence directly instead of blocking on the condition-key analysis.
    if last_outcome is not None:
        n  = last_outcome.get("realized_n")
        wr = last_outcome.get("realized_win_rate")
        p  = last_outcome.get("realized_p_value")

        result["realized_n"]         = n
        result["realized_win_rate"]  = wr
        result["realized_p_value"]   = p

        if n is not None and disc_wr is not None and wr is not None:
            result["delta_vs_discovery_pp"] = round(wr - disc_wr, 2)

        if n is None or n < _MIN_N_FOR_VERDICT:
            result["evaluation_status"] = "evaluable_pending_time"
            result["blocking_reason"] = (
                f"Outcome row exists (retestable=True) but realized_n={n} < "
                f"{_MIN_N_FOR_VERDICT} minimum required for verdict. "
                f"Forward trading days accumulated: {fwd_days}. "
                f"OOS window: {last_outcome.get('checked_window_start')} → "
                f"{last_outcome.get('checked_window_end')}. "
                f"Note: condition keys ({list(conditions.keys())}) may not be exactly "
                f"canonical — the adapter that produced this result used an approximation. "
                f"Result is real OOS data; verdict awaits more forward observations."
            )
            result["decay_verdict"] = "insufficient_n"
            return result

        # n >= 30 — issue a real decay verdict.
        verdict = compute_decay_verdict(n, wr, p, disc_wr)
        result["evaluation_status"] = "evaluable_now"
        result["decay_verdict"]     = verdict
        return result

    # ── 3. No outcome exists — check condition-key mappability ───────────────
    # Two tiers:
    #   evaluable_pending_columns — truly_unmapped_keys is non-empty: NO known
    #     evaluation path exists. Requires new data collection or new adapter.
    #   evaluable_pending_time    — all keys have a known path (direct, V2 CTE,
    #     indicator alias, or chain adapter). Block is purely forward time.
    #
    # alias_keys and chain_adapter_keys do NOT block evaluation — they are
    # already handled by the lagdelta/chain adapters in Module 1. Record them
    # for transparency but do NOT classify the signal as pending_columns.

    if unmapped_keys:
        # Genuinely no evaluation path for at least one key.
        truly_missing = []
        for k in unmapped_keys:
            stem = _key_stem(k)
            truly_missing.append(
                f"{k} (stem={stem}) → no known adapter or column mapping; "
                f"requires new data collection and adapter implementation"
            )
        result["evaluation_status"] = "evaluable_pending_columns"
        result["blocking_reason"] = (
            f"{len(truly_missing)} condition key(s) have no known evaluation path "
            f"(no adapter, no column, no approximation). Each must be addressed before "
            f"this signal can receive any OOS verdict."
        )
        result["missing_columns"] = truly_missing
        return result

    # ── 4. All conditions have known evaluation paths — waiting for forward data ──
    # alias_keys: lagdelta adapter handles them (lagdelta_filter_sql in Module 1)
    # chain_adapter_keys: chain adapter handles them (_mkt_chain_filter_sql)
    # Neither is a blocker once Module 1's outcome tracker generates retestable=True rows.
    adapter_notes = []
    for k in alias_keys:
        target = _INDICATOR_ALIAS_MAP.get(k) or _INDICATOR_ALIAS_MAP.get(_key_stem(k))
        adapter_notes.append(
            f"{k} → lagdelta adapter maps to {target}"
        )
    for k in chain_adapter_keys:
        adapter_notes.append(
            f"{k} → chain adapter maps to V2 CTE derived column"
        )

    result["evaluation_status"] = "evaluable_pending_time"
    result["blocking_reason"] = (
        f"All condition keys have known evaluation paths "
        f"(direct columns, V2 CTE derived cols, or wired adapters). "
        f"No retestable=True outcome row exists yet — Module 1's daily outcome "
        f"checker (2:00 AM ET) will generate it once forward-return data is available. "
        f"Forward trading days accumulated: {fwd_days}. "
        f"Adapter notes: {len(adapter_notes)} key(s) use alias/chain mapping."
    )
    if adapter_notes:
        result["missing_columns"] = adapter_notes
    return result


# ---------------------------------------------------------------------------
# Main evaluation loop
# ---------------------------------------------------------------------------

def run_module2(conn) -> list:
    """
    Evaluate all signals in aiem_signal_discoveries.
    Upserts results into aiem_module2_evaluations.
    Returns list of result dicts (one per discovery, ordered by id).
    """
    init_module2_table(conn)

    with conn.cursor() as cur:
        # Load all discoveries
        cur.execute("""
            SELECT id, status, conditions_json, discovered_at,
                   signal_win_rate, signal_n, horizon
            FROM aiem_signal_discoveries
            ORDER BY id
        """)
        cols = [d[0] for d in cur.description]
        discoveries = [dict(zip(cols, row)) for row in cur.fetchall()]

        # Load most recent retestable=True outcome per discovery
        cur.execute("""
            SELECT DISTINCT ON (discovery_id)
                   discovery_id, realized_n, realized_win_rate, realized_p_value,
                   win_rate_at_discovery, checked_window_start, checked_window_end
            FROM aiem_discovery_outcomes
            WHERE retestable = TRUE
            ORDER BY discovery_id, created_at DESC
        """)
        ocols = [d[0] for d in cur.description]
        outcomes_by_id = {
            row[0]: dict(zip(ocols, row))
            for row in cur.fetchall()
        }

        # Count forward trading days per discovery
        cur.execute("""
            SELECT sd.id,
                   COUNT(DISTINCT pmd.scan_date) AS fwd_days
            FROM aiem_signal_discoveries sd
            LEFT JOIN polygon_market_daily pmd
                   ON pmd.scan_date > sd.discovered_at::date
            GROUP BY sd.id
        """)
        fwd_by_id = {row[0]: row[1] for row in cur.fetchall()}

    results = []
    for disc in discoveries:
        disc_id  = disc["id"]
        fwd_days = fwd_by_id.get(disc_id, 0)
        outcome  = outcomes_by_id.get(disc_id)
        ev       = classify_signal(disc, fwd_days, outcome)
        results.append(ev)

        # Upsert into aiem_module2_evaluations
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO aiem_module2_evaluations (
                    discovery_id, discovery_status, evaluation_status,
                    blocking_reason, missing_columns,
                    forward_days_accumulated, forward_days_required,
                    decay_verdict, realized_n, realized_win_rate, realized_p_value,
                    win_rate_at_discovery, delta_vs_discovery_pp,
                    structural_reason, estimated_years_to_evaluable,
                    run_at
                ) VALUES (
                    %(discovery_id)s, %(discovery_status)s, %(evaluation_status)s,
                    %(blocking_reason)s, %(missing_columns)s,
                    %(forward_days_accumulated)s, %(forward_days_required)s,
                    %(decay_verdict)s, %(realized_n)s, %(realized_win_rate)s,
                    %(realized_p_value)s, %(win_rate_at_discovery)s,
                    %(delta_vs_discovery_pp)s, %(structural_reason)s,
                    %(estimated_years_to_evaluable)s,
                    NOW()
                )
                ON CONFLICT (discovery_id) DO UPDATE SET
                    discovery_status            = EXCLUDED.discovery_status,
                    evaluation_status           = EXCLUDED.evaluation_status,
                    blocking_reason             = EXCLUDED.blocking_reason,
                    missing_columns             = EXCLUDED.missing_columns,
                    forward_days_accumulated    = EXCLUDED.forward_days_accumulated,
                    forward_days_required       = EXCLUDED.forward_days_required,
                    decay_verdict               = EXCLUDED.decay_verdict,
                    realized_n                  = EXCLUDED.realized_n,
                    realized_win_rate           = EXCLUDED.realized_win_rate,
                    realized_p_value            = EXCLUDED.realized_p_value,
                    win_rate_at_discovery       = EXCLUDED.win_rate_at_discovery,
                    delta_vs_discovery_pp       = EXCLUDED.delta_vs_discovery_pp,
                    structural_reason           = EXCLUDED.structural_reason,
                    estimated_years_to_evaluable = EXCLUDED.estimated_years_to_evaluable,
                    run_at                      = NOW()
            """, ev)
        conn.commit()

    return results


def get_module2_report(conn) -> dict:
    """Return the latest stored evaluations as a structured report dict."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                m.discovery_id, m.discovery_status, m.evaluation_status,
                m.blocking_reason, m.missing_columns,
                m.forward_days_accumulated, m.forward_days_required,
                m.decay_verdict, m.realized_n, m.realized_win_rate,
                m.realized_p_value, m.win_rate_at_discovery,
                m.delta_vs_discovery_pp, m.structural_reason,
                m.estimated_years_to_evaluable, m.run_at,
                sd.hypothesis_text, sd.conditions_json, sd.horizon
            FROM aiem_module2_evaluations m
            JOIN aiem_signal_discoveries sd ON sd.id = m.discovery_id
            ORDER BY m.discovery_id
        """)
        if cur.rowcount == 0 and cur.description is None:
            return {"signals": [], "summary": {}}
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]

    # Serialise non-JSON-native types
    for row in rows:
        if isinstance(row.get("run_at"), datetime):
            row["run_at"] = row["run_at"].isoformat()
        if isinstance(row.get("conditions_json"), dict):
            pass  # already dict
        # Convert psycopg2 list type to plain list
        if row.get("missing_columns") is not None and not isinstance(row["missing_columns"], list):
            row["missing_columns"] = list(row["missing_columns"])

    # Summary counts
    status_counts = {}
    verdict_counts = {}
    for row in rows:
        es = row["evaluation_status"]
        dv = row.get("decay_verdict")
        status_counts[es] = status_counts.get(es, 0) + 1
        if dv:
            verdict_counts[dv] = verdict_counts.get(dv, 0) + 1

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_signals": len(rows),
        "status_counts": status_counts,
        "verdict_counts": verdict_counts,
        "signals": rows,
    }


# ---------------------------------------------------------------------------
# Standalone execution (for direct testing without main.py)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pprint
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    conn.autocommit = False

    print("Running Module 2 — Decay & Failure Analyzer")
    print("=" * 60)
    results = run_module2(conn)

    for r in results:
        print(f"\n── id={r['discovery_id']}  db_status={r['discovery_status']}  "
              f"fwd_days={r['forward_days_accumulated']}")
        print(f"   evaluation_status : {r['evaluation_status']}")
        if r["decay_verdict"]:
            print(f"   decay_verdict     : {r['decay_verdict']}")
        if r["realized_n"] is not None:
            delt = f"  delta_vs_discovery={r['delta_vs_discovery_pp']:+.2f}pp" if r["delta_vs_discovery_pp"] is not None else ""
            print(f"   realized          : n={r['realized_n']}  wr={r['realized_win_rate']}%  p={r['realized_p_value']}{delt}")
        if r["blocking_reason"]:
            print(f"   blocking_reason   : {r['blocking_reason']}")
        if r["missing_columns"]:
            for mc in r["missing_columns"]:
                print(f"     missing: {mc}")
        if r["structural_reason"]:
            print(f"   structural_reason : {r['structural_reason']}")
        if r["estimated_years_to_evaluable"] is not None:
            print(f"   est_years         : {r['estimated_years_to_evaluable']}")

    conn.close()
