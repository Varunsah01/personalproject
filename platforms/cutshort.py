"""Cutshort platform integration.

Build step 8. India tech roles, moderate volume.
Daily cap: 25 (configurable via DAILY_CAP_CUTSHORT in .env).

Cutshort uses tag-based filtering rather than free-text search. Keywords
from CLAUDE.md §4 are mapped to Cutshort tag slugs at the top of this file.
When the caller passes a keyword, _tags_for_keyword() looks it up in the
mapping and returns the list of tags to try.

Most Cutshort applications are single-click Easy Apply: click Apply, no form
appears, application is submitted immediately. Some jobs surface a minimal
form (1–2 standard fields). Custom essay questions are rare but possible.

ATS redirect: some companies redirect the Apply click to their own ATS
(Greenhouse, Lever, Workable, etc.). This is detected before clicking
(external href on the apply button) and after clicking (URL leaves
cutshort.io). Both cases are logged as "off_platform_redirect" and skipped.

Selector strategy: all CSS selectors are class constants at the top.
They WILL break when Cutshort ships UI changes — update them here,
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
from urllib.parse import urlparse

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from core.browser import create_browser_context, human_type
from core.logger import LogEntry, count_today, init_log, is_duplicate, log_application
from core.scorer import Job, classify_tier, score_job, should_apply
from platforms.base import BasePlatform

logger = logging.getLogger(__name__)


# ── Keyword → Cutshort tag mapping ─────────────────────────────────────
# Cutshort filters jobs by tag slugs, not free-text. Each keyword from
# CLAUDE.md §4 maps to one or more tag slugs to try in sequence.
# When a keyword has multiple tags, the search is run once per tag
# (deduplication prevents double-applying to the same job URL).
# TODO: validate tag slugs against Cutshort's actual tag list after
# a live DOM inspection — these are best-guess slugs for now.

KEYWORD_TAG_MAP: dict[str, list[str]] = {
    # Primary keywords (CLAUDE.md §4)
    "growth manager":               ["growth-manager", "growth-hacking"],
    "head of growth":               ["head-of-growth", "growth-manager"],
    "product manager":              ["product-manager", "product-management"],
    "associate product manager":    ["associate-product-manager", "product-manager"],
    "strategy and operations":      ["strategy-and-operations", "operations"],
    "business development manager": ["business-development", "bd-manager"],
    "founding team":                ["founding-team", "early-stage-startup"],
    "founding member":              ["founding-member", "founding-team"],
    "GTM manager":                  ["go-to-market", "gtm"],
    "early employee":               ["early-stage-startup", "founding-team"],

    # Secondary keywords (CLAUDE.md §4)
    "partnerships manager":         ["partnerships", "business-development"],
    "revenue operations":           ["revenue-operations", "revops"],
    "VC analyst":                   ["venture-capital", "vc-analyst"],
    "chief of staff":               ["chief-of-staff"],
    "program manager":              ["program-management", "project-management"],

    # Niche / high-fit keywords (CLAUDE.md §4)
    "founding growth":              ["founding-team", "growth-hacking"],
    "founder's office":             ["founders-office", "founding-team"],
    "growth associate":             ["growth-hacking", "growth-manager"],
    "early-stage operator":         ["early-stage-startup", "operations"],
    "0 to 1":                       ["early-stage-startup", "founding-team"],
    "pre-seed analyst":             ["venture-capital", "pre-seed"],
}

# Cutshort's primary domain — used to detect off-platform ATS redirects
CUTSHORT_DOMAIN = "cutshort.io"


# ── Selectors ──────────────────────────────────────────────────────────
# CRITICAL FINDING 2026-04-28 — email/password login removed from Cutshort
# ─────────────────────────────────────────────────────────────────────────
# Live DOM inspection (headless=False, anti-detection context) confirmed that
# Cutshort's login flow no longer has an email/password form. The /login URL
# redirects to the home page. Clicking "Candidate login" opens a modal with:
#   • "Signup or login with Google"  (OAuth)
#   • "Login using phone"            (SMS OTP)
# No email/password inputs exist.
#
# IMPACT: the current login() method cannot log in automatically. It will
# always fail the _is_logged_in() check and skip the platform.
#
# RECOMMENDED WORKAROUND: persistent session approach.
#   1. Run once with headless=False (python apply.py --platform cutshort --dry-run
#      after temporarily setting HEADLESS=false).
#   2. Click "Signup or login with Google" or "Login using phone" manually.
#   3. Once logged in, close the browser. The session is saved to the
#      persistent profile at data/browser_profiles/cutshort/.
#   4. Future runs reuse the saved session and skip the login form entirely
#      (the _is_logged_in() guard will see a valid session and return True).
#
# The selectors below for SEL_LOGIN_EMAIL, SEL_LOGIN_PASSWORD, SEL_LOGIN_SUBMIT
# are kept as a no-op — the login() method tries them, times out gracefully,
# and then checks the persistent session. If the session is valid, the run
# proceeds. If not, it logs auth_failed and skips the platform for the day.

# Login page
LOGIN_URL = "https://cutshort.io/"                          # verified 2026-04-28: /login redirects to / — use home page
SEL_LOGIN_GOOGLE = "button:has-text('Signup or login with Google')"  # verified 2026-04-28: Google OAuth button in login modal
SEL_LOGIN_PHONE = "button[label='Login using phone']"       # verified 2026-04-28: phone OTP button in login modal
SEL_CANDIDATE_LOGIN_BTN = "button:has-text('Candidate login')"  # verified 2026-04-28: button on home page that opens the login modal
# Legacy email/password selectors — these fields DO NOT EXIST on Cutshort as of 2026-04-28.
# Kept so login() gracefully times out and falls through to the persistent-session check.
SEL_LOGIN_EMAIL = "input[type='email']"                     # DOES NOT EXIST — email login removed; kept as no-op
SEL_LOGIN_PASSWORD = "input[type='password']"               # DOES NOT EXIST — email login removed; kept as no-op
SEL_LOGIN_SUBMIT = "button[type='submit']"                  # DOES NOT EXIST — email login removed; kept as no-op
SEL_LOGIN_SUCCESS = "a[href*='/profile']"                   # ? needs-credentials: element only visible when logged in (profile nav link)

# Search results (tag-based)
# Cutshort search by tag: /jobs?tags[]=growth-manager&locations[]=Delhi
SEARCH_URL_TEMPLATE = (
    "https://cutshort.io/jobs"
    "?tags[]={tag}"
    "&locations[]={location}"
    "&page={page}"
)
# NOTE 2026-04-28: Cutshort uses Styled-Components with obfuscated class names
# (e.g. "sc-8c2323fe-0 hwKSIH"). The data-test attribute approach below is the
# correct strategy but needs verification against a logged-in session to confirm
# Cutshort actually uses data-test attributes on their job cards and apply flow.
SEL_JOB_CARD = "div[data-test='job-card']"                  # ? needs-credentials: each job card in search results — data-test attr unconfirmed
SEL_JOB_TITLE = "a[data-test='job-title']"                  # ? needs-credentials: job title link — unconfirmed
SEL_JOB_COMPANY = "span[data-test='company-name']"          # ? needs-credentials: company name text — unconfirmed
SEL_JOB_LOCATION = "span[data-test='job-location']"         # ? needs-credentials: location text — unconfirmed
SEL_JOB_EXPERIENCE = "span[data-test='experience']"         # ? needs-credentials: experience range text — unconfirmed
SEL_JOB_URL = "a[data-test='job-title']"                    # ? needs-credentials: same as title; read href attr — unconfirmed
SEL_JOB_SNIPPET = "p[data-test='job-description']"          # ? needs-credentials: JD snippet on card — unconfirmed
SEL_NEXT_PAGE = "a[rel='next'], button[data-test='next-page']"  # ? needs-credentials: pagination next — unconfirmed

# Job detail / apply flow
SEL_APPLY_BUTTON = "button[data-test='apply-button'], a[data-test='apply-button']"  # ? needs-credentials: Apply button — unconfirmed
SEL_ALREADY_APPLIED = "span[data-test='applied-status']"    # ? needs-credentials: "Applied" badge/text — unconfirmed
SEL_APPLY_SUCCESS = "div[data-test='application-success']"  # ? needs-credentials: success state after single-click apply — unconfirmed

# Minimal form (appears on some Cutshort jobs after clicking Apply)
SEL_APPLY_FORM = "form[data-test='apply-form'], div[data-test='apply-modal']"  # ? needs-credentials: form container — unconfirmed
SEL_FORM_INPUT_TEXT = "input[type='text']"                  # ? needs-credentials: text inputs inside the form
SEL_FORM_LABEL = "label"                                    # ? needs-credentials: field labels
SEL_FORM_TEXTAREA = "textarea"                              # ? needs-credentials: textarea (custom question — auto-Yellow)
SEL_RESUME_UPLOAD = "input[type='file']"                    # ? needs-credentials: resume file input
SEL_FORM_SUBMIT = "button[type='submit'], button[data-test='submit-application']"  # ? needs-credentials: submit button


# ── Config ─────────────────────────────────────────────────────────────

MAX_PAGES_PER_KEYWORD = 5       # CLAUDE.md §6
APPLY_DELAY = (6, 18)           # seconds between applications — Cutshort-specific
PAGE_DELAY = (30, 90)           # seconds between search result pages
SELECTOR_RETRY_DELAY = 3
MAX_SELECTOR_FAILURES = 5
NETWORK_RETRY_DELAYS = [5, 15]
MAX_SESSION_SECONDS = 90 * 60
LONG_TEXT_THRESHOLD = 100

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


class CutshortPlatform(BasePlatform):
    """Cutshort platform integration.

    Lifecycle: login → search (per keyword, tag-expanded) →
    score & apply (per job) → logout.
    Uses persistent browser profile at data/browser_profiles/cutshort/.

    Most applies are single-click: click Apply, application submitted
    immediately (no form). A minority show a minimal 1–2 field form.
    ATS redirects (Greenhouse, Lever, etc.) are detected and skipped.

    Args:
        log_path: Path to applications_log.csv.
        headless: Run browser in headless mode.
        dry_run: Score and log but never click Apply.
    """

    PLATFORM_NAME = "cutshort"

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
        self.email = os.getenv("CUTSHORT_EMAIL", "")
        self.password = os.getenv("CUTSHORT_PASSWORD", "")
        self.daily_cap = int(os.getenv("DAILY_CAP_CUTSHORT", "25"))

        # Standard answers for form filling
        self.standard_answers: dict[str, str] = {
            "name": "Varun Sah",
            "email": self.email,
            "phone": "+91-8595062552",
            "location": "Delhi NCR",
            "notice_period": "Immediate",
            "years_of_experience": "4",
            "linkedin": "https://www.linkedin.com/in/varun-sah/",
            "current_ctc": os.getenv("CUTSHORT_CURRENT_CTC", ""),
            "expected_ctc": os.getenv("CUTSHORT_EXPECTED_CTC", ""),
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
        """Log into Cutshort via persistent session (email/password no longer exists).

        As of 2026-04-28 Cutshort removed email/password login. The only
        options are Google OAuth and phone OTP — neither is automatable.
        This method ONLY checks for an existing logged-in session in the
        persistent browser profile. If none is found, it logs a clear
        error instructing the user to seed the session manually and returns
        False so the platform is skipped for the day.

        To seed the session:
            HEADLESS=false python apply.py --platform cutshort --dry-run
        Then log in via Google or phone in the browser window that opens.
        Subsequent runs (including headless) will reuse the saved cookies.

        Returns:
            True if a valid session was found in the persistent profile.
            False if no session found (user must log in manually first).
        """
        from playwright.async_api import async_playwright
        self._playwright = await async_playwright().start()
        self._context = await create_browser_context(
            platform=self.PLATFORM_NAME,
            headless=self.headless,
            playwright=self._playwright,
        )
        self._page = await self._context.new_page()

        await self._page.goto(LOGIN_URL, wait_until="domcontentloaded")
        await asyncio.sleep(2)

        if await self._is_logged_in():
            logger.info("Cutshort: existing session found — proceeding")
            return True

        logger.error(
            "Cutshort: no active session found. "
            "Email/password login was removed from Cutshort (2026-04-28). "
            "To seed the session: set HEADLESS=false and run "
            "'python apply.py --platform cutshort --dry-run', "
            "then log in via Google or phone in the browser window."
        )
        self._log_error("auth_failed: no persistent session — seed required (see logs)")
        return False

    @staticmethod
    def _tags_for_keyword(keyword: str) -> list[str]:
        """Return Cutshort tag slugs for a given keyword string.

        Looks up the keyword in KEYWORD_TAG_MAP (case-insensitive).
        Falls back to slugifying the keyword directly if no mapping found.
        This fallback is intentionally conservative: an unrecognised keyword
        produces one tag (the keyword itself with spaces → hyphens) rather
        than zero tags, so new keywords don't silently produce no results.

        Args:
            keyword: Raw keyword string, e.g. "growth manager".

        Returns:
            List of tag slugs to try for this keyword, e.g. ["growth-manager", "growth-hacking"].
        """
        tags = KEYWORD_TAG_MAP.get(keyword.lower())
        if tags:
            return tags
        # Fallback: slugify the keyword
        slug = keyword.lower().strip().replace(" ", "-")
        logger.debug("No tag mapping for keyword '%s' — using slug '%s' as fallback", keyword, slug)
        return [slug]

    async def search(self, keyword: str, filters: dict) -> AsyncIterator[Job]:
        """Search Cutshort for jobs matching a keyword via its tag mapping.

        Expands the keyword to one or more Cutshort tags using
        KEYWORD_TAG_MAP, then runs a paginated search per tag. Deduplication
        in _process_job prevents double-processing a job that appears under
        multiple tags.

        Args:
            keyword: Search query from CLAUDE.md §4, e.g. "growth manager".
            filters: Dict with 'locations' (list[str]), 'experience_min' (int).

        Yields:
            Job objects parsed from search result cards.
        """
        if self._page is None:
            logger.error("search() called before login()")
            return

        location = filters.get("locations", [""])[0] if filters.get("locations") else ""
        tags = self._tags_for_keyword(keyword)

        for tag in tags:
            if self._should_stop():
                return

            for page_num in range(1, MAX_PAGES_PER_KEYWORD + 1):
                if self._should_stop():
                    return

                url = SEARCH_URL_TEMPLATE.format(
                    tag=tag.replace(" ", "+"),
                    location=location.replace(" ", "+"),
                    page=page_num,
                )
                logger.info("[cutshort] Tag '%s' page %d for '%s': %s", tag, page_num, keyword, url)

                try:
                    await self._page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                except PlaywrightTimeout:
                    logger.warning("Search page %d timed out for tag '%s'", page_num, tag)
                    retried = await self._retry_navigation(url)
                    if not retried:
                        break

                job_cards = await self._page.query_selector_all(SEL_JOB_CARD)
                if not job_cards:
                    logger.info("No job cards on page %d for tag '%s' — end of results", page_num, tag)
                    break

                for card in job_cards:
                    if self._should_stop():
                        return
                    job = await self._parse_job_card(card)
                    if job is not None:
                        yield job

                next_button = await self._page.query_selector(SEL_NEXT_PAGE)
                if not next_button:
                    logger.info("No next page for tag '%s' — end of results", tag)
                    break

                delay = random.uniform(*PAGE_DELAY)
                logger.debug("Waiting %.1fs before next page", delay)
                await asyncio.sleep(delay)

    async def open_application_form(self, job: Job) -> dict:
        """Navigate to job page, detect the apply type, and return a descriptor.

        Cutshort apply types:
        - "off_platform_redirect": Apply button links to an external ATS, or
          clicking it navigates away from cutshort.io. Log and skip.
        - "already_applied": Job shows an "Applied" badge. Skip.
        - "no_apply_button": No apply button found. Skip.
        - "direct_apply": Clicking Apply submits immediately (no form appears).
          The most common path.
        - "form_apply": A minimal form appears after clicking Apply.
          Check for custom/essay fields.
        - "unknown": Unclear state after clicking.

        Returns a dict:
            {
                "path": str,
                "has_unrecognized": bool,
                "questions": list[dict],   # label + type per field (form_apply only)
            }
        """
        if self._page is None:
            raise RuntimeError("open_application_form() called before login()")

        job_url = job.posted_date
        await self._page.goto(job_url, wait_until="domcontentloaded", timeout=30_000)

        # Check if already applied
        already = await self._page.query_selector(SEL_ALREADY_APPLIED)
        if already:
            text = (await already.inner_text()).strip().lower()
            if "applied" in text:
                return {"path": "already_applied", "has_unrecognized": False, "questions": []}

        # Find the Apply button
        apply_btn = await self._page.query_selector(SEL_APPLY_BUTTON)
        if not apply_btn:
            return {"path": "no_apply_button", "has_unrecognized": False, "questions": []}

        # Check if the Apply button is an anchor pointing off-platform
        # (detectable before clicking — no browser navigation needed)
        btn_href = await apply_btn.get_attribute("href")
        if btn_href and self._is_off_platform_url(btn_href):
            return {"path": "off_platform_redirect", "has_unrecognized": False, "questions": []}

        # Record the current URL before clicking, to detect post-click redirects
        url_before = self._page.url

        # Click Apply
        try:
            await apply_btn.click(timeout=10_000)
        except PlaywrightTimeout:
            await asyncio.sleep(SELECTOR_RETRY_DELAY)
            try:
                await apply_btn.click(timeout=10_000)
            except PlaywrightTimeout:
                self._selector_failures += 1
                raise

        # Brief wait for either: redirect, form, or success state
        await asyncio.sleep(1.5)

        # Check if the page navigated away from cutshort.io (ATS redirect)
        if self._is_off_platform_url(self._page.url):
            return {"path": "off_platform_redirect", "has_unrecognized": False, "questions": []}

        # Check for immediate success (direct apply — no form)
        success_el = await self._page.query_selector(SEL_APPLY_SUCCESS)
        if success_el:
            return {"path": "direct_apply", "has_unrecognized": False, "questions": []}

        # Check for a form that appeared after clicking
        form_el = await self._page.query_selector(SEL_APPLY_FORM)
        if form_el:
            questions, has_unrecognized = await self._parse_apply_form()
            return {"path": "form_apply", "has_unrecognized": has_unrecognized, "questions": questions}

        # Unclear state — could be a slow form load; wait a bit more and retry
        try:
            await self._page.wait_for_selector(
                f"{SEL_APPLY_SUCCESS}, {SEL_APPLY_FORM}",
                timeout=5_000,
            )
        except PlaywrightTimeout:
            # Still nothing — unknown state
            return {"path": "unknown", "has_unrecognized": False, "questions": []}

        # Re-check after the extra wait
        if await self._page.query_selector(SEL_APPLY_SUCCESS):
            return {"path": "direct_apply", "has_unrecognized": False, "questions": []}
        if await self._page.query_selector(SEL_APPLY_FORM):
            questions, has_unrecognized = await self._parse_apply_form()
            return {"path": "form_apply", "has_unrecognized": has_unrecognized, "questions": questions}

        return {"path": "unknown", "has_unrecognized": False, "questions": []}

    def _is_off_platform_url(self, url: str) -> bool:
        """Return True if a URL points outside of cutshort.io.

        Used to detect both pre-click (href on the Apply anchor) and
        post-click (page.url after navigation) ATS redirects.
        """
        if not url or url.startswith("#") or url.startswith("/"):
            return False  # relative URL — stays on Cutshort
        try:
            host = urlparse(url).netloc.lower()
            return CUTSHORT_DOMAIN not in host
        except Exception as exc:
            logger.debug("URL parse error checking for ATS redirect: %s", exc)
            return False

    async def _parse_apply_form(self) -> tuple[list[dict], bool]:
        """Parse fields in the apply form/modal.

        Returns:
            (questions, has_unrecognized) where questions is a list of
            {"label": str, "type": str} dicts and has_unrecognized is True
            if any field is an essay or unrecognised question.
        """
        questions: list[dict] = []
        has_unrecognized = False

        # Text inputs
        text_inputs = await self._page.query_selector_all(
            f"{SEL_APPLY_FORM} {SEL_FORM_INPUT_TEXT}"
        )
        for inp in text_inputs:
            label = await self._get_field_label(inp)
            if self._is_essay_question(label):
                has_unrecognized = True
                questions.append({"label": label, "type": "text_essay"})
            elif not self._is_recognized_question(label):
                has_unrecognized = True
                questions.append({"label": label, "type": "text_unknown"})
            else:
                questions.append({"label": label, "type": "text"})

        # Textareas — always unrecognised (Cutshort rarely uses these for standard fields)
        textareas = await self._page.query_selector_all(
            f"{SEL_APPLY_FORM} {SEL_FORM_TEXTAREA}"
        )
        for ta in textareas:
            label = await self._get_field_label(ta)
            has_unrecognized = True
            questions.append({"label": label, "type": "textarea"})

        return questions, has_unrecognized

    async def _get_field_label(self, element) -> str:
        """Extract label text for a form field element."""
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
        """Fill the Cutshort apply form and submit (form_apply path only).

        For direct_apply, the application was already submitted by clicking
        the Apply button — this method is not called.

        Returns:
            {"status": "applied"|"applied_unconfirmed"|"error"|"skipped", "notes": str}
        """
        if self._page is None:
            raise RuntimeError("fill_and_submit() called before login()")

        path = form["path"]

        if path in ("off_platform_redirect", "already_applied", "no_apply_button"):
            return {"status": "skipped", "notes": path.replace("_", " ")}
        if path == "direct_apply":
            # Already submitted — caller shouldn't reach here, but handle gracefully
            return {"status": "applied", "notes": "direct apply (no form)"}
        if path != "form_apply":
            return {"status": "error", "notes": f"unknown form path: {path}"}

        # Upload resume if file input is present
        resume_path = Path("Varun_Sah_CV.pdf")
        if resume_path.exists():
            file_inputs = await self._page.query_selector_all(
                f"{SEL_APPLY_FORM} {SEL_RESUME_UPLOAD}"
            )
            for fi in file_inputs:
                try:
                    await fi.set_input_files(str(resume_path))
                except Exception as exc:
                    logger.debug("Resume upload skipped or failed: %s", exc)

        # Fill empty text inputs with standard answers
        text_inputs = await self._page.query_selector_all(
            f"{SEL_APPLY_FORM} {SEL_FORM_INPUT_TEXT}"
        )
        for inp in text_inputs:
            value = await inp.get_attribute("value") or ""
            if value.strip():
                continue  # pre-filled — don't touch
            label = await self._get_field_label(inp)
            answer = self._match_standard_answer(label)
            if answer:
                inp_id = await inp.get_attribute("id")
                selector = f"#{inp_id}" if inp_id else SEL_FORM_INPUT_TEXT
                try:
                    await human_type(self._page, selector, answer)
                except Exception as exc:
                    logger.debug("Failed to fill field '%s': %s", label, exc)

        # Fill textareas from human-reviewed custom answers (review-queue path only).
        # SEL_FORM_TEXTAREA is TODO-marked — verify against a live form with a textarea.
        custom_answers = answers.get("_custom", {})
        if custom_answers:
            textareas = await self._page.query_selector_all(
                f"{SEL_APPLY_FORM} {SEL_FORM_TEXTAREA}"
            )
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
            submit_btn = await self._page.wait_for_selector(SEL_FORM_SUBMIT, timeout=5_000)
            await submit_btn.click(timeout=10_000)
        except PlaywrightTimeout:
            self._selector_failures += 1
            return {"status": "error", "notes": "selector_broken: submit button"}

        # Wait for success state
        try:
            await self._page.wait_for_selector(SEL_APPLY_SUCCESS, timeout=10_000)
            return {"status": "applied", "notes": ""}
        except PlaywrightTimeout:
            return {"status": "applied_unconfirmed", "notes": "no success indicator after form submit"}

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

    async def logout(self) -> None:
        """Close browser context. Persistent profile retains the session."""
        logger.info("Cutshort: closing browser context")
        if self._context is not None:
            await self._context.close()
        if self._playwright is not None:
            await self._playwright.stop()

    # ── Orchestration ──────────────────────────────────────────────────

    async def run(self, keywords: list[str], filters: dict) -> dict[str, int]:
        """Full run: login → search each keyword (tag-expanded) → process each job → logout.

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

                logger.info("[cutshort] Searching: %s", keyword)
                async for job in self.search(keyword, filters):
                    if self._should_stop():
                        break
                    await self._process_job(job)
        finally:
            await self.logout()

        logger.info("[cutshort] Run complete — %s", self._stats)
        return self._stats

    async def _process_job(self, job: Job) -> None:
        """Per-job flow: dedupe → hard-skip → score → threshold → cap → apply."""
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
            self._record(job, fit_score, "skipped", f"cap reached: cutshort {current}/{self.daily_cap}")
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

        # 8. Delay — Cutshort-specific: 6-18s
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

        if path == "off_platform_redirect":
            self._record(job, fit_score, "skipped", "off_platform_redirect")
            return

        if path == "already_applied":
            self._record(job, fit_score, "skipped", "already applied on Cutshort")
            return

        if path == "no_apply_button":
            self._record(job, fit_score, "skipped", "no apply button found")
            return

        if path == "unknown":
            self._record(job, fit_score, "error", "unknown application state")
            return

        # Direct apply — application already submitted by clicking the button
        if path == "direct_apply":
            self._record(job, fit_score, "applied", "direct apply (single-click)")
            return

        # Form appeared — check for custom/essay questions → Yellow
        if path == "form_apply" and form["has_unrecognized"]:
            unrecognized = [
                q for q in form["questions"]
                if q["type"] in ("textarea", "text_essay", "text_unknown")
            ]
            self._queue_for_review(
                job, fit_score, tier, "custom_questions",
                custom_questions=[
                    {"question": q.get("label", ""), "suggested_answer": ""}
                    for q in unrecognized
                ],
            )
            return

        # Form appeared with only standard fields — fill and submit
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
        """Extract a Job from a search result card element."""
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
                url = f"https://cutshort.io{url}"

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
            logger.warning("STOP file detected — stopping cutshort")
            return True
        if self._session_start and (time.monotonic() - self._session_start) > MAX_SESSION_SECONDS:
            logger.warning("Session exceeded %ds — stopping cutshort", MAX_SESSION_SECONDS)
            return True
        if self._selector_failures >= MAX_SELECTOR_FAILURES:
            logger.error("Too many selector failures (%d) — stopping cutshort", self._selector_failures)
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
