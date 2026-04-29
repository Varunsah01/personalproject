# guidelines.md — Automation Scope & Rules

> Defines what the bot is allowed to automate, where the boundary between automated and human-in-the-loop sits, and the operational rules every automation run must follow. This is the **behavioural contract** of the bot — distinct from `GUARDRAILS.md` (which forbids actions) and `CLAUDE.md` (which describes the project). If automation behaviour conflicts with `GUARDRAILS.md`, guardrails win.

---

## 1. PHILOSOPHY

The bot exists to do the **mechanical** parts of job hunting at volume: searching, filtering, deduping, scoring, filling standard forms, logging, and reporting. It does **not** exist to do the parts that need judgement: which 10 companies are worth a real cover letter, which recruiter to DM, which offer to take.

**Rule of thumb:** if a human at a job platform would notice the action looks robotic and penalise the account, the bot doesn't do it. If a recruiter would notice the application looks copy-pasted, the bot tagged it as "auto" and Varun decides whether to send a tailored version too.

**Three-tier model:**

| Tier | What happens | Human involvement |
|---|---|---|
| **Green** | Bot acts autonomously | None during run; review summary daily |
| **Yellow** | Bot prepares + queues; Varun approves | Daily 10-min review |
| **Red** | Manual only | Full human authorship |

Most volume should sit in Green. Quality applications belong in Yellow or Red.

---

## 2. AUTOMATION SCOPE

### 2.1 Green — fully automated

The bot may do these without per-action approval, as long as daily caps and guardrails hold.

- Logging into existing accounts using `.env` credentials
- Running search queries across configured keywords
- Reading and parsing job listings
- Scoring jobs against the rubric in `CLAUDE.md` §6
- Deduping against `applications_log.csv`
- Filling **standard** application fields (name, email, phone, location, current CTC if explicitly set in `.env`, expected CTC if explicitly set in `.env`, notice period, years of experience, LinkedIn URL)
- Uploading the master resume PDF
- Clicking "Easy Apply" / "Quick Apply" / "1-Click Apply" buttons where the form contains only standard fields
- Logging every attempt (applied / skipped / errored) to CSV
- Writing the daily summary row
- Sending the 7 PM digest email to Varun himself (configured in `.env`)

### 2.2 Yellow — prepare, queue, require approval

The bot prepares the action, writes it to a queue, and waits for Varun's review. Approval happens via a CLI command (`python apply.py --review-queue`) or in chat with Claude Code.

- Applications to roles with a fit score in **[0.5, 0.7)** in Tier 1, or any T2/T3 role
- Applications that require **non-standard** form fields (custom essay questions, "why this company," "describe a time when…")
- Applications to companies marked as **high-priority** in a `priority_companies.txt` file (when this file exists) — these always get human review even if the score is high
- Resume tailoring beyond the master CV
- Cover letter generation
- Any first-time application to a new platform (the very first apply on Naukri, then LinkedIn, etc.) — supervised live, then promoted to Green

### 2.3 Red — never automated

These are off-limits to the bot regardless of approval. They require Varun to act manually.

- Sending DMs / InMails / messages to recruiters or hiring managers
- Replying to messages from recruiters
- Phone screens, video interviews, or any synchronous interaction
- Salary negotiation in any form
- Accepting or declining offers
- Posting publicly (status updates, comments, "open to work" toggles)
- Endorsing skills, requesting recommendations, sending connection requests
- Filling fields that require fabrication (current CTC if not set, current employer if not set, references)
- Anything that involves another human's data being entered or modified

### 2.4 Outreach tier mapping

The same Green/Yellow/Red model applies to the outreach pipeline.

**Green (auto):**
- Role research (scraping public job listings for context)
- People identification (finding hiring managers / team leads from public sources)
- Channel / email discovery (public LinkedIn profiles, company pages, free Hunter.io tier)
- Draft writing (generating message drafts into `tracker.csv` with status `drafted`)
- Status updates in `tracker.csv` (e.g., marking `sent`, `replied`, `closed`)
- Archiving sent messages

**Yellow (queue + approve):**
- Nothing in outreach moves directly from `drafted` → `sent`. Varun manually changes status from `drafted` to `queued` — this is the approval signal. The sender only processes rows with status `queued`.

**Red (manual only):**
- Replies to inbound recruiter messages
- Messages to anyone who has previously declined or asked not to be contacted
- Messages mentioning sensitive personal context about the recipient (e.g., a recent loss, health issue, or personal event not shared publicly)

---

## 3. AUTOMATION RULES

### 3.1 Pre-flight (before any platform run)

1. Load `.env` and verify every credential needed for the platforms in scope. Missing credential → skip that platform with a clear log entry; don't crash.
2. Open `applications_log.csv`. Build the dedupe set as `(platform, normalised_job_url)`. Normalised = stripped of tracking params (`?utm_*`, `?ref=*`, `?source=*`, fragment hashes).
3. Read today's row in `daily_summary.csv` if it exists. Sum applied counts per platform. If a platform is already at cap, skip it.
4. Check the last 24 hours of error rate per platform. If > 25% of attempts errored, **don't run that platform today** — log the throttle and notify Varun.
5. Confirm the resume file exists and is < 60 days old. If older, warn but proceed.

