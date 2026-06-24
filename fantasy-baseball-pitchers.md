# Fantasy Baseball Pitcher Evaluation Bot — Project Plan

## Goals
- Free to run, forever — no paid APIs, no paid hosting
- Discord bot with a daily 8:00 AM PST morning report
- Geared toward **H2H Points leagues**
- Connected to your **ESPN Fantasy league** — reads your exact scoring settings, roster, and matchup schedule

## Your League — The Yastrzemski Legacy
- **10 teams | H2H Points | Snake Draft**
- **Roster:** 19 total — 16 starters (1C, 1B, 1 2B, 1 3B, 1 SS, 3 OF, 1 UTIL, **7 P**), 3 bench, 3 IL
- **Pitcher limit:** 12 Games Started max per week
- **Draft:** Set via GitHub repo variable (editable in browser, no bot needed)

---

## Your Exact Scoring — The Yastrzemski Legacy

### Pitching Points
| Event | Points | Notes |
|-------|--------|-------|
| Inning Pitched (IP) | **+3** | Per inning — volume is king |
| Strikeout (K) | **+1** | Direct points, repeatable skill |
| Win (W) | **+2** | Matters more than many leagues |
| Hold (HD) | **+2** | Setup men have real value here |
| Save (SV) | **+5** | Closers are extremely valuable |
| Hit Allowed (H) | **-1** | WHIP-like penalty |
| Walk Issued (BB) | **-1** | Walks hurt directly |
| Earned Run (ER) | **-2** | Biggest negative — run prevention is critical |
| Loss (L) | **-2** | Avoid pitchers on bad offenses |

### What a Good Start Looks Like (Points Math)
```
6 IP × 3    = +18
7 K × 1     = +7
5 H × -1    = -5
2 ER × -2   = -4
1 BB × -1   = -1
Win         = +2
            ——————
            +17 points (a great start)

A disaster start (3 IP, 5 ER, 4 BB):
3 × 3 = +9, 5 × -2 = -10, 4 × -1 = -4, 7 H × -1 = -7
= -12 points (actively hurts you)
```

### What This Means for Pitcher Evaluation

**ER avoidance is the top priority** (-2 per ER is steep). Then volume, then strikeouts.

Rank pitchers by these in order:

1. **xERA / FIP** — best predictor of earned runs allowed (your biggest point swing)
2. **IP per start / average innings depth** — more IP = more +3 ticks, more K chances
3. **BB/9 or BB%** — walks cost -1 AND lead to ER (-2 each) — double damage
4. **H/9 or BABIP** — hits cost -1 AND lead to ER
5. **K/9 or K%** — direct +1 per K, and SwStr% predicts future Ks
6. **Win probability** — team's run support and bullpen matter for W/L (+2/-2)
7. **Closer role** — SV is +5, the single highest-value event in your scoring

**Saves change everything:** A closer with 3 saves in a week (+15 pts) outscores many starters. Streaming closers on winning teams is a real strategy.

**Holds are not worth a roster spot.** A setup man getting 2 holds/week = +4 pts. That's less than a mediocre SP start. Don't chase holds.

**The 12 GS limit:** You can start 12 pitchers per week. With 7 P slots, you're looking at ~1–2 starts per slot per week. This means streaming (picking up a pitcher just for one week) is viable and the bot should track it.

**Avoid:** Pitchers on bad offenses (L risk), fly ball pitchers in hitter-friendly parks (ER risk)

### Relief Pitcher Filter — Closers Only, and Only Good Ones

The bot is SP-first. A closer only surfaces if it clears **all three** bars:

| Bar | Threshold | Why |
|-----|-----------|-----|
| **Role locked** | Sole closer, not a committee | Committee situations dilute SV chances |
| **Team context** | Winning team with save opportunities | Closers on losing teams rarely get saves |
| **Quality** | ERA < 3.50 AND FIP < 3.50 | A bad closer getting shelled costs -2/ER and still loses the save |

```
Show closer if:
  role is locked (no committee)
  AND team winning% > .500
  AND ERA < 3.50 AND FIP < 3.50

Otherwise: ignore — even if it's a save opportunity, a bad closer hurts you
```

Setup men, holds, closer-in-waiting: **never surfaced.** Not worth the roster spot vs. an extra SP.

---

## Free APIs — No Scraping Required

