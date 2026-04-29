# job-bot

Playwright-based Python bot that auto-applies to relevant jobs across Indian job boards. Target: 150 logged applications/day.

## Platforms (in build order)

1. **Naukri.com** — highest volume Indian board (cap: 75/day)
2. **LinkedIn Easy Apply** — best quality, stricter rate limits (cap: 40/day)
3. **Wellfound** — startups, best profile fit (cap: 30/day)
4. **Cutshort** — India tech roles (cap: 25/day)

## Setup

```bash
# Install dependencies
pip install -r requirements.txt
playwright install chromium

# Configure credentials
cp .env.example .env
# Edit .env with your platform credentials
```

## Usage

```bash
# Full run, all platforms
python apply.py

# Dry run — score and log without clicking Apply
python apply.py --dry-run

# Single platform
python apply.py --platform naukri

# Custom keyword
python apply.py --keyword "founding member"

# Lower the apply threshold for a day
python apply.py --threshold 0.4

# Email yesterday's summary only
python apply.py --email-summary-only

# Review the Yellow-tier queue
python apply.py --review-queue

# Debug logging
python apply.py --verbose
```

## Where the rules live

| File | What it controls |
|---|---|
| `GUARDRAILS.md` | Hard rules — what's forbidden, non-negotiable |
| `guidelines.md` | Runtime behaviour contract — how the bot interacts with platforms |
| `CLAUDE.md` | Architecture, conventions, build order |
| `profile.md` | Varun's personal data for tailoring applications |

Priority order when rules conflict: GUARDRAILS > guidelines > CLAUDE.md > profile.md.

## Project structure

```
apply.py              # CLI entrypoint
core/
  logger.py           # CSV logger, dedupe, review queue
  scorer.py           # Job scoring engine
  browser.py          # Playwright setup, anti-detection
  notifier.py         # Daily email digest
platforms/
  base.py             # BasePlatform abstract class
  naukri.py           # (built in step 4)
  linkedin.py         # (built in step 5)
  wellfound.py        # (built in step 7)
  cutshort.py         # (built in step 8)
data/                 # Created at runtime, gitignored
  applications_log.csv
  daily_summary.csv
  review_queue.csv
  browser_profiles/
```

## Tests

```bash
pytest
```

## Scheduling

### Linux / macOS (cron)

7 PM IST = 13:30 UTC. The entry below runs Monday–Friday and mails you if the job fails.

**Step 1 — open your crontab:**
```bash
crontab -e
```

**Step 2 — paste these lines** (adjust `BOTDIR` to your actual path):
```cron
MAILTO=varunsah@yahoo.com
BOTDIR=/Users/varunsah/Code/Job Automation

# Run the bot at 7 PM IST (13:30 UTC) on weekdays
30 13 * * 1-5 cd "$BOTDIR" && /usr/bin/python3 apply.py --email-summary >> "$BOTDIR/data/cron.log" 2>&1

# Email yesterday's summary at 7 PM IST if you want the digest without a full run
# 30 13 * * 1-5 cd "$BOTDIR" && /usr/bin/python3 apply.py --email-summary-only >> "$BOTDIR/data/cron.log" 2>&1
```

`MAILTO` makes cron email you if the command exits non-zero. If your server doesn't have a local MTA, use the wrapper script below instead.

**Step 3 — find the right Python path:**
```bash
which python3   # use this in the crontab if it differs from /usr/bin/python3
```

**Rotating the cron log** — add a `logrotate` config so the file doesn't grow unbounded:
```bash
# /etc/logrotate.d/job-bot  (or ~/logrotate.conf for a user-level setup)
/path/to/job-bot/data/cron.log {
    weekly
    rotate 8
    compress
    missingok
    notifempty
}
```

Run manually with `logrotate -f ~/logrotate.conf` or let the system cron handle it.

**Failure alert wrapper** — if `MAILTO` isn't available, use this instead of calling `apply.py` directly:

```bash
#!/usr/bin/env bash
# save as job-bot/run_with_alert.sh, chmod +x
set -euo pipefail
BOTDIR="$(cd "$(dirname "$0")" && pwd)"
LOG="$BOTDIR/data/cron.log"

cd "$BOTDIR"
if ! python3 apply.py --email-summary >> "$LOG" 2>&1; then
    tail -n 40 "$LOG" | mail -s "job-bot FAILED on $(date +%Y-%m-%d)" varunsah@yahoo.com
fi
```

Then reference the script in cron:
```cron
30 13 * * 1-5 /Users/varunsah/Code/Job\ Automation/run_with_alert.sh
```

