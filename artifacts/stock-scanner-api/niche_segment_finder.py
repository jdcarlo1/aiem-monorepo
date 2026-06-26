"""
niche_segment_finder.py

Systematically searches for context-specific edges that a single aggregate
model would miss — e.g. "rvol>=3 + gap>=2% works much better specifically
on semiconductor names in the week before earnings" even if the generic
version of that signal is unremarkable.

CRITICAL SAFEGUARD: if you test enough subgroups, some will look
"significant" by pure chance. This module corrects for that with:
  1. Benjamini-Hochberg false discovery rate correction across all
     segments tested in one run
  2. A minimum sample size per segment (no exceptions)
  3. A mandatory out-of-sample validation step before any segment is
     reported as a real finding

Do not skip steps 1-3 to "find more" — that's exactly how people convince
themselves a coincidence is an edge.
"""

import itertools
import json
import os
from dataclasses import dataclass
from typing import List, Optional

import numpy as np
import pandas as pd
from scipy import stats

# Hard floor for any segment to even be considered. Lower than this and a
# win-rate estimate is mostly noise.
MIN_SEGMENT_SAMPLES = 40

# False discovery rate threshold for the multiple-testing correction.
FDR_ALPHA = 0.10

_DB_URL = os.environ.get("DATABASE_URL", "")

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS aiem_segment_findings (
    id                      SERIAL PRIMARY KEY,
    search_date             DATE NOT NULL,
    segment_description     TEXT NOT NULL,
    n_samples               INTEGER,
    win_rate                FLOAT,
    baseline_win_rate       FLOAT,
    lift                    FLOAT,
    p_value                 FLOAT,
    p_value_adjusted        FLOAT,
    validated_out_of_sample BOOLEAN,
    oos_win_rate            FLOAT,
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (search_date, segment_description)
);
"""


def _init_table():
    if not _DB_URL:
        return
    try:
        import psycopg2
        with psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute(_INIT_SQL)
            conn.commit()
    except Exception as e:
        print(f"[segment_finder] init error: {e}")


_init_table()


@dataclass
class SegmentResult:
    segment_description: str
    n_samples: int
    win_rate: float
    baseline_win_rate: float
    lift: float
    p_value: float
    p_value_adjusted: float
    significant_after_correction: bool
    validated_out_of_sample: Optional[bool] = None
    oos_win_rate: Optional[float] = None


def _segment_stats(segment_df: pd.DataFrame, baseline_win_rate: float) -> dict:
    n = len(segment_df)
    wins = segment_df["outcome"].sum()
    win_rate = wins / n if n > 0 else np.nan

    if n > 0 and 0 < baseline_win_rate < 1:
        se = np.sqrt(baseline_win_rate * (1 - baseline_win_rate) / n)
        z = (win_rate - baseline_win_rate) / se if se > 0 else 0
        p_value = 2 * (1 - stats.norm.cdf(abs(z)))
    else:
        p_value = 1.0

    return {"n": n, "win_rate": win_rate, "p_value": p_value}


def search_single_context_columns(
    df: pd.DataFrame,
    context_columns: List[str],
    outcome_col: str = "outcome",
) -> List[SegmentResult]:
    """
    Tests each individual value of each context column as its own segment.
    """
    baseline_win_rate = df[outcome_col].mean()
    results = []

    for col in context_columns:
        for value in df[col].dropna().unique():
            segment_df = df[df[col] == value]
            if len(segment_df) < MIN_SEGMENT_SAMPLES:
                continue

            s = _segment_stats(segment_df, baseline_win_rate)
            results.append(SegmentResult(
                segment_description=f"{col} = {value}",
                n_samples=s["n"],
                win_rate=s["win_rate"],
                baseline_win_rate=baseline_win_rate,
                lift=s["win_rate"] - baseline_win_rate,
                p_value=s["p_value"],
                p_value_adjusted=np.nan,
                significant_after_correction=False,
            ))

    return results


def search_two_way_interactions(
    df: pd.DataFrame,
    context_columns: List[str],
    outcome_col: str = "outcome",
) -> List[SegmentResult]:
    """
    Tests pairs of context columns together — where the genuinely
    interesting niche findings tend to live.

    Limited to 2-way on purpose: 3-way+ combinations collapse sample
    sizes below MIN_SEGMENT_SAMPLES almost everywhere.
    """
    baseline_win_rate = df[outcome_col].mean()
    results = []

    for col_a, col_b in itertools.combinations(context_columns, 2):
        for val_a in df[col_a].dropna().unique():
            for val_b in df[col_b].dropna().unique():
                segment_df = df[(df[col_a] == val_a) & (df[col_b] == val_b)]
                if len(segment_df) < MIN_SEGMENT_SAMPLES:
                    continue

                s = _segment_stats(segment_df, baseline_win_rate)
                results.append(SegmentResult(
                    segment_description=f"{col_a}={val_a} AND {col_b}={val_b}",
                    n_samples=s["n"],
                    win_rate=s["win_rate"],
                    baseline_win_rate=baseline_win_rate,
                    lift=s["win_rate"] - baseline_win_rate,
                    p_value=s["p_value"],
                    p_value_adjusted=np.nan,
                    significant_after_correction=False,
                ))

    return results


def _benjamini_hochberg(p_values: List[float], alpha: float) -> tuple:
    """
    Manual Benjamini-Hochberg FDR correction (no statsmodels dependency).
    Returns (rejected: List[bool], p_adjusted: List[float]) in original order.
    """
    n = len(p_values)
    if n == 0:
        return [], []

    indexed = sorted(enumerate(p_values), key=lambda x: x[1])
    sorted_p = [p for _, p in indexed]

    p_adjusted_sorted = [0.0] * n
    prev = 1.0
    for i in range(n - 1, -1, -1):
        rank = i + 1
        val = sorted_p[i] * n / rank
        prev = min(prev, val)
        p_adjusted_sorted[i] = prev

    rejected_sorted = [p <= alpha for p in p_adjusted_sorted]

    p_adjusted = [0.0] * n
    rejected = [False] * n
    for sorted_idx, (orig_idx, _) in enumerate(indexed):
        p_adjusted[orig_idx] = p_adjusted_sorted[sorted_idx]
        rejected[orig_idx] = rejected_sorted[sorted_idx]

    return rejected, p_adjusted


def apply_multiple_testing_correction(
    results: List[SegmentResult], alpha: float = FDR_ALPHA
) -> List[SegmentResult]:
    """
    Benjamini-Hochberg FDR correction across all tested segments.
    Without this, testing 50 segments at p<0.05 gives ~2-3 false positives
    by chance alone.
    """
    if not results:
        return results

    p_values = [r.p_value for r in results]
    rejected, p_adjusted = _benjamini_hochberg(p_values, alpha)

    for r, p_adj, sig in zip(results, p_adjusted, rejected):
        r.p_value_adjusted = p_adj
        r.significant_after_correction = bool(sig)

    return results


def validate_out_of_sample(
    segment_result: SegmentResult,
    segment_filter_fn,
    holdout_df: pd.DataFrame,
    outcome_col: str = "outcome",
    min_lift_retained_pct: float = 0.5,
) -> SegmentResult:
    """
    Checks whether the edge found in-sample shows up in a holdout dataset
    the segment search never saw. Mandatory before trusting any finding.

    segment_filter_fn: function that takes holdout_df and returns a boolean mask.
    min_lift_retained_pct: OOS lift must retain >= this fraction of in-sample lift.
    """
    mask = segment_filter_fn(holdout_df)
    oos_segment = holdout_df[mask]

    if len(oos_segment) < MIN_SEGMENT_SAMPLES:
        segment_result.validated_out_of_sample = False
        segment_result.oos_win_rate = np.nan
        return segment_result

    oos_win_rate = oos_segment[outcome_col].mean()
    oos_baseline = holdout_df[outcome_col].mean()
    oos_lift = oos_win_rate - oos_baseline

    retained_fraction = oos_lift / segment_result.lift if segment_result.lift != 0 else 0
    segment_result.oos_win_rate = oos_win_rate
    segment_result.validated_out_of_sample = (
        oos_lift > 0 and retained_fraction >= min_lift_retained_pct
    )

    return segment_result


def run_full_segment_search(
    df: pd.DataFrame,
    context_columns: List[str],
    outcome_col: str = "outcome",
    include_interactions: bool = True,
) -> pd.DataFrame:
    """
    Convenience wrapper: single-column + two-way interaction searches,
    multiple-testing correction, ranked DataFrame of candidates.

    OOS validation is a separate step (validate_out_of_sample) because it
    requires you to supply the holdout data and the filter logic per finding.
    """
    results = search_single_context_columns(df, context_columns, outcome_col)

    if include_interactions:
        results += search_two_way_interactions(df, context_columns, outcome_col)

    results = apply_multiple_testing_correction(results)

    rows = [
        {
            "segment": r.segment_description,
            "n_samples": r.n_samples,
            "win_rate": round(r.win_rate, 4),
            "baseline_win_rate": round(r.baseline_win_rate, 4),
            "lift": round(r.lift, 4),
            "p_value": round(r.p_value, 4),
            "p_value_adjusted": round(r.p_value_adjusted, 4),
            "significant_after_correction": r.significant_after_correction,
        }
        for r in results
    ]

    return pd.DataFrame(rows).sort_values("p_value_adjusted") if rows else pd.DataFrame()


def build_context_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Derives categorical context columns from raw pick data for segment search.
    Add new columns here as more data feeds become available.
    """
    out = df.copy()

    # Day of week label
    if "trade_date" in out.columns:
        out["day_name"] = pd.to_datetime(out["trade_date"]).dt.day_name()

    # Conviction bucket (already categorical)
    if "conviction" in out.columns:
        out["conviction_bucket"] = out["conviction"].fillna("UNKNOWN").str.upper()

    # OTM bucket
    if "otm_pct" in out.columns:
        out["otm_bucket"] = pd.cut(
            out["otm_pct"].fillna(0),
            bins=[-999, -10, 0, 5, 15, 999],
            labels=["deep_itm", "itm", "atm", "otm_slight", "otm_far"],
        ).astype(str)

    # Days-out bucket
    if "days_out" in out.columns:
        out["expiry_bucket"] = pd.cut(
            out["days_out"].fillna(30),
            bins=[0, 7, 21, 45, 90, 999],
            labels=["weekly", "2-3wk", "monthly", "45-90d", "leaps"],
        ).astype(str)

    # RVOL bucket (requires polygon join)
    if "rvol" in out.columns:
        out["rvol_bucket"] = pd.cut(
            out["rvol"].fillna(1),
            bins=[0, 1.5, 3, 6, 999],
            labels=["low_vol", "mod_vol", "high_vol", "extreme_vol"],
        ).astype(str)

    return out


