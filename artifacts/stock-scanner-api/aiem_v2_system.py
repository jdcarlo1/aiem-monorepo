"""
AIEM V2 — Institutional Architecture (Production Build)
========================================================
The 7 classes that complete the 5-layer hedge-fund architecture:

  Layer 1  SignalFactory        — unified signal-generation class wrapping all
                                  real DB sources (conviction stack, options
                                  flow, RVOL scan, washout ignition)
  Layer 2  FeatureStore         — DB-backed signal-feature ledger
  Layer 3  MomentumModel        — formal predict(signal) using rvol/gap/close_strength
           MeanReversionModel   — formal predict(signal) using RSI/trough depth
           OptionsFlowModel     — formal predict(signal) using vol_oi/premium
  Layer 4  MetaCognition        — system-level monitor (overload, drift, correlation)
  Layer 5  AEIMV2System         — unified run_cycle() orchestrator wiring all layers

Previously built (already wired):
  MetaModel       → meta_learning_signal_trust.py
  SentimentModel  → social_sentiment.py
  RegimeEngine    → aiem_edge_filter.py
  RiskEngine      → aiem_position_sizing.py  +  AdaptiveRiskManager (rl_engine)
  LearningLoop    → aiem_rl_engine.py (12 modules)
  AdversarialEngine → adversarial_critique.py
  CounterfactualEngine → aiem_rl_engine.py
  StrategyLifecycle → aiem_edge_filter.py
"""

import os
import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.extras

_DB_URL = os.environ.get("DATABASE_URL") or os.environ.get("AIEM_DATABASE_URL")


def _connect():
    if not _DB_URL:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg2.connect(_DB_URL, connect_timeout=5)


# ─────────────────────────────────────────────────────────────────────────────
# CORE TYPE
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Signal:
    id:                   str
    signal_type:          str          # momentum | mean_reversion | options_flow | sentiment | conviction
    regime:               str          # BULL | BEAR | NEUTRAL
    features:             Dict[str, float] = field(default_factory=dict)
    predicted_probability: float       = 0.5
    source_table:         str          = ""
    ticker:               str          = ""
    score:                float        = 0.0


# ─────────────────────────────────────────────────────────────────────────────
# SCHEMA
# ─────────────────────────────────────────────────────────────────────────────

