"""
date_utils.py - date-boundary-safe splitting helpers, shared by
calibration.py and walk_forward.py.

Why this exists: every generic time-split utility in this codebase
(data_prep.simple_time_split, data_prep.walk_forward_splits, and
sklearn.model_selection.TimeSeriesSplit as used inside model_training.py)
splits by ROW COUNT. That is safe when each row is its own time step, but
this dataset has many picks sharing the same trade_date (up to 30-50/day),
so a row-count boundary can - and empirically did, before this fix - cut a
single date in half between train and validation/test. That is a real
leakage bug: a model could see some of a date's outcomes at train time and
be "tested" on other outcomes from the exact same date.

These helpers guarantee every unique trade_date is entirely on one side of
any split. Kept local to this package rather than changing the shared
data_prep.py/model_training.py, which are used elsewhere and whose row-count
behavior may be fine for other, more truly time-series-shaped datasets.

Caveat this does NOT fix: model_training.train_model()'s internal
TimeSeriesSplit cross-validation (used for the printed cv_auc/is_trustworthy
figures) is still row-count based. With only 9-11 unique trade_dates in the
current dataset, those CV folds can still straddle a single date. Treat
train.py's cv_auc and "TRUSTWORTHY" label as "row-count sufficient,
date-count immature" - not yet a validated estimate - until more distinct
trading days accumulate.
"""
from dataclasses import dataclass
import pandas as pd


@dataclass
class DateSafeSplit:
    train: pd.DataFrame
    validation: pd.DataFrame
    test: pd.DataFrame
    train_dates: list
    val_dates: list
    test_dates: list


def date_safe_three_way_split(
    df: pd.DataFrame,
    date_col: str = "trade_date",
    train_frac: float = 0.6,
    val_frac: float = 0.2,
) -> DateSafeSplit:
    """
    Splits by UNIQUE DATE (not row count) into train/validation/test,
    chronologically. A date is always entirely on one side.
    """
    dates = sorted(df[date_col].unique())
    n = len(dates)
    train_end = max(1, int(n * train_frac))
    val_end = train_end + max(1, int(n * val_frac))
    val_end = min(val_end, n - 1) if n > train_end else train_end

    train_dates = dates[:train_end]
    val_dates = dates[train_end:val_end]
    test_dates = dates[val_end:]

    return DateSafeSplit(
        train=df[df[date_col].isin(train_dates)],
        validation=df[df[date_col].isin(val_dates)],
        test=df[df[date_col].isin(test_dates)],
        train_dates=train_dates,
        val_dates=val_dates,
        test_dates=test_dates,
    )


def date_safe_walk_forward_splits(df: pd.DataFrame, date_col: str,
                                   initial_train_days: int, val_window_days: int,
                                   step_days: int):
    """
    Expanding-window walk-forward, windowed by unique date instead of row
    count, for the same reason as date_safe_three_way_split above.
    """
    dates = sorted(df[date_col].unique())
    n_dates = len(dates)
    if n_dates < initial_train_days + val_window_days:
        return

    train_end_idx = initial_train_days
    while train_end_idx + val_window_days <= n_dates:
        train_dates = set(dates[:train_end_idx])
        val_dates = set(dates[train_end_idx:train_end_idx + val_window_days])
        yield df[df[date_col].isin(train_dates)], df[df[date_col].isin(val_dates)]
        train_end_idx += step_days


def assert_no_date_overlap(*date_lists) -> None:
    """Raises AssertionError if any two date groups share a date."""
    seen = set()
    for dates in date_lists:
        s = set(dates)
        overlap = seen & s
        if overlap:
            raise AssertionError(f"date split overlap detected: {overlap}")
        seen |= s
