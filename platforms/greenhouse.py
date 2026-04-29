"""Greenhouse Job Board API integration.

Uses Greenhouse's public Job Board API for discovery (no auth needed)
and Playwright for form submission at each job's absolute_url.

API docs: https://developers.greenhouse.io/job-board.html
Companies list: config/greenhouse_companies.txt
"""

from __future__ import annotations

import asyncio
import csv
import html
import json
import logging
import os
import random
import re
import time as time_mod
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.browser import create_browser_context, human_type
from core.logger import LogEntry, count_today, log_application
from core.scorer import Job, classify_tier, score_job, should_apply
from core.types import BotConfig, PlatformStats
from platforms.base import BasePlatform

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────

API_BASE = "https://boards-api.greenhouse.io/v1/boards"
COMPANIES_PATH = Path("config/greenhouse_companies.txt")

CACHE_DIR = Path("data/greenhouse_cache")
CACHE_TTL_SECONDS = 6 * 3600  # 6 hours

API_DELAY_SECONDS = 1.0       # polite rate limit between API requests
APPLY_DELAY = (5, 15)         # seconds between form submissions (guidelines.md §3.2)
MAX_SESSION_SECONDS = 90 * 60 # 90 minutes per platform (guidelines.md §3.3)
MAX_SELECTOR_FAILURES = 5     # per session before stopping (guidelines.md §3.4)

STOP_FILE = Path("data/STOP")
RESUME_PATH = Path("Varun_Sah_CV.pdf")

DEFAULT_HEAR_ABOUT = "Personal referral / network"

REVIEW_QUEUE_PATH = Path("data/review_queue.csv")
REVIEW_QUEUE_COLUMNS = [
    "queued_at", "platform", "company_name", "role_title", "job_url",
    "fit_score", "tier", "reason_queued", "custom_questions", "expires_at",
]

# ── Location filters ─────────────────────────────────────────────────

_INDIA_INDICATORS = [
    "india", "delhi", "bangalore", "bengaluru", "mumbai", "gurgaon",
    "gurugram", "noida", "hyderabad", "pune", "chennai", "kolkata",
    "ncr",
]

_REMOTE_INDICATORS = [
    "remote", "anywhere", "global", "worldwide", "distributed",
]

# ── Greenhouse form selectors ────────────────────────────────────────
# Greenhouse application forms have a standard structure.  These selectors
# target the hosted form at boards.greenhouse.io.

SEL_APPLY_BUTTON = 'a[href*="#app"], a.postings-btn, button:has-text("Apply")'
SEL_FORM = "#application-form, #application_form, form.application-form, form[action*='applications']"
SEL_FIRST_NAME = "#first_name, input[name='first_name']"
SEL_LAST_NAME = "#last_name, input[name='last_name']"
SEL_EMAIL = "#email, input[name='email']"
SEL_PHONE = "#phone, input[name='phone']"
SEL_RESUME_INPUT = "input[type='file'][name*='resume'], input[type='file'][name*='cv'], input[type='file']"
SEL_LINKEDIN = "input[name*='linkedin'], input[id*='linkedin'], input[aria-label*='LinkedIn']"
SEL_SUBMIT = "button[type='submit'], input[type='submit'], button:has-text('Submit')"
SEL_CONFIRMATION = ".confirmation, .thank-you, .success, h1:has-text('Thank'), h1:has-text('Application')"


def _strip_html(text: str) -> str:
    """Remove HTML tags and decode entities to plain text."""
    decoded = html.unescape(text)
    return re.sub(r"<[^>]+>", " ", decoded).strip()


def _load_companies() -> list[str]:
    """Read board tokens from config/greenhouse_companies.txt."""
    if not COMPANIES_PATH.exists():
        logger.warning("Companies file not found at %s", COMPANIES_PATH)
        return []
    tokens: list[str] = []
    for line in COMPANIES_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        tokens.append(line)
    return tokens


def _location_matches(location_name: str) -> bool:
    """Return True if the location is compatible with India / Remote."""
    loc = location_name.lower()
    if any(ind in loc for ind in _INDIA_INDICATORS):
        return True
    if any(ind in loc for ind in _REMOTE_INDICATORS):
        return True
    return False


def _keyword_matches(title: str, keyword: str) -> bool:
    """Return True if the keyword is found in the job title (case-insensitive)."""
    return keyword.lower() in title.lower()


