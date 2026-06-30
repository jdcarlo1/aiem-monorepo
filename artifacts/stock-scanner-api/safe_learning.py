"""
safe_learning.py
================
AEIM Safe Learning Architecture Module (Level 3–5 Safe Upgrade)

Purpose:
  - Allow learning from past trades without overfitting
  - Prevent live logic drift via temporal drift detection
  - Separate research/weight-update from execution
  - Gate weight updates behind safety checks

Components:
  1. TradeMemory         — in-memory deque + optional DB seed from existing tables
  2. PerformanceAnalyzer — per-strategy batch stats (avg_pnl, volatility, win_rate)
  3. DriftDetector       — temporal drift: old_stats → new_stats shift detection
  4. AllocationEngine    — exponential-smoothed strategy weights (0.8/0.2 formula)
  5. SafetyGate          — blocks updates: min ≥30 samples, avg_pnl ≥ -5%
  6. SafeLearningSystem  — full orchestrator combining all five

How it coexists with existing modules:
  - drift_alarm.py:          live-vs-BACKTEST drift (Fisher's exact); orthogonal
  - meta_learning_signal_trust.py: per-SIGNAL + context-bucket EWM; orthogonal
  - pre_decision_risk_gate.py:     per-TRADE gate; this module gates UPDATES only
  - portfolio_allocator.py:        per-PICK capital allocation; orthogonal

DB seeding uses: eod_outcomes, signal_fire_log, ai_short_calls_log
"""

import os
import statistics
import datetime as dt
from collections import deque, defaultdict
from typing import Dict, Any, List, Optional

import numpy as np

try:
    import psycopg2
    import psycopg2.extras
    _PG_AVAILABLE = True
except ImportError:
    _PG_AVAILABLE = False


def _db_connect():
    url = os.environ.get("AIEM_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set — cannot seed TradeMemory from DB.")
    return psycopg2.connect(url, connect_timeout=5,
                            options="-c statement_timeout=5000")


# ─────────────────────────────────────────────────────────────────────────────
# 1. TRADE MEMORY STORE
# ─────────────────────────────────────────────────────────────────────────────

