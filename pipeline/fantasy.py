"""Hessle CC Fantasy Cricket: scoring, positions, prices and gameweeks.

Used by the Play-Cricket automation (to score each Saturday and send points to Supabase)
and to build the files the fantasy page reads.

Rules agreed with the club
--------------------------
Squad: 11 starters = 4 batters, 4 bowlers, 2 all-rounders, 1 wicketkeeper, plus 3 subs (any positions);
budget £100m for all 14. If a starter doesn't play, the first sub (in bench order) of the SAME position who
did play comes on. Captain scores double; the vice-captain takes over if the captain doesn't play.
Both must be starters, and subs never take the captaincy.
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
SQUAD = {'BAT': 4, 'BOWL': 4, 'AR': 2, 'WK': 1}   # the starting 11
BENCH = 3                                         # subs, any positions
BUDGET = 100.0
PRICE_MIN, PRICE_MAX = 3.5, 10.5   # £100m covers 14 players (11 + 3 subs)
FREE_TRANSFERS, TRANSFER_HIT = 2, 4

num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)
NOT_PLAYERS = {'Unsure'}
NEW_PLAYER_PRICE = 4.0   # a debutant with no track record starts as a bargain


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
    """£3.5m–£10.5m from expected points per gameweek going into `season`:
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
        with open(path, newline='', encoding='utf-8-sig') as f:
            # skip the explanatory note lines (starting with #) so the real column headings are read
            for row in csv.DictReader(line for line in f if not line.lstrip().startswith('#') and line.strip()):
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
            pos[p] = o['position'] or 'BAT'; prices[p] = o['price'] or NEW_PLAYER_PRICE; exp[p] = 0.0
    # debutants this season: added automatically after their first game, position from their games so far
    new = []
    for p in appearances(data, [season]):
        if p not in pos:
            kept = {_key(r) for r in data['keeping'] if r.get('Keeper') == p and r['Date'][:4] == str(season)}
            games = appearances(data, [season])[p]
            pos[p] = 'WK' if kept and len(kept) / len(games) >= 1 / 3 else _profile_role(data, p, season)
            prices[p] = NEW_PLAYER_PRICE; exp[p] = 0.0; new.append(p)
    xi = {}
    apps = appearances(data, [season, season - 1, season - 2])
    for p, a in apps.items():
        n1 = sum(1 for k in a if k[1] == '1st'); xi[p] = '1st' if n1 / len(a) >= 0.5 else '2nd'
    out = []
    for p in sorted(pos):
        o = ov.get(p, {})
        if o and not o.get('active', True):
            continue
        out.append({'name': p, 'position': (o.get('position') or pos[p]), 'price': o.get('price') or prices[p],
                    'xi': xi.get(p, '1st'), 'expected': round(exp.get(p, 0.0), 1), 'new': p in new and p not in ov})
    return out


def write_overrides_template(players, path):
    with open(path, 'w', newline='', encoding='utf-8') as f:
        f.write('# Hessle CC Fantasy: positions and prices. Edit any row; delete nothing.\n')
        f.write('# Position: BAT, BOWL, AR or WK.  Price: in £m (3.5-10.5).  Active: no = not available this season.\n')
        f.write('# Add a new signing as a new row. The automation re-reads this file every time it runs.\n')
        w = csv.writer(f); w.writerow(['Player', 'Position', 'Price', 'Active', 'XI', 'Expected pts per week'])
        for p in sorted(players, key=lambda x: ({'WK': 0, 'BAT': 1, 'AR': 2, 'BOWL': 3}[x['position']], -x['price'], x['name'])):
            w.writerow([p['name'], p['position'], f"{p['price']:.1f}", 'yes', p['xi'], p['expected']])


# ---------------------------------------------------------------- team checks (mirrors the database rules)
def check_team(team, players_by_name):
    """Returns a list of problems (empty = valid). team = {'players': [11 starters], 'bench': [3 subs], 'captain', 'vice'}."""
    errs = []
    ps, bench = team.get('players') or [], team.get('bench') or []
    if len(ps) != 11:
        errs.append('Pick 11 starters.')
    if len(bench) != BENCH:
        errs.append(f'Pick {BENCH} subs.')
    if len(set(ps + bench)) != len(ps) + len(bench):
        errs.append('A player can only be picked once.')
    unknown = [p for p in ps + bench if p not in players_by_name]
    if unknown:
        errs.append('Unknown players: ' + ', '.join(unknown))
        return errs
    count = defaultdict(int)
    for p in ps:
        count[players_by_name[p]['position']] += 1
    for pos, need in SQUAD.items():
        if count[pos] != need:
            errs.append(f'Your starting 11 needs {need} {pos} (you have {count[pos]}).')
    cost = sum(players_by_name[p]['price'] for p in ps + bench)
    if cost > BUDGET + 1e-9:
        errs.append(f'Over budget: £{cost:.1f}m of £{BUDGET:.0f}m.')
    if team.get('captain') not in ps or team.get('vice') not in ps or team.get('captain') == team.get('vice'):
        errs.append('Choose a captain and a different vice-captain from your starting 11.')
    return errs


