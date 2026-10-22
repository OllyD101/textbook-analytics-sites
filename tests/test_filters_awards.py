"""T20s and friendlies are left out; Man of the Match settles for 3 days, then freezes.

    python tests/make_fixtures.py tests/baseline_data.json tests/fixtures   # once
    python tests/test_filters_awards.py
"""
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_pipeline import sandbox, data, add_2027_match, run_seasons   # noqa: E402
from test_third_xi import add_third_xi_fixtures, run_with   # noqa: E402
import subprocess   # noqa: E402
from datetime import datetime   # noqa: E402

FX = lambda tmp, *p: os.path.join(tmp, "tests", "fixtures", *p)


def tag(tmp, mid, season, **fields):
    """Set competition fields on a fixture match (details and listing)."""
    p = FX(tmp, f"match_{mid}.json"); md = json.load(open(p)); md["match_details"][0].update(fields); json.dump(md, open(p, "w"))
    lp = FX(tmp, f"matches_{season}.json"); lst = json.load(open(lp))
    for m in lst["matches"]:
        if m["id"] == mid:
            m.update({k: v for k, v in fields.items() if k != "no_of_overs"})
    json.dump(lst, open(lp, "w"))


def report(tmp):
    return open(os.path.join(tmp, "pipeline", "last_report.md"), encoding="utf-8").read()


def test_t20_and_friendlies_left_out():
    tmp = sandbox()
    games = [(700001, "01/05/2027"), (700002, "08/05/2027"), (700003, "15/05/2027"), (700004, "22/05/2027")]
    for mid, d in games:
        add_2027_match(tmp, new_date=d, new_id=mid)
    tag(tmp, 700001, "2027", competition_type="Friendly")
    tag(tmp, 700002, "2027", competition_type="League", league_name="East Yorkshire T20 Cup")
    tag(tmp, 700003, "2027", competition_type="League", no_of_overs=20)     # only the details say 20 overs
    tag(tmp, 700004, "2027", competition_type="League", league_name="Hull & East Riding League")
    run_seasons(tmp, today="2027-06-30")
    d = data(tmp); rep = report(tmp); shutil.rmtree(tmp)
    got = sorted(m["Date"] for m in d["matches"] if m["Date"] >= "2027")
    assert got == ["2027-05-22"], got
    for why in ("competition type 'Friendly'", "'T20' in", "20-over game"):
        assert why in rep, (why, rep[-1500:])
    assert "Not included" in rep
    return "friendly, T20 (by name) and 20-over game left out and listed in the report; league game kept"


def amend(tmp, mid, who_runs):
    """Give one Hessle batter extra runs on a fixture, as a scorer's correction."""
    p = FX(tmp, f"match_{mid}.json"); md = json.load(open(p)); det = md["match_details"][0]
    h = next(i for i in det["innings"] if i["team_batting_name"].startswith("Hessle"))
    b = next(x for x in h["bat"] if x["batsman_name"] == who_runs[0])
    b["runs"] = str(int(b["runs"] or 0) + who_runs[1]); h["runs"] = str(int(h["runs"]) + who_runs[1])
    det["last_updated"] = det["last_updated"] + "x"
    json.dump(md, open(p, "w"))
    lp = FX(tmp, "matches_2027.json"); lst = json.load(open(lp))
    for m in lst["matches"]:
        if m["id"] == mid: m["last_updated"] = det["last_updated"]
    json.dump(lst, open(lp, "w"))
    return b["batsman_name"]


def motm(tmp, dt):
    return [r["MOTM"] for r in data(tmp)["motm"] if r["Date"] == dt]


