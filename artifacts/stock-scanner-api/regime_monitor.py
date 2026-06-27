"""
regime_monitor.py
-------------------
Detects when the market regime a signal was trained on has likely shifted.
Raises FLAGS for human review — never automatically disables a signal.
"""

import os
import json
import datetime as dt
from typing import Dict, Any, List, Optional

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras


DDL = """
CREATE TABLE IF NOT EXISTS regime_flags (
    id SERIAL PRIMARY KEY,
    signal_name TEXT NOT NULL,
    flagged_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    flag_type TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'critical')),
    details JSONB,
    acknowledged BOOLEAN NOT NULL DEFAULT FALSE,
    acknowledged_at TIMESTAMPTZ,
    acknowledged_by TEXT
);
"""


def _connect():
    url = os.environ.get("AIEM_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("No database URL found (set AIEM_DATABASE_URL or DATABASE_URL).")
    return psycopg2.connect(url)


def init_schema():
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(DDL)
        conn.commit()
    print("[regime_monitor] schema ready")


def _log_flag(signal_name: str, flag_type: str, severity: str, details: Dict[str, Any]):
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO regime_flags (signal_name, flag_type, severity, details)
                VALUES (%s, %s, %s, %s)
                """,
                (signal_name, flag_type, severity, json.dumps(details)),
            )
        conn.commit()


def check_volatility_regime(
    signal_name: str,
    price_history: pd.DataFrame,
    lookback_days: int = 252,
    recent_days: int = 20,
    z_threshold: float = 2.0,
) -> Optional[Dict[str, Any]]:
    df = price_history.sort_values("date").copy()
    df["log_ret"] = np.log(df["close"] / df["close"].shift(1))

    hist   = df["log_ret"].iloc[-(lookback_days + recent_days):-recent_days]
    recent = df["log_ret"].iloc[-recent_days:]

    if len(hist) < 30 or len(recent) < 5:
        return None

    hist_vol   = hist.std() * np.sqrt(252)
    recent_vol = recent.std() * np.sqrt(252)
    vol_of_vol = hist.rolling(recent_days).std().std() * np.sqrt(252)

    if vol_of_vol == 0 or np.isnan(vol_of_vol):
        return None

    z = (recent_vol - hist_vol) / vol_of_vol

    if abs(z) >= z_threshold:
        severity = "critical" if abs(z) >= 3 else "warning"
        details = {
            "historical_annualized_vol": round(float(hist_vol), 4),
            "recent_annualized_vol":     round(float(recent_vol), 4),
            "z_score":                   round(float(z), 2),
            "interpretation": (
                "Recent vol unusually HIGH vs training regime — signal may be over-firing."
                if z > 0 else
                "Recent vol unusually LOW vs training regime — signal edge may be muted."
            ),
        }
        _log_flag(signal_name, "volatility_regime", severity, details)
        return details
    return None


def check_correlation_decay(
    signal_name: str,
    signal_scores: pd.Series,
    forward_returns: pd.Series,
    window: int = 60,
    min_periods: int = 20,
    decay_threshold: float = 0.5,
) -> Optional[Dict[str, Any]]:
    df = pd.DataFrame({"score": signal_scores, "fwd_ret": forward_returns}).dropna()
    if len(df) < (window + min_periods):
        return None

    baseline_corr = df["score"].iloc[:window].corr(df["fwd_ret"].iloc[:window])
    recent_corr   = df["score"].iloc[-window:].corr(df["fwd_ret"].iloc[-window:])

    if baseline_corr is None or np.isnan(baseline_corr) or abs(baseline_corr) < 1e-6:
        return None

    decay_ratio = 1 - (recent_corr / baseline_corr) if baseline_corr != 0 else None
    if decay_ratio is not None and decay_ratio >= decay_threshold:
        severity = "critical" if decay_ratio >= 0.8 else "warning"
        details = {
            "baseline_correlation": round(float(baseline_corr), 4),
            "recent_correlation":   round(float(recent_corr), 4),
            "decay_ratio":          round(float(decay_ratio), 4),
            "interpretation": (
                f"Signal's predictive correlation decayed by "
                f"{round(decay_ratio*100,1)}% vs baseline. "
                f"Consider reducing conviction weight pending review."
            ),
        }
        _log_flag(signal_name, "correlation_decay", severity, details)
        return details
    return None


def check_trend_regime(
    signal_name: str,
    price_history: pd.DataFrame,
    short_window: int = 20,
    long_window: int = 100,
) -> Optional[Dict[str, Any]]:
    df = price_history.sort_values("date").copy()
    df["sma_short"] = df["close"].rolling(short_window).mean()
    df["sma_long"]  = df["close"].rolling(long_window).mean()
    df["spread"]    = df["sma_short"] - df["sma_long"]
    df = df.dropna()

    if len(df) < long_window + 10:
        return None

    recent_sign   = np.sign(df["spread"].iloc[-1])
    baseline_sign = np.sign(df["spread"].iloc[-(long_window + 10):-long_window].mean())

    if recent_sign != 0 and baseline_sign != 0 and recent_sign != baseline_sign:
        details = {
            "baseline_trend": "up" if baseline_sign > 0 else "down",
            "recent_trend":   "up" if recent_sign > 0 else "down",
            "interpretation": (
                "Trend direction flipped vs likely training period. "
                "Signals trained in one regime often underperform after a flip — "
                "flagged for review, not auto-disabled."
            ),
        }
        _log_flag(signal_name, "trend_regime", "warning", details)
        return details
    return None


def run_all_regime_checks(
    signal_name: str,
    price_history: pd.DataFrame,
) -> Dict[str, Any]:
    """Convenience: run all three checks and return a combined summary."""
    vol_flag   = check_volatility_regime(signal_name, price_history)
    trend_flag = check_trend_regime(signal_name, price_history)
    flags = []
    if vol_flag:   flags.append({"type": "volatility_regime",  **vol_flag})
    if trend_flag: flags.append({"type": "trend_regime",       **trend_flag})
    return {
        "signal_name":  signal_name,
        "checks_run":   ["volatility_regime", "trend_regime"],
        "flags_raised": len(flags),
        "flags":        flags,
        "recommendation": (
            "One or more regime flags raised — review before updating conviction weights."
            if flags else "No regime flags — signal operating within trained parameters."
        ),
    }


def get_open_flags(signal_name: Optional[str] = None) -> List[Dict[str, Any]]:
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if signal_name:
                cur.execute(
                    "SELECT * FROM regime_flags WHERE signal_name=%s AND acknowledged=FALSE ORDER BY flagged_at DESC",
                    (signal_name,),
                )
            else:
                cur.execute(
                    "SELECT * FROM regime_flags WHERE acknowledged=FALSE ORDER BY flagged_at DESC LIMIT 50"
                )
            rows = []
            for r in cur.fetchall():
                d = dict(r)
                if d.get("flagged_at"):      d["flagged_at"]      = d["flagged_at"].isoformat()
                if d.get("acknowledged_at"): d["acknowledged_at"] = d["acknowledged_at"].isoformat()
                rows.append(d)
            return rows


def acknowledge_flag(flag_id: int, acknowledged_by: str):
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE regime_flags
                SET acknowledged=TRUE, acknowledged_at=now(), acknowledged_by=%s
                WHERE id=%s
                """,
                (acknowledged_by, flag_id),
            )
        conn.commit()


if __name__ == "__main__":
    init_schema()
    print("regime_monitor schema ready.")
