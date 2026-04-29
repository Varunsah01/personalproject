"""LinkedIn Easy Apply platform integration.

Build step 5. Best quality Indian board, but stricter rate limits.
Daily cap: 40 (configurable via DAILY_CAP_LINKEDIN in .env).

Easy Apply only — "Apply on company website" redirects are skipped.
Multi-step modal flow: the bot walks each step, checks for essay
questions or unrecognised fields, and demotes to Yellow if found.

Selectors live in platforms/selectors/linkedin.yaml (guidelines.md §4.4).
Edit the YAML to fix broken selectors; call self.sel.reload() to hot-patch.
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

from core.browser import create_browser_context, human_click, human_type, is_bot_challenged
from core.logger import LogEntry, count_today, init_log, is_duplicate, log_application
from core.scorer import Job, classify_tier, score_job, should_apply
from core.selectors import SelectorStore
from core.types import BotConfig
from platforms.base import BasePlatform

logger = logging.getLogger(__name__)


# ── URLs ──────────────────────────────────────────────────────────────
# Selectors have moved to platforms/selectors/linkedin.yaml.

LOGIN_URL = "https://www.linkedin.com/login"
SEARCH_URL_TEMPLATE = (
    "https://www.linkedin.com/jobs/search/"
    "?keywords={keyword}"
    "&location={location}"
    "&f_AL=true"            # Easy Apply filter
    "&f_E=2%2C3%2C4"        # Experience level: entry, associate, mid-senior
    "&f_TPR=r604800"         # Time posted: past week
    "&sortBy=DD"             # Sort by date
    "&start={offset}"        # Pagination offset (0, 25, 50, …)
)


# ── Config ────────────────────────────────────────────────────────────

MAX_PAGES_PER_KEYWORD = 5         # CLAUDE.md §6
JOBS_PER_PAGE = 25                # LinkedIn shows 25 results per page
APPLY_DELAY = (10, 25)            # seconds between applications — LinkedIn-specific
JOB_VIEW_DELAY = (8, 15)         # min 8s between job-detail views
PAGE_DELAY = (30, 90)             # seconds between search result pages (guidelines.md §3.2)
SELECTOR_RETRY_DELAY = 3          # seconds before retrying a missing selector (guidelines.md §3.4)
MAX_SELECTOR_FAILURES = 5         # per session before stopping platform (guidelines.md §3.4)
NETWORK_RETRY_DELAYS = [5, 15]    # exponential backoff for network errors (guidelines.md §3.4)
MAX_SESSION_SECONDS = 60 * 60     # 60 minutes per platform — stricter than the default 90
LONG_TEXT_THRESHOLD = 100         # chars — fields longer than this demote to Yellow (guidelines.md §3.2)
MAX_MODAL_STEPS = 10              # safety valve: bail out if modal has more steps than this

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

# Essay / long-answer patterns that auto-demote to Yellow.
_ESSAY_DEMOTE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"why\s+(?:are\s+you\s+interested|do\s+you\s+want)", re.IGNORECASE),
    re.compile(r"tell\s+us\s+about\s+yourself", re.IGNORECASE),
    re.compile(r"describe\s+a\s+time", re.IGNORECASE),
    re.compile(r"cover\s+letter", re.IGNORECASE),
    re.compile(r"what\s+makes\s+you\s+a\s+good\s+fit", re.IGNORECASE),
    re.compile(r"why\s+should\s+we\s+hire", re.IGNORECASE),
    re.compile(r"additional\s+information", re.IGNORECASE),
]


class LinkedInPlatform(BasePlatform):
    """LinkedIn Easy Apply integration.

    Lifecycle: login → search (per keyword) → score & apply (per job) → logout.
    Uses persistent browser profile at data/browser_profiles/linkedin/.

    Only processes Easy Apply jobs. "Apply on company website" buttons are
    skipped. Multi-step Easy Apply modals are walked step-by-step; any
    unrecognised or essay-type question demotes the application to Yellow.

    Args:
        config: BotConfig with shared settings (log path, caps, headless, etc.).
    """

    PLATFORM_NAME = "linkedin"

    def __init__(self, config: BotConfig) -> None:
        super().__init__(config)

        # Platform-specific credentials (from .env)
        self.email = os.getenv("LINKEDIN_EMAIL", "")
        self.password = os.getenv("LINKEDIN_PASSWORD", "")

        # Selector registry — backed by platforms/selectors/linkedin.yaml
        self.sel = SelectorStore("linkedin")

        # Augment shared standard_answers with LinkedIn-specific fields
        self.standard_answers.update({
            "email": self.email,
            "linkedin": "https://www.linkedin.com/in/varun-sah/",
            "current_ctc": os.getenv("LINKEDIN_CURRENT_CTC", ""),
            "expected_ctc": os.getenv("LINKEDIN_EXPECTED_CTC", ""),
        })

        # LinkedIn-specific state
        self._human_check_detected = False

    # ── BasePlatform interface ─────────────────────────────────────────

    async def login(self) -> bool:
        """Log into LinkedIn using .env credentials.

        Uses persistent browser profile so subsequent runs may already
        be logged in (cookie-based session).

        Returns:
            True if login succeeded, False otherwise.
            On human-verification challenge, logs auth_challenge and returns False.
        """
        if not self.email or not self.password:
            logger.error("LINKEDIN_EMAIL or LINKEDIN_PASSWORD not set in .env")
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

        # Check for human verification challenge before attempting login
        if await self._detect_human_check():
            return False

        # Fill credentials — human_type for both fields, human_click for submit
        try:
            await human_type(self._page, self.sel.login_email, self.email)
            await human_type(self._page, self.sel.login_password, self.password)
            await human_click(self._page, self.sel.login_submit)
            await self._page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            logger.error("Login form interaction timed out")
            self._log_error("auth_failed: timeout")
            return False

        # Wait up to 8s for feed indicator OR checkpoint page
        try:
            await self._page.wait_for_selector(
                f"{self.sel.login_success}, {self.sel.human_check}",
                timeout=8_000,
            )
        except PlaywrightTimeout:
            pass  # fall through to explicit checks below

        # Post-login: check for bot challenge via core.browser
        if await is_bot_challenged(self._page):
            await self._handle_bot_detection()
            self._human_check_detected = True
            self._log_error("auth_challenge: bot_detection")
            return False

        # Post-login human verification check
        if await self._detect_human_check():
            return False

        if not await self._is_logged_in():
            logger.error("Login failed — success indicator not found")
            self._log_error("auth_failed: credentials rejected or unknown error")
            return False

        logger.info("LinkedIn login successful")
        return True

    async def search(self, keyword: str, filters: dict) -> AsyncIterator[Job]:
        """Search LinkedIn for Easy Apply jobs matching keyword and filters.

        Paginates up to MAX_PAGES_PER_KEYWORD pages (25 results each).
        Inserts PAGE_DELAY between pages. Only returns jobs with Easy Apply.

        Args:
            keyword: Search query, e.g. "growth manager".
            filters: Dict with 'locations' (list[str]).

        Yields:
            Job objects parsed from search result cards.
        """
        if self._page is None:
            logger.error("search() called before login()")
            return

        location = "India"  # LinkedIn Easy Apply — broad India scope

        for page_num in range(MAX_PAGES_PER_KEYWORD):
            if self._should_stop():
                return

            offset = page_num * JOBS_PER_PAGE
            url = SEARCH_URL_TEMPLATE.format(
                keyword=keyword.replace(" ", "%20"),
                location=location.replace(" ", "%20"),
                offset=offset,
            )
            logger.info("[linkedin] Page %d for '%s': %s", page_num + 1, keyword, url)

            try:
                await self._page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            except PlaywrightTimeout:
                logger.warning("Search page %d timed out for '%s'", page_num + 1, keyword)
                retried = await self._retry_navigation(url)
                if not retried:
                    break

            # Check for human verification mid-session
            if await self._detect_human_check():
                return

            # Wait for job cards to render
            try:
                await self._page.wait_for_selector(self.sel.job_card, timeout=10_000)
            except PlaywrightTimeout:
                logger.info("No job cards found on page %d — end of results", page_num + 1)
                break

            # Parse job cards from this page
            job_cards = await self._page.query_selector_all(self.sel.job_card)
            if not job_cards:
                logger.info("No job cards found on page %d — end of results", page_num + 1)
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
        """Navigate to job page, click Easy Apply, and inspect the modal.

        LinkedIn Easy Apply has three possible states:
        - External apply: "Apply" button links to company site → skip
        - Already applied: badge shows previous application → skip
        - Easy Apply: multi-step modal with form fields

        For Easy Apply, walks through each modal step to catalogue all
        questions before filling anything. Essay questions auto-demote
        to Yellow.

        Returns a dict:
            {
                "path": "easy_apply"|"external_apply"|"already_applied"|"no_apply_button"|"unknown",
                "steps": list[list[dict]],
                "questions": list[dict],
                "has_unrecognized": bool,
                "pre_filled_count": int,
            }
        """
        if self._page is None:
            raise RuntimeError("open_application_form() called before login()")

        job_url = job.posted_date  # URL stashed in posted_date

        # Min 8s between job-detail views
        await asyncio.sleep(random.uniform(*JOB_VIEW_DELAY))

        await self._page.goto(job_url, wait_until="domcontentloaded", timeout=30_000)

        # Check for human verification
        if await self._detect_human_check():
            return {"path": "unknown", "steps": [], "questions": [], "has_unrecognized": False, "pre_filled_count": 0}

        # Check if already applied
        already_applied = await self._page.query_selector(self.sel.already_applied)
        if already_applied:
            text = (await already_applied.inner_text()).strip().lower()
            if "applied" in text:
                return {"path": "already_applied", "steps": [], "questions": [], "has_unrecognized": False, "pre_filled_count": 0}

        # Check for Easy Apply button vs external apply
        easy_apply_btn = await self._page.query_selector(self.sel.easy_apply_button)
        external_btn = await self._page.query_selector(self.sel.external_apply_button)

        if external_btn and not easy_apply_btn:
            return {"path": "external_apply", "steps": [], "questions": [], "has_unrecognized": False, "pre_filled_count": 0}

        if not easy_apply_btn:
            return {"path": "no_apply_button", "steps": [], "questions": [], "has_unrecognized": False, "pre_filled_count": 0}

        # Click Easy Apply — modal should appear
        try:
            await human_click(self._page, self.sel.easy_apply_button)
        except PlaywrightTimeout:
            await asyncio.sleep(SELECTOR_RETRY_DELAY)
            try:
                await human_click(self._page, self.sel.easy_apply_button)
            except PlaywrightTimeout:
                self._selector_failures += 1
                raise

        # Wait for the modal to appear
        try:
            await self._page.wait_for_selector(self.sel.modal_container, timeout=10_000)
        except PlaywrightTimeout:
            return {"path": "unknown", "steps": [], "questions": [], "has_unrecognized": False, "pre_filled_count": 0}

        # Walk through modal steps to catalogue all questions
        all_steps: list[list[dict]] = []
        all_questions: list[dict] = []
        has_unrecognized = False
        pre_filled_count = 0

        for step_num in range(MAX_MODAL_STEPS):
            step_fields = await self._parse_modal_step()
            all_steps.append(step_fields)

            for field in step_fields:
                all_questions.append(field)
                if field.get("value"):
                    pre_filled_count += 1
                if self._is_essay_question(field.get("label", "")):
                    has_unrecognized = True
                elif field["type"] == "textarea":
                    has_unrecognized = True
                elif not self._is_recognized_question(field.get("label", "")):
                    if field["type"] != "file":
                        has_unrecognized = True

            # Check if there's a Next button (more steps) or Review/Submit (last step)
            next_btn = await self._page.query_selector(self.sel.modal_next_button)
            review_btn = await self._page.query_selector(self.sel.modal_review_button)
            submit_btn = await self._page.query_selector(self.sel.modal_submit_button)

            if next_btn:
                try:
                    await next_btn.click(timeout=5_000)
                    await asyncio.sleep(1)
                except PlaywrightTimeout:
                    logger.warning("Failed to advance to next modal step")
                    break
            elif review_btn or submit_btn:
                break
            else:
                break

        return {
            "path": "easy_apply",
            "steps": all_steps,
            "questions": all_questions,
            "has_unrecognized": has_unrecognized,
            "pre_filled_count": pre_filled_count,
        }

    async def _parse_modal_step(self) -> list[dict]:
        """Parse all form fields visible in the current Easy Apply modal step.

        Returns:
            List of {"label": str, "type": "text"|"select"|"radio"|"textarea"|"file",
                      "value": str|None, "options": list[str]}
        """
        fields: list[dict] = []

        # Text inputs
        text_inputs = await self._page.query_selector_all(
            f"{self.sel.modal_container} {self.sel.modal_input_text}"
        )
        for inp in text_inputs:
            label = await self._get_field_label(inp)
            value = await inp.get_attribute("value") or ""
            fields.append({"label": label, "type": "text", "value": value, "options": []})

        # Select dropdowns
        selects = await self._page.query_selector_all(
            f"{self.sel.modal_container} {self.sel.modal_input_select}"
        )
        for sel_el in selects:
            label = await self._get_field_label(sel_el)
            selected = await sel_el.evaluate("el => el.options[el.selectedIndex]?.text || ''")
            options = await sel_el.evaluate(
                "el => Array.from(el.options).map(o => o.text).filter(t => t.trim())"
            )
            fields.append({"label": label, "type": "select", "value": selected, "options": options})

        # Radio button groups
        radios = await self._page.query_selector_all(
            f"{self.sel.modal_container} {self.sel.modal_input_radio}"
        )
        radio_groups: dict[str, list] = {}
        for radio in radios:
            name = await radio.get_attribute("name") or "unknown"
            if name not in radio_groups:
                radio_groups[name] = []
            label_el = await radio.evaluate_handle(
                "el => el.closest('label') || el.parentElement.querySelector('label')"
            )
            label_text = ""
            if label_el:
                try:
                    label_text = (await label_el.inner_text()).strip()
                except Exception as exc:
                    logger.debug("Could not read radio label text: %s", exc)
            checked = await radio.is_checked()
            radio_groups[name].append({"text": label_text, "checked": checked})

        for group_name, options in radio_groups.items():
            group_label = await self._get_radio_group_label(group_name)
            selected = next((o["text"] for o in options if o["checked"]), None)
            option_texts = [o["text"] for o in options if o["text"]]
            fields.append({
                "label": group_label,
                "type": "radio",
                "value": selected,
                "options": option_texts,
            })

        # Textareas (essay questions — danger zone)
        textareas = await self._page.query_selector_all(
            f"{self.sel.modal_container} {self.sel.modal_textarea}"
        )
        for ta in textareas:
            label = await self._get_field_label(ta)
            value = await ta.input_value()
            fields.append({"label": label, "type": "textarea", "value": value, "options": []})

        # File upload inputs
        file_inputs = await self._page.query_selector_all(
            f"{self.sel.modal_container} {self.sel.resume_upload}"
        )
        for fi in file_inputs:
            label = await self._get_field_label(fi)
            fields.append({"label": label or "resume", "type": "file", "value": None, "options": []})

        return fields

    async def _get_field_label(self, element) -> str:
        """Extract the label text for a form field element.

        Tries: aria-label → associated <label> via id → closest label ancestor
        → question text span.
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
                const container = el.closest('.fb-form-element, .jobs-easy-apply-form-section__grouping');
                if (container) {
                    const label = container.querySelector('label, span.fb-form-element-label');
                    if (label) return label.textContent.trim();
                }
                return '';
            }""")
            if label_text:
                return label_text
        except Exception as exc:
            logger.debug("JS label extraction failed: %s", exc)

        return ""

    async def _get_radio_group_label(self, group_name: str) -> str:
        """Get the label for a radio button group by its name attribute."""
        try:
            label_text = await self._page.evaluate(f"""() => {{
                const fieldset = document.querySelector('fieldset:has(input[name="{group_name}"])');
                if (fieldset) {{
                    const legend = fieldset.querySelector('legend');
                    if (legend) return legend.textContent.trim();
                }}
                return '{group_name}';
            }}""")
            return label_text
        except Exception as exc:
            logger.debug("JS radio group label extraction failed for '%s': %s", group_name, exc)
            return group_name

    def _is_essay_question(self, label: str) -> bool:
        """Check if a question label matches essay/long-answer patterns.

        These always demote to Yellow regardless of other factors.
        """
        for pattern in _ESSAY_DEMOTE_PATTERNS:
            if pattern.search(label):
                return True
        return False

    def _is_recognized_question(self, question_text: str) -> bool:
        """Check if a form field label maps to a known standard field."""
        q_lower = question_text.lower()
        for field_name in STANDARD_FIELD_NAMES:
            if field_name in q_lower:
                return True
        return False

    async def fill_and_submit(self, form: dict, answers: dict) -> dict:
        """Fill Easy Apply modal fields and submit.

        Handles multiple paths:
        - "external_apply" / "already_applied" / "no_apply_button": return skip status.
        - "easy_apply": walk modal steps, fill empty standard fields (don't
          re-fill pre-populated ones), upload resume, click through to Submit.

        Returns:
            {"status": "applied"|"applied_unconfirmed"|"error"|"skipped", "notes": str}
        """
        if self._page is None:
            raise RuntimeError("fill_and_submit() called before login()")

        path = form["path"]

        if path == "external_apply":
            return {"status": "skipped", "notes": "external apply (company website)"}
        if path == "already_applied":
            return {"status": "skipped", "notes": "already applied"}
        if path == "no_apply_button":
            return {"status": "skipped", "notes": "no apply button found"}
        if path != "easy_apply":
            return {"status": "error", "notes": f"unknown form path: {path}"}

        # The modal was walked during open_application_form() for inspection.
        # Close and re-open to fill from step 1.
        await self._close_modal()
        await asyncio.sleep(1)

        # Re-click Easy Apply to start fresh
        easy_apply_btn = await self._page.query_selector(self.sel.easy_apply_button)
        if not easy_apply_btn:
            return {"status": "error", "notes": "Easy Apply button not found on re-open"}

        try:
            await human_click(self._page, self.sel.easy_apply_button)
            await self._page.wait_for_selector(self.sel.modal_container, timeout=10_000)
        except PlaywrightTimeout:
            return {"status": "error", "notes": "modal did not reappear on re-click"}

        # Walk each step: fill empty fields, skip pre-filled, advance
        for step_num in range(MAX_MODAL_STEPS):
            await self._fill_modal_step(answers)

            submit_btn = await self._page.query_selector(self.sel.modal_submit_button)
            review_btn = await self._page.query_selector(self.sel.modal_review_button)
            next_btn = await self._page.query_selector(self.sel.modal_next_button)

            if submit_btn:
                try:
                    await submit_btn.click(timeout=10_000)
                except PlaywrightTimeout:
                    self._selector_failures += 1
                    return {"status": "error", "notes": "selector_broken: submit button"}
                break
            elif review_btn:
                try:
                    await review_btn.click(timeout=10_000)
                    await asyncio.sleep(1)
                except PlaywrightTimeout:
                    self._selector_failures += 1
                    return {"status": "error", "notes": "selector_broken: review button"}
                # After review, submit button should appear
                try:
                    submit_btn = await self._page.wait_for_selector(
                        self.sel.modal_submit_button, timeout=5_000
                    )
                    await submit_btn.click(timeout=10_000)
                except PlaywrightTimeout:
                    return {"status": "error", "notes": "submit button not found after review"}
                break
            elif next_btn:
                try:
                    await next_btn.click(timeout=5_000)
                    await asyncio.sleep(1)
                except PlaywrightTimeout:
                    return {"status": "error", "notes": f"failed to advance past step {step_num + 1}"}

                # Check for validation errors after advancing
                error_el = await self._page.query_selector(self.sel.modal_error)
                if error_el:
                    error_text = (await error_el.inner_text()).strip()
                    return {"status": "error", "notes": f"validation error at step {step_num + 1}: {error_text}"}
            else:
                return {"status": "error", "notes": f"no navigation button found at step {step_num + 1}"}

        # Wait for success confirmation
        try:
            await self._page.wait_for_selector(self.sel.apply_success_toast, timeout=10_000)
            return {"status": "applied", "notes": ""}
        except PlaywrightTimeout:
            return {"status": "applied_unconfirmed", "notes": "no success toast after submit"}

    async def _fill_modal_step(self, answers: dict) -> None:
        """Fill form fields in the current modal step.

        Pre-filled fields are left alone (LinkedIn "Apply with profile"
        pre-populates most standard fields). Only empty recognised fields
        are filled from the answers dict.
        """
        # Fill empty text inputs
        text_inputs = await self._page.query_selector_all(
            f"{self.sel.modal_container} {self.sel.modal_input_text}"
        )
        for inp in text_inputs:
            value = await inp.get_attribute("value") or ""
            if value.strip():
                continue  # pre-filled — don't touch
            label = await self._get_field_label(inp)
            answer = self._match_standard_answer(label)
            if answer:
                el_id = await inp.get_attribute("id")
                selector = f"#{el_id}" if el_id else f"{self.sel.modal_container} {self.sel.modal_input_text}"
                await human_type(self._page, selector, answer)

        # Fill empty selects
        selects = await self._page.query_selector_all(
            f"{self.sel.modal_container} {self.sel.modal_input_select}"
        )
        for sel_el in selects:
            selected_idx = await sel_el.evaluate("el => el.selectedIndex")
            if selected_idx > 0:
                continue  # already has a non-default selection
            label = await self._get_field_label(sel_el)
            options = await sel_el.evaluate(
                "el => Array.from(el.options).map((o, i) => ({value: o.value, text: o.text, index: i}))"
            )
            best_idx = self._pick_select_option(label, options)
            if best_idx is not None:
                await sel_el.select_option(index=best_idx)

        # Upload resume if file input is present and empty
        file_inputs = await self._page.query_selector_all(
            f"{self.sel.modal_container} {self.sel.resume_upload}"
        )
        resume_path = Path("Varun_Sah_CV.pdf")
        if resume_path.exists():
            for fi in file_inputs:
                try:
                    await fi.set_input_files(str(resume_path))
                except Exception as exc:
                    logger.debug("Resume upload skipped or failed: %s", exc)

        # Fill textareas from human-reviewed custom answers (review-queue path only)
        custom_answers = answers.get("_custom", {})
        if custom_answers:
            textareas = await self._page.query_selector_all(
                f"{self.sel.modal_container} {self.sel.modal_textarea}"
            )
            for ta in textareas:
                current = await ta.input_value()
                if current.strip():
                    continue  # pre-filled
                label = await self._get_field_label(ta)
                answer = custom_answers.get(label.lower())
                if answer:
                    try:
                        await ta.click()
                        await ta.type(answer, delay=80)
                    except Exception as exc:
                        logger.debug("Failed to fill textarea '%s': %s", label, exc)

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

    def _pick_select_option(self, label: str, options: list[dict]) -> int | None:
        """Pick the best <select> option index for a given field label.

        Returns the option index to select, or None if can't determine.
        """
        label_lower = label.lower()

        if any(kw in label_lower for kw in ("city", "location", "country")):
            for opt in options:
                opt_lower = opt["text"].lower()
                if any(loc in opt_lower for loc in ["delhi", "india", "ncr", "remote"]):
                    return opt["index"]

        if any(kw in label_lower for kw in ("experience", "years")):
            for opt in options:
                if any(n in opt["text"] for n in ["3", "4", "2"]):
                    return opt["index"]

        if "notice" in label_lower:
            for opt in options:
                opt_lower = opt["text"].lower()
                if any(kw in opt_lower for kw in ["immediate", "15 days", "serving"]):
                    return opt["index"]

        return None

    async def _close_modal(self) -> None:
        """Close the Easy Apply modal without submitting.

        Clicks the X button, then handles the "Discard application?" confirmation.
        """
        try:
            close_btn = await self._page.query_selector(self.sel.modal_close_button)
            if close_btn:
                await close_btn.click(timeout=5_000)
                await asyncio.sleep(0.5)
                discard_btn = await self._page.query_selector(self.sel.modal_discard_button)
                if discard_btn:
                    await discard_btn.click(timeout=5_000)
        except PlaywrightTimeout:
            logger.debug("Modal close/discard timed out — may already be closed")

    async def logout(self) -> None:
        """Log out of LinkedIn and close browser context."""
        if self._page is not None:
            try:
                await self._page.click(self.sel.profile_menu, timeout=5_000)
                await asyncio.sleep(0.5)
                await self._page.click(self.sel.logout_link, timeout=5_000)
                # Wait briefly for login page to confirm logout
                try:
                    await self._page.wait_for_url("**/login**", timeout=5_000)
                except PlaywrightTimeout:
                    pass
                logger.info("LinkedIn logout successful")
            except PlaywrightTimeout:
                logger.warning("Logout selectors failed — closing browser anyway")

        if self._context is not None:
            await self._context.close()
        if self._playwright is not None:
            await self._playwright.stop()

    # ── Orchestration ──────────────────────────────────────────────────

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
            return self._stats.as_dict()

        try:
            for keyword in keywords:
                if self._should_stop():
                    break

                logger.info("[linkedin] Searching: %s", keyword)
                async for job in self.search(keyword, filters):
                    if self._should_stop():
                        break
                    await self._process_job(job)
        finally:
            await self.logout()

        logger.info("[linkedin] Run complete — %s", self._stats.as_dict())
        return self._stats.as_dict()

    async def _process_job(self, job: Job) -> None:
        """Per-job flow: dedupe → hard-skip → score → threshold → cap → apply.

        Implements guidelines.md §3.2 steps 1–8.
        """
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
            self._record(job, fit_score, "skipped", f"cap reached: linkedin {current}/{self.daily_cap}")
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

        # 8. Delay between applications — LinkedIn-specific: 10-25s
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

        # External apply — skip (Easy Apply only)
        if path == "external_apply":
            self._record(job, fit_score, "skipped", "external apply (company website)")
            return

        # Already applied
        if path == "already_applied":
            self._record(job, fit_score, "skipped", "already applied on LinkedIn")
            return

        # No apply button
        if path == "no_apply_button":
            self._record(job, fit_score, "skipped", "no apply button found")
            return

        # Unknown state
        if path == "unknown":
            self._record(job, fit_score, "error", "unknown application state")
            return

        # Easy Apply — check for unrecognised / essay questions → Yellow
        if form["has_unrecognized"]:
            unrecognized = [
                q for q in form["questions"]
                if self._is_essay_question(q.get("label", ""))
                or (q["type"] == "textarea")
                or (not self._is_recognized_question(q.get("label", "")) and q["type"] != "file")
            ]
            self._queue_for_review(
                job, fit_score, tier, "custom_questions",
                custom_questions=[
                    {"question": q.get("label", ""), "suggested_answer": ""}
                    for q in unrecognized
                ],
            )
            await self._close_modal()
            return

        # Fill and submit (Green path — all standard fields)
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

    async def _detect_human_check(self) -> bool:
        """Check for LinkedIn's human-verification challenge.

        If detected, logs auth_challenge and sets the kill flag. The error-rate
        pre-flight in apply.py will prevent re-running for 24h.
        """
        try:
            await self._page.wait_for_selector(self.sel.human_check, timeout=2_000)
        except PlaywrightTimeout:
            return False

        logger.error("Human verification challenge detected — stopping linkedin for 24h")
        self._log_error("auth_challenge: human_verification")
        self._human_check_detected = True
        return True

    async def _parse_job_card(self, card) -> Job | None:
        """Extract a Job from a search result card element.

        Returns None if essential fields can't be parsed.
        """
        try:
            title = await card.query_selector(self.sel.job_title)
            title_text = (await title.inner_text()).strip() if title else ""

            company = await card.query_selector(self.sel.job_company)
            company_text = (await company.inner_text()).strip() if company else ""

            location = await card.query_selector(self.sel.job_location)
            location_text = (await location.inner_text()).strip() if location else ""

            # LinkedIn doesn't show experience on the search card
            experience_text = ""

            url_el = await card.query_selector(self.sel.job_url)
            url = (await url_el.get_attribute("href")) if url_el else ""
            # LinkedIn job URLs are relative; prepend base if needed
            if url and not url.startswith("http"):
                url = f"https://www.linkedin.com{url}"

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
                posted_date=url,  # stash URL in posted_date
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
        """Check kill-switches: STOP file, session timer, selector failures, human check."""
        import time
        if Path("data/STOP").exists():
            logger.warning("STOP file detected — stopping linkedin")
            return True
        if self._session_start and (time.monotonic() - self._session_start) > MAX_SESSION_SECONDS:
            logger.warning("Session exceeded %ds — stopping linkedin", MAX_SESSION_SECONDS)
            return True
        if self._selector_failures >= MAX_SELECTOR_FAILURES:
            logger.error("Too many selector failures (%d) — stopping linkedin", self._selector_failures)
            return True
        if self._human_check_detected:
            logger.error("Human verification was detected — linkedin stopped for 24h")
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

        Schema per guidelines.md §3.6. Expires after 5 days.
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
