#!/usr/bin/env python3
"""
Fetch NBA player stats and evaluate Starter Criteria for QO adjustments.
Outputs sc_data.json with SC status for all relevant players.

SC Rules (CBA Article XI):
- Season 4 only: 41+ GS OR 2000+ MIN
- Average of seasons 3+4: avg 41+ GS OR avg 2000+ MIN
Meeting EITHER test = SC met.
"""

import json
import time
import sys
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from datetime import datetime

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.nba.com/",
    "Accept": "application/json",
}

def fetch_nba_stats(season: str) -> list[dict]:
    """Fetch player totals for a given season from NBA stats API."""
    url = (
        f"https://stats.nba.com/stats/leaguedashplayerstats"
        f"?Season={season}&SeasonType=Regular+Season&PerMode=Totals&MeasureType=Base"
    )
    req = Request(url, headers=HEADERS)
    try:
        with urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except (URLError, HTTPError) as e:
        print(f"Error fetching {season}: {e}", file=sys.stderr)
        return []

    rs = data["resultSets"][0]
    headers = rs["headers"]
    rows = rs["rowSet"]

    name_i = headers.index("PLAYER_NAME")
    gs_i = headers.index("GS")
    min_i = headers.index("MIN")
    gp_i = headers.index("GP")

    players = []
    for r in rows:
        players.append({
            "name": r[name_i],
            "gp": int(r[gp_i]),
            "gs": int(r[gs_i]),
            "min": float(r[min_i]),
        })
    return players


def evaluate_sc(curr: dict, prev: dict | None) -> dict:
    """
    Evaluate Starter Criteria.
    curr = season 4 stats, prev = season 3 stats (may be None).
    Returns { met: bool, gs4, min4, gs3, min3, reason }
    """
    gs4 = curr.get("gs", 0)
    min4 = curr.get("min", 0)
    gs3 = prev.get("gs", 0) if prev else 0
    min3 = prev.get("min", 0) if prev else 0

    # Test 1: Season 4 alone
    s4_gs = gs4 >= 41
    s4_min = min4 >= 2000

    # Test 2: Average of seasons 3+4
    avg_gs = (gs3 + gs4) / 2 if prev else gs4
    avg_min = (min3 + min4) / 2 if prev else min4
    avg_gs_met = avg_gs >= 41
    avg_min_met = avg_min >= 2000

    met = s4_gs or s4_min or avg_gs_met or avg_min_met

    reasons = []
    if s4_gs: reasons.append(f"S4 GS={gs4}≥41")
    if s4_min: reasons.append(f"S4 MIN={min4:.0f}≥2000")
    if avg_gs_met and not s4_gs: reasons.append(f"Avg GS={avg_gs:.1f}≥41")
    if avg_min_met and not s4_min: reasons.append(f"Avg MIN={avg_min:.0f}≥2000")
    if not reasons:
        reasons.append(f"S4 GS={gs4}, MIN={min4:.0f}")

    return {
        "met": met,
        "gs4": gs4,
        "min4": round(min4),
        "gs3": gs3,
        "min3": round(min3),
        "reason": "; ".join(reasons),
    }


def main():
    # Determine current and previous season strings
    now = datetime.now()
    # NBA season spans Oct-Jun; if month >= 10, we're in the new season
    if now.month >= 10:
        curr_end = now.year + 1
    else:
        curr_end = now.year
    curr_season = f"{curr_end - 1}-{str(curr_end)[2:]}"  # e.g. "2025-26"
    prev_season = f"{curr_end - 2}-{str(curr_end - 1)[2:]}"  # e.g. "2024-25"

    print(f"Fetching current season: {curr_season}")
    curr_stats = fetch_nba_stats(curr_season)
    print(f"  Got {len(curr_stats)} players")

    time.sleep(1)  # Rate limit courtesy

    print(f"Fetching previous season: {prev_season}")
    prev_stats = fetch_nba_stats(prev_season)
    print(f"  Got {len(prev_stats)} players")

    if not curr_stats:
        print("No current season data, aborting.", file=sys.stderr)
        sys.exit(1)

    # Build lookup by name
    prev_by_name = {p["name"]: p for p in prev_stats}

    # Evaluate SC for all current players
    sc_data = {}
    for p in curr_stats:
        prev = prev_by_name.get(p["name"])
        sc = evaluate_sc(p, prev)
        sc_data[p["name"]] = sc

    # Write output
    output = {
        "updated": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "current_season": curr_season,
        "previous_season": prev_season,
        "players": sc_data,
    }

    with open("sc_data.json", "w") as f:
        json.dump(output, f, indent=2)

    # Summary
    met_count = sum(1 for v in sc_data.values() if v["met"])
    print(f"\nSC evaluated: {len(sc_data)} players, {met_count} meet criteria")
    print("Written to sc_data.json")


if __name__ == "__main__":
    main()
