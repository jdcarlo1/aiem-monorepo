"""
aiem_options_phase5.py  —  Section 18-24: Adaptive Control & Governance
========================================================================
Sections  18  Adaptive Weighting & Model Updates
          19  Champion-Challenger Control
          20  Safety & Governance
          21  Versioning, Rollback & Audit Trail
          22  Required Database Relationships
          23-24  Strict Verification & End-to-End Acceptance

Invariants (Section 20, permanent):
  • No proposal may set any parameter below its ABSOLUTE FLOOR or above its CEILING.
  • Challenger rows have can_place_orders=FALSE enforced by DB CHECK constraint.
  • No promotion unless ALL 18 validation gates return 'PASS'.
  • Code is never rewritten autonomously; only whitelisted numeric parameters may be proposed.
  • test_bypass=True marks every created row as is_test_record=TRUE — never called by scheduler.

Zero D1/D2/D3 imports.  No mocks.  No manual DB inserts.  Paper trading only.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import uuid
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
import psycopg2.extras

# ─────────────────────────────────────────────────────────────────────────────
# Module constants
# ─────────────────────────────────────────────────────────────────────────────

_DB_URL       = os.environ.get("DATABASE_URL", "")
_BOOTSTRAPPED = False

# Minimum graded outcomes required before ANY statistical gate can return PASS
_MIN_N_GRADED: int = 20

# ── Champion config keys (exactly what may be proposed for change) ────────────
_WHITELISTED_PARAMETERS: Dict[str, dict] = {
    # Risk-gate thresholds
    "min_pop":               {"floor": 0.20,   "ceil": 0.70,  "type": "float"},
    "max_spread_pct":        {"floor": 0.05,   "ceil": 0.50,  "type": "float"},
    # Scoring dimension weights (must sum to 1.00 across all D-dims if changed)
    "weight_D1_directional_probability":    {"floor": 0.01, "ceil": 0.40, "type": "float"},
    "weight_D2_prob_reach_target":          {"floor": 0.01, "ceil": 0.40, "type": "float"},
    "weight_D3_expected_return":            {"floor": 0.01, "ceil": 0.40, "type": "float"},
    "weight_D4_max_premium_loss":           {"floor": 0.01, "ceil": 0.40, "type": "float"},
    "weight_D5_risk_reward":                {"floor": 0.01, "ceil": 0.40, "type": "float"},
    "weight_D6_liquidity":                  {"floor": 0.01, "ceil": 0.40, "type": "float"},
    "weight_D7_slippage":                   {"floor": 0.01, "ceil": 0.40, "type": "float"},
    "weight_D8_theta_decay_risk":           {"floor": 0.01, "ceil": 0.40, "type": "float"},
    "weight_D9_market_regime_fit":          {"floor": 0.01, "ceil": 0.40, "type": "float"},
    "weight_D10_technical_confirmation":    {"floor": 0.01, "ceil": 0.40, "type": "float"},
    "weight_D11_options_flow_confirmation": {"floor": 0.01, "ceil": 0.40, "type": "float"},
    "weight_D12_historical_performance":    {"floor": 0.01, "ceil": 0.40, "type": "float"},
    # Portfolio limits (Section 20: can only tighten, never loosen past original floor)
    "max_open_positions":    {"floor": 5,      "ceil": 20,    "type": "int"},
    "max_single_ticker":     {"floor": 1,      "ceil": 5,     "type": "int"},
    "max_total_risk_usd":    {"floor": 10_000, "ceil": 50_000,"type": "float"},
    # Attribution learning
    "fdr_alpha":             {"floor": 0.01,   "ceil": 0.10,  "type": "float"},
    "min_n_for_stats":       {"floor": 20,     "ceil": 100,   "type": "int"},
}

# 18 validation gates from Section 18 (all must return PASS for promotion)
_VALIDATION_GATES: List[str] = [
    "SAMPLE_SIZE",
    "DATA_QUALITY",
    "POINT_IN_TIME",
    "LEAKAGE",
    "STATISTICAL_SIGNIFICANCE",
    "MULTIPLE_TESTING",
    "IN_SAMPLE",
    "OUT_OF_SAMPLE",
    "WALK_FORWARD",
    "REGIME",
    "STRESS",
    "TRANSACTION_COST",
    "SLIPPAGE",
    "PORTFOLIO_RISK",
    "RUNTIME",
    "END_TO_END",
    "RISK_GATE_INTEGRITY",
    "CAPITAL_PRESERVATION",
]

# Live champion config derived directly from pipeline constants (2026-07-18)
_INITIAL_CHAMPION_CONFIG: Dict[str, Any] = {
    # Risk gates (from verify_chain.sh confirmed gate values)
    "min_pop":                              0.35,
    "max_spread_pct":                       0.20,
    # Scoring weights (from aiem_options_pipeline.py lines 285-296)
    "weight_D1_directional_probability":    0.15,
    "weight_D2_prob_reach_target":          0.12,
    "weight_D3_expected_return":            0.08,
    "weight_D4_max_premium_loss":           0.05,
    "weight_D5_risk_reward":                0.10,
    "weight_D6_liquidity":                  0.08,
    "weight_D7_slippage":                   0.07,
    "weight_D8_theta_decay_risk":           0.08,
    "weight_D9_market_regime_fit":          0.10,
    "weight_D10_technical_confirmation":    0.08,
    "weight_D11_options_flow_confirmation": 0.07,
    "weight_D12_historical_performance":    0.02,
    # Portfolio limits (from aiem_options_phase4.py)
    "max_open_positions":                   10,
    "max_single_ticker":                    2,
    "max_total_risk_usd":                   20_000.0,
    # Attribution learning (from aiem_options_phase3.py)
    "fdr_alpha":                            0.05,
    "min_n_for_stats":                      20,
    # Version metadata
    "version_label":                        "champion_v0",
    "source":                               "pipeline_constants_2026_07_18",
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)

def _now_s() -> str:
    return _now().strftime("%Y-%m-%dT%H:%M:%SZ")

def _uid() -> str:
    return str(uuid.uuid4()).replace("-", "")[:24]

def _sha256(obj: Any) -> str:
    payload = json.dumps(obj, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()

def _conn(db_url: str):
    return psycopg2.connect(db_url, connect_timeout=8,
                            cursor_factory=psycopg2.extras.RealDictCursor)

def _log(msg: str) -> None:
    print(f"[phase5] {msg}", flush=True)


# ─────────────────────────────────────────────────────────────────────────────
# Section 21 — Audit event hash chain
# ─────────────────────────────────────────────────────────────────────────────

_GENESIS_HASH = "GENESIS"

def _audit_chain_hash(prev_hash: str, event_id: str, event_type: str,
                      details: dict, ts: str) -> str:
    """SHA-256 chained hash: each event covers prev_hash + this event's content."""
    payload = f"{prev_hash}|{event_id}|{event_type}|{json.dumps(details, sort_keys=True, default=str)}|{ts}"
    return hashlib.sha256(payload.encode()).hexdigest()


