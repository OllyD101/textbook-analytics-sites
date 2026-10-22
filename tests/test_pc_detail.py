"""Older Play-Cricket scorecards often have no balls faced, fours or sixes.

    python tests/make_fixtures.py tests/baseline_data.json tests/fixtures   # once
    python tests/test_pc_detail.py

A blank on Play-Cricket (or a 0 against a blank on the site) is "not recorded": the dry run must not list it,
and publishing must keep the site's figures. Two different numbers are still a difference.
"""
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_pipeline import sandbox, data, put   # noqa: E402


def fx_path(tmp, *p):
    return os.path.join(tmp, "tests", "fixtures", *p)


def matches_2026(tmp):
    return json.load(open(fx_path(tmp, "matches_2026.json")))["matches"]


def load_md(tmp, mid):
    return json.load(open(fx_path(tmp, f"match_{mid}.json")))


def save_md(tmp, mid, obj):
    json.dump(obj, open(fx_path(tmp, f"match_{mid}.json"), "w"))


def hessle_bat(md, site_names):
    for inn in md["innings"]:
        if any(b.get("batsman_name") in site_names for b in inn.get("bat") or []):
            return inn
    raise AssertionError("no Hessle innings")


def run(tmp, mode):
    p = subprocess.run([sys.executable, "pipeline/run.py", "--mode", mode, "--fixtures", "tests/fixtures", "--seasons", "2026"],
                       cwd=tmp, capture_output=True, text=True, env=dict(os.environ, PIPELINE_TODAY="2026-10-01"))
    assert p.returncode == 0, p.stdout + p.stderr
    return open(os.path.join(tmp, "pipeline", "last_report.md"), encoding="utf-8").read()


def pick(tmp, n):
    """n 2026 matches that have a fixture file, with their dashboard key."""
    out = []
    for m in matches_2026(tmp):
        if os.path.exists(fx_path(tmp, f"match_{m['id']}.json")):
            md = load_md(tmp, m["id"])["match_details"][0]
            d, mo, y = md["match_date"].split("/")
            out.append((m["id"], f"{y}-{mo}-{d}"))
        if len(out) == n:
            return out


def site_rows(d, date, team=None):
    return [r for r in d["batting"] if r["Date"] == date and (team is None or r["Team"] == team) and r["Dismissal"] != "DNB"]


def blank_detail(tmp, mid, names):
    obj = load_md(tmp, mid); inn = hessle_bat(obj["match_details"][0], names)
    for b in inn["bat"]:
        b["fours"], b["sixes"], b["balls"] = "", "", ""
    save_md(tmp, mid, obj)


def test_unrecorded_detail_is_not_a_difference():
    tmp = sandbox(); d = data(tmp)
    run(tmp, "publish")                                   # site and Play-Cricket in step first
    base = run(tmp, "dry_run")
    (mid, date), = pick(tmp, 1)
    names = {r["Batter"] for r in d["batting"] if r["Date"] == date}
    blank_detail(tmp, mid, names)
    rep = run(tmp, "dry_run"); shutil.rmtree(tmp)
    bad = [l for l in rep.splitlines() if ("4s" in l or "6s" in l or "Balls" in l) and "PC" in l]
    assert not bad, bad[:5]
    assert rep.count("### ") == base.count("### "), "a match with only unrecorded detail is listed as different"
    return f"{date}: Play-Cricket with no balls/fours/sixes -> no differences reported"


def test_zero_against_site_blank_is_not_a_difference():
    tmp = sandbox(); run(tmp, "publish"); d = data(tmp)
    (mid, date), = pick(tmp, 1)
    rows = site_rows(d, date)
    for r in d["batting"]:
        if r in rows and r["Runs"] == 0:
            pass
    target = [r for r in d["batting"] if r["Date"] == date and r["Dismissal"] != "DNB"][:3]
    for r in target:
        r["4s"], r["6s"] = "-", "-"                    # site: not recorded; Play-Cricket: 0 or a count
    put(tmp, d)
    obj = load_md(tmp, mid); inn = hessle_bat(obj["match_details"][0], {r["Batter"] for r in target})
    for b in inn["bat"]:
        if b["batsman_name"] in {r["Batter"] for r in target}:
            b["fours"], b["sixes"] = "0", "0"
    save_md(tmp, mid, obj)
    rep = run(tmp, "dry_run"); shutil.rmtree(tmp)
    bad = [l for l in rep.splitlines() if ("4s" in l or "6s" in l) and "PC" in l]
    assert not bad, bad[:5]
    return f"{date}: site '-' vs Play-Cricket 0 -> no differences reported"


def test_real_difference_still_reported():
    tmp = sandbox(); run(tmp, "publish"); d = data(tmp)
    (mid, date), = pick(tmp, 1)
    names = {r["Batter"] for r in d["batting"] if r["Date"] == date}
    obj = load_md(tmp, mid); inn = hessle_bat(obj["match_details"][0], names)
    b = next(x for x in inn["bat"] if x.get("fours") not in ("", None))
    b["fours"] = str(int(b["fours"]) + 3)
    save_md(tmp, mid, obj)
    rep = run(tmp, "dry_run"); shutil.rmtree(tmp)
    assert any(b["batsman_name"] in l and "4s" in l for l in rep.splitlines()), "a real fours difference was hidden"
    return f"{date}: {b['batsman_name']} fours differ (two numbers) -> still reported"


def test_publish_keeps_site_detail():
    tmp = sandbox(); run(tmp, "publish"); before = data(tmp)
    (mid, date), = pick(tmp, 1)
    names = {r["Batter"] for r in before["batting"] if r["Date"] == date}
    blank_detail(tmp, mid, names)
    # plus a real correction elsewhere in the same match, so it gets replaced
    obj = load_md(tmp, mid); md = obj["match_details"][0]
    other = next(i for i in md["innings"] if not any(x.get("batsman_name") in names for x in i.get("bat") or []))
    other["bowl"][0]["maidens"] = str(int(other["bowl"][0].get("maidens") or 0) + 1)
    bowler = other["bowl"][0]["bowler_name"]
    save_md(tmp, mid, obj)
    rep = run(tmp, "publish"); after = data(tmp); shutil.rmtree(tmp)
    assert "amended" in rep and date in rep, rep[:800]
    key = lambda r: (r["Innings"], r["Pos"], r["Batter"])
    b0 = {key(r): r for r in before["batting"] if r["Date"] == date}
    b1 = {key(r): r for r in after["batting"] if r["Date"] == date}
    for k, r in b0.items():
        for f in ("Balls", "4s", "6s", "SR", "Boundary%"):
            assert b1[k].get(f) == r.get(f), (k, f, r.get(f), b1[k].get(f))
    m0 = next(r for r in before["bowling"] if r["Date"] == date and r["Bowler"] == bowler)
    m1 = next(r for r in after["bowling"] if r["Date"] == date and r["Bowler"] == bowler)
    assert m1["Maidens"] == m0["Maidens"] + 1
    return f"{date}: match replaced for a real bowling correction; every batter's balls/fours/sixes kept from the site"


if __name__ == "__main__":
    ok = 0; tests = [test_unrecorded_detail_is_not_a_difference, test_zero_against_site_blank_is_not_a_difference,
                     test_real_difference_still_reported, test_publish_keeps_site_detail]
    for t in tests:
        try:
            print("PASS", t.__name__, "-", t()); ok += 1
        except Exception as e:
            print("FAIL", t.__name__, "-", repr(e)[:1200])
    print(f"{ok}/{len(tests)} passed")
    sys.exit(0 if ok == len(tests) else 1)
