"""Man of the Match and Player of the Month by Match Value (from the date in config).

Match Value measures each contribution in runs, judged only against that match:
  Batting  = runs + tempo_weight x (runs - balls x match scoring rate off the bat / 6)
             (innings with no balls recorded, or estimated balls, score on runs only)
  Bowling  = wickets x W + (match run rate x overs - runs conceded)
  Catch / stumping = catch_share x W, taken from the bowler's share of that wicket
  Run-out  = runout_share x W         Caught-and-bowled stays fully with the bowler
  Tie-break: higher Match Value, then more wickets, then more runs.

Rules (agreed with the club):
  * Awards are calculated once and then frozen. Nothing here ever recalculates or
    overwrites an existing award, so a corrected scorecard can't change a winner.
  * Matches before `match_value_from` are never touched (earlier awards came from the
    Impact formula in Hessle2025.xlsx and stay exactly as they are).
  * A captain can override either award via pipeline/manual/*.csv (applied after this).
"""
from collections import defaultdict
from datetime import date, datetime, timedelta

num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)


def _b6(o):
    o = o or 0
    w = int(o)
    return w * 6 + int(round((o - w) * 10))


def _key(r):
    return (r["Date"], r["Team"], r["Opponent"])


def match_value(data, m, cfg):
    """Per-player Match Value for one match: {player: {'bat','bowl','field','total','wkts','runs'}}."""
    W, cs, rs, tw = cfg["wicket_value"], cfg["catch_share"], cfg["runout_share"], cfg["tempo_weight"]
    k = _key(m)
    bat = [r for r in data["batting"] if _key(r) == k]
    bowl = [r for r in data["bowling"] if _key(r) == k]
    fld = [r for r in data["fielding"] if _key(r) == k]
    kp = [r for r in data["keeping"] if _key(r) == k]
    balls = _b6(m.get("Overs Batted")) + _b6(m.get("Overs Bowled"))
    overs = balls / 6 if balls else 0
    off_bat = sum(r["Runs"] for r in bat if num(r.get("Runs"))) + max(
        0, sum(r.get("Runs") or 0 for r in bowl) - sum((r.get("Wides") or 0) + (r.get("No Balls") or 0) for r in bowl))
    bat_rr = off_bat / overs if overs else 0
    rr = ((m.get("Runs Scored") or 0) + (m.get("Runs Conceded") or 0)) / overs if overs else 0
    v = defaultdict(lambda: {"bat": 0.0, "bowl": 0.0, "field": 0.0, "wkts": 0, "runs": 0})
    for r in bat:
        if r.get("Dismissal") == "DNB" or not num(r.get("Runs")):
            continue
        b = r["Balls"] if num(r.get("Balls")) and r["Balls"] > 0 and not r.get("BallsEstimated") else None
        v[r["Batter"]]["bat"] += r["Runs"] + (tw * (r["Runs"] - bat_rr * b / 6) if b else 0)
        v[r["Batter"]]["runs"] += r["Runs"]
    for r in bowl:
        ov = _b6(r.get("Overs")) / 6
        w = r.get("Wickets") or 0
        v[r["Bowler"]]["bowl"] += w * W + (rr * ov - (r.get("Runs") or 0))
        v[r["Bowler"]]["wkts"] += w
    for rows, who in ((fld, "Fielder"), (kp, "Keeper")):
        for r in rows:
            p, bw, dis = r.get(who), r.get("Bowler"), r.get("Dismissal")
            if not p:
                continue
            if dis == "Run Out":
                v[p]["field"] += rs * W
            elif dis in ("Caught", "Stumping", "Stumped") and p != bw:
                v[p]["field"] += cs * W
                if bw:
                    v[bw]["bowl"] -= cs * W
    for p, x in v.items():
        x["total"] = x["bat"] + x["bowl"] + x["field"]
    return dict(v)


def ranked(values):
    return sorted(values, key=lambda p: (round(values[p]["total"], 6), values[p]["wkts"], values[p]["runs"]), reverse=True)


def award_match_of_the_match(data, cfg, report):
    """Add a frozen Match Value MoTM for every eligible match that doesn't have one yet."""
    start = cfg["match_value_from"]
    have = {(r["Date"], r["Team"]) for r in data.get("motm", [])}
    added = 0
    for m in sorted(data["matches"], key=lambda m: (m["Date"], m["Team"])):
        if m["Date"] < start or (m["Date"], m["Team"]) in have:
            continue  # earlier seasons keep their existing award; existing awards are frozen
        vals = match_value(data, m, cfg)
        if not vals:
            report["warnings"].append(f"{m['Date']} {m['Team']} XI v {m['Opponent']}: no scorecard rows, so no Man of the Match yet")
            continue
        order = ranked(vals)
        win = order[0]
        data.setdefault("motm", []).append({"Date": m["Date"], "Opponent": m["Opponent"], "Team": m["Team"], "MOTM": win,
                                            "Source": "Match Value", "Match Value": round(vals[win]["total"], 1)})
        added += 1
        runner = f"; runner-up {order[1]} {vals[order[1]]['total']:.0f}" if len(order) > 1 else ""
        report.setdefault("awards", []).append(f"Man of the Match {m['Date']} {m['Team']} XI v {m['Opponent']}: {win} ({vals[win]['total']:.0f}{runner})")
    return added


def _month_label(d):
    return datetime.strptime(d, "%Y-%m-%d").strftime("%b %Y")


def award_player_of_the_month(data, cfg, report, today=None):
    """Once a month is over (plus a few days' grace for late scorecards), award it once and freeze it."""
    today = today or date.today()
    start = cfg["match_value_from"]
    grace = timedelta(days=cfg.get("potm_grace_days", 3))
    awarded = {m for r in data.get("potm", []) for m in r["months"]}
    totals = defaultdict(lambda: defaultdict(float))
    games = defaultdict(lambda: defaultdict(int))
    for m in data["matches"]:
        if m["Date"] < start:
            continue
        lab = _month_label(m["Date"])
        for p, x in match_value(data, m, cfg).items():
            totals[lab][p] += x["total"]
            games[lab][p] += 1
    added = 0
    for lab in sorted(totals, key=lambda s: datetime.strptime(s, "%b %Y")):
        first = datetime.strptime(lab, "%b %Y").date()
        nxt = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
        if lab in awarded or today < nxt + grace:
            continue
        win = max(totals[lab], key=lambda p: (round(totals[lab][p], 6), games[lab][p]))
        entry = next((r for r in data.setdefault("potm", []) if r["player"] == win), None)
        if entry is None:
            entry = {"player": win, "wins": 0, "months": []}
            data["potm"].append(entry)
        entry["months"].append(lab)
        entry["wins"] = len(entry["months"])
        added += 1
        report.setdefault("awards", []).append(f"Player of the Month {lab}: {win} ({totals[lab][win]:.0f} over {games[lab][win]} matches)")
    data["potm"] = sorted(data.get("potm", []), key=lambda r: (-r["wins"], r["player"]))
    return added
