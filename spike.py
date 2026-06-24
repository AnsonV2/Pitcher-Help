#!/usr/bin/env python3
"""
spike.py - Local data pipeline test. No Discord, no hosting.
Proves the data pulls and joins work before building anything else.

Uses MLB Stats API for both season stats and probables - same source,
so MLBAM IDs match natively with no translation layer needed.
FanGraphs skipped entirely (403 blocked via pybaseball).

Run:  python spike.py
Test a specific date:  python spike.py 2025-06-20
"""

import sys
from datetime import date
from io import StringIO

import pandas as pd
import requests

# -- Your exact scoring (The Yastrzemski Legacy) -------------------------------
SCORING = {
    'IP':  3.0,
    'SO':  1.0,
    'W':   2.0,
    'SV':  5.0,
    'HD':  2.0,
    'H':  -1.0,
    'BB': -1.0,
    'ER': -2.0,
    'L':  -2.0,
}

# FIP constant varies slightly by year; 2025 estimate
FIP_CONSTANT = 3.17

# Rough SP win/loss rates (league average)
WIN_PROB  = 0.33
LOSS_PROB = 0.15


# -- Step 1: Pull pitcher season stats from MLB Stats API ---------------------

def get_season_stats(season=2025, min_ip=20.0):
    """
    MLB Stats API - free, no key, MLBAM IDs native.
    Returns SP-eligible pitchers with counting stats + computed FIP.
    """
    r = requests.get(
        'https://statsapi.mlb.com/api/v1/stats',
        params={
            'stats':      'season',
            'group':      'pitching',
            'season':     season,
            'playerPool': 'all',
            'sportId':    1,
            'limit':      1000,
        },
        timeout=15,
    )
    r.raise_for_status()

    rows = []
    for split in r.json().get('stats', [{}])[0].get('splits', []):
        stat   = split.get('stat', {})
        person = split.get('player', {})

        ip = float(stat.get('inningsPitched', 0) or 0)
        gs = int(stat.get('gamesStarted', 0) or 0)
        if ip < min_ip or gs == 0:
            continue

        so = int(stat.get('strikeOuts', 0) or 0)
        bb = int(stat.get('baseOnBalls', 0) or 0)
        h  = int(stat.get('hits', 0) or 0)
        er = int(stat.get('earnedRuns', 0) or 0)
        hr = int(stat.get('homeRuns', 0) or 0)
        w  = int(stat.get('wins', 0) or 0)
        l  = int(stat.get('losses', 0) or 0)

        fip = round((13 * hr + 3 * bb - 2 * so) / ip + FIP_CONSTANT, 2) if ip > 0 else None

        rows.append({
            'name':      person.get('fullName'),
            'mlbam_id':  person.get('id'),
            'GS': gs,  'IP': ip,
            'SO': so,  'BB': bb,
            'H':  h,   'ER': er,
            'HR': hr,  'W':  w,   'L': l,
            'ERA':  float(stat.get('era', 0) or 0),
            'WHIP': float(stat.get('whip', 0) or 0),
            'FIP':  fip,
        })

    return pd.DataFrame(rows)


# -- Step 2: Pull advanced metrics from Baseball Savant (optional) ------------

def get_savant_stats(season=2025, min_pa=100):
    """
    Baseball Savant expected stats leaderboard - free CSV endpoint.
    Adds xERA, hard-hit%, barrel% against. Player IDs are MLBAM native.
    Returns empty DataFrame on failure so the rest of the script still runs.
    """
    url = (
        f"https://baseballsavant.mlb.com/leaderboard/expected_statistics"
        f"?type=pitcher&year={season}&position=&team=&min={min_pa}&csv=true"
    )
    r = requests.get(url, timeout=15, headers={'User-Agent': 'Mozilla/5.0'})
    r.raise_for_status()

    df = pd.read_csv(StringIO(r.text))
    df = df.rename(columns={
        'player_id':          'mlbam_id',
        'xera':               'xERA',
        'exit_velocity_avg':  'exit_velo',
        'barrel_batted_rate': 'barrel_pct',
        'hard_hit_percent':   'hard_hit_pct',
    })
    keep = ['mlbam_id', 'xERA', 'exit_velo', 'barrel_pct', 'hard_hit_pct']
    return df[[c for c in keep if c in df.columns]]


# -- Team ID -> abbreviation lookup ------------------------------------------

def get_team_abbrevs(season=2026):
    """Build a dict of {team_id: abbreviation} from the MLB teams endpoint."""
    r = requests.get(
        'https://statsapi.mlb.com/api/v1/teams',
        params={'sportId': 1, 'season': season},
        timeout=10,
    )
    r.raise_for_status()
    return {t['id']: t.get('abbreviation', t['name']) for t in r.json().get('teams', [])}


# -- Step 3: Pull today's probable starters -----------------------------------

def get_probables(game_date=None, team_abbrevs=None):
    """MLB Stats API - same source, same MLBAM IDs, no translation needed."""
    if game_date is None:
        game_date = date.today().strftime('%Y-%m-%d')
    if team_abbrevs is None:
        team_abbrevs = {}

    r = requests.get(
        'https://statsapi.mlb.com/api/v1/schedule',
        params={'sportId': 1, 'date': game_date, 'hydrate': 'probablePitcher'},
        timeout=10,
    )
    r.raise_for_status()

    def abbrev(team_dict):
        t = team_dict.get('team', {})
        return team_abbrevs.get(t.get('id')) or t.get('name', '???')[:3].upper()

    starters = []
    for date_entry in r.json().get('dates', []):
        for game in date_entry.get('games', []):
            for side in ('home', 'away'):
                p = game['teams'][side].get('probablePitcher')
                if not p:
                    continue
                opp = 'away' if side == 'home' else 'home'
                starters.append({
                    'name':     p['fullName'],
                    'mlbam_id': p['id'],
                    'team':     abbrev(game['teams'][side]),
                    'opponent': abbrev(game['teams'][opp]),
                    'home':     side == 'home',
                })
    return starters


