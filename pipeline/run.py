"""Hessle CC dashboard updater.

    python pipeline/run.py                      # mode from PIPELINE_MODE (default dry_run)
    python pipeline/run.py --mode publish
    python pipeline/run.py --fixtures tests/fixtures   # offline test with saved responses

dry_run : fetch and convert this season's matches, compare them with the data
          already on the site, write a report. Changes nothing.
publish : add new / amended matches to docs/data.json, refresh docs/league.json,
          apply manual MoTM / PotM entries. Refuses to write if any blocking
          check fails (the GitHub job then fails and emails you).
"""
import argparse
import csv
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from pc_api import FixtureClient, PlayCricketClient, PlayCricketError  # noqa: E402
from transform import Names, convert, overs_to_balls, valid_overs, r2, norm_name  # noqa: E402
from awards import award_match_of_the_match, award_player_of_the_month, recheck_potm_for_teams, drop_placeholder_awards  # noqa: E402

DATASETS = ("matches", "batting", "bowling", "fielding", "keeping")


# ---------------------------------------------------------------- data file
def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_meta(path, data):
    """docs/data-meta.json: when the data last changed, for the dashboard's "Updated" stamp."""
    now = os.environ.get("PIPELINE_NOW") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")   # PIPELINE_NOW: tests only
    write_json(path, {"updated": now, "latest_match": max((m["Date"] for m in data["matches"]), default=None)}, compact=False)


