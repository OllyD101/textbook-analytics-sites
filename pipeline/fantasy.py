"""Hessle CC Fantasy Cricket: scoring, positions, prices and gameweeks.

Used by the Play-Cricket automation (to score each Saturday and send points to Supabase)
and to build the files the fantasy page reads.

Rules agreed with the club
--------------------------
Squad: 11 players = 4 batters, 4 bowlers, 2 all-rounders, 1 wicketkeeper, budget £100m.
Captain scores double; the vice-captain takes over if the captain doesn't play.
Gameweeks: each Saturday (a match belongs to the most recent Saturday on or before it);
teams lock at 12:00 UK time on that Saturday.
Transfers: 2 free a week, -4 points for each extra; one wildcard per season.
"""
import csv
import json
import os
from collections import defaultdict
from datetime import date, datetime, timedelta

# ---------------------------------------------------------------- scoring
POINTS = {
    'run': 1, 'six': 2, 'fifty': 10, 'hundred': 20, 'duck': -8,
    'wicket': 12, 'maiden': 4, 'four_wkts': 8, 'five_wkts': 16,
    'catch': 5, 'stumping': 8, 'run_out': 10, 'three_catches': 4,
    'motm': 10, 'win': 3,
}
SQUAD = {'BAT': 4, 'BOWL': 4, 'AR': 2, 'WK': 1}
BUDGET = 100.0
PRICE_MIN, PRICE_MAX = 4.0, 12.0
FREE_TRANSFERS, TRANSFER_HIT = 2, 4

num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)
NOT_PLAYERS = {'Unsure'}


def _key(r):
    return (r['Date'], r['Team'])


def gameweek_of(d):
    """The Saturday on or before the match date (ISO string)."""
    dt = datetime.strptime(d, '%Y-%m-%d').date()
    return (dt - timedelta(days=(dt.weekday() - 5) % 7)).isoformat()


def match_points(data, d, team):
    """{player: {'total': int, 'parts': {rule: points}}} for one match (date + XI)."""
    k = (d, team)
    out = defaultdict(lambda: {'total': 0, 'parts': defaultdict(int)})

    def add(p, rule, pts):
        if not p or p in NOT_PLAYERS or not pts:
            return
        out[p]['parts'][rule] += pts
        out[p]['total'] += pts

    played = set()
    for r in data['batting']:
        if _key(r) != k or not r.get('Batter') or r['Batter'] in NOT_PLAYERS:
            continue
        played.add(r['Batter'])
        if r.get('Dismissal') == 'DNB' or not num(r.get('Runs')):
            continue
        runs = r['Runs']
        add(r['Batter'], 'runs', runs * POINTS['run'])
        if num(r.get('6s')):
            add(r['Batter'], 'sixes', r['6s'] * POINTS['six'])
        if runs >= 100:
            add(r['Batter'], 'hundred', POINTS['hundred'])
        elif runs >= 50:
            add(r['Batter'], 'fifty', POINTS['fifty'])
        dis = (r.get('Dismissal') or '').lower()
        if runs == 0 and 'not out' not in dis and 'retired' not in dis:
            add(r['Batter'], 'duck', POINTS['duck'])
    for r in data['bowling']:
        if _key(r) != k or not r.get('Bowler'):
            continue
        played.add(r['Bowler'])
        w = r.get('Wickets') or 0
        add(r['Bowler'], 'wickets', w * POINTS['wicket'])
        add(r['Bowler'], 'maidens', (r.get('Maidens') or 0) * POINTS['maiden'])
        if w >= 5:
            add(r['Bowler'], 'five_wkts', POINTS['five_wkts'])
        elif w >= 4:
            add(r['Bowler'], 'four_wkts', POINTS['four_wkts'])
    catches = defaultdict(int)
    for ds, who in (('fielding', 'Fielder'), ('keeping', 'Keeper')):
        for r in data[ds]:
            if _key(r) != k or not r.get(who):
                continue
            p = r[who]; played.add(p); dis = (r.get('Dismissal') or '').lower()
            if 'stump' in dis:
                add(p, 'stumpings', POINTS['stumping'])
            elif dis == 'run out':
                add(p, 'run_outs', POINTS['run_out'])
            else:
                add(p, 'catches', POINTS['catch']); catches[p] += 1
    for p, n in catches.items():
        if n >= 3:
            add(p, 'three_catches', POINTS['three_catches'])
    m = next((x for x in data['matches'] if (x['Date'], x['Team']) == k), None)
    if m and m.get('Result') == 'W':
        for p in played:
            add(p, 'win', POINTS['win'])
    motm = next((x['MOTM'] for x in data.get('motm', []) if (x['Date'], x['Team']) == k), None)
    if motm in played:
        add(motm, 'motm', POINTS['motm'])
    for p in played:          # a player who appeared but scored nothing still "played" (0 points)
        out[p]
    return {p: {'total': v['total'], 'parts': dict(v['parts'])} for p, v in out.items()}