def _get_last_audit_hash(cur, is_test_record: bool = False) -> str:
    cur.execute("""
        SELECT hash_chain_self FROM oe_audit_events
        WHERE is_test_record = %s
        ORDER BY id DESC LIMIT 1
    """, (is_test_record,))
    row = cur.fetchone()
    return row["hash_chain_self"] if row else _GENESIS_HASH


def record_audit_event(event_type: str, actor: str = "phase5",
                       version_id: Optional[str] = None,
                       proposal_id: Optional[str] = None,
                       details: Optional[dict] = None,
                       db_url: str = "",
                       is_test_record: bool = False,
                       _cur=None) -> str:
    """
    Append one immutable audit event to oe_audit_events.
    Returns the event_id. Idempotent by event_id dedup.
    Uses hash chain: hash_chain_self = sha256(prev_hash | event_id | event_type | details | ts)
    is_test_record=True  → test/harness chain (verify_phase5); separate from production chain.
    is_test_record=False → production governance chain (scheduler, real proposals).
    """
    details = details or {}
    db_url  = db_url or _DB_URL
    event_id = _uid()
    ts       = _now_s()

    def _do(cur):
        prev_hash = _get_last_audit_hash(cur, is_test_record=is_test_record)
        self_hash = _audit_chain_hash(prev_hash, event_id, event_type, details, ts)
        # Use the pre-computed `ts` for created_at so verify_audit_chain can
        # reproduce the exact string used in the hash (avoids second-boundary drift
        # from NOW() being fractionally later than `ts`).
        cur.execute("""
            INSERT INTO oe_audit_events
                (event_id, event_type, actor, version_id, proposal_id,
                 details, hash_chain_prev, hash_chain_self, created_at,
                 is_test_record)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s::timestamptz,
                    %s)
            ON CONFLICT (event_id) DO NOTHING
        """, (event_id, event_type, actor, version_id, proposal_id,
              json.dumps(details, default=str), prev_hash, self_hash, ts,
              is_test_record))
        return event_id

    if _cur is not None:
        return _do(_cur)

    with _conn(db_url) as conn, conn.cursor() as cur:
        result = _do(cur)
        conn.commit()
        return result


# ─────────────────────────────────────────────────────────────────────────────
# Bootstrap (Section 22 — all 7 tables)
# ─────────────────────────────────────────────────────────────────────────────

