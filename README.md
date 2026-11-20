# Chess.com Titled Tuesday — Game Outcome Prediction

Predict the outcome of a blitz chess game (white win / draw / black win) using only
information available **before the game starts**, built on Chess.com's Published Data API.

**Scope:** this is a general game predictor. Titled Tuesday is only the data source, as the
brief says ("collect your dataset from recent Titled Tuesday tournaments"). So the features
describe the two players (rating and recent form), never the tournament: no round number,
standings or prize situation. Caveat: all training and test games are between strong titled
players at 5+0 in a Swiss event, so results hold for that population; casual games would
need casual training data.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Data collection

```bash
.venv/bin/python -m src.collect
```

Collects every 5+0 Titled Tuesday that finished in 2026. Responses are cached under
`data/raw/api/`, so re-runs are instant and an interrupted run resumes. Smoke test:
`python -m src.collect --max-events 2 --max-players 15`.

**How events are found:** the API has no endpoint listing tournaments, so the collector
starts from two known events plus a few regular players, then snowballs through each
participant's `/player/{u}/tournaments` history. It stops once 300 histories in a row
turn up no new 2026 event.

**Storage:** the API always returns full move text, which we never use. Responses are
reduced to the fields we need before caching: players, ratings, results, start/end time,
time control and tournament, blitz games only. Raw archives would take about 12 GB; the
compact cache is about 0.8 GB. A full run from scratch takes about 10 minutes
(archive downloads run 8 at a time).

**Missing rounds:** six round groups permanently return 404 (Aug 4 rounds 1/4/6, Aug 18
round 4, Aug 25 round 1, Jun 23 round 8). Their games are rebuilt from players' monthly
archives, which tag each game with its tournament. Games in a round start within about 2 s
of each other and rounds are about 10 min apart, so games are clustered by start time into
rounds. A rebuild is accepted only if the number of clusters equals the number of rounds
and every known game lands in its own round. All six passed: 1,146 games recovered, marked
`source = archive_rebuild` in `games.csv`; details in `rebuilt_rounds.json`.

**2026 collection result:** 46 events (every Tuesday from Jan 6 to Nov 17), 90,795 games,
2,768 players. Outcomes: 48.5% White wins, 8.5% draws, 42.9% Black wins.

**Outputs (`data/processed/`):**

| File | Contents |
|---|---|
| `events.csv` | One row per event: start/finish time, rounds, registered players |
| `games.csv` | One row per game: event, round, players, result label, start/end time, **post-game** ratings |
| `players.csv` | Profile per participant (title, country, joined, …) |
| `player_history.csv` | Each participant's blitz games from their monthly archives around each event (used for pre-event features) |
| `excluded_events.json` | Candidate events skipped and why |
| `rebuilt_rounds.json` | Rounds rebuilt from archives, with consistency checks |

## Features

```bash
.venv/bin/python -m src.features
```

Writes `data/processed/features.csv` (one row per game, about 6 s) and
`features_report.json` (dropped games, missing values, leakage checks).

**As-of rule:** every feature uses only the player's rated blitz games that **ended before
this game started**. Earlier rounds of the same event count like any other recent game.

| Group | Features |
|---|---|
| Rating | `white_rating_pre`, `black_rating_pre`, `rating_diff` (White − Black), `rating_mean`, `elo_expected` = 1 / (1 + 10^(−diff/400)) |
| Recent form, 30 days before the game, per player and White − Black (`diff_`) | `games_30d`, `score_vs_expected_30d` (mean actual − Elo-expected score), `rating_change_30d`, `draw_rate_30d` |

| Label | `outcome` ∈ {white, draw, black} |

`days_since_last_game` was considered and dropped: because earlier rounds count as recent
games, in rounds 2–11 it only measured the few minutes since the previous round.

`event_id`, `round`, `game_url`, `start_time`, `white`, `black` are kept as keys for the
split and for tracing rows, not as features.

**Pre-game ratings for opponents in history games:** archive ratings are post-game, so in
`score_vs_expected` the player's pre-game rating is their previous game's rating and the
opponent's is their post-game rating with the same change undone (rating changes are
close to symmetric).

