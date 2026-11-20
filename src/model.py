"""Model pipelines and hyperparameter tuning on the time-ordered CV folds.

Every model is a scikit-learn Pipeline, so preprocessing is learned from the data the
model is fitted on (a CV fold's training events, or all training events for the final
fit) and only applied to validation/test data:

  diff_games_30d   signed log scale: sign(x) * log(1 + |x|)   (heavy-tailed)
  missing values   training median (draw rates are missing for players with no
                   rated blitz games in the last 30 days, ~0.5% per side)
  scaling          standardised (needed for regularised logistic regression)

Models compared (the test set is not used here):
  elo           Elo formula + training draw rate (src/baseline.py), nothing tuned
  lr_rating     logistic regression on rating_diff only
  lr            logistic regression on MODEL_FEATURES
  hgb           histogram gradient-boosted trees on MODEL_FEATURES

Run:  python -m src.model
"""
from __future__ import annotations

import itertools
import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, StandardScaler

from .baseline import CLASSES, elo_probs, metrics
from .split import cv_folds, load, train_test

warnings.filterwarnings("ignore", category=FutureWarning)

RESULTS = Path(__file__).resolve().parents[1] / "results"

# Final inputs, chosen on the training folds (src/feature_selection.py).
MODEL_FEATURES = ["rating_diff", "rating_mean", "diff_games_30d",
                  "white_draw_rate_30d", "black_draw_rate_30d"]
RATING_ONLY = ["rating_diff"]
LOG_COLS = ["diff_games_30d"]

LR_GRID = {"C": [0.001, 0.01, 0.1, 1.0, 10.0, 100.0]}
HGB_GRID = {"learning_rate": [0.03, 0.1], "max_leaf_nodes": [7, 15, 31],
            "min_samples_leaf": [50, 200], "max_iter": [100, 300]}


def signed_log1p(x):
    return np.sign(x) * np.log1p(np.abs(x))


def preprocessor(features: list[str]) -> ColumnTransformer:
    log_cols = [c for c in features if c in LOG_COLS]
    other = [c for c in features if c not in LOG_COLS]
    num = lambda: [("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler())]
    parts = [("plain", Pipeline(num()), other)]
    if log_cols:
        parts.append(("log", Pipeline([("log", FunctionTransformer(signed_log1p, feature_names_out="one-to-one"))]
                                      + num()), log_cols))
    return ColumnTransformer(parts)


def make_model(kind: str, features: list[str], **params) -> Pipeline:
    if kind == "lr":
        clf = LogisticRegression(max_iter=5000, **params)
    elif kind == "hgb":
        clf = HistGradientBoostingClassifier(random_state=0, **params)
    else:
        raise ValueError(kind)
    return Pipeline([("prep", preprocessor(features)), ("clf", clf)])


def predict_proba(model: Pipeline, df: pd.DataFrame) -> np.ndarray:
    """Probabilities in CLASSES order (white, draw, black)."""
    p = model.predict_proba(df)
    order = [list(model.classes_).index(c) for c in CLASSES]
    return p[:, order]


def cv_score(train: pd.DataFrame, kind: str, features: list[str], **params) -> dict:
    folds = []
    for fold, fit, val in cv_folds(train):
        if kind == "elo":
            p = elo_probs(val["elo_expected"], float((fit["outcome"] == "draw").mean()))
        else:
            p = predict_proba(make_model(kind, features, **params).fit(fit[features], fit["outcome"]), val)
        folds.append(metrics(val["outcome"], p))
    f = pd.DataFrame(folds)
    return {"log_loss": f["log_loss"].mean(), "brier": f["brier"].mean(), "accuracy": f["accuracy"].mean(),
            "per_fold_log_loss": f["log_loss"].round(5).tolist()}


def grid(g: dict) -> list[dict]:
    return [dict(zip(g, v)) for v in itertools.product(*g.values())]


def main() -> None:
    train, _ = train_test(load())
    rows = []

    def run(name, kind, features, params):
        r = cv_score(train, kind, features, **params)
        rows.append({"model": name, "params": params, **r})
        print(f"{name:10s} {json.dumps(params):75s} log_loss {r['log_loss']:.5f}")
        return r

    run("elo", "elo", [], {})
    for p in grid(LR_GRID):
        run("lr_rating", "lr", RATING_ONLY, p)
    for p in grid(LR_GRID):
        run("lr", "lr", MODEL_FEATURES, p)
    for p in grid(HGB_GRID):
        run("hgb", "hgb", MODEL_FEATURES, p)

    res = pd.DataFrame(rows)
    best = res.loc[res.groupby("model")["log_loss"].idxmin()].set_index("model")
    best = best.loc[["elo", "lr_rating", "lr", "hgb"]]
    ref = np.array(best.loc["lr_rating", "per_fold_log_loss"])
    best["folds_better_than_lr_rating"] = [int((np.array(v) < ref).sum()) for v in best["per_fold_log_loss"]]
    print("\nbest per model (mean of 5 CV folds)")
    print(best[["params", "log_loss", "brier", "accuracy", "folds_better_than_lr_rating"]].to_string())

    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "model_cv.json").write_text(json.dumps({
        "features": MODEL_FEATURES,
        "best": best.reset_index().to_dict("records"),
        "all_configs": res.to_dict("records"),
    }, indent=1, default=float))


if __name__ == "__main__":
    main()
