#!/usr/bin/env python3
"""
morning_report.py - Production script for GitHub Actions.

Pulls pitcher data, formats a Discord message, and POSTs it to a webhook.
All secrets come from environment variables (GitHub Actions secrets).

Required env vars:
  DISCORD_WEBHOOK_URL  — copied from Discord channel > Integrations > Webhooks

Optional env vars (Phase 2 — ESPN integration, not yet used):
  ESPN_S2, ESPN_SWID, ESPN_LEAGUE_ID

Run locally to test Discord formatting:
  set DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
  python morning_report.py
  python morning_report.py 2025-06-20   # test with a past date
"""

import os
import sys
from datetime import date
from io import StringIO

import pandas as pd
import requests

# -- Scoring (The Yastrzemski Legacy) -----------------------------------------
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

FIP_CONSTANT = 3.17
WIN_PROB     = 0.33
LOSS_PROB    = 0.15


# -- Data pulls (same logic as spike.py) --------------------------------------

def get_season_stats(season=2025, min_ip=20.0):
    r = requests.get(
        'https://statsapi.mlb.com/api/v1/stats',
        params={
            'stats': 'season', 'group': 'pitching',
            'season': season, 'playerPool': 'all',
            'sportId': 1, 'limit': 1000,
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
            'name': person.get('fullName'), 'mlbam_id': person.get('id'),
            'GS': gs, 'IP': ip, 'SO': so, 'BB': bb,
            'H': h, 'ER': er, 'HR': hr, 'W': w, 'L': l,
            'ERA': float(stat.get('era', 0) or 0),
            'WHIP': float(stat.get('whip', 0) or 0),
            'FIP': fip,
        })

    return pd.DataFrame(rows)


def get_savant_stats(season=2025, min_pa=100):
    url = (
        f"https://baseballsavant.mlb.com/leaderboard/expected_statistics"
        f"?type=pitcher&year={season}&position=&team=&min={min_pa}&csv=true"
    )
    r = requests.get(url, timeout=15, headers={'User-Agent': 'Mozilla/5.0'})
    r.raise_for_status()

    df = pd.read_csv(StringIO(r.text))
    df = df.rename(columns={'player_id': 'mlbam_id', 'xera': 'xERA'})
    keep = ['mlbam_id', 'xERA']
    return df[[c for c in keep if c in df.columns]]


def get_team_abbrevs(season=2025):
    r = requests.get(
        'https://statsapi.mlb.com/api/v1/teams',
        params={'sportId': 1, 'season': season},
        timeout=10,
    )
    r.raise_for_status()
    return {t['id']: t.get('abbreviation', t['name']) for t in r.json().get('teams', [])}


def get_probables(game_date=None, team_abbrevs=None):
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
                    'name': p['fullName'], 'mlbam_id': p['id'],
                    'team': abbrev(game['teams'][side]),
                    'opponent': abbrev(game['teams'][opp]),
                    'home': side == 'home',
                })
    return starters


# -- Scoring projection -------------------------------------------------------

def project_start(row):
    ip_total = float(row.get('IP', 0) or 0)
    gs       = max(int(row.get('GS', 1) or 1), 1)
    ip_start = min(ip_total / gs, 7.0)

    if ip_total == 0:
        return 0.0

    def rate(col):
        return float(row.get(col, 0) or 0) / ip_total

    return round(
        ip_start              * SCORING['IP'] +
        rate('SO') * ip_start * SCORING['SO'] +
        rate('H')  * ip_start * SCORING['H']  +
        rate('BB') * ip_start * SCORING['BB'] +
        rate('ER') * ip_start * SCORING['ER'] +
        WIN_PROB              * SCORING['W']   +
        LOSS_PROB             * SCORING['L'],
        1
    )


# -- Discord formatting -------------------------------------------------------

