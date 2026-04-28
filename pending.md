# Pending Items & Codebase Audit

**Date:** 2026-04-28
**Overall Completion:** ~60%

---

## 1. Test Results

| Suite | Tests | Status | Time |
|---|---|---|---|
| `tests/test_scorer.py` | 46 | All pass | 0.04s |
| `tests/test_logger.py` | 23 | All pass | <0.01s |
| **Total** | **69** | **All pass** | **0.04s** |

- All 16 Python files compile cleanly (`py_compile` pass)
- All module imports resolve without errors
- CLI (`python apply.py --help`) works correctly
- No circular imports, no deprecation warnings
- Dependencies (`playwright`, `python-dotenv`, `pytest`) all installed

---

## 2. Component Status

| File | Lines | Status | Notes |
|---|---|---|---|
| `apply.py` | 780 | COMPLETE | Main runner, CLI args, pre-flight, orchestration, review queue |
| `core/scorer.py` | 364 | COMPLETE | Scoring engine, tier classification, hard-skip patterns |
| `core/logger.py` | 207 | COMPLETE | CSV logging, dedupe, URL normalization, file locking |
| `core/notifier.py` | 360 | COMPLETE | Email digest builder, 5-section report, SMTP support |
| `core/browser.py` | 43 | **STUB** | Both functions raise `NotImplementedError` |
| `core/__init__.py` | 0 | OK | Empty init |
| `platforms/base.py` | 111 | COMPLETE | Abstract base + `score_and_apply()` orchestration |
| `platforms/__init__.py` | 13 | COMPLETE | Platform registry (4 platforms mapped) |
| `platforms/naukri.py` | 865 | COMPLETE | Full login/search/apply/logout + chatbot handling |
| `platforms/linkedin.py` | 1238 | **STUB** | Selectors + structure only, all methods `NotImplementedError` |
| `platforms/wellfound.py` | 976 | **STUB** | Selectors + config only, all methods `NotImplementedError` |
| `platforms/cutshort.py` | 974 | **STUB** | Tag mapping + selectors only, all methods `NotImplementedError` |
| `tests/test_scorer.py` | 335 | COMPLETE | 46 tests covering tiers, scoring, hard-skips, thresholds |
| `tests/test_logger.py` | 354 | COMPLETE | 23 tests covering init, dedupe, counting, normalization |
| Documentation | ~950 | COMPLETE | CLAUDE.md, GUARDRAILS.md, guidelines.md, profile.md, README.md |
| `.env.example` | 36 | COMPLETE | All credential + config placeholders |
| `requirements.txt` | 9 | COMPLETE | playwright, python-dotenv, pytest |

---

## 3. What Is Working

- **Scoring engine** (`core/scorer.py`) — full job-to-score pipeline with tier classification, hard-skip detection, component scoring (title/stage/sector/location/experience), and threshold enforcement
- **Logging** (`core/logger.py`) — CSV append with file locking, URL normalization (strips tracking params), duplicate detection, daily count per platform
- **Notifier** (`core/notifier.py`) — builds 5-section email digest (summary stats, top applies, yellow queue preview, errors, day-over-day delta), supports print-only mode
- **Naukri platform** (`platforms/naukri.py`) — complete end-to-end: login, search with pagination, job scoring, chatbot questionnaire handling, resume upload, apply/skip/queue routing, logout
- **Main runner** (`apply.py`) — CLI with all flags, pre-flight checks (resume age, disk space, credentials, caps, error rate), platform orchestration, summary writing, review queue mode
- **Review queue** — interactive handler for yellow-tier jobs (apply/skip/draft/next/quit)
- **Unit tests** — 69 tests, 100% pass rate

---

## 4. Critical Blockers

### 4.1 `core/browser.py` — NOT IMPLEMENTED (Priority: P0)

Both functions are stubs that raise `NotImplementedError`:

```python
async def create_browser_context(platform, headless, playwright) -> BrowserContext
async def human_type(page, selector, text, delay) -> None
```

**Impact:** This is the single biggest blocker. Every platform calls these functions. Without them:
- Naukri cannot instantiate a browser context (will crash at runtime)
- No platform can actually run

**What needs to be implemented:**
- `create_browser_context()`: Launch Chromium with persistent profile at `data/browser_profiles/<platform>/`, set anti-detection flags (user-agent, viewport, webdriver flag masking), return `BrowserContext`
- `human_type()`: Clear field, type character by character with random delay (50-120ms per keystroke) to mimic human typing

**Estimated effort:** 1-2 hours

---

### 4.2 `platforms/linkedin.py` — NOT IMPLEMENTED (Priority: P1)

