"""Consistency checks on docs/data.json: scores vs scorecards, overs, innings, names, awards.

    python pipeline/check_data.py            # checks docs/data.json
    python pipeline/check_data.py other.json

Nothing is changed; it only lists things worth a look. See pipeline/DATA_CHECKS.md for the October 2026 list.
"""
import sys, json, re, os, difflib
from collections import Counter, defaultdict
from datetime import date, datetime
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from run import expand, load_json
_fwd = load_json(os.path.join(HERE, 'key_map.json'))
_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(HERE), 'docs', 'data.json')
d = expand(json.load(open(_path, encoding='utf-8')), {v: k for k, v in _fwd.items()})
F = defaultdict(list)
key = lambda r: (r['Date'], r['Team'])
lab = lambda k: f"{k[0]} {k[1]} XI"
by = {ds: defaultdict(list) for ds in ('batting', 'bowling', 'fielding', 'keeping')}
for ds in by:
    for r in d[ds]: by[ds][key(r)].append(r)
num = lambda x: x if isinstance(x, (int, float)) and not isinstance(x, bool) else 0
balls = lambda o: int(o) * 6 + round((o - int(o)) * 10) if isinstance(o, (int, float)) else None
print('Dismissal values (batting):', dict(Counter(r['Dismissal'] for r in d['batting'])))
print('Dismissal values (fielding):', dict(Counter(r['Dismissal'] for r in d['fielding'])), '| keeping:', dict(Counter(r['Dismissal'] for r in d['keeping'])))
NOT_OUT = {'Not Out', 'DNB', 'Retired', 'Retired Not Out', 'Retired Hurt', 'Did Not Bat', 'Absent'}
for m in d['matches']:
    k = key(m); L = lab(k) + ' v ' + m['Opponent']
    bat, bowl, fld, kp = (by[x][k] for x in ('batting', 'bowling', 'fielding', 'keeping'))
    bf = m['Bat/Field First']; bi, wi = (1, 2) if bf == 'Bat' else (2, 1)
    if bf not in ('Bat', 'Field'): F['Bat/Field First value'].append(f"{L}: {bf!r}")
    rs, rc = m['Runs Scored'], m['Runs Conceded']
    if m['Result'] in ('W', 'L') and isinstance(rs, (int, float)) and isinstance(rc, (int, float)):
        if (m['Result'] == 'W') != (rs > rc): F['Result vs scores'].append(f"{L}: result {m['Result']} but {rs} v {rc}")
    if {r['Innings'] for r in bat} - {bi}: F['Innings numbers'].append(f"{L}: batting innings {sorted({r['Innings'] for r in bat})}, expected {bi} ({bf} first)")
    if {r['Innings'] for r in bowl} - {wi}: F['Innings numbers'].append(f"{L}: bowling innings {sorted({r['Innings'] for r in bowl})}, expected {wi}")
    if {r['Innings'] for r in fld + kp} - {wi}: F['Innings numbers'].append(f"{L}: fielding/keeping innings {sorted({r['Innings'] for r in fld + kp})}, expected {wi}")
    if not bat: F['Missing scorecard'].append(f"{L}: no batting rows")
    if not bowl: F['Missing scorecard'].append(f"{L}: no bowling rows")
    if len(bat) > 11: F['Too many batters'].append(f"{L}: {len(bat)}")
    sr = sum(num(r['Runs']) for r in bat)
    if isinstance(rs, (int, float)) and sr > rs: F['Batters total more than team score'].append(f"{L}: batters {sr} > team {rs}")
    outs = sum(1 for r in bat if r['Dismissal'] not in NOT_OUT)
    if isinstance(m['Wickets Lost'], (int, float)) and outs != m['Wickets Lost']: F['Wickets lost vs dismissals'].append(f"{L}: match says {m['Wickets Lost']}, batting rows show {outs}")
    for r in bat:
        if r.get('Team Runs') != rs: F['Batting Team Runs vs match'].append(f"{L} {r['Batter']}: {r.get('Team Runs')} vs {rs}"); break
    for r in bat + bowl:
        if r.get('W/L') != m['Result']: F['Row W/L vs match result'].append(f"{L}: row {r.get('W/L')} vs {m['Result']}"); break
    br = sum(num(r['Runs']) for r in bowl)
    if isinstance(rc, (int, float)) and br > rc: F['Bowling runs more than runs conceded'].append(f"{L}: bowlers {br} > conceded {rc}")
    bw = sum(num(r['Wickets']) for r in bowl); ro = sum(1 for r in fld if 'Run' in (r['Dismissal'] or ''))
    wt = m['Wickets Taken']
    if isinstance(wt, (int, float)) and bw > wt: F['Bowler wickets more than wickets taken'].append(f"{L}: bowlers {bw} > {wt}")
    if isinstance(wt, (int, float)) and bw + ro > wt: F['Wickets + run-outs more than wickets taken'].append(f"{L}: {bw}+{ro} run-outs > {wt}")
    ob = balls(m['Overs Bowled']); sb = sum(balls(r['Overs']) or 0 for r in bowl)
    if ob is not None and bowl and sb != ob: F['Bowlers overs vs match overs'].append(f"{L}: bowlers {sb//6}.{sb%6} vs match {m['Overs Bowled']}")
    caught = sum(1 for r in fld if r['Dismissal'] == 'Caught') + sum(1 for r in kp if r['Dismissal'] in ('Caught', 'Stumping'))
    if caught > bw: F['Catches/stumpings more than bowler wickets'].append(f"{L}: {caught} > {bw}")
    names_bowl = {r['Bowler'] for r in bowl}
    for r in fld + kp:
        b = r.get('Bowler')
        if b and b not in names_bowl and 'Run' not in (r['Dismissal'] or ''): F['Catch credited to a bowler who did not bowl'].append(f"{L}: {r.get('Fielder') or r.get('Keeper')} off {b}")
    xi = {r['Batter'] for r in bat}
    for r in fld + kp:
        p = r.get('Fielder') or r.get('Keeper')
        if xi and p not in xi and p not in names_bowl: F['Fielder not in the team sheet'].append(f"{L}: {p}")
    for r in bowl:
        if xi and r['Bowler'] not in xi: F['Bowler not in the team sheet'].append(f"{L}: {r['Bowler']}")
    if len({r['Keeper'] for r in kp}) > 1: F['More than one keeper'].append(f"{L}: {sorted({r['Keeper'] for r in kp})}")
    for nm, c in Counter(r['Batter'] for r in bat).items():
        if c > 1: F['Player listed twice'].append(f"{L}: batter {nm}")
    for nm, c in Counter(r['Bowler'] for r in bowl).items():
        if c > 1: F['Player listed twice'].append(f"{L}: bowler {nm}")
    pos = [r['Pos'] for r in bat if r['Pos'] is not None]
    if len(pos) != len(set(pos)): F['Batting positions repeated'].append(f"{L}: {sorted(pos)}")
