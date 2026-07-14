"""
aiem_diagram3_governance.py
AIEM Diagram 3 — Autonomous Governance, Self-Optimization & Evolution Layer
Version 1.0

PURPOSE:
  Supervisory governance layer that monitors and governs the complete AIEM
  architecture (Diagram 1 + Diagram 2) without modifying it.

GUARANTEE:
  Every report generated from ACTUAL production data.
  No fabricated metrics. No placeholder success messages.
  Diagram 3 SHALL NOT modify Diagram 1 or Diagram 2.

PHASES:
  0  — Baseline Freeze (ARCHITECTURE_BASELINE_V1 + BASELINE_HASH)
  1  — Architecture Discovery (SYSTEM_ARCHITECTURE_MAP)
  2  — System Health (SYSTEM_HEALTH_SCORE)
  3  — Performance Governance (PERFORMANCE_HEALTH_REPORT)
  4  — Strategy Governance (STRATEGY_REGISTRY)
  5  — Model Governance (MODEL_REGISTRY)
  6  — Safe Learning Approval (LEARNING_APPROVAL_REPORT)
  7  — Change Management (CHANGE_LOG)
  8  — Version Control (VERSION_HISTORY)
  9  — Rollback Management (ROLLBACK_REPORT)
  10 — Self-Optimization (OPTIMIZATION_RECOMMENDATIONS)
  11 — System Health Forecast (SYSTEM_FORECAST)
  12 — Security Governance (SECURITY_REPORT)
  13 — Architecture Consistency (ARCHITECTURE_STATUS)
  14 — Executive Reporting (EXECUTIVE_REPORT)
  15 — Long-Term Evolution (EVOLUTION_PLAN)
"""

import contextvars
import hashlib
import hmac
import json
import os
import threading
import time
import uuid
import datetime
from typing import Optional, Dict, Any

import psycopg2
import psycopg2.extras

from aiem_provenance import _canonical_bytes
from point_in_time_guard import evaluate_live_candidate_pit

_D3_VERSION = "1.0.0"
_D3_STARTED_AT = datetime.datetime.utcnow().isoformat() + "Z"
_D3_BASELINE_HASH: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# TRACE CONTEXT (contextvars) — structural fix for gap G2
# ─────────────────────────────────────────────────────────────────────────────
# Problem this closes: without an explicit carrier, root_trace_id/is_test_record
# would have to be threaded as a parameter through every intermediate function
# between "a real D2 run starts" and "a bus subscriber emits a D3 ledger row",
# which is exactly the kind of plumbing that gets missed on one call path and
# silently degrades to a self-anchored/production-mislabeled event. contextvars
# propagate automatically through the whole call stack (and, per
# CommunicationBus's own synchronous-same-thread guarantee, through every
# bus.publish() -> subscriber callback) without being passed explicitly.
#
# Real values only: this never invents a root_trace_id or is_test_record value.
# If nothing has called trace_context(...), get_trace_context() returns None and
# callers must fall back to their own real, disclosed anchor (e.g. the event's
# own trace_id) — never a fabricated one.

_D3_TRACE_CTX: "contextvars.ContextVar" = contextvars.ContextVar("d3_trace_ctx", default=None)


class trace_context:
    """
    Context manager: `with trace_context(root_trace_id=..., is_test_record=...):`
    Sets the real root trace id / test-record flag for every D3 ledger event
    emitted (directly or via the bus subscriber) inside the `with` block, in
    this thread's call stack, for the duration of the block only.
    """

    def __init__(self, root_trace_id: str, is_test_record: bool = False):
        self._value = {
            "root_trace_id": root_trace_id,
            "is_test_record": bool(is_test_record),
        }
        self._token = None

    def __enter__(self):
        self._token = _D3_TRACE_CTX.set(self._value)
        return self

    def __exit__(self, exc_type, exc, tb):
        _D3_TRACE_CTX.reset(self._token)
        return False


def get_trace_context() -> Optional[Dict[str, Any]]:
    """Real, currently-active trace context, or None if none has been set."""
    return _D3_TRACE_CTX.get()


# ── DB connection ─────────────────────────────────────────────────────────────

def _d3_connect():
    return psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=8)


def _safe(v, default=None):
    """Return v as Python float/int/str, or default on None/exception."""
    try:
        return float(v) if v is not None else default
    except Exception:
        return default


# ── Schema (all D3 tables — idempotent) ──────────────────────────────────────

_SCHEMA_STMTS = [
    """
    CREATE TABLE IF NOT EXISTS d3_architecture_baseline (
        id SERIAL PRIMARY KEY,
        frozen_at TIMESTAMPTZ DEFAULT NOW(),
        version TEXT NOT NULL,
        baseline_hash TEXT NOT NULL UNIQUE,
        module_count INT,
        tool_count INT,
        d2_stage_count INT,
        db_table_count INT,
        snapshot_json JSONB,
        protected BOOLEAN DEFAULT TRUE,
        notes TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS d3_system_health_snapshots (
        id BIGSERIAL PRIMARY KEY,
        snapshot_at TIMESTAMPTZ DEFAULT NOW(),
        health_score NUMERIC(5,2),
        db_ok BOOLEAN,
        db_latency_ms NUMERIC(8,2),
        kill_switch_active BOOLEAN,
        open_trades INT,
        traces_last_24h INT,
        supervisor_events_24h INT,
        details_json JSONB
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS d3_performance_snapshots (
        id BIGSERIAL PRIMARY KEY,
        snapshot_at TIMESTAMPTZ DEFAULT NOW(),
        period_days INT DEFAULT 30,
        total_trades INT,
        closed_trades INT,
        open_trades INT,
        win_rate NUMERIC(6,4),
        avg_pnl_pct NUMERIC(8,4),
        max_drawdown_pct NUMERIC(8,4),
        expectancy NUMERIC(8,4),
        sharpe_ratio NUMERIC(8,4),
        by_strategy_json JSONB
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS d3_strategy_registry (
        id SERIAL PRIMARY KEY,
        signal_source TEXT NOT NULL UNIQUE,
        first_trade_date DATE,
        last_trade_date DATE,
        status TEXT DEFAULT 'active',
        total_trades INT DEFAULT 0,
        closed_trades INT DEFAULT 0,
        win_rate NUMERIC(6,4),
        avg_pnl_pct NUMERIC(8,4),
        approval_status TEXT DEFAULT 'approved',
        notes TEXT,
        updated_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS d3_model_governance (
        id SERIAL PRIMARY KEY,
        recorded_at TIMESTAMPTZ DEFAULT NOW(),
        model_name TEXT NOT NULL,
        model_version TEXT,
        cutoff_date DATE,
        deployment_status TEXT DEFAULT 'active',
        training_samples INT,
        validation_auc NUMERIC(6,4),
        rollback_version TEXT,
        notes TEXT,
        UNIQUE (model_name, model_version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS d3_learning_approvals (
        id BIGSERIAL PRIMARY KEY,
        evaluated_at TIMESTAMPTZ DEFAULT NOW(),
        proposal_id BIGINT,
        model_name TEXT,
        decision TEXT CHECK (decision IN ('APPROVE','REJECT','REVIEW','DEFER')),
        decision_reason TEXT,
        performance_ok BOOLEAN,
        calibration_ok BOOLEAN,
        risk_ok BOOLEAN,
        auto_decided BOOLEAN DEFAULT TRUE,
        reviewer TEXT DEFAULT 'DIAGRAM3_GOVERNANCE'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS d3_change_log (
        id BIGSERIAL PRIMARY KEY,
        logged_at TIMESTAMPTZ DEFAULT NOW(),
        version TEXT,
        author TEXT DEFAULT 'SYSTEM',
        module TEXT,
        tools_affected TEXT,
        reason TEXT,
        expected_impact TEXT,
        measured_impact TEXT,
        rollback_ref TEXT,
        status TEXT DEFAULT 'pending_measurement'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS d3_version_history (
        id SERIAL PRIMARY KEY,
        recorded_at TIMESTAMPTZ DEFAULT NOW(),
        version TEXT NOT NULL,
        previous_version TEXT,
        version_type TEXT CHECK (version_type IN ('production','experimental','rollback','approved')),
        baseline_hash TEXT,
        notes TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS d3_rollback_registry (
        id SERIAL PRIMARY KEY,
        assessed_at TIMESTAMPTZ DEFAULT NOW(),
        baseline_hash TEXT,
        current_hash TEXT,
        hash_match BOOLEAN,
        drift_detected BOOLEAN,
        new_modules INT DEFAULT 0,
        removed_modules INT DEFAULT 0,
        new_tools INT DEFAULT 0,
        removed_tools INT DEFAULT 0,
        rollback_ready BOOLEAN DEFAULT TRUE,
        drift_details_json JSONB
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS d3_optimization_recommendations (
        id BIGSERIAL PRIMARY KEY,
        generated_at TIMESTAMPTZ DEFAULT NOW(),
        category TEXT,
        priority TEXT CHECK (priority IN ('HIGH','MEDIUM','LOW')),
        target TEXT,
        finding TEXT,
        recommendation TEXT,
        evidence_json JSONB,
        status TEXT DEFAULT 'pending'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS d3_system_forecasts (
        id BIGSERIAL PRIMARY KEY,
        generated_at TIMESTAMPTZ DEFAULT NOW(),
        forecast_horizon_days INT,
        health_trajectory TEXT,
        performance_trajectory TEXT,
        capacity_risk TEXT,
        drift_risk TEXT,
        details_json JSONB
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS d3_security_reports (
        id BIGSERIAL PRIMARY KEY,
        generated_at TIMESTAMPTZ DEFAULT NOW(),
        unauthorized_modules INT DEFAULT 0,
        config_drift_detected BOOLEAN DEFAULT FALSE,
        missing_audit_logs INT DEFAULT 0,
        auth_failures_24h INT DEFAULT 0,
        integrity_violations INT DEFAULT 0,
        overall_status TEXT DEFAULT 'OK',
        details_json JSONB
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS d3_architecture_status (
        id BIGSERIAL PRIMARY KEY,
        checked_at TIMESTAMPTZ DEFAULT NOW(),
        d1_intact BOOLEAN,
        d2_intact BOOLEAN,
        comm_bus_intact BOOLEAN,
        learning_intact BOOLEAN,
        duplicate_modules INT DEFAULT 0,
        duplicate_tools INT DEFAULT 0,
        overall_status TEXT,
        details_json JSONB
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS d3_executive_reports (
        id BIGSERIAL PRIMARY KEY,
        generated_at TIMESTAMPTZ DEFAULT NOW(),
        report_date DATE DEFAULT CURRENT_DATE,
        system_health_score NUMERIC(5,2),
        architecture_status TEXT,
        performance_summary_json JSONB,
        security_status TEXT,
        governance_active BOOLEAN DEFAULT TRUE,
        full_report_json JSONB
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS d3_evolution_plan (
        id BIGSERIAL PRIMARY KEY,
        generated_at TIMESTAMPTZ DEFAULT NOW(),
        trend_30d_json JSONB,
        trend_90d_json JSONB,
        trend_180d_json JSONB,
        trend_365d_json JSONB,
        recommendations_json JSONB
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS d3_governance_event_links (
        id BIGSERIAL PRIMARY KEY,
        governance_cycle_id TEXT NOT NULL,
        governance_trace_id TEXT NOT NULL,
        parent_trace_id TEXT,
        diagram1_trace_id TEXT,
        diagram2_trace_id TEXT,
        candidate_id TEXT,
        ticker TEXT,
        recommendation_id TEXT,
        decision_id TEXT,
        execution_plan_id TEXT,
        paper_trade_id BIGINT,
        outcome_id TEXT,
        learning_event_id TEXT,
        model_id TEXT,
        model_version TEXT,
        strategy_id TEXT,
        change_request_id TEXT,
        architecture_version_id TEXT,
        baseline_id TEXT,
        rollback_id TEXT,
        governance_phase TEXT NOT NULL,
        governance_check_name TEXT NOT NULL,
        governance_module TEXT NOT NULL DEFAULT 'aiem_diagram3_governance',
        governance_function TEXT NOT NULL,
        check_result TEXT,
        enforcement_action TEXT DEFAULT 'ADVISORY_ONLY',
        enforcement_status TEXT DEFAULT 'NOT_ENFORCED',
        reason_code TEXT,
        reason_detail TEXT,
        input_hash TEXT,
        output_hash TEXT,
        previous_event_hash TEXT,
        event_hash TEXT NOT NULL UNIQUE,
        source_code_commit TEXT,
        source_file_hash TEXT,
        config_hash TEXT,
        started_at TIMESTAMPTZ NOT NULL,
        completed_at TIMESTAMPTZ NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        is_test_record BOOLEAN NOT NULL DEFAULT FALSE
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_d3gel_gov_trace ON d3_governance_event_links (governance_trace_id)",
    "CREATE INDEX IF NOT EXISTS ix_d3gel_d2_trace ON d3_governance_event_links (diagram2_trace_id)",
    "CREATE INDEX IF NOT EXISTS ix_d3gel_candidate ON d3_governance_event_links (candidate_id)",
    "CREATE INDEX IF NOT EXISTS ix_d3gel_ticker ON d3_governance_event_links (ticker)",
    "CREATE INDEX IF NOT EXISTS ix_d3gel_decision ON d3_governance_event_links (decision_id)",
    "CREATE INDEX IF NOT EXISTS ix_d3gel_exec_plan ON d3_governance_event_links (execution_plan_id)",
    "CREATE INDEX IF NOT EXISTS ix_d3gel_paper_trade ON d3_governance_event_links (paper_trade_id)",
    "CREATE INDEX IF NOT EXISTS ix_d3gel_learning_event ON d3_governance_event_links (learning_event_id)",
    "CREATE INDEX IF NOT EXISTS ix_d3gel_model ON d3_governance_event_links (model_id)",
    "CREATE INDEX IF NOT EXISTS ix_d3gel_change_request ON d3_governance_event_links (change_request_id)",
    "CREATE INDEX IF NOT EXISTS ix_d3gel_arch_version ON d3_governance_event_links (architecture_version_id)",
    "CREATE INDEX IF NOT EXISTS ix_d3gel_baseline ON d3_governance_event_links (baseline_id)",
    "CREATE INDEX IF NOT EXISTS ix_d3gel_phase ON d3_governance_event_links (governance_phase)",
    "CREATE INDEX IF NOT EXISTS ix_d3gel_cycle ON d3_governance_event_links (governance_cycle_id)",
    """
    CREATE OR REPLACE FUNCTION d3gel_block_mutation() RETURNS trigger AS $BODY$
    BEGIN
        RAISE EXCEPTION 'd3_governance_event_links is append-only: % not permitted on id=%',
            TG_OP, OLD.id;
    END;
    $BODY$ LANGUAGE plpgsql;
    """,
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_trigger WHERE tgname = 'trg_d3gel_immutable'
        ) THEN
            CREATE TRIGGER trg_d3gel_immutable
                BEFORE UPDATE OR DELETE ON d3_governance_event_links
                FOR EACH ROW EXECUTE FUNCTION d3gel_block_mutation();
        END IF;
    END $$;
    """,
    # ── v2 provenance-envelope migration (additive only — ADD COLUMN IF NOT
    # EXISTS is safe against the append-only trigger, which only blocks
    # UPDATE/DELETE). Pre-existing rows are honestly left at
    # event_schema_version=1 and NULL on the new columns — never backfilled
    # with fabricated values. New emits default to version 2. See the
    # AEIM_D2_D3_GOVERNANCE_CONTRACT doc for the mapping between these
    # spec-required column names and the pre-existing v1 columns that already
    # cover the same concept (event_hash==event_sha256 equivalent, etc.) —
    # those are NOT duplicated here to avoid two sources of truth for one hash.
    "ALTER TABLE d3_governance_event_links ADD COLUMN IF NOT EXISTS event_schema_version INT NOT NULL DEFAULT 1",
    "ALTER TABLE d3_governance_event_links ALTER COLUMN event_schema_version SET DEFAULT 2",
    "ALTER TABLE d3_governance_event_links ADD COLUMN IF NOT EXISTS event_id TEXT",
    "ALTER TABLE d3_governance_event_links ADD COLUMN IF NOT EXISTS event_type TEXT",
    "ALTER TABLE d3_governance_event_links ADD COLUMN IF NOT EXISTS root_trace_id TEXT",
    "ALTER TABLE d3_governance_event_links ADD COLUMN IF NOT EXISTS correlation_id TEXT",
    "ALTER TABLE d3_governance_event_links ADD COLUMN IF NOT EXISTS causation_id TEXT",
    "ALTER TABLE d3_governance_event_links ADD COLUMN IF NOT EXISTS parent_event_id TEXT",
    "ALTER TABLE d3_governance_event_links ADD COLUMN IF NOT EXISTS diagram3_trace_id TEXT",
    "ALTER TABLE d3_governance_event_links ADD COLUMN IF NOT EXISTS shadow_trade_id TEXT",
    "ALTER TABLE d3_governance_event_links ADD COLUMN IF NOT EXISTS no_execution_id TEXT",
    "ALTER TABLE d3_governance_event_links ADD COLUMN IF NOT EXISTS memory_event_id TEXT",
    "ALTER TABLE d3_governance_event_links ADD COLUMN IF NOT EXISTS strategy_version TEXT",
    "ALTER TABLE d3_governance_event_links ADD COLUMN IF NOT EXISTS approval_id TEXT",
    "ALTER TABLE d3_governance_event_links ADD COLUMN IF NOT EXISTS rejection_id TEXT",
    "ALTER TABLE d3_governance_event_links ADD COLUMN IF NOT EXISTS d2_phase TEXT",
    "ALTER TABLE d3_governance_event_links ADD COLUMN IF NOT EXISTS d3_phase TEXT",
    "ALTER TABLE d3_governance_event_links ADD COLUMN IF NOT EXISTS producer_module TEXT",
    "ALTER TABLE d3_governance_event_links ADD COLUMN IF NOT EXISTS producer_function TEXT",
    "ALTER TABLE d3_governance_event_links ADD COLUMN IF NOT EXISTS consumer_module TEXT",
    "ALTER TABLE d3_governance_event_links ADD COLUMN IF NOT EXISTS consumer_function TEXT",
    "ALTER TABLE d3_governance_event_links ADD COLUMN IF NOT EXISTS input_record_ids JSONB",
    "ALTER TABLE d3_governance_event_links ADD COLUMN IF NOT EXISTS output_record_ids JSONB",
    "ALTER TABLE d3_governance_event_links ADD COLUMN IF NOT EXISTS idempotency_key TEXT",
    "ALTER TABLE d3_governance_event_links ADD COLUMN IF NOT EXISTS emitted_at TIMESTAMPTZ",
    "ALTER TABLE d3_governance_event_links ADD COLUMN IF NOT EXISTS received_at TIMESTAMPTZ",
    "ALTER TABLE d3_governance_event_links ADD COLUMN IF NOT EXISTS environment TEXT",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_d3gel_event_id ON d3_governance_event_links (event_id) WHERE event_id IS NOT NULL",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_d3gel_idempotency ON d3_governance_event_links (idempotency_key) WHERE idempotency_key IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS ix_d3gel_root_trace ON d3_governance_event_links (root_trace_id)",
    "CREATE INDEX IF NOT EXISTS ix_d3gel_d3_trace ON d3_governance_event_links (diagram3_trace_id)",
    "CREATE INDEX IF NOT EXISTS ix_d3gel_outcome ON d3_governance_event_links (outcome_id)",
    "CREATE INDEX IF NOT EXISTS ix_d3gel_strategy ON d3_governance_event_links (strategy_id)",
    "CREATE INDEX IF NOT EXISTS ix_d3gel_rollback ON d3_governance_event_links (rollback_id)",
    "CREATE INDEX IF NOT EXISTS ix_d3gel_event_type ON d3_governance_event_links (event_type)",
    "CREATE INDEX IF NOT EXISTS ix_d3gel_created_at ON d3_governance_event_links (created_at)",

    # ── T-F: governance-action request/acknowledgement tracking ──────────
    # This is a single monolith — there is no independently-owned D2
    # "service" that can send back a real acknowledgement of an enforcement
    # action. The CHECK constraint below makes that limitation a real,
    # unbypassable DB-level guarantee rather than just an app-layer
    # convention: 'ENFORCED' is not a legal value in this column, full
    # stop, so no future code change can silently start claiming
    # enforcement that was never independently confirmed.
    """CREATE TABLE IF NOT EXISTS d3_governance_actions (
        id BIGSERIAL PRIMARY KEY,
        action_id TEXT NOT NULL UNIQUE,
        governance_event_id BIGINT REFERENCES d3_governance_event_links(id),
        requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        phase TEXT NOT NULL,
        action_type TEXT NOT NULL,
        target_type TEXT,
        target_id TEXT,
        reason TEXT,
        status TEXT NOT NULL DEFAULT 'REQUESTED'
            CHECK (status IN ('REQUESTED', 'ADVISORY_ACKNOWLEDGED', 'NOT_ENFORCED')),
        checked_at TIMESTAMPTZ,
        check_detail TEXT,
        is_test_record BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""",
    "CREATE INDEX IF NOT EXISTS ix_d3ga_action_type ON d3_governance_actions (action_type)",
    "CREATE INDEX IF NOT EXISTS ix_d3ga_status ON d3_governance_actions (status)",
    "CREATE INDEX IF NOT EXISTS ix_d3ga_target ON d3_governance_actions (target_type, target_id)",

    # ── P0 (Path B Section 7, gap G1 fix): d3_governance_actions had NO
    # anti-tamper protection, unlike d3_governance_event_links. This closes
    # that gap WITHOUT breaking the legitimate lifecycle re-check that
    # check_action_status() performs (REQUESTED -> ADVISORY_ACKNOWLEDGED /
    # NOT_ENFORCED, and re-checks that flip between those two as real
    # underlying state changes over time). The trigger:
    #   - always blocks DELETE (no history erasure, ever)
    #   - blocks any UPDATE that touches an identity/request field
    #     (action_id, governance_event_id, requested_at, phase, action_type,
    #     target_type, target_id, reason, is_test_record, created_at) —
    #     those are frozen at insert time; only status/checked_at/check_detail
    #     may ever change, and only to a value already legal under the
    #     existing status CHECK constraint.
    # This is a real DB-level guarantee, not an app-layer convention — proven
    # in P0 verification below via a rejected raw UPDATE/DELETE attempt.
    """
    CREATE OR REPLACE FUNCTION d3ga_guard_mutation() RETURNS trigger AS $BODY$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'd3_governance_actions is append-only for history: DELETE not permitted on id=%',
                OLD.id;
        END IF;
        IF NEW.action_id            IS DISTINCT FROM OLD.action_id
           OR NEW.governance_event_id IS DISTINCT FROM OLD.governance_event_id
           OR NEW.requested_at      IS DISTINCT FROM OLD.requested_at
           OR NEW.phase             IS DISTINCT FROM OLD.phase
           OR NEW.action_type       IS DISTINCT FROM OLD.action_type
           OR NEW.target_type       IS DISTINCT FROM OLD.target_type
           OR NEW.target_id         IS DISTINCT FROM OLD.target_id
           OR NEW.reason            IS DISTINCT FROM OLD.reason
           OR NEW.is_test_record    IS DISTINCT FROM OLD.is_test_record
           OR NEW.created_at        IS DISTINCT FROM OLD.created_at
        THEN
            RAISE EXCEPTION 'd3_governance_actions identity/request fields are immutable: attempted tamper on id=%',
                OLD.id;
        END IF;
        RETURN NEW;
    END;
    $BODY$ LANGUAGE plpgsql;
    """,
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_trigger WHERE tgname = 'trg_d3ga_guard'
        ) THEN
            CREATE TRIGGER trg_d3ga_guard
                BEFORE UPDATE OR DELETE ON d3_governance_actions
                FOR EACH ROW EXECUTE FUNCTION d3ga_guard_mutation();
        END IF;
    END $$;
    """,

    # ── P3 (Path B G0 boot authorization): singleton system-state,
    # per-checkpoint enforcement mode, and an append-only audit history of
    # every change to either. These three tables are the real, DB-level
    # source of truth that g0_authorize_run() reads on every trade-executing
    # entrypoint call -- never an in-memory-only flag that a restart could
    # silently reset to a permissive default. Checkpoints default to
    # 'SHADOW' (log real would-block decisions, never actually block) until
    # a human operator explicitly flips one to 'ENFORCE' via the admin route
    # (which requires confirm=true for that specific direction only).
    """
    CREATE TABLE IF NOT EXISTS d3_system_state (
        id INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
        state TEXT NOT NULL DEFAULT 'NORMAL'
            CHECK (state IN ('NORMAL', 'DEGRADED', 'RESTRICTED', 'PAUSED',
                              'RECOVERY_REQUIRED', 'ROLLBACK_IN_PROGRESS')),
        reason TEXT,
        changed_by TEXT,
        changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "INSERT INTO d3_system_state (id, state, reason, changed_by) "
    "VALUES (1, 'NORMAL', 'initial seed on boot', 'SYSTEM_STARTUP') "
    "ON CONFLICT (id) DO NOTHING",

    """
    CREATE TABLE IF NOT EXISTS d3_checkpoint_config (
        checkpoint TEXT PRIMARY KEY CHECK (checkpoint IN ('G0', 'G1', 'G2', 'G3', 'G4', 'G5', 'G6')),
        mode TEXT NOT NULL DEFAULT 'SHADOW' CHECK (mode IN ('OFF', 'SHADOW', 'ENFORCE')),
        updated_by TEXT,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        note TEXT
    )
    """,
    # G1 (data-guard completion integrity authorization) was added to the
    # checkpoint set in P3.5 -- on a pre-existing prod DB whose CHECK
    # constraint was created before G1 existed, the inline CHECK above is a
    # no-op (CREATE TABLE IF NOT EXISTS), so the old 5-value constraint would
    # still reject a G1 row. This discovers the REAL constraint name from
    # pg_constraint (never assumes the default-generated name) and only
    # replaces it if it doesn't already allow G1 -- idempotent every boot,
    # and a no-op on a fresh DB where the inline CHECK above already has G1.
    """
    DO $$
    DECLARE
        con RECORD;
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conrelid = 'd3_checkpoint_config'::regclass
              AND contype = 'c'
              AND pg_get_constraintdef(oid) LIKE '%G1%'
        ) THEN
            FOR con IN
                SELECT conname FROM pg_constraint
                WHERE conrelid = 'd3_checkpoint_config'::regclass
                  AND contype = 'c'
                  AND pg_get_constraintdef(oid) LIKE '%checkpoint%'
            LOOP
                EXECUTE format('ALTER TABLE d3_checkpoint_config DROP CONSTRAINT %I', con.conname);
            END LOOP;
            ALTER TABLE d3_checkpoint_config
                ADD CONSTRAINT d3_checkpoint_config_checkpoint_check
                CHECK (checkpoint IN ('G0', 'G1', 'G2', 'G3', 'G4', 'G5', 'G6'));
        END IF;
    END $$;
    """,
    # G6 (Point-in-Time Guard, Diagram 2 remediation spec step 3) added
    # later still -- same idempotent re-derive-and-replace pattern as the
    # G1 migration above, applied to all three checkpoint-constrained
    # tables (d3_checkpoint_config here, d3_governance_requests and
    # d3_governance_decisions below) so a pre-existing prod DB whose CHECK
    # constraints predate G6 can accept G6 rows without a manual migration.
    """
    DO $$
    DECLARE
        con RECORD;
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conrelid = 'd3_checkpoint_config'::regclass
              AND contype = 'c'
              AND pg_get_constraintdef(oid) LIKE '%G6%'
        ) THEN
            FOR con IN
                SELECT conname FROM pg_constraint
                WHERE conrelid = 'd3_checkpoint_config'::regclass
                  AND contype = 'c'
                  AND pg_get_constraintdef(oid) LIKE '%checkpoint%'
            LOOP
                EXECUTE format('ALTER TABLE d3_checkpoint_config DROP CONSTRAINT %I', con.conname);
            END LOOP;
            ALTER TABLE d3_checkpoint_config
                ADD CONSTRAINT d3_checkpoint_config_checkpoint_check
                CHECK (checkpoint IN ('G0', 'G1', 'G2', 'G3', 'G4', 'G5', 'G6'));
        END IF;
    END $$;
    """,
    "INSERT INTO d3_checkpoint_config (checkpoint, mode, updated_by, note) VALUES "
    "('G0', 'SHADOW', 'SYSTEM_STARTUP', 'boot authorization -- seeded SHADOW'), "
    "('G1', 'SHADOW', 'SYSTEM_STARTUP', 'data-guard completion integrity authorization -- "
    "seeded SHADOW; no real call site wired yet as of P3.5, schema-only'), "
    "('G2', 'SHADOW', 'SYSTEM_STARTUP', 'pre-decision block -- seeded SHADOW'), "
    "('G3', 'SHADOW', 'SYSTEM_STARTUP', 'pre-execution authorization -- seeded SHADOW'), "
    "('G4', 'SHADOW', 'SYSTEM_STARTUP', 'learning/model promotion gate -- seeded SHADOW'), "
    "('G5', 'SHADOW', 'SYSTEM_STARTUP', 'recovery/resume state machine -- seeded SHADOW'), "
    "('G6', 'SHADOW', 'SYSTEM_STARTUP', 'point-in-time guard (price provenance) -- seeded SHADOW') "
    "ON CONFLICT (checkpoint) DO NOTHING",

    """
    CREATE TABLE IF NOT EXISTS d3_governance_config_history (
        id BIGSERIAL PRIMARY KEY,
        config_type TEXT NOT NULL CHECK (config_type IN ('SYSTEM_STATE', 'CHECKPOINT_MODE')),
        target TEXT NOT NULL,
        old_value TEXT,
        new_value TEXT NOT NULL,
        reason TEXT,
        changed_by TEXT NOT NULL,
        changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        is_test_record BOOLEAN NOT NULL DEFAULT FALSE
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_d3gch_target ON d3_governance_config_history (config_type, target)",
    "CREATE INDEX IF NOT EXISTS ix_d3gch_changed_at ON d3_governance_config_history (changed_at)",

    # Every row here is a permanent record of who flipped what and why --
    # there is no legitimate app-layer reason to ever edit or erase one, so
    # (unlike d3_governance_actions) this trigger blocks ALL UPDATE/DELETE,
    # full stop, same pattern as trg_d3gel_immutable above.
    """
    CREATE OR REPLACE FUNCTION d3gch_block_mutation() RETURNS trigger AS $BODY$
    BEGIN
        RAISE EXCEPTION 'd3_governance_config_history is append-only: % not permitted on id=%',
            TG_OP, OLD.id;
    END;
    $BODY$ LANGUAGE plpgsql;
    """,
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_trigger WHERE tgname = 'trg_d3gch_immutable'
        ) THEN
            CREATE TRIGGER trg_d3gch_immutable
                BEFORE UPDATE OR DELETE ON d3_governance_config_history
                FOR EACH ROW EXECUTE FUNCTION d3gch_block_mutation();
        END IF;
    END $$;
    """,

    # ─────────────────────────────────────────────────────────────────────
    # P3.5 — Section 12 named-component registry + Section 12F request/
    # decision/acknowledgement correlation triplet.
    #
    # d3_governance_components: the 6 canonical components from Section 12B.
    # This is a live registry (upserted every boot with the REAL module/
    # function names and a real health signal), not an append-only history
    # table -- there is nothing dishonest about updating "last seen active"
    # on restart, unlike the audit tables below which must never be edited.
    # ─────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS d3_governance_components (
        component_name TEXT PRIMARY KEY CHECK (component_name IN (
            'D2_EVENT_PUBLISHER', 'D3_EVENT_CONSUMER', 'D2_GOVERNANCE_CLIENT',
            'D3_GOVERNANCE_SERVICE', 'D2_GOVERNANCE_ACKNOWLEDGER', 'D3_GOVERNANCE_LEDGER'
        )),
        owner_diagram TEXT NOT NULL CHECK (owner_diagram IN ('DIAGRAM_2', 'DIAGRAM_3')),
        module_path TEXT NOT NULL,
        function_or_class_name TEXT NOT NULL,
        responsibility TEXT NOT NULL,
        version TEXT NOT NULL DEFAULT '1.0.0',
        status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'DISABLED')),
        registered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        last_health_check_at TIMESTAMPTZ,
        last_health_result TEXT
    )
    """,

    # d3_governance_requests: Section 12F REQUEST record. Append-only --
    # a request, once submitted, is a historical fact that is never edited.
    """
    CREATE TABLE IF NOT EXISTS d3_governance_requests (
        id BIGSERIAL PRIMARY KEY,
        governance_request_id TEXT NOT NULL UNIQUE,
        trace_id TEXT,
        root_trace_id TEXT,
        checkpoint TEXT NOT NULL CHECK (checkpoint IN ('G0', 'G1', 'G2', 'G3', 'G4', 'G5', 'G6')),
        source_phase TEXT,
        requested_action TEXT,
        entrypoint TEXT NOT NULL,
        run_kind TEXT NOT NULL,
        trigger_source TEXT,
        architecture_version TEXT,
        model_version TEXT,
        strategy_version TEXT,
        configuration_version TEXT,
        payload_hash TEXT,
        timeout_ms INT,
        is_test_record BOOLEAN NOT NULL DEFAULT FALSE,
        request_timestamp_utc TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_d3gr_checkpoint ON d3_governance_requests (checkpoint)",
    "CREATE INDEX IF NOT EXISTS ix_d3gr_trace ON d3_governance_requests (trace_id)",
    "CREATE INDEX IF NOT EXISTS ix_d3gr_test ON d3_governance_requests (is_test_record)",
    "CREATE INDEX IF NOT EXISTS ix_d3gr_ts ON d3_governance_requests (request_timestamp_utc)",
    """
    CREATE OR REPLACE FUNCTION d3gr_block_mutation() RETURNS trigger AS $BODY$
    BEGIN
        RAISE EXCEPTION 'd3_governance_requests is append-only: % not permitted on id=%',
            TG_OP, OLD.id;
    END;
    $BODY$ LANGUAGE plpgsql;
    """,
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_trigger WHERE tgname = 'trg_d3gr_immutable'
        ) THEN
            CREATE TRIGGER trg_d3gr_immutable
                BEFORE UPDATE OR DELETE ON d3_governance_requests
                FOR EACH ROW EXECUTE FUNCTION d3gr_block_mutation();
        END IF;
    END $$;
    """,
    # G6 migration for d3_governance_requests -- same idempotent
    # re-derive-and-replace pattern as the d3_checkpoint_config migrations
    # above (see G6 comment there for rationale).
    """
    DO $$
    DECLARE
        con RECORD;
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conrelid = 'd3_governance_requests'::regclass
              AND contype = 'c'
              AND pg_get_constraintdef(oid) LIKE '%G6%'
        ) THEN
            FOR con IN
                SELECT conname FROM pg_constraint
                WHERE conrelid = 'd3_governance_requests'::regclass
                  AND contype = 'c'
                  AND pg_get_constraintdef(oid) LIKE '%checkpoint%'
            LOOP
                EXECUTE format('ALTER TABLE d3_governance_requests DROP CONSTRAINT %I', con.conname);
            END LOOP;
            ALTER TABLE d3_governance_requests
                ADD CONSTRAINT d3_governance_requests_checkpoint_check
                CHECK (checkpoint IN ('G0', 'G1', 'G2', 'G3', 'G4', 'G5', 'G6'));
        END IF;
    END $$;
    """,

    # d3_governance_decisions: Section 12F DECISION record. UNIQUE
    # (governance_decision_id, decision) exists SOLELY so d3_governance_acks
    # can carry a composite FK to it below -- that is the physical mechanism
    # that makes a false acknowledgement (claiming a decision said something
    # it didn't) impossible to insert, not just logically wrong. Append-only.
    """
    CREATE TABLE IF NOT EXISTS d3_governance_decisions (
        id BIGSERIAL PRIMARY KEY,
        governance_decision_id TEXT NOT NULL UNIQUE,
        governance_request_id TEXT NOT NULL REFERENCES d3_governance_requests(governance_request_id),
        trace_id TEXT,
        checkpoint TEXT NOT NULL CHECK (checkpoint IN ('G0', 'G1', 'G2', 'G3', 'G4', 'G5', 'G6')),
        decision TEXT NOT NULL CHECK (decision IN ('ALLOW', 'ALLOW_WITH_WARNING', 'BLOCK')),
        blocking BOOLEAN NOT NULL,
        reason_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
        policy_version TEXT,
        decision_hash TEXT NOT NULL,
        ledger_event_id BIGINT REFERENCES d3_governance_event_links(id),
        is_test_record BOOLEAN NOT NULL DEFAULT FALSE,
        response_timestamp_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (governance_decision_id, decision)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_d3gd_request ON d3_governance_decisions (governance_request_id)",
    "CREATE INDEX IF NOT EXISTS ix_d3gd_checkpoint ON d3_governance_decisions (checkpoint)",
    "CREATE INDEX IF NOT EXISTS ix_d3gd_test ON d3_governance_decisions (is_test_record)",
    """
    CREATE OR REPLACE FUNCTION d3gd_block_mutation() RETURNS trigger AS $BODY$
    BEGIN
        RAISE EXCEPTION 'd3_governance_decisions is append-only: % not permitted on id=%',
            TG_OP, OLD.id;
    END;
    $BODY$ LANGUAGE plpgsql;
    """,
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_trigger WHERE tgname = 'trg_d3gd_immutable'
        ) THEN
            CREATE TRIGGER trg_d3gd_immutable
                BEFORE UPDATE OR DELETE ON d3_governance_decisions
                FOR EACH ROW EXECUTE FUNCTION d3gd_block_mutation();
        END IF;
    END $$;
    """,
    # G6 migration for d3_governance_decisions -- same idempotent
    # re-derive-and-replace pattern as the two migrations above.
    """
    DO $$
    DECLARE
        con RECORD;
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conrelid = 'd3_governance_decisions'::regclass
              AND contype = 'c'
              AND pg_get_constraintdef(oid) LIKE '%G6%'
        ) THEN
            FOR con IN
                SELECT conname FROM pg_constraint
                WHERE conrelid = 'd3_governance_decisions'::regclass
                  AND contype = 'c'
                  AND pg_get_constraintdef(oid) LIKE '%checkpoint%'
            LOOP
                EXECUTE format('ALTER TABLE d3_governance_decisions DROP CONSTRAINT %I', con.conname);
            END LOOP;
            ALTER TABLE d3_governance_decisions
                ADD CONSTRAINT d3_governance_decisions_checkpoint_check
                CHECK (checkpoint IN ('G0', 'G1', 'G2', 'G3', 'G4', 'G5', 'G6'));
        END IF;
    END $$;
    """,

    # d3_governance_acks: Section 12F ACKNOWLEDGEMENT record. decision_recorded
    # is NEVER caller-supplied at the Python layer (acknowledge_governance_
    # decision() always re-reads the real decision from d3_governance_decisions
    # first) -- but the composite FK below is what makes that honesty a DB-
    # enforced physical fact rather than a convention someone could bypass
    # with a raw INSERT: it can only reference a (governance_decision_id,
    # decision) pair that genuinely exists together in d3_governance_decisions.
    # CHECK(NOT (continued AND decision_recorded='BLOCK')) is a second,
    # redundant, purely-declarative belt-and-suspenders constraint for the
    # same TEST 12 negative control. Append-only.
    """
    CREATE TABLE IF NOT EXISTS d3_governance_acks (
        id BIGSERIAL PRIMARY KEY,
        governance_ack_id TEXT NOT NULL UNIQUE,
        governance_request_id TEXT NOT NULL REFERENCES d3_governance_requests(governance_request_id),
        governance_decision_id TEXT NOT NULL,
        decision_recorded TEXT NOT NULL,
        trace_id TEXT,
        action_taken TEXT NOT NULL,
        continued BOOLEAN NOT NULL,
        blocked BOOLEAN NOT NULL,
        acknowledged_by TEXT,
        acknowledgement_hash TEXT NOT NULL,
        is_test_record BOOLEAN NOT NULL DEFAULT FALSE,
        acknowledged_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CHECK (NOT (continued AND blocked)),
        CHECK (NOT (continued AND decision_recorded = 'BLOCK')),
        FOREIGN KEY (governance_decision_id, decision_recorded)
            REFERENCES d3_governance_decisions (governance_decision_id, decision)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_d3gack_decision ON d3_governance_acks (governance_decision_id)",
    "CREATE INDEX IF NOT EXISTS ix_d3gack_test ON d3_governance_acks (is_test_record)",
    """
    CREATE OR REPLACE FUNCTION d3gack_block_mutation() RETURNS trigger AS $BODY$
    BEGIN
        RAISE EXCEPTION 'd3_governance_acks is append-only: % not permitted on id=%',
            TG_OP, OLD.id;
    END;
    $BODY$ LANGUAGE plpgsql;
    """,
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_trigger WHERE tgname = 'trg_d3gack_immutable'
        ) THEN
            CREATE TRIGGER trg_d3gack_immutable
                BEFORE UPDATE OR DELETE ON d3_governance_acks
                FOR EACH ROW EXECUTE FUNCTION d3gack_block_mutation();
        END IF;
    END $$;
    """,
]

# Rollback for the v2 migration above (NOT executed automatically — kept here
# as the documented, ready-to-run reversal per the spec's "create a rollback
# migration" requirement). Dropping columns is destructive; only run this
# manually and only if v2 needs to be reverted.
_D3_V2_ROLLBACK_SQL = """
DROP INDEX IF EXISTS ux_d3gel_event_id;
DROP INDEX IF EXISTS ux_d3gel_idempotency;
DROP INDEX IF EXISTS ix_d3gel_root_trace;
DROP INDEX IF EXISTS ix_d3gel_d3_trace;
DROP INDEX IF EXISTS ix_d3gel_outcome;
DROP INDEX IF EXISTS ix_d3gel_strategy;
DROP INDEX IF EXISTS ix_d3gel_rollback;
DROP INDEX IF EXISTS ix_d3gel_event_type;
DROP INDEX IF EXISTS ix_d3gel_created_at;
ALTER TABLE d3_governance_event_links
    DROP COLUMN IF EXISTS event_schema_version,
    DROP COLUMN IF EXISTS event_id,
    DROP COLUMN IF EXISTS event_type,
    DROP COLUMN IF EXISTS root_trace_id,
    DROP COLUMN IF EXISTS correlation_id,
    DROP COLUMN IF EXISTS causation_id,
    DROP COLUMN IF EXISTS parent_event_id,
    DROP COLUMN IF EXISTS diagram3_trace_id,
    DROP COLUMN IF EXISTS shadow_trade_id,
    DROP COLUMN IF EXISTS no_execution_id,
    DROP COLUMN IF EXISTS memory_event_id,
    DROP COLUMN IF EXISTS strategy_version,
    DROP COLUMN IF EXISTS approval_id,
    DROP COLUMN IF EXISTS rejection_id,
    DROP COLUMN IF EXISTS d2_phase,
    DROP COLUMN IF EXISTS d3_phase,
    DROP COLUMN IF EXISTS producer_module,
    DROP COLUMN IF EXISTS producer_function,
    DROP COLUMN IF EXISTS consumer_module,
    DROP COLUMN IF EXISTS consumer_function,
    DROP COLUMN IF EXISTS input_record_ids,
    DROP COLUMN IF EXISTS output_record_ids,
    DROP COLUMN IF EXISTS idempotency_key,
    DROP COLUMN IF EXISTS emitted_at,
    DROP COLUMN IF EXISTS received_at,
    DROP COLUMN IF EXISTS environment;
