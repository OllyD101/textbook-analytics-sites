"""Sunday and bank holiday Monday games: the 3rd XI play on Sundays, the 1st and 2nd XI on Saturdays,
and some games fall on bank holiday Mondays.

    python tests/make_fixtures.py docs/data.json tests/fixtures   # once
    python tests/test_sunday_games.py

Checks that a Sunday 3rd XI game:
  - comes through the automation like any other match (Play-Cricket-shaped scorecard),
  - counts in that weekend's fantasy gameweek (the Saturday before), alongside Saturday's games,
  - lets a player who plays on both days score for both,
  - never moves prices until the weekend is over (Monday), so prices can't move on Saturday's
    games and then move back once Sunday's arrive.
and that a bank holiday Monday game counts in that weekend too, with prices waiting until Tuesday.
"""
import copy
import json
import os
import shutil
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_pipeline import REPO, sandbox, data   # noqa: E402
from test_third_xi import add_third_xi_fixtures, run_with   # noqa: E402

sys.path.insert(0, os.path.join(REPO, "pipeline"))
import fantasy   # noqa: E402
import fantasy_sync   # noqa: E402
import bank_holidays   # noqa: E402

DS = ("batting", "bowling", "fielding", "keeping")


def sunday_after(iso):
    return (date.fromisoformat(iso) + timedelta(days=1)).isoformat()


def with_sunday_third_xi(d, src_date, src_team="2nd", extra_player=None):
    """Copy one real Saturday match as a 3rd XI game on the Sunday after. extra_player (from Saturday's
    1st XI) is put into the Sunday side as well, so he plays on both days."""
    d = copy.deepcopy(d); sun = sunday_after(src_date)
    m = next(x for x in d["matches"] if x["Date"] == src_date and x["Team"] == src_team)
    d["matches"].append(dict(m, Date=sun, Team="3rd"))
    for ds in DS:
        d[ds] += [dict(r, Date=sun, Team="3rd") for r in d[ds] if r["Date"] == src_date and r["Team"] == src_team]
    if extra_player:
        b = next(r for r in d["batting"] if r["Date"] == sun and r["Team"] == "3rd" and r.get("Dismissal") != "DNB")
        b["Batter"] = extra_player
    return d, sun


def busy_saturday(d, season="2026"):
    """A 2026 Saturday when both the 1st and 2nd XI played."""
    by = {}
    for m in d["matches"]:
        if m["Date"].startswith(season) and date.fromisoformat(m["Date"]).weekday() == 5:
            by.setdefault(m["Date"], set()).add(m["Team"])
    return sorted(x for x, t in by.items() if {"1st", "2nd"} <= t)[3]


def test_sunday_game_counts_in_that_weekends_gameweek():
    d = data(REPO); sat = busy_saturday(d)
    first = [r["Batter"] for r in d["batting"] if r["Date"] == sat and r["Team"] == "1st" and r.get("Dismissal") != "DNB" and (r.get("Runs") or 0) > 0]
    both = first[0]
    d2, sun = with_sunday_third_xi(d, sat, extra_player=both)
    assert fantasy.gameweek_of(sun) == sat, "the Sunday should belong to the Saturday gameweek"
    sp = fantasy.season_points(d2, 2026)
    assert sun not in sp, "no separate Sunday gameweek"
    sat_only = fantasy.season_points(d, 2026)[sat]
    sun_game = fantasy.match_points(d2, sun, "3rd")
    # every player's weekend total = Saturday + Sunday
    for p in set(sat_only) | set(sun_game):
        want = sat_only.get(p, {}).get("total", 0) + sun_game.get(p, {}).get("total", 0)
        assert sp[sat][p]["total"] == want, (p, sp[sat][p]["total"], want)
    assert any(" 3rd XI " in x for x in sp[sat][both]["matches"]) and any(" 1st XI " in x for x in sp[sat][both]["matches"]), sp[sat][both]["matches"]
    assert sp[sat][both]["total"] > sat_only[both]["total"], "a player on both days scores for both games"
    return f"Sunday {sun} counts in gameweek {sat}; {both} played both days ({sat_only[both]['total']} + {sun_game[both]['total']} = {sp[sat][both]['total']} pts)"