for r in d['batting']:
    L = f"{lab(key(r))} v {r['Opponent']} {r['Batter']}"
    R, B, f4, f6 = r['Runs'], r['Balls'], num(r['4s']), num(r['6s'])
    if isinstance(R, (int, float)) and R < 0: F['Negative runs'].append(L)
    if isinstance(R, (int, float)) and f4 * 4 + f6 * 6 > R: F['Boundaries more than runs'].append(f"{L}: {R} runs, {f4}x4 {f6}x6")
    if isinstance(B, (int, float)) and B > 0 and isinstance(R, (int, float)) and R > B * 6: F['Runs impossible for balls'].append(f"{L}: {R} off {B}")
    if r['Dismissal'] == 'DNB' and (num(R) or num(B)): F['DNB with runs/balls'].append(f"{L}: {R} ({B})")
for r in d['bowling']:
    L = f"{lab(key(r))} v {r['Opponent']} {r['Bowler']}"
    o = r['Overs']
    if isinstance(o, (int, float)) and round((o - int(o)) * 10) > 5: F['Invalid overs'].append(f"{L}: {o}")
    if isinstance(o, (int, float)) and num(r['Maidens']) > int(o): F['More maidens than overs'].append(f"{L}: {r['Maidens']} in {o}")
    if num(r['Wickets']) > 10: F['More than 10 wickets'].append(L)
    if isinstance(o, (int, float)) and r['Balls'] != balls(o): F['Balls vs overs'].append(f"{L}: {o} overs, {r['Balls']} balls")
