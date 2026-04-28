# CLAUDE.md — Job Auto-Apply Bot

> This file is read by Claude Code at the start of every session. Keep it scannable. Detailed personal data lives in `profile.md`. Hard rules live in `GUARDRAILS.md`. Read both before doing anything substantive.

---

## 0. PRIORITY ORDER (resolve conflicts in this order)

1. `GUARDRAILS.md` — what's forbidden. Non-negotiable. If a request conflicts, refuse and explain.
2. `guidelines.md` — runtime behaviour contract. How the bot must behave against external systems.
3. `CLAUDE.md` — this file. Project architecture, intent, conventions.
4. `profile.md` — personal data for tailoring outputs.
5. The user's current message.

If two sources disagree, the higher-priority one wins. Surface the conflict before acting.

---

## 1. PROJECT IDENTITY

**Name:** job-bot
**Owner:** Varun Sah (varunsah@yahoo.com, +91 8595062552, Delhi NCR)
**LinkedIn:** https://www.linkedin.com/in/varun-sah/
**Purpose:** Playwright-based Python bot that auto-applies to relevant jobs across Indian job boards. Target: 150 logged applications/day. Optimised for speed of getting interviews, not volume for its own sake.

**Why this exists:** I'm transitioning from founding/operating to a full-time role. Manual applications don't scale. The bot does the volume; I spend my time on tailoring high-signal applications and prepping for interviews.

---

## 2. PLATFORMS (in build order)

| # | Platform | Module | Why this order | Daily cap |
|---|----------|--------|----------------|-----------|
| 1 | Naukri.com | `platforms/naukri.py` | Highest volume Indian board, easiest selectors | 75 |
| 2 | LinkedIn (Easy Apply) | `platforms/linkedin.py` | Best quality, but stricter rate limits | 40 |
| 3 | Wellfound (AngelList) | `platforms/wellfound.py` | Startups — best fit for my profile | 30 |
| 4 | Cutshort | `platforms/cutshort.py` | India tech roles, moderate volume | 25 |
| 5 | Instahyre | `platforms/instahyre.py` | (later) curated tech, recruiter-led | 20 |

Total daily ceiling: 190. Working target: 150 successful applies/day.

---

## 3. TARGET ROLES

Pulled from `profile.md`. Use this priority when ranking jobs the bot finds.

**Tier 1 (apply aggressively):**
- Growth Manager / Head of Growth
- Product Manager / Associate PM
- Strategy & Operations
- Business Development Manager (B2B SaaS, fintech, edtech)
- Founding Team / Founding Member / Early Employee

**Tier 2 (apply if relevance score ≥ 0.6):**
- Partnerships Manager
- GTM / Go-to-Market Lead
- Revenue Operations
- VC Analyst / Associate (early-stage funds only)
- Strategy Consultant / Analyst

**Tier 3 (apply only if relevance score ≥ 0.75):**
- Marketing Manager (B2B, content, SEO)
- Account Executive / Sales (SaaS only)
- Chief of Staff
- Program Manager

**Hard skips (never apply):**
- Roles requiring 5+ years of experience as a hard floor
- Pure backend/data engineering roles
- Pure design roles (no product remit)
- Large MNC / FMCG unless the role is explicitly an early-stage product/strategy team within
- Roles that mention bond / lock-in / no-notice-period
- Field sales for non-tech (insurance agent, real estate, etc.)
- Anything outside India unless explicitly remote-from-India

---

## 4. SEARCH KEYWORDS

**Primary (rotate across queries):**
`growth manager`, `product manager`, `strategy and operations`, `business development manager`, `founding team`, `founding member`, `GTM manager`, `head of growth`, `early employee`

**Secondary:**
`partnerships manager`, `revenue operations`, `VC analyst`, `chief of staff`, `program manager`, `associate product manager`

**Niche / high-fit (lower volume, higher signal):**
`founding growth`, `founder's office`, `growth associate`, `early-stage operator`, `0 to 1`, `pre-seed analyst`

---

## 5. FILTERS (apply on every platform)

- **Location:** Delhi NCR, Delhi, Gurgaon, Noida, Bangalore, Bengaluru, Mumbai, Remote India, Pan India
- **Experience:** 1–5 years (some boards use 2–4; widen if results are sparse)
- **Date posted:** Last 7 days (Naukri/LinkedIn), last 14 days (Wellfound/Cutshort)
- **Salary:** Do NOT filter — apply broadly
- **Company size preference:** Seed → Series C, VC-backed; consulting; PE/VC funds
- **Industry preference (in order):** B2B SaaS, fintech, edtech, sales-tech, GTM tooling, AI/GenAI, marketplace

---