def format_discord(df, game_date=None):
    """Returns a list of message strings, each under 1900 chars."""
    label = date.today().strftime('%A %b %d') if not game_date else game_date

    df = df.copy()
    has_stats = df['IP'].notna()
    df.loc[has_stats, 'proj_pts'] = df[has_stats].apply(project_start, axis=1)
    df = df.sort_values('proj_pts', ascending=False, na_position='last')

    col_header = (
        f"{'PITCHER':<22} {'MATCHUP':<13} {'PROJ':>6}  {'ERA':>4}  {'FIP':>4}  {'xERA':>4}\n"
        f"{'-'*22} {'-'*13} {'-'*6}  {'-'*4}  {'-'*4}  {'-'*4}\n"
    )
    title   = f"⚾ **The Yastrzemski Legacy** — {label}\n```\n{col_header}"
    footer  = "```\n*IP×+3  K×+1  ER×-2  H×-1  BB×-1  W×+2  L×-2*  🟢≥14  🟡9-13  🔴<9"

    def fmt(v, d=2):
        return f"{v:.{d}f}" if pd.notna(v) and v == v else "  --"

    def row_line(row):
        venue    = "vs" if row['home'] else " @"
        matchup  = f"{row['team']} {venue} {row['opponent']}"
        proj     = row.get('proj_pts')
        icon     = ("🟢" if pd.notna(proj) and proj >= 14 else
                    "🟡" if pd.notna(proj) and proj >= 9  else
                    "🔴" if pd.notna(proj)                else "⬜")
        proj_str = f"+{fmt(proj,1)}" if pd.notna(proj) else "   --"
        return (
            f"{icon}{row['name']:<21} {matchup:<13} "
            f"{proj_str:>6}  {fmt(row.get('ERA')):>4}  "
            f"{fmt(row.get('FIP')):>4}  {fmt(row.get('xERA')):>4}\n"
        )

    lines   = [row_line(r) for _, r in df.iterrows()]
    no_data = df[df['IP'].isna()]['name'].tolist()
    warn    = f"\n⚠ No season data (rookie/call-up): {', '.join(no_data)}" if no_data else ""

    messages, chunk = [], title
    for line in lines:
        if len(chunk) + len(line) + len(footer) + len(warn) > 1900:
            messages.append(chunk + footer)
            chunk = "```\n"
        chunk += line
    messages.append(chunk + footer + warn)

    return messages


# -- Discord POST -------------------------------------------------------------

def post_to_discord(messages, webhook_url):
    for msg in messages:
        r = requests.post(webhook_url, json={'content': msg}, timeout=10)
        r.raise_for_status()


# -- Main ---------------------------------------------------------------------

if __name__ == '__main__':
    game_date = sys.argv[1] if len(sys.argv) > 1 else None
    target    = game_date or date.today().strftime('%Y-%m-%d')

    season = int(target[:4])

    print(f"Pulling SP season stats ({season})...")
    stats = get_season_stats(season=season)
    print(f"  Got {len(stats)} qualified SPs.")

    print("Pulling Statcast xERA...")
    try:
        savant = get_savant_stats(season=season)
        stats  = stats.merge(savant, on='mlbam_id', how='left')
        print(f"  Merged xERA for {stats['xERA'].notna().sum()}/{len(stats)} pitchers.")
    except Exception as e:
        print(f"  WARNING: Savant fetch failed ({e}) — continuing without xERA.")

    print(f"Pulling probable starters for {target}...")
    team_abbrevs = get_team_abbrevs(season=season)
    probables    = get_probables(target, team_abbrevs)
    print(f"  Found {len(probables)} probable starters.")

    if not probables:
        print("No games today — nothing to post.")
        sys.exit(0)

    prob_df = pd.DataFrame(probables)
    merged  = prob_df.merge(stats, on='mlbam_id', how='left', suffixes=('', '_season'))
    print(f"  Matched {merged['IP'].notna().sum()}/{len(merged)} pitchers to season data.")

    webhook = os.environ.get('DISCORD_WEBHOOK_URL')
    if not webhook:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        print("\nDISCORD_WEBHOOK_URL not set — printing to console instead:\n")
        for msg in format_discord(merged, game_date):
            print(msg)
        sys.exit(0)

    print("Posting to Discord...")
    msgs = format_discord(merged, game_date)
    post_to_discord(msgs, webhook)
    print(f"Done — sent {len(msgs)} message(s).")
