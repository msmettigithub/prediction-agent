"""Sports data — ESPN API + The Odds API for lines and implied probabilities."""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from config import load_config
from tools.base_tool import BaseTool, ToolFetchError

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sports_data.json"

# ESPN sport/league paths
LEAGUES = {
    "nfl": ("football", "nfl"),
    "nba": ("basketball", "nba"),
    "mlb": ("baseball", "mlb"),
    "nhl": ("hockey", "nhl"),
    "ncaaf": ("football", "college-football"),
    "ncaab": ("basketball", "mens-college-basketball"),
}


class SportsDataTool(BaseTool):
    ESPN_URL = "https://site.api.espn.com/apis/site/v2/sports"
    ODDS_URL = "https://api.the-odds-api.com/v4/sports"

    def __init__(self, mock_mode: bool = False):
        self.mock_mode = mock_mode
        self.config = load_config()
        self._last_request = 0.0
        self._min_interval = 0.5

    @property
    def name(self) -> str:
        return "sports_data"

    def get_schema(self) -> dict:
        return {
            "name": "sports_data",
            "description": "Fetch sports scores, team stats, injury reports from ESPN, and Vegas odds/implied probabilities from The Odds API. Supports: NFL, NBA, MLB, NHL, NCAAF, NCAAB.",
            "parameters": {
                "type": "object",
                "properties": {
                    "league": {"type": "string", "enum": list(LEAGUES.keys()), "description": "Sport league"},
                    "team": {"type": "string", "description": "Team name to focus on (optional)"},
                    "include_odds": {"type": "boolean", "default": True},
                },
                "required": ["league"],
            },
        }

    def _rate_limit(self):
        elapsed = time.time() - self._last_request
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request = time.time()

    def _fetch(self, league: str = "nba", team: str = None, include_odds: bool = True, **kwargs) -> Any:
        if self.mock_mode:
            return json.loads(FIXTURE_PATH.read_text())

        result = {"events": [], "odds": None, "team_stats": {}}
        sport, lg = LEAGUES.get(league, ("basketball", "nba"))

        # ESPN scoreboard
        self._rate_limit()
        for attempt in range(2):
            try:
                resp = requests.get(f"{self.ESPN_URL}/{sport}/{lg}/scoreboard", timeout=10)
                if resp.status_code >= 500 and attempt == 0:
                    time.sleep(2)
                    continue
                resp.raise_for_status()
                espn_data = resp.json()
                for event in espn_data.get("events", []):
                    competitions = event.get("competitions", [{}])
                    if competitions:
                        comp = competitions[0]
                        teams = comp.get("competitors", [])
                        ev = {
                            "name": event.get("name", ""),
                            "start_time": event.get("date", ""),
                            "status": event.get("status", {}).get("type", {}).get("name", ""),
                            "teams": [],
                        }
                        for t in teams:
                            team_data = {
                                "name": t.get("team", {}).get("displayName", ""),
                                "score": t.get("score", "0"),
                                "home_away": t.get("homeAway", ""),
                                "record": t.get("records", [{}])[0].get("summary", "") if t.get("records") else "",
                            }
                            ev["teams"].append(team_data)
                        result["events"].append(ev)
                break
            except requests.RequestException as e:
                if attempt == 0:
                    time.sleep(2)
                    continue
                raise ToolFetchError(f"ESPN API error: {e}")

        # The Odds API
        if include_odds:
            odds_key = getattr(self.config, "odds_api_key", "")
            if odds_key:
                self._rate_limit()
                try:
                    odds_sport = f"americanfootball_{lg}" if sport == "football" else f"{sport}_{lg}"
                    resp = requests.get(
                        f"{self.ODDS_URL}/{odds_sport}/odds",
                        params={"apiKey": odds_key, "regions": "us", "markets": "h2h,spreads,totals"},
                        timeout=10,
                    )
                    if resp.status_code == 200:
                        result["odds"] = resp.json()
                except requests.RequestException:
                    pass

        return result

    def _parse(self, raw_data: Any) -> dict:
        events = []
        for ev in raw_data.get("events", []):
            if isinstance(ev, dict):
                parsed_ev = {
                    "sport": ev.get("sport", ""),
                    "league": ev.get("league", ""),
                    "start_time": ev.get("start_time", ""),
                }
                # Handle both mock format and live format
                if "home_team" in ev:
                    parsed_ev["home_team"] = ev["home_team"]
                    parsed_ev["away_team"] = ev["away_team"]
                elif "teams" in ev:
                    for t in ev.get("teams", []):
                        key = "home_team" if t.get("home_away") == "home" else "away_team"
                        parsed_ev[key] = {"name": t["name"], "record": t.get("record", ""), "score": t.get("score")}
                events.append(parsed_ev)

        odds = raw_data.get("odds")
        odds_parsed = None
        if odds and isinstance(odds, dict):
            implied = odds.get("implied_win_prob", {})
            odds_parsed = {
                "moneyline": odds.get("moneyline"),
                "spread": odds.get("spread"),
                "implied_win_prob": implied,
                "source": odds.get("source", "consensus"),
            }

        return {
            "events": events,
            "odds": odds_parsed,
            "team_stats": raw_data.get("team_stats", {}),
            "source": "espn_odds",
            "fetched_at": datetime.utcnow().isoformat(),
            "confidence": "high" if odds_parsed else "medium",
        }

    def health_check(self) -> dict:
        if self.mock_mode:
            return {"healthy": True, "latency_ms": 1.0, "error": None}
        start = time.time()
        try:
            resp = requests.get(f"{self.ESPN_URL}/basketball/nba/scoreboard", timeout=5)
            latency = (time.time() - start) * 1000
            return {"healthy": resp.status_code == 200, "latency_ms": latency, "error": None}
        except Exception as e:
            return {"healthy": False, "latency_ms": 0, "error": str(e)}
