# Pitcher-Help — The Yastrzemski Legacy

A fantasy-baseball pitcher helper bot for **H2H points leagues** on ESPN. It posts
formatted reports to a Discord channel on a schedule, run entirely free on GitHub
Actions (no server to maintain).

There are two reports:

| Report | When | What it posts |
| --- | --- | --- |
| **Daily morning report** ([morning_report.py](morning_report.py)) | Every morning (~7–9 AM PT) | Today's probable starters, split into **your starters / opponent's starters / wire pickups**, plus injury watch, breakout candidates, and wire closers. |
| **Sunday wire sweep** ([sunday_sweep.py](sunday_sweep.py)) | Sunday night (~9 PM PT) | The 5 best **2-start** pitchers available on the waiver wire for the upcoming week. |

Each pitcher is scored in **your league's exact scoring** (see below), adjusted for
ballpark, opponent offense, and team win%.

---

## How it works

```
GitHub Actions cron
   └─ pip install pandas requests espn-api
        └─ python morning_report.py   (or sunday_sweep.py)
              ├─ MLB Stats API      → season stats, probables, standings, IL, schedule
              ├─ Baseball Savant    → xERA (expected ERA)
              ├─ ESPN Fantasy API   → your roster, opponent roster, free agents
              └─ POST to Discord webhook
```

- **No database.** Every run pulls fresh data from public APIs. Nothing is stored
  between runs.
