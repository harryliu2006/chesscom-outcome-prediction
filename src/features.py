"""Build the pre-game feature table: data/processed/features.csv.

The model is a general blitz game predictor; Titled Tuesday is only the data source.
So every feature describes the two players, never the tournament:

* rating:       each player's blitz rating before the game, their difference and mean,
                and the Elo expected score for White (also the baseline to beat)
* recent form:  each player's rated blitz games in the 30 days before the game

The as-of rule for every feature: only player-history games that ENDED BEFORE this
game STARTED are used. A game's own result therefore never reaches its features, and
earlier rounds of the same event count like any other recent game.

Run:  python -m src.features
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
FORM_WINDOW_DAYS = 30
DAY = 86_400

KEYS = ["event_id", "round", "game_url", "start_time", "white", "black"]  # not features
LABEL = "outcome"
RATING_FEATURES = ["white_rating_pre", "black_rating_pre", "rating_diff", "rating_mean", "elo_expected"]
FORM_STATS = ["games_30d", "score_vs_expected_30d", "rating_change_30d", "draw_rate_30d",
              "days_since_last_game"]
FORM_FEATURES = ([f"{side}_{s}" for side in ("white", "black") for s in FORM_STATS]
                 + [f"diff_{s}" for s in FORM_STATS])
FEATURES = RATING_FEATURES + FORM_FEATURES
# not features: marks rows whose player history was rebuilt from opponents' archives
FLAGS = ["white_history_from_opponents", "black_history_from_opponents"]


def elo_expected(diff):
    return 1.0 / (1.0 + 10.0 ** (-np.asarray(diff, dtype=float) / 400.0))


class History:
    """Each player's rated blitz games, sorted by (player, end_time), with prefix sums
    so any "games before time t" or "games in [t - 30d, t)" query is two binary searches."""

    SHIFT = 10**11  # > any unix timestamp; key = player_code * SHIFT + end_time

    def __init__(self, hist: pd.DataFrame):
        h = (hist[hist["rated"]]
             .drop_duplicates(["username", "game_url"])
             .sort_values(["username", "end_time"])
             .reset_index(drop=True))
        self.codes = {u: i for i, u in enumerate(h["username"].unique())}
        code = h["username"].map(self.codes).to_numpy(np.int64)
        self.code = code
        self.key = code * self.SHIFT + h["end_time"].to_numpy(np.int64)
        self.end_time = h["end_time"].to_numpy(np.int64)
        self.rating_post = h["rating_post"].to_numpy(float)

        # Ratings in the archives are post-game. The player's pre-game rating is their
        # previous game's post rating; the opponent's is approximated by undoing the
        # same rating change (rating changes are close to symmetric).
        same_player = np.r_[False, code[1:] == code[:-1]]
        pre = np.where(same_player, np.r_[np.nan, self.rating_post[:-1]], np.nan)
        delta = self.rating_post - pre
        opp_pre = h["opponent_rating_post"].to_numpy(float) + delta
        resid = h["score"].to_numpy(float) - elo_expected(pre - opp_pre)
        valid = ~np.isnan(resid)

        def prefix(x):
            return np.r_[0.0, np.cumsum(x)]
        self.c_games = prefix(np.ones(len(h)))
        self.c_draw = prefix(h["score"].to_numpy(float) == 0.5)
        self.c_resid = prefix(np.where(valid, resid, 0.0))
        self.c_resid_n = prefix(valid)
        self.c_delta = prefix(np.nan_to_num(delta))

    def player_features(self, players: pd.Series, t: pd.Series) -> pd.DataFrame:
        code = players.map(self.codes).fillna(-1).to_numpy(np.int64)
        t = t.to_numpy(np.int64)
        known = code >= 0
        # first index with end_time >= t (i.e. count of this player's games ending before t)
        hi = np.searchsorted(self.key, code * self.SHIFT + t, side="left")
        lo = np.searchsorted(self.key, code * self.SHIFT + t - FORM_WINDOW_DAYS * DAY, side="left")
        last = hi - 1
        has_prev = known & (last >= 0) & (self.code[np.clip(last, 0, None)] == code)
        lastc = np.clip(last, 0, None)

        n = self.c_games[hi] - self.c_games[lo]
        n_resid = self.c_resid_n[hi] - self.c_resid_n[lo]
        with np.errstate(invalid="ignore", divide="ignore"):
            out = pd.DataFrame({
                "rating_pre": np.where(has_prev, self.rating_post[lastc], np.nan),
                "last_game_end": np.where(has_prev, self.end_time[lastc], -1),
                "games_30d": np.where(known, n, 0),
                "score_vs_expected_30d": np.where(n_resid > 0,
                                                  (self.c_resid[hi] - self.c_resid[lo]) / n_resid, np.nan),
                "rating_change_30d": np.where(known, self.c_delta[hi] - self.c_delta[lo], 0.0),
                "draw_rate_30d": np.where(n > 0, (self.c_draw[hi] - self.c_draw[lo]) / n, np.nan),
                "days_since_last_game": np.where(has_prev, (t - self.end_time[lastc]) / DAY, np.nan),
            })
        return out


def load_history(games: pd.DataFrame) -> tuple[pd.DataFrame, set[str]]:
    """Player history, plus a reconstructed history for participants whose own archive
    the API hides (e.g. magnuscarlsen returns 404). Their games still appear in their
    opponents' archives, so those rows are added again from the hidden player's side.
    Their rating is exact; their form only covers games against other participants.
    A closed account (penguin_cm1) appears in nobody's archive and stays without history."""
    hist = pd.read_csv(PROCESSED / "player_history.csv",
                       usecols=["username", "opponent", "end_time", "game_url", "rating_post",
                                "opponent_rating_post", "score", "rated"])
    participants = set(games["white"]) | set(games["black"])
    hidden = participants - set(hist["username"])
    m = hist[hist["opponent"].str.lower().isin(hidden)]
    mirror = pd.DataFrame({"username": m["opponent"].str.lower(), "opponent": m["username"],
                           "end_time": m["end_time"], "game_url": m["game_url"],
                           "rating_post": m["opponent_rating_post"],
                           "opponent_rating_post": m["rating_post"],
                           "score": 1 - m["score"], "rated": m["rated"]})
    return pd.concat([hist, mirror], ignore_index=True), set(mirror["username"])


