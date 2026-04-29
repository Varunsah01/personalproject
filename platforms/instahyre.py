"""Instahyre platform integration.

Instahyre is a recruiter-led curated job board popular in India tech.
Candidates see "opportunities" pushed by recruiters and can express
interest (click "Interested" / "Apply"). The platform is invite-driven
but also surfaces open roles for job seekers.

Selector strategy: all CSS selectors live in platforms/selectors/instahyre.yaml.
They are BEST-GUESS UNVERIFIED — run a headful seed session before going live.
After verifying, update the YAML and remove the UNVERIFIED marker.
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import os
import random
import re
import time as time_mod
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from core.browser import create_browser_context, human_type, is_bot_challenged
from core.logger import LogEntry, count_today, init_log, is_duplicate, log_application
from core.scorer import Job, classify_tier, score_job, should_apply
from core.selectors import SelectorStore
from core.types import BotConfig, PlatformStats
from platforms.base import BasePlatform

logger = logging.getLogger(__name__)


# ── URLs ──────────────────────────────────────────────────────────────

BASE_URL = "https://www.instahyre.com"
LOGIN_URL = f"{BASE_URL}/login/"
OPPORTUNITIES_URL = f"{BASE_URL}/candidate/opportunities/"


# ── Config ────────────────────────────────────────────────────────────

MAX_PAGES_PER_KEYWORD = 5         # CLAUDE.md §6
APPLY_DELAY = (5, 15)             # seconds between applications (guidelines.md §3.2)
PAGE_DELAY = (30, 90)             # seconds between search result pages (guidelines.md §3.2)
SELECTOR_RETRY_DELAY = 3          # seconds before retrying a missing selector (guidelines.md §3.4)
MAX_SELECTOR_FAILURES = 5         # per session before stopping platform (guidelines.md §3.4)
NETWORK_RETRY_DELAYS = [5, 15]    # exponential backoff for network errors (guidelines.md §3.4)
MAX_SESSION_SECONDS = 90 * 60     # 90 minutes per platform (guidelines.md §3.3)
LONG_TEXT_THRESHOLD = 100         # chars — fields longer than this demote to Yellow (guidelines.md §3.2)

STOP_FILE = Path("data/STOP")
RESUME_PATH = Path("Varun_Sah_CV.pdf")

REVIEW_QUEUE_PATH = Path("data/review_queue.csv")
REVIEW_QUEUE_COLUMNS = [
    "queued_at", "platform", "company_name", "role_title", "job_url",
    "fit_score", "tier", "reason_queued", "custom_questions", "expires_at",
]

# Standard field names the bot knows how to answer
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


class InstahyrePlatform(BasePlatform):
    """Instahyre integration.

    Instahyre is recruiter-led: opportunities are pushed to candidates.
    The search flow browses the candidate opportunities page, optionally
    filtered by keyword. Apply is typically a one-click "Interested" or
    a short form.

    Args:
        config: BotConfig with shared settings (log path, caps, headless, etc.).
    """

    PLATFORM_NAME = "instahyre"

    def __init__(self, config: BotConfig) -> None:
        super().__init__(config)

        # Platform-specific credentials (from .env)
        self.email = os.getenv("INSTAHYRE_EMAIL", "")
        self.password = os.getenv("INSTAHYRE_PASSWORD", "")

        # Selector registry — backed by platforms/selectors/instahyre.yaml
        self.sel = SelectorStore("instahyre")

        # Augment shared standard_answers with Instahyre-specific fields
        self.standard_answers.update({
            "email": self.email,
            "current_ctc": os.getenv("INSTAHYRE_CURRENT_CTC", ""),
            "expected_ctc": os.getenv("INSTAHYRE_EXPECTED_CTC", ""),
        })

    # ── URL helpers ────────────────────────────────────────────────────

    @staticmethod
    def _build_search_url(
        keyword: str,
        location: str = "",
        page: int = 1,
    ) -> str:
        """Build an Instahyre opportunities URL with query parameters.

        Instahyre's candidate opportunities page supports keyword and
        location query params. The exact param names are BEST-GUESS
        UNVERIFIED — verify during headful seed session.

        Format:
            https://www.instahyre.com/candidate/opportunities/?q={keyword}&location={location}&page={page}
        """
        params: dict[str, str | int] = {}
        if keyword:
            params["q"] = keyword
        if location:
            params["location"] = location
        if page > 1:
            params["page"] = page

        qs = urlencode(params) if params else ""
        base = OPPORTUNITIES_URL
        return f"{base}?{qs}" if qs else base

    # ── BasePlatform interface ─────────────────────────────────────────

    async def login(self) -> bool:
        """Log into Instahyre using .env credentials.

        Uses persistent browser profile so subsequent runs may already
        be logged in (cookie-based session).

        Returns:
            True if login succeeded, False otherwise.
            On CAPTCHA / bot detection, logs auth_challenge and returns False.
        """
        if not self.email or not self.password:
            logger.error("INSTAHYRE_EMAIL or INSTAHYRE_PASSWORD not set in .env")
            self._log_error("auth_failed: missing credentials")
            return False

        from playwright.async_api import async_playwright
        self._session_start = time_mod.monotonic()
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
            try:
                await self._page.wait_for_load_state("load", timeout=10_000)
            except PlaywrightTimeout:
                pass
            return True

        # Check for CAPTCHA before attempting login
        if await self._detect_captcha():
            logger.error("CAPTCHA detected on login page — stopping platform")
            self._log_error("auth_challenge: captcha")
            return False

        # Check for bot detection (403 / challenge page)
        if await is_bot_challenged(self._page):
            logger.error("Bot detection triggered — stopping platform")
            self._log_error("auth_challenge: bot_detection")
            return False

        # Fill credentials
        try:
            await self._page.fill(self.sel.login_email, self.email)
            await human_type(self._page, self.sel.login_password, self.password)
            await self._page.click(self.sel.login_submit)
            await self._page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            logger.error("Login form interaction timed out")
            self._log_error("auth_failed: timeout")
            return False

        # Post-login bot detection check
        if await self._detect_captcha():
            logger.error("CAPTCHA detected after login submit — stopping platform")
            self._log_error("auth_challenge: captcha")
            return False

        if await is_bot_challenged(self._page):
            logger.error("Bot detection after login — stopping platform")
            self._log_error("auth_challenge: bot_detection")
            return False

        if not await self._is_logged_in():
            logger.error("Login failed — success indicator not found")
            self._log_error("auth_failed: credentials rejected or unknown error")
            return False

        logger.info("Instahyre login successful")
        return True

    async def search(self, keyword: str, filters: dict) -> AsyncIterator[Job]:
        """Search Instahyre opportunities matching keyword and filters.

        Paginates up to MAX_PAGES_PER_KEYWORD pages. Yields Job objects.
        Inserts PAGE_DELAY between pages.

        Args:
            keyword: Search query, e.g. "growth manager".
            filters: Dict with 'locations' (list[str]).

        Yields:
            Job objects parsed from opportunity cards.
        """
        if self._page is None:
            logger.error("search() called before login()")
            return

        location = filters.get("locations", [""])[0] if filters.get("locations") else ""

        for page_num in range(1, MAX_PAGES_PER_KEYWORD + 1):
            if self._should_stop():
                return

            url = self._build_search_url(keyword, location, page=page_num)
            logger.info("[instahyre] Page %d for '%s': %s", page_num, keyword, url)

            try:
                await self._page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            except Exception as exc:
                logger.warning(
                    "Search page %d navigation failed for '%s': %s",
                    page_num, keyword, exc,
                )
                retried = await self._retry_navigation(url)
                if not retried:
                    break

            # Wait for opportunity cards to render (may be CSR / React)
            try:
                await self._page.wait_for_selector(self.sel.job_card, timeout=15_000)
            except PlaywrightTimeout:
                pass  # fall through to the empty-check below

            job_cards = await self._page.query_selector_all(self.sel.job_card)
            if not job_cards:
                logger.info("No opportunity cards found on page %d — end of results", page_num)
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
        """Navigate to opportunity page, click Apply/Interested, inspect what appears.

        Instahyre may have:
        - One-click "Interested" (no form, instant apply)
        - Short application form (pre-filled from profile)
        - External redirect

        Returns a dict:
            {
                "path": "one_click"|"form"|"no_apply_button"|"unknown",
                "status": "applied"|None,
                "message": str,
                "has_unrecognized": bool,
                "custom_questions": list[str],
            }
        """
        if self._page is None:
            raise RuntimeError("open_application_form() called before login()")

        job_url = job.posted_date
        await self._page.goto(job_url, wait_until="domcontentloaded", timeout=30_000)

        # Check for bot detection on the job page
        if await is_bot_challenged(self._page):
            logger.error("Bot detection on job page — aborting")
            self._log_error("auth_challenge: bot_detection on job page")
            return {"path": "no_apply_button", "status": None, "message": "bot detected", "has_unrecognized": False, "custom_questions": []}

        # Look for apply / interested button
        apply_btn = await self._page.query_selector(self.sel.apply_button)
        if not apply_btn:
            # Also try the "Accept" button for recruiter invitations
            apply_btn = await self._page.query_selector(self.sel.accept_button)
        if not apply_btn:
            return {"path": "no_apply_button", "status": None, "message": "no apply/interested button", "has_unrecognized": False, "custom_questions": []}

        # Click apply / interested
        try:
            await apply_btn.click(timeout=10_000)
        except PlaywrightTimeout:
            await asyncio.sleep(SELECTOR_RETRY_DELAY)
            try:
                await apply_btn.click(timeout=10_000)
            except PlaywrightTimeout:
                self._selector_failures += 1
                raise

        # Wait to see what happens: form, confirmation, or nothing
        await self._page.wait_for_timeout(3000)

        # Check for success confirmation (one-click apply)
        success_el = await self._page.query_selector(self.sel.apply_success)
        if success_el:
            message = ""
            msg_el = await self._page.query_selector(self.sel.apply_message)
            if msg_el:
                message = (await msg_el.inner_text()).strip()
            return {"path": "one_click", "status": "applied", "message": message, "has_unrecognized": False, "custom_questions": []}

        # Check for application form
        form_el = await self._page.query_selector(self.sel.form_container)
        if form_el:
            # Detect custom / unrecognized fields
            custom_qs = await self._detect_custom_fields()
            has_unrecognized = len(custom_qs) > 0
            return {"path": "form", "status": None, "message": "", "has_unrecognized": has_unrecognized, "custom_questions": custom_qs}

        # Unknown outcome — button was clicked but neither form nor confirmation
        return {"path": "unknown", "status": None, "message": "no form or confirmation after clicking apply", "has_unrecognized": False, "custom_questions": []}

    async def fill_and_submit(self, form: dict, answers: dict) -> dict:
        """Fill the Instahyre application form and submit.

        Handles two paths:
        - "one_click": already applied, return the status.
        - "form": fill fields, upload resume, submit.

        Returns:
            {"status": "applied"|"applied_unconfirmed"|"error", "notes": str}
        """
        if self._page is None:
            raise RuntimeError("fill_and_submit() called before login()")

        # One-click path — already completed
        if form["path"] == "one_click":
            return {"status": "applied", "notes": form.get("message", "")}

        # Form path — fill standard fields
        try:
            # Name
            name_el = await self._page.query_selector(self.sel.field_name)
            if name_el:
                current = (await name_el.input_value()).strip()
                if not current:
                    await human_type(self._page, self.sel.field_name, answers.get("name", "Varun Sah"))

            # Email
            email_el = await self._page.query_selector(self.sel.field_email)
            if email_el:
                current = (await email_el.input_value()).strip()
                if not current:
                    await human_type(self._page, self.sel.field_email, answers.get("email", ""))

            # Phone
            phone_el = await self._page.query_selector(self.sel.field_phone)
            if phone_el:
                current = (await phone_el.input_value()).strip()
                if not current:
                    await human_type(self._page, self.sel.field_phone, answers.get("phone", "+91-8595062552"))

            # LinkedIn
            linkedin_el = await self._page.query_selector(self.sel.field_linkedin)
            if linkedin_el:
                current = (await linkedin_el.input_value()).strip()
                if not current:
                    await human_type(
                        self._page, self.sel.field_linkedin,
                        answers.get("linkedin", "https://www.linkedin.com/in/varun-sah/"),
                    )

            # Resume upload
            resume_el = await self._page.query_selector(self.sel.resume_upload)
            if resume_el and RESUME_PATH.exists():
                await resume_el.set_input_files(str(RESUME_PATH.resolve()))

            # Submit
            await self._page.click(self.sel.submit_button, timeout=10_000)
            await self._page.wait_for_timeout(3000)

            # Check for confirmation
            success_el = await self._page.query_selector(self.sel.apply_success)
            if success_el:
                return {"status": "applied", "notes": ""}
            return {"status": "applied_unconfirmed", "notes": "no confirmation element found after submit"}

        except PlaywrightTimeout:
            self._selector_failures += 1
            return {"status": "error", "notes": "selector_broken: form field or submit button"}
        except Exception as exc:
            return {"status": "error", "notes": f"form fill/submit error: {exc}"}

    async def logout(self) -> None:
        """Log out of Instahyre and close browser context."""
        if self._page is not None:
            try:
                await self._page.click(self.sel.profile_menu, timeout=5_000)
                await self._page.click(self.sel.logout_link, timeout=5_000)
                logger.info("Instahyre logout successful")
            except PlaywrightTimeout:
                logger.warning("Logout selectors failed — closing browser anyway")

        if self._context is not None:
            await self._context.close()
        if self._playwright is not None:
            await self._playwright.stop()

        self._page = None
        self._context = None
        self._playwright = None

    # ── Orchestration ──────────────────────────────────────────────────

    async def run(self, keywords: list[str], filters: dict) -> dict[str, int]:
        """Full run: login -> search each keyword -> process each job -> logout.

        Returns:
            Stats dict: {applied, skipped, errored, queued}.
        """
        self._session_start = time_mod.monotonic()
        self._stats.reset()

        init_log(self.log_path)

        logged_in = await self.login()
        if not logged_in:
            return self._stats.as_dict()

        try:
            for keyword in keywords:
                if self._should_stop():
                    break

                logger.info("[instahyre] Searching: %s", keyword)
                async for job in self.search(keyword, filters):
                    if self._should_stop():
                        break
                    await self._process_job(job)
        finally:
            await self.logout()

        logger.info("[instahyre] Run complete — %s", self._stats.as_dict())
        return self._stats.as_dict()

    async def _process_job(self, job: Job) -> None:
        """Per-job flow: dedupe -> hard-skip -> score -> threshold -> cap -> apply.

        Implements guidelines.md section 3.2 steps 1-8.
        """
        # 1. Dedupe (URL is stashed in posted_date)
        if is_duplicate(self.PLATFORM_NAME, job.posted_date, self.log_path):
            self._record(job, 0.0, "skipped", "duplicate")
            return

        # 2-4. Score and decide
        fit_score = score_job(job)
        tier = classify_tier(job.title)
        do_apply, reason = should_apply(job, fit_score, tier)

        if not do_apply:
            self._record(job, fit_score, "skipped", reason)
            return

        # 5. Cap check
        current = count_today(self.PLATFORM_NAME, self.log_path)
        if current >= self.daily_cap:
            self._record(job, fit_score, "skipped",
                         f"cap reached: instahyre {current}/{self.daily_cap}")
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

        # 8. Apply (Green path)
        await self._attempt_apply(job, fit_score, tier)

        # Delay between applications
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
                logger.error("Too many selector failures (%d) — stopping platform",
                             self._selector_failures)
            return
        except Exception as exc:
            self._record(job, fit_score, "error", f"error opening form: {exc}")
            return

        # No apply button — external apply or not available
        if form["path"] == "no_apply_button":
            self._record(job, fit_score, "skipped", "no apply button (external or unavailable)")
            return

        # Unknown outcome
        if form["path"] == "unknown":
            self._record(job, fit_score, "error", form["message"])
            return

        # One-click path — already applied
        if form["path"] == "one_click":
            result = await self.fill_and_submit(form, self.standard_answers)
            self._record(job, fit_score, result["status"], result.get("notes", ""))
            return

        # Form path — check for unrecognized fields (queue for review)
        if form["has_unrecognized"]:
            self._queue_for_review(
                job, fit_score, tier, "custom_questions",
                custom_questions=[
                    {"question": q, "suggested_answer": ""}
                    for q in form.get("custom_questions", [])
                ],
            )
            return

        # Fill form and submit
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
        """Extract a Job from an opportunity card element.

        Returns None if essential fields can't be parsed.
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

            url_el = await card.query_selector(self.sel.job_url)
            url = (await url_el.get_attribute("href")) if url_el else ""

            # Make relative URLs absolute
            if url and not url.startswith("http"):
                url = f"{BASE_URL}{url}" if url.startswith("/") else f"{BASE_URL}/{url}"

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
            logger.debug("Failed to parse opportunity card: %s", exc)
            return None

    async def _detect_custom_fields(self) -> list[str]:
        """Detect unrecognized form fields that need human review.

        Returns list of field label texts that are not in STANDARD_FIELD_NAMES.
        """
        if self._page is None:
            return []

        custom_qs: list[str] = []
        labels = await self._page.query_selector_all("label")
        for label_el in labels:
            text = (await label_el.inner_text()).strip().lower()
            if not text:
                continue
            if any(k in text for k in STANDARD_FIELD_NAMES):
                continue
            # Skip demographic / consent labels
            if any(k in text for k in ("gender", "race", "ethnicity", "veteran",
                                        "disability", "consent", "privacy", "agree")):
                continue
            custom_qs.append(text)
        return custom_qs

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
            except Exception:
                continue
        return False

    def _should_stop(self) -> bool:
        """Check kill-switches: STOP file, session timer, selector failures."""
        if STOP_FILE.exists():
            logger.warning("STOP file detected — stopping instahyre")
            return True
        if self._session_start and (time_mod.monotonic() - self._session_start) > MAX_SESSION_SECONDS:
            logger.warning("Session exceeded %ds — stopping instahyre", MAX_SESSION_SECONDS)
            return True
        if self._selector_failures >= MAX_SELECTOR_FAILURES:
            logger.error("Too many selector failures (%d) — stopping instahyre",
                         self._selector_failures)
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
            job_url=job.posted_date,
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

        Schema per guidelines.md section 3.6. Expires after 5 days.
        """
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
