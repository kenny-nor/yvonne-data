#!/usr/bin/env python3
"""
Yvonne's Lightning Strikes - automatic data builder.

Downloads the free nflverse data files and builds data/snapshot.json, the same
data the app used to carry inside itself. Run it on a schedule (see
.github/workflows/update-data.yml) so the app always has fresh data.

Safety: if any download fails or the data looks broken, the script stops and
leaves the last good snapshot.json in place. It never publishes a half-built file.

Usage: python3 build_snapshot.py [--season 2026] [--out data/snapshot.json]
"""
import argparse, csv, datetime as dt, gzip, io, json, os, sys, time, urllib.request

BASE = "https://github.com/nflverse/nflverse-data/releases/download"
UA = {"User-Agent": "yvonne-lightning-strikes-data-builder"}

GAME_COLS = ["game_id", "season", "game_type", "week", "gameday", "weekday", "gametime", "away_team", "away_score",
             "home_team", "home_score", "location", "result", "total", "overtime", "away_rest", "home_rest",
             "away_moneyline", "home_moneyline", "spread_line", "total_line", "div_game", "roof", "surface", "temp",
             "wind", "away_qb_name", "home_qb_name", "away_coach", "home_coach", "stadium"]
TEAM_COLS = ["season", "week", "team", "season_type", "game_id", "opponent_team", "attempts", "sacks_suffered",
             "passing_epa", "rushing_epa", "carries", "passing_interceptions", "sack_fumbles_lost",
             "rushing_fumbles_lost", "receiving_fumbles_lost", "penalties"]
PLAYER_COLS = ["player_id", "player_display_name", "position", "season", "week", "team", "season_type", "attempts",
               "sacks_suffered", "passing_epa", "rushing_epa", "receiving_epa", "carries", "targets",
               "def_pass_defended", "def_interceptions", "rushing_tds", "receiving_tds"]
DEPTH_SLOTS = {"QB", "RB", "WR", "TE", "LCB", "RCB", "SS", "FS"}


def fetch(path, tries=4):
    url = f"{BASE}/{path}"
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=180) as r:
                data = r.read()
            if path.endswith(".gz"):
                data = gzip.decompress(data)
            print(f"  downloaded {path} ({len(data)//1024} KB)")
            return data.decode("utf-8")
        except Exception as e:  # network hiccup: wait and retry
            print(f"  retry {i+1} for {path}: {e}")
            time.sleep(5 * (i + 1))
    raise SystemExit(f"FAILED to download {path}; keeping the last good snapshot")


def rows(text):
    return list(csv.DictReader(io.StringIO(text)))


def pick(r, cols):
    return {c: ("" if (r.get(c) or "") == "NA" else (r.get(c) or "")) for c in cols}


