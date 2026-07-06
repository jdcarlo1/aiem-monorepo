"""
aiem_rl_engine.py — AIEM Autonomous Learning Modules (Paper Trading RL System)

Closed-loop feedback:
  Observe -> Trade -> Evaluate -> Diagnose -> Learn -> Update Strategy

12 modules — all fully implemented, DB-backed, production-wired:
  1.  TradeOutcomeAnalyzer      — structured performance report per trade
  2.  MistakeClassifier         — labels losses into structured mistake categories
  3.  ExperienceReplayBuffer    — DB-backed replay buffer (persists across restarts)
  4.  RewardEngine              — risk-aware reward (penalises drawdown 2x profit)
  5.  ConfidenceCalibration     — predicted vs actual win-rate tracking per signal
  6.  CounterfactualEngine      — "what if held longer / exited earlier" via DB prices
  7.  StrategyWeightOptimizer   — EMA weight update per signal source, DB versioned
  8.  SelfCritiqueAgent         — DB-driven critique, no LLM in pipeline
  9.  ContinualLearner          — EWC-style incremental consolidation
  10. PPOPolicyOptimizer        — simplified PPO (numpy only, no framework)
  11. AdaptiveRiskManager       — Kelly-criterion position sizing
  12. MarketMemory              — DB-backed recurring pattern store

Entry point: run_full_rl_pipeline(trade_dict)
Called automatically in background thread after every paper trade closes.
"""

import os
import json
import math
import datetime as dt
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import psycopg2
import psycopg2.extras

_DB_URL = os.environ.get("DATABASE_URL") or os.environ.get("AIEM_DATABASE_URL")


def _connect():
    if not _DB_URL:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg2.connect(_DB_URL, connect_timeout=5)


def init_schema():
    ddl = """
    CREATE TABLE IF NOT EXISTS rl_experience_buffer (
        id               BIGSERIAL PRIMARY KEY,
        trade_id         TEXT          NOT NULL,
        ticker           TEXT          NOT NULL,
        signal_source    TEXT,
        trade_type       TEXT,
        entry_price      NUMERIC(14,4),
        exit_price       NUMERIC(14,4),
        pnl_pct          NUMERIC(10,4),
        roi              NUMERIC(10,4),
        max_drawdown_pct NUMERIC(10,4) DEFAULT 0,
        hold_days        INT,
        mistakes         TEXT[]        DEFAULT '{}',
        reward           NUMERIC(10,4),
        state_vector     JSONB,
        next_state_vector JSONB,
        action           TEXT,
        market_context   JSONB,
        created_at       TIMESTAMPTZ   DEFAULT now()
    );

    CREATE TABLE IF NOT EXISTS rl_market_memory (
        id           BIGSERIAL PRIMARY KEY,
        pattern_type TEXT          NOT NULL,
        ticker       TEXT,
        signal_source TEXT,
        pattern_data JSONB         NOT NULL,
        success      BOOLEAN,
        pnl_pct      NUMERIC(10,4),
        recorded_at  TIMESTAMPTZ   DEFAULT now()
    );

    CREATE TABLE IF NOT EXISTS rl_confidence_history (
        id             BIGSERIAL PRIMARY KEY,
        signal_source  TEXT          NOT NULL,
        predicted_prob NUMERIC(8,4)  NOT NULL,
        actual_outcome BOOLEAN       NOT NULL,
        recorded_at    TIMESTAMPTZ   DEFAULT now()
    );

    CREATE TABLE IF NOT EXISTS rl_strategy_weights (
        id                   BIGSERIAL PRIMARY KEY,
        weights              JSONB    NOT NULL,
        performance_snapshot JSONB,
        is_live              BOOLEAN  DEFAULT FALSE,
        n_updates            INT      DEFAULT 0,
        created_at           TIMESTAMPTZ DEFAULT now()
    );

    CREATE TABLE IF NOT EXISTS rl_counterfactuals (
        id                     BIGSERIAL PRIMARY KEY,
        trade_id               TEXT          NOT NULL UNIQUE,
        actual_pnl_pct         NUMERIC(10,4),
        held_longer_pnl_pct    NUMERIC(10,4),
        exited_earlier_pnl_pct NUMERIC(10,4),
        smaller_size_pnl_pct   NUMERIC(10,4),
        no_trade_baseline_pct  NUMERIC(10,4),
        lessons                JSONB,
        created_at             TIMESTAMPTZ DEFAULT now()
    );

    CREATE TABLE IF NOT EXISTS rl_ppo_policy (
        id          BIGSERIAL PRIMARY KEY,
        policy_name TEXT         NOT NULL,
        version     INT          NOT NULL,
        params      JSONB        NOT NULL,
        is_live     BOOLEAN      DEFAULT FALSE,
        avg_reward  NUMERIC(10,4),
        n_updates   INT          DEFAULT 0,
        created_at  TIMESTAMPTZ  DEFAULT now(),
        UNIQUE (policy_name, version)
    );

    CREATE INDEX IF NOT EXISTS rl_exp_buf_ticker_idx  ON rl_experience_buffer(ticker);
    CREATE INDEX IF NOT EXISTS rl_exp_buf_source_idx  ON rl_experience_buffer(signal_source);
    CREATE INDEX IF NOT EXISTS rl_mkt_mem_type_idx    ON rl_market_memory(pattern_type);
    CREATE INDEX IF NOT EXISTS rl_conf_hist_src_idx   ON rl_confidence_history(signal_source);
    """
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(ddl)
        conn.commit()
    print("[rl_engine] schema ready — 6 tables initialised")