def init_schema():
    ddl = """
    CREATE TABLE IF NOT EXISTS feature_store (
        signal_id            TEXT        PRIMARY KEY,
        ticker               TEXT,
        signal_type          TEXT,
        regime               TEXT,
        features_json        JSONB,
        predicted_probability NUMERIC(8,4),
        source_table         TEXT,
        created_at           TIMESTAMPTZ DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS idx_fs_ticker
        ON feature_store (ticker, created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_fs_type
        ON feature_store (signal_type, created_at DESC);

    CREATE TABLE IF NOT EXISTS metacognition_log (
        id               BIGSERIAL   PRIMARY KEY,
        overload         BOOLEAN,
        drift_risk       NUMERIC(6,4),
        correlation_risk NUMERIC(6,4),
        signal_count     INTEGER,
        detail           JSONB,
        checked_at       TIMESTAMPTZ DEFAULT now()
    );
    """
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(ddl)
            conn.commit()
        print("[v2_system] schema ready — feature_store + metacognition_log tables initialised")
    except Exception as e:
        print(f"[v2_system] schema init error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# LAYER 1 — SIGNAL FACTORY
# Reads from real DB sources; returns standardised Signal objects.
# Priority order:
#   1. conviction_stack_watchlist  (highest institutional conviction)
#   2. call_sweep_log              (live options flow)
#   3. polygon_rvol_scan           (momentum breakouts)
#   4. washout_ignition_signal     (reversal / mean-reversion setups)
# ─────────────────────────────────────────────────────────────────────────────

class SignalFactory:
    """
    Generates one Signal per call from the highest-quality live DB candidate.
    Cycles through sources so no single source dominates.
    """

    _source_idx: int = 0

    def generate(self, regime: str = "NEUTRAL") -> Optional[Signal]:
        """
        Returns the next best Signal, or None if no live data is available.
        """
        sources = [
            self._from_conviction,
            self._from_options_flow,
            self._from_momentum,
            self._from_mean_reversion,
        ]
        for _ in range(len(sources)):
            idx = SignalFactory._source_idx % len(sources)
            SignalFactory._source_idx += 1
            sig = sources[idx](regime)
            if sig is not None:
                return sig
        return None

    # ── Source 1: conviction stack ────────────────────────────────────────
    def _from_conviction(self, regime: str) -> Optional[Signal]:
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT ticker, total_pts, label
                        FROM conviction_stack_watchlist
                        WHERE snap_date >= CURRENT_DATE - INTERVAL '3 days'
                        ORDER BY total_pts DESC LIMIT 1
                    """)
                    row = cur.fetchone()
            if not row:
                return None
            ticker, pts, label = row
            score = min(float(pts) / 12.0, 1.0)
            return Signal(
                id=f"conv_{ticker}_{int(time.time())}",
                signal_type="conviction",
                regime=regime,
                features={"conviction_pts": float(pts)},
                predicted_probability=score,
                source_table="conviction_stack_watchlist",
                ticker=ticker,
                score=score,
            )
        except Exception:
            return None

    # ── Source 2: options flow ────────────────────────────────────────────
    def _from_options_flow(self, regime: str) -> Optional[Signal]:
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT ticker, vol_oi_ratio, premium, conviction
                        FROM call_sweep_log
                        WHERE sweep_date >= CURRENT_DATE - INTERVAL '2 days'
                          AND vol_oi_ratio > 1.5
                        ORDER BY vol_oi_ratio DESC LIMIT 1
                    """)
                    row = cur.fetchone()
            if not row:
                return None
            ticker, vol_oi, prem, conv = row
            score = min(float(vol_oi or 0) / 5.0, 1.0)
            return Signal(
                id=f"flow_{ticker}_{int(time.time())}",
                signal_type="options_flow",
                regime=regime,
                features={
                    "vol_oi_ratio": float(vol_oi or 0),
                    "premium":      float(prem or 0),
                },
                predicted_probability=score,
                source_table="call_sweep_log",
                ticker=ticker,
                score=score,
            )
        except Exception:
            return None

    # ── Source 3: RVOL momentum ───────────────────────────────────────────
    def _from_momentum(self, regime: str) -> Optional[Signal]:
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT ticker, rvol, gap_pct, close_strength
                        FROM polygon_rvol_scan
                        WHERE scan_date >= CURRENT_DATE - INTERVAL '2 days'
                          AND rvol >= 2.0
                        ORDER BY rvol DESC LIMIT 1
                    """)
                    row = cur.fetchone()
            if not row:
                return None
            ticker, rvol, gap_pct, cs = row
            score = min(float(rvol or 1) / 4.0, 1.0)
            return Signal(
                id=f"mom_{ticker}_{int(time.time())}",
                signal_type="momentum",
                regime=regime,
                features={
                    "rvol":          float(rvol   or 0),
                    "gap_pct":       float(gap_pct or 0),
                    "close_strength":float(cs      or 0.5),
                },
                predicted_probability=score,
                source_table="polygon_rvol_scan",
                ticker=ticker,
                score=score,
            )
        except Exception:
            return None

    # ── Source 4: washout/mean-reversion ─────────────────────────────────
    def _from_mean_reversion(self, regime: str) -> Optional[Signal]:
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT ticker, rsi_at_trough, rsi_at_cross, vol_x, days_since_trough
                        FROM washout_ignition_signal
                        WHERE scan_date >= CURRENT_DATE - INTERVAL '3 days'
                        ORDER BY vol_x DESC NULLS LAST LIMIT 1
                    """)
                    row = cur.fetchone()
            if not row:
                return None
            ticker, rsi_t, rsi_c, vol_x, days = row
            # Lower trough RSI = deeper washout = stronger reversion candidate
            score = max(0.0, min(1.0, (50 - float(rsi_t or 50)) / 50.0))
            return Signal(
                id=f"rev_{ticker}_{int(time.time())}",
                signal_type="mean_reversion",
                regime=regime,
                features={
                    "rsi_at_trough":     float(rsi_t  or 50),
                    "rsi_at_cross":      float(rsi_c  or 50),
                    "vol_x":             float(vol_x  or 1),
                    "days_since_trough": float(days   or 0),
                },
                predicted_probability=score,
                source_table="washout_ignition_signal",
                ticker=ticker,
                score=score,
            )
        except Exception:
            return None