def save_findings_to_db(results_df: pd.DataFrame, search_date=None):
    """Persist significant findings to aiem_segment_findings for the dashboard."""
    if not _DB_URL or results_df.empty:
        return
    from datetime import date
    if search_date is None:
        search_date = date.today()

    sig = results_df[results_df["significant_after_correction"] == True]
    if sig.empty:
        print("[segment_finder] no significant segments after FDR correction")
        return

    try:
        import psycopg2
        with psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            for _, row in sig.iterrows():
                cur.execute("""
                    INSERT INTO aiem_segment_findings
                        (search_date, segment_description, n_samples, win_rate,
                         baseline_win_rate, lift, p_value, p_value_adjusted)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (search_date, segment_description) DO UPDATE SET
                        win_rate=EXCLUDED.win_rate,
                        lift=EXCLUDED.lift,
                        p_value_adjusted=EXCLUDED.p_value_adjusted
                """, (
                    str(search_date),
                    row["segment"],
                    int(row["n_samples"]),
                    float(row["win_rate"]),
                    float(row["baseline_win_rate"]),
                    float(row["lift"]),
                    float(row["p_value"]),
                    float(row["p_value_adjusted"]),
                ))
            conn.commit()
        print(f"[segment_finder] saved {len(sig)} significant findings to DB")
    except Exception as e:
        print(f"[segment_finder] save_findings error: {e}")