def build() -> tuple[pd.DataFrame, dict]:
    games = pd.read_csv(PROCESSED / "games.csv")
    hist, from_opponents = load_history(games)
    H = History(hist)

    df = games[KEYS + [LABEL]].copy()
    for side in ("white", "black"):
        df[f"{side}_history_from_opponents"] = games[side].isin(from_opponents)
        pf = H.player_features(games[side], games["start_time"])
        assert (pf["last_game_end"][pf["rating_pre"].notna()] < games["start_time"][pf["rating_pre"].notna()]).all()
        df[f"{side}_rating_pre"] = pf["rating_pre"]
        for s in FORM_STATS:
            df[f"{side}_{s}"] = pf[s]

    missing = df["white_rating_pre"].isna() | df["black_rating_pre"].isna()
    no_history = sorted((set(games["white"]) | set(games["black"])) - set(hist["username"]))
    report = {"games_in": len(df),
              "history_from_opponents": sorted(from_opponents),
              "participants_without_any_history": no_history,
              "dropped_no_pre_rating": int(missing.sum()),
              "dropped_by_round": {int(k): int(v) for k, v in df.loc[missing, "round"].value_counts().items()}}
    df = df[~missing].reset_index(drop=True)

    df["rating_diff"] = df["white_rating_pre"] - df["black_rating_pre"]
    df["rating_mean"] = (df["white_rating_pre"] + df["black_rating_pre"]) / 2
    df["elo_expected"] = elo_expected(df["rating_diff"])
    for s in FORM_STATS:
        df[f"diff_{s}"] = df[f"white_{s}"] - df[f"black_{s}"]

    df = df.sort_values(["start_time", "game_url"]).reset_index(drop=True)
    report["games_out"] = len(df)
    report["missing_share"] = {c: round(float(df[c].isna().mean()), 4) for c in FEATURES if df[c].isna().any()}
    report["games_with_history_from_opponents"] = int(
        (df["white_history_from_opponents"] | df["black_history_from_opponents"]).sum())
    return df[KEYS + FEATURES + FLAGS + [LABEL]], report


