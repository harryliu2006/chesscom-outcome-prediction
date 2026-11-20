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

`days_since_last_game` was considered and dropped: because earlier rounds count as recent
games, in rounds 2–11 it only measured the few minutes since the previous round.
| Label | `outcome` ∈ {white, draw, black} |

`event_id`, `round`, `game_url`, `start_time`, `white`, `black` are kept as keys for the
split and for tracing rows, not as features.

**Pre-game ratings for opponents in history games:** archive ratings are post-game, so in
`score_vs_expected` the player's pre-game rating is their previous game's rating and the
opponent's is their post-game rating with the same change undone (rating changes are
close to symmetric).

**Hidden archives:** the API returns 404 for `magnuscarlsen`'s game archive. His games are
still in his opponents' archives, so his history is rebuilt from their side
(`*_history_from_opponents = True`, 129 games). His rating is exact; his form covers only
games against other participants (`games_30d` too low, `draw_rate_30d` skewed towards
titled opponents). `penguin_cm1` is a closed account that appears in no archive; its 73
games are dropped.

**Checks** (`features_report.json`, the build fails if they don't pass):
- 320 sampled games (20 of them Magnus's) recomputed with a plain "ended before start"
  filter: 0 mismatches.
- A game never appears in its own features (every game ends after it starts).
- Rounds 2–11: 99.8% of pre-game ratings equal the previous round's post-game rating; the
  rest played another blitz game in between.
- No post-game rating column in the table.

**Result:** 90,495 of 90,795 games (300 dropped, 0.33%).

## Split and baseline

```bash
.venv/bin/python -m src.baseline
```

**Split** (`src/split.py`), by whole events in time order:
- **Test:** the last 6 events, Oct 13 – Nov 17 (12,461 games, 13.8%). Scored once, at the
  end, for the final comparison; nothing is tuned on it.
- **Train:** the first 40 events (78,034 games). Feature selection and tuning use
  expanding-window folds: fit on events 1..k, validate on the next 4 (k = 20, 24, …, 36:
  5 folds). Every validation game is later than every game it was fitted on.

**Elo baseline.** The Elo formula gives White's expected score
E = 1 / (1 + 10^(−diff/400)) = P(white) + ½·P(draw). The only thing learned is the
training draw rate d: P(draw) = d, P(white) = E − d/2, P(black) = 1 − E − d/2.

Cross-validation results (mean of 5 folds; results in `results/baseline_cv.json`):

| Model | Log loss | Brier | Accuracy |
|---|---|---|---|
| Class frequencies (floor) | 0.922 | 0.572 | 48.8% |
| **Elo formula** | **0.860** | **0.479** | **67.5%** |

**Elo is overconfident for these players** (training games, binned by E):

| Elo expected score for White | 0.0–0.1 | 0.1–0.2 | 0.2–0.3 | 0.7–0.8 | 0.8–0.9 | 0.9–1.0 |
|---|---|---|---|---|---|---|
| White's actual score | 0.165 | 0.255 | 0.346 | 0.706 | 0.788 | 0.878 |

Underdogs score about 0.10 more than Elo predicts and favourites about 0.05–0.06 less; the
gap is larger for White underdogs, consistent with White's first-move advantage, which Elo
ignores. Draws are 14–15% between evenly matched players and 5–6% in lopsided games, while
the baseline uses one draw rate. Few games are evenly matched: the Swiss system pairs the
top half against the bottom half early on. These are the patterns a trained model can learn.

## Rating-only model

```bash
.venv/bin/python -m src.models
```

Multinomial logistic regression on rating features only (`src/models.py`), same 5 CV
folds (results in `results/rating_lr_cv.json`):

| Model | Inputs | Log loss | Brier | Accuracy |
|---|---|---|---|---|
| Elo formula | – | 0.860 | 0.479 | 67.5% |
| Logistic regression | `rating_diff` | 0.797 | 0.465 | 67.5% |
| Logistic regression | + `abs(rating_diff)` | 0.797 | 0.465 | 67.5% |
| **Logistic regression (chosen)** | **`rating_diff`, `rating_mean`** | **0.795** | **0.465** | **67.5%** |

- **Learning from ratings beats the Elo formula by 0.065 log loss**, in every fold
  (−0.051 to −0.087). It fixes Elo's overconfidence: per rating point the fitted model
  moves the odds about 3/4 as much as Elo does, and it learns White's first-move advantage,
  worth about 36 rating points.
- **`abs(rating_diff)` is not needed.** With three classes, the draw probability already peaks
  for evenly matched players, because the win and loss probabilities pull in opposite
  directions as the rating gap grows.
- **`rating_mean` helps a little, in every fold** (about −0.0017): draws are more common between
  stronger players.
- **Accuracy doesn't move** (67.5%): the model still picks the higher-rated player, so the
  same games are "right". The gain is in the probabilities. That's why log loss is the main
  metric.
- Calibration on the validation folds is close: e.g. where Elo says 0.07 for White, the
  model says 0.15 and White actually scores 0.17; where Elo says 0.93, the model says 0.88
  and White scores 0.87.

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
- **Games without a pre-game rating are dropped, not imputed** (300 games, 0.33%). 227 have a
  player with no earlier rated blitz game in our archives (inactive, bullet/rapid only, new
  account, or their first game of the event came after a bye); 73 involve the closed account
  `penguin_cm1`. The player's next post-game rating cannot stand in: it contains the result.
- **Train/test split is by event, in time order** (no random split across rounds of one event).