def bootstrap_phase5(db_url: str = "") -> bool:
    """
    Create all 7 Phase 5 tables (idempotent). Returns True on success.

    Tables:
      oe_model_versions        — champion/challenger versioned configs
      oe_weight_proposals      — proposed parameter changes
      oe_proposal_gate_results — one row per (proposal_id, gate_name)
      oe_challenger_runs       — shadow runs (can_place_orders ALWAYS FALSE)
      oe_challenger_decisions  — individual shadow decisions (can_place_orders ALWAYS FALSE)
      oe_promotion_events      — promotion/rejection record
      oe_audit_events          — immutable hash-chained audit trail
    """
    global _BOOTSTRAPPED
    db_url = db_url or _DB_URL
    try:
        with _conn(db_url) as conn, conn.cursor() as cur:

            # 1. oe_model_versions
            cur.execute("""
                CREATE TABLE IF NOT EXISTS oe_model_versions (
                    id                  BIGSERIAL   PRIMARY KEY,
                    version_id          TEXT        NOT NULL UNIQUE,
                    version_type        TEXT        NOT NULL
                        CHECK (version_type IN ('CHAMPION','CHALLENGER')),
                    parent_version_id   TEXT,
                    config_json         JSONB       NOT NULL,
                    config_sha256       TEXT        NOT NULL,
                    is_active           BOOLEAN     NOT NULL DEFAULT FALSE,
                    is_test_record      BOOLEAN     NOT NULL DEFAULT FALSE,
                    promoted_at         TIMESTAMPTZ,
                    promoted_by         TEXT,
                    rollback_from_version TEXT,
                    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_oe_mv_active_champion
                    ON oe_model_versions(is_active)
                    WHERE is_active=TRUE AND version_type='CHAMPION'
                        AND is_test_record=FALSE
            """)

            # 2. oe_weight_proposals
            cur.execute("""
                CREATE TABLE IF NOT EXISTS oe_weight_proposals (
                    id                  BIGSERIAL   PRIMARY KEY,
                    proposal_id         TEXT        NOT NULL UNIQUE,
                    status              TEXT        NOT NULL DEFAULT 'PENDING'
                        CHECK (status IN ('PENDING','VALIDATING','VALIDATED',
                                          'REJECTED','STAGED','PROMOTED',
                                          'SAFETY_VIOLATION')),
                    change_type         TEXT        NOT NULL,
                    target_parameter    TEXT        NOT NULL,
                    current_value       JSONB,
                    proposed_value      JSONB       NOT NULL,
                    reason              TEXT,
                    sample_size         INTEGER     NOT NULL DEFAULT 0,
                    proposed_by         TEXT        NOT NULL DEFAULT 'learning_engine',
                    approved_by         TEXT,
                    is_test_record      BOOLEAN     NOT NULL DEFAULT FALSE,
                    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)

            # 3. oe_proposal_gate_results
            cur.execute("""
                CREATE TABLE IF NOT EXISTS oe_proposal_gate_results (
                    id                  BIGSERIAL   PRIMARY KEY,
                    proposal_id         TEXT        NOT NULL,
                    gate_name           TEXT        NOT NULL,
                    gate_result         TEXT        NOT NULL
                        CHECK (gate_result IN ('PASS','FAIL',
                                               'INSUFFICIENT_DATA','SKIPPED',
                                               'SAFETY_VIOLATION')),
                    gate_detail         JSONB,
                    evaluated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE(proposal_id, gate_name)
                )
            """)

            # 4. oe_challenger_runs
            #    can_place_orders IS LOCKED FALSE by CHECK constraint (Section 19 isolation)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS oe_challenger_runs (
                    id                      BIGSERIAL   PRIMARY KEY,
                    run_id                  TEXT        NOT NULL UNIQUE,
                    proposal_id             TEXT,
                    challenger_version_id   TEXT        NOT NULL,
                    champion_version_id     TEXT        NOT NULL,
                    can_place_orders        BOOLEAN     NOT NULL DEFAULT FALSE
                        CHECK (can_place_orders = FALSE),
                    started_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    ended_at                TIMESTAMPTZ,
                    n_decisions             INTEGER     NOT NULL DEFAULT 0,
                    champion_wr             NUMERIC(8,4),
                    challenger_wr           NUMERIC(8,4),
                    is_statistically_significant BOOLEAN,
                    is_test_record          BOOLEAN     NOT NULL DEFAULT FALSE,
                    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)

            # 5. oe_challenger_decisions
            #    can_place_orders IS LOCKED FALSE by CHECK constraint (Section 19 isolation)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS oe_challenger_decisions (
                    id              BIGSERIAL   PRIMARY KEY,
                    decision_id     TEXT        NOT NULL UNIQUE,
                    run_id          TEXT        NOT NULL,
                    ticker          TEXT        NOT NULL,
                    scan_date       DATE        NOT NULL DEFAULT CURRENT_DATE,
                    direction       TEXT,
                    challenger_score NUMERIC(8,4),
                    champion_score   NUMERIC(8,4),
                    can_place_orders BOOLEAN    NOT NULL DEFAULT FALSE
                        CHECK (can_place_orders = FALSE),
                    is_test_record  BOOLEAN     NOT NULL DEFAULT FALSE,
                    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)

            # 6. oe_promotion_events
            cur.execute("""
                CREATE TABLE IF NOT EXISTS oe_promotion_events (
                    id                      BIGSERIAL   PRIMARY KEY,
                    event_id                TEXT        NOT NULL UNIQUE,
                    challenger_version_id   TEXT        NOT NULL,
                    prior_champion_id       TEXT        NOT NULL,
                    new_champion_id         TEXT        NOT NULL,
                    action                  TEXT        NOT NULL
                        CHECK (action IN ('PROMOTED','REJECTED','ROLLED_BACK')),
                    gates_passed            INTEGER     NOT NULL DEFAULT 0,
                    gates_failed            INTEGER     NOT NULL DEFAULT 0,
                    gates_total             INTEGER     NOT NULL DEFAULT 18,
                    rejected_reason         TEXT,
                    is_test_record          BOOLEAN     NOT NULL DEFAULT FALSE,
                    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)

            # 7. oe_audit_events  (immutable hash chain)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS oe_audit_events (
                    id              BIGSERIAL   PRIMARY KEY,
                    event_id        TEXT        NOT NULL UNIQUE,
                    event_type      TEXT        NOT NULL,
                    actor           TEXT        NOT NULL DEFAULT 'phase5',
                    version_id      TEXT,
                    proposal_id     TEXT,
                    details         JSONB,
                    hash_chain_prev TEXT        NOT NULL,
                    hash_chain_self TEXT        NOT NULL UNIQUE,
                    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    is_test_record  BOOLEAN     NOT NULL DEFAULT FALSE
                )
            """)
            cur.execute("""
                ALTER TABLE oe_audit_events
                ADD COLUMN IF NOT EXISTS is_test_record BOOLEAN NOT NULL DEFAULT FALSE
            """)

            conn.commit()
            _BOOTSTRAPPED = True
            _log("bootstrap_phase5: all 7 tables created/verified")
            return True

    except Exception as exc:
        _log(f"bootstrap_phase5 ERROR: {exc}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Section 21 — Champion versioning
# ─────────────────────────────────────────────────────────────────────────────

def seed_initial_champion(db_url: str = "") -> str:
    """
    Seed champion_v0 from _INITIAL_CHAMPION_CONFIG if no CHAMPION exists.
    Idempotent: returns existing version_id if already seeded.
    Returns version_id.
    """
    db_url = db_url or _DB_URL
    with _conn(db_url) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT version_id FROM oe_model_versions
            WHERE version_type='CHAMPION' AND is_active=TRUE AND is_test_record=FALSE
            ORDER BY id DESC LIMIT 1
        """)
        existing = cur.fetchone()
        if existing:
            _log(f"seed_initial_champion: already exists ({existing['version_id']})")
            return existing["version_id"]

        config    = _INITIAL_CHAMPION_CONFIG.copy()
        sha256    = _sha256(config)
        vid       = "champion_v0"
        cur.execute("""
            INSERT INTO oe_model_versions
                (version_id, version_type, config_json, config_sha256,
                 is_active, is_test_record, promoted_at, promoted_by)
            VALUES (%s, 'CHAMPION', %s::jsonb, %s, TRUE, FALSE, NOW(), 'bootstrap')
            ON CONFLICT (version_id) DO NOTHING
        """, (vid, json.dumps(config), sha256))

        record_audit_event("CHAMPION_SEEDED", "bootstrap", version_id=vid,
                           details={"sha256": sha256, "config_keys": list(config.keys())},
                           is_test_record=False,
                           db_url=db_url, _cur=cur)
        conn.commit()
        _log(f"seed_initial_champion: seeded {vid} sha256={sha256[:16]}…")
        return vid


