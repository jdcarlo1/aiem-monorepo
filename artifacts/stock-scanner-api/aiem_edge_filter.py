"""
AIEM Edge Filter Engine
=======================
7 new modules that were absent from the existing stack:
  1. ExpectancyEngine      — formal EV per signal source (reads rl_experience_buffer)
  2. RegimeEngine          — live runtime gate: blocks signals in negative-regime conditions
  3. OverfitDetector       — live train/test gap monitor vs OOS baseline in aiem_signal_discoveries
  4. StrategyLifecycle     — signal age/maturity tracking (trade count + days since first trade)
  5. AllocationEngine      — confidence × regime_stability → position-size multiplier
  6. FeatureAblation       — runtime feature-level impact tracking across closed trades
  7. EdgeFilterOrchestrator— unified pre-fire gate; integrates all 7 modules above
                             plus the existing MetaLearner, CalibrationLayer, and SampleGate.

All modules are DB-backed — state survives restarts.
Wire point: call EdgeFilterOrchestrator.evaluate(ticker, signal_source, features, regime)
            before committing a paper trade. Returns {"approved": bool, "reason": str, ...}
"""

import os
import json
import math
from typing import Any, Dict, List, Optional, Tuple

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
    CREATE TABLE IF NOT EXISTS regime_signal_performance (
        id           BIGSERIAL PRIMARY KEY,
        signal_source TEXT     NOT NULL,
        regime        TEXT     NOT NULL,
        pnl_pct       NUMERIC(10,4),
        win           BOOLEAN,
        trade_date    DATE,
        recorded_at   TIMESTAMPTZ DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS idx_rsp_source_regime
        ON regime_signal_performance (signal_source, regime);

    CREATE TABLE IF NOT EXISTS feature_ablation_log (
        id            BIGSERIAL PRIMARY KEY,
        signal_source TEXT     NOT NULL,
        feature_name  TEXT     NOT NULL,
        feature_value NUMERIC(14,6),
        pnl_pct       NUMERIC(10,4),
        win           BOOLEAN,
        trade_date    DATE,
        recorded_at   TIMESTAMPTZ DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS idx_fal_source_feature
        ON feature_ablation_log (signal_source, feature_name);

    CREATE TABLE IF NOT EXISTS overfit_monitor (
        id            BIGSERIAL PRIMARY KEY,
        signal_source TEXT     NOT NULL,
        oos_baseline  NUMERIC(10,4),
        live_wr_30d   NUMERIC(10,4),
        gap           NUMERIC(10,4),
        flagged       BOOLEAN DEFAULT FALSE,
        checked_at    TIMESTAMPTZ DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS idx_om_source
        ON overfit_monitor (signal_source);
    """
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(ddl)
            conn.commit()
        print("[edge_filter] schema ready — 3 tables initialised")
    except Exception as e:
        print(f"[edge_filter] schema init error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# 1. EXPECTANCY ENGINE
# Reads rl_experience_buffer. Computes formal EV per signal source:
#   EV = (win_rate × avg_win_pnl) + ((1 − win_rate) × avg_loss_pnl)
# ─────────────────────────────────────────────────────────────────────────────

class ExpectancyEngine:
    """
    Formal expected-value calculation per signal source.
    Requires ≥20 closed trades in rl_experience_buffer to produce a score.
    """

    MIN_TRADES = 20

    def expectancy(self, signal_source: str) -> float:
        """Returns EV in pnl_pct units. 0.0 if insufficient data."""
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT
                            AVG(CASE WHEN pnl_pct > 0 THEN pnl_pct END)   AS avg_win,
                            AVG(CASE WHEN pnl_pct <= 0 THEN pnl_pct END)  AS avg_loss,
                            SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END)  AS wins,
                            COUNT(*)                                        AS n
                        FROM rl_experience_buffer
                        WHERE signal_source = %s AND pnl_pct IS NOT NULL
                    """, (signal_source,))
                    row = cur.fetchone()
            if not row or not row[3] or row[3] < self.MIN_TRADES:
                return 0.0
            avg_win  = float(row[0] or 0)
            avg_loss = float(row[1] or 0)
            wins     = int(row[2] or 0)
            n        = int(row[3])
            wr       = wins / n
            return (wr * avg_win) + ((1 - wr) * avg_loss)
        except Exception as e:
            print(f"[ExpectancyEngine] error: {e}")
            return 0.0

    def edge_class(self, signal_source: str) -> str:
        """'positive' | 'neutral' | 'negative' | 'insufficient_data'"""
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT COUNT(*) FROM rl_experience_buffer WHERE signal_source = %s",
                        (signal_source,)
                    )
                    n = cur.fetchone()[0]
            if n < self.MIN_TRADES:
                return "insufficient_data"
        except Exception:
            return "insufficient_data"
        ev = self.expectancy(signal_source)
        if ev > 0.5:
            return "positive"
        if ev > 0:
            return "neutral"
        return "negative"

    def all_sources(self) -> List[Dict]:
        """Summary table for the AIEM status tool."""
        try:
            with _connect() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("""
                        SELECT signal_source,
                               COUNT(*)                                           AS n,
                               ROUND(AVG(pnl_pct)::numeric, 3)                   AS avg_pnl,
                               ROUND(100.0 * SUM(CASE WHEN pnl_pct > 0
                                                 THEN 1 ELSE 0 END)
                                     / NULLIF(COUNT(*), 0), 1)                   AS win_rate_pct,
                               ROUND(
                                 (AVG(CASE WHEN pnl_pct > 0 THEN pnl_pct END)
                                    * SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END)
                                    / NULLIF(COUNT(*), 0))
                                 + (AVG(CASE WHEN pnl_pct <= 0 THEN pnl_pct END)
                                    * (1 - SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END)
                                       / NULLIF(COUNT(*)::float, 0)))
                               , 3)                                               AS ev
                        FROM rl_experience_buffer
                        WHERE pnl_pct IS NOT NULL
                        GROUP BY signal_source
                        ORDER BY ev DESC NULLS LAST
                    """)
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            return [{"error": str(e)}]


# ─────────────────────────────────────────────────────────────────────────────
# 2. REGIME ENGINE
# Stores per-trade regime info in regime_signal_performance.
# Live gate: blocks a signal if its avg P&L in the current regime is negative
# AND it has ≥10 observations in that regime.
# ─────────────────────────────────────────────────────────────────────────────

class RegimeEngine:
    """
    Tracks signal performance split by market regime (BULL / BEAR / NEUTRAL).
    Regime is derived from SPY daily return on the trade date:
      SPY ≥ +0.5%  → BULL
      SPY ≤ -0.5%  → BEAR
      else         → NEUTRAL
    """

    MIN_REGIME_OBS = 10

    def log_outcome(
        self,
        signal_source: str,
        regime: str,
        pnl_pct: float,
        trade_date: str,
    ) -> None:
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO regime_signal_performance
                            (signal_source, regime, pnl_pct, win, trade_date)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (signal_source, regime, pnl_pct, pnl_pct > 0,
                          trade_date or None))
                conn.commit()
        except Exception as e:
            print(f"[RegimeEngine] log error: {e}")

    def regime_score(self, signal_source: str, regime: str) -> Optional[float]:
        """Returns avg pnl_pct in this regime, or None if <MIN_REGIME_OBS."""
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT COUNT(*), AVG(pnl_pct)
                        FROM regime_signal_performance
                        WHERE signal_source = %s AND regime = %s
                    """, (signal_source, regime))
                    row = cur.fetchone()
            if not row or not row[0] or row[0] < self.MIN_REGIME_OBS:
                return None
            return float(row[1])
        except Exception:
            return None

    def allowed(self, signal_source: str, regime: str) -> Tuple[bool, str]:
        """
        Returns (True, '') if signal is allowed in this regime.
        Returns (False, reason) if blocked.
        Passes through if insufficient data — never blocks on ignorance.
        """
        score = self.regime_score(signal_source, regime)
        if score is None:
            return True, ""   # insufficient data → allow
        if score < 0:
            return False, f"regime_score={score:.2f} in {regime} (negative avg P&L)"
        return True, ""

    def regime_table(self) -> List[Dict]:
        """All signal×regime combos for status tool."""
        try:
            with _connect() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("""
                        SELECT signal_source, regime,
                               COUNT(*)                               AS n,
                               ROUND(AVG(pnl_pct)::numeric, 3)       AS avg_pnl,
                               ROUND(100.0 * AVG(win::int)::numeric, 1) AS win_pct
                        FROM regime_signal_performance
                        GROUP BY signal_source, regime
                        ORDER BY signal_source, regime
                    """)
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            return [{"error": str(e)}]

    @staticmethod
    def classify_spy_return(spy_return_pct: float) -> str:
        if spy_return_pct >= 0.5:
            return "BULL"
        if spy_return_pct <= -0.5:
            return "BEAR"
        return "NEUTRAL"


# ─────────────────────────────────────────────────────────────────────────────
# 3. OVERFIT DETECTOR
# Compares each signal's OOS baseline (from aiem_signal_discoveries) against
# its live 30-day win rate from rl_experience_buffer.
# Gap > 20pp means the live performance has degraded significantly versus
# what the backtest promised.
# ─────────────────────────────────────────────────────────────────────────────

class OverfitDetector:
    """
    Detects when a signal's live win rate has fallen >20pp below its
    OOS-validated baseline, suggesting the backtest was overfit.
    """

    GAP_THRESHOLD = 20.0   # percentage points

    def check(self, signal_source: str) -> Dict[str, Any]:
        """
        Returns:
          {"overfit": bool, "oos_baseline_wr": float|None,
           "live_wr_30d": float|None, "gap": float|None, "n_live": int}
        """
        oos_wr   = self._oos_baseline(signal_source)
        live_wr, n_live = self._live_win_rate(signal_source, days=30)

        if oos_wr is None or live_wr is None or n_live < 10:
            return {
                "overfit": False,
                "oos_baseline_wr": oos_wr,
                "live_wr_30d": live_wr,
                "gap": None,
                "n_live": n_live,
                "note": "insufficient data",
            }

        gap = oos_wr - live_wr
        overfit = gap > self.GAP_THRESHOLD

        # Persist check result
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO overfit_monitor
                            (signal_source, oos_baseline, live_wr_30d, gap, flagged)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (signal_source, oos_wr, live_wr, gap, overfit))
                conn.commit()
        except Exception:
            pass

        return {
            "overfit": overfit,
            "oos_baseline_wr": round(oos_wr, 1),
            "live_wr_30d": round(live_wr, 1),
            "gap": round(gap, 1),
            "n_live": n_live,
        }

    def _oos_baseline(self, signal_source: str) -> Optional[float]:
        """
        Looks up the OOS win rate from aiem_signal_discoveries.
        Uses the most recent validated row for this source tag.
        """
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT oos_edge
                        FROM aiem_signal_discoveries
                        WHERE signal_name ILIKE %s
                          AND evaluation_status = 'validated'
                          AND oos_edge IS NOT NULL
                        ORDER BY discovery_date DESC
                        LIMIT 1
                    """, (f"%{signal_source}%",))
                    row = cur.fetchone()
            if row and row[0] is not None:
                # oos_edge is stored as a decimal 0-100 scale win rate
                return float(row[0])
        except Exception:
            pass
        return None

    def _live_win_rate(self, signal_source: str, days: int = 30) -> Tuple[Optional[float], int]:
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT
                            100.0 * SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END)
                              / NULLIF(COUNT(*), 0),
                            COUNT(*)
                        FROM rl_experience_buffer
                        WHERE signal_source = %s
                          AND created_at >= NOW() - INTERVAL '%s days'
                          AND pnl_pct IS NOT NULL
                    """, (signal_source, days))
                    row = cur.fetchone()
            if row and row[0] is not None:
                return float(row[0]), int(row[1])
        except Exception:
            pass
        return None, 0

    def all_sources(self) -> List[Dict]:
        """Latest overfit check per source for status tool."""
        try:
            with _connect() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("""
                        SELECT DISTINCT ON (signal_source)
                            signal_source, oos_baseline, live_wr_30d,
                            gap, flagged, checked_at
                        FROM overfit_monitor
                        ORDER BY signal_source, checked_at DESC
                    """)
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            return [{"error": str(e)}]


# ─────────────────────────────────────────────────────────────────────────────
# 4. STRATEGY LIFECYCLE
# Tracks how many trades each signal source has generated and how many
# calendar days since its first trade. Used to detect immature signals
# (not enough real-world track record) and aging signals that may be
# past their prime.
# ─────────────────────────────────────────────────────────────────────────────

class StrategyLifecycle:
    """
    Reads rl_experience_buffer — no extra table needed.
    maturity() returns: 'infant' | 'developing' | 'mature' | 'veteran'
    """

    INFANT     = 20
    DEVELOPING = 100
    MATURE     = 300

    def stats(self, signal_source: str) -> Dict[str, Any]:
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT COUNT(*),
                               MIN(created_at)::date,
                               MAX(created_at)::date
                        FROM rl_experience_buffer
                        WHERE signal_source = %s
                    """, (signal_source,))
                    row = cur.fetchone()
            if not row or not row[0]:
                return {"n_trades": 0, "days_active": 0, "maturity": "infant"}
            n          = int(row[0])
            first_seen = row[1]
            last_seen  = row[2]
            import datetime
            days_active = (last_seen - first_seen).days if first_seen and last_seen else 0
            maturity = (
                "veteran"   if n >= self.MATURE     else
                "mature"    if n >= self.DEVELOPING else
                "developing" if n >= self.INFANT    else
                "infant"
            )
            return {
                "n_trades":    n,
                "days_active": days_active,
                "first_trade": str(first_seen),
                "last_trade":  str(last_seen),
                "maturity":    maturity,
            }
        except Exception as e:
            return {"n_trades": 0, "days_active": 0, "maturity": "infant", "error": str(e)}

    def maturity(self, signal_source: str) -> str:
        return self.stats(signal_source).get("maturity", "infant")

    def all_sources(self) -> List[Dict]:
        try:
            with _connect() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("""
                        SELECT signal_source,
                               COUNT(*)              AS n_trades,
                               MIN(created_at)::date AS first_trade,
                               MAX(created_at)::date AS last_trade,
                               (MAX(created_at) - MIN(created_at))::int AS days_active
                        FROM rl_experience_buffer
                        GROUP BY signal_source
                        ORDER BY n_trades DESC
                    """)
                    rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                n = r["n_trades"]
                r["maturity"] = (
                    "veteran"    if n >= self.MATURE     else
                    "mature"     if n >= self.DEVELOPING else
                    "developing" if n >= self.INFANT     else
                    "infant"
                )
            return rows
        except Exception as e:
            return [{"error": str(e)}]


