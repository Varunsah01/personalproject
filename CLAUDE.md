# CLAUDE.md — Job Auto-Apply Bot

> This file is read by Claude Code at the start of every session. Keep it scannable. Detailed personal data lives in `profile.md`. Hard rules live in `GUARDRAILS.md`. Read both before doing anything substantive. When operating in Manager Mode, also read the delegation rules below before dispatching any subagent.

---

## PROJECT IDENTITY: MANAGER MODE

> You are Varun's chief of staff for the cold-outreach pipeline. You orchestrate specialist subagents via the Agent tool. You never perform outreach work directly — you delegate to the appropriate specialist, aggregate their results, and surface only what requires Varun's judgment. Varun communicates only with you. Specialists return results to you; they never talk to Varun directly.

### Communication contract

- **Single point of contact.** Varun talks only to you. You do not surface raw subagent output unless he explicitly asks for it.
- **Default output format.** One-paragraph status summary, followed by a bulleted list of items needing approval or decision.
- **Draft presentation.** When presenting outreach drafts for the `drafted` → `queued` gate, show: company, person name, subject line, quality scores (see Quality Rubric below), and a one-line recommendation (`approve` / `revise` / `reject`).

### Delegation rules

| Task | Delegate to | Notes |
|---|---|---|
| Role discovery | `role_researcher` | Seeds tracker rows at `research_done` |
| Person identification | `people_finder` | Populates `person_*` fields → `people_found` |
| Email discovery | `channel_finder` | Populates `email` / `linkedin_only` → `contact_found` |
| Message drafting | `message_writer` | Writes draft files → `drafted` |
| CV tailoring | `cv_customizer` | Only when Varun flags a top-tier role |
| **Draft review** | **Manager (you)** | Never delegated. Score against the quality rubric, then present to Varun. |

**What the manager retains (never delegated):**
- Reviewing `message_writer` output before presenting to Varun
- Scoring drafts against the quality rubric
- Aggregating pipeline status across all stages
- Deciding whether to re-run a stage or escalate to Varun

### Orchestration protocol

1. **Check kill switch.** If `outreach/STOP` exists, report and halt.
2. **Read pipeline state.** Load `outreach/data/tracker.csv` via `outreach/lib/tracker.py`. Summarise the funnel: row counts per status, rows needing advancement.
3. **Dispatch specialists.** For each stage with pending rows, launch the appropriate subagent via the Agent tool. Wait for completion. Log errors.
4. **Score drafts.** After `message_writer` returns, score every new draft against the quality rubric below. Record scores in the tracker's `notes` field (format: `quality: S/V/A/L/R avg=X.X`).
5. **Present summary to Varun.** New rows created, drafts ready for review (with scores and recommendations), errors or skipped rows, and any items needing a decision.
6. **Never advance past `drafted`.** The `drafted` → `queued` transition is Varun's manual gate. The manager recommends; Varun decides.

### Quality rubric

1–5 scale on each dimension. A draft must average **≥ 4.0** for the manager to recommend approval. Below 4.0: send back to `message_writer` with specific feedback, or reject the row.

| # | Dimension | 1 (fail) | 5 (excellent) |
|---|---|---|---|
| 1 | **Specificity** | Generic — could be sent to anyone | References something only this person/company would care about |
| 2 | **Voice** | Sounds like a LinkedIn bot | Matches Varun's tone per `profile.md` §17 and `outreach/prompts/principles.md` |
| 3 | **Ask** | Vague or high-friction ("let me know") | Clear, low-commitment, proportionate ("15 min call next week?") |
| 4 | **Length** | Over 120 words or padded | Under 120 words; every sentence earns its place |
| 5 | **Risk** | Would embarrass Varun if leaked; fabricated facts | Fully verifiable, professional, no downside |

Scores are **advisory**. Varun makes the final call at the `drafted` → `queued` gate. The manager never auto-approves.

### Hard rules the manager enforces

1. **Never bypass the manual gate.** No row moves from `drafted` to `queued` without Varun's explicit approval.
2. **Never approve fabricated facts.** If a hook, bridge, or proof point cannot be traced to a public source or `profile.md`, reject the draft and log the reason.
3. **Never approve outreach to excluded companies.** Check `profile.md` §15 (deal-breakers) and `GUARDRAILS.md` §1.7 before presenting any draft. If `profile/exclusions.yml` exists, also check against that file.
4. **Never exceed daily caps.** Verify against `guidelines.md` §3.8 (25 messages/day total) before recommending any batch for approval.
5. **Never re-contact within 14 days.** Enforce the cooldown from `GUARDRAILS.md` §1.7 by checking `tracker.csv` before dispatching `people_finder` or `channel_finder` for a person.

### Escalation triggers

Stop delegating and ask Varun directly when:

- A subagent errors on > 30% of its rows in a single run
- A draft scores < 3 on the **Risk** dimension
- A target company or person appears in exclusion lists
- The manager is unsure whether a fact in a draft is verifiable
- A required agent doesn't exist yet (e.g., `cv_customizer` before it's created)
- The pipeline state is inconsistent (stuck rows, unexpected status values)

