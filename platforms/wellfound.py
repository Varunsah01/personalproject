"""Wellfound (AngelList) platform integration.

Build step 7. Startup-focused board — almost every listing is seed–Series C,
so stage_match in the scorer naturally skews high. That's correct.
Daily cap: 30 (configurable via DAILY_CAP_WELLFOUND in .env).

Two Wellfound-specific behaviours vs the other platforms:
- Profile-completeness gate: checked immediately after login. If the profile
  reads below 80%, Wellfound silently deprioritises applications — better to
  warn and stop than waste 30 applies against a weak profile.
- "Why do you want to work here?" appears on most Wellfound applications and
  is never filled automatically. Any job that shows this field is auto-Yellow.

Apply flow: single-step modal (simpler than LinkedIn's multi-step).
External apply (company site redirect): detected and skipped.

Selector strategy: all CSS selectors are class constants at the top.
They WILL break when Wellfound ships UI changes — update them here,
nowhere else. Each selector has a comment with what it targets and
when it was last verified.
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import os
import random
import re
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from core.browser import create_browser_context, human_type
from core.logger import LogEntry, count_today, init_log, is_duplicate, log_application
from core.scorer import Job, classify_tier, score_job, should_apply
from platforms.base import BasePlatform

logger = logging.getLogger(__name__)


# ── Selectors ──────────────────────────────────────────────────────────
# TODO — selector mapping session (2026-04-28)
# All selectors below are placeholders. Real values will be filled
# after a live DOM inspection session with Varun.

# Login page
LOGIN_URL = "https://wellfound.com/login"
SEL_LOGIN_EMAIL = "input[name='user[email]']"               # TODO — verify: email input on login page
SEL_LOGIN_PASSWORD = "input[name='user[password]']"         # TODO — verify: password input on login page
SEL_LOGIN_SUBMIT = "button[type='submit']"                   # TODO — verify: "Sign in" / "Log in" button
SEL_LOGIN_SUCCESS = "a[href*='/me']"                         # TODO — verify: element only visible when logged in (e.g. profile nav link)

# Profile completeness (checked immediately after login)
PROFILE_URL = "https://wellfound.com/profile/edit"
SEL_PROFILE_COMPLETENESS = "div.profile-completeness"        # TODO — verify: element showing completeness % (e.g. "82% complete" or a progress bar with aria-valuenow)

# Search results page
SEARCH_URL_TEMPLATE = (
    "https://wellfound.com/jobs"
    "?q={keyword}"
    "&l={location}"
    "&page={page}"
)
SEL_JOB_CARD = "div[data-test='StartupResult']"              # TODO — verify: each job listing card in search results
SEL_JOB_TITLE = "a[data-test='job-title']"                   # TODO — verify: job title link inside card
SEL_JOB_COMPANY = "a[data-test='startup-link']"              # TODO — verify: company name/link
SEL_JOB_LOCATION = "span[data-test='location']"              # TODO — verify: location text
SEL_JOB_EXPERIENCE = "span[data-test='job-type']"            # TODO — verify: experience/type text (may not exist per-card)
SEL_JOB_URL = "a[data-test='job-title']"                     # TODO — verify: same as title; read href attr
SEL_JOB_SNIPPET = "p[data-test='job-description']"           # TODO — verify: short JD snippet on card
SEL_NEXT_PAGE = "a[rel='next']"                              # TODO — verify: next page link or load-more button

# Job detail / apply flow
SEL_APPLY_BUTTON = "button[data-test='apply-button']"        # TODO — verify: "Apply" button on job detail page
SEL_EXTERNAL_APPLY_INDICATOR = "a[data-test='external-apply']"  # TODO — verify: link/button that redirects to company site instead of Wellfound modal
SEL_ALREADY_APPLIED = "span[data-test='applied-badge']"      # TODO — verify: "Applied" badge when already applied to this role
SEL_APPLY_MODAL = "div[data-test='apply-modal']"             # TODO — verify: the apply form container/modal
SEL_MODAL_CLOSE = "button[data-test='close-modal']"          # TODO — verify: X / close button on the modal

# Modal form fields
SEL_WHY_TEXTAREA = "textarea[name*='why'], textarea[placeholder*='why'], textarea[data-test*='why']"  # TODO — verify: the "Why do you want to work here?" textarea. Multiple candidate selectors listed because this field name varies. The real one needs DOM inspection.
SEL_MODAL_INPUT_TEXT = "input[type='text']"                  # TODO — verify: standard text inputs inside the apply modal
SEL_MODAL_LABEL = "label"                                    # TODO — verify: field labels inside modal
SEL_RESUME_UPLOAD = "input[type='file']"                     # TODO — verify: hidden file input for resume upload
SEL_MODAL_SUBMIT = "button[data-test='submit-application']"  # TODO — verify: "Send Application" / "Submit" button in modal

# Post-apply confirmation
SEL_APPLY_SUCCESS = "div[data-test='application-sent']"      # TODO — verify: success message / toast shown after successful submission


# ── Config ─────────────────────────────────────────────────────────────

PROFILE_COMPLETENESS_MIN = 80   # stop platform if profile reads below this percent
MAX_PAGES_PER_KEYWORD = 5       # CLAUDE.md §6
APPLY_DELAY = (8, 20)           # seconds between applications — Wellfound-specific
PAGE_DELAY = (30, 90)           # seconds between search result pages (guidelines.md §3.2)
SELECTOR_RETRY_DELAY = 3        # seconds before retrying a missing selector (guidelines.md §3.4)
MAX_SELECTOR_FAILURES = 5       # per session before stopping platform (guidelines.md §3.4)
NETWORK_RETRY_DELAYS = [5, 15]  # exponential backoff for network errors (guidelines.md §3.4)
MAX_SESSION_SECONDS = 90 * 60   # 90 minutes per platform (guidelines.md §3.3)
LONG_TEXT_THRESHOLD = 100       # chars — text fields over this length demote to Yellow

REVIEW_QUEUE_PATH = Path("data/review_queue.csv")
REVIEW_QUEUE_COLUMNS = [
    "queued_at", "platform", "company_name", "role_title", "job_url",
    "fit_score", "tier", "reason_queued", "custom_questions", "expires_at",
]

STANDARD_FIELD_NAMES = {
    "name", "full name", "first name", "last name",
    "email", "email address", "e-mail",
    "phone", "mobile", "contact number", "phone number",
    "location", "city", "current location", "preferred location",
    "notice period", "notice",
    "years of experience", "experience", "total experience",
    "linkedin", "linkedin url", "linkedin profile",
    "current ctc", "current salary", "ctc",
    "expected ctc", "expected salary",
    "resume", "cv",
}

# Essay / long-answer patterns that auto-demote to Yellow.
# "why do you want to work here" is the dominant one on Wellfound.
_ESSAY_DEMOTE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"why\s+do\s+you\s+want\s+to\s+work", re.IGNORECASE),
    re.compile(r"why\s+are\s+you\s+interested", re.IGNORECASE),
    re.compile(r"why\s+(?:this|our)\s+company", re.IGNORECASE),
    re.compile(r"tell\s+us\s+about\s+yourself", re.IGNORECASE),
    re.compile(r"describe\s+a\s+time", re.IGNORECASE),
    re.compile(r"cover\s+letter", re.IGNORECASE),
    re.compile(r"what\s+makes\s+you\s+a\s+good\s+fit", re.IGNORECASE),
    re.compile(r"why\s+should\s+we\s+hire", re.IGNORECASE),
    re.compile(r"additional\s+information", re.IGNORECASE),
]


class WellfoundPlatform(BasePlatform):
    """Wellfound (AngelList) integration.

    Lifecycle: login → completeness check → search (per keyword) →
    score & apply (per job) → logout.
    Uses persistent browser profile at data/browser_profiles/wellfound/.

    Skips external-apply jobs and any job that shows "Why do you want to
    work here?" or other custom essay fields (queued to Yellow instead).

    Args:
        log_path: Path to applications_log.csv.
        headless: Run browser in headless mode.
        dry_run: Score and log but never click Apply.
    """

    PLATFORM_NAME = "wellfound"

    def __init__(
        self,
        log_path: Path | str = "data/applications_log.csv",
        headless: bool = True,
        dry_run: bool = False,
    ) -> None:
        self.log_path = Path(log_path)
        self.headless = headless
        self.dry_run = dry_run

        # Loaded from .env at runtime
        self.email = os.getenv("WELLFOUND_EMAIL", "")
        self.password = os.getenv("WELLFOUND_PASSWORD", "")
        self.daily_cap = int(os.getenv("DAILY_CAP_WELLFOUND", "30"))

        # Standard answers for form filling
        self.standard_answers: dict[str, str] = {
            "name": "Varun Sah",
            "email": self.email,
            "phone": "+91-8595062552",
            "location": "Delhi NCR",
            "notice_period": "Immediate",
            "years_of_experience": "4",
            "linkedin": "https://www.linkedin.com/in/varun-sah/",
            "current_ctc": os.getenv("WELLFOUND_CURRENT_CTC", ""),
            "expected_ctc": os.getenv("WELLFOUND_EXPECTED_CTC", ""),
        }

        # Session state
        self._page: Page | None = None
        self._context = None
        self._playwright = None
        self._selector_failures = 0
        self._session_start = 0.0
        self._stats = {"applied": 0, "skipped": 0, "errored": 0, "queued": 0}

    # ── BasePlatform interface ─────────────────────────────────────────

    async def login(self) -> bool:
        """Log into Wellfound using .env credentials.

        Uses persistent browser profile so subsequent runs may already
        be logged in (cookie-based session).

        After a successful login, checks profile completeness. If the
        profile reads below PROFILE_COMPLETENESS_MIN (80%), logs a warning
        and stops. Applications submitted against an incomplete profile are
        silently deprioritised by Wellfound's algorithm — better to stop
        and surface the issue than waste 30 applies.

        Returns:
            True if login and completeness check passed, False otherwise.
        """
        if not self.email or not self.password:
            logger.error("WELLFOUND_EMAIL or WELLFOUND_PASSWORD not set in .env")
            self._log_error("auth_failed: missing credentials")
            return False

        from playwright.async_api import async_playwright
        self._playwright = await async_playwright().start()
        self._context = await create_browser_context(
            platform=self.PLATFORM_NAME,
            headless=self.headless,
            playwright=self._playwright,
        )
        self._page = await self._context.new_page()

        await self._page.goto(LOGIN_URL, wait_until="domcontentloaded")

        # Check if already logged in (persistent profile may have valid session)
        if await self._is_logged_in():
            logger.info("Already logged in via persistent session")
        else:
            try:
                await self._page.fill(SEL_LOGIN_EMAIL, self.email)
                await human_type(self._page, SEL_LOGIN_PASSWORD, self.password)
                await self._page.click(SEL_LOGIN_SUBMIT)
                await self._page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                logger.error("Login form interaction timed out")
                self._log_error("auth_failed: timeout")
                return False

            if not await self._is_logged_in():
                logger.error("Login failed — success indicator not found")
                self._log_error("auth_failed: credentials rejected or unknown error")
                return False

        logger.info("Wellfound login successful")

        # Profile completeness gate — Wellfound-specific
        completeness = await self._check_profile_completeness()
        if completeness == -1:
            # Selector broken — log warning but proceed; don't let a broken selector
            # block all 30 applies when the profile might be fine
            logger.warning(
                "Profile completeness selector not found — proceeding without completeness check"
            )
        elif completeness < PROFILE_COMPLETENESS_MIN:
            logger.warning(
                "Wellfound profile completeness is %d%% (minimum %d%%) — "
                "stopping to avoid wasted applications. Complete your profile first.",
                completeness, PROFILE_COMPLETENESS_MIN,
            )
            self._log_error(f"profile_incomplete: {completeness}%")
            return False
        else:
            logger.info("Profile completeness: %d%% (OK)", completeness)

        return True

    async def _check_profile_completeness(self) -> int:
        """Navigate to the profile page and read the completeness percentage.

        Returns:
            Completeness as an integer 0–100.
            -1 if the selector is not found (treat as unknown, not as failure).
            0 if the element is found but the percentage can't be parsed.
        """
        if self._page is None:
            return -1

        try:
            await self._page.goto(PROFILE_URL, wait_until="domcontentloaded", timeout=20_000)
        except PlaywrightTimeout:
            logger.warning("Profile page timed out during completeness check")
            return -1

        try:
            el = await self._page.wait_for_selector(SEL_PROFILE_COMPLETENESS, timeout=5_000)
        except PlaywrightTimeout:
            return -1  # selector not found — treat as unknown

        # Try text content first ("82% complete", "82%")
        text = (await el.inner_text()).strip()
        match = re.search(r"(\d+)\s*%", text)
        if match:
            return int(match.group(1))

        # Try aria-valuenow (progress bar pattern)
        aria_val = await el.get_attribute("aria-valuenow")
        if aria_val and aria_val.isdigit():
            return int(aria_val)

        logger.warning(
            "Profile completeness element found but percentage couldn't be parsed from: %r", text
        )
        return 0  # parseable failure → treat as 0 so caller stops

    async def search(self, keyword: str, filters: dict) -> AsyncIterator[Job]:
        """Search Wellfound for jobs matching keyword and filters.

        Paginates up to MAX_PAGES_PER_KEYWORD pages. Inserts PAGE_DELAY
        between pages. The 14-day date window is set by apply.py in the
        filters dict (date_max_age_days=14), but the actual URL param for
        date filtering needs to be confirmed in the selector session.

        Args:
            keyword: Search query, e.g. "growth manager".
            filters: Dict with 'locations' (list[str]), 'experience_min' (int).

        Yields:
            Job objects parsed from search result cards.
        """
        if self._page is None:
            logger.error("search() called before login()")
            return

        location = filters.get("locations", [""])[0] if filters.get("locations") else ""

        for page_num in range(1, MAX_PAGES_PER_KEYWORD + 1):
            if self._should_stop():
                return

            url = SEARCH_URL_TEMPLATE.format(
                keyword=keyword.replace(" ", "+"),
                location=location.replace(" ", "+"),
                page=page_num,
            )
            logger.info("[wellfound] Page %d for '%s': %s", page_num, keyword, url)

            try:
                await self._page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            except PlaywrightTimeout:
                logger.warning("Search page %d timed out for '%s'", page_num, keyword)
                retried = await self._retry_navigation(url)
                if not retried:
                    break

            job_cards = await self._page.query_selector_all(SEL_JOB_CARD)
            if not job_cards:
                logger.info("No job cards found on page %d — end of results", page_num)
                break

            for card in job_cards:
                if self._should_stop():
                    return
                job = await self._parse_job_card(card)
                if job is not None:
                    yield job

            # Check for next page
            next_button = await self._page.query_selector(SEL_NEXT_PAGE)
            if not next_button:
                logger.info("No next page button — end of results for '%s'", keyword)
                break

            delay = random.uniform(*PAGE_DELAY)
            logger.debug("Waiting %.1fs before next page", delay)
            await asyncio.sleep(delay)

    async def open_application_form(self, job: Job) -> dict:
        """Navigate to job page, click Apply, and inspect the modal.

        Wellfound's apply flow is a single-step modal, simpler than
        LinkedIn's multi-step. The main check is whether the "Why do you
        want to work here?" textarea is present — if so, the job is
        auto-Yellow and the modal is closed without submitting.

        Returns a dict:
            {
                "path": "wellfound_apply"|"external_apply"|"already_applied"|
                        "no_apply_button"|"unknown",
                "has_unrecognized": bool,
                "has_why_textarea": bool,    # True if the why-textarea is present
                "questions": list[dict],      # label + type for each field in the modal
            }
        """
        if self._page is None:
            raise RuntimeError("open_application_form() called before login()")

        job_url = job.posted_date  # URL stashed in posted_date
        await self._page.goto(job_url, wait_until="domcontentloaded", timeout=30_000)

        # Check if already applied
        already = await self._page.query_selector(SEL_ALREADY_APPLIED)
        if already:
            text = (await already.inner_text()).strip().lower()
            if "applied" in text:
                return {"path": "already_applied", "has_unrecognized": False, "has_why_textarea": False, "questions": []}

        # Check for external apply (redirects to company site — skip)
        external = await self._page.query_selector(SEL_EXTERNAL_APPLY_INDICATOR)
        if external:
            return {"path": "external_apply", "has_unrecognized": False, "has_why_textarea": False, "questions": []}

        # Check for the Apply button
        apply_btn = await self._page.query_selector(SEL_APPLY_BUTTON)
        if not apply_btn:
            return {"path": "no_apply_button", "has_unrecognized": False, "has_why_textarea": False, "questions": []}

        # Click Apply — modal should appear
        try:
            await apply_btn.click(timeout=10_000)
        except PlaywrightTimeout:
            await asyncio.sleep(SELECTOR_RETRY_DELAY)
            try:
                await apply_btn.click(timeout=10_000)
            except PlaywrightTimeout:
                self._selector_failures += 1
                raise

        # Wait for the apply modal
        try:
            await self._page.wait_for_selector(SEL_APPLY_MODAL, timeout=10_000)
        except PlaywrightTimeout:
            return {"path": "unknown", "has_unrecognized": False, "has_why_textarea": False, "questions": []}

        # Inspect modal fields
        questions: list[dict] = []
        has_unrecognized = False
        has_why_textarea = False

        # Check for "Why do you want to work here?" textarea (the primary Yellow trigger)
        why_el = await self._page.query_selector(SEL_WHY_TEXTAREA)
        if why_el:
            has_why_textarea = True
            has_unrecognized = True
            questions.append({"label": "Why do you want to work here?", "type": "textarea"})

        # Check all text inputs for unrecognised labels
        text_inputs = await self._page.query_selector_all(
            f"{SEL_APPLY_MODAL} {SEL_MODAL_INPUT_TEXT}"
        )
        for inp in text_inputs:
            label = await self._get_field_label(inp)
            # Check if this textarea is an essay question even if not caught by SEL_WHY_TEXTAREA
            if self._is_essay_question(label):
                has_unrecognized = True
                questions.append({"label": label, "type": "text_essay"})
            elif not self._is_recognized_question(label):
                has_unrecognized = True
                questions.append({"label": label, "type": "text_unknown"})
            else:
                questions.append({"label": label, "type": "text"})

        # Check for any textareas not caught by SEL_WHY_TEXTAREA
        textareas = await self._page.query_selector_all(f"{SEL_APPLY_MODAL} textarea")
        for ta in textareas:
            # SEL_WHY_TEXTAREA may already cover this — dedupe by checking count
            label = await self._get_field_label(ta)
            if not any(q["label"] == label for q in questions):
                has_unrecognized = True
                questions.append({"label": label, "type": "textarea"})

        return {
            "path": "wellfound_apply",
            "has_unrecognized": has_unrecognized,
            "has_why_textarea": has_why_textarea,
            "questions": questions,
        }

    async def _get_field_label(self, element) -> str:
        """Extract label text for a form field element.

        Tries: aria-label → associated <label> via id → closest label ancestor.
        """
        aria = await element.get_attribute("aria-label")
        if aria:
            return aria.strip()

        el_id = await element.get_attribute("id")
        if el_id:
            label_el = await self._page.query_selector(f"label[for='{el_id}']")
            if label_el:
                return (await label_el.inner_text()).strip()

        try:
            label_text = await element.evaluate("""el => {
                const label = el.closest('label') ||
                              el.parentElement &&
                              el.parentElement.querySelector('label');
                return label ? label.textContent.trim() : '';
            }""")
            if label_text:
                return label_text
        except Exception as exc:
            logger.debug("JS label extraction failed: %s", exc)

        return ""

    def _is_essay_question(self, label: str) -> bool:
        """Check if a field label matches essay/long-answer patterns."""
        for pattern in _ESSAY_DEMOTE_PATTERNS:
            if pattern.search(label):
                return True
        return False

    def _is_recognized_question(self, question_text: str) -> bool:
        """Check if a field label maps to a known standard field."""
        q_lower = question_text.lower()
        for field_name in STANDARD_FIELD_NAMES:
            if field_name in q_lower:
                return True
        return False

    async def fill_and_submit(self, form: dict, answers: dict) -> dict:
        """Fill the Wellfound apply modal and submit.

        Only called for jobs where has_unrecognized is False (Green path).
        The "Why do you want to work here?" textarea will never be present
        at this point — those jobs were queued to Yellow in _attempt_apply.

        Returns:
            {"status": "applied"|"applied_unconfirmed"|"error"|"skipped", "notes": str}
        """
        if self._page is None:
            raise RuntimeError("fill_and_submit() called before login()")

        path = form["path"]

        if path == "external_apply":
            return {"status": "skipped", "notes": "external apply (company website)"}
        if path == "already_applied":
            return {"status": "skipped", "notes": "already applied on Wellfound"}
        if path == "no_apply_button":
            return {"status": "skipped", "notes": "no apply button found"}
        if path != "wellfound_apply":
            return {"status": "error", "notes": f"unknown form path: {path}"}

        # Upload resume if file input is present
        resume_path = Path("Varun_Sah_CV.pdf")
        if resume_path.exists():
            file_inputs = await self._page.query_selector_all(
                f"{SEL_APPLY_MODAL} {SEL_RESUME_UPLOAD}"
            )
            for fi in file_inputs:
                try:
                    await fi.set_input_files(str(resume_path))
                except Exception as exc:
                    logger.debug("Resume upload skipped or failed: %s", exc)

        # Fill empty text inputs with standard answers
        text_inputs = await self._page.query_selector_all(
            f"{SEL_APPLY_MODAL} {SEL_MODAL_INPUT_TEXT}"
        )
        for inp in text_inputs:
            value = await inp.get_attribute("value") or ""
            if value.strip():
                continue  # pre-filled — don't touch
            label = await self._get_field_label(inp)
            answer = self._match_standard_answer(label)
            if answer:
                inp_id = await inp.get_attribute("id")
                selector = f"#{inp_id}" if inp_id else SEL_MODAL_INPUT_TEXT
                try:
                    await human_type(self._page, selector, answer)
                except Exception as exc:
                    logger.debug("Failed to fill field '%s': %s", label, exc)

        # Fill textareas from human-reviewed custom answers (review-queue path only).
        # SEL_WHY_TEXTAREA is unverified — see selector note at top of file.
        custom_answers = answers.get("_custom", {})
        if custom_answers:
            textareas = await self._page.query_selector_all(f"{SEL_APPLY_MODAL} textarea")
            for ta in textareas:
                try:
                    current = await ta.input_value()
                except Exception as exc:
                    logger.debug("Could not read textarea value: %s", exc)
                    current = ""
                if current.strip():
                    continue  # pre-filled — don't touch
                label = await self._get_field_label(ta)
                answer = custom_answers.get(label.lower())
                if answer:
                    try:
                        await ta.click()
                        await ta.type(answer, delay=80)
                    except Exception as exc:
                        logger.debug("Failed to fill textarea '%s': %s", label, exc)

        # Click submit
        try:
            submit_btn = await self._page.wait_for_selector(SEL_MODAL_SUBMIT, timeout=5_000)
            await submit_btn.click(timeout=10_000)
        except PlaywrightTimeout:
            self._selector_failures += 1
            return {"status": "error", "notes": "selector_broken: submit button"}

        # Wait for success state
        try:
            await self._page.wait_for_selector(SEL_APPLY_SUCCESS, timeout=10_000)
            return {"status": "applied", "notes": ""}
        except PlaywrightTimeout:
            return {"status": "applied_unconfirmed", "notes": "no success indicator after submit"}

    def _match_standard_answer(self, label: str) -> str | None:
        """Match a field label to a standard answer."""
        label_lower = label.lower()

        if any(kw in label_lower for kw in ("first name", "full name", "name")):
            return self.standard_answers.get("name")
        if "email" in label_lower:
            return self.standard_answers.get("email")
        if any(kw in label_lower for kw in ("phone", "mobile", "contact")):
            return self.standard_answers.get("phone")
        if any(kw in label_lower for kw in ("city", "location")):
            return self.standard_answers.get("location")
        if "notice" in label_lower:
            return self.standard_answers.get("notice_period")
        if any(kw in label_lower for kw in ("experience", "years")):
            return self.standard_answers.get("years_of_experience")
        if "linkedin" in label_lower:
            return self.standard_answers.get("linkedin")
        if "current" in label_lower and any(kw in label_lower for kw in ("ctc", "salary")):
            return self.standard_answers.get("current_ctc")
        if "expected" in label_lower and any(kw in label_lower for kw in ("ctc", "salary")):
            return self.standard_answers.get("expected_ctc")

        return None

    async def _close_modal(self) -> None:
        """Close the apply modal without submitting."""
        try:
            close_btn = await self._page.query_selector(SEL_MODAL_CLOSE)
            if close_btn:
                await close_btn.click(timeout=5_000)
                await asyncio.sleep(0.5)
        except PlaywrightTimeout:
            logger.debug("Modal close timed out — may already be closed")

    async def logout(self) -> None:
        """Log out of Wellfound and close browser context."""
        # Wellfound logout is typically at /logout or via a nav menu.
        # Just closing the context is sufficient for session management
        # since we use persistent profiles — no need to explicitly log out.
        # If a logout selector is needed, add it here after the selector session.
        logger.info("Wellfound: closing browser context (no explicit logout needed)")

        if self._context is not None:
            await self._context.close()
        if self._playwright is not None:
            await self._playwright.stop()

    # ── Orchestration ──────────────────────────────────────────────────

    async def run(self, keywords: list[str], filters: dict) -> dict[str, int]:
        """Full run: login → completeness check → search each keyword → process each job → logout.

        Returns:
            Stats dict: {applied, skipped, errored, queued}.
        """
        import time
        self._session_start = time.monotonic()
        self._stats = {"applied": 0, "skipped": 0, "errored": 0, "queued": 0}

        init_log(self.log_path)

        logged_in = await self.login()
        if not logged_in:
            return self._stats

        try:
            for keyword in keywords:
                if self._should_stop():
                    break

                logger.info("[wellfound] Searching: %s", keyword)
                async for job in self.search(keyword, filters):
                    if self._should_stop():
                        break
                    await self._process_job(job)
        finally:
            await self.logout()

        logger.info("[wellfound] Run complete — %s", self._stats)
        return self._stats

    async def _process_job(self, job: Job) -> None:
        """Per-job flow: dedupe → hard-skip → score → threshold → cap → apply.

        Implements guidelines.md §3.2 steps 1–8.
        """
        # 1. Dedupe
        if is_duplicate(self.PLATFORM_NAME, job.posted_date, self.log_path):
            self._record(job, 0.0, "skipped", "duplicate")
            return

        # 2–4. Score and decide
        fit_score = score_job(job)
        tier = classify_tier(job.title)
        apply, reason = should_apply(job, fit_score, tier)

        if not apply:
            self._record(job, fit_score, "skipped", reason)
            return

        # 5. Cap check
        current = count_today(self.PLATFORM_NAME, self.log_path)
        if current >= self.daily_cap:
            self._record(job, fit_score, "skipped", f"cap reached: wellfound {current}/{self.daily_cap}")
            logger.info("Daily cap reached (%d/%d) — stopping", current, self.daily_cap)
            return

        # 6. Tier route
        if self._should_queue(fit_score, tier):
            self._queue_for_review(job, fit_score, tier, "score_in_review_band")
            return

        # 7. Dry run check
        if self.dry_run:
            self._record(job, fit_score, "skipped", "dry run: would apply")
            return

        # 7. Apply (Green path)
        await self._attempt_apply(job, fit_score, tier)

        # 8. Delay — Wellfound-specific: 8-20s
        delay = random.uniform(*APPLY_DELAY)
        logger.debug("Waiting %.1fs before next application", delay)
        await asyncio.sleep(delay)

    async def _attempt_apply(self, job: Job, fit_score: float, tier: str) -> None:
        """Open form, check complexity, fill, submit."""
        try:
            form = await self.open_application_form(job)
        except PlaywrightTimeout:
            self._selector_failures += 1
            self._record(job, fit_score, "error", "selector_broken: application form")
            if self._selector_failures >= MAX_SELECTOR_FAILURES:
                logger.error("Too many selector failures (%d) — stopping platform", self._selector_failures)
            return
        except Exception as exc:
            self._record(job, fit_score, "error", f"error opening form: {exc}")
            return

        path = form["path"]

        if path == "external_apply":
            self._record(job, fit_score, "skipped", "external apply (company website)")
            return

        if path == "already_applied":
            self._record(job, fit_score, "skipped", "already applied on Wellfound")
            return

        if path == "no_apply_button":
            self._record(job, fit_score, "skipped", "no apply button found")
            return

        if path == "unknown":
            self._record(job, fit_score, "error", "unknown application state")
            return

        # Wellfound apply — check for custom/essay questions → Yellow
        if form["has_unrecognized"]:
            unrecognized = [q for q in form["questions"] if q["type"] in ("textarea", "text_essay", "text_unknown")]
            self._queue_for_review(
                job, fit_score, tier, "custom_questions",
                custom_questions=[
                    {"question": q.get("label", ""), "suggested_answer": ""}
                    for q in unrecognized
                ],
            )
            await self._close_modal()
            return

        # Fill and submit (Green path)
        try:
            result = await self.fill_and_submit(form, self.standard_answers)
        except PlaywrightTimeout:
            self._selector_failures += 1
            self._record(job, fit_score, "error", "selector_broken: submit")
            return
        except Exception as exc:
            self._record(job, fit_score, "error", f"error submitting: {exc}")
            return

        self._record(job, fit_score, result["status"], result.get("notes", ""))

    # ── Private helpers ────────────────────────────────────────────────

    async def _is_logged_in(self) -> bool:
        """Check if the page shows a logged-in state."""
        try:
            await self._page.wait_for_selector(SEL_LOGIN_SUCCESS, timeout=3_000)
            return True
        except PlaywrightTimeout:
            return False

    async def _parse_job_card(self, card) -> Job | None:
        """Extract a Job from a search result card element.

        Returns None if essential fields can't be parsed.
        Stores job URL in posted_date for use in the apply flow.
        """
        try:
            title = await card.query_selector(SEL_JOB_TITLE)
            title_text = (await title.inner_text()).strip() if title else ""

            company = await card.query_selector(SEL_JOB_COMPANY)
            company_text = (await company.inner_text()).strip() if company else ""

            location = await card.query_selector(SEL_JOB_LOCATION)
            location_text = (await location.inner_text()).strip() if location else ""

            experience = await card.query_selector(SEL_JOB_EXPERIENCE)
            experience_text = (await experience.inner_text()).strip() if experience else ""

            url_el = await card.query_selector(SEL_JOB_URL)
            url = (await url_el.get_attribute("href")) if url_el else ""
            if url and not url.startswith("http"):
                url = f"https://wellfound.com{url}"

            snippet = await card.query_selector(SEL_JOB_SNIPPET)
            snippet_text = (await snippet.inner_text()).strip() if snippet else ""

            if not title_text or not url:
                return None

            return Job(
                title=title_text,
                company=company_text,
                location=location_text,
                experience_required=experience_text,
                jd_text=snippet_text[:500],
                posted_date=url,  # stash URL in posted_date until Job gets a url field
            )
        except Exception as exc:
            logger.debug("Failed to parse job card: %s", exc)
            return None

    async def _retry_navigation(self, url: str) -> bool:
        """Retry page navigation with exponential backoff (guidelines.md §3.4)."""
        for delay in NETWORK_RETRY_DELAYS:
            logger.info("Retrying navigation in %ds...", delay)
            await asyncio.sleep(delay)
            try:
                await self._page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                return True
            except PlaywrightTimeout:
                continue
        return False

    def _should_stop(self) -> bool:
        """Check kill-switches: STOP file, session timer, selector failures."""
        import time
        if Path("data/STOP").exists():
            logger.warning("STOP file detected — stopping wellfound")
            return True
        if self._session_start and (time.monotonic() - self._session_start) > MAX_SESSION_SECONDS:
            logger.warning("Session exceeded %ds — stopping wellfound", MAX_SESSION_SECONDS)
            return True
        if self._selector_failures >= MAX_SELECTOR_FAILURES:
            logger.error("Too many selector failures (%d) — stopping wellfound", self._selector_failures)
            return True
        return False

    def _should_queue(self, fit_score: float, tier: str) -> bool:
        """Determine if a job should go to the Yellow review queue."""
        if tier in ("T2", "T3"):
            return True
        if tier == "T1" and fit_score < 0.7:
            return True
        return False

    def _record(self, job: Job, fit_score: float, status: str, notes: str) -> None:
        """Log one row to applications_log.csv via core.logger."""
        now = datetime.now(timezone.utc)
        entry = LogEntry(
            date_applied=now.strftime("%Y-%m-%d"),
            time_applied=now.strftime("%H:%M"),
            platform=self.PLATFORM_NAME,
            company_name=job.company,
            role_title=job.title,
            experience_required=job.experience_required,
            location=job.location,
            job_url=job.posted_date,
            fit_score=fit_score,
            status=status,
            notes=notes,
        )
        log_application(entry, self.log_path)
        self._stats[status] = self._stats.get(status, 0) + 1
        logger.debug("Recorded: %s at %s [%s] %s", job.title, job.company, status, notes)

    def _log_error(self, notes: str) -> None:
        """Log a platform-level error without a specific job."""
        now = datetime.now(timezone.utc)
        entry = LogEntry(
            date_applied=now.strftime("%Y-%m-%d"),
            time_applied=now.strftime("%H:%M"),
            platform=self.PLATFORM_NAME,
            company_name="",
            role_title="",
            experience_required="",
            location="",
            job_url="",
            fit_score=0.0,
            status="error",
            notes=notes,
        )
        log_application(entry, self.log_path)
        self._stats["errored"] += 1

    def _queue_for_review(
        self,
        job: Job,
        fit_score: float,
        tier: str,
        reason: str,
        custom_questions: list[dict] | None = None,
    ) -> None:
        """Write a job to data/review_queue.csv and log as queued."""
        now = datetime.now(timezone.utc)
        expires = now + timedelta(days=5)

        if not REVIEW_QUEUE_PATH.exists():
            REVIEW_QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(REVIEW_QUEUE_PATH, "w", newline="") as f:
                csv.writer(f).writerow(REVIEW_QUEUE_COLUMNS)

        row = {
            "queued_at": now.isoformat(),
            "platform": self.PLATFORM_NAME,
            "company_name": job.company,
            "role_title": job.title,
            "job_url": job.posted_date,
            "fit_score": f"{fit_score:.2f}",
            "tier": tier,
            "reason_queued": reason,
            "custom_questions": json.dumps(custom_questions or []),
            "expires_at": expires.isoformat(),
        }
        with open(REVIEW_QUEUE_PATH, "a", newline="") as f:
            csv.DictWriter(f, fieldnames=REVIEW_QUEUE_COLUMNS).writerow(row)

        self._record(job, fit_score, "queued", f"queued: {reason}")
        logger.info("Queued for review: %s at %s (%s)", job.title, job.company, reason)

    def _match_radio_answer(self, question_lower: str, options: list[str]) -> str | None:
        """Pick the best radio option for a known question."""
        if "notice period" in question_lower:
            preferred = ["immediate", "15 days or less", "serving notice period"]
            for pref in preferred:
                for opt in options:
                    if pref in opt.lower():
                        return opt
            return options[0] if options else None

        if "experience" in question_lower or "years" in question_lower:
            for opt in options:
                if "3" in opt or "4" in opt or "2" in opt:
                    return opt
            return options[0] if options else None

        if "location" in question_lower or "city" in question_lower or "relocate" in question_lower:
            for opt in options:
                opt_lower = opt.lower()
                if any(loc in opt_lower for loc in ["delhi", "ncr", "gurgaon", "noida", "remote", "yes"]):
                    return opt
            return options[0] if options else None

        if "ctc" in question_lower or "salary" in question_lower:
            return options[0] if options else None

        return options[0] if options else None