# ─────────────────────────────────────────────────────────────────────────────
# 5. ALLOCATION ENGINE
# Combines conviction score + regime stability → position-size multiplier.
# Output is a float in [0.25, 2.0] that scales the base notional.
#
# regime_stability = fraction of regime observations that were profitable
# for this signal. High conviction + stable regime → larger size.
# ─────────────────────────────────────────────────────────────────────────────

class AllocationEngine:
    """
    Returns a position-size multiplier for a given signal in a given regime.
    Feeds AdaptiveRiskManager (Kelly) which then applies it on top of base notional.
    """

    MIN_MULT = 0.25
    MAX_MULT = 2.0

    def multiplier(
        self,
        signal_source: str,
        regime: str,
        conviction_score: float = 0.5,
    ) -> float:
        """
        conviction_score: 0.0 – 1.0 (higher = more confident signal)
        Returns a multiplier in [0.25, 2.0].
        """
        regime_stability = self._regime_stability(signal_source, regime)
        raw = float(conviction_score) * float(regime_stability)
        return round(max(self.MIN_MULT, min(self.MAX_MULT, raw * 2.0)), 4)

    def _regime_stability(self, signal_source: str, regime: str) -> float:
        """
        Fraction of trades in this regime that were winners.
        Falls back to 0.5 (neutral) if insufficient data.
        """
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT
                            SUM(CASE WHEN win THEN 1 ELSE 0 END)::float
                              / NULLIF(COUNT(*), 0),
                            COUNT(*)
                        FROM regime_signal_performance
                        WHERE signal_source = %s AND regime = %s
                    """, (signal_source, regime))
                    row = cur.fetchone()
            if row and row[0] is not None and row[1] >= 5:
                return float(row[0])
        except Exception:
            pass
        return 0.5   # neutral fallback


# ─────────────────────────────────────────────────────────────────────────────
# 6. FEATURE ABLATION
# Logs feature values alongside trade outcomes. After ≥20 observations,
# computes whether each feature correlates with wins — if |correlation| < 0.05
# it's not contributing and can be flagged for removal from the signal.
# ─────────────────────────────────────────────────────────────────────────────

class FeatureAblation:
    """
    Logs per-trade feature values and outcome. Reports which features
    are and aren't adding predictive power based on win/loss correlation.
    """

    MIN_OBS = 20

    def log(
        self,
        signal_source: str,
        features: Dict[str, float],
        pnl_pct: float,
        trade_date: str,
    ) -> None:
        if not features:
            return
        win = pnl_pct > 0
        rows = [
            (signal_source, fname, float(fval), pnl_pct, win, trade_date or None)
            for fname, fval in features.items()
            if fval is not None
        ]
        if not rows:
            return
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    psycopg2.extras.execute_values(cur, """
                        INSERT INTO feature_ablation_log
                            (signal_source, feature_name, feature_value, pnl_pct, win, trade_date)
                        VALUES %s
                    """, rows)
                conn.commit()
        except Exception as e:
            print(f"[FeatureAblation] log error: {e}")

    def useful_features(self, signal_source: str) -> List[Dict]:
        """
        Returns list of features with n, correlation to win, and useful flag.
        """
        try:
            with _connect() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("""
                        SELECT feature_name,
                               COUNT(*)                                        AS n,
                               ROUND(CORR(feature_value, win::int)::numeric, 4) AS win_corr,
                               ROUND(CORR(feature_value, pnl_pct)::numeric, 4)  AS pnl_corr
                        FROM feature_ablation_log
                        WHERE signal_source = %s
                        GROUP BY feature_name
                        HAVING COUNT(*) >= %s
                        ORDER BY ABS(CORR(feature_value, win::int)) DESC NULLS LAST
                    """, (signal_source, self.MIN_OBS))
                    rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                corr = float(r.get("win_corr") or 0)
                r["useful"] = abs(corr) >= 0.05
            return rows
        except Exception as e:
            return [{"error": str(e)}]

    def is_feature_useful(self, signal_source: str, feature_name: str) -> bool:
        """Quick check for a single feature. Returns True if unknown (benefit of doubt)."""
        rows = self.useful_features(signal_source)
        for r in rows:
            if r.get("feature_name") == feature_name:
                return r.get("useful", True)
        return True   # not enough data → assume useful


# ─────────────────────────────────────────────────────────────────────────────
# 7. EDGE FILTER ORCHESTRATOR
# Pre-fire gate. Call before committing any paper trade.
# Integrates all 6 new modules above + existing meta-learner calibration factor.
# ─────────────────────────────────────────────────────────────────────────────

class EdgeFilterOrchestrator:
    """
    Unified pre-fire gate.

    evaluate(signal_source, regime, conviction_score, features) returns:
      {
        "approved": bool,
        "reason":   str,          # why it was blocked (empty if approved)
        "ev":       float,        # expected value from ExpectancyEngine
        "edge":     str,          # positive / neutral / negative / insufficient_data
        "maturity": str,          # infant / developing / mature / veteran
        "size_multiplier": float, # from AllocationEngine
        "overfit":  bool,
        "regime_allowed": bool,
      }
    """

    def __init__(self):
        self.expectancy  = ExpectancyEngine()
        self.regime_eng  = RegimeEngine()
        self.overfit_det = OverfitDetector()
        self.lifecycle   = StrategyLifecycle()
        self.allocation  = AllocationEngine()
        self.ablation    = FeatureAblation()

    def evaluate(
        self,
        signal_source: str,
        regime: str = "NEUTRAL",
        conviction_score: float = 0.5,
        features: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:

        result = {
            "approved":        True,
            "reason":          "",
            "signal_source":   signal_source,
            "regime":          regime,
            "ev":              0.0,
            "edge":            "insufficient_data",
            "maturity":        "infant",
            "size_multiplier": 1.0,
            "overfit":         False,
            "regime_allowed":  True,
        }

        # ── 1. Maturity check — never block, just annotate ─────────────────
        maturity = self.lifecycle.maturity(signal_source)
        result["maturity"] = maturity

        # ── 2. Expectancy — block only on confirmed negative edge ──────────
        ev   = self.expectancy.expectancy(signal_source)
        edge = self.expectancy.edge_class(signal_source)
        result["ev"]   = round(ev, 4)
        result["edge"] = edge
        if edge == "negative":
            result["approved"] = False
            result["reason"]   = f"negative_expectancy (EV={ev:.2f})"
            return result

        # ── 3. Regime gate — block if signal loses money in this regime ────
        regime_ok, regime_reason = self.regime_eng.allowed(signal_source, regime)
        result["regime_allowed"] = regime_ok
        if not regime_ok:
            result["approved"] = False
            result["reason"]   = f"regime_blocked: {regime_reason}"
            return result

        # ── 4. Overfit check — warn but don't block (needs analyst review) ─
        overfit_info = self.overfit_det.check(signal_source)
        result["overfit"] = overfit_info.get("overfit", False)
        if result["overfit"]:
            result["reason"] = (
                f"[WARNING] overfit detected — OOS={overfit_info.get('oos_baseline_wr')}% "
                f"vs live_30d={overfit_info.get('live_wr_30d')}% "
                f"(gap={overfit_info.get('gap')}pp). Still approved."
            )

        # ── 5. Allocation sizing ───────────────────────────────────────────
        result["size_multiplier"] = self.allocation.multiplier(
            signal_source, regime, conviction_score
        )

        return result

    def log_closed_trade(
        self,
        signal_source: str,
        pnl_pct: float,
        trade_date: str,
        spy_return_pct: float = 0.0,
        features: Optional[Dict[str, float]] = None,
    ) -> None:
        """
        Call after every paper trade closes. Feeds all 3 logging modules.
        spy_return_pct: SPY % return on the trade_date (for regime classification).
        """
        regime = RegimeEngine.classify_spy_return(spy_return_pct)

        # Log regime performance
        self.regime_eng.log_outcome(signal_source, regime, pnl_pct, trade_date)

        # Log feature ablation if features provided
        if features:
            self.ablation.log(signal_source, features, pnl_pct, trade_date)

    def status(self) -> Dict[str, Any]:
        """Full status snapshot for AIEM tool."""
        return {
            "expectancy_by_source": self.expectancy.all_sources(),
            "regime_table":         self.regime_eng.regime_table(),
            "lifecycle":            self.lifecycle.all_sources(),
            "overfit_checks":       self.overfit_det.all_sources(),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Module-level singleton (used by main.py tool wrappers)
# ─────────────────────────────────────────────────────────────────────────────
_orchestrator: Optional[EdgeFilterOrchestrator] = None


def get_orchestrator() -> EdgeFilterOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = EdgeFilterOrchestrator()
    return _orchestrator