def write_json(path, obj, compact=True):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        if compact:
            json.dump(obj, f, separators=(",", ":"), ensure_ascii=False)
        else:
            json.dump(obj, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def expand(obj, rev):
    if isinstance(obj, dict):
        return {rev.get(k, k): expand(v, rev) for k, v in obj.items()}
    if isinstance(obj, list):
        return [expand(v, rev) for v in obj]
    return obj


def shrink(obj, fwd):
    if isinstance(obj, dict):
        return {fwd.get(k, k): shrink(v, fwd) for k, v in obj.items()}
    if isinstance(obj, list):
        return [shrink(v, fwd) for v in obj]
    return obj


def mkey(r):
    return (r["Date"], r["Team"], r["Opponent"])


# ---------------------------------------------------------------- checks
def blocking_checks(data):
    """Problems that must stop a publish. Mirrors the dashboard's Data Quality page."""
    errs = []
    keys = Counter(mkey(m) for m in data["matches"])
    errs += [f"duplicate match {k}" for k, n in keys.items() if n > 1]
    match_keys = set(keys)
    for ds in ("batting", "bowling", "fielding", "keeping"):
        orphans = {mkey(r) for r in data[ds]} - match_keys
        errs += [f"{ds} rows with no matching match: {k}" for k in sorted(orphans)]
    for m in data["matches"]:
        for k in ("Overs Batted", "Overs Bowled"):
            v = m.get(k)
            if isinstance(v, (int, float)) and not valid_overs(v):
                errs.append(f"invalid overs {v} in {mkey(m)} {k}")
        if m.get("Result") not in ("W", "L", "D"):
            errs.append(f"unexpected result {m.get('Result')} in {mkey(m)}")
    return errs


# ---------------------------------------------------------------- compare (dry run)
# Balls faced, fours and sixes: Play-Cricket often has none recorded for older games. A blank there (or a 0
# against a blank on the site) means "not recorded", not a difference: the site's figure stands, in the
# dry-run report and when publishing. Two different numbers are still a difference.
DETAIL = ("Balls", "4s", "6s")


def _blank(v):
    return v in ("-", None, "")


def same_detail(site_v, pc_v):
    return site_v == pc_v or _blank(pc_v) or (pc_v == 0 and _blank(site_v))


NOT_OUT = {"Not Out", "DNB", "Retired", "Retired Not Out", "Retired Hurt", "Did Not Bat", "Absent", "Absent Hurt"}
OVERS = ("Overs Batted", "Overs Bowled")


def _bat_key(rows):
    """One match's batting rows keyed by (name, n-th time that name appears). Hessle bat once per match, so the
    innings number is left out (it differs whenever the site and Play-Cricket disagree on who batted first);
    a different batting order doesn't pair a batter with the wrong person, and repeated names like 'Unsure'
    still line up."""
    seen, out = Counter(), {}
    for r in sorted(rows, key=lambda r: r.get("Pos") or 99):
        seen[r["Batter"]] += 1
        out[(r["Batter"], seen[r["Batter"]])] = r
    return out


def fix_totals(rows, report, label):
    """A Play-Cricket innings total can say fewer wickets than its own scorecard shows (opposition innings
    not fully entered). Use the scorecard's count when it's higher, and say so in the report."""
    m = rows["match"]
    taken = sum(r["Wickets"] for r in rows["bowling"] if isinstance(r.get("Wickets"), (int, float))) + \
        sum(1 for r in rows["fielding"] if r.get("Dismissal") == "Run Out")
    lost = sum(1 for r in rows["batting"] if r.get("Dismissal") not in NOT_OUT)
    for f, n in (("Wickets Taken", taken), ("Wickets Lost", lost)):
        if isinstance(m.get(f), (int, float)) and n > m[f] and n <= 10:
            report["warnings"].append(f"{label}: Play-Cricket total says {m[f]} {f.lower()}, but its scorecard shows {n} - using {n}")
            m[f] = n


def load_keep_site():
    """pipeline/manual/keep_site.csv: matches where the site's version is right and Play-Cricket's isn't."""
    path = os.path.join(HERE, "manual", "keep_site.csv")
    keep = {}
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                d = (row.get("Date") or "").strip()
                team = {"1st XI": "1st", "2nd XI": "2nd", "3rd XI": "3rd"}.get((row.get("XI") or "").strip(), (row.get("XI") or "").strip())
                if d and team:
                    keep[(d, team)] = (row.get("Reason") or "").strip()
    return keep


def keep_site_detail(new, data):
    """Before a match is replaced, carry over balls/fours/sixes that Play-Cricket doesn't have."""
    k = mkey(new["match"])
    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)
    om = next((m for m in data["matches"] if (m["Date"], m["Team"]) == k[:2]), None)
    if om:
        for f in OVERS:   # no overs recorded on Play-Cricket: keep the site's
            if new["match"].get(f) in (None, 0, 0.0) and num(om.get(f)) and om[f]:
                new["match"][f] = om[f]
    old = _bat_key([r for r in data["batting"] if (r["Date"], r["Team"]) == k[:2]])
    kept = 0
    for key, r in _bat_key(new["batting"]).items():
        o = old.get(key)
        if not o or r.get("Dismissal") == "DNB" or o.get("Dismissal") == "DNB":
            continue
        changed = False
        for f in DETAIL:
            if r.get(f) != o.get(f) and same_detail(o.get(f), r.get(f)):
                r[f] = o.get(f); changed = True
        if not changed:
            continue
        kept += 1
        if all(r.get(f) == o.get(f) for f in DETAIL):
            for f in ("SR", "Boundary%", "BallsEstimated"):
                if f in o: r[f] = o[f]
        else:
            b, runs = r.get("Balls"), r.get("Runs")
            r["SR"] = "-" if not num(b) else (r2(runs / b * 100) if b and num(runs) else 0)
            r["Boundary%"] = r2((r["4s"] + r["6s"]) / b * 100) if num(b) and b and num(r.get("4s")) and num(r.get("6s")) else "0"
    return kept


FILTER_FIELDS = ("competition_type", "competition_name", "league_name", "match_type", "game_type")


def excluded_reason(m, cfg):
    """Why a Play-Cricket match (listing entry or full details) is left out, or None to include it."""
    f = cfg.get("match_filter") or {}
    ctype = (m.get("competition_type") or "").strip()
    if ctype and ctype.lower() in {t.lower() for t in f.get("exclude_competition_types", [])}:
        return f"competition type '{ctype}'"
    text = " | ".join(str(m.get(k)) for k in FILTER_FIELDS if m.get(k))
    for word in f.get("exclude_if_name_contains", []):
        if word.lower() in text.lower():
            return f"'{word}' in '{text}'"
    try:
        overs = int(float(m.get("no_of_overs") or 0))
    except (TypeError, ValueError):
        overs = 0
    if overs and overs in f.get("exclude_overs", []):
        return f"{overs}-over game"
    return None


def hessle_xi(m, cfg):
    """The Hessle XI ('1st', '2nd', '3rd') a Play-Cricket match (listing or details) is for, or None."""
    club = cfg["club_name_match"].lower()
    sides = [sd for sd in ("home", "away") if club in (m.get(f"{sd}_club_name") or "").lower()]
    return cfg["teams"].get((m.get(f"{sides[0]}_team_name") or "").strip()) if len(sides) == 1 else None