def test_prices_wait_for_the_weekend():
    d = data(REPO); sat = busy_saturday(d)
    d2, sun = with_sunday_third_xi(d, sat)
    players = fantasy.build_players(d2, 2026)
    sat_d = date.fromisoformat(sat)
    on = {k: sat_d + timedelta(days=n) for k, n in (("saturday", 0), ("sunday", 1), ("monday", 2))}
    hist = {k: fantasy.price_changes(d2, 2026, players, t)[0] for k, t in on.items()}
    gws = lambda h: {gw for v in h.values() for gw, _ in v}
    assert sat not in gws(hist["saturday"]) and sat not in gws(hist["sunday"]), "no price change for a weekend still being played"
    assert sat in gws(hist["monday"]), "prices move on the Monday"
    # Saturday and Sunday runs give exactly the same prices: nothing moves and then moves back
    assert fantasy.price_changes(d2, 2026, players, on["saturday"])[1] == fantasy.price_changes(d2, 2026, players, on["sunday"])[1]
    # the Monday price includes the Sunday game: the 3rd XI players aren't treated as having missed the week
    third = sorted({r["Batter"] for r in d2["batting"] if r["Date"] == sun and r.get("Dismissal") != "DNB"} - {r["Batter"] for r in d["batting"] if r["Date"] == sat and r["Team"] == "1st"})
    with_sun = fantasy.price_changes(d2, 2026, players, on["monday"])[0]
    without_sun = fantasy.price_changes(d, 2026, players, on["monday"])[0]
    changed = [p for p in third if dict(with_sun[p]).get(sat) != dict(without_sun.get(p, [])).get(sat)]
    # and with no date given (the demo and older callers), every scored gameweek counts as before
    assert gws(fantasy.price_changes(d2, 2026, players)[0]) == set(fantasy.season_points(d2, 2026))
    return f"prices for {sat} held on Saturday and Sunday, applied Monday; Sunday's game changed the Monday price of {len(changed)} 3rd XI player(s)"


def test_sync_sends_sunday_points_at_once_but_prices_on_monday():
    d = data(REPO); sat = busy_saturday(d)
    d2, sun = with_sunday_third_xi(d, sat)
    gw_dates = set(fantasy.season_points(d2, 2026))
    sun_d = date.fromisoformat(sun)
    _, pts, skipped, _, prices = fantasy_sync.build_payload(d2, 2026, gw_dates, today=sun_d)
    assert not skipped, skipped
    assert any(r["gw"] == sat and any(" 3rd XI " in m for m in r["matches"]) for r in pts), "Sunday points go out on Sunday evening"
    assert not any(r["gw"] == sat for r in prices), "no price rows for the weekend until Monday"
    _, _, _, _, prices_mon = fantasy_sync.build_payload(d2, 2026, gw_dates, today=sun_d + timedelta(days=1))
    assert any(r["gw"] == sat for r in prices_mon)
    return f"Sunday run sends {sum(1 for r in pts if r['gw'] == sat)} player scores for {sat} and no prices; Monday run adds the prices"


def move_fixture(tmp, days):
    """Turn a real Saturday scorecard into a 3rd XI game `days` later. Returns (saturday, new date)."""
    added = add_third_xi_fixtures(tmp, n=1); fx = os.path.join(tmp, "tests", "fixtures")
    md = added[0]; dd, mm, yy = md["match_date"].split("/")
    sat = date(int(yy), int(mm), int(dd)); sun = sat + timedelta(days=days)
    md["match_date"] = sun.strftime("%d/%m/%Y")
    json.dump({"match_details": [md]}, open(os.path.join(fx, f"match_{md['id']}.json"), "w"))
    listing = json.load(open(os.path.join(fx, "matches_2026.json")))
    for e in listing["matches"]:
        if e["id"] == md["id"]:
            e["match_date"] = md["match_date"]
    json.dump(listing, open(os.path.join(fx, "matches_2026.json"), "w"))
    return sat, sun


def test_automation_takes_a_sunday_scorecard():
    tmp = sandbox(); sat, sun = move_fixture(tmp, 1)
    run_with(tmp, {"PIPELINE_TODAY": "2026-10-01"}, "--seasons", "2026")
    d = data(tmp); shutil.rmtree(tmp)
    got = [m for m in d["matches"] if m["Team"] == "3rd"]
    assert [m["Date"] for m in got] == [sun.isoformat()], got
    assert date.fromisoformat(got[0]["Date"]).weekday() == 6
    rows = {ds: sum(1 for r in d[ds] if r["Team"] == "3rd") for ds in DS}
    assert rows["batting"] and rows["bowling"], rows
    assert fantasy.gameweek_of(got[0]["Date"]) == sat.isoformat()
    return f"Sunday {sun} 3rd XI scorecard published ({rows['batting']} batting, {rows['bowling']} bowling rows), fantasy gameweek {sat}"