def leakage_checks(df: pd.DataFrame, n_sample: int = 300, seed: int = 0) -> dict:
    """Independent re-computation on a sample of games with plain pandas filters
    (end_time < start_time), compared with the vectorised table."""
    games = pd.read_csv(PROCESSED / "games.csv", usecols=["game_url", "white", "black", "start_time",
                                                          "round", "event_id", "white_rating_post",
                                                          "black_rating_post"])
    hist, _ = load_history(games)
    hist = hist[hist["rated"]].drop_duplicates(["username", "game_url"])
    by_user = dict(tuple(hist.groupby("username")))
    flagged = df[df["white_history_from_opponents"] | df["black_history_from_opponents"]]
    sample = pd.concat([df.sample(n_sample, random_state=seed), flagged.sample(20, random_state=seed)])
    mism = 0
    for r in sample.itertuples():
        for side in ("white", "black"):
            u = getattr(r, side)
            x = by_user[u]
            before = x[x["end_time"] < r.start_time].sort_values("end_time")
            win = before[before["end_time"] >= r.start_time - FORM_WINDOW_DAYS * DAY]
            ok = (before["rating_post"].iloc[-1] == getattr(r, f"{side}_rating_pre")
                  and len(win) == getattr(r, f"{side}_games_30d")
                  and (len(win) == 0 or abs((win["score"] == 0.5).mean() - getattr(r, f"{side}_draw_rate_30d")) < 1e-9))
            mism += not ok

    # The player's own game must never be inside its features: the game ends after it starts.
    own = games.merge(hist[["username", "game_url", "end_time"]],
                      left_on=["white", "game_url"], right_on=["username", "game_url"])
    own_after_start = bool((own["end_time"] > own["start_time"]).all())

    # Rounds 2+: pre-game rating should mostly equal the previous round's post-game rating
    # (differs only if the player fitted another rated blitz game in between).
    g = games.sort_values("start_time")
    long = pd.concat([g[["event_id", "round", "game_url", s, f"{s}_rating_post"]]
                      .set_axis(["event_id", "round", "game_url", "player", "post"], axis=1)
                      for s in ("white", "black")])
    long = long.sort_values(["event_id", "player", "round"])
    long["prev_post"] = long.groupby(["event_id", "player"])["post"].shift()
    long["prev_round"] = long.groupby(["event_id", "player"])["round"].shift()
    pre = pd.concat([df[["game_url", f"{s}", f"{s}_rating_pre"]].set_axis(["game_url", "player", "pre"], axis=1)
                     for s in ("white", "black")])
    m = long.merge(pre, on=["game_url", "player"])
    m = m[m["prev_round"] == m["round"] - 1]
    return {"sample_games": len(sample), "sample_mismatches": mism,
            "own_game_ends_after_start": own_after_start,
            "pre_rating_equals_prev_round_post": round(float((m["pre"] == m["prev_post"]).mean()), 4),
            "post_rating_columns_in_features": [c for c in df.columns if "post" in c]}


def main() -> None:
    df, report = build()
    df.to_csv(PROCESSED / "features.csv", index=False)
    report["checks"] = leakage_checks(df)
    (PROCESSED / "features_report.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))
    c = report["checks"]
    assert c["sample_mismatches"] == 0, "vectorised features disagree with the naive recomputation"
    assert c["own_game_ends_after_start"] and not c["post_rating_columns_in_features"]


if __name__ == "__main__":
    main()
