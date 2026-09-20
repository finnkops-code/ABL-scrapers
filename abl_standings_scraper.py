"""
ABL (Australian Baseball League) standings scraper.

Data source
===========
Same MLB Advanced Media Stats API backend used by abl_schedule_scraper.py
(see that file's docstring for how this was confirmed). Standings come
from:

    https://statsapi.mlb.com/api/v1/standings
        ?leagueId=595   -> Australian Baseball League
        &season=YYYY
        &sportId=17     -> Winter Leagues

Verified live: querying season=2025 returned the ABL's real final regular
season standings (Sydney Blue Sox 24-14 in first, down to Brisbane
Bandits 16-22 in fourth). Querying season=2026 (the season that starts
2026-11-19) currently returns "records": [] because no games have been
played yet.

The zero position fallback
===========================
Per explicit request: theabl.com.au/standings/ itself shows an empty
table right now for the 2026 season (no games played yet), and this
scraper should still always render a table rather than nothing. So when
the Stats API returns no standings records for the current season, this
script builds a placeholder table instead:
  - every team that is actually rostered for that season (fetched live
    from the Stats API's own team list for that season, so this doesn't
    need to be hardcoded and self corrects if the league adds or drops
    a franchise),
  - sorted alphabetically by team name (since there is no real
    performance to rank by yet),
  - every stat at zero (0 wins, 0 losses, ".000" pct, "-" games back and
    streak, 0 runs scored/allowed/differential).

The output includes a "placeholder" boolean so any page rendering this
JSON can show a "season hasn't started yet" note if it wants to, without
having to guess from the numbers.

Output shape
============
    {
      "bijgewerkt": "<ISO 8601 UTC timestamp>",
      "bron": "<the exact Stats API URL that was queried>",
      "season": <int>,
      "placeholder": <bool>,
      "standings": [
        {
          "rank": 1,
          "team": "Sydney Blue Sox",
          "logo": "https://www.mlbstatic.com/team-logos/4069.svg",
          "wins": 24,
          "losses": 14,
          "pct": ".632",
          "games_back": "-",
          "streak": "L1",
          "runs_scored": 208,
          "runs_allowed": 142,
          "run_differential": 66
        },
        ...
      ]
    }
"""

import json
import urllib.request
import urllib.error
from datetime import datetime, timezone

STATS_API_STANDINGS = "https://statsapi.mlb.com/api/v1/standings"
STATS_API_TEAMS = "https://statsapi.mlb.com/api/v1/teams"
SPORT_ID = 17          # Winter Leagues
LEAGUE_ID = 595         # Australian Baseball League
OUTPUT_FILE = "abl_standings.json"

# Full team names, matching abl_schedule_scraper.py's korte_naam map but
# inverted (Stats API standings rows only give the short club name, e.g.
# "Blue Sox" rather than "Sydney Blue Sox").
TEAM_FULL_NAMES = {
    4064: "Adelaide Giants",
    4065: "Brisbane Bandits",
    4066: "Canberra Cavalry",
    4067: "Melbourne Aces",
    4068: "Perth Heat",
    4069: "Sydney Blue Sox",
    5404: "Geelong-Korea",
    5405: "Auckland Tuatara",
}


def abl_season_for_date(d):
    """
    Same season boundary logic as abl_schedule_scraper.py: a calendar
    date before ~Feb 15 belongs to the previous season's label.
    """
    if (d.month, d.day) < (2, 15):
        return d.year - 1
    return d.year


def team_logo(team_id):
    return f"https://www.mlbstatic.com/team-logos/{team_id}.svg"


def team_full_name(team_id, fallback_name):
    return TEAM_FULL_NAMES.get(team_id, fallback_name)


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_standings(season):
    url = f"{STATS_API_STANDINGS}?leagueId={LEAGUE_ID}&season={season}&sportId={SPORT_ID}"
    return url, fetch_json(url)


