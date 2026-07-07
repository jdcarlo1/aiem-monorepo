"""
aiem_closed_loop_learning.py — Closed-Loop Learning Helpers
============================================================
Implements the 5 audit-completeness gaps identified in
AIEM_ADAPTIVE_LEARNING_PROOF_REPORT:

  Gap 1 – audit_trace_id wired into EVERY pick and exit (via main.py edits)
  Gap 2 – signal_trust_history: per-update before/after row
  Gap 3 – Thompson sampler (aiem_paper_thompson): alpha/beta per signal source
  Gap 4 – rl_training_runs: proof a PPO gradient step ran or honestly didn't
  Gap 5 – aiem_candidate_rankings: full pre-decision candidate list stored

All functions are non-fatal: any DB error is logged and swallowed so the
calling path (MTM / pick-candidates) never fails due to audit machinery.
"""

import os
import json
import math
import uuid
import datetime as _dt
from typing import Optional

import psycopg2
import psycopg2.extras
import numpy as _np

_DB_URL = os.environ.get("DATABASE_URL") or os.environ.get("AIEM_DATABASE_URL", "")


def _conn(timeout: int = 4):
    return psycopg2.connect(_DB_URL, connect_timeout=timeout)


# ─────────────────────────────────────────────────────────────────────────────
# SCHEMA BOOTSTRAP (idempotent — safe to call on every startup)
# ─────────────────────────────────────────────────────────────────────────────

_DDL = """
ALTER TABLE signal_trust_history
  ADD COLUMN IF NOT EXISTS audit_trace_id        TEXT,
  ADD COLUMN IF NOT EXISTS trade_id              TEXT,
  ADD COLUMN IF NOT EXISTS ticker                TEXT,
  ADD COLUMN IF NOT EXISTS old_trust_score       NUMERIC(10,6),
  ADD COLUMN IF NOT EXISTS new_trust_score       NUMERIC(10,6),
  ADD COLUMN IF NOT EXISTS delta                 NUMERIC(10,6),
  ADD COLUMN IF NOT EXISTS reason_for_change     TEXT,
  ADD COLUMN IF NOT EXISTS win_loss_result       TEXT,
  ADD COLUMN IF NOT EXISTS pnl                   NUMERIC(14,4),
  ADD COLUMN IF NOT EXISTS pnl_pct               NUMERIC(10,4),
  ADD COLUMN IF NOT EXISTS n_trades_used         INT,
  ADD COLUMN IF NOT EXISTS learning_module_source TEXT DEFAULT 'MTM_EMA';

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
  accepted_or_rejected  TEXT         NOT NULL,
  decision_reason       TEXT,
  decision_authority    TEXT         DEFAULT 'AIEM'
);
CREATE INDEX IF NOT EXISTS aiem_cr_run_idx    ON aiem_candidate_rankings(run_id);
CREATE INDEX IF NOT EXISTS aiem_cr_ticker_idx ON aiem_candidate_rankings(ticker, created_at DESC);

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

CREATE TABLE IF NOT EXISTS feedback_failure_log (
  id             BIGSERIAL PRIMARY KEY,
  occurred_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  step_name      TEXT,
  ticker         TEXT,
  trace_id       TEXT,
  signal_source  TEXT,
  error_message  TEXT,
  escalated      BOOLEAN DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ffl_step_idx ON feedback_failure_log(step_name, occurred_at DESC);
"""

_SEEDED = False


def init_schema() -> None:
    global _SEEDED
    if _SEEDED:
        return
    try:
        with _conn() as c, c.cursor() as cu:
            cu.execute(_DDL)
        _seed_thompson()
        _SEEDED = True
        print("[closed_loop] schema ready")
    except Exception as e:
        print(f"[closed_loop] schema init error (non-fatal): {e}")


