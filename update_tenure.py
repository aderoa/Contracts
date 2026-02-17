#!/usr/bin/env python3
"""
update_tenure.py — Fetch date each player joined their current NBA team.

FAST VERSION: Uses bulk leaguedashplayerstats (1 call per season) instead of
per-player career stats (~600 calls). Total: ~20 API calls, runs in ~2 minutes.

Strategy:
  1. Fetch current season stats → all active players + their current team
  2. Walk backwards season by season (bulk call each) to find when each
     player first appeared on their current team continuously
  3. Output tenure_data.json

"""

import json, time, requests, sys
from datetime import datetime, timezone

# --- CONFIG ---
CURRENT_SEASON = '2025-26'
MAX_LOOKBACK = 22  # LeBron's been in the league 23 seasons, cover edge cases
OUTPUT = 'tenure_data.json'

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://www.nba.com/',
    'x-nba-stats-origin': 'stats',
    'x-nba-stats-token': 'true',
    'Origin': 'https://www.nba.com',
    'Connection': 'keep-alive',
}

NBA_TEAMS = {
    1610612737: 'ATL', 1610612738: 'BOS', 1610612751: 'BKN', 1610612766: 'CHA',
    1610612741: 'CHI', 1610612739: 'CLE', 1610612742: 'DAL', 1610612743: 'DEN',
    1610612765: 'DET', 1610612744: 'GSW', 1610612745: 'HOU', 1610612754: 'IND',
    1610612746: 'LAC', 1610612747: 'LAL', 1610612763: 'MEM', 1610612748: 'MIA',
    1610612749: 'MIL', 1610612750: 'MIN', 1610612740: 'NOP', 1610612752: 'NYK',
    1610612760: 'OKC', 1610612753: 'ORL', 1610612755: 'PHI', 1610612756: 'PHX',
    1610612757: 'POR', 1610612758: 'SAC', 1610612759: 'SAS', 1610612761: 'TOR',
    1610612762: 'UTA', 1610612764: 'WAS',
}


def season_str(start_year):
    """Convert start year to season string: 2025 → '2025-26'"""
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def prev_season(s):
    """'2025-26' → '2024-25'"""
    yr = int(s.split('-')[0])
    return season_str(yr - 1)


def api_get(url, params):
    """Make NBA stats API request with retry."""
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, params=params, timeout=45)
            if r.status_code == 200:
                return r.json()
            print(f"  HTTP {r.status_code}, retry {attempt+1}/3...")
        except Exception as e:
            print(f"  Error: {e}, retry {attempt+1}/3...")
        time.sleep(3 * (attempt + 1))
    return None


def fetch_season_players(season):
    """
    Fetch all players for a season via leaguedashplayerstats.
    Returns:
      - player_teams: dict player_id → set of team_ids they played for
      - player_names: dict player_id → name
    
    Players traded mid-season appear once per team.
    """
    print(f"  Fetching {season}...", end='', flush=True)
    data = api_get(
        'https://stats.nba.com/stats/leaguedashplayerstats',
        params={
            'Season': season,
            'SeasonType': 'Regular Season',
            'PerMode': 'Totals',
            'MeasureType': 'Base',
            'LeagueID': '00',
        }
    )
    if not data:
        print(" FAILED")
        return None, None

    player_teams = {}
    player_names = {}

    for rs in data.get('resultSets', []):
        if rs['name'] != 'LeagueDashPlayerStats':
            continue
        hdrs = rs['headers']
        pid_i = hdrs.index('PLAYER_ID')
        name_i = hdrs.index('PLAYER_NAME')
        tid_i = hdrs.index('TEAM_ID')

        for row in rs['rowSet']:
            pid = row[pid_i]
            tid = row[tid_i]
            name = row[name_i]
            if pid not in player_teams:
                player_teams[pid] = set()
            player_teams[pid].add(tid)
            player_names[pid] = name

    print(f" → {len(player_teams)} players")
    return player_teams, player_names