- **No secrets in the repo.** All credentials live in GitHub Actions **Secrets**
  (see [Configuration](#configuration)).
- **Fails soft.** If any single data source is down, that section is skipped and the
  rest of the report still posts. ESPN auth failure additionally fires a Discord
  alert telling you to refresh your cookies.

### Files

| File | Purpose |
| --- | --- |
| [morning_report.py](morning_report.py) | Daily report. Also the shared library — `sunday_sweep.py` imports its data-pull, scoring, and Discord functions. |
| [sunday_sweep.py](sunday_sweep.py) | Weekly 2-start wire sweep. |
| [park_factors.py](park_factors.py) | Static ballpark run factors (FanGraphs 3-year). Used to adjust ER/H projections. |
| [.github/workflows/morning_report.yml](.github/workflows/morning_report.yml) | The cron schedule + GitHub Actions job. |
| [fantasy-baseball-pitchers.md](fantasy-baseball-pitchers.md) | Design notes / strategy reference. |
| [requirements.txt](requirements.txt) | Local-dev deps (the Action installs its own list — see note below). |

### Scoring (your league — "The Yastrzemski Legacy")

Defined in [morning_report.py](morning_report.py) `SCORING`:

```
IP ×+3   SO ×+1   W ×+2   SV ×+5   HD ×+2
 H ×-1   BB ×-1   ER ×-2   L ×-2
```

If your league re-scores, **edit the `SCORING` dict** and the projection math follows.

---

## Configuration

All configuration is via GitHub Actions secrets/variables:
**Repo → Settings → Secrets and variables → Actions.**

### Secrets (required for full report)

| Secret | Required? | Where to get it |
| --- | --- | --- |
| `DISCORD_WEBHOOK_URL` | **Yes** | Discord channel → Edit Channel → Integrations → Webhooks → New Webhook → Copy URL. |
| `ESPN_S2` | For roster/wire data | Browser cookie (see [refreshing ESPN cookies](#espn-cookies-expired-most-common-issue)). |
| `ESPN_SWID` | For roster/wire data | Same cookie source as `ESPN_S2`. |
| `ESPN_LEAGUE_ID` | For roster/wire data | The number in your ESPN league URL: `…/leagueId=XXXXXX`. |
| `ESPN_TEAM_ID` | For roster/wire data | Your team's number in the league (1–10). |

### Variables (optional)

| Variable | Purpose |
| --- | --- |
| `DRAFT_DATE` | `YYYY-MM-DD`. If set and in the future, adds a "Draft in N days" countdown to the report. |

> **Without the ESPN secrets** the bot still runs — it just can't tell which pitchers
> are yours vs. the opponent vs. free agents, so it falls back to a single combined
> list of all probable starters.

---

## Schedule

Crons are in [.github/workflows/morning_report.yml](.github/workflows/morning_report.yml)
(times are **UTC**):

- `0 14 * * *` + `5 14 * * *` — daily morning report (~7 AM PT target; see note).
- `0 4 * * 1` + `5 4 * * 1` — Sunday-night 2-start sweep (Monday 04:00 UTC).

The duplicate `*5` crons are **intentional redundancy** — GitHub frequently skips or
delays scheduled runs, so each report fires twice 5 minutes apart. The script is
designed so a double-fire just posts the report twice (no harm); in practice GitHub
usually only runs one.

> **GitHub cron is best-effort and often fires 1–2 hours late.** This is expected and
> not a bug. Every post is timestamped with the real send time ("🕒 Posted …"), so
> late delivery is obvious and harmless. If you need an exact time, run it manually.

### Run it manually

**Repo → Actions → Morning Report → Run workflow.** You can choose:
- `report`: `daily` or `sunday`
- `date`: a `YYYY-MM-DD` to backfill/test a past date (blank = today)

---

# Troubleshooting / Runbook

This is the "something's wrong" section. Start here.

## First step for any failure

1. **Repo → Actions → Morning Report → click the latest run → expand "Run report".**
2. Read the log. The scripts print a line for every step (`Pulling …`, `Got N …`,
   `WARNING: …`). The failing step names itself.
3. To reproduce locally, see [Running locally](#running-locally) — you get the same
   log without waiting for cron.

The bot is built to **fail soft**: a `WARNING:` line means one section was skipped but
the report still posted. A hard crash (red X, non-zero exit) means a *required* step
failed — almost always the MLB Stats API or the Discord webhook.

---

## ESPN cookies expired (most common issue)

**Symptoms:**
- Discord posts an alert: *"🚨 ESPN Auth Failed — Action Required."*
- The report still arrives, but with **no "your starters / opponent / wire" split** —
  just one combined list.
- Log shows `ESPN auth failed` or `ESPN fetch failed`.

**Why:** ESPN's `espn_s2` and `SWID` cookies are session cookies and **expire roughly
every 1–12 months**, or whenever you log out / change your password. This is the single
thing most likely to "break" over a season. Nothing in the code can prevent it — the
cookies must be re-copied from your browser.

**Fix — refresh the cookies:**

1. Log into [fantasy.espn.com](https://fantasy.espn.com) in your browser.
2. Open DevTools (**F12**) → **Application** tab → **Cookies** → `https://fantasy.espn.com`.
3. Copy the **value** of:
   - `espn_s2` (long URL-encoded string)
   - `SWID` (looks like `{XXXXXXXX-XXXX-…}` — **include the curly braces**)
4. **Repo → Settings → Secrets and variables → Actions** → update:
   - `ESPN_S2` ← the `espn_s2` value
   - `ESPN_SWID` ← the `SWID` value
5. Re-run manually (**Actions → Run workflow**) to confirm the split sections return.

> Tip: refreshing cookies in a private/incognito window and **not** logging out of that
> window can make them last longer.

---

## No report arrived at all

Check, in order:

1. **Did the workflow run?** Actions tab → is there a run for today? If not, GitHub
   skipped/delayed the cron — wait, or run it manually. (GitHub also auto-disables
   scheduled workflows after **60 days of repo inactivity**; just push any commit or
   re-enable the workflow to wake it up.)
2. **Did it fail?** Red X → open the log, find the traceback.
3. **Webhook problem?** Log shows a `requests` error POSTing to Discord, or
   `DISCORD_WEBHOOK_URL not set`. The webhook may have been deleted in Discord, or the
   secret is missing/typo'd. Recreate the webhook and update `DISCORD_WEBHOOK_URL`.
4. **Legitimately nothing to post?** The script exits cleanly (green check, no Discord
   message) when:
   - No MLB games are scheduled that day (off-day / off-season).
   - Preseason: games scheduled but no season stats exist yet to score.
   - These are **not errors** — see [Off-season behavior](#off-season--preseason).

---

## "No season data (rookie/call-up)" warnings

Expected. A pitcher appears in today's probables but has fewer than the `min_ip`
threshold (20 IP) of MLB stats this season — a rookie or fresh call-up. They're listed
with `--` stats and no projection. Nothing to fix.

---

## A section is missing from the report

Each section degrades independently. Match the missing section to its log warning:

| Missing section | Log line | Cause |
| --- | --- | --- |
| Roster/opponent/wire split | `ESPN auth failed` | [Refresh ESPN cookies](#espn-cookies-expired-most-common-issue). |
| Wire pickups (unfiltered, shows everyone) | `Could not fetch free agents` | Transient ESPN error; usually self-heals next run. |
| `xERA` column shows `--` | `Savant xERA fetch failed` | Baseball Savant down/changed CSV. Self-heals, or see below. |
| Injury watch | `IL fetch failed` | MLB transactions API hiccup. Self-heals. |
| Win% / `W%` column flat at `.500` | `Standings fetch failed` | MLB standings API hiccup. Self-heals. |
| Opponent (`OPP`) column blank | `Team batting stats fetch failed` | MLB team-stats API hiccup. Self-heals. |

Most of these are **transient** — one API call timed out. If a section is missing for
**several days running**, that source likely changed its format; see below.

---

## When an upstream data source changes (deeper failures)

These APIs are public and undocumented-ish; they occasionally change. By likelihood:

### Baseball Savant xERA (`get_savant_stats`)
Savant serves a CSV from a leaderboard URL. If they rename columns or change the URL,
xERA silently goes all `--`. Check the URL in [morning_report.py](morning_report.py)
`get_savant_stats` still returns CSV in a browser; the code renames `player_id`→`mlbam_id`
and `xera`→`xERA`. Update those if the headers changed. Non-fatal — the bot runs without xERA.

### MLB Stats API (`statsapi.mlb.com`)
The backbone: season stats, probables, standings, IL, schedule. If MLB changes a field
name, the affected section breaks. This API is very stable, but if season stats or
probables fail outright the whole report can't run. The endpoints and params are all in
the `get_*` functions at the top of [morning_report.py](morning_report.py).

### ESPN Fantasy API (`espn-api` library)
We use the [`espn-api`](https://github.com/cwendt94/espn-api) PyPI package. ESPN changes
their private API every preseason. If roster parsing breaks after auth succeeds,
**upgrade the library** — the workflow installs `espn-api` unpinned, so a re-run usually
picks up the fix. If ESPN moves to a new season's endpoint, the maintainers typically
patch it within days.

---

## Things that need yearly / seasonal attention

- **Season rollover.** The scripts derive the season from the run date
  (`season = int(target[:4])` / `week_start.year`), so January 1 they automatically
  target the new season. No code change needed — but expect preseason "nothing to post"
  exits until games and stats exist.
- **ESPN cookies** — will expire at least once mid-season. See above.
- **`DRAFT_DATE`** — update or clear it each year (it's a repo Variable, not in code).
- **Park factors** ([park_factors.py](park_factors.py)) — static FanGraphs 3-year values.
  Refresh them once a year if you want, and add/rename any team whose abbreviation
  changes (e.g. the Athletics' `ATH`). A missing team just defaults to neutral (1.0).
- **`FIP_CONSTANT`** (3.17 in [morning_report.py](morning_report.py)) — the league FIP
  constant drifts slightly year to year; optional to update.

---

## Off-season / preseason

Both scripts **intentionally stay silent** rather than spam an empty report for ~6
months:

- **No MLB games next week** → `sunday_sweep.py` exits without posting.
- **No games today** → `morning_report.py` exits without posting.
- **Games scheduled but no season stats yet** (spring training) → exits without posting.

A green check with no Discord message during the off-season is **correct behavior**, not
a failure.

---

## Running locally

Useful for testing formatting or debugging without waiting for cron. From the repo root
(PowerShell):

```powershell
pip install pandas requests espn-api

# Daily report — prints to console if no webhook is set
$env:DISCORD_WEBHOOK_URL = ""          # leave empty to print instead of post
python morning_report.py               # today
python morning_report.py 2025-06-20    # backfill a past date

# To exercise the ESPN sections locally, also set:
$env:ESPN_S2 = "..."; $env:ESPN_SWID = "..."
$env:ESPN_LEAGUE_ID = "..."; $env:ESPN_TEAM_ID = "..."

# Sunday sweep
python sunday_sweep.py                  # upcoming week
python sunday_sweep.py 2025-06-29       # pretend "today" is this date
```

With `DISCORD_WEBHOOK_URL` **unset**, both scripts print the exact messages to the
console instead of posting — the safest way to preview changes.

> **Dependency note:** the runtime needs only `pandas`, `requests`, and `espn-api`
> (now the full contents of [requirements.txt](requirements.txt)). The GitHub Action
> installs these directly in its "Install dependencies" step rather than from the file —
> so if you add an import, update **both** [requirements.txt](requirements.txt) and the
> workflow's `pip install` line. ([spike.py](spike.py) is a standalone scratch/prototype
> script and isn't run by the bot.)

---

## Quick reference — where to change things

| I want to… | Edit |
| --- | --- |
| Change league scoring | `SCORING` in [morning_report.py](morning_report.py) |
| Change post times | crons in [.github/workflows/morning_report.yml](.github/workflows/morning_report.yml) |
| Refresh ESPN login | `ESPN_S2` / `ESPN_SWID` secrets (browser cookies) |
| Change Discord channel | `DISCORD_WEBHOOK_URL` secret |
| Adjust ballpark factors | [park_factors.py](park_factors.py) |
| Tune projection thresholds (🟢/🟡/🔴, BUY/SELL, breakout/closer filters) | the `_section` / `row_line` helpers in [morning_report.py](morning_report.py) |
