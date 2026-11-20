# Detailed report

Every step of the project, with the numbers and plots behind the [README](README.md). Each
section starts with the command that runs it; `./run.sh` runs them all in order (setup:
`python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`).

## Data collection

```bash
.venv/bin/python -m src.collect
```

Collects every 5+0 Titled Tuesday that finished in 2026. Responses are cached under
`data/raw/api/`, so re-runs are instant and an interrupted run resumes. Smoke test:
`python -m src.collect --max-events 2 --max-players 15`.

**Finding events:** the API has no endpoint listing tournaments, so the collector starts
from two known events plus a few regular players, then snowballs through each participant's
`/player/{u}/tournaments` history, stopping once 300 histories in a row turn up no new 2026
event.

**Storage:** the API always returns full move text, which is never used, so responses are
reduced to the needed fields (players, ratings, results, times, time control, tournament;
blitz only) before caching: about 0.8 GB instead of 12 GB. A full run takes about 10
minutes (archive downloads run 8 at a time).

**Missing rounds:** six round groups permanently return 404 (Aug 4 rounds 1/4/6, Aug 18
round 4, Aug 25 round 1, Jun 23 round 8). Their games are rebuilt from players' monthly
archives, which tag each game with its tournament. Games in a round start within about 2 s
of each other and rounds are about 10 min apart, so games are clustered by start time. A
rebuild is accepted only if the number of clusters equals the number of rounds and every
known game lands in its own round. All six passed: 1,146 games recovered
(`source = archive_rebuild` in `games.csv`).

**Result:** 46 events (every Tuesday, Jan 6 – Nov 17), 90,795 games, 2,768 players.
Outcomes: 48.5% White wins, 8.5% draws, 42.9% Black wins.

**Outputs (`data/processed/`):** `events.csv`, `games.csv` (one row per game, with
**post-game** ratings), `players.csv` (profiles), `player_history.csv` (participants'
blitz games around each event, for form features), `excluded_events.json`,
`rebuilt_rounds.json`.

## Features

```bash
.venv/bin/python -m src.features
```

Writes `data/processed/features.csv` (one row per game) and `features_report.json`.

**As-of rule:** every feature uses only the player's rated blitz games that **ended before
this game started**. Earlier rounds of the same event count like any other recent game.

| Group | Features |
|---|---|
| Rating | `white_rating_pre`, `black_rating_pre`, `rating_diff` (White − Black), `rating_mean`, `elo_expected` |
| Recent form (30 days, per player and White − Black `diff_`) | `games_30d`, `score_vs_expected_30d`, `rating_change_30d`, `draw_rate_30d` |
| Label | `outcome` ∈ {white, draw, black} |

`days_since_last_game` was dropped: in rounds 2–11 it only measured the minutes since the
previous round.

**Data fixes:**
- **Archive gaps:** a game missing from a player's archive is added from the opponent's
  (97,987 rows). This covers 5 archive months that stay 404 and `magnuscarlsen`, whose
  whole archive returns 404: his rating is exact, but his form only covers games against
  other participants (129 games).
- **Renamed account:** `penguin_cm1` and `llawlietcanon` share an account uuid and are
  merged.
- **Forfeits removed:** games of ≤ 10 plies or ≤ 10 seconds: 1,955 games (2.2%). 1,905 are
  no-shows or disconnects (one side never moved and lost on time), 118 resignations within
  5 moves, 3 agreed draws with no moves; none is a checkmate. Removed from the test set too,
  so the model is evaluated on games actually played; a deployed model would need a
  separate no-show rate.