def fetch_current_roster():
    """
    Fetch current season player stats.
    Returns dict: player_id → {name, team, team_id, gp}
    """
    print(f"Fetching current season ({CURRENT_SEASON})...")
    data = api_get(
        'https://stats.nba.com/stats/leaguedashplayerstats',
        params={
            'Season': CURRENT_SEASON,
            'SeasonType': 'Regular Season',
            'PerMode': 'Totals',
            'MeasureType': 'Base',
            'LeagueID': '00',
        }
    )
    if not data:
        print("FATAL: Cannot fetch current season data")
        sys.exit(1)

    players = {}
    for rs in data.get('resultSets', []):
        if rs['name'] != 'LeagueDashPlayerStats':
            continue
        hdrs = rs['headers']
        pid_i = hdrs.index('PLAYER_ID')
        name_i = hdrs.index('PLAYER_NAME')
        tid_i = hdrs.index('TEAM_ID')
        abbr_i = hdrs.index('TEAM_ABBREVIATION')
        gp_i = hdrs.index('GP')

        for row in rs['rowSet']:
            pid = row[pid_i]
            tid = row[tid_i]
            name = row[name_i]
            abbr = row[abbr_i]
            gp = row[gp_i]

            # If player appears multiple times (traded mid-season),
            # keep entry with more GP (= primary/current team)
            if pid not in players or gp > players[pid]['gp']:
                players[pid] = {
                    'name': name,
                    'team': abbr,
                    'team_id': tid,
                    'gp': gp,
                }

    return players


def main():
    print("=" * 60)
    print("NBA Player Tenure Data Fetcher (Fast Bulk Version)")
    print("=" * 60)

    # Step 1: Get current rosters
    players = fetch_current_roster()
    print(f"Found {len(players)} active players.\n")

    # Initialize: everyone's tenure starts at current season
    tenure = {pid: CURRENT_SEASON for pid in players}
    # Unresolved = players we still need to check further back
    unresolved = set(players.keys())

    # Step 2: Walk backwards season by season
    print("Walking backwards through seasons...")
    season = CURRENT_SEASON
    seasons_checked = 0

    for i in range(MAX_LOOKBACK):
        if not unresolved:
            print("  All players resolved!")
            break

        season = prev_season(season)
        seasons_checked += 1

        past_teams, past_names = fetch_season_players(season)
        if past_teams is None:
            print(f"  Could not fetch {season}, stopping lookback.")
            break

        newly_resolved = set()

        for pid in unresolved:
            current_team_id = players[pid]['team_id']

            if pid not in past_teams:
                # Player wasn't in the league → tenure starts next season
                newly_resolved.add(pid)
            elif current_team_id not in past_teams[pid]:
                # Player was on a DIFFERENT team → tenure starts next season
                newly_resolved.add(pid)
            else:
                # Player was on same team → extend tenure back
                tenure[pid] = season

        unresolved -= newly_resolved
        if newly_resolved:
            print(f"    Resolved {len(newly_resolved)} players, {len(unresolved)} remaining")

        time.sleep(1.5)

    if unresolved:
        print(f"\n  {len(unresolved)} players with {MAX_LOOKBACK}+ year tenure:")
        for pid in unresolved:
            print(f"    - {players[pid]['name']}")

    # Step 3: Build output
    print(f"\n{'='*60}")
    print("Building tenure_data.json...")

    output = {
        'updated': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'season': CURRENT_SEASON,
        'seasons_checked': seasons_checked,
        'players': {}
    }

    for pid, info in players.items():
        joined = tenure[pid]
        joined_yr = int(joined.split('-')[0])
        current_yr = int(CURRENT_SEASON.split('-')[0])
        continuous = current_yr - joined_yr + 1

        output['players'][info['name']] = {
            'team': info['team'],
            'team_id': info['team_id'],
            'player_id': pid,
            'joined_season': joined,
            'joined_date': f"{joined_yr}-10-01",
            'continuous_seasons': continuous,
            'joined_this_season': (joined == CURRENT_SEASON),
        }

    # Sort by team, then tenure (longest first)
    output['players'] = dict(sorted(
        output['players'].items(),
        key=lambda x: (x[1]['team'], -x[1]['continuous_seasons'], x[0])
    ))

    with open(OUTPUT, 'w') as f:
        json.dump(output, f, indent=2)

    print(f"Wrote {len(output['players'])} players to {OUTPUT}")

    # Summary
    print(f"\nTop 10 longest-tenured players:")
    by_tenure = sorted(output['players'].items(), key=lambda x: -x[1]['continuous_seasons'])
    for name, info in by_tenure[:10]:
        print(f"  {name} ({info['team']}) — since {info['joined_season']} ({info['continuous_seasons']} seasons)")

    print(f"\n{'='*60}")
    print(f"Done! Total API calls: {seasons_checked + 1}")
    print(f"{'='*60}")


if __name__ == '__main__':
    main()
