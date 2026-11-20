"""Trained models, scored on the time-ordered CV folds of the training set.

Rating-only multinomial logistic regression, in three nested variants:

* diff             rating_diff only: the Elo shape, but with a learned scale and a White
                   advantage (intercept), so it can fix Elo's overconfidence
* diff_abs         + |rating_diff|: lets the draw probability peak for evenly matched
                   players (a linear term alone can only tilt draws one way)
* diff_abs_mean    + rating_mean: draws and upsets may depend on the level of play

Inputs are standardised inside the pipeline, fitted on the fold's training events only.
The test set is not scored here.

Run:  python -m src.models
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .baseline import CLASSES, elo_probs, metrics
from .split import cv_folds, load, train_test

RESULTS = Path(__file__).resolve().parents[1] / "results"


def model_inputs(df: pd.DataFrame) -> pd.DataFrame:
    """Columns derived from features.csv (pure transforms, no new information)."""
    out = df.copy()
    out["abs_rating_diff"] = out["rating_diff"].abs()
    return out


RATING_VARIANTS = {
    "lr_diff": ["rating_diff"],
    "lr_diff_abs": ["rating_diff", "abs_rating_diff"],
    "lr_diff_abs_mean": ["rating_diff", "abs_rating_diff", "rating_mean"],
    "lr_diff_mean": ["rating_diff", "rating_mean"],
}
RATING_MODEL = "lr_diff_mean"  # chosen: |diff| adds nothing (softmax already peaks draws at diff 0)


def logreg():
    return make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000))


def predict_proba(model, X: pd.DataFrame) -> np.ndarray:
    """Class probabilities in CLASSES order (white, draw, black)."""
    p = model.predict_proba(X)
    order = [list(model.classes_).index(c) for c in CLASSES]
    return p[:, order]


def cv_scores(train: pd.DataFrame, variants: dict[str, list[str]], make_model=logreg) -> pd.DataFrame:
    rows = []
    for fold, fit, val in cv_folds(train):
        fit, val = model_inputs(fit), model_inputs(val)
        d = float((fit["outcome"] == "draw").mean())
        rows.append({"fold": fold, "model": "elo", **metrics(val["outcome"], elo_probs(val["elo_expected"], d))})
        for name, cols in variants.items():
            m = make_model().fit(fit[cols], fit["outcome"])
            rows.append({"fold": fold, "model": name, **metrics(val["outcome"], predict_proba(m, val[cols]))})
    return pd.DataFrame(rows)


def oof_predictions(train: pd.DataFrame, cols: list[str], make_model=logreg) -> pd.DataFrame:
    """Validation-fold predictions stacked together (each game predicted by a model that
    never saw its event), for calibration checks."""
    parts = []
    for fold, fit, val in cv_folds(train):
        fit, val = model_inputs(fit), model_inputs(val)
        m = make_model().fit(fit[cols], fit["outcome"])
        p = predict_proba(m, val[cols])
        d = float((fit["outcome"] == "draw").mean())
        parts.append(val.assign(p_white=p[:, 0], p_draw=p[:, 1], p_black=p[:, 2],
                                elo_draw_rate=d))
    return pd.concat(parts, ignore_index=True)


def calibration(oof: pd.DataFrame) -> pd.DataFrame:
    """Actual White score vs predicted expected score, LR and Elo, by bins of Elo's E."""
    score = oof["outcome"].map({"white": 1.0, "draw": 0.5, "black": 0.0})
    pred = oof["p_white"] + 0.5 * oof["p_draw"]
    bins = pd.cut(oof["elo_expected"], np.linspace(0, 1, 11))
    return (pd.DataFrame({"elo_E": oof["elo_expected"], "lr_E": pred, "actual": score,
                          "lr_p_draw": oof["p_draw"], "actual_draw": oof["outcome"] == "draw"})
            .groupby(bins, observed=True)
            .agg(games=("elo_E", "size"), elo_E=("elo_E", "mean"), lr_E=("lr_E", "mean"),
                 actual_score=("actual", "mean"), lr_p_draw=("lr_p_draw", "mean"),
                 actual_draw=("actual_draw", "mean")))


def main() -> None:
    train, _test = train_test(load())
    res = cv_scores(train, RATING_VARIANTS)
    summary = res.groupby("model", sort=False)[["log_loss", "brier", "accuracy"]].mean()
    folds = res.pivot(index="fold", columns="model", values="log_loss")
    gain = folds.sub(folds["elo"], axis=0).drop(columns="elo")  # negative = better than Elo

    best = RATING_MODEL
    fitted = logreg().fit(model_inputs(train)[RATING_VARIANTS[best]], train["outcome"])
    lr = fitted[-1]
    coefs = pd.DataFrame(lr.coef_, index=lr.classes_, columns=RATING_VARIANTS[best])
    coefs["intercept"] = lr.intercept_

    calib = calibration(oof_predictions(train, RATING_VARIANTS[best]))

    print("mean over 5 folds\n", summary.round(4).to_string(), "\n")
    print("log-loss change vs Elo, per fold (negative = better)\n", gain.round(4).to_string(), "\n")
    print(f"{best} coefficients (standardised inputs, full training set)\n", coefs.round(3).to_string(), "\n")
    print("calibration on validation folds\n", calib.round(3).to_string())

    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "rating_lr_cv.json").write_text(json.dumps({
        "folds": res.to_dict(orient="records"),
        "mean": summary.round(5).to_dict(orient="index"),
        "log_loss_vs_elo_per_fold": gain.round(5).to_dict(orient="index"),
        "coefficients_" + best: coefs.round(4).to_dict(orient="index"),
        "calibration": calib.reset_index().astype({"elo_expected": str}).round(4).to_dict(orient="records"),
    }, indent=1))


if __name__ == "__main__":
    main()