# ---------------------------------------------------------------- bank holiday Mondays
OFFICIAL_MONDAYS = {   # from gov.uk/bank-holidays (England and Wales), the cricket-season ones
    2025: ["2025-04-21", "2025-05-05", "2025-05-26", "2025-08-25"],
    2026: ["2026-04-06", "2026-05-04", "2026-05-25", "2026-08-31"],
    2027: ["2027-03-29", "2027-05-03", "2027-05-31", "2027-08-30"],
    2028: ["2028-04-17", "2028-05-01", "2028-05-29", "2028-08-28"],
}


def test_bank_holiday_dates():
    for y, want in OFFICIAL_MONDAYS.items():
        got = [d.isoformat() for d in bank_holidays.bank_holidays(y, set(), set()) if d.weekday() == 0 and 3 <= d.month <= 9]
        assert got == want, (y, got, want)
    # one-offs from the settings: 2022's spring holiday moved to Thursday 2 June, plus the Queen's funeral
    extra, gone = {date(2022, 6, 2), date(2022, 6, 3), date(2022, 9, 19)}, {date(2022, 5, 30)}
    assert bank_holidays.is_bank_holiday(date(2022, 9, 19), extra, gone) and not bank_holidays.is_bank_holiday(date(2022, 5, 30), extra, gone)
    assert fantasy.weekend_end("2027-05-29") == date(2027, 6, 1) and fantasy.weekend_end("2027-05-22") == date(2027, 5, 24)
    return "2025-2028 match gov.uk; one-off extras and moved holidays respected; bank holiday weekends end on Tuesday"


def test_bank_holiday_monday_game():
    d = data(REPO); sat = "2026-05-23"; mon = "2026-05-25"
    src = next(m for m in d["matches"] if m["Date"].startswith("2026") and m["Team"] == "2nd")
    d2 = copy.deepcopy(d); d2["matches"].append(dict(src, Date=mon, Team="3rd"))
    for ds in DS:
        d2[ds] += [dict(r, Date=mon, Team="3rd") for r in d[ds] if r["Date"] == src["Date"] and r["Team"] == "2nd"]
    assert fantasy.gameweek_of(mon) == sat
    sp = fantasy.season_points(d2, 2026)
    assert any(" 3rd XI " in x and x.startswith(mon) for p in sp[sat].values() for x in p["matches"]), "Monday game counts in that weekend"
    players = fantasy.build_players(d2, 2026)
    gws = lambda t: {gw for v in fantasy.price_changes(d2, 2026, players, t)[0].values() for gw, _ in v}
    assert sat not in gws(date(2026, 5, 25)), "prices wait through the bank holiday Monday"
    assert sat in gws(date(2026, 5, 26)), "and move on Tuesday"
    assert "2026-05-16" in gws(date(2026, 5, 18)), "an ordinary weekend still moves on Monday"
    return f"Monday {mon} game counts in gameweek {sat}; prices held on the Monday, applied Tuesday"


def test_automation_takes_a_monday_scorecard():
    tmp = sandbox(); sat, mon = move_fixture(tmp, 2)
    run_with(tmp, {"PIPELINE_TODAY": "2026-10-01"}, "--seasons", "2026")
    d = data(tmp); shutil.rmtree(tmp)
    got = [m["Date"] for m in d["matches"] if m["Team"] == "3rd"]
    assert got == [mon.isoformat()] and fantasy.gameweek_of(got[0]) == sat.isoformat(), got
    return f"Monday {mon} scorecard published, fantasy gameweek {sat}"


if __name__ == "__main__":
    ok = 0; tests = [test_sunday_game_counts_in_that_weekends_gameweek, test_prices_wait_for_the_weekend,
                     test_sync_sends_sunday_points_at_once_but_prices_on_monday, test_automation_takes_a_sunday_scorecard,
                     test_bank_holiday_dates, test_bank_holiday_monday_game, test_automation_takes_a_monday_scorecard]
    for t in tests:
        try:
            print("PASS", t.__name__, "-", t()); ok += 1
        except Exception as e:
            print("FAIL", t.__name__, "-", repr(e)[:600])
    print(f"{ok}/{len(tests)} passed")
    sys.exit(0 if ok == len(tests) else 1)
