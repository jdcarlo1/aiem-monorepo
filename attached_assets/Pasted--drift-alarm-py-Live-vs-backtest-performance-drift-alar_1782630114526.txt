"""
drift_alarm.py
---------------------------
Live-vs-backtest performance drift alarm.

WHY THIS EXISTS
----------------
A backtest says a signal wins 62% of the time. Once it's live in
shadow_ledger.py's paper-trading mode, the real question is: is live
performance actually consistent with that number, or has it quietly
decayed (signal decay, regime shift, or — the uncomfortable possibility —
the backtest had a lookahead leak and was never really 62% to begin with)?

Nothing currently compares these two numbers with an actual statistical
test. This module does that: pulls live results from shadow_ledger.py's
shadow_performance(), compares against the backtest's claimed win rate
using Fisher's exact test (appropriate for the typically-small live
sample sizes you'll have early in a shadow window — a z-test's large-
sample assumption doesn't hold yet), and only raises an alarm when the
divergence is BOTH statistically significant AND practically large enough
to matter. This avoids the two failure modes: missing a real decay, and
crying wolf over noise from 15 shadow trades.

This deliberately does NOT auto-pull the backtest win rate from
hypothesis_registry's result JSONB — that schema's win_rate location
depends on what your adversarial_review call stored, and guessing the
wrong key silently would be worse than just asking for it explicitly. Pass
backtest_win_rate / backtest_n_trades from wherever you already have them
(you have them, since they were required arguments to adversarial_review
when the hypothesis was first registered).

INTEGRATION
-----------
A WARN or ALERT verdict from this module is exactly the kind of event that
should feed into kill_switch.py's check_kill_switch() and
decision_logger.py's audit trail — this module raises the flag, it
deliberately does not have the authority to halt anything itself (same
separation-of-concerns principle as decision_logger.py: this only reports).

REQUIRES: scipy (for fisher_exact)
"""

import datetime as dt
from typing import Dict, Any

from scipy.stats import fisher_exact

from shadow_ledger import shadow_performance


def compute_drift(signal_name: str, backtest_win_rate: float, backtest_n_trades: int,
                   min_live_trades: int = 15, significance_alpha: float = 0.05,
                   practical_threshold_pct: float = 0.10) -> Dict[str, Any]:
    """
    Compares live shadow performance against the backtest baseline.

    min_live_trades: below this, returns INSUFFICIENT_DATA rather than a
    verdict — too early to say anything meaningful yet, and a single
    Fisher's exact test on 5 trades would just be noise dressed up as a
    statistic.

    practical_threshold_pct: the live/backtest win-rate gap must exceed
    this (default 10 percentage points) AND be statistically significant
    to trigger ALERT. A statistically significant but tiny gap (e.g. 2
    percentage points with a huge backtest sample) isn't actionable.
    """
    live = shadow_performance(signal_name)

    if live["trades"] < min_live_trades:
        return {
            "signal_name": signal_name,
            "verdict": "INSUFFICIENT_DATA",
            "live_trades": live["trades"],
            "min_required": min_live_trades,
            "message": f"Only {live['trades']} live trades so far — need at least "
                       f"{min_live_trades} before a drift comparison means anything.",
        }

    live_wins = round(live["win_rate"] * live["trades"])
    live_losses = live["trades"] - live_wins
    backtest_wins = round(backtest_win_rate * backtest_n_trades)
    backtest_losses = backtest_n_trades - backtest_wins

    contingency_table = [
        [live_wins, live_losses],
        [backtest_wins, backtest_losses],
    ]
    odds_ratio, p_value = fisher_exact(contingency_table)

    gap = live["win_rate"] - backtest_win_rate
    statistically_significant = p_value < significance_alpha
    practically_significant = abs(gap) >= practical_threshold_pct

    if statistically_significant and practically_significant:
        verdict = "ALERT_UNDERPERFORMING" if gap < 0 else "ALERT_OUTPERFORMING"
        message = (
            f"{signal_name}: live win rate {live['win_rate']:.1%} ({live['trades']} trades) "
            f"vs backtest {backtest_win_rate:.1%} ({backtest_n_trades} trades) — "
            f"gap of {gap:+.1%} is both statistically significant (p={p_value:.4f}) "
            f"and practically large. "
            + ("Signal may be decaying, regime-dependent, or the original backtest "
               "may have had a leak — worth re-running through adversarial_review."
               if gap < 0 else
               "Outperforming is good, but also worth checking — an unexpectedly large "
               "positive gap can itself indicate a data issue rather than a pleasant surprise.")
        )
    elif statistically_significant and not practically_significant:
        verdict = "WATCH"
        message = (
            f"{signal_name}: gap of {gap:+.1%} is statistically significant "
            f"(p={p_value:.4f}) but small in magnitude — worth monitoring, not yet actionable."
        )
    else:
        verdict = "CONSISTENT"
        message = (
            f"{signal_name}: live win rate {live['win_rate']:.1%} is consistent with "
            f"backtest {backtest_win_rate:.1%} (p={p_value:.4f}, not significant)."
        )

    return {
        "signal_name": signal_name,
        "verdict": verdict,
        "message": message,
        "live_win_rate": live["win_rate"],
        "live_trades": live["trades"],
        "backtest_win_rate": backtest_win_rate,
        "backtest_n_trades": backtest_n_trades,
        "gap": round(gap, 4),
        "p_value": round(p_value, 4),
        "checked_at": dt.datetime.utcnow().isoformat(),
    }


def check_all_active_signals(signal_baselines: Dict[str, Dict[str, Any]],
                              min_live_trades: int = 15) -> Dict[str, Any]:
    """
    Convenience wrapper for running drift checks across every signal
    currently in a shadow window. signal_baselines: {signal_name:
    {"backtest_win_rate": float, "backtest_n_trades": int}, ...} — build
    this from your hypothesis_registry locked results before calling.

    Returns only signals worth your attention (anything other than
    CONSISTENT or INSUFFICIENT_DATA), so this is safe to run daily and
    only surfaces what actually needs a look.
    """
    results = []
    for signal_name, baseline in signal_baselines.items():
        result = compute_drift(
            signal_name,
            backtest_win_rate=baseline["backtest_win_rate"],
            backtest_n_trades=baseline["backtest_n_trades"],
            min_live_trades=min_live_trades,
        )
        if result["verdict"] not in ("CONSISTENT", "INSUFFICIENT_DATA"):
            results.append(result)

    return {
        "checked_at": dt.datetime.utcnow().isoformat(),
        "signals_needing_attention": results,
        "total_signals_checked": len(signal_baselines),
    }