def season_points(data, season):
    """{gameweek: {player: {'total', 'parts', 'matches': [..]}}} — both XIs' matches in each gameweek add up."""
    gws = defaultdict(lambda: defaultdict(lambda: {'total': 0, 'parts': defaultdict(int), 'matches': []}))
    for m in sorted(data['matches'], key=lambda x: (x['Date'], x['Team'])):
        if m['Date'][:4] != str(season):
            continue
        gw = gameweek_of(m['Date'])
        for p, v in match_points(data, m['Date'], m['Team']).items():
            e = gws[gw][p]; e['total'] += v['total']; e['matches'].append(m['Date'] + ' ' + m['Team'] + ' XI v ' + m['Opponent'])
            for rule, pts in v['parts'].items():
                e['parts'][rule] += pts
    return {gw: {p: {'total': e['total'], 'parts': dict(e['parts']), 'matches': e['matches']} for p, e in ps.items()} for gw, ps in sorted(gws.items())}


# ---------------------------------------------------------------- positions
def _profile_role(data, p, upto):
    """Same Batter / Bowler / All-rounder rules as the player profile badge (career to date)."""
    bat = [r for r in data['batting'] if r.get('Batter') == p and r['Date'][:4] <= str(upto) and r.get('Dismissal') != 'DNB']
    bowl = [r for r in data['bowling'] if r.get('Bowler') == p and r['Date'][:4] <= str(upto)]
    batM = len({_key(r) for r in bat}); bowlM = len({_key(r) for r in bowl})
    wpm = sum(r.get('Wickets') or 0 for r in bowl) / max(bowlM, 1)
    meaningful = bowlM >= 5 and wpm >= 0.7
    pct = bowlM / max(bowlM + batM, 1)
    if meaningful and bowlM > batM and wpm >= 1.0:
        return 'BOWL'
    if meaningful and pct >= 0.30 and batM >= 5:
        return 'AR'
    return 'BAT'


def appearances(data, seasons):
    apps = defaultdict(set)
    for ds, who in (('batting', 'Batter'), ('bowling', 'Bowler'), ('fielding', 'Fielder'), ('keeping', 'Keeper')):
        for r in data[ds]:
            p = r.get(who)
            if p and p not in NOT_PLAYERS and r['Date'][:4] in {str(s) for s in seasons}:
                apps[p].add(_key(r))
    return apps


def auto_positions(data, season):
    """Positions going into `season`, from the seasons before it. WK = kept in a third or more of their recent games."""
    recent = [season - 1, season - 2]
    apps = appearances(data, recent)
    pos = {}
    for p in apps:
        # a keeper if they kept in a third or more of their games (min 3) in EITHER recent season
        is_wk = False
        for s in recent:
            a = appearances(data, [s]).get(p, set())
            k = {_key(r) for r in data['keeping'] if r.get('Keeper') == p and r['Date'][:4] == str(s)}
            if a and len(k) >= 3 and len(k) / len(a) >= 1 / 3:
                is_wk = True
        pos[p] = 'WK' if is_wk else _profile_role(data, p, season - 1)
    return pos


# ---------------------------------------------------------------- prices
def auto_prices(data, season, players):
    """£4.0m–£12.0m from expected points per gameweek going into `season`:
    points per game in the previous season (60%) and the one before (40%), times how often they played."""
    def per_season(s):
        sp = season_points(data, s); n_gw = max(1, len(sp))
        tot = defaultdict(float); games = defaultdict(int)
        for gw in sp.values():
            for p, v in gw.items():
                tot[p] += v['total']; games[p] += len(v['matches'])
        return {p: (tot[p] / games[p], games[p] / n_gw) for p in games}
    a, b = per_season(season - 1), per_season(season - 2)
    exp = {}
    for p in players:
        parts = []
        if p in a: parts.append((0.6, a[p][0] * min(1.0, a[p][1])))
        if p in b: parts.append((0.4, b[p][0] * min(1.0, b[p][1])))
        exp[p] = sum(w * v for w, v in parts) / sum(w for w, _ in parts) if parts else 0.0
    ranked = sorted(players, key=lambda p: exp[p])
    n = max(1, len(ranked) - 1)
    prices = {}
    for i, p in enumerate(ranked):
        # rank-based so prices spread evenly across £4.0m-£12.0m, rounded to £0.5m
        raw = PRICE_MIN + (PRICE_MAX - PRICE_MIN) * (i / n) ** 1.3
        prices[p] = round(raw * 2) / 2
    return prices, exp