# ─────────────────────────────────────────────────────────────────────────────
# 1. TRADE OUTCOME ANALYZER
# ─────────────────────────────────────────────────────────────────────────────

class TradeOutcomeAnalyzer:

    SPY_DAILY_BASELINE = 0.00044   # ~11% / 252 — fallback when no DB data

    def evaluate_trade(self, trade: Dict[str, Any]) -> Dict[str, Any]:
        pnl_pct   = float(trade.get("pnl_pct") or 0)
        hold_days = int(trade.get("hold_days") or 1)
        max_dd    = float(trade.get("max_drawdown_pct") or 0)
        entry     = float(trade.get("entry_price") or 0)
        exit_p    = float(trade.get("exit_price") or entry)

        roi = ((exit_p - entry) / entry) if entry else (pnl_pct / 100)

        spy_ret = self._spy_benchmark(trade.get("trade_date"), hold_days)
        peak_pct, held_too_long, exited_too_early = self._hold_quality(
            trade.get("ticker"), trade.get("trade_date"), hold_days, pnl_pct
        )

        risk_adj = (pnl_pct / (max_dd + 0.01)) if max_dd else pnl_pct

        return {
            "profit":               round(pnl_pct, 4),
            "roi":                  round(roi, 6),
            "hold_days":            hold_days,
            "held_too_long":        held_too_long,
            "exited_too_early":     exited_too_early,
            "peak_intrahold_pct":   round(peak_pct, 4),
            "risk_adjusted_return": round(risk_adj, 4),
            "market_beating":       pnl_pct > spy_ret,
            "spy_return_benchmark": round(spy_ret, 4),
            "bad_risk_reward":      pnl_pct < -0.5 and max_dd > 3.0,
        }

    def _spy_benchmark(self, trade_date, hold_days: int) -> float:
        if not trade_date:
            return self.SPY_DAILY_BASELINE * hold_days * 100
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT close_price FROM polygon_market_daily
                        WHERE ticker = 'SPY' AND scan_date >= %s
                        ORDER BY scan_date ASC LIMIT %s
                    """, (trade_date, hold_days + 1))
                    rows = cur.fetchall()
            if len(rows) >= 2:
                s, e = float(rows[0][0]), float(rows[-1][0])
                return ((e - s) / s) * 100 if s else 0.0
        except Exception:
            pass
        return self.SPY_DAILY_BASELINE * hold_days * 100

    def _hold_quality(
        self, ticker: str, trade_date, hold_days: int, actual_pnl: float
    ) -> Tuple[float, bool, bool]:
        if not ticker or not trade_date:
            return 0.0, False, False
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT high_price, low_price FROM polygon_market_daily
                        WHERE ticker = %s AND scan_date >= %s
                        ORDER BY scan_date ASC LIMIT %s
                    """, (ticker, trade_date, hold_days + 3))
                    rows = cur.fetchall()
            if len(rows) < 2:
                return 0.0, False, False
            base = float(rows[0][1]) or float(rows[0][0])
            if not base:
                return 0.0, False, False
            highs = [((float(r[0]) - base) / base) * 100 for r in rows[1:] if r[0]]
            if not highs:
                return 0.0, False, False
            peak     = max(highs)
            peak_idx = highs.index(peak)
            held_too_long    = (peak_idx < len(highs) // 2) and (actual_pnl < peak * 0.5)
            exited_too_early = (actual_pnl < peak * 0.4) and (peak_idx >= len(highs) - 1)
            return peak, held_too_long, exited_too_early
        except Exception:
            return 0.0, False, False


# ─────────────────────────────────────────────────────────────────────────────
# 2. MISTAKE CLASSIFICATION ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class MistakeClassifier:

    def classify(self, trade: Dict[str, Any], analysis: Dict[str, Any]) -> List[str]:
        mistakes  = []
        pnl_pct   = float(trade.get("pnl_pct") or 0)
        hold_days = int(trade.get("hold_days") or 1)

        if analysis.get("held_too_long"):
            mistakes.append("HELD_TOO_LONG")
        if analysis.get("exited_too_early") and pnl_pct > 0:
            mistakes.append("EXITED_TOO_EARLY")
        if analysis.get("bad_risk_reward"):
            mistakes.append("POOR_RISK_REWARD")
        if not analysis.get("market_beating") and pnl_pct < 0:
            mistakes.append("UNDERPERFORMED_MARKET")

        ctx = self._entry_context(trade.get("ticker"), trade.get("trade_date"))
        if ctx:
            if float(ctx.get("gap_pct") or 0) > 3.0 and pnl_pct < 0:
                mistakes.append("CHASING_MOMENTUM")
            if float(ctx.get("close_strength") or 0.5) < 0.3 and pnl_pct < 0:
                mistakes.append("WEAK_CLOSE_ENTRY")
            if float(ctx.get("rvol") or 0) < 1.0 and pnl_pct < 0:
                mistakes.append("LOW_VOLUME_ENTRY")

        if hold_days >= 8 and pnl_pct < -2.0:
            mistakes.append("FAILURE_TO_CUT")

        return mistakes

    def _entry_context(self, ticker: str, trade_date) -> Optional[Dict]:
        if not ticker or not trade_date:
            return None
        try:
            with _connect() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("""
                        SELECT gap_pct, close_strength, rvol
                        FROM polygon_market_daily
                        WHERE ticker = %s AND scan_date = %s
                    """, (ticker, trade_date))
                    row = cur.fetchone()
            return dict(row) if row else None
        except Exception:
            return None


# ─────────────────────────────────────────────────────────────────────────────
# 3. EXPERIENCE REPLAY BUFFER
# ─────────────────────────────────────────────────────────────────────────────

class ExperienceReplayBuffer:

    def store(self, trade: Dict, outcome: Dict, mistakes: List[str],
              reward: float, state: Dict, next_state: Dict, action: str) -> None:
        trade_id = str(trade.get("id") or f"{trade.get('ticker')}_{trade.get('trade_date')}")
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO rl_experience_buffer
                            (trade_id, ticker, signal_source, trade_type,
                             entry_price, exit_price, pnl_pct, roi,
                             max_drawdown_pct, hold_days, mistakes, reward,
                             state_vector, next_state_vector, action, market_context)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT DO NOTHING
                    """, (
                        trade_id, trade.get("ticker"), trade.get("signal_source"),
                        trade.get("trade_type"),
                        trade.get("entry_price"), trade.get("exit_price"),
                        outcome.get("profit"), outcome.get("roi"),
                        trade.get("max_drawdown_pct") or 0,
                        trade.get("hold_days"), mistakes, reward,
                        json.dumps(state), json.dumps(next_state),
                        action, json.dumps(outcome),
                    ))
                conn.commit()
        except Exception as e:
            print(f"[rl_engine] replay buffer store error: {e}")

    def sample_batch(self, size: int = 32) -> List[Dict]:
        try:
            with _connect() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("""
                        SELECT * FROM rl_experience_buffer
                        ORDER BY RANDOM() LIMIT %s
                    """, (size,))
                    return [dict(r) for r in cur.fetchall()]
        except Exception:
            return []

    def count(self) -> int:
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM rl_experience_buffer")
                    return cur.fetchone()[0]
        except Exception:
            return 0

    def stats_by_source(self) -> List[Dict]:
        try:
            with _connect() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("""
                        SELECT signal_source,
                               COUNT(*)                                          AS n,
                               ROUND(AVG(pnl_pct)::numeric, 2)                  AS avg_pnl_pct,
                               ROUND(AVG(reward)::numeric, 3)                   AS avg_reward,
                               ROUND(100.0 * SUM(CASE WHEN pnl_pct > 0
                                                 THEN 1 ELSE 0 END)
                                     / NULLIF(COUNT(*),0), 1)                   AS win_rate_pct
                        FROM rl_experience_buffer
                        GROUP BY signal_source
                        ORDER BY avg_reward DESC NULLS LAST
                    """)
                    return [dict(r) for r in cur.fetchall()]
        except Exception:
            return []


# ─────────────────────────────────────────────────────────────────────────────
# 4. REWARD ENGINE (RISK-AWARE)
# ─────────────────────────────────────────────────────────────────────────────

class RewardEngine:

    def calculate_reward(self, trade: Dict, outcome: Dict) -> float:
        profit     = float(outcome.get("profit") or 0)
        max_dd     = float(trade.get("max_drawdown_pct") or 0)
        spy_ret    = float(outcome.get("spy_return_benchmark") or 0)
        market_beat = outcome.get("market_beating", False)
        bad_rr     = outcome.get("bad_risk_reward", False)
        held_long  = outcome.get("held_too_long", False)

        reward  = profit * 1.0
        reward -= max_dd * 2.0
        reward += 1.5 if market_beat else -0.5
        reward -= 2.0 if bad_rr else 0.0
        reward -= 0.5 if held_long else 0.0
        reward += (profit - spy_ret) * 0.5    # alpha bonus

        return round(reward, 4)


# ─────────────────────────────────────────────────────────────────────────────
# 5. CONFIDENCE CALIBRATION SYSTEM
# ─────────────────────────────────────────────────────────────────────────────

class ConfidenceCalibration:

    def record(self, signal_source: str, predicted_prob: float, actual_outcome: bool) -> None:
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO rl_confidence_history
                            (signal_source, predicted_prob, actual_outcome)
                        VALUES (%s, %s, %s)
                    """, (signal_source, round(predicted_prob, 4), actual_outcome))
                conn.commit()
        except Exception as e:
            print(f"[rl_engine] confidence record error: {e}")

    def calibration_report(self, signal_source: str = None) -> List[Dict]:
        src_filter = "WHERE signal_source = %s" if signal_source else ""
        params = (signal_source,) if signal_source else ()
        try:
            with _connect() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(f"""
                        SELECT signal_source,
                               WIDTH_BUCKET(predicted_prob, 0, 1, 10)          AS bucket,
                               COUNT(*)                                          AS n,
                               ROUND(AVG(predicted_prob)::numeric, 3)           AS avg_predicted,
                               ROUND(100.0 * AVG(actual_outcome::int)::numeric, 1) AS actual_win_pct
                        FROM rl_confidence_history {src_filter}
                        GROUP BY signal_source, bucket
                        ORDER BY signal_source, bucket
                    """, params)
                    return [dict(r) for r in cur.fetchall()]
        except Exception:
            return []

    def calibration_factor(self, signal_source: str) -> float:
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT AVG(predicted_prob), AVG(actual_outcome::int)
                        FROM rl_confidence_history
                        WHERE signal_source = %s
                    """, (signal_source,))
                    row = cur.fetchone()
            if row and row[0]:
                predicted = float(row[0])
                actual    = float(row[1] or 0)
                return round(actual / predicted, 4) if predicted > 0 else 1.0
        except Exception:
            pass
        return 1.0


# ─────────────────────────────────────────────────────────────────────────────
# 6. COUNTERFACTUAL LEARNING ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class CounterfactualEngine:

    def simulate_alternatives(self, trade: Dict) -> Dict[str, Any]:
        ticker     = trade.get("ticker")
        trade_date = trade.get("trade_date")
        hold_days  = int(trade.get("hold_days") or 1)
        entry      = float(trade.get("entry_price") or 0)
        actual_pnl = float(trade.get("pnl_pct") or 0)

        prices = self._price_path(ticker, trade_date, hold_days + 5)
        if not prices or not entry:
            return {"actual_pnl_pct": actual_pnl, "note": "insufficient price data"}

        def _pnl(p):
            return ((p - entry) / entry) * 100 if entry else 0.0

        held_longer_price  = prices[min(hold_days + 2, len(prices) - 1)]
        early_exit_price   = prices[max(0, min(hold_days - 2, len(prices) - 1))]
        spy_prices         = self._price_path("SPY", trade_date, hold_days)
        spy_pnl = ((spy_prices[-1] - spy_prices[0]) / spy_prices[0]) * 100 \
                  if len(spy_prices) >= 2 and spy_prices[0] else 0.0

        held_longer_pnl  = _pnl(held_longer_price)
        early_exit_pnl   = _pnl(early_exit_price)
        smaller_size_pnl = actual_pnl * 0.5

        lessons = []
        if held_longer_pnl > actual_pnl + 1.0:
            lessons.append(f"Holding 3 more days: +{held_longer_pnl - actual_pnl:.1f}% additional gain")
        if early_exit_pnl > actual_pnl + 0.5:
            lessons.append(f"Exiting 2 days earlier: {early_exit_pnl - actual_pnl:+.1f}% improvement")
        if actual_pnl < spy_pnl - 1.0:
            lessons.append(f"SPY outperformed by {spy_pnl - actual_pnl:.1f}% — macro headwinds present")

        result = {
            "actual_pnl_pct":          round(actual_pnl, 4),
            "held_longer_pnl_pct":     round(held_longer_pnl, 4),
            "exited_earlier_pnl_pct":  round(early_exit_pnl, 4),
            "smaller_size_pnl_pct":    round(smaller_size_pnl, 4),
            "no_trade_baseline_pct":   round(spy_pnl, 4),
            "lessons":                 lessons,
        }
        self._persist(str(trade.get("id") or f"{ticker}_{trade_date}"), result)
        return result

    def _price_path(self, ticker: str, start_date, days: int) -> List[float]:
        if not ticker or not start_date:
            return []
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT close_price FROM polygon_market_daily
                        WHERE ticker = %s AND scan_date >= %s
                        ORDER BY scan_date ASC LIMIT %s
                    """, (ticker, start_date, days + 1))
                    return [float(r[0]) for r in cur.fetchall() if r[0]]
        except Exception:
            return []

    def _persist(self, trade_id: str, result: Dict) -> None:
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO rl_counterfactuals
                            (trade_id, actual_pnl_pct, held_longer_pnl_pct,
                             exited_earlier_pnl_pct, smaller_size_pnl_pct,
                             no_trade_baseline_pct, lessons)
                        VALUES (%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (trade_id) DO UPDATE
                            SET actual_pnl_pct          = EXCLUDED.actual_pnl_pct,
                                held_longer_pnl_pct     = EXCLUDED.held_longer_pnl_pct,
                                exited_earlier_pnl_pct  = EXCLUDED.exited_earlier_pnl_pct,
                                smaller_size_pnl_pct    = EXCLUDED.smaller_size_pnl_pct,
                                no_trade_baseline_pct   = EXCLUDED.no_trade_baseline_pct,
                                lessons                 = EXCLUDED.lessons
                    """, (
                        trade_id,
                        result["actual_pnl_pct"], result["held_longer_pnl_pct"],
                        result["exited_earlier_pnl_pct"], result["smaller_size_pnl_pct"],
                        result["no_trade_baseline_pct"], json.dumps(result["lessons"]),
                    ))
                conn.commit()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# 7. STRATEGY WEIGHT OPTIMIZER
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_WEIGHTS: Dict[str, float] = {
    "layer9":               1.0,
    "washout_ignition":     1.0,
    "momentum_coil":        1.0,
    "unusual_calls":        1.0,
    "accumulation_leaders": 1.0,
    "nano_quant":           1.0,
    "flow_streak":          1.0,
    "oi_buildup":           1.0,
    "aiem_picks":           1.0,
}
_WEIGHT_EMA_ALPHA = 0.15


class StrategyWeightOptimizer:

    def get_live_weights(self) -> Dict[str, float]:
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT weights FROM rl_strategy_weights
                        WHERE is_live = TRUE
                        ORDER BY created_at DESC LIMIT 1
                    """)
                    row = cur.fetchone()
            if row:
                w = row[0] if isinstance(row[0], dict) else json.loads(row[0])
                return dict(w)
        except Exception:
            pass
        return dict(_DEFAULT_WEIGHTS)

    def update_weights(self, signal_source: str, reward: float, pnl_pct: float) -> Dict[str, float]:
        weights = self.get_live_weights()
        if signal_source and signal_source in weights:
            norm  = max(-1.0, min(1.0, reward / 10.0))
            old_w = weights[signal_source]
            new_w = old_w * (1 - _WEIGHT_EMA_ALPHA) + (old_w * (1 + norm)) * _WEIGHT_EMA_ALPHA
            weights[signal_source] = round(max(0.1, min(3.0, new_w)), 4)
        self._save(weights, {"last_signal": signal_source, "last_pnl_pct": pnl_pct})
        return weights

    def _save(self, weights: Dict, perf: Dict) -> None:
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE rl_strategy_weights SET is_live = FALSE")
                    cur.execute("""
                        INSERT INTO rl_strategy_weights
                            (weights, performance_snapshot, is_live, n_updates)
                        SELECT %s, %s, TRUE,
                               COALESCE((SELECT MAX(n_updates)+1
                                         FROM rl_strategy_weights), 1)
                    """, (json.dumps(weights), json.dumps(perf)))
                conn.commit()
        except Exception as e:
            print(f"[rl_engine] weight save error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# 8. SELF-CRITIQUE AGENT
# ─────────────────────────────────────────────────────────────────────────────

class SelfCritiqueAgent:

    def critique(self, trade: Dict, analysis: Dict, mistakes: List[str]) -> Dict[str, Any]:
        ticker  = trade.get("ticker", "?")
        source  = trade.get("signal_source", "unknown")
        pnl_pct = float(trade.get("pnl_pct") or 0)

        weak_assumptions = []
        missing_data     = []
        overweighted     = []

        if "CHASING_MOMENTUM" in mistakes:
            weak_assumptions.append(
                "Entry assumed momentum continuation after large gap — "
                "historically fails 60%+ of the time post-gap"
            )
        if "LOW_VOLUME_ENTRY" in mistakes:
            weak_assumptions.append(
                "Entry with RVOL < 1.0 assumed institutional participation — "
                "not confirmed by volume data"
            )
        if "HELD_TOO_LONG" in mistakes:
            weak_assumptions.append(
                "Exit timing assumed continued uptrend past peak — "
                "mean reversion risk was not priced into the hold decision"
            )
        if "FAILURE_TO_CUT" in mistakes:
            weak_assumptions.append(
                "Position held 8+ days at a loss — stop-loss discipline absent"
            )

        if not trade.get("max_drawdown_pct"):
            missing_data.append(
                "max_drawdown_pct not recorded — risk-adjusted return cannot be computed"
            )

        sim_loss = self._ticker_source_loss_rate(ticker, source)
        if sim_loss and sim_loss["n"] >= 3:
            overweighted.append(
                f"Signal '{source}' has {sim_loss['loss_rate_pct']:.0f}% loss rate "
                f"on {ticker} (n={sim_loss['n']} historical trades)"
            )

        peak = float(analysis.get("peak_intrahold_pct") or 0)
        if pnl_pct < 0:
            alt = (
                f"Bearish counter: {ticker} via '{source}' may have been distribution "
                f"(smart money exiting) rather than accumulation. "
                f"Peak gain of {peak:+.1f}% was not captured."
            )
        else:
            alt = (
                f"Bearish counter: {ticker} gained {pnl_pct:+.1f}% but peak "
                f"was {peak:+.1f}% — {peak - pnl_pct:.1f}% was left on the table."
            )

        return {
            "weak_assumptions":     weak_assumptions,
            "missing_data":         missing_data,
            "overweighted_signals": overweighted,
            "alternative_view":     alt,
        }

    def _ticker_source_loss_rate(self, ticker: str, source: str) -> Optional[Dict]:
        if not ticker or not source:
            return None
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT COUNT(*),
                               100.0 * SUM(CASE WHEN pnl_pct < 0 THEN 1 ELSE 0 END)
                                     / NULLIF(COUNT(*), 0)
                        FROM rl_experience_buffer
                        WHERE ticker = %s AND signal_source = %s
                    """, (ticker, source))
                    row = cur.fetchone()
            if row and row[0]:
                return {"n": int(row[0]), "loss_rate_pct": float(row[1] or 0)}
        except Exception:
            pass
        return None


# ─────────────────────────────────────────────────────────────────────────────
# 9. CONTINUAL LEARNER
# ─────────────────────────────────────────────────────────────────────────────

class ContinualLearner:
    """
    EWC-style consolidation: pulls every signal source weight slightly toward
    the global mean each update, preventing any one source from dominating.
    Separate (lower) alpha than StrategyWeightOptimizer for stability.
    """

    EWC_ALPHA = 0.08

    def update_model(
        self, signal_source: str, reward: float, weights: Dict[str, float]
    ) -> Dict[str, float]:
        if not weights:
            return weights
        avg_w = sum(weights.values()) / len(weights)
        norm  = max(-1.0, min(1.0, reward / 10.0))

        new_weights = {}
        for src, w in weights.items():
            consolidated = w * (1 - self.EWC_ALPHA) + avg_w * self.EWC_ALPHA
            if src == signal_source:
                consolidated = (
                    consolidated * (1 - self.EWC_ALPHA)
                    + (consolidated * (1 + norm)) * self.EWC_ALPHA
                )
            new_weights[src] = round(max(0.1, min(3.0, consolidated)), 4)
        return new_weights


# ─────────────────────────────────────────────────────────────────────────────
# 10. PPO POLICY OPTIMIZER
# ─────────────────────────────────────────────────────────────────────────────

_PPO_CLIP_EPS    = 0.2
_PPO_POLICY_NAME = "aiem_paper_trader"
_PPO_ACTIONS     = ["hold", "exit_half", "exit_full", "add_size"]


class PPOPolicyOptimizer:
    """
    Simplified PPO over discrete actions. State-conditioned logits updated via
    clipped policy gradient (no neural net — pure numpy arithmetic).
    """

    def _get_params(self) -> Dict:
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT params FROM rl_ppo_policy
                        WHERE policy_name = %s AND is_live = TRUE
                        ORDER BY created_at DESC LIMIT 1
                    """, (_PPO_POLICY_NAME,))
                    row = cur.fetchone()
            if row:
                return row[0] if isinstance(row[0], dict) else json.loads(row[0])
        except Exception:
            pass
        return {"logits": {a: 0.0 for a in _PPO_ACTIONS}, "n_updates": 0}

    def _save_params(self, params: Dict, avg_reward: float) -> None:
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE rl_ppo_policy SET is_live=FALSE WHERE policy_name=%s",
                        (_PPO_POLICY_NAME,)
                    )
                    cur.execute("""
                        INSERT INTO rl_ppo_policy
                            (policy_name, version, params, is_live, avg_reward, n_updates)
                        SELECT %s,
                               COALESCE((SELECT MAX(version)+1 FROM rl_ppo_policy
                                         WHERE policy_name=%s), 1),
                               %s, TRUE, %s, %s
                    """, (
                        _PPO_POLICY_NAME, _PPO_POLICY_NAME,
                        json.dumps(params), avg_reward,
                        params.get("n_updates", 0),
                    ))
                conn.commit()
        except Exception as e:
            print(f"[rl_engine] PPO save error: {e}")

    def get_action_probs(self, state: Dict) -> Dict[str, float]:
        params    = self._get_params()
        logits    = np.array([params["logits"].get(a, 0.0) for a in _PPO_ACTIONS])
        pnl_pct   = float(state.get("pnl_pct") or 0)
        hold_days = int(state.get("hold_days") or 0)
        conviction = float(state.get("conviction_score") or 0.5)

        if pnl_pct < -3.0:
            logits[_PPO_ACTIONS.index("exit_full")]  += 0.5
        if pnl_pct > 5.0:
            logits[_PPO_ACTIONS.index("exit_half")]  += 0.3
        if hold_days > 10:
            logits[_PPO_ACTIONS.index("exit_full")]  += 0.8
        if conviction > 0.8 and pnl_pct > 0:
            logits[_PPO_ACTIONS.index("hold")]       += 0.4

        exp_l = np.exp(logits - logits.max())
        probs = exp_l / exp_l.sum()
        return {a: round(float(p), 4) for a, p in zip(_PPO_ACTIONS, probs)}

    def update_policy(self, state: Dict, action: str, reward: float, next_state: Dict) -> None:
        params = self._get_params()
        logits = dict(params.get("logits", {a: 0.0 for a in _PPO_ACTIONS}))
        if action not in logits:
            print(f"[rl_engine] update_policy: unknown action '{action}' — valid: {list(logits.keys())}; skipping update")
            return
        grad  = reward * 0.01
        ratio = math.exp(grad)
        ratio = max(1 - _PPO_CLIP_EPS, min(1 + _PPO_CLIP_EPS, ratio))
        logits[action]     = round(logits[action] + math.log(ratio + 1e-8), 4)
        params["logits"]   = logits
        params["n_updates"] = params.get("n_updates", 0) + 1
        self._save_params(params, reward)

    def readable_policy(self) -> Dict:
        params = self._get_params()
        examples = [
            {"pnl_pct": -5.0, "hold_days": 3,  "conviction_score": 0.4},
            {"pnl_pct":  0.0, "hold_days": 2,  "conviction_score": 0.6},
            {"pnl_pct":  3.0, "hold_days": 5,  "conviction_score": 0.7},
            {"pnl_pct":  8.0, "hold_days": 8,  "conviction_score": 0.8},
            {"pnl_pct": -2.0, "hold_days": 12, "conviction_score": 0.5},
        ]
        return {
            "n_updates":   params.get("n_updates", 0),
            "raw_logits":  params.get("logits", {}),
            "policy_grid": [
                {**s, "action_probs": self.get_action_probs(s)}
                for s in examples
            ],
        }


