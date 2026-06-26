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
from datetime import date, datetime, timezone
from io import StringIO

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

import pandas as pd
import requests

from park_factors import get_park_factor, park_label

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
        bf = int(stat.get('battersFaced', 0) or 0)

        fip  = round((13 * hr + 3 * bb - 2 * so) / ip + FIP_CONSTANT, 2) if ip > 0 else None
        k9   = round((so / ip) * 9, 1) if ip > 0 else None
        bb9  = round((bb / ip) * 9, 1) if ip > 0 else None
        kpct = round((so / bf) * 100, 1) if bf > 0 else None

        team = split.get('team', {})
        rows.append({
            'name': person.get('fullName'), 'mlbam_id': person.get('id'),
            'team_id': team.get('id'),  # resolved to abbreviation in __main__
            'team': '',
            'GS': gs, 'IP': ip, 'SO': so, 'BB': bb,
            'H': h, 'ER': er, 'HR': hr, 'W': w, 'L': l, 'SV': sv,
            'ERA': float(stat.get('era', 0) or 0),
            'WHIP': float(stat.get('whip', 0) or 0),
            'FIP': fip, 'K9': k9, 'BB9': bb9, 'K%': kpct,
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


def get_team_win_pcts(season=2025, team_abbrevs=None):
    """Returns dict team_abbrev → win% (float) for all MLB teams from current standings.
    Requires team_abbrevs (team_id → abbrev) because the standings API omits abbreviation."""
    r = requests.get(
        'https://statsapi.mlb.com/api/v1/standings',
        params={'leagueId': '103,104', 'season': season},
        timeout=10,
    )
    r.raise_for_status()
    result = {}
    for record in r.json().get('records', []):
        for tr in record.get('teamRecords', []):
            team_id = tr.get('team', {}).get('id')
            abbrev  = (team_abbrevs or {}).get(team_id, '')
            pct     = float(tr.get('winningPercentage', 0.500) or 0.500)
            if abbrev:
                result[abbrev] = pct
    return result


def get_team_batting_stats(season=2025, team_abbrevs=None):
    """
    Returns dict team_abbrev → opp_factor (float, centered on 1.0).
    Factor = team runs/game ÷ league-average runs/game.
    Values > 1.0 mean a stronger offense (harder matchup); < 1.0 = weaker offense.
    Apply to the opponent's abbreviation when projecting a pitcher's start.
    """
    r = requests.get(
        'https://statsapi.mlb.com/api/v1/teams/stats',
        params={'stats': 'season', 'group': 'hitting', 'season': season, 'sportId': 1},
        timeout=10,
    )
    r.raise_for_status()

    splits = r.json().get('stats', [{}])[0].get('splits', [])
    team_rpg = {}
    for split in splits:
        team_id = split.get('team', {}).get('id')
        abbrev  = (team_abbrevs or {}).get(team_id)
        if not abbrev:
            continue
        stat   = split.get('stat', {})
        runs   = float(stat.get('runs', 0) or 0)
        games  = float(stat.get('gamesPlayed', 1) or 1)
        team_rpg[abbrev] = runs / games

    if not team_rpg:
        return {}

    league_avg = sum(team_rpg.values()) / len(team_rpg)
    return {abbrev: round(rpg / league_avg, 3) for abbrev, rpg in team_rpg.items()}


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
      fa_names   — normalized names of available free agent pitchers
      espn_ok    — True if auth succeeded, False if cookies are expired/invalid

    Returns (set(), set(), [], set(), False) on any ESPN failure so the report still runs.
    """
    try:
        from espn_api.baseball import League
    except ImportError:
        print("  WARNING: espn_api not installed — skipping ESPN integration.")
        return set(), set(), [], set(), False

    try:
        league   = League(league_id=int(league_id), year=season, espn_s2=espn_s2, swid=swid)
        my_team  = next((t for t in league.teams if t.team_id == int(team_id)), None)
        if my_team is None:
            print(f"  WARNING: ESPN team ID {team_id} not found — skipping ESPN integration.")
            return set(), set(), [], set(), False

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

        return my_names, opp_names, my_all, fa_names, True

    except Exception as e:
        print(f"  WARNING: ESPN fetch failed ({e}) — continuing without roster data.")
        return set(), set(), [], set(), False


# -- Injury / IL data ---------------------------------------------------------

def _parse_il_desc(desc):
    """Extract a short readable status from a full MLB transaction description."""
    m = re.search(r'(\d+)-day', desc, re.IGNORECASE)
    il_type = f"{m.group(1)}-day IL" if m else "IL"
    cond = re.search(r'\bwith\b (.+?)\.?\s*$', desc, re.IGNORECASE)
    if cond:
        return f"{il_type} — {cond.group(1).strip().rstrip('.')}"
    return il_type


def get_il_pitchers(days_back=60):
    """
    Returns dict mlbam_id (int) → short status string for players currently on the IL.
    Pulls the MLB transaction log and subtracts anyone who's been activated.
    """
    from datetime import timedelta
    end_date   = date.today()
    start_date = end_date - timedelta(days=days_back)

    r = requests.get(
        'https://statsapi.mlb.com/api/v1/transactions',
        params={
            'sportId':   1,
            'startDate': start_date.strftime('%Y-%m-%d'),
            'endDate':   end_date.strftime('%Y-%m-%d'),
        },
        timeout=15,
    )
    r.raise_for_status()

    placed  = {}   # mlbam_id → short description
    removed = set()

    for tx in r.json().get('transactions', []):
        pid  = (tx.get('person') or {}).get('id')
        if not pid:
            continue
        pid  = int(pid)
        code = (tx.get('typeCode') or '').upper()
        desc = tx.get('description', '')
        desc_lower = desc.lower()

        if 'IL' in code or 'DL' in code or ('injured list' in desc_lower and 'placed' in desc_lower):
            placed[pid] = _parse_il_desc(desc)
        elif code in ('ACT', 'DTO') or any(w in desc_lower for w in ('reinstated', 'activated', 'outrighted')):
            removed.add(pid)

    return {pid: desc for pid, desc in placed.items() if pid not in removed}


# -- Scoring projection -------------------------------------------------------

def project_start(row, park_factor=1.0, win_pct=0.500, opp_factor=1.0):
    ip_total = float(row.get('IP', 0) or 0)
    gs       = max(int(row.get('GS', 1) or 1), 1)
    ip_start = min(ip_total / gs, 7.0)

    if ip_total == 0:
        return 0.0

    def rate(col):
        return float(row.get(col, 0) or 0) / ip_total

    # Scale W/L probability by team win% relative to .500 baseline.
    # A .600 team gets 20% more win probability; a .400 team gets 20% less.
    w_prob = WIN_PROB  * (win_pct / 0.500)
    l_prob = LOSS_PROB * ((1.0 - win_pct) / 0.500)

    # Combined environment: park factor × opponent offense factor.
    # Both scale ER and H rates — the two biggest point-swing stats (-2 and -1).
    # e.g. Coors (1.16) × Yankees lineup (1.20) = 1.39 combined pressure.
    env = park_factor * opp_factor

    return round(
        ip_start                    * SCORING['IP'] +
        rate('SO') * ip_start       * SCORING['SO'] +
        rate('H')  * ip_start * env * SCORING['H']  +
        rate('BB') * ip_start       * SCORING['BB'] +
        rate('ER') * ip_start * env * SCORING['ER'] +
        w_prob                      * SCORING['W']   +
        l_prob                      * SCORING['L'],
        1
    )


# -- Discord formatting -------------------------------------------------------

def _injury_section(my_all, all_stats, il_data):
    """Injury watch: roster pitchers currently on the IL."""
    if not my_all or all_stats is None or not il_data:
        return ""
    id_map = {name: int(pid) for name, pid in zip(all_stats['name'], all_stats['mlbam_id'])}
    lines = [
        f"⚠ {name} — {il_data[pid]}"
        for name in my_all
        if (pid := id_map.get(name)) and pid in il_data
    ]
    if not lines:
        return ""
    return "\n**🏥 INJURY WATCH — YOUR ROSTER**\n" + "\n".join(lines)


def _breakout_section(all_stats, fa_names):
    """Wire starters whose ERA is inflated vs FIP + xERA with elite K%."""
    if all_stats is None or not fa_names:
        return ""
    tmp = all_stats.copy()
    tmp['_norm'] = tmp['name'].apply(_normalize)
    candidates = tmp[
        tmp['_norm'].isin(fa_names) &
        (tmp['GS'] > 0) &
        (tmp['ERA'] - tmp['FIP'] > 1.0) &
        tmp['xERA'].notna() & (tmp['xERA'] < 4.00) &
        tmp['K%'].notna() & (tmp['K%'] >= 23.0)
    ].sort_values('FIP')
    if candidates.empty:
        return ""
    lines = "".join(
        f"  {r['name']:<23} ERA:{r['ERA']:.2f}  FIP:{r['FIP']:.2f}"
        f"  xERA:{r['xERA']:.2f}  K%:{r['K%']:.1f}\n"
        for _, r in candidates.iterrows()
    )
    return f"\n**📈 BREAKOUT CANDIDATES — ON WIRE**\n```\n{lines}```"


def _closer_section(all_stats, fa_names, win_pcts=None):
    """Returns a formatted string for wire closers meeting quality thresholds.
    Requires winning team (win% > .500) per the closer filter spec."""
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
    ]
    if win_pcts and 'team' in closers.columns:
        closers = closers[closers['team'].map(lambda t: win_pcts.get(t, 0.500) > 0.500)]
    closers = closers.sort_values('SV', ascending=False)
    if closers.empty:
        return ""
    lines = "".join(
        f"🔒 {r['name']:<23} SV:{int(r['SV']):>2}  ERA:{r['ERA']:.2f}  FIP:{r['FIP']:.2f}"
        f"  W%:{win_pcts.get(r.get('team',''), 0.500):.3f}\n"
        if win_pcts else
        f"🔒 {r['name']:<23} SV:{int(r['SV']):>2}  ERA:{r['ERA']:.2f}  FIP:{r['FIP']:.2f}\n"
        for _, r in closers.iterrows()
    )
    return f"\n**WIRE CLOSERS — ADD IMMEDIATELY**\n```\n{lines}```"


def opp_label(of):
    """3-char label for opponent offense strength relative to league average."""
    if of >= 1.15: return "+++"
    if of >= 1.08: return " ++"
    if of >= 1.03: return "  +"
    if of <= 0.87: return "---"
    if of <= 0.93: return " --"
    if of <= 0.97: return "  -"
    return "   "


def format_discord(df, game_date=None, my_names=None, opp_names=None, my_all=None, fa_names=None,
                   draft_date=None, all_stats=None, il_data=None, win_pcts=None, opp_factors=None):
    """
    Returns a list of message strings, each under 1900 chars.

    When my_names / opp_names are provided (Phase 2 ESPN integration):
      - Splits into: YOUR STARTERS / OPP STARTERS / WIRE PICKUPS sections
      - Prepends a no-start alert for your roster pitchers not starting today
    When omitted: falls back to a single sorted list of all starters.
    """
    label = date.today().strftime('%A %b %d') if not game_date else game_date

    # When the report was generated, in Pacific time (handles PST/PDT).
    # Falls back to UTC if the tz database isn't available.
    if ZoneInfo is not None:
        try:
            now_pt  = datetime.now(ZoneInfo('America/Los_Angeles'))
            tz_abbr = now_pt.tzname()
        except Exception:
            now_pt, tz_abbr = datetime.now(timezone.utc), 'UTC'
    else:
        now_pt, tz_abbr = datetime.now(timezone.utc), 'UTC'
    hour12      = now_pt.hour % 12 or 12
    ampm        = 'AM' if now_pt.hour < 12 else 'PM'
    posted_line = f"\n🕒 Posted {now_pt:%a %b %d} · {hour12}:{now_pt:%M} {ampm} {tz_abbr}"

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
    df['park_factor'] = df.apply(get_park_factor, axis=1)
    df['win_pct']     = df['team'].map(lambda t: (win_pcts or {}).get(t, 0.500))
    df['opp_factor']  = df['opponent'].map(lambda t: (opp_factors or {}).get(t, 1.0))
    has_stats = df['IP'].notna()
    df.loc[has_stats, 'proj_pts'] = df[has_stats].apply(
        lambda r: project_start(r, park_factor=r['park_factor'],
                                win_pct=r['win_pct'], opp_factor=r['opp_factor']), axis=1
    )
    df = df.sort_values('proj_pts', ascending=False, na_position='last')

    scoring_legend = (
        "*IP×+3  K×+1  ER×-2  H×-1  BB×-1  W×+2  L×-2*  🟢≥14  🟡9-13  🔴<9\n"
        "*PARK (home stadium): COO=Coors  ++=hitter-friendly  +=mild hitter  --=pitcher-friendly  -=mild pitcher*\n"
        "*OPP (lineup vs pitcher): ++=tough offense  +=above avg  --=weak offense  -=below avg  (++ hurts pitcher)*"
    )

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
        park     = park_label(row.get('park_factor', 1.0))
        opp      = opp_label(row.get('opp_factor', 1.0))
        win_pct_val = row.get('win_pct', 0.500)
        wpct_str = f"{win_pct_val:.3f}" if pd.notna(win_pct_val) else ".500"
        return (
            f"{icon}{row['name']:<21} {matchup:<13} "
            f"{proj_str:>6}  {fmt(row.get('ERA')):>4}  "
            f"{fmt(row.get('FIP')):>4}  {fmt(row.get('xERA')):>4}  "
            f"{fmt(row.get('K%'), 1):>5}  {wpct_str}  {flag} {park} {opp}\n"
        )

    col_header = (
        f"{'PITCHER':<22} {'MATCHUP':<13} {'PROJ':>6}  {'ERA':>4}  {'FIP':>4}  {'xERA':>4}  {'K%':>5}    W%  FLAG PARK OPP\n"
        f"{'-'*22} {'-'*13} {'-'*6}  {'-'*4}  {'-'*4}  {'-'*4}  {'-'*5}  ----  ---- ---- ---\n"
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

        header = f"⚾ **The Yastrzemski Legacy** — {label}{countdown}{posted_line}{no_start_line}"
        body   = (
            section("YOUR STARTERS TODAY", mine_df) +
            section("OPPONENT'S STARTERS TODAY", opp_df) +
            section("WIRE PICKUPS — STARTING TODAY", wire_df) +
            _injury_section(my_all, all_stats, il_data) +
            _breakout_section(all_stats, fa_names) +
            _closer_section(all_stats, fa_names, win_pcts=win_pcts) +
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
    title   = f"⚾ **The Yastrzemski Legacy** — {label}{countdown}{posted_line}\n```\n{col_header}"
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

    # team_abbrevs first — needed to resolve team names in stats
    team_abbrevs = get_team_abbrevs(season=season)

    print(f"Pulling SP season stats ({season})...")
    stats = get_season_stats(season=season)
    stats['team'] = stats['team_id'].map(team_abbrevs).fillna('')
    print(f"  Got {len(stats)} qualified SPs.")

    print("Pulling Statcast xERA...")
    try:
        savant = get_savant_stats(season=season)
        stats  = stats.merge(savant, on='mlbam_id', how='left')
        print(f"  Merged xERA for {stats['xERA'].notna().sum()}/{len(stats)} pitchers.")
    except Exception as e:
        print(f"  WARNING: Savant xERA fetch failed ({e}) — continuing without xERA.")

    print(f"Pulling probable starters for {target}...")
    probables = get_probables(target, team_abbrevs)
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
        my_names, opp_names, my_all, fa_names, espn_ok = get_espn_data(
            league_id=league_id, espn_s2=espn_s2, swid=espn_swid,
            team_id=team_id, season=season,
        )
        if espn_ok:
            print(f"  My roster pitchers: {len(my_all)}  |  Opponent pitchers: {len(opp_names)}  |  Free agent pitchers: {len(fa_names)}")
        else:
            print("  ESPN auth failed — sending Discord alert.")
            alert_webhook = os.environ.get('DISCORD_WEBHOOK_URL')
            if alert_webhook:
                alert = (
                    "🚨 **ESPN Auth Failed — Action Required**\n"
                    "Your ESPN cookies have expired. Today's report ran without roster/matchup data.\n\n"
                    "**To fix:**\n"
                    "1. Log into ESPN Fantasy in your browser\n"
                    "2. Open DevTools (F12) → Application → Cookies → `fantasy.espn.com`\n"
                    "3. Copy the values of `espn_s2` and `SWID`\n"
                    "4. Go to your GitHub repo → Settings → Secrets → update `ESPN_S2` and `ESPN_SWID`"
                )
                try:
                    requests.post(alert_webhook, json={'content': alert}, timeout=10).raise_for_status()
                except Exception as e:
                    print(f"  WARNING: Could not send Discord alert ({e}).")
    else:
        print("ESPN env vars not set — using Phase 1 format (all starters, no roster split).")

    print("Pulling IL data...")
    try:
        il_data = get_il_pitchers()
        print(f"  {len(il_data)} players on IL.")
    except Exception as e:
        print(f"  WARNING: IL fetch failed ({e}) — skipping injury watch.")
        il_data = {}

    print("Pulling team standings (win%)...")
    try:
        win_pcts = get_team_win_pcts(season=season, team_abbrevs=team_abbrevs)
        print(f"  Got win% for {len(win_pcts)} teams.")
    except Exception as e:
        print(f"  WARNING: Standings fetch failed ({e}) — using flat win probability.")
        win_pcts = {}

    print("Pulling team batting stats (opponent quality)...")
    try:
        opp_factors = get_team_batting_stats(season=season, team_abbrevs=team_abbrevs)
        print(f"  Got offense ratings for {len(opp_factors)} teams.")
    except Exception as e:
        print(f"  WARNING: Team batting stats fetch failed ({e}) — using neutral opponent quality.")
        opp_factors = {}

    webhook = os.environ.get('DISCORD_WEBHOOK_URL')
    draft_date = os.environ.get('DRAFT_DATE')
    msgs = format_discord(merged, game_date, my_names=my_names, opp_names=opp_names,
                          my_all=my_all, fa_names=fa_names,
                          draft_date=draft_date, all_stats=stats, il_data=il_data,
                          win_pcts=win_pcts, opp_factors=opp_factors)

    if not webhook:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        print("\nDISCORD_WEBHOOK_URL not set — printing to console instead:\n")
        for msg in msgs:
            print(msg)
        sys.exit(0)

    print("Posting to Discord...")
    post_to_discord(msgs, webhook)
    print(f"Done — sent {len(msgs)} message(s).")