def fetch_active_teams(season):
    """
    Every team rostered for the given season, used both to build the
    zero position fallback and to resolve full team names/logos.
    """
    url = f"{STATS_API_TEAMS}?sportId={SPORT_ID}&leagueId={LEAGUE_ID}&season={season}"
    data = fetch_json(url)
    teams = data.get("teams", [])
    if not teams:
        # Defensive fallback: no teams tagged for that season yet (e.g.
        # queried too far ahead of the league confirming next season's
        # roster) - fall back to the league's currently active teams.
        url = f"{STATS_API_TEAMS}?sportId={SPORT_ID}&leagueId={LEAGUE_ID}&activeStatus=Active"
        data = fetch_json(url)
        teams = data.get("teams", [])
    return url, teams


def build_real_standings(records):
    """
    Flatten the Stats API's standings "records" (grouped by
    standingsType, usually just one "regularSeason" group for the ABL)
    into a single ranked list.
    """
    rows = []
    for record_group in records:
        for tr in record_group.get("teamRecords", []):
            team_id = tr.get("team", {}).get("id")
            short_name = tr.get("team", {}).get("name", "")
            rows.append(
                {
                    "rank": int(tr.get("leagueRank", 0)) if str(tr.get("leagueRank", "")).isdigit() else None,
                    "team": team_full_name(team_id, short_name),
                    "logo": team_logo(team_id),
                    "wins": tr.get("wins", 0),
                    "losses": tr.get("losses", 0),
                    "pct": tr.get("winningPercentage", ".000"),
                    "games_back": tr.get("leagueGamesBack", "-"),
                    "streak": tr.get("streak", {}).get("streakCode", "-"),
                    "runs_scored": tr.get("runsScored", 0),
                    "runs_allowed": tr.get("runsAllowed", 0),
                    "run_differential": tr.get("runDifferential", 0),
                }
            )

    # Sort by rank when the API provided one, otherwise by win pct.
    if all(row["rank"] is not None for row in rows):
        rows.sort(key=lambda row: row["rank"])
    else:
        rows.sort(key=lambda row: float(row.get("pct") or 0), reverse=True)
        for i, row in enumerate(rows, start=1):
            row["rank"] = i

    return rows


def build_placeholder_standings(teams):
    """
    Every rostered team at 0, alphabetical by full team name, used when
    the Stats API has no standings records yet (season not started).
    """
    named = []
    for team in teams:
        team_id = team.get("id")
        full_name = team_full_name(team_id, team.get("name", ""))
        named.append((full_name, team_id))

    named.sort(key=lambda pair: pair[0])

    rows = []
    for rank, (full_name, team_id) in enumerate(named, start=1):
        rows.append(
            {
                "rank": rank,
                "team": full_name,
                "logo": team_logo(team_id),
                "wins": 0,
                "losses": 0,
                "pct": ".000",
                "games_back": "-",
                "streak": "-",
                "runs_scored": 0,
                "runs_allowed": 0,
                "run_differential": 0,
            }
        )
    return rows


def main():
    today = datetime.now(timezone.utc).date()
    season = abl_season_for_date(today)

    try:
        standings_url, standings_data = fetch_standings(season)
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        raise SystemExit(f"Failed to fetch ABL standings from Stats API: {exc}")

    records = standings_data.get("records", [])
    has_real_standings = any(record_group.get("teamRecords") for record_group in records)

    if has_real_standings:
        standings = build_real_standings(records)
        placeholder = False
        source_url = standings_url
    else:
        try:
            teams_url, teams = fetch_active_teams(season)
        except (urllib.error.URLError, urllib.error.HTTPError) as exc:
            raise SystemExit(f"Failed to fetch ABL team list from Stats API: {exc}")
        standings = build_placeholder_standings(teams)
        placeholder = True
        source_url = f"{standings_url} | {teams_url}"

    output = {
        "bijgewerkt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bron": source_url,
        "season": season,
        "placeholder": placeholder,
        "standings": standings,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(
        f"Wrote {OUTPUT_FILE}: {len(standings)} team row(s), "
        f"season {season}, placeholder={placeholder}."
    )


if __name__ == "__main__":
    main()