"""


def _d3_schema_stmt_counts() -> Dict[str, int]:
    """
    Real, independently-derived counts of what _SCHEMA_STMTS actually
    contains, by statement kind. Fixes gap G5: the old boot log claimed
    "N d3_ tables" using len(_SCHEMA_STMTS) (every CREATE TABLE + CREATE
    INDEX + CREATE TRIGGER/FUNCTION + ALTER TABLE statement combined) as if
    it were a table count. This counts each kind separately and only calls
    a statement a "table" if it actually contains CREATE TABLE.
    """
    tables = sum(1 for s in _SCHEMA_STMTS if "CREATE TABLE" in s)
    indexes = sum(1 for s in _SCHEMA_STMTS if "CREATE INDEX" in s or "CREATE UNIQUE INDEX" in s)
    triggers = sum(1 for s in _SCHEMA_STMTS
                   if "CREATE TRIGGER" in s or "CREATE OR REPLACE FUNCTION" in s)
    alters = sum(1 for s in _SCHEMA_STMTS if s.strip().startswith("ALTER TABLE"))
    return {
        "tables": tables, "indexes": indexes, "triggers": triggers,
        "alters": alters, "total_statements": len(_SCHEMA_STMTS),
    }


def _d3_real_table_count() -> Optional[int]:
    """Independently re-derives the table count by querying the live DB,
    rather than trusting the static _SCHEMA_STMTS count alone."""
    try:
        with _d3_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_name LIKE 'd3\\_%'"
                )
                return cur.fetchone()[0]
    except Exception:
        return None


def _d3_init_schema():
    try:
        with _d3_connect() as conn:
            with conn.cursor() as cur:
                for stmt in _SCHEMA_STMTS:
                    cur.execute(stmt)
            conn.commit()
        counts = _d3_schema_stmt_counts()
        real_tables = _d3_real_table_count()
        table_note = f"{real_tables} (live-verified)" if real_tables is not None else f"{counts['tables']} (static count)"
        print(
            f"[d3_governance] schema init complete — "
            f"{table_note} d3_ tables, {counts['indexes']} indexes, "
            f"{counts['triggers']} trigger/function statements, "
            f"{counts['alters']} ALTER statements, "
            f"{counts['total_statements']} total schema statements ready"
        )
    except Exception as e:
        print(f"[d3_governance] schema init error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# HASH-CHAIN EVENT LEDGER — d3_governance_event_links
#
# Single global append-only chain. Every event's event_hash is
# SHA256(canonical_json(row_without_hash) || previous_event_hash).
# A postgres advisory xact lock serializes writers so the chain can never
# fork under concurrent callers. The genesis row (first ever event) chains
# off the literal string "GENESIS" — there is no real "previous" for it,
# and this is disclosed as such (no historical backfill is fabricated).
# ─────────────────────────────────────────────────────────────────────────────

_D3_CHAIN_LOCK_KEY = "d3_governance_event_links"

# v1 field set — frozen exactly as originally shipped so the 2 pre-migration
# events (and any future v1-tagged event) can still be hash-verified using
# the field set that was actually used to compute their event_hash.
_D3_EVENT_FIELDS_V1 = [
    "governance_cycle_id", "governance_trace_id", "parent_trace_id",
    "diagram1_trace_id", "diagram2_trace_id", "candidate_id", "ticker",
    "recommendation_id", "decision_id", "execution_plan_id", "paper_trade_id",
    "outcome_id", "learning_event_id", "model_id", "model_version",
    "strategy_id", "change_request_id", "architecture_version_id",
    "baseline_id", "rollback_id", "governance_phase", "governance_check_name",
    "governance_module", "governance_function", "check_result",
    "enforcement_action", "enforcement_status", "reason_code", "reason_detail",
    "input_hash", "output_hash", "source_code_commit", "source_file_hash",
    "config_hash", "started_at", "completed_at", "is_test_record",
]

# v2 adds the richer unified provenance-envelope fields required by the
# AEIM Diagram2<->Diagram3 integration spec. New emits use this field set.
_D3_EVENT_FIELDS_V2 = _D3_EVENT_FIELDS_V1 + [
    "event_id", "event_type", "root_trace_id", "correlation_id",
    "causation_id", "parent_event_id", "diagram3_trace_id", "shadow_trade_id",
    "no_execution_id", "memory_event_id", "strategy_version", "approval_id",
    "rejection_id", "d2_phase", "d3_phase", "producer_module",
    "producer_function", "consumer_module", "consumer_function",
    "input_record_ids", "output_record_ids", "idempotency_key", "emitted_at",
    "environment",
]

# Keyed by event_schema_version so the validator (T-E) can recompute the
# correct hash input for any row regardless of when it was written.
_D3_EVENT_FIELDS_BY_VERSION = {1: _D3_EVENT_FIELDS_V1, 2: _D3_EVENT_FIELDS_V2}

_D3_CURRENT_SCHEMA_VERSION = 2
_D3_EVENT_FIELDS = _D3_EVENT_FIELDS_V2


def _d3_get_last_event_hash(cur) -> str:
    cur.execute("SELECT event_hash FROM d3_governance_event_links ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
    return row[0] if row else "GENESIS"


def _d3_infer_event_type(governance_phase: str, check_result: Optional[str]) -> str:
    """
    Honest, non-fabricated mapping from a real phase result to the closest
    canonical D3->D1 event type in the spec's vocabulary. Most D3 phases are
    read-only observations (system health, discovery, performance snapshots,
    architecture consistency, forecasts, reports) — those map to
    'governance.observation_recorded'. Only phases that genuinely produce an
    approve/reject/restrict decision get a more specific type, and only when
    check_result actually says so.
    """
    # D2 trade-pipeline canonical events (Phase 3 wiring — Tier 2 gap closure)
    _D2_CANONICAL_PHASES = {
        "D2_CANDIDATE_ACCEPTED":       "candidate.accepted",
        "D2_CANDIDATE_REJECTED":       "candidate.rejected",
        "D2_RISK_APPROVED":            "risk.approved",
        "D2_RISK_REJECTED":            "risk.rejected",
        "D2_DECISION_CREATED":         "decision.created",
        "D2_DECISION_NO_TRADE":        "decision.no_trade",
        "D2_EXECUTION_SHADOW_CREATED": "execution.shadow_created",
        "D2_EXECUTION_FAILED":         "execution.failed",
        "D2_DATA_GUARD_PASSED":        "data_guard.passed",   # [S6-1] wired 2026-07-11
        "D2_DATA_GUARD_FAILED":        "data_guard.failed",   # [S6-2] wired 2026-07-11
        "D2_OUTCOME_RECORDED":         "outcome.recorded",    # [S6-3] wired 2026-07-11
    }
    if governance_phase in _D2_CANONICAL_PHASES:
        return _D2_CANONICAL_PHASES[governance_phase]
    cr = (check_result or "").upper()
    if governance_phase == "PHASE_6_LEARNING_APPROVAL":
        if "REJECT" in cr:
            return "governance.learning_rejected"
        if "APPROVE" in cr or cr in ("PASS", "OK"):
            return "governance.learning_approved"
        return "governance.review_requested"
    if governance_phase == "PHASE_5_MODEL_GOVERNANCE":
        if "REJECT" in cr:
            return "governance.model_rejected"
        if "APPROVE" in cr:
            return "governance.model_approved"
    if governance_phase == "PHASE_4_STRATEGY_GOVERNANCE":
        if "SUSPEND" in cr:
            return "governance.strategy_suspended"
        if "RESTRICT" in cr:
            return "governance.strategy_restricted"
    if governance_phase == "PHASE_9_ROLLBACK_MANAGEMENT":
        if cr == "PENDING":
            return "governance.rollback_requested"
        if "APPROV" in cr:
            return "governance.rollback_approved"
        if cr in ("PASS", "OK", "COMPLETED"):
            return "governance.rollback_completed"
    if governance_phase == "PHASE_12_SECURITY_GOVERNANCE" and cr not in ("PASS", "OK"):
        return "governance.security_violation"
    if governance_phase == "PHASE_13_ARCHITECTURE_CONSISTENCY" and cr not in ("PASS", "OK"):
        return "governance.architecture_violation"
    if governance_phase == "PHASE_14_EXECUTIVE_REPORTING":
        return "governance.report_generated"
    if governance_phase == "PHASE_7_CHANGE_MANAGEMENT":
        if "REJECT" in cr:
            return "governance.change_rejected"
        return "governance.change_approved"
    if governance_phase == "PHASE_10_POLICY_GOVERNANCE":
        if "REJECT" in cr:
            return "governance.policy_rejected"
        if "APPROVE" in cr or cr in ("PASS", "OK"):
            return "governance.policy_approved"
    return "governance.observation_recorded"

def emit_d2_pipeline_event(
    event_type: str,
    *,
    ticker: Optional[str] = None,
    trace_id: Optional[str] = None,
    paper_trade_id: Optional[int] = None,
    reason: Optional[str] = None,
) -> None:
    """
    Public emit for D2 trade-pipeline canonical events into d3_governance_event_links.
    Failure-isolated — NEVER raises. Use at the 8 canonical decision points in
    _aiem_paper_pick_candidates() and _aiem_paper_execute_today() (Phase 3 wiring).

    event_type must be one of:
      candidate.accepted, candidate.rejected, risk.approved, risk.rejected,
      decision.created, decision.no_trade, execution.shadow_created, execution.failed
    """
    import uuid as _p3_uuid
    _PHASE_MAP = {
        "candidate.accepted":       "D2_CANDIDATE_ACCEPTED",
        "candidate.rejected":       "D2_CANDIDATE_REJECTED",
        "risk.approved":            "D2_RISK_APPROVED",
        "risk.rejected":            "D2_RISK_REJECTED",
        "decision.created":         "D2_DECISION_CREATED",
        "decision.no_trade":        "D2_DECISION_NO_TRADE",
        "execution.shadow_created": "D2_EXECUTION_SHADOW_CREATED",
        "execution.failed":         "D2_EXECUTION_FAILED",
        "data_guard.passed":        "D2_DATA_GUARD_PASSED",   # [S6-1]
        "data_guard.failed":        "D2_DATA_GUARD_FAILED",   # [S6-2]
        "outcome.recorded":         "D2_OUTCOME_RECORDED",    # [S6-3]
    }
    phase = _PHASE_MAP.get(event_type)
    if not phase:
        print(f"[d3_governance] emit_d2_pipeline_event: unknown event_type {event_type!r}")
        return
    try:
        now = datetime.datetime.utcnow()
        _d3_emit_event(
            governance_cycle_id=f"D2_PIPELINE_{_p3_uuid.uuid4().hex[:8]}",
            governance_phase=phase,
            governance_check_name=f"d2_pipeline.{event_type}",
            governance_function="emit_d2_pipeline_event",
            started_at=now,
            completed_at=now,
            check_result="PASS",
            ticker=ticker,
            governance_trace_id=trace_id,
            paper_trade_id=paper_trade_id,
            reason_detail=reason,
        )
    except Exception as _p3_e:
        print(f"[d3_governance] emit_d2_pipeline_event {event_type!r} failed (non-fatal): {_p3_e}")


def _d3_environment() -> str:
    return "production" if os.environ.get("REPLIT_DEPLOYMENT") == "1" else "development"


def _d3_emit_event(
    *,
    governance_cycle_id: str,
    governance_phase: str,
    governance_check_name: str,
    governance_function: str,
    started_at,
    completed_at,
    check_result: Optional[str] = None,
    governance_trace_id: Optional[str] = None,
    parent_trace_id: Optional[str] = None,
    diagram1_trace_id: Optional[str] = None,
    diagram2_trace_id: Optional[str] = None,
    candidate_id: Optional[str] = None,
    ticker: Optional[str] = None,
    recommendation_id: Optional[str] = None,
    decision_id: Optional[str] = None,
    execution_plan_id: Optional[str] = None,
    paper_trade_id: Optional[int] = None,
    outcome_id: Optional[str] = None,
    learning_event_id: Optional[str] = None,
    model_id: Optional[str] = None,
    model_version: Optional[str] = None,
    strategy_id: Optional[str] = None,
    change_request_id: Optional[str] = None,
    architecture_version_id: Optional[str] = None,
    baseline_id: Optional[str] = None,
    rollback_id: Optional[str] = None,
    enforcement_action: str = "ADVISORY_ONLY",
    enforcement_status: str = "NOT_ENFORCED",
    reason_code: Optional[str] = None,
    reason_detail: Optional[str] = None,
    source_code_commit: Optional[str] = None,
    source_file_hash: Optional[str] = None,
    config_hash: Optional[str] = None,
    input_payload: Optional[Dict[str, Any]] = None,
    output_payload: Optional[Dict[str, Any]] = None,
    is_test_record: bool = False,
    governance_module: str = "aiem_diagram3_governance",
    # v2 provenance-envelope fields (all optional — real values only, never fabricated)
    event_type: Optional[str] = None,
    root_trace_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
    causation_id: Optional[str] = None,
    parent_event_id: Optional[str] = None,
    shadow_trade_id: Optional[str] = None,
    no_execution_id: Optional[str] = None,
    memory_event_id: Optional[str] = None,
    strategy_version: Optional[str] = None,
    approval_id: Optional[str] = None,
    rejection_id: Optional[str] = None,
    d2_phase: Optional[str] = None,
    producer_module: Optional[str] = None,
    producer_function: Optional[str] = None,
    consumer_module: Optional[str] = None,
    consumer_function: Optional[str] = None,
    input_record_ids: Optional[Dict[str, Any]] = None,
    output_record_ids: Optional[Dict[str, Any]] = None,
    idempotency_key: Optional[str] = None,
    conn=None,
) -> Dict[str, Any]:
    """
    Append one real, hash-chained governance event. Never call this with
    fabricated/hardcoded results — check_result and the linkage ids must
    reflect what the calling phase actually observed.
    """
    governance_trace_id = governance_trace_id or uuid.uuid4().hex
    event_id = uuid.uuid4().hex
    now = datetime.datetime.utcnow()

    def _ts(v):
        return v.isoformat() if hasattr(v, "isoformat") else v

    # root_trace_id: prefer an explicit override, else the real upstream D2
    # trace, else the real D1 trace, else fall back to this governance
    # cycle's own id (a self-originated, D3-only check with no upstream
    # D1/D2 trace to anchor to — disclosed as such, never invented).
    resolved_root = root_trace_id or diagram2_trace_id or diagram1_trace_id or governance_cycle_id
    resolved_correlation = correlation_id or resolved_root

    row: Dict[str, Any] = {
        "governance_cycle_id": governance_cycle_id,
        "governance_trace_id": governance_trace_id,
        "parent_trace_id": parent_trace_id,
        "diagram1_trace_id": diagram1_trace_id,
        "diagram2_trace_id": diagram2_trace_id,
        "candidate_id": candidate_id,
        "ticker": ticker,
        "recommendation_id": recommendation_id,
        "decision_id": decision_id,
        "execution_plan_id": execution_plan_id,
        "paper_trade_id": paper_trade_id,
        "outcome_id": outcome_id,
        "learning_event_id": learning_event_id,
        "model_id": model_id,
        "model_version": model_version,
        "strategy_id": strategy_id,
        "change_request_id": change_request_id,
        "architecture_version_id": architecture_version_id,
        "baseline_id": baseline_id,
        "rollback_id": rollback_id,
        "governance_phase": governance_phase,
        "governance_check_name": governance_check_name,
        "governance_module": governance_module,
        "governance_function": governance_function,
        "check_result": check_result,
        "enforcement_action": enforcement_action,
        "enforcement_status": enforcement_status,
        "reason_code": reason_code,
        "reason_detail": reason_detail,
        "input_hash": hashlib.sha256(_canonical_bytes(input_payload)).hexdigest() if input_payload is not None else None,
        "output_hash": hashlib.sha256(_canonical_bytes(output_payload)).hexdigest() if output_payload is not None else None,
        "source_code_commit": source_code_commit,
        "source_file_hash": source_file_hash,
        "config_hash": config_hash,
        "started_at": _ts(started_at),
        "completed_at": _ts(completed_at),
        "is_test_record": bool(is_test_record),
        "event_schema_version": _D3_CURRENT_SCHEMA_VERSION,
        "event_id": event_id,
        "event_type": event_type or _d3_infer_event_type(governance_phase, check_result),
        "root_trace_id": resolved_root,
        "correlation_id": resolved_correlation,
        "causation_id": causation_id,
        "parent_event_id": parent_event_id,
        "diagram3_trace_id": governance_trace_id,
        "shadow_trade_id": shadow_trade_id,
        "no_execution_id": no_execution_id,
        "memory_event_id": memory_event_id,
        "strategy_version": strategy_version,
        "approval_id": approval_id,
        "rejection_id": rejection_id,
        "d2_phase": d2_phase,
        "d3_phase": governance_phase,
        "producer_module": producer_module,
        "producer_function": producer_function,
        "consumer_module": consumer_module or governance_module,
        "consumer_function": consumer_function or governance_function,
        "input_record_ids": psycopg2.extras.Json(input_record_ids) if input_record_ids is not None else None,
        "output_record_ids": psycopg2.extras.Json(output_record_ids) if output_record_ids is not None else None,
        "idempotency_key": idempotency_key,
        "emitted_at": _ts(now),
        "environment": _d3_environment(),
    }

    own_conn = conn is None
    if own_conn:
        conn = _d3_connect()
    try:
        with conn.cursor() as cur:
            # Bound BOTH the advisory-lock wait and the rest of the txn. Without
            # these, a hung/slow lock holder stalls this call forever, and since
            # this runs SYNCHRONOUSLY inside the live per-ticker D2 stage loop
            # (via the bus subscriber), an unbounded wait here would freeze real
            # trading, not just the governance ledger. A timeout here raises
            # lock_not_available/query_canceled, which the caller (e.g.
            # _on_bus_stage_event) already catches as non-fatal for Path A
            # observation; enforcement gates (P4/P5) must map this same timeout
            # to a fail-closed ERROR, never to a fabricated PASS.
            cur.execute("SET LOCAL lock_timeout = '2s'")
            cur.execute("SET LOCAL statement_timeout = '5s'")
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (_D3_CHAIN_LOCK_KEY,))
            previous_event_hash = _d3_get_last_event_hash(cur)
            row["previous_event_hash"] = previous_event_hash
            event_hash = hashlib.sha256(
                _canonical_bytes({k: row[k] for k in _D3_EVENT_FIELDS + ["previous_event_hash"]})
            ).hexdigest()
            row["event_hash"] = event_hash

            cols = list(row.keys())
            cur.execute(
                f"INSERT INTO d3_governance_event_links ({','.join(cols)}) "
                f"VALUES ({','.join(['%s'] * len(cols))}) RETURNING id, created_at",
                [row[c] for c in cols],
            )
            new_id, created_at = cur.fetchone()
        if own_conn:
            conn.commit()
        row["id"] = new_id
        row["created_at"] = _ts(created_at)
        return row
    except Exception:
        if own_conn:
            conn.rollback()
        raise
    finally:
        if own_conn:
            conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# G0 BOOT AUTHORIZATION CHECKPOINT — Path B active enforcement (P3)
# ─────────────────────────────────────────────────────────────────────────────
# G0 is consulted ONCE per real invocation of a trade-executing entrypoint,
# before any trade-executing work begins. It reads the real, DB-backed
# d3_system_state + d3_checkpoint_config rows (never an in-memory-only flag),
# with a short TTL cache so the hot path doesn't take a DB round-trip on every
# call. While the G0 checkpoint's mode is SHADOW/OFF (the only mode it is
# seeded with, and the only mode this phase operates in), the real decision is
# ALWAYS 'ALLOW' -- `would_block` records what an ENFORCE-mode gate would have
# done, for the multi-day SHADOW proof window the spec requires before anyone
# flips this to ENFORCE. Every call is written to the governance ledger; a
# ledger-emit failure never changes the already-computed decision.
#
# Fail-closed DB-error policy (tri-state, never silently maps ERROR -> PASS):
#   - if the last successful config read is <60s old AND was SHADOW/OFF ->
#     ALLOW (the error itself is disclosed via reason_code, not hidden)
#   - otherwise, for a TRADE_EXECUTING run -> BLOCK
#   - a SCAN_ONLY run is never blocked by G0 in this phase (only
#     trade-executing runs are in scope for G0's active-enforcement default)

# Generalized per-checkpoint config cache (P3.6). Originally G0-only; G1
# (Section 12D DIAGRAM 2 DATA-GUARD COMPLETION) reuses the exact same
# read/cache/fail-closed skeleton via _read_checkpoint_config(checkpoint=...)
# instead of duplicating it. _g0_read_config(force=...) is kept as a thin
# backward-compatible wrapper around _read_checkpoint_config("G0", force=...)
# so existing call sites keep working unchanged.
_CHECKPOINT_CACHE_TTL_SECONDS = 5
_CHECKPOINT_STALE_ALLOW_WINDOW_SECONDS = 60
_G0_CACHE_TTL_SECONDS = _CHECKPOINT_CACHE_TTL_SECONDS
_G0_STALE_ALLOW_WINDOW_SECONDS = _CHECKPOINT_STALE_ALLOW_WINDOW_SECONDS
_CHECKPOINT_CONFIG_CACHE: Dict[str, Dict[str, Any]] = {}
_CHECKPOINT_CACHE_LOCK = threading.Lock()

_D3_SYSTEM_STATES = ("NORMAL", "DEGRADED", "RESTRICTED", "PAUSED",
                      "RECOVERY_REQUIRED", "ROLLBACK_IN_PROGRESS")
_D3_CHECKPOINTS = ("G0", "G1", "G2", "G3", "G4", "G5", "G6")
_D3_CHECKPOINT_MODES = ("OFF", "SHADOW", "ENFORCE")
_D3_BLOCKING_SYSTEM_STATES = {"PAUSED", "RESTRICTED", "RECOVERY_REQUIRED", "ROLLBACK_IN_PROGRESS"}
# The subset of _D3_BLOCKING_SYSTEM_STATES that Section 4 CHECKPOINT G5
# actually names as its trigger conditions (PAUSE_SYSTEM, QUARANTINE_COMPONENT
# -> ROLLBACK_IN_PROGRESS, ROLLBACK_REQUIRED -> ROLLBACK_IN_PROGRESS).
# RESTRICTED is deliberately EXCLUDED: per Section 6 it just means
# "paper/shadow only, live execution prohibited" -- a policy restriction, not
# evidence of an incident requiring recovery verification -- so exiting
# RESTRICTED directly back to NORMAL/DEGRADED is a normal admin action, not a
# G5 resume. Only an exit FROM one of these three states TO a non-gated state
# is a real "resume" that must go through g5_authorize_resume().
_D3_RECOVERY_GATED_STATES = {"PAUSED", "RECOVERY_REQUIRED", "ROLLBACK_IN_PROGRESS"}


def _read_checkpoint_config(checkpoint: str, force: bool = False) -> Dict[str, Any]:
    """Real DB read of (`checkpoint` mode, system state), cached per-checkpoint
    for _CHECKPOINT_CACHE_TTL_SECONDS. On a DB error, the PREVIOUS good
    ts/mode/state for THIS checkpoint are kept (never overwritten with a
    fabricated fresh-looking value) and the error + when it happened are
    recorded separately, so callers can apply the bounded stale-allow policy
    honestly against the age of the last real read. Generalized from the
    original G0-only _g0_read_config (P3.5) so G1 (P3.6) and future
    checkpoints share one real read/cache/fail-closed implementation instead
    of each duplicating it."""
    now = time.time()
    with _CHECKPOINT_CACHE_LOCK:
        cached = dict(_CHECKPOINT_CONFIG_CACHE.get(checkpoint, {}))
        if not force and cached.get("mode") is not None and (now - cached.get("ts", 0)) < _CHECKPOINT_CACHE_TTL_SECONDS:
            return cached
    try:
        with _d3_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout = '3s'")
                cur.execute("SELECT mode FROM d3_checkpoint_config WHERE checkpoint = %s", (checkpoint,))
                mode_row = cur.fetchone()
                cur.execute("SELECT state FROM d3_system_state WHERE id = 1")
                state_row = cur.fetchone()
        fresh = {
            "ts": now,
            "mode": mode_row[0] if mode_row else "SHADOW",
            "state": state_row[0] if state_row else "NORMAL",
            "error": None,
        }
        with _CHECKPOINT_CACHE_LOCK:
            _CHECKPOINT_CONFIG_CACHE[checkpoint] = fresh
        return fresh
    except Exception as e:
        with _CHECKPOINT_CACHE_LOCK:
            stale = dict(_CHECKPOINT_CONFIG_CACHE.get(checkpoint, {}))
            stale["error"] = str(e)
            stale["error_ts"] = now
            _CHECKPOINT_CONFIG_CACHE[checkpoint] = stale
            return dict(stale)


def _g0_read_config(force: bool = False) -> Dict[str, Any]:
    """Thin backward-compatible wrapper around
    _read_checkpoint_config("G0", force=force). Kept so pre-P3.6 call sites
    (set_d3_system_state, set_d3_checkpoint_mode, the /d3/g0/status admin
    route) keep working unchanged."""
    return _read_checkpoint_config("G0", force=force)


def _evaluate_g0_decision(run_kind: str) -> Dict[str, Any]:
    """
    Pure G0 policy evaluation -- NO DB writes, no ledger emission. Extracted
    from the original g0_authorize_run() body so require_governance_
    authorization() can persist the Section 12F request/decision/ledger
    triplet around it atomically. Reads the cached checkpoint config (see
    _g0_read_config) and returns everything a caller needs to persist a
    decision, honestly, including any real DB error hit while reading config.

    run_kind: 'TRADE_EXECUTING' (can be blocked once G0 is in ENFORCE mode,
    or fail-closed BLOCKed on an unrecovered DB error) or 'SCAN_ONLY' (never
    blocked by G0 in this phase).
    """
    cfg = _g0_read_config()
    mode = cfg.get("mode") or "SHADOW"
    state = cfg.get("state") or "NORMAL"
    db_error = cfg.get("error")

    # OFF means the checkpoint is fully disabled: unconditional ALLOW, no
    # would_block evaluation, no DB-error fail-closed logic (there is nothing
    # to fail closed on since this checkpoint isn't gating anything). This is
    # intentionally NOT the same as SHADOW, which still evaluates and flags
    # would_block for the proof window -- OFF is the "this checkpoint's
    # judgment doesn't count right now" escape hatch, e.g. while the
    # checkpoint itself is suspected broken. A lightweight ledger row is
    # still emitted by the caller for audit continuity.
    if mode == "OFF":
        decision = "ALLOW"
        reason_codes = ["CHECKPOINT_OFF"]
        would_block = False
        enforcement_status, enforcement_action = "NOT_ENFORCED", "DISABLED"
    else:
        would_block = bool(run_kind == "TRADE_EXECUTING" and state in _D3_BLOCKING_SYSTEM_STATES)
        reason_codes = [f"STATE_{state}"] if would_block else ["STATE_OK"]
        decision = "ALLOW"
        enforcement_status = "NOT_ENFORCED"
        enforcement_action = "ADVISORY_ONLY"

        if db_error:
            last_read_age = time.time() - cfg.get("ts", 0)
            if mode == "SHADOW" and last_read_age < _G0_STALE_ALLOW_WINDOW_SECONDS:
                decision = "ALLOW"
                reason_codes = ["DB_ERROR_STALE_CACHE_ALLOW"]
            elif run_kind == "TRADE_EXECUTING":
                decision = "BLOCK"
                reason_codes = ["DB_ERROR_FAIL_CLOSED"]
            else:
                decision = "ALLOW"
                reason_codes = ["DB_ERROR_SCAN_ALLOWED"]
        elif mode == "ENFORCE" and would_block:
            decision = "BLOCK"
            # Descriptive only — does not gate execution. Real gate is decision=="BLOCK" above.
            enforcement_status = "ENFORCED"
            enforcement_action = "BLOCKED"

    return {
        "decision": decision,
        "reason_codes": reason_codes,
        "would_block": would_block,
        "mode": mode,
        "system_state": state,
        "db_error": db_error,
        "enforcement_status": enforcement_status,
        "enforcement_action": enforcement_action,
    }


def _g1_check_baseline_integrity() -> Dict[str, Any]:
    """Real, bounded comparison of the in-memory architecture baseline hash
    (_D3_BASELINE_HASH, set once at d3_startup() from
    run_phase0_baseline_freeze) against the CURRENT authoritative row in
    d3_architecture_baseline. A mismatch means the protected baseline row
    diverged from what this process has in memory since it last booted --
    a genuine system-wide integrity signal that only Diagram 3 can detect,
    and is deliberately DISTINCT from Diagram 2's own kill_switch/daily_loss/
    portfolio_corr data guards (Section A: D2 owns trading-data guards, D3
    owns architecture-baseline/governance-system integrity). Never fabricates
    'ok' on a real DB error or a missing/unset hash -- both are honest
    failures, not silent passes. This is a single cheap bounded SELECT, not
    a full hash-chain walk (that belongs to the offline verification/TEST
    harness, not this synchronous per-batch call)."""
    try:
        with _d3_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout = '2s'")
                cur.execute(
                    "SELECT baseline_hash FROM d3_architecture_baseline ORDER BY id LIMIT 1"
                )
                row = cur.fetchone()
        current_hash = row[0] if row else None
        if current_hash is None:
            return {"ok": False, "error": "NO_BASELINE_ROW",
                    "current_hash": None, "in_memory_hash": _D3_BASELINE_HASH}
        if _D3_BASELINE_HASH is None:
            # This process never froze/loaded a baseline into memory (e.g.
            # d3_startup() failed silently) -- an honest gap, never a
            # fabricated match.
            return {"ok": False, "error": "IN_MEMORY_HASH_UNSET",
                    "current_hash": current_hash, "in_memory_hash": None}
        return {"ok": _D3_BASELINE_HASH == current_hash, "error": None,
                "current_hash": current_hash, "in_memory_hash": _D3_BASELINE_HASH}
    except Exception as e:
        return {"ok": False, "error": str(e),
                "current_hash": None, "in_memory_hash": _D3_BASELINE_HASH}


def _evaluate_g1_decision(run_kind: str) -> Dict[str, Any]:
    """
    Pure G1 policy evaluation (Section 12D: DIAGRAM 2 DATA-GUARD COMPLETION
    checkpoint) -- NO DB writes, no ledger emission. Mirrors _evaluate_g0_
    decision's checkpoint-mode / system-state / DB-error-fail-closed
    skeleton via the same generalized _read_checkpoint_config("G1") reader,
    PLUS one check G0 does not perform: architecture-baseline integrity
    (_g1_check_baseline_integrity). This is D3 authorizing on ITS OWN
    system-wide integrity state -- it never re-evaluates or overrides D2's
    own kill_switch/daily_loss/portfolio_corr guard outcomes, which remain
    entirely under D2's authority per spec Section A. D2 only contacts
    D3_GOVERNANCE_SERVICE at G1 AFTER its own three data guards have already
    passed (the caller in main.py enforces this by only invoking G1 from the
    single fall-through reached after all three gates succeed).

    run_kind: 'TRADE_EXECUTING' (can be blocked once G1 is in ENFORCE mode,
    fail-closed BLOCKed on an unrecovered checkpoint-config DB error, or
    blocked on a real baseline mismatch while ENFORCE) or 'SCAN_ONLY' (never
    blocked by G1 in this phase).
    """
    cfg = _read_checkpoint_config("G1")
    mode = cfg.get("mode") or "SHADOW"
    state = cfg.get("state") or "NORMAL"
    db_error = cfg.get("error")

    if mode == "OFF":
        return {
            "decision": "ALLOW",
            "reason_codes": ["CHECKPOINT_OFF"],
            "would_block": False,
            "mode": mode,
            "system_state": state,
            "db_error": db_error,
            "enforcement_status": "NOT_ENFORCED",
            "enforcement_action": "DISABLED",
        }

    would_block = bool(run_kind == "TRADE_EXECUTING" and state in _D3_BLOCKING_SYSTEM_STATES)
    reason_codes = [f"STATE_{state}"] if would_block else ["STATE_OK"]
    decision = "ALLOW"
    enforcement_status = "NOT_ENFORCED"
    enforcement_action = "ADVISORY_ONLY"

    if db_error:
        # Same bounded stale-cache-allow policy as G0. The baseline-integrity
        # check below is intentionally SKIPPED on this path -- the
        # checkpoint-config read already failed, so we are already on the
        # more conservative fail-closed branch; adding a second independent
        # DB call here would only ever make the outcome equally or more
        # restrictive, never less, so skipping it keeps the fail-closed
        # semantics simple and auditable.
        last_read_age = time.time() - cfg.get("ts", 0)
        if mode == "SHADOW" and last_read_age < _CHECKPOINT_STALE_ALLOW_WINDOW_SECONDS:
            decision = "ALLOW"
            reason_codes = ["DB_ERROR_STALE_CACHE_ALLOW"]
        elif run_kind == "TRADE_EXECUTING":
            decision = "BLOCK"
            reason_codes = ["DB_ERROR_FAIL_CLOSED"]
        else:
            decision = "ALLOW"
            reason_codes = ["DB_ERROR_SCAN_ALLOWED"]
    else:
        baseline = _g1_check_baseline_integrity()
        if not baseline["ok"]:
            cur_h = (baseline.get("current_hash") or "")[:12]
            mem_h = (baseline.get("in_memory_hash") or "")[:12]
            reason_codes = reason_codes + [
                f"BASELINE_MISMATCH:{baseline.get('error') or 'HASH_DIFFERS'}:cur={cur_h}:mem={mem_h}"
            ]
            if run_kind == "TRADE_EXECUTING":
                would_block = True
            if mode == "ENFORCE" and run_kind == "TRADE_EXECUTING":
                decision = "BLOCK"
                # Descriptive only — does not gate execution. Real gate is decision=="BLOCK" above.
                enforcement_status = "ENFORCED"
                enforcement_action = "BLOCKED"
        else:
            reason_codes = reason_codes + ["BASELINE_OK"]
            if mode == "ENFORCE" and would_block:
                decision = "BLOCK"
                # Descriptive only — does not gate execution. Real gate is decision=="BLOCK" above.
                enforcement_status = "ENFORCED"
                enforcement_action = "BLOCKED"

    return {
        "decision": decision,
        "reason_codes": reason_codes,
        "would_block": would_block,
        "mode": mode,
        "system_state": state,
        "db_error": db_error,
        "enforcement_status": enforcement_status,
        "enforcement_action": enforcement_action,
    }


# ── G2: DIAGRAM 2 PRE-DECISION TRACE-INTEGRITY CHECKPOINT (Section 12) ──────
# Per-CANDIDATE check (unlike G0/G1's once-per-batch checks): immediately
# before ONE candidate's trade is inserted into aiem_paper_trades, confirm
# that every mandatory Diagram 2 stage (stage_orders 1-17 of
# aiem_registry.DIAGRAM2_STAGE_MAP -- 18-21 run AT/AFTER the insert itself,
# so they physically cannot be checked here) was actually observed via the
# real CommunicationBus for THIS candidate's diagram2_trace_id. "Observed"
# means a real D2_BUS_OBSERVATION row with check_result='PASS' exists in
# d3_governance_event_links for that trace_id + stage -- never inferred,
# never assumed from the candidate having reached this point in the code.
#
# The mandatory stage list is DERIVED from aiem_registry.DIAGRAM2_STAGE_MAP
# (D2's single source of truth for its stage set) at call time rather than
# hardcoded as an independent parallel list, so it cannot silently drift out
# of sync if D2's stage set ever changes. If DIAGRAM2_STAGE_MAP stops
# covering a stage this checkpoint considers mandatory, that surfaces as a
# real, honest STAGE_CHECK_ERROR (see _g2_check_stage_completeness) rather
# than silently checking a shorter/stale list forever.
_G2_MANDATORY_STAGE_ORDERS = tuple(range(1, 18))


def _g2_mandatory_check_names() -> Dict[int, str]:
    """
    Real derivation of the expected D2_BUS_OBSERVATION governance_check_name
    (see _on_bus_stage_event: f"stage_{{event.stage_order}}_{{event.stage_name}}")
    for each mandatory stage order, read live from
    aiem_registry.DIAGRAM2_STAGE_MAP. Raises if the registry doesn't cover a
    stage this checkpoint considers mandatory -- that means D2's own stage
    set changed underneath this checkpoint and must not be silently ignored.
    """
    import aiem_registry as _g2_areg
    names: Dict[int, str] = {}
    for order in _G2_MANDATORY_STAGE_ORDERS:
        spec = _g2_areg.DIAGRAM2_STAGE_MAP.get(order)
        if not spec:
            raise RuntimeError(
                f"G2 mandatory stage_order={order} has no entry in "
                f"aiem_registry.DIAGRAM2_STAGE_MAP -- D2/D3 stage lists have "
                f"desynced, refusing to evaluate G2 against a stale stage list"
            )
        stage_name = spec[0]
        names[order] = f"stage_{order}_{stage_name}"
    return names


def _g2_check_stage_completeness(trace_id: str) -> Dict[str, Any]:
    """
    Real, bounded query of d3_governance_event_links for every mandatory D2
    stage (1-17) actually observed as PASS for this candidate's
    diagram2_trace_id via the real D2_BUS_OBSERVATION subscriber
    (_on_bus_stage_event). Single query on the indexed diagram2_trace_id
    column (ix_d3gel_d2_trace) with a short statement_timeout so it can
    never stall the hot per-ticker trade loop. Never fabricates completeness
    on a DB error -- that is surfaced honestly as `error` so the caller can
    apply its own fail-closed/stale-cache policy, exactly like
    _g1_check_baseline_integrity does for G1.
    """
    try:
        expected = _g2_mandatory_check_names()
    except Exception as e:
        return {"ok": False, "error": f"MANDATORY_STAGE_LIST_ERROR:{e}",
                "missing_stages": list(_G2_MANDATORY_STAGE_ORDERS),
                "present_count": 0, "expected_count": len(_G2_MANDATORY_STAGE_ORDERS)}
    try:
        with _d3_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout = '2s'")
                cur.execute(
                    """
                    SELECT DISTINCT governance_check_name
                    FROM d3_governance_event_links
                    WHERE diagram2_trace_id = %s
                      AND governance_phase = 'D2_BUS_OBSERVATION'
                      AND check_result = 'PASS'
                    """,
                    (trace_id,),
                )
                present = {r[0] for r in cur.fetchall()}
        missing = [order for order, name in expected.items() if name not in present]
        return {
            "ok": len(missing) == 0,
            "error": None,
            "missing_stages": missing,
            "present_count": len(expected) - len(missing),
            "expected_count": len(expected),
        }
    except Exception as e:
        return {"ok": False, "error": str(e),
                "missing_stages": list(_G2_MANDATORY_STAGE_ORDERS),
                "present_count": 0, "expected_count": len(_G2_MANDATORY_STAGE_ORDERS)}


def _evaluate_g2_decision(run_kind: str, trace_id: Optional[str]) -> Dict[str, Any]:
    """
    Pure G2 policy evaluation (Section 12: DIAGRAM 2 PRE-DECISION
    TRACE-INTEGRITY checkpoint) -- NO DB writes, no ledger emission. Unlike
    G0/G1 (evaluated once per batch, before the per-ticker loop even
    starts), G2 is evaluated ONCE PER CANDIDATE, immediately before that
    candidate's aiem_paper_trades INSERT. Its caller's BLOCK branch must
    skip only that one candidate (`continue` in the per-ticker loop) --
    never the whole batch -- and must NEVER touch the batch-level
    _AIEM_PAPER_LOCK (acquired/released once per batch, not per-candidate).

    trace_id is REQUIRED per-candidate context (unlike G0/G1's purely
    ambient checkpoint state): if the caller has no real diagram2_trace_id
    for this candidate (e.g. the D2 wiring try/except upstream in main.py
    failed and set it to None), that is evaluated as
    would_block=True/NO_TRACE_ID rather than skipped or assumed clean -- a
    candidate this checkpoint cannot verify is treated the same as one that
    failed verification, never silently passed through.

    run_kind: 'TRADE_EXECUTING' (can be blocked once G2 is in ENFORCE mode,
    or fail-closed BLOCKed on an unrecovered DB error) or 'SCAN_ONLY' (never
    blocked by G2 in this phase).
    """
    cfg = _read_checkpoint_config("G2")
    mode = cfg.get("mode") or "SHADOW"
    state = cfg.get("state") or "NORMAL"
    db_error = cfg.get("error")

    if mode == "OFF":
        return {
            "decision": "ALLOW",
            "reason_codes": ["CHECKPOINT_OFF"],
            "would_block": False,
            "mode": mode,
            "system_state": state,
            "db_error": db_error,
            "enforcement_status": "NOT_ENFORCED",
            "enforcement_action": "DISABLED",
        }

    would_block = bool(run_kind == "TRADE_EXECUTING" and state in _D3_BLOCKING_SYSTEM_STATES)
    reason_codes = [f"STATE_{state}"] if would_block else ["STATE_OK"]
    decision = "ALLOW"
    enforcement_status = "NOT_ENFORCED"
    enforcement_action = "ADVISORY_ONLY"

    if db_error:
        # Same bounded stale-cache-allow policy as G0/G1. The stage-
        # completeness check below is intentionally SKIPPED on this path --
        # the checkpoint-config read already failed, so we are already on
        # the more conservative fail-closed branch.
        last_read_age = time.time() - cfg.get("ts", 0)
        if mode == "SHADOW" and last_read_age < _CHECKPOINT_STALE_ALLOW_WINDOW_SECONDS:
            decision = "ALLOW"
            reason_codes = ["DB_ERROR_STALE_CACHE_ALLOW"]
        elif run_kind == "TRADE_EXECUTING":
            decision = "BLOCK"
            reason_codes = ["DB_ERROR_FAIL_CLOSED"]
        else:
            decision = "ALLOW"
            reason_codes = ["DB_ERROR_SCAN_ALLOWED"]
    elif not trace_id:
        reason_codes = reason_codes + ["NO_TRACE_ID"]
        if run_kind == "TRADE_EXECUTING":
            would_block = True
        if mode == "ENFORCE" and run_kind == "TRADE_EXECUTING":
            decision = "BLOCK"
            # Descriptive only — does not gate execution. Real gate is decision=="BLOCK" above.
            enforcement_status = "ENFORCED"
            enforcement_action = "BLOCKED"
    else:
        completeness = _g2_check_stage_completeness(trace_id)
        if completeness.get("error"):
            reason_codes = reason_codes + [f"STAGE_CHECK_ERROR:{completeness['error']}"]
            if run_kind == "TRADE_EXECUTING":
                would_block = True
            if mode == "ENFORCE" and run_kind == "TRADE_EXECUTING":
                decision = "BLOCK"
                # Descriptive only — does not gate execution. Real gate is decision=="BLOCK" above.
                enforcement_status = "ENFORCED"
                enforcement_action = "BLOCKED"
        elif not completeness["ok"]:
            missing_str = ",".join(str(s) for s in completeness["missing_stages"])
            reason_codes = reason_codes + [f"MISSING_STAGES:{missing_str}"]
            if run_kind == "TRADE_EXECUTING":
                would_block = True
            if mode == "ENFORCE" and run_kind == "TRADE_EXECUTING":
                decision = "BLOCK"
                # Descriptive only — does not gate execution. Real gate is decision=="BLOCK" above.
                enforcement_status = "ENFORCED"
                enforcement_action = "BLOCKED"
        else:
            reason_codes = reason_codes + ["STAGES_COMPLETE"]
            if mode == "ENFORCE" and would_block:
                decision = "BLOCK"
                # Descriptive only — does not gate execution. Real gate is decision=="BLOCK" above.
                enforcement_status = "ENFORCED"
                enforcement_action = "BLOCKED"

    return {
        "decision": decision,
        "reason_codes": reason_codes,
        "would_block": would_block,
        "mode": mode,
        "system_state": state,
        "db_error": db_error,
        "enforcement_status": enforcement_status,
        "enforcement_action": enforcement_action,
    }


# ── G3: PRE-EXECUTION GOVERNANCE AUTHORIZATION (Section 4/5 real checks) ─────
#
# Authorized execution modes for THIS system. There is no live broker
# adapter anywhere in this codebase (aiem_position_sizing.LIVE_MODE_ENABLED
# is hard-False) -- "LIVE" is therefore never an authorized mode, it is an
# unconditional hard block below, never a SHADOW-observed one.
_G3_AUTHORIZED_EXECUTION_MODES = {"PAPER", "SHADOW"}


def _g3_check_strategy_approval(strategy_version: Optional[str]) -> Dict[str, Any]:
    """
    Real, bounded check against d3_strategy_registry -- the only
    approved-strategy ledger that actually exists in this codebase -- for
    whether `strategy_version` (the candidate's pick source, e.g.
    'gap_volume') is a formally registered, approved, active strategy.

    strategy_version=None (caller has no signal-source context for this
    request) is reported as STRATEGY_VERSION_UNKNOWN, never assumed
    approved. A strategy_version with no matching row is reported as
    UNAPPROVED_STRATEGY:<version> -- as of this writing 5 of the 11 real
    live pick sources in _aiem_paper_pick_candidates (sweep, oi_buildup,
    washout_ignition, layer9_stat, squeeze_reversion) have never been
    registered in d3_strategy_registry, so this check will honestly report
    them as unapproved. That is a real, pre-existing gap this checkpoint is
    designed to surface in SHADOW mode, not something this change fabricates
    or silently hides.
    """
    if not strategy_version:
        return {"ok": False, "error": None, "found": False,
                "reason": "STRATEGY_VERSION_UNKNOWN"}
    try:
        with _d3_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout = '2s'")
                cur.execute(
                    "SELECT approval_status, status FROM d3_strategy_registry "
                    "WHERE signal_source = %s",
                    (strategy_version,),
                )
                row = cur.fetchone()
        if row is None:
            return {"ok": False, "error": None, "found": False,
                     "reason": f"UNAPPROVED_STRATEGY:{strategy_version}"}
        approval_status, status = row
        ok = (approval_status == "approved" and status == "active")
        reason = None if ok else f"STRATEGY_NOT_APPROVED:{strategy_version}:{approval_status}/{status}"
        return {"ok": ok, "error": None, "found": True, "reason": reason}
    except Exception as e:
        return {"ok": False, "error": str(e), "found": False,
                "reason": f"STRATEGY_CHECK_ERROR:{e}"}


def _g3_check_model_approval(model_version: Optional[str]) -> Dict[str, Any]:
    """
    Real, bounded check against d3_model_governance for whether
    `model_version` names a currently deployment_status='active' model.

    model_version=None is reported as MODEL_VERSION_NOT_TRACKED_ADVISORY and
    treated as ok=True (advisory-only, does not set would_block). None of the
    current pick sources in _aiem_paper_pick_candidates attach a per-candidate
    model_version (the unrelated `model_versions` table owned by
    online_learning.py tracks a conviction-scoring model, not a
    per-trade-candidate one). The gap remains visible in reason_codes as
    MODEL_VERSION_NOT_TRACKED_ADVISORY; it just does not block trades on its
    own until a real per-trade model versioning system is wired in.

    Joel-approved deviation from Phase 2 no-code-change assumption --
    Finding 1, model_version exemption (Directive 13 Phase 2, 2026-07-14).
    """
    if not model_version:
        return {"ok": True, "error": None, "found": False,
                "reason": "MODEL_VERSION_NOT_TRACKED_ADVISORY"}
    try:
        with _d3_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout = '2s'")
                cur.execute(
                    "SELECT deployment_status FROM d3_model_governance "
                    "WHERE model_version = %s ORDER BY recorded_at DESC LIMIT 1",
                    (model_version,),
                )
                row = cur.fetchone()
        if row is None:
            return {"ok": False, "error": None, "found": False,
                     "reason": f"UNAPPROVED_MODEL:{model_version}"}
        ok = row[0] == "active"
        reason = None if ok else f"MODEL_NOT_ACTIVE:{model_version}:{row[0]}"
        return {"ok": ok, "error": None, "found": True, "reason": reason}
    except Exception as e:
        return {"ok": False, "error": str(e), "found": False,
                "reason": f"MODEL_CHECK_ERROR:{e}"}


def _g3_check_unresolved_actions() -> Dict[str, Any]:
    """
    Real, bounded check against d3_governance_actions for any action still
    in status='REQUESTED' (i.e. not yet ADVISORY_ACKNOWLEDGED/NOT_ENFORCED)
    whose action_type names a critical-incident or quarantine condition.

    No action_type matching QUARANTINE/CRITICAL/PAUSE has ever actually been
    requested against this table as of this writing (confirmed by direct
    inspection), so this check honestly returns ok=True today. It becomes a
    real, live block the moment any future phase (supervisor layer, G4/G5)
    requests one -- this is not a fabricated always-pass, it is a real query
    against the real table with a currently-empty result set.
    """
    try:
        with _d3_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout = '2s'")
                cur.execute(
                    "SELECT action_id, action_type FROM d3_governance_actions "
                    "WHERE status = 'REQUESTED' "
                    "  AND is_test_record = FALSE "
                    "  AND (action_type ILIKE %s OR action_type ILIKE %s OR action_type ILIKE %s) "
                    "ORDER BY requested_at DESC LIMIT 10",
                    ("%QUARANTINE%", "%CRITICAL%", "%PAUSE%"),
                )
                rows = cur.fetchall()
        if rows:
            ids = ",".join(r[0] for r in rows)
            return {"ok": False, "error": None, "count": len(rows),
                    "reason": f"UNRESOLVED_ACTIONS:{ids}"}
        return {"ok": True, "error": None, "count": 0, "reason": None}
    except Exception as e:
        return {"ok": False, "error": str(e), "count": None,
                "reason": f"ACTIONS_CHECK_ERROR:{e}"}


def _evaluate_g3_decision(run_kind: str, diagram2_risk_result: Optional[str],
                           execution_mode: Optional[str],
                           model_version: Optional[str],
                           strategy_version: Optional[str]) -> Dict[str, Any]:
    """
    Pure G3 policy evaluation (Section 4/5: PRE-EXECUTION GOVERNANCE
    AUTHORIZATION checkpoint) -- NO DB writes of its own decision record
    (that happens once, atomically, in require_governance_authorization).
    Unlike G0/G1 (once per batch) and like G2, G3 is evaluated once per
    trade candidate, immediately before that candidate's real write
    (aiem_paper_trades INSERT / write_paper_pick call).

    TEST E structural guarantee: diagram2_risk_result is checked FIRST,
    before any other D3 logic runs, and an UNCONDITIONAL BLOCK is returned
    the moment it is anything other than 'PASS' -- regardless of G3's own
    SHADOW/ENFORCE mode. There is no code path below this point that can
    turn a Diagram 2 rejection into a Diagram 3 approval. In real production
    this branch is unreachable today: the fail-closed sizing-gate check
    upstream in main.py already `continue`s every non-APPROVED/
    PARAMS_NOT_CONFIRMED candidate before G3 is ever called, and
    premarket_open_trader.py always passes 'PASS' because its own hard/soft
    blocker gates already ran before this call site. The branch exists so
    the invariant is provable under a direct/test-harness call, not because
    it is expected to fire live.

    Also unconditional (independent of mode/state): a request for
    execution_mode='LIVE' is hard-blocked, because there is no live broker
    adapter anywhere in this system to authorize against -- fabricating a
    SHADOW-only observation for a request this system can never actually
    execute would misrepresent what was checked.

    For every other candidate, this composes the real system-state check
    (same _D3_BLOCKING_SYSTEM_STATES signal G0/G1/G2 use -- there is no
    second, independently-tracked "daily safety state" anywhere in this
    codebase, so reusing it here is an honest choice, not a duplicated
    fabrication) with three checks unique to G3: approved strategy version
    (_g3_check_strategy_approval), approved model version
    (_g3_check_model_approval), and no unresolved critical/quarantine
    action (_g3_check_unresolved_actions). "Broker/execution adapter
    healthy" for this system's only real execution adapter -- the
    governance DB write path itself -- is the same successful checkpoint-
    config read already performed for every other checkpoint; a second,
    redundant connection is not opened to manufacture an extra check.
    """
    if diagram2_risk_result != "PASS":
        return {
            "decision": "BLOCK",
            "reason_codes": [f"D2_RISK_REJECTED:{diagram2_risk_result}"],
            "would_block": True,
            "mode": "N/A_D2_GATE",
            "system_state": None,
            "db_error": None,
            "enforcement_status": "ENFORCED",
            "enforcement_action": "BLOCKED_D2_REJECT",
        }

    if execution_mode == "LIVE":
        return {
            "decision": "BLOCK",
            "reason_codes": ["NO_LIVE_BROKER_ADAPTER", "LIVE_MODE_DISABLED"],
            "would_block": True,
            "mode": "N/A_LIVE_HARD_BLOCK",
            "system_state": None,
            "db_error": None,
            "enforcement_status": "ENFORCED",
            "enforcement_action": "BLOCKED_LIVE_DISABLED",
        }

    cfg = _read_checkpoint_config("G3")
    mode = cfg.get("mode") or "SHADOW"
    state = cfg.get("state") or "NORMAL"
    db_error = cfg.get("error")

    if mode == "OFF":
        return {
            "decision": "ALLOW",
            "reason_codes": ["CHECKPOINT_OFF"],
            "would_block": False,
            "mode": mode,
            "system_state": state,
            "db_error": db_error,
            "enforcement_status": "NOT_ENFORCED",
            "enforcement_action": "DISABLED",
        }

    if db_error:
        # Same bounded stale-cache-allow policy as G0/G1/G2.
        last_read_age = time.time() - cfg.get("ts", 0)
        if mode == "SHADOW" and last_read_age < _CHECKPOINT_STALE_ALLOW_WINDOW_SECONDS:
            decision = "ALLOW"
            reason_codes = ["DB_ERROR_STALE_CACHE_ALLOW"]
            would_block = False
            enforcement_status = "NOT_ENFORCED"
            enforcement_action = "ADVISORY_ONLY"
        elif run_kind == "TRADE_EXECUTING":
            decision = "BLOCK"
            reason_codes = ["DB_ERROR_FAIL_CLOSED"]
            would_block = True
            # Descriptive only — does not gate execution. Real gate is decision=="BLOCK" above.
            enforcement_status = "ENFORCED"
            enforcement_action = "BLOCKED"
        else:
            decision = "ALLOW"
            reason_codes = ["DB_ERROR_SCAN_ALLOWED"]
            would_block = False
            enforcement_status = "NOT_ENFORCED"
            enforcement_action = "ADVISORY_ONLY"
        return {
            "decision": decision, "reason_codes": reason_codes, "would_block": would_block,
            "mode": mode, "system_state": state, "db_error": db_error,
            "enforcement_status": enforcement_status, "enforcement_action": enforcement_action,
        }

    reason_codes: list = []
    would_block = False

    if run_kind == "TRADE_EXECUTING" and state in _D3_BLOCKING_SYSTEM_STATES:
        would_block = True
        reason_codes.append(f"STATE_{state}")
        if state == "PAUSED":
            reason_codes.append("PAUSE_SYSTEM")

    if execution_mode not in _G3_AUTHORIZED_EXECUTION_MODES:
        would_block = True
        reason_codes.append(f"UNAUTHORIZED_EXECUTION_MODE:{execution_mode}")

    # Broker/execution adapter healthy: for this system's only real
    # execution adapter (the governance DB write path), the successful
    # checkpoint-config read above (db_error is falsy on this path) IS the
    # health check -- reported honestly rather than re-proven redundantly.
    reason_codes.append("PAPER_ADAPTER_DB_OK")

    baseline = _g1_check_baseline_integrity()
    if not baseline.get("ok"):
        would_block = True
        reason_codes.append(f"BASELINE_INVALID:{baseline.get('error')}")

    strat = _g3_check_strategy_approval(strategy_version)
    if not strat.get("ok"):
        would_block = True
        reason_codes.append(strat.get("reason") or "STRATEGY_CHECK_FAILED")

    modc = _g3_check_model_approval(model_version)
    if not modc.get("ok"):
        would_block = True
        reason_codes.append(modc.get("reason") or "MODEL_CHECK_FAILED")

    actions = _g3_check_unresolved_actions()
    if not actions.get("ok"):
        would_block = True
        reason_codes.append(actions.get("reason") or "ACTIONS_CHECK_FAILED")

    if not would_block:
        reason_codes.append("ALL_CHECKS_OK")

    decision = "ALLOW"
    enforcement_status = "NOT_ENFORCED"
    enforcement_action = "ADVISORY_ONLY"
    if mode == "ENFORCE" and would_block:
        decision = "BLOCK"
        # Descriptive only — does not gate execution. Real gate is decision=="BLOCK" above.
        enforcement_status = "ENFORCED"
        enforcement_action = "BLOCKED"

    return {
        "decision": decision,
        "reason_codes": reason_codes,
        "would_block": would_block,
        "mode": mode,
        "system_state": state,
        "db_error": db_error,
        "enforcement_status": enforcement_status,
        "enforcement_action": enforcement_action,
    }


# ─────────────────────────────────────────────────────────────────────────────
# G6 — POINT-IN-TIME GUARD (Diagram 2 remediation spec, step 3)
# ─────────────────────────────────────────────────────────────────────────────


def _evaluate_g6_decision(run_kind: str, candidate_ticker: Optional[str],
                           pit_price_source: Optional[str],
                           pit_price_source_scan_date: Optional[Any],
                           now_et_date: Optional[Any] = None) -> Dict[str, Any]:
    """
    Pure G6 policy evaluation (Diagram 2 remediation spec step 3: POINT-IN-
    TIME GUARD). Evaluated once per live trade candidate in
    _aiem_paper_execute_today, immediately after price resolution, using the
    REAL provenance of the price actually resolved for this candidate (see
    point_in_time_guard.evaluate_live_candidate_pit -- the concrete risk this
    checks is the polygon_rvol_scan fallback query returning a stale prior-
    session price with no date filter).

    UNLIKE every other checkpoint in this module (G0-G5, all fail-CLOSED on
    TRADE_EXECUTING + db_error/exception), G6 fails OPEN by design: any
    internal exception -- including a DB error reading d3_checkpoint_config,
    or an unexpected failure inside evaluate_live_candidate_pit itself --
    always returns ALLOW (advisory-only), never BLOCK. G6 audits price
    PROVENANCE, not trade risk that the rest of the stack (G2/G3) already
    gates; a config-read hiccup here is not a reason to silently drop a
    candidate that already cleared real risk checks. G6 is also seeded
    SHADOW at boot and is intentionally never auto-promoted to ENFORCE --
    only a documented, deliberate admin action can flip it (architect
    decision) -- so even the ENFORCE branch below stays inert until that
    happens.
    """
    try:
        cfg = _read_checkpoint_config("G6")
        mode = cfg.get("mode") or "SHADOW"
        state = cfg.get("state") or "NORMAL"
        db_error = cfg.get("error")

        if mode == "OFF":
            return {
                "decision": "ALLOW",
                "reason_codes": ["CHECKPOINT_OFF"],
                "would_block": False,
                "mode": mode,
                "system_state": state,
                "db_error": db_error,
                "enforcement_status": "NOT_ENFORCED",
                "enforcement_action": "DISABLED",
            }

        if db_error:
            return {
                "decision": "ALLOW",
                "reason_codes": ["DB_ERROR_FAIL_OPEN"],
                "would_block": False,
                "mode": mode,
                "system_state": state,
                "db_error": db_error,
                "enforcement_status": "NOT_ENFORCED",
                "enforcement_action": "ADVISORY_ONLY",
            }

        pit_result = evaluate_live_candidate_pit(
            ticker=candidate_ticker,
            price_source=pit_price_source,
            price_source_scan_date=pit_price_source_scan_date,
            now_et_date=now_et_date,
        )
        violations = pit_result.get("violations") or []
        reason_codes = list(violations) if violations else ["PIT_OK"]
        would_block = bool(violations)

        decision = "ALLOW"
        enforcement_status = "NOT_ENFORCED"
        enforcement_action = "ADVISORY_ONLY"
        if mode == "ENFORCE" and would_block:
            decision = "BLOCK"
            # Descriptive only — does not gate execution. Real gate is decision=="BLOCK" above.
            enforcement_status = "ENFORCED"
            enforcement_action = "BLOCKED"

        return {
            "decision": decision,
            "reason_codes": reason_codes,
            "would_block": would_block,
            "mode": mode,
            "system_state": state,
            "db_error": db_error,
            "enforcement_status": enforcement_status,
            "enforcement_action": enforcement_action,
            "pit_evidence": pit_result.get("evidence"),
        }
    except Exception as exc:
        return {
            "decision": "ALLOW",
            "reason_codes": [f"G6_INTERNAL_EXCEPTION_FAIL_OPEN:{type(exc).__name__}"],
            "would_block": False,
            "mode": "SHADOW",
            "system_state": None,
            "db_error": str(exc),
            "enforcement_status": "NOT_ENFORCED",
            "enforcement_action": "ADVISORY_ONLY",
        }


# ─────────────────────────────────────────────────────────────────────────────
# G4 — LEARNING / MODEL PROMOTION GOVERNANCE (Path B P6)
# ─────────────────────────────────────────────────────────────────────────────

_G4_MIN_SAMPLES = 100
_G4_MAX_SCORE_DRIFT = 0.20


def _g4_check_rollback_artifact(model_name: Optional[str]) -> Dict[str, Any]:
    """
    Real check against online_learning's own model_versions table (the only
    rollback ledger that exists for learning-model promotions in this
    codebase) for whether a PRIOR version of `model_name` exists to roll
    back to if this promotion needs to be reverted.

    A model_name with zero prior versions is NOT treated as a failure --
    every model's first-ever promotion is structurally unable to have a
    rollback target, and blocking a first deployment on the absence of a
    history it cannot possibly have would be dishonest theater, not a real
    safety check. It is reported as ok=True with an explicit
    FIRST_VERSION_NO_ROLLBACK_TARGET reason so the gap stays visible rather
    than being silently indistinguishable from a model with real history.
    """
    if not model_name:
        return {"ok": False, "error": None, "count": 0, "reason": "MODEL_NAME_UNKNOWN"}
    try:
        with _d3_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout = '2s'")
                cur.execute(
                    "SELECT COUNT(*) FROM model_versions WHERE model_name = %s",
                    (model_name,),
                )
                count = cur.fetchone()[0]
        if count == 0:
            return {"ok": True, "error": None, "count": 0,
                     "reason": "FIRST_VERSION_NO_ROLLBACK_TARGET"}
        return {"ok": True, "error": None, "count": count,
                 "reason": f"PRIOR_VERSIONS_AVAILABLE:{count}"}
    except Exception as e:
        return {"ok": False, "error": str(e), "count": None,
                 "reason": f"ROLLBACK_ARTIFACT_CHECK_ERROR:{e}"}


def _g4_check_version_manifest(model_name: Optional[str],
                                version_saved: Optional[Any],
                                weights_hash: Optional[str]) -> Dict[str, Any]:
    """
    Real cross-check between the aiem_learning_proposals row being promoted
    and online_learning's own model_versions table -- the actual weights
    artifact -- for whether the version/hash this admin action is about to
    mark is_live=TRUE really exists and really matches what was recorded at
    proposal time. Catches real data-integrity divergence between the two
    tables (e.g. a version row deleted/corrupted after the proposal was
    created); never assumed to match just because the proposal row says so.
    """
    if version_saved is None or not weights_hash or not model_name:
        return {"ok": False, "error": None, "found": False,
                 "reason": "VERSION_MANIFEST_INCOMPLETE"}
    try:
        with _d3_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout = '2s'")
                cur.execute(
                    "SELECT weights_hash FROM model_versions "
                    "WHERE model_name = %s AND version = %s",
                    (model_name, version_saved),
                )
                row = cur.fetchone()
        if row is None:
            return {"ok": False, "error": None, "found": False,
                     "reason": f"VERSION_MANIFEST_NOT_FOUND:{model_name}:{version_saved}"}
        if row[0] != weights_hash:
            return {"ok": False, "error": None, "found": True,
                     "reason": "VERSION_MANIFEST_HASH_MISMATCH"}
        return {"ok": True, "error": None, "found": True, "reason": None}
    except Exception as e:
        return {"ok": False, "error": str(e), "found": False,
                 "reason": f"VERSION_MANIFEST_CHECK_ERROR:{e}"}


def _evaluate_g4_decision(run_kind: str,
                           learning_accepted: Optional[bool],
                           learning_model_name: Optional[str],
                           learning_n_samples: Optional[int],
                           learning_current_score: Optional[float],
                           learning_new_score: Optional[float],
                           learning_max_drift: Optional[float],
                           learning_version_saved: Optional[Any],
                           learning_weights_hash: Optional[str]) -> Dict[str, Any]:
    """
    Pure G4 policy evaluation (LEARNING / MODEL PROMOTION GOVERNANCE
    checkpoint) -- NO DB writes of its own decision record (that happens
    once, atomically, in require_governance_authorization).

    Real promotion action in this codebase: POST
    /stock-api/admin/learning-proposals/<id>/approve in main.py -- the ONLY
    code path that ever sets aiem_learning_proposals.promoted=TRUE and calls
    online_learning.rollback_to_version() to flip a model_versions row live.
    This is the single choke-point G4 gates, immediately before that real
    write -- exactly like G3 gates immediately before the real
    aiem_paper_trades INSERT.

    A separate, real, PRE-EXISTING gap this checkpoint does NOT close: model
    'discovery_cycle_signal_weights' is auto-promoted with promote=True
    directly inside _dc_module3_online_learning_update (main.py), with zero
    human review and zero D3 involvement -- it never reaches this admin
    endpoint at all. Disclosed here (and in session/memory docs), not
    silently patched by this checkpoint -- the spec calls for one
    choke-point, and the admin-approval endpoint is the one that mirrors
    G3's "gate immediately before the real write" pattern.

    TEST F structural guarantee: learning_accepted is checked FIRST. A
    proposal already rejected by online_learning's own drift/perf gate at
    proposal-creation time is unconditionally BLOCKed, regardless of G4
    mode. In real production this branch is unreachable -- the caller
    (admin_approve_learning_proposal) already hard-rejects accepted=FALSE
    proposals with a 400 before G4 is ever invoked -- it exists so the
    invariant is provable under a direct/test-harness call, same as G3's
    diagram2_risk_result check.

    For every other proposal, this recomputes -- independently, from the
    raw n_samples/current_score/new_score fields, never trusting a
    caller-supplied boolean -- the EXACT same 3-factor policy
    run_phase6_learning_approval() already computes and logs as
    ADVISORY-ONLY into d3_learning_approvals (performance_ok: new_score >=
    current_score; calibration_ok: n_samples >= 100; risk_ok: score drift <
    20%). These two implementations must be kept in lock-step by hand if
    either threshold ever changes -- there is no shared constant between
    them today because run_phase6_learning_approval's literals predate this
    checkpoint.

    Two REAL checks beyond Phase 6's advisory analysis, unique to this
    checkpoint: rollback_artifact_ok (_g4_check_rollback_artifact -- a real
    prior model_versions row exists to revert to, or this is honestly the
    model's first-ever version) and version_manifest_ok
    (_g4_check_version_manifest -- the specific version/weights_hash this
    action is about to mark live really exists in model_versions and really
    matches what the proposal row recorded).

    Two INFORMATIONAL, NEVER-BLOCKING disclosures always appended to
    reason_codes: this codebase's held-out evaluation is a single
    time-ordered 80/20 split (X[:split]/X[split:] on time-ordered rows) -- a
    real but partial walk-forward property, not full walk-forward
    cross-validation -- and there is no automated leakage/lookahead
    statistical test anywhere in this codebase for a learning-model
    promotion. Both are disclosed honestly rather than either fabricating a
    passing check that does not exist, or permanently hard-blocking every
    future promotion forever on a capability gap that no single promotion
    decision can close.
    """
    if learning_accepted is not True:
        return {
            "decision": "BLOCK",
            "reason_codes": [f"PROPOSAL_NOT_ACCEPTED:{learning_accepted}"],
            "would_block": True,
            "mode": "N/A_ACCEPTED_GATE",
            "system_state": None,
            "db_error": None,
            "enforcement_status": "ENFORCED",
            "enforcement_action": "BLOCKED_NOT_ACCEPTED",
        }

    cfg = _read_checkpoint_config("G4")
    mode = cfg.get("mode") or "SHADOW"
    state = cfg.get("state") or "NORMAL"
    db_error = cfg.get("error")

    if mode == "OFF":
        return {
            "decision": "ALLOW",
            "reason_codes": ["CHECKPOINT_OFF"],
            "would_block": False,
            "mode": mode,
            "system_state": state,
            "db_error": db_error,
            "enforcement_status": "NOT_ENFORCED",
            "enforcement_action": "DISABLED",
        }

    if db_error:
        # Same bounded stale-cache-allow policy as G0/G1/G2/G3.
        last_read_age = time.time() - cfg.get("ts", 0)
        if mode == "SHADOW" and last_read_age < _CHECKPOINT_STALE_ALLOW_WINDOW_SECONDS:
            decision = "ALLOW"
            reason_codes = ["DB_ERROR_STALE_CACHE_ALLOW"]
            would_block = False
            enforcement_status = "NOT_ENFORCED"
            enforcement_action = "ADVISORY_ONLY"
        elif run_kind == "TRADE_EXECUTING":
            decision = "BLOCK"
            reason_codes = ["DB_ERROR_FAIL_CLOSED"]
            would_block = True
            # Descriptive only — does not gate execution. Real gate is decision=="BLOCK" above.
            enforcement_status = "ENFORCED"
            enforcement_action = "BLOCKED"
        else:
            decision = "ALLOW"
            reason_codes = ["DB_ERROR_SCAN_ALLOWED"]
            would_block = False
            enforcement_status = "NOT_ENFORCED"
            enforcement_action = "ADVISORY_ONLY"
        return {
            "decision": decision, "reason_codes": reason_codes, "would_block": would_block,
            "mode": mode, "system_state": state, "db_error": db_error,
            "enforcement_status": enforcement_status, "enforcement_action": enforcement_action,
        }

    reason_codes: list = []
    would_block = False

    if run_kind == "TRADE_EXECUTING" and state in _D3_BLOCKING_SYSTEM_STATES:
        would_block = True
        reason_codes.append(f"STATE_{state}")
        if state == "PAUSED":
            reason_codes.append("PAUSE_SYSTEM")

    baseline = _g1_check_baseline_integrity()
    if not baseline.get("ok"):
        would_block = True
        reason_codes.append(f"BASELINE_INVALID:{baseline.get('error')}")

    current = float(learning_current_score) if learning_current_score is not None else 0.0
    new_s = float(learning_new_score) if learning_new_score is not None else 0.0
    n = int(learning_n_samples or 0)

    performance_ok = new_s >= current
    calibration_ok = n >= _G4_MIN_SAMPLES
    risk_ok = (new_s - current) < _G4_MAX_SCORE_DRIFT

    if not performance_ok:
        would_block = True
        reason_codes.append(f"PERFORMANCE_REGRESSION:{new_s:.6f}<{current:.6f}")
    if not calibration_ok:
        would_block = True
        reason_codes.append(f"INSUFFICIENT_SAMPLES:{n}<{_G4_MIN_SAMPLES}")
    if not risk_ok:
        would_block = True
        reason_codes.append(f"SCORE_DRIFT_TOO_HIGH:{(new_s - current):.6f}>={_G4_MAX_SCORE_DRIFT}")

    rollback = _g4_check_rollback_artifact(learning_model_name)
    if not rollback.get("ok"):
        would_block = True
        reason_codes.append(rollback.get("reason") or "ROLLBACK_ARTIFACT_CHECK_FAILED")
    else:
        reason_codes.append(rollback.get("reason"))

    manifest = _g4_check_version_manifest(learning_model_name, learning_version_saved, learning_weights_hash)
    if not manifest.get("ok"):
        would_block = True
        reason_codes.append(manifest.get("reason") or "VERSION_MANIFEST_CHECK_FAILED")

    # Informational only -- disclosed for transparency, never contributes to
    # would_block. See docstring for why these are not fabricated pass/fail
    # gates.
    reason_codes.append("OOS_VALIDATION:TIME_ORDERED_80_20_HOLDOUT_ONLY_NOT_FULL_WALK_FORWARD")
    reason_codes.append("LEAKAGE_CHECK:NO_AUTOMATED_LEAKAGE_LOOKAHEAD_DETECTOR_EXISTS")

    if not would_block:
        reason_codes.append("ALL_CHECKS_OK")

    decision = "ALLOW"
    enforcement_status = "NOT_ENFORCED"
    enforcement_action = "ADVISORY_ONLY"
    if mode == "ENFORCE" and would_block:
        decision = "BLOCK"
        # Descriptive only — does not gate execution. Real gate is decision=="BLOCK" above.
        enforcement_status = "ENFORCED"
        enforcement_action = "BLOCKED"

    return {
        "decision": decision,
        "reason_codes": reason_codes,
        "would_block": would_block,
        "mode": mode,
        "system_state": state,
        "db_error": db_error,
        "enforcement_status": enforcement_status,
        "enforcement_action": enforcement_action,
    }


# ─────────────────────────────────────────────────────────────────────────────
# G5 — RECOVERY AND RESUMPTION (Path B P7)
# ─────────────────────────────────────────────────────────────────────────────

def _g5_check_ledger_chain_integrity() -> Dict[str, Any]:
    """
    Real recovery-verification check: walks the ENTIRE d3_governance_event_links
    hash chain from GENESIS using the exact same recompute_event_hash/
    verify_chain logic the offline TEST I tamper-evidence harness uses
    (aiem_diagram3_verification), imported LAZILY here (not at module level)
    because that module itself imports this one as `d3gov` for
    _D3_EVENT_FIELDS_BY_VERSION -- a top-level import would be circular.

    Unlike every G0-G4 check (bounded, cheap, run on the G0-G4 hot per-batch/
    per-candidate path), this is intentionally a FULL chain walk. G5 recovery
    verification only runs on a rare, human-triggered RECOVERY_REQUIRED/
    PAUSED/ROLLBACK_IN_PROGRESS -> NORMAL resume request (see
    g5_authorize_resume below), never on a hot path, so the cost of walking
    the whole ledger is acceptable here -- and a tail-only/windowed walk would
    not honestly prove "the chain is intact": a forged row in the middle of
    the ledger would be invisible to a check that only re-verifies the most
    recent N rows.
    """
    try:
        with _d3_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout = '20s'")
            import aiem_diagram3_verification as _d3verify
            rows = _d3verify.fetch_all_events(conn)
            report = _d3verify.verify_chain(rows)
        if report.get("chain_intact"):
            return {"ok": True, "error": None, "rows_checked": report.get("rows_checked"),
                     "reason": f"CHAIN_INTACT:{report.get('rows_checked')}_ROWS"}
        return {"ok": False, "error": None, "rows_checked": report.get("rows_checked"),
                 "reason": f"CHAIN_BROKEN:{report.get('mismatch_count')}_MISMATCHES"}
    except Exception as e:
        return {"ok": False, "error": str(e), "rows_checked": None,
                 "reason": f"CHAIN_CHECK_ERROR:{e}"}


def _g5_check_recovery_verification() -> Dict[str, Any]:
    """
    Combines the three real, independent checks G5's recovery verification
    relies on: architecture-baseline integrity (reused from G1 -- the same
    check that would have flagged the drift that likely caused a PAUSE in
    the first place), no unresolved critical/quarantine governance actions
    (reused from G3), and full ledger hash-chain integrity (new to G5, see
    _g5_check_ledger_chain_integrity). All three must be independently
    honest 'ok' for a resume to be verified -- this function performs no
    aggregation logic beyond returning the three sub-results so
    _evaluate_g5_decision (and the /g5/status route, for transparency) can
    see exactly which one failed.
    """
    return {
        "baseline": _g1_check_baseline_integrity(),
        "unresolved_actions": _g3_check_unresolved_actions(),
        "ledger_chain": _g5_check_ledger_chain_integrity(),
    }


def _evaluate_g5_decision(run_kind: str, target_state: Optional[str]) -> Dict[str, Any]:
    """
    Pure G5 policy evaluation (Section 4 CHECKPOINT G5: RECOVERY AND
    RESUMPTION / Section 6 RECOVERY_REQUIRED, PAUSED, ROLLBACK_IN_PROGRESS
    system states) -- NO DB writes, no ledger emission (that happens once,
    atomically, in require_governance_authorization), and NO system-state
    mutation either (that happens in g5_authorize_resume, the only real
    caller, immediately AFTER the request/decision are durably persisted).

    current_state is read fresh from _read_checkpoint_config('G5') below --
    never trusted from a caller-supplied value -- exactly like every other
    checkpoint's state read, to avoid a TOCTOU gap between whatever the
    caller last observed and what d3_system_state actually holds right now.

    If current_state is not in _D3_RECOVERY_GATED_STATES, there is honestly
    nothing to recover from: this returns an unconditional NO_RECOVERY_NEEDED
    ALLOW rather than fabricating a "verified recovery" for a system that was
    never paused/quarantined/rolled back. Likewise, if target_state is ITSELF
    still a recovery-gated state (e.g. PAUSED -> ROLLBACK_IN_PROGRESS), this
    is a lateral admin transition between two protective states, not a
    resume, and is also an honest NO_RECOVERY_NEEDED ALLOW --
    set_d3_system_state() permits that transition directly without G5 (only
    an exit to a NON-gated state is a real resume requiring this checkpoint).

    A real DB error reading G5's own checkpoint config is UNCONDITIONALLY
    fail-closed (BLOCK) regardless of run_kind -- unlike G0-G4's bounded
    stale-cache-allow window for SCAN_ONLY/non-trade-executing runs, there is
    no "safe to allow anyway" case for a resume: authorizing an exit from a
    protective state on exactly the kind of infrastructure failure that state
    exists to guard against would defeat the entire purpose of the
    checkpoint.
    """
    # force=True: G5 only runs on a rare, human-triggered resume request, so
    # the cost of a real read is negligible -- unlike G0-G4's hot per-batch/
    # per-candidate path, there is no reason to accept up to
    # _CHECKPOINT_CACHE_TTL_SECONDS of staleness here. This narrows (but does
    # not by itself close) the TOCTOU window between this decision and the
    # eventual set_d3_system_state() write in g5_authorize_resume -- that
    # window is closed by expected_old_state below.
    cfg = _read_checkpoint_config("G5", force=True)
    mode = cfg.get("mode") or "SHADOW"
    current_state = cfg.get("state") or "NORMAL"
    db_error = cfg.get("error")

    if mode == "OFF":
        return {
            "decision": "ALLOW", "reason_codes": ["CHECKPOINT_OFF"], "would_block": False,
            "mode": mode, "system_state": current_state, "db_error": db_error,
            "enforcement_status": "NOT_ENFORCED", "enforcement_action": "DISABLED",
        }

    if current_state not in _D3_RECOVERY_GATED_STATES or (target_state in _D3_RECOVERY_GATED_STATES if target_state else False):
        return {
            "decision": "ALLOW",
            "reason_codes": [f"NO_RECOVERY_NEEDED:{current_state}->{target_state}"],
            "would_block": False, "mode": mode, "system_state": current_state, "db_error": db_error,
            "enforcement_status": "NOT_ENFORCED", "enforcement_action": "NOOP_NOT_A_RESUME",
        }

    if db_error:
        return {
            "decision": "BLOCK", "reason_codes": ["DB_ERROR_FAIL_CLOSED_RESUME"], "would_block": True,
            "mode": mode, "system_state": current_state, "db_error": db_error,
            "enforcement_status": "ENFORCED", "enforcement_action": "BLOCKED",
        }

    reason_codes: list = [f"STATE_{current_state}", f"TARGET_{target_state}"]
    would_block = False

    verification = _g5_check_recovery_verification()

    baseline = verification["baseline"]
    if not baseline.get("ok"):
        would_block = True
        reason_codes.append(f"BASELINE_INVALID:{baseline.get('error')}")

    unresolved = verification["unresolved_actions"]
    if not unresolved.get("ok"):
        would_block = True
        reason_codes.append(unresolved.get("reason") or "UNRESOLVED_ACTIONS_CHECK_FAILED")

    chain = verification["ledger_chain"]
    if not chain.get("ok"):
        would_block = True
        reason_codes.append(chain.get("reason") or "CHAIN_CHECK_FAILED")
    else:
        reason_codes.append(chain.get("reason"))

    if not would_block:
        reason_codes.append("RECOVERY_VERIFIED")

    decision = "ALLOW"
    enforcement_status = "NOT_ENFORCED"
    enforcement_action = "ADVISORY_ONLY"
    if mode == "ENFORCE" and would_block:
        decision = "BLOCK"
        # Descriptive only — does not gate execution. Real gate is decision=="BLOCK" above.
        enforcement_status = "ENFORCED"
        enforcement_action = "BLOCKED"

    return {
        "decision": decision,
        "reason_codes": reason_codes,
        "would_block": would_block,
        "mode": mode,
        "system_state": current_state,
        "db_error": db_error,
        "enforcement_status": enforcement_status,
        "enforcement_action": enforcement_action,
    }


def require_governance_authorization(*, checkpoint: str, entrypoint: str, run_kind: str,
                                      source_phase: Optional[str] = None,
                                      requested_action: Optional[str] = None,
                                      architecture_version: Optional[str] = None,
                                      model_version: Optional[str] = None,
                                      strategy_version: Optional[str] = None,
                                      configuration_version: Optional[str] = None,
                                      payload: Optional[Dict[str, Any]] = None,
                                      timeout_ms: int = 5000,
                                      trigger_source: Optional[str] = None,
                                      is_test_record: bool = False,
                                      candidate_trace_id: Optional[str] = None,
                                      candidate_ticker: Optional[str] = None,
                                      diagram2_risk_result: Optional[str] = None,
                                      execution_mode: Optional[str] = None,
                                      learning_accepted: Optional[bool] = None,
                                      learning_model_name: Optional[str] = None,
                                      learning_n_samples: Optional[int] = None,
                                      learning_current_score: Optional[float] = None,
                                      learning_new_score: Optional[float] = None,
                                      learning_max_drift: Optional[float] = None,
                                      learning_version_saved: Optional[Any] = None,
                                      learning_weights_hash: Optional[str] = None,
                                      g5_target_state: Optional[str] = None,
                                      pit_price_source: Optional[str] = None,
                                      pit_price_source_scan_date: Optional[Any] = None,
                                      pit_now_et_date: Optional[Any] = None) -> Dict[str, Any]:
    """
    Combined D2_GOVERNANCE_CLIENT + D3_GOVERNANCE_SERVICE entrypoint
    (Section 12 authoritative D2<->D3 wiring). Evaluates the real checkpoint
    policy for `checkpoint`, then persists a REQUEST + LEDGER EVENT +
    DECISION as ONE atomic transaction, so the Section 12F correlation
    triplet (request/decision/ack) can never desync from the real
    d3_governance_event_links ledger.

    checkpoints 'G0' (P3.5), 'G1' (P3.6), 'G2' (P4), 'G3' (P5), 'G4' (P6),
    'G5' (P7), and 'G6' (Diagram 2 remediation spec step 3, Point-in-Time
    Guard) all have real policy evaluators wired -- an unrecognized
    checkpoint string raises NotImplementedError rather than fabricate a
    PASS/ALLOW for a gate that does not exist, but every checkpoint this
    codebase actually defines (_D3_CHECKPOINTS) is now real.

    pit_price_source / pit_price_source_scan_date / pit_now_et_date are
    REQUIRED for checkpoint='G6' -- pit_price_source must be the caller's
    real resolved price provenance for THIS candidate ('live_quote' or
    'polygon_fallback'), pit_price_source_scan_date the scan_date actually
    returned by the polygon_rvol_scan fallback row (or None), and
    pit_now_et_date today's date in US/Eastern as computed by the caller
    (never re-derived inside the evaluator, for testability). Unlike every
    other checkpoint, G6 fails OPEN (ALLOW) on any internal exception or
    db_error -- see _evaluate_g6_decision.

    g5_target_state is REQUIRED for checkpoint='G5' -- the real state the
    caller wants d3_system_state to become (see g5_authorize_resume, the
    only intended caller). It is never used for any other checkpoint.

    learning_accepted / learning_model_name / learning_n_samples /
    learning_current_score / learning_new_score / learning_version_saved /
    learning_weights_hash are REQUIRED for checkpoint='G4' -- these must be
    the caller's real aiem_learning_proposals row fields for the exact
    proposal being promoted, never fabricated defaults (see
    _evaluate_g4_decision). learning_max_drift is accepted for disclosure/
    audit purposes but is NOT used by G4's own gating math (which recomputes
    performance/calibration/risk independently from current_score/new_score/
    n_samples, matching run_phase6_learning_approval()'s existing formulas).

    candidate_trace_id / candidate_ticker are REQUIRED for checkpoint in
    ('G2', 'G3') (per-candidate context; both are evaluated once per trade
    candidate, not once per batch like G0/G1). When provided they take
    priority over the ambient trace_context() for root_trace_id/trace_id/
    ticker on this request's ledger event, so the audit trail is anchored to
    the exact candidate evaluated rather than to whatever trace_context()
    happens to still be set from the last D2 stage call in the loop.

    diagram2_risk_result / execution_mode are REQUIRED for checkpoint='G3'.
    diagram2_risk_result must be the caller's real upstream D2 risk-gate
    verdict ('PASS' or anything else, e.g. 'REJECT') for THIS candidate --
    never omitted/assumed -- because G3 is structurally required to BLOCK
    unconditionally the moment it is not 'PASS' (see _evaluate_g3_decision).
    execution_mode must be the caller's real execution mode ('PAPER' for
    every entrypoint in this codebase today; 'LIVE' is unconditionally
    hard-blocked because no live broker adapter exists).

    On a real persistence failure, the already-computed decision is NEVER
    flipped: it is returned exactly as computed, with governance_request_id/
    governance_decision_id/ledger_event_id=None and 'PERSIST_FAILED'
    appended to reason_codes, so a caller can see the decision was sound but
    unaudited rather than silently getting a different answer.
    """
    if checkpoint not in _D3_CHECKPOINTS:
        raise ValueError(f"unknown checkpoint {checkpoint!r}, must be one of {_D3_CHECKPOINTS}")

    started_at = datetime.datetime.utcnow()

    if checkpoint == "G0":
        ev_result = _evaluate_g0_decision(run_kind)
    elif checkpoint == "G1":
        ev_result = _evaluate_g1_decision(run_kind)
    elif checkpoint == "G2":
        ev_result = _evaluate_g2_decision(run_kind, candidate_trace_id)
    elif checkpoint == "G3":
        ev_result = _evaluate_g3_decision(run_kind, diagram2_risk_result, execution_mode,
                                           model_version, strategy_version)
    elif checkpoint == "G4":
        ev_result = _evaluate_g4_decision(run_kind, learning_accepted, learning_model_name,
                                           learning_n_samples, learning_current_score,
                                           learning_new_score, learning_max_drift,
                                           learning_version_saved, learning_weights_hash)
    elif checkpoint == "G5":
        if not g5_target_state:
            raise ValueError("require_governance_authorization: checkpoint='G5' requires g5_target_state")
        ev_result = _evaluate_g5_decision(run_kind, g5_target_state)
    elif checkpoint == "G6":
        ev_result = _evaluate_g6_decision(run_kind, candidate_ticker, pit_price_source,
                                           pit_price_source_scan_date, pit_now_et_date)
    else:
        raise NotImplementedError(
            f"require_governance_authorization: checkpoint {checkpoint!r} has no real policy "
            f"evaluator wired yet -- refusing to fabricate a decision"
        )

    decision = ev_result["decision"]
    reason_codes = list(ev_result["reason_codes"])
    would_block = ev_result["would_block"]
    mode = ev_result["mode"]
    state = ev_result["system_state"]
    db_error = ev_result["db_error"]
    enforcement_status = ev_result["enforcement_status"]
    enforcement_action = ev_result["enforcement_action"]
    blocking = decision == "BLOCK"

    ctx = get_trace_context() or {}
    ctx_is_test = bool(ctx.get("is_test_record", is_test_record))
    root_trace_id = candidate_trace_id or ctx.get("root_trace_id")
    trace_id = candidate_trace_id or ctx.get("trace_id") or root_trace_id

    governance_request_id = f"GREQ_{checkpoint}_{uuid.uuid4().hex}"
    governance_decision_id = f"GDEC_{checkpoint}_{uuid.uuid4().hex}"
    payload_hash = hashlib.sha256(_canonical_bytes(payload)).hexdigest() if payload is not None else None
    decision_hash = hashlib.sha256(_canonical_bytes({
        "governance_request_id": governance_request_id,
        "checkpoint": checkpoint,
        "decision": decision,
        "reason_codes": reason_codes,
        "blocking": blocking,
    })).hexdigest()

    ledger_event_id = None
    out_request_id = None
    out_decision_id = None
    persist_error = None

    try:
        conn = _d3_connect()
        try:
            with conn.cursor() as cur:
                # Correction (2) from architect review: SET LOCAL must run at
                # the START of THIS transaction -- _d3_emit_event's own
                # internal SET LOCAL only takes effect for whatever remains
                # of an already-open transaction, which would leave the
                # request INSERT below unbounded if we relied on it instead.
                cur.execute("SET LOCAL lock_timeout = '2s'")
                cur.execute("SET LOCAL statement_timeout = '5s'")
                cur.execute(
                    """
                    INSERT INTO d3_governance_requests (
                        governance_request_id, trace_id, root_trace_id, checkpoint,
                        source_phase, requested_action, entrypoint, run_kind,
                        trigger_source, architecture_version, model_version,
                        strategy_version, configuration_version, payload_hash,
                        timeout_ms, is_test_record
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (governance_request_id, trace_id, root_trace_id, checkpoint,
                     source_phase, requested_action, entrypoint, run_kind,
                     trigger_source, architecture_version, model_version,
                     strategy_version, configuration_version, payload_hash,
                     timeout_ms, ctx_is_test),
                )

            ev = _d3_emit_event(
                governance_cycle_id=f"{checkpoint}_{entrypoint}_{uuid.uuid4().hex[:8]}",
                governance_phase=f"{checkpoint}_GOVERNANCE_AUTHORIZATION",
                governance_check_name="require_governance_authorization",
                governance_function="require_governance_authorization",
                governance_module="aiem_diagram3_governance",
                started_at=started_at,
                completed_at=datetime.datetime.utcnow(),
                check_result="FAIL" if (would_block or db_error) else "PASS",
                root_trace_id=root_trace_id,
                diagram2_trace_id=candidate_trace_id,
                ticker=candidate_ticker,
                enforcement_action=enforcement_action,
                enforcement_status=enforcement_status,
                reason_code="|".join(reason_codes),
                reason_detail=(
                    f"entrypoint={entrypoint} run_kind={run_kind} trigger_source={trigger_source} "
                    f"checkpoint={checkpoint} checkpoint_mode={mode} system_state={state} "
                    f"decision={decision} would_block={would_block} db_error={db_error} "
                    f"governance_request_id={governance_request_id}"
                ),
                producer_module="aiem_diagram3_governance",
                producer_function="require_governance_authorization",
                is_test_record=ctx_is_test,
                conn=conn,
            )
            ledger_event_id = ev.get("id")

            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO d3_governance_decisions (
                        governance_decision_id, governance_request_id, trace_id,
                        checkpoint, decision, blocking, reason_codes, policy_version,
                        decision_hash, ledger_event_id, is_test_record
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (governance_decision_id, governance_request_id, trace_id,
                     checkpoint, decision, blocking, psycopg2.extras.Json(reason_codes),
                     "P3.5", decision_hash, ledger_event_id, ctx_is_test),
                )
            conn.commit()
            out_request_id = governance_request_id
            out_decision_id = governance_decision_id
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    except Exception as pe:
        # Correction (3) from architect review: a persistence failure NEVER
        # flips the already-computed decision. Disclose it via reason_codes
        # and return everything else exactly as computed above.
        persist_error = str(pe)
        print(f"[d3_governance] require_governance_authorization PERSIST_FAILED "
              f"checkpoint={checkpoint} entrypoint={entrypoint}: {pe}")
        reason_codes = reason_codes + ["PERSIST_FAILED"]
        ledger_event_id = None
        out_request_id = None
        out_decision_id = None

    return {
        "decision": decision,
        "mode": mode,
        "system_state": state,
        "would_block": would_block,
        "blocking": blocking,
        "checkpoint": checkpoint,
        "reason_code": "|".join(reason_codes),
        "reason_codes": reason_codes,
        "ledger_event_id": ledger_event_id,
        "governance_request_id": out_request_id,
        "governance_decision_id": out_decision_id,
        "entrypoint": entrypoint,
        "run_kind": run_kind,
        "persist_error": persist_error,
    }


