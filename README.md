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

**Outputs (`data/processed/`):**

| File | Contents |
|---|---|
| `events.csv` | One row per event: start/finish time, rounds, registered players |
| `games.csv` | One row per game: event, round, players, result label, start/end time, **post-game** ratings |
| `players.csv` | Profile per participant (title, country, joined, …) |
| `player_history.csv` | Each participant's blitz games from their monthly archives around each event (used for pre-event features) |
| `excluded_events.json` | Candidate events skipped and why |

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
- **Train/test split is by event, in time order** (no random split across rounds of one event).
