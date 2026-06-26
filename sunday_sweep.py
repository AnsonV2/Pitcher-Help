#!/usr/bin/env python3
"""
sunday_sweep.py - Weekly Sunday-night post: the 5 best 2-start pitchers on the
waiver wire for the upcoming week.

Probable pitchers are only announced ~1-2 days out, so "who has 2 starts next
week" is *projected*: we reconstruct each team's rotation from its recent actual
starts (the MLB Stats API exposes completed-game starters) and extend it through
the unannounced back half of the week with a least-recently-used (most-rested)
cycle. Rotations shift on off-days/rainouts, so the post is labeled an estimate.

Reuses morning_report.py's data pulls, scoring, and Discord plumbing — this file
only adds the 2-start projection and the weekly-sweep formatting.

Required env vars:
  DISCORD_WEBHOOK_URL  — Discord channel webhook

Optional (enables wire filtering — without it, ownership is unknown):
  ESPN_S2, ESPN_SWID, ESPN_LEAGUE_ID, ESPN_TEAM_ID

Run locally:
  python sunday_sweep.py                # upcoming week from today
  python sunday_sweep.py 2025-06-29     # pretend "today" is that date (testing)
"""

import os
import sys
from datetime import date, datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

import pandas as pd
import requests

from park_factors import get_park_factor, park_label
from morning_report import (
    get_team_abbrevs, get_season_stats, get_savant_stats,
    get_team_win_pcts, get_team_batting_stats,
    get_espn_data, project_start, _normalize, post_to_discord,
    send_espn_auth_alert,
)


def project_two_start_pitchers(week_start, week_end, team_abbrevs):
    """Project each starter's starts within [week_start, week_end] (inclusive).

    Reconstructs each team's rotation from recent actual starts, then fills
    unannounced upcoming games via a least-recently-used (most-rested) cycle.
    Returns (results, week_game_slots) where results is
      dict mlbam_id -> {name, team, starts:[{date,opponent,home,projected}], count}
    and week_game_slots is the number of game slots scheduled in the week window
    (0 ⇒ no MLB games next week, i.e. off-season — caller should not post).
    """
    lookback = week_start - timedelta(days=12)
    r = requests.get(
        'https://statsapi.mlb.com/api/v1/schedule',
        params={'sportId': 1, 'startDate': lookback.isoformat(),
                'endDate': week_end.isoformat(), 'hydrate': 'probablePitcher'},
        timeout=25,
    )
    r.raise_for_status()

    def ab(team_dict):
        t = team_dict.get('team', {})
        return team_abbrevs.get(t.get('id')) or t.get('name', '???')[:3].upper()

    # Per-team chronological game slots; starter_* is None when not yet announced.
    team_games = {}
    for d in r.json().get('dates', []):
        gdate = date.fromisoformat(d['date'])
        for g in d.get('games', []):
            for side in ('home', 'away'):
                t = g['teams'][side]
                opp = 'away' if side == 'home' else 'home'
                tid = t['team']['id']
                pp = t.get('probablePitcher')
                team_games.setdefault(tid, []).append({
                    'date': gdate, 'opp': ab(g['teams'][opp]), 'home': side == 'home',
                    'starter_id': pp['id'] if pp else None,
                    'starter_name': pp['fullName'] if pp else None,
                })

    results = {}
    for tid, games in team_games.items():
        games.sort(key=lambda s: s['date'])

        # Seed the rotation from pre-week actual starts (pid -> last start date).
        last_start, pid_name = {}, {}
        for s in games:
            if s['starter_id'] and s['date'] < week_start:
                last_start[s['starter_id']] = s['date']
                pid_name[s['starter_id']] = s['starter_name']

        # Walk all games in order: use the announced starter, else assign the
        # most-rested rotation arm (oldest last_start) and advance its turn.
        for s in games:
            if s['starter_id']:
                pid, projected = s['starter_id'], False
                pid_name[pid] = s['starter_name']
            else:
                if not last_start:
                    continue  # no rotation history (early season) — can't project
                pid, projected = min(last_start, key=lambda p: last_start[p]), True
            last_start[pid] = s['date']

            if week_start <= s['date'] <= week_end:
                rec = results.setdefault(pid, {
                    'name': pid_name.get(pid, '?'),
                    'team': team_abbrevs.get(tid, '???'),
                    'starts': [], 'count': 0,
                })
                rec['starts'].append({'date': s['date'], 'opponent': s['opp'],
                                      'home': s['home'], 'projected': projected})
                rec['count'] += 1

    week_game_slots = sum(
        1 for games in team_games.values()
        for s in games if week_start <= s['date'] <= week_end
    )
    return results, week_game_slots