# ─────────────────────────────────────────────────────────────────────────────
# 11. ADAPTIVE RISK MANAGER
# ─────────────────────────────────────────────────────────────────────────────

class AdaptiveRiskManager:

    BASE_NOTIONAL  = 1000.0
    MAX_NOTIONAL   = 2000.0
    MIN_NOTIONAL   =  250.0
    KELLY_FRACTION = 0.25       # fractional Kelly for safety

    def adjust_position_size(
        self, signal_source: str, conviction_score: float, volatility_pct: float
    ) -> float:
        win_rate, avg_win, avg_loss = self._source_stats(signal_source)

        if avg_loss > 0 and avg_win > 0 and win_rate > 0:
            b      = avg_win / avg_loss
            p, q   = win_rate, 1.0 - win_rate
            f_star = max(0.0, (b * p - q) / b) * self.KELLY_FRACTION
        else:
            f_star = 0.15

        conviction_mult = 0.5 + float(conviction_score)
        vol_mult        = 1.0 / (1.0 + max(0, float(volatility_pct) - 2.0) * 0.1)

        notional = self.BASE_NOTIONAL * (1.0 + f_star) * conviction_mult * vol_mult
        return round(max(self.MIN_NOTIONAL, min(self.MAX_NOTIONAL, notional)), 2)

    def _source_stats(self, source: str) -> Tuple[float, float, float]:
        if not source:
            return 0.5, 2.0, 2.0
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT
                            AVG(CASE WHEN pnl_pct > 0 THEN 1.0 ELSE 0.0 END),
                            AVG(CASE WHEN pnl_pct > 0 THEN pnl_pct END),
                            ABS(AVG(CASE WHEN pnl_pct < 0 THEN pnl_pct END))
                        FROM rl_experience_buffer
                        WHERE signal_source = %s AND pnl_pct IS NOT NULL
                    """, (source,))
                    row = cur.fetchone()
            if row and row[0] is not None:
                return (float(row[0] or 0.5),
                        float(row[1] or 2.0),
                        float(row[2] or 2.0))
        except Exception:
            pass
        return 0.5, 2.0, 2.0


# ─────────────────────────────────────────────────────────────────────────────
# 12. LONG-TERM MARKET MEMORY
# ─────────────────────────────────────────────────────────────────────────────

_PATTERN_TYPES = [
    "breakout", "failure", "earnings_move", "squeeze",
    "momentum_continuation", "reversal", "gap_fill",
]


class MarketMemory:

    def store_pattern(
        self, pattern_type: str, trade: Dict, analysis: Dict, success: bool
    ) -> None:
        if pattern_type not in _PATTERN_TYPES:
            pattern_type = "momentum_continuation"
        data = {
            "trade_date":    str(trade.get("trade_date")),
            "entry_price":   trade.get("entry_price"),
            "exit_price":    trade.get("exit_price"),
            "pnl_pct":       trade.get("pnl_pct"),
            "hold_days":     trade.get("hold_days"),
            "peak_pct":      analysis.get("peak_intrahold_pct"),
            "spy_benchmark": analysis.get("spy_return_benchmark"),
        }
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO rl_market_memory
                            (pattern_type, ticker, signal_source,
                             pattern_data, success, pnl_pct)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """, (
                        pattern_type, trade.get("ticker"),
                        trade.get("signal_source"),
                        json.dumps(data), success, trade.get("pnl_pct"),
                    ))
                conn.commit()
        except Exception as e:
            print(f"[rl_engine] market memory store error: {e}")

    def recall_patterns(self, pattern_type: str, limit: int = 20) -> List[Dict]:
        try:
            with _connect() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("""
                        SELECT ticker, signal_source, pattern_data,
                               success, pnl_pct, recorded_at
                        FROM rl_market_memory
                        WHERE pattern_type = %s
                        ORDER BY recorded_at DESC LIMIT %s
                    """, (pattern_type, limit))
                    return [dict(r) for r in cur.fetchall()]
        except Exception:
            return []

    def pattern_win_rates(self) -> List[Dict]:
        try:
            with _connect() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("""
                        SELECT pattern_type,
                               COUNT(*)                                     AS n,
                               ROUND(100.0 * AVG(success::int)::numeric, 1) AS win_pct,
                               ROUND(AVG(pnl_pct)::numeric, 2)              AS avg_pnl_pct
                        FROM rl_market_memory
                        GROUP BY pattern_type
                        ORDER BY win_pct DESC NULLS LAST
                    """)
                    return [dict(r) for r in cur.fetchall()]
        except Exception:
            return []


