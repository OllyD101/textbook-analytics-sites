"""England and Wales bank holidays, worked out from the fixed rules, so nothing needs updating each year.

Used to:
  - keep a bank holiday weekend's fantasy gameweek open until Tuesday (Monday games count in it),
  - run the automation every 30 minutes on bank holiday Monday afternoons (see update-data.yml).

One-off extra holidays (a coronation, a jubilee) go in pipeline/config.json under "extra_bank_holidays",
e.g. ["2023-05-08"]. A usual holiday that the government moves goes in "not_bank_holidays".

    python pipeline/bank_holidays.py --today-is-holiday   # exit code 0 if today (UK) is a bank holiday
    python pipeline/bank_holidays.py 2027                 # list a year's bank holidays
"""
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))


def easter_sunday(year):
    """Gregorian Easter (the 'anonymous' algorithm)."""
    a = year % 19; b, c = divmod(year, 100); d, e = divmod(b, 4); f = (b + 8) // 25; g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30; i, k = divmod(c, 4); l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451; month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _first_monday(year, month):
    d = date(year, month, 1)
    return d + timedelta(days=(7 - d.weekday()) % 7)


def _last_monday(year, month):
    d = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return d - timedelta(days=d.weekday())


def _weekday_or_next(d, taken=()):
    while d.weekday() >= 5 or d in taken:
        d += timedelta(days=1)
    return d


def _settings():
    try:
        with open(os.path.join(HERE, 'config.json'), encoding='utf-8') as f:
            c = json.load(f)
    except (OSError, ValueError):
        c = {}
    as_dates = lambda xs: {date.fromisoformat(x) for x in xs or []}
    return as_dates(c.get('extra_bank_holidays')), as_dates(c.get('not_bank_holidays'))


def bank_holidays(year, extra=None, removed=None):
    if extra is None or removed is None:
        e, r = _settings(); extra = e if extra is None else extra; removed = r if removed is None else removed
    easter = easter_sunday(year)
    new_year = _weekday_or_next(date(year, 1, 1))
    christmas = _weekday_or_next(date(year, 12, 25))
    boxing = _weekday_or_next(date(year, 12, 26), {christmas})
    days = {new_year, easter - timedelta(days=2), easter + timedelta(days=1), _first_monday(year, 5),
            _last_monday(year, 5), _last_monday(year, 8), christmas, boxing}
    days |= {d for d in extra if d.year == year}
    return sorted(days - set(removed))


def is_bank_holiday(d, extra=None, removed=None):
    return d in bank_holidays(d.year, extra, removed)


def uk_today():
    """Today's date in the UK (GitHub runs in UTC; this is close enough for an afternoon check)."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    bst = _last_sunday(now.year, 3) <= now.date() < _last_sunday(now.year, 10)
    return (now + timedelta(hours=1 if bst else 0)).date()


def _last_sunday(year, month):
    d = date(year, month + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() + 1) % 7)


if __name__ == '__main__':
    if '--today-is-holiday' in sys.argv:
        t = uk_today(); yes = is_bank_holiday(t)
        print(f"{t}: {'bank holiday' if yes else 'not a bank holiday'}")
        sys.exit(0 if yes else 1)
    for y in [int(a) for a in sys.argv[1:]] or [date.today().year]:
        print(y, ', '.join(d.strftime('%a %d %b') for d in bank_holidays(y)))
