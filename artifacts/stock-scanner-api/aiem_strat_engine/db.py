"""
db.py — DB connection helper + DDL for all ase_* tables.
All tables use the ase_ prefix to avoid collision with existing aiem_* tables.
"""
from __future__ import annotations
import os
import psycopg2
import psycopg2.extras


def get_conn():
    return psycopg2.connect(os.environ["DATABASE_URL"])


DDL = """
-- ═══════════════════════════════════════════════════════════════════════════
-- Advanced Strategy Engine — schema (ase_ prefix)
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS ase_strategy_registry (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    family          TEXT NOT NULL,
    aliases         JSONB NOT NULL DEFAULT '[]',
    risk_class      TEXT NOT NULL,   -- DEFINED_RISK | LIMITED_RISK | UNDEFINED_RISK
    execution_mode  TEXT NOT NULL,   -- AUTONOMOUS | ANALYSIS_ONLY
    direction       TEXT NOT NULL,   -- BULLISH | BEARISH | NEUTRAL | ANY
    vol_thesis      TEXT NOT NULL,   -- HIGH_IV | LOW_IV | NEUTRAL | ANY
    min_legs        INTEGER NOT NULL,
    max_legs        INTEGER NOT NULL,
    has_stock       BOOLEAN NOT NULL DEFAULT FALSE,
    leg_templates   JSONB NOT NULL DEFAULT '[]',
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (name)
);

CREATE TABLE IF NOT EXISTS ase_engine_jobs (
    id              SERIAL PRIMARY KEY,
    ticker          TEXT NOT NULL,
    thesis          TEXT NOT NULL,   -- BULLISH | BEARISH | NEUTRAL | HIGH_IV | LOW_IV | EVENT
    scan_date       DATE NOT NULL,
    status          TEXT NOT NULL DEFAULT 'PENDING',  -- PENDING|CLAIMED|EXECUTING|DONE|FAILED
    priority        INTEGER NOT NULL DEFAULT 5,
    claimed_at      TIMESTAMPTZ,
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    error_msg       TEXT,
    attempts        INTEGER NOT NULL DEFAULT 0,
    decision_run_id TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (ticker, scan_date, thesis)
);
CREATE INDEX IF NOT EXISTS idx_ase_engine_jobs_status ON ase_engine_jobs(status, scan_date);

CREATE TABLE IF NOT EXISTS ase_decision_runs (
    id              SERIAL PRIMARY KEY,
    run_id          TEXT NOT NULL UNIQUE,  -- deterministic: ase_{ticker}_{date}_{thesis}_{uuid8}
    ticker          TEXT NOT NULL,
    underlying_price NUMERIC(12,4),
    thesis          TEXT NOT NULL,
    market_regime   TEXT,
    volatility_regime TEXT,
    event_context   TEXT,
    iv_rank         NUMERIC(6,2),
    iv_percentile   NUMERIC(6,2),
    expected_move   NUMERIC(10,4),
    strategies_evaluated INTEGER NOT NULL DEFAULT 0,
    strategies_rejected  INTEGER NOT NULL DEFAULT 0,
    selected_strategy_name TEXT,
    selected_evaluation_id INTEGER,
    runner_up_name  TEXT,
    no_trade_score  NUMERIC(6,4),
    decision        TEXT,  -- TRADE | NO_TRADE | INSUFFICIENT_DATA
    config_sha256   TEXT,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at     TIMESTAMPTZ,
    FOREIGN KEY (selected_evaluation_id) REFERENCES ase_strategy_evaluations(id)
        DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX IF NOT EXISTS idx_ase_decision_runs_ticker ON ase_decision_runs(ticker, started_at DESC);

CREATE TABLE IF NOT EXISTS ase_strategy_evaluations (
    id              SERIAL PRIMARY KEY,
    run_id          TEXT NOT NULL REFERENCES ase_decision_runs(run_id),
    strategy_name   TEXT NOT NULL,
    strategy_family TEXT NOT NULL,
    strategy_fingerprint TEXT NOT NULL,
    risk_class      TEXT NOT NULL,
    execution_mode  TEXT NOT NULL,
    eligible        BOOLEAN NOT NULL,
    rejection_reasons JSONB,
    net_debit_credit  NUMERIC(10,4),
    mid_price         NUMERIC(10,4),
    conservative_fill NUMERIC(10,4),
    slippage          NUMERIC(10,4),
    commission        NUMERIC(10,4),
    max_profit        NUMERIC(14,4),  -- NULL = unlimited
    max_loss          NUMERIC(14,4),  -- NULL = undefined
    breakevens        JSONB,
    pop               NUMERIC(6,4),
    pop_touch         NUMERIC(6,4),
    pop_max_profit    NUMERIC(6,4),
    pop_max_loss      NUMERIC(6,4),
    ev_before_costs   NUMERIC(10,4),
    ev_after_costs    NUMERIC(10,4),
    return_on_capital NUMERIC(8,4),
    return_on_risk    NUMERIC(8,4),
    capital_at_risk   NUMERIC(12,2),
    buying_power      NUMERIC(12,2),
    reward_risk       NUMERIC(8,4),
    delta             NUMERIC(8,4),
    gamma             NUMERIC(8,4),
    theta             NUMERIC(8,4),
    vega              NUMERIC(8,4),
    rho               NUMERIC(8,4),
    charm             NUMERIC(8,4),
    vanna             NUMERIC(8,4),
    vomma             NUMERIC(8,4),
    iv_rank           NUMERIC(6,2),
    iv_percentile     NUMERIC(6,2),
    realized_vol      NUMERIC(8,4),
    skew_exposure     NUMERIC(8,4),
    term_structure_exp NUMERIC(8,4),
    expected_move_coverage NUMERIC(6,4),
    assignment_risk   TEXT,
    pin_risk          TEXT,
    event_risk        TEXT,
    liquidity_score   NUMERIC(6,4),
    score_pop         NUMERIC(6,4),
    score_ev          NUMERIC(6,4),
    score_capital_pres NUMERIC(6,4),
    score_defined_risk NUMERIC(6,4),
    score_cap_efficiency NUMERIC(6,4),
    score_liquidity   NUMERIC(6,4),
    score_thesis_fit  NUMERIC(6,4),
    score_regime_fit  NUMERIC(6,4),
    score_vol_fit     NUMERIC(6,4),
    score_diversification NUMERIC(6,4),
    penalty_total     NUMERIC(6,4),
    capital_compounding_score NUMERIC(8,4),
    legs_json         JSONB NOT NULL,
    payoff_grid       JSONB,
    stress_losses     JSONB,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ase_evals_run ON ase_strategy_evaluations(run_id);
CREATE INDEX IF NOT EXISTS idx_ase_evals_score ON ase_strategy_evaluations(capital_compounding_score DESC);

CREATE TABLE IF NOT EXISTS ase_paper_trades (
    id              SERIAL PRIMARY KEY,
    paper_trade_id  TEXT NOT NULL UNIQUE,  -- ase_pt_{uuid}
    strategy_evaluation_id INTEGER REFERENCES ase_strategy_evaluations(id),
    strategy_fingerprint TEXT NOT NULL,
    decision_run_id TEXT NOT NULL,
    underlying      TEXT NOT NULL,
    strategy_name   TEXT NOT NULL,
    family          TEXT NOT NULL,
    thesis          TEXT NOT NULL,
    direction       TEXT,
    volatility_thesis TEXT,
    entry_time      TIMESTAMPTZ NOT NULL,
    planned_exit    TIMESTAMPTZ,
    probability_of_profit NUMERIC(6,4),
    expected_value  NUMERIC(10,4),
    maximum_profit  NUMERIC(14,4),
    maximum_loss    NUMERIC(14,4),
    capital_at_risk NUMERIC(12,2),
    buying_power    NUMERIC(12,2),
    return_on_risk  NUMERIC(8,4),
    liquidity_score NUMERIC(6,4),
    selected_score  NUMERIC(8,4),
    runner_up       TEXT,
    no_trade_score  NUMERIC(6,4),
    market_regime   TEXT,
    volatility_regime TEXT,
    event_context   TEXT,
    underlying_price_at_entry NUMERIC(12,4),
    status          TEXT NOT NULL DEFAULT 'OPEN',
    close_time      TIMESTAMPTZ,
    close_reason    TEXT,
    gross_pnl       NUMERIC(12,4),
    net_pnl         NUMERIC(12,4),
    commission_paid NUMERIC(8,4),
    return_on_capital_realized NUMERIC(8,4),
    max_favorable_excursion NUMERIC(12,4),
    max_adverse_excursion   NUMERIC(12,4),
    holding_period_days     INTEGER,
    assignment_occurred     BOOLEAN DEFAULT FALSE,
    exercise_occurred       BOOLEAN DEFAULT FALSE,
    is_adjustment           BOOLEAN DEFAULT FALSE,
    parent_trade_id         TEXT,
    audit_hash      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ase_pt_underlying ON ase_paper_trades(underlying, status);
CREATE INDEX IF NOT EXISTS idx_ase_pt_status ON ase_paper_trades(status, entry_time DESC);

CREATE TABLE IF NOT EXISTS ase_paper_trade_legs (
    id              SERIAL PRIMARY KEY,
    paper_trade_id  TEXT NOT NULL REFERENCES ase_paper_trades(paper_trade_id),
    leg_number      INTEGER NOT NULL,
    asset_type      TEXT NOT NULL,
    option_symbol   TEXT,
    call_or_put     TEXT,
    buy_or_sell     TEXT NOT NULL,
    open_or_close   TEXT NOT NULL DEFAULT 'OPEN',
    quantity        INTEGER NOT NULL,
    ratio           INTEGER NOT NULL DEFAULT 1,
    strike          NUMERIC(12,4),
    expiration      DATE,
    dte_at_entry    INTEGER,
    bid             NUMERIC(10,4),
    ask             NUMERIC(10,4),
    mid             NUMERIC(10,4),
    modeled_fill    NUMERIC(10,4),
    paper_fill      NUMERIC(10,4),
    iv              NUMERIC(8,4),
    delta           NUMERIC(8,4),
    gamma           NUMERIC(8,4),
    theta           NUMERIC(8,4),
    vega            NUMERIC(8,4),
    rho             NUMERIC(8,4),
    volume          INTEGER,
    open_interest   INTEGER,
    quote_timestamp TIMESTAMPTZ,
    data_provider   TEXT DEFAULT 'tradier',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (paper_trade_id, leg_number)
);

CREATE TABLE IF NOT EXISTS ase_adjustments (
    id              SERIAL PRIMARY KEY,
    adjustment_id   TEXT NOT NULL UNIQUE,
    paper_trade_id  TEXT NOT NULL REFERENCES ase_paper_trades(paper_trade_id),
    adjustment_type TEXT NOT NULL,  -- ROLL_UP|ROLL_DOWN|ROLL_OUT|ADD_WING|REDUCE|CLOSE_LEG|FULL_CLOSE|CONVERT
    reason          TEXT NOT NULL,
    legs_closed     JSONB NOT NULL DEFAULT '[]',
    legs_opened     JSONB NOT NULL DEFAULT '[]',
    net_cost        NUMERIC(10,4),
    new_paper_trade_id TEXT,  -- if adjustment creates a new parent record
    executed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ase_position_valuations (
    id              SERIAL PRIMARY KEY,
    paper_trade_id  TEXT NOT NULL REFERENCES ase_paper_trades(paper_trade_id),
    valuation_date  DATE NOT NULL,
    underlying_price NUMERIC(12,4),
    theoretical_value NUMERIC(12,4),
    modeled_value   NUMERIC(12,4),
    paper_value     NUMERIC(12,4),
    unrealized_pnl  NUMERIC(12,4),
    delta           NUMERIC(8,4),
    gamma           NUMERIC(8,4),
    theta           NUMERIC(8,4),
    vega            NUMERIC(8,4),
    dte_remaining   INTEGER,
    regime          TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (paper_trade_id, valuation_date)
);

CREATE TABLE IF NOT EXISTS ase_performance_reports (
    id              SERIAL PRIMARY KEY,
    report_id       TEXT NOT NULL UNIQUE,
    period_type     TEXT NOT NULL,  -- DAILY | WEEKLY | MONTHLY
    period_start    DATE NOT NULL,
    period_end      DATE NOT NULL,
    scans_run       INTEGER NOT NULL DEFAULT 0,
    strategies_evaluated INTEGER NOT NULL DEFAULT 0,
    strategies_rejected  INTEGER NOT NULL DEFAULT 0,
    no_trade_decisions   INTEGER NOT NULL DEFAULT 0,
    trades_opened        INTEGER NOT NULL DEFAULT 0,
    trades_closed        INTEGER NOT NULL DEFAULT 0,
    net_pnl_theoretical  NUMERIC(14,4),
    net_pnl_modeled      NUMERIC(14,4),
    net_pnl_paper        NUMERIC(14,4),
    win_count           INTEGER,
    loss_count          INTEGER,
    breakeven_count     INTEGER,
    win_rate            NUMERIC(6,4),
    avg_winner          NUMERIC(12,4),
    avg_loser           NUMERIC(12,4),
    profit_factor       NUMERIC(8,4),
    expectancy          NUMERIC(10,4),
    sharpe              NUMERIC(8,4),
    sortino             NUMERIC(8,4),
    max_drawdown        NUMERIC(12,4),
    calmar              NUMERIC(8,4),
    return_on_capital   NUMERIC(8,4),
    capital_utilization NUMERIC(6,4),
    brier_score         NUMERIC(8,4),
    by_family           JSONB,
    by_symbol           JSONB,
    by_regime           JSONB,
    by_iv_bucket        JSONB,
    by_dte_bucket       JSONB,
    trade_ledger        JSONB,
    equity_curve        JSONB,
    drawdown_curve      JSONB,
    report_sha256       TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (period_type, period_start)
);
"""

