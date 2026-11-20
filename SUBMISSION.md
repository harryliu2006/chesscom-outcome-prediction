# Submission notes

Option A: predict the outcome of a chess game (White win / draw / Black win) from pre-game
information only. Details, numbers and plots are in the [README](README.md).

**Reproduce everything with one command:** `./run.sh` (collection → features → audit →
baseline → EDA → feature selection → tuning → test evaluation). The first run downloads
from the API (about 10 minutes); later runs read the cache.

## Main decisions and trade-offs

**Data.** Every 5+0 Titled Tuesday of 2026 (46 events, Jan 6 – Nov 17, 90,795 games), not
only the two dates in the brief. The API has no tournament list, so events are found by
snowballing through participants' tournament histories. Six rounds return 404 permanently;
their 1,146 games were rebuilt from players' monthly archives, with consistency checks.

**Scope.** Treated as a general blitz game predictor, with Titled Tuesday as the data
source. Features describe the two players only (rating and recent form), never the
tournament (round, standings).

**Leakage**, the main risk, found while exploring the API:
- Ratings attached to a game are **post-game** (they include the result). Pre-game rating =
  rating after the player's previous game.
- Every feature uses only games that **ended before this game started**.
- Profile fields (title, followers, …) are today's snapshot, not the value at game time:
  excluded. The tournament `points` field is the final score: dropped.
- Checks in the build: a sample recomputed independently (0 mismatches), no post-game
  columns, and pre-game ratings matching the previous round's post-game rating 99.97% of
  the time.

**Cleaning.** A data audit found 1,955 forfeits (2.2%), almost all no-shows where one side
never moved and lost on time. They are removed from training *and* test, so the model is
evaluated on games actually played; a deployed model would need a separate no-show rate.
203 games where a player had no earlier rated blitz game are dropped, not imputed.
88,637 games remain.

**Split.** By whole events in time order: the last 6 events (12,144 games, 13.7%) are the
test set; the first 40 are training, with 5 expanding-window CV folds for feature selection
and tuning. The evaluation plan was committed before the test set was scored, and the test
set was scored once.

**Features.** EDA and CV tests on the training folds picked 5 inputs: `rating_diff`,
`rating_mean` (draws rise among top players), `diff_games_30d` (the more active player
underperforms their rating), and both players' 30-day draw rates. A redundancy test (all
subsets, drop-one) showed each still helps with the others present. Dropped:
`elo_expected` (same ordering as `rating_diff`), recent rating change and score vs expected
(no consistent gain).

**Models.** Elo formula (baseline), logistic regression on `rating_diff`, logistic
regression on the 5 features, and gradient-boosted trees on the 5 features. Kept simple on
purpose; one shared preprocessing pipeline fitted on training data only.

## Honest view of model quality

| Model (test set) | Log loss | Accuracy |
|---|---|---|
| Elo formula | 0.843 | 68.7% |
| Logistic regression, rating only | 0.788 | 68.8% |
| Logistic regression, 5 features | 0.785 | 68.7% |
| Boosted trees, 5 features | **0.783** | 68.8% |

- Every paired difference is significant (bootstrap 95% intervals exclude 0), and test
  scores match cross-validation within 0.002.
- **Rating does most of the work.** Fitting the rating difference to the data instead of
  using the Elo curve gives about 90% of the gain; recent form and the trees add a small,
  real improvement (0.0055 log loss).
- The probabilities are well calibrated; Elo is overconfident at large rating gaps.
- Accuracy does not move, and no model ever predicts a draw: even the most drawish
  pairings reach only ~15%. Draws are weakly predictable (AUC 0.60).
- Limitations: games within an event share players, so the intervals are somewhat too
  narrow; all games are between titled players at 5+0; new accounts have provisional
  ratings that understate the player; Magnus Carlsen's archive is hidden by the API, so
  his activity is undercounted.

## With more time

- **Try other form windows.** All recent-form features use a fixed 30-day window, a
  reasonable default that I didn't test. A 7-day window might capture current form better,
  and a 90-day window would be steadier for players who rarely play (it would need a
  longer archive history than the ~2 months collected per event).
- **Test on broader data.** Every game here is between titled players at 5+0. Training and
  testing on other time controls and non-titled games would show whether the model holds
  up beyond Titled Tuesday.

## Time breakdown

I worked on this in between classes and other commitments, from the first commit (Nov 19,
afternoon) to the last (Nov 20, early morning), so the elapsed time in the git history is
much longer than the time actually spent on it.

The split between parts, in order of effort:
1. **API exploration, collector and data collection at scale**: the largest share. The API
   has no tournament list, six rounds return 404 and had to be rebuilt from archives, and
   the raw archives filled the disk, so they had to be stored compactly.
2. **Features, leakage checks, data audit and fixes.**
3. **Split, baseline, EDA and feature selection.**
4. **Modelling, evaluation and write-up.**

## AI usage

Built with Claude Code (Claude Opus 5.5) as a pair programmer: I made the scoping, feature,
cleaning and evaluation decisions; the agent wrote most of the code and ran the analyses.
Agent transcripts are in `transcripts/`.