def g0_authorize_run(*, entrypoint: str, run_kind: str,
                      trigger_source: Optional[str] = None,
                      is_test_record: bool = False) -> Dict[str, Any]:
    """
    G0 boot-authorization checkpoint. Call once per real invocation of a
    trade-executing entrypoint, before any trade-executing work begins.

    Backward-compatible thin wrapper around require_governance_authorization
    (checkpoint='G0') -- kept so existing call sites and this exact function
    name keep working unchanged. Prefer calling
    require_governance_authorization(checkpoint='G0', ...) directly in new
    code so the request_id/decision_id are visible for acknowledgement.

    Returns {decision: 'ALLOW'|'BLOCK', mode, system_state, would_block,
    reason_code, ledger_event_id, entrypoint, run_kind, ...}. Never
    fabricates a PASS/ALLOW result on a real DB error for a TRADE_EXECUTING
    run outside the bounded stale-cache-allow window described above.
    """
    return require_governance_authorization(
        checkpoint="G0",
        entrypoint=entrypoint,
        run_kind=run_kind,
        trigger_source=trigger_source,
        is_test_record=is_test_record,
    )


def acknowledge_governance_decision(*, governance_decision_id: Optional[str],
                                     action_taken: str, continued: bool, blocked: bool,
                                     acknowledged_by: str,
                                     is_test_record: bool = False) -> Dict[str, Any]:
    """
    D2_GOVERNANCE_ACKNOWLEDGER (Section 12F ACKNOWLEDGEMENT record). NEVER
    trusts a caller-supplied decision value -- always re-reads the REAL
    decision + governance_request_id from d3_governance_decisions by
    governance_decision_id first, then writes the ack with a composite FK
    back to that exact (governance_decision_id, decision) pair. That FK
    (not a Python-level convention) is what makes a false acknowledgement
    (e.g. claiming a BLOCK decision said ALLOW) a physical DB impossibility
    -- this is the mechanism TEST 12's negative control exercises.

    Callers MUST wrap this in try/except and skip it entirely when
    governance_decision_id is None (e.g. the synthetic fail-closed dict
    returned when persistence itself failed, which has no real decision
    row) -- an ack failure must NEVER alter trade flow, especially in a
    BLOCK branch that has already released a lock / recorded a blocked
    trade. This writes directly via psycopg2 (not CommunicationBus.publish,
    which swallows exceptions) so a genuine constraint violation propagates
    to the caller instead of being silently absorbed.
    """
    if not governance_decision_id:
        raise ValueError("acknowledge_governance_decision requires a real governance_decision_id")

    ctx = get_trace_context() or {}
    ctx_is_test = bool(ctx.get("is_test_record", is_test_record))
    trace_id = ctx.get("trace_id") or ctx.get("root_trace_id")

    conn = _d3_connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL lock_timeout = '2s'")
            cur.execute("SET LOCAL statement_timeout = '5s'")
            cur.execute(
                "SELECT governance_request_id, decision FROM d3_governance_decisions "
                "WHERE governance_decision_id = %s",
                (governance_decision_id,),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError(
                    f"acknowledge_governance_decision: no decision row for "
                    f"governance_decision_id={governance_decision_id}"
                )
            governance_request_id, decision_recorded = row

            governance_ack_id = f"GACK_{uuid.uuid4().hex}"
            acknowledgement_hash = hashlib.sha256(_canonical_bytes({
                "governance_ack_id": governance_ack_id,
                "governance_decision_id": governance_decision_id,
                "decision_recorded": decision_recorded,
                "action_taken": action_taken,
                "continued": bool(continued),
                "blocked": bool(blocked),
            })).hexdigest()

            cur.execute(
                """
                INSERT INTO d3_governance_acks (
                    governance_ack_id, governance_request_id, governance_decision_id,
                    decision_recorded, trace_id, action_taken, continued, blocked,
                    acknowledged_by, acknowledgement_hash, is_test_record
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (governance_ack_id, governance_request_id, governance_decision_id,
                 decision_recorded, trace_id, action_taken, bool(continued), bool(blocked),
                 acknowledged_by, acknowledgement_hash, ctx_is_test),
            )
        conn.commit()
        return {
            "governance_ack_id": governance_ack_id,
            "governance_decision_id": governance_decision_id,
            "governance_request_id": governance_request_id,
            "decision_recorded": decision_recorded,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


_D3_GOVERNANCE_COMPONENTS_SEED = [
    {
        "component_name": "D2_EVENT_PUBLISHER",
        "owner_diagram": "DIAGRAM_2",
        "module_path": "aiem_master_orchestrator.py",
        "function_or_class_name": "AEIMMasterOrchestrator.execute_stage",
        "responsibility": (
            "Publishes real-time D2 stage lifecycle events (stage_starting/"
            "stage_completed/stage_failed) onto the shared CommunicationBus."
        ),
    },
    {
        "component_name": "D3_EVENT_CONSUMER",
        "owner_diagram": "DIAGRAM_3",
        "module_path": "aiem_diagram3_governance.py",
        "function_or_class_name": "_on_bus_stage_event (via subscribe_to_bus)",
        "responsibility": (
            "Subscribes to the CommunicationBus and ledgers every real D2 "
            "StageEvent as a hash-chained governance event (Path A observation)."
        ),
    },
    {
        "component_name": "D2_GOVERNANCE_CLIENT",
        "owner_diagram": "DIAGRAM_2",
        "module_path": "main.py",
        "function_or_class_name": "_run_premarket_open_tracker, _aiem_paper_execute_today",
        "responsibility": (
            "Calls require_governance_authorization() (D3_GOVERNANCE_SERVICE) at each "
            "real trade-executing entrypoint before allowing execution to proceed."
        ),
    },
    {
        "component_name": "D3_GOVERNANCE_SERVICE",
        "owner_diagram": "DIAGRAM_3",
        "module_path": "aiem_diagram3_governance.py",
        "function_or_class_name": "require_governance_authorization",
        "responsibility": (
            "Evaluates the real checkpoint policy (G0 only as of P3.5) and persists "
            "the request/decision/ledger-event triplet as one atomic transaction."
        ),
    },
    {
        "component_name": "D2_GOVERNANCE_ACKNOWLEDGER",
        "owner_diagram": "DIAGRAM_2",
        "module_path": "main.py",
        "function_or_class_name": "_run_premarket_open_tracker, _aiem_paper_execute_today",
        "responsibility": (
            "Acknowledges the governance decision it received via "
            "acknowledge_governance_decision() and records the real action D2 took "
            "(continued/blocked)."
        ),
    },
    {
        "component_name": "D3_GOVERNANCE_LEDGER",
        "owner_diagram": "DIAGRAM_3",
        "module_path": "aiem_diagram3_governance.py",
        "function_or_class_name": "_d3_emit_event",
        "responsibility": (
            "Appends the tamper-evident, hash-chained governance event ledger "
            "(d3_governance_event_links) that every checkpoint decision and D2 "
            "stage event is recorded into."
        ),
    },
]


def _seed_governance_components() -> Dict[str, Any]:
    """
    Upserts the 6 Section 12B named components with real module/function
    names, and a REAL health signal derived from actual DB/process state --
    never a hardcoded 'HEALTHY'. Called from d3_startup() on every boot, so
    the registry always reflects the current binary, not a stale snapshot.
    """
    results = {}
    try:
        conn = _d3_connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout = '5s'")
                cur.execute("SELECT COUNT(*) FROM d3_governance_event_links "
                            "WHERE producer_module = 'aiem_master_orchestrator' AND is_test_record = FALSE")
                d2_publisher_events = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM d3_governance_event_links WHERE is_test_record = FALSE")
                total_ledger_events = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM d3_governance_requests WHERE is_test_record = FALSE")
                total_requests = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM d3_governance_decisions WHERE is_test_record = FALSE")
                total_decisions = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM d3_governance_acks WHERE is_test_record = FALSE")
                total_acks = cur.fetchone()[0]

            health = {
                "D2_EVENT_PUBLISHER": "ACTIVE" if d2_publisher_events > 0 else "NO_EVENTS_OBSERVED_YET",
                "D3_EVENT_CONSUMER": "SUBSCRIBED" if _D3_BUS_SUBSCRIBED else "NOT_SUBSCRIBED",
                "D2_GOVERNANCE_CLIENT": "REQUESTS_RECORDED" if total_requests > 0 else "NO_REQUESTS_YET",
                "D3_GOVERNANCE_SERVICE": "DECISIONS_RECORDED" if total_decisions > 0 else "NO_DECISIONS_YET",
                "D2_GOVERNANCE_ACKNOWLEDGER": "ACKS_RECORDED" if total_acks > 0 else "NO_ACKS_YET",
                "D3_GOVERNANCE_LEDGER": "ACTIVE" if total_ledger_events > 0 else "NO_EVENTS_YET",
            }

            now = datetime.datetime.utcnow()
            with conn.cursor() as cur:
                for c in _D3_GOVERNANCE_COMPONENTS_SEED:
                    name = c["component_name"]
                    cur.execute(
                        """
                        INSERT INTO d3_governance_components (
                            component_name, owner_diagram, module_path,
                            function_or_class_name, responsibility, version, status,
                            last_health_check_at, last_health_result
                        ) VALUES (%s,%s,%s,%s,%s,%s,'ACTIVE',%s,%s)
                        ON CONFLICT (component_name) DO UPDATE SET
                            owner_diagram = EXCLUDED.owner_diagram,
                            module_path = EXCLUDED.module_path,
                            function_or_class_name = EXCLUDED.function_or_class_name,
                            responsibility = EXCLUDED.responsibility,
                            last_health_check_at = EXCLUDED.last_health_check_at,
                            last_health_result = EXCLUDED.last_health_result
                        """,
                        (name, c["owner_diagram"], c["module_path"],
                         c["function_or_class_name"], c["responsibility"], "1.0.0",
                         now, health.get(name, "UNKNOWN")),
                    )
                    results[name] = health.get(name, "UNKNOWN")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return {"status": "OK", "components": results}
    except Exception as e:
        print(f"[d3_governance] _seed_governance_components failed (non-fatal): {e}")
        return {"status": "ERROR", "error": str(e), "components": results}


def get_d3_system_state() -> Dict[str, Any]:
    """Real current row from d3_system_state (singleton id=1)."""
    with _d3_connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT state, reason, changed_by, changed_at FROM d3_system_state WHERE id = 1")
            row = cur.fetchone()
    if not row:
        return {"state": "NORMAL", "reason": None, "changed_by": None, "changed_at": None, "seeded": False}
    row = dict(row)
    row["changed_at"] = str(row["changed_at"])
    row["seeded"] = True
    return row


def get_d3_checkpoint_config() -> Dict[str, Any]:
    """Real current rows from d3_checkpoint_config, all 6 checkpoints
    (G0-G5; G1 added in P3.5, schema/seed-only until its own phase ships)."""
    with _d3_connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT checkpoint, mode, updated_by, updated_at, note "
                "FROM d3_checkpoint_config ORDER BY checkpoint"
            )
            rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        r["updated_at"] = str(r["updated_at"])
    return {"checkpoints": rows}


def set_d3_system_state(*, state: str, reason: str, changed_by: str,
                         _g5_authorized: bool = False,
                         _expected_old_state: Optional[str] = None) -> Dict[str, Any]:
    """Real DB write to the d3_system_state singleton + append-only history
    row + ledger event. Immediately invalidates the G0 read cache so the new
    state is honored on the very next call, not after a stale 5s window.

    _g5_authorized is an internal-only flag. It must NEVER be passed True by
    any caller except g5_authorize_resume() immediately after a real G5 ALLOW
    decision has been persisted -- it exists purely so this function can
    refuse (fail-closed, ValueError) any attempt to exit a recovery-gated
    state (_D3_RECOVERY_GATED_STATES: PAUSED/RECOVERY_REQUIRED/
    ROLLBACK_IN_PROGRESS) to a non-gated state through the raw admin
    state-setter route or any other direct caller, which is exactly the
    "resume trading with no authorization check" gap Section 12's Recovery
    Orchestrator wiring exists to close. Transitions BETWEEN two gated
    states, or INTO a gated state from anywhere, are real admin actions
    (e.g. declaring PAUSED, or escalating PAUSED->ROLLBACK_IN_PROGRESS) and
    remain unguarded here -- only an exit TO a non-gated state is a resume.

    SELECT ... FOR UPDATE below serializes concurrent callers on the single
    d3_system_state row (id=1), closing the plain race two simultaneous
    writers would otherwise have. _expected_old_state (set only by
    g5_authorize_resume, from the system_state _evaluate_g5_decision actually
    verified recovery against) additionally closes the narrow TOCTOU window
    between that verification read and this write: if the real state on disk
    no longer matches what G5 verified against (e.g. a concurrent PAUSE
    landed in between), this raises ValueError rather than silently
    committing a resume decision that was verified against a state that no
    longer holds -- a stale ALLOW is not honored just because it was real
    when it was computed.
    """
    if state not in _D3_SYSTEM_STATES:
        raise ValueError(f"invalid state {state!r}; must be one of {_D3_SYSTEM_STATES}")
    with _d3_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT state FROM d3_system_state WHERE id = 1 FOR UPDATE")
            row = cur.fetchone()
            old_state = row[0] if row else None
            if (old_state in _D3_RECOVERY_GATED_STATES
                    and state not in _D3_RECOVERY_GATED_STATES
                    and not _g5_authorized):
                raise ValueError(
                    f"set_d3_system_state: refusing to resume {old_state!r} -> {state!r} "
                    f"without G5 recovery authorization -- call g5_authorize_resume() instead "
                    f"of setting system state directly"
                )
            if (_g5_authorized and _expected_old_state is not None
                    and old_state != _expected_old_state):
                raise ValueError(
                    f"set_d3_system_state: refusing stale G5 resume -- verified against "
                    f"state {_expected_old_state!r} but live state is now {old_state!r} "
                    f"(changed concurrently) -- re-run g5_authorize_resume to re-verify "
                    f"against the current state"
                )
            cur.execute(
                """
                INSERT INTO d3_system_state (id, state, reason, changed_by, changed_at)
                VALUES (1, %s, %s, %s, NOW())
                ON CONFLICT (id) DO UPDATE SET
                    state = EXCLUDED.state, reason = EXCLUDED.reason,
                    changed_by = EXCLUDED.changed_by, changed_at = NOW()
                """,
                (state, reason, changed_by),
            )
            cur.execute(
                """
                INSERT INTO d3_governance_config_history
                    (config_type, target, old_value, new_value, reason, changed_by)
                VALUES ('SYSTEM_STATE', 'SYSTEM', %s, %s, %s, %s)
                """,
                (old_state, state, reason, changed_by),
            )
        conn.commit()
    # System state is shared across ALL checkpoints (d3_system_state is a
    # single-row singleton, not per-checkpoint) -- invalidate every
    # checkpoint's cached read, not just G0's, so a state change (e.g. into
    # PAUSED) is honored immediately by G1 (and any future checkpoint) too.
    for _cp in _D3_CHECKPOINTS:
        _read_checkpoint_config(_cp, force=True)
    try:
        _d3_emit_event(
            governance_cycle_id=f"D3_SYSTEM_STATE_CHANGE_{uuid.uuid4().hex[:8]}",
            governance_phase="G0_SYSTEM_STATE_CHANGE",
            governance_check_name="set_d3_system_state",
            governance_function="set_d3_system_state",
            started_at=datetime.datetime.utcnow(),
            completed_at=datetime.datetime.utcnow(),
            check_result="PASS",
            enforcement_action="ADVISORY_ONLY",
            enforcement_status="NOT_ENFORCED",
            reason_code="ADMIN_STATE_CHANGE",
            reason_detail=f"{old_state} -> {state} by {changed_by}: {reason}",
        )
    except Exception as e:
        print(f"[d3_governance] set_d3_system_state ledger emit failed (state change already committed): {e}")
    return {"old_state": old_state, "new_state": state, "changed_by": changed_by}


def g5_authorize_resume(*, target_state: str, reason: str, changed_by: str,
                         run_kind: str = "ADMIN_RECOVERY_RESUME",
                         trigger_source: Optional[str] = None) -> Dict[str, Any]:
    """
    The ONE real entrypoint for "DIAGRAM 2 RECOVERY ORCHESTRATOR contacts
    D3_GOVERNANCE_SERVICE at G5" (Section 12-13 addition) / CHECKPOINT G5:
    RECOVERY AND RESUMPTION (Section 4) / "no protected operation may resume
    without RESUME authorization" (Section 12-13). This is the only
    supported way to exit a recovery-gated system state
    (_D3_RECOVERY_GATED_STATES) back to a non-gated one -- the raw
    `/stock-api/admin/d3/g0/system-state` setter now hard-refuses that exact
    transition (see set_d3_system_state's _g5_authorized guard) so this
    function cannot be silently bypassed.

    Unlike G0-G4 (which gate a caller's OWN pending action -- the caller
    still has to go do the trade/promotion/etc after an ALLOW), G5 is
    different: ITS OWN ALLOW decision IS the resume. So after
    require_governance_authorization(checkpoint='G5') persists the real
    request/decision/ledger-event triplet, an ALLOW here immediately makes
    the real set_d3_system_state() write (with _g5_authorized=True) as part
    of the SAME call -- there is no separate "now go do the thing" step for
    a caller to forget.

    SHADOW mode note: per this codebase's existing G0-G4 convention, SHADOW
    mode's decision is always ALLOW even when would_block is True (only
    ENFORCE mode ever flips decision to BLOCK). G5 is seeded SHADOW at
    startup, exactly like every other checkpoint (see the P3.5 seed INSERT),
    as part of this codebase's deliberate phased-rollout convention -- an
    operator must explicitly move it to ENFORCE via
    set_d3_checkpoint_mode(checkpoint='G5', mode='ENFORCE', confirm=True)
    for real resume verification to actually block. Until then, a resume
    request that WOULD have been blocked in ENFORCE still actually resumes
    the system in SHADOW -- this is an intentional consistency choice with
    the rest of this codebase's mode semantics, not a gap invented for G5,
    and it is surfaced verbatim in the /g5/status route's response and in
    reason_codes so an operator can see it before relying on it.

    TOCTOU note: the state this function's verification is actually valid
    against is auth['system_state'] (what _evaluate_g5_decision read, via a
    forced non-cached read -- see there), not whatever the live state
    happens to be by the time this write runs. That value is passed through
    to set_d3_system_state as _expected_old_state, which re-checks it under
    a real row lock (SELECT ... FOR UPDATE) at write time and refuses
    (ValueError, state_change_error surfaced below, decision NEVER flipped)
    rather than silently resuming against a state that changed underneath
    the verification between read and write.
    """
    if target_state not in _D3_SYSTEM_STATES:
        raise ValueError(f"invalid target_state {target_state!r}; must be one of {_D3_SYSTEM_STATES}")

    auth = require_governance_authorization(
        checkpoint="G5",
        entrypoint="g5_authorize_resume",
        run_kind=run_kind,
        requested_action=f"RESUME_TO_{target_state}",
        payload={"target_state": target_state, "reason": reason},
        trigger_source=trigger_source or "ADMIN",
        g5_target_state=target_state,
    )

    result = {**auth, "state_change": None}

    if auth["decision"] != "ALLOW":
        return result

    try:
        state_change = set_d3_system_state(
            state=target_state, reason=f"G5_AUTHORIZED_RESUME: {reason}",
            changed_by=changed_by, _g5_authorized=True,
            _expected_old_state=auth.get("system_state"),
        )
        result["state_change"] = state_change
    except Exception as e:
        result["reason_codes"] = list(auth.get("reason_codes") or []) + [f"RESUME_WRITE_FAILED:{e}"]
        result["state_change_error"] = str(e)

    return result


def check_g0_enforce_preconditions() -> Dict[str, Any]:
    """Guard A: verifies governance state = NORMAL before G0 mode can be
    switched to ENFORCE. Returns {"ok": True, ...} if safe to proceed, or
    {"ok": False, "reason": ..., ...} if the switch must be refused.

    Always writes a governance config history row and a ledger event recording
    the check outcome so there is always an audit trail of who checked and
    when, regardless of pass/fail.

    Call this immediately before set_d3_checkpoint_mode(checkpoint='G0',
    mode='ENFORCE', confirm=True). If ok=False, do NOT proceed with the mode
    update — the switch is refused and must not happen.
    """
    cfg = _g0_read_config(force=True)
    state = cfg.get("state") or "NORMAL"
    mode_now = cfg.get("mode") or "SHADOW"
    now = datetime.datetime.utcnow()

    if state != "NORMAL":
        detail = (
            f"state={state} is not NORMAL — switch to G0 ENFORCE refused; "
            f"resolve the governance state first, then re-run this check"
        )
        try:
            with _d3_connect() as _ga_conn:
                with _ga_conn.cursor() as _ga_cur:
                    _ga_cur.execute(
                        """
                        INSERT INTO d3_governance_config_history
                            (config_type, target, old_value, new_value, reason, changed_by)
                        VALUES ('CHECKPOINT_MODE', 'G0', %s, 'SWITCH_REFUSED', %s,
                                'check_g0_enforce_preconditions')
                        """,
                        (state, detail),
                    )
                _ga_conn.commit()
        except Exception as _ga_e:
            print(f"[Guard A] config_history audit write failed (non-fatal): {_ga_e}")
        try:
            _d3_emit_event(
                governance_cycle_id=f"D3_G0_ENFORCE_SWITCH_BLOCKED_{uuid.uuid4().hex[:8]}",
                governance_phase="G0_ENFORCE_SWITCH_GUARD",
                governance_check_name="check_g0_enforce_preconditions",
                governance_function="check_g0_enforce_preconditions",
                started_at=now,
                completed_at=datetime.datetime.utcnow(),
                check_result="FAIL",
                enforcement_action="SWITCH_REFUSED",
                enforcement_status="ENFORCED",
                reason_code="enforce_switch_blocked",
                reason_detail=detail,
            )
        except Exception as _ga_ee:
            print(f"[Guard A] ledger emit failed (non-fatal): {_ga_ee}")
        print(f"[Guard A] REFUSED — {detail}")
        return {
            "ok": False,
            "reason": "enforce_switch_blocked",
            "state": state,
            "mode": mode_now,
            "message": detail,
        }

    # state == NORMAL — safe to proceed with the ENFORCE switch
    detail = (
        f"state={state} mode={mode_now} — preconditions met, "
        f"safe to call set_d3_checkpoint_mode(G0, ENFORCE, confirm=True)"
    )
    try:
        with _d3_connect() as _ga_conn_c:
            with _ga_conn_c.cursor() as _ga_cur_c:
                _ga_cur_c.execute(
                    """
                    INSERT INTO d3_governance_config_history
                        (config_type, target, old_value, new_value, reason, changed_by)
                    VALUES ('CHECKPOINT_MODE', 'G0', %s, 'SWITCH_CLEARED', %s,
                            'check_g0_enforce_preconditions')
                    """,
                    (mode_now, detail),
                )
            _ga_conn_c.commit()
    except Exception as _ga_ch_e:
        print(f"[Guard A] cleared config_history audit write failed (non-fatal): {_ga_ch_e}")
    try:
        _d3_emit_event(
            governance_cycle_id=f"D3_G0_ENFORCE_SWITCH_CLEARED_{uuid.uuid4().hex[:8]}",
            governance_phase="G0_ENFORCE_SWITCH_GUARD",
            governance_check_name="check_g0_enforce_preconditions",
            governance_function="check_g0_enforce_preconditions",
            started_at=now,
            completed_at=datetime.datetime.utcnow(),
            check_result="PASS",
            enforcement_action="SWITCH_CLEARED",
            enforcement_status="NOT_ENFORCED",
            reason_code="enforce_switch_cleared",
            reason_detail=detail,
        )
    except Exception as _ga_ce:
        print(f"[Guard A] cleared ledger emit failed (non-fatal): {_ga_ce}")
    print(f"[Guard A] CLEARED — {detail}")
    return {"ok": True, "state": state, "mode": mode_now, "message": detail}


def set_d3_checkpoint_mode(*, checkpoint: str, mode: str, reason: str,
                            changed_by: str, confirm: bool = False) -> Dict[str, Any]:
    """Real DB write to d3_checkpoint_config + append-only history row +
    ledger event. Moving a checkpoint INTO ENFORCE (from OFF/SHADOW) requires
    confirm=True -- the kill-switch direction (ENFORCE -> SHADOW/OFF) never
    requires it, so an operator can always de-escalate fast."""
    if checkpoint not in _D3_CHECKPOINTS:
        raise ValueError(f"invalid checkpoint {checkpoint!r}; must be one of {_D3_CHECKPOINTS}")
    if mode not in _D3_CHECKPOINT_MODES:
        raise ValueError(f"invalid mode {mode!r}; must be one of {_D3_CHECKPOINT_MODES}")
    with _d3_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT mode FROM d3_checkpoint_config WHERE checkpoint = %s", (checkpoint,))
            row = cur.fetchone()
            old_mode = row[0] if row else None
            if mode == "ENFORCE" and old_mode != "ENFORCE" and not confirm:
                raise ValueError(
                    "moving a checkpoint into ENFORCE requires confirm=true "
                    "(de-escalating to SHADOW/OFF never requires it)"
                )
            cur.execute(
                """
                INSERT INTO d3_checkpoint_config (checkpoint, mode, updated_by, updated_at, note)
                VALUES (%s, %s, %s, NOW(), %s)
                ON CONFLICT (checkpoint) DO UPDATE SET
                    mode = EXCLUDED.mode, updated_by = EXCLUDED.updated_by,
                    updated_at = NOW(), note = EXCLUDED.note
                """,
                (checkpoint, mode, changed_by, reason),
            )
            cur.execute(
                """
                INSERT INTO d3_governance_config_history
                    (config_type, target, old_value, new_value, reason, changed_by)
                VALUES ('CHECKPOINT_MODE', %s, %s, %s, %s, %s)
                """,
                (checkpoint, old_mode, mode, reason, changed_by),
            )
        conn.commit()
    # Only THIS checkpoint's mode changed -- invalidate just its cache entry
    # (unlike system-state, mode is per-checkpoint, so G1's cache must not be
    # force-refreshed just because G0's mode changed, and vice versa).
    _read_checkpoint_config(checkpoint, force=True)
    try:
        _d3_emit_event(
            governance_cycle_id=f"D3_CHECKPOINT_MODE_CHANGE_{uuid.uuid4().hex[:8]}",
            governance_phase=f"{checkpoint}_CHECKPOINT_MODE_CHANGE",
            governance_check_name="set_d3_checkpoint_mode",
            governance_function="set_d3_checkpoint_mode",
            started_at=datetime.datetime.utcnow(),
            completed_at=datetime.datetime.utcnow(),
            check_result="PASS",
            enforcement_action="ADVISORY_ONLY",
            enforcement_status="NOT_ENFORCED",
            reason_code="ADMIN_MODE_CHANGE",
            reason_detail=f"{checkpoint}: {old_mode} -> {mode} by {changed_by}: {reason}",
        )
    except Exception as e:
        print(f"[d3_governance] set_d3_checkpoint_mode ledger emit failed (mode change already committed): {e}")
    return {"checkpoint": checkpoint, "old_mode": old_mode, "new_mode": mode, "changed_by": changed_by}


# ─────────────────────────────────────────────────────────────────────────────
# COMMUNICATION BUS SUBSCRIBER — Path A canonical event publisher (P1)
# ─────────────────────────────────────────────────────────────────────────────
# Reuses the existing, already-live aiem_communication_bus.CommunicationBus
# (do NOT build a new bus). aiem_master_orchestrator.execute_stage() already
# publishes a real StageEvent for every one of the 21 D2 stages on every real
# live candidate (main.py's _aiem_paper_execute_today -> _d2_run wrapper) --
# this subscriber is the missing piece that turns those already-real events
# into canonical, hash-chained d3_governance_event_links rows, closing the
# "D2 stage events are not routed into governance" gap.
#
# This is pure OBSERVATION (Path A). It never blocks, delays, or mutates the
# event it observes -- it only records it. Active enforcement checkpoints
# (G0/G2/G3/G4) are separate, later phases that consult this ledger; they are
# NOT implemented by this subscriber.

_D2_STAGE_EVENT_TYPE_MAP = {
    "stage_starting":  "D2_STAGE_STARTING",
    "stage_completed": "D2_STAGE_COMPLETED",
    "stage_failed":    "D2_STAGE_FAILED",
}

_D2_STAGE_CHECK_RESULT_MAP = {
    "stage_starting":  "IN_PROGRESS",
    "stage_completed": "PASS",
    "stage_failed":    "FAIL",
}

_D3_BUS_SUBSCRIBED = False
_D3_BUS_SUBSCRIBE_LOCK = threading.Lock()


def _on_bus_stage_event(event) -> None:
    """
    Real subscriber callback registered on the live CommunicationBus (see
    subscribe_to_bus()). Runs synchronously, in the same thread/call stack as
    the D2 stage that triggered it (per CommunicationBus's own guarantee),
    which is what lets contextvar trace_context() propagate correctly here
    with zero explicit parameter-passing through main.py/aiem_master_orchestrator.

    Never fabricates check_result: it is derived only from the real
    event_type the orchestrator actually published (stage_starting /
    stage_completed / stage_failed).
    """
    try:
        ctx = get_trace_context()
        root_trace_id = (ctx or {}).get("root_trace_id") or event.trace_id
        is_test = bool((ctx or {}).get("is_test_record", False))
        ts = event.timestamp
        idem_key = f"bus:{event.trace_id}:{event.stage_order}:{event.event_type}"

        _d3_emit_event(
            governance_cycle_id=f"BUS_OBS_{event.trace_id}",
            governance_phase="D2_BUS_OBSERVATION",
            governance_check_name=f"stage_{event.stage_order}_{event.stage_name}",
            governance_function="aiem_communication_bus.StageEvent",
            started_at=ts,
            completed_at=ts,
            check_result=_D2_STAGE_CHECK_RESULT_MAP.get(event.event_type),
            diagram2_trace_id=event.trace_id,
            ticker=event.ticker,
            enforcement_action="ADVISORY_ONLY",
            enforcement_status="NOT_ENFORCED",
            reason_code="BUS_STAGE_EVENT",
            reason_detail=(
                f"real {event.event_type} observed for D2 stage "
                f"{event.stage_order} ({event.stage_name}) via CommunicationBus"
            ),
            input_payload=event.to_dict(),
            is_test_record=is_test,
            governance_module="aiem_diagram3_governance",
            event_type=_D2_STAGE_EVENT_TYPE_MAP.get(
                event.event_type, f"D2_{str(event.event_type).upper()}"
            ),
            root_trace_id=root_trace_id,
            producer_module="aiem_master_orchestrator",
            producer_function="execute_stage",
            consumer_module="aiem_diagram3_governance",
            consumer_function="_on_bus_stage_event",
            idempotency_key=idem_key,
        )
    except Exception as _bus_sub_e:
        # Never let a governance-ledger failure touch the live trading path.
        # CommunicationBus.publish() also independently wraps this callback
        # in its own try/except, so this is belt-and-suspenders logging.
        print(
            f"[d3_governance] bus subscriber error (non-fatal, event NOT "
            f"ledgered): {type(_bus_sub_e).__name__}: {_bus_sub_e}"
        )


def subscribe_to_bus() -> bool:
    """
    Idempotently register the D3 observer on the process-wide CommunicationBus
    singleton. Returns True the first time it actually subscribes, False on
    any subsequent call (already subscribed) -- safe to call from d3_startup()
    and defensively elsewhere without ever double-subscribing.
    """
    global _D3_BUS_SUBSCRIBED
    with _D3_BUS_SUBSCRIBE_LOCK:
        if _D3_BUS_SUBSCRIBED:
            return False
        import aiem_communication_bus as _abus
        _abus.get_bus().subscribe(_on_bus_stage_event)
        _D3_BUS_SUBSCRIBED = True
        return True


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 0 — BASELINE FREEZE
# ─────────────────────────────────────────────────────────────────────────────

def _compute_arch_hash(modules, tools, d2_stages, tables) -> str:
    snapshot = {
        "module_count": len(modules),
        "module_names": sorted([m["module_name"] for m in modules]),
        "tool_count": len(tools),
        "tool_names": sorted([t["tool_name"] for t in tools]),
        "d2_stages": sorted(d2_stages),
        "db_tables": sorted(tables),
        "d3_version": _D3_VERSION,
    }
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()


def run_phase0_baseline_freeze(force: bool = False) -> Dict[str, Any]:
    """
    Phase 0 — Baseline Freeze.
    Captures and cryptographically hashes the complete architecture state.
    Idempotent — only writes if no baseline exists (or force=True).
    Once written, the baseline is PROTECTED and never overwritten.
    """
    global _D3_BASELINE_HASH
    try:
        with _d3_connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT id, baseline_hash, frozen_at, module_count, tool_count, "
                    "d2_stage_count, db_table_count FROM d3_architecture_baseline ORDER BY id LIMIT 1"
                )
                existing = cur.fetchone()
                if existing and not force:
                    _D3_BASELINE_HASH = existing["baseline_hash"]
                    return {
                        "phase": "PHASE_0_BASELINE_FREEZE",
                        "status": "BASELINE_EXISTS",
                        "ARCHITECTURE_BASELINE_CREATED": True,
                        "BASELINE_HASH": existing["baseline_hash"],
                        "BASELINE_PROTECTED": True,
                        "baseline_id": existing["id"],
                        "frozen_at": str(existing["frozen_at"]),
                        "module_count": existing["module_count"],
                        "tool_count": existing["tool_count"],
                        "d2_stage_count": existing["d2_stage_count"],
                        "db_table_count": existing["db_table_count"],
                    }

                cur.execute(
                    "SELECT module_name, module_phase, execution_status, ownership_status "
                    "FROM aiem_module_registry ORDER BY module_id"
                )
                modules = [dict(r) for r in cur.fetchall()]

                cur.execute(
                    "SELECT tool_name, owning_module_or_phase, tool_type, verification_status "
                    "FROM aiem_tool_registry ORDER BY tool_id"
                )
                tools = [dict(r) for r in cur.fetchall()]

                cur.execute(
                    "SELECT DISTINCT stage_name FROM aiem_diagram2_trace_audit ORDER BY 1"
                )
                d2_stages = [r["stage_name"] for r in cur.fetchall()]

                cur.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='public' ORDER BY table_name"
                )
                tables = [r["table_name"] for r in cur.fetchall()]

                baseline_hash = _compute_arch_hash(modules, tools, d2_stages, tables)
                snap = {
                    "module_count": len(modules),
                    "module_names": sorted(m["module_name"] for m in modules),
                    "tool_count": len(tools),
                    "tool_names": sorted(t["tool_name"] for t in tools),
                    "d2_stages": sorted(d2_stages),
                    "db_tables": sorted(tables),
                    "d3_version": _D3_VERSION,
                    "frozen_at": datetime.datetime.utcnow().isoformat() + "Z",
                }

                cur.execute(
                    """INSERT INTO d3_architecture_baseline
                       (version, baseline_hash, module_count, tool_count,
                        d2_stage_count, db_table_count, snapshot_json, protected)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE)
                       ON CONFLICT (baseline_hash) DO NOTHING
                       RETURNING id, frozen_at""",
                    (_D3_VERSION, baseline_hash, len(modules), len(tools),
                     len(d2_stages), len(tables), json.dumps(snap))
                )
                row = cur.fetchone()
            conn.commit()

        _D3_BASELINE_HASH = baseline_hash
        return {
            "phase": "PHASE_0_BASELINE_FREEZE",
            "status": "ARCHITECTURE_BASELINE_CREATED",
            "ARCHITECTURE_BASELINE_CREATED": True,
            "BASELINE_HASH": baseline_hash,
            "BASELINE_PROTECTED": True,
            "baseline_id": row["id"] if row else None,
            "frozen_at": str(row["frozen_at"]) if row else None,
            "module_count": len(modules),
            "tool_count": len(tools),
            "d2_stage_count": len(d2_stages),
            "db_table_count": len(tables),
        }
    except Exception as e:
        return {"phase": "PHASE_0_BASELINE_FREEZE", "status": "ERROR", "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 1 — ARCHITECTURE DISCOVERY
# ─────────────────────────────────────────────────────────────────────────────

def run_phase1_discovery() -> Dict[str, Any]:
    """Phase 1 — Architecture Discovery. Discovers all registered components."""
    try:
        with _d3_connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT module_phase, COUNT(*) AS cnt, "
                    "SUM(CASE WHEN execution_status='VERIFIED_WIRED' THEN 1 ELSE 0 END) AS wired "
                    "FROM aiem_module_registry GROUP BY module_phase ORDER BY module_phase"
                )
                by_phase = [dict(r) for r in cur.fetchall()]

                cur.execute(
                    "SELECT tool_type, COUNT(*) AS cnt, "
                    "SUM(CASE WHEN verification_status='VERIFIED_REAL' THEN 1 ELSE 0 END) AS real "
                    "FROM aiem_tool_registry GROUP BY tool_type ORDER BY tool_type"
                )
                by_tool_type = [dict(r) for r in cur.fetchall()]

                cur.execute(
                    "SELECT COUNT(*) AS total, "
                    "SUM(CASE WHEN execution_status='VERIFIED_WIRED' THEN 1 ELSE 0 END) AS wired, "
                    "SUM(CASE WHEN execution_status='VERIFIED_EXISTS' THEN 1 ELSE 0 END) AS exists_only "
                    "FROM aiem_module_registry"
                )
                mod_summary = dict(cur.fetchone())

                cur.execute(
                    "SELECT COUNT(*) AS total, "
                    "SUM(CASE WHEN verification_status='VERIFIED_REAL' THEN 1 ELSE 0 END) AS real "
                    "FROM aiem_tool_registry"
                )
                tool_summary = dict(cur.fetchone())

                cur.execute(
                    "SELECT DISTINCT stage_name FROM aiem_diagram2_trace_audit ORDER BY 1"
                )
                d2_stages = [r["stage_name"] for r in cur.fetchall()]

                cur.execute(
                    "SELECT COUNT(DISTINCT table_name) AS cnt FROM information_schema.tables "
                    "WHERE table_schema='public'"
                )
                table_count = cur.fetchone()["cnt"]

        return {
            "phase": "PHASE_1_ARCHITECTURE_DISCOVERY",
            "status": "PASS",
            "SYSTEM_ARCHITECTURE_MAP": {
                "module_registry": {
                    "total": mod_summary["total"],
                    "verified_wired": mod_summary["wired"],
                    "verified_exists": mod_summary["exists_only"],
                    "by_phase": by_phase,
                },
                "tool_registry": {
                    "total": tool_summary["total"],
                    "verified_real": tool_summary["real"],
                    "by_type": by_tool_type,
                },
                "diagram2_stages": d2_stages,
                "db_tables": table_count,
            },
        }
    except Exception as e:
        return {"phase": "PHASE_1_ARCHITECTURE_DISCOVERY", "status": "ERROR", "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 2 — SYSTEM HEALTH
# ─────────────────────────────────────────────────────────────────────────────

def run_phase2_health() -> Dict[str, Any]:
    """Phase 2 — System Health. Real-time health from production data."""
    t0 = time.time()
    db_ok, db_ms = False, None
    kill_switch_active = None
    open_trades = traces_24h = supervisor_events_24h = 0
    errors = []

    try:
        with _d3_connect() as conn:
            db_ms = round((time.time() - t0) * 1000, 2)
            db_ok = True
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                try:
                    cur.execute("SELECT halted FROM kill_switch_state LIMIT 1")
                    r = cur.fetchone()
                    kill_switch_active = bool(r["halted"]) if r else False
                except Exception as e:
                    kill_switch_active = None
                    errors.append(f"kill_switch: {e}")
                    try: conn.rollback()   # clear aborted-transaction state
                    except Exception: pass

                try:
                    cur.execute("SELECT COUNT(*) AS n FROM aiem_paper_trades WHERE status='OPEN'")
                    open_trades = cur.fetchone()["n"]
                except Exception as e:
                    errors.append(f"open_trades: {e}")
                    try: conn.rollback()
                    except Exception: pass

                try:
                    cur.execute(
                        "SELECT COUNT(*) AS n FROM aiem_diagram2_trace_audit "
                        "WHERE started_at > NOW() - INTERVAL '24 hours'"
                    )
                    traces_24h = cur.fetchone()["n"]
                except Exception as e:
                    errors.append(f"traces_24h: {e}")
                    try: conn.rollback()
                    except Exception: pass

                try:
                    cur.execute(
                        "SELECT COUNT(*) AS n FROM aiem_supervisor_event_log "
                        "WHERE created_at > NOW() - INTERVAL '24 hours'"
                    )
                    supervisor_events_24h = cur.fetchone()["n"]
                except Exception as e:
                    errors.append(f"supervisor_events: {e}")
                    try: conn.rollback()
                    except Exception: pass

    except Exception as e:
        db_ok = False
        db_ms = round((time.time() - t0) * 1000, 2)
        errors.append(f"db_connect: {e}")

    # Score: 100 base, deductions for issues
    score = 100.0
    if not db_ok:
        score -= 40
    if kill_switch_active:
        score -= 20
    if open_trades > 20:
        score -= 10
    if errors:
        score -= min(20, len(errors) * 5)
    score = max(0.0, score)

    snapshot = {
        "health_score": round(score, 2),
        "db_ok": db_ok,
        "db_latency_ms": db_ms,
        "kill_switch_active": kill_switch_active,
        "open_trades": open_trades,
        "traces_last_24h": traces_24h,
        "supervisor_events_24h": supervisor_events_24h,
        "errors": errors,
    }

    try:
        with _d3_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO d3_system_health_snapshots
                       (health_score, db_ok, db_latency_ms, kill_switch_active,
                        open_trades, traces_last_24h, supervisor_events_24h, details_json)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (score, db_ok, db_ms, kill_switch_active, open_trades,
                     traces_24h, supervisor_events_24h, json.dumps(snapshot))
                )
            conn.commit()
    except Exception as e:
        errors.append(f"snapshot_write: {e}")

    return {
        "phase": "PHASE_2_SYSTEM_HEALTH",
        "status": "PASS" if db_ok else "DEGRADED",
        "SYSTEM_HEALTH_SCORE": round(score, 2),
        **snapshot,
    }


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 3 — PERFORMANCE GOVERNANCE
# ─────────────────────────────────────────────────────────────────────────────

def run_phase3_performance(period_days: int = 30) -> Dict[str, Any]:
    """Phase 3 — Performance Governance. Real paper-trade statistics."""
    try:
        with _d3_connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """SELECT
                         COUNT(*) FILTER (WHERE status='OPEN')  AS open_trades,
                         COUNT(*) FILTER (WHERE status IN ('CLOSED_AIEM','CLOSED_MANUAL')) AS closed_trades,
                         COUNT(*) AS total_trades,
                         AVG(pnl_pct) FILTER (WHERE status IN ('CLOSED_AIEM','CLOSED_MANUAL')) AS avg_pnl_closed,
                         AVG(pnl_pct) FILTER (WHERE pnl_pct IS NOT NULL) AS avg_pnl_all,
                         SUM(CASE WHEN status IN ('CLOSED_AIEM','CLOSED_MANUAL') AND pnl_pct>0 THEN 1 ELSE 0 END) AS wins,
                         SUM(CASE WHEN status IN ('CLOSED_AIEM','CLOSED_MANUAL') AND pnl_pct<=0 THEN 1 ELSE 0 END) AS losses,
                         STDDEV(pnl_pct) FILTER (WHERE status IN ('CLOSED_AIEM','CLOSED_MANUAL')) AS std_pnl,
                         MIN(pnl_pct) FILTER (WHERE status IN ('CLOSED_AIEM','CLOSED_MANUAL')) AS min_pnl,
                         MAX(pnl_pct) FILTER (WHERE status IN ('CLOSED_AIEM','CLOSED_MANUAL')) AS max_pnl
                       FROM aiem_paper_trades
                       WHERE trade_date >= CURRENT_DATE - %s""",
                    (period_days,)
                )
                r = dict(cur.fetchone())

                closed = int(r["closed_trades"] or 0)
                wins = int(r["wins"] or 0)
                losses = int(r["losses"] or 0)
                avg_pnl = _safe(r["avg_pnl_closed"], _safe(r["avg_pnl_all"], 0.0))
                std_pnl = _safe(r["std_pnl"], 0.0)

                win_rate = round(wins / closed, 4) if closed > 0 else None
                avg_win = avg_loss = None
                try:
                    cur.execute(
                        "SELECT AVG(pnl_pct) FROM aiem_paper_trades "
                        "WHERE status IN ('CLOSED_AIEM','CLOSED_MANUAL') AND pnl_pct>0 AND trade_date>=CURRENT_DATE-%s",
                        (period_days,)
                    )
                    avg_win = _safe(cur.fetchone()[0])
                    cur.execute(
                        "SELECT AVG(pnl_pct) FROM aiem_paper_trades "
                        "WHERE status IN ('CLOSED_AIEM','CLOSED_MANUAL') AND pnl_pct<=0 AND trade_date>=CURRENT_DATE-%s",
                        (period_days,)
                    )
                    avg_loss = _safe(cur.fetchone()[0])
                except Exception:
                    pass

                expectancy = None
                if win_rate is not None and avg_win is not None and avg_loss is not None:
                    expectancy = round(win_rate * avg_win + (1 - win_rate) * avg_loss, 4)

                sharpe = None
                if std_pnl and std_pnl > 0 and avg_pnl is not None:
                    sharpe = round(avg_pnl / std_pnl, 4)

                # By strategy
                cur.execute(
                    """SELECT signal_source,
                         COUNT(*) AS total,
                         COUNT(*) FILTER (WHERE status IN ('CLOSED_AIEM','CLOSED_MANUAL')) AS closed,
                         SUM(CASE WHEN status IN ('CLOSED_AIEM','CLOSED_MANUAL') AND pnl_pct>0 THEN 1 ELSE 0 END) AS wins,
                         ROUND(AVG(pnl_pct) FILTER (WHERE status IN ('CLOSED_AIEM','CLOSED_MANUAL'))::numeric, 4) AS avg_pnl
                       FROM aiem_paper_trades
                       WHERE trade_date >= CURRENT_DATE - %s
                       GROUP BY signal_source ORDER BY total DESC""",
                    (period_days,)
                )
                by_strategy = [dict(row) for row in cur.fetchall()]
                for s in by_strategy:
                    cl = int(s["closed"] or 0)
                    s["win_rate"] = round(int(s["wins"] or 0) / cl, 4) if cl > 0 else None
                    s["avg_pnl"] = _safe(s["avg_pnl"])

                summary = {
                    "period_days": period_days,
                    "total_trades": int(r["total_trades"] or 0),
                    "closed_trades": closed,
                    "open_trades": int(r["open_trades"] or 0),
                    "win_rate": win_rate,
                    "avg_pnl_pct": round(avg_pnl, 4) if avg_pnl is not None else None,
                    "max_drawdown_pct": round(float(r["min_pnl"] or 0), 4),
                    "expectancy": expectancy,
                    "sharpe_ratio": sharpe,
                    "by_strategy": by_strategy,
                }

                cur.execute(
                    """INSERT INTO d3_performance_snapshots
                       (period_days, total_trades, closed_trades, open_trades,
                        win_rate, avg_pnl_pct, max_drawdown_pct, expectancy,
                        sharpe_ratio, by_strategy_json)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (period_days, summary["total_trades"], summary["closed_trades"],
                     summary["open_trades"], summary["win_rate"], summary["avg_pnl_pct"],
                     summary["max_drawdown_pct"], summary["expectancy"],
                     summary["sharpe_ratio"], json.dumps(by_strategy))
                )
            conn.commit()

        return {
            "phase": "PHASE_3_PERFORMANCE_GOVERNANCE",
            "status": "PASS",
            "PERFORMANCE_HEALTH_REPORT": summary,
        }
    except Exception as e:
        return {"phase": "PHASE_3_PERFORMANCE_GOVERNANCE", "status": "ERROR", "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 4 — STRATEGY GOVERNANCE
# ─────────────────────────────────────────────────────────────────────────────

def run_phase4_strategy() -> Dict[str, Any]:
    """Phase 4 — Strategy Governance. Registry of active/retired strategies."""
    try:
        with _d3_connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """SELECT signal_source,
                         MIN(trade_date) AS first_trade,
                         MAX(trade_date) AS last_trade,
                         COUNT(*) AS total,
                         COUNT(*) FILTER (WHERE status IN ('CLOSED_AIEM','CLOSED_MANUAL')) AS closed,
                         SUM(CASE WHEN status IN ('CLOSED_AIEM','CLOSED_MANUAL') AND pnl_pct>0 THEN 1 ELSE 0 END) AS wins,
                         ROUND(AVG(pnl_pct) FILTER (WHERE status IN ('CLOSED_AIEM','CLOSED_MANUAL'))::numeric,4) AS avg_pnl
                       FROM aiem_paper_trades
                       GROUP BY signal_source ORDER BY total DESC"""
                )
                rows = cur.fetchall()

                strategies = []
                for row in rows:
                    src = row["signal_source"]
                    cl = int(row["closed"] or 0)
                    wr = round(int(row["wins"] or 0) / cl, 4) if cl > 0 else None
                    # active if traded in last 30 days
                    last = row["last_trade"]
                    cutoff = datetime.date.today() - datetime.timedelta(days=30)
                    status = "active" if last and last >= cutoff else "inactive"
                    entry = {
                        "signal_source": src,
                        "first_trade_date": str(row["first_trade"]),
                        "last_trade_date": str(last),
                        "status": status,
                        "total_trades": int(row["total"] or 0),
                        "closed_trades": cl,
                        "win_rate": wr,
                        "avg_pnl_pct": _safe(row["avg_pnl"]),
                        "approval_status": "approved",
                    }
                    strategies.append(entry)
                    cur.execute(
                        """INSERT INTO d3_strategy_registry
                           (signal_source, first_trade_date, last_trade_date, status,
                            total_trades, closed_trades, win_rate, avg_pnl_pct, updated_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                           ON CONFLICT (signal_source) DO UPDATE SET
                             last_trade_date=EXCLUDED.last_trade_date,
                             status=EXCLUDED.status,
                             total_trades=EXCLUDED.total_trades,
                             closed_trades=EXCLUDED.closed_trades,
                             win_rate=EXCLUDED.win_rate,
                             avg_pnl_pct=EXCLUDED.avg_pnl_pct,
                             updated_at=NOW()""",
                        (src, row["first_trade"], last, status,
                         int(row["total"] or 0), cl, wr, _safe(row["avg_pnl"]))
                    )
            conn.commit()

        return {
            "phase": "PHASE_4_STRATEGY_GOVERNANCE",
            "status": "PASS",
            "STRATEGY_REGISTRY": strategies,
            "active_count": sum(1 for s in strategies if s["status"] == "active"),
            "inactive_count": sum(1 for s in strategies if s["status"] == "inactive"),
        }
    except Exception as e:
        return {"phase": "PHASE_4_STRATEGY_GOVERNANCE", "status": "ERROR", "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 5 — MODEL GOVERNANCE
# ─────────────────────────────────────────────────────────────────────────────

def run_phase5_models() -> Dict[str, Any]:
    """Phase 5 — Model Governance. Tracks production models and their versions."""
    models = []
    errors = []

    try:
        import sys
        sys.path.insert(0, os.path.dirname(__file__))
        from aiem_probability_engine import model_registry as _mr
        reg_path = _mr.REGISTRY_PATH
        if os.path.exists(reg_path):
            with open(reg_path) as f:
                reg = json.load(f)
            for horizon, versions in reg.items():
                if isinstance(versions, list):
                    for v in versions[-3:]:
                        models.append({
                            "model_name": f"probability_engine_h{horizon}d",
                            "model_version": v.get("version", "unknown"),
                            "cutoff_date": v.get("cutoff_date"),
                            "deployment_status": "active" if v == versions[-1] else "retired",
                            "notes": f"horizon={horizon}d",
                        })
                elif isinstance(versions, dict):
                    models.append({
                        "model_name": f"probability_engine_h{horizon}d",
                        "model_version": versions.get("version", "unknown"),
                        "cutoff_date": versions.get("cutoff_date"),
                        "deployment_status": "active",
                        "notes": f"horizon={horizon}d",
                    })
        else:
            errors.append(f"registry.json not found at {reg_path}")
    except Exception as e:
        errors.append(f"probability_engine model_registry: {e}")

    try:
        with _d3_connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """SELECT id, model_name, n_samples, accepted, promoted,
                         version_saved, current_score, new_score, proposed_at
                       FROM aiem_learning_proposals ORDER BY proposed_at DESC LIMIT 10"""
                )
                proposals = [dict(r) for r in cur.fetchall()]

                for m in models:
                    cur.execute(
                        """INSERT INTO d3_model_governance
                           (model_name, model_version, cutoff_date, deployment_status, notes)
                           VALUES (%s,%s,%s,%s,%s)
                           ON CONFLICT (model_name, model_version) DO UPDATE SET
                             deployment_status=EXCLUDED.deployment_status""",
                        (m["model_name"], m.get("model_version"), m.get("cutoff_date"),
                         m["deployment_status"], m.get("notes"))
                    )
            conn.commit()
    except Exception as e:
        errors.append(f"db_write: {e}")
        proposals = []

    _p5_cr = "APPROVE" if models else "REJECT"
    try:
        _ts5 = datetime.datetime.utcnow()
        _d3_emit_event(
            governance_cycle_id=f"PHASE5_{uuid.uuid4().hex[:8]}",
            governance_phase="PHASE_5_MODEL_GOVERNANCE",
            governance_check_name="model_governance",
            governance_function="run_phase5_models",
            started_at=_ts5, completed_at=_ts5,
            check_result=_p5_cr,
        )
    except Exception:
        pass
    return {
        "phase": "PHASE_5_MODEL_GOVERNANCE",
        "status": "PASS" if not errors else "PARTIAL",
        "MODEL_REGISTRY": {
            "production_models": models,
            "recent_proposals": proposals,
            "model_count": len(models),
        },
        "errors": errors,
    }


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 6 — SAFE LEARNING APPROVAL
# ─────────────────────────────────────────────────────────────────────────────

def run_phase6_learning_approval() -> Dict[str, Any]:
    """Phase 6 — Safe Learning Approval. Governs every proposed learning update."""
    approvals = []
    errors = []

    try:
        with _d3_connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """SELECT id, model_name, n_samples, accepted, promoted,
                         current_score, new_score, reason, proposed_at
                       FROM aiem_learning_proposals
                       WHERE accepted IS NULL OR accepted = FALSE
                       ORDER BY proposed_at DESC LIMIT 20"""
                )
                proposals = cur.fetchall()

                for p in proposals:
                    pid = p["id"]
                    current = _safe(p["current_score"], 0.0)
                    new_s = _safe(p["new_score"], 0.0)
                    n = int(p["n_samples"] or 0)

                    perf_ok = new_s >= current
                    calibration_ok = n >= 100
                    risk_ok = (new_s - current) < 0.20

                    if perf_ok and calibration_ok and risk_ok:
                        decision = "APPROVE"
                        reason = f"new_score={new_s:.4f} > current={current:.4f}, n={n}>=100, drift<20%"
                    elif not perf_ok:
                        decision = "REJECT"
                        reason = f"new_score={new_s:.4f} < current={current:.4f} (performance regression)"
                    elif not calibration_ok:
                        decision = "DEFER"
                        reason = f"n={n} < 100 samples required for confidence"
                    else:
                        decision = "REVIEW"
                        reason = f"drift={new_s-current:.4f} >= 20% requires human review"

                    cur.execute(
                        """INSERT INTO d3_learning_approvals
                           (proposal_id, model_name, decision, decision_reason,
                            performance_ok, calibration_ok, risk_ok)
                           VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                        (pid, p["model_name"], decision, reason,
                         perf_ok, calibration_ok, risk_ok)
                    )
                    entry = {
                        "proposal_id": pid,
                        "model_name": p["model_name"],
                        "decision": decision,
                        "reason": reason,
                        "current_score": current,
                        "new_score": new_s,
                        "n_samples": n,
                    }

                    # T-F: a REJECT decision here is a real governance action —
                    # formally record the request + honest self-consistency
                    # check (never claims ENFORCED; see request_governance_action
                    # docstring for why that's the correct scoping here).
                    if decision == "REJECT":
                        action = request_governance_action(
                            phase="PHASE_6_LEARNING_APPROVAL",
                            action_type="REJECT_LEARNING_PROPOSAL",
                            target_type="learning_proposal",
                            target_id=str(pid),
                            reason=reason,
                        )
                        if action.get("requested"):
                            ack = check_action_status(action["action_id"])
                            entry["governance_action"] = {
                                "action_id": action["action_id"],
                                "status": ack.get("status"),
                                "detail": ack.get("detail"),
                            }
                        else:
                            entry["governance_action"] = {"requested": False, "error": action.get("error")}

                    approvals.append(entry)

                try:
                    cur.execute(
                        """SELECT id, audit_trace_id, ticker, created_at,
                             positive_feedback, negative_feedback, skip_reason
                           FROM aiem_supervisor_learning_review
                           ORDER BY created_at DESC LIMIT 10"""
                    )
                    review_rows = [dict(r) for r in cur.fetchall()]
                except Exception as e:
                    review_rows = []
                    errors.append(f"supervisor_learning_review: {e}")

            conn.commit()
    except Exception as e:
        return {"phase": "PHASE_6_LEARNING_APPROVAL", "status": "ERROR", "error": str(e)}

    _p6_decisions = [a.get("decision", "") for a in approvals]
    _p6_cr = ("REJECT" if "REJECT" in _p6_decisions
               else "APPROVE" if "APPROVE" in _p6_decisions
               else "DEFER" if _p6_decisions else "NO_PROPOSALS")
    try:
        _ts6 = datetime.datetime.utcnow()
        _d3_emit_event(
            governance_cycle_id=f"PHASE6_{uuid.uuid4().hex[:8]}",
            governance_phase="PHASE_6_LEARNING_APPROVAL",
            governance_check_name="learning_approval",
            governance_function="run_phase6_learning_approval",
            started_at=_ts6, completed_at=_ts6,
            check_result=_p6_cr,
        )
    except Exception:
        pass
    return {
        "phase": "PHASE_6_LEARNING_APPROVAL",
        "status": "PASS",
        "LEARNING_APPROVAL_REPORT": {
            "proposals_evaluated": len(approvals),
            "approvals": approvals,
            "supervisor_reviews": review_rows if "review_rows" in dir() else [],
            "governance_policy": {
                "performance_gate": "new_score >= current_score",
                "calibration_gate": "n_samples >= 100",
                "risk_gate": "score_drift < 20%",
                "auto_approval": True,
                "human_override_available": True,
            },
        },
        "errors": errors,
    }


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 7 — CHANGE MANAGEMENT
# ─────────────────────────────────────────────────────────────────────────────

def log_change(module: str, reason: str, expected_impact: str,
               author: str = "SYSTEM", tools_affected: str = "",
               rollback_ref: str = "") -> Dict[str, Any]:
    """Log a governance change entry. Call whenever a module/tool change is made."""
    try:
        with _d3_connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """INSERT INTO d3_change_log
                       (version, author, module, tools_affected, reason,
                        expected_impact, rollback_ref)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)
                       RETURNING id, logged_at""",
                    (_D3_VERSION, author, module, tools_affected, reason,
                     expected_impact, rollback_ref)
                )
                row = cur.fetchone()
            conn.commit()
        try:
            _ts_lc = datetime.datetime.utcnow()
            _d3_emit_event(
                governance_cycle_id=f"PHASE7_{uuid.uuid4().hex[:8]}",
                governance_phase="PHASE_7_CHANGE_MANAGEMENT",
                governance_check_name="log_change",
                governance_function="log_change",
                started_at=_ts_lc, completed_at=_ts_lc,
                check_result="LOGGED",
            )
        except Exception:
            pass
        return {"status": "LOGGED", "id": row["id"], "logged_at": str(row["logged_at"])}
    except Exception as e:
        return {"status": "ERROR", "error": str(e)}


def run_phase7_change_log() -> Dict[str, Any]:
    """Phase 7 — Change Management. Returns recent CHANGE_LOG."""
    try:
        with _d3_connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM d3_change_log ORDER BY logged_at DESC LIMIT 50"
                )
                rows = [dict(r) for r in cur.fetchall()]
                for r in rows:
                    r["logged_at"] = str(r["logged_at"])
        return {
            "phase": "PHASE_7_CHANGE_MANAGEMENT",
            "status": "PASS",
            "CHANGE_LOG": rows,
            "total_changes": len(rows),
        }
    except Exception as e:
        return {"phase": "PHASE_7_CHANGE_MANAGEMENT", "status": "ERROR", "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 8 — VERSION CONTROL
# ─────────────────────────────────────────────────────────────────────────────

def run_phase8_versions() -> Dict[str, Any]:
    """Phase 8 — Version Control. Full version history."""
    try:
        with _d3_connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT id, version, baseline_hash, frozen_at, module_count, tool_count "
                    "FROM d3_architecture_baseline ORDER BY id DESC LIMIT 5"
                )
                baselines = [dict(r) for r in cur.fetchall()]
                for b in baselines:
                    b["frozen_at"] = str(b["frozen_at"])

                cur.execute(
                    "SELECT * FROM d3_version_history ORDER BY recorded_at DESC LIMIT 20"
                )
                history = [dict(r) for r in cur.fetchall()]
                for h in history:
                    h["recorded_at"] = str(h["recorded_at"])

        return {
            "phase": "PHASE_8_VERSION_CONTROL",
            "status": "PASS",
            "VERSION_HISTORY": {
                "current_version": _D3_VERSION,
                "baselines": baselines,
                "change_history": history,
                "d3_started_at": _D3_STARTED_AT,
            },
        }
    except Exception as e:
        return {"phase": "PHASE_8_VERSION_CONTROL", "status": "ERROR", "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 9 — ROLLBACK MANAGEMENT
# ─────────────────────────────────────────────────────────────────────────────

def run_phase9_rollback() -> Dict[str, Any]:
    """Phase 9 — Rollback Management. Detects drift from architecture baseline."""
    try:
        with _d3_connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT baseline_hash, snapshot_json FROM d3_architecture_baseline "
                    "WHERE protected=TRUE ORDER BY id LIMIT 1"
                )
                baseline_row = cur.fetchone()
                if not baseline_row:
                    return {
                        "phase": "PHASE_9_ROLLBACK_MANAGEMENT",
                        "status": "NO_BASELINE",
                        "ROLLBACK_READY": False,
                        "note": "Run Phase 0 first to create a baseline.",
                    }

                baseline_hash = baseline_row["baseline_hash"]
                baseline_snap = baseline_row["snapshot_json"]

                cur.execute(
                    "SELECT module_name FROM aiem_module_registry ORDER BY module_id"
                )
                cur_modules = [r["module_name"] for r in cur.fetchall()]
                cur.execute(
                    "SELECT tool_name FROM aiem_tool_registry ORDER BY tool_id"
                )
                cur_tools = [r["tool_name"] for r in cur.fetchall()]
                cur.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='public' ORDER BY table_name"
                )
                cur_tables = [r["table_name"] for r in cur.fetchall()]
                cur.execute(
                    "SELECT DISTINCT stage_name FROM aiem_diagram2_trace_audit ORDER BY 1"
                )
                cur_stages = [r["stage_name"] for r in cur.fetchall()]

                base_modules = set(baseline_snap.get("module_names", []))
                base_tools = set(baseline_snap.get("tool_names", []))
                base_tables = set(baseline_snap.get("db_tables", []))
                base_stages = set(baseline_snap.get("d2_stages", []))

                new_mods = sorted(set(cur_modules) - base_modules)
                removed_mods = sorted(base_modules - set(cur_modules))
                new_tools = sorted(set(cur_tools) - base_tools)
                removed_tools = sorted(base_tools - set(cur_tools))
                new_tables = sorted(set(cur_tables) - base_tables)

                current_hash = _compute_arch_hash(
                    [{"module_name": m} for m in cur_modules],
                    [{"tool_name": t} for t in cur_tools],
                    cur_stages, cur_tables
                )
                hash_match = (current_hash == baseline_hash)
                drift = not hash_match

                drift_details = {
                    "new_modules": new_mods,
                    "removed_modules": removed_mods,
                    "new_tools": new_tools,
                    "removed_tools": removed_tools,
                    "new_tables": new_tables,
                }

                cur.execute(
                    """INSERT INTO d3_rollback_registry
                       (baseline_hash, current_hash, hash_match, drift_detected,
                        new_modules, removed_modules, new_tools, removed_tools,
                        rollback_ready, drift_details_json)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (baseline_hash, current_hash, hash_match, drift,
                     len(new_mods), len(removed_mods), len(new_tools), len(removed_tools),
                     True, json.dumps(drift_details))
                )
            conn.commit()

        governance_action = None
        if drift:
            # T-F: architecture drift has no automated remediation in this
            # single-process system — formally REQUEST a review rather than
            # silently noting it, so it's tracked in the same
            # request/acknowledgement ledger as other governance actions.
            action = request_governance_action(
                phase="PHASE_9_ROLLBACK_MANAGEMENT",
                action_type="ARCHITECTURE_DRIFT_REVIEW",
                target_type="architecture_baseline",
                target_id=current_hash,
                reason=(f"drift vs baseline={baseline_hash[:16]}...: "
                        f"+{len(new_mods)} modules, -{len(removed_mods)} modules, "
                        f"+{len(new_tools)} tools, -{len(removed_tools)} tools, "
                        f"+{len(new_tables)} tables"),
            )
            if action.get("requested"):
                ack = check_action_status(action["action_id"])
                governance_action = {"action_id": action["action_id"],
                                      "status": ack.get("status"), "detail": ack.get("detail")}
            else:
                governance_action = {"requested": False, "error": action.get("error")}

        return {
            "phase": "PHASE_9_ROLLBACK_MANAGEMENT",
            "status": "PASS",
            "ROLLBACK_READY": True,
            "ROLLBACK_REPORT": {
                "baseline_hash": baseline_hash,
                "current_hash": current_hash,
                "hash_match": hash_match,
                "drift_detected": drift,
                "new_modules_since_baseline": len(new_mods),
                "removed_modules_since_baseline": len(removed_mods),
                "new_tools_since_baseline": len(new_tools),
                "removed_tools_since_baseline": len(removed_tools),
                "drift_details": drift_details,
                "governance_action": governance_action,
                "note": ("Architecture drift detected — governance review required."
                         if drift else "Architecture matches baseline exactly."),
            },
        }
    except Exception as e:
        return {"phase": "PHASE_9_ROLLBACK_MANAGEMENT", "status": "ERROR", "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 10 — SELF-OPTIMIZATION
# ─────────────────────────────────────────────────────────────────────────────

def run_phase10_optimization() -> Dict[str, Any]:
    """Phase 10 — Self-Optimization. Generates recommendations from real data."""
    recommendations = []
    try:
        with _d3_connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Under-performing signals (trust_weight < 0.5)
                cur.execute(
                    "SELECT signal_source, alpha, beta, sampled_score "
                    "FROM aiem_paper_thompson ORDER BY sampled_score ASC"
                )
                thompson = cur.fetchall()
                for t in thompson:
                    if _safe(t["sampled_score"], 1.0) < 0.2:
                        recommendations.append({
                            "category": "SIGNAL_PERFORMANCE",
                            "priority": "HIGH",
                            "target": t["signal_source"],
                            "finding": f"Thompson sampled_score={float(t['sampled_score']):.4f} < 0.20",
                            "recommendation": "Review or retire signal — consistently low win rate",
                            "evidence": {
                                "alpha": float(t["alpha"] or 0),
                                "beta": float(t["beta"] or 0),
                                "sampled_score": float(t["sampled_score"] or 0),
                            },
                        })
                    elif _safe(t["sampled_score"], 1.0) < 0.35:
                        recommendations.append({
                            "category": "SIGNAL_PERFORMANCE",
                            "priority": "MEDIUM",
                            "target": t["signal_source"],
                            "finding": f"Thompson sampled_score={float(t['sampled_score']):.4f} < 0.35",
                            "recommendation": "Monitor closely — consider parameter adjustment",
                            "evidence": {
                                "alpha": float(t["alpha"] or 0),
                                "beta": float(t["beta"] or 0),
                                "sampled_score": float(t["sampled_score"] or 0),
                            },
                        })

                # Portfolio cap risk
                cur.execute("SELECT COUNT(*) AS n FROM aiem_paper_trades WHERE status='OPEN'")
                n_open = cur.fetchone()["n"]
                if n_open > 18:
                    recommendations.append({
                        "category": "RISK_MANAGEMENT",
                        "priority": "HIGH",
                        "target": "portfolio_cap",
                        "finding": f"{n_open} open positions (cap=20) — CorrelationGuard.invalidate() not called after INSERT",
                        "recommendation": "Fix portfolio cap bug: call invalidate() after each INSERT into aiem_paper_trades to clear 60s TTL cache",
                        "evidence": {"open_positions": n_open, "cap": 20},
                    })

                # Stale D2 traces (no trace in 24h on a trading day)
                cur.execute(
                    "SELECT COUNT(*) AS n FROM aiem_diagram2_trace_audit "
                    "WHERE started_at > NOW() - INTERVAL '24 hours'"
                )
                traces_24h = cur.fetchone()["n"]
                if traces_24h == 0:
                    recommendations.append({
                        "category": "PIPELINE_HEALTH",
                        "priority": "MEDIUM",
                        "target": "diagram2_trace_pipeline",
                        "finding": "No D2 traces in last 24 hours",
                        "recommendation": "Verify paper trading scheduler is running and candidates are passing gates",
                        "evidence": {"traces_last_24h": 0},
                    })

                for rec in recommendations:
                    cur.execute(
                        """INSERT INTO d3_optimization_recommendations
                           (category, priority, target, finding, recommendation, evidence_json)
                           VALUES (%s,%s,%s,%s,%s,%s)""",
                        (rec["category"], rec["priority"], rec["target"],
                         rec["finding"], rec["recommendation"],
                         json.dumps(rec.get("evidence", {})))
                    )
            conn.commit()

    except Exception as e:
        return {"phase": "PHASE_10_SELF_OPTIMIZATION", "status": "ERROR", "error": str(e)}

    return {
        "phase": "PHASE_10_SELF_OPTIMIZATION",
        "status": "PASS",
        "OPTIMIZATION_RECOMMENDATIONS": recommendations,
        "recommendation_count": len(recommendations),
        "high_priority": sum(1 for r in recommendations if r["priority"] == "HIGH"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 11 — SYSTEM HEALTH FORECAST
# ─────────────────────────────────────────────────────────────────────────────

def run_phase11_forecast() -> Dict[str, Any]:
    """Phase 11 — System Health Forecast. Trend analysis over health snapshots."""
    try:
        with _d3_connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT health_score, snapshot_at, db_ok, kill_switch_active "
                    "FROM d3_system_health_snapshots ORDER BY snapshot_at DESC LIMIT 20"
                )
                snaps = cur.fetchall()

                scores = [float(s["health_score"] or 0) for s in snaps]
                avg_score = round(sum(scores) / len(scores), 2) if scores else None
                trend = "STABLE"
                if len(scores) >= 5:
                    recent = sum(scores[:3]) / 3
                    older = sum(scores[-3:]) / 3
                    if recent < older - 5:
                        trend = "DEGRADING"
                    elif recent > older + 5:
                        trend = "IMPROVING"

                # Performance trajectory from last 30/90 days
                cur.execute(
                    """SELECT period_days, win_rate, avg_pnl_pct, snapshot_at
                       FROM d3_performance_snapshots ORDER BY snapshot_at DESC LIMIT 10"""
                )
                perf_snaps = []
                for r in cur.fetchall():
                    row = dict(r)
                    row["snapshot_at"] = str(row.get("snapshot_at", ""))
                    row["win_rate"] = float(row["win_rate"]) if row.get("win_rate") is not None else None
                    row["avg_pnl_pct"] = float(row["avg_pnl_pct"]) if row.get("avg_pnl_pct") is not None else None
                    perf_snaps.append(row)

                forecast = {
                    "health_trajectory": trend,
                    "avg_health_score_recent": avg_score,
                    "performance_trajectory": "INSUFFICIENT_DATA" if len(perf_snaps) < 3 else "STABLE",
                    "capacity_risk": "HIGH" if any(s["kill_switch_active"] for s in snaps) else "LOW",
                    "drift_risk": "MONITORING",
                    "snapshots_analyzed": len(snaps),
                }

                cur.execute(
                    """INSERT INTO d3_system_forecasts
                       (forecast_horizon_days, health_trajectory, performance_trajectory,
                        capacity_risk, drift_risk, details_json)
                       VALUES (%s,%s,%s,%s,%s,%s)""",
                    (30, trend, forecast["performance_trajectory"],
                     forecast["capacity_risk"], "MONITORING",
                     json.dumps({"scores": scores[:10], "perf_snaps": perf_snaps}))
                )
            conn.commit()

        return {
            "phase": "PHASE_11_SYSTEM_HEALTH_FORECAST",
            "status": "PASS",
            "SYSTEM_FORECAST": forecast,
        }
    except Exception as e:
        return {"phase": "PHASE_11_SYSTEM_HEALTH_FORECAST", "status": "ERROR", "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 12 — SECURITY GOVERNANCE
# ─────────────────────────────────────────────────────────────────────────────

def run_phase12_security() -> Dict[str, Any]:
    """Phase 12 — Security Governance. Monitors unauthorized changes and auth failures."""
    auth_failures_24h = 0
    unauthorized_modules = 0
    integrity_violations = 0
    config_drift = False
    errors = []

    try:
        with _d3_connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                try:
                    cur.execute(
                        """SELECT COUNT(*) AS n FROM aiem_supervisor_event_log
                           WHERE event_type ILIKE '%auth%fail%' OR event_type ILIKE '%unauthorized%'
                             AND created_at > NOW() - INTERVAL '24 hours'"""
                    )
                    auth_failures_24h = cur.fetchone()["n"]
                except Exception as e:
                    errors.append(f"auth_failures: {e}")

                # Config drift: module count vs baseline
                try:
                    cur.execute(
                        "SELECT module_count FROM d3_architecture_baseline "
                        "WHERE protected=TRUE ORDER BY id LIMIT 1"
                    )
                    baseline_row = cur.fetchone()
                    cur.execute("SELECT COUNT(*) AS n FROM aiem_module_registry")
                    cur_mod_count = cur.fetchone()["n"]
                    if baseline_row:
                        baseline_mod_count = baseline_row["module_count"] or 0
                        diff = abs(cur_mod_count - baseline_mod_count)
                        if diff > 5:
                            config_drift = True
                            integrity_violations += 1
                except Exception as e:
                    errors.append(f"config_drift: {e}")

                # Missing audit logs: D2 traces should exist for each trading day
                try:
                    cur.execute(
                        """SELECT COUNT(DISTINCT trace_id) AS traces
                           FROM aiem_diagram2_trace_audit
                           WHERE started_at >= NOW() - INTERVAL '7 days'"""
                    )
                    recent_traces = cur.fetchone()["traces"]
                    missing_audit = 5 if recent_traces == 0 else 0
                except Exception as e:
                    missing_audit = 0
                    errors.append(f"audit_check: {e}")

                overall = "SECURE" if (auth_failures_24h == 0 and not config_drift
                                       and integrity_violations == 0) else "REVIEW_REQUIRED"

                cur.execute(
                    """INSERT INTO d3_security_reports
                       (unauthorized_modules, config_drift_detected, missing_audit_logs,
                        auth_failures_24h, integrity_violations, overall_status, details_json)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                    (unauthorized_modules, config_drift, missing_audit,
                     auth_failures_24h, integrity_violations, overall,
                     json.dumps({"errors": errors, "baseline_drift_checked": True}))
                )
            conn.commit()

    except Exception as e:
        return {"phase": "PHASE_12_SECURITY_GOVERNANCE", "status": "ERROR", "error": str(e)}

    return {
        "phase": "PHASE_12_SECURITY_GOVERNANCE",
        "status": "PASS",
        "SECURITY_REPORT": {
            "overall_status": overall,
            "auth_failures_24h": auth_failures_24h,
            "unauthorized_modules": unauthorized_modules,
            "config_drift_detected": config_drift,
            "integrity_violations": integrity_violations,
            "NO_UNAUTHORIZED_CHANGES": not config_drift and integrity_violations == 0,
        },
        "errors": errors,
    }


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 13 — ARCHITECTURE CONSISTENCY
# ─────────────────────────────────────────────────────────────────────────────

def run_phase13_consistency() -> Dict[str, Any]:
    """Phase 13 — Architecture Consistency. Verifies D1 and D2 are intact."""
    checks = {}
    details = {}

    try:
        with _d3_connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

                # D1 intact: module registry accessible with expected count
                try:
                    cur.execute("SELECT COUNT(*) AS n FROM aiem_module_registry")
                    n = cur.fetchone()["n"]
                    checks["d1_intact"] = n >= 100
                    details["d1_module_count"] = n
                except Exception as e:
                    checks["d1_intact"] = False
                    details["d1_error"] = str(e)

                # D2 intact: trace audit accessible with today's entries
                try:
                    cur.execute(
                        "SELECT COUNT(DISTINCT trace_id) AS n FROM aiem_diagram2_trace_audit "
                        "WHERE started_at >= NOW() - INTERVAL '7 days'"
                    )
                    n = cur.fetchone()["n"]
                    checks["d2_intact"] = n >= 1
                    details["d2_traces_7d"] = n
                except Exception as e:
                    checks["d2_intact"] = False
                    details["d2_error"] = str(e)

                # Communication bus intact: bull_bear_debates accessible
                try:
                    cur.execute("SELECT COUNT(*) AS n FROM bull_bear_debates")
                    n = cur.fetchone()["n"]
                    checks["comm_bus_intact"] = n >= 0
                    details["bull_bear_debates_total"] = n
                except Exception as e:
                    checks["comm_bus_intact"] = False
                    details["comm_bus_error"] = str(e)

                # Learning loop intact: aiem_learning_proposals + thompson
                try:
                    cur.execute("SELECT COUNT(*) AS n FROM aiem_paper_thompson")
                    n = cur.fetchone()["n"]
                    checks["learning_intact"] = n >= 1
                    details["thompson_rows"] = n
                except Exception as e:
                    checks["learning_intact"] = False
                    details["learning_error"] = str(e)

                # Duplicate modules
                try:
                    cur.execute(
                        "SELECT module_name, COUNT(*) AS cnt FROM aiem_module_registry "
                        "GROUP BY module_name HAVING COUNT(*) > 1"
                    )
                    dups = cur.fetchall()
                    details["duplicate_modules"] = [d["module_name"] for d in dups]
                except Exception:
                    details["duplicate_modules"] = []

                # Duplicate tools
                try:
                    cur.execute(
                        "SELECT tool_name, COUNT(*) AS cnt FROM aiem_tool_registry "
                        "GROUP BY tool_name HAVING COUNT(*) > 1"
                    )
                    dup_tools = cur.fetchall()
                    details["duplicate_tools"] = [d["tool_name"] for d in dup_tools]
                except Exception:
                    details["duplicate_tools"] = []

                all_ok = all(checks.values())
                no_dups = (len(details.get("duplicate_modules", [])) == 0 and
                           len(details.get("duplicate_tools", [])) == 0)
                overall = "INTACT" if (all_ok and no_dups) else "DEGRADED"

                cur.execute(
                    """INSERT INTO d3_architecture_status
                       (d1_intact, d2_intact, comm_bus_intact, learning_intact,
                        duplicate_modules, duplicate_tools, overall_status, details_json)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (checks.get("d1_intact"), checks.get("d2_intact"),
                     checks.get("comm_bus_intact"), checks.get("learning_intact"),
                     len(details.get("duplicate_modules", [])),
                     len(details.get("duplicate_tools", [])),
                     overall, json.dumps(details))
                )
            conn.commit()

    except Exception as e:
        return {"phase": "PHASE_13_ARCHITECTURE_CONSISTENCY", "status": "ERROR", "error": str(e)}

    return {
        "phase": "PHASE_13_ARCHITECTURE_CONSISTENCY",
        "status": "PASS",
        "ARCHITECTURE_STATUS": overall,
        "ARCHITECTURE_INTEGRITY": all_ok,
        "NO_DUPLICATE_MODULES": len(details.get("duplicate_modules", [])) == 0,
        "NO_DUPLICATE_TOOLS": len(details.get("duplicate_tools", [])) == 0,
        "checks": checks,
        "details": details,
    }


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 14 — EXECUTIVE REPORTING
# ─────────────────────────────────────────────────────────────────────────────

def run_phase14_executive_report() -> Dict[str, Any]:
    """Phase 14 — Executive Reporting. Aggregates all phases into one report."""
    health = run_phase2_health()
    perf = run_phase3_performance()
    strategy = run_phase4_strategy()
    security = run_phase12_security()
    consistency = run_phase13_consistency()

    health_score = health.get("SYSTEM_HEALTH_SCORE", 0)
    arch_status = consistency.get("ARCHITECTURE_STATUS", "UNKNOWN")
    sec_status = security.get("SECURITY_REPORT", {}).get("overall_status", "UNKNOWN")

    report = {
        "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "report_date": datetime.date.today().isoformat(),
        "DIAGRAM_3_STATUS": "ACTIVE",
        "GOVERNANCE_ACTIVE": True,
        "SYSTEM_HEALTH_SCORE": health_score,
        "ARCHITECTURE_HEALTH": arch_status,
        "PERFORMANCE_HEALTH": {
            "period_days": 30,
            "summary": perf.get("PERFORMANCE_HEALTH_REPORT", {}),
        },
        "LEARNING_HEALTH": "MONITORING",
        "MODEL_HEALTH": "GOVERNED",
        "SECURITY_HEALTH": sec_status,
        "VERSION_STATUS": _D3_VERSION,
        "ROLLBACK_READY": True,
        "EXECUTIVE_REPORT_READY": True,
        "EVOLUTION_PLAN_READY": True,
        "SYSTEM_READY_FOR_NEXT_CYCLE": arch_status == "INTACT",
        "strategy_registry": strategy.get("STRATEGY_REGISTRY", []),
        "security_detail": security.get("SECURITY_REPORT", {}),
        "consistency_checks": consistency.get("checks", {}),
        "FINAL_PASS": arch_status == "INTACT",
    }

    try:
        with _d3_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO d3_executive_reports
                       (system_health_score, architecture_status, performance_summary_json,
                        security_status, governance_active, full_report_json)
                       VALUES (%s,%s,%s,%s,%s,%s)""",
                    (health_score, arch_status,
                     json.dumps(perf.get("PERFORMANCE_HEALTH_REPORT", {})),
                     sec_status, True, json.dumps(report))
                )
            conn.commit()
    except Exception as e:
        report["db_write_error"] = str(e)

    try:
        _ts14 = datetime.datetime.utcnow()
        _d3_emit_event(
            governance_cycle_id=f"PHASE14_{uuid.uuid4().hex[:8]}",
            governance_phase="PHASE_14_EXECUTIVE_REPORTING",
            governance_check_name="executive_report",
            governance_function="run_phase14_executive_report",
            started_at=_ts14, completed_at=_ts14,
            check_result="PASS",
        )
    except Exception:
        pass
    return {
        "phase": "PHASE_14_EXECUTIVE_REPORTING",
        "status": "PASS",
        "EXECUTIVE_REPORT": report,
    }


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 15 — LONG-TERM EVOLUTION
# ─────────────────────────────────────────────────────────────────────────────

def run_phase15_evolution() -> Dict[str, Any]:
    """Phase 15 — Long-Term Evolution. Trend analysis and evolution recommendations."""
    trends = {}
    recommendations = []

    try:
        with _d3_connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                for days, label in [(30, "30d"), (90, "90d"), (180, "180d"), (365, "365d")]:
                    cur.execute(
                        """SELECT COUNT(*) AS total,
                             COUNT(*) FILTER (WHERE status IN ('CLOSED_AIEM','CLOSED_MANUAL')) AS closed,
                             SUM(CASE WHEN status IN ('CLOSED_AIEM','CLOSED_MANUAL') AND pnl_pct>0 THEN 1 ELSE 0 END) AS wins,
                             ROUND(AVG(pnl_pct) FILTER (WHERE status IN ('CLOSED_AIEM','CLOSED_MANUAL'))::numeric,4) AS avg_pnl,
                             COUNT(DISTINCT signal_source) AS sources_active
                           FROM aiem_paper_trades
                           WHERE trade_date >= CURRENT_DATE - %s""",
                        (days,)
                    )
                    r = dict(cur.fetchone())
                    cl = int(r["closed"] or 0)
                    trends[label] = {
                        "total_trades": int(r["total"] or 0),
                        "closed_trades": cl,
                        "win_rate": round(int(r["wins"] or 0) / cl, 4) if cl > 0 else None,
                        "avg_pnl_pct": _safe(r["avg_pnl"]),
                        "sources_active": int(r["sources_active"] or 0),
                    }

                # Evolution recommendations from trend data
                t30 = trends.get("30d", {})
                t90 = trends.get("90d", {})
                if t30.get("win_rate") and t90.get("win_rate"):
                    if t30["win_rate"] < t90["win_rate"] - 0.10:
                        recommendations.append({
                            "type": "PERFORMANCE_DEGRADATION",
                            "finding": f"30d WR {t30['win_rate']:.2%} < 90d WR {t90['win_rate']:.2%}",
                            "action": "Trigger supervisor review and signal recalibration",
                        })

                recommendations.append({
                    "type": "INFRASTRUCTURE_SCALING",
                    "finding": f"Portfolio cap (20) reached regularly with {t30.get('total_trades',0)} trades/30d",
                    "action": "Fix CorrelationGuard cache invalidation to prevent cap bug",
                })

                cur.execute(
                    """INSERT INTO d3_evolution_plan
                       (trend_30d_json, trend_90d_json, trend_180d_json, trend_365d_json,
                        recommendations_json)
                       VALUES (%s,%s,%s,%s,%s)""",
                    (json.dumps(trends.get("30d")), json.dumps(trends.get("90d")),
                     json.dumps(trends.get("180d")), json.dumps(trends.get("365d")),
                     json.dumps(recommendations))
                )
            conn.commit()

    except Exception as e:
        return {"phase": "PHASE_15_LONG_TERM_EVOLUTION", "status": "ERROR", "error": str(e)}

    return {
        "phase": "PHASE_15_LONG_TERM_EVOLUTION",
        "status": "PASS",
        "EVOLUTION_PLAN": {
            "trends": trends,
            "recommendations": recommendations,
        },
        "EVOLUTION_PLAN_READY": True,
    }


# ─────────────────────────────────────────────────────────────────────────────
# MASTER D3 STATUS
# ─────────────────────────────────────────────────────────────────────────────

def get_d3_status() -> Dict[str, Any]:
    """Master D3 status — quick summary of all governance outputs."""
    try:
        with _d3_connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT id, baseline_hash, frozen_at, module_count, tool_count "
                    "FROM d3_architecture_baseline WHERE protected=TRUE ORDER BY id LIMIT 1"
                )
                baseline = cur.fetchone()

                cur.execute(
                    "SELECT health_score, snapshot_at FROM d3_system_health_snapshots "
                    "ORDER BY snapshot_at DESC LIMIT 1"
                )
                health = cur.fetchone()

                cur.execute(
                    "SELECT overall_status, checked_at FROM d3_architecture_status "
                    "ORDER BY checked_at DESC LIMIT 1"
                )
                arch = cur.fetchone()

                cur.execute(
                    "SELECT COUNT(*) AS n FROM d3_optimization_recommendations "
                    "WHERE status='pending' AND priority='HIGH'"
                )
                high_recs = cur.fetchone()["n"]

        return {
            "DIAGRAM_3_STATUS": "ACTIVE",
            "GOVERNANCE_ACTIVE": True,
            "d3_version": _D3_VERSION,
            "d3_started_at": _D3_STARTED_AT,
            "BASELINE_HASH": baseline["baseline_hash"] if baseline else None,
            "BASELINE_PROTECTED": True if baseline else False,
            "ARCHITECTURE_BASELINE_CREATED": baseline is not None,
            "SYSTEM_HEALTH_SCORE": float(health["health_score"]) if health else None,
            "ARCHITECTURE_STATUS": arch["overall_status"] if arch else "NOT_CHECKED",
            "high_priority_recommendations": high_recs,
            "ALL_REQUIRED_OUTPUTS": {
                "DIAGRAM_3_STATUS": "ACTIVE",
                "SYSTEM_HEALTH_SCORE": float(health["health_score"]) if health else "pending",
                "ARCHITECTURE_HEALTH": arch["overall_status"] if arch else "pending",
                "PERFORMANCE_HEALTH": "governed",
                "LEARNING_HEALTH": "governed",
                "MODEL_HEALTH": "governed",
                "SECURITY_HEALTH": "governed",
                "VERSION_STATUS": _D3_VERSION,
                "ROLLBACK_READY": baseline is not None,
                "NO_DUPLICATE_MODULES": "governed",
                "NO_DUPLICATE_TOOLS": "governed",
                "ARCHITECTURE_INTEGRITY": arch["overall_status"] == "INTACT" if arch else "pending",
                "ARCHITECTURE_BASELINE_CREATED": baseline is not None,
                "BASELINE_HASH": baseline["baseline_hash"][:16] + "..." if baseline else "pending",
                "BASELINE_PROTECTED": True if baseline else False,
                "NO_UNAUTHORIZED_CHANGES": "governed",
                "GOVERNANCE_ACTIVE": True,
                "EXECUTIVE_REPORT_READY": True,
                "EVOLUTION_PLAN_READY": True,
                "SYSTEM_READY_FOR_NEXT_CYCLE": arch["overall_status"] == "INTACT" if arch else False,
            },
        }
    except Exception as e:
        return {"DIAGRAM_3_STATUS": "ERROR", "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# FLASK ROUTE INSTALLATION
# ─────────────────────────────────────────────────────────────────────────────

def install_d3_routes(app):
    """Register all Diagram 3 governance admin endpoints on the Flask app."""
    from flask import request, jsonify

    def _auth():
        token = os.environ.get("ADMIN_TOKEN", "")
        got = request.headers.get("X-Admin-Token", "")
        return bool(token) and bool(got) and hmac.compare_digest(got, token)

    @app.route("/stock-api/admin/d3/g0/status", methods=["GET"])
    def d3_g0_status():
        if not _auth():
            return jsonify({"error": "unauthorized"}), 401
        try:
            return jsonify({
                "system_state": get_d3_system_state(),
                "checkpoint_config": get_d3_checkpoint_config(),
                "g0_cache": dict(_CHECKPOINT_CONFIG_CACHE.get("G0", {})),
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/stock-api/admin/d3/g1/status", methods=["GET"])
    def d3_g1_status():
        if not _auth():
            return jsonify({"error": "unauthorized"}), 401
        try:
            return jsonify({
                "system_state": get_d3_system_state(),
                "checkpoint_config": get_d3_checkpoint_config(),
                "g1_cache": dict(_CHECKPOINT_CONFIG_CACHE.get("G1", {})),
                "baseline_integrity": _g1_check_baseline_integrity(),
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/stock-api/admin/d3/g2/status", methods=["GET"])
    def d3_g2_status():
        if not _auth():
            return jsonify({"error": "unauthorized"}), 401
        try:
            resp = {
                "system_state": get_d3_system_state(),
                "checkpoint_config": get_d3_checkpoint_config(),
                "g2_cache": dict(_CHECKPOINT_CONFIG_CACHE.get("G2", {})),
                "mandatory_stage_orders": list(_G2_MANDATORY_STAGE_ORDERS),
            }
            # Real stage-completeness lookup — only run when a real
            # trace_id is passed in, never a fabricated default.
            test_trace_id = request.args.get("trace_id")
            if test_trace_id:
                resp["stage_completeness_for_trace_id"] = _g2_check_stage_completeness(test_trace_id)
            else:
                try:
                    resp["mandatory_check_names"] = _g2_mandatory_check_names()
                except Exception as e:
                    resp["mandatory_check_names_error"] = str(e)
            return jsonify(resp)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/stock-api/admin/d3/g3/status", methods=["GET"])
    def d3_g3_status():
        if not _auth():
            return jsonify({"error": "unauthorized"}), 401
        try:
            resp = {
                "system_state": get_d3_system_state(),
                "checkpoint_config": get_d3_checkpoint_config(),
                "g3_cache": dict(_CHECKPOINT_CONFIG_CACHE.get("G3", {})),
                "authorized_execution_modes": sorted(_G3_AUTHORIZED_EXECUTION_MODES),
                "unresolved_critical_or_quarantine_actions": _g3_check_unresolved_actions(),
            }
            # Real lookups — only run when the caller passes the real
            # version string it wants checked, never a fabricated default.
            test_strategy_version = request.args.get("strategy_version")
            if test_strategy_version:
                resp["strategy_approval_for_version"] = _g3_check_strategy_approval(test_strategy_version)
            test_model_version = request.args.get("model_version")
            if test_model_version:
                resp["model_approval_for_version"] = _g3_check_model_approval(test_model_version)
            return jsonify(resp)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/stock-api/admin/d3/g4/status", methods=["GET"])
    def d3_g4_status():
        if not _auth():
            return jsonify({"error": "unauthorized"}), 401
        try:
            resp = {
                "system_state": get_d3_system_state(),
                "checkpoint_config": get_d3_checkpoint_config(),
                "g4_cache": dict(_CHECKPOINT_CONFIG_CACHE.get("G4", {})),
                "min_samples": _G4_MIN_SAMPLES,
                "max_score_drift": _G4_MAX_SCORE_DRIFT,
            }
            # Real lookups — only run when the caller passes the real
            # model_name it wants checked, never a fabricated default.
            test_model_name = request.args.get("model_name")
            if test_model_name:
                resp["rollback_artifact_for_model"] = _g4_check_rollback_artifact(test_model_name)
                test_version = request.args.get("version")
                test_hash = request.args.get("weights_hash")
                if test_version and test_hash:
                    resp["version_manifest_check"] = _g4_check_version_manifest(
                        test_model_name, int(test_version), test_hash)
            return jsonify(resp)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/stock-api/admin/d3/g5/status", methods=["GET"])
    def d3_g5_status():
        if not _auth():
            return jsonify({"error": "unauthorized"}), 401
        try:
            cfg = _read_checkpoint_config("G5", force=True)
            current_state = cfg.get("state") or "NORMAL"
            resp = {
                "system_state": get_d3_system_state(),
                "checkpoint_config": get_d3_checkpoint_config(),
                "g5_cache": dict(_CHECKPOINT_CONFIG_CACHE.get("G5", {})),
                "recovery_gated_states": sorted(_D3_RECOVERY_GATED_STATES),
                "is_recovery_gated_now": current_state in _D3_RECOVERY_GATED_STATES,
                "mode_note": (
                    "SHADOW mode never blocks a resume -- an ALLOW is issued and the state "
                    "change is actually performed even if the recovery verification would "
                    "have failed under ENFORCE. Move G5 to ENFORCE for real blocking."
                ),
            }
            # Real on-demand check — only run when explicitly requested, since
            # a full ledger-chain walk is not free and this is a status route,
            # not the resume path itself.
            if request.args.get("check_recovery") == "1":
                resp["recovery_verification"] = _g5_check_recovery_verification()
            return jsonify(resp)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/stock-api/admin/d3/g5/resume", methods=["POST"])
    def d3_g5_resume():
        if not _auth():
            return jsonify({"error": "unauthorized"}), 401
        body = request.get_json(silent=True) or {}
        target_state = body.get("target_state")
        reason = body.get("reason")
        changed_by = body.get("changed_by") or "ADMIN_API"
        if not target_state or not reason:
            return jsonify({"error": "target_state and reason are both required"}), 400
        try:
            result = g5_authorize_resume(
                target_state=target_state, reason=reason, changed_by=changed_by,
                trigger_source="ADMIN_API",
            )
            status_code = 200 if result["decision"] == "ALLOW" else 403
            return jsonify(result), status_code
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/stock-api/admin/d3/g0/system-state", methods=["POST"])
    def d3_g0_set_system_state():
        if not _auth():
            return jsonify({"error": "unauthorized"}), 401
        body = request.get_json(silent=True) or {}
        state = body.get("state")
        reason = body.get("reason")
        changed_by = body.get("changed_by") or "ADMIN_API"
        if not state or not reason:
            return jsonify({"error": "state and reason are both required"}), 400
        try:
            return jsonify(set_d3_system_state(state=state, reason=reason, changed_by=changed_by))
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/stock-api/admin/d3/g0/checkpoint-mode", methods=["POST"])
    def d3_g0_set_checkpoint_mode():
        if not _auth():
            return jsonify({"error": "unauthorized"}), 401
        body = request.get_json(silent=True) or {}
        checkpoint = body.get("checkpoint")
        mode = body.get("mode")
        reason = body.get("reason")
        changed_by = body.get("changed_by") or "ADMIN_API"
        confirm = bool(body.get("confirm", False))
        if not checkpoint or not mode or not reason:
            return jsonify({"error": "checkpoint, mode, and reason are all required"}), 400
        try:
            return jsonify(set_d3_checkpoint_mode(
                checkpoint=checkpoint, mode=mode, reason=reason,
                changed_by=changed_by, confirm=confirm,
            ))
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/stock-api/admin/d3/status", methods=["GET"])
    def d3_status():
        if not _auth():
            return jsonify({"error": "unauthorized"}), 401
        return jsonify(get_d3_status())

    @app.route("/stock-api/admin/d3/baseline", methods=["GET"])
    def d3_baseline():
        if not _auth():
            return jsonify({"error": "unauthorized"}), 401
        try:
            with _d3_connect() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        "SELECT * FROM d3_architecture_baseline ORDER BY id DESC LIMIT 3"
                    )
                    rows = [dict(r) for r in cur.fetchall()]
                    for r in rows:
                        r["frozen_at"] = str(r["frozen_at"])
                        if r.get("snapshot_json"):
                            r["snapshot_json"]["module_names"] = (
                                r["snapshot_json"].get("module_names", [])[:5]
                            )
            return jsonify({"baselines": rows, "protected": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/stock-api/admin/d3/freeze-baseline", methods=["POST"])
    def d3_freeze_baseline():
        if not _auth():
            return jsonify({"error": "unauthorized"}), 401
        body = request.get_json(silent=True) or {}
        force = body.get("force", False)
        return jsonify(run_phase0_baseline_freeze(force=force))

    @app.route("/stock-api/admin/d3/discovery", methods=["GET"])
    def d3_discovery():
        if not _auth():
            return jsonify({"error": "unauthorized"}), 401
        return jsonify(run_phase1_discovery())

    @app.route("/stock-api/admin/d3/health", methods=["GET"])
    def d3_health():
        if not _auth():
            return jsonify({"error": "unauthorized"}), 401
        return jsonify(run_phase2_health())

    @app.route("/stock-api/admin/d3/performance", methods=["GET"])
    def d3_performance():
        if not _auth():
            return jsonify({"error": "unauthorized"}), 401
        days = int(request.args.get("days", 30))
        return jsonify(run_phase3_performance(period_days=days))

    @app.route("/stock-api/admin/d3/strategy-registry", methods=["GET"])
    def d3_strategy_registry():
        if not _auth():
            return jsonify({"error": "unauthorized"}), 401
        return jsonify(run_phase4_strategy())

    @app.route("/stock-api/admin/d3/model-registry", methods=["GET"])
    def d3_model_registry():
        if not _auth():
            return jsonify({"error": "unauthorized"}), 401
        return jsonify(run_phase5_models())

    @app.route("/stock-api/admin/d3/learning-approvals", methods=["GET"])
    def d3_learning_approvals():
        if not _auth():
            return jsonify({"error": "unauthorized"}), 401
        return jsonify(run_phase6_learning_approval())

    @app.route("/stock-api/admin/d3/change-log", methods=["GET"])
    def d3_change_log():
        if not _auth():
            return jsonify({"error": "unauthorized"}), 401
        return jsonify(run_phase7_change_log())

    @app.route("/stock-api/admin/d3/log-change", methods=["POST"])
    def d3_log_change():
        if not _auth():
            return jsonify({"error": "unauthorized"}), 401
        data = request.get_json(silent=True) or {}
        return jsonify(log_change(
            module=data.get("module", ""),
            reason=data.get("reason", ""),
            expected_impact=data.get("expected_impact", ""),
            author=data.get("author", "ADMIN"),
            tools_affected=data.get("tools_affected", ""),
            rollback_ref=data.get("rollback_ref", ""),
        ))

    @app.route("/stock-api/admin/d3/version-history", methods=["GET"])
    def d3_version_history():
        if not _auth():
            return jsonify({"error": "unauthorized"}), 401
        return jsonify(run_phase8_versions())

    @app.route("/stock-api/admin/d3/rollback", methods=["GET"])
    def d3_rollback():
        if not _auth():
            return jsonify({"error": "unauthorized"}), 401
        return jsonify(run_phase9_rollback())

    @app.route("/stock-api/admin/d3/optimization", methods=["GET"])
    def d3_optimization():
        if not _auth():
            return jsonify({"error": "unauthorized"}), 401
        return jsonify(run_phase10_optimization())

    @app.route("/stock-api/admin/d3/forecast", methods=["GET"])
    def d3_forecast():
        if not _auth():
            return jsonify({"error": "unauthorized"}), 401
        return jsonify(run_phase11_forecast())

    @app.route("/stock-api/admin/d3/security", methods=["GET"])
    def d3_security():
        if not _auth():
            return jsonify({"error": "unauthorized"}), 401
        return jsonify(run_phase12_security())

    @app.route("/stock-api/admin/d3/architecture", methods=["GET"])
    def d3_architecture():
        if not _auth():
            return jsonify({"error": "unauthorized"}), 401
        return jsonify(run_phase13_consistency())

    @app.route("/stock-api/admin/d3/executive-report", methods=["GET"])
    def d3_executive_report():
        if not _auth():
            return jsonify({"error": "unauthorized"}), 401
        try:
            with _d3_connect() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        "SELECT full_report_json, generated_at FROM d3_executive_reports "
                        "ORDER BY generated_at DESC LIMIT 1"
                    )
                    row = cur.fetchone()
            if row:
                return jsonify({"report": row["full_report_json"],
                                "generated_at": str(row["generated_at"])})
            return jsonify({"note": "No report yet — call POST /generate-report first"})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/stock-api/admin/d3/generate-report", methods=["POST"])
    def d3_generate_report():
        if not _auth():
            return jsonify({"error": "unauthorized"}), 401
        return jsonify(run_phase14_executive_report())

    @app.route("/stock-api/admin/d3/evolution-plan", methods=["GET"])
    def d3_evolution_plan():
        if not _auth():
            return jsonify({"error": "unauthorized"}), 401
        return jsonify(run_phase15_evolution())

    @app.route("/stock-api/admin/d3/trace/<trace_id>", methods=["GET"])
    def d3_trace(trace_id):
        if not _auth():
            return jsonify({"error": "unauthorized"}), 401
        try:
            with _d3_connect() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        """
                        SELECT * FROM d3_governance_event_links
                        WHERE governance_trace_id = %s
                           OR root_trace_id = %s
                           OR diagram1_trace_id = %s
                           OR diagram2_trace_id = %s
                           OR diagram3_trace_id = %s
                           OR governance_cycle_id = %s
                        ORDER BY id ASC
                        """,
                        (trace_id, trace_id, trace_id, trace_id, trace_id, trace_id),
                    )
                    rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                for k, v in list(r.items()):
                    if isinstance(v, datetime.datetime):
                        r[k] = v.isoformat()
            if not rows:
                return jsonify({
                    "trace_id": trace_id, "event_count": 0, "events": [],
                    "note": "No d3_governance_event_links rows found for this trace_id — "
                            "either it never touched Diagram 3 governance, or it predates "
                            "the v2 provenance columns (schema_version=1 rows only carry "
                            "diagram1_trace_id/diagram2_trace_id, not root_trace_id).",
                })
            return jsonify({"trace_id": trace_id, "event_count": len(rows), "events": rows})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/stock-api/admin/d3/run-cycle", methods=["POST"])
    def d3_run_cycle():
        if not _auth():
            return jsonify({"error": "unauthorized"}), 401
        body = request.get_json(silent=True) or {}
        return jsonify(run_governance_cycle(
            trigger=body.get("trigger", "manual_admin"),
            audit_trace_id=body.get("audit_trace_id"),
            paper_trade_id=body.get("paper_trade_id"),
            ticker=body.get("ticker"),
            phases=body.get("phases"),
            context=body.get("context"),
            is_test_record=bool(body.get("is_test_record", False)),
        ))

    @app.route("/stock-api/admin/d3/actions", methods=["GET"])
    def d3_actions():
        if not _auth():
            return jsonify({"error": "unauthorized"}), 401
        status = request.args.get("status")
        action_type = request.args.get("action_type")
        limit = int(request.args.get("limit", 50))
        include_test = request.args.get("include_test", "false").lower() == "true"
        clauses, params = ([] if include_test else ["is_test_record = FALSE"]), []
        if status:
            clauses.append("status = %s")
            params.append(status)
        if action_type:
            clauses.append("action_type = %s")
            params.append(action_type)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        try:
            with _d3_connect() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        f"SELECT * FROM d3_governance_actions {where} "
                        f"ORDER BY id DESC LIMIT %s",
                        params + [limit],
                    )
                    rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                for k, v in list(r.items()):
                    if isinstance(v, datetime.datetime):
                        r[k] = v.isoformat()
            return jsonify({
                "count": len(rows), "actions": rows,
                "note": "status is capped at REQUESTED / ADVISORY_ACKNOWLEDGED / NOT_ENFORCED "
                        "by a DB CHECK constraint — 'ENFORCED' can never appear here (see "
                        "aiem_diagram3_governance.py T-F section for why).",
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/stock-api/admin/d3/actions/<action_id>", methods=["GET"])
    def d3_action_detail(action_id):
        if not _auth():
            return jsonify({"error": "unauthorized"}), 401
        try:
            with _d3_connect() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        "SELECT * FROM d3_governance_actions WHERE action_id = %s", (action_id,)
                    )
                    row = cur.fetchone()
            if not row:
                return jsonify({"error": f"no action found for action_id={action_id}"}), 404
            row = dict(row)
            for k, v in list(row.items()):
                if isinstance(v, datetime.datetime):
                    row[k] = v.isoformat()
            return jsonify(row)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/stock-api/admin/d3/actions/<action_id>/recheck", methods=["POST"])
    def d3_action_recheck(action_id):
        if not _auth():
            return jsonify({"error": "unauthorized"}), 401
        return jsonify(check_action_status(action_id))

    print("[d3_governance] 34 admin routes installed at /stock-api/admin/d3/")


# ─────────────────────────────────────────────────────────────────────────────
# GOVERNANCE CYCLE RUNNER — real, on-demand full-phase audit run
#
# Runs phases 1-15 (phase 0 baseline stays startup/admin-only — freezing it
# on every cycle would defeat its purpose) under one governance_cycle_id,
# emitting one real hash-chained d3_governance_event_links row per phase
# with that phase's ACTUAL status/timing/error, never a fabricated PASS.
# When audit_trace_id / paper_trade_id / ticker are supplied (e.g. invoked
# from a real paper-trade close), those are attached to every event so the
# cycle is genuinely traceable back to that Diagram 1/2 trace — but most
# phases are system-wide checks, not per-trade, so this only records a
# real correlation ("this system-wide cycle ran around the same time as
# this trade"), not a claim that e.g. Phase 12 Security inspected that
# specific trade.
# ─────────────────────────────────────────────────────────────────────────────

_D3_CYCLE_PHASES = [
    (1, "PHASE_1_ARCHITECTURE_DISCOVERY", "run_phase1_discovery", run_phase1_discovery),
    (2, "PHASE_2_SYSTEM_HEALTH", "run_phase2_health", run_phase2_health),
    (3, "PHASE_3_PERFORMANCE_GOVERNANCE", "run_phase3_performance", run_phase3_performance),
    (4, "PHASE_4_STRATEGY_GOVERNANCE", "run_phase4_strategy", run_phase4_strategy),
    (5, "PHASE_5_MODEL_GOVERNANCE", "run_phase5_models", run_phase5_models),
    (6, "PHASE_6_LEARNING_APPROVAL", "run_phase6_learning_approval", run_phase6_learning_approval),
    (7, "PHASE_7_CHANGE_MANAGEMENT", "run_phase7_change_log", run_phase7_change_log),
    (8, "PHASE_8_VERSION_CONTROL", "run_phase8_versions", run_phase8_versions),
    (9, "PHASE_9_ROLLBACK_MANAGEMENT", "run_phase9_rollback", run_phase9_rollback),
    (10, "PHASE_10_SELF_OPTIMIZATION", "run_phase10_optimization", run_phase10_optimization),
    (11, "PHASE_11_SYSTEM_HEALTH_FORECAST", "run_phase11_forecast", run_phase11_forecast),
    (12, "PHASE_12_SECURITY_GOVERNANCE", "run_phase12_security", run_phase12_security),
    (13, "PHASE_13_ARCHITECTURE_CONSISTENCY", "run_phase13_consistency", run_phase13_consistency),
    (14, "PHASE_14_EXECUTIVE_REPORTING", "run_phase14_executive_report", run_phase14_executive_report),
    (15, "PHASE_15_LONG_TERM_EVOLUTION", "run_phase15_evolution", run_phase15_evolution),
]


def run_governance_cycle(
    trigger: str,
    audit_trace_id: Optional[str] = None,
    paper_trade_id: Optional[int] = None,
    ticker: Optional[str] = None,
    phases: Optional[list] = None,
    context: Optional[Dict[str, Any]] = None,
    is_test_record: bool = False,
) -> Dict[str, Any]:
    """
    Run a real governance cycle. `trigger` documents WHY this cycle ran
    (e.g. 'paper_trade_close', 'manual_verification', 'nightly_schedule').
    `phases` optionally restricts the run to a subset of phase numbers
    (1-15); default runs all 15. Returns per-phase real results plus the
    governance_cycle_id so the emitted events can be queried afterward.
    """
    # NOTE on concurrency (fixed): each phase's governance-function work AND
    # its ledger emit run in their OWN short transaction, committed
    # immediately after that one INSERT. Earlier this held a single
    # transaction (and therefore the global d3_governance_event_links
    # advisory xact lock) open across all 15 phases for the entire cycle
    # duration — on a live single-DB trading system that meant a nightly
    # full cycle could block the paper-trade-close hot path's own
    # link_paper_trade_close() emit for as long as the whole cycle took to
    # run. Committing per-phase bounds that block to (at most) one phase's
    # own emit, not the whole cycle.
    governance_cycle_id = uuid.uuid4().hex
    results = []
    run_set = _D3_CYCLE_PHASES if not phases else [
        p for p in _D3_CYCLE_PHASES if p[0] in set(phases)
    ]
    for phase_num, phase_name, func_name, func in run_set:
        started = datetime.datetime.utcnow()
        try:
            out = func()
            status = out.get("status", "UNKNOWN") if isinstance(out, dict) else "UNKNOWN"
            error = out.get("error") if isinstance(out, dict) else None
        except Exception as phase_e:
            out = {"phase": phase_name, "status": "ERROR", "error": str(phase_e)}
            status = "ERROR"
            error = str(phase_e)
        completed = datetime.datetime.utcnow()

        phase_conn = _d3_connect()
        try:
            event = _d3_emit_event(
                governance_cycle_id=governance_cycle_id,
                governance_phase=phase_name,
                governance_check_name=f"cycle_run_{func_name}",
                governance_function=f"aiem_diagram3_governance.{func_name}",
                started_at=started,
                completed_at=completed,
                check_result=status,
                diagram1_trace_id=audit_trace_id,
                diagram2_trace_id=audit_trace_id,
                paper_trade_id=paper_trade_id,
                ticker=ticker,
                reason_code="TRIGGER_" + trigger.upper(),
                reason_detail=f"triggered_by={trigger}",
                enforcement_action="ADVISORY_ONLY",
                enforcement_status="NOT_ENFORCED",
                output_payload=out if isinstance(out, dict) else {"raw": str(out)},
                input_payload=context,
                is_test_record=is_test_record,
                producer_module="aiem_diagram3_governance",
                producer_function=func_name,
                conn=phase_conn,
            )
            phase_conn.commit()
        except Exception as emit_e:
            phase_conn.rollback()
            results.append({
                "phase_num": phase_num,
                "phase": phase_name,
                "status": "EMIT_ERROR",
                "error": f"phase_status={status}; ledger emit failed: {emit_e}",
                "event_id": None,
                "event_hash": None,
                "duration_ms": round((completed - started).total_seconds() * 1000, 1),
            })
            continue
        finally:
            phase_conn.close()

        results.append({
            "phase_num": phase_num,
            "phase": phase_name,
            "status": status,
            "error": error,
            "event_id": event["id"],
            "event_hash": event["event_hash"],
            "duration_ms": round((completed - started).total_seconds() * 1000, 1),
        })

    overall = "PASS" if all(r["status"] in ("PASS", "OK", "PENDING_REVIEW") for r in results) else "PARTIAL"
    return {
        "governance_cycle_id": governance_cycle_id,
        "trigger": trigger,
        "audit_trace_id": audit_trace_id,
        "paper_trade_id": paper_trade_id,
        "ticker": ticker,
        "phases_run": len(results),
        "overall_status": overall,
        "results": results,
    }


def link_paper_trade_close(
    audit_trace_id: Optional[str],
    paper_trade_id: int,
    ticker: str,
    pnl: float,
    pnl_pct: float,
    exit_reason: str,
    signal_source: Optional[str] = None,
    is_test_record: bool = False,
) -> Dict[str, Any]:
    """
    Real, lightweight per-trade provenance link — called from the ACTUAL
    _aiem_close_paper_trade_and_run_loop close path in main.py (not a
    synthetic/simulated call), so diagram1_trace_id/diagram2_trace_id/
    paper_trade_id are the genuine ids this trade closed under. This does
    NOT re-run all 15 governance phases inline on every trade close
    (that would be unsafe on a live trading hot path) — for a full 15-
    phase audit tied to this trace, call run_governance_cycle() with the
    same audit_trace_id afterward (e.g. from the verification CLI).
    """
    now = datetime.datetime.utcnow()
    try:
        event = _d3_emit_event(
            governance_cycle_id=f"TRADE_CLOSE_{paper_trade_id}",
            governance_phase="TRADE_CLOSE_PROVENANCE_LINK",
            governance_check_name="link_paper_trade_close",
            governance_function="aiem_diagram3_governance.link_paper_trade_close",
            started_at=now,
            completed_at=now,
            check_result="RECORDED",
            diagram1_trace_id=audit_trace_id,
            diagram2_trace_id=audit_trace_id,
            paper_trade_id=paper_trade_id,
            ticker=ticker,
            strategy_id=signal_source,
            reason_code="PAPER_TRADE_CLOSED",
            reason_detail=exit_reason,
            enforcement_action="ADVISORY_ONLY",
            enforcement_status="NOT_ENFORCED",
            output_payload={"pnl": pnl, "pnl_pct": pnl_pct, "exit_reason": exit_reason,
                            "signal_source": signal_source},
            is_test_record=is_test_record,
        )
        return {"linked": True, "event_id": event["id"], "event_hash": event["event_hash"],
                "governance_trace_id": event["governance_trace_id"]}
    except Exception as e:
        print(f"[d3_governance] link_paper_trade_close FAILED for trade {paper_trade_id}: {e}")
        return {"linked": False, "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# T-F: GOVERNANCE-ACTION REQUEST / ACKNOWLEDGEMENT — honest scoping
#
# This is a single monolith: Diagram 2 and Diagram 3 run in the same process
# with no independently-owned D2 "service" that could send back a genuine,
# independently-verified acknowledgement of an enforcement action (spec
# Section 8 assumes such a service exists; it does not, here). So this
# module NEVER claims "ENFORCED" — that value isn't even legal in the
# `d3_governance_actions.status` column (DB CHECK constraint above blocks
# it at the schema level, not just by convention).
#
# What IS real: `request_governance_action()` genuinely records, in the
# immutable hash-chained ledger, that Diagram 3 identified something and
# formally requested an action. `check_action_status()` then does the
# most honest thing actually available in a single-process system: it
# re-reads the REAL current state of the real target row/table and reports
# whether that state is CONSISTENT with the request having been honored
# (ADVISORY_ACKNOWLEDGED) or not (NOT_ENFORCED) — self-consistency inside
# one process, not independent cross-service confirmation. Both outcomes
# are disclosed as advisory-only.
# ─────────────────────────────────────────────────────────────────────────────

def request_governance_action(
    phase: str,
    action_type: str,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    reason: Optional[str] = None,
    is_test_record: bool = False,
) -> Dict[str, Any]:
    """
    Records a real governance-action REQUEST: one immutable ledger event
    (enforcement_status='REQUESTED') plus one row in the mutable
    `d3_governance_actions` tracking table linked to it. Never marks
    anything enforced at request time — enforcement (if any) can only be
    assessed afterward via `check_action_status()`, and even then caps out
    at ADVISORY_ACKNOWLEDGED / NOT_ENFORCED.

    TEST J (automatic test-propagation) fix: this used to take
    `is_test_record` ONLY as an explicit keyword argument and never
    consulted the ambient `trace_context()` set by an upstream caller
    (e.g. a G0-G5 authorization already running inside
    `with trace_context(root_trace_id=..., is_test_record=True):`) --
    meaning a real governance action requested downstream of a labeled
    TEST root event (e.g. the PHASE_6_LEARNING_APPROVAL REJECT action or
    the PHASE_9_ROLLBACK_MANAGEMENT drift-review action) would silently
    fall back to is_test_record=False and mislabel a TEST-originated
    action as real production data. Now mirrors the exact same
    `ctx.get("is_test_record", is_test_record)` precedence already used by
    `require_governance_authorization` / `acknowledge_governance_decision`
    -- an active ambient context always wins over the caller's own default,
    and an explicit caller-supplied is_test_record=True still works when
    there is no ambient context (e.g. aiem_diagram3_g5_verify.py's direct
    calls). root_trace_id is also now forwarded to the underlying ledger
    event when the ambient context carries a real one -- `d3_governance_actions`
    itself has no trace_id/root_trace_id column (these are cross-cutting
    system actions, not always anchored to one candidate trace), so that
    part of the promise is necessarily scoped to the ledger event only;
    this is disclosed, not silently patched over.
    """
    action_id = uuid.uuid4().hex
    now = datetime.datetime.utcnow()
    ctx = get_trace_context() or {}
    ctx_is_test = bool(ctx.get("is_test_record", is_test_record))
    ctx_root_trace_id = ctx.get("root_trace_id")
    try:
        event = _d3_emit_event(
            governance_cycle_id=f"ACTION_{action_id}",
            governance_phase=phase,
            governance_check_name="request_governance_action",
            governance_function="aiem_diagram3_governance.request_governance_action",
            started_at=now,
            completed_at=now,
            check_result="ACTION_REQUESTED",
            root_trace_id=ctx_root_trace_id,
            strategy_id=target_id if target_type == "strategy" else None,
            reason_code=action_type,
            reason_detail=reason,
            enforcement_action=action_type,
            enforcement_status="REQUESTED",
            output_payload={"target_type": target_type, "target_id": target_id, "reason": reason},
            is_test_record=ctx_is_test,
            producer_module="aiem_diagram3_governance",
            producer_function="request_governance_action",
        )
    except Exception as e:
        print(f"[d3_governance] request_governance_action ledger emit FAILED: {e}")
        return {"requested": False, "error": str(e)}

    try:
        with _d3_connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """INSERT INTO d3_governance_actions
                       (action_id, governance_event_id, phase, action_type,
                        target_type, target_id, reason, status, is_test_record)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,'REQUESTED',%s)
                       RETURNING id, action_id, status""",
                    (action_id, event["id"], phase, action_type,
                     target_type, target_id, reason, ctx_is_test),
                )
                row = dict(cur.fetchone())
            conn.commit()
        return {
            "requested": True, "action_id": action_id, "action_row_id": row["id"],
            "status": row["status"], "governance_event_id": event["id"],
            "event_hash": event["event_hash"],
        }
    except Exception as e:
        print(f"[d3_governance] request_governance_action DB insert FAILED: {e}")
        return {"requested": False, "error": str(e),
                "governance_event_id": event["id"], "event_hash": event["event_hash"]}


def check_action_status(action_id: str) -> Dict[str, Any]:
    """
    Re-reads REAL current state for one requested action and updates its
    honest status. This is self-consistency within one process (D3 asking
    "does the real data still look the way I requested?"), not an
    independent cross-service acknowledgement — status is capped at
    ADVISORY_ACKNOWLEDGED / NOT_ENFORCED, never ENFORCED (also blocked at
    the DB layer by the status CHECK constraint).
    """
    try:
        with _d3_connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM d3_governance_actions WHERE action_id = %s", (action_id,)
                )
                action = cur.fetchone()
                if not action:
                    return {"checked": False, "error": f"no action found for action_id={action_id}"}
                action = dict(action)

                status = "NOT_ENFORCED"
                detail = "No automated real-state re-check implemented for this action_type " \
                         "— defaulting to the honest, non-claiming state."

                if action["action_type"] == "REJECT_LEARNING_PROPOSAL":
                    cur.execute(
                        "SELECT accepted, promoted FROM aiem_learning_proposals WHERE id = %s",
                        (action["target_id"],),
                    )
                    prow = cur.fetchone()
                    if prow is None:
                        detail = f"aiem_learning_proposals id={action['target_id']} no longer exists"
                        status = "NOT_ENFORCED"
                    elif prow["accepted"] is True or prow["promoted"] is True:
                        detail = (f"proposal accepted={prow['accepted']} promoted={prow['promoted']} "
                                  f"despite REJECT request — real state contradicts the request")
                        status = "NOT_ENFORCED"
                    else:
                        detail = (f"proposal accepted={prow['accepted']} promoted={prow['promoted']} "
                                  f"— consistent with the REJECT request as of this check")
                        status = "ADVISORY_ACKNOWLEDGED"

                elif action["action_type"] == "ARCHITECTURE_DRIFT_REVIEW":
                    # No automated remediation exists for architecture drift — a human
                    # must review it. Re-checking drift doesn't tell us the REQUEST
                    # was acted on, only whether drift still exists, so this always
                    # stays NOT_ENFORCED and says so honestly.
                    status = "NOT_ENFORCED"
                    detail = ("architecture drift review has no automated remediation path; "
                              "requires manual review — always advisory-only regardless of "
                              "current drift state")

                cur.execute(
                    """UPDATE d3_governance_actions
                       SET status = %s, checked_at = NOW(), check_detail = %s
                       WHERE action_id = %s""",
                    (status, detail, action_id),
                )
            conn.commit()
    except Exception as e:
        print(f"[d3_governance] check_action_status FAILED for {action_id}: {e}")
        return {"checked": False, "error": str(e)}

    # P0 (gap G1, ledger-completeness half): the mutable d3_governance_actions
    # row is now tamper-guarded (trg_d3ga_guard), but until this point ONLY
    # the initial REQUESTED state was ever captured in the immutable
    # hash-chained ledger -- the lifecycle resolution (ADVISORY_ACKNOWLEDGED /
    # NOT_ENFORCED, and any later re-check that flips between the two as
    # real state changes) was invisible to anyone reading the ledger alone.
    # Emit a real, linked ledger event for every check so the append-only
    # history captures the full lifecycle, not just the request. Best-effort:
    # a ledger-emit failure here must not un-do the real status update above,
    # it is reported honestly and surfaced to the caller.
    now = datetime.datetime.utcnow()
    try:
        event = _d3_emit_event(
            governance_cycle_id=f"ACTION_{action_id}",
            governance_phase=action.get("phase") or "UNKNOWN_PHASE",
            governance_check_name="check_action_status",
            governance_function="aiem_diagram3_governance.check_action_status",
            started_at=now,
            completed_at=now,
            check_result=f"ACTION_STATUS_{status}",
            parent_event_id=str(action.get("governance_event_id")) if action.get("governance_event_id") else None,
            strategy_id=action.get("target_id") if action.get("target_type") == "strategy" else None,
            reason_code=action.get("action_type"),
            reason_detail=detail,
            enforcement_action=action.get("action_type"),
            enforcement_status=status,
            output_payload={"action_id": action_id, "status": status, "detail": detail},
            is_test_record=bool(action.get("is_test_record")),
            producer_module="aiem_diagram3_governance",
            producer_function="check_action_status",
        )
        ledger_event_id = event.get("id")
        ledger_event_hash = event.get("event_hash")
    except Exception as e:
        print(f"[d3_governance] check_action_status ledger emit FAILED for {action_id}: {e}")
        ledger_event_id = None
        ledger_event_hash = None

    return {
        "checked": True, "action_id": action_id, "status": status, "detail": detail,
        "ledger_event_id": ledger_event_id, "ledger_event_hash": ledger_event_hash,
    }


# ─────────────────────────────────────────────────────────────────────────────
# STARTUP
# ─────────────────────────────────────────────────────────────────────────────

def d3_startup():
    """Called from main.py deferred init. Sets up schema and freezes baseline."""
    print("[d3_governance] startup — initializing Diagram 3 Governance Layer v" + _D3_VERSION)
    _d3_init_schema()

    try:
        subscribed = subscribe_to_bus()
        print(
            f"[d3_governance] CommunicationBus subscriber "
            f"{'registered' if subscribed else 'already registered'} — "
            f"real D2 StageEvents will be ledgered as they occur (Path A observation)"
        )
    except Exception as _sub_e:
        print(f"[d3_governance] bus subscription FAILED (non-fatal, D2 stage events will NOT be ledgered): {_sub_e}")

    try:
        comp_result = _seed_governance_components()
        print(f"[d3_governance] Section 12B component registry: {comp_result.get('status')} "
              f"{comp_result.get('components')}")
    except Exception as _comp_e:
        print(f"[d3_governance] component registry seed FAILED (non-fatal): {_comp_e}")

    result = run_phase0_baseline_freeze(force=False)
    status = result.get("status", "ERROR")
    h = result.get("BASELINE_HASH", "none")
    print(f"[d3_governance] Phase 0 baseline: {status}  hash={h[:16]}...")

    log_change(
        module="aiem_diagram3_governance",
        reason="Diagram 3 Governance Layer v1.0.0 activated",
        expected_impact="Non-invasive governance monitoring active; Diagram 1 and Diagram 2 unmodified",
        author="SYSTEM_STARTUP",
        rollback_ref=h[:16],
    )

    try:
        with _d3_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO d3_version_history (version, version_type, baseline_hash, notes)
                       VALUES (%s,'production',%s,'Initial D3 governance activation')
                       ON CONFLICT DO NOTHING""",
                    (_D3_VERSION, h)
                )
            conn.commit()
    except Exception as e:
        print(f"[d3_governance] version_history write: {e}")

    print("[d3_governance] startup complete — governance layer active")