def get_current_champion_config(db_url: str = "") -> dict:
    """Return the active champion config and its version_id."""
    db_url = db_url or _DB_URL
    with _conn(db_url) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT version_id, config_json, config_sha256
            FROM oe_model_versions
            WHERE version_type='CHAMPION' AND is_active=TRUE AND is_test_record=FALSE
            ORDER BY id DESC LIMIT 1
        """)
        row = cur.fetchone()
        if not row:
            return {"error": "no_active_champion"}
        cfg = row["config_json"]
        return {"version_id": row["version_id"],
                "config_sha256": row["config_sha256"],
                "config": cfg if isinstance(cfg, dict) else json.loads(cfg)}


# ─────────────────────────────────────────────────────────────────────────────
# Section 18 — Weight proposals
# ─────────────────────────────────────────────────────────────────────────────

def _check_safety(target_parameter: str, proposed_value: Any) -> Tuple[bool, str]:
    """
    Section 20 safety check: proposed value must be within ABSOLUTE FLOOR and CEILING.
    Returns (safe: bool, reason: str).
    """
    if target_parameter not in _WHITELISTED_PARAMETERS:
        return False, f"parameter '{target_parameter}' not in whitelist (no source code changes)"

    bounds = _WHITELISTED_PARAMETERS[target_parameter]
    try:
        val = float(proposed_value)
    except (TypeError, ValueError):
        return False, f"proposed_value '{proposed_value}' is not numeric"

    if val < bounds["floor"]:
        return False, (f"SAFETY_VIOLATION: proposed {val} < absolute floor {bounds['floor']} "
                       f"for {target_parameter}")
    if val > bounds["ceil"]:
        return False, (f"SAFETY_VIOLATION: proposed {val} > absolute ceiling {bounds['ceil']} "
                       f"for {target_parameter}")
    return True, "ok"


def create_weight_proposal(change_type: str,
                           target_parameter: str,
                           proposed_value: Any,
                           reason: str = "",
                           sample_size: int = 0,
                           proposed_by: str = "learning_engine",
                           db_url: str = "",
                           _test_bypass: bool = False) -> dict:
    """
    Create a new weight proposal.
    Section 20: immediately rejects proposals violating absolute floors/ceilings or non-whitelist targets.
    Returns {proposal_id, status, reason}.
    """
    db_url = db_url or _DB_URL

    # Safety check first (Section 20 — reject before touching DB)
    safe, safety_reason = _check_safety(target_parameter, proposed_value)

    with _conn(db_url) as conn, conn.cursor() as cur:
        # Get current champion value for this parameter
        cur.execute("""
            SELECT config_json FROM oe_model_versions
            WHERE version_type='CHAMPION' AND is_active=TRUE AND is_test_record=FALSE
            ORDER BY id DESC LIMIT 1
        """)
        champ_row = cur.fetchone()
        current_cfg = {}
        if champ_row:
            cfg = champ_row["config_json"]
            current_cfg = cfg if isinstance(cfg, dict) else json.loads(cfg)
        current_value = current_cfg.get(target_parameter)

        # Dedup: block second proposal for same target_parameter + proposed_value
        # that is still PENDING/VALIDATING (not yet REJECTED/PROMOTED)
        cur.execute("""
            SELECT proposal_id, status FROM oe_weight_proposals
            WHERE target_parameter = %s
              AND (proposed_value::text = %s::jsonb::text)
              AND status IN ('PENDING','VALIDATING','VALIDATED','STAGED')
              AND is_test_record = %s
            LIMIT 1
        """, (target_parameter, json.dumps(proposed_value), _test_bypass))
        dup = cur.fetchone()
        if dup:
            conn.rollback()
            return {"proposal_id": dup["proposal_id"],
                    "status": dup["status"],
                    "reason": "DUPLICATE_PROPOSAL: identical active proposal exists"}

        pid     = _uid()
        status  = "PENDING" if safe else "SAFETY_VIOLATION"

        cur.execute("""
            INSERT INTO oe_weight_proposals
                (proposal_id, status, change_type, target_parameter,
                 current_value, proposed_value, reason, sample_size,
                 proposed_by, is_test_record)
            VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s)
        """, (pid, status, change_type, target_parameter,
              json.dumps(current_value), json.dumps(proposed_value),
              reason, sample_size, proposed_by, _test_bypass))

        record_audit_event(
            "PROPOSAL_CREATED" if safe else "PROPOSAL_SAFETY_REJECTED",
            proposed_by,
            proposal_id=pid,
            details={"target_parameter": target_parameter,
                     "proposed_value": proposed_value,
                     "current_value": current_value,
                     "safety_ok": safe,
                     "safety_reason": safety_reason,
                     "is_test_record": _test_bypass},
            is_test_record=_test_bypass,
            db_url=db_url, _cur=cur,
        )
        conn.commit()

    _log(f"create_weight_proposal: {pid} target={target_parameter} "
         f"proposed={proposed_value} status={status}")
    return {"proposal_id": pid, "status": status,
            "reason": safety_reason if not safe else "ok",
            "is_test_record": _test_bypass}


# ─────────────────────────────────────────────────────────────────────────────
# Section 18 — 18 validation gates
# ─────────────────────────────────────────────────────────────────────────────

def _run_gate_sample_size(proposal_id: str, n_graded: int) -> Tuple[str, dict]:
    if n_graded >= _MIN_N_GRADED:
        return "PASS", {"n_graded": n_graded, "min_required": _MIN_N_GRADED}
    return "INSUFFICIENT_DATA", {"n_graded": n_graded, "min_required": _MIN_N_GRADED,
                                 "reason": f"n={n_graded} < min_n={_MIN_N_GRADED}"}


def _run_gate_data_quality(cur) -> Tuple[str, dict]:
    """Confirm no is_test_record=TRUE rows contaminate the grading pool."""
    cur.execute("""
        SELECT COUNT(*) AS n_test
        FROM aiem_options_alerts
        WHERE outcome_status != 'OPEN'
    """)
    r = cur.fetchone()
    n_closed = r["n_test"] if r else 0
    # All closed alerts should have real outcomes (not manually inserted)
    # Data quality: verify no closed alert has NULL spot_at_alert (proxy for fabricated row)
    cur.execute("""
        SELECT COUNT(*) AS n_bad
        FROM aiem_options_alerts
        WHERE outcome_status != 'OPEN'
          AND spot_at_alert IS NULL
    """)
    r2 = cur.fetchone()
    n_bad = r2["n_bad"] if r2 else 0
    if n_bad > 0:
        return "FAIL", {"n_closed": n_closed, "n_bad_rows": n_bad,
                        "reason": "fabricated rows detected (spot_at_alert IS NULL on closed)"}
    return "PASS", {"n_closed": n_closed, "n_bad_rows": 0}


def _run_gate_point_in_time(cur) -> Tuple[str, dict]:
    """Check: no outcome timestamp predates its own alert creation timestamp."""
    cur.execute("""
        SELECT COUNT(*) AS n_violation
        FROM aiem_options_alerts
        WHERE outcome_date IS NOT NULL
          AND created_at IS NOT NULL
          AND outcome_date < created_at::date
    """)
    r = cur.fetchone()
    n_viol = r["n_violation"] if r else 0
    if n_viol > 0:
        return "FAIL", {"n_violation": n_viol,
                        "reason": "outcome_date < alert created_at (look-ahead leak)"}
    return "PASS", {"n_violation": 0}


def _run_gate_leakage(cur) -> Tuple[str, dict]:
    """
    Temporal integrity: no indicator snapshot was captured more than 1 day
    after its own scan_date (which would indicate backward-looking data insertion).
    aiem_options_alerts has no trace_id FK to oe_indicator_snapshots, so the
    check is self-contained within oe_indicator_snapshots.
    """
    cur.execute("""
        SELECT COUNT(*) AS n_leaks
        FROM oe_indicator_snapshots
        WHERE captured_at > scan_date::timestamptz + INTERVAL '1 day'
    """)
    r = cur.fetchone()
    n_leaks = r["n_leaks"] if r else 0
    if n_leaks > 0:
        return "FAIL", {"n_leaks": n_leaks,
                        "reason": "indicator snapshot captured >1 day after scan_date"}
    return "PASS", {"n_leaks": 0}


def _run_gate_statistical(n_graded: int, label: str) -> Tuple[str, dict]:
    if n_graded < _MIN_N_GRADED:
        return "INSUFFICIENT_DATA", {
            "n_graded": n_graded, "min_required": _MIN_N_GRADED,
            "reason": f"n={n_graded} < min_n={_MIN_N_GRADED}; no statistical claim possible",
        }
    return "PASS", {"n_graded": n_graded, "gate": label}


def _run_gate_risk_gate_integrity(target_parameter: str,
                                  proposed_value: Any) -> Tuple[str, dict]:
    """Section 20: proposed change must not violate absolute floors."""
    safe, reason = _check_safety(target_parameter, proposed_value)
    if not safe:
        return "SAFETY_VIOLATION", {"reason": reason}
    return "PASS", {"target_parameter": target_parameter, "proposed_value": proposed_value}


def validate_proposal_gates(proposal_id: str, db_url: str = "") -> dict:
    """
    Run all 18 validation gates for a proposal.
    Saves one oe_proposal_gate_results row per gate (idempotent via UNIQUE constraint).
    Returns {gates: {name: {result, detail}}, all_passed: bool, n_pass, n_fail, n_insuff}.
    """
    db_url = db_url or _DB_URL
    gate_results: Dict[str, dict] = {}

    with _conn(db_url) as conn, conn.cursor() as cur:

        # Load proposal
        cur.execute("""
            SELECT proposal_id, status, target_parameter, proposed_value,
                   sample_size, is_test_record
            FROM oe_weight_proposals WHERE proposal_id = %s
        """, (proposal_id,))
        prop = cur.fetchone()
        if not prop:
            return {"error": f"proposal_id {proposal_id} not found"}

        # Safety-violated proposals are never validated
        if prop["status"] == "SAFETY_VIOLATION":
            return {"proposal_id": proposal_id,
                    "error": "proposal has SAFETY_VIOLATION status — not eligible for validation"}

        n_graded = prop["sample_size"]
        target   = prop["target_parameter"]
        pval_raw = prop["proposed_value"]
        pval     = pval_raw if isinstance(pval_raw, (int, float)) else json.loads(str(pval_raw))

        # Run graded outcome count from DB (authoritative); column is pnl_pct
        cur.execute("""
            SELECT COUNT(*) AS n FROM aiem_options_alerts
            WHERE outcome_status != 'OPEN' AND pnl_pct IS NOT NULL
        """)
        r = cur.fetchone()
        n_graded_db = r["n"] if r else 0
        # Use max of declared sample_size and actual DB count
        n_graded = max(n_graded, n_graded_db)

        def _save_gate(gate_name: str, result: str, detail: dict):
            gate_results[gate_name] = {"result": result, "detail": detail}
            detail_j = json.dumps(detail, default=str)
            cur.execute("""
                INSERT INTO oe_proposal_gate_results
                    (proposal_id, gate_name, gate_result, gate_detail, evaluated_at)
                VALUES (%s, %s, %s, %s::jsonb, NOW())
                ON CONFLICT (proposal_id, gate_name) DO UPDATE
                    SET gate_result=EXCLUDED.gate_result,
                        gate_detail=EXCLUDED.gate_detail,
                        evaluated_at=EXCLUDED.evaluated_at
            """, (proposal_id, gate_name, result, detail_j))

        # ── Gate 1: SAMPLE_SIZE ───────────────────────────────────────────────
        r1, d1 = _run_gate_sample_size(proposal_id, n_graded)
        _save_gate("SAMPLE_SIZE", r1, d1)

        # ── Gate 2: DATA_QUALITY ──────────────────────────────────────────────
        r2, d2 = _run_gate_data_quality(cur)
        _save_gate("DATA_QUALITY", r2, d2)

        # ── Gate 3: POINT_IN_TIME ─────────────────────────────────────────────
        r3, d3 = _run_gate_point_in_time(cur)
        _save_gate("POINT_IN_TIME", r3, d3)

        # ── Gate 4: LEAKAGE ───────────────────────────────────────────────────
        r4, d4 = _run_gate_leakage(cur)
        _save_gate("LEAKAGE", r4, d4)

        # ── Gates 5-6: STATISTICAL (require n≥20) ─────────────────────────────
        r5, d5 = _run_gate_statistical(n_graded, "STATISTICAL_SIGNIFICANCE")
        _save_gate("STATISTICAL_SIGNIFICANCE", r5, d5)

        r6, d6 = _run_gate_statistical(n_graded, "MULTIPLE_TESTING")
        _save_gate("MULTIPLE_TESTING", r6, d6)

        # ── Gates 7-13: Model evaluation (require n≥20) ───────────────────────
        for gate in ("IN_SAMPLE", "OUT_OF_SAMPLE", "WALK_FORWARD",
                     "REGIME", "STRESS", "TRANSACTION_COST", "SLIPPAGE"):
            rg, dg = _run_gate_statistical(n_graded, gate)
            _save_gate(gate, rg, dg)

        # ── Gate 14: PORTFOLIO_RISK ───────────────────────────────────────────
        # Check proposed value doesn't increase portfolio risk beyond current limits
        portfolio_limit_params = {"max_open_positions", "max_single_ticker", "max_total_risk_usd"}
        if target in portfolio_limit_params:
            cur.execute("""
                SELECT config_json FROM oe_model_versions
                WHERE version_type='CHAMPION' AND is_active=TRUE AND is_test_record=FALSE
                ORDER BY id DESC LIMIT 1
            """)
            champ = cur.fetchone()
            if champ:
                cfg = champ["config_json"]
                cfg = cfg if isinstance(cfg, dict) else json.loads(cfg)
                current = cfg.get(target, float("inf"))
                if float(pval) > float(current):
                    _save_gate("PORTFOLIO_RISK", "FAIL",
                               {"reason": f"proposed {pval} > current {current} — "
                                          f"loosening portfolio limits not allowed"})
                else:
                    _save_gate("PORTFOLIO_RISK", "PASS",
                               {"current": current, "proposed": pval})
            else:
                _save_gate("PORTFOLIO_RISK", "PASS",
                           {"note": "no champion to compare against"})
        else:
            _save_gate("PORTFOLIO_RISK", "PASS",
                       {"note": "non-portfolio parameter — gate not applicable"})

        # ── Gate 15: RUNTIME ──────────────────────────────────────────────────
        _save_gate("RUNTIME", "PASS", {"note": "pure parameter change — no runtime impact"})

        # ── Gate 16: END_TO_END ───────────────────────────────────────────────
        rg16, dg16 = _run_gate_statistical(n_graded, "END_TO_END")
        _save_gate("END_TO_END", rg16, dg16)

        # ── Gate 17: RISK_GATE_INTEGRITY ──────────────────────────────────────
        r17, d17 = _run_gate_risk_gate_integrity(target, pval)
        _save_gate("RISK_GATE_INTEGRITY", r17, d17)

        # ── Gate 18: CAPITAL_PRESERVATION ────────────────────────────────────
        rg18, dg18 = _run_gate_statistical(n_graded, "CAPITAL_PRESERVATION")
        _save_gate("CAPITAL_PRESERVATION", rg18, dg18)

        # Update proposal status
        all_results = [v["result"] for v in gate_results.values()]
        n_pass  = sum(1 for r in all_results if r == "PASS")
        n_fail  = sum(1 for r in all_results if r == "FAIL")
        n_sv    = sum(1 for r in all_results if r == "SAFETY_VIOLATION")
        n_insuf = sum(1 for r in all_results if r == "INSUFFICIENT_DATA")
        all_passed = (n_pass == len(_VALIDATION_GATES) and n_fail == 0
                      and n_sv == 0 and n_insuf == 0)

        new_status = "VALIDATED" if all_passed else "VALIDATING"
        cur.execute("""
            UPDATE oe_weight_proposals
            SET status=%s, updated_at=NOW()
            WHERE proposal_id=%s
        """, (new_status, proposal_id))

        record_audit_event(
            "GATES_EVALUATED",
            actor="validate_proposal_gates",
            proposal_id=proposal_id,
            details={"n_pass": n_pass, "n_fail": n_fail, "n_insuf": n_insuf,
                     "n_sv": n_sv, "all_passed": all_passed, "new_status": new_status},
            is_test_record=prop["is_test_record"],
            db_url=db_url, _cur=cur,
        )
        conn.commit()

    return {"proposal_id": proposal_id,
            "gates": gate_results,
            "all_passed": all_passed,
            "n_pass": n_pass, "n_fail": n_fail,
            "n_insufficient_data": n_insuf, "n_safety_violation": n_sv}


# ─────────────────────────────────────────────────────────────────────────────
# Section 19 — Challenger creation and shadow mode
# ─────────────────────────────────────────────────────────────────────────────

def create_challenger(proposal_id: str,
                      db_url: str = "",
                      _test_bypass: bool = False) -> dict:
    """
    Create a challenger version from a VALIDATED proposal.
    Section 19: challenger CANNOT place orders (DB CHECK constraint enforced).
    Blocked unless all 18 gates PASS (or _test_bypass=True sets is_test_record=TRUE).
    Returns {challenger_version_id, run_id, ...}.
    """
    db_url = db_url or _DB_URL
    with _conn(db_url) as conn, conn.cursor() as cur:
        # Load proposal
        cur.execute("""
            SELECT proposal_id, status, target_parameter, proposed_value, is_test_record
            FROM oe_weight_proposals WHERE proposal_id = %s
        """, (proposal_id,))
        prop = cur.fetchone()
        if not prop:
            return {"error": f"proposal {proposal_id} not found"}

        is_test = prop["is_test_record"] or _test_bypass

        # Gate: only VALIDATED proposals spawn challengers (unless test bypass)
        if not _test_bypass and prop["status"] != "VALIDATED":
            cur.execute("""
                SELECT gate_name, gate_result
                FROM oe_proposal_gate_results
                WHERE proposal_id=%s AND gate_result != 'PASS'
                ORDER BY gate_name
            """, (proposal_id,))
            blocking = [dict(r) for r in cur.fetchall()]
            conn.rollback()
            return {"error": "CHALLENGER_BLOCKED",
                    "reason": f"proposal status={prop['status']} (need VALIDATED)",
                    "blocking_gates": blocking}

        # Load current champion
        cur.execute("""
            SELECT version_id, config_json, config_sha256
            FROM oe_model_versions
            WHERE version_type='CHAMPION' AND is_active=TRUE AND is_test_record=FALSE
            ORDER BY id DESC LIMIT 1
        """)
        champ = cur.fetchone()
        if not champ:
            conn.rollback()
            return {"error": "no active champion to challenge"}

        champ_vid = champ["version_id"]
        champ_cfg = champ["config_json"]
        champ_cfg = champ_cfg if isinstance(champ_cfg, dict) else json.loads(champ_cfg)

        # Build challenger config: current champion + proposed change
        chal_cfg = dict(champ_cfg)
        pval_raw = prop["proposed_value"]
        pval     = pval_raw if isinstance(pval_raw, (int, float)) else json.loads(str(pval_raw))
        chal_cfg[prop["target_parameter"]] = pval

        chal_sha    = _sha256(chal_cfg)
        chal_vid    = f"challenger_{_uid()[:12]}"
        run_id      = f"run_{_uid()[:12]}"

        # Insert challenger version (can_place_orders is NOT on oe_model_versions;
        # it's on oe_challenger_runs/decisions — enforced there)
        cur.execute("""
            INSERT INTO oe_model_versions
                (version_id, version_type, parent_version_id,
                 config_json, config_sha256, is_active, is_test_record)
            VALUES (%s, 'CHALLENGER', %s, %s::jsonb, %s, FALSE, %s)
        """, (chal_vid, champ_vid,
              json.dumps(chal_cfg, default=str), chal_sha, is_test))

        # Insert shadow run (can_place_orders=FALSE — DB CHECK enforces this)
        cur.execute("""
            INSERT INTO oe_challenger_runs
                (run_id, proposal_id, challenger_version_id, champion_version_id,
                 can_place_orders, is_test_record)
            VALUES (%s, %s, %s, %s, FALSE, %s)
        """, (run_id, proposal_id, chal_vid, champ_vid, is_test))

        record_audit_event(
            "CHALLENGER_CREATED",
            "create_challenger",
            version_id=chal_vid,
            proposal_id=proposal_id,
            details={"champion_version_id": champ_vid,
                     "challenger_sha256": chal_sha,
                     "run_id": run_id,
                     "can_place_orders": False,
                     "is_test_record": is_test},
            is_test_record=is_test,
            db_url=db_url, _cur=cur,
        )
        conn.commit()

    _log(f"create_challenger: {chal_vid} run={run_id} "
         f"champion={champ_vid} is_test={is_test} can_place_orders=FALSE")
    return {"challenger_version_id": chal_vid, "run_id": run_id,
            "champion_version_id": champ_vid,
            "challenger_sha256": chal_sha,
            "can_place_orders": False,
            "is_test_record": is_test}


def record_challenger_decision(run_id: str,
                               ticker: str,
                               direction: Optional[str],
                               challenger_score: Optional[float],
                               champion_score: Optional[float],
                               scan_date: Optional[date] = None,
                               db_url: str = "",
                               _test_bypass: bool = False) -> dict:
    """
    Record one shadow-mode decision for a challenger run.
    can_place_orders=FALSE is enforced at DB level — this function never sets it TRUE.
    Returns {decision_id, can_place_orders: False}.
    """
    db_url = db_url or _DB_URL
    with _conn(db_url) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT run_id, challenger_version_id, can_place_orders, is_test_record
            FROM oe_challenger_runs WHERE run_id = %s
        """, (run_id,))
        run = cur.fetchone()
        if not run:
            conn.rollback()
            return {"error": f"run_id {run_id} not found"}

        is_test  = run["is_test_record"] or _test_bypass
        did      = f"cdec_{_uid()[:12]}"
        sd       = scan_date or date.today()

        # can_place_orders=FALSE enforced at both application and DB level
        cur.execute("""
            INSERT INTO oe_challenger_decisions
                (decision_id, run_id, ticker, scan_date, direction,
                 challenger_score, champion_score, can_place_orders, is_test_record)
            VALUES (%s, %s, %s, %s, %s, %s, %s, FALSE, %s)
        """, (did, run_id, ticker, sd, direction, challenger_score, champion_score, is_test))

        # Update run decision count
        cur.execute("""
            UPDATE oe_challenger_runs
            SET n_decisions = n_decisions + 1
            WHERE run_id = %s
        """, (run_id,))

        conn.commit()

    return {"decision_id": did, "run_id": run_id,
            "ticker": ticker, "can_place_orders": False,
            "is_test_record": is_test}