def _score_week(rec, srow, win_pcts, opp_factors):
    """Sum projected points across a pitcher's starts this week.
    srow is the pitcher's season-stat row (Series). Returns (total, per_start list)."""
    win_pct = (win_pcts or {}).get(rec['team'], 0.500)
    total, per = 0.0, []
    for s in rec['starts']:
        row = srow.to_dict()
        row['team'], row['opponent'], row['home'] = rec['team'], s['opponent'], s['home']
        pf = get_park_factor(row)
        of = (opp_factors or {}).get(s['opponent'], 1.0)
        pts = project_start(row, park_factor=pf, win_pct=win_pct, opp_factor=of)
        total += pts
        per.append((s, pts))
    return round(total, 1), per


def build_sweep(stats, two_start, fa_names, win_pcts, opp_factors,
                week_start, week_end, top_n=5):
    """Return a list of Discord message strings for the weekly 2-start sweep."""
    stats = stats.copy()
    stats['_norm'] = stats['name'].apply(_normalize)
    by_id = stats.set_index('mlbam_id')

    rows = []
    for pid, rec in two_start.items():
        if rec['count'] < 2:
            continue
        if pid not in by_id.index:
            continue                       # no season stats (rookie/no data) — skip
        srow = by_id.loc[pid]
        if isinstance(srow, pd.DataFrame):  # dup id safety
            srow = srow.iloc[0]
        if int(srow.get('GS', 0) or 0) == 0:
            continue                       # reliever caught in a doubleheader, etc.
        if fa_names is not None and srow['_norm'] not in fa_names:
            continue                       # not on the wire
        total, per = _score_week(rec, srow, win_pcts, opp_factors)
        rows.append({'rec': rec, 'srow': srow, 'total': total, 'per': per})

    rows.sort(key=lambda x: x['total'], reverse=True)
    rows = rows[:top_n]

    # ---- header / timestamp (Pacific, handles PST/PDT) ----
    if ZoneInfo is not None:
        try:
            now_pt = datetime.now(ZoneInfo('America/Los_Angeles'))
            tz_abbr = now_pt.tzname()
        except Exception:
            now_pt, tz_abbr = datetime.now(timezone.utc), 'UTC'
    else:
        now_pt, tz_abbr = datetime.now(timezone.utc), 'UTC'
    hour12 = now_pt.hour % 12 or 12
    ampm = 'AM' if now_pt.hour < 12 else 'PM'
    posted = f"🕒 Posted {now_pt:%a %b %d} · {hour12}:{now_pt:%M} {ampm} {tz_abbr}"

    span = f"{week_start:%b %d} – {week_end:%b %d}"
    wire_note = "" if fa_names is not None else "\n⚠ ESPN not configured — ownership unknown (showing all rosters)"

    header = (
        f"⚾ **The Yastrzemski Legacy** — Sunday Sweep\n"
        f"🗓️ **5 Best 2-Start Wire Pitchers** · Week of {span}\n"
        f"{posted}{wire_note}\n"
        f"*Starts are projected from rotation turn — shift on off-days/rainouts.*"
    )

    if not rows:
        return [header + "\n```\n(no 2-start wire pitchers projected this week)\n```"]

    def fmt(v, d=2):
        return f"{v:.{d}f}" if pd.notna(v) and v == v else "  --"

    def line(x):
        rec, srow, total = x['rec'], x['srow'], x['total']
        # 🟢≥28 🟡18-27 🔴<18  (per-start 14/9 thresholds, doubled for 2 starts)
        icon = "🟢" if total >= 28 else "🟡" if total >= 18 else "🔴"
        starts_str = ", ".join(
            f"{'vs ' if s['home'] else '@ '}{s['opponent']}" + ("*" if s['projected'] else "")
            for s, _ in x['per']
        )
        return (
            f"{icon}{rec['name']:<20} {('+'+fmt(total,1)):>6}  "
            f"{starts_str:<18}  {fmt(srow.get('FIP')):>4}  "
            f"{fmt(srow.get('xERA')):>4}  {fmt(srow.get('K%'),1):>5}\n"
        )

    col = f"{'PITCHER':<21} {'WK PTS':>6}  {'STARTS':<18}  {'FIP':>4}  {'xERA':>4}  {'K%':>5}\n"
    body = "".join(line(x) for x in rows)
    legend = (
        "*WK PTS = sum of both projected starts in YOUR scoring (IP×3 K×1 ER×-2 H×-1 BB×-1 W×+2)*\n"
        "*🟢≥28  🟡18-27  🔴<18  ·  `@`=away `vs`=home  ·  `*`=projected start (not yet announced)*"
    )
    return [f"{header}\n```\n{col}{body}```\n{legend}"]