You do NOT need to scrape anything. All the data you need is available through free official or library APIs.

### 1. MLB Stats API (Official, Completely Free, No Key)
```
https://statsapi.mlb.com/api/
```
- No API key, no sign-up, no rate limit published (be reasonable)
- Returns: schedules, rosters, game logs, injury list, pitcher game-by-game stats
- Best for: "Who is starting today/this week?" and injury/IL updates

Examples:
```
# Today's games and probable pitchers
https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=2025-06-24&hydrate=probablePitcher

# Pitcher season stats
https://statsapi.mlb.com/api/v1/people/{playerId}/stats?stats=season&group=pitching&season=2025
```

### 2. Baseball Savant / Statcast (Free, No Key)
```
https://baseballsavant.mlb.com/
```
- CSV exports available programmatically
- Returns: Stuff+, xERA, SwStr%, CSW%, Barrel%, exit velocity, spin rate
- Best for: deep quality-of-stuff metrics, breakout/decline signals
- Accessed cleanly through the `pybaseball` Python library

### 3. pybaseball (Python Library — Wraps Everything Above)
```
pip install pybaseball
```
- Pulls FanGraphs leaderboards (FIP, xFIP, SIERA, K%, BB%, GB%, BABIP, LOB%)
- Pulls Baseball Savant Statcast data
- Pulls Baseball Reference data
- All free, no keys needed
- This is your primary data tool

### 4. FanGraphs (via pybaseball — Free)
- Accessed through `pybaseball.pitching_stats()` — no key, no sign-up
- Has every advanced metric: FIP, xFIP, SIERA, Stuff+, SwStr%, CSW%, K%, BB%

### 5. ESPN Fantasy API (Unofficial, Free, No Key)
```
https://fantasy.espn.com/apis/v3/games/flb/
```
- No official key needed — uses browser session cookies for private leagues
- Returns: your exact scoring settings, your roster, waiver wire, matchup schedule, standings
- Python library: `espn-api` (`pip install espn_api`)
- Auth: copy two cookies (`espn_s2` and `SWID`) from your browser once — they last weeks/months

```python
from espn_api.baseball import League

league = League(
    league_id=123456,       # from your ESPN league URL
    year=2025,
    espn_s2="your_cookie",  # from browser dev tools
    swid="your_cookie"
)

# Your exact scoring rules — no hardcoding needed
scoring = league.settings.stat_scores
# e.g. {"K": 1.0, "IP": 3.0, "ER": -1.0, "BB": -0.5, "H": -0.5, "W": 5.0}

# Your current roster
my_team = league.teams[0]
my_pitchers = [p for p in my_team.roster if p.position in ["SP", "RP", "P"]]

# Available free agents this week
free_agents = league.free_agents(size=50)
```

**How to get your cookies (one-time setup):**
1. Log into ESPN Fantasy in Chrome/Firefox
2. Open DevTools (F12) → Application → Cookies → `fantasy.espn.com`
3. Copy the value of `espn_s2` and `SWID`
4. Paste into your `.env` file — never commit these to GitHub

**Summary: pybaseball + MLB Stats API + ESPN API = everything you need, 100% free.**

---

## Tech Stack (Explained Simply)

```
Python 3.11+          The programming language. Everything is written in this.

discord.py            Python library that lets you build Discord bots.
                      Handles commands like /pitcher, /starts, /report.

pybaseball            Python library. Pulls pitcher stats from FanGraphs
                      and Baseball Savant for free. One import, done.

requests              Standard Python library for calling the MLB Stats API.
                      Gets schedules, probable starters, injury lists.

SQLite                A database stored as a single file on disk.
                      No server needed. Saves pitcher data so you're not
                      pulling it fresh every single time someone runs a command.

APScheduler           Python library that runs functions on a schedule.
                      Tells the bot "at 8:00 AM PST every day, send the report."

.env file             A text file that holds your Discord bot token secretly,
                      so you don't accidentally share it in your code.
```

### How It Fits Together