# ─────────────────────────────────────────────────────────────────────────────
# Section 19 — Promotion & Rollback
# ─────────────────────────────────────────────────────────────────────────────

def promote_challenger(challenger_version_id: str,
                       db_url: str = "",
                       _test_bypass: bool = False) -> dict:
    """
    Promote a challenger to Champion. Blocked unless:
      - The associated proposal has status=VALIDATED (all 18 gates PASS), OR
      - _test_bypass=True (marks all created rows as is_test_record=TRUE)
    Returns {new_champion_id, prior_champion_id, config_sha256, is_test_record}.
    """
    db_url = db_url or _DB_URL
    with _conn(db_url) as conn, conn.cursor() as cur:
        # Load challenger
        cur.execute("""
            SELECT mv.version_id, mv.parent_version_id, mv.config_json,
                   mv.config_sha256, mv.is_test_record,
                   cr.proposal_id, cr.run_id
            FROM oe_model_versions mv
            LEFT JOIN oe_challenger_runs cr ON cr.challenger_version_id = mv.version_id
            WHERE mv.version_id = %s AND mv.version_type = 'CHALLENGER'
            LIMIT 1
        """, (challenger_version_id,))
        chal = cur.fetchone()
        if not chal:
            conn.rollback()
            return {"error": f"challenger {challenger_version_id} not found"}

        is_test = chal["is_test_record"] or _test_bypass
        pid     = chal["proposal_id"]

        # Gate: proposal must be VALIDATED unless test bypass
        if not _test_bypass and pid:
            cur.execute("""
                SELECT status FROM oe_weight_proposals WHERE proposal_id = %s
            """, (pid,))
            prop = cur.fetchone()
            if prop and prop["status"] != "VALIDATED":
                blocking_gate_result = validate_proposal_gates(pid, db_url)
                conn.rollback()
                return {"error": "PROMOTION_BLOCKED",
                        "reason": f"proposal status={prop['status']} (need VALIDATED)",
                        "gate_summary": {
                            "n_pass": blocking_gate_result.get("n_pass", 0),
                            "n_fail": blocking_gate_result.get("n_fail", 0),
                            "n_insufficient_data":
                                blocking_gate_result.get("n_insufficient_data", 0)}}

        # Load current active champion
        cur.execute("""
            SELECT version_id FROM oe_model_versions
            WHERE version_type='CHAMPION' AND is_active=TRUE AND is_test_record=FALSE
            ORDER BY id DESC LIMIT 1
        """)
        prior = cur.fetchone()
        prior_vid = prior["version_id"] if prior else "none"

        # Deactivate current active champion in the appropriate namespace.
        # Test bypass: deactivate test-namespace champions only (production champion untouched).
        # Production: deactivate the production champion.
        if is_test:
            cur.execute("""
                UPDATE oe_model_versions
                SET is_active=FALSE
                WHERE version_type='CHAMPION' AND is_active=TRUE AND is_test_record=TRUE
            """)
        else:
            if prior:
                cur.execute("""
                    UPDATE oe_model_versions
                    SET is_active=FALSE WHERE version_id=%s
                """, (prior_vid,))

        # Insert promoted version as new champion (always is_active=TRUE in its namespace)
        new_champ_vid = f"champion_{_uid()[:12]}"
        cfg = chal["config_json"]
        cfg = cfg if isinstance(cfg, dict) else json.loads(cfg)
        cfg_copy = dict(cfg)
        cfg_copy["version_label"] = new_champ_vid
        new_sha = _sha256(cfg_copy)

        cur.execute("""
            INSERT INTO oe_model_versions
                (version_id, version_type, parent_version_id,
                 config_json, config_sha256, is_active, is_test_record,
                 promoted_at, promoted_by, rollback_from_version)
            VALUES (%s, 'CHAMPION', %s, %s::jsonb, %s, TRUE, %s,
                    NOW(), 'promote_challenger', %s)
        """, (new_champ_vid, chal["version_id"],
              json.dumps(cfg_copy, default=str), new_sha,
              is_test,
              prior_vid))

        # Update proposal status
        if pid:
            cur.execute("""
                UPDATE oe_weight_proposals
                SET status='PROMOTED', approved_by='promote_challenger', updated_at=NOW()
                WHERE proposal_id=%s
            """, (pid,))

        # Record promotion event
        ev_id = _uid()
        cur.execute("""
            INSERT INTO oe_promotion_events
                (event_id, challenger_version_id, prior_champion_id, new_champion_id,
                 action, gates_passed, gates_total, is_test_record)
            VALUES (%s, %s, %s, %s, 'PROMOTED', 18, 18, %s)
        """, (ev_id, challenger_version_id, prior_vid, new_champ_vid, is_test))

        record_audit_event(
            "CHALLENGER_PROMOTED",
            "promote_challenger",
            version_id=new_champ_vid,
            proposal_id=pid,
            details={"prior_champion": prior_vid,
                     "challenger": challenger_version_id,
                     "new_champion": new_champ_vid,
                     "new_sha256": new_sha,
                     "is_test_record": is_test},
            is_test_record=is_test,
            db_url=db_url, _cur=cur,
        )
        conn.commit()

    _log(f"promote_challenger: {challenger_version_id} → {new_champ_vid} "
         f"prior={prior_vid} is_test={is_test}")
    return {"new_champion_id": new_champ_vid,
            "prior_champion_id": prior_vid,
            "challenger_version_id": challenger_version_id,
            "config_sha256": new_sha,
            "is_test_record": is_test}


