"""
aiem_level2.py — AIEM Level 2 Research-Grade System
=====================================================
Implements the 7-component Level 2 architecture using REAL data from the
live database and existing AIEM modules.  No simulated price series.

Components (matching the Level 2 master file architecture):
  1. MarketDataEngine  — polygon_market_daily DB table
  2. FeatureEngine     — wraps feature_engineering.build_feature_row()
  3. SignalEngine      — momentum + volume_z + volatility scoring
  4. BacktestEngine    — wraps backtest.backtest_strategy()
  5. MetricsEngine     — Sharpe, max-drawdown, win-rate, AUC
  6. ValidationEngine  — time-split + walk-forward (wraps data_prep)
  7. AEIM_Level2       — orchestrator: fetch → features → backtest → metrics → validate
"""

import os
import logging
import math
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)

_DB_URL = os.environ.get("DATABASE_URL", "")


# ─────────────────────────────────────────────────────────────────────────────
# 1. MARKET DATA ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class MarketDataEngine:
    """
    Fetches OHLCV history from polygon_market_daily.
    Falls back gracefully if the table is empty for the requested symbol.
    """

    def fetch_ohlcv(self, symbol: str, days_back: int = 200) -> pd.DataFrame:
        symbol = symbol.upper().strip()
        try:
            with psycopg2.connect(_DB_URL, connect_timeout=5) as conn, \
                 conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT
                        scan_date  AS timestamp,
                        open_price AS open,
                        high_price AS high,
                        low_price  AS low,
                        close_price AS close,
                        volume,
                        rvol,
                        gap_pct
                    FROM polygon_market_daily
                    WHERE ticker = %s
                      AND scan_date >= CURRENT_DATE - %s
                    ORDER BY scan_date ASC
                """, (symbol, days_back))
                rows = cur.fetchall()
        except Exception as e:
            logger.warning(f"[L2/MarketData] DB error for {symbol}: {e}")
            return pd.DataFrame()

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame([dict(r) for r in rows])
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.set_index("timestamp")
        for col in ["open", "high", "low", "close", "volume", "rvol", "gap_pct"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.dropna(subset=["close"])


# ─────────────────────────────────────────────────────────────────────────────
# 2. FEATURE ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class FeatureEngine:
    """
    Adds Level 2 features to the OHLCV dataframe:
      returns, volatility (10-day rolling std), momentum (5-day price diff),
      volume_z (volume z-score vs 20-day mean).
    Also calls feature_engineering.build_feature_row() for the ML feature vector
    on the most recent row.
    """

    def add_features(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty or len(df) < 21:
            return df

        df = df.copy()
        df["returns"]    = df["close"].pct_change()
        df["volatility"] = df["returns"].rolling(10).std()
        df["momentum"]   = df["close"] - df["close"].shift(5)

        vol_mean = df["volume"].rolling(20).mean()
        vol_std  = df["volume"].rolling(20).std().replace(0, np.nan)
        df["volume_z"] = (df["volume"] - vol_mean) / vol_std

        df = df.dropna(subset=["returns", "momentum"])
        return df

    def build_ml_features(self, symbol: str, df: pd.DataFrame) -> Dict[str, Any]:
        """Call feature_engineering.build_feature_row() on the latest bar."""
        try:
            from feature_engineering import build_feature_row
            if df.empty:
                return {}
            latest = df.iloc[-1]
            pick = {
                "rvol":       float(latest.get("rvol", 1.0) or 1.0),
                "gap_pct":    float(latest.get("gap_pct", 0.0) or 0.0),
                "vol_oi":     None,
                "otm_pct":    None,
                "days_out":   None,
                "trade_date": df.index[-1].date() if hasattr(df.index[-1], "date") else date.today(),
                "conviction": None,
            }
            feat_df = df.reset_index().rename(columns={"timestamp": "date",
                                                        "close_price": "close"})
            return build_feature_row(pick, feat_df)
        except Exception as e:
            logger.warning(f"[L2/FeatureEngine] ML features error: {e}")
            return {}


# ─────────────────────────────────────────────────────────────────────────────
# 3. SIGNAL ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class SignalEngine:
    """
    Generates a 0-1 signal score for each row based on:
      momentum (weight 0.4) + volume_z (weight 0.3) + low-volatility (weight 0.3)
    Mirrors the Level 2 master architecture.

    Thresholds are calibrated via train() using training-window statistics
    so that walk-forward validation refits the model between windows rather
    than applying fixed thresholds to every out-of-sample period.
    """

    def __init__(self):
        # Default thresholds (overwritten by train())
        self._momentum_threshold  = 0.0    # signal fires when momentum > this
        self._volume_z_threshold  = 1.0    # signal fires when volume_z > this
        self._volatility_threshold = 0.02  # signal fires when volatility < this
        self._is_trained          = False

    def train(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        Calibrate signal thresholds from a training window.

        Sets:
          momentum_threshold  = 40th-percentile momentum (bias toward
                                above-average momentum picks)
          volume_z_threshold  = 60th-percentile volume_z (above-average flow)
          volatility_threshold = 55th-percentile volatility (below-average
                                  vol preferred, but not extremes only)

        Using percentiles anchored to the training window means thresholds
        adapt to each window's volatility/volume regime rather than being
        fixed constants across the full time series.

        Returns a dict with the calibrated thresholds for inspection.
        """
        if df is None or df.empty or len(df) < 5:
            return {"trained": False, "reason": "insufficient_training_rows"}

        try:
            mom_col  = "momentum"  if "momentum"  in df.columns else None
            volz_col = "volume_z"  if "volume_z"  in df.columns else None
            vola_col = "volatility" if "volatility" in df.columns else None

            if mom_col:
                self._momentum_threshold = float(
                    df[mom_col].dropna().quantile(0.40)
                    if not df[mom_col].dropna().empty else 0.0
                )
            if volz_col:
                self._volume_z_threshold = float(
                    df[volz_col].dropna().quantile(0.60)
                    if not df[volz_col].dropna().empty else 1.0
                )
            if vola_col:
                self._volatility_threshold = float(
                    df[vola_col].dropna().quantile(0.55)
                    if not df[vola_col].dropna().empty else 0.02
                )
            self._is_trained = True
            return {
                "trained":              True,
                "n_train_rows":         len(df),
                "momentum_threshold":   round(self._momentum_threshold, 6),
                "volume_z_threshold":   round(self._volume_z_threshold, 6),
                "volatility_threshold": round(self._volatility_threshold, 6),
            }
        except Exception as exc:
            return {"trained": False, "reason": str(exc)}

    def generate_signal(self, row: pd.Series) -> float:
        score = 0.0
        try:
            if pd.notna(row.get("momentum")) and row["momentum"] > self._momentum_threshold:
                score += 0.4
            if pd.notna(row.get("volume_z")) and row["volume_z"] > self._volume_z_threshold:
                score += 0.3
            if pd.notna(row.get("volatility")) and row["volatility"] < self._volatility_threshold:
                score += 0.3
        except Exception:
            pass
        return min(max(score, 0.0), 1.0)

    def score_series(self, df: pd.DataFrame) -> pd.Series:
        """Return a signal score for every row in the dataframe."""
        return df.apply(self.generate_signal, axis=1)


