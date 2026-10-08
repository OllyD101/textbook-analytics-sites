"""End-to-end tests. Each one runs the real pipeline on a throwaway copy of the repo.

    python tests/make_fixtures.py docs/data.json tests/fixtures   # once
    python tests/test_pipeline.py
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "pipeline"))
from run import expand, shrink  # noqa: E402

KM = json.load(open(os.path.join(REPO, "pipeline", "key_map.json")))
REV = {v: k for k, v in KM.items()}
DS = ("matches", "batting", "bowling", "fielding", "keeping")


def sandbox():
    tmp = tempfile.mkdtemp()
    for d in ("docs", "pipeline", "tests"):
        shutil.copytree(os.path.join(REPO, d), os.path.join(tmp, d), ignore=shutil.ignore_patterns("state.json", "last_report.md", "__pycache__"))
    return tmp


def run(tmp, *extra, ok=True):
    p = subprocess.run([sys.executable, "pipeline/run.py", "--mode", "publish", "--fixtures", "tests/fixtures", *extra],
                       cwd=tmp, capture_output=True, text=True)
    if ok and p.returncode != 0:
        raise AssertionError(p.stdout + p.stderr)
    return p


def data(tmp):
    return expand(json.load(open(os.path.join(tmp, "docs", "data.json"))), REV)


def put(tmp, d):
    json.dump(shrink(d, KM), open(os.path.join(tmp, "docs", "data.json"), "w"), separators=(",", ":"))


def sig(r):
    def v(x):
        # the existing data mixes Excel and Python rounding, so compare to 0.1 (differences are 0.01)
        return round(x, 1) if isinstance(x, float) else x
    # SR / Economy.1 / Opp Runs.1 on bowling rows are empty spreadsheet leftovers the dashboard never reads
    skip = {"BallsEstimated"} | ({"SR", "Economy.1", "Opp Runs.1"} if "Bowler" in r else set())
    return tuple(sorted((k, v(x)) for k, x in r.items() if k not in skip))


def innings_contradictions(d):
    """Matches where the spreadsheet's Innings column disagrees with its own Bat/Field First."""
    m = {(x["Date"], x["Team"], x["Opponent"]): x["Bat/Field First"] for x in d["matches"]}
    bad = set()
    for r in d["batting"]:
        k = (r["Date"], r["Team"], r["Opponent"])
        if k in m and r["Innings"] != (1 if m[k] == "Bat" else 2): bad.add(k)
    for r in d["bowling"]:
        k = (r["Date"], r["Team"], r["Opponent"])
        if k in m and r["Innings"] != (2 if m[k] == "Bat" else 1): bad.add(k)
    return bad


def season_rows(d, season="2026", ignore_innings=()):
    def s2(r):
        if (r["Date"], r["Team"], r["Opponent"]) in ignore_innings:
            r = {k: v for k, v in r.items() if k != "Innings"}
        return sig(r)
    return {ds: Counter(s2(r) for r in d[ds] if r["Date"][:4] == season) for ds in DS}


def md5(path):
    return hashlib.md5(open(path, "rb").read()).hexdigest()


def test_restores_deleted_matches_exactly():
    tmp = sandbox(); orig = data(tmp)
    drop = sorted({(m["Date"], m["Team"]) for m in orig["matches"] if m["Date"][:4] == "2026"})[::7]  # every 7th match
    d = data(tmp)
    for ds in DS:
        d[ds] = [r for r in d[ds] if (r["Date"], r["Team"]) not in drop]
    put(tmp, d)
    out = run(tmp).stdout
    after = data(tmp)
    known = innings_contradictions(orig)
    assert season_rows(after, ignore_innings=known) == season_rows(orig, ignore_innings=known), "2026 rows differ after re-adding deleted matches"
    # for the known contradictions, Play-Cricket's batting order wins: innings follow Bat/Field First
    assert not ({k for k in innings_contradictions(after) if k[0][:4] == "2026"}), "innings still contradict Bat/Field First"
    for ds in DS:  # earlier seasons untouched
        assert Counter(sig(r) for r in after[ds] if r["Date"][:4] != "2026") == Counter(sig(r) for r in orig[ds] if r["Date"][:4] != "2026")
    n26 = len([k for k in known if k[0][:4] == "2026"])
    return f"{len(drop)} deleted matches re-added; every 2026 row identical (bar the innings label in the {n26} known contradictions, now corrected); earlier seasons untouched"


def test_second_run_changes_nothing():
    tmp = sandbox(); run(tmp)
    before = md5(os.path.join(tmp, "docs", "data.json"))
    out = run(tmp).stdout
    assert md5(os.path.join(tmp, "docs", "data.json")) == before, "data.json changed on a no-op run"
    assert "Added: **0**" in out and "Amended: **0**" in out
    return "second run: 0 added, 0 amended, data.json byte-identical"


