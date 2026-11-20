"""Exploratory data analysis on the TRAINING events only (the test events stay unseen).

Plots (results/eda/):
  1_score_vs_rating_diff.png   White's score by rating difference, Elo curve overlaid
  2_draw_rate.png              draw rate vs rating level and vs rating gap
  3_outcomes.png               outcome distribution, overall and by rating gap
  4_form_distributions.png     distributions of the recent-form features
  5_correlations.png           Spearman correlation heatmap of all features
  6_residuals.png              does a feature carry signal beyond rating_diff?

Plus results/eda/incremental_signal.json: cross-validated log loss of a logistic
regression on rating_diff alone vs rating_diff + one other feature.

Run:  python -m src.eda
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib
import warnings

matplotlib.use("Agg")
warnings.filterwarnings("ignore", category=FutureWarning)
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402

from .baseline import metrics  # noqa: E402
from .features import FEATURES, FORM_STATS  # noqa: E402
from .split import cv_folds, load, train_test  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "results" / "eda"
CLASSES = ["white", "draw", "black"]

# Reference palette (dataviz skill, light mode); outcome colours validated as a set.
INK, INK2, MUTED, GRID, AXIS, SURFACE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7", "#fcfcfb"
OUTCOME_COLOR = {"white": "#2a78d6", "draw": "#eb6834", "black": "#1baf7a"}
BLUE, BLUE_LIGHT = "#2a78d6", "#b7d3f6"
DIVERGING = LinearSegmentedColormap.from_list("div", ["#e34948", "#f0efec", "#2a78d6"])

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": AXIS, "axes.labelcolor": INK2, "xtick.color": MUTED, "ytick.color": MUTED,
    "text.color": INK, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
    "axes.spines.top": False, "axes.spines.right": False, "font.size": 10,
    "axes.titlesize": 11, "axes.titleweight": "bold", "axes.titlelocation": "left",
    "lines.linewidth": 2, "legend.frameon": False,
})


def white_score(df):
    return df["outcome"].map({"white": 1.0, "draw": 0.5, "black": 0.0})


def binned(x, y, edges):
    """Mean of y per bin of x, with a 95% normal-approximation interval."""
    b = pd.cut(x, edges)
    g = pd.DataFrame({"x": x, "y": y}).groupby(b, observed=True)
    s = g.agg(x=("x", "mean"), m=("y", "mean"), sd=("y", "std"), n=("y", "size"))
    s["ci"] = 1.96 * s["sd"] / np.sqrt(s["n"])
    return s[s["n"] >= 50]


def save(fig, name):
    fig.savefig(OUT / name, dpi=150, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------- 1
def plot_score_vs_diff(tr):
    s = binned(tr["rating_diff"], white_score(tr), np.arange(-900, 901, 50))
    fig, (ax, axh) = plt.subplots(2, 1, figsize=(8, 6.2), sharex=True,
                                  gridspec_kw={"height_ratios": [3, 1], "hspace": 0.08})
    d = np.linspace(-900, 900, 400)
    ax.plot(d, 1 / (1 + 10 ** (-d / 400)), color=MUTED, lw=2, ls="--", label="Elo expected score")
    ax.fill_between(s["x"], s["m"] - s["ci"], s["m"] + s["ci"], color=BLUE_LIGHT, lw=0)
    ax.plot(s["x"], s["m"], color=BLUE, marker="o", ms=4, label="White's actual score (95% CI)")
    ax.axhline(0.5, color=AXIS, lw=1)
    ax.axvline(0, color=AXIS, lw=1)
    near0 = white_score(tr)[tr["rating_diff"].abs() <= 50].mean()
    ax.annotate(f"Equal ratings (±50): White scores {near0:.3f}", xy=(0, near0), xytext=(60, 0.25),
                color=INK2, arrowprops=dict(arrowstyle="-", color=MUTED))
    ax.set_ylabel("White's score (win 1, draw ½)")
    ax.set_title("White's score vs rating difference: Elo is too confident at large gaps")
    ax.legend(loc="upper left")
    axh.hist(tr["rating_diff"].clip(-900, 900), bins=np.arange(-900, 901, 25), color=BLUE, rwidth=0.9)
    axh.set_ylabel("Games")
    axh.set_xlabel("Rating difference, White − Black (pre-game)")
    save(fig, "1_score_vs_rating_diff.png")


# ---------------------------------------------------------------- 2
def plot_draw_rate(tr):
    draw = (tr["outcome"] == "draw").astype(float)
    fig, axes = plt.subplots(1, 3, figsize=(14, 4.2), gridspec_kw={"width_ratios": [1, 1, 1.15]})
    for ax, x, edges, xl, t in [
        (axes[0], tr["rating_mean"], np.arange(1900, 3501, 100), "Average rating of the two players",
         "vs rating level"),
        (axes[1], tr["rating_diff"].abs(), np.arange(0, 1001, 50), "Rating gap |White − Black|",
         "vs rating gap"),
    ]:
        s = binned(x, draw, edges)
        ax.fill_between(s["x"], (s["m"] - s["ci"]) * 100, (s["m"] + s["ci"]) * 100, color="#f8c9b5", lw=0)
        ax.plot(s["x"], s["m"] * 100, color=OUTCOME_COLOR["draw"], marker="o", ms=4)
        ax.axhline(draw.mean() * 100, color=MUTED, ls="--", lw=1)
        ax.set_xlabel(xl)
        ax.set_ylabel("Draw rate (%)")
        ax.set_title(f"Draw rate {t}")
    axes[0].annotate(f"overall {draw.mean():.1%}", xy=(2550, draw.mean() * 100 - 2.2), color=MUTED)
    # both at once
    lv = pd.cut(tr["rating_mean"], [0, 2400, 2600, 2800, 3000, 4000],
                labels=["<2400", "2400–2600", "2600–2800", "2800–3000", "3000+"])
    gp = pd.cut(tr["rating_diff"].abs(), [-1, 100, 200, 300, 400, 600, 5000],
                labels=["0–100", "100–200", "200–300", "300–400", "400–600", "600+"])
    tab = draw.groupby([lv, gp], observed=False).agg(["mean", "size"]).reset_index()
    piv = tab.pivot(index=lv.name, columns=gp.name, values="mean")
    cnt = tab.pivot(index=lv.name, columns=gp.name, values="size")
    piv = piv.where(cnt >= 100)
    ax = axes[2]
    seq = LinearSegmentedColormap.from_list("seq", ["#fdeee7", "#eb6834", "#7a2a0c"])
    im = ax.imshow(piv.to_numpy() * 100, cmap=seq, aspect="auto", origin="lower")
    for i in range(piv.shape[0]):
        for j in range(piv.shape[1]):
            v = piv.iat[i, j]
            if pd.notna(v):
                ax.text(j, i, f"{v:.0%}", ha="center", va="center", fontsize=8,
                        color="#ffffff" if v > 0.12 else INK)
    ax.set_xticks(range(piv.shape[1]), piv.columns, rotation=30)
    ax.set_yticks(range(piv.shape[0]), piv.index)
    ax.set_xlabel("Rating gap")
    ax.set_ylabel("Average rating")
    ax.set_title("Draw rate by level and gap (cells ≥100 games)")
    ax.grid(False)
    fig.colorbar(im, ax=ax, shrink=0.8, label="Draw rate (%)")
    fig.tight_layout()
    save(fig, "2_draw_rate.png")


# ---------------------------------------------------------------- 3
def plot_outcomes(tr):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 3.8), gridspec_kw={"width_ratios": [1, 2]})
    share = tr["outcome"].value_counts(normalize=True).reindex(CLASSES)
    y = np.arange(3)[::-1]
    ax1.barh(y, share * 100, color=[OUTCOME_COLOR[c] for c in CLASSES], height=0.6)
    for yi, c in zip(y, CLASSES):
        ax1.text(share[c] * 100 + 1, yi, f"{share[c]:.1%}  ({(tr['outcome'] == c).sum():,})",
                 va="center", color=INK2)
    ax1.set_yticks(y, ["White wins", "Draw", "Black wins"])
    ax1.set_xlim(0, 70)
    ax1.set_xlabel("Share of games (%)")
    ax1.set_title(f"Outcomes ({len(tr):,} training games)")
    ax1.grid(axis="y", visible=False)

    edges = [-5000, -400, -200, -100, -50, 50, 100, 200, 400, 5000]
    labels = ["≤−400", "−400…−200", "−200…−100", "−100…−50", "−50…50", "50…100", "100…200", "200…400", "≥400"]
    b = pd.cut(tr["rating_diff"], edges, labels=labels)
    ct = pd.crosstab(b, tr["outcome"], normalize="index").reindex(columns=CLASSES) * 100
    n = b.value_counts().reindex(labels)
    left = np.zeros(len(ct))
    for c, name in zip(CLASSES, ["White wins", "Draw", "Black wins"]):
        ax2.bar(range(len(ct)), ct[c], bottom=left, color=OUTCOME_COLOR[c], width=0.75,
                edgecolor=SURFACE, linewidth=2, label=name)
        left += ct[c].to_numpy()
    for i, (lab, cnt) in enumerate(n.items()):
        ax2.text(i, 101.5, f"n={cnt:,}", ha="center", fontsize=8, color=MUTED)
    ax2.set_xticks(range(len(ct)), labels, rotation=20)
    ax2.set_ylim(0, 106)
    ax2.set_ylabel("Share of games (%)")
    ax2.set_xlabel("Rating difference, White − Black")
    ax2.set_title("Outcome mix by rating difference")
    ax2.legend(loc="upper center", bbox_to_anchor=(0.5, -0.28), ncol=3)
    ax2.grid(axis="x", visible=False)
    fig.tight_layout()
    save(fig, "3_outcomes.png")


# ---------------------------------------------------------------- 4
def plot_form(tr):
    titles = {"games_30d": "Games in last 30 days", "score_vs_expected_30d": "Score vs expected (per game)",
              "rating_change_30d": "Rating change, 30 days", "draw_rate_30d": "Draw rate, 30 days"}
    fig, axes = plt.subplots(2, 4, figsize=(15, 6.4))
    for j, s in enumerate(FORM_STATS):
        per = pd.concat([tr[f"white_{s}"], tr[f"black_{s}"]]).dropna()
        dif = tr[f"diff_{s}"].dropna()
        for i, (x, row) in enumerate([(per, "per player"), (dif, "White − Black")]):
            ax = axes[i, j]
            if s == "games_30d" and i == 0:
                ax.hist(np.log10(x + 1), bins=50, color=BLUE)
                ax.set_xticks(np.log10(np.array([0, 10, 100, 1000]) + 1), ["0", "10", "100", "1000"])
                ax.set_xlabel("games (log scale)")
            else:
                lo, hi = x.quantile([0.005, 0.995])
                ax.hist(x.clip(lo, hi), bins=60, color=BLUE)
                ax.set_xlabel(f"clipped to 0.5–99.5% ({lo:.3g} … {hi:.3g})", fontsize=8, color=MUTED)
            ax.set_title(f"{titles[s]} — {row}", fontsize=10)
            ax.text(0.98, 0.95, f"median {x.median():.3g}\nmissing {tr[f'diff_{s}'].isna().mean():.1%}"
                    if i else f"median {x.median():.3g}",
                    transform=ax.transAxes, ha="right", va="top", fontsize=8, color=INK2)
            ax.grid(axis="x", visible=False)
    fig.suptitle("Recent-form features (training games)", x=0.01, ha="left", fontweight="bold")
    fig.tight_layout()
    save(fig, "4_form_distributions.png")


# ---------------------------------------------------------------- 5
def plot_corr(tr):
    c = tr[FEATURES].corr(method="spearman")
    fig, ax = plt.subplots(figsize=(11, 9.5))
    im = ax.imshow(c, cmap=DIVERGING, vmin=-1, vmax=1)
    for i in range(len(c)):
        for j in range(len(c)):
            v = c.iat[i, j]
            ax.text(j, i, f"{v:.2f}".replace("0.", ".").replace("-.", "−."), ha="center", va="center",
                    fontsize=6.5, color="#ffffff" if abs(v) > 0.6 else INK)
    ax.set_xticks(range(len(c)), c.columns, rotation=60, ha="right", fontsize=8)
    ax.set_yticks(range(len(c)), c.index, fontsize=8)
    ax.grid(False)
    ax.set_title("Spearman correlation of features (training games)")
    fig.colorbar(im, ax=ax, shrink=0.7)
    save(fig, "5_correlations.png")
    return c


# ---------------------------------------------------------------- 6
def design(df, cols, ref):
    """Features for the logistic regression: games_30d on a signed log scale, missing
    values filled with the fitting data's median, standardised on the fitting data."""
    def tx(d):
        X = d[cols].astype(float).copy()
        for col in cols:
            if "games_30d" in col:
                X[col] = np.sign(X[col]) * np.log1p(X[col].abs())
        return X
    Xr, X = tx(ref), tx(df)
    med = Xr.median()
    Xr, X = Xr.fillna(med), X.fillna(med)
    return ((X - Xr.mean()) / Xr.std()).to_numpy()