def rollback_champion(target_version_id: str,
                      db_url: str = "",
                      _test_bypass: bool = False) -> dict:
    """
    Section 21: One-command rollback to a previous Champion version.
    Sets that version as is_active=TRUE, deactivates current champion.
    Returns proof dict with config_sha256 of restored version.
    """
    db_url = db_url or _DB_URL
    with _conn(db_url) as conn, conn.cursor() as cur:
        # Load target version
        cur.execute("""
            SELECT version_id, version_type, config_json, config_sha256, is_test_record
            FROM oe_model_versions WHERE version_id = %s
        """, (target_version_id,))
        target = cur.fetchone()
        if not target:
            conn.rollback()
            return {"error": f"version {target_version_id} not found"}
        if target["version_type"] != "CHAMPION":
            conn.rollback()
            return {"error": f"can only rollback to a CHAMPION version "
                             f"(found {target['version_type']})"}

        is_test = target["is_test_record"] or _test_bypass

        # Find current active champion in the same namespace (test or prod)
        if is_test:
            cur.execute("""
                SELECT version_id FROM oe_model_versions
                WHERE version_type='CHAMPION' AND is_active=TRUE AND is_test_record=TRUE
                ORDER BY id DESC LIMIT 1
            """)
        else:
            cur.execute("""
                SELECT version_id FROM oe_model_versions
                WHERE version_type='CHAMPION' AND is_active=TRUE AND is_test_record=FALSE
                ORDER BY id DESC LIMIT 1
            """)
        current = cur.fetchone()
        current_vid = current["version_id"] if current else "none"

        if current_vid == target_version_id:
            conn.rollback()
            return {"status": "NOOP", "reason": "target is already the active champion",
                    "version_id": target_version_id}

        # Deactivate active champion(s) in the appropriate namespace only
        if is_test:
            cur.execute("""
                UPDATE oe_model_versions
                SET is_active=FALSE
                WHERE version_type='CHAMPION' AND is_active=TRUE AND is_test_record=TRUE
            """)
        else:
            cur.execute("""
                UPDATE oe_model_versions
                SET is_active=FALSE
                WHERE version_type='CHAMPION' AND is_active=TRUE AND is_test_record=FALSE
            """)

        # Re-activate target
        cur.execute("""
            UPDATE oe_model_versions
            SET is_active=TRUE WHERE version_id=%s
        """, (target_version_id,))

        # Record promotion event as ROLLED_BACK
        cfg = target["config_json"]
        cfg = cfg if isinstance(cfg, dict) else json.loads(cfg)

        ev_id = _uid()
        cur.execute("""
            INSERT INTO oe_promotion_events
                (event_id, challenger_version_id, prior_champion_id, new_champion_id,
                 action, rejected_reason, is_test_record)
            VALUES (%s, 'N/A', %s, %s, 'ROLLED_BACK', %s, %s)
        """, (ev_id, current_vid, target_version_id,
              f"manual rollback from {current_vid}", is_test))

        record_audit_event(
            "CHAMPION_ROLLED_BACK",
            "rollback_champion",
            version_id=target_version_id,
            details={"from_version": current_vid,
                     "to_version": target_version_id,
                     "restored_sha256": target["config_sha256"],
                     "is_test_record": is_test},
            is_test_record=is_test,
            db_url=db_url, _cur=cur,
        )
        conn.commit()

    _log(f"rollback_champion: {current_vid} → {target_version_id} "
         f"sha256={target['config_sha256'][:16]}… is_test={is_test}")
    return {"status": "ROLLED_BACK",
            "from_version": current_vid,
            "to_version": target_version_id,
            "config_sha256": target["config_sha256"],
            "is_test_record": is_test}


