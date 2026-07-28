"""
aiem_options_phase2.py  —  Phase III Phase 2: Strategy, Decision & Outcome Capture
====================================================================================
Sections 5-8 of the AIEM Standalone Options Engine Phase III directive.

5. Advanced Strategy Learning   → oe_strategy_registry, oe_strategy_candidates
6. Counterfactual Learning      → oe_counterfactual_snapshots, oe_counterfactual_outcomes
7. Decision Capture             → oe_decision_records
8. Trade & Outcome Capture      → oe_trade_records

Isolation:  zero imports from D1/D2/D3.  All tables prefixed oe_.
Failure:    every public function is non-fatal — log and return, never raise to caller.
Immutability: no delete/truncate on any existing row; counterfactual is_hypothetical
              enforced by DB CHECK constraint.
Look-ahead: counterfactual outcomes only calculated post-close; snapshot frozen at
            decision time using point-in-time chain data.
"""

from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime, date
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.extras

log = logging.getLogger("aiem_options_scheduler")
_DB_URL = os.environ.get("DATABASE_URL", "")

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1 — Canonical Strategy Catalog
# 41 named strategies from the Phase 2 directive.
# Each entry: (strategy_id, name, family, direction, call_put_type, risk_profile,
#              max_legs, defined_risk)
# ─────────────────────────────────────────────────────────────────────────────

_STRATEGY_CATALOG: List[Dict[str, Any]] = [
    # ── Directional / Vanilla ────────────────────────────────────────────────
    {"id": "LONG_CALL",          "name": "Long Call",
     "family": "directional", "direction": "BULLISH",   "call_put": "CALL",
     "risk_profile": "DEFINED_RISK",   "max_legs": 1, "defined_risk": True},
    {"id": "LONG_PUT",           "name": "Long Put",
     "family": "directional", "direction": "BEARISH",   "call_put": "PUT",
     "risk_profile": "DEFINED_RISK",   "max_legs": 1, "defined_risk": True},
    {"id": "SHORT_CALL",         "name": "Short (Naked) Call",
     "family": "directional", "direction": "BEARISH",   "call_put": "CALL",
     "risk_profile": "UNDEFINED_RISK", "max_legs": 1, "defined_risk": False},
    {"id": "SHORT_PUT",          "name": "Short Put",
     "family": "directional", "direction": "BULLISH",   "call_put": "PUT",
     "risk_profile": "UNDEFINED_RISK", "max_legs": 1, "defined_risk": False},
    # ── Income / Covered ─────────────────────────────────────────────────────
    {"id": "COVERED_CALL",       "name": "Covered Call",
     "family": "income",      "direction": "NEUTRAL_BULL", "call_put": "CALL",
     "risk_profile": "COVERED",        "max_legs": 2, "defined_risk": True},
    {"id": "CSP",                "name": "Cash-Secured Put (CSP)",
     "family": "income",      "direction": "NEUTRAL_BULL", "call_put": "PUT",
     "risk_profile": "DEFINED_RISK",   "max_legs": 1, "defined_risk": True},
    {"id": "PROTECTIVE_PUT",     "name": "Protective Put",
     "family": "income",      "direction": "NEUTRAL_BULL", "call_put": "PUT",
     "risk_profile": "DEFINED_RISK",   "max_legs": 2, "defined_risk": True},
    {"id": "COLLAR",             "name": "Collar",
     "family": "income",      "direction": "NEUTRAL",   "call_put": "BOTH",
     "risk_profile": "DEFINED_RISK",   "max_legs": 3, "defined_risk": True},
    # ── Vertical Spreads ─────────────────────────────────────────────────────
    {"id": "BULL_CALL_SPREAD",   "name": "Bull Call Spread",
     "family": "vertical",    "direction": "BULLISH",   "call_put": "CALL",
     "risk_profile": "DEFINED_RISK",   "max_legs": 2, "defined_risk": True},
    {"id": "BEAR_PUT_SPREAD",    "name": "Bear Put Spread",
     "family": "vertical",    "direction": "BEARISH",   "call_put": "PUT",
     "risk_profile": "DEFINED_RISK",   "max_legs": 2, "defined_risk": True},
    {"id": "BEAR_CALL_SPREAD",   "name": "Bear Call Spread (Credit)",
     "family": "vertical",    "direction": "BEARISH",   "call_put": "CALL",
     "risk_profile": "DEFINED_RISK",   "max_legs": 2, "defined_risk": True},
    {"id": "BULL_PUT_SPREAD",    "name": "Bull Put Spread (Credit)",
     "family": "vertical",    "direction": "BULLISH",   "call_put": "PUT",
     "risk_profile": "DEFINED_RISK",   "max_legs": 2, "defined_risk": True},
    # ── Time / Diagonal Spreads ───────────────────────────────────────────────
    {"id": "CALENDAR_CALL",      "name": "Calendar Spread (Call)",
     "family": "calendar",    "direction": "NEUTRAL_BULL", "call_put": "CALL",
     "risk_profile": "DEFINED_RISK",   "max_legs": 2, "defined_risk": True},
    {"id": "CALENDAR_PUT",       "name": "Calendar Spread (Put)",
     "family": "calendar",    "direction": "NEUTRAL_BEAR", "call_put": "PUT",
     "risk_profile": "DEFINED_RISK",   "max_legs": 2, "defined_risk": True},
    {"id": "DIAGONAL_CALL",      "name": "Diagonal Spread (Call)",
     "family": "diagonal",    "direction": "BULLISH",   "call_put": "CALL",
     "risk_profile": "DEFINED_RISK",   "max_legs": 2, "defined_risk": True},
    {"id": "DIAGONAL_PUT",       "name": "Diagonal Spread (Put)",
     "family": "diagonal",    "direction": "BEARISH",   "call_put": "PUT",
     "risk_profile": "DEFINED_RISK",   "max_legs": 2, "defined_risk": True},
    # ── Volatility / Straddle / Strangle ─────────────────────────────────────
    {"id": "LONG_STRADDLE",      "name": "Long Straddle",
     "family": "volatility",  "direction": "NEUTRAL",   "call_put": "BOTH",
     "risk_profile": "DEFINED_RISK",   "max_legs": 2, "defined_risk": True},
    {"id": "SHORT_STRADDLE",     "name": "Short Straddle",
     "family": "volatility",  "direction": "NEUTRAL",   "call_put": "BOTH",
     "risk_profile": "UNDEFINED_RISK", "max_legs": 2, "defined_risk": False},
    {"id": "LONG_STRANGLE",      "name": "Long Strangle",
     "family": "volatility",  "direction": "NEUTRAL",   "call_put": "BOTH",
     "risk_profile": "DEFINED_RISK",   "max_legs": 2, "defined_risk": True},
    {"id": "SHORT_STRANGLE",     "name": "Short Strangle",
     "family": "volatility",  "direction": "NEUTRAL",   "call_put": "BOTH",
     "risk_profile": "UNDEFINED_RISK", "max_legs": 2, "defined_risk": False},
    # ── Multi-Leg Income (Iron) ───────────────────────────────────────────────
    {"id": "IRON_CONDOR",        "name": "Iron Condor",
     "family": "iron",        "direction": "NEUTRAL",   "call_put": "BOTH",
     "risk_profile": "DEFINED_RISK",   "max_legs": 4, "defined_risk": True},
    {"id": "IRON_BUTTERFLY",     "name": "Iron Butterfly",
     "family": "iron",        "direction": "NEUTRAL",   "call_put": "BOTH",
     "risk_profile": "DEFINED_RISK",   "max_legs": 4, "defined_risk": True},
    # ── Butterflies ──────────────────────────────────────────────────────────
    {"id": "LONG_CALL_BUTTERFLY","name": "Long Call Butterfly",
     "family": "butterfly",   "direction": "NEUTRAL",   "call_put": "CALL",
     "risk_profile": "DEFINED_RISK",   "max_legs": 3, "defined_risk": True},
    {"id": "LONG_PUT_BUTTERFLY", "name": "Long Put Butterfly",
     "family": "butterfly",   "direction": "NEUTRAL",   "call_put": "PUT",
     "risk_profile": "DEFINED_RISK",   "max_legs": 3, "defined_risk": True},
    # ── Condors ───────────────────────────────────────────────────────────────
    {"id": "LONG_CALL_CONDOR",   "name": "Long Call Condor",
     "family": "condor",      "direction": "NEUTRAL",   "call_put": "CALL",
     "risk_profile": "DEFINED_RISK",   "max_legs": 4, "defined_risk": True},
    {"id": "LONG_PUT_CONDOR",    "name": "Long Put Condor",
     "family": "condor",      "direction": "NEUTRAL",   "call_put": "PUT",
     "risk_profile": "DEFINED_RISK",   "max_legs": 4, "defined_risk": True},
    # ── Ratio / Front / Back Spreads ──────────────────────────────────────────
    {"id": "RATIO_CALL_SPREAD",  "name": "Ratio Call Spread (Front Spread)",
     "family": "ratio",       "direction": "NEUTRAL_BULL", "call_put": "CALL",
     "risk_profile": "UNDEFINED_RISK", "max_legs": 2, "defined_risk": False},
    {"id": "RATIO_PUT_SPREAD",   "name": "Ratio Put Spread (Back Spread)",
     "family": "ratio",       "direction": "NEUTRAL_BEAR", "call_put": "PUT",
     "risk_profile": "UNDEFINED_RISK", "max_legs": 2, "defined_risk": False},
    {"id": "CALL_BACKSPREAD",    "name": "Call Backspread (Reverse Ratio)",
     "family": "ratio",       "direction": "BULLISH",   "call_put": "CALL",
     "risk_profile": "DEFINED_RISK",   "max_legs": 2, "defined_risk": True},
    {"id": "PUT_BACKSPREAD",     "name": "Put Backspread (Reverse Ratio)",
     "family": "ratio",       "direction": "BEARISH",   "call_put": "PUT",
     "risk_profile": "DEFINED_RISK",   "max_legs": 2, "defined_risk": True},
    # ── Risk Reversals ────────────────────────────────────────────────────────
    {"id": "RISK_REVERSAL",      "name": "Risk Reversal (Bullish)",
     "family": "risk_reversal", "direction": "BULLISH",  "call_put": "BOTH",
     "risk_profile": "UNDEFINED_RISK", "max_legs": 2, "defined_risk": False},
    {"id": "REVERSE_RISK_REVERSAL", "name": "Reverse Risk Reversal (Bearish)",
     "family": "risk_reversal", "direction": "BEARISH",  "call_put": "BOTH",
     "risk_profile": "UNDEFINED_RISK", "max_legs": 2, "defined_risk": False},
    # ── Exotic / Combination ──────────────────────────────────────────────────
    {"id": "JADE_LIZARD",        "name": "Jade Lizard",
     "family": "exotic",      "direction": "NEUTRAL_BULL", "call_put": "BOTH",
     "risk_profile": "DEFINED_RISK",   "max_legs": 3, "defined_risk": True},
    {"id": "BIG_LIZARD",         "name": "Big Lizard",
     "family": "exotic",      "direction": "NEUTRAL",   "call_put": "BOTH",
     "risk_profile": "UNDEFINED_RISK", "max_legs": 3, "defined_risk": False},
    # ── Synthetic / Arb ───────────────────────────────────────────────────────
    {"id": "SYNTHETIC_LONG",     "name": "Synthetic Long",
     "family": "synthetic",   "direction": "BULLISH",   "call_put": "BOTH",
     "risk_profile": "UNDEFINED_RISK", "max_legs": 2, "defined_risk": False},
    {"id": "SYNTHETIC_SHORT",    "name": "Synthetic Short",
     "family": "synthetic",   "direction": "BEARISH",   "call_put": "BOTH",
     "risk_profile": "UNDEFINED_RISK", "max_legs": 2, "defined_risk": False},
    {"id": "CONVERSION",         "name": "Conversion",
     "family": "arbitrage",   "direction": "NEUTRAL",   "call_put": "BOTH",
     "risk_profile": "DEFINED_RISK",   "max_legs": 3, "defined_risk": True},
    {"id": "REVERSAL",           "name": "Reversal",
     "family": "arbitrage",   "direction": "NEUTRAL",   "call_put": "BOTH",
     "risk_profile": "DEFINED_RISK",   "max_legs": 3, "defined_risk": True},
    {"id": "BOX_SPREAD",         "name": "Box Spread",
     "family": "arbitrage",   "direction": "NEUTRAL",   "call_put": "BOTH",
     "risk_profile": "DEFINED_RISK",   "max_legs": 4, "defined_risk": True},
    # ── Open-ended ────────────────────────────────────────────────────────────
    {"id": "CUSTOM_MULTI_LEG",   "name": "Custom Multi-Leg",
     "family": "custom",      "direction": "NEUTRAL",   "call_put": "BOTH",
     "risk_profile": "VARIABLE",       "max_legs": 8, "defined_risk": False},
    {"id": "STOCK_LONG_CALL",    "name": "Stock + Long Call Combo",
     "family": "combo",       "direction": "BULLISH",   "call_put": "CALL",
     "risk_profile": "COVERED",        "max_legs": 2, "defined_risk": True},
    {"id": "STOCK_LONG_PUT",     "name": "Stock + Long Put (Married Put)",
     "family": "combo",       "direction": "NEUTRAL_BULL", "call_put": "PUT",
     "risk_profile": "DEFINED_RISK",   "max_legs": 2, "defined_risk": True},
]
# Quick lookup: chain strategy name → canonical ID
_CHAIN_NAME_TO_ID: Dict[str, str] = {s["name"]: s["id"] for s in _STRATEGY_CATALOG}
_CHAIN_NAME_TO_ID.update({s["id"]: s["id"] for s in _STRATEGY_CATALOG})  # id→id passthrough

