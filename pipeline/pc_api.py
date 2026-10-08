"""Small client for the Play-Cricket v2 API.

The API key is only ever read from the PLAY_CRICKET_API_TOKEN environment
variable (a GitHub Actions secret) - never from a file in the repository.

FixtureClient serves saved JSON responses from a folder instead of calling
Play-Cricket, so the whole pipeline can be tested without network access or a key.
"""
import json
import os
import time
import urllib.parse
import urllib.request

BASE = "https://play-cricket.com/api/v2/"


class PlayCricketError(Exception):
    pass


class PlayCricketClient:
    def __init__(self, token, pause_seconds=1.0, retries=3):
        if not token:
            raise PlayCricketError("PLAY_CRICKET_API_TOKEN is not set")
        self.token = token
        self.pause = pause_seconds
        self.retries = retries

    def _get(self, endpoint, **params):
        params["api_token"] = self.token
        url = BASE + endpoint + "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
        last = None
        for attempt in range(1, self.retries + 1):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "HessleCC-stats-pipeline"})
                with urllib.request.urlopen(req, timeout=60) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                time.sleep(self.pause)  # be gentle with Play-Cricket
                return data
            except Exception as e:  # network blips, 5xx, timeouts
                last = e
                time.sleep(self.pause * attempt * 3)
        # Never echo the URL: it contains the API key
        raise PlayCricketError(f"{endpoint} failed after {self.retries} attempts: {type(last).__name__}")

    def matches(self, site_id, season, from_entry_date=None):
        return self._get("matches.json", site_id=site_id, season=season, from_entry_date=from_entry_date).get("matches", [])

    def match_detail(self, match_id):
        md = self._get("match_detail.json", match_id=match_id).get("match_details", [])
        return md[0] if md else None

    def league_table(self, division_id):
        lt = self._get("league_table.json", division_id=division_id).get("league_table", [])
        return lt[0] if lt else None


class FixtureClient:
    """Reads saved responses: matches_<season>.json, match_<id>.json, league_<division_id>.json"""

    def __init__(self, folder):
        self.folder = folder

    def _load(self, name):
        path = os.path.join(self.folder, name)
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def matches(self, site_id, season, from_entry_date=None):
        d = self._load(f"matches_{season}.json")
        return (d or {}).get("matches", [])

    def match_detail(self, match_id):
        d = self._load(f"match_{match_id}.json")
        md = (d or {}).get("match_details", [])
        return md[0] if md else None

    def league_table(self, division_id):
        d = self._load(f"league_{division_id}.json")
        lt = (d or {}).get("league_table", [])
        return lt[0] if lt else None