def effective_eleven(team, gw_points, positions):
    """Auto-subs: each starter who didn't play is replaced by the first sub (bench order) of the same
    position who did play. Returns (eleven, {starter_out: sub_in})."""
    eleven = list(team['players']); used = set(); subs = {}
    for i, p in enumerate(team['players']):
        if p in gw_points:
            continue
        for b in team.get('bench') or []:
            if b not in used and b in gw_points and positions.get(b) == positions.get(p):
                eleven[i] = b; used.add(b); subs[p] = b
                break
    return eleven, subs


def team_gameweek_points(team, gw_points, positions=None):
    """Points for a team in a gameweek, after auto-subs. Captain doubles; if the captain didn't play,
    the vice-captain doubles instead (subs never take the captaincy)."""
    eleven, _ = effective_eleven(team, gw_points, positions or {}) if team.get('bench') else (list(team['players']), {})
    pts = {p: gw_points.get(p, {}).get('total', 0) for p in eleven}
    cap = team['captain'] if team['captain'] in gw_points else (team['vice'] if team['vice'] in gw_points else None)
    return sum(pts.values()) + (pts.get(cap, 0) if cap else 0), cap


# ---------------------------------------------------------------- price changes (after each Saturday, on form)
PRICE_FLOOR, PRICE_CEILING = 3.0, 12.0
PRICE_STEP_UP_BIG, PRICE_STEP_UP, PRICE_STEP_DOWN = 0.2, 0.1, 0.1


def expected_for_price(players):
    """Weekly points a player 'should' score for his price: a straight line fitted to the starting prices
    and the expected weekly points they were based on (players with a track record only)."""
    pts = [(p['price'], p['expected']) for p in players if p.get('expected', 0) > 0]
    if len(pts) < 2:
        return lambda price: max(1.0, price)
    n = len(pts); mx = sum(x for x, _ in pts) / n; my = sum(y for _, y in pts) / n
    var = sum((x - mx) ** 2 for x, _ in pts) or 1.0
    b = sum((x - mx) * (y - my) for x, y in pts) / var; a = my - b * mx
    return lambda price: max(1.0, a + b * price)


def price_changes(data, season, players):
    """Recalculated from scratch every run (so reruns and scorecard corrections always agree).
    players: the season's list with STARTING prices. Returns {player: [(gw, price_after_that_gw), ...]}
    and {player: current_price}. A new price applies from the next gameweek."""
    sp = season_points(data, season)
    gws = list(sp.keys())
    expect = expected_for_price(players)
    price = {p['name']: p['price'] for p in players}
    history = {p['name']: [] for p in players}
    for k, gw in enumerate(gws):
        if k >= 2:   # form needs three gameweeks
            last3 = gws[k - 2:k + 1]
            for name in price:
                weeks = [sp[g].get(name) for g in last3]
                missed = sum(1 for w in weeks if w is None)
                form = sum(w['total'] for w in weeks if w) / 3
                ratio = form / expect(price[name])
                if missed >= 2 or ratio <= 0.5:
                    step = -PRICE_STEP_DOWN
                elif ratio >= 1.5:
                    step = PRICE_STEP_UP_BIG
                elif ratio >= 1.2:
                    step = PRICE_STEP_UP
                else:
                    step = 0.0
                price[name] = round(min(PRICE_CEILING, max(PRICE_FLOOR, price[name] + step)), 1)
        for name in price:
            history[name].append((gw, price[name]))
    return history, dict(price)


# ---------------------------------------------------------------- chips
CHIPS = ('triple', 'bench')   # Triple Captain, Bench Boost: once a season each, one chip per gameweek (wildcard included)


def team_gameweek_points_chip(team, gw_points, positions, chip=None):
    """Like team_gameweek_points, with a chip played that gameweek.
    triple: the doubled player (captain, or vice if the captain didn't play) scores x3 instead of x2.
    bench:  all 14 count (no auto-subs needed)."""
    if chip == 'bench':
        squad = list(team['players']) + list(team.get('bench') or [])
        pts = sum(gw_points.get(p, {}).get('total', 0) for p in squad)
        cap = team['captain'] if team['captain'] in gw_points else (team['vice'] if team['vice'] in gw_points else None)
        return pts + (gw_points[cap]['total'] if cap else 0), cap
    total, cap = team_gameweek_points(team, gw_points, positions)
    if chip == 'triple' and cap:
        total += gw_points[cap]['total']
    return total, cap


# ---------------------------------------------------------------- team of the week
def team_of_the_week(gw_points, positions):
    """Best possible 4 batters, 4 bowlers, 2 all-rounders and 1 keeper by points that gameweek."""
    best = []
    for pos, n in SQUAD.items():
        best += sorted([p for p in gw_points if positions.get(p) == pos], key=lambda p: (-gw_points[p]['total'], p))[:n]
    return best