# -- Step 4: Project points for a typical start -------------------------------

def project_start(row):
    ip_total = float(row.get('IP', 0) or 0)
    gs       = max(int(row.get('GS', 1) or 1), 1)
    ip_start = min(ip_total / gs, 7.0)

    if ip_total == 0:
        return 0.0

    def rate(col):
        return float(row.get(col, 0) or 0) / ip_total

    pts = (
        ip_start              * SCORING['IP'] +
        rate('SO') * ip_start * SCORING['SO'] +
        rate('H')  * ip_start * SCORING['H']  +
        rate('BB') * ip_start * SCORING['BB'] +
        rate('ER') * ip_start * SCORING['ER'] +
        WIN_PROB              * SCORING['W']   +
        LOSS_PROB             * SCORING['L']
    )
    return round(pts, 1)


# -- Step 5: Print the report -------------------------------------------------

def print_report(df, game_date=None):
    label = date.today().strftime('%A, %B %d %Y') if not game_date else game_date

    print(f"\n{'='*72}")
    print(f"  The Yastrzemski Legacy -- Morning Report")
    print(f"  {label}")
    print(f"{'='*72}\n")

    if df.empty:
        print("  No probable starters found.")
        return

    df = df.copy()
    has_stats = df['IP'].notna()
    df.loc[has_stats, 'proj_pts'] = df[has_stats].apply(project_start, axis=1)
    df = df.sort_values('proj_pts', ascending=False, na_position='last')

    print(f"  {'PITCHER':<23} {'MATCHUP':<13} {'PROJ':>6}  {'ERA':>5}  {'FIP':>5}  {'xERA':>5}  {'WHIP':>5}")
    print(f"  {'-'*23} {'-'*13} {'-'*6}  {'-'*5}  {'-'*5}  {'-'*5}  {'-'*5}")

    for _, row in df.iterrows():
        venue   = "vs" if row['home'] else "@"
        matchup = f"{row['team']} {venue} {row['opponent']}"
        proj    = row.get('proj_pts')

        flag = ("G " if pd.notna(proj) and proj >= 14 else
                "Y " if pd.notna(proj) and proj >= 9  else
                "R " if pd.notna(proj)                else "- ")

        def fmt(v, d=2):
            return f"{v:.{d}f}" if pd.notna(v) else "  ---"

        proj_str = (f"+{fmt(proj, 1)}") if pd.notna(proj) else "   ---"

        print(
            f"  [{flag}] {row['name']:<21} {matchup:<13} "
            f"{proj_str:>6}  "
            f"{fmt(row.get('ERA')):>5}  "
            f"{fmt(row.get('FIP')):>5}  "
            f"{fmt(row.get('xERA')):>5}  "
            f"{fmt(row.get('WHIP')):>5}"
        )

    no_data = df[df['IP'].isna()]
    if not no_data.empty:
        print(f"\n  WARNING: No season data for: {', '.join(no_data['name'].tolist())}")
        print(f"  (Rookie, call-up, or hasn't reached 20 IP yet)")

    print(f"\n  Scoring: IP x3 | K x+1 | ER x-2 | H x-1 | BB x-1 | W x+2 | L x-2")
    print(f"  [G] >= 14 pts  [Y] 9-13 pts  [R] < 9 pts  [-] no data\n")


# -- Main ---------------------------------------------------------------------

if __name__ == '__main__':
    game_date = sys.argv[1] if len(sys.argv) > 1 else None
    target    = game_date or date.today().strftime('%Y-%m-%d')

    print("Step 1/4 -- Pulling SP season stats from MLB Stats API...")
    stats = get_season_stats()
    print(f"           Got {len(stats)} qualified SPs.\n")

    print("Step 2/4 -- Pulling Statcast advanced metrics from Baseball Savant...")
    try:
        savant = get_savant_stats()
        stats  = stats.merge(savant, on='mlbam_id', how='left')
        print(f"           Merged xERA for {stats['xERA'].notna().sum()}/{len(stats)} pitchers.\n")
    except Exception as e:
        print(f"           WARNING: Savant fetch failed ({e}) -- continuing without xERA.\n")

    print(f"Step 3/4 -- Pulling probable starters for {target}...")
    team_abbrevs = get_team_abbrevs()
    probables = get_probables(game_date, team_abbrevs)
    print(f"           Found {len(probables)} probable starters.\n")

    if not probables:
        print("  No games scheduled. Try: python spike.py 2025-06-20")
        sys.exit(0)

    print("Step 4/4 -- Joining and building report...")
    prob_df = pd.DataFrame(probables)
    merged  = prob_df.merge(stats, on='mlbam_id', how='left', suffixes=('', '_season'))

    matched = merged['IP'].notna().sum()
    print(f"           Matched {matched}/{len(merged)} pitchers to season data.\n")

    print_report(merged, game_date)