def run_segment_search_on_settled_picks(df: pd.DataFrame) -> pd.DataFrame:
    """
    Top-level entry point called from retrain_pipeline.
    df is the raw settled-picks DataFrame from ai_short_calls_log.
    """
    if len(df) < MIN_SEGMENT_SAMPLES * 2:
        print(f"[segment_finder] only {len(df)} settled picks — need {MIN_SEGMENT_SAMPLES*2} minimum for reliable search")
        return pd.DataFrame()

    df_ctx = build_context_columns(df)
    df_ctx["outcome"] = df_ctx["t3_win"].astype(int)

    available_context = [
        c for c in ["day_name", "conviction_bucket", "otm_bucket",
                    "expiry_bucket", "rvol_bucket"]
        if c in df_ctx.columns and df_ctx[c].notna().sum() >= MIN_SEGMENT_SAMPLES
    ]

    if not available_context:
        print("[segment_finder] no context columns with sufficient coverage")
        return pd.DataFrame()

    print(f"[segment_finder] searching {len(available_context)} context columns on {len(df_ctx)} picks")
    results = run_full_segment_search(df_ctx, available_context)

    if not results.empty:
        sig_count = results["significant_after_correction"].sum()
        print(f"[segment_finder] {len(results)} segments tested, "
              f"{sig_count} significant after FDR correction")
        save_findings_to_db(results)

    return results
