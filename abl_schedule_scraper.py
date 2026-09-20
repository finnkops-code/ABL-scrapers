"""
ABL (Australian Baseball League) schedule scraper.

Data source
------------
theabl.com.au is a Nuxt.js front end whose schedule/scores data is loaded
client-side from MLB Advanced Media's public Stats API (the same backend
used for MLB, MiLB and several international winter leagues). This was
confirmed by inspecting the site's Vuex store in a browser: team, season
and game objects all use the exact Stats API schema (teamCode, fileCode,
parentOrgName, etc.), and the site's own "Game centre" links resolve to
/game/{gamePk}, where {gamePk} is the Stats API's own game primary key.

Endpoint used:
    https://statsapi.mlb.com/api/v1/schedule
        ?sportId=17          -> "Winter Leagues" (the Stats API sport
                                 bucket that several winter leagues,
                                 including the ABL, are filed under)
        &leagueId=595        -> Australian Baseball League
        &startDate=YYYY-MM-DD
        &endDate=YYYY-MM-DD
        &hydrate=team,linescore,venue

This was verified live: querying leagueId=595 for a November 2025 date
range returned real ABL games (Sydney Blue Sox @ Brisbane Bandits, etc.)
with correct team names, scores and venues.

Output shape mirrors the project's existing npb_schedule_scraper.py:
    {
      "bijgewerkt": "<ISO 8601 UTC timestamp>",
      "bron": "<the exact Stats API URL that was queried>",
      "season": <int>,
      "results": [ ...finished games... ],
      "schedule": [ ...upcoming games... ]
    }

Each game entry:
    {
      "league": "Australian Baseball League",
      "date": "YYYY-MM-DD",            # officialDate
      "game_number": 1,
      "venue": "Viticon Stadium",
      "start_time": "2025-11-13T08:30:00Z",   # gameDate, UTC ISO 8601
      "away_team": "Sydney Blue Sox",
      "away_logo": "https://www.mlbstatic.com/team-logos/4069.svg",
      "away_score": 8,
      "home_team": "Brisbane Bandits",
      "home_logo": "https://www.mlbstatic.com/team-logos/4065.svg",
      "home_score": 1,
      "played": true,
      "boxscore_url": "https://theabl.com.au/game/825548"
    }

Unlike the NPB scraper, the ABL has a single league (no Central/Pacific
split), so "league" is always the constant "Australian Baseball League" -
kept as a field for structural consistency with the NPB JSON.

A game is bucketed into "results" if the Stats API reports it Final
(abstractGameState == "Final"), otherwise into "schedule" (Preview,
Live, Postponed, etc. all count as "not yet a final result").

Window fetched: yesterday -> today+2 days (5 days total), which comfortably
covers "yesterday/today" results and "today/tomorrow" upcoming games the
same way the NPB scraper does, while tolerating days with no games (e.g.
the ABL off-season, when the API simply returns zero games and this
script writes empty results/schedule arrays rather than failing).
"""

import json
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone

STATS_API_BASE = "https://statsapi.mlb.com/api/v1/schedule"
SPORT_ID = 17          # Winter Leagues
LEAGUE_ID = 595         # Australian Baseball League
OUTPUT_FILE = "abl_schedule.json"

DAYS_BEFORE = 1
DAYS_AFTER = 2


def abl_season_for_date(d):
    """
    The ABL season labelled e.g. "2026" runs roughly from
    mid-February of that year through to February of the following
    year (pre-season start ~Feb 15, championship in late Jan/early Feb).
    So a calendar date before ~Feb 15 belongs to the PREVIOUS season
    label. This only affects the informational "season" field and the
    optional &season= query param; the actual date range (startDate/
    endDate) is what really determines which games come back.
    """
    if (d.month, d.day) < (2, 15):
        return d.year - 1
    return d.year


def fetch_schedule(start_date, end_date, season):
    params = (
        f"?sportId={SPORT_ID}"
        f"&leagueId={LEAGUE_ID}"
        f"&season={season}"
        f"&startDate={start_date.isoformat()}"
        f"&endDate={end_date.isoformat()}"
        f"&hydrate=team,venue"
    )
    url = STATS_API_BASE + params
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return url, data


def team_logo(team_id):
    return f"https://www.mlbstatic.com/team-logos/{team_id}.svg"


def build_game_entry(game):
    away = game["teams"]["away"]
    home = game["teams"]["home"]
    state = game.get("status", {}).get("abstractGameState", "")
    played = state == "Final"

    return {
        "league": "Australian Baseball League",
        "date": game.get("officialDate"),
        "game_number": game.get("gameNumber", 1),
        "venue": game.get("venue", {}).get("name"),
        "start_time": game.get("gameDate"),
        "away_team": away["team"]["name"],
        "away_logo": team_logo(away["team"]["id"]),
        "away_score": away.get("score") if played else None,
        "home_team": home["team"]["name"],
        "home_logo": team_logo(home["team"]["id"]),
        "home_score": home.get("score") if played else None,
        "played": played,
        "boxscore_url": f"https://theabl.com.au/game/{game['gamePk']}",
    }


def main():
    today = datetime.now(timezone.utc).date()
    start_date = today - timedelta(days=DAYS_BEFORE)
    end_date = today + timedelta(days=DAYS_AFTER)
    season = abl_season_for_date(today)

    try:
        source_url, data = fetch_schedule(start_date, end_date, season)
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        raise SystemExit(f"Failed to fetch ABL schedule from Stats API: {exc}")

    results = []
    schedule = []

    for day in data.get("dates", []):
        for game in day.get("games", []):
            try:
                entry = build_game_entry(game)
            except (KeyError, TypeError):
                # Skip malformed/unexpected entries (e.g. split-squad
                # exhibition rows) rather than failing the whole run.
                continue
            if entry["played"]:
                results.append(entry)
            else:
                schedule.append(entry)

    output = {
        "bijgewerkt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bron": source_url,
        "season": season,
        "results": results,
        "schedule": schedule,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(
        f"Wrote {OUTPUT_FILE}: {len(results)} result(s), "
        f"{len(schedule)} scheduled game(s) "
        f"({start_date} to {end_date}, season {season})."
    )


if __name__ == "__main__":
    main()
