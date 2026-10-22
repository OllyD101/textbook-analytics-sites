"""docs/data-meta.json: the dashboard's "Updated" stamp.

    python tests/make_fixtures.py docs/data.json tests/fixtures   # once
    python tests/test_data_meta.py
"""
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_pipeline import sandbox, data   # noqa: E402
from test_third_xi import add_third_xi_fixtures   # noqa: E402


def run(tmp, mode, now, *extra):
    p = subprocess.run([sys.executable, "pipeline/run.py", "--mode", mode, "--fixtures", "tests/fixtures", "--seasons", "2026", *extra],
                       cwd=tmp, capture_output=True, text=True, env=dict(os.environ, PIPELINE_NOW=now, PIPELINE_TODAY="2026-10-01"))
    assert p.returncode == 0, p.stdout + p.stderr


def meta(tmp):
    return json.load(open(os.path.join(tmp, "docs", "data-meta.json")))


def test_stamp_moves_only_when_data_changes():
    tmp = sandbox(); before = meta(tmp)
    run(tmp, "dry_run", "2027-05-01T18:00:00Z")
    assert meta(tmp) == before, "a dry run must not touch the stamp"
    run(tmp, "publish", "2027-05-01T18:30:00Z")
    first = meta(tmp)          # nothing new on Play-Cricket: data unchanged, stamp unchanged
    assert first == before, (first, before)
    add_third_xi_fixtures(tmp, n=2)
    run(tmp, "publish", "2027-05-01T19:00:00Z")
    after = meta(tmp); d = data(tmp); shutil.rmtree(tmp)
    assert after["updated"] == "2027-05-01T19:00:00Z", after
    assert after["latest_match"] == max(m["Date"] for m in d["matches"]), after
    return f"dry run and no-change runs leave it alone; a run with new games stamps it ({after})"


def test_workflow_commits_it():
    wf = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".github", "workflows", "update-data.yml")).read()
    assert "docs/data-meta.json" in wf
    return "the workflow commits docs/data-meta.json"


if __name__ == "__main__":
    ok = 0; tests = [test_stamp_moves_only_when_data_changes, test_workflow_commits_it]
    for t in tests:
        try:
            print("PASS", t.__name__, "-", t()); ok += 1
        except Exception as e:
            print("FAIL", t.__name__, "-", repr(e)[:1200])
    print(f"{ok}/{len(tests)} passed")
    sys.exit(0 if ok == len(tests) else 1)