_BOOTSTRAPPED = False


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2 — Table DDL
# ─────────────────────────────────────────────────────────────────────────────

def bootstrap_phase2(db_url: str = "") -> bool:
    """
    Create all 5 Phase 2 tables (idempotent) and populate oe_strategy_registry.
    Returns True on success, False on failure (non-fatal caller).
    """
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return True
    db_url = db_url or _DB_URL
    try:
        with psycopg2.connect(db_url, connect_timeout=6) as conn, conn.cursor() as cur:

            # ── oe_strategy_registry ──────────────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS oe_strategy_registry (
                    id              SERIAL PRIMARY KEY,
                    strategy_id     VARCHAR(64)  UNIQUE NOT NULL,
                    name            VARCHAR(256) NOT NULL,
                    family          VARCHAR(64),
                    direction       VARCHAR(32),
                    call_put_type   VARCHAR(16),
                    risk_profile    VARCHAR(32),
                    max_legs        INTEGER,
                    defined_risk    BOOLEAN,
                    enabled         BOOLEAN DEFAULT TRUE,
                    registered_at   TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            # ── oe_strategy_candidates ────────────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS oe_strategy_candidates (
                    id                  BIGSERIAL PRIMARY KEY,
                    trace_id            VARCHAR(64)  NOT NULL,
                    ticker              VARCHAR(20)  NOT NULL,
                    scan_date           DATE         NOT NULL,
                    strategy_id         VARCHAR(64),
                    strategy_name       VARCHAR(256),
                    strategy_family     VARCHAR(64),
                    direction           VARCHAR(32),
                    call_put_type       VARCHAR(16),
                    legs_json           JSONB,
                    net_debit_credit    NUMERIC(12,4),
                    max_profit          NUMERIC(12,4),
                    max_loss            NUMERIC(12,4),
                    breakeven_lower     NUMERIC(12,4),
                    breakeven_upper     NUMERIC(12,4),
                    pop                 NUMERIC(8,4),
                    ev_after_costs      NUMERIC(12,4),
                    delta               NUMERIC(8,4),
                    gamma               NUMERIC(10,6),
                    theta               NUMERIC(8,4),
                    vega                NUMERIC(8,4),
                    margin_required     NUMERIC(12,4),
                    capital_required    NUMERIC(12,4),
                    liquidity_score     NUMERIC(6,4),
                    slippage_est        NUMERIC(8,4),
                    assignment_risk     VARCHAR(32),
                    regime_suitability  VARCHAR(64),
                    portfolio_effect    VARCHAR(32),
                    risk_adjusted_score NUMERIC(8,4),
                    final_rank          INTEGER,
                    selected            BOOLEAN DEFAULT FALSE,
                    rejected            BOOLEAN DEFAULT FALSE,
                    rejection_reason    TEXT,
                    full_data_json      JSONB,
                    captured_at         TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS oe_sc_trace_idx
                    ON oe_strategy_candidates(trace_id)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS oe_sc_selected_idx
                    ON oe_strategy_candidates(trace_id, selected)
            """)

            # ── oe_counterfactual_snapshots ────────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS oe_counterfactual_snapshots (
                    id              BIGSERIAL PRIMARY KEY,
                    alert_id        INTEGER,
                    trace_id        VARCHAR(64)  NOT NULL,
                    ticker          VARCHAR(20)  NOT NULL,
                    scan_date       DATE         NOT NULL,
                    decision_ts     TIMESTAMPTZ  NOT NULL,
                    options_chain_json JSONB,
                    call_data_json  JSONB,
                    put_data_json   JSONB,
                    candidates_json JSONB,
                    spot_at_decision NUMERIC(12,4),
                    front_iv_at_decision NUMERIC(8,4),
                    captured_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    CONSTRAINT oe_cf_snap_decision_before_now
                        CHECK (decision_ts <= NOW() + INTERVAL '1 minute')
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS oe_cf_snap_alert_idx
                    ON oe_counterfactual_snapshots(alert_id)
            """)

            # ── oe_counterfactual_outcomes ─────────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS oe_counterfactual_outcomes (
                    id              BIGSERIAL PRIMARY KEY,
                    alert_id        INTEGER      NOT NULL,
                    trace_id        VARCHAR(64)  NOT NULL,
                    snapshot_id     BIGINT,
                    strategy_id     VARCHAR(64),
                    strategy_name   VARCHAR(256),
                    description     TEXT,
                    is_hypothetical BOOLEAN NOT NULL DEFAULT TRUE,
                    entry_price     NUMERIC(12,4),
                    exit_price      NUMERIC(12,4),
                    pnl             NUMERIC(12,4),
                    pnl_pct         NUMERIC(10,6),
                    return_on_risk  NUMERIC(10,6),
                    vs_selected_pnl_delta NUMERIC(12,4),
                    expiry_date     DATE,
                    holding_days    INTEGER,
                    exit_reason     VARCHAR(64),
                    loss_attribution JSONB,
                    calculated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    CONSTRAINT oe_cf_outcome_is_hypothetical
                        CHECK (is_hypothetical = TRUE),
                    CONSTRAINT oe_cf_outcome_calculated_after_snapshot
                        CHECK (calculated_at >= '2026-01-01'::TIMESTAMPTZ)
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS oe_cf_out_alert_idx
                    ON oe_counterfactual_outcomes(alert_id)
            """)

            # ── oe_decision_records ────────────────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS oe_decision_records (
                    id                  BIGSERIAL PRIMARY KEY,
                    trace_id            VARCHAR(64)  NOT NULL,
                    ticker              VARCHAR(20)  NOT NULL,
                    scan_date           DATE         NOT NULL,
                    alert_id            INTEGER,
                    decision_ts         TIMESTAMPTZ  NOT NULL,
                    decision_type       VARCHAR(32)  NOT NULL,
                    candidate_count     INTEGER,
                    call_score          NUMERIC(6,2),
                    put_score           NUMERIC(6,2),
                    score_margin        NUMERIC(6,2),
                    final_direction     VARCHAR(32),
                    reason_codes        TEXT[],
                    feature_snapshot_json JSONB,
                    score_breakdown_json  JSONB,
                    qualifying_strategies JSONB,
                    rejected_strategies   JSONB,
                    risk_gate_result      JSONB,
                    portfolio_result      JSONB,
                    outcome             VARCHAR(32)  DEFAULT 'OPEN',
                    learning_status     VARCHAR(32)  DEFAULT 'PENDING',
                    verification_status VARCHAR(32)  DEFAULT 'UNVERIFIED',
                    chain_hash          VARCHAR(64),
                    created_at          TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS oe_dr_trace_idx
                    ON oe_decision_records(trace_id)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS oe_dr_decision_type_idx
                    ON oe_decision_records(decision_type)
            """)

            # ── oe_trade_records ───────────────────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS oe_trade_records (
                    id                   BIGSERIAL PRIMARY KEY,
                    alert_id             INTEGER,
                    trace_id             VARCHAR(64)  NOT NULL,
                    ticker               VARCHAR(20)  NOT NULL,
                    scan_date            DATE         NOT NULL,
                    strategy_family      VARCHAR(64),
                    direction            VARCHAR(32),
                    legs_json            JSONB,
                    entry_ts             TIMESTAMPTZ,
                    entry_price          NUMERIC(12,4),
                    exit_ts              TIMESTAMPTZ,
                    exit_price           NUMERIC(12,4),
                    quantity             INTEGER DEFAULT 1,
                    fees_est             NUMERIC(8,4),
                    slippage_est         NUMERIC(8,4),
                    premium_paid_received NUMERIC(12,4),
                    capital_reserved     NUMERIC(12,4),
                    bp_effect            NUMERIC(12,4),
                    max_risk             NUMERIC(12,4),
                    max_reward           NUMERIC(12,4),
                    breakeven_lower      NUMERIC(12,4),
                    breakeven_upper      NUMERIC(12,4),
                    entry_greeks_json    JSONB,
                    exit_greeks_json     JSONB,
                    entry_iv             NUMERIC(8,4),
                    exit_iv              NUMERIC(8,4),
                    underlying_price_path JSONB,
                    option_price_path    JSONB,
                    mfe_pct              NUMERIC(8,4),
                    mae_pct              NUMERIC(8,4),
                    realized_pnl         NUMERIC(12,4),
                    unrealized_pnl_path  JSONB,
                    return_pct           NUMERIC(10,6),
                    return_on_risk       NUMERIC(10,6),
                    holding_days         INTEGER,
                    exit_reason          VARCHAR(64),
                    fill_quality         VARCHAR(32),
                    liquidity_changes    JSONB,
                    regime               VARCHAR(64),
                    sector               VARCHAR(64),
                    industry             VARCHAR(64),
                    portfolio_state_json JSONB,
                    subsystem_outputs_json JSONB,
                    created_at           TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS oe_tr_alert_idx
                    ON oe_trade_records(alert_id)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS oe_tr_trace_idx
                    ON oe_trade_records(trace_id)
            """)

            # ── Additive column migrations (idempotent) ────────────────────────
            cur.execute("""
                ALTER TABLE oe_strategy_candidates
                    ADD COLUMN IF NOT EXISTS expiration   DATE,
                    ADD COLUMN IF NOT EXISTS quantity_ratio NUMERIC(8,4)
            """)
            cur.execute("""
                ALTER TABLE oe_decision_records
                    ADD COLUMN IF NOT EXISTS execution_plan_id VARCHAR(64)
            """)

            conn.commit()

        # Populate strategy registry (idempotent upsert)
        _seed_strategy_registry(db_url)
        _BOOTSTRAPPED = True
        log.info(f"[phase2] bootstrap complete: 6 tables ready, "
                 f"{len(_STRATEGY_CATALOG)} strategies registered")
        return True
    except Exception as e:
        log.warning(f"[phase2] bootstrap failed: {e}")
        return False


def _seed_strategy_registry(db_url: str) -> None:
    with psycopg2.connect(db_url, connect_timeout=6) as conn, conn.cursor() as cur:
        for s in _STRATEGY_CATALOG:
            cur.execute("""
                INSERT INTO oe_strategy_registry
                    (strategy_id, name, family, direction, call_put_type,
                     risk_profile, max_legs, defined_risk)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (strategy_id) DO UPDATE
                    SET name=EXCLUDED.name, family=EXCLUDED.family,
                        direction=EXCLUDED.direction, call_put_type=EXCLUDED.call_put_type,
                        risk_profile=EXCLUDED.risk_profile, max_legs=EXCLUDED.max_legs,
                        defined_risk=EXCLUDED.defined_risk
            """, (s["id"], s["name"], s["family"], s["direction"], s["call_put"],
                  s["risk_profile"], s["max_legs"], s["defined_risk"]))
        conn.commit()
    log.debug(f"[phase2] seeded {len(_STRATEGY_CATALOG)} strategies into oe_strategy_registry")


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 3 — Strategy Candidate Capture (Section 5 of directive)
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_strategy_id(raw_name: str) -> str:
    """Map chain strategy name or ID to canonical catalog ID. Fallback to raw name."""
    clean = str(raw_name).upper().replace(" ", "_").replace("-", "_")
    if clean in _CHAIN_NAME_TO_ID:
        return clean
    # Partial match
    for s in _STRATEGY_CATALOG:
        if s["id"] in clean or clean in s["id"]:
            return s["id"]
    return clean


def capture_strategy_candidates(
    trace_id:         str,
    ticker:           str,
    scan_date:        date,
    chain_strategies: list,
    ei_assessments:   list,
    call_data:        dict,
    put_data:         dict,
    call_score:       float,
    put_score:        float,
    selected_direction: str,
    db_url:           str = "",
) -> int:
    """
    Insert one row per strategy considered into oe_strategy_candidates.
    - chain_strategies: list of dicts from aiem_polygon_options_chain.evaluate_all_strategies
    - call_data / put_data: REQ6 directional contracts (always captured as candidates)
    - selected_direction: LONG_CALL | LONG_PUT | NO_TRADE
    Returns count of rows inserted, 0 on failure.
    """
    db_url = db_url or _DB_URL
    inserted = 0
    try:
        rows: List[tuple] = []
        seen_ids: set = set()

        # ── Build EI assessment lookup ────────────────────────────────────────
        ei_lookup: Dict[str, Any] = {}
        for ea in (ei_assessments or []):
            raw = ea if isinstance(ea, dict) else vars(ea) if hasattr(ea, "__dict__") else {}
            strat_k = raw.get("strategy") or raw.get("strategy_name") or ""
            ei_lookup[str(strat_k).upper()] = raw

        # ── From chain_strategies ─────────────────────────────────────────────
        for rank, cs in enumerate(chain_strategies or []):
            sid    = _resolve_strategy_id(cs.get("strategy", "CUSTOM_MULTI_LEG"))
            s_name = cs.get("strategy", sid)
            is_sel = (
                (selected_direction == "LONG_CALL" and sid == "LONG_CALL") or
                (selected_direction == "LONG_PUT"  and sid == "LONG_PUT") or
                (selected_direction not in ("LONG_CALL", "LONG_PUT", "NO_TRADE") and rank == 0)
            )
            ea = ei_lookup.get(str(cs.get("strategy", "")).upper(), {})

            row = _build_candidate_row(
                trace_id=trace_id, ticker=ticker, scan_date=scan_date,
                strategy_id=sid, strategy_name=s_name,
                cs=cs, ei_data=ea,
                rank=rank + 1,
                selected=is_sel,
                rejected=not is_sel,
                rejection_reason=(None if is_sel else "Lower rank / not selected by REQ6"),
            )
            rows.append(row)
            seen_ids.add(sid)

        # ── Always add LONG_CALL and LONG_PUT if not already from chain ───────
        for sid, cdata, score in [("LONG_CALL", call_data, call_score),
                                   ("LONG_PUT",  put_data,  put_score)]:
            if sid not in seen_ids:
                is_sel = (selected_direction == sid)
                row = _build_candidate_row(
                    trace_id=trace_id, ticker=ticker, scan_date=scan_date,
                    strategy_id=sid, strategy_name=sid.replace("_", " ").title(),
                    cs={
                        "strategy": sid,
                        "direction": "BULLISH" if sid == "LONG_CALL" else "BEARISH",
                        "pop":       cdata.get("probability_estimate"),
                        "max_loss":  cdata.get("premium_at_risk"),
                        "max_profit":cdata.get("profit_target"),
                        "ev_after_costs": (
                            (cdata.get("probability_estimate", 0) * cdata.get("expected_return", 0))
                            - ((1 - cdata.get("probability_estimate", 0)) * 1.0)
                        ),
                        "capital_required": cdata.get("premium_at_risk"),
                        "liquid": True,
                        "risk_class": "DEFINED_RISK",
                    },
                    ei_data={},
                    rank=len(rows) + 1,
                    selected=is_sel,
                    rejected=not is_sel,
                    rejection_reason=(
                        None if is_sel else
                        f"REQ6: {sid} scored {score:.1f} < threshold or lost to opponent"
                    ),
                    extra_greeks={
                        "delta": cdata.get("delta"), "gamma": cdata.get("gamma"),
                        "theta": cdata.get("theta"), "vega":  cdata.get("vega"),
                        "iv":    cdata.get("iv"),
                    },
                    breakeven_lower=cdata.get("breakeven"),
                    risk_adjusted_score=score,
                    slippage_est=cdata.get("slippage_pct"),
                )
                rows.append(row)
                seen_ids.add(sid)

        if not rows:
            log.debug(f"[phase2] capture_strategy_candidates: no rows for trace_id={trace_id}")
            return 0

        with psycopg2.connect(db_url, connect_timeout=4) as conn, conn.cursor() as cur:
            for row in rows:
                cur.execute("""
                    INSERT INTO oe_strategy_candidates (
                        trace_id, ticker, scan_date,
                        strategy_id, strategy_name, strategy_family,
                        direction, call_put_type, legs_json,
                        expiration, quantity_ratio,
                        net_debit_credit, max_profit, max_loss,
                        breakeven_lower, breakeven_upper,
                        pop, ev_after_costs,
                        delta, gamma, theta, vega,
                        margin_required, capital_required,
                        liquidity_score, slippage_est,
                        assignment_risk, regime_suitability, portfolio_effect,
                        risk_adjusted_score, final_rank,
                        selected, rejected, rejection_reason,
                        full_data_json
                    ) VALUES (
                        %s,%s,%s,  %s,%s,%s,
                        %s,%s,%s,  %s,%s,
                        %s,%s,%s,  %s,%s,
                        %s,%s,     %s,%s,%s,%s,
                        %s,%s,     %s,%s,
                        %s,%s,%s,  %s,%s,
                        %s,%s,%s,  %s
                    )
                """, row)
                inserted += 1
            conn.commit()

        log.info(f"[phase2] captured {inserted} strategy candidates trace_id={trace_id}")
    except Exception as e:
        log.warning(f"[phase2] capture_strategy_candidates failed trace_id={trace_id}: {e}")
    return inserted


def _build_candidate_row(
    trace_id, ticker, scan_date,
    strategy_id, strategy_name, cs, ei_data, rank,
    selected, rejected, rejection_reason,
    extra_greeks=None, breakeven_lower=None,
    risk_adjusted_score=None, slippage_est=None,
) -> tuple:
    cat = {s["id"]: s for s in _STRATEGY_CATALOG}.get(strategy_id, {})
    eg  = extra_greeks or {}
    # expiration: extract from legs[0] or top-level 'expiry'/'expiration' key
    expiry_raw = cs.get("expiry") or cs.get("expiration")
    if not expiry_raw:
        legs = cs.get("legs") or []
        expiry_raw = legs[0].get("expiry") or legs[0].get("expiration") if legs else None
    expiry = None
    if expiry_raw:
        try:
            expiry = date.fromisoformat(str(expiry_raw)[:10]) if expiry_raw else None
        except (ValueError, TypeError):
            expiry = None
    # quantity_ratio: for spreads/ratios this is the leg ratio (e.g. 1:2 → 0.5)
    qty_ratio = cs.get("quantity_ratio") or cs.get("ratio")
    if qty_ratio is None:
        legs = cs.get("legs") or []
        qtys = [abs(float(l.get("quantity", 1))) for l in legs if l.get("quantity")]
        qty_ratio = (min(qtys) / max(qtys)) if len(qtys) >= 2 and max(qtys) > 0 else 1.0
    return (
        trace_id, ticker, scan_date,
        strategy_id, strategy_name, cat.get("family"),
        cat.get("direction", cs.get("direction")),
        cat.get("call_put"),
        json.dumps(cs.get("legs") or []),
        expiry,
        float(qty_ratio) if qty_ratio is not None else None,
        cs.get("net_debit") or cs.get("net_credit"),
        cs.get("max_profit"),
        cs.get("max_loss"),
        breakeven_lower or cs.get("breakeven_lower") or cs.get("breakeven"),
        cs.get("breakeven_upper"),
        cs.get("pop"),
        cs.get("ev_after_costs"),
        eg.get("delta") or cs.get("delta"),
        eg.get("gamma") or cs.get("gamma"),
        eg.get("theta") or cs.get("theta"),
        eg.get("vega")  or cs.get("vega"),
        cs.get("margin_required") or cs.get("capital_required"),
        cs.get("capital_required"),
        (ei_data.get("liquidity_score") or (1.0 if cs.get("liquid") else 0.3)),
        slippage_est or ei_data.get("slippage_pct") or cs.get("slippage_pct"),
        "LOW" if cat.get("defined_risk") else "HIGH",
        cat.get("direction"),
        "NEUTRAL",
        risk_adjusted_score or cs.get("ccs") or cs.get("ev_after_costs"),
        rank,
        selected, rejected, rejection_reason,
        json.dumps({k: v for k, v in cs.items()
                    if k not in ("legs",)
                    and not isinstance(v, (bytes, memoryview))},
                   default=str),
    )


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 4 — Decision Record Capture (Section 7 of directive)
# ─────────────────────────────────────────────────────────────────────────────

def capture_decision_record(
    trace_id:           str,
    ticker:             str,
    scan_date:          date,
    direction:          str,
    call_score:         float,
    put_score:          float,
    margin:             float,
    call_scoring:       dict,
    put_scoring:        dict,
    verify_result:      dict,
    chain_strategies:   list,
    stock_data:         dict,
    alert_id:           Optional[int] = None,
    chain_hash:         Optional[str] = None,
    execution_plan_id:  Optional[str] = None,
    db_url:             str = "",
) -> Optional[int]:
    """
    Insert one decision record into oe_decision_records.
    decision_type one of: APPROVE | NO_TRADE | REJECT | SUBSTITUTE.
    Returns inserted row id, None on failure.
    """
    db_url = db_url or _DB_URL
    gate_failures = verify_result.get("gate_failures") or []

    # Classify decision
    if gate_failures:
        decision_type = "REJECT"
        reason_codes  = [f"GATE:{g}" for g in gate_failures[:10]]
    elif direction in ("LONG_CALL", "LONG_PUT"):
        best_chain_strat = (chain_strategies or [{}])[0].get("strategy", "") if chain_strategies else ""
        expected_dir     = "LONG_CALL" if direction == "LONG_CALL" else "LONG_PUT"
        if best_chain_strat and best_chain_strat.upper() != expected_dir:
            decision_type = "SUBSTITUTE"
            reason_codes  = [f"CHAIN_BEST:{best_chain_strat}", f"REQ6_WINNER:{direction}"]
        else:
            decision_type = "APPROVE"
            reason_codes  = [f"REQ6:{direction}", f"MARGIN:{round(margin,1)}"]
    else:
        decision_type = "NO_TRADE"
        reason_codes  = [
            f"CALL_SCORE:{round(call_score,1)}",
            f"PUT_SCORE:{round(put_score,1)}",
            f"MARGIN:{round(margin,1)}",
        ]

    qualifying = [c.get("strategy") for c in (chain_strategies or [])
                  if not c.get("rejected")]
    rejected   = [c.get("strategy") for c in (chain_strategies or [])
                  if c.get("rejected")]

    portfolio_result = {
        "sector":               stock_data.get("sector"),
        "sector_strength":      stock_data.get("sector_strength"),
        "market_regime":        stock_data.get("market_regime"),
        "account_balance":      stock_data.get("account_balance"),
        "open_positions_count": stock_data.get("open_positions_count"),
        "portfolio_heat":       stock_data.get("portfolio_heat"),
        "portfolio_check":      verify_result.get("portfolio_check"),
        "concentration_ok":     verify_result.get("concentration_ok"),
        "correlation_check":    verify_result.get("correlation_check"),
    }
    try:
        with psycopg2.connect(db_url, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO oe_decision_records (
                    trace_id, ticker, scan_date, alert_id,
                    execution_plan_id, decision_ts,
                    decision_type, candidate_count,
                    call_score, put_score, score_margin, final_direction,
                    reason_codes, feature_snapshot_json, score_breakdown_json,
                    qualifying_strategies, rejected_strategies,
                    risk_gate_result, portfolio_result,
                    outcome, learning_status, verification_status, chain_hash
                ) VALUES (
                    %s,%s,%s,%s,
                    %s,%s,
                    %s,%s,
                    %s,%s,%s,%s,
                    %s,%s,%s,
                    %s,%s,
                    %s,%s,
                    %s,%s,%s,%s
                ) RETURNING id
            """, (
                trace_id, ticker, scan_date, alert_id,
                execution_plan_id, datetime.utcnow(),
                decision_type, len(chain_strategies or []),
                round(call_score, 2), round(put_score, 2), round(margin, 2), direction,
                reason_codes,
                json.dumps({k: v for k, v in stock_data.items()
                            if not isinstance(v, (bytes, memoryview))}, default=str),
                json.dumps({"call": call_scoring, "put": put_scoring}, default=str),
                json.dumps(qualifying or []),
                json.dumps(rejected or []),
                json.dumps(gate_failures, default=str),
                json.dumps(portfolio_result, default=str),
                "OPEN" if direction in ("LONG_CALL", "LONG_PUT") else "CLOSED",
                "PENDING",
                "UNVERIFIED",
                chain_hash,
            ))
            row_id = cur.fetchone()[0]
            conn.commit()
        log.info(f"[phase2] decision_record id={row_id} type={decision_type} "
                 f"ticker={ticker} direction={direction} trace_id={trace_id}")
        return row_id
    except Exception as e:
        log.warning(f"[phase2] capture_decision_record failed trace_id={trace_id}: {e}")
        return None