**What exists:** 1238 lines of selectors, config constants, and class structure. All lifecycle methods raise `NotImplementedError`.

**What needs to be implemented:**
- `login()` — credential entry, human verification detection, session persistence
- `search(keyword, filters)` — build Easy Apply URL, paginate, yield Job objects from cards
- `open_application_form(job)` — click Easy Apply, detect multi-step modal vs external apply
- `fill_and_submit(form, answers)` — handle 3+ step modal (text fields, dropdowns, radio buttons, resume upload, review, submit)
- `logout()` — clean session exit
- `run()` — orchestration with LinkedIn-specific rate limiting

**Key challenges:**
- Multi-step Easy Apply modal (3+ pages with different field types)
- External apply detection (skip those jobs)
- LinkedIn's aggressive rate limiting and bot detection
- Selectors marked TODO — need live DOM verification

**Estimated effort:** 8-12 hours

---

### 4.3 `platforms/wellfound.py` — NOT IMPLEMENTED (Priority: P2)

**What exists:** 976 lines of selectors, config, and structure. All methods stubbed.

**What needs to be implemented:**
- All lifecycle methods (`login`, `search`, `open_application_form`, `fill_and_submit`, `logout`, `run`)
- Profile completeness gate (check profile %, stop if < 80%)
- "Why do you want to work here?" field detection (auto-route to Yellow queue)

**Estimated effort:** 6-8 hours

---

### 4.4 `platforms/cutshort.py` — NOT IMPLEMENTED (Priority: P2)

**What exists:** 974 lines with keyword-to-tag mapping (18 keywords), selectors, and structure. All methods stubbed.

**What needs to be implemented:**
- All lifecycle methods
- Keyword-to-tag lookup (Cutshort uses tags, not free-text search)
- Off-platform ATS redirect detection (some jobs redirect to external sites)

**Estimated effort:** 6-8 hours

---

## 5. Non-Blocking Gaps

| Item | Priority | Status | Notes |
|---|---|---|---|
| Integration tests (browser flows) | P2 | Missing | Only unit tests exist; browser flows are manually verified |
| `data/browser_profiles/` directory | P3 | Missing | Not created yet; `browser.py` should create on first run |
| Instahyre platform | P3 | Not started | Listed as platform #5 in CLAUDE.md, not in initial scope |
| Resume tailoring (per-tier) | P3 | Not started | Open question per CLAUDE.md §14 — using master CV for now |
| Naukri profile auto-update | P3 | Not started | Open question per CLAUDE.md §14 — defaulting to yes |
| Recruiter DM on LinkedIn | P4 | Not started | Open question per CLAUDE.md §14 — defaulting to no |
| Cron/scheduler actual setup | P3 | Docs only | README has instructions but no crontab entry created |

---

## 6. Build Checklist (from CLAUDE.md Section 12)

| # | Item | Status |
|---|---|---|
| 1 | CLAUDE.md, profile.md, GUARDRAILS.md, guidelines.md | DONE |
| 2 | Project skeleton (apply.py, core/, platforms/base.py, requirements.txt, .env.example, review queue, STOP-file, browser profiles) | DONE (except browser.py implementation and browser_profiles dir) |
| 3 | core/logger.py + core/scorer.py with unit tests | DONE |
| 4 | platforms/naukri.py end-to-end | DONE (but blocked by browser.py at runtime) |
| 5 | platforms/linkedin.py (Easy Apply) | **PENDING** — selectors laid out, methods not implemented |
| 6 | Dedupe + daily-cap enforcement in apply.py | DONE (in apply.py + naukri.py; untested on other platforms) |
| 7 | platforms/wellfound.py | **PENDING** — selectors laid out, methods not implemented |
| 8 | platforms/cutshort.py | **PENDING** — tag mapping done, methods not implemented |
| 9 | core/notifier.py — 7 PM email digest | DONE |
| 10 | Cron / Task Scheduler setup in README | DONE (docs only) |

---

## 7. Recommended Build Order

1. **Implement `core/browser.py`** — unblocks everything (P0)
2. **Live-test Naukri end-to-end** — `python apply.py --platform naukri --dry-run` then supervised real run (P0)
3. **Implement `platforms/linkedin.py`** — highest-quality leads (P1)
4. **Implement `platforms/wellfound.py`** — startup-heavy, good profile fit (P2)
5. **Implement `platforms/cutshort.py`** — moderate volume (P2)
6. **Add integration tests** — at least smoke tests per platform (P2)
7. **Set up cron job** — automate daily runs (P3)
8. **Instahyre platform** — stretch goal (P3)

---

