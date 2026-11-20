"""Final evaluation on the held-out test events, run once (README "Evaluation plan").

Each model is refitted on all training events with the settings chosen on CV
(results/model_cv.json) and scored on the test events. Writes:
  results/test_results.json      metrics, per-class results, bootstrap intervals
  results/test_calibration.png   predicted vs actual rate, per outcome

Run:  python -m src.evaluate
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sklearn.metrics import roc_auc_score  # noqa: E402

from .baseline import CLASSES, elo_probs, metrics  # noqa: E402
from .model import MODEL_FEATURES, RATING_ONLY, make_model, predict_proba  # noqa: E402
from .split import load, train_test  # noqa: E402

warnings.filterwarnings("ignore", category=FutureWarning)
RESULTS = Path(__file__).resolve().parents[1] / "results"
N_BOOT = 1000
NAMES = {"elo": "Elo formula", "lr_rating": "Logistic regression, rating_diff only",
         "lr": "Logistic regression, 5 features", "hgb": "Boosted trees, 5 features"}
COMPARISONS = [("lr", "lr_rating"), ("hgb", "lr"), ("lr_rating", "elo"), ("lr", "elo"), ("hgb", "elo"),
               ("hgb", "lr_rating")]

# dataviz reference palette (light); first three categorical slots, validated as a set
INK2, MUTED, GRID, AXIS, SURFACE = "#52514e", "#898781", "#e1e0d9", "#c3c2b7", "#fcfcfb"
COLOR = {"elo": "#2a78d6", "lr": "#eb6834", "hgb": "#1baf7a"}


def fit_predict(train: pd.DataFrame, test: pd.DataFrame) -> dict[str, np.ndarray]:
    best = {r["model"]: r["params"] for r in json.loads((RESULTS / "model_cv.json").read_text())["best"]}
    preds = {"elo": elo_probs(test["elo_expected"], float((train["outcome"] == "draw").mean()))}
    for name, kind, feats in [("lr_rating", "lr", RATING_ONLY), ("lr", "lr", MODEL_FEATURES),
                              ("hgb", "hgb", MODEL_FEATURES)]:
        m = make_model(kind, feats, **best[name]).fit(train[feats], train["outcome"])
        preds[name] = predict_proba(m, test)
    return preds, best


def per_game_log_loss(y_idx: np.ndarray, p: np.ndarray) -> np.ndarray:
    return -np.log(p[np.arange(len(y_idx)), y_idx])


def per_class(y: pd.Series, p: np.ndarray) -> dict:
    out = {}
    pred_cls = np.array(CLASSES)[p.argmax(axis=1)]
    for i, c in enumerate(CLASSES):
        actual = (y == c).to_numpy()
        out[c] = {"actual_rate": float(actual.mean()), "mean_predicted": float(p[:, i].mean()),
                  "auc": float(roc_auc_score(actual, p[:, i])),
                  "predicted_as_most_likely": int((pred_cls == c).sum()),
                  "recall": float((pred_cls[actual] == c).mean())}
    return out


def bootstrap(y_idx: np.ndarray, preds: dict[str, np.ndarray], seed: int = 0) -> dict:
    ll = {m: per_game_log_loss(y_idx, p) for m, p in preds.items()}
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(y_idx), size=(N_BOOT, len(y_idx)))
    boot = {m: v[idx].mean(axis=1) for m, v in ll.items()}
    ci = lambda a: [float(np.percentile(a, 2.5)), float(np.percentile(a, 97.5))]
    out = {"models": {m: {"log_loss": float(ll[m].mean()), "ci95": ci(b)} for m, b in boot.items()},
           "differences": {}}
    for a, b in COMPARISONS:
        d = boot[a] - boot[b]
        out["differences"][f"{a} - {b}"] = {"diff": float(ll[a].mean() - ll[b].mean()), "ci95": ci(d),
                                             "share_of_resamples_a_better": float((d < 0).mean())}
    return out


def plot_calibration(y: pd.Series, preds: dict[str, np.ndarray]) -> None:
    plt.rcParams.update({"figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "axes.edgecolor": AXIS,
                         "axes.labelcolor": INK2, "xtick.color": MUTED, "ytick.color": MUTED,
                         "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
                         "axes.spines.top": False, "axes.spines.right": False, "font.size": 10,
                         "axes.titlesize": 11, "axes.titleweight": "bold", "axes.titlelocation": "left",
                         "legend.frameon": False})
    fig, axes = plt.subplots(1, 3, figsize=(14, 4.6))
    titles = {"white": "White wins", "draw": "Draw", "black": "Black wins"}
    for i, c in enumerate(CLASSES):
        ax = axes[i]
        actual = (y == c).to_numpy().astype(float)
        hi = 1.0 if c != "draw" else 0.3
        ax.plot([0, hi], [0, hi], color=AXIS, lw=1.5, ls="--", label="perfect calibration")
        for m in ["elo", "lr", "hgb"]:
            p = preds[m][:, i]
            edges = np.unique(np.quantile(p, np.linspace(0, 1, 11)))
            b = np.clip(np.searchsorted(edges, p, side="right") - 1, 0, len(edges) - 2)
            df = pd.DataFrame({"p": p, "a": actual, "b": b}).groupby("b").agg(p=("p", "mean"), a=("a", "mean"))
            ax.plot(df["p"], df["a"], color=COLOR[m], marker="o", ms=5, lw=2, label=NAMES[m])
        ax.set_xlim(0, hi)
        ax.set_ylim(0, hi)
        ax.set_xlabel(f"Predicted P({titles[c].lower()})")
        ax.set_ylabel("Actual rate")
        ax.set_title(titles[c])
    axes[0].legend(loc="upper left", fontsize=8)
    fig.suptitle("Calibration on the test events (deciles of predicted probability)", x=0.01, ha="left",
                 fontweight="bold")
    fig.tight_layout()
    fig.savefig(RESULTS / "test_calibration.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    train, test = train_test(load())
    preds, params = fit_predict(train, test)
    y = test["outcome"]
    y_idx = y.map({c: i for i, c in enumerate(CLASSES)}).to_numpy()

    table = {m: metrics(y, p) for m, p in preds.items()}
    out = {"train_games": len(train), "test_games": len(test),
           "test_events": sorted(test["event_id"].unique().tolist()),
           "params": params, "metrics": table,
           "per_class": {m: per_class(y, p) for m, p in preds.items()},
           "bootstrap": bootstrap(y_idx, preds)}
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "test_results.json").write_text(json.dumps(out, indent=1))
    plot_calibration(y, preds)

    b = out["bootstrap"]
    print(f"test: {len(test)} games, {test['event_id'].nunique()} events\n")
    for m in preds:
        t = table[m]
        lo, hi = b["models"][m]["ci95"]
        print(f"{NAMES[m]:40s} log loss {t['log_loss']:.4f} [{lo:.4f}, {hi:.4f}]  "
              f"Brier {t['brier']:.4f}  accuracy {t['accuracy']:.1%}")
    print("\npaired differences in log loss (negative = first model better)")
    for k, v in b["differences"].items():
        print(f"  {k:18s} {v['diff']:+.4f}  95% CI [{v['ci95'][0]:+.4f}, {v['ci95'][1]:+.4f}]  "
              f"first better in {v['share_of_resamples_a_better']:.0%} of resamples")
    print("\nper class (5-feature trees)")
    print(pd.DataFrame(out["per_class"]["hgb"]).round(3).to_string())
    print("\nper class (Elo)")
    print(pd.DataFrame(out["per_class"]["elo"]).round(3).to_string())


if __name__ == "__main__":
    main()