def fit_lr(fit, cols):
    m = LogisticRegression(max_iter=2000, C=1e4)
    m.fit(design(fit, cols, fit), fit["outcome"])
    return m


def predict(m, df, cols, ref):
    p = m.predict_proba(design(df, cols, ref))
    return pd.DataFrame(p, columns=m.classes_, index=df.index)[CLASSES].to_numpy()


def log_loss(y, p, labels=None):
    """Log loss with p's columns in CLASSES order (white, draw, black). sklearn's
    log_loss assumes alphabetical column order, so it is not used here."""
    return metrics(y, p)["log_loss"]


def incremental_signal(tr):
    """CV log loss of rating_diff alone vs rating_diff + one feature. Negative delta = the
    feature adds signal. Also out-of-fold predictions of the rating_diff model, for the
    residual plots."""
    base = ["rating_diff"]
    candidates = [f for f in FEATURES if f != "rating_diff"]
    rows, oof = [], []
    for fold, fit, val in cv_folds(tr):
        m0 = fit_lr(fit, base)
        p0 = predict(m0, val, base, fit)
        ll0 = log_loss(val["outcome"], p0, labels=CLASSES)
        oof.append(val.assign(p_white=p0[:, 0], p_draw=p0[:, 1], p_black=p0[:, 2]))
        for f in candidates:
            cols = base + [f]
            p = predict(fit_lr(fit, cols), val, cols, fit)
            rows.append({"fold": fold, "feature": f, "delta": log_loss(val["outcome"], p, labels=CLASSES) - ll0})
        rows.append({"fold": fold, "feature": "(rating_diff only)", "delta": 0.0, "base_ll": ll0})
    r = pd.DataFrame(rows)
    summ = (r[r["feature"] != "(rating_diff only)"].groupby("feature")["delta"]
            .agg(mean_delta="mean", folds_better=lambda d: int((d < 0).sum()), worst="max", best="min")
            .sort_values("mean_delta"))
    # all form features together, and rating_mean + all form
    allform = [f for f in FEATURES if any(s in f for s in FORM_STATS)]
    combos = {"rating_diff + rating_mean": base + ["rating_mean"],
              "rating_diff + all form features": base + allform,
              "rating_diff + rating_mean + all form features": base + ["rating_mean"] + allform}
    combo_rows = []
    for name, cols in combos.items():
        d = []
        for fold, fit, val in cv_folds(tr):
            ll0 = r[(r["fold"] == fold) & (r["feature"] == "(rating_diff only)")]["base_ll"].iat[0]
            d.append(log_loss(val["outcome"], predict(fit_lr(fit, cols), val, cols, fit), labels=CLASSES) - ll0)
        combo_rows.append({"model": name, "mean_delta": float(np.mean(d)), "folds_better": int(sum(x < 0 for x in d)),
                           "per_fold": [round(x, 5) for x in d]})
    base_ll = float(r[r["feature"] == "(rating_diff only)"]["base_ll"].mean())
    return summ, pd.DataFrame(combo_rows), base_ll, pd.concat(oof)