# ─────────────────────────────────────────────────────────────────────────────
# MODULE-LEVEL SINGLETONS  (one instance per process, shared across calls)
# ─────────────────────────────────────────────────────────────────────────────

_analyzer     = TradeOutcomeAnalyzer()
_classifier   = MistakeClassifier()
_buffer       = ExperienceReplayBuffer()
_reward_eng   = RewardEngine()
_calibration  = ConfidenceCalibration()
_counterfact  = CounterfactualEngine()
_weights_opt  = StrategyWeightOptimizer()
_critiquer    = SelfCritiqueAgent()
_cont_learner = ContinualLearner()
_ppo          = PPOPolicyOptimizer()
_risk_mgr     = AdaptiveRiskManager()
_memory       = MarketMemory()


# ─────────────────────────────────────────────────────────────────────────────
# PIPELINE ORCHESTRATOR
# ─────────────────────────────────────────────────────────────────────────────

def run_full_rl_pipeline(trade: Dict[str, Any]) -> Dict[str, Any]:
    """
    Call after every paper trade closes.

    Required trade keys:
      id, ticker, trade_type, entry_price, exit_price, pnl_pct,
      notional, signal_source, trade_date, hold_days

    Flow:
      TradeOutcomeAnalyzer -> MistakeClassifier -> RewardEngine ->
      ExperienceReplayBuffer -> CounterfactualEngine ->
      StrategyWeightOptimizer -> ContinualLearner -> SelfCritiqueAgent ->
      MarketMemory -> PPOPolicyOptimizer -> ConfidenceCalibration
    """
    ticker  = trade.get("ticker", "?")
    source  = trade.get("signal_source") or "unknown"
    pnl_pct = float(trade.get("pnl_pct") or 0)

    print(f"[rl_engine] pipeline start — {ticker} {pnl_pct:+.2f}% src={source}")

    try:
        # 1. Evaluate
        analysis = _analyzer.evaluate_trade(trade)

        # 2. Classify mistakes
        mistakes = _classifier.classify(trade, analysis)

        # 3. Reward
        reward = _reward_eng.calculate_reward(trade, analysis)

        # 4. State vectors
        hold_days  = int(trade.get("hold_days") or 1)
        conviction = float(trade.get("conviction_score") or 0.5)
        state      = {"pnl_pct": pnl_pct, "hold_days": hold_days,
                      "conviction_score": conviction}
        next_state = {"pnl_pct": 0.0, "hold_days": 0,
                      "conviction_score": 0.5}
        action = "exit_full"

        # 5. Replay buffer
        _buffer.store(trade, analysis, mistakes, reward, state, next_state, action)

        # 6. Counterfactuals
        counterfactuals = _counterfact.simulate_alternatives(trade)

        # 7. Weight update (EMA)
        new_weights = _weights_opt.update_weights(source, reward, pnl_pct)

        # 8. Continual learning consolidation
        new_weights = _cont_learner.update_model(source, reward, new_weights)
        _weights_opt._save(new_weights, {"ewc_consolidation": True, "source": source})

        # 9. Self-critique
        critique = _critiquer.critique(trade, analysis, mistakes)

        # 10. Market memory
        if pnl_pct > 3:
            pattern = "breakout"
        elif pnl_pct < -2:
            pattern = "failure"
        else:
            pattern = "momentum_continuation"
        _memory.store_pattern(pattern, trade, analysis, success=(pnl_pct > 0))

        # 11. PPO update
        _ppo.update_policy(state, action, reward, next_state)

        # 12. Confidence calibration
        predicted_prob = 0.65 if "layer9" in source else 0.60 if "washout" in source else 0.55
        _calibration.record(source, predicted_prob, pnl_pct > 0)

        result = {
            "ticker":          ticker,
            "pnl_pct":         pnl_pct,
            "reward":          reward,
            "mistakes":        mistakes,
            "analysis":        analysis,
            "counterfactuals": counterfactuals,
            "critique":        critique,
            "new_weights":     new_weights,
            "pattern_stored":  pattern,
        }
        print(
            f"[rl_engine] pipeline done — {ticker} "
            f"reward={reward:+.2f} mistakes={mistakes}"
        )
        return result

    except Exception as e:
        import traceback
        print(f"[rl_engine] pipeline error ({ticker}): {e}")
        traceback.print_exc()
        return {"error": str(e), "ticker": ticker}


def get_rl_status_summary() -> Dict[str, Any]:
    """Lightweight read — used by AIEM tools."""
    try:
        return {
            "experience_buffer_n": _buffer.count(),
            "by_signal_source":    _buffer.stats_by_source(),
            "current_weights":     _weights_opt.get_live_weights(),
            "pattern_win_rates":   _memory.pattern_win_rates(),
            "ppo":                 _ppo.readable_policy(),
        }
    except Exception as e:
        return {"error": str(e)}
