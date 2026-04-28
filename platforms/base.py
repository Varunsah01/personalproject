"""BasePlatform — orchestration layer for all job platform integrations.

Subclasses implement platform-specific primitives (login, search, etc.).
Orchestration logic (dedupe, score, threshold, cap, tier-route, apply, delay)
lives here in process_job() and run() — subclasses do NOT override these.

Design rationale: the per-application flow from guidelines.md §3.2 is
identical across platforms. Only the mechanics of interacting with each
site's HTML differ. This separation means the flow logic changes in one
place, and platform modules stay focused on selectors and page interaction.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from pathlib import Path

from core.logger import ApplicationLogger
from core.scorer import JobScorer
from core.types import (
    ApplyResult,
    ApplyStatus,
    FormDescriptor,
    Job,
    ProcessResult,
    ProcessStatus,
)

logger = logging.getLogger(__name__)

# Kill-switch file — checked between every application (guidelines.md §6)
STOP_FILE = Path("data/STOP")

# Session limits (guidelines.md §3.3)
MAX_SESSION_SECONDS = 90 * 60  # 90 minutes per platform
MAX_PAGES_PER_KEYWORD = 5      # CLAUDE.md §6

# Delay ranges (guidelines.md §3.2)
APPLY_DELAY_RANGE = (5, 15)        # seconds between applications
PAGE_DELAY_RANGE = (30, 90)        # seconds between search result pages

# Selector retry (guidelines.md §3.4)
SELECTOR_RETRY_DELAY = 3  # seconds
MAX_SELECTOR_FAILURES = 5  # per session before stopping platform

# Network retry (guidelines.md §3.4)
NETWORK_RETRY_DELAYS = [5, 15]  # exponential backoff


class BasePlatform(ABC):
    """Base class for all job platform integrations.

    Subclasses implement platform-specific primitives (login, search, etc.).
    Orchestration logic lives here — subclasses do NOT override process_job()
    or run().

    Args:
        platform_name: e.g. 'naukri', 'linkedin'.
        app_logger: ApplicationLogger instance for CSV writes and dedupe.
        scorer: JobScorer instance for scoring and tier classification.
        daily_cap: Max applications per day for this platform.
        dry_run: If True, score and log but never submit.
        standard_answers: Dict of standard form field answers (name, email, etc.).
    """

    def __init__(
        self,
        platform_name: str,
        app_logger: ApplicationLogger,
        scorer: JobScorer,
        daily_cap: int,
        dry_run: bool = False,
        standard_answers: dict | None = None,
    ) -> None:
        self.platform_name = platform_name.lower()
        self.app_logger = app_logger
        self.scorer = scorer
        self.daily_cap = daily_cap
        self.dry_run = dry_run
        self.standard_answers = standard_answers or {}

        # Session state
        self._session_start: float = 0.0
        self._selector_failures: int = 0
        self._stats: dict[str, int] = {
            "applied": 0,
            "skipped": 0,
            "errored": 0,
            "queued": 0,
        }

    # --- Abstract primitives (subclass implements) ---

    @abstractmethod
    async def login(self) -> bool:
        """Log into the platform. Returns True on success."""
        ...

    @abstractmethod
    async def search(self, keyword: str, filters: dict) -> AsyncIterator[Job]:
        """Yield Job objects from search results for a keyword.

        Should paginate up to MAX_PAGES_PER_KEYWORD pages.
        Should insert PAGE_DELAY_RANGE delays between pages.
        """
        ...

    @abstractmethod
    async def open_application_form(self, job: Job) -> FormDescriptor:
        """Navigate to the job's apply form and describe its fields.

        Returns a FormDescriptor so the orchestration layer can decide
        whether this is a Green (standard) or Yellow (complex) application.
        """
        ...

    @abstractmethod
    async def fill_and_submit(
        self, form: FormDescriptor, answers: dict
    ) -> ApplyResult:
        """Fill the form with the provided answers and click submit.

        Only called for Green-tier applications. The answers dict contains
        standard field values from .env and profile.md.
        """
        ...

    @abstractmethod
    async def logout(self) -> None:
        """Log out of the platform gracefully."""
        ...

    # --- Orchestration (base class, not overridden) ---

    async def process_job(self, job: Job) -> ProcessResult:
        """Full per-application flow from guidelines.md §3.2.

        Steps:
            1. Dedupe check
            2. Hard-skip check (deal-breakers)
            3. Score
            4. Threshold check
            5. Cap check
            6. Tier route (Green -> apply, Yellow -> queue)
            7. Apply (Green only):
               a. open_application_form()
               b. Detect form complexity -> demote to Yellow if needed
               c. fill_and_submit()
               d. Log result
            8. Random delay (5-15s)
        """
        # 1. Dedupe
        if self.app_logger.is_duplicate(self.platform_name, job.job_url):
            self._log_and_record(job, 0.0, ProcessStatus.SKIPPED, "duplicate")
            return ProcessResult(
                status=ProcessStatus.SKIPPED, notes="duplicate"
            )

        # 2. Hard-skip
        skip_reason = self.scorer.check_hard_skip(job)
        if skip_reason:
            self._log_and_record(job, 0.0, ProcessStatus.SKIPPED, skip_reason)
            return ProcessResult(
                status=ProcessStatus.SKIPPED, notes=skip_reason
            )

        # 3. Score
        fit_score = self.scorer.score_job(job)
        tier = self.scorer.get_tier(job.role_title)

        # 4. Threshold check
        if tier is None:
            self._log_and_record(
                job, fit_score, ProcessStatus.SKIPPED, "no matching tier"
            )
            return ProcessResult(
                status=ProcessStatus.SKIPPED,
                notes="no matching tier",
                fit_score=fit_score,
            )
        if not self.scorer.passes_threshold(fit_score, tier):
            note = f"below threshold: {fit_score:.2f}"
            self._log_and_record(job, fit_score, ProcessStatus.SKIPPED, note)
            return ProcessResult(
                status=ProcessStatus.SKIPPED,
                notes=note,
                fit_score=fit_score,
                tier=tier,
            )

        # 5. Cap check
        today_counts = self.app_logger.get_today_counts()
        current_count = today_counts.get(self.platform_name, 0)
        if current_count >= self.daily_cap:
            note = f"cap reached: {self.platform_name} {current_count}/{self.daily_cap}"
            self._log_and_record(job, fit_score, ProcessStatus.SKIPPED, note)
            return ProcessResult(
                status=ProcessStatus.SKIPPED,
                notes=note,
                fit_score=fit_score,
                tier=tier,
            )

        # 6. Tier route — determine Green vs Yellow
        is_yellow = self._should_queue(fit_score, tier)

        if is_yellow:
            reason = self._queue_reason(fit_score, tier)
            self.app_logger.log_to_review_queue(
                platform=self.platform_name,
                company_name=job.company_name,
                role_title=job.role_title,
                job_url=job.job_url,
                fit_score=fit_score,
                tier=tier,
                reason=reason,
            )
            self._log_and_record(job, fit_score, ProcessStatus.QUEUED, f"queued: {reason}")
            return ProcessResult(
                status=ProcessStatus.QUEUED,
                notes=f"queued: {reason}",
                fit_score=fit_score,
                tier=tier,
            )

        # 7. Apply (Green path)
        if self.dry_run:
            note = "dry run: would apply"
            self._log_and_record(job, fit_score, ProcessStatus.SKIPPED, note)
            return ProcessResult(
                status=ProcessStatus.SKIPPED,
                notes=note,
                fit_score=fit_score,
                tier=tier,
            )

        result = await self._attempt_apply(job, fit_score, tier)

        # 8. Delay
        delay = random.uniform(*APPLY_DELAY_RANGE)
        logger.debug("Waiting %.1fs before next application", delay)
        await asyncio.sleep(delay)

        return result

    async def run(self, keywords: list[str], filters: dict) -> dict[str, int]:
        """Outer loop: login -> search each keyword -> process each job -> logout.

        Returns:
            Summary dict: {applied, skipped, errored, queued}.

        Stops early if:
            - STOP file exists (guidelines.md §6)
            - Daily cap reached
            - Session exceeds 90 minutes (guidelines.md §3.3)
        """
        self._session_start = time.monotonic()
        self._stats = {"applied": 0, "skipped": 0, "errored": 0, "queued": 0}

        # Check STOP file before starting
        if STOP_FILE.exists():
            logger.warning("STOP file found — aborting %s run", self.platform_name)
            return self._stats

        # Login
        try:
            logged_in = await self.login()
        except Exception:
            logger.exception("Login failed for %s", self.platform_name)
            return self._stats
        if not logged_in:
            logger.error("Login returned False for %s — skipping platform", self.platform_name)
            return self._stats

        try:
            for keyword in keywords:
                if self._should_stop():
                    break

                logger.info("[%s] Searching: %s", self.platform_name, keyword)
                try:
                    async for job in self.search(keyword, filters):
                        if self._should_stop():
                            break

                        try:
                            result = await self.process_job(job)
                            self._stats[result.status.value] = (
                                self._stats.get(result.status.value, 0) + 1
                            )
                            # Cap reached — stop the platform run entirely
                            if "cap reached" in (result.notes or ""):
                                logger.info("Daily cap reached for %s", self.platform_name)
                                break
                        except Exception:
                            logger.exception(
                                "Unexpected error processing %s at %s",
                                job.role_title,
                                job.company_name,
                            )
                            self._stats["errored"] += 1
                except Exception:
                    logger.exception(
                        "Search failed for keyword '%s' on %s",
                        keyword,
                        self.platform_name,
                    )
        finally:
            try:
                await self.logout()
            except Exception:
                logger.exception("Logout failed for %s", self.platform_name)

        elapsed = time.monotonic() - self._session_start
        logger.info(
            "[%s] Run complete in %.0fs — %s",
            self.platform_name,
            elapsed,
            self._stats,
        )
        return self._stats

    # --- Private helpers ---

    def _should_stop(self) -> bool:
        """Check kill switches: STOP file and session timer."""
        if STOP_FILE.exists():
            logger.warning("STOP file detected — stopping %s", self.platform_name)
            return True
        elapsed = time.monotonic() - self._session_start
        if elapsed > MAX_SESSION_SECONDS:
            logger.warning(
                "Session exceeded %ds for %s — stopping",
                MAX_SESSION_SECONDS,
                self.platform_name,
            )
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

    def _queue_reason(self, fit_score: float, tier: str) -> str:
        """Determine why a job is being queued."""
        if tier in ("T2", "T3"):
            return "score_in_review_band"
        if tier == "T1" and fit_score < 0.7:
            return "score_in_review_band"
        return "score_in_review_band"

    async def _attempt_apply(
        self, job: Job, fit_score: float, tier: str
    ) -> ProcessResult:
        """Attempt to open form, check complexity, fill, and submit."""
        try:
            form = await self.open_application_form(job)
        except TimeoutError:
            self._selector_failures += 1
            if self._selector_failures >= MAX_SELECTOR_FAILURES:
                logger.error(
                    "Too many selector failures (%d) on %s — stopping platform",
                    self._selector_failures,
                    self.platform_name,
                )
            note = "selector_broken: application form"
            self._log_and_record(job, fit_score, ProcessStatus.ERROR, note)
            return ProcessResult(
                status=ProcessStatus.ERROR,
                notes=note,
                fit_score=fit_score,
                tier=tier,
            )
        except Exception as exc:
            note = f"error opening form: {exc}"
            self._log_and_record(job, fit_score, ProcessStatus.ERROR, note)
            return ProcessResult(
                status=ProcessStatus.ERROR,
                notes=note,
                fit_score=fit_score,
                tier=tier,
            )

        # Demote to Yellow if form is complex (guidelines.md §3.2 step 7)
        if form.has_unrecognized_fields or form.has_long_text_fields:
            reason = "custom_questions" if form.has_long_text_fields else "custom_questions"
            self.app_logger.log_to_review_queue(
                platform=self.platform_name,
                company_name=job.company_name,
                role_title=job.role_title,
                job_url=job.job_url,
                fit_score=fit_score,
                tier=tier,
                reason=reason,
                custom_questions=[
                    {"question": f.name, "suggested_answer": ""}
                    for f in form.fields
                    if not f.is_recognized
                ],
            )
            note = "promoted to review: custom essay"
            self._log_and_record(job, fit_score, ProcessStatus.QUEUED, note)
            return ProcessResult(
                status=ProcessStatus.QUEUED,
                notes=note,
                fit_score=fit_score,
                tier=tier,
            )

        # Fill and submit
        try:
            apply_result = await self.fill_and_submit(form, self.standard_answers)
        except TimeoutError:
            self._selector_failures += 1
            note = "selector_broken: submit"
            self._log_and_record(job, fit_score, ProcessStatus.ERROR, note)
            return ProcessResult(
                status=ProcessStatus.ERROR,
                notes=note,
                fit_score=fit_score,
                tier=tier,
            )
        except Exception as exc:
            note = f"error submitting: {exc}"
            self._log_and_record(job, fit_score, ProcessStatus.ERROR, note)
            return ProcessResult(
                status=ProcessStatus.ERROR,
                notes=note,
                fit_score=fit_score,
                tier=tier,
            )

        # Map ApplyResult to ProcessResult
        status_map = {
            ApplyStatus.APPLIED: ProcessStatus.APPLIED,
            ApplyStatus.APPLIED_UNCONFIRMED: ProcessStatus.APPLIED_UNCONFIRMED,
            ApplyStatus.ERROR: ProcessStatus.ERROR,
        }
        process_status = status_map[apply_result.status]
        self._log_and_record(
            job, fit_score, process_status, apply_result.notes
        )
        return ProcessResult(
            status=process_status,
            notes=apply_result.notes,
            fit_score=fit_score,
            tier=tier,
        )

    def _log_and_record(
        self,
        job: Job,
        fit_score: float,
        status: ProcessStatus,
        notes: str,
    ) -> None:
        """Log to CSV and update session stats."""
        self.app_logger.log_application(
            platform=self.platform_name,
            company_name=job.company_name,
            role_title=job.role_title,
            experience_required=job.experience_required,
            location=job.location,
            job_url=job.job_url,
            fit_score=fit_score,
            status=status.value,
            notes=notes,
        )