# ─────────────────────────────────────────────────────────────────────────────
# 4. BACKTEST ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class BacktestEngine:
    """
    Runs a vectorised equity-curve backtest.
    For DB-backed Polygon data: uses SignalEngine scores directly.
    For indicator-scored data: delegates to backtest.backtest_strategy().
    """

    def run_backtest(
        self,
        df: pd.DataFrame,
        signal_engine: SignalEngine,
        buy_threshold: float = 0.7,
        sell_threshold: float = 0.3,
        initial_cash: float = 10_000.0,
    ) -> List[float]:
        equity   = initial_cash
        position = 0.0
        curve    = []

        for i in range(20, len(df)):
            row    = df.iloc[i]
            signal = signal_engine.generate_signal(row)
            close  = float(row.get("close", 0) or 0)

            if close <= 0:
                curve.append(equity)
                continue

            if signal > buy_threshold and position == 0:
                position    = equity / close
                equity      = 0.0

            elif signal < sell_threshold and position > 0:
                equity      = position * close
                position    = 0.0

            current_val = equity + position * close
            curve.append(current_val)

        return curve

    def run_backtest_via_module(
        self,
        symbol: str,
        days_back: int = 252,
        buy_threshold: float = 6.5,
        sell_threshold: float = 4.5,
    ) -> Dict[str, Any]:
        """Use the full backtest.backtest_strategy() pipeline with indicator scoring."""
        try:
            from backtest import backtest_strategy
            from indicators import build_history
            hist = build_history(symbol, period=f"{days_back}d")
            if hist is None or hist.empty:
                return {"error": "no_history", "symbol": symbol}
            return backtest_strategy(
                hist,
                buy_threshold=buy_threshold,
                sell_threshold=sell_threshold,
                initial_cash=10_000.0,
            )
        except Exception as e:
            return {"error": str(e), "symbol": symbol}