**Gaps filled from opponents' archives:** every game between two participants appears in
both players' archives, so a game missing from a player's own archive is added from the
opponent's side (97,987 history rows). This covers archive months the API 404s on (5 months
stay 404 after retrying) and `magnuscarlsen`, whose whole archive returns 404
(`*_history_from_opponents = True`, 129 games): his rating is exact, but his form covers
only games against other participants (`games_30d` too low, `draw_rate_30d` skewed towards
titled opponents).

**Renamed account:** `penguin_cm1` has the same account uuid as `llawlietcanon`; the old
name appears in older tournament games and the new one in every archive. The two are
merged (`load_games` in `src/features.py`).

**Forfeits removed:** games of 5 moves or fewer (≤ 10 plies) or 10 seconds or less are
dropped: 1,955 games (2.2%). 981 are games where White never moved and 924 where Black
never replied, each lost on time after about 5 minutes: no-shows and disconnects. The rest
are resignations within 5 moves (118) and 3 agreed draws with no moves. None is a
checkmate. Their results say nothing about strength. They are removed from the test set
too, so the model is evaluated on games that were actually played; a deployed model would
need a separate no-show rate.

**Checks** (`features_report.json`, the build fails if they don't pass):
- 320 sampled games (20 of them Magnus's) recomputed with a plain "ended before start"
  filter: 0 mismatches.
- A game never appears in its own features (every game ends after it starts).
- Rounds 2–11: 99.97% of pre-game ratings equal the previous round's post-game rating; the
  rest played another blitz game in between.
- No post-game rating column in the table.

**Result:** 88,637 of 90,795 games: 1,955 forfeits and 203 games without a pre-game rating
dropped.

## Data quality audit

```bash
.venv/bin/python -m src.audit
```

Writes `results/data_audit.json`. Findings:
- **Clean:** no missing values or duplicates in `games.csv`; no player twice in a round or
  pairing repeated in an event; every result code pair consistent with the outcome; all
  games 5+0, rated, and inside their event's time window; 473,474 games seen from both
  players' archives, all with mirrored scores and identical ratings; rebuilt rounds have a
  normal outcome mix.
- **Fixed:** 14 archive months cached as 404 although listed in the player's archive index
  (9 downloaded on retry; the client now retries cached 404s for archive months), the
  renamed account, and forfeits (above).
- **Outliers kept, to handle in modelling:** 856 player-entries with a pre-game rating
  below 2,000 and 271 whose rating moved by more than 50 in one game: new accounts with
  provisional ratings that understate the player. `games_30d` is heavy-tailed (median 77,
  max 2,914). Extreme `score_vs_expected_30d` values come mostly from players with fewer
  than 10 recent games.

## Split and baseline

```bash
.venv/bin/python -m src.baseline
```

**Split** (`src/split.py`), by whole events in time order:
- **Test:** the last 6 events, Oct 13 – Nov 17 (12,144 games, 13.7%). Scored once, at the
  end, for the final comparison; nothing is tuned on it.
- **Train:** the first 40 events (76,493 games). Feature selection and tuning use
  expanding-window folds: fit on events 1..k, validate on the next 4 (k = 20, 24, …, 36:
  5 folds). Every validation game is later than every game it was fitted on.

**Elo baseline.** The Elo formula gives White's expected score
E = 1 / (1 + 10^(−diff/400)) = P(white) + ½·P(draw). The only thing learned is the
training draw rate d: P(draw) = d, P(white) = E − d/2, P(black) = 1 − E − d/2.

Cross-validation results (mean of 5 folds; results in `results/baseline_cv.json`):

| Model | Log loss | Brier | Accuracy |
|---|---|---|---|
| Class frequencies (floor) | 0.925 | 0.573 | 48.8% |
| **Elo formula** | **0.842** | **0.469** | **68.3%** |

Removing forfeits improved the Elo baseline from 0.860 to 0.842 log loss on its own.

**Elo is overconfident for these players** (training games, binned by E):

| Elo expected score for White | 0.0–0.1 | 0.1–0.2 | 0.2–0.3 | 0.7–0.8 | 0.8–0.9 | 0.9–1.0 |
|---|---|---|---|---|---|---|
| White's actual score | 0.154 | 0.248 | 0.339 | 0.716 | 0.797 | 0.885 |

Underdogs score about 0.09 more than Elo predicts and favourites about 0.04–0.05 less; the
gap is larger for White underdogs, consistent with White's first-move advantage, which Elo
ignores. Draws are 14–15% between evenly matched players and 5–6% in lopsided games, while
the baseline uses one draw rate. Few games are evenly matched: the Swiss system pairs the
top half against the bottom half early on. These are the patterns a trained model can learn.

## Exploratory analysis

```bash
.venv/bin/python -m src.eda
```

Training events only (76,493 games); the test events stay unseen. Plots in `results/eda/`.

| Plot | Finding |
|---|---|
| `1_score_vs_rating_diff.png` | **Elo is too confident at large gaps**: at −500 White scores 0.11 vs Elo's 0.05; at +500, 0.89 vs 0.95. At equal ratings (±50) White scores **0.529**: the first-move advantage. Rating differences are **bimodal** (peaks at ±200): Swiss pairing puts the top half against the bottom half, so evenly matched games are rare. |
| `2_draw_rate.png` | Draws fall with the rating gap (16% when within 50 points, 4% beyond 500) and **rise sharply above 3,000 average rating** (19% for close games at 3,000+, about 9% below 2,800). |
| `3_outcomes.png` | White 48.6%, draw 8.7%, Black 42.7%. The outcome mix moves smoothly with the rating gap. |
| `4_form_distributions.png` | `games_30d` is heavy-tailed (median 75, up to 2,900): used on a log scale. `rating_change_30d` is right-skewed (fast risers: new accounts). `draw_rate_30d` has a spike at 0 (players with few games). |
| `5_correlations.png` | Redundant pairs: `elo_expected` is a monotone transform of `rating_diff` (rank correlation 1.00); `white/black_rating_pre` are `rating_mean` ± `rating_diff`/2; `score_vs_expected_30d` and `rating_change_30d` correlate 0.83 per player. Form features are only weakly related to rating (|ρ| ≤ 0.3). |
| `6_residuals.png` | Residuals of a `rating_diff`-only model against other features (see below). |

**Does anything add signal beyond `rating_diff`?** Two checks, both out of sample:
1. **Residual plots:** fit a model on `rating_diff` alone, then plot what it gets wrong (actual
   − predicted, in validation folds) against each other feature. A flat line at 0 means the
   feature adds nothing.
2. **Incremental log loss:** cross-validated log loss of a logistic regression on
   `rating_diff` (0.789) vs `rating_diff` + one feature (`results/eda/incremental_signal.json`).

| Added to `rating_diff` | Δ CV log loss | Folds better |
|---|---|---|
| `rating_mean` (= `white_rating_pre` or `black_rating_pre`: same information) | −0.0014 | 5/5 |
| `diff_games_30d` | −0.0013 | 5/5 |
| `black_draw_rate_30d` / `white_draw_rate_30d` | −0.0010 / −0.0007 | 5/5 |
| `diff_score_vs_expected_30d` | −0.0003 | 4/5 |
| `elo_expected` | 0.0000 | 3/5 |
| `rating_change_30d` (any form) | ≥ 0 | ≤ 2/5 |
| **all of the above** | **−0.0039** (0.789 → 0.785) | **5/5** |

- **Among the rating features only `rating_mean` adds anything**, and mostly for draws: the
  draw residual jumps by +0.05 above 3,000. `elo_expected` adds nothing (same ordering as
  `rating_diff`), and the two pre-game ratings carry exactly `rating_mean` + `rating_diff`.
- **The more active player underperforms their rating** (`diff_games_30d`: about −0.035 score
  at the extremes). A plausible reading: frequent players' ratings are inflated by farming
  weaker opponents, while rarely-playing strong players are underrated.
- **Recent rating gains do not carry forward**: players up 100+ points in 30 days score
  slightly *below* prediction (regression to the mean), but the shape is not linear, so a
  linear model gains nothing from it.
- **Overall, form adds a little, consistently**: about 0.5% lower log loss than rating
  difference alone. Rating already contains most of what is predictable before the game.

## Models and tuning

```bash
.venv/bin/python -m src.model
```

**Inputs** (`MODEL_FEATURES` in `src/model.py`, chosen on the training folds with
`src/feature_selection.py`): `rating_diff`, `rating_mean`, `diff_games_30d`,
`white_draw_rate_30d`, `black_draw_rate_30d`.

**One preprocessing pipeline for every model** (scikit-learn `Pipeline`), learned only from
the data the model is fitted on: `diff_games_30d` on a signed log scale, missing draw rates
(players with no rated blitz game in the last 30 days, ~0.5% per side) filled with the
training median, all features standardised.

**Tuning on the 5 CV folds** (results in `results/model_cv.json`; the test set is not used):

| Model | Inputs | Grid | Chosen | CV log loss | Brier | Accuracy |
|---|---|---|---|---|---|---|
| Elo formula | rating difference | – | – | 0.8418 | 0.4691 | 68.3% |
| Logistic regression | `rating_diff` | C ∈ {0.001 … 100} | C = 0.01 | 0.7888 | 0.4580 | 68.3% |
| Logistic regression | 5 features | C ∈ {0.001 … 100} | C = 0.01 | 0.7851 | 0.4566 | 68.3% |
| Gradient-boosted trees | 5 features | 24 settings | learning rate 0.03, 7 leaves, ≥ 200 games per leaf | **0.7839** | **0.4563** | 68.3% |

- Regularisation barely matters for the logistic regressions (≤ 0.0001 across the grid):
  with ~60,000 games and 1–5 inputs there is little to overfit.
- Small trees win: 7 leaves beats 15 and 31. The trees stop early on their own
  (scikit-learn holds out 10% of the fitting data), which is why 100 and 300 rounds give the
  same result.
- The 5-feature models beat `rating_diff` alone in 5/5 folds. The trees beat the 5-feature
  regression in only 3/5 folds (by 0.002–0.003 in the three latest folds, worse by 0.0005 in
  the two earliest), so whether the trees' non-linearity helps is not settled on CV.
- Accuracy is identical across models: the most likely outcome is almost always "the
  higher-rated player wins". The models differ in how well their probabilities are
  calibrated, which log loss and Brier measure.

## Evaluation plan (fixed before scoring the test set)

Committed before the test set is scored, so the comparison can't be tuned to it.

1. **Models:** the four above, with the settings chosen on CV. Nothing else is tuned.
2. **Fit:** each model is refitted on all 40 training events (76,493 games), then scores the
   6 test events (Oct 13 – Nov 17, 12,144 games) **once**.
3. **Metrics:** log loss (primary), Brier score, accuracy; per-class results (how well draws
   are predicted); a calibration plot of predicted vs actual White win, draw and Black win
   rates.
4. **Uncertainty:** 1,000 bootstrap resamples of the test games. Each model is scored on the
   same resamples, giving 95% intervals for each model and for the paired differences
   (5-feature regression vs `rating_diff` only; trees vs 5-feature regression; each vs Elo).
   A difference counts as real only if its interval excludes 0. Caveat: games in one event
   share players, so these intervals are somewhat too narrow.
5. **Reported as is:** whatever the test result, including if the form features or the trees
   do not help.

## Data leakage rules

Found while exploring the API; the feature code enforces them:

- **Game ratings are post-game.** `white.rating` / `WhiteElo` already include that game's
  result (verified: 92% of round-to-round rating changes match the game they're attached to).
  Pre-game rating = rating after the player's previous game (previous round; for round 1,
  the last blitz game before the event in their archive).
- **Group `players[].points` is the final tournament score**, not the score going into a round.
  It is dropped at collection time (tournament standings are not features anyway).
- **Profile fields are current snapshots** (`title`, `status`, `is_streamer`, `verified`,
  `followers`, `last_online`, `league`). An IM who became a GM in November shows as GM for
  January. Excluded from the main model; `title` is tested only as an ablation. `country` and
  `joined` don't change.
- **`/player/{u}/stats` is not used**: it holds today's ratings and lifetime records.
- **Nothing produced during the game** is a feature: moves, FEN, ECO opening, termination, end time.
- **Every feature uses a cutoff at the game's start time**: only games that ended before it.
- **Player usernames are not a feature**; rating already captures player strength.
- **Games without a pre-game rating are dropped, not imputed** (203 games, 0.2%): a player
  has no earlier rated blitz game in our data (inactive, bullet/rapid only, new account, or
  their first game of the event came after a bye). The player's next post-game rating
  cannot stand in: it contains the result.
- **Train/test split is by event, in time order** (no random split across rounds of one event).
