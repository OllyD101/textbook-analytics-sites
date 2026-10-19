"""3rd XI tests: weekly runs and the one-off backfill of past seasons.

    python tests/make_fixtures.py docs/data.json tests/fixtures   # once
    python tests/test_third_xi.py

Three real 2026 2nd XI scorecards are relabelled as 3rd XI games (with their own league), so the
automation sees genuine Play-Cricket-shaped 3rd XI data.
"""
import copy
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_pipeline import REPO, KM, REV, DS, sandbox, data, sig   # noqa: E402

THIRD_DIV = "5003"


def add_third_xi_fixtures(tmp, n=3):
    fx = os.path.join(tmp, "tests", "fixtures")
    listing = json.load(open(os.path.join(fx, "matches_2026.json")))
    seconds = [m for m in listing["matches"] if (m.get("home_team_name") == "2nd XI" and "Hessle" in m.get("home_club_name", ""))
               or (m.get("away_team_name") == "2nd XI" and "Hessle" in m.get("away_club_name", ""))]
    added = []
    for i, m in enumerate(seconds[:n]):
        md = json.load(open(os.path.join(fx, f"match_{m['id']}.json")))["match_details"][0]
        md = copy.deepcopy(md); new_id = 700000 + i; md["id"] = new_id
        for sd in ("home", "away"):
            if "Hessle" in (md.get(f"{sd}_club_name") or ""):
                md[f"{sd}_team_name"] = "3rd XI"
        md["competition_type"] = "League"; md["competition_id"] = THIRD_DIV
        json.dump({"match_details": [md]}, open(os.path.join(fx, f"match_{new_id}.json"), "w"))
        entry = {k: md.get(k) for k in ("id", "status", "published", "last_updated", "match_date", "home_club_name", "home_team_name", "away_club_name", "away_team_name")}
        entry["status"] = entry.get("status") or "New"; entry["published"] = entry.get("published") or "Yes"
        listing["matches"].append(entry); added.append(md)
    json.dump(listing, open(os.path.join(fx, "matches_2026.json"), "w"))
    rows = [("Walkington CC", 16, 11, 0, 5, 120, 0.9), ("Hessle CC", 16, 9, 0, 7, 101, 0.3)]
    vals = [{"position": str(i + 1), "team_id": str(i), "column_1": f"{c} - 3rd XI", "column_2": str(p), "column_3": str(w), "column_4": str(t),
             "column_5": str(l), "column_6": str(pts), "column_7": str(nrr)} for i, (c, p, w, t, l, pts, nrr) in enumerate(rows)]
    json.dump({"league_table": [{"id": int(THIRD_DIV), "division_name": "Test Division (3rd XI)",
                                 "headings": {"column_1": "Team", "column_2": "P", "column_3": "W", "column_4": "T", "column_5": "L", "column_6": "Pts", "column_7": "NRR"},
                                 "values": vals, "key": ""}]}, open(os.path.join(fx, f"league_{THIRD_DIV}.json"), "w"))
    return added


def run_with(tmp, env_extra, *extra):
    env = dict(os.environ, **env_extra)
    p = subprocess.run([sys.executable, "pipeline/run.py", "--mode", "publish", "--fixtures", "tests/fixtures", *extra], cwd=tmp, capture_output=True, text=True, env=env)
    if p.returncode != 0:
        raise AssertionError(p.stdout + p.stderr)
    return p


def rows_by_team(d, team):
    return {ds: sorted((sig(r) for r in d[ds] if r.get("Team") == team), key=repr) for ds in DS}


