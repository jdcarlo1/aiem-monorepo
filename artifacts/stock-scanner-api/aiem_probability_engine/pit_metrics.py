"""
pit_metrics.py - honest before/after accuracy comparison for the
2026-07-02 PIT leakage fix (see config.py "LEAKAGE GAP", reports.py, and
pit_correction.py for the bug and the fix itself).

Compares THREE groups per horizon, all measured with the exact same
metrics (evaluation_metrics.py at the stock-scanner-api root, reused here
rather than reinvented):

  1. CONTAMINATED - the original leaked rows, scored with their ORIGINAL
     (pre-fix) prob_up_Nd values. This is "what the bug made us believe."
  2. CORRECTED    - those same rows' EMBARGO-RETRAINED prob_up_Nd values
     from pit_correction.py, for whichever horizons cleared that script's
     floor (most will not - see pit_correction.py docstring for why).
     This is "what an honest PIT-safe rescore says," on a smaller,
     disclosed n - never padded to match the contaminated group's size.
  3. GENUINE      - rows logged AFTER the fix with pit_status='pit_safe',
     i.e. real forward predictions that were never contaminated in the
     first place. This is the system's actual ongoing accuracy.

All three groups require outcome_label_{h}d IS NOT NULL for that horizon
(it must have already settled) - unsettled rows are excluded everywhere,
never counted as a win, a loss, or folded into an average as zero.

Run directly:
    python pit_metrics.py
"""
import os
import sys

import pandas as pd
import psycopg2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DB_URL, HORIZONS
from evaluation_metrics import (
    classification_metrics,
    calibration_curve_table,
    brier_score,
    precision_at_confidence_threshold,
)

PRED_TABLE = "aiem_probability_engine_predictions"
CORR_TABLE = "aiem_probability_engine_pit_corrections"


def _fetch_contaminated() -> pd.DataFrame:
    sql = f"""
        SELECT id, signal_date, ticker,
               prob_up_1d, prob_up_2d, prob_up_3d, prob_up_4d,
               outcome_label_1d, outcome_label_2d, outcome_label_3d, outcome_label_4d
        FROM {PRED_TABLE}
        WHERE pit_status = 'leaked'
    """
    with psycopg2.connect(DB_URL) as conn:
        return pd.read_sql_query(sql, conn)


def _fetch_corrected() -> pd.DataFrame:
    sql = f"""
        SELECT c.original_prediction_id AS id, c.signal_date, c.ticker,
               c.correction_status,
               c.corrected_prob_up_1d AS prob_up_1d,
               c.corrected_prob_up_2d AS prob_up_2d,
               c.corrected_prob_up_3d AS prob_up_3d,
               c.corrected_prob_up_4d AS prob_up_4d,
               p.outcome_label_1d, p.outcome_label_2d, p.outcome_label_3d, p.outcome_label_4d
        FROM {CORR_TABLE} c
        JOIN {PRED_TABLE} p ON p.id = c.original_prediction_id
    """
    with psycopg2.connect(DB_URL) as conn:
        return pd.read_sql_query(sql, conn)


def _fetch_genuine() -> pd.DataFrame:
    sql = f"""
        SELECT id, signal_date, ticker,
               prob_up_1d, prob_up_2d, prob_up_3d, prob_up_4d,
               outcome_label_1d, outcome_label_2d, outcome_label_3d, outcome_label_4d
        FROM {PRED_TABLE}
        WHERE pit_status = 'pit_safe'
    """
    with psycopg2.connect(DB_URL) as conn:
        return pd.read_sql_query(sql, conn)


