"""Convert one Play-Cricket match_detail record into the dashboard's rows.

Every derived figure is calculated the same way as the existing data:
  SR = runs / balls * 100              % of TR = runs / team runs * 100
  Boundary% = (4s + 6s) / balls * 100  Economy = runs / true overs
  Strike Rate = balls / wickets (0 if no wickets)
  Overs are cricket notation: 4.3 means 4 overs and 3 balls (27 balls).
Batting "Innings" = 1 if Hessle batted first, else 2; bowling "Innings" = 1 if
Hessle bowled first. Caught-and-bowled counts as a fielding catch by the bowler.

Anything unrecognised is reported in `issues`, never silently guessed.
"""
import re
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal


def r2(x):
    """Round to 2dp the way Excel does (1.625 -> 1.63), so values match the existing data."""
    return float(Decimal(repr(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

# Play-Cricket how_out codes -> dashboard dismissal labels. Codes are matched
# case-insensitively with spaces/punctuation removed, so "Run Out", "run out"
# and "runout" all match. Unknown codes are reported for this file to be extended.
HOW_OUT = {
    "b": "Bowled", "bowled": "Bowled",
    "ct": "Caught", "c": "Caught", "caught": "Caught",
    "lbw": "LBW",
    "st": "Stumped", "stumped": "Stumped",
    "runout": "Run Out", "ro": "Run Out",
    "no": "Not Out", "notout": "Not Out",
    "dnb": "DNB", "didnotbat": "DNB",
    "ctb": "Ct & B", "cb": "Ct & B", "ctandb": "Ct & B", "candb": "Ct & B", "caughtandbowled": "Ct & B",
    "hw": "Hit Wicket", "hitwicket": "Hit Wicket",
    "rtd": "Retired", "retired": "Retired", "retiredout": "Retired", "ro_retired": "Retired",
    "rh": "Retired Not Out", "retiredhurt": "Retired Not Out", "retirednotout": "Retired Not Out", "rtno": "Retired Not Out",
}


def _code(s):
    return re.sub(r"[^a-z_]", "", (s or "").lower().replace("&", "and"))


def num(v):
    """Play-Cricket sends numbers as strings, with "" for 'not recorded'."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return v
    s = str(v).strip()
    if s in ("", "-", "\u2013"):
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    return int(f) if f.is_integer() and "." not in s else f


def overs_value(v):
    """'40.5' -> 40.5, '45' -> 45 (same shape as the spreadsheet data)."""
    return num(v)


def overs_to_balls(o):
    if o is None:
        return 0
    whole = int(o)
    return whole * 6 + int(round((o - whole) * 10))


def valid_overs(o):
    return o is None or int(round((o - int(o)) * 10)) <= 5


def norm_name(s):
    s = (s or "").lower().replace("&", " and ")
    s = re.sub(r"\bcricket club\b|\bcc\b", " ", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


class Names:
    """Resolves Play-Cricket names to the names the dashboard already uses."""

    def __init__(self, existing_opponents, existing_players, aliases, state_players):
        self.opp_by_norm = {norm_name(o): o for o in existing_opponents}
        self.opp_alias = {norm_name(k): v for k, v in aliases.get("opponents", {}).items()}
        self.player_alias = {k.strip().lower(): v for k, v in aliases.get("players", {}).items()}
        self.known_players = set(existing_players)
        self.by_id = state_players  # player_id -> dashboard name (kept in state.json)
        self.new_players, self.new_opponents = set(), set()

    def opponent(self, club_name):
        n = norm_name(club_name)
        if n in self.opp_alias:
            return self.opp_alias[n]
        if n in self.opp_by_norm:
            return self.opp_by_norm[n]
        self.new_opponents.add(club_name)
        return re.sub(r"\s+", " ", club_name).strip()

    def player(self, name, pid=None):
        name = re.sub(r"\s+", " ", (name or "")).strip()
        if not name:
            return None
        pid = str(pid) if pid not in (None, "") else None
        if pid and pid in self.by_id:
            return self.by_id[pid]
        resolved = self.player_alias.get(name.lower(), name)
        if resolved not in self.known_players:
            self.new_players.add(resolved)
        if pid:
            self.by_id[pid] = resolved
        return resolved


def _date(s):
    return datetime.strptime(s.strip(), "%d/%m/%Y").strftime("%Y-%m-%d")


def hessle_side(md, club_match, team_map):
    """Return ('home'|'away', team_id, team_label '1st'/'2nd') or None."""
    for side in ("home", "away"):
        club = md.get(f"{side}_club_name") or ""
        if club_match.lower() in club.lower():
            label = team_map.get((md.get(f"{side}_team_name") or "").strip())
            return side, str(md.get(f"{side}_team_id")), label
    return None


def convert(md, names, club_match, team_map):
    """Returns (rows, issues). rows is None if the match should be skipped."""
    issues = []
    mid = md.get("id") or md.get("match_id")
    side_info = hessle_side(md, club_match, team_map)
    if not side_info:
        return None, [f"match {mid}: no Hessle team found"]
    side, hid, team = side_info
    if not team:
        return None, [f"match {mid}: Hessle team '{md.get(side + '_team_name')}' isn't mapped to 1st/2nd XI (skipped)"]

    other = "away" if side == "home" else "home"
    date = _date(md["match_date"])
    opponent = names.opponent(md.get(f"{other}_club_name"))
    tag = f"{date} {team} XI v {opponent}"

    innings = md.get("innings") or []
    if len(innings) == 0:
        return None, [f"{tag}: no scorecard yet ({md.get('result_description') or 'no result'}) - skipped"]
    if len(innings) > 2:
        return None, [f"{tag}: {len(innings)} innings (multi-day) isn't supported - skipped"]
    h_inn = next((i for i in innings if str(i.get("team_batting_id")) == hid), None)
    o_inn = next((i for i in innings if str(i.get("team_batting_id")) != hid), None)
    if h_inn is None or o_inn is None:
        return None, [f"{tag}: scorecard is incomplete (only one innings entered) - skipped for now"]

    res = (md.get("result") or "").upper()
    applied = str(md.get("result_applied_to") or "")
    if res in ("W", "L"):
        won = (applied == hid) if res == "W" else (applied != hid)
        result = "W" if won else "L"
    elif res in ("D",):
        result = "D"
    elif res in ("T",):
        result = "D"
        issues.append(f"{tag}: tied - recorded as 'D' (the dashboard has no tie category)")
    else:
        return None, [f"{tag}: result '{res or 'none'}' ({md.get('result_description')}) - skipped"]

    batted_first = str(md.get("batted_first") or "")
    if batted_first:
        hessle_bat_first = batted_first == hid
    else:
        hessle_bat_first = innings.index(h_inn) == 0
        issues.append(f"{tag}: batted_first missing - inferred from innings order")

    base = {"Date": date, "Team": team, "Opponent": opponent, "Home/Away": "Home" if side == "home" else "Away"}
    h_runs, o_runs = num(h_inn.get("runs")) or 0, num(o_inn.get("runs")) or 0
    match = dict(base, **{
        "Toss Won/Lost": "W" if str(md.get("toss_won_by_team_id") or "") == hid else "L",
        "Bat/Field First": "Bat" if hessle_bat_first else "Field",
        "Result": result,
        "Runs Scored": h_runs, "Wickets Lost": num(h_inn.get("wickets")) or 0,
        "Runs Conceded": o_runs, "Wickets Taken": num(o_inn.get("wickets")) or 0,
        "Overs Batted": overs_value(h_inn.get("overs")), "Overs Bowled": overs_value(o_inn.get("overs")),
    })
    for k in ("Overs Batted", "Overs Bowled"):
        if not valid_overs(match[k]):
            issues.append(f"{tag}: invalid overs value {match[k]} for {k}")

    # ---- Hessle batting
    batting, unknown_codes = [], set()
    bat_inn_no = 1 if hessle_bat_first else 2
    for b in h_inn.get("bat") or []:
        code = _code(b.get("how_out"))
        runs = num(b.get("runs"))
        dismissal = HOW_OUT.get(code)
        if dismissal is None:
            if code == "" and runs is None:
                dismissal = "DNB"
            else:
                dismissal = (b.get("how_out") or "").strip().title() or "Unknown"
                unknown_codes.add(b.get("how_out"))
        if dismissal == "Caught" and b.get("fielder_id") and b.get("fielder_id") == b.get("bowler_id"):
            dismissal = "Ct & B"
        row = dict(base, Innings=bat_inn_no, Pos=num(b.get("position")),
                   Batter=names.player(b.get("batsman_name"), b.get("batsman_id")), Dismissal=dismissal)
        if dismissal == "DNB":
            row.update({"Runs": "-", "Balls": "-", "4s": "-", "6s": "-", "SR": "-", "% of TR": "0", "Boundary%": "0"})
        else:
            balls, fours, sixes = num(b.get("balls")), num(b.get("fours")), num(b.get("sixes"))
            runs = runs or 0
            row.update({
                "Runs": runs, "Balls": balls if balls is not None else "-",
                "4s": fours if fours is not None else "-", "6s": sixes if sixes is not None else "-",
                # balls None = not recorded ("-"); 0 balls faced = strike rate 0, as in the existing data
                "SR": "-" if balls is None else (r2(runs / balls * 100) if balls else 0),
                "% of TR": r2(runs / h_runs * 100) if h_runs else 0,
                "Boundary%": r2(((fours or 0) + (sixes or 0)) / balls * 100) if balls and fours is not None and sixes is not None else "0",
            })
            if balls is None:
                issues.append(f"{tag}: no balls recorded for {row['Batter']} (strike rate will be blank)")
        row.update({"Team Runs": h_runs, "W/L": result})
        batting.append(row)

    extras = num(h_inn.get("total_extras"))
    if extras is None:
        extras = sum(num(h_inn.get(k)) or 0 for k in ("extra_byes", "extra_leg_byes", "extra_wides", "extra_no_balls", "extra_penalty_runs"))
    bat_sum = sum(r["Runs"] for r in batting if isinstance(r["Runs"], (int, float)))
    if bat_sum + extras != h_runs:
        issues.append(f"{tag}: batters' runs ({bat_sum}) + extras ({extras}) = {bat_sum + extras}, but the total is {h_runs}")

    # ---- Hessle bowling (in the opposition's innings)
    bowling = []
    bowl_inn_no = 2 if hessle_bat_first else 1
    o_overs = overs_value(o_inn.get("overs"))
    for w in o_inn.get("bowl") or []:
        ov = overs_value(w.get("overs")) or 0
        balls = overs_to_balls(ov)
        runs, wk = num(w.get("runs")) or 0, num(w.get("wickets")) or 0
        if not valid_overs(ov):
            issues.append(f"{tag}: invalid overs {ov} for bowler {w.get('bowler_name')}")
        bowling.append(dict(base, Innings=bowl_inn_no, Bowler=names.player(w.get("bowler_name"), w.get("bowler_id")),
                            Overs=ov, Maidens=num(w.get("maidens")) or 0, Runs=runs, Wickets=wk,
                            Wides=num(w.get("wides")) or 0, **{"No Balls": num(w.get("no_balls")) or 0,
                            "Economy": r2(runs / (balls / 6)) if balls else 0,
                            "Strike Rate": r2(balls / wk) if wk else 0, "Balls": balls,
                            "W/L": result, "Opp Runs": o_runs, "Opp Overs": o_overs}))
    bowled_balls = sum(r["Balls"] for r in bowling)
    if o_overs is not None and bowled_balls != overs_to_balls(o_overs):
        issues.append(f"{tag}: bowlers' overs add up to {bowled_balls} balls, but the innings lasted {overs_to_balls(o_overs)}")

    # ---- Hessle fielding / keeping (from how the opposition batters were out)
    players = md.get("players") or []
    team_sheet = []
    for block in players:
        team_sheet += block.get(f"{side}_team", []) if isinstance(block, dict) else []
    keepers_id = {str(p.get("player_id")) for p in team_sheet if p.get("wicket_keeper") in (True, "true", "True", "yes", "Yes")}
    keepers_nm = {(p.get("player_name") or "").strip().lower() for p in team_sheet if str(p.get("player_id")) in keepers_id}
    if not keepers_id:
        issues.append(f"{tag}: no wicketkeeper flagged on the team sheet - catches all counted as fielding")
    fielding, keeping = [], []
    for b in o_inn.get("bat") or []:
        code = _code(b.get("how_out"))
        kind = HOW_OUT.get(code)
        fid, fname = str(b.get("fielder_id") or ""), (b.get("fielder_name") or "").strip()
        bowler = names.player(b.get("bowler_name"), b.get("bowler_id")) if b.get("bowler_name") else None
        is_keeper = (fid and fid in keepers_id) or (fname.lower() in keepers_nm and fname)
        if kind == "Ct & B" or (kind == "Caught" and fid and fid == str(b.get("bowler_id") or "")):
            fielding.append(dict(base, Innings=bowl_inn_no, Fielder=bowler, Dismissal="Caught", Bowler=bowler))
        elif kind == "Caught":
            if not fname:
                issues.append(f"{tag}: catch with no fielder named (batter {b.get('batsman_name')})")
                continue
            who = names.player(fname, fid)
            (keeping if is_keeper else fielding).append(
                dict(base, Innings=bowl_inn_no, **({"Keeper": who} if is_keeper else {"Fielder": who}), Dismissal="Caught", Bowler=bowler))
        elif kind == "Stumped":
            who = names.player(fname, fid) if fname else (next((p.get("player_name") for p in team_sheet if str(p.get("player_id")) in keepers_id), None))
            if who:
                keeping.append(dict(base, Innings=bowl_inn_no, Keeper=names.player(who) if not fname else who, Dismissal="Stumping", Bowler=bowler))
            else:
                issues.append(f"{tag}: stumping with no keeper identifiable")
        elif kind == "Run Out":
            if fname:
                fielding.append(dict(base, Innings=bowl_inn_no, Fielder=names.player(fname, fid), Dismissal="Run Out", Bowler=None))
            else:
                issues.append(f"{tag}: run out with no fielder named (batter {b.get('batsman_name')}) - not credited")
        elif kind is None and code and num(b.get("runs")) is not None:
            unknown_codes.add(b.get("how_out"))

    for c in sorted(x for x in unknown_codes if x):
        issues.append(f"{tag}: unrecognised dismissal code '{c}' - add it to HOW_OUT in transform.py")

    points = {}
    for p in md.get("points") or []:
        if str(p.get("team_id")) == hid:
            points = {k: num(v) for k, v in p.items() if k != "team_id"}

    return {"match": match, "batting": batting, "bowling": bowling, "fielding": fielding,
            "keeping": keeping, "points": points}, issues
