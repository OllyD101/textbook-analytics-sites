# Automatic updates from Play-Cricket

Once switched on, the dashboard updates itself. GitHub checks Play-Cricket every 30 minutes on Saturday and Sunday afternoons and evenings, and once each weekday evening. When a new or corrected scorecard appears, it converts it, checks it, and publishes it. You don't need to do anything after a match, apart from Man of the Match (see below).

## How it works

1. `pipeline/run.py` asks Play-Cricket which Hessle matches are new or changed (1st and 2nd XI only).
2. It downloads each scorecard and converts it into the dashboard's format, calculated exactly as the existing data is.
3. It runs the same safety checks as the Data Quality page. **If anything is impossible (for example 7 balls in an over), it publishes nothing** and the job fails, which makes GitHub email you.
4. It updates `docs/data.json` and `docs/league.json`, and the site refreshes.

Every update is a commit, so the full history is kept and any change can be undone.

## One-time setup

Everything happens in the GitHub repository, under **Settings → Secrets and variables → Actions**.

1. **Add the files.** Copy the `pipeline/`, `tests/` and `.github/` folders into the repository root, next to `docs/`.
2. **Add the API key** on the **Secrets** tab: *New repository secret*, name `PLAY_CRICKET_API_TOKEN`, value = the key from Play-Cricket. Never put the key in a file or share it.
3. **Add the club's site ID** on the **Variables** tab: *New repository variable*, name `PLAY_CRICKET_SITE_ID`, value = the number.
4. **Do a dry run.** Go to the **Actions** tab → *Update dashboard from Play-Cricket* → *Run workflow*, choose `dry_run`, season `2026`. Open the finished run to read the report. It compares every 2026 scorecard from Play-Cricket with what's already on the site.
5. **Fix anything the report lists.** Usually this is a name spelled differently on Play-Cricket. Add it to `pipeline/aliases.json` (Play-Cricket name on the left, dashboard name on the right) and run the dry run again.
6. **Switch it on.** When the report says every match is identical, add a variable `PIPELINE_MODE` with value `publish`. From then on, the scheduled runs update the site.

## After each match

- **Nothing**, for the scorecard.
- **Man of the Match** isn't on Play-Cricket scorecards. Add a line to `pipeline/manual/motm.csv`. You can do this on the GitHub website using the pencil icon:
  `2027-05-08,1st XI,Jordan Callis`
- **Player of the Month**: add a line to `pipeline/manual/potm.csv`, e.g. `May 2027,Jordan Callis`.

The report lists any match still missing a Man of the Match, so nothing gets forgotten.

## Man of the Match and Player of the Month

- **From 2027** the automation awards both by **Match Value** (see `pipeline/awards.py`; settings under `awards` in `pipeline/config.json`, wicket value 15). Man of the Match is worked out once each scorecard is in; Player of the Month once the month is over (plus 3 days for late scorecards). Every award is then **frozen**: a later scorecard correction never changes a winner.
- **Before 2027** awards are never touched by the automation.
- **Captain's override:** add a line to `pipeline/manual/motm.csv` (`2027-05-08,1st XI,Jordan Callis`) or `pipeline/manual/potm.csv` (`May 2027,Jordan Callis`). It replaces the automatic award.
- **Adding older seasons (2017 and before) from the spreadsheets:** after importing the matches, run
  `python pipeline/import_awards.py path/to/Hessle2025.xlsx` to see what would be added, then add `--apply`.
  It takes awards from the workbook's Impact formula **only for matches and months that have none yet**. The Impact formula uses all-time averages, so new old seasons can shift its answer for later matches; the report lists any such disagreements but never changes an existing award.

## When a run fails

GitHub emails the repository owner. Open the run in the **Actions** tab; the report at the bottom explains what stopped it. Common fixes:

| Report says | Do this |
|---|---|
| Player / opponent name not seen before | Add it to `pipeline/aliases.json` if it's an existing player or club |
| Unrecognised dismissal code 'xyz' | Add it to `HOW_OUT` at the top of `pipeline/transform.py` |
| Batters' runs + extras don't equal the total | The scorecard on Play-Cricket needs correcting by the scorer. The next run picks up the fix |
| Invalid overs / blocking problem | Correct the scorecard on Play-Cricket; nothing was published |

## Good to know

- **Not live.** Play-Cricket's API isn't real-time, so the site updates once the scorer has uploaded the scorecard.
- **Off-season.** GitHub pauses scheduled jobs on public repositories after 60 days without activity. At the start of each season, open the **Actions** tab and re-enable the workflow if prompted.
- **First publish.** Two 2026 matches (6 June, both XIs) have batting marked as 1st innings when Hessle fielded first. Play-Cricket's batting order will correct them automatically.
- **League page wording** currently says "Final standings". It will need a small change before next season to show a table in progress.

## Testing without Play-Cricket

```
python tests/make_fixtures.py docs/data.json tests/fixtures   # builds Play-Cricket-format scorecards from 2026 data
python pipeline/run.py --mode dry_run --fixtures tests/fixtures
python tests/test_pipeline.py                                   # 6 end-to-end tests on a throwaway copy
```
