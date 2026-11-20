"""Elo baseline, scored on the time-ordered CV folds of the training set.

The Elo formula gives White's expected score E = P(white) + 0.5 * P(draw), one number.
To get three class probabilities, the only thing learned is the draw rate of the fold's
training events:

    P(draw)  = d
    P(white) = E - d/2
    P(black) = 1 - E - d/2        (clipped to [eps, 1] and renormalised)

The class-frequency model (always predict the training shares) is reported as the floor.
The test set is not scored here; all models are scored on it once, at the end.

Run:  python -m src.baseline
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .split import cv_folds, load, train_test

CLASSES = ["white", "draw", "black"]
EPS = 1e-6
RESULTS = Path(__file__).resolve().parents[1] / "results"


def elo_probs(elo_expected: np.ndarray, draw_rate: float) -> np.ndarray:
    e = np.asarray(elo_expected, dtype=float)
    p = np.column_stack([e - draw_rate / 2, np.full_like(e, draw_rate), 1 - e - draw_rate / 2])
    p = np.clip(p, EPS, 1)
    return p / p.sum(axis=1, keepdims=True)


def metrics(y: pd.Series, p: np.ndarray) -> dict:
    idx = y.map({c: i for i, c in enumerate(CLASSES)}).to_numpy()
    onehot = np.eye(len(CLASSES))[idx]
    return {"log_loss": float(-np.log(p[np.arange(len(idx)), idx]).mean()),
            "brier": float(((p - onehot) ** 2).sum(axis=1).mean()),
            "accuracy": float((p.argmax(axis=1) == idx).mean()),
            "n": int(len(idx))}


def class_freq_probs(fit: pd.DataFrame, n: int) -> np.ndarray:
    share = fit["outcome"].value_counts(normalize=True).reindex(CLASSES).to_numpy()
    return np.tile(share, (n, 1))


def main() -> None:
    df = load()
    train, test = train_test(df)
    rows = []
    for fold, fit, val in cv_folds(train):
        d = float((fit["outcome"] == "draw").mean())
        for name, p in [("class_frequency", class_freq_probs(fit, len(val))),
                        ("elo", elo_probs(val["elo_expected"], d))]:
            rows.append({"fold": fold, "model": name, "fit_events": fit["event_id"].nunique(),
                         "val_events": val["event_id"].nunique(), **metrics(val["outcome"], p)})
    res = pd.DataFrame(rows)

    # Calibration of the Elo expected score on the training set (where it can't leak):
    # actual White score vs E, by bins of E.
    s = train["outcome"].map({"white": 1.0, "draw": 0.5, "black": 0.0})
    bins = pd.cut(train["elo_expected"], np.linspace(0, 1, 11))
    calib = (pd.DataFrame({"E": train["elo_expected"], "score": s, "draw": train["outcome"] == "draw"})
             .groupby(bins, observed=True).agg(games=("E", "size"), mean_E=("E", "mean"),
                                               actual_score=("score", "mean"), draw_rate=("draw", "mean")))

    summary = res.groupby("model")[["log_loss", "brier", "accuracy"]].mean()
    print(f"train: {train['event_id'].nunique()} events, {len(train)} games | "
          f"test (held out): {test['event_id'].nunique()} events, {len(test)} games\n")
    print(res.round(4).to_string(index=False), "\n")
    print("mean over folds\n", summary.round(4).to_string(), "\n")
    print("Elo calibration on training games\n", calib.round(3).to_string())

    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "baseline_cv.json").write_text(json.dumps({
        "train_games": len(train), "test_games": len(test),
        "folds": rows, "mean": summary.round(5).to_dict(orient="index"),
        "elo_calibration": calib.reset_index().astype({"elo_expected": str}).round(4).to_dict(orient="records"),
    }, indent=1))


if __name__ == "__main__":
    main()