def test_scorer_correction_is_picked_up():
    tmp = sandbox(); run(tmp)
    fx = os.path.join(tmp, "tests", "fixtures")
    f = sorted(x for x in os.listdir(fx) if x.startswith("match_5"))[3]
    md = json.load(open(os.path.join(fx, f)))
    det = md["match_details"][0]
    hessle = next(i for i in det["innings"] if i["team_batting_name"].startswith("Hessle"))
    batter = next(b for b in hessle["bat"] if b["runs"] not in ("", "0"))
    batter["runs"] = str(int(batter["runs"]) + 5); hessle["runs"] = str(int(hessle["runs"]) + 5)
    det["last_updated"] = "30/09/2026"
    json.dump(md, open(os.path.join(fx, f), "w"))
    lst = json.load(open(os.path.join(fx, "matches_2026.json")))
    for m in lst["matches"]:
        if m["id"] == det["id"]:
            m["last_updated"] = "30/09/2026"
    json.dump(lst, open(os.path.join(fx, "matches_2026.json"), "w"))
    out = run(tmp).stdout
    d = data(tmp)
    date = "-".join(reversed(det["match_date"].split("/")))
    row = next(r for r in d["batting"] if r["Date"] == date and r["Batter"] == batter["batsman_name"])
    assert row["Runs"] == int(batter["runs"]), (row["Runs"], batter["runs"])
    assert "Amended: **1**" in out and "Added: **0**" in out
    return f"amended scorecard picked up: {batter['batsman_name']} now {row['Runs']} on {date}; only that match amended"


def test_motm_entry_applies():
    tmp = sandbox()
    d = data(tmp)
    m = sorted((x for x in d["matches"] if x["Date"][:4] == "2026"), key=lambda x: (x["Date"], x["Team"]))[-1]
    with open(os.path.join(tmp, "pipeline", "manual", "motm.csv"), "a") as f:
        f.write(f"{m['Date']},{m['Team']} XI,Test Player\n")
    with open(os.path.join(tmp, "pipeline", "manual", "potm.csv"), "a") as f:
        f.write("Sep 2026,Test Player\n")
    run(tmp, "--seasons", "2026")
    # nothing on Play-Cricket changed, so force the write to check manual entries apply
    os.environ["FORCE_WRITE"] = "1"; run(tmp); os.environ.pop("FORCE_WRITE")
    d = data(tmp)
    got = [r for r in d["motm"] if (r["Date"], r["Team"]) == (m["Date"], m["Team"])]
    assert len(got) == 1 and got[0]["MOTM"] == "Test Player"
    assert any(r["player"] == "Test Player" and "Sep 2026" in r["months"] for r in d["potm"])
    return f"MoTM for {m['Date']} {m['Team']} XI replaced (not duplicated); PotM added"


def test_bad_data_blocks_publish():
    tmp = sandbox()
    fx = os.path.join(tmp, "tests", "fixtures")
    f = sorted(x for x in os.listdir(fx) if x.startswith("match_5"))[0]
    md = json.load(open(os.path.join(fx, f)))
    md["match_details"][0]["innings"][0]["overs"] = "40.7"  # impossible: 7 balls in an over
    json.dump(md, open(os.path.join(fx, f), "w"))
    before = md5(os.path.join(tmp, "docs", "data.json"))
    p = run(tmp, ok=False)
    assert p.returncode != 0, "publish should have refused"
    assert md5(os.path.join(tmp, "docs", "data.json")) == before, "data.json was modified despite blocking error"
    assert "invalid overs" in p.stdout
    return "impossible overs (40.7) -> publish refused, exit code non-zero, data.json untouched"


def test_league_table_written():
    tmp = sandbox(); run(tmp)
    lg = json.load(open(os.path.join(tmp, "docs", "league.json")))
    h1 = next(r for r in lg["tables"]["1st"]["rows"] if r["isHessle"])
    assert (h1["pos"], h1["pts"], h1["W"], h1["nrr"]) == (2, 158, 15, 0.61), h1
    assert lg["tables"]["2nd"]["rows"][1]["team"] == "Hessle CC 2nd XI"
    return f"league.json written for both XIs (Hessle 1st XI: pos {h1['pos']}, {h1['pts']} pts)"



# ---------------------------------------------------------------- Match Value awards (2027 onwards)
def add_2027_match(tmp, src_date="05/09/2026", team_id="1001", new_date="01/05/2027", new_id=700001):
    """Clone a real 2026 scorecard as if it were played in 2027 (Hessle 1st XI v Sutton: Owen Waterson 7/53)."""
    fx = os.path.join(tmp, "tests", "fixtures")
    for f in sorted(os.listdir(fx)):
        if not f.startswith("match_5"):
            continue
        md = json.load(open(os.path.join(fx, f)))
        det = md["match_details"][0]
        if det["match_date"] == src_date and team_id in (str(det["home_team_id"]), str(det["away_team_id"])):
            det.update(id=new_id, match_date=new_date, last_updated=new_date)
            json.dump(md, open(os.path.join(fx, f"match_{new_id}.json"), "w"))
            season = new_date[-4:]
            lp = os.path.join(fx, f"matches_{season}.json")
            lst = json.load(open(lp)) if os.path.exists(lp) else {"matches": []}
            lst["matches"].append({k: det[k] for k in ("id", "status", "published", "last_updated", "match_date", "home_club_name",
                                                          "home_team_name", "home_team_id", "away_club_name", "away_team_name", "away_team_id")})
            json.dump(lst, open(lp, "w"))
            return "-".join(reversed(new_date.split("/")))
    raise AssertionError("source match not found")


