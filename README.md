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
