"""Dry-run report fixes, from the first 2018-2026 run.

    python tests/make_fixtures.py docs/data.json tests/fixtures   # once
    python tests/test_dry_run_report.py
"""
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_pipeline import sandbox, data   # noqa: E402
from test_pc_detail import run, pick, load_md, save_md, hessle_bat, fx_path   # noqa: E402
from test_third_xi import add_third_xi_fixtures   # noqa: E402


def setup():
    tmp = sandbox(); run(tmp, "publish"); d = data(tmp)
    (mid, date), = pick(tmp, 1)
    names = {r["Batter"] for r in d["batting"] if r["Date"] == date}
    return tmp, d, mid, date, names


def section(rep, date):
    i = rep.find(f"### {date}")
    if i < 0:
        return ""
    j = rep.find("\n### ", i + 4); k = rep.find("\n## ", i + 4)
    ends = [x for x in (j, k) if x > 0]
    return rep[i:min(ends) if ends else None]


def test_batting_order_is_one_line():
    tmp, d, mid, date, names = setup()
    obj = load_md(tmp, mid); inn = hessle_bat(obj["match_details"][0], names)
    bats = [b for b in inn["bat"] if b.get("how_out") not in ("", "dnb", None)]
    bats[0]["position"], bats[1]["position"] = bats[1]["position"], bats[0]["position"]
    save_md(tmp, mid, obj)
    rep = run(tmp, "dry_run"); shutil.rmtree(tmp)
    sec = section(rep, date)
    lines = [l for l in sec.splitlines() if l.startswith("- ")]
    assert lines and lines[0].startswith("- batting order:") and len(lines) == 1, sec
    return f"{date}: two batters swapped -> one line ({lines[0][2:60]}...), no false run/boundary differences"


def test_missing_overs_kept():
    tmp, d, mid, date, names = setup()
    obj = load_md(tmp, mid)
    for inn in obj["match_details"][0]["innings"]:
        inn["overs"] = ""
    save_md(tmp, mid, obj)
    rep = run(tmp, "dry_run")
    assert "Overs" not in section(rep, date), section(rep, date)
    # and a publish that replaces the match for another reason keeps the overs
    obj = load_md(tmp, mid); other = next(i for i in obj["match_details"][0]["innings"] if not any(b.get("batsman_name") in names for b in i["bat"]))
    other["bowl"][0]["maidens"] = str(int(other["bowl"][0].get("maidens") or 0) + 1); save_md(tmp, mid, obj)
    before = next(m for m in d["matches"] if m["Date"] == date)
    run(tmp, "publish"); after = next(m for m in data(tmp)["matches"] if m["Date"] == date and m["Team"] == before["Team"]); shutil.rmtree(tmp)
    assert (after["Overs Batted"], after["Overs Bowled"]) == (before["Overs Batted"], before["Overs Bowled"]), (after, before)
    return f"{date}: no overs on Play-Cricket -> not a difference, and kept on publish ({before['Overs Batted']}/{before['Overs Bowled']})"


def test_impossible_wicket_total_corrected():
    tmp = sandbox(); run(tmp, "publish"); d = data(tmp)
    for mid, date in pick(tmp, 20):                      # a match where Hessle took plenty of wickets
        m0 = next(m for m in d["matches"] if m["Date"] == date)
        if m0["Wickets Taken"] >= 6:
            break
    names = {r["Batter"] for r in d["batting"] if r["Date"] == date and r["Team"] == m0["Team"]}
    obj = load_md(tmp, mid)
    opp = next(i for i in obj["match_details"][0]["innings"] if not any(b.get("batsman_name") in names for b in i["bat"]))
    opp["wickets"] = "2"                                 # innings total not fully entered
    save_md(tmp, mid, obj)
    rep = run(tmp, "dry_run"); shutil.rmtree(tmp)
    assert "Wickets Taken" not in section(rep, date), section(rep, date)
    assert f"Play-Cricket total says 2 wickets taken, but its scorecard shows {m0['Wickets Taken']}" in rep
    return f"{date}: Play-Cricket total 2 wickets vs {m0['Wickets Taken']} on its own scorecard -> scorecard count used, warning in report"


def test_keep_site_list():
    tmp, d, mid, date, names = setup()
    team = next(m["Team"] for m in d["matches"] if m["Date"] == date)
    obj = load_md(tmp, mid); inn = hessle_bat(obj["match_details"][0], names)
    b = next(x for x in inn["bat"] if x["runs"] not in ("", None)); b["runs"] = str(int(b["runs"]) + 50)
    save_md(tmp, mid, obj)
    with open(os.path.join(tmp, "pipeline", "manual", "keep_site.csv"), "a") as f:
        f.write(f"{date},{team} XI,PC scorecard incomplete\n")
    rep = run(tmp, "dry_run")
    assert f"{date} {team} XI" in rep[rep.find("## Kept as on the site"):] and not section(rep, date), rep[-800:]
    before = data(tmp); run(tmp, "publish"); after = data(tmp); shutil.rmtree(tmp)
    assert before == after
    return f"{date} {team} XI on the keep list: reported as kept, not compared, untouched by publish"


def test_new_matches_listed():
    tmp = sandbox(); run(tmp, "publish"); add_third_xi_fixtures(tmp, n=3)
    rep = run(tmp, "dry_run"); shutil.rmtree(tmp)
    sec = rep[rep.find("## New matches"):]
    assert "## New matches" in rep and "2026 3rd XI 3" in sec and sec.count("\n- 2026-") >= 3, sec[:400]
    return "dry run lists new matches, with a count by season and XI (3 x 2026 3rd XI)"


if __name__ == "__main__":
    ok = 0; tests = [test_batting_order_is_one_line, test_missing_overs_kept, test_impossible_wicket_total_corrected,
                     test_keep_site_list, test_new_matches_listed]
    for t in tests:
        try:
            print("PASS", t.__name__, "-", t()); ok += 1
        except Exception as e:
            print("FAIL", t.__name__, "-", repr(e)[:1500])
    print(f"{ok}/{len(tests)} passed")
    sys.exit(0 if ok == len(tests) else 1)
