"""
Module 3 — Hypothesis Promotion Evaluator
==========================================
Scans signals in 'hypothesis' status for sufficient positive OOS evidence
and generates promotion/retirement recommendations for Module 4's approval gate.

This module NEVER changes signal status automatically.
Every status transition requires a POST to /stock-api/admin/module4-approve.

Promotion criteria (all must be met for 'promote_ready'):
  1. db_status = 'hypothesis'
  2. Most recent retestable=True outcome exists in aiem_discovery_outcomes
  3. realized_n >= 30   (minimum evidence threshold)
  4. realized_win_rate >= 52.0%   (meaningfully above 50% random baseline)
  5. realized_p_value < 0.10   (statistically suggestive)
  6. realized_win_rate >= signal_win_rate - 7.5pp   (OOS not collapsed vs in-sample)

Retirement recommendation (all must be met for 'hypothesis_failing'):
  1. db_status = 'hypothesis'
  2. retestable=True outcome exists
  3. realized_n >= 30
  4. realized_win_rate < 50.0%   (below random baseline)
  5. realized_p_value < 0.05   (statistically significant failure)

Promotion statuses:
  promote_ready      — qualifies for Module 4 'promote' action
  hypothesis_failing — recommend Module 4 'retire' action
  borderline         — n>=30 but mixed evidence; keep accumulating
  accumulating       — retestable=True outcome exists, n<30; need more time
  no_outcome_yet     — no retestable=True outcome in DB yet
  structural         — structural multi-row pattern; cannot be evaluated by
                       this module (Module 2 handles structural signals)
"""

import datetime as _dt
import math as _math

_PROMOTE_N_FLOOR     = 30
_PROMOTE_WR_FLOOR    = 52.0
_PROMOTE_P_CEILING   = 0.10
_PROMOTE_DELTA_FLOOR = -7.5   # pp — OOS WR must not fall > 7.5pp below in-sample

_FAIL_WR_CEILING     = 50.0
_FAIL_P_CEILING      = 0.05
_FAIL_N_FLOOR        = 30

_STRUCTURAL_KEYS = frozenset({
    "cross", "funnel", "trough", "confirm", "fire_day", "universe",
})


# ---------------------------------------------------------------------------
# Schema init