### 3.2 Per-application flow

For every job the bot considers applying to:

1. **Dedupe check.** If `(platform, normalised_url)` is in the log, skip with `notes="duplicate"`.
2. **Hard-skip check.** If the JD contains any deal-breaker pattern from `profile.md` §15 (regex match on title and first 500 chars of JD), skip with `notes="hard skip: <reason>"`.
3. **Score.** Compute `fit_score` per `CLAUDE.md` §6.
4. **Threshold check.** Below tier threshold → skip with `notes="below threshold: 0.42"`.
5. **Cap check.** In multi-platform mode, candidates are pooled and ranked by fit_score; the global cap (`DAILY_CAP_GLOBAL`, default 300) limits total applies across all platforms, and per-platform soft ceilings (`DAILY_CAP_<PLATFORM>_CEILING`, default 100) prevent any single platform from dominating. In single-platform mode, the effective cap is `min(global_cap, platform_ceiling)` — stop when reached.
6. **Tier route.**
   - Green → apply directly
   - Yellow → write to `data/review_queue.csv` with full job context; do NOT apply
7. **Apply (Green only).**
   - Open the application form
   - Detect form complexity: if any field requires text > 100 chars or any field is unrecognised → **demote to Yellow** (write to queue, log as `skipped — promoted to review`)
   - Fill recognised fields from `.env` and `profile.md`
   - Upload resume
   - Click submit
   - Wait for confirmation page or success state
   - Log the result
8. **Delay.** Random 5–15 seconds before the next application. Random 30–90 seconds between search-result pages.

### 3.3 Rate limiting & anti-detection

- Global daily cap: `DAILY_CAP_GLOBAL` (default 300). Per-platform soft ceilings: `DAILY_CAP_<PLATFORM>_CEILING` (default 100 each).
- Total session length per platform: max 90 minutes. Beyond that, log out and wait 4+ hours before re-running that platform.
- Browser context: persistent profile per platform, stored in `data/browser_profiles/<platform>/`. Don't recreate every run — looks suspicious.
- User agent: real, recent Chrome on Windows. Don't randomise per session.
- Headless: configurable via `.env` (`HEADLESS=true|false`). Default true. For debugging, run with `HEADLESS=false`.
- No parallel platform runs by default. Sequential. Parallel is allowed only if Varun sets `PARALLEL=true` and accepts the detection risk.
- Mouse/keyboard: use Playwright's built-in human-like input (`type` with `delay`, not `fill`, for any free-text field).

### 3.4 Error handling

| Error type | Action |
|---|---|
| Login failed (wrong credentials) | Stop platform, alert Varun, do NOT retry automatically |
| Login failed (2FA / CAPTCHA / unusual login challenge) | Stop platform, alert Varun, log `auth_challenge` |
| Selector not found | Retry once after 3s. If still fails, log `selector_broken: <selector>` and skip the application. After 5 such failures in a session, stop the platform. |
| Network error / timeout | Retry up to 2 times with exponential backoff (5s, 15s). Then skip. |
| Form submission appears successful but no confirmation | Log as `applied_unconfirmed` — count toward cap but flag for manual check |
| Unknown exception | Log full stack trace, mark application `error`, move to next job. Never crash the run. |

### 3.5 Logging discipline

Every job the bot looks at gets exactly one row in `applications_log.csv`. No silent drops. Statuses:

- `applied` — submitted and confirmed
- `applied_unconfirmed` — submitted but no confirmation page seen
- `queued` — sent to Yellow review queue
- `skipped` — intentionally not applied (always include reason in `notes`)
- `error` — something broke (always include error message in `notes`)

`notes` field examples:
- `duplicate`
- `hard skip: 7+ years required`
- `hard skip: outside India`
- `below threshold: 0.42`
- `cap reached: naukri 75/75`
- `promoted to review: custom essay`
- `selector_broken: button[data-test=apply]`
- `auth_challenge: captcha`

### 3.6 The Yellow review queue

`data/review_queue.csv` schema:

| column | description |
|---|---|
| `queued_at` | ISO timestamp |
| `platform` | naukri / linkedin / etc. |
| `company_name` | str |
| `role_title` | str |
| `job_url` | str |
| `fit_score` | float |
| `tier` | T1/T2/T3 |
| `reason_queued` | enum: `score_in_review_band`, `custom_questions`, `priority_company`, `first_time_platform` |
| `custom_questions` | JSON array of `{question, suggested_answer}` if applicable |
| `expires_at` | ISO timestamp, queued_at + 5 days |