**Checks** (the build fails if they don't pass):
- 320 sampled games (20 of them Magnus's) recomputed independently: 0 mismatches.
- Rounds 2–11: 99.97% of pre-game ratings equal the previous round's post-game rating; the
  rest played another blitz game in between.
- No post-game rating column in the table.

**Result:** 88,637 games (1,955 forfeits and 203 games without a pre-game rating dropped).

## Data quality audit

```bash
.venv/bin/python -m src.audit
```

Results in `results/data_audit.json`.
- **Clean:** no missing values or duplicates; no player twice in a round; result codes
  consistent with outcomes; all games 5+0, rated and inside their event's window; 473,474
  games seen from both sides with mirrored scores and identical ratings.
- **Fixed:** 14 archive months wrongly cached as 404 (9 recovered on retry), the renamed
  account, forfeits.
- **Kept, handled in modelling:** 856 player-entries rated below 2,000 and 271 with a
  50+ point swing in one game (new accounts with provisional ratings); heavy-tailed
  `games_30d` (median 77, max 2,914).

## Split and baseline

```bash
.venv/bin/python -m src.baseline
```

**Split** (`src/split.py`), by whole events in time order:
- **Test:** the last 6 events, Oct 13 – Nov 17 (12,144 games, 13.7%), scored once at the end.
- **Train:** the first 40 events (76,493 games), with 5 expanding-window CV folds: fit on
  events 1..k, validate on the next 4 (k = 20, 24, …, 36).

**Elo baseline:** White's expected score E = 1 / (1 + 10^(−diff/400)) = P(white) +
½·P(draw). The only learned value is the training draw rate d: P(draw) = d, P(white) =
E − d/2, P(black) = 1 − E − d/2.

| CV (mean of 5 folds) | Log loss | Brier | Accuracy |
|---|---|---|---|
| Class frequencies (floor) | 0.925 | 0.573 | 48.8% |
| **Elo formula** | **0.842** | **0.469** | **68.3%** |

Removing forfeits alone improved Elo from 0.860 to 0.842.

## Exploratory analysis

```bash
.venv/bin/python -m src.eda
```

Training events only (76,493 games). Plots in `results/eda/`.

| Plot | Finding |
|---|---|
| `1_score_vs_rating_diff.png` | **Elo is too confident at large gaps**: at −500 White scores 0.11 vs Elo's 0.05; at +500, 0.89 vs 0.95. At equal ratings (±50) White scores 0.529: the first-move advantage. Rating gaps are **bimodal** (peaks at ±200) because Swiss pairing puts the top half against the bottom half. |
| `2_draw_rate.png` | Draws fall with the rating gap (16% within 50 points, 4% beyond 500) and **rise above 3,000 average rating** (19% for close games at 3,000+, about 9% below 2,800). |
| `3_outcomes.png` | White 48.6%, draw 8.7%, Black 42.7%; the mix moves smoothly with the rating gap. |
| `4_form_distributions.png` | `games_30d` is heavy-tailed (used on a log scale); `rating_change_30d` right-skewed (new accounts); `draw_rate_30d` spikes at 0 (few games). |
| `5_correlations.png` | `elo_expected` is a monotone transform of `rating_diff`; the two pre-game ratings are `rating_mean` ± `rating_diff`/2; form features are only weakly related to rating (\|ρ\| ≤ 0.3). |
| `6_residuals.png` | Errors of a `rating_diff`-only model against each other feature (a flat line = no added signal). |

**Signal beyond `rating_diff`:** CV log loss of a logistic regression on `rating_diff`
(0.789) vs `rating_diff` + one feature (`results/eda/incremental_signal.json`):

| Added to `rating_diff` | Δ CV log loss | Folds better |
|---|---|---|
| `rating_mean` | −0.0014 | 5/5 |
| `diff_games_30d` | −0.0013 | 5/5 |
| `black_draw_rate_30d` / `white_draw_rate_30d` | −0.0010 / −0.0007 | 5/5 |
| `diff_score_vs_expected_30d` | −0.0003 | 4/5 |
| `elo_expected` | 0.0000 | 3/5 |
| `rating_change_30d` (any form) | ≥ 0 | ≤ 2/5 |
| **all of the above** | **−0.0039** (0.789 → 0.785) | **5/5** |

- `rating_mean` helps mostly with draws, which jump above 3,000.
- **The more active player underperforms their rating** (about −0.035 score at the
  extremes); one possible reading is that frequent players' ratings are inflated by
  farming weaker opponents.
- Recent rating gains don't carry forward (slight regression to the mean), and not
  linearly, so a linear model gains nothing from them.

## Models and tuning

```bash
.venv/bin/python -m src.model
```

**Inputs** (chosen on the training folds by `src/feature_selection.py`, which also tests
every subset and drop-one): `rating_diff`, `rating_mean`, `diff_games_30d`,
`white_draw_rate_30d`, `black_draw_rate_30d`.

**Preprocessing** (one scikit-learn `Pipeline`, fitted only on the training data of each
fit): signed log of `diff_games_30d`, missing draw rates (~0.5% per side) filled with the
training median, all features standardised.

**Tuning on the 5 CV folds** (`results/model_cv.json`):

| Model | Inputs | Chosen | CV log loss | Brier | Accuracy |
|---|---|---|---|---|---|
| Elo formula | rating difference | – | 0.8418 | 0.4691 | 68.3% |
| Logistic regression | `rating_diff` | C = 0.01 | 0.7888 | 0.4580 | 68.3% |
| Logistic regression | 5 features | C = 0.01 | 0.7851 | 0.4566 | 68.3% |
| Gradient-boosted trees | 5 features | learning rate 0.03, 7 leaves, ≥ 200 games per leaf (24 settings tried) | **0.7839** | **0.4563** | 68.3% |

- Regularisation barely matters (≤ 0.0001 across C = 0.001 … 100); small trees win (7
  leaves beats 15 and 31).
- The 5-feature models beat `rating_diff` alone in 5/5 folds. The trees beat the 5-feature
  regression in only 3/5, so the value of their non-linearity is not settled on CV.
- Accuracy is identical across models: the top prediction is almost always "the
  higher-rated player wins". Log loss and Brier measure what differs: the probabilities.

## Evaluation plan (fixed before scoring the test set)

Committed before the test set was scored, so the comparison can't be tuned to it.

1. **Models:** the four above, with the settings chosen on CV. Nothing else is tuned.
2. **Fit:** each model is refitted on all 40 training events, then scores the 6 test events
   **once**.
3. **Metrics:** log loss (primary), Brier, accuracy, per-class results, calibration plot.
4. **Uncertainty:** 1,000 bootstrap resamples of the test games, shared by all models, for
   95% intervals on each model and on the paired differences. Caveat: games in one event
   share players, so these intervals are somewhat too narrow.
5. **Reported as is:** whatever the result.

## Test results

```bash
.venv/bin/python -m src.evaluate
```

Run once, as planned. Results in `results/test_results.json`.

| Model | Log loss (95% CI) | Brier | Accuracy |
|---|---|---|---|
| Elo formula | 0.8429 (0.825–0.861) | 0.4666 | 68.7% |
| Logistic regression, `rating_diff` only | 0.7881 (0.776–0.801) | 0.4568 | 68.8% |
| Logistic regression, 5 features | 0.7851 (0.773–0.797) | 0.4559 | 68.7% |
| Boosted trees, 5 features | 0.7827 (0.770–0.795) | 0.4549 | 68.8% |

Test scores match CV within 0.001–0.002 for every model: no sign of overfitting to the
folds or of drift between Jan–Sep and Oct–Nov.

**Paired differences** (same bootstrap resamples for every model):

| Comparison | Log loss difference (95% CI) |
|---|---|
| Rating-only regression vs Elo formula | −0.0547 (−0.066 to −0.044) |
| 5 features vs rating only (both regressions) | −0.0031 (−0.0046 to −0.0015) |
| Trees vs regression (both 5 features) | −0.0024 (−0.0037 to −0.0011) |
| Trees (5 features) vs rating-only regression | −0.0055 (−0.0074 to −0.0036) |

Every interval excludes 0, but they treat games as independent; with shared players and
only 6 test events they are too narrow. The gain over Elo (0.055) is large enough to
survive that. The form features' gain also held on 5/5 CV folds, so it looks consistent;
the trees beat the regression on only 3/5 CV folds, so the two are best treated as about
equal.

**Per outcome** (boosted trees; `results/test_calibration.png`):

| | White wins | Draw | Black wins |
|---|---|---|---|
| Actual rate | 48.2% | 8.6% | 43.2% |
| Mean predicted | 48.5% | 8.7% | 42.8% |
| AUC (one outcome vs the rest) | 0.775 | 0.600 (Elo: 0.512) | 0.779 |
| Predicted as most likely | 6,156 games | 0 | 5,988 games |

- **Calibration:** both 5-feature models stay close to the diagonal for all three
  outcomes (the draw curve is noisier); Elo is too confident at both ends.
- **No model ever predicts a draw** as the most likely result: even the most drawish
  pairings reach only about 15%. Draws are weakly predictable (AUC 0.60, vs 0.51 for Elo).
- The 31% of games "got wrong" are the 8.6% draws plus about 22.6% upsets where the
  favoured side lost.

## Data leakage rules

Found while exploring the API; the feature code enforces them.

- **Game ratings are post-game:** `white.rating` / `WhiteElo` include that game's result
  (92% of round-to-round rating changes match the attached game). Pre-game rating = rating
  after the player's previous game; games with none are dropped (203), never filled with
  the next post-game rating.
- **Group `players[].points` is the final tournament score**: dropped at collection.
- **Profile fields are today's snapshot** (`title`, `followers`, `status`, …: an IM who
  became a GM in November shows as GM for January): excluded; `/player/{u}/stats` (today's
  ratings) is not used either.
- **Nothing produced during the game** is a feature: moves, opening, termination, end time.
- **Usernames are not a feature**; rating already captures strength.