def _week_window(ref):
    """Upcoming fantasy week (Mon–Sun): the Monday on/after ref, through that Sunday."""
    week_start = ref + timedelta(days=(0 - ref.weekday()) % 7)
    return week_start, week_start + timedelta(days=6)


if __name__ == '__main__':
    if ZoneInfo is not None:
        try:
            today_pt = datetime.now(ZoneInfo('America/Los_Angeles')).date()
        except Exception:
            today_pt = date.today()
    else:
        today_pt = date.today()

    ref = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else today_pt
    week_start, week_end = _week_window(ref)
    season = week_start.year

    print(f"Sunday sweep for week {week_start} – {week_end} (season {season})")

    team_abbrevs = get_team_abbrevs(season=season)

    print("Projecting 2-start pitchers...")
    two_start, week_game_slots = project_two_start_pitchers(week_start, week_end, team_abbrevs)
    if week_game_slots == 0:
        # No MLB games scheduled next week → off-season. Stay silent rather than
        # posting an empty sweep every Sunday for ~6 months.
        print("No MLB games scheduled next week (off-season) — nothing to post.")
        sys.exit(0)
    print(f"  {sum(1 for r in two_start.values() if r['count'] >= 2)} pitchers projected for 2+ starts.")

    print("Pulling SP season stats...")
    stats = get_season_stats(season=season)
    if stats.empty or 'team_id' not in stats.columns:
        # Games scheduled but no season stats yet (preseason/spring training) —
        # can't score anyone, so skip without crashing.
        print("No season stats yet (preseason) — nothing to post.")
        sys.exit(0)
    stats['team'] = stats['team_id'].map(team_abbrevs).fillna('')
    print(f"  {len(stats)} qualified pitchers.")

    print("Pulling Statcast xERA...")
    try:
        savant = get_savant_stats(season=season)
        stats = stats.merge(savant, on='mlbam_id', how='left')
    except Exception as e:
        print(f"  WARNING: xERA fetch failed ({e}).")
    if 'xERA' not in stats.columns:
        stats['xERA'] = pd.NA

    fa_names = None
    espn_vars = (os.environ.get('ESPN_S2'), os.environ.get('ESPN_SWID'),
                 os.environ.get('ESPN_LEAGUE_ID'), os.environ.get('ESPN_TEAM_ID'))
    if all(espn_vars):
        print("Pulling ESPN free agents...")
        _, _, _, fa_names, espn_ok = get_espn_data(
            league_id=espn_vars[2], espn_s2=espn_vars[0], swid=espn_vars[1],
            team_id=espn_vars[3], season=season,
        )
        if not espn_ok:
            fa_names = None
            print("  ESPN auth failed — wire filter disabled, sending Discord alert.")
            send_espn_auth_alert(os.environ.get('DISCORD_WEBHOOK_URL'), context="Sunday sweep")
        elif not fa_names:
            # Auth OK but the free-agent list came back empty — that's a transient
            # ESPN error, not a genuinely empty wire. Disable the filter (show all)
            # instead of posting a misleading "no pitchers" sweep.
            fa_names = None
            print("  No free agents returned — wire filter disabled (showing all).")
        else:
            print(f"  {len(fa_names)} free agent pitchers.")
    else:
        print("ESPN env vars not set — showing all rosters (ownership unknown).")

    print("Pulling standings + opponent quality...")
    try:
        win_pcts = get_team_win_pcts(season=season, team_abbrevs=team_abbrevs)
    except Exception as e:
        print(f"  WARNING: standings failed ({e}).")
        win_pcts = {}
    try:
        opp_factors = get_team_batting_stats(season=season, team_abbrevs=team_abbrevs)
    except Exception as e:
        print(f"  WARNING: team batting failed ({e}).")
        opp_factors = {}

    msgs = build_sweep(stats, two_start, fa_names, win_pcts, opp_factors,
                       week_start, week_end)

    webhook = os.environ.get('DISCORD_WEBHOOK_URL')
    if not webhook:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        print("\nDISCORD_WEBHOOK_URL not set — printing instead:\n")
        for m in msgs:
            print(m)
        sys.exit(0)

    print("Posting to Discord...")
    post_to_discord(msgs, webhook)
    print(f"Done — sent {len(msgs)} message(s).")