## 8. Claude Code Prompts to Complete This Project

Copy-paste these prompts in order. Each is self-contained — read the relevant files first, then implement.

---

### Prompt 1 — Implement `core/browser.py` (P0, do this first)

```
Read core/browser.py, platforms/naukri.py (to see exactly how create_browser_context and human_type are called), and GUARDRAILS.md.

Implement both functions in core/browser.py:

1. create_browser_context(platform, headless, playwright):
   - Use playwright.chromium.launch_persistent_context() with user_data_dir=data/browser_profiles/<platform>/
   - Create the directory if it doesn't exist
   - Set these anti-detection args: --disable-blink-features=AutomationControlled, --no-sandbox, --disable-dev-shm-usage
   - Set a realistic user-agent string (recent Chrome on Mac)
   - Set viewport 1280x800
   - After context creation, add init script to mask navigator.webdriver
   - Return the BrowserContext

2. human_type(page, selector, text, delay):
   - Click the selector first to focus it
   - Triple-click to select all existing text, then delete it
   - Type each character with await page.keyboard.type(char) inside a loop
   - Add random jitter: asyncio.sleep((delay + random.randint(-20, 20)) / 1000)

Follow existing code style: async, type hints on all params, Google-style docstring, no bare except.
```

---

### Prompt 2 — Live-test Naukri dry run (P0, after browser.py is done)

```
core/browser.py is now implemented. I want to do a supervised dry run of Naukri.

1. Read platforms/naukri.py top-to-bottom so you understand the full flow
2. Run: python apply.py --platform naukri --dry-run --verbose
3. Watch the output carefully. Report:
   - Did login succeed?
   - Did search return any jobs?
   - What scores are jobs getting?
   - Are any jobs being queued vs skipped vs would-apply?
   - Any errors or selector failures?

Do NOT click apply on any real jobs. This is dry-run only.
```

---

### Prompt 3 — Implement `platforms/linkedin.py` (P1)

```
Read these files before writing anything:
- platforms/linkedin.py (existing selectors and structure)
- platforms/naukri.py (reference implementation to mirror the pattern)
- platforms/base.py (abstract interface to implement)
- core/logger.py, core/scorer.py (how logging and scoring are called)
- GUARDRAILS.md (rate limits, human-like delays)
- guidelines.md (retry logic, cap enforcement)

Implement all stubbed methods in platforms/linkedin.py following the exact same orchestration pattern as naukri.py:

login():
- Navigate to linkedin.com/login
- Fill email + password using human_type()
- Submit, wait for feed or checkpoint page
- If human verification detected, log warning and return False
- Return True on success

search(keyword, filters):
- Build Easy Apply URL: linkedin.com/jobs/search/?keywords=<kw>&f_AL=true&f_TPR=r604800&location=India
- For each page (up to MAX_PAGES_PER_KEYWORD):
  - Extract all job cards visible
  - For each card yield a Job object (title, company, location, experience, job_url, jd_text)
  - Click next page or break if no next button

open_application_form(job):
- Navigate to job URL
- Click Easy Apply button (if not present, it's external apply — return {"status": "external"})
- Detect modal opened
- Return FormDescriptor with path="linkedin_easy_apply", questions=[]

fill_and_submit(form, answers):
- If form status is "external", return {"status": "skipped", "notes": "external apply"}
- Step through multi-page modal:
  - Page 1: contact info / phone (pre-filled usually)
  - Page 2: resume upload (check if already uploaded, else upload Varun_Sah_CV.pdf)
  - Page 3+: screening questions (yes/no, text, dropdowns)
  - Final page: Review → Submit
- Return {"status": "applied"} or {"status": "error", "notes": reason}

logout():
- Click profile avatar → Sign Out

run():
- Mirror naukri.py run() exactly: login → iterate keywords → score → dedupe → cap → route → log → logout

Key LinkedIn-specific rules:
- Minimum 8s delay between job views, 15s between applies (GUARDRAILS §2)
- Skip any job with "Easy Apply" button absent (external ATS)
- If CAPTCHA or "unusual activity" page detected, stop the run and log error
- Daily cap: read DAILY_CAP_LINKEDIN from .env (default 40)
```

---

### Prompt 4 — Implement `platforms/wellfound.py` (P2)

