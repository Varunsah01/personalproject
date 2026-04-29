# Pending Items & Codebase Audit

**Date:** 2026-04-29
**Overall Completion:** ~90%

---

## 1. Test Results

| Suite | Tests | Status | Time |
|---|---|---|---|
| `tests/test_scorer.py` | 46 | All pass | 0.04s |
| `tests/test_logger.py` | 23 | All pass | <0.01s |
| `tests/test_integration.py` | 8 | All pass | 14s |
| `tests/test_browser.py` | 37 | All pass | 0.10s |
| **Total** | **114** | **All pass** | **~14s** |

- All Python files compile cleanly
- All module imports resolve without errors
- CLI (`python apply.py --help`) works correctly
- No circular imports, no deprecation warnings
- Dependencies all installed (`playwright`, `python-dotenv`, `pytest`, `playwright-stealth`, `pytest-asyncio`)

---

## 2. Component Status

| File | Lines | Status | Notes |
|---|---|---|---|
| `apply.py` | 780 | COMPLETE | Main runner, CLI args, pre-flight, orchestration, review queue |
| `core/scorer.py` | 364 | COMPLETE | Scoring engine, tier classification, hard-skip patterns |
| `core/logger.py` | 207 | COMPLETE | CSV logging, dedupe, URL normalization, file locking |
| `core/notifier.py` | 360 | COMPLETE | Email digest builder, 5-section report, SMTP support |
| `core/browser.py` | 209 | COMPLETE | `create_browser_context`, `human_type`, `human_click`, `BotDetectionError`, `is_bot_challenged` |
| `core/__init__.py` | 0 | OK | Empty init |
| `platforms/base.py` | 111 | COMPLETE | Abstract base + `score_and_apply()` orchestration |
| `platforms/__init__.py` | 13 | COMPLETE | Platform registry (4 platforms mapped) |
| `platforms/naukri.py` | 865 | COMPLETE | Full login/search/apply/logout + chatbot handling |
| `platforms/linkedin.py` | 1135 | COMPLETE | Full login/search/apply/logout, multi-step Easy Apply modal |
| `platforms/wellfound.py` | 929 | COMPLETE | Full login/search/apply/logout, profile completeness gate |
| `platforms/cutshort.py` | 969 | COMPLETE | Full login/search/apply/logout, tag-based search, ATS redirect detection |
| `tests/test_scorer.py` | 335 | COMPLETE | 46 tests covering tiers, scoring, hard-skips, thresholds |
| `tests/test_logger.py` | 354 | COMPLETE | 23 tests covering init, dedupe, counting, normalization |
| `tests/test_integration.py` | 374 | COMPLETE | 8 smoke tests covering platform orchestration flow |
| `tests/test_browser.py` | 355 | COMPLETE | 37 tests covering browser.py (BotDetectionError, is_bot_challenged, human_type, create_browser_context) |
| Documentation | ~950 | COMPLETE | CLAUDE.md, GUARDRAILS.md, guidelines.md, profile.md, README.md |
| `.env.example` | 36 | COMPLETE | All credential + config placeholders |
| `requirements.txt` | 9 | COMPLETE | playwright, python-dotenv, pytest, playwright-stealth, pytest-asyncio |

---

## 3. What Is Working

- **Scoring engine** (`core/scorer.py`) — full job-to-score pipeline with tier classification, hard-skip detection, component scoring (title/stage/sector/location/experience), and threshold enforcement
- **Logging** (`core/logger.py`) — CSV append with file locking, URL normalization (strips tracking params), duplicate detection, daily count per platform
- **Notifier** (`core/notifier.py`) — builds 5-section email digest (summary stats, top applies, yellow queue preview, errors, day-over-day delta), supports print-only mode
- **Browser helpers** (`core/browser.py`) — Playwright context with stealth (playwright-stealth), persistent per-platform profiles, randomised viewport, `human_type` with per-char jitter, `human_click` with jittered mouse coords, `is_bot_challenged` title + evaluate checks
- **Naukri platform** (`platforms/naukri.py`) — complete end-to-end: login, search with pagination, job scoring, chatbot questionnaire handling, resume upload, apply/skip/queue routing, logout
- **LinkedIn platform** (`platforms/linkedin.py`) — full Easy Apply flow: login, search, multi-step modal (contact → resume → screening questions → review → submit), external apply detection, logout
- **Wellfound platform** (`platforms/wellfound.py`) — full flow: login, profile completeness gate (< 80% → warn + abort), search with `location=India`, `why_work_here` textarea → auto-Yellow queue, logout
- **Cutshort platform** (`platforms/cutshort.py`) — full flow: login with graceful 3s timeout for removed email/password fields (falls through to persistent-session check), tag-based search via `KEYWORD_TAG_MAP`, off-platform ATS redirect detection (pre- and post-click), logout
- **Main runner** (`apply.py`) — CLI with all flags, pre-flight checks (resume age, disk space, credentials, caps, error rate), platform orchestration, summary writing, review queue mode
- **Review queue** — interactive handler for yellow-tier jobs (apply/skip/draft/next/quit)
- **Unit + integration tests** — 114 tests, 100% pass rate

---

## 4. Critical Blockers

None. All four platforms are fully implemented.

---

## 5. Remaining Gaps Before First Real Run