**Review workflow:**
- `python apply.py --review-queue` opens an interactive prompt that walks through pending items.
- For each: show fit score, JD link, suggested answers, skip/apply/draft choice.
- If `apply`: bot finishes the submission with the user-confirmed answers.
- If `skip`: log to `applications_log.csv` with `notes="reviewed: skipped"`.
- If `draft`: open the JD in the default browser, mark as `notes="reviewed: manual"`, remove from queue.
- After `expires_at`, items are auto-skipped with `notes="queue expired"`.

### 3.7 Daily reporting

Every run ends with:
1. A row appended to `daily_summary.csv` (schema in `CLAUDE.md` §9).
2. A stdout summary table: counts per platform, top 5 highest-score applies of the day, queue size.
3. If `--email-summary` flag set: a digest email to `DIGEST_TO` (= Varun) at the end of the run, or at 7 PM if scheduled separately.

Digest email contents:
- Date, total applied, per-platform breakdown
- Top 10 applies by fit score (company, role, score, URL)
- Queue items needing review (count + first 5)
- Errors / throttles
- Yesterday-vs-today delta

### 3.8 Outreach rate limits and timing

- **Daily cap:** 25 sends total across all inboxes combined. Hard ceiling — see `GUARDRAILS.md` §1.7.
- **Per-inbox cap:** 25/day max per single inbox. Rotation picks the inbox with the lowest send count for the day.
- **Send windows:** 10:00–11:00 OR 14:00–15:00 in the *recipient's local timezone*. Never outside these windows. Never on Saturday or Sunday in recipient timezone.
- **Pipeline runs:** 5/day at 07:00, 11:00, 14:00, 17:00, 20:00 IST. Each run advances rows through the research → draft stages.
- **Sender tick:** every 30 minutes between 09:00–22:00 IST. Each tick fires only `queued` rows whose `send_at_utc` falls within the past 30-minute window.
- **Stop-file:** `outreach/STOP` — if this file exists, the sender exits immediately without sending. Same pattern as the apply-bot's `data/STOP`.

---

## 4. EVOLUTION RULES

These govern how the bot itself changes over time, not what it does on a given run.

### 4.1 New platforms
- A new platform module starts in **supervised mode**: every apply is Yellow for the first 20 applications, regardless of score.
- After 20 successful Yellow applies with no false positives (wrong company, wrong role family, fabricated data), promote to Green.
- New platform must implement the full `BasePlatform` interface and pass dedupe + cap unit tests before its first real run.

### 4.2 Threshold tuning
- Apply thresholds (T1 ≥ 0.5, T2 ≥ 0.6, T3 ≥ 0.75) are reviewed weekly based on:
  - Interview-to-application conversion (target ≥ 3%)
  - Recruiter response rate (target ≥ 8%)
  - Manual sanity check on a random sample of 20 applied jobs
- Tightening (raise threshold) is allowed any time. Loosening (lower threshold) requires a week of data.

### 4.3 Profile drift
- `profile.md` is the source of truth. The bot reads it at start of each run.
- If the bot ever needs a fact not in `profile.md`, it logs `missing_profile_field: <field>` and Varun adds it.
- The bot never auto-edits `profile.md`.

### 4.4 Selector maintenance
- Each platform's selectors live as constants at the top of its module.
- When a selector breaks, the bot logs the exact selector that failed.
- Fixing selectors is a Yellow-tier task: Claude Code can propose a fix but Varun reviews before merging — wrong selectors silently break the bot for days.

---

## 5. METRICS THAT MATTER (track weekly)

| Metric | Target | What it tells us |
|---|---|---|
| Daily applies (rolling 7d avg) | 120–150 | Are we running? |
| Apply success rate | ≥ 90% | Is the bot healthy? |
| Recruiter response rate | ≥ 8% | Is the targeting right? |
| Interview rate (response → screen) | ≥ 30% | Are recruiters seeing fit? |
| Fit-score accuracy (sampled) | ≥ 80% | Is the rubric calibrated? |
| Queue review latency | < 24h | Is Varun reviewing? |
| Hard-skip rate | < 30% of seen | Are search keywords too broad? |

If any metric trips for 5+ days, treat it as a signal to retune — don't keep running blindly.

---

## 6. KILL SWITCHES

The bot stops itself when:
- Any platform throws auth-challenge errors (CAPTCHA, 2FA, account lock) → stop that platform for 24h
- Error rate on a platform exceeds 25% in a session → stop that platform for the day
- Total errors across all platforms exceed 50 in a session → stop the entire run
- `data/STOP` file exists in the repo root → stop everything immediately, don't even start
- Disk space on the volume holding `data/` drops below 500 MB → stop

To stop manually mid-run: `touch data/STOP` from another terminal. The bot checks for this file between every application.

---

## 7. VERSIONING

**Version:** 1.1
**Last updated:** 2026-04-29
**Changelog:**
- 1.1 (2026-04-29): Added §2.4 outreach tier mapping; added §3.8 outreach rate limits and timing.
- 1.0 (2026-04-28): Initial version. Three-tier scope model (Green/Yellow/Red), rate-limit and anti-detection rules, error-handling matrix, kill switches, evolution rules.
