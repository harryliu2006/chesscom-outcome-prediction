"""Data quality audit of the processed tables: missing values, duplicates, failed joins,
invalid values and outliers. Writes results/data_audit.json.

Run:  python -m src.audit
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .features import MAX_FORFEIT_PLIES, MAX_FORFEIT_SECONDS

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / "data" / "processed"
DRAW_CODES = {"repetition", "insufficient", "timevsinsufficient", "stalemate", "agreed", "50move"}


def audit() -> dict:
    g = pd.read_csv(P / "games.csv")
    e = pd.read_csv(P / "events.csv")
    pl = pd.read_csv(P / "players.csv")
    h = pd.read_csv(P / "player_history.csv")
    f = pd.read_csv(P / "features.csv")
    out: dict = {}

    # ---- games
    long = pd.concat([g[["event_id", "round", s]].set_axis(["e", "r", "u"], axis=1) for s in ("white", "black")])
    pair = g[["white", "black"]].min(axis=1) + "|" + g[["white", "black"]].max(axis=1)
    m = g.merge(e[["event_id", "start_time", "finish_time", "total_rounds"]], on="event_id", suffixes=("", "_ev"))
    dur = g["end_time"] - g["start_time"]
    score = g["outcome"].map({"white": 1.0, "draw": 0.5, "black": 0.0})
    out["games"] = {
        "rows": len(g),
        "missing": {c: int(n) for c, n in g.isna().sum().items() if n},
        "duplicate_game_url": int(g["game_url"].duplicated().sum()),
        "player_twice_in_one_round": int(long.duplicated().sum()),
        "same_pairing_twice_in_event": int(pd.concat([g["event_id"], pair], axis=1).duplicated().sum()),
        "white_equals_black": int((g["white"] == g["black"]).sum()),
        "outcome_vs_score_mismatch": int((score != g["white_score"]).sum()),
        "not_5plus0": int((g["time_control"] != 300).sum()),
        "unrated": int((~g["rated"]).sum()),
        "rating_post_range": [int(g[["white_rating_post", "black_rating_post"]].min().min()),
                              int(g[["white_rating_post", "black_rating_post"]].max().max())],
        "start_before_event": int((m["start_time"] < m["start_time_ev"] - 60).sum()),
        "end_after_event_finish": int((m["end_time"] > m["finish_time"] + 600).sum()),
        "round_beyond_total": int((m["round"] > m["total_rounds"]).sum()),
        "duration_s": {"min": int(dur.min()), "median": float(dur.median()), "max": int(dur.max())},
        "forfeits": {f"<= {MAX_FORFEIT_SECONDS} s": int((dur <= MAX_FORFEIT_SECONDS).sum()),
                     f"<= {MAX_FORFEIT_PLIES} plies (5 moves)": int((g["plies"] <= MAX_FORFEIT_PLIES).sum()),
                     "either (dropped)": int(((dur <= MAX_FORFEIT_SECONDS) | (g["plies"] <= MAX_FORFEIT_PLIES)).sum())},
        "plies_distribution": g["plies"].quantile([0, .001, .01, .5, .99, 1]).round(1).to_dict(),
        "outcome_share_by_source": pd.crosstab(g["source"], g["outcome"], normalize="index").round(3).to_dict("index"),
    }

    # ---- joins
    parts = set(g["white"]) | set(g["black"])
    uuid_names = (pd.concat([g[[s, f"{s}_uuid"]].set_axis(["n", "u"], axis=1) for s in ("white", "black")])
                  .drop_duplicates().groupby("u")["n"].apply(sorted))
    have = set(zip(h["username"], h["game_url"]))
    lg = pd.concat([g[["game_url", s]].set_axis(["url", "u"], axis=1) for s in ("white", "black")])
    absent = lg[[(u, x) not in have for u, x in zip(lg["u"], lg["url"])]]
    out["joins"] = {
        "participants_without_profile": sorted(parts - set(pl["username"])),
        "profile_not_found": pl.loc[~pl["profile_found"], "username"].tolist(),
        "renamed_accounts (same uuid, several names)": [ns for ns in uuid_names if len(ns) > 1],
        "participants_without_own_archive": sorted(parts - set(h["username"])),
        "tournament_games_missing_from_own_archive": {"rows": len(absent),
                                                      "top_players": absent["u"].value_counts().head(10).to_dict()},
        "feature_rows_without_game": int((~f["game_url"].isin(g["game_url"])).sum()),
    }

    # ---- history
    exp = np.where(h["result_code"] == "win", 1.0, np.where(h["result_code"].isin(DRAW_CODES), 0.5, 0.0))
    hh = h.assign(opponent=h["opponent"].str.lower())
    both = hh.merge(hh[["username", "game_url", "score", "rating_post"]],
                    left_on=["opponent", "game_url"], right_on=["username", "game_url"], suffixes=("", "_o"))
    out["player_history"] = {
        "rows": len(h),
        "missing": {c: int(n) for c, n in h.isna().sum().items() if n and c != "tournament"},
        "duplicate_player_game": int(h.duplicated(["username", "game_url"]).sum()),
        "score_vs_result_code_mismatch": int((exp != h["score"]).sum()),
        "games_seen_from_both_sides": len(both),
        "both_sides_score_not_mirrored": int(((both["score"] + both["score_o"]) != 1).sum()),
        "both_sides_rating_disagree": int((both["opponent_rating_post"] != both["rating_post_o"]).sum()),
    }

    # ---- feature outliers
    fe = f.merge(g[["game_url", "white_rating_post", "black_rating_post"]], on="game_url")
    jump = pd.concat([(fe[f"{s}_rating_post"] - fe[f"{s}_rating_pre"]).abs() for s in ("white", "black")])
    pre = pd.concat([f["white_rating_pre"], f["black_rating_pre"]])
    gm = pd.concat([f["white_games_30d"], f["black_games_30d"]])
    sve = pd.concat([f["white_score_vs_expected_30d"], f["black_score_vs_expected_30d"]])
    out["features"] = {
        "rows": len(f),
        "duplicate_game_url": int(f["game_url"].duplicated().sum()),
        "missing": {c: int(n) for c, n in f.isna().sum().items() if n},
        "rating_pre_below_2000 (likely provisional)": int((pre < 2000).sum()),
        "rating_moved_over_50_in_one_game (provisional)": int((jump > 50).sum()),
        "games_30d_quantiles": gm.quantile([.5, .99, .999, 1]).to_dict(),
        "score_vs_expected_abs_over_0.3": {"games_30d < 10": int(((sve.abs() > .3) & (gm < 10)).sum()),
                                           "games_30d >= 10": int(((sve.abs() > .3) & (gm >= 10)).sum())},
    }
    return out


def main() -> None:
    out = audit()
    (ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "results" / "data_audit.json").write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