def plot_residuals(oof):
    """Residual = actual minus what the rating_diff-only model predicted (out of fold).
    A flat line at 0 means the feature adds nothing once rating_diff is known."""
    act = white_score(oof)
    exp = oof["p_white"] + 0.5 * oof["p_draw"]
    res_score = act - exp
    res_draw = (oof["outcome"] == "draw").astype(float) - oof["p_draw"]
    feats = ["rating_mean", "diff_score_vs_expected_30d", "diff_rating_change_30d",
             "diff_games_30d", "diff_draw_rate_30d"]
    fig, axes = plt.subplots(2, len(feats), figsize=(17, 6.2), sharey="row")
    for j, f in enumerate(feats):
        x = oof[f]
        q = x.quantile(np.linspace(0, 1, 11)).to_numpy()
        q = np.unique(q)
        for i, (res, lab, col) in enumerate([(res_score, "White's score residual", BLUE),
                                              (res_draw, "Draw residual", OUTCOME_COLOR["draw"])]):
            s = binned(x, res, q)
            ax = axes[i, j]
            ax.axhline(0, color=AXIS, lw=1)
            ax.errorbar(s["x"], s["m"], yerr=s["ci"], color=col, marker="o", ms=4, lw=2, capsize=0)
            if j == 0:
                ax.set_ylabel(f"{lab}\n(actual − predicted)")
            if i == 1:
                ax.set_xlabel(f + "\n(deciles)", fontsize=9)
            if i == 0:
                ax.set_title(f, fontsize=10)
    fig.suptitle("Signal beyond rating_diff: residuals of a rating_diff-only model (out-of-fold, 95% CI)",
                 x=0.01, ha="left", fontweight="bold")
    fig.tight_layout()
    save(fig, "6_residuals.png")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    train, _test = train_test(load())
    tr = train
    plot_score_vs_diff(tr)
    plot_draw_rate(tr)
    plot_outcomes(tr)
    plot_form(tr)
    corr = plot_corr(tr)
    summ, combos, base_ll, oof = incremental_signal(tr)
    plot_residuals(oof)
    out = {"train_games": len(tr), "cv_log_loss_rating_diff_only": round(base_ll, 5),
           "single_feature_added": summ.round(5).reset_index().to_dict("records"),
           "combinations": combos.to_dict("records"),
           "spearman_with_rating_diff": corr["rating_diff"].round(3).to_dict()}
    (OUT / "incremental_signal.json").write_text(json.dumps(out, indent=1))
    print(f"rating_diff-only CV log loss: {base_ll:.4f}\n")
    print(summ.round(5).to_string(), "\n")
    print(combos.to_string(index=False))


if __name__ == "__main__":
    main()