def init_schema(conn) -> None:
    """Create aiem_module3_evaluations if it doesn't exist."""
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS aiem_module3_evaluations (
                id                   BIGSERIAL PRIMARY KEY,
                discovery_id         INT NOT NULL,
                discovery_status     TEXT,
                promotion_status     TEXT NOT NULL,
                recommendation       TEXT,
                blocking_reason      TEXT,
                realized_n           INT,
                realized_win_rate    DOUBLE PRECISION,
                realized_p_value     DOUBLE PRECISION,
                delta_vs_discovery_pp DOUBLE PRECISION,
                win_rate_at_discovery DOUBLE PRECISION,
                n_at_discovery       INT,
                forward_days_since_discovery INT,
                run_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT uq_m3_disc UNIQUE (discovery_id)
            )
        """)
    conn.commit()


# ---------------------------------------------------------------------------
# Classification helpers

def _is_structural(conditions: dict) -> bool:
    return bool(set(conditions.keys()) & _STRUCTURAL_KEYS)


def _classify(disc: dict, outcome: dict | None) -> dict:
    """
    Classify a single hypothesis signal given its most recent retestable=True
    outcome row (or None if none exists). Returns a result dict.
    """
    disc_id  = disc["id"]
    disc_wr  = disc.get("signal_win_rate")
    disc_n   = disc.get("signal_n")
    conds    = disc.get("conditions_json") or {}
    horizon  = disc.get("horizon")
    status   = disc.get("status")

    result = {
        "discovery_id":              disc_id,
        "discovery_status":          status,
        "promotion_status":          None,
        "recommendation":            None,
        "blocking_reason":           None,
        "realized_n":                None,
        "realized_win_rate":         None,
        "realized_p_value":          None,
        "delta_vs_discovery_pp":     None,
        "win_rate_at_discovery":     disc_wr,
        "n_at_discovery":            disc_n,
        "forward_days_since_discovery": disc.get("fwd_days", 0),
    }

    # Structural check — this module cannot evaluate multi-row state machines
    if _is_structural(conds):
        result["promotion_status"] = "structural"
        result["blocking_reason"] = (
            "Signal uses stage-label condition keys — multi-row state machine pattern. "
            "Module 2 handles structural evaluation via _mkt_washout_ignition_retest(). "
            "Promotion path requires a dedicated structural retest adapter."
        )
        result["recommendation"] = None
        return result

    # No retestable=True outcome yet
    if outcome is None:
        result["promotion_status"] = "no_outcome_yet"
        result["blocking_reason"] = (
            "No retestable=True outcome row exists in aiem_discovery_outcomes. "
            "Module 1's daily outcome checker will generate one once forward-return data "
            "is available in polygon_market_daily."
        )
        result["recommendation"] = "wait — Module 1 runs 2:00 AM ET daily"
        return result

    n  = outcome.get("realized_n")
    wr = outcome.get("realized_win_rate")
    p  = outcome.get("realized_p_value")

    result["realized_n"]        = n
    result["realized_win_rate"] = wr
    result["realized_p_value"]  = p

    if n is not None and disc_wr is not None and wr is not None:
        result["delta_vs_discovery_pp"] = round(wr - disc_wr, 2)

    delta = result["delta_vs_discovery_pp"]

    # Accumulating — n < 30, too early for any verdict
    if n is None or n < _PROMOTE_N_FLOOR:
        result["promotion_status"] = "accumulating"
        result["blocking_reason"] = (
            f"retestable=True outcome exists but realized_n={n} < {_PROMOTE_N_FLOOR} "
            f"minimum required for any verdict. "
            f"Current OOS: wr={wr}% p={p}. "
            f"Continue accumulating forward observations."
        )
        result["recommendation"] = (
            f"wait — need {_PROMOTE_N_FLOOR - (n or 0)} more retestable fires "
            f"(currently n={n})"
        )
        return result

    # n >= 30 — evaluate direction
    wr_ok    = (wr is not None and wr >= _PROMOTE_WR_FLOOR)
    p_ok     = (p  is not None and p  <  _PROMOTE_P_CEILING)
    delta_ok = (delta is not None and delta >= _PROMOTE_DELTA_FLOOR)

    fail_wr  = (wr is not None and wr < _FAIL_WR_CEILING)
    fail_p   = (p  is not None and p  < _FAIL_P_CEILING)

    if wr_ok and p_ok and delta_ok:
        result["promotion_status"] = "promote_ready"
        result["recommendation"]   = "promote — run Module 4 with action='promote'"
        result["blocking_reason"]  = None
        return result

    if fail_wr and fail_p:
        result["promotion_status"] = "hypothesis_failing"
        result["recommendation"]   = "retire — run Module 4 with action='retire'"
        result["blocking_reason"] = (
            f"OOS evidence shows signal is underperforming: "
            f"wr={wr}% < {_FAIL_WR_CEILING}% (below random baseline), "
            f"p={p} < {_FAIL_P_CEILING} (statistically significant). "
            f"n={n}."
        )
        return result

    # n >= 30 but inconclusive
    reasons = []
    if not wr_ok:
        reasons.append(f"wr={wr}% < {_PROMOTE_WR_FLOOR}% threshold")
    if not p_ok:
        reasons.append(f"p={p} >= {_PROMOTE_P_CEILING} (not significant)")
    if not delta_ok:
        reasons.append(
            f"delta={delta}pp < {_PROMOTE_DELTA_FLOOR}pp floor "
            f"(OOS collapsed vs in-sample {disc_wr}%)"
        )
    result["promotion_status"] = "borderline"
    result["blocking_reason"]  = (
        f"n={n} >= threshold but criteria not yet met: {'; '.join(reasons)}. "
        f"Continue accumulating. Reassess once n >= {max(n + 20, 60)}."
    )
    result["recommendation"]   = "wait — keep accumulating OOS observations"
    return result


# ---------------------------------------------------------------------------
# Main evaluation loop

def run_module3(conn) -> list[dict]:
    """
    Evaluate all hypothesis signals and return a list of classification dicts.
    Upserts results into aiem_module3_evaluations.
    """
    with conn.cursor() as cur:
        # Fetch all hypothesis signals
        cur.execute("""
            SELECT id, status, signal_win_rate, signal_n, horizon,
                   conditions_json, discovered_at
            FROM aiem_signal_discoveries
            WHERE status = 'hypothesis'
            ORDER BY id
        """)
        cols = [d[0] for d in cur.description]
        disc_rows = [dict(zip(cols, r)) for r in cur.fetchall()]

        # Fetch most recent retestable=True outcome per signal
        cur.execute("""
            SELECT DISTINCT ON (discovery_id)
                discovery_id, realized_n, realized_win_rate, realized_p_value,
                checked_window_start, checked_window_end
            FROM aiem_discovery_outcomes
            WHERE retestable = TRUE
              AND discovery_id IN (
                  SELECT id FROM aiem_signal_discoveries WHERE status = 'hypothesis'
              )
            ORDER BY discovery_id, checked_window_end DESC, id DESC
        """)
        outcome_cols = [d[0] for d in cur.description]
        outcomes_by_id = {
            r[0]: dict(zip(outcome_cols, r))
            for r in cur.fetchall()
        }

        # Forward days per signal (count distinct scan_dates after discovered_at)
        cur.execute("""
            SELECT d.id,
                   COUNT(DISTINCT pm.scan_date) AS fwd_days
            FROM aiem_signal_discoveries d
            LEFT JOIN polygon_market_daily pm
                   ON pm.scan_date > d.discovered_at
            WHERE d.status = 'hypothesis'
            GROUP BY d.id
        """)
        fwd_by_id = {r[0]: r[1] for r in cur.fetchall()}

    results = []
    for disc in disc_rows:
        disc["fwd_days"] = fwd_by_id.get(disc["id"], 0)
        outcome = outcomes_by_id.get(disc["id"])
        r = _classify(disc, outcome)
        results.append(r)

    # Upsert into aiem_module3_evaluations
    with conn.cursor() as cur:
        for r in results:
            cur.execute("""
                INSERT INTO aiem_module3_evaluations (
                    discovery_id, discovery_status, promotion_status,
                    recommendation, blocking_reason,
                    realized_n, realized_win_rate, realized_p_value,
                    delta_vs_discovery_pp, win_rate_at_discovery,
                    n_at_discovery, forward_days_since_discovery, run_at
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, NOW()
                )
                ON CONFLICT (discovery_id) DO UPDATE SET
                    discovery_status              = EXCLUDED.discovery_status,
                    promotion_status              = EXCLUDED.promotion_status,
                    recommendation                = EXCLUDED.recommendation,
                    blocking_reason               = EXCLUDED.blocking_reason,
                    realized_n                    = EXCLUDED.realized_n,
                    realized_win_rate             = EXCLUDED.realized_win_rate,
                    realized_p_value              = EXCLUDED.realized_p_value,
                    delta_vs_discovery_pp         = EXCLUDED.delta_vs_discovery_pp,
                    win_rate_at_discovery         = EXCLUDED.win_rate_at_discovery,
                    n_at_discovery                = EXCLUDED.n_at_discovery,
                    forward_days_since_discovery  = EXCLUDED.forward_days_since_discovery,
                    run_at                        = NOW()
            """, (
                r["discovery_id"], r["discovery_status"], r["promotion_status"],
                r["recommendation"], r["blocking_reason"],
                r["realized_n"], r["realized_win_rate"], r["realized_p_value"],
                r["delta_vs_discovery_pp"], r["win_rate_at_discovery"],
                r["n_at_discovery"], r["forward_days_since_discovery"],
            ))
    conn.commit()
    return results


# ---------------------------------------------------------------------------
# Status report (for GET endpoint)

def get_module3_report(conn) -> dict:
    """Return the last stored Module 3 evaluations from DB."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT m3.discovery_id, m3.discovery_status, m3.promotion_status,
                   m3.recommendation, m3.blocking_reason,
                   m3.realized_n, m3.realized_win_rate, m3.realized_p_value,
                   m3.delta_vs_discovery_pp, m3.win_rate_at_discovery,
                   m3.n_at_discovery, m3.forward_days_since_discovery,
                   m3.run_at,
                   d.hypothesis_text, d.horizon
            FROM aiem_module3_evaluations m3
            JOIN aiem_signal_discoveries d ON d.id = m3.discovery_id
            ORDER BY m3.discovery_id
        """)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()

    signals = []
    status_counts: dict[str, int] = {}
    for row in rows:
        r = dict(zip(cols, row))
        if r.get("run_at"):
            r["run_at"] = r["run_at"].isoformat()
        ps = r.get("promotion_status") or "unknown"
        status_counts[ps] = status_counts.get(ps, 0) + 1
        signals.append(r)

    return {
        "generated_at":  _dt.datetime.utcnow().isoformat() + "Z",
        "scope":         "hypothesis signals only",
        "promote_ready_count": status_counts.get("promote_ready", 0),
        "status_counts": status_counts,
        "signals":       signals,
    }
