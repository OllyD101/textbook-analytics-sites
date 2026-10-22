"""Seasons before 2018: a one-off backfill from Play-Cricket (e.g. Run workflow with seasons 2010-2017).

    python tests/make_fixtures.py tests/baseline_data.json tests/fixtures   # once
    python tests/test_older_seasons.py

Real 2026 Play-Cricket-shaped scorecards are copied back to 2016 (1st and 2nd XI, several months),
in their own 2016 league, then fetched with --seasons 2016.
"""
import copy
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_pipeline import sandbox, data, sig, DS   # noqa: E402

OLD_DIV = "4001"


def add_2016_fixtures(tmp, per_xi=6):
    fx = os.path.join(tmp, "tests", "fixtures")
    listing = json.load(open(os.path.join(fx, "matches_2026.json")))
    out, added = [], []
    for xi in ("1st XI", "2nd XI"):
        ours = [m for m in listing["matches"] if any(m.get(f"{sd}_team_name") == xi and "Hessle" in (m.get(f"{sd}_club_name") or "") for sd in ("home", "away"))]
        ours = [m for m in ours if os.path.exists(os.path.join(fx, f"match_{m['id']}.json"))]
        for i, m in enumerate(ours[:per_xi]):
            md = json.load(open(os.path.join(fx, f"match_{m['id']}.json")))["match_details"][0]
            md = copy.deepcopy(md); new_id = 800000 + len(added); md["id"] = new_id
            d, mo, _ = md["match_date"].split("/"); md["match_date"] = f"{d}/{mo}/2016"
            if md.get("competition_type") == "League":
                md["competition_id"] = OLD_DIV
            json.dump({"match_details": [md]}, open(os.path.join(fx, f"match_{new_id}.json"), "w"))
            e = dict(m, id=new_id, match_date=md["match_date"]); out.append(e); added.append(md)
    json.dump({"matches": out}, open(os.path.join(fx, "matches_2016.json"), "w"))
    # a 2016 league table that must NOT replace the current one
    json.dump({"league_table": [{"id": int(OLD_DIV), "division_name": "Old Division 2016", "headings": {"column_1": "Team", "column_2": "P", "column_3": "W", "column_4": "T", "column_5": "L", "column_6": "Pts"},
                                 "values": [{"position": "1", "team_id": "1", "column_1": "Hessle CC - 1st XI", "column_2": "18", "column_3": "18", "column_4": "0", "column_5": "0", "column_6": "999"}], "key": ""}]},
              open(os.path.join(fx, f"league_{OLD_DIV}.json"), "w"))
    return added


def run(tmp, *extra, today="2026-10-21"):
    p = subprocess.run([sys.executable, "pipeline/run.py", "--mode", "publish", "--fixtures", "tests/fixtures", *extra], cwd=tmp,
                       capture_output=True, text=True, env=dict(os.environ, PIPELINE_TODAY=today))
    if p.returncode != 0:
        raise AssertionError(p.stdout + p.stderr)
    return open(os.path.join(tmp, "pipeline", "last_report.md"), encoding="utf-8").read()


def snapshot(d):
    return {ds: sorted((sig(r) for r in d[ds] if r["Date"] >= "2018"), key=repr) for ds in DS}