# text where a number should be
for ds, cols in (('batting', ('Runs', 'Balls', '4s', '6s', 'Pos')), ('bowling', ('Overs', 'Maidens', 'Runs', 'Wickets', 'Wides', 'No Balls')),
                 ('matches', ('Runs Scored', 'Wickets Lost', 'Runs Conceded', 'Wickets Taken', 'Overs Batted', 'Overs Bowled'))):
    for r in d[ds]:
        for c in cols:
            v = r.get(c)
            if v is not None and not (v == '-' and r.get('Dismissal') == 'DNB') and not isinstance(v, (int, float)):
                F['Text where a number should be'].append(f"{ds} {lab(key(r))} v {r['Opponent']} {r.get('Batter') or r.get('Bowler') or ''} {c}={v!r}")
# names
people = Counter()
for ds, c in (('batting', 'Batter'), ('bowling', 'Bowler'), ('fielding', 'Fielder'), ('keeping', 'Keeper'), ('fielding', 'Bowler'), ('keeping', 'Bowler')):
    for r in d[ds]:
        if r[c] is not None or c != 'Bowler': people[r[c]] += 1
for n in people:
    if not n or re.search(r'unsure|unknown|^\?|sub\b|^tbc', n or '', re.I): F['Placeholder names'].append(f"{n!r} ({people[n]} rows)")
    elif n != n.strip() or '  ' in n: F['Stray spaces in names'].append(repr(n))
lower = defaultdict(list)
for n in people: lower[re.sub(r'\W', '', (n or '').lower())].append(n)
for v in lower.values():
    if len(v) > 1: F['Same name, different spelling/case'].append(' / '.join(v))
ns = sorted(n for n in people if n)
for i, a in enumerate(ns):
    for b in ns[i + 1:]:
        if difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio() > 0.87 and a.split()[-1][:3].lower() == b.split()[-1][:3].lower():
            F['Similar names (check)'].append(f"{a} ({people[a]}) / {b} ({people[b]})")
opp = defaultdict(set)
for ds in ('matches', 'batting', 'bowling', 'fielding', 'keeping'):
    for r in d[ds]: opp[re.sub(r'\b(cc|cricket club)\b|\W', '', r['Opponent'].lower())].add(r['Opponent'])
for v in opp.values():
    if len(v) > 1: F['Opponent spelled more than one way'].append(' / '.join(sorted(v)))
mk = {key(m) for m in d['matches']}
for r in d['motm']:
    k = (r['Date'], r['Team'])
    if k not in mk: F['MoTM for a match not in the data'].append(lab(k))
    elif r['MOTM'] not in {x['Batter'] for x in by['batting'][k]} | {x['Bowler'] for x in by['bowling'][k]} | {x['Fielder'] for x in by['fielding'][k]}:
        F['MoTM winner not in that match'].append(f"{lab(k)}: {r['MOTM']}")
played = defaultdict(set)
for ds, c in (('batting', 'Batter'), ('bowling', 'Bowler')):
    for r in d[ds]: played[datetime.strptime(r['Date'], '%Y-%m-%d').strftime('%b %Y')].add(r[c])
for r in d['potm']:
    for mo in r['months']:
        if r['player'] not in played[mo]: F['PotM winner did not play that month'].append(f"{mo}: {r['player']}")
# findings already looked at and accepted (pipeline/manual/data_checks_accepted.json)
try:
    _acc = json.load(open(os.path.join(HERE, 'manual', 'data_checks_accepted.json'), encoding='utf-8'))
except (OSError, ValueError):
    _acc = {}
accepted = {}
for k in list(F):
    ok = set((_acc.get(k) or {}).get('items', []))
    keep = [] if (_acc.get(k) or {}).get('all') else [x for x in F[k] if x not in ok]
    if len(keep) < len(F[k]): accepted[k] = len(F[k]) - len(keep)
    if keep: F[k] = keep
    else: del F[k]
days = Counter(date.fromisoformat(m['Date']).strftime('%A') for m in d['matches'])
print('Match days:', dict(days))
print('Already checked and accepted:', sum(accepted.values()), dict(accepted))
print('New issues:', sum(len(v) for v in F.values()) or 'none')
for k, v in F.items():
    print(f"\n## {k} ({len(v)})")
    for x in v[:40]: print('  -', x)
    if len(v) > 40: print(f'  ... {len(v) - 40} more')