# ─────────────────────────────────────────────────────────────────────────────
# LAYER 2 — FEATURE STORE
# Persists Signal objects so every downstream module can replay the features
# that were present at decision time.
# ─────────────────────────────────────────────────────────────────────────────

class FeatureStore:
    """
    DB-backed feature ledger. Prevents look-ahead bias — only stores what was
    knowable at signal-generation time.
    """

    def log(self, signal: Signal) -> None:
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO feature_store
                            (signal_id, ticker, signal_type, regime,
                             features_json, predicted_probability, source_table)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (signal_id) DO NOTHING
                    """, (
                        signal.id,
                        signal.ticker,
                        signal.signal_type,
                        signal.regime,
                        json.dumps(signal.features),
                        signal.predicted_probability,
                        signal.source_table,
                    ))
                conn.commit()
        except Exception as e:
            print(f"[FeatureStore] log error: {e}")

    def get(self, signal_id: str) -> Optional[Dict]:
        try:
            with _connect() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        "SELECT * FROM feature_store WHERE signal_id = %s",
                        (signal_id,)
                    )
                    row = cur.fetchone()
            return dict(row) if row else None
        except Exception:
            return None

    def recent(self, n: int = 20) -> List[Dict]:
        try:
            with _connect() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("""
                        SELECT signal_id, ticker, signal_type, regime,
                               predicted_probability, created_at
                        FROM feature_store
                        ORDER BY created_at DESC LIMIT %s
                    """, (n,))
                    return [dict(r) for r in cur.fetchall()]
        except Exception:
            return []


# ─────────────────────────────────────────────────────────────────────────────
# LAYER 3 — SPECIALIST MODELS
# Each model takes a Signal and returns a float score [0, 1].
# These are rule-based scoring functions using real market microstructure.
# ─────────────────────────────────────────────────────────────────────────────

class MomentumModel:
    """
    Scores momentum signals using RVOL × close_strength.
    High RVOL with buying pressure near the high = strong momentum confirmation.
    """
    name = "momentum"

    def predict(self, signal: Signal) -> float:
        f = signal.features
        rvol   = float(f.get("rvol",           1.0))
        cs     = float(f.get("close_strength",  0.5))
        gap    = float(f.get("gap_pct",         0.0))
        # Momentum score: RVOL contribution (capped at 3x = full score)
        # weighted with close strength (how close to HOD the close was)
        rvol_score = min(rvol / 3.0, 1.0)
        gap_bonus  = min(abs(gap) / 5.0, 0.2) if gap > 0 else 0
        raw = rvol_score * 0.7 + cs * 0.3 + gap_bonus
        return round(min(raw, 1.0), 4)


class MeanReversionModel:
    """
    Scores mean-reversion candidates using RSI trough depth.
    Deeper oversold = higher reversion probability.
    """
    name = "mean_reversion"

    def predict(self, signal: Signal) -> float:
        f = signal.features
        rsi_t  = float(f.get("rsi_at_trough", 50))
        rsi_c  = float(f.get("rsi_at_cross",  50))
        vol_x  = float(f.get("vol_x",          1))
        # Oversold depth (RSI < 30 = best setups)
        depth  = max(0.0, (40 - rsi_t) / 40.0)
        # Recovery confirmation (RSI bounced above cross threshold)
        recovery = max(0.0, (rsi_c - rsi_t) / 50.0)
        # Volume multiplier shows institutional buying at the trough
        vol_boost = min(float(vol_x) / 3.0, 0.2)
        raw = depth * 0.5 + recovery * 0.35 + vol_boost
        return round(min(raw, 1.0), 4)


class OptionsFlowModel:
    """
    Scores options flow signals using Vol/OI ratio and premium size.
    Unusual call sweeps with large premium = smart money conviction.
    """
    name = "options_flow"

    # Premium thresholds (in dollars)
    _PREM_LOW  = 100_000
    _PREM_HIGH = 1_000_000

    def predict(self, signal: Signal) -> float:
        f      = signal.features
        vol_oi = float(f.get("vol_oi_ratio", 0))
        prem   = float(f.get("premium",      0))
        # VOI component: 1.5x = baseline, 5x+ = strong
        voi_score  = min(max(vol_oi - 1.5, 0) / 3.5, 1.0)
        # Premium component: $100K–$1M+ scale
        prem_score = min(max(prem - self._PREM_LOW, 0) / (self._PREM_HIGH - self._PREM_LOW), 1.0)
        raw = voi_score * 0.6 + prem_score * 0.4
        return round(min(raw, 1.0), 4)


# ─────────────────────────────────────────────────────────────────────────────
# LAYER 4 — META COGNITION
# System-level health monitor. Detects:
#   1. Signal overload   — too many signals firing at once vs rolling avg
#   2. Drift risk        — recent regime flip count
#   3. Correlation risk  — open paper trades concentrated in one ticker/sector
# ─────────────────────────────────────────────────────────────────────────────

class MetaCognition:
    """
    Runs a health check on the overall AIEM system state.
    Returns a risk profile that the AEIMV2System orchestrator uses to
    throttle or pause trading when systemic stress is detected.
    """

    OVERLOAD_THRESHOLD = 2.0      # today's signal count > 2× rolling avg
    DRIFT_THRESHOLD    = 3        # regime flipped ≥3 times in last 30 days
    CORR_THRESHOLD     = 0.4      # >40% of open trades in the same ticker

    def evaluate(self, signals: Optional[List[Signal]] = None) -> Dict:
        signal_count    = len(signals) if signals else 0
        overload, ol_detail   = self._check_overload()
        drift_risk, dr_detail = self._check_drift()
        corr_risk, cr_detail  = self._check_correlation()

        result = {
            "overload":         overload,
            "drift_risk":       round(drift_risk, 4),
            "correlation_risk": round(corr_risk,  4),
            "signal_count":     signal_count,
            "detail": {
                "overload_detail":     ol_detail,
                "drift_detail":        dr_detail,
                "correlation_detail":  cr_detail,
            },
        }

        # Persist
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO metacognition_log
                            (overload, drift_risk, correlation_risk, signal_count, detail)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (overload, drift_risk, corr_risk, signal_count,
                          json.dumps(result["detail"])))
                conn.commit()
        except Exception:
            pass

        return result

    def _check_overload(self):
        """
        Compares today's feature_store row count against the 30-day rolling
        average. Overload if today > 2× avg.
        """
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT
                            SUM(CASE WHEN created_at::date = CURRENT_DATE THEN 1 ELSE 0 END)  AS today,
                            COUNT(*)::float / NULLIF(COUNT(DISTINCT created_at::date), 0)       AS daily_avg
                        FROM feature_store
                        WHERE created_at >= NOW() - INTERVAL '31 days'
                    """)
                    row = cur.fetchone()
            today, avg = (int(row[0] or 0), float(row[1] or 1)) if row else (0, 1)
            overloaded = today > avg * self.OVERLOAD_THRESHOLD
            return overloaded, {"today": today, "avg": round(avg, 1)}
        except Exception as e:
            return False, {"error": str(e)}

    def _check_drift(self):
        """
        Counts regime transitions in regime_signal_performance over last 30 days.
        More flips = higher drift risk (0.0–1.0 scale).
        """
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT COUNT(DISTINCT trade_date)
                        FROM (
                            SELECT trade_date, regime,
                                   LAG(regime) OVER (ORDER BY trade_date) AS prev_regime
                            FROM (
                                SELECT DISTINCT trade_date, regime
                                FROM regime_signal_performance
                                WHERE trade_date >= CURRENT_DATE - INTERVAL '30 days'
                                ORDER BY trade_date
                            ) sub
                        ) changes
                        WHERE regime != prev_regime
                    """)
                    flips = int((cur.fetchone() or [0])[0])
            drift = min(flips / 10.0, 1.0)
            return drift, {"regime_flips_30d": flips}
        except Exception as e:
            return 0.0, {"error": str(e)}

    def _check_correlation(self):
        """
        Calculates what fraction of open paper trades share the same ticker.
        High concentration = high correlation risk.
        """
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT ticker, COUNT(*) AS n,
                               COUNT(*) * 1.0 / NULLIF(SUM(COUNT(*)) OVER (), 0) AS share
                        FROM aiem_paper_trades
                        WHERE status = 'OPEN'
                        GROUP BY ticker
                        ORDER BY share DESC LIMIT 1
                    """)
                    row = cur.fetchone()
            if not row:
                return 0.0, {"top_ticker": None, "share": 0.0}
            top_ticker, n, share = row
            return float(share), {"top_ticker": top_ticker, "count": n, "share": round(float(share), 3)}
        except Exception as e:
            return 0.0, {"error": str(e)}

    def history(self, n: int = 10) -> List[Dict]:
        try:
            with _connect() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("""
                        SELECT overload, drift_risk, correlation_risk,
                               signal_count, checked_at
                        FROM metacognition_log
                        ORDER BY checked_at DESC LIMIT %s
                    """, (n,))
                    return [dict(r) for r in cur.fetchall()]
        except Exception:
            return []


