"""Are the kept features redundant with each other? Training events, CV folds only.

Candidates (each added a little on top of rating_diff on its own, results/eda):
  rating_mean, diff_games_30d, draw rates (white_draw_rate_30d + black_draw_rate_30d)

Three checks:
1. Every subset: CV log loss of rating_diff + each of the 8 subsets of the 3 groups.
2. Drop-one: from the full set, remove one group at a time. If log loss barely rises,
   the other groups already carry that group's signal.
3. Overlap: sum of the groups' individual gains minus their joint gain. About 0 means
   the gains add up (no redundancy); a large positive number means they overlap.
Plus the Spearman correlations between the candidates.

Run:  python -m src.feature_selection
"""
from __future__ import annotations

import itertools
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .eda import fit_lr, log_loss, predict
from .split import cv_folds, load, train_test

OUT = Path(__file__).resolve().parents[1] / "results" / "feature_selection.json"
BASE = ["rating_diff"]
GROUPS = {"rating_mean": ["rating_mean"],
          "diff_games_30d": ["diff_games_30d"],
          "draw_rates": ["white_draw_rate_30d", "black_draw_rate_30d"]}


def cv_ll(train: pd.DataFrame, cols: list[str]) -> np.ndarray:
    return np.array([log_loss(val["outcome"], predict(fit_lr(fit, cols), val, cols, fit))
                     for _, fit, val in cv_folds(train)])


def main() -> None:
    train, _ = train_test(load())
    names = list(GROUPS)
    ll = {}
    for k in range(len(names) + 1):
        for combo in itertools.combinations(names, k):
            cols = BASE + [c for g in combo for c in GROUPS[g]]
            ll[combo] = cv_ll(train, cols)
    base = ll[()]
    full = ll[tuple(names)]

    subsets = pd.DataFrame([{"added_to_rating_diff": " + ".join(c) or "(nothing)",
                             "cv_log_loss": l.mean(), "gain_vs_rating_diff": base.mean() - l.mean(),
                             "folds_better": int((l < base).sum())}
                            for c, l in ll.items()]).sort_values("cv_log_loss")

    drop = pd.DataFrame([{"dropped": g,
                          "cv_log_loss": ll[tuple(n for n in names if n != g)].mean(),
                          "loss_increase": ll[tuple(n for n in names if n != g)].mean() - full.mean(),
                          "folds_worse": int((ll[tuple(n for n in names if n != g)] > full).sum()),
                          "gain_alone": base.mean() - ll[(g,)].mean()}
                         for g in names])
    drop["share_of_gain_kept_in_full_model"] = drop["loss_increase"] / drop["gain_alone"]

    sum_alone = sum(base.mean() - ll[(g,)].mean() for g in names)
    joint = base.mean() - full.mean()
    pairs = []
    for a, b in itertools.combinations(names, 2):
        ga, gb = base.mean() - ll[(a,)].mean(), base.mean() - ll[(b,)].mean()
        gab = base.mean() - ll[tuple(n for n in names if n in (a, b))].mean()
        pairs.append({"pair": f"{a} & {b}", "gain_a": ga, "gain_b": gb, "gain_together": gab,
                      "overlap": ga + gb - gab, "overlap_share": (ga + gb - gab) / (ga + gb)})
    pairs = pd.DataFrame(pairs)

    corr = train[[c for g in names for c in GROUPS[g]]].corr(method="spearman")

    pd.set_option("display.width", 200)
    print(f"rating_diff alone: {base.mean():.5f}\n")
    print("1. every subset\n", subsets.round(5).to_string(index=False), "\n")
    print("2. drop one group from the full set\n", drop.round(5).to_string(index=False), "\n")
    print(f"3. overlap: sum of individual gains {sum_alone:.5f}, joint gain {joint:.5f}, "
          f"overlap {sum_alone - joint:.5f} ({(sum_alone - joint) / sum_alone:.0%})")
    print(pairs.round(5).to_string(index=False), "\n")
    print("Spearman correlations\n", corr.round(3).to_string())

    OUT.write_text(json.dumps({
        "cv_log_loss_rating_diff_only": base.mean(),
        "subsets": subsets.round(6).to_dict("records"),
        "drop_one": drop.round(6).to_dict("records"),
        "overlap": {"sum_of_individual_gains": sum_alone, "joint_gain": joint,
                    "pairs": pairs.round(6).to_dict("records")},
        "spearman": corr.round(4).to_dict(),
    }, indent=1))


if __name__ == "__main__":
    main()
