"""Collect every 2026 5+0 Titled Tuesday from Chess.com's Published Data API.

Usage:
    python -m src.collect                  # full run (resumable; cached in data/raw/api)
    python -m src.collect --max-events 2   # quick smoke test

Steps
  1. discover  - find 2026 Titled Tuesday events by snowballing through players'
                 tournament histories (the API has no "list tournaments" endpoint).
  2. games     - every round/group/game of each event.
  3. archives  - monthly game archives around each event, so features (pre-game
                 rating for round 1, recent form, head-to-head) can be computed
                 strictly from games that ended *before* the event started.
  3b. rebuild  - some round groups 404 permanently; their games are recovered
                 from players' archives (clustered into rounds by start time).
  4. profiles  - /player/{u} for every participant (title etc. are *current
                 snapshots*; flagged as such in players.csv).
  5. build     - write tidy CSVs to data/processed/.

Leakage notes (see README): game ratings in the API are POST-game; group
``players[].points`` is the FINAL tournament score; neither is written to the
processed tables as a feature. Nothing here computes features - that happens in
src/features.py with explicit as-of cutoffs.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .api import ApiError, ChessComClient

ROOT = Path(__file__).resolve().parent.parent
PROCESSED = ROOT / "data" / "processed"
FAILURES = ROOT / "data" / "raw" / "failures.json"

YEAR = 2026
TIME_CONTROL = "300"  # 5+0 blitz
SEED_USERS = ["hikaru", "javokhir_sindarov05", "lachesisq", "gmwso", "konavets"]
SEED_EVENTS = [
    "titled-tuesday-blitz-february-10-2026-6221327",
    "titled-tuesday-blitz-march-10-2026-6277141",
]
DRAW_CODES = {"agreed", "repetition", "stalemate", "insufficient", "50move", "timevsinsufficient"}
ARCHIVE_LOOKBACK_MONTHS = 6  # how far back to search for a player's last pre-event blitz game
ARCHIVE_WORKERS = 8  # parallel archive downloads (~10x faster than serial; no 429s observed)

failures: list[dict] = []
missing_rounds: list[dict] = []  # round groups the API 404s on; rebuilt from player archives
ROUND_GAP_SECONDS = 120  # games of one round start within ~2s of each other; rounds are ~10 min apart


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def safe_get(client: ChessComClient, url: str, what: str, **kw):
    try:
        return client.get(url, **kw)
    except ApiError as e:
        failures.append({"what": what, "url": url, "error": str(e)})
        log(f"  ! giving up on {what}: {e}")
        return None


ARCHIVE_DIR = ROOT / "data" / "raw" / "archives"  # compact per player-month files


# ---------------------------------------------------------------- compact storage
# The API always returns full move text (PGN). We never use moves, so only the
# fields below are cached: ~50x smaller than the raw responses.
def pgn_header(pgn: str, key: str) -> str | None:
    m = re.search(rf'^\[{key} "([^"]*)"\]', pgn or "", re.M)
    return m.group(1) if m else None


def utc_ts(date: str | None, time_: str | None) -> int | None:
    if not date or not time_:
        return None
    return int(datetime.strptime(f"{date} {time_}", "%Y.%m.%d %H:%M:%S")
               .replace(tzinfo=timezone.utc).timestamp())


def game_start(g: dict) -> int | None:
    if "start_time" in g:
        return g["start_time"]
    pgn = g.get("pgn", "")
    return utc_ts(pgn_header(pgn, "UTCDate"), pgn_header(pgn, "UTCTime"))


def compact_game(g: dict) -> dict:
    side = lambda s: {k: s.get(k) for k in ("username", "rating", "result", "uuid")}
    return {"url": g.get("url"), "start_time": game_start(g), "end_time": g.get("end_time"),
            "time_control": g.get("time_control"), "time_class": g.get("time_class"),
            "rules": g.get("rules"), "rated": g.get("rated"), "tournament": g.get("tournament"),
            "white": side(g["white"]), "black": side(g["black"])}


def compact_archive(data: dict) -> dict:
    """Keep only standard-chess blitz games, without moves."""
    return {"games": [compact_game(g) for g in data.get("games", [])
                      if g.get("time_class") == "blitz" and g.get("rules") == "chess"]}


def compact_group(data: dict) -> dict:
    """Keep the games without moves; drop players[].points (final score = leakage)."""
    return {"games": [compact_game(g) for g in data.get("games", [])], "compact": True}


def compact_cached_groups() -> None:
    """One-off: strip move text from tournament groups cached by earlier runs."""
    n = 0
    for f in (ROOT / "data" / "raw" / "api" / "pub" / "tournament").glob("*/*/*.json"):
        d = json.loads(f.read_text())
        if "games" in d and not d.get("compact"):
            f.write_text(json.dumps(compact_group(d)))
            n += 1
    if n:
        log(f"compacted {n} cached tournament group files")


def event_id(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def is_candidate(eid: str) -> bool:
    return "titled-tuesday" in eid and f"-{YEAR}-" in eid


# ---------------------------------------------------------------- 1+2. events & games
def validate_event(t: dict) -> str | None:
    """Return a reason to exclude the event, or None if it is in scope."""
    s = t.get("settings", {})
    start = t.get("start_time")
    if t.get("status") != "finished":
        return f"status={t.get('status')}"
    if not start or datetime.fromtimestamp(start, timezone.utc).year != YEAR:
        return "not in year"
    if str(s.get("time_control")) != TIME_CONTROL:
        return f"time_control={s.get('time_control')}"
    if str(t.get("creator", "")).lower() != "chesscom":
        return f"creator={t.get('creator')}"
    return None


def fetch_event_games(client: ChessComClient, eid: str, t: dict) -> list[dict]:
    games = []
    for rurl in t.get("rounds", []):
        rnd = int(rurl.rstrip("/").rsplit("/", 1)[-1])
        r = safe_get(client, rurl, f"{eid} round {rnd}")
        if not r:
            continue
        for gurl in r.get("groups", []):
            g = safe_get(client, gurl, f"{eid} round {rnd} group", transform=compact_group)
            if not g:
                missing_rounds.append({"event_id": eid, "round": rnd, "url": gurl})
                log(f"  ! {eid} round {rnd}: group unavailable (will rebuild from archives)")
                continue
            for game in g.get("games", []):
                game["_event_id"] = eid
                game["_round"] = rnd
                games.append(game)
    return games


def discover_and_collect(client: ChessComClient, max_events: int | None, patience: int):
    events: dict[str, dict] = {}
    games: dict[str, list[dict]] = {}
    excluded: dict[str, str] = {}
    checked: set[str] = set()
    appearances: Counter = Counter()  # player -> #in-scope events (prioritises the snowball)
    visited: set[str] = set()

    def consider(eid: str) -> bool:
        if eid in checked or not is_candidate(eid):
            return False
        if max_events and len(events) >= max_events:
            return False
        checked.add(eid)
        t = safe_get(client, f"tournament/{eid}", f"tournament {eid}")
        if t is None:
            excluded[eid] = "fetch failed / missing"
            return False
        reason = validate_event(t)
        if reason:
            excluded[eid] = reason
            return False
        events[eid] = t
        games[eid] = fetch_event_games(client, eid, t)
        players = {g[c]["username"].lower() for g in games[eid] for c in ("white", "black")}
        appearances.update(players)
        log(f"+ {eid}: {len(games[eid])} games, {len(players)} players "
            f"({len(events)} events, {client.n_network} requests so far)")
        return True

    for eid in SEED_EVENTS:
        consider(eid)
    for u in SEED_USERS:
        appearances[u] += 0

    dry = 0
    while dry < patience and not (max_events and len(events) >= max_events):
        frontier = [(n, u) for u, n in appearances.items() if u not in visited]
        if not frontier:
            break
        _, u = max(frontier)
        visited.add(u)
        hist = safe_get(client, f"player/{u}/tournaments", f"tournaments {u}") or {}
        found = False
        for status in ("finished", "in_progress", "registered"):
            for item in hist.get(status, []):
                found |= consider(event_id(item.get("@id") or item.get("url", "")))
        dry = 0 if found else dry + 1
    log(f"discovery done: {len(events)} events, {len(visited)} player histories checked, "
        f"{len(excluded)} candidates excluded")
    return events, games, excluded


# ---------------------------------------------------------------- 3. profiles
PROFILE_FIELDS = ["username", "player_id", "title", "country", "joined", "status",
                  "is_streamer", "verified", "followers", "last_online", "league"]


def fetch_profiles(client: ChessComClient, usernames: list[str]) -> pd.DataFrame:
    rows = []
    for i, u in enumerate(usernames, 1):
        p = safe_get(client, f"player/{u}", f"profile {u}") or {}
        row = {k: p.get(k) for k in PROFILE_FIELDS}
        row["username"] = u
        row["country"] = (row["country"] or "").rsplit("/", 1)[-1] or None
        row["profile_found"] = bool(p)
        rows.append(row)
        if i % 250 == 0:
            log(f"  profiles {i}/{len(usernames)} ({client.n_network} requests)")
    df = pd.DataFrame(rows)
    df["profile_fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return df


# ---------------------------------------------------------------- 4. archives
def month_key(ts: int) -> tuple[int, int]:
    d = datetime.fromtimestamp(ts, timezone.utc)
    return d.year, d.month


def prev_month(ym: tuple[int, int]) -> tuple[int, int]:
    y, m = ym
    return (y - 1, 12) if m == 1 else (y, m - 1)


def score_for(side: dict) -> float:
    if side["result"] == "win":
        return 1.0
    return 0.5 if side["result"] in DRAW_CODES else 0.0


def archive_rows(u: str, month_json: dict) -> list[dict]:
    rows = []
    for g in month_json.get("games", []):
        if g.get("time_class") != "blitz" or g.get("rules") != "chess":
            continue
        w, b = g["white"], g["black"]
        if w["username"].lower() == u:
            me, opp, color = w, b, "white"
        elif b["username"].lower() == u:
            me, opp, color = b, w, "black"
        else:
            continue
        rows.append({
            "username": u,
            "end_time": g.get("end_time"),
            "game_url": g.get("url"),
            "color": color,
            "rating_post": me.get("rating"),
            "opponent": opp["username"].lower(),
            "opponent_rating_post": opp.get("rating"),
            "score": score_for(me),
            "result_code": me["result"],
            "time_control": g.get("time_control"),
            "rated": g.get("rated"),
            "tournament": g.get("tournament"),
        })
    return rows


def fetch_archives(client: ChessComClient, player_events: dict[str, list[int]],
                   event_ids: set[str]) -> tuple[pd.DataFrame, dict[str, dict]]:
    """For each player fetch the month of each event they played and the month
    before; if no blitz game before an event's start is found, walk further back
    (up to ARCHIVE_LOOKBACK_MONTHS) so round-1 pre-game ratings can be recovered.

    Also returns every raw archive game that belongs to an in-scope event
    (keyed by game url), used to rebuild rounds the tournament endpoint lacks."""
    def one_player(u: str, starts: list[int]):
        idx = safe_get(client, f"player/{u}/games/archives", f"archives index {u}") or {}
        available = {tuple(int(x) for x in a.rstrip("/").rsplit("/", 2)[-2:])
                     for a in idx.get("archives", [])}
        fetched: dict[tuple[int, int], list[dict]] = {}
        evg: dict[str, dict] = {}

        def load(ym):
            if ym not in fetched:
                if ym in available:
                    j = safe_get(client, f"player/{u}/games/{ym[0]}/{ym[1]:02d}", f"archive {u} {ym}",
                                 transform=compact_archive,
                                 cache_path=ARCHIVE_DIR / u / f"{ym[0]}-{ym[1]:02d}.json")
                    fetched[ym] = archive_rows(u, j or {})
                    for g in (j or {}).get("games", []):
                        if g.get("tournament") and event_id(g["tournament"]) in event_ids:
                            evg.setdefault(g["url"], g)
                else:
                    fetched[ym] = []
            return fetched[ym]

        for start in starts:
            ym = month_key(start)
            load(ym)
            load(prev_month(ym))
            look, steps = prev_month(ym), 1
            while (not any(r["end_time"] < start for m in fetched.values() for r in m)
                   and steps < ARCHIVE_LOOKBACK_MONTHS and available and look >= min(available)):
                look = prev_month(look)
                load(look)
                steps += 1
        return [r for rows in fetched.values() for r in rows], evg

    out = []
    event_games: dict[str, dict] = {}
    with ThreadPoolExecutor(ARCHIVE_WORKERS) as ex:
        futs = [ex.submit(one_player, u, s) for u, s in player_events.items()]
        for i, fut in enumerate(as_completed(futs), 1):
            rows, evg = fut.result()
            out.extend(rows)
            for url, g in evg.items():
                event_games.setdefault(url, g)
            if i % 100 == 0:
                log(f"  archives {i}/{len(player_events)} players ({client.n_network} requests)")
    df = pd.DataFrame(out)
    if not df.empty:
        df = df.drop_duplicates(["username", "game_url"]).sort_values(["username", "end_time"])
    return df, event_games


def rebuild_missing_rounds(games: dict[str, list[dict]], events: dict[str, dict],
                           archive_games: dict[str, dict]) -> list[dict]:
    """Recover games from rounds the tournament endpoint 404s on.

    Every game of a round starts within a couple of seconds of the others, so the
    event's games (known + from archives) are clustered by start time; cluster k is
    round k. The assignment is only trusted if the cluster count equals the number of
    rounds and every known game lands in its own round."""
    report = []
    for eid in sorted({m["event_id"] for m in missing_rounds}):
        want = {m["round"] for m in missing_rounds if m["event_id"] == eid}
        known = games.get(eid, [])
        known_urls = {g["url"] for g in known}
        cands = [g for g in archive_games.values()
                 if event_id(g["tournament"]) == eid and g["url"] not in known_urls]
        start = game_start
        times = sorted({start(g) for g in known + cands if start(g)})
        clusters, prev = [], None
        for t in times:
            if prev is None or t - prev > ROUND_GAP_SECONDS:
                clusters.append(t)
            prev = t
        round_of = lambda ts: sum(c <= ts for c in clusters)  # 1-based cluster index
        n_rounds = events[eid]["settings"].get("total_rounds")
        mismatched = sum(round_of(start(g)) != g["_round"] for g in known)
        rec = {"event_id": eid, "missing_rounds": sorted(want), "clusters": len(clusters),
               "total_rounds": n_rounds, "known_games_misassigned": mismatched}
        if len(clusters) != n_rounds or mismatched:
            rec["status"] = "skipped: round clustering inconsistent"
            log(f"  ! rebuild {eid}: {rec}")
            report.append(rec)
            continue
        added = Counter()
        for g in cands:
            r = round_of(start(g))
            if r in want:
                games[eid].append({**g, "_event_id": eid, "_round": r, "_source": "archive_rebuild"})
                added[r] += 1
        rec["status"] = "rebuilt"
        rec["games_added"] = {int(r): n for r, n in sorted(added.items())}
        rec["unexpected_games_in_known_rounds"] = len(cands) - sum(added.values())
        log(f"  rebuilt {eid}: {rec['games_added']}")
        report.append(rec)
    return report


# ---------------------------------------------------------------- 5. tables

def games_table(games: dict[str, list[dict]]) -> pd.DataFrame:
    rows = []
    for eid, gs in games.items():
        for g in gs:
            w, b = g["white"], g["black"]
            ws = score_for(w)
            rows.append({
                "event_id": eid,
                "round": g["_round"],
                "game_url": g.get("url"),
                "white": w["username"].lower(),
                "black": b["username"].lower(),
                "white_uuid": w.get("uuid"),
                "black_uuid": b.get("uuid"),
                # POST-game ratings: never use directly as features
                "white_rating_post": w.get("rating"),
                "black_rating_post": b.get("rating"),
                "white_result_code": w["result"],
                "black_result_code": b["result"],
                "white_score": ws,
                "outcome": {1.0: "white", 0.5: "draw", 0.0: "black"}[ws],
                "start_time": game_start(g),
                "end_time": g.get("end_time"),
                "time_control": g.get("time_control"),
                "rated": g.get("rated"),
                "source": g.get("_source", "tournament"),
            })
    return pd.DataFrame(rows).sort_values(["event_id", "round", "game_url"])


def events_table(events: dict[str, dict], games_df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for eid, t in events.items():
        s = t.get("settings", {})
        rows.append({
            "event_id": eid,
            "name": t.get("name"),
            "start_time": t.get("start_time"),
            "start_utc": datetime.fromtimestamp(t["start_time"], timezone.utc).isoformat(),
            "finish_time": t.get("finish_time"),
            "time_control": s.get("time_control"),
            "total_rounds": s.get("total_rounds"),
            "registered_user_count": s.get("registered_user_count"),
            "n_games": int((games_df["event_id"] == eid).sum()),
        })
    return pd.DataFrame(rows).sort_values("start_time")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max-events", type=int, default=None, help="stop after N events (smoke test)")
    ap.add_argument("--patience", type=int, default=300,
                    help="stop discovery after this many player histories yield no new event")
    ap.add_argument("--max-players", type=int, default=None,
                    help="only fetch profiles/archives for the first N players (smoke test)")
    ap.add_argument("--skip-archives", action="store_true")
    args = ap.parse_args(argv)

    client = ChessComClient()
    t0 = time.time()
    PROCESSED.mkdir(parents=True, exist_ok=True)
    compact_cached_groups()

    log("step 1-2: discovering events and collecting games")
    events, games, excluded = discover_and_collect(client, args.max_events, args.patience)
    (PROCESSED / "excluded_events.json").write_text(json.dumps(excluded, indent=1, sort_keys=True))
    starts = {eid: t["start_time"] for eid, t in events.items()}

    def player_events_of(gs: dict[str, list[dict]]) -> dict[str, list[int]]:
        pe: dict[str, set[int]] = defaultdict(set)
        for eid, lst in gs.items():
            for g in lst:
                for c in ("white", "black"):
                    pe[g[c]["username"].lower()].add(starts[eid])
        return {u: sorted(s) for u, s in sorted(pe.items(), key=lambda kv: -len(kv[1]))}

    player_events = player_events_of(games)
    players = list(player_events)[: args.max_players] if args.max_players else list(player_events)

    hist_parts = []
    if not args.skip_archives:
        log(f"step 3: archives for {len(players)} players")
        hist, archive_event_games = fetch_archives(client, {u: player_events[u] for u in players}, set(events))
        hist_parts.append(hist)
        if missing_rounds:
            log(f"step 3b: rebuilding {len(missing_rounds)} unavailable rounds from archives")
            report = rebuild_missing_rounds(games, events, archive_event_games)
            (PROCESSED / "rebuilt_rounds.json").write_text(json.dumps(report, indent=1))
            # players who only appear in rebuilt rounds also need archives
            new = {u: s for u, s in player_events_of(games).items() if u not in player_events}
            if new and not args.max_players:
                log(f"  archives for {len(new)} players found only in rebuilt rounds")
                hist_parts.append(fetch_archives(client, new, set(events))[0])
                players += list(new)
        hist = pd.concat(hist_parts).drop_duplicates(["username", "game_url"])
        hist.to_csv(PROCESSED / "player_history.csv", index=False)
        log(f"wrote {len(hist)} blitz games of player history")

    games_df = games_table(games)
    events_df = events_table(events, games_df)
    games_df.to_csv(PROCESSED / "games.csv", index=False)
    events_df.to_csv(PROCESSED / "events.csv", index=False)
    log(f"wrote {len(games_df)} games from {len(events_df)} events "
        f"({int((games_df['source'] == 'archive_rebuild').sum())} rebuilt from archives)")

    log(f"step 4: profiles for {len(players)} players")
    fetch_profiles(client, players).to_csv(PROCESSED / "players.csv", index=False)

    FAILURES.parent.mkdir(parents=True, exist_ok=True)
    FAILURES.write_text(json.dumps({"failures": failures, "missing_rounds": missing_rounds}, indent=1))
    log(f"done in {(time.time() - t0) / 60:.1f} min; {client.n_network} network requests; "
        f"{len(failures)} failures logged to {FAILURES.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