---

### 9 AM IST daily run (current live schedule)

The bot runs every day at **9:00 AM IST (03:30 UTC)** via `scripts/run_daily.sh`.
The script finds the project root, activates `.venv` if present, and writes
output to a dated file at `data/logs/YYYY-MM-DD.log`.

**Crontab entry** — paste this with `crontab -e`:

```cron
# job-bot: run every day at 9 AM IST (03:30 UTC)
30 3 * * * /Users/varunsah/Code/Job\ Automation/scripts/run_daily.sh
```

> macOS cron requires the full absolute path — no `~`, no env-var expansion in
> the command field. The backslash-escaped space in the path is correct.

**Verify the entry was saved:**
```bash
crontab -l | grep job-bot
```

**Test the script manually before trusting cron:**
```bash
bash /Users/varunsah/Code/Job\ Automation/scripts/run_daily.sh
# Check output
cat data/logs/$(date +%Y-%m-%d).log
```

---

### macOS launchd alternative (recommended over cron on macOS)

`launchd` is the macOS-native scheduler. Unlike cron it survives sleep/wake
cycles and is visible in Console.app. Save the plist below, then load it once.

**`~/Library/LaunchAgents/com.varunsah.job-bot.plist`:**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.varunsah.job-bot</string>

    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>/Users/varunsah/Code/Job Automation/scripts/run_daily.sh</string>
    </array>

    <!-- 9:00 AM local time (set your Mac clock to IST / Asia/Kolkata) -->
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>    <integer>9</integer>
        <key>Minute</key>  <integer>0</integer>
    </dict>

    <!-- launchd captures any output the script doesn't redirect itself -->
    <key>StandardOutPath</key>
    <string>/Users/varunsah/Code/Job Automation/data/logs/launchd.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/varunsah/Code/Job Automation/data/logs/launchd.log</string>

    <key>RunAtLoad</key>  <false/>
</dict>
</plist>
```

**Install and start:**
```bash
cp com.varunsah.job-bot.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.varunsah.job-bot.plist
launchctl list | grep job-bot   # confirm it registered
```

**Run immediately to test (without waiting for 9 AM):**
```bash
launchctl start com.varunsah.job-bot
```

**Unload / disable:**
```bash
launchctl unload ~/Library/LaunchAgents/com.varunsah.job-bot.plist
```

---

### Windows (Task Scheduler)

Run this once in PowerShell (Admin) — adjust `$botDir` and the Python path:

```powershell
$botDir   = "C:\path\to\job-bot"
$python   = "C:\Users\YourName\AppData\Local\Programs\Python\Python312\python.exe"
$logFile  = "$botDir\data\cron.log"

$action   = New-ScheduledTaskAction `
    -Execute $python `
    -Argument "apply.py --email-summary >> `"$logFile`" 2>&1" `
    -WorkingDirectory $botDir

# 7 PM IST = 19:00 in your local timezone if Windows clock is set to IST
$trigger  = New-ScheduledTaskTrigger `
    -Weekly `
    -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday `
    -At "7:00PM"

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 3) `
    -RestartCount 0 `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName   "job-bot-daily" `
    -Action     $action `
    -Trigger    $trigger `
    -Settings   $settings `
    -RunLevel   Highest `
    -Force
```

To check runs: **Task Scheduler → Task Scheduler Library → job-bot-daily → History tab**.

To remove: `Unregister-ScheduledTask -TaskName "job-bot-daily" -Confirm:$false`

---

## Outreach pipeline

Personalised cold-outreach system that researches openings, identifies contacts, discovers emails, drafts messages, and sends via rotated Gmail inboxes. Target: up to 25 reviewed messages/day.

### How it works

Five stages run in sequence, each advancing rows through `outreach/data/tracker.csv`:

1. **Role Researcher** — searches Wellfound, YC, LinkedIn, Cutshort for openings matching target roles
2. **People Finder** — identifies 2-3 hiring-team contacts per company from public sources
3. **Channel Finder** — discovers verified email or marks LinkedIn-only
4. **Message Writer** — drafts personalised 4-6 sentence emails with verifiable hooks
5. **Sender** — sends queued messages via Gmail API within recipient-local time windows

**Human gate:** every draft must be manually moved from `drafted` → `queued` by Varun before the sender touches it. No automation bridges this gap.

### Daily review workflow

Review drafted messages before the sender picks them up:

```bash
# Review up to 10 drafted messages (default)
python outreach/review.py