class TradeMemory:
    """
    In-memory circular buffer of resolved trades.

    Each trade record:
        {
            "strategy": str,      # e.g. "momentum", "mean_reversion", "stat_arb"
            "pnl":      float,    # net P&L as a fraction (e.g. 0.023 = +2.3%)
            "regime":   str,      # e.g. "trend_up", "chop", "high_volatility"
            "features": dict      # optional feature snapshot at entry
        }

    Can be seeded from existing DB tables (eod_outcomes, signal_fire_log,
    ai_short_calls_log) so it works with historical data from day 1, rather
    than requiring 30+ live trades to accumulate first.
    """

    def __init__(self, max_size: int = 5000):
        self.trades: deque = deque(maxlen=max_size)

    def log_trade(self, trade: Dict[str, Any]) -> None:
        """Log a single resolved trade."""
        required = {"strategy", "pnl"}
        for k in required:
            if k not in trade:
                raise ValueError(f"TradeMemory.log_trade: missing required key '{k}'")
        record = {
            "strategy": str(trade["strategy"]),
            "pnl":      float(trade["pnl"]),
            "regime":   str(trade.get("regime", "unknown")),
            "features": trade.get("features", {}),
        }
        self.trades.append(record)

    def get_all(self) -> List[Dict[str, Any]]:
        return list(self.trades)

    def size(self) -> int:
        return len(self.trades)

    def seed_from_db(self, days_back: int = 90) -> int:
        """
        Seed TradeMemory from existing DB tables.  Called once at startup so
        PerformanceAnalyzer has enough history without waiting for 30 live trades.

        Mapping:
          eod_outcomes         → strategy="eod_swing"    pnl=open_to_close_pct/100
          signal_fire_log      → strategy=kind           pnl=outcome_pct/100 (if present)
          ai_short_calls_log   → strategy="ai_short"     pnl=(entry-exit)/entry

        Returns: number of trades loaded.
        """
        if not _PG_AVAILABLE:
            return 0
        loaded = 0
        since = dt.date.today() - dt.timedelta(days=days_back)
        try:
            with _db_connect() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    # ── eod_outcomes ─────────────────────────────────────────
                    cur.execute("""
                        SELECT trade_date, ticker, open_to_close_pct, regime
                        FROM eod_outcomes
                        WHERE trade_date >= %s
                          AND open_to_close_pct IS NOT NULL
                        ORDER BY trade_date DESC
                        LIMIT 1000
                    """, (since,))
                    for row in cur.fetchall():
                        self.trades.append({
                            "strategy": "eod_swing",
                            "pnl":      float(row["open_to_close_pct"]) / 100.0,
                            "regime":   str(row.get("regime") or "unknown"),
                            "features": {"ticker": row["ticker"],
                                         "date": str(row["trade_date"])},
                        })
                        loaded += 1

                    # ── signal_fire_log ──────────────────────────────────────
                    cur.execute("""
                        SELECT kind, outcome_pct, regime, fire_date
                        FROM signal_fire_log
                        WHERE fire_date >= %s
                          AND outcome_pct IS NOT NULL
                        ORDER BY fire_date DESC
                        LIMIT 1000
                    """, (since,))
                    for row in cur.fetchall():
                        self.trades.append({
                            "strategy": str(row.get("kind") or "signal"),
                            "pnl":      float(row["outcome_pct"]) / 100.0,
                            "regime":   str(row.get("regime") or "unknown"),
                            "features": {"date": str(row["fire_date"])},
                        })
                        loaded += 1

                    # ── ai_short_calls_log ───────────────────────────────────
                    cur.execute("""
                        SELECT entry_price, exit_price, regime, created_at
                        FROM ai_short_calls_log
                        WHERE created_at >= %s
                          AND exit_price IS NOT NULL
                          AND entry_price > 0
                        ORDER BY created_at DESC
                        LIMIT 500
                    """, (since,))
                    for row in cur.fetchall():
                        ep = float(row["entry_price"])
                        xp = float(row["exit_price"])
                        pnl_pct = (xp - ep) / ep if ep > 0 else 0.0
                        self.trades.append({
                            "strategy": "ai_short",
                            "pnl":      round(pnl_pct, 6),
                            "regime":   str(row.get("regime") or "unknown"),
                            "features": {},
                        })
                        loaded += 1

        except Exception as exc:
            print(f"[safe_learning] TradeMemory.seed_from_db error: {exc}")
        return loaded


# ─────────────────────────────────────────────────────────────────────────────
# 2. PERFORMANCE ANALYZER
# ─────────────────────────────────────────────────────────────────────────────

class PerformanceAnalyzer:
    """
    Batch per-strategy stats from TradeMemory.
    Requires ≥30 samples before including a strategy in the summary —
    below that, the stats are noise.
    """

    def __init__(self, memory: TradeMemory, min_samples: int = 30):
        self.memory = memory
        self.min_samples = min_samples

    def strategy_stats(self) -> Dict[str, Dict[str, Any]]:
        """
        Returns a dict of { strategy_name → stats_dict } for every strategy
        with enough samples.  Excludes strategies with < min_samples trades.
        """
        buckets: Dict[str, List[float]] = defaultdict(list)
        for t in self.memory.get_all():
            buckets[t["strategy"]].append(t["pnl"])

        summary: Dict[str, Dict[str, Any]] = {}
        for strat, pnls in buckets.items():
            if len(pnls) < self.min_samples:
                continue
            wins = [p for p in pnls if p > 0]
            summary[strat] = {
                "avg_pnl":     round(statistics.mean(pnls), 6),
                "volatility":  round(statistics.pstdev(pnls), 6),
                "win_rate":    round(len(wins) / len(pnls), 4),
                "sample_size": len(pnls),
                "best_trade":  round(max(pnls), 4),
                "worst_trade": round(min(pnls), 4),
            }
        return summary

    def all_strategy_counts(self) -> Dict[str, int]:
        """Returns sample counts for ALL strategies (incl. those below min_samples)."""
        counts: Dict[str, int] = defaultdict(int)
        for t in self.memory.get_all():
            counts[t["strategy"]] += 1
        return dict(counts)