# ---------------------------------------------------------------- the editable positions & prices file
def load_overrides(path):
    out = {}
    if os.path.exists(path):
        with open(path, newline='', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                p = (row.get('Player') or '').strip()
                if not p or p.startswith('#'):
                    continue
                out[p] = {'position': (row.get('Position') or '').strip().upper() or None,
                          'price': float(row['Price']) if (row.get('Price') or '').strip() else None,
                          'active': (row.get('Active') or 'yes').strip().lower() not in ('no', 'n', '0', 'false')}
    return out


def build_players(data, season, overrides_path=None):
    """Player list for a season: auto positions & prices, then the club's overrides applied on top."""
    pos = auto_positions(data, season)
    players = sorted(pos)
    prices, exp = auto_prices(data, season, players)
    ov = load_overrides(overrides_path) if overrides_path else {}
    for p, o in ov.items():
        if p not in pos:  # a new signing added by hand
            pos[p] = o['position'] or 'BAT'; prices[p] = o['price'] or 4.5; exp[p] = 0.0
    xi = {}
    apps = appearances(data, [season - 1, season - 2])
    for p, a in apps.items():
        n1 = sum(1 for k in a if k[1] == '1st'); xi[p] = '1st' if n1 / len(a) >= 0.5 else '2nd'
    out = []
    for p in sorted(pos):
        o = ov.get(p, {})
        if o and not o.get('active', True):
            continue
        out.append({'name': p, 'position': (o.get('position') or pos[p]), 'price': o.get('price') or prices[p],
                    'xi': xi.get(p, '1st'), 'expected': round(exp.get(p, 0.0), 1)})
    return out


def write_overrides_template(players, path):
    with open(path, 'w', newline='', encoding='utf-8') as f:
        f.write('# Hessle CC Fantasy: positions and prices. Edit any row; delete nothing.\n')
        f.write('# Position: BAT, BOWL, AR or WK.  Price: in £m (4.0-12.0).  Active: no = not available this season.\n')
        f.write('# Add a new signing as a new row. The automation re-reads this file every time it runs.\n')
        w = csv.writer(f); w.writerow(['Player', 'Position', 'Price', 'Active', 'XI', 'Expected pts per week'])
        for p in sorted(players, key=lambda x: ({'WK': 0, 'BAT': 1, 'AR': 2, 'BOWL': 3}[x['position']], -x['price'], x['name'])):
            w.writerow([p['name'], p['position'], f"{p['price']:.1f}", 'yes', p['xi'], p['expected']])


# ---------------------------------------------------------------- team checks (mirrors the database rules)
def check_team(team, players_by_name):
    """Returns a list of problems (empty = valid). team = {'players': [...11], 'captain', 'vice'}."""
    errs = []
    ps = team.get('players') or []
    if len(ps) != 11 or len(set(ps)) != 11:
        errs.append('Pick 11 different players.')
    unknown = [p for p in ps if p not in players_by_name]
    if unknown:
        errs.append('Unknown players: ' + ', '.join(unknown))
        return errs
    count = defaultdict(int)
    for p in ps:
        count[players_by_name[p]['position']] += 1
    for pos, need in SQUAD.items():
        if count[pos] != need:
            errs.append(f'You need {need} {pos} (you have {count[pos]}).')
    cost = sum(players_by_name[p]['price'] for p in ps)
    if cost > BUDGET + 1e-9:
        errs.append(f'Over budget: £{cost:.1f}m of £{BUDGET:.0f}m.')
    if team.get('captain') not in ps or team.get('vice') not in ps or team.get('captain') == team.get('vice'):
        errs.append('Choose a captain and a different vice-captain from your 11.')
    return errs


def team_gameweek_points(team, gw_points):
    """Captain doubles; if the captain didn't play, the vice-captain doubles instead."""
    pts = {p: gw_points.get(p, {}).get('total', 0) for p in team['players']}
    played = {p for p in team['players'] if p in gw_points}
    cap = team['captain'] if team['captain'] in played else (team['vice'] if team['vice'] in played else None)
    return sum(pts.values()) + (pts[cap] if cap else 0), cap
