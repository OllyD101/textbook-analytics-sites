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
            # a remembered name still follows aliases.json, so an alias added later applies to players already seen
            known = self.by_id[pid]
            resolved = self.player_alias.get(name.lower()) or self.player_alias.get(known.lower()) or known
            self.by_id[pid] = resolved
            return resolved
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
        return None, [f"match {mid}: Hessle team '{md.get(side + '_team_name')}' isn't one of the XIs in config.json 'teams' (skipped)"]

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

    detail = scorecard_detail(h_inn, o_inn, base, names, bat_inn_no, bowl_inn_no, h_runs, tag, issues)
    return dict({"match": match, "batting": batting, "bowling": bowling, "fielding": fielding,
                 "keeping": keeping, "points": points}, **detail), issues



# ---------------------------------------------------------------- the rest of the scorecard
# Opposition batting and bowling, who dismissed each Hessle batter, the fall of wickets, and Hessle's partnerships.
# Opposition players keep their Play-Cricket names and IDs (the IDs join them up across seasons); Hessle players go
# through the usual name matching.
# Stored with the site data. The fall of wickets is read to work out partnerships but not kept (it would add a lot of
# size for nothing the dashboard shows); empty fields are left out of these rows for the same reason.
DETAIL_SETS = ("opp_batting", "opp_bowling", "dismissals", "partnerships")
NOT_OUT_KINDS = {"Not Out", "DNB", "Retired Not Out"}


def _pid(v):
    v = str(v or "").strip()
    return v or None


def _fow_entries(inn, tag, issues):
    """Fall of wickets as [(wicket, runs, out_name, out_id)], whatever the field names turn out to be."""
    out, odd = [], set()
    for i, f in enumerate(inn.get("fow") or []):
        if not isinstance(f, dict):
            continue
        runs = num(f.get("runs") if "runs" in f else f.get("score"))
        wkt = num(next((f.get(k) for k in ("wickets", "wicket", "wkt", "wicket_number") if k in f), i + 1))
        name = next((f.get(k) for k in ("batsman_out_name", "batsman_name", "player_name", "out_batsman_name", "batsman") if f.get(k)), None)
        pid = next((f.get(k) for k in ("batsman_out_id", "batsman_id", "player_id", "out_batsman_id") if f.get(k)), None)
        if runs is None:
            odd.add(", ".join(sorted(f.keys())))
            continue
        out.append((int(wkt or i + 1), runs, (name or "").strip() or None, _pid(pid)))
    for k in odd:
        issues.append(f"{tag}: fall of wickets has fields this version doesn't read ({k}) - tell Claude so it can be added")
    return sorted(out)


def _partnerships(order, fow, total, base, tag, issues):
    """Hessle partnerships from the batting order and the fall of wickets (who was out at each wicket).
    Returns [] (with a note) if it doesn't add up to the team total."""
    if not fow or len(order) < 2:
        return []
    crease, nxt, prev, rows = [order[0], order[1]], 2, 0, []
    for wkt, runs, out_name, out_id in fow:
        out = next((p for p in crease if (out_id and p["id"] == out_id) or (out_name and p["name"].lower() == out_name.lower())), None)
        if out is None:
            issues.append(f"{tag}: partnership for wicket {wkt} not worked out (couldn't tell who was out)")
            return []
        a, b = crease
        rows.append(dict(base, Wicket=wkt, Runs=runs - prev, Batter1=a["name"], Batter2=b["name"], Unbroken=False))
        prev = runs
        crease.remove(out)
        if nxt < len(order):
            crease.append(order[nxt]); nxt += 1
        else:
            break
    if len(crease) == 2 and total is not None and total - prev > 0:
        rows.append(dict(base, Wicket=len(fow) + 1, Runs=total - prev, Batter1=crease[0]["name"], Batter2=crease[1]["name"], Unbroken=True))
    if total is not None and sum(r["Runs"] for r in rows) != total:
        issues.append(f"{tag}: partnerships add up to {sum(r['Runs'] for r in rows)}, not the total {total} - left out")
        return []
    return rows


