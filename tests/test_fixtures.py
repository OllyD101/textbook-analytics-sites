"""Fixtures file (docs/fixtures.csv) tests.

    python tests/make_fixtures.py docs/data.json tests/fixtures   # once
    python tests/test_fixtures.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_pipeline import REPO, sandbox, data   # noqa: E402

sys.path.insert(0, os.path.join(REPO, "pipeline"))
import fixtures   # noqa: E402
import fantasy   # noqa: E402

SAMPLE = """Date,XI,Opponent,Home/Away
# 2027 sample (Excel-style dates)
03/04/2027,1st,Walkington CC,H
03/04/2027,2nd XI,Sutton CC,A
04/04/2027,3rd,Cottingham CC,A

10/04/2027,1st,Kirkella CC,A
24/04/2027,1st,Welton CC,H
26/04/2027,3rd,Driffield CC,H
2027-05-29,1st,Beverley Town,A
31/05/2027,3rd,Hull Strikers,H
32/05/2027,1st,Typo CC,H
05/06/2027,4th,Juniors,H
"""


def with_file(text):
    tmp = tempfile.mkdtemp(); p = os.path.join(tmp, "fixtures.csv")
    open(p, "w", encoding="utf-8").write(text)
    old = fixtures.PATH; fixtures.PATH = p
    return old, tmp


def test_reads_the_file_and_flags_typos():
    old, tmp = with_file(SAMPLE)
    try:
        games, problems = fixtures.load()
        assert len(games) == 8, games
        assert {g["xi"] for g in games} == {"1st", "2nd", "3rd"}
        assert any("32/05/2027" in p for p in problems) and any('"4th"' in p for p in problems), problems
        assert fixtures.seasons() == {2027}
    finally:
        fixtures.PATH = old; shutil.rmtree(tmp)
    return f"{len(games)} games read; {len(problems)} typos reported by row ({'; '.join(problems)})"


def test_weekends_follow_the_fixtures():
    old, tmp = with_file(SAMPLE)
    try:
        cases = {
            "2027-04-03": date(2027, 4, 5),    # Saturday + Sunday 3rd XI: Monday
            "2027-04-10": date(2027, 4, 12),   # Saturday only: still Monday at the earliest
            "2027-04-24": date(2027, 4, 27),   # Monday 26 April game (not a bank holiday): Tuesday
            "2027-05-29": date(2027, 6, 1),    # bank holiday Monday game: Tuesday
            "2027-06-05": date(2027, 6, 7),    # no valid games that week: Monday
        }
        for gw, want in cases.items():
            got = fantasy.weekend_end(gw)
            assert got == want, (gw, got, want)
        # a season with no fixtures in the file falls back to the bank holiday rule
        assert fantasy.weekend_end("2026-05-23") == date(2026, 5, 26) and fantasy.weekend_end("2026-05-16") == date(2026, 5, 18)
    finally:
        fixtures.PATH = old; shutil.rmtree(tmp)
    return "weekends end the day after the last fixture (Monday at the earliest); seasons without fixtures use the bank holiday rule"


def test_good_friday_game():
    old, tmp = with_file("Date,XI\n14/04/2028,1st\n15/04/2028,1st\n17/04/2028,3rd\n")
    try:
        assert fantasy.gameweek_of("2028-04-14") == "2028-04-08"
        assert fantasy.weekend_end("2028-04-08") == date(2028, 4, 15), "the week before waits for the Good Friday game"
        assert fantasy.weekend_end("2028-04-15") == date(2028, 4, 18)
    finally:
        fixtures.PATH = old; shutil.rmtree(tmp)
    return "a Good Friday game holds the previous gameweek's prices until Saturday"


def test_empty_or_missing_file_changes_nothing():
    old, tmp = with_file("Date,XI,Opponent,Home/Away\n")
    try:
        assert fixtures.load() == ([], []) and fantasy.weekend_end("2027-05-29") == date(2027, 6, 1)
        fixtures.PATH = os.path.join(tmp, "missing.csv")
        assert fixtures.load() == ([], []) and fantasy.weekend_end("2027-05-22") == date(2027, 5, 24)
    finally:
        fixtures.PATH = old; shutil.rmtree(tmp)
    return "empty or missing file: the usual Monday / bank holiday rule"


def test_report_flags_rearranged_games():
    tmp = sandbox(); d = data(tmp)
    games = sorted({(m["Date"], m["Team"]) for m in d["matches"] if m["Date"].startswith("2026")})
    listed, left_out = games[:-1], games[-1]
    rows = ["Date,XI,Opponent,Home/Away"] + [f"{date.fromisoformat(x).strftime('%d/%m/%Y')},{t},Somebody,H" for x, t in listed]
    open(os.path.join(tmp, "docs", "fixtures.csv"), "w").write("\n".join(rows) + "\n")
    p = subprocess.run([sys.executable, "pipeline/run.py", "--mode", "dry_run", "--fixtures", "tests/fixtures"], cwd=tmp, capture_output=True, text=True)
    report = open(os.path.join(tmp, "pipeline", "last_report.md")).read(); shutil.rmtree(tmp)
    assert p.returncode == 0, p.stdout + p.stderr
    want = f"{left_out[0]} {left_out[1]} XI"
    assert "not in docs/fixtures.csv" in report and want in report, report[-1500:]
    return f"report flags {want}, which was played but isn't in the fixtures file"


if __name__ == "__main__":
    ok = 0; tests = [test_reads_the_file_and_flags_typos, test_weekends_follow_the_fixtures, test_good_friday_game,
                     test_empty_or_missing_file_changes_nothing, test_report_flags_rearranged_games]
    for t in tests:
        try:
            print("PASS", t.__name__, "-", t()); ok += 1
        except Exception as e:
            print("FAIL", t.__name__, "-", repr(e)[:800])
    print(f"{ok}/{len(tests)} passed")
    sys.exit(0 if ok == len(tests) else 1)