```
Every morning at 8 AM PST:
  APScheduler fires
    → ESPN API pulls YOUR scoring settings (exact point values)
    → ESPN API pulls your current roster + this week's matchup opponent
    → pybaseball pulls fresh pitcher stats (K%, FIP, SwStr%, etc.)
    → MLB Stats API gets today's probable starters
    → Bot scores each pitcher using YOUR exact ESPN point values
    → Compares your pitchers vs available free agents
    → Sends personalized morning report to Discord

On demand (any time):
  /pitcher Zack Wheeler  → stat card scored in YOUR league's points
  /roster                → your pitching staff ranked by projected points
  /stream                → best FA pitchers to add this week (your scoring)
  /matchup               → this week's opponent's pitchers vs yours

Weekly refresh (Sunday night):
  Pull full leaderboard, refresh SQLite cache
  Re-read ESPN scoring settings (in case commissioner changed anything)
  Flag regression candidates (ERA >> FIP)
  Flag breakout candidates (Stuff+ rising)
```

---

## Hosting — GitHub Actions Only

**Everything runs on GitHub Actions. No server, no VM, no credit card.**

GitHub Actions is free CI/CD infrastructure. You push your code to a GitHub repo, set up a schedule (cron), and GitHub runs the script on their servers automatically. For this bot it works perfectly because:

- The report is **one-way** — it posts to Discord and exits. No 24/7 process needed.
- Scripts finish in ~30 seconds. At 3 runs/day × 30 days = ~45 min/month. Free tier gives you 2,000 min/month on public repos, 500 on private. Plenty of headroom.
- Secrets (ESPN cookies, Discord webhook URL) are stored in GitHub's encrypted secrets vault — never in your code.

**What you give up:** No interactive slash commands (`/pitcher`, `/wire`). The reports arrive on schedule automatically — you just read them. If you ever want on-demand queries later, that's a separate project.

**Draft date without a bot:** Store it as a GitHub **repo variable** (not a secret). Go to your repo → Settings → Secrets and variables → Actions → Variables → New variable. Name it `DRAFT_DATE`, set the value to `2026-03-23`. To reschedule the draft, just edit that variable in the browser. The next report run picks it up automatically.

---

## Architecture (GitHub Actions Only)

```
[GitHub Repo]
  Your code lives here.
  Secrets: ESPN cookies, Discord webhook URL (encrypted, never visible)
  Variables: DRAFT_DATE (editable in browser)
       |
[GitHub Actions — 3 Cron Jobs]
  8:00 AM PST daily    → morning report (today's starters + wire pickups)
  8:00 AM PST Wed      → mid-week wire check (starts remaining this week)
  9:00 PM PST Sunday   → weekly sweep (best available for next week)
       |
  Each run: pulls MLB Stats API + Baseball Savant → scores pitchers → exits
       |
[Discord Webhook]
  A URL you copy from Discord channel settings (one-time setup).
  The script POSTs a formatted message to it. No bot account needed.
  Messages appear in your Discord channel automatically.
```

**How a Discord webhook works:** In your Discord server, go to the channel you want → Edit Channel → Integrations → Webhooks → Create Webhook. Copy the URL. Paste it into GitHub secrets as `DISCORD_WEBHOOK_URL`. That's it — your script sends a `POST` request to that URL and Discord displays the message.

---

## Waiver Wire — How Often the Bot Checks

The wire isn't static — it changes from injuries, roster drops, and weekly schedule shifts. Here's the cadence:

### Automated Checks

| Time | What it checks | Why |
|------|---------------|-----|
| **Daily 8 AM PST** (morning report) | Pitchers starting today who are on the wire | Catch same-day streamers before first pitch |
| **Sunday 9 PM PST** | Full weekly wire sweep — who has 2+ starts next week | New matchup week starts Monday; best time to plan adds |
| **Wednesday 8 AM PST** | Mid-week wire check — starts remaining this week | You're mid-matchup; can still pick up a Thursday/Friday/weekend starter |
| **Any time via command** | `/wire` — pulls current wire on demand | When you want to check right now, not wait for a scheduled run |

### No Real-Time Alerts (GitHub Actions Limitation)
GitHub Actions runs on a schedule — it can't push alerts the moment something happens. You won't get an instant ping when a closer role changes. The next scheduled report (at most ~1 day away) will reflect current availability.

ESPN processes waiver claims at **~3 AM ET daily**. The 8 AM PST check (11 AM ET) runs after processing, so the report always shows who is actually available to add.

---

## Draft Date — Set via GitHub Variable

No bot, no commands. The draft date lives as a GitHub repo variable:

```
Repo → Settings → Secrets and variables → Actions → Variables → DRAFT_DATE
Value: 2026-03-23
```

If the commissioner reschedules the draft, you just edit that variable in the browser. The next report run reads the updated value automatically.

The report uses it to:
- Count down in the morning report header during spring training ("Draft in 13 days")
- Switch into draft prep mode 30 days before the date
- Switch back to off-season mode after draft day passes

---

## Morning Report — What It Should Show

Every day at 8:00 AM PST, personalized to your ESPN league:

```
⚾ The Yastrzemski Legacy — Morning Report
   Tuesday June 24 | Week 12 vs Team Chaos | Draft: 127 days away

YOUR STARTERS TODAY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🟢 Zack Wheeler    SP  vs COL  — Proj: +18.4 pts | FIP: 2.84 | K%: 29.1 | SwStr%: 14.2%
🟡 MacKenzie Gore  SP  vs NYM  — Proj: +11.2 pts | FIP: 3.52 | K%: 24.1 | SwStr%: 11.9%

YOUR OPPONENT'S STARTERS TODAY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🟢 Chris Sale      SP  vs MIA  — Proj: +16.1 pts | FIP: 3.11 | K%: 27.3

WIRE PICKUPS — STARTING TODAY (SP)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Hunter Brown   SP  vs TEX  — Proj: +14.7 pts | FIP 3.28, SwStr% rising → ADD
• Cade Povich    SP  vs MIA  — Proj: +10.3 pts | FIP 3.41, 2 starts this week

WIRE PICKUPS — RELIEF (Closer/Elite Setup Only)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔒 Andres Munoz   CL  SEA     — Available (3% owned?) | 18 SV pace | Add immediately
⚠  Alexis Diaz    CL  CIN     — Shaky recently, Tejay Antone getting looks → WATCH

INJURY WATCH
━━━━━━━━━━━━
⚠ Corbin Burnes (YOUR ROSTER) — day-to-day, elbow soreness. Wire replacement: Hunter Brown
```

Point projections use **your exact ESPN scoring** (IP×3, K×1, ER×-2, BB×-1, H×-1, W×+2, SV×+5, HD×+2).

---

## Off-Season — What Happens When Baseball Ends

The MLB season runs roughly **April → early October**. Your draft is **March 23, 2026**.
That leaves two distinct off-season windows.

### October → Mid-February: True Off-Season
The bot goes into low-power mode. No daily reports needed (no games).

What it can still do:
- **Free agent signings / trade news** — MLB offseason moves affect next year's value
  - Source: MLB Stats API still works, or RSS feeds from MLB.com (free)
- **Injury updates** — Tommy John surgeries, elbow/shoulder repairs announced in Oct/Nov affect 2026 draft value
- Weekly digest instead of daily: "Here's what changed this week that affects your 2026 draft"

### Mid-February → March 23: Spring Training Mode
Spring training stats start Feb 15ish. This is **draft prep season** and the bot gets busy again.

What to track:
- **Spring training ERA, K rate** — small sample but signals role clarity (who made the rotation?)
- **Rotation battles** — who won the 5th starter job?
- **Closer competitions** — who locked up the save role?
- **Injury replacements** — who is getting starts because someone got hurt?
- **ADP movement** — how is draft position shifting as news comes out?

The morning report flips to **Draft Prep Mode:**
```
⚾ Draft Prep Report — March 10, 2026 (13 days until your draft)

ROTATION NEWS
• Rays: Shane Baz won the 5th starter battle over Taj Bradley
• Cubs: Justin Steele throwing off mound, on track for Opening Day

CLOSER LOCKS (Draft with confidence)
• Emmanuel Clase — CLE, locked in, elite
• Ryan Helsley — STL, locked in, elite

CLOSER UNCERTAINTIES (Avoid or discount)
• SF Giants — committee situation, 3 guys competing

SPRING STAT LEADERS (Min 10 IP)
• Mason Miller — 14 K in 10 IP, 0.90 ERA
```

### Draft Day — March 23, 2026
The bot can run a **live draft assistant** in your Discord:
- You tell it who was just picked: `/drafted "Paul Skenes"`
- It updates available players and re-ranks remaining options
- `/suggest` gives you the best available pitcher for your next pick
- All ranked using your exact scoring formula

### Summary of Bot Modes by Month

