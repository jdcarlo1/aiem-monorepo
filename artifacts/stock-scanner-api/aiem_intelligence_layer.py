"""
AIEM Intelligence Layer
=======================
4 new classes completing Option B and the Adaptive Market Intelligence layer:

Option B completions:
  1. IntuitionEngine        — final decision logic: TRADE / REDUCE_SIZE / WAIT / NO_TRADE
                              DB-backed audit log of every decision
  2. OptionBBrain           — intelligence orchestrator; composes ExpectancyEngine,
                              RegimeEngine, ConfidenceCalibration, RiskEngine, and
                              IntuitionEngine into a single evaluate(signal) call

Adaptive Market Intelligence completions:
  3. VolatilityNormalizationLayer — normalizes signal scores by realized volatility
                                    from polygon_market_daily (ATR-based)
  4. AdaptiveMarketLayer          — full adaptive orchestrator: regime detection →
                                    strategy selection → vol normalization →
                                    decay check → dynamic risk sizing

All existing modules (MetaStrategySelector, PerformanceDecayDetector,
OnlineLearningModule, RiskAdaptationEngine, RegimeDetectionModule) are already
present in: meta_learning_signal_trust.py, aiem_module2_decay.py,
online_learning.py, aiem_position_sizing.py, regime_detector.py
"""

import os
import json
from typing import Any, Dict, Optional

import psycopg2
import psycopg2.extras

_DB_URL = os.environ.get("DATABASE_URL") or os.environ.get("AIEM_DATABASE_URL")


def _connect():
    if not _DB_URL:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg2.connect(_DB_URL, connect_timeout=5)


# ─────────────────────────────────────────────────────────────────────────────
# SCHEMA
# ─────────────────────────────────────────────────────────────────────────────