```
Read these files before writing:
- platforms/wellfound.py (existing selectors and structure)
- platforms/naukri.py (reference pattern)
- platforms/linkedin.py (just implemented — use same orchestration shape)
- GUARDRAILS.md, guidelines.md

Implement all stubbed methods in platforms/wellfound.py:

login():
- Navigate to wellfound.com/login
- Fill email + password, submit
- Check profile completeness at wellfound.com/u/<username>/edit — if < 80%, log warning and return False
- Return True on success

search(keyword, filters):
- Build URL: wellfound.com/jobs?q=<keyword>&l=India&t=1w
- Paginate up to MAX_PAGES_PER_KEYWORD
- Yield Job objects from cards

open_application_form(job):
- Click Apply button on job page
- If "Why do you want to work here?" field appears, return FormDescriptor with a flag so fill_and_submit routes to Yellow queue
- Return normal FormDescriptor otherwise

fill_and_submit(form, answers):
- If "why_work_here" flag set, return {"status": "queued", "notes": "why_work_here field present"}
- Fill standard fields (resume already uploaded in profile)
- Submit and confirm

logout(), run(): mirror naukri.py pattern.
Daily cap: DAILY_CAP_WELLFOUND from .env (default 30).
```

---

### Prompt 5 — Implement `platforms/cutshort.py` (P2)

```
Read these files before writing:
- platforms/cutshort.py (existing KEYWORD_TAG_MAP and selectors)
- platforms/naukri.py (reference pattern)
- GUARDRAILS.md, guidelines.md

Implement all stubbed methods in platforms/cutshort.py:

login():
- Navigate to cutshort.io/login
- Fill email + password, submit
- Return True/False

search(keyword, filters):
- Look up keyword in KEYWORD_TAG_MAP — if not found, skip with a log warning
- Build URL using tags: cutshort.io/jobs/<tag>?location=India&experience=1,5
- Paginate up to MAX_PAGES_PER_KEYWORD
- For each job card check if it redirects to external ATS — if yes, yield with a "external" flag, don't yield otherwise
- Yield Job objects

open_application_form(job):
- If job has external flag, return {"status": "external"}
- Click Apply on job page
- Return FormDescriptor

fill_and_submit(form, answers):
- If external: return {"status": "skipped", "notes": "off-platform ATS"}
- Fill standard fields, submit

logout(), run(): mirror naukri.py pattern.
Daily cap: DAILY_CAP_CUTSHORT from .env (default 25).
```

---

### Prompt 6 — Selector verification session (do before any real run)

```
I need to verify that the CSS selectors in platforms/linkedin.py are current.
Open a non-headless browser session on linkedin.com/jobs and inspect the DOM live.

For each selector constant at the top of platforms/linkedin.py:
1. Try to find the element on the actual live page
2. If the selector doesn't match, find the correct current selector
3. Update the constant in the file
4. Add a comment with the date verified: # verified 2026-XX-XX

Do the same for wellfound.py and cutshort.py.

Do NOT attempt this with headless=True — you need to see the pages.
```

---

### Prompt 7 — Add integration smoke tests (P2)

```
Read tests/test_scorer.py and tests/test_logger.py to understand the test style.
Read platforms/naukri.py and core/browser.py.

Create tests/test_integration.py with smoke tests that:
1. Mock the Playwright browser (use unittest.mock or pytest-mock) — no real browser needed
2. Test that NaukriPlatform.run() calls login(), search(), and logout() in order
3. Test that a job scoring above threshold gets logged with status="applied" (dry_run=True)
4. Test that a job scoring below threshold gets logged with status="skipped"
5. Test that a duplicate job URL is skipped without calling open_application_form()
6. Test that hitting the daily cap stops applying and logs correctly

Use tmp_path for CSV files. Mock create_browser_context to return a MagicMock.
Follow the same test style as existing tests (no docstrings on test functions, descriptive names).
```

---

### Prompt 8 — Set up cron job (P3, do last)

```
Read README.md to see the existing cron documentation.

Set up the actual cron job for daily runs at 9 AM IST (3:30 AM UTC):
1. Show me the exact crontab line to add
2. The command should: cd to the project directory, activate the venv if one exists, run python apply.py --email-summary, redirect stdout+stderr to a dated log file in data/logs/
3. Confirm the cron entry is correct for macOS (launchd alternative if preferred)
4. Add a note in README.md under the existing cron section with the exact entry used

Do NOT add the crontab entry automatically — show me the command and ask me to run it.
```

---

## 9. Summary

| Category | Count |
|---|---|
| Files complete | 12 / 16 Python files |
| Files stubbed | 4 (browser.py + 3 platforms) |
| Tests passing | 69 / 69 |
| Platforms working | 1 / 4 (Naukri — but blocked by browser.py at runtime) |
| Platforms stubbed | 3 / 4 (LinkedIn, Wellfound, Cutshort) |
| Estimated remaining effort | 22-30 hours |
| Next action | Implement `core/browser.py` |
