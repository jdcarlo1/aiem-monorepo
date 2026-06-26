"""
data_prep.py

Time-aware splitting for financial data. Never use a random shuffle split
on time-series data — it leaks future information into training and makes
accuracy numbers look better than they actually are.
"""

from dataclasses import dataclass
from typing import Iterator, Tuple
import pandas as pd


@dataclass
class SplitResult:
    train: pd.DataFrame
    validation: pd.DataFrame
    test: pd.DataFrame


def simple_time_split(
    df: pd.DataFrame,
    date_col: str = "signal_date",
    train_frac: float = 0.6,
    val_frac: float = 0.2,
) -> SplitResult:
    """
    Sorts by date and splits into train / validation / test chronologically.
    train_frac + val_frac must be < 1.0; the remainder is test.
    """
    df_sorted = df.sort_values(date_col).reset_index(drop=True)
    n = len(df_sorted)
    train_end = int(n * train_frac)
    val_end = train_end + int(n * val_frac)

    return SplitResult(
        train=df_sorted.iloc[:train_end],
        validation=df_sorted.iloc[train_end:val_end],
        test=df_sorted.iloc[val_end:],
    )


def walk_forward_splits(
    df: pd.DataFrame,
    date_col: str = "signal_date",
    initial_train_size: int = 100,
    val_window_size: int = 25,
    step_size: int = 25,
) -> Iterator[Tuple[pd.DataFrame, pd.DataFrame]]:
    """
    Generates (train, validation) pairs with an expanding training window.
    Each validation window is strictly after its training window's dates.
    """
    df_sorted = df.sort_values(date_col).reset_index(drop=True)
    n = len(df_sorted)

    if n < initial_train_size + val_window_size:
        return

    train_end = initial_train_size
    while train_end + val_window_size <= n:
        train = df_sorted.iloc[:train_end]
        validation = df_sorted.iloc[train_end:train_end + val_window_size]
        yield train, validation
        train_end += step_size