def run_seasons(tmp, *extra, today=None, ok=True):
    env = dict(os.environ, PIPELINE_SEASONS="2026,2027")
    if today: env["PIPELINE_TODAY"] = today
    p = subprocess.run([sys.executable, "pipeline/run.py", "--mode", "publish", "--fixtures", "tests/fixtures", *extra],
                       cwd=tmp, capture_output=True, text=True, env=env)
    if ok and p.returncode != 0:
        raise AssertionError(p.stdout + p.stderr)
    return p


def awards_before(d, cutoff="2027-01-01"):
    return (sorted((r["Date"], r["Team"], r["MOTM"]) for r in d["motm"] if r["Date"] < cutoff),
            sorted((m, r["player"]) for r in d["potm"] for m in r["months"] if m[-4:] < cutoff[:4]))


def test_match_value_awards_2027_match():
    tmp = sandbox(); dt = add_2027_match(tmp)
    out = run_seasons(tmp, today="2027-05-02").stdout
    got = [r for r in data(tmp)["motm"] if r["Date"] == dt]
    assert len(got) == 1 and got[0]["MOTM"] == "Owen Waterson" and got[0]["Source"] == "Match Value", got
    assert abs(got[0]["Match Value"] - 94.6) < 0.2, got[0]       # same figure as the 2026 analysis
    assert "Man of the Match 2027-05-01" in out
    return f"2027 match: Owen Waterson (7/53) awarded by Match Value, value {got[0]['Match Value']}"


def test_past_awards_never_change():
    tmp = sandbox(); before = awards_before(data(tmp)); add_2027_match(tmp)
    run_seasons(tmp, today="2027-06-10")
    assert awards_before(data(tmp)) == before, "an award before 2027 changed"
    return f"all {len(before[0])} MoTM and {len(before[1])} PotM awards before 2027 unchanged after a full publish"


def test_awards_are_frozen():
    tmp = sandbox(); dt = add_2027_match(tmp); run_seasons(tmp, today="2027-05-02")
    fx = os.path.join(tmp, "tests", "fixtures", "match_700001.json"); md = json.load(open(fx)); det = md["match_details"][0]
    hessle = next(i for i in det["innings"] if i["team_batting_name"].startswith("Hessle"))
    b = next(x for x in hessle["bat"] if x["runs"] not in ("",)); b["runs"] = str(int(b["runs"]) + 150); hessle["runs"] = str(int(hessle["runs"]) + 150)
    det["last_updated"] = "10/05/2027"; json.dump(md, open(fx, "w"))
    lp = os.path.join(tmp, "tests", "fixtures", "matches_2027.json"); lst = json.load(open(lp))
    for m in lst["matches"]:
        if m["id"] == 700001: m["last_updated"] = "10/05/2027"
    json.dump(lst, open(lp, "w"))
    out = run_seasons(tmp, today="2027-05-11").stdout
    got = [r for r in data(tmp)["motm"] if r["Date"] == dt]
    assert "Amended: **1**" in out and len(got) == 1 and got[0]["MOTM"] == "Owen Waterson", got
    return f"scorecard amended (+150 runs to {b['batsman_name']}) -> match updated, award stays Owen Waterson"


def test_captain_override_wins():
    tmp = sandbox(); dt = add_2027_match(tmp)
    with open(os.path.join(tmp, "pipeline", "manual", "motm.csv"), "a") as f: f.write(f"{dt},1st XI,Joe Hudson\n")
    with open(os.path.join(tmp, "pipeline", "manual", "potm.csv"), "a") as f: f.write("May 2027,Joe Hudson\n")
    run_seasons(tmp, today="2027-06-10")
    d = data(tmp); got = [r for r in d["motm"] if r["Date"] == dt]
    may = [r["player"] for r in d["potm"] if "May 2027" in r["months"]]
    assert len(got) == 1 and got[0]["MOTM"] == "Joe Hudson" and got[0]["Source"] == "Captain", got
    assert may == ["Joe Hudson"], may
    return "captain overrides replace the automatic MoTM and PotM (no duplicates)"


def test_potm_waits_for_month_end():
    tmp = sandbox(); add_2027_match(tmp)
    run_seasons(tmp, today="2027-05-20")
    early = [r["player"] for r in data(tmp)["potm"] if "May 2027" in r["months"]]
    run_seasons(tmp, today="2027-06-05")
    late = [r["player"] for r in data(tmp)["potm"] if "May 2027" in r["months"]]
    assert early == [] and late == ["Owen Waterson"], (early, late)
    return "May 2027 PotM not awarded on 20 May; awarded on 5 June (after 3 days' grace): Owen Waterson"

if __name__ == "__main__":
    failed = 0
    for name, fn in [(k, v) for k, v in globals().items() if k.startswith("test_")]:
        try:
            print(f"PASS {name}: {fn()}")
        except Exception as e:
            failed += 1
            print(f"FAIL {name}: {str(e)[:1500]}")
    sys.exit(1 if failed else 0)
