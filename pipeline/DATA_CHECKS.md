# Data checks

Run `python pipeline/check_data.py` at any time. It changes nothing; it lists anything in
`docs/data.json` that doesn't add up (scores vs scorecards, wickets, overs, innings, names, awards).

## Status (23 October 2026)

**No open issues.** Everything the audit finds has been looked at and is recorded in
`pipeline/manual/data_checks_accepted.json`, with the reason. The audit sets those aside, so only
new problems show. Delete an entry there to see it again.

- Wins with a lower score than the opposition: rain-affected games.
- Wickets one or two short, missing bowling, extra boundaries, "Unsure" players: the original
  scorecards are incomplete and no further details are available.
- Small overs differences and '-' for unrecorded fours and sixes: fine as they are.

## October 2026 corrections

From the corrected `Hessle2025 Data.xlsx` (26 matches changed), plus:
- Names merged: Gareth Nicholls -> Gaz Nicholls, Peter Walker -> Pete Walker, Jack rudston,
  O Davison, Hafiz Ahmed, Mohammad A, Daniel Mcintyre (all in `pipeline/aliases.json`).
- Fielding and keeping innings fixed on five matches (still to change in the workbook:
  2021-06-12 1st, 2021-07-24 2nd, 2023-07-01 2nd, 2023-07-29 2nd, 2023-08-12 1st).
- Awards: Man of the Match 23 Aug 2025 and 30 Aug 2025 (2nd XI) -> Navin Sharma;
  Player of the Month Aug 2025 -> Navin Sharma. 27 May 2023 (Ben Rogers) and 29 Jul 2023
  (Joe Broadley) kept, although the workbook's Impact formula now picks someone else.

## After the first Play-Cricket dry run (23 October 2026)

From the updated `Hessle2025 Data.xlsx` (36 matches, mostly 2026): batting orders, dismissal types
(caught and bowled), catches, a toss and some bowling figures, plus fixes for Patrington 2023
(3 wickets), the boundary counts for Zaheer Butt, Jack Walker and Steven Cooper, and the leftover
Craig Asquith bowling row on 23 Aug 2025. Awards unchanged. Strike rates, % of team runs, boundary %,
economy and bowling strike rates were recalculated for the 13 matches whose workbook formulas
hadn't followed the edits. Steve Cooper added to `pipeline/aliases.json`.

## After the 2018-2025 dry run (23 October 2026)

Two corrections from `Hessle2025 Data.xlsx`: 23 Jun 2018 1st XI wickets taken 10 -> 9; Oliver Davison
4 Jun 2022 (2nd XI) 75 balls, not 119. The workbook's fours and sixes for 2018-2024 had been cleared by
accident; the site kept them, and `Hessle2025_boundaries_restore.xlsx` puts them back in the workbook.