| Period | Mode | What It Does |
|--------|------|-------------|
| Oct–mid Feb | Low-power | Weekly offseason moves digest, injury news |
| Mid-Feb–March 22 | Draft prep | Daily spring training report, rotation/closer news, ADP tracking |
| March 23 | Draft day | Live draft assistant in Discord |
| April–early Oct | In-season | Daily 8 AM morning report, full bot commands |

### Practical Implementation
GitHub Actions makes this easy — you just change the cron schedule per mode:

```yaml
# In-season: daily at 8 AM PST (15:00 UTC)
- cron: '0 15 * * *'

# Spring training: daily at 8 AM PST, Feb 15 – March 22
- cron: '0 15 * 2,3 *'

# Off-season: weekly Sunday digest, Oct – Feb
- cron: '0 15 * 10,11,12,1,2 0'
```

---

## ⚠️ Edge Cases, Risks & Gotchas (Read Before Coding)

These are the things that will actually break. Ranked by how much pain they'll cause.

### 1. Player name matching across sources — THE #1 problem
ESPN, MLB Stats API, FanGraphs, and Baseball Savant all spell names differently.
Accents (`José Ramírez` vs `Jose Ramirez`), suffixes (`Jr.`), and duplicate names
(there are two active `Luis Ortiz` pitchers) will silently mismatch and corrupt your data.

- **Never join data on name strings.** Join on **MLBAM player ID** (the universal ID).
- ESPN exposes a player's MLBAM id; `pybaseball.playerid_lookup()` maps names ↔ ids.
- Build an ID-mapping table **once** and cache it. Budget real time for this — it's the
  unglamorous core that everything else depends on.

### 2. There is no free "who is the closer" data field
The RP filter assumes `role == "CLOSER"` exists as data. **It doesn't anywhere free.**
Closer/setup roles are editorial — maintained by sites like RotoWire/FanGraphs depth
charts (scraping required, fragile). Practical options:
- **Infer** the role from recent usage (saves + save opportunities in last ~14 days).
- Or keep a **small manually-edited list** (`closers.json`) you update every few weeks.
- Holds have the same problem — "high-leverage setup man" isn't a queryable field.

### 3. GitHub Actions cron is in UTC and ignores Daylight Saving
`0 15 * * *` = 15:00 UTC. That's **8 AM PDT** (Mar–Nov, the season) but **7 AM PST**
(winter). If you want a true 8 AM local year-round you need two cron lines or to compute
the offset in code. Also: **GitHub's scheduled jobs are best-effort and routinely run
5–30+ min late, and occasionally skip entirely** under load. Do not promise "8:00 sharp."

### 4. GitHub Actions is stateless — solved by using repo variables
There's no persistent disk between runs. This is fine because:
- The draft date is a **repo variable** (GitHub stores it, your script reads it via env var)
- Season stats are pulled fresh each run (fast enough, no cache needed)
- ESPN cookies are **repo secrets** (GitHub stores them encrypted)
- No SQLite, no config file, no shared state to worry about

### 5. ESPN `espn_s2` cookies expire and can't self-refresh
"Weeks/months" is optimistic — they can die sooner, especially if you log out or ESPN
rotates sessions. When they expire the bot 401s and goes silent. Add a startup check that
**DMs/pings you when auth fails** so you know to refresh, rather than silent failure.

### 6. pybaseball scrapes — it breaks and gets blocked
- FanGraphs/Baseball Savant sit behind Cloudflare. Requests from **datacenter IPs**
  (GitHub Actions, cloud VMs) get challenged/blocked far more than your home IP.
- When those sites change layout, pybaseball breaks until maintainers patch it.
- **Mitigations:** pull the full leaderboard **once daily and cache to SQLite** (don't
  refetch per command), add retries with backoff, and pin a known-good pybaseball version.

### 7. Probable pitchers are only known ~1–2 days out
"Who has 2 starts next week" on Sunday is an **estimate** — MLB hasn't announced those
probables yet. Rotations also shift from off-days, doubleheaders, and rainouts. Label
multi-day projections as estimates and recompute daily; don't treat them as confirmed.

### 8. Real-time news (IL moves, role changes) has no free push source
"Beat writer tweet" alerts aren't feasible for free — the Twitter/X API is paid. MLB Stats
API injury data exists but is **delayed and coarse**. Expect to learn about a closer change
hours late, not at the moment it happens. Set expectations accordingly.

