-- AIEM Closed-Loop Learning Migration
-- Implements all 5 audit-completeness gaps from AIEM_ADAPTIVE_LEARNING_PROOF_REPORT
-- Run once; all statements are idempotent (IF NOT EXISTS / ADD COLUMN IF NOT EXISTS).

-- ─────────────────────────────────────────────────────────────────────────────
-- GAP 2: Repair signal_trust_history — full before/after per update
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE signal_trust_history
  ADD COLUMN IF NOT EXISTS audit_trace_id       TEXT,
  ADD COLUMN IF NOT EXISTS trade_id             TEXT,
  ADD COLUMN IF NOT EXISTS ticker               TEXT,
  ADD COLUMN IF NOT EXISTS old_trust_score      NUMERIC(10,6),
  ADD COLUMN IF NOT EXISTS new_trust_score      NUMERIC(10,6),
  ADD COLUMN IF NOT EXISTS delta                NUMERIC(10,6),
  ADD COLUMN IF NOT EXISTS reason_for_change    TEXT,
  ADD COLUMN IF NOT EXISTS win_loss_result      TEXT,
  ADD COLUMN IF NOT EXISTS pnl                  NUMERIC(14,4),
  ADD COLUMN IF NOT EXISTS pnl_pct              NUMERIC(10,4),
  ADD COLUMN IF NOT EXISTS n_trades_used        INT,
  ADD COLUMN IF NOT EXISTS learning_module_source TEXT DEFAULT 'MTM_EMA';

CREATE INDEX IF NOT EXISTS sth_signal_idx  ON signal_trust_history(signal_name, recorded_at DESC);
CREATE INDEX IF NOT EXISTS sth_trade_idx   ON signal_trust_history(trade_id);
CREATE INDEX IF NOT EXISTS sth_ticker_idx  ON signal_trust_history(ticker, recorded_at DESC);

-- ─────────────────────────────────────────────────────────────────────────────
-- GAP 5: Intermediate candidate rankings — prove why each ticker was
--        promoted or suppressed
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS aiem_candidate_rankings (
  id                    BIGSERIAL PRIMARY KEY,
  created_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  audit_trace_id        TEXT,
  run_id                TEXT         NOT NULL,
  ticker                TEXT         NOT NULL,
  signal_source         TEXT         NOT NULL,
  candidate_rank        INT,
  raw_score             NUMERIC(10,4),
  module_score_json     JSONB,
  trust_multiplier      NUMERIC(10,4) DEFAULT 1.0,
  drift_multiplier      NUMERIC(10,4) DEFAULT 1.0,
  rl_weight             NUMERIC(10,4) DEFAULT 1.0,
  final_adjusted_score  NUMERIC(10,4),
  accepted_or_rejected  TEXT         NOT NULL CHECK (accepted_or_rejected IN ('ACCEPTED','REJECTED')),
  decision_reason       TEXT,
  decision_authority    TEXT         DEFAULT 'AIEM'
);
CREATE INDEX IF NOT EXISTS aiem_cr_run_idx    ON aiem_candidate_rankings(run_id);
CREATE INDEX IF NOT EXISTS aiem_cr_ticker_idx ON aiem_candidate_rankings(ticker, created_at DESC);
CREATE INDEX IF NOT EXISTS aiem_cr_date_idx   ON aiem_candidate_rankings(created_at DESC);

-- ─────────────────────────────────────────────────────────────────────────────
-- GAP 3: Thompson sampler for paper trading — live alpha/beta per signal source
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS aiem_paper_thompson (
  id                    BIGSERIAL PRIMARY KEY,
  signal_source         TEXT         NOT NULL UNIQUE,
  alpha                 NUMERIC(10,4) NOT NULL DEFAULT 1.0,
  beta                  NUMERIC(10,4) NOT NULL DEFAULT 1.0,
  wins                  INT          NOT NULL DEFAULT 0,
  losses                INT          NOT NULL DEFAULT 0,
  sampled_score         NUMERIC(10,4),
  last_updated          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  last_audit_trace_id   TEXT,
  last_trade_id         TEXT,
  last_ticker           TEXT
);

CREATE TABLE IF NOT EXISTS aiem_paper_thompson_history (
  id                    BIGSERIAL PRIMARY KEY,
  recorded_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  signal_source         TEXT         NOT NULL,
  old_alpha             NUMERIC(10,4),
  old_beta              NUMERIC(10,4),
  new_alpha             NUMERIC(10,4),
  new_beta              NUMERIC(10,4),
  win_loss              TEXT,
  reward                NUMERIC(10,4),
  pnl_pct               NUMERIC(10,4),
  ticker                TEXT,
  trade_id              TEXT,
  audit_trace_id        TEXT
);
CREATE INDEX IF NOT EXISTS apth_src_idx   ON aiem_paper_thompson_history(signal_source, recorded_at DESC);
CREATE INDEX IF NOT EXISTS apth_trade_idx ON aiem_paper_thompson_history(trade_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- GAP 4: RL training run log — proof that PPO gradient step ran or didn't
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS rl_training_runs (
  id                      BIGSERIAL PRIMARY KEY,
  started_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  completed_at            TIMESTAMPTZ,
  buffer_rows_used        INT,
  policy_version_before   INT,
  policy_version_after    INT,
  loss_value              NUMERIC(10,6),
  gradient_step_completed BOOLEAN     NOT NULL DEFAULT FALSE,
  reward_mean             NUMERIC(10,4),
  reward_std              NUMERIC(10,4),
  notes                   TEXT
);

-- Seed Thompson sampler rows for all known signal sources
INSERT INTO aiem_paper_thompson (signal_source, wins, losses, alpha, beta)
VALUES
  ('gap_volume',       13, 24, 14.0, 25.0),
  ('multi_signal',      0, 31,  1.0, 32.0),
  ('unusual_calls',     0,  9,  1.0, 10.0),
  ('aiem_ai',           3,  0,  4.0,  1.0),
  ('conviction_stack',  1,  0,  2.0,  1.0),
  ('sweep',             0,  0,  1.0,  1.0),
  ('layer9',            0,  0,  1.0,  1.0),
  ('washout_ignition',  0,  0,  1.0,  1.0),
  ('oi_buildup',        0,  0,  1.0,  1.0)
ON CONFLICT (signal_source) DO NOTHING;