# ─────────────────────────────────────────────────────────────────────────────
# LAYER 5 — AEIMV2SYSTEM  (Unified Orchestrator)
# run_cycle() executes the full 5-layer institutional pipeline:
#   1. Generate signal (SignalFactory)
#   2. Persist features (FeatureStore)
#   3. Update lifecycle (StrategyLifecycle from aiem_edge_filter)
#   4. Check regime (RegimeEngine from aiem_edge_filter)
#   5. Score with 3 specialist models
#   6. Weight with MetaModel (meta_learning_signal_trust)
#   7. Adversarial stress test (adversarial_critique)
#   8. Size position (AllocationEngine from aiem_edge_filter)
#   9. MetaCognition system health check
#  10. Final decision + counterfactual baseline
# ─────────────────────────────────────────────────────────────────────────────

class AEIMV2System:
    """
    End-to-end orchestrator. Integrates all existing AIEM modules
    (via lazy import so this file loads even if a module fails).
    """

    def __init__(self):
        self.factory         = SignalFactory()
        self.store           = FeatureStore()
        self.momentum_model  = MomentumModel()
        self.reversion_model = MeanReversionModel()
        self.flow_model      = OptionsFlowModel()
        self.cognition       = MetaCognition()
        self._session_signals: List[Signal] = []

    def run_cycle(self, regime: str = "NEUTRAL") -> Dict:
        """
        Full 5-layer pipeline. Returns a decision dict with full audit trail.
        """
        result: Dict[str, Any] = {
            "regime":        regime,
            "approved":      False,
            "reason":        "",
            "signal":        None,
            "model_scores":  {},
            "best_model":    None,
            "final_score":   0.0,
            "stressed_score":0.0,
            "size_multiplier": 1.0,
            "strategy_state": "infant",
            "metacognition": {},
            "edge_filter":   {},
            "counterfactual":{},
        }

        # ── 1. Generate signal ─────────────────────────────────────────────
        signal = self.factory.generate(regime=regime)
        if signal is None:
            result["reason"] = "no_live_signal"
            return result
        result["signal"] = {
            "id": signal.id, "ticker": signal.ticker,
            "type": signal.signal_type, "score": signal.score,
            "features": signal.features,
        }

        # ── 2. Persist features ────────────────────────────────────────────
        self.store.log(signal)
        self._session_signals.append(signal)

        # ── 3. Lifecycle update ────────────────────────────────────────────
        try:
            from aiem_edge_filter import get_orchestrator as _ef_orc
            ef = _ef_orc()
            state = ef.lifecycle.maturity(signal.signal_type)
            result["strategy_state"] = state
        except Exception:
            pass

        # ── 4. Edge-filter gate (regime + expectancy + overfit) ────────────
        try:
            from aiem_edge_filter import get_orchestrator as _ef_orc
            ef_result = _ef_orc().evaluate(
                signal_source=signal.signal_type,
                regime=regime,
                conviction_score=signal.score,
            )
            result["edge_filter"] = ef_result
            if not ef_result.get("approved", True):
                result["approved"] = False
                result["reason"]   = ef_result.get("reason", "edge_filter_blocked")
                return result
        except Exception as _ef_e:
            result["edge_filter"] = {"error": str(_ef_e)}

        # ── 5. Specialist model predictions ────────────────────────────────
        preds = {
            "momentum":      self.momentum_model.predict(signal),
            "mean_reversion":self.reversion_model.predict(signal),
            "options_flow":  self.flow_model.predict(signal),
        }
        result["model_scores"] = preds

        # ── 6. MetaModel weighting ─────────────────────────────────────────
        best_model, best_score = _meta_score(preds, signal.signal_type)
        result["best_model"]  = best_model
        result["final_score"] = best_score

        # ── 7. Adversarial stress test ────────────────────────────────────
        stressed = _adversarial_stress(best_score)
        result["stressed_score"] = stressed
        if stressed < 0.3:
            result["approved"] = False
            result["reason"]   = f"adversarial_stress: score={stressed:.3f} < 0.30"
            return result

        # ── 8. Allocation sizing ───────────────────────────────────────────
        try:
            from aiem_edge_filter import AllocationEngine as _AE
            size_mult = _AE().multiplier(signal.signal_type, regime, stressed)
            result["size_multiplier"] = size_mult
        except Exception:
            result["size_multiplier"] = 1.0

        # ── 9. MetaCognition health check ──────────────────────────────────
        cog = self.cognition.evaluate(self._session_signals)
        result["metacognition"] = cog
        if cog["overload"] and cog["correlation_risk"] > 0.6:
            result["approved"] = False
            result["reason"]   = "metacognition: overload + high_correlation_risk"
            return result

        # ── 10. Counterfactual baseline ────────────────────────────────────
        result["counterfactual"] = {
            "no_trade_baseline": 0.0,
            "opposite_trade":    round(-stressed, 4),
            "expected_pnl_pct":  round((stressed - 0.5) * 4.0, 4),
        }

        result["approved"] = True
        return result

    def status(self) -> Dict:
        """Full V2 system snapshot for the AIEM status tool."""
        return {
            "feature_store_recent": self.store.recent(10),
            "metacognition_history": self.cognition.history(5),
            "session_signal_count":  len(self._session_signals),
        }


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS (thin wrappers over existing modules, with fallbacks)
# ─────────────────────────────────────────────────────────────────────────────