# ─────────────────────────────────────────────────────────────────────────────
# 3. DRIFT DETECTOR
# ─────────────────────────────────────────────────────────────────────────────

class DriftDetector:
    """
    Temporal drift: compares a strategy's current stats against its previous
    stats snapshot.  Flags drift when (win_rate_shift + |avg_pnl_shift|) / 2
    exceeds the threshold.

    This is DIFFERENT from drift_alarm.py which does live-vs-backtest
    statistical testing (Fisher's exact).  This module detects gradual
    temporal decay between consecutive update cycles.
    """

    def detect_drift(
        self,
        old_stats: Dict[str, Dict[str, Any]],
        new_stats: Dict[str, Dict[str, Any]],
        threshold: float = 0.35,
    ) -> Dict[str, bool]:
        """
        Returns { strategy: bool } where True = drifted beyond threshold.
        Only checks strategies present in both old and new stats.
        """
        drift_flags: Dict[str, bool] = {}

        for strat in new_stats:
            if strat not in old_stats:
                continue

            old = old_stats[strat]
            new = new_stats[strat]

            win_rate_shift = abs(float(old["win_rate"]) - float(new["win_rate"]))
            pnl_shift      = abs(float(old["avg_pnl"])  - float(new["avg_pnl"]))

            drift_score = (win_rate_shift + pnl_shift) / 2.0
            drift_flags[strat] = drift_score > threshold

        return drift_flags

    def drift_report(
        self,
        old_stats: Dict[str, Dict[str, Any]],
        new_stats: Dict[str, Dict[str, Any]],
        threshold: float = 0.35,
    ) -> Dict[str, Any]:
        """Extended report with numeric scores per strategy."""
        flags = self.detect_drift(old_stats, new_stats, threshold)
        details = {}
        for strat in flags:
            old = old_stats[strat]
            new = new_stats[strat]
            wr_shift  = float(old["win_rate"]) - float(new["win_rate"])
            pnl_shift = float(old["avg_pnl"])  - float(new["avg_pnl"])
            details[strat] = {
                "drifted":        flags[strat],
                "win_rate_shift": round(wr_shift, 4),
                "pnl_shift":      round(pnl_shift, 6),
                "drift_score":    round((abs(wr_shift) + abs(pnl_shift)) / 2.0, 4),
                "old_win_rate":   old["win_rate"],
                "new_win_rate":   new["win_rate"],
            }
        return {
            "threshold":     threshold,
            "strategies":    details,
            "any_drift":     any(flags.values()),
            "drifted_count": sum(flags.values()),
            "checked_at":    dt.datetime.utcnow().isoformat(),
        }


# ─────────────────────────────────────────────────────────────────────────────
# 4. ALLOCATION ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class AllocationEngine:
    """
    Strategy-level capital allocation weights using exponential smoothing.

    Score per strategy = avg_pnl × win_rate, clipped to [-1, 1].
    Weight update: new_weight = 0.8 × old_weight + 0.2 × target_weight

    This prevents overreaction to a single good/bad week (the 0.8 inertia
    factor means a strategy needs several consecutive cycles to shift weight
    significantly).

    This is DIFFERENT from meta_learning_signal_trust.py which maintains
    per-SIGNAL per-CONTEXT-BUCKET EWM weights stored in the DB.
    This module operates at the strategy-level (momentum, mean_reversion,
    stat_arb, eod_swing, ai_short) in memory.
    """

    def __init__(self):
        self.weights: Dict[str, float] = {}

    def update_weights(
        self,
        performance_stats: Dict[str, Dict[str, Any]],
    ) -> Dict[str, float]:
        """
        Recompute weights from latest performance stats.
        Returns the updated weight dict.
        """
        scores: Dict[str, float] = {}
        total_score = 0.0

        for strat, stats in performance_stats.items():
            score = float(stats["avg_pnl"]) * float(stats["win_rate"])
            score = float(np.clip(score, -1.0, 1.0))
            scores[strat] = score
            total_score += abs(score)

        if total_score == 0:
            return self.weights

        for strat, score in scores.items():
            target_weight = abs(score) / total_score
            old_weight    = self.weights.get(strat, 0.1)
            # Exponential smoothing: slow-moving, prevents instability
            self.weights[strat] = round(0.8 * old_weight + 0.2 * target_weight, 6)

        return self.weights

    def get_weight(self, strategy: str, default: float = 0.1) -> float:
        return self.weights.get(strategy, default)

    def normalize(self) -> Dict[str, float]:
        """Return weights normalized so they sum to 1.0."""
        total = sum(self.weights.values())
        if total == 0:
            return self.weights
        return {k: round(v / total, 6) for k, v in self.weights.items()}