def scorecard_detail(h_inn, o_inn, base, names, bat_inn_no, bowl_inn_no, h_runs, tag, issues):
    key = {k: base[k] for k in ("Date", "Team", "Opponent")}
    opp_batting, opp_bowling, dismissals, fow = [], [], [], []
    # the opposition's batting, and which Hessle player got each of them out
    for b in o_inn.get("bat") or []:
        kind = HOW_OUT.get(_code(b.get("how_out")))
        runs = num(b.get("runs"))
        if kind == "DNB" or (runs is None and kind in (None, "DNB")):
            continue
        bowler = names.player(b.get("bowler_name"), b.get("bowler_id")) if (b.get("bowler_name") or "").strip() else None
        fielder = names.player(b.get("fielder_name"), b.get("fielder_id")) if (b.get("fielder_name") or "").strip() else None
        if kind == "Ct & B" or (kind == "Caught" and _pid(b.get("fielder_id")) and _pid(b.get("fielder_id")) == _pid(b.get("bowler_id"))):
            fielder = bowler
        opp_batting.append(dict(key, Innings=bowl_inn_no, Pos=num(b.get("position")), Batter=(b.get("batsman_name") or "").strip(),
                                BatterId=_pid(b.get("batsman_id")), Dismissal=kind or (b.get("how_out") or ""),
                                Bowler=bowler if kind not in NOT_OUT_KINDS and kind != "Run Out" else None,
                                Fielder=fielder if kind in ("Caught", "Ct & B", "Stumped", "Run Out") else None,
                                Runs=runs, Balls=num(b.get("balls")), **{"4s": num(b.get("fours")), "6s": num(b.get("sixes"))}))
    # the opposition's bowling
    for w in h_inn.get("bowl") or []:
        if not (w.get("bowler_name") or "").strip():
            continue
        opp_bowling.append(dict(key, Innings=bat_inn_no, Bowler=w["bowler_name"].strip(), BowlerId=_pid(w.get("bowler_id")),
                                Overs=overs_value(w.get("overs")), Maidens=num(w.get("maidens")), Runs=num(w.get("runs")),
                                Wickets=num(w.get("wickets")), Wides=num(w.get("wides")), **{"No Balls": num(w.get("no_balls"))}))
    # who dismissed each Hessle batter; and the batting order for partnerships
    order = []
    for b in sorted(h_inn.get("bat") or [], key=lambda x: num(x.get("position")) or 99):
        kind = HOW_OUT.get(_code(b.get("how_out")))
        name = names.player(b.get("batsman_name"), b.get("batsman_id")) if (b.get("batsman_name") or "").strip() else None
        if not name or kind == "DNB":
            continue
        order.append({"name": name, "id": _pid(b.get("batsman_id"))})
        if kind in NOT_OUT_KINDS or kind is None or kind == "Retired":
            continue
        bowler = (b.get("bowler_name") or "").strip() or None
        fielder = (b.get("fielder_name") or "").strip() or None
        if kind == "Ct & B":
            fielder = bowler
        dismissals.append(dict(key, Innings=bat_inn_no, Batter=name, How=kind,
                               Bowler=bowler if kind != "Run Out" else None, BowlerId=_pid(b.get("bowler_id")) if kind != "Run Out" else None,
                               Fielder=fielder if kind in ("Caught", "Ct & B", "Stumped", "Run Out") else None,
                               FielderId=_pid(b.get("fielder_id")) if kind in ("Caught", "Ct & B", "Stumped", "Run Out") else None))
    # fall of wickets, both sides
    h_fow, o_fow = _fow_entries(h_inn, tag, issues), _fow_entries(o_inn, tag, issues)
    for side, inn_no, entries, ours in (("Hessle", bat_inn_no, h_fow, True), ("Opposition", bowl_inn_no, o_fow, False)):
        for wkt, runs, out_name, out_id in entries:
            who = (names.player(out_name, out_id) if out_name else None) if ours else out_name
            fow.append(dict(key, Innings=inn_no, Side=side, Wicket=wkt, Runs=runs, Out=who, OutId=out_id))
    partnerships = _partnerships(order, [(w, r, (names.player(n, i) if n else None), i) for w, r, n, i in h_fow], h_runs, key, tag, issues)
    compact = lambda rows: [{k: v for k, v in r.items() if v is not None and v != ""} for r in rows]
    return {"opp_batting": compact(opp_batting), "opp_bowling": compact(opp_bowling), "dismissals": compact(dismissals),
            "partnerships": compact(partnerships), "fow": fow}
