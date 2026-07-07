-- AIEM_SUPERVISOR_META_REASONING_LAYER — DB Migration
-- Run once; all statements are idempotent.

-- ── Module 1: Learning Loop Audit ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS aiem_supervisor_loop_audit (
    id               BIGSERIAL PRIMARY KEY,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    audit_trace_id   TEXT,
    ticker           TEXT,
    trade_id         BIGINT,
    signal_source    TEXT,
    loop_complete    BOOLEAN NOT NULL DEFAULT FALSE,
    missing_steps_json  JSONB NOT NULL DEFAULT '[]',
    supervisor_verdict  TEXT NOT NULL DEFAULT 'INCOMPLETE',
    notes            TEXT
);
CREATE INDEX IF NOT EXISTS idx_sup_loop_trade ON aiem_supervisor_loop_audit(trade_id);
CREATE INDEX IF NOT EXISTS idx_sup_loop_created ON aiem_supervisor_loop_audit(created_at DESC);

-- ── Module 2: Bad Learning Detector ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS aiem_supervisor_bad_learning_flags (
    id                     BIGSERIAL PRIMARY KEY,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    audit_trace_id         TEXT,
    trade_id               BIGINT,
    ticker                 TEXT,
    signal_source          TEXT,
    flag_type              TEXT NOT NULL,
    old_value              NUMERIC,
    new_value              NUMERIC,
    expected_allowed_change NUMERIC,
    sample_size            INTEGER,
    reason                 TEXT,
    supervisor_action      TEXT NOT NULL
        CHECK (supervisor_action IN (
            'ALLOW_UPDATE','LIMIT_UPDATE','REVERSE_UPDATE',
            'REQUIRE_MORE_DATA','FREEZE_SIGNAL','FLAG_FOR_REVIEW'
        ))
);
CREATE INDEX IF NOT EXISTS idx_sup_bad_source ON aiem_supervisor_bad_learning_flags(signal_source);
CREATE INDEX IF NOT EXISTS idx_sup_bad_created ON aiem_supervisor_bad_learning_flags(created_at DESC);

-- ── Module 3: Risk Control ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS aiem_supervisor_risk_checks (
    id                     BIGSERIAL PRIMARY KEY,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    audit_trace_id         TEXT,
    ticker                 TEXT,
    trade_id               BIGINT,
    risk_score             NUMERIC NOT NULL DEFAULT 0,
    risk_flags_json        JSONB NOT NULL DEFAULT '[]',
    approved_by_aiem       BOOLEAN NOT NULL DEFAULT TRUE,
    approved_by_supervisor BOOLEAN NOT NULL DEFAULT TRUE,
    supervisor_action      TEXT NOT NULL
        CHECK (supervisor_action IN (
            'ALLOW','REDUCE_CONFIDENCE','REDUCE_POSITION_SIZE',
            'BLOCK_TRADE','PAUSE_SIGNAL_FAMILY','PAUSE_TRADING_DAY'
        )),
    reason                 TEXT
);
CREATE INDEX IF NOT EXISTS idx_sup_risk_ticker ON aiem_supervisor_risk_checks(ticker);
CREATE INDEX IF NOT EXISTS idx_sup_risk_created ON aiem_supervisor_risk_checks(created_at DESC);

-- ── Module 4: Performance Reports ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS aiem_supervisor_performance_reports (
    id                          BIGSERIAL PRIMARY KEY,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    period_type                 TEXT NOT NULL CHECK (period_type IN ('daily','weekly','monthly')),
    period_start                DATE NOT NULL,
    period_end                  DATE NOT NULL,
    total_alerts                INTEGER NOT NULL DEFAULT 0,
    total_trades                INTEGER NOT NULL DEFAULT 0,
    win_rate                    NUMERIC,
    avg_pnl_pct                 NUMERIC,
    max_drawdown                NUMERIC,
    confidence_calibration_score NUMERIC,
    learning_quality_score      NUMERIC,
    risk_discipline_score       NUMERIC,
    overall_supervisor_grade    TEXT CHECK (overall_supervisor_grade IN ('A','B','C','D','F')),
    report_json                 JSONB NOT NULL DEFAULT '{}',
    UNIQUE (period_type, period_start)
);

-- ── Module 5: Signal Lifecycle ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS aiem_supervisor_signal_lifecycle (
    id               BIGSERIAL PRIMARY KEY,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    signal_id        INTEGER,
    signal_name      TEXT NOT NULL,
    signal_source    TEXT NOT NULL,
    current_status   TEXT NOT NULL,
    new_status       TEXT NOT NULL
        CHECK (new_status IN (
            'ACTIVE','PROMOTED','WATCHLIST','FROZEN','DEMOTED','RETIRED'
        )),
    reason           TEXT,
    sample_size      INTEGER,
    win_rate         NUMERIC,
    avg_return       NUMERIC,
    recent_return    NUMERIC,
    regime_stability NUMERIC,
    oos_status       TEXT,
    supervisor_decision TEXT
);
CREATE INDEX IF NOT EXISTS idx_sup_lifecycle_source ON aiem_supervisor_signal_lifecycle(signal_source);

-- ── Module 6: Overfit Protection ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS aiem_supervisor_overfit_checks (
    id                      BIGSERIAL PRIMARY KEY,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    signal_id               INTEGER,
    signal_source           TEXT,
    audit_trace_id          TEXT,
    overfit_score           NUMERIC NOT NULL DEFAULT 0,
    sample_size             INTEGER,
    filter_count            INTEGER,
    in_sample_edge          NUMERIC,
    out_of_sample_edge      NUMERIC,
    recent_edge             NUMERIC,
    regime_stability_score  NUMERIC,
    outlier_dependency_score NUMERIC,
    verdict                 TEXT NOT NULL
        CHECK (verdict IN (
            'NOT_OVERFIT','POSSIBLE_OVERFIT','LIKELY_OVERFIT','REJECT_SIGNAL'
        )),
    action                  TEXT
);

-- ── Module 7: Supervisor Override Log ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS aiem_supervisor_overrides (
    id                          BIGSERIAL PRIMARY KEY,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    audit_trace_id              TEXT,
    ticker                      TEXT,
    trade_id                    BIGINT,
    aiem_original_decision      TEXT,
    aiem_original_confidence    NUMERIC,
    supervisor_final_decision   TEXT,
    supervisor_adjusted_confidence NUMERIC,
    override_type               TEXT,
    reason                      TEXT,
    evidence_json               JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_sup_override_ticker ON aiem_supervisor_overrides(ticker);
CREATE INDEX IF NOT EXISTS idx_sup_override_created ON aiem_supervisor_overrides(created_at DESC);

-- ── Convenience view: current signal health ────────────────────────────────
CREATE OR REPLACE VIEW aiem_supervisor_signal_health AS
SELECT
    apt.signal_source,
    COUNT(*)                                        AS total_trades,
    SUM(CASE WHEN apt.pnl > 0 THEN 1 ELSE 0 END)   AS wins,
    ROUND(
        100.0 * SUM(CASE WHEN apt.pnl > 0 THEN 1 ELSE 0 END)
        / NULLIF(COUNT(*), 0), 1)                   AS win_rate_pct,
    ROUND(AVG(apt.pnl_pct)::numeric, 4)             AS avg_pnl_pct,
    MAX(asl.new_status)                             AS lifecycle_status
FROM aiem_paper_trades apt
LEFT JOIN aiem_supervisor_signal_lifecycle asl
    ON asl.signal_source = apt.signal_source
WHERE apt.status != 'OPEN'
GROUP BY apt.signal_source;