def test_backfill_2016():
    tmp = sandbox(); added = add_2016_fixtures(tmp)
    lp = os.path.join(tmp, "docs", "league.json")
    run(tmp, "--seasons", "2026")   # the site as it is now, with this season's league tables
    before = data(tmp); league_before = open(lp).read()
    report = run(tmp, "--seasons", "2016"); after = data(tmp)
    old = [m for m in after["matches"] if m["Date"].startswith("2016")]
    assert len(old) == len(added), (len(old), len(added))
    # every 2016 game has a Match Value Man of the Match
    motm = {(r["Date"], r["Team"]): r for r in after["motm"]}
    assert all(motm.get((m["Date"], m["Team"]), {}).get("Source") == "Match Value" for m in old), "missing 2016 MoTM"
    assert "no Man of the Match yet" not in report, report
    # every 2016 month has a Player of the Month
    from datetime import datetime
    months = {datetime.strptime(m["Date"], "%Y-%m-%d").strftime("%b %Y") for m in old}
    have = {x for r in after["potm"] for x in r["months"]}
    assert months <= have, months - have
    # nothing from 2018 onwards changed: scorecards, Man of the Match, Player of the Month, league tables
    assert snapshot(before) == snapshot(after), "2018+ rows changed"
    assert [r for r in before["motm"]] == [r for r in after["motm"] if r["Date"] >= "2018"], "2018+ MoTM changed"
    old_potm = {x for r in before["potm"] for x in r["months"]}
    assert {(r["player"], x) for r in before["potm"] for x in r["months"]} == {(r["player"], x) for r in after["potm"] for x in r["months"] if x in old_potm}
    lg_after = json.load(open(lp))
    assert lg_after["tables"] == json.loads(league_before)["tables"], "current league tables were replaced"
    assert "league table left as it is" in report
    hist = lg_after.get("history", {})
    assert set(hist.get("2016", {})) == {"1st", "2nd"}, hist.keys()      # past seasons' tables kept by season
    assert hist["2016"]["1st"]["division_name"] == "Old Division 2016" and any(r["isHessle"] for r in hist["2016"]["1st"]["rows"])
    assert "2016 1st XI: saved" in report
    # a second run changes nothing (awards are frozen, past tables aren't fetched again)
    data_file = open(os.path.join(tmp, "docs", "data.json")).read(); league_file = open(lp).read()
    rep2 = run(tmp, "--seasons", "2016")
    assert open(os.path.join(tmp, "docs", "data.json")).read() == data_file, "second run changed something"
    assert "2016 1st XI: saved" not in rep2
    shutil.rmtree(tmp)
    n_months = len(months)
    return f"{len(old)} 2016 games added, each with a Match Value MoTM; {n_months} 2016 months got a PotM; 2016 league tables kept in the history; current tables untouched; rerun changes nothing"


def test_history_for_seasons_already_published():
    # seasons published before the history existed: their games are skipped as unchanged, so the division is
    # looked up from one of the season's scorecards
    tmp = sandbox(); add_2016_fixtures(tmp); lp = os.path.join(tmp, "docs", "league.json")
    run(tmp, "--seasons", "2026"); run(tmp, "--seasons", "2016")
    lg = json.load(open(lp)); lg.pop("history", None); json.dump(lg, open(lp, "w"))
    rep = run(tmp, "--seasons", "2016"); hist = json.load(open(lp)).get("history", {}); shutil.rmtree(tmp)
    assert "Unchanged since last run: 12" in rep, rep[:600]
    assert set(hist.get("2016", {})) == {"1st", "2nd"}, hist
    return "2016 already published (games skipped as unchanged): division found from a scorecard, both tables saved"


def test_current_season_tables_still_update():
    # a normal weekly run (current season) still saves league tables
    tmp = sandbox(); report = run(tmp, "--seasons", "2026")
    lg = json.load(open(os.path.join(tmp, "docs", "league.json"))); shutil.rmtree(tmp)
    assert "league table left as it is" not in report and lg["tables"], report
    return f"current-season run saves league tables as before ({', '.join(sorted(lg['tables']))} XI)"


if __name__ == "__main__":
    ok = 0; tests = [test_backfill_2016, test_history_for_seasons_already_published, test_current_season_tables_still_update]
    for t in tests:
        try:
            print("PASS", t.__name__, "-", t()); ok += 1
        except Exception as e:
            print("FAIL", t.__name__, "-", repr(e)[:1500])
    print(f"{ok}/{len(tests)} passed")
    sys.exit(0 if ok == len(tests) else 1)