class GreenhousePlatform(BasePlatform):
    """Greenhouse Job Board integration.

    Discovery uses the public REST API (httpx, no browser).
    Application uses Playwright to fill the standard Greenhouse form.

    Args:
        config: BotConfig with shared settings.
    """

    PLATFORM_NAME = "greenhouse"

    def __init__(self, config: BotConfig) -> None:
        super().__init__(config)
        self._companies: list[str] = _load_companies()
        self._api_cache: dict[str, list[dict]] = {}  # company -> raw jobs list
        self._seen_urls: set[str] = set()  # dedupe across keywords within a run

        self.standard_answers.update({
            "email": os.getenv("GREENHOUSE_EMAIL", os.getenv("NAUKRI_EMAIL", "")),
            "hear_about": os.getenv("GREENHOUSE_HEAR_ABOUT", DEFAULT_HEAR_ABOUT),
        })

    # ── BasePlatform interface ────────────────────────────────────────

    async def login(self) -> bool:
        """Set up a browser context for form filling.

        No credentials required — the Greenhouse public API needs no auth,
        and the application form is public.

        Returns:
            True always.
        """
        from playwright.async_api import async_playwright

        self._session_start = time_mod.monotonic()
        self._playwright = await async_playwright().start()
        self._context = await create_browser_context(
            platform=self.PLATFORM_NAME,
            headless=self.headless,
            playwright=self._playwright,
        )
        self._page = await self._context.new_page()
        logger.info("Greenhouse browser context ready")
        return True

    async def search(self, keyword: str, filters: dict) -> AsyncIterator[Job]:
        """Search all configured Greenhouse boards for matching jobs.

        For each company, fetches the full job list from the API (cached
        for 6 hours), then filters by keyword in title and location.

        Args:
            keyword: Search query, e.g. "growth manager".
            filters: Dict with 'locations' (used for logging only — actual
                     filtering is done against the API's location field).

        Yields:
            Job objects for matching positions.
        """
        for company in self._companies:
            if self._should_stop():
                return

            jobs = await self._fetch_jobs(company)

            for raw in jobs:
                if self._should_stop():
                    return

                title = raw.get("title", "")
                if not _keyword_matches(title, keyword):
                    continue

                loc_name = raw.get("location", {}).get("name", "")
                if not _location_matches(loc_name):
                    continue

                absolute_url = raw.get("absolute_url", "")
                if not absolute_url:
                    continue

                # Dedupe within this run (same job may match multiple keywords)
                if absolute_url in self._seen_urls:
                    continue
                self._seen_urls.add(absolute_url)

                content_html = raw.get("content", "")
                jd_text = _strip_html(content_html)[:500] if content_html else ""

                yield Job(
                    title=title,
                    company=company,
                    location=loc_name,
                    experience_required="",  # Greenhouse API doesn't expose this directly
                    jd_text=jd_text,
                    posted_date=absolute_url,  # URL stashed in posted_date
                )

    async def open_application_form(self, job: Job) -> dict:
        """Navigate to the Greenhouse application page and locate the form.

        Args:
            job: Job object (URL in job.posted_date).

        Returns:
            Dict with form metadata:
            - path: "greenhouse" if form found, "no_form" otherwise
            - has_unrecognized: bool — True if custom questions detected
            - custom_questions: list of unrecognized question labels
        """
        if self._page is None:
            return {"path": "no_form", "message": "no browser page"}

        url = job.posted_date
        try:
            await self._page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        except Exception as exc:
            return {"path": "no_form", "message": f"navigation error: {exc}"}

        # Some Greenhouse pages show the form inline; others require clicking Apply
        form_el = await self._page.query_selector(SEL_FORM)
        if not form_el:
            try:
                apply_btn = await self._page.query_selector(SEL_APPLY_BUTTON)
                if apply_btn:
                    await apply_btn.click()
                    await self._page.wait_for_timeout(2000)
                    form_el = await self._page.query_selector(SEL_FORM)
            except Exception:
                pass

        if not form_el:
            return {"path": "no_form", "message": "application form not found on page"}

        # Detect custom questions (fields we don't auto-fill)
        custom_qs: list[str] = []
        labels = await self._page.query_selector_all("label")
        known_labels = {
            "first name", "last name", "full name", "email", "phone",
            "resume", "cv", "cover letter", "linkedin",
            "how did you hear", "website", "location",
        }
        for label_el in labels:
            text = (await label_el.inner_text()).strip().lower()
            if not text:
                continue
            if any(k in text for k in known_labels):
                continue
            # Skip demographic / consent labels
            if any(k in text for k in ("gender", "race", "ethnicity", "veteran",
                                        "disability", "consent", "privacy", "agree")):
                continue
            custom_qs.append(text)

        return {
            "path": "greenhouse",
            "has_unrecognized": len(custom_qs) > 0,
            "custom_questions": custom_qs,
        }

    async def fill_and_submit(self, form: dict, answers: dict) -> dict:
        """Fill the standard Greenhouse application form and submit.

        Args:
            form: Dict from open_application_form().
            answers: Standard answers dict.

        Returns:
            {"status": "applied"|"applied_unconfirmed"|"error", "notes": str}
        """
        if self._page is None:
            return {"status": "error", "notes": "no browser page"}

        try:
            # First name
            first_name_el = await self._page.query_selector(SEL_FIRST_NAME)
            if first_name_el:
                await human_type(self._page, SEL_FIRST_NAME, "Varun")

            # Last name
            last_name_el = await self._page.query_selector(SEL_LAST_NAME)
            if last_name_el:
                await human_type(self._page, SEL_LAST_NAME, "Sah")

            # Email
            email_el = await self._page.query_selector(SEL_EMAIL)
            if email_el:
                await human_type(self._page, SEL_EMAIL, answers.get("email", ""))

            # Phone
            phone_el = await self._page.query_selector(SEL_PHONE)
            if phone_el:
                await human_type(self._page, SEL_PHONE, answers.get("phone", "+91-8595062552"))

            # Resume upload
            resume_el = await self._page.query_selector(SEL_RESUME_INPUT)
            if resume_el and RESUME_PATH.exists():
                await resume_el.set_input_files(str(RESUME_PATH.resolve()))

            # LinkedIn
            linkedin_el = await self._page.query_selector(SEL_LINKEDIN)
            if linkedin_el:
                await human_type(
                    self._page, SEL_LINKEDIN,
                    answers.get("linkedin", "https://www.linkedin.com/in/varun-sah/"),
                )

            # "How did you hear about us?" — try select dropdown first, then text input
            await self._fill_hear_about(answers.get("hear_about", DEFAULT_HEAR_ABOUT))

            # GDPR / consent checkboxes — check all visible ones
            consent_boxes = await self._page.query_selector_all(
                "input[type='checkbox'][name*='consent'], input[type='checkbox'][name*='data_compliance']"
            )
            for cb in consent_boxes:
                checked = await cb.is_checked()
                if not checked:
                    await cb.check()

            # Submit
            await self._page.click(SEL_SUBMIT, timeout=10_000)
            await self._page.wait_for_timeout(3000)

            # Check for confirmation
            confirmation = await self._page.query_selector(SEL_CONFIRMATION)
            if confirmation:
                return {"status": "applied", "notes": ""}
            return {"status": "applied_unconfirmed", "notes": "no confirmation element found"}

        except Exception as exc:
            return {"status": "error", "notes": f"form fill/submit error: {exc}"}

    async def logout(self) -> None:
        """Close the browser context. No actual logout needed."""
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
        self._page = None
        self._context = None
        self._playwright = None

    # ── Orchestration (mirrors naukri.py pattern) ─────────────────────

    async def run(self, keywords: list[str], filters: dict) -> dict[str, int]:
        """Full single-platform run: login → search → process → logout.

        Used in single-platform mode (--platform greenhouse).

        Returns:
            {"applied": int, "skipped": int, "errored": int, "queued": int}
        """
        self._session_start = time_mod.monotonic()
        self._stats.reset()

        from core.logger import init_log
        init_log(self.log_path)

        logged_in = await self.login()
        if not logged_in:
            return self._stats.as_dict()

        try:
            for keyword in keywords:
                if self._should_stop():
                    break
                async for job in self.search(keyword, filters):
                    if self._should_stop():
                        break
                    await self._process_job(job)
        finally:
            await self.logout()

        return self._stats.as_dict()

    async def _process_job(self, job: Job) -> None:
        """Per-job flow: dedupe → score → threshold → cap → queue/apply.

        Implements guidelines.md §3.2 steps 1-8.
        """
        from core.logger import is_duplicate

        # 1. Dedupe
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
                         f"cap reached: greenhouse {current}/{self.daily_cap}")
            return

        # 6. Tier route — Yellow queue for T2/T3 or T1 < 0.7
        if self._should_queue(fit_score, tier):
            self._queue_for_review(job, fit_score, tier, "score_in_review_band")
            return

        # 7. Dry run check
        if self.dry_run:
            self._record(job, fit_score, "skipped", "dry run: would apply")
            return

        # 8. Apply
        await self._attempt_apply(job, fit_score, tier)

        # Delay between applications
        delay = random.uniform(*APPLY_DELAY)
        await asyncio.sleep(delay)

    async def _attempt_apply(self, job: Job, fit_score: float, tier: str) -> None:
        """Open form, check complexity, fill, submit."""
        try:
            form = await self.open_application_form(job)
        except Exception as exc:
            self._record(job, fit_score, "error", f"error opening form: {exc}")
            return

        if form["path"] == "no_form":
            self._record(job, fit_score, "skipped",
                         f"no form: {form.get('message', 'unknown')}")
            return

        # Custom questions → queue for human review
        if form.get("has_unrecognized"):
            self._queue_for_review(
                job, fit_score, tier, "custom_questions",
                custom_questions=[
                    {"question": q, "suggested_answer": ""}
                    for q in form.get("custom_questions", [])
                ],
            )
            return

        result = await self.fill_and_submit(form, self.standard_answers)
        self._record(job, fit_score, result["status"], result.get("notes", ""))

    # ── Helpers ───────────────────────────────────────────────────────

    async def _fetch_jobs(self, company: str) -> list[dict]:
        """Fetch job listings for a company, using cache if fresh.

        Returns the raw API 'jobs' array. Empty list on error.
        """
        # In-memory cache (across keywords within one run)
        if company in self._api_cache:
            return self._api_cache[company]

        # Disk cache
        cache_file = CACHE_DIR / f"{company}.json"
        if cache_file.exists():
            age = time_mod.time() - cache_file.stat().st_mtime
            if age < CACHE_TTL_SECONDS:
                try:
                    data = json.loads(cache_file.read_text(encoding="utf-8"))
                    self._api_cache[company] = data
                    logger.debug("[greenhouse] Cache hit for %s (%d jobs)", company, len(data))
                    return data
                except (json.JSONDecodeError, OSError):
                    pass  # cache corrupt — refetch

        # Fetch from API
        import httpx

        url = f"{API_BASE}/{company}/jobs?content=true"
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url)

            if resp.status_code == 404:
                logger.debug("[greenhouse] Board not found for %s", company)
                self._api_cache[company] = []
                return []

            resp.raise_for_status()
            payload = resp.json()
            jobs = payload.get("jobs", [])
        except Exception as exc:
            logger.warning("[greenhouse] API error for %s: %s", company, exc)
            self._api_cache[company] = []
            return []

        # Write to disk cache
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            cache_file.write_text(json.dumps(jobs), encoding="utf-8")
        except OSError as exc:
            logger.debug("Could not write cache for %s: %s", company, exc)

        self._api_cache[company] = jobs
        logger.info("[greenhouse] Fetched %d jobs for %s", len(jobs), company)

        # Polite rate limit between API requests
        await asyncio.sleep(API_DELAY_SECONDS)

        return jobs

    async def _fill_hear_about(self, answer: str) -> None:
        """Try to fill the 'How did you hear about us?' field."""
        if self._page is None:
            return

        # Try select dropdown
        selects = await self._page.query_selector_all("select")
        for sel in selects:
            name = (await sel.get_attribute("name") or "").lower()
            label_id = await sel.get_attribute("aria-labelledby") or ""
            if "hear" in name or "source" in name:
                # Try to select matching option
                options = await sel.query_selector_all("option")
                for opt in options:
                    text = (await opt.inner_text()).strip().lower()
                    if "referral" in text or "network" in text or "other" in text:
                        value = await opt.get_attribute("value")
                        if value:
                            await sel.select_option(value=value)
                            return
                # Fall back to first non-empty option
                for opt in options:
                    value = await opt.get_attribute("value")
                    if value:
                        await sel.select_option(value=value)
                        return

        # Try text input with label containing "hear"
        labels = await self._page.query_selector_all("label")
        for label_el in labels:
            text = (await label_el.inner_text()).strip().lower()
            if "hear" in text or "source" in text:
                for_attr = await label_el.get_attribute("for")
                if for_attr:
                    inp = await self._page.query_selector(f"#{for_attr}")
                    if inp:
                        tag = await inp.evaluate("el => el.tagName.toLowerCase()")
                        if tag == "input":
                            await human_type(self._page, f"#{for_attr}", answer)
                            return
                        if tag == "textarea":
                            await human_type(self._page, f"#{for_attr}", answer)
                            return

    def _should_stop(self) -> bool:
        """Check if the platform run should stop."""
        if STOP_FILE.exists():
            logger.warning("STOP file detected — stopping greenhouse")
            return True
        if time_mod.monotonic() - self._session_start > MAX_SESSION_SECONDS:
            logger.warning("Session time limit reached — stopping greenhouse")
            return True
        if self._selector_failures >= MAX_SELECTOR_FAILURES:
            logger.warning("Too many selector failures — stopping greenhouse")
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
        self._stats.errored += 1

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
