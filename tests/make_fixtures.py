"""Build Play-Cricket-format API responses from the dashboard's own 2026 data.

Running the pipeline on these should reproduce the existing data exactly, which
proves the converter's logic. (The real-API dry run then confirms Play-Cricket's
actual responses look the way its documentation says.)

    python tests/make_fixtures.py docs/data.json tests/fixtures
"""
import json
import os
import re
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "pipeline"))
from run import expand, load_json  # noqa: E402

PC_NAMES = {"Sutton": "Sutton-on-Hull CC", "NCCE": "North Cave Coal Exporters CC", "Bransholme": "Bransholme & Kingswood CC",
            "Middleton & ND": "Middleton & North Dalton CC"}
HOW = {"Caught": "ct", "Bowled": "b", "LBW": "lbw", "Run Out": "run out", "Stumped": "st", "Not Out": "no",
       "DNB": "did not bat", "Hit Wicket": "hw", "Retired": "rtd", "Retired Not Out": "rh"}


def s(v):
    return "" if v in (None, "-", "\u2013") else str(v)


def pc_club(opp):
    return PC_NAMES.get(opp, opp if opp.endswith("CC") else opp + " CC")


def main(data_path, out, season="2026"):
    os.makedirs(out, exist_ok=True)
    keymap = load_json(os.path.join(os.path.dirname(HERE), "pipeline", "key_map.json"))
    d = expand(load_json(data_path), {v: k for k, v in keymap.items()})
    pid, ids = defaultdict(lambda: str(900000 + len(ids))), {}
    ids = pid
    team_id = {"1st": "1001", "2nd": "1002"}
    keeper_of = {t: Counter(r["Keeper"] for r in d["keeping"] if r["Date"][:4] == season and r["Team"] == t).most_common(1)[0][0] for t in ("1st", "2nd")}
    listing, mid = [], 500000
    matches = sorted([m for m in d["matches"] if m["Date"][:4] == season], key=lambda m: (m["Date"], m["Team"]))
    for m in matches:
        mid += 1
        k = (m["Date"], m["Team"], m["Opponent"])
        rows = {ds: [r for r in d[ds] if (r["Date"], r["Team"], r["Opponent"]) == k] for ds in ("batting", "bowling", "fielding", "keeping")}
        hid, oid = team_id[m["Team"]], str(2000 + mid)
        dd = "/".join(reversed(m["Date"].split("-")))
        home = m["Home/Away"] == "Home"
        sides = {"h": {"club": "Hessle CC", "team": f"{m['Team']} XI", "id": hid}, "o": {"club": pc_club(m["Opponent"]), "team": "1st XI", "id": oid}}
        H, A = (sides["h"], sides["o"]) if home else (sides["o"], sides["h"])
        # Hessle innings
        bat = []
        for r in sorted(rows["batting"], key=lambda r: r["Pos"]):
            code = HOW.get(r["Dismissal"], r["Dismissal"])
            e = {"position": str(r["Pos"]), "batsman_name": r["Batter"], "batsman_id": ids[r["Batter"]], "how_out": code,
                 "fielder_name": "", "fielder_id": "", "bowler_name": "", "bowler_id": "",
                 "runs": s(r["Runs"]) if r["Dismissal"] != "DNB" else "", "fours": s(r["4s"]), "sixes": s(r["6s"]),
                 "balls": s(r["Balls"]) if not r.get("BallsEstimated") else ""}
            if r["Dismissal"] == "Ct & B":
                e.update(how_out="ct", fielder_name="Opp Bowler", fielder_id="77", bowler_name="Opp Bowler", bowler_id="77")
            elif r["Dismissal"] == "Caught":
                e.update(fielder_name="Opp Fielder", fielder_id="78", bowler_name="Opp Bowler", bowler_id="77")
            bat.append(e)
        bat_runs = sum(r["Runs"] for r in rows["batting"] if isinstance(r["Runs"], (int, float)))
        h_inn = {"team_batting_name": f"Hessle CC - {m['Team']} XI", "team_batting_id": hid, "innings_number": 1,
                 "total_extras": str(m["Runs Scored"] - bat_runs), "runs": str(m["Runs Scored"]), "wickets": str(m["Wickets Lost"]),
                 "overs": str(m["Overs Batted"]), "declared": False, "bat": bat, "fow": [], "bowl": []}
        # Opposition innings: rebuild dismissals from Hessle's fielding / keeping / bowling records
        obat, n = [], 0
        credited = Counter()

        def add(how, fielder=None, bowler=None, same=False):
            nonlocal n
            n += 1
            fid = ids[fielder] if fielder else ""
            obat.append({"position": str(n), "batsman_name": f"Opp Batter {n}", "batsman_id": str(n), "how_out": how,
                         "fielder_name": fielder or "", "fielder_id": ids[bowler] if same else fid,
                         "bowler_name": bowler or "", "bowler_id": ids[bowler] if bowler else "", "runs": "0", "fours": "", "sixes": "", "balls": ""})
        for r in rows["fielding"]:
            if r["Dismissal"] == "Run Out":
                add("run out", r["Fielder"])
            elif r["Fielder"] == r["Bowler"]:
                add("ct", r["Bowler"], r["Bowler"], same=True); credited[r["Bowler"]] += 1
            else:
                add("ct", r["Fielder"], r["Bowler"]); credited[r["Bowler"]] += 1
        for r in rows["keeping"]:
            add("ct" if r["Dismissal"] == "Caught" else "st", r["Keeper"], r["Bowler"]); credited[r["Bowler"]] += 1
        for r in rows["bowling"]:
            for _ in range(max(0, (r["Wickets"] or 0) - credited[r["Bowler"]])):
                add("b", None, r["Bowler"])
        bowl = [{"bowler_name": r["Bowler"], "bowler_id": ids[r["Bowler"]], "overs": str(r["Overs"]), "maidens": s(r["Maidens"]),
                 "runs": s(r["Runs"]), "wides": s(r["Wides"]), "wickets": s(r["Wickets"]), "no_balls": s(r["No Balls"])} for r in rows["bowling"]]
        o_inn = {"team_batting_name": f"{pc_club(m['Opponent'])} - 1st XI", "team_batting_id": oid, "innings_number": 1, "total_extras": "",
                 "runs": str(m["Runs Conceded"]), "wickets": str(m["Wickets Taken"]), "overs": str(m["Overs Bowled"]),
                 "declared": False, "bat": obat, "fow": [], "bowl": bowl}
        innings = [h_inn, o_inn] if m["Bat/Field First"] == "Bat" else [o_inn, h_inn]
        team_names = sorted({r["Batter"] for r in rows["batting"]} | {r["Bowler"] for r in rows["bowling"]})
        # flag whoever actually kept in this match (as a real team sheet would); fall back to the usual keeper
        kept = {r["Keeper"] for r in rows["keeping"]}
        catchers = {r["Fielder"] for r in rows["fielding"]}
        keeper = next(iter(kept)) if kept else (keeper_of[m["Team"]] if keeper_of[m["Team"]] not in catchers else None)
        sheet = [{"position": i + 1, "player_name": p, "player_id": int(ids[p]), "captain": False, "wicket_keeper": p == keeper}
                 for i, p in enumerate(team_names)]
        res = {"W": ("W", hid), "L": ("W", oid), "D": ("D", "")}[m["Result"]]
        md = {"id": mid, "status": "New", "published": "Yes", "last_updated": dd, "competition_type": "League",
              "competition_id": "5001" if m["Team"] == "1st" else "5002", "match_date": dd,
              "home_club_name": H["club"], "home_team_name": H["team"], "home_team_id": H["id"],
              "away_club_name": A["club"], "away_team_name": A["team"], "away_team_id": A["id"],
              "toss_won_by_team_id": hid if m["Toss Won/Lost"] == "W" else oid,
              "batted_first": hid if m["Bat/Field First"] == "Bat" else oid,
              "result": res[0], "result_applied_to": res[1], "result_description": "",
              "points": [{"team_id": hid, "bonus_points_batting": "0", "bonus_points_bowling": "0"}],
              "players": [{"home_team": sheet if home else []}, {"away_team": [] if home else sheet}], "innings": innings}
        json.dump({"match_details": [md]}, open(os.path.join(out, f"match_{mid}.json"), "w"))
        listing.append({k2: md[k2] for k2 in ("id", "status", "published", "last_updated", "match_date", "home_club_name",
                                               "home_team_name", "home_team_id", "away_club_name", "away_team_name", "away_team_id")})
    # awkward extras: a junior fixture, an abandoned match with no scorecard, a future fixture
    listing.append({"id": 600001, "status": "New", "published": "Yes", "last_updated": "01/06/2026", "match_date": "01/06/2026",
                    "home_club_name": "Hessle CC", "home_team_name": "Under 15", "away_club_name": "Hull CC", "away_team_name": "Under 15"})
    json.dump({"match_details": [{"id": 600002, "match_date": "20/06/2026", "home_club_name": "Hessle CC", "home_team_name": "2nd XI",
                                  "home_team_id": "1002", "away_club_name": "Rain CC", "away_team_name": "1st XI", "away_team_id": "9",
                                  "result": "A", "result_description": "Abandoned", "innings": []}]}, open(os.path.join(out, "match_600002.json"), "w"))
    listing.append({"id": 600002, "status": "New", "published": "Yes", "last_updated": "20/06/2026", "match_date": "20/06/2026",
                    "home_club_name": "Hessle CC", "home_team_name": "2nd XI", "away_club_name": "Rain CC", "away_team_name": "1st XI"})
    listing.append({"id": 600003, "status": "New", "published": "Yes", "last_updated": "01/01/2026", "match_date": "01/05/2099",
                    "home_club_name": "Hessle CC", "home_team_name": "1st XI", "away_club_name": "Future CC", "away_team_name": "1st XI"})
    json.dump({"matches": listing}, open(os.path.join(out, f"matches_{season}.json"), "w"))
    # league tables (headings as Play-Cricket sends them)
    for div, team, rows in (("5001", "1st", [("Cottingham CC", 22, 17, 1, 2, 189, 2.01), ("Hessle CC", 22, 15, 0, 5, 158, 0.61), ("Brandesburton CC", 22, 2, 0, 18, 42, -2.11)]),
                            ("5002", "2nd", [("Londesborough Park CC", 17, 15, 0, 0, 158, 4.43), ("Hessle CC", 17, 14, 0, 3, 142, 0.85)])):
        vals = [{"position": str(i + 1), "team_id": str(i), "column_1": f"{c} - {team} XI", "column_2": str(p), "column_3": str(w),
                 "column_4": str(t), "column_5": str(l), "column_6": str(pts), "column_7": str(nrr)} for i, (c, p, w, t, l, pts, nrr) in enumerate(rows)]
        json.dump({"league_table": [{"id": int(div), "division_name": f"Test Division ({team} XI)",
                                     "headings": {"column_1": "Team", "column_2": "P", "column_3": "W", "column_4": "T", "column_5": "L", "column_6": "Pts", "column_7": "NRR"},
                                     "values": vals, "key": ""}]}, open(os.path.join(out, f"league_{div}.json"), "w"))
    print(f"wrote {len(matches)} scorecards + 3 awkward cases + 2 league tables to {out}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
