"""Train/test split and time-ordered cross-validation folds, both by event.

* Test set: the last TEST_EVENTS events (Oct 13 - Nov 17, 2026; ~14% of games). It is
  scored once, at the end, for the final comparison. Nothing is tuned on it.
* Training set: all earlier events. Feature selection and tuning use expanding-window
  folds: train on events 1..k, validate on the next FOLD_SIZE events.

Splitting by whole events (never within one) keeps rounds of the same evening apart, and
ordering by time means every validation or test game is later than all training games.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
FEATURES_CSV = ROOT / "data" / "processed" / "features.csv"

TEST_EVENTS = 6
MIN_TRAIN_EVENTS = 20
FOLD_SIZE = 4


def load() -> pd.DataFrame:
    return pd.read_csv(FEATURES_CSV)


def event_order(df: pd.DataFrame) -> list[str]:
    return df.groupby("event_id")["start_time"].min().sort_values().index.tolist()


def train_test(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    events = event_order(df)
    test_events = set(events[-TEST_EVENTS:])
    is_test = df["event_id"].isin(test_events)
    return df[~is_test].reset_index(drop=True), df[is_test].reset_index(drop=True)


def cv_folds(train: pd.DataFrame):
    """Yield (fold_no, fit_df, val_df) with expanding training windows."""
    events = event_order(train)
    for i, k in enumerate(range(MIN_TRAIN_EVENTS, len(events), FOLD_SIZE), start=1):
        fit_ev, val_ev = set(events[:k]), set(events[k:k + FOLD_SIZE])
        yield (i, train[train["event_id"].isin(fit_ev)].reset_index(drop=True),
               train[train["event_id"].isin(val_ev)].reset_index(drop=True))