def test_motm_settles_then_freezes():
    tmp = sandbox(); dt = add_2027_match(tmp)          # Sat 1 May 2027: Owen Waterson 7/53
    run_seasons(tmp, today="2027-05-01")
    first = motm(tmp, dt)
    det = json.load(open(FX(tmp, "match_700001.json")))["match_details"][0]
    h = next(i for i in det["innings"] if i["team_batting_name"].startswith("Hessle"))
    other = next(x["batsman_name"] for x in h["bat"] if x["batsman_name"] not in first and x["runs"] not in ("", None))
    amend(tmp, 700001, (other, 200))                    # Sunday correction: inside the 3 days
    run_seasons(tmp, today="2027-05-02")
    second = motm(tmp, dt)
    rep = report(tmp)
    another = next(x["batsman_name"] for x in h["bat"] if x["batsman_name"] not in (first[0], other) and x["runs"] not in ("", None))
    amend(tmp, 700001, (another, 400))                  # a week later: frozen
    run_seasons(tmp, today="2027-05-09")
    third = motm(tmp, dt); shutil.rmtree(tmp)
    assert first == ["Owen Waterson"], first
    assert second == [other], (second, other)
    assert "scorecard corrected" in rep
    assert third == second, third
    return f"Sat: {first[0]}; Sun correction (+200 to {other}) -> {other}; a week later another correction -> stays {other}"


def test_older_awards_never_recalculated():
    tmp = sandbox(); before = sorted((r["Date"], r["Team"], r["MOTM"]) for r in data(tmp)["motm"])
    run_seasons(tmp, today="2026-09-14")                 # two days after the last 2026 games
    after = sorted((r["Date"], r["Team"], r["MOTM"]) for r in data(tmp)["motm"]); shutil.rmtree(tmp)
    assert before == after
    return "2026 awards (before match_value_from) untouched even inside the 3 days"


def run_2026(tmp, mode, **env):
    p = subprocess.run([sys.executable, "pipeline/run.py", "--mode", mode, "--fixtures", "tests/fixtures", "--seasons", "2026"],
                       cwd=tmp, capture_output=True, text=True, env=dict(os.environ, PIPELINE_TODAY="2026-10-01", **env))
    assert p.returncode == 0, p.stdout + p.stderr
    return report(tmp)


def test_cup_game_removed_from_site():
    tmp = sandbox(); run_2026(tmp, "publish")
    lst = json.load(open(FX(tmp, "matches_2026.json")))["matches"]
    m = next(x for x in lst if os.path.exists(FX(tmp, f"match_{x['id']}.json")))
    md = json.load(open(FX(tmp, f"match_{m['id']}.json")))["match_details"][0]
    d, mo, y = md["match_date"].split("/"); dt = f"{y}-{mo}-{d}"
    hs = "home" if "Hessle" in md["home_club_name"] else "away"
    team = {"1st XI": "1st", "2nd XI": "2nd"}[md[f"{hs}_team_name"]]
    tag(tmp, m["id"], "2026", competition_type="Cup")
    before = data(tmp)
    rep = run_2026(tmp, "dry_run")
    assert "Would be removed" in rep and dt in rep, rep[-1200:]
    assert data(tmp) == before, "a dry run changed the data"
    rep = run_2026(tmp, "publish"); after = data(tmp); shutil.rmtree(tmp)
    assert "Removed from the site" in rep and dt in rep
    for ds in ("matches", "batting", "bowling", "fielding", "keeping", "motm"):
        assert not [r for r in after[ds] if r["Date"] == dt and r["Team"] == team], ds
    others = lambda d: sorted((r["Date"], r["Team"]) for r in d["matches"] if (r["Date"], r["Team"]) != (dt, team))
    assert others(before) == others(after)
    return f"{dt} {team} XI marked as a cup game on Play-Cricket: dry run lists it, publish removes it and its awards, nothing else touched"


def month(dt):
    return datetime.strptime(dt, "%Y-%m-%d").strftime("%b %Y")


