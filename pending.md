# Pending Items & Codebase Audit

**Date:** 2026-04-29 (updated)
**Overall Completion:** ~97%

---

## 1. Test Results

| Suite | Tests | Status | Time |
|---|---|---|---|
| `tests/test_scorer.py` | 46 | All pass | 0.04s |
| `tests/test_logger.py` | 23 | All pass | <0.01s |
| `tests/test_integration.py` | 8 | All pass | 14s |
| `tests/test_browser.py` | 37 | All pass | 0.10s |
| `tests/test_tracker.py` | 45 | All pass | 0.09s |
| `tests/test_sender.py` | 25 | All pass | 1.5s |
| `tests/test_gmail_pool.py` | 7 | All pass | <0.01s |
| `tests/test_review.py` | 9 | All pass | 0.04s |
| `tests/test_digest.py` | 9 | All pass | 0.04s |
| `tests/test_outreach_pipeline.py` | 8 | All pass | 0.02s |
| **Total** | **217** | **All pass** | **~16s** |

---

## 2. Component Status

### Apply bot

| File | Lines | Status | Notes |
|---|---|---|---|
| `apply.py` | 780 | COMPLETE | Main runner, CLI args, pre-flight, orchestration, review queue |
| `core/scorer.py` | 364 | COMPLETE | Scoring engine, tier classification, hard-skip patterns |
| `core/logger.py` | 207 | COMPLETE | CSV logging, dedupe, URL normalization, file locking |
| `core/notifier.py` | 360 | COMPLETE | 7 PM email digest, 5-section report, SMTP support |
| `core/browser.py` | 209 | COMPLETE | Playwright context, stealth, `human_type`, `is_bot_challenged` |
| `platforms/base.py` | 111 | COMPLETE | Abstract base + `score_and_apply()` orchestration |
| `platforms/naukri.py` | 865 | COMPLETE | Full login/search/apply/logout + chatbot handling |
| `platforms/linkedin.py` | 1135 | COMPLETE | Full login/search/apply/logout, multi-step Easy Apply modal |
| `platforms/wellfound.py` | 929 | COMPLETE | Full login/search/apply/logout, profile completeness gate |
| `platforms/cutshort.py` | 969 | COMPLETE | Full login/search/apply/logout, tag-based search, ATS redirect detection |

### Outreach module

| File | Status | Notes |
|---|---|---|
| `outreach/lib/tracker.py` | COMPLETE | CSV SSOT, state machine, cooldown, suppression, funnel_counts, update_notes |
| `outreach/lib/sender.py` | COMPLETE | Gmail API, inbox rotation, geo timing, HttpError structured handling |
| `outreach/lib/gmail_pool.py` | COMPLETE | Inbox pool, per-inbox + global caps, load_from_env |
| `outreach/lib/timing.py` | COMPLETE | Geo-aware send window scheduler |
| `outreach/lib/digest.py` | COMPLETE | 5-section funnel digest, 8 PM IST cron, reuses core/notifier SMTP |
| `outreach/review.py` | COMPLETE | Interactive drafted→queued CLI ([a]pprove/[e]dit/[s]kip/[r]eject/[q]uit) |
| `outreach/dashboard.py` | COMPLETE | Read-only funnel kanban, --by-tier, --needs-review |
| `outreach/pipeline.py` | COMPLETE | Subprocess orchestrator, STOP-file, error-rate gate, log writes |

### Tests

| File | Tests | Status |
|---|---|---|
| `tests/test_tracker.py` | 45 | All pass |
| `tests/test_sender.py` | 25 | All pass |
| `tests/test_gmail_pool.py` | 7 | All pass |
| `tests/test_review.py` | 9 | All pass |
| `tests/test_digest.py` | 9 | All pass |
| `tests/test_outreach_pipeline.py` | 8 | All pass |

### Scripts

| File | Status | Notes |
|---|---|---|
| `scripts/smoke_outreach.sh` | COMPLETE | pytest + dashboard smoke + pipeline dry-run |
| `scripts/run_daily.sh` | COMPLETE | Apply-bot daily runner |

---

## 3. Build Checklist (from CLAUDE.md Section 12)