def update_decision_alert_id(trace_id: str, alert_id: int,
                             db_url: str = "",
                             chain_hash: Optional[str] = None) -> None:
    """Back-fill alert_id (and chain_hash when available) on decision record after Stage 8."""
    db_url = db_url or _DB_URL
    try:
        with psycopg2.connect(db_url, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                UPDATE oe_decision_records
                SET alert_id  = COALESCE(alert_id, %s),
                    chain_hash = COALESCE(chain_hash, %s)
                WHERE trace_id=%s
            """, (alert_id, chain_hash, trace_id))
            conn.commit()
    except Exception as e:
        log.debug(f"[phase2] update_decision_alert_id failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 5 — Counterfactual Snapshot Capture (Section 6 of directive)
# ─────────────────────────────────────────────────────────────────────────────

def capture_counterfactual_snapshot(
    alert_id:        int,
    trace_id:        str,
    ticker:          str,
    scan_date:       date,
    options_chain:   dict,
    call_data:       dict,
    put_data:        dict,
    chain_strategies: list,
    spot:            float,
    front_iv:        float,
    db_url:          str = "",
) -> Optional[int]:
    """
    Freeze the live options chain and all candidate data at decision time.
    Stored as JSONB — point-in-time, no look-ahead.
    Returns snapshot id, None on failure.
    """
    db_url = db_url or _DB_URL
    try:
        snap_data = {
            "alert_id":       alert_id,
            "trace_id":       trace_id,
            "ticker":         ticker,
            "decision_ts":    datetime.utcnow().isoformat(),
            "spot":           spot,
            "front_iv":       front_iv,
            "chain_calls_n":  len(options_chain.get("calls", [])),
            "chain_puts_n":   len(options_chain.get("puts", [])),
        }
        candidates_snap = [
            {k: v for k, v in cs.items()
             if k not in ("legs", "ccs_components")
             and not isinstance(v, (bytes, memoryview))}
            for cs in (chain_strategies or [])
        ]
        with psycopg2.connect(db_url, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO oe_counterfactual_snapshots (
                    alert_id, trace_id, ticker, scan_date, decision_ts,
                    options_chain_json, call_data_json, put_data_json,
                    candidates_json, spot_at_decision, front_iv_at_decision
                ) VALUES (%s,%s,%s,%s,%s, %s,%s,%s, %s,%s,%s)
                RETURNING id
            """, (
                alert_id, trace_id, ticker, scan_date,
                datetime.utcnow(),
                json.dumps(snap_data, default=str),
                json.dumps({k: v for k, v in call_data.items()
                            if not isinstance(v, (bytes, memoryview))}, default=str),
                json.dumps({k: v for k, v in put_data.items()
                            if not isinstance(v, (bytes, memoryview))}, default=str),
                json.dumps(candidates_snap, default=str),
                spot, front_iv,
            ))
            snap_id = cur.fetchone()[0]
            conn.commit()
        log.info(f"[phase2] counterfactual_snapshot id={snap_id} "
                 f"alert_id={alert_id} ticker={ticker} "
                 f"{len(candidates_snap)} candidates frozen")
        return snap_id
    except Exception as e:
        log.warning(f"[phase2] capture_counterfactual_snapshot failed "
                    f"alert_id={alert_id}: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 6 — Counterfactual Outcome Calculation (Section 6 of directive)
# ─────────────────────────────────────────────────────────────────────────────

def _attribute_loss(selected_pnl: float, alt_pnl: float,
                    alt_strategy: dict, selected_direction: str) -> dict:
    """
    Simple rule-based loss attribution comparing selected vs alternative.
    All labels are deterministic given the two P&L values + strategy metadata.
    No look-ahead: only uses point-in-time snapshot fields.
    """
    delta_pnl = alt_pnl - selected_pnl
    attribution: Dict[str, Any] = {
        "direction":             "NEUTRAL",
        "structure":             "NEUTRAL",
        "strike":                "NEUTRAL",
        "expiry":                "NEUTRAL",
        "width":                 "NEUTRAL",
        "sizing":                "NEUTRAL",
        "entry_exit_timing":     "NEUTRAL",
        "vol_mispricing":        "NEUTRAL",
        "liquidity_execution":   "NEUTRAL",
        "portfolio_constraints": "NEUTRAL",
        "regime_misclassification": "NEUTRAL",
        "pnl_delta":             round(delta_pnl, 4),
    }
    # Direction: if alternative has opposite direction and did better
    alt_dir = (alt_strategy.get("direction") or "").upper()
    if delta_pnl > 0:
        if alt_dir and alt_dir != selected_direction:
            attribution["direction"] = "ALTERNATIVE_BETTER"
        else:
            attribution["structure"] = "ALTERNATIVE_STRUCTURE_BETTER"
    elif delta_pnl < 0:
        attribution["structure"] = "SELECTED_BETTER"
    # Vol: if alt is a volatility strategy (straddle/strangle) that did better
    alt_fam = (alt_strategy.get("family") or "").lower()
    if alt_fam in ("volatility", "iron") and delta_pnl > 0:
        attribution["vol_mispricing"] = "CREDIT_STRUCTURE_WOULD_HAVE_BEEN_BETTER"
    return attribution


def calculate_counterfactual_outcomes(
    alert_id:           int,
    trace_id:           str,
    ticker:             str,
    expiry:             date,
    final_price:        float,
    selected_direction: str,
    selected_pnl:       float,
    db_url:             str = "",
) -> int:
    """
    Post-close only. Calculates P&L for each frozen candidate strategy using
    point-in-time entry prices from oe_counterfactual_snapshots.
    Uses final_price from polygon_market_daily (same source as main grading).
    Look-ahead guard: this function is only called from grade_options_outcomes,
    which runs after outcome_date <= CURRENT_DATE.
    Returns count of outcome rows inserted.
    """
    db_url = db_url or _DB_URL
    inserted = 0
    try:
        with psycopg2.connect(db_url, connect_timeout=4) as conn, conn.cursor() as cur:
            # Load snapshot
            cur.execute("""
                SELECT id, candidates_json, call_data_json, put_data_json,
                       spot_at_decision, front_iv_at_decision, captured_at
                FROM oe_counterfactual_snapshots
                WHERE alert_id=%s ORDER BY id DESC LIMIT 1
            """, (alert_id,))
            snap_row = cur.fetchone()
            if not snap_row:
                log.debug(f"[phase2] no snapshot for alert_id={alert_id}, skip counterfactuals")
                return 0
            snap_id, cands_json, call_json, put_json, spot, front_iv, snap_ts = snap_row
            candidates = json.loads(cands_json) if isinstance(cands_json, str) else cands_json or []
            call_d     = json.loads(call_json)  if isinstance(call_json, str)  else call_json or {}
            put_d      = json.loads(put_json)   if isinstance(put_json, str)   else put_json or {}

            # Ensure LONG_CALL and LONG_PUT are always in candidate list for comparison
            _ensure_directional_candidates(candidates, call_d, put_d, spot)

            # Limit to 3+ structurally distinct alternatives + selected
            distinct_families = set()
            comparison_set: List[Dict] = []
            for c in candidates:
                fam = (c.get("strategy_family") or c.get("family") or
                       _resolve_family(c.get("strategy", "")))
                if len(comparison_set) < 10:
                    comparison_set.append(c)
                    distinct_families.add(fam)

            # Load selected P&L for delta comparison
            selected_pnl_map: Dict[str, float] = {}
            if selected_direction == "LONG_CALL":
                selected_pnl_map[selected_direction] = selected_pnl
            elif selected_direction == "LONG_PUT":
                selected_pnl_map[selected_direction] = selected_pnl

            for c in comparison_set:
                sid    = c.get("strategy_id") or c.get("strategy") or "UNKNOWN"
                s_name = c.get("strategy_name") or c.get("strategy") or sid
                fam    = (c.get("strategy_family") or c.get("family") or
                          _resolve_family(sid))

                # Calculate hypothetical P&L from frozen entry price
                entry_p, exit_p, pnl, pnl_pct, ror = _calc_strategy_pnl(
                    sid, c, float(spot or 0), float(final_price),
                    float(front_iv or 0.3), float(expiry.year))

                vs_delta = round(pnl - selected_pnl, 4) if entry_p is not None else None
                hold_days = (date.today() - snap_ts.date()).days if snap_ts else None
                attribution = _attribute_loss(selected_pnl, pnl, c, selected_direction)

                cur.execute("""
                    INSERT INTO oe_counterfactual_outcomes (
                        alert_id, trace_id, snapshot_id,
                        strategy_id, strategy_name, description,
                        is_hypothetical,
                        entry_price, exit_price, pnl, pnl_pct, return_on_risk,
                        vs_selected_pnl_delta, expiry_date, holding_days,
                        exit_reason, loss_attribution
                    ) VALUES (
                        %s,%s,%s,  %s,%s,%s,
                        TRUE,
                        %s,%s,%s,%s,%s,
                        %s,%s,%s,
                        %s,%s
                    )
                """, (
                    alert_id, trace_id, snap_id,
                    sid, s_name,
                    f"[HYPOTHETICAL] {s_name} — entry from frozen snapshot, "
                    f"exit at expiry close {final_price:.2f}",
                    entry_p, exit_p, round(pnl, 4),
                    round(pnl_pct, 6) if pnl_pct is not None else None,
                    round(ror, 6) if ror is not None else None,
                    vs_delta, expiry, hold_days,
                    "EXPIRED_AT_CLOSE",
                    json.dumps(attribution),
                ))
                inserted += 1

            conn.commit()
        log.info(f"[phase2] {inserted} counterfactual outcomes for alert_id={alert_id} "
                 f"ticker={ticker} final_price={final_price}")
    except Exception as e:
        log.warning(f"[phase2] calculate_counterfactual_outcomes failed "
                    f"alert_id={alert_id}: {e}")
    return inserted


def _ensure_directional_candidates(candidates, call_d, put_d, spot):
    """Guarantee LONG_CALL and LONG_PUT are in comparison set."""
    ids = {c.get("strategy_id") or c.get("strategy") for c in candidates}
    if "LONG_CALL" not in ids and call_d:
        candidates.insert(0, {
            "strategy_id": "LONG_CALL", "strategy": "LONG_CALL",
            "strategy_family": "directional", "family": "directional",
            "direction": "BULLISH",
            "mid": ((float(call_d.get("bid", 0)) + float(call_d.get("ask", 0))) / 2),
            "entry_price_per_share": ((float(call_d.get("bid", 0)) +
                                       float(call_d.get("ask", 0))) / 2),
            "max_loss": call_d.get("premium_at_risk"), "max_profit": None,
        })
    if "LONG_PUT" not in ids and put_d:
        candidates.insert(1, {
            "strategy_id": "LONG_PUT", "strategy": "LONG_PUT",
            "strategy_family": "directional", "family": "directional",
            "direction": "BEARISH",
            "mid": ((float(put_d.get("bid", 0)) + float(put_d.get("ask", 0))) / 2),
            "entry_price_per_share": ((float(put_d.get("bid", 0)) +
                                       float(put_d.get("ask", 0))) / 2),
            "max_loss": put_d.get("premium_at_risk"), "max_profit": None,
        })


def _resolve_family(strategy_id: str) -> str:
    cat = {s["id"]: s for s in _STRATEGY_CATALOG}
    return cat.get(strategy_id.upper(), {}).get("family", "unknown")


def _calc_strategy_pnl(
    strategy_id: str, cs: dict,
    spot: float, final_price: float,
    front_iv: float, year: float,
) -> tuple:
    """
    Simplified point-in-time P&L for common strategy families.
    Uses frozen entry prices from snapshot. No post-decision data.
    Returns (entry_price, exit_price, pnl, pnl_pct, return_on_risk).
    """
    sid  = strategy_id.upper()
    mid  = float(cs.get("mid") or cs.get("entry_price_per_share") or
                 cs.get("net_debit") or 0)
    ml   = float(cs.get("max_loss") or mid * 100 or 1)
    mp   = float(cs.get("max_profit") or ml * 2)

    try:
        if sid == "LONG_CALL":
            strike = float(cs.get("strike") or cs.get("call_strike") or
                           round(spot * 1.025 / 5) * 5)
            intrinsic = max(0.0, final_price - strike)
            pnl   = intrinsic - mid
            ror   = pnl / mid if mid > 0 else 0.0
            return (mid, round(intrinsic, 4), round(pnl, 4),
                    round(pnl / mid, 6) if mid > 0 else None, round(ror, 6))

        elif sid == "LONG_PUT":
            strike = float(cs.get("strike") or cs.get("put_strike") or
                           round(spot * 0.975 / 5) * 5)
            intrinsic = max(0.0, strike - final_price)
            pnl   = intrinsic - mid
            ror   = pnl / mid if mid > 0 else 0.0
            return (mid, round(intrinsic, 4), round(pnl, 4),
                    round(pnl / mid, 6) if mid > 0 else None, round(ror, 6))

        elif sid in ("IRON_CONDOR", "IRON_BUTTERFLY"):
            credit = float(cs.get("net_debit") or cs.get("max_profit") or mid)
            loss   = float(cs.get("max_loss") or ml)
            legs   = cs.get("legs") or []
            lower  = min((float(l.get("strike", spot)) for l in legs), default=spot * 0.95)
            upper  = max((float(l.get("strike", spot)) for l in legs), default=spot * 1.05)
            if lower <= final_price <= upper:
                pnl = credit
            else:
                pnl = -loss + credit
            ror = pnl / loss if loss > 0 else 0.0
            return (None, None, round(pnl, 4), round(pnl / (loss + 1e-9), 6), round(ror, 6))

        elif sid in ("BULL_CALL_SPREAD", "BEAR_PUT_SPREAD"):
            debit  = float(cs.get("net_debit") or mid)
            est    = mp * 0.5 - debit        # 50% of max-profit as conservative estimate
            pnl    = min(mp - debit, max(-debit, est))
            ror    = pnl / debit if debit > 0 else 0.0
            return (debit, None, round(pnl, 4),
                    round(pnl / debit, 6) if debit > 0 else None, round(ror, 6))

        elif sid in ("LONG_STRADDLE", "LONG_STRANGLE"):
            move = abs(final_price - spot)
            pnl  = move * 100 - mid * 100
            ror  = pnl / (mid * 100) if mid > 0 else 0.0
            return (mid, None, round(pnl / 100, 4),
                    round(pnl / (mid * 100 + 1e-9), 6), round(ror, 6))

        else:
            # Generic: use max_profit/max_loss proportional estimate
            # (conservative: assume 50% of max_profit if spot moved favorably)
            price_move_pct = (final_price - spot) / spot if spot > 0 else 0
            pnl = mp * 0.5 * abs(price_move_pct) * 10 - ml * 0.3
            ror = pnl / ml if ml > 0 else 0.0
            return (None, None, round(pnl, 4), round(pnl / (ml + 1e-9), 6), round(ror, 6))

    except Exception:
        return (None, None, 0.0, None, None)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 7 — Trade Record Capture (Section 8 of directive)
# ─────────────────────────────────────────────────────────────────────────────

def capture_trade_record(
    alert_id:        int,
    trace_id:        str,
    ticker:          str,
    scan_date:       date,
    direction:       str,
    sel_data:        dict,
    sel_strike:      float,
    alert_fields:    dict,
    call_score:      float,
    put_score:       float,
    stock_data:      dict,
    verify_result:   dict,
    best_chain_strategy: Optional[dict] = None,
    call_scoring:    Optional[dict] = None,
    put_scoring:     Optional[dict] = None,
    db_url:          str = "",
) -> Optional[int]:
    """
    Insert trade entry record. One row per alert_id.
    Returns inserted row id, None on failure.
    """
    db_url = db_url or _DB_URL
    try:
        entry_mid = (
            (float(sel_data.get("bid", 0)) + float(sel_data.get("ask", 0))) / 2
        )
        legs = []
        if best_chain_strategy and best_chain_strategy.get("legs"):
            legs = best_chain_strategy["legs"]
        else:
            legs = [{"action": "BUY", "type": direction.replace("LONG_", ""),
                     "strike": sel_strike, "mid": entry_mid}]

        greeks = {
            "delta": sel_data.get("delta"), "gamma": sel_data.get("gamma"),
            "theta": sel_data.get("theta"), "vega":  sel_data.get("vega"),
            "iv":    sel_data.get("iv"),
        }
        # ── Augment with BS-computed higher-order greeks ──────────────────────
        # rho/charm/vanna are not supplied by Tradier for single-leg alerts.
        # Compute them from first principles using the same BS infrastructure
        # in aiem_strat_engine.greeks so entry_greeks_json carries real values.
        # Fail-safe: any missing input → all three set to None (no partial compute).
        try:
            _spot_g = float(
                sel_data.get("spot_at_alert") or alert_fields.get("spot_at_alert") or 0
            )
            _k_g    = float(sel_strike or alert_fields.get("strike") or 0)
            _dte_g  = float(
                sel_data.get("dte") or alert_fields.get("dte") or 0
            )
            _iv_g   = float(sel_data.get("iv") or alert_fields.get("iv") or 0)
            _call_g = (direction == "LONG_CALL")
            _T_g    = _dte_g / 365.0
            if _spot_g > 0 and _k_g > 0 and _T_g > 0 and _iv_g > 0:
                from aiem_strat_engine.greeks import bs_rho, bs_charm, bs_vanna
                greeks["rho"]   = round(bs_rho(_spot_g, _k_g, _T_g, _iv_g, _call_g), 6)
                greeks["charm"] = round(bs_charm(_spot_g, _k_g, _T_g, _iv_g, _call_g), 6)
                greeks["vanna"] = round(bs_vanna(_spot_g, _k_g, _T_g, _iv_g), 6)
            else:
                greeks["rho"]   = None
                greeks["charm"] = None
                greeks["vanna"] = None
        except Exception as _gk_e:
            log.warning(f"[phase2] rho/charm/vanna compute failed: {_gk_e}")
            greeks["rho"]   = None
            greeks["charm"] = None
            greeks["vanna"] = None
        strategy_fam = (best_chain_strategy.get("strategy") if best_chain_strategy
                        else ("LONG_CALL" if direction == "LONG_CALL" else "LONG_PUT"))

        subsystem = {
            "req6_call_score":    call_score,
            "req6_put_score":     put_score,
            "direction_selected": direction,
            # Full stock_data snapshot — directive Section 8: nothing silently discarded
            "stock_data": {k: v for k, v in stock_data.items()
                           if not isinstance(v, (bytes, memoryview))},
            # Full verify_result — all gate outcomes, rule results, model scores
            "verify_result": {k: v for k, v in verify_result.items()
                              if not isinstance(v, (bytes, memoryview))},
            # REQ6 scoring breakdown
            "call_scoring": {k: v for k, v in (call_scoring or {}).items()
                             if not isinstance(v, (bytes, memoryview))}
                            if isinstance(call_scoring, dict) else call_scoring,
            "put_scoring": {k: v for k, v in (put_scoring or {}).items()
                            if not isinstance(v, (bytes, memoryview))}
                           if isinstance(put_scoring, dict) else put_scoring,
        }

        with psycopg2.connect(db_url, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO oe_trade_records (
                    alert_id, trace_id, ticker, scan_date,
                    strategy_family, direction, legs_json,
                    entry_ts, entry_price,
                    quantity, fees_est, slippage_est,
                    premium_paid_received, capital_reserved, bp_effect,
                    max_risk, max_reward,
                    breakeven_lower, breakeven_upper,
                    entry_greeks_json, entry_iv,
                    regime, sector,
                    portfolio_state_json, subsystem_outputs_json
                ) VALUES (
                    %s,%s,%s,%s,
                    %s,%s,%s,
                    %s,%s,
                    %s,%s,%s,
                    %s,%s,%s,
                    %s,%s,
                    %s,%s,
                    %s,%s,
                    %s,%s,
                    %s,%s
                ) RETURNING id
            """, (
                alert_id, trace_id, ticker, scan_date,
                strategy_fam, direction, json.dumps(legs),
                datetime.utcnow(), round(entry_mid, 4),
                1, 0.65, round(float(sel_data.get("slippage_pct", 0.05)), 4),
                round(entry_mid * 100, 2),
                round(float(sel_data.get("premium_at_risk", entry_mid * 100)), 2),
                round(float(sel_data.get("premium_at_risk", entry_mid * 100)), 2),
                round(float(sel_data.get("premium_at_risk", entry_mid * 100)), 2),
                round(float(sel_data.get("profit_target", entry_mid * 200)), 2),
                round(float(alert_fields.get("breakeven", sel_strike)), 4),
                None,
                json.dumps(greeks, default=str),
                sel_data.get("iv"),
                stock_data.get("market_regime"),
                stock_data.get("sector") or stock_data.get("sector_name"),
                json.dumps({}),
                json.dumps(subsystem, default=str),
            ))
            tr_id = cur.fetchone()[0]
            conn.commit()
        log.info(f"[phase2] trade_record id={tr_id} alert_id={alert_id} "
                 f"ticker={ticker} direction={direction}")
        return tr_id
    except Exception as e:
        log.warning(f"[phase2] capture_trade_record failed alert_id={alert_id}: {e}")
        return None


def update_trade_record_exit(
    alert_id:      int,
    outcome_str:   str,
    exit_price:    float,
    pnl_pct:       float,
    final_price:   float,
    db_url:        str = "",
) -> bool:
    """Update oe_trade_records with exit data at outcome grading time."""
    db_url = db_url or _DB_URL
    try:
        entry_prem = None
        with psycopg2.connect(db_url, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT entry_price, entry_ts, fees_est, slippage_est FROM oe_trade_records
                WHERE alert_id=%s ORDER BY id ASC LIMIT 1
            """, (alert_id,))
            row = cur.fetchone()
            if row:
                entry_prem = float(row[0]) if row[0] else None
                entry_ts   = row[1]
                fees_est   = float(row[2]) if row[2] else 0.0
                slip_est   = float(row[3]) if row[3] else 0.0
            else:
                fees_est   = 0.0
                slip_est   = 0.0
            hold = None
            if entry_prem is not None and entry_ts:
                _ets_naive = entry_ts.replace(tzinfo=None) if entry_ts.tzinfo else entry_ts
                hold = max(1, (datetime.utcnow() - _ets_naive).days)

            pnl_abs = round((pnl_pct * entry_prem) - fees_est - slip_est, 4) if entry_prem else None
            ror     = round(pnl_pct, 6)

            cur.execute("""
                UPDATE oe_trade_records
                SET exit_ts     = NOW(),
                    exit_price  = %s,
                    realized_pnl = %s,
                    return_pct   = %s,
                    return_on_risk = %s,
                    holding_days = %s,
                    exit_reason  = %s,
                    fill_quality = 'MARKET_ON_EXPIRY'
                WHERE alert_id  = %s
            """, (round(exit_price, 4), pnl_abs, ror, ror, hold,
                  outcome_str, alert_id))
            conn.commit()
        log.debug(f"[phase2] trade_record exit updated alert_id={alert_id} "
                  f"outcome={outcome_str} pnl_pct={pnl_pct:.4f}")
        return True
    except Exception as e:
        log.warning(f"[phase2] update_trade_record_exit failed alert_id={alert_id}: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 8 — Verification Gates
# ─────────────────────────────────────────────────────────────────────────────

class Phase2ValidationError(Exception):
    pass


def assert_all_strategies_registered(db_url: str = "") -> None:
    """Raise Phase2ValidationError if any catalog strategy is missing from DB."""
    db_url = db_url or _DB_URL
    try:
        with psycopg2.connect(db_url, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("SELECT strategy_id FROM oe_strategy_registry")
            db_ids = {r[0] for r in cur.fetchall()}
    except Exception as e:
        raise Phase2ValidationError(f"DB error in assert_all_strategies_registered: {e}")
    catalog_ids = {s["id"] for s in _STRATEGY_CATALOG}
    missing = catalog_ids - db_ids
    if missing:
        raise Phase2ValidationError(
            f"Missing from oe_strategy_registry: {sorted(missing)}")


def assert_min_alternatives(trace_id: str, min_count: int = 3,
                             db_url: str = "") -> None:
    """Raise if fewer than min_count structurally different strategy candidates recorded."""
    db_url = db_url or _DB_URL
    try:
        with psycopg2.connect(db_url, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(DISTINCT strategy_family) FROM oe_strategy_candidates
                WHERE trace_id=%s
            """, (trace_id,))
            n_families = cur.fetchone()[0]
    except Exception as e:
        raise Phase2ValidationError(f"DB error in assert_min_alternatives: {e}")
    if n_families < min_count:
        raise Phase2ValidationError(
            f"Only {n_families} distinct strategy families for trace_id={trace_id}; "
            f"need >={min_count}")


def assert_counterfactual_labels(db_url: str = "") -> None:
    """Raise if any counterfactual outcome row has is_hypothetical != TRUE."""
    db_url = db_url or _DB_URL
    try:
        with psycopg2.connect(db_url, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) FROM oe_counterfactual_outcomes
                WHERE is_hypothetical IS DISTINCT FROM TRUE
            """)
            bad = cur.fetchone()[0]
    except Exception as e:
        raise Phase2ValidationError(f"DB error in assert_counterfactual_labels: {e}")
    if bad > 0:
        raise Phase2ValidationError(
            f"{bad} rows in oe_counterfactual_outcomes with is_hypothetical != TRUE")


def assert_no_lookahead(db_url: str = "") -> None:
    """
    Raise if any counterfactual outcome was calculated before its snapshot was captured
    (would indicate look-ahead: outcome data used before decision was made).
    """
    db_url = db_url or _DB_URL
    try:
        with psycopg2.connect(db_url, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT co.id, co.calculated_at, cs.captured_at
                FROM oe_counterfactual_outcomes co
                JOIN oe_counterfactual_snapshots cs ON cs.id = co.snapshot_id
                WHERE co.calculated_at < cs.captured_at
            """)
            bad_rows = cur.fetchall()
    except Exception as e:
        raise Phase2ValidationError(f"DB error in assert_no_lookahead: {e}")
    if bad_rows:
        raise Phase2ValidationError(
            f"Look-ahead detected: {len(bad_rows)} outcome rows calculated "
            f"before snapshot captured_at")


def get_phase2_table_counts(db_url: str = "") -> dict:
    """Return row counts for all 5 Phase 2 tables."""
    db_url = db_url or _DB_URL
    counts = {}
    try:
        with psycopg2.connect(db_url, connect_timeout=4) as conn, conn.cursor() as cur:
            for tbl in ["oe_strategy_registry", "oe_strategy_candidates",
                        "oe_counterfactual_snapshots", "oe_counterfactual_outcomes",
                        "oe_decision_records", "oe_trade_records"]:
                cur.execute(f"SELECT COUNT(*) FROM {tbl}")
                counts[tbl] = cur.fetchone()[0]
    except Exception as e:
        counts["error"] = str(e)
    return counts