# ─────────────────────────────────────────────────────────────────────────────
# 5. METRICS ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class MetricsEngine:
    """
    Computes Sharpe ratio, max drawdown, win rate, and (when labels are
    available) classification metrics via evaluation_metrics.py.
    """

    def analyze(self, equity_curve: List[float]) -> Dict[str, Any]:
        if len(equity_curve) < 2:
            return {"error": "insufficient_data"}

        series  = pd.Series(equity_curve, dtype=float)
        returns = series.pct_change().dropna()

        std = returns.std()
        sharpe = float(returns.mean() / std * math.sqrt(252)) if std > 1e-9 else 0.0

        peak         = series.cummax()
        drawdown_abs = (peak - series).max()
        max_dd_pct   = float(drawdown_abs / peak.max() * 100) if peak.max() > 0 else 0.0

        win_rate = float((returns > 0).mean())

        total_return = float((series.iloc[-1] - series.iloc[0]) / series.iloc[0] * 100)

        return {
            "sharpe_ratio":    round(sharpe, 4),
            "max_drawdown_pct": round(max_dd_pct, 2),
            "win_rate":        round(win_rate, 4),
            "total_return_pct": round(total_return, 2),
            "n_periods":       len(equity_curve),
            "start_value":     round(equity_curve[0], 2),
            "end_value":       round(equity_curve[-1], 2),
        }

    def classification_metrics(
        self,
        y_true: List[int],
        y_pred_proba: List[float],
        threshold: float = 0.5,
    ) -> Dict[str, Any]:
        """Delegate to evaluation_metrics.py for AUC / precision / recall."""
        try:
            from evaluation_metrics import classification_metrics as _cm, brier_score
            import pandas as _pd
            yt = _pd.Series(y_true)
            yp = _pd.Series(y_pred_proba)
            result = _cm(yt, yp, threshold=threshold)
            result["brier_score"] = round(brier_score(yt, yp), 4)
            return result
        except Exception as e:
            return {"error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# 6. VALIDATION ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class ValidationEngine:
    """
    Time-aware train/test split + walk-forward validation.
    Wraps data_prep.simple_time_split() and data_prep.walk_forward_splits().
    """

    def split_data(self, df: pd.DataFrame, date_col: str = "timestamp") -> Dict[str, pd.DataFrame]:
        try:
            from data_prep import simple_time_split
            dc = date_col if date_col in df.columns else df.index.name or "timestamp"
            df2 = df.reset_index() if dc not in df.columns else df
            result = simple_time_split(df2, date_col=dc)
            return {
                "train": result.train,
                "validation": result.validation,
                "test": result.test,
            }
        except Exception as e:
            split = int(len(df) * 0.7)
            return {
                "train":      df.iloc[:split],
                "validation": df.iloc[split : split + int(len(df) * 0.15)],
                "test":       df.iloc[split + int(len(df) * 0.15):],
                "note":       str(e),
            }

    def walk_forward_test(
        self,
        df: pd.DataFrame,
        signal_engine: SignalEngine,
        step: int = 30,
    ) -> Dict[str, Any]:
        """
        Rolling-window walk-forward with expanding training window.

        For each test window [i : i+step]:
          1. Train signal_engine on the EXPANDING window df.iloc[:i] — all
             data available BEFORE the test window starts.  This recalibrates
             the signal thresholds to the regime seen in training data only,
             respecting the point-in-time constraint.
          2. Score each bar in the test window with the freshly-trained model.
          3. Record the per-window threshold params alongside scores so the
             caller can verify that parameters genuinely changed between windows
             (proof that a refit occurred, not just that scores differ).

        Returns mean signal score across all out-of-sample windows plus a
        per-window breakdown with calibrated thresholds.
        """
        scores: List[float]              = []
        per_window: List[Dict[str, Any]] = []
        n = len(df)

        for i in range(step * 2, n, step):
            train_window = df.iloc[:i]          # expanding: all data before test window
            test_window  = df.iloc[i:i + step]  # out-of-sample test window
            if test_window.empty:
                break

            train_info = signal_engine.train(train_window)

            window_scores = [signal_engine.generate_signal(test_window.iloc[j])
                             for j in range(len(test_window))]
            mean_score = float(np.mean(window_scores))
            scores.append(mean_score)
            per_window.append({
                "test_window_start": str(test_window.index[0])  if hasattr(test_window.index[0], '__str__') else i,
                "test_window_end":   str(test_window.index[-1]) if hasattr(test_window.index[-1], '__str__') else i + step - 1,
                "n_train_rows":      train_info.get("n_train_rows", len(train_window)),
                "momentum_threshold":   train_info.get("momentum_threshold"),
                "volume_z_threshold":   train_info.get("volume_z_threshold"),
                "volatility_threshold": train_info.get("volatility_threshold"),
                "mean_oos_score":    round(mean_score, 4),
            })

        if not scores:
            return {"error": "insufficient_data_for_walk_forward"}

        return {
            "windows":         len(scores),
            "mean_signal":     round(float(np.mean(scores)), 4),
            "std_signal":      round(float(np.std(scores)), 4),
            "min_signal":      round(float(min(scores)), 4),
            "max_signal":      round(float(max(scores)), 4),
            "consistency_pct": round(float(np.mean([s > 0.3 for s in scores])) * 100, 1),
            "per_window":      per_window,
        }


# ─────────────────────────────────────────────────────────────────────────────
# 7. AEIM_Level2 ORCHESTRATOR
# ─────────────────────────────────────────────────────────────────────────────

class AEIM_Level2:
    """
    Full Level 2 pipeline:
      fetch OHLCV → add features → score signals → backtest → metrics → validate
    All using real Polygon data from the live database.
    """

    def __init__(self):
        self.data     = MarketDataEngine()
        self.features = FeatureEngine()
        self.signal   = SignalEngine()
        self.backtest = BacktestEngine()
        self.metrics  = MetricsEngine()
        self.validate = ValidationEngine()

    def run(self, symbol: str, days_back: int = 200) -> Dict[str, Any]:
        symbol = symbol.upper().strip()
        logger.info(f"[AEIM_Level2] Starting pipeline for {symbol}")

        # 1. Data
        df = self.data.fetch_ohlcv(symbol, days_back=days_back)
        if df.empty or len(df) < 25:
            return {
                "symbol": symbol,
                "error":  "insufficient_data",
                "rows":   len(df),
                "note":   "Need ≥25 bars in polygon_market_daily for this symbol.",
            }

        # 2. Features
        df = self.features.add_features(df)
        ml_feats = self.features.build_ml_features(symbol, df)

        # 3. Signal scores
        df["signal_score"] = self.signal.score_series(df)
        high_signal_pct = round(float((df["signal_score"] > 0.7).mean() * 100), 1)

        # 4. Backtest
        equity_curve = self.backtest.run_backtest(df, self.signal)

        # 5. Metrics
        perf = self.metrics.analyze(equity_curve)

        # 6. Validation
        wf_result = self.validate.walk_forward_test(df, self.signal)

        return {
            "symbol":            symbol,
            "rows_used":         len(df),
            "days_back":         days_back,
            "high_signal_pct":   high_signal_pct,
            "latest_signal":     round(float(df["signal_score"].iloc[-1]), 4),
            "latest_momentum":   round(float(df["momentum"].iloc[-1]), 4) if "momentum" in df else None,
            "latest_volume_z":   round(float(df["volume_z"].iloc[-1]), 4) if "volume_z" in df else None,
            "latest_volatility": round(float(df["volatility"].iloc[-1]), 6) if "volatility" in df else None,
            "ml_features":       ml_feats,
            "performance":       perf,
            "walk_forward":      wf_result,
        }