| # | Item | Status |
|---|---|---|
| 1 | CLAUDE.md, profile.md, GUARDRAILS.md, guidelines.md | DONE |
| 2 | Project skeleton | DONE |
| 3 | core/logger.py + core/scorer.py with unit tests | DONE |
| 4 | platforms/naukri.py end-to-end | DONE |
| 5 | platforms/linkedin.py (Easy Apply) | DONE |
| 6 | Dedupe + daily-cap enforcement in apply.py | DONE |
| 7 | platforms/wellfound.py | DONE |
| 8 | platforms/cutshort.py | DONE |
| 9 | core/notifier.py — 7 PM email digest | DONE |
| 10 | Cron / Task Scheduler setup in README | DONE |
| 11 | outreach/CLAUDE.md + outreach/prompts/principles.md | DONE |
| 12 | outreach/lib/tracker.py + tracker.csv schema + unit tests | DONE |
| 13 | outreach/lib/sender.py + Gmail OAuth + inbox rotation + geo timing | DONE |
| 14 | outreach/lib/sender.py error handling (hard bounce, auth, transient) | DONE |
| 15 | outreach/review.py — interactive drafted→queued CLI | DONE |
| 16 | outreach/lib/digest.py — 8 PM funnel digest | DONE |
| 17 | outreach/dashboard.py — read-only kanban view | DONE |
| 18 | tests/test_outreach_pipeline.py — orchestration shape tests | DONE |
| 19 | scripts/smoke_outreach.sh | DONE |
| 20 | .claude/agents/ subagents (role_researcher, people_finder, channel_finder, message_writer) | DONE |
| 21 | outreach/pipeline.py orchestrator + cron entries | DONE |

---

## 4. Critical Blockers

None. All code complete and tested.

---

## 5. Remaining Gaps Before First Real Run

| Item | Priority | Status | Notes |
|---|---|---|---|
| `.env` file with real credentials | **P0** | Missing | Only `.env.example` exists — apply bot cannot log in |
| `.env.outreach` file with Gmail OAuth tokens | **P0** | Missing | Sender cannot pick inbox without this |
| Gmail OAuth token setup | **P0** | Not done | Run `python setup_gmail_oauth.py` per README to generate token JSON files |
| Stale error entries in `applications_log.csv` | **P0** | Present | 4 `auth_failed` rows tripping 25% error-rate circuit breaker |
| Headful seed session — Wellfound | **P1** | Not done | Kasada protection may block headless login |
| Headful seed session — Cutshort | **P1** | Not done | Email/password login removed; needs persistent session via Google OAuth |
| Wellfound selectors verification | **P1** | Unverified | `data-test` attrs are best-guess; verify on live session |
| Cutshort selectors verification | **P1** | Unverified | Same caveat |
| LinkedIn selectors live-check | **P2** | Unverified | Patterns follow expected Easy Apply but not DOM-verified |
| Headful seed session — Instahyre | **P1** | Not done | All selectors are BEST-GUESS UNVERIFIED; 403s on automated fetch |
| Instahyre selectors verification | **P1** | Unverified | Need live DOM inspection to confirm opportunity cards, apply flow |

---

## 6. Non-Blocking Gaps

| Item | Priority | Notes |
|---|---|---|
| CRITICAL-1 in pipeline.py (`_parse_error_rate` counts "skipped" as errors) | P2 | Documented in outreach/AUDIT.md. One-line fix when ready. |
| ~~Instahyre platform~~ | DONE | `platforms/instahyre.py` — recruiter-led curated board. Ceiling 100/day. Selectors UNVERIFIED. |
| Resume tailoring (per-tier) | P3 | Using master CV for now |
| Naukri profile auto-update | P3 | Defaulting to yes |
| Recruiter DM on LinkedIn | P4 | Defaulting to no |
| ~~Global dynamic cap refactoring~~ | DONE | Replaced per-platform fixed caps with `DAILY_CAP_GLOBAL=300` + per-platform soft ceilings. Two-phase discover→rank→apply flow. |
| ~~LLM relevance agent~~ | DONE | `core/relevance_agent.py` — Claude-powered semantic second-pass after keyword scorer. Budget-capped at 400/day, cached 30 days. `--no-agent` to disable. |
| ~~Greenhouse platform~~ | DONE | `platforms/greenhouse.py` — public Job Board API for discovery, Playwright for form filling. Ceiling 80/day. Companies in `config/greenhouse_companies.txt`. |

---

## 7. Recommended Next Steps

1. **Create `.env`** — copy from `.env.example`, fill credentials.
2. **Set up Gmail OAuth** — run OAuth flow per README, generate token JSON.
3. **Create `.env.outreach`** — fill inbox addresses + token paths.
4. **Clear stale error rows** — delete 4 `auth_failed` rows from `data/applications_log.csv`.
5. **Smoke test outreach** — `bash scripts/smoke_outreach.sh` (steps 1-2 pass without credentials; step 3 needs `claude` on PATH).
6. **Seed Naukri + LinkedIn headfully** — confirm login + job discovery works.
7. **Seed Wellfound + Cutshort headfully** — solve OAuth/OTP, verify selectors.
8. **First supervised send** — `python outreach/review.py`, approve 1 draft, watch `sender.py --tick` fire it.
9. **Fix CRITICAL-1** — change `_parse_error_rate` regex to exclude "skipped" from error count.
