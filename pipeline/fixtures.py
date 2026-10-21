"""The club's fixtures, from docs/fixtures.csv (one row per game).

When a season's fixtures are in the file, they decide:
  - when each fantasy gameweek's weekend is over (the day after its last game, and never before the Monday),
    so prices and "still to come" notes cover Sunday games, bank holiday Mondays, Good Friday, anything,
  - which days the automation checks Play-Cricket every 30 minutes (any day with a game).
For a season with no fixtures in the file, the usual rule applies: Monday, or Tuesday after a bank holiday.

The file (it opens in Excel; save it as CSV):
    Date,XI,Opponent,Home/Away
    03/04/2027,1st,Walkington CC,H
    04/04/2027,3rd,Cottingham CC,A
Date as DD/MM/YYYY (or YYYY-MM-DD); XI as 1st, 2nd, 3rd (or "1st XI"). Opponent and Home/Away are optional.
Blank rows and rows starting with # are ignored. Rearranged games: change the date in the file.

    python pipeline/fixtures.py                     # check the file and list each weekend
    python pipeline/fixtures.py --today-has-games   # exit code 0 if there's a game today (UK)
"""
import csv
import os
import sys
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(os.path.dirname(HERE), 'docs', 'fixtures.csv')
XIS = {'1st', '2nd', '3rd'}
_cache = {}


def _date(s):
    s = (s or '').strip()
    for fmt in ('%d/%m/%Y', '%Y-%m-%d', '%d/%m/%y', '%d-%m-%Y'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def load(path=None):
    """(games, problems). games: [{'date': date, 'xi': '1st', 'opponent': str, 'venue': str}]."""
    path = path or PATH
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return [], []
    if _cache.get(path, (None,))[0] == mtime:
        return _cache[path][1]
    games, problems = [], []
    with open(path, encoding='utf-8-sig', newline='') as f:
        rows = [r for r in csv.reader(f)]
    head = [h.strip().lower() for h in (rows[0] if rows else [])]
    col = lambda *names: next((head.index(n) for n in names if n in head), None)
    c_date, c_xi, c_opp, c_ven = col('date'), col('xi', 'team'), col('opponent', 'opposition', 'v'), col('home/away', 'venue', 'h/a')
    if c_date is None or c_xi is None:
        problems.append('docs/fixtures.csv needs a header row with at least Date and XI columns')
        return [], problems
    for n, r in enumerate(rows[1:], start=2):
        if not any(x.strip() for x in r) or r[0].strip().startswith('#'):
            continue
        get = lambda c: r[c].strip() if c is not None and c < len(r) else ''
        d, xi = _date(get(c_date)), get(c_xi).replace(' XI', '').replace('XI', '').strip().lower()
        if d is None:
            problems.append(f'fixtures.csv row {n}: date "{get(c_date)}" not understood (use DD/MM/YYYY)')
            continue
        if xi not in XIS:
            problems.append(f'fixtures.csv row {n}: XI "{get(c_xi)}" should be 1st, 2nd or 3rd')
            continue
        games.append({'date': d, 'xi': xi, 'opponent': get(c_opp), 'venue': get(c_ven).upper()[:1]})
    games.sort(key=lambda g: (g['date'], g['xi']))
    _cache[path] = (mtime, (games, problems))
    return games, problems


def seasons(path=None):
    return {g['date'].year for g in load(path)[0]}


def game_days(path=None):
    return {g['date'] for g in load(path)[0]}


def saturday_of(d):
    return d - timedelta(days=(d.weekday() - 5) % 7)


def weekend_end(gw, path=None):
    """From the fixtures: the day after the gameweek's last game, never before the Monday.
    None when that season has no fixtures in the file."""
    sat = date.fromisoformat(gw) if isinstance(gw, str) else gw
    if sat.year not in seasons(path):
        return None
    days = [d for d in game_days(path) if saturday_of(d) == sat]
    return max([sat + timedelta(days=2)] + [d + timedelta(days=1) for d in days])


def check_against_results(matches, path=None):
    """Played matches (dashboard rows: Date, Team) in a season with fixtures whose date isn't in the file."""
    games = {(g['date'].isoformat(), g['xi']) for g in load(path)[0]}
    yrs = seasons(path)
    return sorted(f"{m['Date']} {m['Team']} XI" for m in matches
                  if int(m['Date'][:4]) in yrs and (m['Date'], m['Team']) not in games)


if __name__ == '__main__':
    sys.path.insert(0, HERE)
    import bank_holidays
    if '--today-has-games' in sys.argv:
        t = bank_holidays.uk_today()
        if t.year in seasons():
            yes = t in game_days(); why = 'a game in the fixtures' if yes else 'no game in the fixtures'
        else:
            yes = bank_holidays.is_bank_holiday(t); why = ('a bank holiday' if yes else 'not a bank holiday') + f' (no {t.year} fixtures yet)'
        print(f'{t}: {why}')
        sys.exit(0 if yes else 1)
    games, problems = load()
    for p in problems:
        print('PROBLEM:', p)
    if not games:
        print('No fixtures in docs/fixtures.csv yet.'); sys.exit(1 if problems else 0)
    by = {}
    for g in games:
        by.setdefault(saturday_of(g['date']), []).append(g)
    for sat, gs in sorted(by.items()):
        days = ', '.join(f"{g['date'].strftime('%a')} {g['xi']}" for g in gs)
        print(f"Gameweek {sat.strftime('%d %b %Y')}: {days}; prices from {weekend_end(sat).strftime('%a %d %b')}")
    print(f'{len(games)} games, {len(problems)} problem(s)')
