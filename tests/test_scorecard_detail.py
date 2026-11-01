"""Full-scorecard detail: opposition batting and bowling, who dismissed our batters, fall of wickets, partnerships.

    python tests/make_fixtures.py tests/baseline_data.json tests/fixtures   # once
    python tests/test_scorecard_detail.py
"""
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_pipeline import REPO, sandbox, data   # noqa: E402
from test_pc_detail import run, pick, load_md, save_md, hessle_bat   # noqa: E402

sys.path.insert(0, os.path.join(REPO, "pipeline"))
from transform import Names, scorecard_detail, _partnerships   # noqa: E402

BASE = {"Date": "2026-05-02", "Team": "1st", "Opponent": "Testers CC", "Home/Away": "Home"}


def names():
    return Names(["Testers CC"], {"Al Ant", "Bo Bee", "Cy Cat", "Di Dog", "Ed Eel", "Fi Fox"}, {"opponents": {}, "players": {}}, {})


def innings(bat, fow, bowl=(), runs="120", wickets="3"):
    return {"team_batting_id": "1", "runs": runs, "wickets": wickets, "bat": list(bat), "fow": list(fow), "bowl": list(bowl)}


def b(pos, name, how, runs, bowler="", fielder="", pid=None, bid="", fid=""):
    return {"position": str(pos), "batsman_name": name, "batsman_id": pid or name[:2], "how_out": how, "runs": str(runs),
            "bowler_name": bowler, "bowler_id": bid, "fielder_name": fielder, "fielder_id": fid, "balls": "", "fours": "", "sixes": ""}


def test_partnerships_from_fall_of_wickets():
    # Al and Bo open; Al out at 20, Cy out at 50, Bo out at 90; Di and Ed unbeaten to 120
    h = innings([b(1, "Al Ant", "b", 15, "Opp One", bid="901"), b(2, "Bo Bee", "ct", 44, "Opp Two", "Opp Three", bid="902", fid="903"),
                 b(3, "Cy Cat", "lbw", 12, "Opp One", bid="901"), b(4, "Di Dog", "no", 25), b(5, "Ed Eel", "no", 18), b(6, "Fi Fox", "dnb", "")],
                [{"runs": "20", "wickets": "1", "batsman_out_name": "Al Ant", "batsman_out_id": "Al"},
                 {"runs": "50", "wickets": "2", "batsman_out_name": "Cy Cat", "batsman_out_id": "Cy"},
                 {"runs": "90", "wickets": "3", "batsman_out_name": "Bo Bee", "batsman_out_id": "Bo"}],
                bowl=[{"bowler_name": "Opp One", "bowler_id": "901", "overs": "8", "maidens": "1", "runs": "30", "wickets": "2", "wides": "1", "no_balls": "0"}])
    o = innings([b(1, "Zed Zebra", "b", 40, "Di Dog", bid="Di"), b(2, "Yan Yak", "ct", 5, "Bo Bee", "Cy Cat", bid="Bo", fid="Cy"),
                 b(3, "Xi Ox", "ctb", 9, "Ed Eel", "Ed Eel", bid="Ed", fid="Ed"), b(4, "Wu Wolf", "ro", 1, "", "Fi Fox", fid="Fi"), b(5, "Vi Vole", "no", 30)],
                [{"runs": "45", "wickets": "1"}, {"runs": "60", "wickets": "2"}], runs="99", wickets="4")
    issues = []
    d = scorecard_detail(h, o, BASE, names(), 1, 2, 120, "test", issues)
    p = [(r["Wicket"], r["Runs"], r["Batter1"], r["Batter2"], r["Unbroken"]) for r in d["partnerships"]]
    assert p == [(1, 20, "Al Ant", "Bo Bee", False), (2, 30, "Bo Bee", "Cy Cat", False), (3, 40, "Bo Bee", "Di Dog", False), (4, 30, "Di Dog", "Ed Eel", True)], p
    dis = {r["Batter"]: (r["How"], r.get("Bowler"), r.get("Fielder")) for r in d["dismissals"]}
    assert dis == {"Al Ant": ("Bowled", "Opp One", None), "Bo Bee": ("Caught", "Opp Two", "Opp Three"), "Cy Cat": ("LBW", "Opp One", None)}, dis
    ob = {r["Batter"]: (r["Dismissal"], r.get("Bowler"), r.get("Fielder")) for r in d["opp_batting"]}
    assert ob["Zed Zebra"] == ("Bowled", "Di Dog", None) and ob["Yan Yak"] == ("Caught", "Bo Bee", "Cy Cat")
    assert ob["Xi Ox"] == ("Ct & B", "Ed Eel", "Ed Eel") and ob["Wu Wolf"] == ("Run Out", None, "Fi Fox") and ob["Vi Vole"][1] is None
    assert d["opp_bowling"][0]["Bowler"] == "Opp One" and d["opp_bowling"][0]["Wickets"] == 2 and d["opp_bowling"][0]["BowlerId"] == "901"
    assert len([r for r in d["fow"] if r["Side"] == "Hessle"]) == 3 and len([r for r in d["fow"] if r["Side"] == "Opposition"]) == 2
    assert not issues, issues
    return "4 partnerships (20, 30, 40, 30*) add up to 120; dismissals, opposition batting/bowling and both falls of wickets correct"