| Item | Priority | Status | Notes |
|---|---|---|---|
| `.env` file with real credentials | **P0** | Missing | Only `.env.example` exists — bot cannot log in to any platform |
| Stale error entries in `applications_log.csv` | **P0** | Present | 4 `auth_failed` rows from dry-run testing are tripping the 25% error-rate circuit breaker. Delete lines 2, 6, 7, 8 or wait 24h for them to age out |
| Headful seed session — Wellfound | **P1** | Not done | Kasada bot protection may block login under headless Playwright. Run once with `HEADLESS=false` to seed persistent profile and solve any OAuth prompt |
| Headful seed session — Cutshort | **P1** | Not done | Email/password login removed 2026-04-28; relies entirely on persistent session. Run once with `HEADLESS=false` to establish the session via Google OAuth or phone OTP |
| Wellfound selectors verification | **P1** | Unverified | All selectors marked `? needs-credentials` — best-guess `data-test` attrs. Verify against a live logged-in session before counting on them |
| Cutshort selectors verification | **P1** | Unverified | All job/apply selectors marked `? needs-credentials` — same caveat as Wellfound |
| LinkedIn selectors live-check | **P2** | Unverified | Selectors follow expected Easy Apply patterns but not DOM-verified on a live session |
| `data/browser_profiles/` directory | **P3** | Created on demand | `create_browser_context` creates it at first run |

---

## 6. Non-Blocking Gaps

| Item | Priority | Status | Notes |
|---|---|---|---|
| Instahyre platform | P3 | Not started | Listed as platform #5 in CLAUDE.md, not in initial scope |
| Resume tailoring (per-tier) | P3 | Not started | Open question per CLAUDE.md §14 — using master CV for now |
| Naukri profile auto-update | P3 | Not started | Open question per CLAUDE.md §14 — defaulting to yes |
| Recruiter DM on LinkedIn | P4 | Not started | Open question per CLAUDE.md §14 — defaulting to no |
| Cron/scheduler actual setup | P3 | Docs only | README has instructions but no crontab entry created |

---

## 7. Build Checklist (from CLAUDE.md Section 12)

| # | Item | Status |
|---|---|---|
| 1 | CLAUDE.md, profile.md, GUARDRAILS.md, guidelines.md | DONE |
| 2 | Project skeleton (apply.py, core/, platforms/base.py, requirements.txt, .env.example, review queue, STOP-file, browser profiles) | DONE |
| 3 | core/logger.py + core/scorer.py with unit tests | DONE |
| 4 | platforms/naukri.py end-to-end | DONE |
| 5 | platforms/linkedin.py (Easy Apply) | DONE |
| 6 | Dedupe + daily-cap enforcement in apply.py | DONE |
| 7 | platforms/wellfound.py | DONE |
| 8 | platforms/cutshort.py | DONE |
| 9 | core/notifier.py — 7 PM email digest | DONE |
| 10 | Cron / Task Scheduler setup in README | DONE (docs only) |

---

## 8. Recommended Next Steps (ordered)

1. **Create `.env`** — copy from `.env.example`, fill in all credentials. This is the only hard blocker for any platform.
2. **Clear stale error rows** — delete the 4 `auth_failed` rows from `data/applications_log.csv` so the circuit breaker doesn't trip.
3. **Seed Naukri + LinkedIn** — run `HEADLESS=false python3 apply.py --platform naukri --dry-run --verbose` and same for LinkedIn. Confirm login succeeds and jobs are found.
4. **Seed Wellfound + Cutshort** — run with `HEADLESS=false`. Solve the Google OAuth / phone OTP prompt manually to establish the persistent browser profile. Verify selectors work against a live logged-in session.
5. **First supervised real run** — `python3 apply.py --platform naukri --verbose` (no `--dry-run`). Watch the first 5 applications go through live, confirm CSV rows are written correctly.
6. **Full pipeline real run** — `python3 apply.py --verbose` once all four platforms are seeded.
7. **Set up cron** — automate daily runs at 9 AM IST.

---

## 9. Dry-Run Results (2026-04-29)

Run attempted: `python3 apply.py --dry-run --verbose`

**Result: All platforms blocked in pre-flight. No browser was launched.**

| Platform | Pre-flight result | Reason |
|---|---|---|
| Naukri | SKIP | Missing `NAUKRI_EMAIL`, `NAUKRI_PASSWORD`; error rate 33% (1/3) |
| LinkedIn | SKIP | Missing `LINKEDIN_EMAIL`, `LINKEDIN_PASSWORD`; error rate 50% (1/2) |
| Wellfound | SKIP | Missing `WELLFOUND_EMAIL`, `WELLFOUND_PASSWORD`; error rate 100% (1/1) |
| Cutshort | SKIP | Missing `CUTSHORT_EMAIL`, `CUTSHORT_PASSWORD`; error rate 100% (1/1) |

Root cause: No `.env` file. Prior test runs (auth_failed) filled the error-rate window.  
No selector failures, no BotDetectionErrors, no debug screenshots — browsers never launched.

---

## 10. Summary

| Category | Count |
|---|---|
| Files complete | 16 / 16 Python files |
| Files stubbed | 0 |
| Tests passing | 114 / 114 |
| Platforms implemented | 4 / 4 |
| Platforms live-verified | 0 / 4 (pending credentials + seed sessions) |
| Remaining blockers | 2 (`.env` file, stale error rows) |
| Next action | Create `.env`, clear stale log rows, seed platforms headfully |
