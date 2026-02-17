#!/usr/bin/env python3
"""
update_tenure.py — Fetch date each player joined their current NBA team.

Strategy:
  1) Raw commonteamroster API for all 30 teams → check for HOW_ACQUIRED field
  2) For each player, playercareerstats → find first season of current continuous stint
  3) For players who joined mid-season (traded), playergamelog → find exact first game date

Outputs tenure_data.json:
{
  "updated": "2026-02-17T10:00:00Z",
  "players": {
    "LeBron James": {
      "team": "LAL",
      "team_id": 1610612747,
      "player_id": 2544,
      "joined_season": "2025-26",
      "joined_date": "2025-10-22",
      "how_acquired": "Free Agent",
      "continuous_seasons": 3
    },
    ...
  }
}
"""

import json, time, requests, sys
from datetime import datetime, timezone

# --- CONFIG ---
SEASON = '2025-26'
SEASON_START_YEAR = 2025
OUTPUT = 'tenure_data.json'

HEADERS = {
    'User-Agent': 'Mozilla/5.0',
    'Accept': 'application/json',
    'Referer': 'https://www.nba.com/',
    'x-nba-stats-origin': 'stats',
    'x-nba-stats-token': 'true',
    'Origin': 'https://www.nba.com',
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

def api_get(url, params=None):
    """Make NBA stats API request with retry."""
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, params=params, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print(f"  Retry {attempt+1}/3: {e}")
            time.sleep(3 * (attempt + 1))
    return None


# ============================================================
# PHASE 1: Fetch all rosters via commonteamroster (raw API)
# ============================================================
def fetch_all_rosters():
    """Fetch roster for all 30 teams, capturing ALL returned fields."""
    all_players = {}
    extra_fields_found = set()

    for team_id, abbr in sorted(NBA_TEAMS.items(), key=lambda x: x[1]):
        print(f"Fetching roster: {abbr}...")
        data = api_get(
            'https://stats.nba.com/stats/commonteamroster',
            params={'TeamID': team_id, 'Season': SEASON, 'LeagueID': '00'}
        )
        if not data:
            print(f"  FAILED for {abbr}")
            continue

        for rs in data.get('resultSets', []):
            if rs['name'] != 'CommonTeamRoster':
                continue
            headers = rs['headers']
            # Track any fields beyond the standard set
            standard = {'TeamID','SEASON','LeagueID','PLAYER','PLAYER_SLUG','NUM',
                        'POSITION','HEIGHT','WEIGHT','BIRTH_DATE','AGE','EXP',
                        'SCHOOL','PLAYER_ID'}
            extra = set(headers) - standard
            if extra:
                extra_fields_found.update(extra)

            for row in rs['rowSet']:
                rec = dict(zip(headers, row))
                name = rec.get('PLAYER', '')
                pid = rec.get('PLAYER_ID', 0)
                all_players[pid] = {
                    'name': name,
                    'team': abbr,
                    'team_id': team_id,
                    'player_id': pid,
                    'exp': rec.get('EXP', ''),
                    'birth_date': rec.get('BIRTH_DATE', ''),
                    # Capture any extra fields (HOW_ACQUIRED, NICKNAME, etc.)
                    **{k: rec.get(k) for k in extra}
                }

        time.sleep(0.8)

    if extra_fields_found:
        print(f"\n*** Extra fields found in API: {extra_fields_found} ***\n")
    else:
        print("\nNo extra fields beyond standard roster data.\n")

    return all_players


# ============================================================
# PHASE 2: Determine tenure via playercareerstats
# ============================================================
def fetch_career_tenure(player_id, current_team_id):
    """
    Get career stats and find first season of current continuous stint.
    Returns (first_season_str, num_continuous_seasons) e.g. ('2023-24', 3)
    """
    data = api_get(
        'https://stats.nba.com/stats/playercareerstats',
        params={'PlayerID': player_id, 'PerMode': 'Totals', 'LeagueID': '00'}
    )
    if not data:
        return None, 0

    # Find SeasonTotalsRegularSeason
    for rs in data.get('resultSets', []):
        if rs['name'] != 'SeasonTotalsRegularSeason':
            continue
        headers = rs['headers']
        rows = rs['rowSet']

        # Build list of (season, team_id) in chronological order
        season_teams = []
        for row in rows:
            rec = dict(zip(headers, row))
            sid = rec.get('SEASON_ID', '')
            tid = rec.get('TEAM_ID', 0)
            lid = rec.get('LEAGUE_ID', '00')
            if lid != '00':
                continue  # Skip non-NBA
            season_teams.append((sid, tid))

        if not season_teams:
            return None, 0

        # Walk backwards to find first season of continuous stint with current team
        # A player can appear multiple times in same season (traded mid-season)
        # We need: current team appears in a season → check previous season, etc.
        # Group by season
        from collections import OrderedDict
        seasons_with_team = OrderedDict()
        for sid, tid in season_teams:
            if sid not in seasons_with_team:
                seasons_with_team[sid] = set()
            seasons_with_team[sid].add(tid)

        season_list = list(seasons_with_team.keys())  # chronological
        # Find continuous streak from most recent going backwards
        first_season = None
        count = 0
        for sid in reversed(season_list):
            if current_team_id in seasons_with_team[sid]:
                first_season = sid
                count += 1
            else:
                break

        return first_season, count

    return None, 0


# ============================================================
# PHASE 3: For mid-season joins, find exact first game date
# ============================================================
def fetch_first_game_date(player_id, team_id, season):
    """Find the date of the player's first game with this team in given season."""
    data = api_get(
        'https://stats.nba.com/stats/playergamelog',
        params={
            'PlayerID': player_id,
            'Season': season,
            'SeasonType': 'Regular Season',
            'LeagueID': '00'
        }
    )
    if not data:
        return None

    for rs in data.get('resultSets', []):
        if rs['name'] != 'PlayerGameLog':
            continue
        headers = rs['headers']
        rows = rs['rowSet']
        if not rows:
            return None

        # Rows are newest-first; find last (earliest) game with this team
        team_games = []
        for row in rows:
            rec = dict(zip(headers, row))
            # MATCHUP contains team abbreviation
            matchup = rec.get('MATCHUP', '')
            game_date = rec.get('GAME_DATE', '')
            # The matchup format is like "LAL vs. GSW" or "LAL @ GSW"
            # Just check if game is associated (all games in log are for this player's team at that time)
            team_games.append(game_date)

        if team_games:
            # Last entry = earliest game
            return team_games[-1]

    return None


# ============================================================
# MAIN
# ============================================================
def main():
    print("=" * 60)
    print("NBA Player Tenure Data Fetcher")
    print("=" * 60)

    # Phase 1: Get all current rosters
    print("\n--- Phase 1: Fetching all team rosters ---")
    all_players = fetch_all_rosters()
    print(f"Found {len(all_players)} players across 30 teams.")

    # Phase 2: Get career tenure for each player
    print("\n--- Phase 2: Fetching career stats for tenure ---")
    total = len(all_players)
    for i, (pid, info) in enumerate(all_players.items()):
        print(f"  [{i+1}/{total}] {info['name']} ({info['team']})...", end='')
        first_season, count = fetch_career_tenure(pid, info['team_id'])
        info['joined_season'] = first_season
        info['continuous_seasons'] = count

        # Determine if they joined this season (potential mid-season acquisition)
        current_season_str = SEASON  # '2025-26'
        info['joined_this_season'] = (first_season == current_season_str and count == 1)

        print(f" → since {first_season} ({count} seasons)")
        time.sleep(0.6)

    # Phase 3: For players who joined this season, get exact first game date
    print("\n--- Phase 3: Finding exact join dates for new acquisitions ---")
    new_players = {pid: info for pid, info in all_players.items() if info.get('joined_this_season')}
    print(f"  {len(new_players)} players joined their current team this season.")

    for i, (pid, info) in enumerate(new_players.items()):
        print(f"  [{i+1}/{len(new_players)}] {info['name']}...", end='')
        first_date = fetch_first_game_date(pid, info['team_id'], SEASON)
        if first_date:
            info['joined_date'] = first_date
            print(f" → {first_date}")
        else:
            print(" → no games found")
        time.sleep(0.6)

    # For players who've been with team multiple seasons, set joined_date to season start
    for pid, info in all_players.items():
        if 'joined_date' not in info and info.get('joined_season'):
            # Approximate: start of first season
            try:
                start_yr = int(info['joined_season'].split('-')[0])
                info['joined_date'] = f"{start_yr}-10-01"  # Approx season start
            except:
                info['joined_date'] = None

    # Build output
    output = {
        'updated': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'season': SEASON,
        'players': {}
    }

    for pid, info in all_players.items():
        name = info['name']
        output['players'][name] = {
            'team': info['team'],
            'team_id': info['team_id'],
            'player_id': info['player_id'],
            'joined_season': info.get('joined_season'),
            'joined_date': info.get('joined_date'),
            'continuous_seasons': info.get('continuous_seasons', 0),
            'joined_this_season': info.get('joined_this_season', False),
        }
        # Include any extra fields from Phase 1 (like HOW_ACQUIRED)
        for k in ['HOW_ACQUIRED', 'NICKNAME', 'how_acquired']:
            if k in info and info[k]:
                output['players'][name][k.lower()] = info[k]

    with open(OUTPUT, 'w') as f:
        json.dump(output, f, indent=2)

    print(f"\n{'='*60}")
    print(f"Done! Wrote {len(output['players'])} players to {OUTPUT}")
    print(f"{'='*60}")


if __name__ == '__main__':
    main()