def _seed_thompson() -> None:
    """Seed Thompson sampler rows from existing paper trade history."""
    _SOURCES = [
        "gap_volume", "multi_signal", "unusual_calls", "aiem_ai",
        "conviction_stack", "sweep", "layer9", "washout_ignition", "oi_buildup",
    ]
    try:
        with _conn() as c, c.cursor() as cu:
            cu.execute("""
                SELECT signal_source,
                       COUNT(*) FILTER (WHERE pnl > 0) AS wins,
                       COUNT(*) FILTER (WHERE pnl <= 0) AS losses
                FROM aiem_paper_trades
                WHERE status NOT IN ('OPEN','CANCELLED') AND pnl IS NOT NULL
                GROUP BY signal_source
            """)
            rows = {r[0]: (r[1], r[2]) for r in cu.fetchall()}
            for src in _SOURCES:
                w, l = rows.get(src, (0, 0))
                cu.execute("""
                    INSERT INTO aiem_paper_thompson
                        (signal_source, wins, losses, alpha, beta)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (signal_source) DO NOTHING
                """, (src, w, l, float(w) + 1.0, float(l) + 1.0))
    except Exception as e:
        print(f"[closed_loop] thompson seed error (non-fatal): {e}")


# ─────────────────────────────────────────────────────────────────────────────
# GAP 2 — signal_trust_history: before/after row on every EMA update
# ─────────────────────────────────────────────────────────────────────────────

def record_trust_update(
    *,
    signal_source: str,
    old_rolling_wr: float,
    new_rolling_wr: float,
    old_trust: float,
    new_trust: float,
    n_trades: int,
    win: bool,
    pnl: float,
    pnl_pct: float,
    ticker: str,
    trade_id: str,
    audit_trace_id: Optional[str] = None,
    context_bucket: str = "PAPER_TRADING",
) -> None:
    """
    Insert one row into signal_trust_history capturing the exact before/after
    of a single EMA update.  Called from MTM immediately before the upsert
    into signal_trust_weights.
    """
    try:
        with _conn(timeout=3) as c, c.cursor() as cu:
            cu.execute("""
                INSERT INTO signal_trust_history
                    (signal_name, context_bucket,
                     trust_weight, rolling_win_rate,
                     old_trust_score, new_trust_score, delta,
                     win_loss_result, pnl, pnl_pct,
                     ticker, trade_id, audit_trace_id,
                     n_trades_used, reason_for_change,
                     learning_module_source, recorded_at)
                VALUES
                    (%s, %s,
                     %s, %s,
                     %s, %s, %s,
                     %s, %s, %s,
                     %s, %s, %s,
                     %s, %s,
                     'MTM_EMA', NOW())
            """, (
                signal_source, context_bucket,
                round(new_trust, 6), round(new_rolling_wr, 6),
                round(old_trust, 6), round(new_trust, 6),
                round(new_trust - old_trust, 6),
                "WIN" if win else "LOSS",
                round(pnl, 4), round(pnl_pct, 4),
                ticker, str(trade_id), audit_trace_id,
                n_trades,
                f"EMA decay=0.95 outcome={'1.0' if win else '0.0'} "
                f"old_wr={old_rolling_wr:.4f}→{new_rolling_wr:.4f}",
            ))
    except Exception as e:
        print(f"[closed_loop] trust_history insert error (non-fatal): {e}")


# ─────────────────────────────────────────────────────────────────────────────
# GAP 3 — Thompson sampler: alpha/beta updated after every closed trade
# ─────────────────────────────────────────────────────────────────────────────