def league_history(client, league, seasons, candidates, known_divs, report, current_year):
    """Final league tables for past seasons, fetched once each and kept in league.json under "history"
    (season -> XI -> table). The division comes from a league match's details when this run didn't already see one."""
    hist = league.setdefault("history", {})
    for (season, team) in sorted(candidates):
        if season >= current_year or team in hist.get(season, {}):
            continue                                  # the current season comes from the weekly table
        div = known_divs.get((season, team))
        for mid in candidates[(season, team)][:4]:    # otherwise look at a few of that season's matches
            if div:
                break
            try:
                md = client.match_detail(mid)
            except PlayCricketError as e:
                report["errors"].append(str(e)); md = None
            if md and md.get("competition_type") == "League" and md.get("competition_id"):
                div = md["competition_id"]
        if not div:
            report["league_history"].append(f"{season} {team} XI: no league division found")
            continue
        try:
            lt = client.league_table(div)
        except PlayCricketError as e:
            report["errors"].append(str(e)); continue
        if not lt:
            report["league_history"].append(f"{season} {team} XI: Play-Cricket has no table for division {div}")
            continue
        parsed = parse_league(lt, team)
        if parsed["columns_missing"] or not any(r["isHessle"] for r in parsed["rows"]):
            report["league_history"].append(f"{season} {team} XI: table for division {div} not usable (missing {parsed['columns_missing'] or 'Hessle'})")
            continue
        hist.setdefault(season, {})[team] = parsed
        report["league_history"].append(f"{season} {team} XI: saved ({parsed.get('division_name') or div}, {len(parsed['rows'])} teams)")


def remove_excluded(m, data, cfg, aliases, report, mode, why):
    """A match Play-Cricket shows as excluded (cup, T20, friendly) that's already on the site: remove it on publish."""
    try:
        d = datetime.strptime(m.get("match_date") or "", "%d/%m/%Y").date().isoformat()
    except ValueError:
        return False
    club = cfg["club_name_match"].lower()
    sides = [sd for sd in ("home", "away") if club in (m.get(f"{sd}_club_name") or "").lower()]
    if len(sides) != 1:
        return False
    team = cfg["teams"].get((m.get(f"{sides[0]}_team_name") or "").strip())
    other = "away" if sides[0] == "home" else "home"
    opp = norm_name(m.get(f"{other}_club_name"))
    opp = norm_name(next((v for k, v in aliases.get("opponents", {}).items() if norm_name(k) == opp), opp))
    hit = [x for x in data["matches"] if x["Date"] == d and x["Team"] == team and norm_name(x["Opponent"]) == opp]
    if not hit:
        return False
    label = f"{d} {team} XI v {hit[0]['Opponent']} ({why})"
    if mode != "publish":
        report["would_remove"].append(label)
        return True
    for ds in DATASETS + ("motm",):
        data[ds] = [r for r in data.get(ds, []) if not (r["Date"] == d and r["Team"] == team)]
    report["removed"].append(label)
    return True