def _report_for_group(df: pd.DataFrame, group_name: str) -> dict:
    out = {"group": group_name, "n_rows_total": len(df), "per_horizon": {}}
    for h in HORIZONS:
        prob_col, label_col = f"prob_up_{h}d", f"outcome_label_{h}d"
        if prob_col not in df.columns or label_col not in df.columns:
            out["per_horizon"][h] = {"n_settled": 0, "note": "column not present in this group"}
            continue
        settled = df.dropna(subset=[prob_col, label_col])
        n = len(settled)
        if n == 0:
            out["per_horizon"][h] = {
                "n_settled": 0,
                "note": "no rows with both a settled outcome and a non-null score for this horizon",
            }
            continue
        y_true = settled[label_col].astype(int)
        y_pred = settled[prob_col].astype(float)
        metrics = classification_metrics(y_true, y_pred)
        out["per_horizon"][h] = {
            "n_settled": n,
            "actual_win_rate": float(y_true.mean()),
            "auc": metrics["auc"],
            "precision": metrics["precision"],
            "recall": metrics["recall"],
            "brier_score": brier_score(y_true, y_pred),
            "calibration_table": calibration_curve_table(y_true, y_pred).to_dict(orient="records"),
            "precision_at_threshold": precision_at_confidence_threshold(y_true, y_pred).to_dict(orient="records"),
        }
    return out


def _print_report(report: dict) -> None:
    print(f"\n=== {report['group']} (n_rows_total={report['n_rows_total']}) ===")
    for h, m in report["per_horizon"].items():
        if m.get("n_settled", 0) == 0:
            print(f"  {h}d: {m['note']}")
            continue
        print(f"  {h}d: n_settled={m['n_settled']:4d}  win_rate={m['actual_win_rate']:.3f}  "
              f"auc={m['auc']:.3f}  precision={m['precision']:.3f}  recall={m['recall']:.3f}  "
              f"brier={m['brier_score']:.4f}")


def run_pit_metrics() -> dict:
    contaminated_df = _fetch_contaminated()
    corrected_df = _fetch_corrected()
    genuine_df = _fetch_genuine()

    contaminated = _report_for_group(contaminated_df, "CONTAMINATED (leaked, original scores)")
    corrected = _report_for_group(corrected_df, "CORRECTED (embargo-retrained, same rows)")
    genuine = _report_for_group(genuine_df, "GENUINE (post-fix pit_safe rows)")

    status_counts = (
        corrected_df["correction_status"].value_counts().to_dict()
        if not corrected_df.empty and "correction_status" in corrected_df.columns
        else {}
    )

    print("=" * 78)
    print("PIT LEAKAGE FIX: honest before/after comparison")
    print("=" * 78)
    _print_report(contaminated)
    if status_counts:
        print(f"\n  [pit_correction.py coverage across ALL leaked rows, not just settled "
              f"ones] {status_counts}")
        print(f"  (run pit_correction.py first if this looks stale/empty; "
              f"'uncorrectable' rows are excluded from CORRECTED above by construction)")
    _print_report(corrected)
    _print_report(genuine)

    print("\n" + "=" * 78)
    print("READ THIS BEFORE TRUSTING ANY NUMBER ABOVE:")
    print("  - CONTAMINATED win-rate/AUC reflect a model that had already seen")
    print("    outcome-adjacent future data for those signal_dates - they are")
    print("    optimistic/inflated by construction, not a real accuracy estimate.")
    print("  - CORRECTED numbers are PIT-safe but on a SMALL, degraded n - most")
    print("    of the leaked rows fall below pit_correction.py's floor (too few")
    print("    prior trade_dates this early in the dataset's history) and are")
    print("    excluded here as 'uncorrectable', never folded in as zero or guessed.")
    print("  - GENUINE is the only group that was never contaminated, but it only")
    print("    covers rows logged since the 2026-07-02 fix, so n_settled will be")
    print("    small for now and will grow daily as more horizons settle.")
    print("=" * 78)

    return {
        "contaminated": contaminated,
        "corrected": corrected,
        "genuine": genuine,
        "correction_status_counts": status_counts,
    }


if __name__ == "__main__":
    run_pit_metrics()