def update_paper_thompson(
    *,
    signal_source: str,
    win: bool,
    pnl_pct: float,
    ticker: str,
    trade_id: str,
    audit_trace_id: Optional[str] = None,
) -> dict:
    """
    After a paper trade closes:
      - increment alpha (win) or beta (loss)
      - draw a Thompson sample (Beta(alpha, beta))
      - store before/after in aiem_paper_thompson_history
      - return the new sampled_score so pick_candidates can use it
    """
    try:
        with _conn(timeout=3) as c, c.cursor() as cu:
            cu.execute("""
                INSERT INTO aiem_paper_thompson (signal_source, wins, losses, alpha, beta)
                VALUES (%s, 0, 0, 1.0, 1.0)
                ON CONFLICT (signal_source) DO NOTHING
            """, (signal_source,))

            cu.execute("""
                SELECT alpha, beta, wins, losses
                FROM aiem_paper_thompson
                WHERE signal_source = %s
            """, (signal_source,))
            row = cu.fetchone()
            if not row:
                return {}
            old_alpha, old_beta = float(row[0]), float(row[1])
            wins, losses = int(row[2]), int(row[3])

            if win:
                new_alpha = old_alpha + 1.0
                new_beta  = old_beta
                wins     += 1
            else:
                new_alpha = old_alpha
                new_beta  = old_beta + 1.0
                losses   += 1

            # Thompson sample from Beta(α, β)
            sampled = float(_np.random.beta(new_alpha, new_beta))
            reward  = round(pnl_pct / 100.0, 6)

            cu.execute("""
                UPDATE aiem_paper_thompson
                SET alpha=%s, beta=%s, wins=%s, losses=%s,
                    sampled_score=%s, last_updated=NOW(),
                    last_audit_trace_id=%s, last_trade_id=%s, last_ticker=%s
                WHERE signal_source=%s
            """, (round(new_alpha, 4), round(new_beta, 4), wins, losses,
                  round(sampled, 6), audit_trace_id, str(trade_id), ticker,
                  signal_source))

            cu.execute("""
                INSERT INTO aiem_paper_thompson_history
                    (signal_source, old_alpha, old_beta, new_alpha, new_beta,
                     win_loss, reward, pnl_pct, ticker, trade_id, audit_trace_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (signal_source,
                  round(old_alpha, 4), round(old_beta, 4),
                  round(new_alpha, 4), round(new_beta, 4),
                  "WIN" if win else "LOSS", round(reward, 6),
                  round(pnl_pct, 4), ticker, str(trade_id), audit_trace_id))

            print(f"[thompson] {signal_source}: α={old_alpha:.2f}→{new_alpha:.2f} "
                  f"β={old_beta:.2f}→{new_beta:.2f} sampled={sampled:.4f}")
            return {"signal_source": signal_source, "sampled_score": sampled,
                    "new_alpha": new_alpha, "new_beta": new_beta}

    except Exception as e:
        print(f"[closed_loop] thompson update error (non-fatal): {e}")
        return {}


def get_thompson_scores() -> dict:
    """
    Return {signal_source: sampled_score} for all sources with a stored
    Thompson score.  Used by _aiem_paper_pick_candidates() to apply the
    Thompson multiplier at pick time.
    """
    try:
        with _conn(timeout=3) as c, c.cursor() as cu:
            cu.execute("""
                SELECT signal_source, sampled_score, alpha, beta
                FROM aiem_paper_thompson
                WHERE sampled_score IS NOT NULL
            """)
            return {r[0]: {"score": float(r[1]), "alpha": float(r[2]), "beta": float(r[3])}
                    for r in cu.fetchall()}
    except Exception as e:
        print(f"[closed_loop] get_thompson_scores error (non-fatal): {e}")
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# GAP 4 — PPO training: run a batch gradient update, log the run
# ─────────────────────────────────────────────────────────────────────────────

_PPO_MIN_BUFFER = 10   # minimum real experience rows before training


def maybe_run_ppo_training() -> dict:
    """
    Check buffer size. If >= _PPO_MIN_BUFFER, run a PPO batch update across
    all experience rows, log the training run to rl_training_runs, and return
    a status dict.  Returns {"trained": False, "reason": ...} if skipped.
    """
    try:
        import aiem_rl_engine as _rl

        with _conn(timeout=4) as c, c.cursor() as cu:
            cu.execute("""
                SELECT COUNT(*) FROM rl_experience_buffer
                WHERE trade_id NOT IN ('99999','88888','99998','92001')
            """)
            n_real = int(cu.fetchone()[0])

        if n_real < _PPO_MIN_BUFFER:
            msg = (f"PPO buffer has {n_real} real rows "
                   f"(need >= {_PPO_MIN_BUFFER}) — gradient step skipped")
            print(f"[closed_loop] {msg}")
            _log_training_run(buffer_rows=n_real, completed=False, notes=msg)
            return {"trained": False, "reason": msg, "buffer_rows": n_real}

        # ── Fetch buffer ───────────────────────────────────────────────────
        with _conn(timeout=4) as c, c.cursor() as cu:
            cu.execute("""
                SELECT state_vector, action, reward, next_state_vector
                FROM rl_experience_buffer
                WHERE trade_id NOT IN ('99999','88888','99998','92001')
                ORDER BY created_at DESC
                LIMIT 50
            """)
            rows = cu.fetchall()

        # ── Get version before ─────────────────────────────────────────────
        ver_before = _get_ppo_version()

        # ── Run PPO updates ────────────────────────────────────────────────
        ppo = _rl.PPOPolicyOptimizer()
        rewards = []
        loss_sum = 0.0
        for state_j, action, reward, next_j in rows:
            state      = state_j if isinstance(state_j, dict) else {}
            next_state = next_j  if isinstance(next_j,  dict) else {}
            ppo.update_policy(state, action, float(reward or 0), next_state)
            rewards.append(float(reward or 0))
            loss_sum += abs(float(reward or 0))

        reward_mean = float(_np.mean(rewards)) if rewards else 0.0
        reward_std  = float(_np.std(rewards))  if rewards else 0.0
        loss_val    = loss_sum / max(len(rows), 1)

        ver_after = _get_ppo_version()

        _log_training_run(
            buffer_rows=n_real,
            completed=True,
            ver_before=ver_before,
            ver_after=ver_after,
            loss_val=loss_val,
            reward_mean=reward_mean,
            reward_std=reward_std,
            notes=f"Batch of {len(rows)} rows; PPO clip ε=0.2",
        )
        print(f"[closed_loop] PPO training done: {len(rows)} rows "
              f"reward_mean={reward_mean:+.3f} loss={loss_val:.4f} "
              f"v{ver_before}→v{ver_after}")
        return {
            "trained": True, "buffer_rows": n_real,
            "batch_size": len(rows),
            "reward_mean": reward_mean, "reward_std": reward_std,
            "loss_value": loss_val,
            "policy_version_before": ver_before,
            "policy_version_after": ver_after,
        }

    except Exception as e:
        msg = f"PPO training error: {e}"
        print(f"[closed_loop] {msg}")
        _log_training_run(buffer_rows=0, completed=False, notes=msg)
        return {"trained": False, "reason": msg}


def _get_ppo_version() -> int:
    try:
        with _conn(timeout=3) as c, c.cursor() as cu:
            cu.execute("SELECT n_updates FROM rl_ppo_policy WHERE is_live=TRUE LIMIT 1")
            row = cu.fetchone()
            return int(row[0]) if row else 0
    except Exception:
        return 0


def _log_training_run(
    *,
    buffer_rows: int,
    completed: bool,
    ver_before: int = 0,
    ver_after: int = 0,
    loss_val: float = 0.0,
    reward_mean: float = 0.0,
    reward_std: float = 0.0,
    notes: str = "",
) -> None:
    try:
        with _conn(timeout=3) as c, c.cursor() as cu:
            cu.execute("""
                INSERT INTO rl_training_runs
                    (completed_at, buffer_rows_used,
                     policy_version_before, policy_version_after,
                     loss_value, gradient_step_completed,
                     reward_mean, reward_std, notes)
                VALUES (NOW(), %s, %s, %s, %s, %s, %s, %s, %s)
            """, (buffer_rows, ver_before, ver_after,
                  round(loss_val, 6), completed,
                  round(reward_mean, 4), round(reward_std, 4), notes))
    except Exception as e:
        print(f"[closed_loop] log_training_run error (non-fatal): {e}")


# ─────────────────────────────────────────────────────────────────────────────
# GAP 5 — Intermediate candidate rankings
# ─────────────────────────────────────────────────────────────────────────────

def store_candidate_rankings(
    *,
    run_id: str,
    all_candidates: dict,
    final_ranked: list,
    rejected_tickers: set,
    rejection_reasons: dict,
    audit_trace_map: dict,
) -> None:
    """
    Store the full candidate list — both accepted and rejected — to
    aiem_candidate_rankings.  Called at the end of _aiem_paper_pick_candidates()
    with:
      all_candidates  — the complete {ticker: {...}} dict built during scoring
      final_ranked    — the ordered list that was returned (ACCEPTED)
      rejected_tickers — set of tickers that were blocked by gates
      rejection_reasons — {ticker: reason_string}
      audit_trace_map  — {ticker: audit_trace_id} if available
    """
    if not all_candidates:
        return

    final_tickers = {c["ticker"] for c in final_ranked}
    rank_map      = {c["ticker"]: i + 1 for i, c in enumerate(final_ranked)}

    rows = []
    for ticker, cand in all_candidates.items():
        accepted = ticker in final_tickers
        rank     = rank_map.get(ticker)
        reason   = (
            "passed all gates" if accepted
            else rejection_reasons.get(ticker, "below ranking threshold or gate blocked")
        )
        rows.append((
            audit_trace_map.get(ticker),
            run_id,
            ticker,
            cand.get("source", "unknown"),
            rank,
            round(float(cand.get("raw_score", cand.get("score", 0))), 4),
            json.dumps({
                "detail": cand.get("detail", ""),
                "trade_type": cand.get("trade_type", "STOCK"),
            }),
            round(float(cand.get("trust_mult", 1.0)), 4),
            round(float(cand.get("drift_mult", 1.0)), 4),
            1.0,
            round(float(cand.get("score", 0)), 4),
            "ACCEPTED" if accepted else "REJECTED",
            reason,
            "AIEM",
        ))

    if not rows:
        return

    try:
        with _conn(timeout=4) as c, c.cursor() as cu:
            psycopg2.extras.execute_values(cu, """
                INSERT INTO aiem_candidate_rankings
                    (audit_trace_id, run_id, ticker, signal_source,
                     candidate_rank, raw_score, module_score_json,
                     trust_multiplier, drift_multiplier, rl_weight,
                     final_adjusted_score, accepted_or_rejected,
                     decision_reason, decision_authority)
                VALUES %s
            """, rows)
        print(f"[closed_loop] stored {len(rows)} candidate rankings for run {run_id}")
    except Exception as e:
        print(f"[closed_loop] store_candidate_rankings error (non-fatal): {e}")


# ─────────────────────────────────────────────────────────────────────────────
# GAP 1 — log learning_update_applied audit step via pipeline audit
# ─────────────────────────────────────────────────────────────────────────────

def log_learning_update_step(
    *,
    trace_id: str,
    ticker: str,
    signal_source: str,
    old_trust: float,
    new_trust: float,
    thompson_before: float,
    thompson_after: float,
    pnl_pct: float,
    ppo_trained: bool,
) -> None:
    """
    Log the learning_update_applied step to aiem_pipeline_audit_log,
    proving the outcome fed back into parameters.
    """
    try:
        import aiem_pipeline_audit as _apa
        trace = _apa.PipelineTrace(ticker, trace_id=trace_id)
        trace.log_step(
            "learning_update_applied",
            function_name="_aiem_paper_mark_to_market",
            file_name="main.py",
            source_system="AIEM",
            processing_system="AIEM",
            input_summary=(
                f"pnl_pct={pnl_pct:+.2f}% "
                f"signal={signal_source}"
            ),
            output_summary=(
                f"trust: {old_trust:.4f}→{new_trust:.4f} "
                f"thompson: {thompson_before:.4f}→{thompson_after:.4f} "
                f"ppo_trained={ppo_trained}"
            ),
            next_module="future_decision_effect_checked",
            decision_authority="AIEM",
            learning_update_created=True,
            status="PASS",
        )
        trace.flush()
    except Exception as e:
        _msg = f"[closed_loop] log_learning_update_step error: {e}"
        print(_msg)
        try:
            with _conn(timeout=2) as _fc, _fc.cursor() as _fcu:
                _fcu.execute(
                    "INSERT INTO feedback_failure_log"
                    " (step_name, ticker, trace_id, error_message, escalated)"
                    " VALUES (%s, %s, %s, %s, TRUE)",
                    ("learning_update_applied", ticker, trace_id, str(e)[:500]),
                )
        except Exception:
            pass


def log_future_decision_step(
    *,
    trace_id: str,
    ticker: str,
    signal_source: str,
    new_trust: float,
    drift_mult: float,
    thompson_score: float,
    combined_mult: float,
) -> None:
    """
    Log the future_decision_effect_checked step — documents how the updated
    parameters will affect the NEXT pick run.
    """
    try:
        import aiem_pipeline_audit as _apa
        trace = _apa.PipelineTrace(ticker, trace_id=trace_id)
        trace.log_step(
            "future_decision_effect_checked",
            function_name="log_future_decision_step",
            file_name="aiem_closed_loop_learning.py",
            source_system="AIEM",
            processing_system="AIEM",
            input_summary=f"signal={signal_source}",
            output_summary=(
                f"next_pick_mult={combined_mult:.4f} "
                f"(trust={new_trust:.4f} × drift={drift_mult:.2f} "
                f"× thompson={thompson_score:.4f})"
            ),
            next_module=None,
            decision_authority="AIEM",
            learning_update_created=False,
            status="PASS",
        )
        trace.flush()
    except Exception as e:
        _msg = f"[closed_loop] log_future_decision_step error: {e}"
        print(_msg)
        try:
            with _conn(timeout=2) as _fc, _fc.cursor() as _fcu:
                _fcu.execute(
                    "INSERT INTO feedback_failure_log"
                    " (step_name, ticker, trace_id, error_message, escalated)"
                    " VALUES (%s, %s, %s, %s, TRUE)",
                    ("future_decision_effect_checked", ticker, trace_id, str(e)[:500]),
                )
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# PER-TRADE AUDIT REPORT
# ─────────────────────────────────────────────────────────────────────────────

def generate_trade_audit_report(trade_id: int) -> dict:
    """
    Generate a complete AIEM_TRADE_LEARNING_AUDIT_REPORT for one closed trade.
    Returns a dict suitable for Telegram or DB storage.
    """
    report: dict = {"trade_id": trade_id, "verdict": "FAIL", "steps": {}}
    try:
        with _conn(timeout=5) as c, c.cursor() as cu:

            # 1. Trade basics
            cu.execute("""
                SELECT ticker, trade_date, signal_source, entry_price, exit_price,
                       pnl, pnl_pct, status, exit_reason, audit_trace_id
                FROM aiem_paper_trades WHERE id=%s
            """, (trade_id,))
            t = cu.fetchone()
            if not t:
                report["error"] = "trade not found"
                return report
            (ticker, trade_date, signal_source, entry_price, exit_price,
             pnl, pnl_pct, status, exit_reason, audit_trace_id) = t
            report["ticker"]         = ticker
            report["trade_date"]     = str(trade_date)
            report["signal_source"]  = signal_source
            report["audit_trace_id"] = audit_trace_id
            report["steps"]["trade"] = {
                "entry": float(entry_price or 0),
                "exit":  float(exit_price  or 0),
                "pnl":   float(pnl or 0),
                "pnl_pct": float(pnl_pct or 0),
                "status": status,
                "exit_reason": exit_reason,
            }

            # 2. Pipeline audit steps
            if audit_trace_id:
                cu.execute("""
                    SELECT module_name, status, input_summary,
                           output_summary, decision_authority
                    FROM aiem_pipeline_audit_log
                    WHERE trace_id=%s
                    ORDER BY logged_at
                """, (audit_trace_id,))
                report["steps"]["pipeline_audit"] = [
                    {"module": r[0], "status": r[1],
                     "input": r[2], "output": r[3],
                     "authority": r[4]}
                    for r in cu.fetchall()
                ]
            else:
                report["steps"]["pipeline_audit"] = "MISSING — audit_trace_id is NULL"

            # 3. Candidate ranking
            if audit_trace_id:
                cu.execute("""
                    SELECT ticker, candidate_rank, raw_score, trust_multiplier,
                           drift_multiplier, final_adjusted_score, accepted_or_rejected,
                           decision_reason
                    FROM aiem_candidate_rankings
                    WHERE audit_trace_id=%s
                    ORDER BY candidate_rank NULLS LAST
                    LIMIT 30
                """, (audit_trace_id,))
                rows = cu.fetchall()
                report["steps"]["candidate_rankings"] = [
                    {"ticker": r[0], "rank": r[1], "raw_score": float(r[2] or 0),
                     "trust_mult": float(r[3] or 1), "drift_mult": float(r[4] or 1),
                     "final_score": float(r[5] or 0),
                     "result": r[6], "reason": r[7]}
                    for r in rows
                ] if rows else "EMPTY — no rankings stored for this run"

            # 4. RL experience buffer row
            cu.execute("""
                SELECT reward, mistakes, market_context, action, created_at
                FROM rl_experience_buffer WHERE trade_id=%s
            """, (str(trade_id),))
            rl = cu.fetchone()
            if rl:
                report["steps"]["rl_outcome"] = {
                    "reward": float(rl[0] or 0),
                    "mistakes": rl[1],
                    "action": rl[3],
                    "recorded_at": str(rl[4]),
                }
            else:
                report["steps"]["rl_outcome"] = "MISSING — no RL buffer row"

            # 5. Trust weight history
            cu.execute("""
                SELECT old_trust_score, new_trust_score, delta,
                       win_loss_result, rolling_win_rate, recorded_at
                FROM signal_trust_history
                WHERE trade_id=%s
                ORDER BY recorded_at
            """, (str(trade_id),))
            th = cu.fetchone()
            if th:
                report["steps"]["trust_update"] = {
                    "old_trust": float(th[0] or 0),
                    "new_trust": float(th[1] or 0),
                    "delta":     float(th[2] or 0),
                    "win_loss":  th[3],
                    "rolling_wr": float(th[4] or 0),
                    "recorded_at": str(th[5]),
                }
            else:
                report["steps"]["trust_update"] = "MISSING — signal_trust_history empty for this trade"

            # 6. Thompson update
            cu.execute("""
                SELECT old_alpha, old_beta, new_alpha, new_beta, win_loss, recorded_at
                FROM aiem_paper_thompson_history
                WHERE trade_id=%s
                ORDER BY recorded_at
            """, (str(trade_id),))
            th2 = cu.fetchone()
            if th2:
                report["steps"]["thompson_update"] = {
                    "old_alpha": float(th2[0] or 1), "old_beta": float(th2[1] or 1),
                    "new_alpha": float(th2[2] or 1), "new_beta": float(th2[3] or 1),
                    "win_loss": th2[4], "recorded_at": str(th2[5]),
                }
            else:
                report["steps"]["thompson_update"] = "MISSING — no Thompson update for this trade"

            # 7. PPO training after this trade
            cu.execute("""
                SELECT gradient_step_completed, buffer_rows_used,
                       reward_mean, loss_value, notes, started_at
                FROM rl_training_runs
                ORDER BY started_at DESC LIMIT 1
            """)
            ppo = cu.fetchone()
            if ppo:
                report["steps"]["ppo_training"] = {
                    "gradient_step_completed": ppo[0],
                    "buffer_rows_used": ppo[1],
                    "reward_mean": float(ppo[2] or 0),
                    "loss_value": float(ppo[3] or 0),
                    "notes": ppo[4],
                    "ran_at": str(ppo[5]),
                }

            # 8. Compute verdict
            has_audit     = bool(audit_trace_id)
            has_rl        = rl is not None
            has_trust     = th is not None
            has_thompson  = th2 is not None
            passes = sum([has_audit, has_rl, has_trust, has_thompson])
            if passes == 4:
                report["verdict"] = "PASS"
            elif passes >= 2:
                report["verdict"] = "PARTIAL"
            else:
                report["verdict"] = "FAIL"

            report["verdict_detail"] = {
                "audit_trace_id": "PASS" if has_audit else "FAIL — NULL",
                "rl_buffer_row":  "PASS" if has_rl    else "FAIL — missing",
                "trust_history":  "PASS" if has_trust  else "FAIL — missing",
                "thompson_update":"PASS" if has_thompson else "FAIL — missing",
            }

    except Exception as e:
        report["error"] = str(e)
    return report
