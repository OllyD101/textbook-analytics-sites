"""Bring in Man of the Match / Player of the Month from Hessle2025.xlsx (the Impact formula)
for matches added from the spreadsheets - e.g. seasons from 2017 and before.

    python pipeline/import_awards.py path/to/Hessle2025.xlsx            # report only
    python pipeline/import_awards.py path/to/Hessle2025.xlsx --apply    # add missing awards

Rules (agreed with the club):
  * Only matches / months that have NO award yet get one. Existing awards are never changed.
  * The Impact formula compares players with all-time averages, so adding older seasons can
    shift its answer for later matches. Any existing award the workbook now disagrees with
    is listed in the report for information - it is not changed.
  * Matches from the Match Value start date onwards are skipped (the automation awards those).
"""
import argparse
import json
import os
import sys
from datetime import datetime

import openpyxl

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from run import expand, load_json, shrink, write_json  # noqa: E402


def read_pivots(path):
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb["Team Pivots"]
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    motm, potm = {}, {}
    # find the MoTM pivot ("PlayerOfTheMatch" header) and the PotM pivot ("POTM Months" header)
    for ri, row in enumerate(rows):
        for ci, v in enumerate(row):
            if v == "PlayerOfTheMatch":
                for r in rows[ri + 1:]:
                    key, who = (r[ci - 1] if ci else None), r[ci]
                    if key and key != "Grand Total" and who:
                        p = str(key).split("_")
                        motm[(f"{p[0][:4]}-{p[0][4:6]}-{p[0][6:8]}", p[-1])] = str(who).strip()
            if v == "POTM Months":
                for r in rows[ri + 1:]:
                    player, months = r[ci - 2], r[ci]
                    if not player or player == "Grand Total":
                        if potm:
                            break
                        continue
                    for m in str(months or "").split(","):
                        m = m.strip()
                        if m:
                            try:
                                potm[datetime.strptime(m, "%B %Y").strftime("%b %Y")] = str(player).strip()
                            except ValueError:
                                potm[m] = str(player).strip()
    if not motm:
        sys.exit("Couldn't find the PlayerOfTheMatch pivot on the Team Pivots sheet")
    return motm, potm


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workbook")
    ap.add_argument("--apply", action="store_true", help="write the missing awards (default: report only)")
    args = ap.parse_args()
    cfg = load_json(os.path.join(HERE, "config.json"))
    start = cfg["awards"]["match_value_from"]
    fwd = load_json(os.path.join(HERE, "key_map.json"))
    data_path = os.path.join(ROOT, cfg["data_path"])
    data = expand(load_json(data_path), {v: k for k, v in fwd.items()})
    wb_motm, wb_potm = read_pivots(args.workbook)

    have = {(r["Date"], r["Team"]): r for r in data["motm"]}
    added, disagree, missing_in_wb = [], [], []
    for m in sorted(data["matches"], key=lambda m: (m["Date"], m["Team"])):
        k = (m["Date"], m["Team"])
        if m["Date"] >= start:
            continue
        w = wb_motm.get(k)
        if k in have:
            if w and w != have[k]["MOTM"]:
                disagree.append(f"{k[0]} {k[1]} XI v {m['Opponent']}: kept {have[k]['MOTM']} (workbook now says {w})")
        elif w:
            added.append(f"{k[0]} {k[1]} XI v {m['Opponent']}: {w}")
            if args.apply:
                data["motm"].append({"Date": k[0], "Opponent": m["Opponent"], "Team": k[1], "MOTM": w, "Source": "Impact"})
        else:
            missing_in_wb.append(f"{k[0]} {k[1]} XI v {m['Opponent']}")

    awarded = {mo: r["player"] for r in data["potm"] for mo in r["months"]}
    match_months = {datetime.strptime(m["Date"], "%Y-%m-%d").strftime("%b %Y") for m in data["matches"] if m["Date"] < start}
    p_added, p_disagree = [], []
    for mo, who in sorted(wb_potm.items(), key=lambda kv: datetime.strptime(kv[0], "%b %Y")):
        if mo not in match_months:
            continue
        if mo in awarded:
            if awarded[mo] != who:
                p_disagree.append(f"{mo}: kept {awarded[mo]} (workbook now says {who})")
        else:
            p_added.append(f"{mo}: {who}")
            if args.apply:
                e = next((r for r in data["potm"] if r["player"] == who), None)
                if e is None:
                    e = {"player": who, "wins": 0, "months": []}
                    data["potm"].append(e)
                e["months"].append(mo)
                e["wins"] = len(e["months"])
    data["potm"] = sorted(data["potm"], key=lambda r: (-r["wins"], r["player"]))

    L = [f"# Award import from {os.path.basename(args.workbook)} ({'applied' if args.apply else 'report only'})", ""]
    L += [f"## Man of the Match added: {len(added)}"] + [f"- {x}" for x in added] + [""]
    L += [f"## Player of the Month added: {len(p_added)}"] + [f"- {x}" for x in p_added] + [""]
    if disagree or p_disagree:
        L += ["## Existing awards the workbook now disagrees with (NOT changed)",
              "The Impact formula uses all-time averages, so new older seasons can shift its answer for later matches.", ""]
        L += [f"- {x}" for x in disagree + p_disagree] + [""]
    if missing_in_wb:
        L += ["## Matches with no award in the workbook either", ""] + [f"- {x}" for x in missing_in_wb] + [""]
    print("\n".join(L))
    if args.apply and (added or p_added):
        write_json(data_path, shrink(data, fwd))
        print(f"Wrote {cfg['data_path']}")


if __name__ == "__main__":
    main()