# Review a specific row
python outreach/review.py --row-id <uuid>

# Review more at once
python outreach/review.py --limit 20
```

Actions per message:
- **[a]pprove** -- moves `drafted` to `queued` (sender can now pick it up)
- **[e]dit** -- opens the draft in `$EDITOR` (default: `vi`), then re-displays
- **[s]kip** -- leaves at `drafted` for next session
- **[r]eject** -- moves to `closed`, prompts for a reason (saved to notes)
- **[q]uit** -- exits with a summary of the session

The reviewer enforces cooldown (14 days) and suppression checks on approval. If a contact is blocked, it prints the reason and re-prompts.

### Quick funnel check

```bash
# Full dashboard: funnel counts, inbox usage, stop-file status, last 5 updates
python outreach/dashboard.py

# Break funnel down by role tier (T1/T2/T3)
python outreach/dashboard.py --by-tier

# List all drafted rows awaiting review (copy-paste IDs for review.py)
python outreach/dashboard.py --needs-review
```

### Scheduling (cron)

The pipeline runs 5x/day. The sender ticks every 30 minutes. Both entries assume the machine clock is set to IST (Asia/Kolkata).

```bash
crontab -e
```

Paste these lines (adjust `BOTDIR` and ensure `claude` is on PATH):

```cron
BOTDIR=/Users/varunsah/Code/Job Automation

# Outreach pipeline: 5 runs/day at IST 07:00, 11:00, 14:00, 17:00, 20:00
0 7,11,14,17,20 * * * cd "$BOTDIR" && claude -p "$(cat outreach/prompts/run_pipeline.md)" >> data/logs/outreach.log 2>&1

# Sender tick: every 30 min between 09:00-22:00 IST
*/30 9-22 * * *       cd "$BOTDIR" && python3 outreach/lib/sender.py --tick >> data/logs/sender.log 2>&1

# Outreach digest: daily 8 PM IST (14:30 UTC)
30 14 * * *           cd "$BOTDIR" && python3 -m outreach.lib.digest --send >> data/logs/outreach-digest.log 2>&1
```

> **Note:** if your machine uses UTC, convert IST times: 07:00 IST = 01:30 UTC, 09:00 IST = 03:30 UTC, 22:00 IST = 16:30 UTC.

### Setup

**1. Google Cloud Console — OAuth registration**

```bash
# Create a project, enable Gmail API, create OAuth Desktop credentials
# Download client_secret.json, then run once per inbox:
python outreach/lib/setup_gmail_oauth.py \
    --client-secret path/to/client_secret.json \
    --inbox-address your.outreach1@gmail.com
```

**2. Configure `.env.outreach`**

```bash
cp .env.outreach.example .env.outreach
# Fill in inbox addresses and token paths (1-5 inboxes supported)
```

**3. Verify subagents are loaded**

```bash
# List available agents (should show role_researcher, people_finder,
# channel_finder, message_writer)
ls .claude/agents/
```

### First-run procedure

Do not go straight to production. Follow this sequence:

1. **Dry-run all stages:**
   ```bash
   python outreach/pipeline.py --stage all --dry-run
   ```
   Verify agents load, read the right files, and produce sensible output.

2. **Run research + people + channel for real** (small batch):
   ```bash
   python outreach/pipeline.py --stage research
   python outreach/pipeline.py --stage people
   python outreach/pipeline.py --stage channel
   ```
   Check `outreach/data/tracker.csv` — rows should be at `contact_found`.

3. **Run message writer:**
   ```bash
   python outreach/pipeline.py --stage write
   ```
   Review every draft in `outreach/data/drafts/`. Reject anything generic, fabricated, or off-voice.

4. **Hand-review 5 drafted messages** in chat. If 3+ are rejected, revisit `outreach/prompts/principles.md` before continuing.

5. **Review and approve drafts:**
   ```bash
   python outreach/review.py
   ```
   Walk through each drafted message: approve, edit, skip, or reject. Approved drafts move to `queued`; rejected ones move to `closed`.

6. **Watch the sender fire it:**
   ```bash
   python outreach/lib/sender.py --tick
   ```

7. **Audit the .eml** in `outreach/data/sent/{date}/`. Confirm the subject, body, and recipient match what you approved.

8. Once satisfied, enable the cron entries and let it run.

### Emergency stop

Create the STOP file to halt everything immediately:

```bash
touch outreach/STOP
```

Both the pipeline and the sender check for this file before every operation. Remove it to resume:

```bash
rm outreach/STOP
```
# personalproject