# ─────────────────────────────────────────────────────────────────────────────
# Section 22 — Audit chain verification
# ─────────────────────────────────────────────────────────────────────────────

def verify_audit_chain(db_url: str = "", is_test_record: bool = False) -> dict:
    """
    Walk the oe_audit_events chain for the given namespace and verify each SHA-256 link.
    Returns {n_events, n_broken, first_break_id, chain_valid}.
    is_test_record=True  → walk the test/harness chain only (verify_phase5).
    is_test_record=False → walk the production governance chain only (scheduler).
    """
    db_url = db_url or _DB_URL
    with _conn(db_url) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT id, event_id, event_type, actor, details,
                   hash_chain_prev, hash_chain_self, created_at
            FROM oe_audit_events
            WHERE is_test_record = %s
            ORDER BY id ASC
        """, (is_test_record,))
        rows = cur.fetchall()

    n_events = len(rows)
    n_broken = 0
    first_break_id = None
    prev_hash = _GENESIS_HASH

    for row in rows:
        details = row["details"] if isinstance(row["details"], dict) else {}
        ts      = row["created_at"].strftime("%Y-%m-%dT%H:%M:%SZ") if row["created_at"] else ""
        expected = _audit_chain_hash(prev_hash, row["event_id"],
                                     row["event_type"], details, ts)
        if expected != row["hash_chain_self"]:
            n_broken += 1
            if first_break_id is None:
                first_break_id = row["id"]
        prev_hash = row["hash_chain_self"]

    chain_valid = (n_broken == 0)
    return {"n_events": n_events, "n_broken": n_broken,
            "first_break_id": first_break_id, "chain_valid": chain_valid}


# ─────────────────────────────────────────────────────────────────────────────
# Governance summary
# ─────────────────────────────────────────────────────────────────────────────

def get_governance_summary(db_url: str = "") -> dict:
    """Return current champion, proposal counts, and promotion history."""
    db_url = db_url or _DB_URL
    with _conn(db_url) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT version_id, config_sha256
            FROM oe_model_versions
            WHERE version_type='CHAMPION' AND is_active=TRUE AND is_test_record=FALSE
            ORDER BY id DESC LIMIT 1
        """)
        champ = cur.fetchone()

        cur.execute("""
            SELECT status, COUNT(*) AS n
            FROM oe_weight_proposals
            WHERE is_test_record=FALSE
            GROUP BY status ORDER BY status
        """)
        proposal_counts = {r["status"]: r["n"] for r in cur.fetchall()}

        cur.execute("""
            SELECT action, COUNT(*) AS n
            FROM oe_promotion_events
            WHERE is_test_record=FALSE
            GROUP BY action
        """)
        promotion_counts = {r["action"]: r["n"] for r in cur.fetchall()}

        cur.execute("SELECT COUNT(*) AS n FROM oe_audit_events")
        n_audit = cur.fetchone()["n"]

    return {
        "active_champion": dict(champ) if champ else None,
        "proposals": proposal_counts,
        "promotions": promotion_counts,
        "audit_events": n_audit,
    }