def test_potm_changes_only_through_third_xi():
    tmp = sandbox(); added = add_third_xi_fixtures(tmp, n=3)
    before = data(tmp); owner0 = {mo: r["player"] for r in before["potm"] for mo in r["months"]}
    # a big 3rd XI innings by someone who isn't that month's winner
    md = added[0]; d, mo, y = md["match_date"].split("/"); lab = month(f"{y}-{mo}-{d}")
    p = FX(tmp, f"match_{md['id']}.json"); obj = json.load(open(p)); det = obj["match_details"][0]
    h = next(i for i in det["innings"] if any(b["batsman_name"] in {r["Batter"] for r in before["batting"]} for b in i["bat"]))
    star = next(b for b in h["bat"] if b["batsman_name"] != owner0[lab] and b["runs"] not in ("", None))
    star["runs"] = str(int(star["runs"]) + 400); h["runs"] = str(int(h["runs"]) + 400)
    json.dump(obj, open(p, "w"))
    run_with(tmp, {"PIPELINE_ONLY_TEAMS": "3rd", "PIPELINE_TODAY": "2026-10-01"}, "--seasons", "2026")
    after = data(tmp); owner1 = {mo: r["player"] for r in after["potm"] for mo in r["months"]}; rep = report(tmp)
    third_months = {month(m["Date"]) for m in after["matches"] if m["Team"] == "3rd"}
    assert owner1[lab] == star["batsman_name"], (lab, owner1[lab], star["batsman_name"])
    assert f"replaces {owner0[lab]}" in rep
    assert all(owner1[m] == owner0[m] for m in owner0 if m not in third_months), "a month without 3rd XI games changed"
    assert sorted(owner1) == sorted(owner0), "months gained or lost an award"
    assert all(r["wins"] == len(r["months"]) and r["wins"] > 0 for r in after["potm"])
    run_with(tmp, {"PIPELINE_ONLY_TEAMS": "3rd", "PIPELINE_TODAY": "2026-10-01"}, "--seasons", "2026")
    owner2 = {mo: r["player"] for r in data(tmp)["potm"] for mo in r["months"]}; shutil.rmtree(tmp)
    assert owner2 == owner1, "a rerun changed Player of the Month again"
    return f"{lab}: {star['batsman_name']}'s big 3rd XI innings takes it from {owner0[lab]}; other months unchanged; rerun stable"


def test_placeholder_never_wins():
    tmp = sandbox(); run_2026(tmp, "publish"); d = data(tmp)
    sys.path.insert(0, os.path.join(tmp, "pipeline"))
    from awards import match_value, ranked
    import run as runmod
    cfg = json.load(open(os.path.join(tmp, "pipeline", "config.json")))["awards"]
    m = d["matches"][5]; k = (m["Date"], m["Team"])
    best = ranked(match_value(d, m, cfg))[0]
    for ds, c in (("batting", "Batter"), ("bowling", "Bowler"), ("fielding", "Fielder"), ("keeping", "Keeper")):
        for r in d[ds]:
            if (r["Date"], r["Team"]) == k and r.get(c) == best:
                r[c] = "Unsure"                                  # the best performer becomes a placeholder
    for r in d["motm"]:
        if (r["Date"], r["Team"]) == k:
            r["MOTM"] = "Unsure"
    from test_pipeline import put
    put(tmp, d)
    rep = run_2026(tmp, "publish"); after = data(tmp); shutil.rmtree(tmp)
    got = [r["MOTM"] for r in after["motm"] if (r["Date"], r["Team"]) == k]
    assert got and got[0] != "Unsure", got
    assert "not a player" in rep
    return f"{k[0]} {k[1]} XI: Man of the Match held by 'Unsure' re-awarded to {got[0]}; a placeholder never wins"


if __name__ == "__main__":
    ok = 0; tests = [test_t20_and_friendlies_left_out, test_motm_settles_then_freezes, test_older_awards_never_recalculated,
             test_cup_game_removed_from_site, test_potm_changes_only_through_third_xi, test_placeholder_never_wins]
    for t in tests:
        try:
            print("PASS", t.__name__, "-", t()); ok += 1
        except Exception as e:
            print("FAIL", t.__name__, "-", repr(e)[:1500])
    print(f"{ok}/{len(tests)} passed")
    sys.exit(0 if ok == len(tests) else 1)