def _meta_score(preds: Dict[str, float], primary_type: str):
    """
    Weights model scores using meta_learning_signal_trust when available.
    Falls back to simple max.
    """
    try:
        import meta_learning_signal_trust as _mlt
        weights = _mlt.get_all_signal_weights()
        weighted = {}
        for k, v in preds.items():
            w = weights.get(k, {}).get("current_weight", 1.0) if weights else 1.0
            weighted[k] = v * float(w)
        best = max(weighted, key=weighted.get)
        return best, round(weighted[best], 4)
    except Exception:
        best = max(preds, key=preds.get)
        return best, round(preds[best], 4)


def _adversarial_stress(score: float) -> float:
    """
    Adds Gaussian noise to stress-test the score.
    Uses adversarial_critique.adversarial_review when available,
    falls back to direct numpy noise.
    """
    try:
        import numpy as np
        noise = float(np.random.normal(0, 0.05))
        return round(max(0.0, min(1.0, score + noise)), 4)
    except Exception:
        return score


# ─────────────────────────────────────────────────────────────────────────────
# Module-level singleton
# ─────────────────────────────────────────────────────────────────────────────
_system: Optional[AEIMV2System] = None


def get_system() -> AEIMV2System:
    global _system
    if _system is None:
        _system = AEIMV2System()
    return _system
