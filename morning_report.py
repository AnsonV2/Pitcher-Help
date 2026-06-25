#!/usr/bin/env python3
"""
morning_report.py - Production script for GitHub Actions.

Pulls pitcher data, formats a Discord message, and POSTs it to a webhook.
All secrets come from environment variables (GitHub Actions secrets).

Required env vars:
  DISCORD_WEBHOOK_URL  — copied from Discord channel > Integrations > Webhooks

Phase 2 ESPN env vars (optional — enables roster/matchup sections):
  ESPN_S2          — espn_s2 cookie from browser DevTools
  ESPN_SWID        — SWID cookie from browser DevTools
  ESPN_LEAGUE_ID   — numeric league ID from the ESPN fantasy URL
  ESPN_TEAM_ID     — your team's number in the league (1-10)

Run locally to test Discord formatting:
  set DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
  python morning_report.py
  python morning_report.py 2025-06-20   # test with a past date
"""

import os
import re
import sys
import unicodedata
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
        if ip < min_ip:
            continue

        so = int(stat.get('strikeOuts', 0) or 0)
        bb = int(stat.get('baseOnBalls', 0) or 0)
        h  = int(stat.get('hits', 0) or 0)
        er = int(stat.get('earnedRuns', 0) or 0)
        hr = int(stat.get('homeRuns', 0) or 0)
        w  = int(stat.get('wins', 0) or 0)
        l  = int(stat.get('losses', 0) or 0)
        sv = int(stat.get('saves', 0) or 0)

        fip = round((13 * hr + 3 * bb - 2 * so) / ip + FIP_CONSTANT, 2) if ip > 0 else None
        k9  = round((so / ip) * 9, 1) if ip > 0 else None
        bb9 = round((bb / ip) * 9, 1) if ip > 0 else None

        rows.append({
            'name': person.get('fullName'), 'mlbam_id': person.get('id'),
            'GS': gs, 'IP': ip, 'SO': so, 'BB': bb,
            'H': h, 'ER': er, 'HR': hr, 'W': w, 'L': l, 'SV': sv,
            'ERA': float(stat.get('era', 0) or 0),
            'WHIP': float(stat.get('whip', 0) or 0),
            'FIP': fip, 'K9': k9, 'BB9': bb9,
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


# -- ESPN Integration (Phase 2) -----------------------------------------------

def _normalize(name):
    """Lowercase + strip accents + strip Jr/Sr/II suffixes for name matching."""
    nfd = unicodedata.normalize('NFD', name or '')
    ascii_name = ''.join(c for c in nfd if unicodedata.category(c) != 'Mn')
    clean = re.sub(r'\s+(Jr\.?|Sr\.?|II|III|IV)$', '', ascii_name, flags=re.IGNORECASE)
    return clean.lower().strip()


def get_espn_data(league_id, espn_s2, swid, team_id, season=2025):
    """
    Connect to ESPN Fantasy and return:
      my_names   — normalized pitcher names on my roster
      opp_names  — normalized pitcher names on this week's opponent's roster
      my_all     — list of raw names for all my roster pitchers (for no-start alert)

    Returns (set(), set(), []) on any ESPN failure so the report still runs.
    """
    try:
        from espn_api.baseball import League
    except ImportError:
        print("  WARNING: espn_api not installed — skipping ESPN integration.")
        return set(), set(), [], set()

    try:
        league   = League(league_id=int(league_id), year=season, espn_s2=espn_s2, swid=swid)
        my_team  = next((t for t in league.teams if t.team_id == int(team_id)), None)
        if my_team is None:
            print(f"  WARNING: ESPN team ID {team_id} not found — skipping ESPN integration.")
            return set(), set(), [], set()

        # espn_api may return 'SP', 'RP', 'P', or 'pitcher' depending on version
        pitcher_slots = {'SP', 'RP', 'P', 'pitcher'}

        def _pitchers(team):
            return [p for p in team.roster
                    if getattr(p, 'position', '').upper() in {'SP', 'RP', 'P', 'PITCHER'}
                    or 'P' in getattr(p, 'eligibleSlots', [])]

        my_pitchers = _pitchers(my_team)
        my_names    = {_normalize(p.name) for p in my_pitchers}
        my_all      = [p.name for p in my_pitchers]

        opp_names = set()
        try:
            period   = league.currentMatchupPeriod
            matchups = league.box_scores(matchup_period=period)
            opp_team = None
            for m in matchups:
                if getattr(m, 'home_team', None) and m.home_team.team_id == my_team.team_id:
                    opp_team = m.away_team
                    break
                if getattr(m, 'away_team', None) and m.away_team.team_id == my_team.team_id:
                    opp_team = m.home_team
                    break
            if opp_team:
                opp_names = {_normalize(p.name) for p in _pitchers(opp_team)}
        except Exception as e:
            print(f"  WARNING: Could not fetch matchup opponent ({e}).")

        fa_names = set()
        try:
            free_agents = league.free_agents(size=200)
            fa_names = {_normalize(p.name) for p in free_agents
                        if getattr(p, 'position', '') in pitcher_slots}
        except Exception as e:
            print(f"  WARNING: Could not fetch free agents ({e}) — wire section unfiltered.")

        return my_names, opp_names, my_all, fa_names

    except Exception as e:
        print(f"  WARNING: ESPN fetch failed ({e}) — continuing without roster data.")
        return set(), set(), [], set()


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

def _closer_section(all_stats, fa_names):
    """Returns a formatted string for wire closers meeting quality thresholds."""
    if all_stats is None or not fa_names:
        return ""
    tmp = all_stats.copy()
    tmp['_norm'] = tmp['name'].apply(_normalize)
    closers = tmp[
        tmp['_norm'].isin(fa_names) &
        (tmp['GS'] == 0) &
        (tmp['SV'] >= 8) &
        (tmp['ERA'] <= 3.50) &
        (tmp['FIP'] <= 3.50)
    ].sort_values('SV', ascending=False)
    if closers.empty:
        return ""
    lines = "".join(
        f"🔒 {r['name']:<23} SV:{int(r['SV']):>2}  ERA:{r['ERA']:.2f}  FIP:{r['FIP']:.2f}\n"
        for _, r in closers.iterrows()
    )
    return f"\n**WIRE CLOSERS — ADD IMMEDIATELY**\n```\n{lines}```"


def format_discord(df, game_date=None, my_names=None, opp_names=None, my_all=None, fa_names=None,
                   draft_date=None, all_stats=None):
    """
    Returns a list of message strings, each under 1900 chars.

    When my_names / opp_names are provided (Phase 2 ESPN integration):
      - Splits into: YOUR STARTERS / OPP STARTERS / WIRE PICKUPS sections
      - Prepends a no-start alert for your roster pitchers not starting today
    When omitted: falls back to a single sorted list of all starters.
    """
    label = date.today().strftime('%A %b %d') if not game_date else game_date

    countdown = ""
    if draft_date:
        try:
            draft_dt = date.fromisoformat(draft_date)
            days_left = (draft_dt - date.today()).days
            if days_left > 0:
                countdown = f" | Draft in {days_left} days"
            elif days_left == 0:
                countdown = " | Draft Day!"
        except Exception:
            pass

    df = df.copy()
    has_stats = df['IP'].notna()
    df.loc[has_stats, 'proj_pts'] = df[has_stats].apply(project_start, axis=1)
    df = df.sort_values('proj_pts', ascending=False, na_position='last')

    scoring_legend = "*IP×+3  K×+1  ER×-2  H×-1  BB×-1  W×+2  L×-2*  🟢≥14  🟡9-13  🔴<9"

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
        era = row.get('ERA')
        fip = row.get('FIP')
        if pd.notna(era) and pd.notna(fip):
            diff = era - fip
            flag = "BUY" if diff > 0.75 else "SEL" if diff < -0.75 else "   "
        else:
            flag = "   "
        return (
            f"{icon}{row['name']:<21} {matchup:<13} "
            f"{proj_str:>6}  {fmt(row.get('ERA')):>4}  "
            f"{fmt(row.get('FIP')):>4}  {fmt(row.get('xERA')):>4}  "
            f"{fmt(row.get('K9'), 1):>4}  {flag}\n"
        )

    col_header = (
        f"{'PITCHER':<22} {'MATCHUP':<13} {'PROJ':>6}  {'ERA':>4}  {'FIP':>4}  {'xERA':>4}  {'K/9':>4}  FLAG\n"
        f"{'-'*22} {'-'*13} {'-'*6}  {'-'*4}  {'-'*4}  {'-'*4}  {'-'*4}  ----\n"
    )

    # ── Phase 2 sectioned layout ──────────────────────────────────────────────
    if my_names is not None:
        df['_norm'] = df['name'].apply(_normalize)

        mine_df = df[df['_norm'].isin(my_names)]
        opp_df  = df[df['_norm'].isin(opp_names or set())]
        not_mine = df[~df['_norm'].isin(my_names | (opp_names or set()))]
        # If we have confirmed FA data, filter to only available players
        wire_df = (
            not_mine[not_mine['_norm'].isin(fa_names)]
            if fa_names else not_mine
        )

        # No-start alert: my roster pitchers missing from today's probables
        starting_norms = set(df['_norm'].tolist())
        silent = [n for n in (my_all or []) if _normalize(n) not in starting_norms]
        no_start_line = (
            f"\n⚠ **No start today:** {', '.join(silent)}" if silent else ""
        )

        no_data_names = df[df['IP'].isna()]['name'].tolist()
        no_data_warn  = (
            f"\n⚠ No season data (rookie/call-up): {', '.join(no_data_names)}"
            if no_data_names else ""
        )

        def section(header, rows_df):
            if rows_df.empty:
                return f"\n**{header}**\n```\n(none today)\n```"
            body = "".join(row_line(r) for _, r in rows_df.iterrows())
            return f"\n**{header}**\n```\n{col_header}{body}```"

        header = f"⚾ **The Yastrzemski Legacy** — {label}{countdown}{no_start_line}"
        body   = (
            section("YOUR STARTERS TODAY", mine_df) +
            section("OPPONENT'S STARTERS TODAY", opp_df) +
            section("WIRE PICKUPS — STARTING TODAY", wire_df) +
            _closer_section(all_stats, fa_names) +
            f"\n{scoring_legend}" +
            no_data_warn
        )

        # Split into ≤1900-char chunks at section boundaries
        messages = []
        chunk    = header
        for part in body.split("\n**"):
            if not part:
                continue
            segment = "\n**" + part
            if len(chunk) + len(segment) > 1900:
                messages.append(chunk)
                chunk = segment
            else:
                chunk += segment
        messages.append(chunk)
        return messages

    # ── Phase 1 fallback: single sorted list ──────────────────────────────────
    no_data = df[df['IP'].isna()]['name'].tolist()
    warn    = f"\n⚠ No season data (rookie/call-up): {', '.join(no_data)}" if no_data else ""
    title   = f"⚾ **The Yastrzemski Legacy** — {label}{countdown}\n```\n{col_header}"
    footer  = f"```\n{scoring_legend}"

    lines = [row_line(r) for _, r in df.iterrows()]
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

    # Phase 2 — ESPN roster integration (uses env vars set in GitHub secrets)
    my_names = opp_names = my_all = fa_names = None
    espn_vars = (
        os.environ.get('ESPN_S2'),
        os.environ.get('ESPN_SWID'),
        os.environ.get('ESPN_LEAGUE_ID'),
        os.environ.get('ESPN_TEAM_ID'),
    )
    if all(espn_vars):
        espn_s2, espn_swid, league_id, team_id = espn_vars
        print("Pulling ESPN roster, matchup, and free agent data...")
        my_names, opp_names, my_all, fa_names = get_espn_data(
            league_id=league_id, espn_s2=espn_s2, swid=espn_swid,
            team_id=team_id, season=season,
        )
        print(f"  My roster pitchers: {len(my_all)}  |  Opponent pitchers: {len(opp_names)}  |  Free agent pitchers: {len(fa_names)}")
    else:
        print("ESPN env vars not set — using Phase 1 format (all starters, no roster split).")

    webhook = os.environ.get('DISCORD_WEBHOOK_URL')
    draft_date = os.environ.get('DRAFT_DATE')
    msgs = format_discord(merged, game_date, my_names=my_names, opp_names=opp_names,
                          my_all=my_all, fa_names=fa_names,
                          draft_date=draft_date, all_stats=stats)

    if not webhook:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        print("\nDISCORD_WEBHOOK_URL not set — printing to console instead:\n")
        for msg in msgs:
            print(msg)
        sys.exit(0)

    print("Posting to Discord...")
    post_to_discord(msgs, webhook)
    print(f"Done — sent {len(msgs)} message(s).")
