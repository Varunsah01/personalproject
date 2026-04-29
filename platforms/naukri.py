"""Naukri.com platform integration.

Build step 4. Highest volume Indian board, easiest selectors.
Daily cap: 75 (configurable via DAILY_CAP_NAUKRI in .env).

Selector strategy: all CSS selectors are class constants at the top.
They WILL break when Naukri ships UI changes — update them here,
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
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from core.browser import create_browser_context, human_type
from core.logger import LogEntry, count_today, init_log, is_duplicate, log_application
from core.scorer import Job, classify_tier, score_job, should_apply
from core.selectors import SelectorStore
from core.types import BotConfig
from platforms.base import BasePlatform

logger = logging.getLogger(__name__)


# ── URLs ───────────────────────────────────────────────────────────────
# Selectors have moved to platforms/selectors/naukri.yaml (guidelines.md §4.4).
# Edit the YAML to fix broken selectors; call self.sel.reload() to hot-patch.

LOGIN_URL = "https://www.naukri.com/nlogin/login"
SEARCH_URL_TEMPLATE = "https://www.naukri.com/{keyword}-jobs?k={keyword}&l={location}&experience={exp_min}&nignbelow_salary=0&jobAge={max_age}"


# ── Config ─────────────────────────────────────────────────────────────

MAX_PAGES_PER_KEYWORD = 5         # CLAUDE.md §6
APPLY_DELAY = (5, 15)             # seconds between applications (guidelines.md §3.2)
PAGE_DELAY = (30, 90)             # seconds between search result pages (guidelines.md §3.2)
SELECTOR_RETRY_DELAY = 3          # seconds before retrying a missing selector (guidelines.md §3.4)
MAX_SELECTOR_FAILURES = 5         # per session before stopping platform (guidelines.md §3.4)
NETWORK_RETRY_DELAYS = [5, 15]    # exponential backoff for network errors (guidelines.md §3.4)
MAX_SESSION_SECONDS = 90 * 60     # 90 minutes per platform (guidelines.md §3.3)
LONG_TEXT_THRESHOLD = 100         # chars — fields longer than this demote to Yellow (guidelines.md §3.2)

REVIEW_QUEUE_PATH = Path("data/review_queue.csv")
REVIEW_QUEUE_COLUMNS = [
    "queued_at", "platform", "company_name", "role_title", "job_url",
    "fit_score", "tier", "reason_queued", "custom_questions", "expires_at",
]

# Standard answers the bot can fill — read from .env and profile.md
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


class NaukriPlatform(BasePlatform):
    """Naukri.com integration.

    Lifecycle: login → search (per keyword) → score & apply (per job) → logout.
    Uses persistent browser profile at data/browser_profiles/naukri/.

    Args:
        config: BotConfig with shared settings (log path, caps, headless, etc.).
    """

    PLATFORM_NAME = "naukri"

    def __init__(self, config: BotConfig) -> None:
        super().__init__(config)

        # Platform-specific credentials (from .env)
        self.email = os.getenv("NAUKRI_EMAIL", "")
        self.password = os.getenv("NAUKRI_PASSWORD", "")

        # Selector registry — backed by platforms/selectors/naukri.yaml
        self.sel = SelectorStore("naukri")

        # Augment shared standard_answers with Naukri-specific fields
        self.standard_answers.update({
            "email": self.email,
            "current_ctc": os.getenv("NAUKRI_CURRENT_CTC", ""),
            "expected_ctc": os.getenv("NAUKRI_EXPECTED_CTC", ""),
        })

    # ── BasePlatform interface ─────────────────────────────────────────

    async def login(self) -> bool:
        """Log into Naukri using .env credentials.

        Uses persistent browser profile so subsequent runs may already
        be logged in (cookie-based session).

        Returns:
            True if login succeeded, False otherwise.
            On CAPTCHA detection, logs auth_challenge and returns False.
        """
        if not self.email or not self.password:
            logger.error("NAUKRI_EMAIL or NAUKRI_PASSWORD not set in .env")
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
            return True

        # Check for CAPTCHA before attempting login
        if await self._detect_captcha():
            logger.error("CAPTCHA detected on login page — stopping platform")
            self._log_error("auth_challenge: captcha")
            return False

        # Fill credentials — use human_type for the password field
        # TODO: replace selectors with real ones
        try:
            await self._page.fill(self.sel.login_email, self.email)
            await human_type(self._page, self.sel.login_password, self.password)
            await self._page.click(self.sel.login_submit)
            await self._page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            logger.error("Login form interaction timed out")
            self._log_error("auth_failed: timeout")
            return False

        # Post-login CAPTCHA check
        if await self._detect_captcha():
            logger.error("CAPTCHA detected after login submit — stopping platform")
            self._log_error("auth_challenge: captcha")
            return False

        if not await self._is_logged_in():
            logger.error("Login failed — success indicator not found")
            self._log_error("auth_failed: credentials rejected or unknown error")
            return False

        logger.info("Naukri login successful")
        return True

    async def search(self, keyword: str, filters: dict) -> AsyncIterator[Job]:
        """Search Naukri for jobs matching keyword and filters.

        Paginates up to MAX_PAGES_PER_KEYWORD pages. Yields Job objects.
        Inserts PAGE_DELAY between pages.

        Args:
            keyword: Search query, e.g. "growth manager".
            filters: Dict with 'locations' (list[str]), 'experience_min' (int),
                     'experience_max' (int).

        Yields:
            Job objects parsed from search result cards.
        """
        if self._page is None:
            logger.error("search() called before login()")
            return

        # Build search URL from keyword and filters
        location = filters.get("locations", [""])[0] if filters.get("locations") else ""
        exp_min = filters.get("experience_min", 1)
        max_age = 7  # last 7 days (CLAUDE.md §5)

        # TODO: construct the actual Naukri search URL with proper encoding
        search_url = SEARCH_URL_TEMPLATE.format(
            keyword=keyword.replace(" ", "-"),
            location=location.replace(" ", "-"),
            exp_min=exp_min,
            max_age=max_age,
        )

        for page_num in range(1, MAX_PAGES_PER_KEYWORD + 1):
            if self._should_stop():
                return

            url = search_url if page_num == 1 else f"{search_url}&pageNo={page_num}"
            logger.info("[naukri] Page %d for '%s': %s", page_num, keyword, url)

            try:
                await self._page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            except PlaywrightTimeout:
                logger.warning("Search page %d timed out for '%s'", page_num, keyword)
                # Network retry per guidelines.md §3.4
                retried = await self._retry_navigation(url)
                if not retried:
                    break

            # Parse job cards from this page
            # TODO: replace with real selectors
            job_cards = await self._page.query_selector_all(self.sel.job_card)
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
            next_button = await self._page.query_selector(self.sel.next_page)
            if not next_button:
                logger.info("No next page button — end of results for '%s'", keyword)
                break

            # Delay between pages (guidelines.md §3.2)
            delay = random.uniform(*PAGE_DELAY)
            logger.debug("Waiting %.1fs before next page", delay)
            await asyncio.sleep(delay)

    async def open_application_form(self, job: Job) -> dict:
        """Navigate to job page, click Apply, and inspect what appears.

        Two apply paths on Naukri:
        - Path A (direct): click Apply → confirmation page (/myapply/saveApply)
        - Path B (chatbot): click Apply → chatbot drawer with recruiter questions

        Returns a dict:
            {
                "path": "direct"|"chatbot",
                "status": "applied"|"rejected"|None,  # only for direct path
                "message": str,                        # status text from confirmation
                "questions": [{"text": str, "options": list[str]}],  # chatbot questions
                "has_unrecognized": bool,
            }
        """
        if self._page is None:
            raise RuntimeError("open_application_form() called before login()")

        # Navigate to job detail page. URL is stashed in posted_date field.
        job_url = job.posted_date
        await self._page.goto(job_url, wait_until="domcontentloaded", timeout=30_000)

        # Check for native apply button — if missing, skip (external apply)
        apply_btn = await self._page.query_selector(self.sel.apply_button)
        if not apply_btn:
            return {"path": "no_apply_button", "status": None, "message": "no native apply button", "questions": [], "has_unrecognized": False}

        # Click apply
        try:
            await self._page.click(self.sel.apply_button, timeout=10_000)
        except PlaywrightTimeout:
            await asyncio.sleep(SELECTOR_RETRY_DELAY)
            try:
                await self._page.click(self.sel.apply_button, timeout=10_000)
            except PlaywrightTimeout:
                self._selector_failures += 1
                raise

        # Wait to see which path: chatbot drawer or confirmation page redirect
        try:
            await self._page.wait_for_selector(
                f"{self.sel.chatbot_drawer}, {self.sel.confirmation_page}",
                timeout=10_000,
            )
        except PlaywrightTimeout:
            return {"path": "unknown", "status": None, "message": "neither chatbot nor confirmation appeared", "questions": [], "has_unrecognized": False}

        # Path A: direct apply — already on confirmation page
        if await self._page.query_selector(self.sel.confirmation_page):
            status = "applied"
            message = ""
            if await self._page.query_selector(self.sel.apply_rejected):
                status = "rejected"
            msg_el = await self._page.query_selector(self.sel.apply_message)
            if msg_el:
                message = (await msg_el.inner_text()).strip()
            return {"path": "direct", "status": status, "message": message, "questions": [], "has_unrecognized": False}

        # Path B: chatbot drawer appeared — inspect questions
        questions = await self._parse_chatbot_questions()
        has_unrecognized = any(
            not self._is_recognized_question(q["text"]) for q in questions
        )
        return {"path": "chatbot", "status": None, "message": "", "questions": questions, "has_unrecognized": has_unrecognized}

    async def _parse_chatbot_questions(self) -> list[dict]:
        """Parse all visible questions from the chatbot drawer.

        Returns:
            List of {"text": str, "options": list[str], "type": "radio"|"text"}
        """
        questions: list[dict] = []
        question_els = await self._page.query_selector_all(self.sel.chatbot_question)

        for q_el in question_els:
            text = (await q_el.inner_text()).strip()
            # Skip the intro message ("Hi Varun Sah, thank you...")
            if text.lower().startswith("hi ") and "thank you" in text.lower():
                continue

            # Check for radio options below this question
            # Options are in the chipMsg container that follows the question
            options: list[str] = []
            radio_labels = await self._page.query_selector_all(self.sel.chatbot_radio_label)
            for label in radio_labels:
                label_text = (await label.inner_text()).strip()
                if label_text:
                    options.append(label_text)

            q_type = "radio" if options else "text"
            questions.append({"text": text, "options": options, "type": q_type})

        return questions

    def _is_recognized_question(self, question_text: str) -> bool:
        """Check if a chatbot question maps to a known standard field."""
        q_lower = question_text.lower()
        for field_name in STANDARD_FIELD_NAMES:
            if field_name in q_lower:
                return True
        return False

    async def fill_and_submit(self, form: dict, answers: dict) -> dict:
        """Fill chatbot questions and submit.

        Handles two paths:
        - "direct": apply already submitted, just return the status.
        - "chatbot": answer questions in the drawer, click Save, check confirmation.

        Returns:
            {"status": "applied"|"applied_unconfirmed"|"error"|"rejected", "notes": str}
        """
        if self._page is None:
            raise RuntimeError("fill_and_submit() called before login()")

        # Path A: direct apply already completed
        if form["path"] == "direct":
            if form["status"] == "rejected":
                return {"status": "error", "notes": f"rejected: {form['message']}"}
            return {"status": "applied", "notes": form["message"]}

        # Path B: chatbot drawer — answer questions
        custom_answers = answers.get("_custom", {})
        for question in form["questions"]:
            q_text = question["text"].lower()

            if question["type"] == "radio" and question["options"]:
                # Use human-reviewed answer if provided, otherwise auto-match
                provided = custom_answers.get(q_text)
                if provided:
                    selected = provided
                else:
                    selected = self._match_radio_answer(q_text, question["options"])
                if selected:
                    labels = await self._page.query_selector_all(self.sel.chatbot_radio_label)
                    for label in labels:
                        label_text = (await label.inner_text()).strip()
                        if label_text == selected:
                            await label.click()
                            break
                else:
                    logger.warning("No matching answer for radio question: %s", question["text"])

            elif question["type"] == "text":
                provided = custom_answers.get(q_text)
                if provided:
                    try:
                        await human_type(self._page, self.sel.chatbot_text_input, provided)
                    except Exception as exc:
                        # Selector is unverified — fail gracefully, don't block the submit
                        logger.warning(
                            "Could not fill text question '%s': %s "
                            "(chatbot_text_input in naukri.yaml may need updating from live DOM)",
                            question["text"], exc,
                        )

        # Upload resume if the file input is available
        resume_path = Path("Varun_Sah_CV.pdf")
        if resume_path.exists():
            try:
                await self._page.set_input_files(self.sel.resume_upload, str(resume_path))
            except Exception as exc:
                logger.debug("Resume upload skipped or failed: %s", exc)

        # Click Save button
        try:
            await self._page.click(self.sel.chatbot_save, timeout=10_000)
        except PlaywrightTimeout:
            self._selector_failures += 1
            return {"status": "error", "notes": "selector_broken: chatbot save button"}

        # Wait for confirmation page (chatbot close → navigates to /myapply/saveApply)
        try:
            await self._page.wait_for_selector(self.sel.confirmation_page, timeout=15_000)
        except PlaywrightTimeout:
            return {"status": "applied_unconfirmed", "notes": "no confirmation page after chatbot save"}

        # Check success vs rejected
        if await self._page.query_selector(self.sel.apply_success):
            return {"status": "applied", "notes": ""}
        if await self._page.query_selector(self.sel.apply_rejected):
            msg_el = await self._page.query_selector(self.sel.apply_message)
            msg = (await msg_el.inner_text()).strip() if msg_el else "rejected"
            return {"status": "error", "notes": f"rejected: {msg}"}

        return {"status": "applied_unconfirmed", "notes": "confirmation page reached but no status header found"}

    def _match_radio_answer(self, question_lower: str, options: list[str]) -> str | None:
        """Pick the best radio option for a known question.

        Returns the option text to click, or None if no match.
        """
        # Notice period
        if "notice period" in question_lower:
            preferred = ["immediate", "15 days or less", "serving notice period"]
            for pref in preferred:
                for opt in options:
                    if pref in opt.lower():
                        return opt
            # Default: first option
            return options[0] if options else None

        # Experience
        if "experience" in question_lower or "years" in question_lower:
            for opt in options:
                if "3" in opt or "4" in opt or "2" in opt:
                    return opt
            return options[0] if options else None

        # Location
        if "location" in question_lower or "city" in question_lower or "relocate" in question_lower:
            for opt in options:
                opt_lower = opt.lower()
                if any(loc in opt_lower for loc in ["delhi", "ncr", "gurgaon", "noida", "remote", "yes"]):
                    return opt
            return options[0] if options else None

        # CTC / salary
        if "ctc" in question_lower or "salary" in question_lower:
            return options[0] if options else None

        # Default: first option
        return options[0] if options else None

    async def logout(self) -> None:
        """Log out of Naukri and close browser context."""
        if self._page is not None:
            try:
                await self._page.click(self.sel.profile_dropdown, timeout=5_000)
                # Logout is the last link in the drawer — match by text
                await self._page.click(f"{self.sel.logout_link} >> text=Logout", timeout=5_000)
                logger.info("Naukri logout successful")
            except PlaywrightTimeout:
                logger.warning("Logout selectors failed — closing browser anyway")

        if self._context is not None:
            await self._context.close()
        if self._playwright is not None:
            await self._playwright.stop()

    # ── Orchestration ──────────────────────────────────────────────────
    # Ties together search → score → dedupe → cap → apply for one run.

    async def run(self, keywords: list[str], filters: dict) -> dict[str, int]:
        """Full run: login → search each keyword → process each job → logout.

        Returns:
            Stats dict: {applied, skipped, errored, queued}.
        """
        import time
        self._session_start = time.monotonic()
        self._stats.reset()

        init_log(self.log_path)

        logged_in = await self.login()
        if not logged_in:
            return self._stats

        try:
            for keyword in keywords:
                if self._should_stop():
                    break

                logger.info("[naukri] Searching: %s", keyword)
                async for job in self.search(keyword, filters):
                    if self._should_stop():
                        break
                    await self._process_job(job)
        finally:
            await self.logout()

        logger.info("[naukri] Run complete — %s", self._stats.as_dict())
        return self._stats.as_dict()

    async def _process_job(self, job: Job) -> None:
        """Per-job flow: dedupe → hard-skip → score → threshold → cap → apply.

        Implements guidelines.md §3.2 steps 1–8.
        """
        now = datetime.now(timezone.utc)

        # 1. Dedupe (URL is stashed in posted_date)
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
            self._record(job, fit_score, "skipped", f"cap reached: naukri {current}/{self.daily_cap}")
            logger.info("Daily cap reached (%d/%d) — stopping", current, self.daily_cap)
            return

        # 6. Tier route — Yellow queue for T1 < 0.7, all T2/T3
        if self._should_queue(fit_score, tier):
            self._queue_for_review(job, fit_score, tier, "score_in_review_band")
            return

        # 7. Dry run check
        if self.dry_run:
            self._record(job, fit_score, "skipped", "dry run: would apply")
            return

        # 7. Apply (Green path)
        await self._attempt_apply(job, fit_score, tier)

        # 8. Delay between applications
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

        # No native apply button — external apply, skip
        if form["path"] == "no_apply_button":
            self._record(job, fit_score, "skipped", "no native apply button (external)")
            return

        # Unknown apply outcome
        if form["path"] == "unknown":
            self._record(job, fit_score, "error", form["message"])
            return

        # Direct path — already applied (or rejected) without chatbot
        if form["path"] == "direct":
            result = await self.fill_and_submit(form, self.standard_answers)
            self._record(job, fit_score, result["status"], result.get("notes", ""))
            return

        # Chatbot path — check for unrecognized questions (guidelines.md §3.2 step 7)
        if form["has_unrecognized"]:
            unrecognized = [q for q in form["questions"] if not self._is_recognized_question(q["text"])]
            self._queue_for_review(
                job, fit_score, tier, "custom_questions",
                custom_questions=[
                    {"question": q["text"], "suggested_answer": ""}
                    for q in unrecognized
                ],
            )
            # Close the chatbot drawer without submitting
            try:
                await self._page.click(self.sel.chatbot_close, timeout=5_000)
            except PlaywrightTimeout:
                pass
            return

        # Fill chatbot and submit
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
            await self._page.wait_for_selector(self.sel.login_success, timeout=3_000)
            return True
        except PlaywrightTimeout:
            return False

    async def _detect_captcha(self) -> bool:
        """Check if a CAPTCHA is present on the current page."""
        try:
            await self._page.wait_for_selector(self.sel.captcha, timeout=2_000)
            return True
        except PlaywrightTimeout:
            return False

    async def _parse_job_card(self, card) -> Job | None:
        """Extract a Job from a search result card element.

        Returns None if essential fields can't be parsed.
        Stores job URL in self._current_job_url for use in apply flow.
        """
        try:
            title = await card.query_selector(self.sel.job_title)
            title_text = (await title.inner_text()).strip() if title else ""

            company = await card.query_selector(self.sel.job_company)
            company_text = (await company.inner_text()).strip() if company else ""

            location = await card.query_selector(self.sel.job_location)
            location_text = (await location.inner_text()).strip() if location else ""

            experience = await card.query_selector(self.sel.job_experience)
            experience_text = (await experience.inner_text()).strip() if experience else ""

            # job_url is same element as job_title — read href.
            # Links have target="_blank", so bot navigates via page.goto() not click.
            url_el = await card.query_selector(self.sel.job_url)
            url = (await url_el.get_attribute("href")) if url_el else ""

            snippet = await card.query_selector(self.sel.job_snippet)
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
        """Retry page navigation with exponential backoff (guidelines.md §3.4).

        Returns True if a retry succeeded, False if all retries failed.
        """
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
            logger.warning("STOP file detected — stopping naukri")
            return True
        if self._session_start and (time.monotonic() - self._session_start) > MAX_SESSION_SECONDS:
            logger.warning("Session exceeded %ds — stopping naukri", MAX_SESSION_SECONDS)
            return True
        if self._selector_failures >= MAX_SELECTOR_FAILURES:
            logger.error("Too many selector failures (%d) — stopping naukri", self._selector_failures)
            return True
        return False

    def _should_queue(self, fit_score: float, tier: str) -> bool:
        """Determine if a job should go to the Yellow review queue.

        Yellow conditions (guidelines.md §2.2):
        - T1 with score in [0.5, 0.7)
        - Any T2 or T3 role
        """
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
            job_url=job.posted_date,  # URL stashed in posted_date until Job gets a url field
            fit_score=fit_score,
            status=status,
            notes=notes,
        )
        log_application(entry, self.log_path)
        self._stats.increment(status)
        logger.debug("Recorded: %s at %s [%s] %s", job.title, job.company, status, notes)

    def _log_error(self, notes: str) -> None:
        """Log a platform-level error (e.g. auth failure) without a specific job."""
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
        self._stats.errored += 1

    def _queue_for_review(
        self,
        job: Job,
        fit_score: float,
        tier: str,
        reason: str,
        custom_questions: list[dict] | None = None,
    ) -> None:
        """Write a job to data/review_queue.csv and log as queued.

        Schema per guidelines.md §3.6. Expires after 5 days.
        """
        now = datetime.now(timezone.utc)
        expires = now + timedelta(days=5)

        # Ensure the review queue CSV exists with headers
        if not REVIEW_QUEUE_PATH.exists():
            REVIEW_QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(REVIEW_QUEUE_PATH, "w", newline="") as f:
                csv.writer(f).writerow(REVIEW_QUEUE_COLUMNS)

        row = {
            "queued_at": now.isoformat(),
            "platform": self.PLATFORM_NAME,
            "company_name": job.company,
            "role_title": job.title,
            "job_url": job.posted_date,  # URL stashed in posted_date until Job gets a url field
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