# ─────────────────────────────────────────────────────────────────────────────
# 5. SAFETY GATE
# ─────────────────────────────────────────────────────────────────────────────

class SafetyGate:
    """
    Prevents weight updates when data is insufficient or performance is
    catastrophically bad.

    Two hard blockers:
      1. sample_size < min_sample_size (default 30)  — too little data
      2. avg_pnl    < max_drawdown_limit (default -5%) — strategy is underwater

    Both are checked across ALL strategies in the stats dict.  If ANY strategy
    fails, the entire update is blocked (conservative — better to freeze weights
    than to update with bad data).
    """

    def __init__(
        self,
        max_drawdown_limit: float = -0.05,
        min_sample_size:    int   = 30,
    ):
        self.max_drawdown_limit = max_drawdown_limit
        self.min_sample_size    = min_sample_size

    def allow_update(self, stats: Dict[str, Dict[str, Any]]) -> bool:
        for strat, s in stats.items():
            if s["sample_size"] < self.min_sample_size:
                return False
            if s["avg_pnl"] < self.max_drawdown_limit:
                return False
        return True

    def block_reason(self, stats: Dict[str, Dict[str, Any]]) -> Optional[str]:
        """Returns a human-readable reason if update is blocked, else None."""
        for strat, s in stats.items():
            if s["sample_size"] < self.min_sample_size:
                return (f"[{strat}] only {s['sample_size']} samples "
                        f"(min {self.min_sample_size})")
            if s["avg_pnl"] < self.max_drawdown_limit:
                return (f"[{strat}] avg_pnl {s['avg_pnl']:.2%} below "
                        f"drawdown limit {self.max_drawdown_limit:.2%}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# 6. SAFE LEARNING ORCHESTRATOR
# ─────────────────────────────────────────────────────────────────────────────

class SafeLearningSystem:
    """
    Ties all five components together.

    Typical flow:
      1. log_trade() — called whenever a trade resolves
      2. update()    — called periodically (e.g. EOD or Sunday AIEM research run)
                       returns new strategy weights or the current frozen weights
      3. get_weights()      — read weights for downstream use
      4. get_strategy_stats() — inspect stats without triggering update
    """

    def __init__(self, min_samples: int = 30, max_drawdown_limit: float = -0.05):
        self.memory   = TradeMemory()
        self.analyzer = PerformanceAnalyzer(self.memory, min_samples=min_samples)
        self.drift    = DriftDetector()
        self.alloc    = AllocationEngine()
        self.safety   = SafetyGate(
            max_drawdown_limit=max_drawdown_limit,
            min_sample_size=min_samples,
        )
        self.previous_stats: Dict[str, Dict[str, Any]] = {}
        self._last_update:   Optional[str] = None
        self._last_blocked:  Optional[str] = None

    # ── public interface ──────────────────────────────────────────────────────

    def log_trade(self, trade: Dict[str, Any]) -> None:
        """Log a resolved trade to memory."""
        self.memory.log_trade(trade)

    def seed_from_db(self, days_back: int = 90) -> int:
        """Seed memory from DB historical tables. Returns rows loaded."""
        n = self.memory.seed_from_db(days_back=days_back)
        print(f"[SafeLearningSystem] seeded {n} trades from DB (last {days_back} days)")
        return n

    def update(self) -> Dict[str, float]:
        """
        Run the full safe-learning update cycle:
          stats → safety check → drift check → weight update

        Returns current weights (updated or frozen).
        """
        current_stats = self.analyzer.strategy_stats()

        if not current_stats:
            print("[SAFE MODE] No strategies with sufficient samples yet.")
            return self.alloc.weights

        # ── 1. Safety gate ────────────────────────────────────────────────────
        if not self.safety.allow_update(current_stats):
            reason = self.safety.block_reason(current_stats)
            msg = f"[SAFE MODE] Update blocked: {reason}"
            print(msg)
            self._last_blocked = msg
            return self.alloc.weights

        self._last_blocked = None

        # ── 2. Drift check ────────────────────────────────────────────────────
        if self.previous_stats:
            drift_flags = self.drift.detect_drift(self.previous_stats, current_stats)
            for strat, drifted in drift_flags.items():
                if drifted:
                    print(f"[WARNING] Safe-learning drift detected in strategy: {strat}")

        # ── 3. Weight update (exponential smoothing) ──────────────────────────
        self.alloc.update_weights(current_stats)
        self.previous_stats = current_stats
        self._last_update   = dt.datetime.utcnow().isoformat()

        return self.alloc.weights

    def get_weights(self, normalized: bool = False) -> Dict[str, float]:
        """Return current strategy weights. normalized=True sums to 1.0."""
        return self.alloc.normalize() if normalized else dict(self.alloc.weights)

    def get_strategy_stats(self) -> Dict[str, Dict[str, Any]]:
        """Return per-strategy stats without triggering an update."""
        return self.analyzer.strategy_stats()

    def status(self) -> Dict[str, Any]:
        """Full status snapshot for AIEM tool / admin inspection."""
        stats = self.get_strategy_stats()
        counts = self.analyzer.all_strategy_counts()
        return {
            "memory_size":       self.memory.size(),
            "strategy_counts":   counts,
            "strategies_ready":  list(stats.keys()),
            "current_weights":   self.get_weights(normalized=True),
            "last_update":       self._last_update,
            "last_blocked":      self._last_blocked,
            "safety_gate": {
                "min_sample_size":    self.safety.min_sample_size,
                "max_drawdown_limit": self.safety.max_drawdown_limit,
            },
        }


# ─────────────────────────────────────────────────────────────────────────────
# MODULE-LEVEL SINGLETON (shared across AIEM tool calls)
# ─────────────────────────────────────────────────────────────────────────────

_safe_learning_system: Optional[SafeLearningSystem] = None


def get_safe_learning_system(seed_db: bool = True, days_back: int = 90) -> SafeLearningSystem:
    """
    Returns the module-level SafeLearningSystem singleton.
    On first call, optionally seeds from DB so it starts with real history.
    """
    global _safe_learning_system
    if _safe_learning_system is None:
        _safe_learning_system = SafeLearningSystem()
        if seed_db:
            try:
                _safe_learning_system.seed_from_db(days_back=days_back)
            except Exception as exc:
                print(f"[safe_learning] DB seed skipped: {exc}")
    return _safe_learning_system


# ─────────────────────────────────────────────────────────────────────────────
# USAGE EXAMPLE
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    system = SafeLearningSystem(min_samples=2)   # lower threshold for demo

    for i in range(5):
        system.log_trade({"strategy": "momentum", "pnl": 0.01,  "regime": "trend",   "features": {}})
        system.log_trade({"strategy": "momentum", "pnl": -0.005,"regime": "chop",    "features": {}})
        system.log_trade({"strategy": "eod_swing","pnl": 0.008, "regime": "trend",   "features": {}})
        system.log_trade({"strategy": "eod_swing","pnl": -0.003,"regime": "chop",    "features": {}})

    weights = system.update()
    print("\nCURRENT SAFE WEIGHTS:")
    for k, v in weights.items():
        print(f"  {k}: {v:.4f}")

    print("\nSTATUS:")
    import json
    print(json.dumps(system.status(), indent=2, default=str))