def compare_match(new, data):
    """Return a list of differences between a converted match and what's on the site."""
    k = mkey(new["match"])
    old_m = [m for m in data["matches"] if (m["Date"], m["Team"]) == k[:2]]
    if not old_m:
        return None  # genuinely new
    diffs = []
    om = old_m[0]
    if om["Opponent"] != k[2]:
        diffs.append(f"opponent name: site '{om['Opponent']}' vs Play-Cricket '{k[2]}' (add an alias)")
    for f, v in new["match"].items():
        if f in ("Opponent",):
            continue
        ov = om.get(f)
        if f in OVERS and v in (None, 0, 0.0) and isinstance(ov, (int, float)):
            continue  # Play-Cricket has no overs recorded: the site's stand
        if isinstance(v, (int, float)) and isinstance(ov, (int, float)):
            if abs(v - ov) > 0.01:
                diffs.append(f"{f}: site {ov} vs PC {v}")
        elif v != ov:
            diffs.append(f"{f}: site {ov!r} vs PC {v!r}")
    okey = mkey(om)

    def rows(ds, src, opp):
        return [r for r in src if (r["Date"], r["Team"], r["Opponent"]) == (k[0], k[1], opp)]

    site_bat, pc_bat = rows("batting", data["batting"], okey[2]), new["batting"]
    order = lambda rs: [r["Batter"] for r in sorted(rs, key=lambda r: r.get("Pos") or 99) if r.get("Dismissal") != "DNB"]
    so, po = order(site_bat), order(pc_bat)
    both = [n for n in so if n in po and so.count(n) == 1 and po.count(n) == 1]   # compare the order of names both have
    s_rank, p_rank = [n for n in so if n in both], [n for n in po if n in both]
    if s_rank != p_rank:
        moved = [f"{n} {so.index(n) + 1}->{po.index(n) + 1}" for n in s_rank if so.index(n) != po.index(n)]
        diffs.append("batting order: " + ", ".join(moved[:8]) + (" ..." if len(moved) > 8 else ""))
    ob, nb = _bat_key(site_bat), _bat_key(pc_bat)
    for key in sorted(set(ob) | set(nb), key=lambda k: ((ob.get(k) or nb.get(k)).get("Pos") or 99, k[1])):
        a, b = ob.get(key), nb.get(key)
        pos = (b or a).get("Pos")
        if not a or not b:
            diffs.append(f"batting #{pos}: {'missing on site' if not a else 'missing from PC'} ({(b or a).get('Batter')})")
            continue
        for f in ("Dismissal", "Runs", "Balls", "4s", "6s"):
            if f == "Balls" and a.get("BallsEstimated"):
                continue
            av, bv = a.get(f), b.get(f)
            if av in ("-", None, "") and bv in ("-", None, ""):
                continue
            if f in DETAIL and same_detail(av, bv):
                continue
            if av != bv:
                diffs.append(f"batting #{pos} {b.get('Batter')}: {f} site {av!r} vs PC {bv!r}")
    ow = {r["Bowler"]: r for r in rows("bowling", data["bowling"], okey[2])}
    nw = {r["Bowler"]: r for r in new["bowling"]}
    for n in sorted(set(ow) | set(nw)):
        a, b = ow.get(n), nw.get(n)
        if not a or not b:
            diffs.append(f"bowling {n}: {'missing on site' if not a else 'missing from PC'}")
            continue
        for f in ("Overs", "Maidens", "Runs", "Wickets", "Wides", "No Balls"):
            if a.get(f) != b.get(f) and not (isinstance(a.get(f), (int, float)) and isinstance(b.get(f), (int, float)) and abs(a.get(f) - b.get(f)) < 0.01):
                diffs.append(f"bowling {n}: {f} site {a.get(f)!r} vs PC {b.get(f)!r}")
    for ds, who in (("fielding", "Fielder"), ("keeping", "Keeper")):
        oc = Counter((r[who], r["Dismissal"]) for r in rows(ds, data[ds], okey[2]))
        nc = Counter((r[who], r["Dismissal"]) for r in new[ds])
        for (p, dis) in sorted(set(oc) | set(nc), key=str):
            if oc[(p, dis)] != nc[(p, dis)]:
                diffs.append(f"{ds} {p} {dis}: site {oc[(p, dis)]} vs PC {nc[(p, dis)]}")
    return diffs


# ---------------------------------------------------------------- merge (publish)
def merge_match(new, data):
    """Replace every row for this match (by date + XI) with the converted rows."""
    k = mkey(new["match"])
    old = [m for m in data["matches"] if (m["Date"], m["Team"]) == k[:2]]
    old_keys = {mkey(m) for m in old}
    for ds in DATASETS:
        data[ds] = [r for r in data[ds] if mkey(r) not in old_keys]
    data["matches"].append(new["match"])
    for ds in ("batting", "bowling", "fielding", "keeping"):
        data[ds].extend(new[ds])
    # keep MoTM pointing at the (possibly renamed) opponent
    for r in data.get("motm", []):
        if (r["Date"], r["Team"]) == k[:2]:
            r["Opponent"] = k[2]
    return "amended" if old else "added"


