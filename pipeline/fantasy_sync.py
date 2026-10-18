"""Send Hessle CC Fantasy data to Supabase after each automation run.

Needs two settings in GitHub (Settings > Secrets and variables > Actions):
  Variable  SUPABASE_URL          e.g. https://abcdxyz.supabase.co
  Secret    SUPABASE_SERVICE_KEY  the service_role key (never put this anywhere else)

What it does each run
  1. Players, positions and prices: worked out from the data, then pipeline/fantasy/positions.csv
     (the club's edits) applied on top. Players no longer listed are marked unavailable.
  2. Points for every gameweek of the fantasy season that has been played.
  3. Because it runs on the automation's schedule, it also keeps the free Supabase project awake.

In dry_run mode it only reports what it would send. It never stops the dashboard update:
any problem is recorded as a warning in the report.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

import fantasy

HERE = os.path.dirname(os.path.abspath(__file__))
OVERRIDES = os.path.join(HERE, 'fantasy', 'positions.csv')


class Supabase:
    def __init__(self, url, key):
        self.base = url.rstrip('/') + '/rest/v1/'
        self.key = key

    def _req(self, method, path, body=None, prefer=None):
        headers = {'apikey': self.key, 'Authorization': 'Bearer ' + self.key, 'Content-Type': 'application/json',
                   'User-Agent': 'HessleCC-fantasy-sync'}
        if prefer:
            headers['Prefer'] = prefer
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                txt = r.read().decode() or 'null'
                return json.loads(txt)
        except urllib.error.HTTPError as e:
            raise RuntimeError(f'Supabase {method} {path.split("?")[0]} failed ({e.code}): {e.read().decode()[:300]}')

    def get(self, path):
        return self._req('GET', path)

    def upsert(self, table, rows, conflict):
        for i in range(0, len(rows), 500):
            self._req('POST', f'{table}?on_conflict={conflict}', rows[i:i + 500], 'resolution=merge-duplicates,return=minimal')

    def patch(self, path, body):
        return self._req('PATCH', path, body, 'return=minimal')


def fantasy_season(gameweeks, today):
    """The season being played or coming up: the first gameweek on or after today, else the latest season set up."""
    upcoming = [g for g in gameweeks if g['gw'] >= today.isoformat()]
    if upcoming:
        return min(upcoming, key=lambda g: g['gw'])['season']
    return max(g['season'] for g in gameweeks) if gameweeks else None


def build_payload(data, season, gameweek_dates, overrides_path=OVERRIDES):
    players = fantasy.build_players(data, season, overrides_path)     # prices here are the season's STARTING prices
    names = {p['name'] for p in players}
    history, current = fantasy.price_changes(data, season, players)    # recalculated from the season's form every run
    player_rows = [{'name': p['name'], 'position': p['position'], 'price': current[p['name']], 'start_price': p['price'], 'xi': p['xi'], 'active': True} for p in players]
    price_rows = [{'player': name, 'gw': gw, 'price': price} for name, h in history.items() for gw, price in h if gw in gameweek_dates]
    new_players = [f"{p['name']} ({p['position']}, £{p['price']:.1f}m)" for p in players if p.get('new')]
    point_rows, skipped = [], []
    for gw, ps in fantasy.season_points(data, season).items():
        if gw not in gameweek_dates:
            skipped.append(gw)
            continue
        for name, v in ps.items():
            if name in names:
                point_rows.append({'player': name, 'gw': gw, 'points': v['total'], 'parts': v['parts'], 'matches': v['matches']})
    return player_rows, point_rows, skipped, new_players, price_rows


def sync(data, mode='dry_run', today=None, client=None):
    """Returns a short summary for the report. Raises nothing: problems come back as 'warning'."""
    url, key = os.environ.get('SUPABASE_URL'), os.environ.get('SUPABASE_SERVICE_KEY')
    if client is None:
        if not url or not key:
            return {'status': 'not set up', 'note': 'SUPABASE_URL / SUPABASE_SERVICE_KEY not set, so fantasy data was not sent.'}
        client = Supabase(url, key)
    today = today or date.today()
    try:
        gws = client.get('fantasy_gameweeks?select=gw,season&order=gw')      # also keeps the project awake
        season = fantasy_season(gws, today)
        if season is None:
            return {'status': 'no gameweeks', 'note': 'No gameweeks set up in Supabase yet (run supabase_setup.sql).'}
        players, points, skipped, new_players, prices = build_payload(data, season, {g['gw'] for g in gws if g['season'] == season})
        summary = {'status': 'dry run' if mode != 'publish' else 'sent', 'season': season, 'players': len(players),
                   'point_rows': len(points), 'gameweeks_scored': len({p['gw'] for p in points}), 'skipped_dates': skipped,
                   'new_players': new_players,
                   'price_changes': sum(1 for p in players if p['price'] != p['start_price'])}
        if mode != 'publish':
            return summary
        client.upsert('fantasy_players', players, 'name')
        current = {r['name'] for r in client.get('fantasy_players?select=name&active=eq.true')}
        gone = sorted(current - {p['name'] for p in players})
        for name in gone:   # no longer listed: unavailable (kept, so existing teams still show them)
            client.patch('fantasy_players?name=eq.' + urllib.parse.quote(name), {'active': False})
        if points:
            client.upsert('fantasy_points', points, 'player,gw')
        if prices:
            client.upsert('fantasy_price_history', prices, 'player,gw')
        summary['made_unavailable'] = gone
        return summary
    except Exception as e:
        return {'status': 'warning', 'note': f'Fantasy sync problem (dashboard update not affected): {e}'}