def test_other_fall_of_wickets_field_names():
    order = [{"name": "Al Ant", "id": "1"}, {"name": "Bo Bee", "id": "2"}, {"name": "Cy Cat", "id": "3"}]
    issues = []
    h = innings([], [{"score": "10", "wicket": "1", "batsman_name": "Al Ant"}])
    from transform import _fow_entries
    fow = _fow_entries(h, "test", issues)
    assert fow == [(1, 10, "Al Ant", None)], fow
    rows = _partnerships(order, fow, 25, BASE, "test", issues)
    assert [(r["Runs"], r["Unbroken"]) for r in rows] == [(10, False), (15, True)] and not issues
    odd = _fow_entries(innings([], [{"total": "10", "w": "1"}]), "test", issues)
    assert odd == [] and any("doesn't read" in i for i in issues)
    return "fall of wickets read with other field names; unknown field names reported, not guessed"


def test_partnerships_that_dont_add_up_are_left_out():
    order = [{"name": "Al Ant", "id": "1"}, {"name": "Bo Bee", "id": "2"}, {"name": "Cy Cat", "id": "3"}]
    issues = []
    rows = _partnerships(order, [(1, 10, "Somebody Else", None)], 30, BASE, "test", issues)
    assert rows == [] and any("couldn't tell who was out" in i for i in issues)
    issues = []
    rows = _partnerships(order[:2], [(1, 10, "Al Ant", None)], 30, BASE, "test", issues)
    assert rows == [] and any("not the total" in i for i in issues), issues
    return "partnerships that can't be worked out, or don't add up to the total, are left out with a note"


def test_existing_games_get_detail_once():
    tmp = sandbox(); before = data(tmp)
    rep = run(tmp, "publish"); after = data(tmp)
    assert "Full scorecard detail added" in rep
    n = int(rep.split("Full scorecard detail added (opposition, dismissals, partnerships): ")[1].split()[0])
    assert n > 0, rep[:800]
    sig = lambda d, ds: sorted(json.dumps(r, sort_keys=True) for r in d[ds] if r["Date"].startswith("2026"))
    for ds in ("matches", "batting", "bowling", "fielding", "keeping"):
        assert sig(before, ds) == sig(after, ds), f"{ds} changed while adding detail"
    assert after["opp_batting"] and after["dismissals"]   # (the test scorecards have no opposition bowling: covered by the first test)
    rep2 = run(tmp, "publish"); shutil.rmtree(tmp)
    assert "Full scorecard detail added (opposition, dismissals, partnerships): 0" in rep2 and "Unchanged since last run: 0" not in rep2
    return f"first publish adds detail to {n} existing 2026 games without changing their Hessle rows; the next publish skips them as unchanged"


def test_older_memory_triggers_one_refetch():
    tmp = sandbox(); run(tmp, "publish")
    sp = os.path.join(tmp, "pipeline", "state.json"); st = json.load(open(sp))
    for v in st["matches"].values():
        v.pop("dv", None)                                    # memory from before the detail existed
    json.dump(st, open(sp, "w"))
    rep = run(tmp, "publish"); shutil.rmtree(tmp)
    assert "Unchanged since last run: 0" in rep, rep[:600]
    return "matches remembered from before the detail existed are fetched once more"


if __name__ == "__main__":
    ok = 0; tests = [test_partnerships_from_fall_of_wickets, test_other_fall_of_wickets_field_names, test_partnerships_that_dont_add_up_are_left_out,
                     test_existing_games_get_detail_once, test_older_memory_triggers_one_refetch]
    for t in tests:
        try:
            print("PASS", t.__name__, "-", t()); ok += 1
        except Exception as e:
            print("FAIL", t.__name__, "-", repr(e)[:1500])
    print(f"{ok}/{len(tests)} passed")
    sys.exit(0 if ok == len(tests) else 1)