def test_backfill_only_adds_third_xi():
    tmp = sandbox(); added = add_third_xi_fixtures(tmp)
    before = data(tmp)
    run_with(tmp, {"PIPELINE_ONLY_TEAMS": "3rd", "PIPELINE_TODAY": "2026-10-01"}, "--seasons", "2026")
    after = data(tmp)
    third = [m for m in after["matches"] if m["Team"] == "3rd"]
    assert len(third) == len(added), f"expected {len(added)} 3rd XI matches, got {len(third)}"
    for team in ("1st", "2nd"):
        assert rows_by_team(before, team) == rows_by_team(after, team), f"{team} XI records changed during a 3rd XI backfill"
    assert all(any(r["Team"] == "3rd" for r in after[ds]) for ds in ("batting", "bowling")), "3rd XI scorecard rows missing"
    old_motm = {(r["Date"], r["Team"]): r["MOTM"] for r in before["motm"]}
    new_motm = {(r["Date"], r["Team"]): r for r in after["motm"]}
    assert all(new_motm[k]["MOTM"] == v for k, v in old_motm.items()), "an existing Man of the Match changed"
    third_awards = [new_motm[(m["Date"], "3rd")] for m in third if (m["Date"], "3rd") in new_motm]
    assert len(third_awards) == len(third) and all(a.get("Source") == "Match Value" for a in third_awards), "past 3rd XI games should get a Match Value MoTM"
    league = json.load(open(os.path.join(tmp, "docs", "league.json")))
    assert "3rd" in league.get("tables", {}) and league["tables"]["3rd"]["rows"], "no 3rd XI league table"
    shutil.rmtree(tmp)
    return (f"backfill added {len(third)} 3rd XI games ({sum(1 for r in after['batting'] if r['Team'] == '3rd')} batting rows), "
            f"1st and 2nd XI records identical, {len(third_awards)} Match Value MoTMs ({', '.join(a['MOTM'] for a in third_awards)}), 3rd XI league table written")


def test_weekly_run_includes_third_xi():
    # the same full weekly run with and without 3rd XI scorecards: the only difference must be the 3rd XI
    base = sandbox(); run_with(base, {"PIPELINE_TODAY": "2026-10-01"}, "--seasons", "2026"); without = data(base); shutil.rmtree(base)
    tmp = sandbox(); added = add_third_xi_fixtures(tmp); run_with(tmp, {"PIPELINE_TODAY": "2026-10-01"}, "--seasons", "2026"); after = data(tmp); shutil.rmtree(tmp)
    assert sum(1 for m in after["matches"] if m["Team"] == "3rd") == len(added)
    for team in ("1st", "2nd"):
        assert rows_by_team(without, team) == rows_by_team(after, team), f"{team} XI records differ when 3rd XI games are present"
    return f"a normal weekly run also collects the {len(added)} 3rd XI games, and 1st and 2nd XI come out exactly as they would without them"


def test_backfill_season_range():
    def new_matches(seasons):
        tmp = sandbox(); added = add_third_xi_fixtures(tmp); fx = os.path.join(tmp, "tests", "fixtures")
        md = copy.deepcopy(added[0]); md["id"] = 710000; d, m, y = md["match_date"].split("/"); md["match_date"] = f"{d}/{m}/2025"
        json.dump({"match_details": [md]}, open(os.path.join(fx, "match_710000.json"), "w"))
        entry = {k: md.get(k) for k in ("id", "status", "published", "last_updated", "match_date", "home_club_name", "home_team_name", "away_club_name", "away_team_name")}
        entry["status"] = entry.get("status") or "New"; entry["published"] = entry.get("published") or "Yes"
        json.dump({"matches": [entry]}, open(os.path.join(fx, "matches_2025.json"), "w"))
        subprocess.run([sys.executable, "pipeline/run.py", "--mode", "dry_run", "--fixtures", "tests/fixtures", "--seasons", seasons], cwd=tmp, capture_output=True, text=True,
                       env=dict(os.environ, PIPELINE_ONLY_TEAMS="3rd", PIPELINE_TODAY="2026-10-01"))
        rep = open(os.path.join(tmp, "pipeline", "last_report.md")).read(); shutil.rmtree(tmp)
        import re
        m = re.search(r"New matches not on the site yet: \*\*(\d+)\*\*", rep); return int(m.group(1)) if m else 0
    one, both = new_matches("2026"), new_matches("2025-2026")
    assert (one, both) == (3, 4), f"2026 alone found {one}, 2025-2026 found {both}"
    return f"a season range works: 2026 alone finds {one} new 3rd XI games, 2025-2026 finds {both} (including the 2025 one)"


if __name__ == "__main__":
    ok = 0; tests = [test_backfill_only_adds_third_xi, test_weekly_run_includes_third_xi, test_backfill_season_range]
    for t in tests:
        try:
            print("PASS", t.__name__ + ":", t()); ok += 1
        except AssertionError as e:
            print("FAIL", t.__name__ + ":", str(e)[:400])
    print(f"\n{ok}/{len(tests)} 3rd XI tests passed")