def build(season, out, prev_status=""):
    prev, prev2 = season - 1, season - 2
    print("Downloading nflverse files...")
    games_all = rows(fetch("schedules/games.csv"))
    tw = rows(fetch(f"stats_team/stats_team_week_{prev2}.csv")) + rows(fetch(f"stats_team/stats_team_week_{prev}.csv")) \
        + rows(fetch(f"stats_team/stats_team_week_{season}.csv"))
    pw = rows(fetch(f"stats_player/stats_player_week_{prev}.csv")) + rows(fetch(f"stats_player/stats_player_week_{season}.csv"))
    depth_raw = rows(fetch(f"depth_charts/depth_charts_{season}.csv"))
    inj_raw = rows(fetch(f"injuries/injuries_{season}.csv"))
    pbp_prev = fetch(f"pbp/play_by_play_{prev}.csv.gz")
    pbp_cur = fetch(f"pbp/play_by_play_{season}.csv.gz")

    games = [pick(g, GAME_COLS) for g in games_all if g["season"].isdigit() and int(g["season"]) >= 2010]
    team_week = [pick(r, TEAM_COLS) for r in tw if r["season"] != str(season) or r["season_type"] == "REG"]
    player_week = [pick(r, PLAYER_COLS) for r in pw if r["season"] != str(season) or r["season_type"] == "REG"]

    # Fourth-quarter points per game (regular season), from play-by-play
    def q4(text, yr):
        acc = {}
        for r in csv.DictReader(io.StringIO(text)):
            g = r["game_id"]
            if not g.startswith(f"{yr}_") or r.get("season_type") not in ("REG", None, ""):
                continue
            try:
                qt = int(float(r["qtr"] or 0)); hs = float(r["total_home_score"] or 0); a = float(r["total_away_score"] or 0)
            except ValueError:
                continue
            e = acc.setdefault(g, {"home": r["home_team"], "away": r["away_team"], "s3": None, "e4": None})
            if qt <= 3: e["s3"] = (hs, a)
            if qt == 4: e["e4"] = (hs, a)
        return [{"game_id": g, "home": e["home"], "away": e["away"], "homeQ4": int(e["e4"][0] - e["s3"][0]),
                 "awayQ4": int(e["e4"][1] - e["s3"][1])} for g, e in sorted(acc.items()) if e["s3"] and e["e4"]]
    q4rows = q4(pbp_prev, prev) + q4(pbp_cur, season)

    # Which weeks are done, and which week is next
    sg = [g for g in games if g["season"] == str(season) and g["game_type"] == "REG"]
    weeks = sorted({int(g["week"]) for g in sg})
    done = [w for w in weeks if all(g["home_score"] != "" for g in sg if int(g["week"]) == w)]
    through = max(done) if done else 0
    target = next((w for w in weeks if w > through), through)

    # Depth charts: one entry per player per slot, ranks 1-3
    def depth_rows(rs):
        out_, seen = [], set()
        for r in rs:
            if r["pos_abb"] not in DEPTH_SLOTS:
                continue
            try:
                rk = int(r["pos_rank"])
            except ValueError:
                continue
            k = (r["team"], r["pos_abb"], rk, r["gsis_id"])
            if rk > 3 or k in seen:
                continue
            seen.add(k)
            out_.append({"team": r["team"], "name": r["player_name"], "position": "CB" if r["pos_abb"] in ("LCB", "RCB") else r["pos_abb"],
                         "slot": r["pos_abb"], "depthRank": rk, "playerId": r["gsis_id"], "snapshotAt": r["dt"]})
        return out_
    by_team = {}
    for r in depth_raw:
        by_team.setdefault(r["team"], {}).setdefault(r["dt"], []).append(r)
    latest = []
    for t, snaps in by_team.items():
        latest += snaps[max(snaps)]
    depth = depth_rows(latest)
    depth_by_week = {}
    for w in weeks:
        if w > target:
            break
        chosen = []
        for g in (x for x in sg if int(x["week"]) == w):
            for t in (g["home_team"], g["away_team"]):
                ok = [d for d in by_team.get(t, {}) if d[:10] <= g["gameday"]]
                if ok:
                    chosen += by_team[t][max(ok)]
        depth_by_week[str(w)] = depth_rows(chosen) if w < target else depth

    # Injury reports by week
    inj_by_week = {}
    for r in inj_raw:
        if r.get("season_type") != "REG":
            continue
        inj_by_week.setdefault(r["week"], []).append({k: r.get(k, "") for k in ("team", "gsis_id", "position", "full_name", "report_status", "practice_status")})

    S = {"snapshot": dt.date.today().isoformat(), "builtAt": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
         "throughWeek2026": through, "targetWeek": target, "season": season, "games": games, "teamWeek": team_week,
         "playerWeek": player_week, "q4": q4rows, "injuries": inj_by_week.get(str(target), []), "depth": depth,
         "depthByWeek": depth_by_week, "injByWeek": inj_by_week}

    # Sanity checks before publishing (never publish a broken file)
    problems = []
    if len(games) < 4000: problems.append(f"only {len(games)} games")
    if len({d['team'] for d in depth}) < 32: problems.append(f"depth charts for only {len({d['team'] for d in depth})} teams")
    if through and sum(1 for r in team_week if r["season"] == str(season)) < through * 26: problems.append("team stats look incomplete")
    if not q4rows: problems.append("no fourth-quarter data")
    for prev_path in (prev_status, out):
        if prev_path and os.path.exists(prev_path):
            try:
                old = json.load(open(prev_path))
                if max(old.get("throughWeek", 0), old.get("throughWeek2026", 0)) > through:
                    problems.append("new data is OLDER than the published data")
            except Exception:
                pass
    if problems:
        raise SystemExit("NOT PUBLISHED - " + "; ".join(problems))

    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    tmp = out + ".tmp"
    with open(tmp, "w") as f:
        json.dump(S, f, separators=(",", ":"))
    os.replace(tmp, out)
    with open(os.path.join(os.path.dirname(out) or ".", "status.json"), "w") as f:
        json.dump({"snapshot": S["snapshot"], "builtAt": S["builtAt"], "throughWeek": through, "targetWeek": target,
                   "injuryReportRows": len(S["injuries"]), "depthTeams": len({d['team'] for d in depth})}, f, indent=2)
    print(f"OK: data through Week {through}, next week {target}, injury report rows for Week {target}: {len(S['injuries'])}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--out", default="data/snapshot.json")
    ap.add_argument("--prev-status", default="", help="status.json from the currently published site, if any")
    a = ap.parse_args()
    build(a.season, a.out, a.prev_status)
