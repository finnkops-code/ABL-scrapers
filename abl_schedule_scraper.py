"""
ABL (Australian Baseball League) schedule scraper.

Data source
===========
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
        &hydrate=team,venue

This was verified live: querying leagueId=595 for a November 2025 date
range returned real, finished ABL games (Sydney Blue Sox 8 - Brisbane
Bandits 1, etc.) with correct team names, scores and venues, and a
2026 query confirmed the next ABL season's first games are scheduled
for 2026-11-19 (Brisbane Bandits @ Adelaide Giants).

Output shape mirrors the project's existing npb_schedule_scraper.py:
    {
      "bijgewerkt": "<ISO 8601 UTC timestamp>",
      "bron": "<the two Stats API URLs that were queried>",
      "season": <int>,
      "results": [ ...most recent completed game day... ],
      "schedule": [ ...next upcoming game day... ]
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

Why two separate fetches instead of one wide one
=================================================
The ABL only plays roughly mid-November to late January/early February,
so a narrow "yesterday to +2 days" window (the NPB scraper's approach,
which plays a near-daily schedule) is empty most of the year. Per an
explicit request, this scraper is expected to ALWAYS have something in
"schedule" (and "results" once a season has been played), even months
into the off-season.

The first version of this script fetched one combined window (400 days
back through 400 days forward) in a single request. That turned out to
silently break: the Stats API caps how many games a single schedule
request returns (confirmed live - a single query spanning ~800 days
returned only the ~87 games of the most recently completed season and
simply dropped every game from the *next* season, even though those
games fall well inside the requested date range, with no error and no
indication in the response that anything was cut off).

The fix is to make two independent, one-directional requests instead of
one two-directional request, since each direction alone comfortably
stays under whatever that cap is (confirmed live: a look-back-only
request and a look-forward-only request each returned their full,
untruncated season of games):
  - a "look back" request, [today - LOOKBACK_DAYS, today], to find
    "results" = every game on the single most recent date that has at
    least one Final game (the latest completed game day, however long
    ago that was).
  - a "look forward" request, [today, today + LOOKAHEAD_DAYS], to find
    "schedule" = every game on the single earliest date that has at
    least one not-yet-Final game (the next upcoming game day, however
    far off that is - even if it's next season).

If one of the two directions happens to contain no games at all (e.g. a
brand new league with no history yet), that list is simply left empty
rather than the script failing.
"""

import json
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone

STATS_API_BASE = "https://statsapi.mlb.com/api/v1/schedule"
SPORT_ID = 17          # Winter Leagues
LEAGUE_ID = 595         # Australian Baseball League
OUTPUT_FILE = "abl_schedule.json"

# Wide enough to always bridge the ABL's multi-month off-season in both
# directions (season runs roughly mid-Nov to early Feb each year), but
# each direction is fetched separately - see module docstring for why.
LOOKBACK_DAYS = 400
LOOKAHEAD_DAYS = 400


def abl_season_for_date(d):
    """
    The ABL season labelled e.g. "2026" runs roughly from mid-February
    of that year through to February of the following year (pre-season
    start ~Feb 15, championship in late Jan/early Feb). So a calendar
    date before ~Feb 15 belongs to the PREVIOUS season label. This is
    purely informational (the "season" field in the output) - the
    actual date range below is what determines which games come back,
    and the API is queried without a &season= param so it isn't
    affected by this boundary at all.
    """
    if (d.month, d.day) < (2, 15):
        return d.year - 1
    return d.year


def fetch_schedule(start_date, end_date):
    params = (
        f"?sportId={SPORT_ID}"
        f"&leagueId={LEAGUE_ID}"
        f"&startDate={start_date.isoformat()}"
        f"&endDate={end_date.isoformat()}"
        f"&hydrate=team,venue"
    )
    url = STATS_API_BASE + params
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
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


def games_by_date(data, want_played):
    """
    Group every game in a schedule response by officialDate, keeping
    only games whose Final-ness matches want_played.
    """
    grouped = {}
    for day in data.get("dates", []):
        for game in day.get("games", []):
            try:
                entry = build_game_entry(game)
            except (KeyError, TypeError):
                # Skip malformed/unexpected entries (e.g. split-squad
                # exhibition rows) rather than failing the whole run.
                continue
            if entry["played"] != want_played:
                continue
            grouped.setdefault(entry["date"], []).append(entry)
    return grouped


def main():
    today = datetime.now(timezone.utc).date()
    today_str = today.isoformat()

    back_start = today - timedelta(days=LOOKBACK_DAYS)
    forward_end = today + timedelta(days=LOOKAHEAD_DAYS)

    try:
        back_url, back_data = fetch_schedule(back_start, today)
        forward_url, forward_data = fetch_schedule(today, forward_end)
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        raise SystemExit(f"Failed to fetch ABL schedule from Stats API: {exc}")

    # "results": all games on the most recent date (<= today) that had
    # at least one finished game.
    finished_by_date = games_by_date(back_data, want_played=True)
    past_dates = [d for d in finished_by_date if d <= today_str]
    results = finished_by_date[max(past_dates)] if past_dates else []

    # "schedule": all games on the earliest date (>= today) that had at
    # least one not-yet-finished game.
    upcoming_by_date = games_by_date(forward_data, want_played=False)
    future_dates = [d for d in upcoming_by_date if d >= today_str]
    schedule = upcoming_by_date[min(future_dates)] if future_dates else []

    output = {
        "bijgewerkt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bron": f"{back_url} | {forward_url}",
        "season": abl_season_for_date(today),
        "results": results,
        "schedule": schedule,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(
        f"Wrote {OUTPUT_FILE}: {len(results)} result(s) "
        f"(latest completed day: {results[0]['date'] if results else 'none found'}), "
        f"{len(schedule)} scheduled game(s) "
        f"(next game day: {schedule[0]['date'] if schedule else 'none found'})."
    )


if __name__ == "__main__":
    main()