## 6. DAILY ROUTINE (what `apply.py` runs end-to-end)

1. **Pre-flight (30s)**
   - Load `.env`, validate every credential exists
   - Open `applications_log.csv`, build dedupe set of `(platform, job_url)` tuples
   - Check yesterday's run summary; if any platform errored out, surface it first

2. **Per platform, in order**
   - Login → if it fails, skip platform and log the error (don't kill the whole run)
   - Run search queries (one keyword at a time, paginate up to 5 pages)
   - Score each job against `profile.md` and Section 3 above (see scoring rubric below)
   - Apply if score ≥ threshold AND not already in log AND under daily cap
   - Log every attempt: applied / skipped / error
   - Random 5–15s delay between applications

3. **Post-run (1 min)**
   - Print summary: `{platform: applied, skipped, errored}` table
   - Write summary row to `daily_summary.csv`
   - If `--email-summary` flag set, fire 7 PM digest

### Scoring rubric (job → fit score 0.0–1.0)

```
score = 0.4 * title_match
      + 0.25 * stage_match        (seed-to-C = 1.0, growth-stage = 0.7, MNC = 0.2)
      + 0.15 * sector_match       (preferred sectors above)
      + 0.10 * location_match
      + 0.10 * experience_match   (within 1–5 yrs = 1.0)
```

Apply threshold per tier: T1 ≥ 0.5, T2 ≥ 0.6, T3 ≥ 0.75.

> **Source of truth:** Runtime values (thresholds, caps, tier routing) are authoritative in `guidelines.md`. Values here are descriptive summaries. If they diverge, `guidelines.md` wins.

---

## 7. ARCHITECTURE

```
job-bot/
├── CLAUDE.md                 # this file
├── GUARDRAILS.md             # hard rules
├── guidelines.md             # runtime behaviour contract
├── profile.md                # personal data
├── apply.py                  # main runner
├── .env                      # credentials (gitignored)
├── .env.example              # committed template
├── .gitignore
├── requirements.txt
├── README.md
├── platforms/
│   ├── __init__.py
│   ├── base.py              # BasePlatform: orchestration + abstract primitives
│   ├── naukri.py
│   ├── linkedin.py
│   ├── wellfound.py
│   └── cutshort.py
├── core/
│   ├── __init__.py
│   ├── types.py             # Job, FormDescriptor, ApplyResult, ProcessResult
│   ├── scorer.py            # job → fit score
│   ├── logger.py            # CSV logger + dedupe + review queue
│   ├── browser.py           # Playwright setup, anti-detection
│   └── notifier.py          # daily email digest
├── data/
│   ├── applications_log.csv
│   ├── daily_summary.csv
│   ├── review_queue.csv     # Yellow-tier jobs awaiting review
│   ├── skipped_log.csv      # for debugging false negatives
│   └── browser_profiles/    # persistent Playwright profiles per platform
└── tests/
    └── test_scorer.py        # unit tests for scoring logic
```

### Module conventions

- One platform per file. Each implements `BasePlatform` from `platforms/base.py`.
- Abstract primitives (subclass implements): `login()`, `search()`, `open_application_form()`, `fill_and_submit()`, `logout()`.
- Orchestration (base class, not overridden): `process_job()`, `run()`.
- All selectors live as class constants at the top of each platform file. When they break, that's the only place to update.
- All platform modules MUST use `core/logger.py` — no platform writes its own CSV.

---

## 8. .ENV STRUCTURE

```
NAUKRI_EMAIL=
NAUKRI_PASSWORD=
LINKEDIN_EMAIL=
LINKEDIN_PASSWORD=
WELLFOUND_EMAIL=
WELLFOUND_PASSWORD=
CUTSHORT_EMAIL=
CUTSHORT_PASSWORD=

DAILY_CAP_NAUKRI=75
DAILY_CAP_LINKEDIN=40
DAILY_CAP_WELLFOUND=30
DAILY_CAP_CUTSHORT=25

LOG_FILE=data/applications_log.csv
SUMMARY_FILE=data/daily_summary.csv
HEADLESS=true
DRY_RUN=false

# Optional for daily digest
SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
DIGEST_TO=varunsah@yahoo.com
```

A committed `.env.example` mirrors this with empty values. The real `.env` is in `.gitignore`.

---

## 9. CSV SCHEMAS

### `data/applications_log.csv`

| column | type | example |
|---|---|---|
| `date_applied` | ISO date | `2026-04-28` |
| `time_applied` | HH:MM | `14:32` |
| `platform` | enum | `naukri` |
| `company_name` | str | `Razorpay` |
| `role_title` | str | `Senior Product Manager — Payments` |
| `experience_required` | str | `3-5 years` |
| `location` | str | `Bangalore` |
| `job_url` | str | full URL |
| `fit_score` | float | `0.78` |
| `status` | enum | `applied` / `skipped` / `error` |
| `notes` | str | reason for skip / error message / "" |

### `data/daily_summary.csv`

| column | example |
|---|---|
| `date` | `2026-04-28` |
| `total_applied` | `137` |
| `naukri_applied` | `74` |
| `linkedin_applied` | `38` |
| `wellfound_applied` | `15` |
| `cutshort_applied` | `10` |
| `total_skipped` | `42` |
| `total_errors` | `3` |
| `runtime_seconds` | `4218` |

---

## 10. RUNTIME COMMANDS

```bash
# Full run, all platforms
python apply.py

# Dry run — score and log "would apply" without clicking
python apply.py --dry-run

# Single platform
python apply.py --platform naukri
python apply.py --platform linkedin

# Custom keyword
python apply.py --keyword "founding member"

# Lower the apply threshold for a day (more volume, lower quality)
python apply.py --threshold 0.4

# Just email yesterday's summary, don't run the bot
python apply.py --email-summary-only
```

---

## 11. CONVENTIONS

- **Python:** 3.10+. Type hints on every public function. `ruff` for lint, `black` for format.
- **Logging:** stdlib `logging` module, level `INFO` by default, `DEBUG` with `--verbose`.
- **Comments:** explain *why*, not *what*. The code says what it does; comments say why it had to be done that way (especially for selector workarounds).
- **Docstrings:** Google style, on every class and public function.
- **Async:** Playwright async API throughout. No blocking calls in async code.
- **Errors:** never silently swallow. Either handle and log, or let it propagate. Never `except: pass`.
- **Tests:** `pytest`. Scoring logic, dedupe, and CSV writes have unit tests. Browser flows do not (manually verified).

---

## 12. WHAT TO BUILD NEXT (ordered)

1. ✅ `CLAUDE.md`, `profile.md`, `GUARDRAILS.md`, `guidelines.md` (this commit)
2. Project skeleton: `apply.py`, `core/` (types, logger, scorer, browser), `platforms/base.py` (orchestration + primitives), `requirements.txt`, `.env.example`, `.gitignore`, review queue init, STOP-file check, browser profile setup
3. `core/logger.py` + `core/scorer.py` with unit tests
4. `platforms/naukri.py` end-to-end (login → search → apply → log)
5. `platforms/linkedin.py` (Easy Apply only)
6. Dedupe + daily-cap enforcement in `apply.py`
7. `platforms/wellfound.py`
8. `platforms/cutshort.py`
9. `core/notifier.py` — 7 PM email digest
10. Cron / Task Scheduler setup notes in README

---

## 13. NOTES TO CLAUDE CODE (working preferences)

- **Read before writing.** Always read `GUARDRAILS.md` and `profile.md` at the start of any non-trivial task. They are short.
- **Plan, then execute.** For anything > 1 file or > 50 lines, write a short plan in chat first, get a thumbs up, then code.
- **One concern per commit.** Don't bundle a feature with a refactor with a bug fix. If you notice something to fix while doing X, note it and ask whether to do it now or defer.
- **Selectors break.** When they do, ask me to paste a fresh HTML snippet of the page rather than guessing.
- **Don't fabricate data.** If you don't know my notice period / current CTC / a yes-no preference for a custom application question, ask. Never invent.
- **Optimise for clarity over cleverness.** I'm technical enough to read the code; I'm not interested in golf.
- **Surface tradeoffs.** When you make a choice (sync vs async, CSV vs SQLite, etc.), say what you ruled out and why.
- **When I say "run it," I mean the full pipeline.** When I say "test naukri," I mean `--platform naukri --dry-run` first, then real if it passes.
- **No emojis in code or commits.** Fine in chat sparingly.
- **Commit messages:** `<area>: <imperative summary>` e.g. `naukri: handle two-factor login flow`. No conventional commits prefixes (no `feat:`, `fix:`).

---

## 14. KNOWN UNKNOWNS / OPEN QUESTIONS

These are things I haven't decided and want to be asked about, not assumed:

- Do I want to pre-screen jobs into a queue for manual review before applying, or fully automated? (Default: automated for T1 ≥ 0.7, queue for T1 < 0.7 and all T2/T3.)
- Should the bot also DM recruiters on LinkedIn after applying? (Default: no, until I say otherwise.)
- Resume tailoring: do we want one resume or per-tier resumes? (Default: one master resume `Varun_Sah_CV.pdf`, until proven we need more.)
- Naukri "auto-update profile to keep it active" — yes or no? (Default: yes, daily.)

If you hit one of these, ask before assuming.