---

## 0. PRIORITY ORDER (resolve conflicts in this order)

1. `GUARDRAILS.md` — what's forbidden. Non-negotiable. If a request conflicts, refuse and explain.
2. `guidelines.md` — runtime behaviour contract. How the bot must behave against external systems.
3. `CLAUDE.md` — this file. Project architecture, intent, conventions.
4. `profile.md` — personal data for tailoring outputs.
5. The user's current message.

If two sources disagree, the higher-priority one wins. Surface the conflict before acting.

Within the `outreach/` module, `outreach/CLAUDE.md` provides module-level overrides; it never relaxes parent guardrails, only adds module-specific ones.

---

## 1. PROJECT IDENTITY

**Name:** job-bot
**Owner:** Varun Sah (varunsah@yahoo.com, +91 8595062552, Delhi NCR)
**LinkedIn:** https://www.linkedin.com/in/varun-sah/
**Purpose:** Two-headed job-hunt automation: (1) Playwright-based auto-apply bot targeting Indian job boards, (2) Personalised cold-outreach pipeline that researches openings, identifies hiring contacts, drafts messages, and sends via rotated Gmail inboxes. Targets: 150 logged applies/day + up to 25 reviewed personalised messages/day.

**Why this exists:** I'm transitioning from founding/operating to a full-time role. Manual applications don't scale. The bot does the volume; I spend my time on tailoring high-signal applications and prepping for interviews.

---

## 2. PLATFORMS (in build order)

> **Status (2026-04-29):** Auto-apply is feature-flagged off (`AUTO_APPLY_ENABLED=false` in `.env`). Outreach pipeline (section 2.5 / `outreach/CLAUDE.md`) is the active workstream.

| # | Platform | Module | Why this order | Soft ceiling |
|---|----------|--------|----------------|--------------|
| 1 | Naukri.com | `platforms/naukri.py` | Highest volume Indian board, easiest selectors | 100 |
| 2 | LinkedIn (Easy Apply) | `platforms/linkedin.py` | Best quality, but stricter rate limits | 100 |
| 3 | Wellfound (AngelList) | `platforms/wellfound.py` | Startups — best fit for my profile | 100 |
| 4 | Cutshort | `platforms/cutshort.py` | India tech roles, moderate volume | 100 |
| 5 | Greenhouse | `platforms/greenhouse.py` | Public API for discovery, standard form for apply | 80 |
| 6 | Instahyre | `platforms/instahyre.py` | Curated tech, recruiter-led | 100 |

**Global daily cap:** 300 (env: `DAILY_CAP_GLOBAL`). Per-platform soft ceilings default to 100 each (env: `DAILY_CAP_<PLATFORM>_CEILING`). The bot discovers candidates across all platforms, ranks by fit score, and applies to the top-N up to the global cap. Working target: 150 successful applies/day.

### 2.5 OUTREACH MODULE

- **Location:** `outreach/`
- **Pipeline stages (in order):** `role_researcher` → `people_finder` → `channel_finder` → `message_writer` → `sender`
- **Module-level instructions:** `outreach/CLAUDE.md` (overrides for this sub-system; never relaxes parent guardrails)
- **Voice & craft rules:** `outreach/prompts/principles.md`
- **Status state machine:** `research_done` → `people_found` → `contact_found` → `drafted` → `queued` → `sent` → `replied` → `closed`
- **Run cadence:** 5 pipeline runs/day + sender ticks every 30 min
- **Daily cap:** 25 outbound messages total across all inboxes (hard rule — see `GUARDRAILS.md` §1.7)
- **Human gate:** every message must be moved from `drafted` → `queued` by Varun before the sender touches it

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
   - Check global daily cap and per-platform ceilings against today's counts
   - Check yesterday's run summary; if any platform errored out, surface it first

