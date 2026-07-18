"""
aiem_portfolio_engine/db.py
DDL bootstrap for all 4 ape_ tables.
Call bootstrap_portfolio_tables(db_url) once at startup.
"""
import psycopg2

_DDL = """
CREATE TABLE IF NOT EXISTS ape_portfolio_snapshots (
    snapshot_id          VARCHAR(64)  PRIMARY KEY,
    trace_id             VARCHAR(64),
    snapshot_ts          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    cash_available       NUMERIC(12,2),
    buying_power         NUMERIC(12,2),
    reserved_capital     NUMERIC(12,2),
    committed_capital    NUMERIC(12,2),
    n_open_positions     INTEGER      DEFAULT 0,
    total_market_value   NUMERIC(12,2),
    total_unrealized_pnl NUMERIC(12,2),
    positions_json       JSONB,
    pending_orders_json  JSONB        DEFAULT '[]',
    reconciled           BOOLEAN      NOT NULL DEFAULT FALSE,
    reconcile_error      TEXT,
    created_at           TIMESTAMPTZ  DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ape_portfolio_greeks (
    id                BIGSERIAL    PRIMARY KEY,
    snapshot_id       VARCHAR(64)  NOT NULL,
    phase             VARCHAR(10)  NOT NULL CHECK (phase IN ('BEFORE','AFTER')),
    portfolio_delta   NUMERIC(12,6),
    portfolio_gamma   NUMERIC(12,6),
    portfolio_theta   NUMERIC(12,6),
    portfolio_vega    NUMERIC(12,6),
    portfolio_rho     NUMERIC(12,6),
    portfolio_charm   NUMERIC(12,6),
    portfolio_vanna   NUMERIC(12,6),
    portfolio_vomma   NUMERIC(12,6),
    stock_equiv_delta NUMERIC(12,6),
    total_delta       NUMERIC(12,6),
    n_positions       INTEGER,
    computed_at       TIMESTAMPTZ  DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ape_stress_results (
    id               BIGSERIAL    PRIMARY KEY,
    snapshot_id      VARCHAR(64)  NOT NULL,
    phase            VARCHAR(10)  NOT NULL CHECK (phase IN ('BEFORE','AFTER')),
    scenario         VARCHAR(64)  NOT NULL,
    spot_change_pct  NUMERIC(8,4),
    iv_change_pct    NUMERIC(8,4),
    time_decay_days  INTEGER      DEFAULT 0,
    pl_portfolio     NUMERIC(12,2),
    pl_candidate     NUMERIC(12,2),
    pl_combined      NUMERIC(12,2),
    incremental_loss NUMERIC(12,2),
    limit_breach     BOOLEAN      DEFAULT FALSE,
    breach_details   TEXT,
    computed_at      TIMESTAMPTZ  DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ape_gate_decisions (
    id                  BIGSERIAL    PRIMARY KEY,
    candidate_id        VARCHAR(64)  NOT NULL,
    trace_id            VARCHAR(64),
    snapshot_id         VARCHAR(64),
    ticker              VARCHAR(20)  NOT NULL,
    scan_date           DATE         NOT NULL,
    strategy_name       VARCHAR(64),
    requested_size      INTEGER,
    approved_size       INTEGER,
    concentration_json  JSONB,
    correlation_json    JSONB,
    stress_json         JSONB,
    liquidity_json      JSONB,
    budget_json         JSONB,
    optimization_json   JSONB,
    greeks_before_json  JSONB,
    greeks_after_json   JSONB,
    decision            VARCHAR(40)  NOT NULL,
    decision_reasons    JSONB,
    limits_tested       JSONB,
    limits_passed       JSONB,
    limits_failed       JSONB,
    pe_gating_enabled   BOOLEAN      NOT NULL DEFAULT FALSE,
    config_sha256       VARCHAR(64),
    prev_evidence_hash  VARCHAR(64),
    evidence_hash       VARCHAR(64),
    created_at          TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ape_gate_ticker_date
    ON ape_gate_decisions (ticker, scan_date);
CREATE INDEX IF NOT EXISTS idx_ape_gate_trace
    ON ape_gate_decisions (trace_id);
CREATE INDEX IF NOT EXISTS idx_ape_snapshots_trace
    ON ape_portfolio_snapshots (trace_id);
CREATE INDEX IF NOT EXISTS idx_ape_greeks_snapshot
    ON ape_portfolio_greeks (snapshot_id, phase);
CREATE INDEX IF NOT EXISTS idx_ape_stress_snapshot
    ON ape_stress_results (snapshot_id, phase);
"""


def bootstrap_portfolio_tables(db_url: str) -> None:
    with psycopg2.connect(db_url, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            for stmt in [s.strip() for s in _DDL.split(";") if s.strip()]:
                cur.execute(stmt)
        conn.commit()