### 9. Discord message limits
Plain messages cap at **2000 chars**; embeds have field/length caps too. A full morning
report can blow past this. Use embeds and/or split into multiple messages.

### 10. Early-season + spring-training samples are tiny/unreliable
In April, FIP/SwStr% over 3 starts is noise. Spring-training stats are barely covered by
pybaseball and ADP data has no clean free source — so **Draft Prep Mode is the hardest part
of this whole plan to deliver as specced.** Treat it as a stretch goal, not Phase 1.

---

## Where to Start the Code

**Do NOT start with Discord, hosting, or cron.** Start with a single local Python script
that proves the riskiest assumption — that you can pull, *join*, and score the data — and
just `print()` the result to your terminal. Hosting and Discord are the easy 10% you bolt
on last.

### Spike (build this first, locally, in order)
1. **Auth + scoring** — connect with `espn_api`, print `league.settings.stat_scores`.
   Confirms your cookies work and locks in your exact point values from real data.
2. **One data pull** — `pybaseball.pitching_stats(2025, qual=20)`, save to a CSV/SQLite
   cache. Confirms pybaseball works from your machine before you automate it.
3. **The ID join** (the hard part) — map your ESPN roster pitchers to their FanGraphs rows
   via MLBAM id. If you can reliably line up all 7 of your pitchers, you've beaten the #1
   risk. If not, fix this before anything else.
4. **Scoring function** — turn the `stat_scores` dict into a function that takes a stat
   line and returns projected points. Unit-test it against the hand-math in this doc
   (the +17 / −12 examples above).
5. **Print the report to console.** No Discord yet. Iterate on format here where it's fast.

### Only after the spike prints a correct report:
6. POST it to a **Discord webhook** (one-way, trivial — no bot account needed).
7. POST it to Discord webhook — 5 lines of `requests.post()`.
8. Push to GitHub, create `.github/workflows/morning_report.yml` with the cron schedule.

**First file to write:** `spike.py` — steps 1–5 above, ~100 lines, runs on your laptop.
Get a correct report printing locally. Everything else is plumbing.

---

## Build Order

### Phase 0 — Local Spike (start HERE, ~1–2 days)
- [ ] `espn_api` auth working, print `stat_scores` (proves cookies + locks scoring)
- [ ] One `pybaseball` pull cached to SQLite
- [ ] ESPN roster ↔ FanGraphs join on MLBAM id (the make-or-break step)
- [ ] Scoring function, unit-tested against this doc's points math
- [ ] Report prints correctly to the console

### Phase 1 — Morning Report on GitHub Actions
- [ ] Create Discord webhook URL (in Discord channel settings)
- [ ] Create GitHub repo, add secrets: `DISCORD_WEBHOOK_URL`, `ESPN_S2`, `ESPN_SWID`, `ESPN_LEAGUE_ID`
- [ ] Add variable: `DRAFT_DATE`
- [ ] Format report for Discord (stay under 2000 chars or use embeds)
- [ ] POST to Discord webhook from `spike.py`
- [ ] Create `.github/workflows/morning_report.yml` with 3 cron jobs (daily, Wed, Sunday)
- [ ] Handle the UTC/PDT offset (15:00 UTC = 8 AM PDT during season)

### Phase 2 — ESPN Integration
- [ ] Connect `espn_api` using `ESPN_S2` and `ESPN_SWID` secrets
- [ ] Pull your roster — show YOUR pitchers starting today vs wire options
- [ ] Pull opponent's roster — show matchup comparison
- [ ] Alert when a pitcher on YOUR roster has no start today

### Phase 3 — Smarter Analysis
- [ ] Regression alerts: ERA > FIP by 0.75+ → flag as "buy low"
- [ ] Breakout alerts: Stuff+ rising + SwStr% up → flag for waiver pickup
- [ ] Opponent quality: factor in opposing lineup strength
- [ ] Ballpark factors: Coors Field penalty, etc.

---

## Total Cost

| Item | Cost |
|------|------|
| pybaseball + MLB Stats API + ESPN API | $0 |
| Discord bot/webhook | $0 |
| GitHub (repo + Actions) | $0 |
| GitHub Actions (scheduled reports) | $0 |
| **Total** | **$0** |
