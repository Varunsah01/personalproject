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
# personalproject