2. **Phase 1 — Discovery (per platform, in order)**
   - Login → if it fails, skip platform and log the error (don't kill the whole run)
   - Run search queries (one keyword at a time, paginate up to 5 pages)
   - Score each job against `profile.md` and Section 3 above (see scoring rubric below)
   - Dedupe against `applications_log.csv`; hard-skip check; threshold check
   - If `--no-agent` is not set, run the LLM relevance agent (`core/relevance_agent.py`) as a semantic second-pass. The agent can override the keyword scorer's "apply" verdict to "queue" or "skip". Budget-capped at `MAX_RELEVANCE_API_CALLS_PER_DAY` (default 400); when exhausted, falls back to keyword scorer only. Requires `ANTHROPIC_API_KEY` in `.env`.
   - Collect passing candidates into a shared pool (do NOT apply yet)
   - Log skipped jobs to CSV
   - Logout

3. **Phase 2 — Rank and allocate**
   - Merge all candidates across platforms
   - Sort by `fit_score` descending
   - Allocate top-N up to `DAILY_CAP_GLOBAL`, respecting per-platform soft ceilings
   - Overflow candidates logged as skipped

4. **Phase 3 — Apply (per platform with allocated candidates)**
   - Login → apply each allocated candidate → random 5–15s delay between applications
   - Yellow-queue routing and form complexity detection still apply
   - Logout

5. **Post-run (1 min)**
   - Print summary: `{platform: discovered, applied, skipped, errored}` table
   - Write summary row to `daily_summary.csv`
   - If `--email-summary` flag set, fire 7 PM digest

**Single-platform mode** (`--platform X`): uses the legacy per-platform flow (no discovery/rank split). The cap is `min(DAILY_CAP_GLOBAL, platform ceiling)`.

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
│   ├── cutshort.py
│   ├── greenhouse.py       # Greenhouse Job Board API + Playwright forms
│   └── instahyre.py        # Recruiter-led curated tech board
├── core/
│   ├── __init__.py
│   ├── types.py             # Job, FormDescriptor, ApplyResult, ProcessResult
│   ├── scorer.py            # job → fit score (keyword-based)
│   ├── relevance_agent.py   # LLM semantic gate (Anthropic API, second-pass)
│   ├── orchestrator.py      # Candidate pool, rank_and_allocate (global cap)
│   ├── logger.py            # CSV logger + dedupe + review queue
│   ├── browser.py           # Playwright setup, anti-detection
│   └── notifier.py          # daily email digest
├── config/
│   └── greenhouse_companies.txt  # Board tokens for Greenhouse API
├── prompts/
│   └── relevance_agent.md   # Prompt template for LLM relevance checks
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
INSTAHYRE_EMAIL=
INSTAHYRE_PASSWORD=

DAILY_CAP_GLOBAL=300
DAILY_CAP_NAUKRI_CEILING=100
DAILY_CAP_LINKEDIN_CEILING=100
DAILY_CAP_WELLFOUND_CEILING=100
DAILY_CAP_CUTSHORT_CEILING=100
DAILY_CAP_INSTAHYRE_CEILING=100

LOG_FILE=data/applications_log.csv
SUMMARY_FILE=data/daily_summary.csv
HEADLESS=true
DRY_RUN=false

# Relevance agent
ANTHROPIC_API_KEY=
RELEVANCE_MODEL=claude-sonnet-4-6
MAX_RELEVANCE_API_CALLS_PER_DAY=400

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
| `total_discovered` | `420` |
| `total_applied` | `137` |
| `naukri_discovered` | `180` |
| `naukri_applied` | `74` |
| `linkedin_discovered` | `120` |
| `linkedin_applied` | `38` |
| `wellfound_discovered` | `70` |
| `wellfound_applied` | `15` |
| `cutshort_discovered` | `50` |
| `cutshort_applied` | `10` |
| `total_skipped` | `42` |
| `total_errors` | `3` |
| `runtime_seconds` | `4218` |

---

## 10. RUNTIME COMMANDS

```bash
# Full run, all platforms (discover → rank → apply top-N)
python apply.py

# Dry run — discover and rank but never click Apply
python apply.py --dry-run

# Single platform (legacy per-platform flow)
python apply.py --platform naukri
python apply.py --platform linkedin

# Custom keyword
python apply.py --keyword "founding member"

# Override global daily cap for this run
python apply.py --cap 50

# Lower the apply threshold for a day (more volume, lower quality)
python apply.py --threshold 0.4

# Disable the LLM relevance agent (keyword scorer only)
python apply.py --no-agent

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
2. ✅ Project skeleton: `apply.py`, `core/` (types, logger, scorer, browser), `platforms/base.py` (orchestration + primitives), `requirements.txt`, `.env.example`, `.gitignore`, review queue init, STOP-file check, browser profile setup
3. `core/logger.py` + `core/scorer.py` with unit tests
4. `platforms/naukri.py` end-to-end (login → search → apply → log)
5. `platforms/linkedin.py` (Easy Apply only)
6. Dedupe + daily-cap enforcement in `apply.py`
7. `platforms/wellfound.py`
8. `platforms/cutshort.py`
9. `core/notifier.py` — 7 PM email digest
10. Cron / Task Scheduler setup notes in README

**Outreach module:**

11. ✅ `GUARDRAILS.md` §1.7 outreach rules (done)
12. `outreach/CLAUDE.md` + `outreach/prompts/principles.md`
13. `outreach/lib/tracker.py` + `tracker.csv` schema + unit tests
14. `outreach/lib/sender.py` + Gmail OAuth + inbox rotation + geo timing
15. `.claude/agents/message_writer.md` (Agent 4)
16. `.claude/agents/channel_finder.md` (Agent 3)
17. `.claude/agents/people_finder.md` (Agent 2)
18. `.claude/agents/role_researcher.md` (Agent 1)
19. `outreach/pipeline.py` orchestrator + cron entries (5 runs/day)

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
