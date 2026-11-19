# Chess.com Titled Tuesday — Game Outcome Prediction

Predict the outcome of a Titled Tuesday blitz game (white win / draw / black win) using only
information available **before the game starts**, built on Chess.com's Published Data API.

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
2,768 players. Outcomes: 48.5% White wins, 8.5% draws, 42.9% Black wins. 99.1% of
round-1 player entries have a blitz game before the event to take a pre-game rating from.

**Outputs (`data/processed/`):**

| File | Contents |
|---|---|
| `events.csv` | One row per event: start/finish time, rounds, registered players |
| `games.csv` | One row per game: event, round, players, result label, start/end time, **post-game** ratings |
| `players.csv` | Profile per participant (title, country, joined, …) |
| `player_history.csv` | Each participant's blitz games from their monthly archives around each event (used for pre-event features) |
| `excluded_events.json` | Candidate events skipped and why |
| `rebuilt_rounds.json` | Rounds rebuilt from archives, with consistency checks |

## Data leakage rules

Found while exploring the API; the feature code enforces them:

- **Game ratings are post-game.** `white.rating` / `WhiteElo` already include that game's
  result (verified: 92% of round-to-round rating changes match the game they're attached to).
  Pre-game rating = rating after the player's previous game (previous round; for round 1,
  the last blitz game before the event in their archive).
- **Group `players[].points` is the final tournament score**, not the score going into a round.
  Running scores are computed from earlier rounds only.
- **Profile fields are current snapshots** (`title`, `status`, `is_streamer`, `verified`,
  `followers`, `last_online`, `league`). An IM who became a GM in November shows as GM for
  January. Excluded from the main model; `title` is tested only as an ablation. `country` and
  `joined` don't change.
- **`/player/{u}/stats` is not used**: it holds today's ratings and lifetime records.
- **Nothing produced during the game** is a feature: moves, FEN, ECO opening, termination, end time.
- **Archive-based features use a cutoff at the event's start time**; within-event features use rounds before *r* only.
- **Player usernames are not a feature**; rating already captures player strength.
- **Round-1 games without a pre-game rating are dropped** (165 of 8,918 round-1 games, 0.18%
  of all games). In these games one player has no blitz game in the 6 months before the
  event: they were inactive, played only bullet/rapid, had a new account, or their archive
  is hidden by the API. The post-round-1 rating cannot stand in, because it contains the result.
- **Train/test split is by event, in time order** (no random split across rounds of one event).