def apply_manual(data, report):
    """MoTM and PotM aren't on Play-Cricket scorecards, so they come from two small CSVs."""
    motm_path = os.path.join(HERE, "manual", "motm.csv")
    if os.path.exists(motm_path):
        with open(motm_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                d, team, player = (row.get("Date") or "").strip(), (row.get("Team") or "").strip(), (row.get("Player") or "").strip()
                if not d or not player:
                    continue
                team = {"1st XI": "1st", "2nd XI": "2nd"}.get(team, team)
                match = [m for m in data["matches"] if m["Date"] == d and m["Team"] == team]
                if not match:
                    report["warnings"].append(f"motm.csv: no {team} XI match on {d} (yet) for {player}")
                    continue
                data["motm"] = [r for r in data["motm"] if not (r["Date"] == d and r["Team"] == team)]
                data["motm"].append({"Date": d, "Opponent": match[0]["Opponent"], "Team": team, "MOTM": player, "Source": "Captain"})
    potm_path = os.path.join(HERE, "manual", "potm.csv")
    if os.path.exists(potm_path):
        months = defaultdict(list)
        for r in data.get("potm", []):
            months[r["player"]] += r["months"]
        with open(potm_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                m, p = (row.get("Month") or "").strip(), (row.get("Player") or "").strip()
                if m and p and m not in months[p]:
                    for other in months:  # a manual entry replaces whoever had that month
                        if m in months[other]:
                            months[other].remove(m)
                    months[p].append(m)
        data["potm"] = sorted([{"player": p, "wins": len(ms), "months": ms} for p, ms in months.items() if ms], key=lambda r: (-len(r["months"]), r["player"]))
    missing = [mkey(m) for m in data["matches"] if (m["Date"], m["Team"]) not in {(r["Date"], r["Team"]) for r in data["motm"]}]
    for k in missing:
        report["warnings"].append(f"no Man of the Match yet for {k[0]} {k[1]} XI v {k[2]} - add a line to pipeline/manual/motm.csv")


# ---------------------------------------------------------------- league tables
def parse_league(lt, team_label):
    heads = {k: (v or "").strip().lower() for k, v in (lt.get("headings") or {}).items()}

    def col(*names):
        for k, h in heads.items():
            if h in names:
                return k
        return None
    c = {"team": col("team"), "P": col("p", "played", "pld"), "W": col("w", "won"), "T": col("t", "tied", "tie"),
         "L": col("l", "lost"), "pts": col("pts", "points", "total"), "nrr": col("nrr", "net run rate")}
    rows = []
    for v in lt.get("values") or []:
        full = (v.get(c["team"]) or "").strip()
        club = full.split(" - ")[0].strip()
        n = lambda key: (float(v.get(c[key])) if c[key] and str(v.get(c[key])).strip() not in ("", "-") else 0)
        is_h = "hessle" in club.lower()
        rows.append({"pos": int(v.get("position") or len(rows) + 1),
                     "team": (club + (f" {team_label} XI" if team_label != "1st" else "")) if is_h else club,
                     "short": club.replace(" CC", "").strip(), "P": int(n("P")), "W": int(n("W")), "T": int(n("T")),
                     "L": int(n("L")), "pts": int(n("pts")), "nrr": round(n("nrr"), 2), "isHessle": is_h, "rem": 0})
    missing = [k for k in ("team", "P", "W", "L", "pts") if not c[k]]
    return {"division_name": lt.get("division_name"), "columns_missing": missing, "rows": rows}


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default=os.environ.get("PIPELINE_MODE") or None)
    ap.add_argument("--fixtures", default=None, help="folder of saved API responses (offline test)")
    ap.add_argument("--seasons", default=None, help="comma-separated, default = current season")
    args = ap.parse_args()

    cfg = load_json(os.path.join(HERE, "config.json"))
    mode = (args.mode or cfg.get("mode") or "dry_run").lower()
    if mode not in ("dry_run", "publish"):
        sys.exit(f"unknown mode {mode}")
    site_id = os.environ.get("PLAY_CRICKET_SITE_ID") or cfg.get("site_id")
    seasons = []
    for part in (args.seasons or os.environ.get("PIPELINE_SEASONS") or str(date.today().year)).split(","):
        part = part.strip()
        if "-" in part:   # a range such as 2018-2026
            a, b = (int(x) for x in part.split("-", 1)); seasons += [str(y) for y in range(a, b + 1)]
        elif part:
            seasons.append(part)
    only_teams = {t.strip() for t in (os.environ.get("PIPELINE_ONLY_TEAMS") or "").split(",") if t.strip()}

    if not args.fixtures and not os.environ.get("PLAY_CRICKET_API_TOKEN"):
        sys.exit("PLAY_CRICKET_API_TOKEN is not set (GitHub: Settings > Secrets and variables > Actions > Secrets)")
    client = FixtureClient(args.fixtures) if args.fixtures else PlayCricketClient(os.environ.get("PLAY_CRICKET_API_TOKEN"), cfg.get("pause_seconds", 1.0))
    if not args.fixtures and not site_id:
        sys.exit("PLAY_CRICKET_SITE_ID is not set (GitHub: Settings > Secrets and variables > Actions > Variables)")

    data_path = os.path.join(ROOT, cfg["data_path"])
    league_path = os.path.join(ROOT, cfg["league_path"])
    state_path = os.path.join(HERE, "state.json")
    fwd = load_json(os.path.join(HERE, "key_map.json"))
    rev = {v: k for k, v in fwd.items()}
    data = expand(load_json(data_path), rev)
    state = load_json(state_path, {"last_run": None, "matches": {}, "players": {}})
    aliases = load_json(os.path.join(HERE, "aliases.json"), {"opponents": {}, "players": {}})

    existing_players = {r.get("Batter") for r in data["batting"]} | {r.get("Bowler") for r in data["bowling"]} | \
        {r.get("Fielder") for r in data["fielding"]} | {r.get("Keeper") for r in data["keeping"]}
    names = Names({m["Opponent"] for m in data["matches"]}, existing_players, aliases, state.setdefault("players", {}))
    report = {"mode": mode, "added": [], "amended": [], "unchanged": [], "identical": [], "compared_ok": [], "compared_diff": {},
              "skipped": [], "filtered": [], "removed": [], "would_remove": [], "kept": [], "league_history": [], "warnings": [], "errors": [], "league": {}, "bonus": []}
    divisions = {}
    keep_site = load_keep_site()

    site_keys = {(x["Date"], x["Team"]) for x in data["matches"]}
    hist_candidates, hist_divs = {}, {}
    for season in seasons:
        try:
            listing = client.matches(site_id, season)
        except PlayCricketError as e:
            report["errors"].append(str(e))
            continue
        today = date.fromisoformat(os.environ['PIPELINE_TODAY']) if os.environ.get('PIPELINE_TODAY') else date.today()  # override for tests only
        for m in listing:
            if (m.get("status") or "New") == "Deleted" or (m.get("published") or "Yes") != "Yes":
                continue
            try:
                if datetime.strptime(m["match_date"], "%d/%m/%Y").date() > today:
                    continue
            except (KeyError, ValueError):
                continue
            # only fetch scorecards for the XIs the dashboard tracks (skip juniors, Sunday XI, etc.)
            hs = [sd for sd in ("home", "away") if cfg["club_name_match"].lower() in (m.get(f"{sd}_club_name") or "").lower()]
            if hs and not any((m.get(f"{sd}_team_name") or "").strip() in cfg["teams"] for sd in hs):
                continue
            if only_teams and not {cfg["teams"].get((m.get(f"{sd}_team_name") or "").strip()) for sd in hs} & only_teams:
                continue  # this run is limited to some XIs (e.g. the 3rd XI backfill), so leave the others alone
            mid = str(m.get("id"))
            xi = hessle_xi(m, cfg)
            if xi and not excluded_reason(m, cfg) and (not only_teams or xi in only_teams):
                hist_candidates.setdefault((season, xi), []).append(mid)
            why = excluded_reason(m, cfg)
            if why:
                report["filtered"].append(f"{m.get('match_date')} {m.get('home_team_name') or ''} v {m.get('away_team_name') or ''} ({m.get('home_club_name') or ''} v {m.get('away_club_name') or ''}): {why}")
                remove_excluded(m, data, cfg, aliases, report, mode, why)
                continue
            prev = state["matches"].get(mid)
            # skip a match only if Play-Cricket hasn't changed it AND it's really on the site (if data.json was
            # replaced by an older copy, the memory says "published" but the match is gone: fetch it again)
            on_site = prev and tuple(prev.get("key", [])[:2]) in site_keys
            if mode == "publish" and prev and prev.get("last_updated") == m.get("last_updated") and prev.get("published") and on_site:
                report["unchanged"].append(mid)
                continue
            try:
                md = client.match_detail(mid)
            except PlayCricketError as e:
                report["errors"].append(str(e))
                continue
            if not md:
                continue
            why = excluded_reason(md, cfg)
            if why:
                report["filtered"].append(f"{md.get('match_date')} {md.get('home_club_name') or ''} v {md.get('away_club_name') or ''}: {why}")
                remove_excluded(md, data, cfg, aliases, report, mode, why)
                continue
            rows, issues = convert(md, names, cfg["club_name_match"], cfg["teams"])
            if rows is None:
                report["skipped"] += issues
                continue
            report["warnings"] += issues
            k = mkey(rows["match"])
            label = f"{k[0]} {k[1]} XI v {k[2]}"
            if (k[0], k[1]) in keep_site:
                report["kept"].append(f"{label}" + (f" - {keep_site[(k[0], k[1])]}" if keep_site[(k[0], k[1])] else ""))
                continue
            fix_totals(rows, report, label)
            if md.get("competition_type") == "League" and md.get("competition_id"):
                divisions[k[1]] = (k[0], md["competition_id"])  # latest league match per XI wins
                hist_divs[(k[0][:4], k[1])] = md["competition_id"]
            if rows["points"]:
                report["bonus"].append((label, rows["points"]))
            if mode == "dry_run":
                diffs = compare_match(rows, data)
                if diffs is None:
                    report["added"].append(label + " (new - not on the site yet)")
                elif diffs:
                    report["compared_diff"][label] = diffs
                else:
                    report["compared_ok"].append(label)
            elif compare_match(rows, data) == []:
                report["identical"].append(label)   # same as the site already has: leave it untouched
            else:
                keep_site_detail(rows, data)        # balls/fours/sixes Play-Cricket doesn't have stay as they are
                report[merge_match(rows, data)].append(label)
                state["matches"][mid] = {"key": list(k), "last_updated": m.get("last_updated"), "published": True}

    # league tables for each XI's current division
    league = load_json(league_path, {"tables": {}})
    for team, (when, div) in sorted(divisions.items()):
        newest = max((m["Date"] for m in data["matches"] if m["Team"] == team), default=when)
        if when[:4] < newest[:4]:   # an older-season backfill: keep the current table
            report["warnings"].append(f"{team} XI league table left as it is: this run's latest {team} XI league game is from {when[:4]}, older than {newest[:4]}")
            continue
        try:
            lt = client.league_table(div)
        except PlayCricketError as e:
            report["errors"].append(str(e))
            continue
        if lt:
            parsed = parse_league(lt, team)
            report["league"][team] = parsed
            if parsed["columns_missing"]:
                report["warnings"].append(f"{team} XI league table: couldn't find columns {parsed['columns_missing']} - left unchanged")
            else:
                league["tables"][team] = parsed
                league.setdefault("history", {}).setdefault(when[:4], {})[team] = parsed
    if mode == "publish":
        this_year = (os.environ.get("PIPELINE_TODAY") or date.today().isoformat())[:4]
        league_history(client, league, seasons, hist_candidates, hist_divs, report, this_year)

    if report["errors"] and not (report["added"] or report["amended"] or report["compared_ok"] or report["compared_diff"]):
        write_report(report, names)
        sys.exit("Play-Cricket requests failed - nothing changed")

    if mode == "publish":
        # automatic awards first (Match Value, frozen once given), then any captain overrides
        acfg = cfg["awards"]
        today = date.fromisoformat(os.environ["PIPELINE_TODAY"]) if os.environ.get("PIPELINE_TODAY") else None  # tests only
        n_awards = drop_placeholder_awards(data, report)
        n_awards += award_match_of_the_match(data, acfg, report, today=today) + award_player_of_the_month(data, acfg, report, today=today) \
            + recheck_potm_for_teams(data, acfg, report)
        apply_manual(data, report)
        problems = blocking_checks(data)
        if problems:
            report["errors"] += problems
            write_report(report, names)
            sys.exit("Blocking data problems - nothing was published. See the report.")
        if report["added"] or report["amended"] or report["removed"] or n_awards or os.environ.get("FORCE_WRITE"):
            write_json(data_path, shrink(data, fwd))
            write_meta(os.path.join(os.path.dirname(data_path), "data-meta.json"), data)
        if report["league"] or any(": saved" in x for x in report["league_history"]):
            league["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            write_json(league_path, league, compact=False)
        state["last_run"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        write_json(state_path, state, compact=False)
    # Fantasy cricket: players, prices and points to Supabase (never blocks the dashboard update)
    try:
        from fantasy_sync import sync as fantasy_sync
        report["fantasy"] = fantasy_sync(data, mode, today=date.fromisoformat(os.environ["PIPELINE_TODAY"]) if os.environ.get("PIPELINE_TODAY") else None,
                                         review_path=os.path.join(os.path.dirname(data_path), "fantasy-player-stats.json") if mode == "publish" else None)
        if report["fantasy"].get("status") == "warning":
            report["warnings"].append(report["fantasy"]["note"])
    except Exception as e:
        report["warnings"].append(f"Fantasy sync could not run (dashboard update not affected): {e}")
    # Fixtures file (docs/fixtures.csv): typos, and games played on a date it doesn't list
    try:
        import fixtures
        fx_games, fx_problems = fixtures.load()
        report["warnings"] += fx_problems
        off = fixtures.check_against_results(data["matches"])
        if off:
            report["warnings"].append("Played on a date not in docs/fixtures.csv (rearranged? change the date in the file): " + ", ".join(off[-10:]))
    except Exception as e:
        report["warnings"].append(f"Couldn't read docs/fixtures.csv: {e}")
    write_report(report, names)
    if mode == "dry_run" and report["compared_diff"]:
        print("Dry run finished WITH differences - see the report before switching to publish.")


def write_report(report, names):
    L = [f"# Dashboard update - {report['mode'].replace('_', ' ')}", ""]
    fz = report.get("fantasy")
    if fz:
        if fz.get("status") in ("sent", "dry run"):
            L += [f"Fantasy ({fz['status']}): season {fz['season']}, {fz['players']} players ({fz.get('price_changes', 0)} with a price change since the start), {fz['point_rows']} player scores across {fz['gameweeks_scored']} gameweeks"
                  + (f"; now unavailable: {', '.join(fz['made_unavailable'])}" if fz.get("made_unavailable") else "")
                  + (f"; matches outside any gameweek: {', '.join(fz['skipped_dates'])}" if fz.get("skipped_dates") else ""), ""]
            if fz.get("new_players"):
                L += ["New fantasy players added automatically (check positions.csv): " + ", ".join(fz["new_players"]), ""]
        else:
            L += [f"Fantasy: {fz.get('note', fz.get('status'))}", ""]
    if report["mode"] == "dry_run":
        L += [f"- Matches identical to the site: **{len(report['compared_ok'])}**",
              f"- Matches with differences: **{len(report['compared_diff'])}**",
              f"- New matches not on the site yet: **{len(report['added'])}**", ""]
        for label, diffs in report["compared_diff"].items():
            L += [f"### {label}"] + [f"- {d}" for d in diffs[:40]] + [""]
        if report["added"]:
            by = Counter((x[:4], x.split(" ")[1]) for x in report["added"])
            L += ["## New matches (not on the site yet)", "",
                  "By season: " + ", ".join(f"{y} {t} XI {n}" for (y, t), n in sorted(by.items())), ""] + \
                 [f"- {x.replace(' (new - not on the site yet)', '')}" for x in report["added"][:300]] + [""]
    else:
        L += [f"- Added: **{len(report['added'])}**  ", f"- Amended: **{len(report['amended'])}**  ",
              f"- Unchanged since last run: {len(report['unchanged'])}",
              f"- Fetched but identical to the site: {len(report.get('identical', []))}", ""]
        L += [f"- added {x}" for x in report["added"]] + [f"- amended {x}" for x in report["amended"]] + [""]
    if report.get("awards"):
        L += ["## Awards (Match Value)", ""] + [f"- {a}" for a in report["awards"]] + [""]
    for t, lt in report["league"].items():
        L.append(f"League table ({t} XI, {lt['division_name']}): {len(lt['rows'])} teams" + (f" - missing columns {lt['columns_missing']}" if lt["columns_missing"] else ""))
    if names.new_players:
        L += ["", "## Player names not seen before", "If any of these is an existing player under a different spelling, add it to `pipeline/aliases.json`:", ""] + [f"- {p}" for p in sorted(names.new_players)]
    if names.new_opponents:
        L += ["", "## Opponent names not matched", "Add these to the `opponents` section of `pipeline/aliases.json` if they're existing opponents:", ""] + [f"- {o}" for o in sorted(names.new_opponents)]
    for title, key in (("Errors (blocking)", "errors"), ("Warnings", "warnings"), ("Skipped", "skipped"),
                       ("Not included (cup games, T20s, friendlies: see match_filter in config.json)", "filtered"),
                       ("Removed from the site (cup game, T20 or friendly)", "removed"),
                       ("Kept as on the site (pipeline/manual/keep_site.csv)", "kept"),
                       ("Past league tables", "league_history"),
                       ("Would be removed from the site on publish (cup game, T20 or friendly)", "would_remove")):
        if report[key]:
            L += ["", f"## {title}", ""] + [f"- {x}" for x in report[key][:200]]
    text = "\n".join(L) + "\n"
    with open(os.path.join(HERE, "last_report.md"), "w", encoding="utf-8") as f:
        f.write(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(text)
    print(text)


if __name__ == "__main__":
    main()
