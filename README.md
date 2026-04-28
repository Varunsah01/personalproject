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
# personalproject