# Self-referential FK workaround: ase_decision_runs.selected_evaluation_id -> ase_strategy_evaluations
# Both tables must exist before the FK is enforced — use DEFERRABLE INITIALLY DEFERRED
DDL_FK_FIX = """
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_name='ase_decision_runs_selected_eval_fk'
    ) THEN
        ALTER TABLE ase_decision_runs
            ADD CONSTRAINT ase_decision_runs_selected_eval_fk
            FOREIGN KEY (selected_evaluation_id)
            REFERENCES ase_strategy_evaluations(id)
            DEFERRABLE INITIALLY DEFERRED;
    END IF;
EXCEPTION WHEN OTHERS THEN NULL;
END $$;
"""


# ── Phase 5 §8 additive column migrations ─────────────────────────────────────
# These ADD COLUMN IF NOT EXISTS statements are safe to run on any DB state.
# They extend ase_strategy_evaluations and ase_decision_runs without altering
# existing rows.  New rows get the values; old rows default to NULL.
DDL_PHASE5_MIGRATIONS = """
ALTER TABLE ase_strategy_evaluations
    ADD COLUMN IF NOT EXISTS score_inputs_json       JSONB,
    ADD COLUMN IF NOT EXISTS score_signal_quality    NUMERIC(6,4),
    ADD COLUMN IF NOT EXISTS direction_confidence_used NUMERIC(6,4),
    ADD COLUMN IF NOT EXISTS compatibility_filter_json JSONB;

ALTER TABLE ase_decision_runs
    ADD COLUMN IF NOT EXISTS n_compatible            INTEGER,
    ADD COLUMN IF NOT EXISTS n_compat_rejected       INTEGER,
    ADD COLUMN IF NOT EXISTS compatibility_filter_json JSONB;
"""


def create_schema() -> bool:
    """Create all ase_* tables if they do not exist. Safe to call repeatedly."""
    try:
        with get_conn() as conn, conn.cursor() as cur:
            # Strip the self-referential FK including its preceding comma so no
            # trailing comma is left before the closing ')' of ase_decision_runs.
            safe_ddl = DDL.replace(
                ",\n    FOREIGN KEY (selected_evaluation_id) REFERENCES ase_strategy_evaluations(id)\n"
                "        DEFERRABLE INITIALLY DEFERRED",
                ""
            )
            cur.execute(safe_ddl)
            conn.commit()
        # Add the FK via ALTER TABLE after both tables exist
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(DDL_FK_FIX)
            conn.commit()
        # Phase 5: additive column migrations
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(DDL_PHASE5_MIGRATIONS)
            conn.commit()
        return True
    except Exception as exc:
        print(f"[ase.db.create_schema] {type(exc).__name__}: {exc}")
        return False


def list_tables() -> list:
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'ase_%' ORDER BY tablename"
            )
            return [r[0] for r in cur.fetchall()]
    except Exception:
        return []