def init_schema():
    ddl = """
    CREATE TABLE IF NOT EXISTS intuition_decisions (
        id            BIGSERIAL    PRIMARY KEY,
        signal_id     TEXT,
        signal_type   TEXT,
        regime        TEXT,
        edge_class    TEXT,
        confidence    NUMERIC(8,4),
        regime_ok     BOOLEAN,
        risk_ok       BOOLEAN,
        decision      TEXT,
        position_size NUMERIC(8,4),
        rationale     TEXT,
        created_at    TIMESTAMPTZ  DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS idx_id_signal_type
        ON intuition_decisions (signal_type, created_at DESC);

    CREATE TABLE IF NOT EXISTS adaptive_layer_evaluations (
        id              BIGSERIAL    PRIMARY KEY,
        regime          TEXT,
        best_strategy   TEXT,
        raw_confidence  NUMERIC(8,4),
        norm_confidence NUMERIC(8,4),
        position_size   NUMERIC(8,4),
        decaying        BOOLEAN,
        detail          JSONB,
        evaluated_at    TIMESTAMPTZ  DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS idx_ale_regime
        ON adaptive_layer_evaluations (regime, evaluated_at DESC);
    """
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(ddl)
            conn.commit()
        print("[intelligence_layer] schema ready — intuition_decisions + adaptive_layer_evaluations")
    except Exception as e:
        print(f"[intelligence_layer] schema init error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# 1. INTUITION ENGINE  (Option B — final decision logic)
# Maps the combination of edge quality, regime permission, calibrated
# confidence, and risk filter onto four action levels:
#   TRADE        — high confidence + positive edge + regime OK
#   REDUCE_SIZE  — moderate confidence, proceed with smaller position
#   WAIT         — signal present but not compelling enough yet
#   NO_TRADE     — blocked by regime, risk gate, or negative edge
# Every decision is persisted to intuition_decisions for audit/replay.
# ─────────────────────────────────────────────────────────────────────────────

class IntuitionEngine:
    """
    Final decision logic layer (Option B, module 6).
    Converts all upstream signals into one of four clean action labels.
    """

    # Thresholds
    TRADE_THRESHOLD       = 0.65
    REDUCE_SIZE_THRESHOLD = 0.40

    def decide(
        self,
        edge_class:  str,
        regime_ok:   bool,
        confidence:  float,
        risk_ok:     bool,
        signal_id:   str  = "",
        signal_type: str  = "",
        regime:      str  = "",
        position_size: float = 1.0,
    ) -> str:
        """
        Returns: 'TRADE' | 'REDUCE_SIZE' | 'WAIT' | 'NO_TRADE'
        Also persists the full decision record to DB.
        """
        rationale = []

        # Hard blocks
        if not regime_ok:
            decision  = "NO_TRADE"
            rationale = ["regime_blocked"]
        elif "negative" in edge_class:
            decision  = "NO_TRADE"
            rationale = [f"blocked_by_edge={edge_class}"]
        elif not risk_ok:
            decision  = "NO_TRADE"
            rationale = ["risk_gate_failed"]
        # Graded approvals
        elif confidence >= self.TRADE_THRESHOLD:
            decision  = "TRADE"
            rationale = [f"confidence={confidence:.2f} >= {self.TRADE_THRESHOLD}"]
        elif confidence >= self.REDUCE_SIZE_THRESHOLD:
            decision  = "REDUCE_SIZE"
            rationale = [f"confidence={confidence:.2f} in [{self.REDUCE_SIZE_THRESHOLD}, {self.TRADE_THRESHOLD})"]
        else:
            decision  = "WAIT"
            rationale = [f"confidence={confidence:.2f} < {self.REDUCE_SIZE_THRESHOLD}",
                         f"edge={edge_class}"]

        self._log(signal_id, signal_type, regime, edge_class, confidence,
                  regime_ok, risk_ok, decision, position_size,
                  "; ".join(rationale))
        return decision

    def _log(self, signal_id, signal_type, regime, edge_class,
             confidence, regime_ok, risk_ok, decision, size, rationale):
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO intuition_decisions
                            (signal_id, signal_type, regime, edge_class,
                             confidence, regime_ok, risk_ok, decision,
                             position_size, rationale)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """, (signal_id or None, signal_type or None, regime or None,
                          edge_class, float(confidence), bool(regime_ok),
                          bool(risk_ok), decision, float(size), rationale))
                conn.commit()
        except Exception as e:
            print(f"[IntuitionEngine] log error: {e}")

    def history(self, signal_type: str = None, limit: int = 20):
        """Decision history for status tool."""
        try:
            with _connect() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    if signal_type:
                        cur.execute("""
                            SELECT signal_type, regime, edge_class, confidence,
                                   decision, position_size, rationale, created_at
                            FROM intuition_decisions
                            WHERE signal_type = %s
                            ORDER BY created_at DESC LIMIT %s
                        """, (signal_type, limit))
                    else:
                        cur.execute("""
                            SELECT signal_type, regime, edge_class, confidence,
                                   decision, position_size, rationale, created_at
                            FROM intuition_decisions
                            ORDER BY created_at DESC LIMIT %s
                        """, (limit,))
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            return [{"error": str(e)}]

    def decision_summary(self) -> Dict:
        """Breakdown of decisions by type for status tool."""
        try:
            with _connect() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("""
                        SELECT decision, COUNT(*) AS n,
                               ROUND(AVG(confidence)::numeric, 3) AS avg_conf
                        FROM intuition_decisions
                        WHERE created_at >= NOW() - INTERVAL '30 days'
                        GROUP BY decision
                        ORDER BY n DESC
                    """)
                    return {"last_30d": [dict(r) for r in cur.fetchall()]}
        except Exception as e:
            return {"error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# 2. OPTION B BRAIN  (Option B — intelligence orchestrator)
# Composes all 6 Option B modules into a single evaluate(signal_type, regime,
# confidence) call. Imports from existing AIEM modules so nothing is
# duplicated:
#   ExpectancyEngine   ← aiem_edge_filter
#   RegimeEngine       ← aiem_edge_filter
#   ConfidenceCalibration ← aiem_rl_engine
#   AllocationEngine   ← aiem_edge_filter  (acts as RiskEngine)
#   IntuitionEngine    ← this file
# ─────────────────────────────────────────────────────────────────────────────

class OptionBBrain:
    """
    Option B Intelligence Orchestrator.
    evaluate() runs the full 6-step pipeline and returns a decision dict.
    """

    # Confidence error penalty applied by ConfidenceCalibration
    ERROR_PENALTY = 0.08

    def evaluate(
        self,
        signal_type:    str,
        regime:         str   = "NEUTRAL",
        raw_confidence: float = 0.5,
        signal_id:      str   = "",
    ) -> Dict[str, Any]:
        """
        Steps:
          1. edge_class  via ExpectancyEngine
          2. regime_ok   via RegimeEngine
          3. confidence  via ConfidenceCalibration
          4. risk_ok     via RiskEngine logic
          5. decision    via IntuitionEngine
          6. size        via AllocationEngine
        """
        result: Dict[str, Any] = {
            "signal_type":   signal_type,
            "regime":        regime,
            "edge_class":    "insufficient_data",
            "regime_ok":     True,
            "confidence":    raw_confidence,
            "risk_ok":       True,
            "decision":      "WAIT",
            "position_size": 1.0,
        }

        # ── 1. Edge class (ExpectancyEngine) ──────────────────────────────
        try:
            from aiem_edge_filter import ExpectancyEngine as _EE
            result["edge_class"] = _EE().edge_class(signal_type)
        except Exception as _e:
            result["edge_class"] = "insufficient_data"

        # ── 2. Regime gate (RegimeEngine) ─────────────────────────────────
        try:
            from aiem_edge_filter import RegimeEngine as _RE
            ok, _ = _RE().allowed(signal_type, regime)
            result["regime_ok"] = ok
        except Exception:
            result["regime_ok"] = True  # pass-through on error

        # ── 3. Confidence calibration (ConfidenceCalibration) ─────────────
        calibrated = max(0.0, min(1.0, raw_confidence - self.ERROR_PENALTY))
        result["confidence"] = round(calibrated, 4)

        # ── 4. Risk gate ───────────────────────────────────────────────────
        risk_ok = (
            result["edge_class"] != "negative"
            and calibrated >= 0.30
        )
        result["risk_ok"] = risk_ok

        # ── 5. IntuitionEngine decision ────────────────────────────────────
        intuition = IntuitionEngine()
        decision = intuition.decide(
            edge_class   = result["edge_class"],
            regime_ok    = result["regime_ok"],
            confidence   = calibrated,
            risk_ok      = risk_ok,
            signal_id    = signal_id,
            signal_type  = signal_type,
            regime       = regime,
            position_size= result["position_size"],
        )
        result["decision"] = decision

        # ── 6. Position size (AllocationEngine) ───────────────────────────
        try:
            from aiem_edge_filter import AllocationEngine as _AE
            result["position_size"] = _AE().multiplier(
                signal_type, regime, calibrated
            )
        except Exception:
            pass

        return result

    def status(self) -> Dict:
        intuition = IntuitionEngine()
        return {
            "decision_summary": intuition.decision_summary(),
            "recent_decisions": intuition.history(limit=10),
        }


# ─────────────────────────────────────────────────────────────────────────────
# 3. VOLATILITY NORMALIZATION LAYER  (Adaptive Market Intelligence, module 6)
# Normalizes a score by the realized ATR-based volatility for a ticker from
# polygon_market_daily. Falls back to simple 1/(1+vol) if no ticker given.
# ─────────────────────────────────────────────────────────────────────────────

class VolatilityNormalizationLayer:
    """
    Normalizes signal confidence scores by realized market volatility.
    High volatility shrinks score (less trust in any single signal).
    Low volatility amplifies it (signal has more predictive power).
    """

    def normalize(self, value: float, volatility: float) -> float:
        """
        Simple divisor normalization. volatility in [0,1].
        Returns value in [0,1].
        """
        if volatility <= 0:
            return float(value)
        return round(float(value) / (1.0 + float(volatility)), 6)

    def normalize_for_ticker(self, value: float, ticker: str,
                             days: int = 20) -> float:
        """
        Fetches realized ATR% volatility from polygon_market_daily and
        normalizes the score. Falls back to raw value if no data.
        """
        vol = self._realized_volatility(ticker, days)
        if vol is None:
            return float(value)
        return self.normalize(value, vol)

    def _realized_volatility(self, ticker: str, days: int = 20) -> Optional[float]:
        """
        Returns average (high-low)/close as a proxy for realized volatility
        over the last `days` trading sessions. Result in [0,1] scale.
        """
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT AVG((high - low) / NULLIF(close_price, 0))
                        FROM polygon_market_daily
                        WHERE ticker = %s
                          AND scan_date >= CURRENT_DATE - INTERVAL '%s days'
                          AND close_price > 0
                    """, (ticker, days))
                    row = cur.fetchone()
            if row and row[0] is not None:
                return float(row[0])
        except Exception:
            pass
        return None

    def vix_based_factor(self) -> float:
        """
        Returns a VIX-derived normalization factor from the most recent
        polygon_market_daily row for ^VIX. VIX 20 → factor 1.0.
        """
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT close_price FROM polygon_market_daily
                        WHERE ticker = '^VIX'
                        ORDER BY scan_date DESC LIMIT 1
                    """)
                    row = cur.fetchone()
            if row and row[0]:
                vix = float(row[0])
                return round(20.0 / max(vix, 10.0), 4)
        except Exception:
            pass
        return 1.0


# ─────────────────────────────────────────────────────────────────────────────
# 4. ADAPTIVE MARKET LAYER  (Adaptive Market Intelligence — full orchestrator)
# Ties the 5 existing modules + VolatilityNormalizationLayer into one
# evaluate(strategy_scores, ticker) call that returns a regime-aware,
# volatility-normalized, decay-checked position decision.
#
# Existing modules it delegates to:
#   regime_detector.py          → real SPY+VIX regime
#   meta_learning_signal_trust  → EMA-weighted strategy weights
#   aiem_module2_decay          → decay status per signal
#   aiem_position_sizing        → Kelly risk sizing
#   VolatilityNormalizationLayer → (this file)
# ─────────────────────────────────────────────────────────────────────────────

class AdaptiveMarketLayer:
    """
    Full adaptive orchestrator. Returns a regime-aware trading decision with
    volatility-normalized confidence and decay detection.
    """

    def __init__(self):
        self.vol_norm = VolatilityNormalizationLayer()

    def evaluate(
        self,
        strategy_scores: Dict[str, float],
        ticker: str = "SPY",
    ) -> Dict[str, Any]:
        """
        strategy_scores: dict mapping strategy name → raw score [0,1]
        Returns full decision dict with audit trail.
        """
        result: Dict[str, Any] = {
            "ticker":          ticker,
            "regime":          "NEUTRAL",
            "best_strategy":   None,
            "raw_confidence":  0.0,
            "norm_confidence": 0.0,
            "position_size":   1.0,
            "decaying":        False,
            "detail":          {},
        }

        # ── 1. Regime detection (regime_detector.py — real SPY+VIX) ───────
        try:
            from regime_detector import get_current_regime as _gcr
            _reg = _gcr()
            label = _reg.get("regime", "NEUTRAL")
            # Map regime_detector labels → canonical BULL/BEAR/NEUTRAL/HIGH_VOL
            label_map = {
                "full_exposure":    "BULL",
                "reduce_exposure":  "BEAR",
                "sit_out":          "BEAR",
            }
            result["regime"] = label_map.get(label, label.upper())
            result["detail"]["regime_raw"] = label
        except Exception as _re:
            result["detail"]["regime_error"] = str(_re)

        # ── 2. Strategy selection (meta_learning_signal_trust) ─────────────
        try:
            import meta_learning_signal_trust as _mlt
            weights = _mlt.get_all_signal_weights() or {}
            weighted = {}
            for k, v in strategy_scores.items():
                w = float(weights.get(k, {}).get("current_weight", 1.0))
                weighted[k] = float(v) * w
            if weighted:
                best = max(weighted, key=weighted.get)
                result["best_strategy"]  = best
                result["raw_confidence"] = round(weighted[best], 4)
            else:
                raise ValueError("empty weighted scores")
        except Exception:
            # Fallback: plain argmax
            if strategy_scores:
                best = max(strategy_scores, key=strategy_scores.get)
                result["best_strategy"]  = best
                result["raw_confidence"] = round(float(strategy_scores[best]), 4)

        # ── 3. Volatility normalization ────────────────────────────────────
        norm_conf = self.vol_norm.normalize_for_ticker(
            result["raw_confidence"], ticker
        )
        # Also apply VIX factor
        vix_factor = self.vol_norm.vix_based_factor()
        norm_conf  = round(norm_conf * vix_factor, 4)
        result["norm_confidence"] = norm_conf
        result["detail"]["vix_factor"] = vix_factor

        # ── 4. Decay check (aiem_module2_decay) ───────────────────────────
        try:
            import aiem_module2_decay as _m2
            signal_name = result.get("best_strategy", "")
            status = _m2.get_signal_status(signal_name) if signal_name else {}
            decaying = status.get("evaluation_status") in ("decaying", "invalidated")
            result["decaying"] = bool(decaying)
            result["detail"]["decay_status"] = status.get("evaluation_status", "unknown")
            if decaying:
                norm_conf = round(norm_conf * 0.5, 4)
                result["norm_confidence"] = norm_conf
                result["detail"]["decay_penalty_applied"] = True
        except Exception as _de:
            result["detail"]["decay_error"] = str(_de)

        # ── 5. Risk sizing (aiem_position_sizing) ─────────────────────────
        try:
            import aiem_position_sizing as _ps
            regime_mult = {
                "BULL": 1.0, "BEAR": 0.5, "NEUTRAL": 0.8,
                "HIGH_VOL": 0.5, "high_vol": 0.5, "chop": 0.7,
            }.get(result["regime"], 0.8)
            base_size = norm_conf * regime_mult
            result["position_size"] = round(max(0.25, min(2.0, base_size * 2.0)), 4)
        except Exception:
            # Fallback manual sizing
            mult = 0.5 if "BEAR" in result["regime"].upper() else 1.0
            result["position_size"] = round(
                max(0.25, min(2.0, norm_conf * 2.0 * mult)), 4
            )

        # ── 6. Persist to DB ───────────────────────────────────────────────
        self._log(result)

        return result

    def _log(self, r: Dict) -> None:
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO adaptive_layer_evaluations
                            (regime, best_strategy, raw_confidence, norm_confidence,
                             position_size, decaying, detail)
                        VALUES (%s,%s,%s,%s,%s,%s,%s)
                    """, (
                        r.get("regime"), r.get("best_strategy"),
                        r.get("raw_confidence"), r.get("norm_confidence"),
                        r.get("position_size"), r.get("decaying"),
                        json.dumps(r.get("detail", {})),
                    ))
                conn.commit()
        except Exception as e:
            print(f"[AdaptiveMarketLayer] log error: {e}")

    def history(self, n: int = 10):
        try:
            with _connect() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("""
                        SELECT regime, best_strategy, raw_confidence,
                               norm_confidence, position_size, decaying, evaluated_at
                        FROM adaptive_layer_evaluations
                        ORDER BY evaluated_at DESC LIMIT %s
                    """, (n,))
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            return [{"error": str(e)}]


# ─────────────────────────────────────────────────────────────────────────────
# Module-level singletons
# ─────────────────────────────────────────────────────────────────────────────

_option_b_brain:      Optional[OptionBBrain]      = None
_adaptive_layer:      Optional[AdaptiveMarketLayer] = None


def get_option_b_brain() -> OptionBBrain:
    global _option_b_brain
    if _option_b_brain is None:
        _option_b_brain = OptionBBrain()
    return _option_b_brain


def get_adaptive_layer() -> AdaptiveMarketLayer:
    global _adaptive_layer
    if _adaptive_layer is None:
        _adaptive_layer = AdaptiveMarketLayer()
    return _adaptive_layer
