"""BasePlatform — abstract base class for all job platform integrations.

Subclasses implement platform-specific primitives (login, search, etc.).
Orchestration logic (dedupe, score, threshold, cap, tier-route, apply, delay)
lives in each subclass's run() / _process_job() for now; shared helpers
(_safe_click, _handle_bot_detection, ensure_logged_in) live here.

Design: the per-application flow from guidelines.md §3.2 is identical across
platforms. Only the mechanics of interacting with each site's HTML differ.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from pathlib import Path

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from core.browser import BotDetectionError, human_click, is_bot_challenged
from core.scorer import Job
from core.types import BotConfig, PlatformStats

logger = logging.getLogger(__name__)

# Retry delay before a second attempt on a broken selector (guidelines.md §3.4)
_SELECTOR_RETRY_DELAY = 3


class BasePlatform(ABC):
    """Base class for all job platform integrations.

    Subclasses implement the five platform-specific primitives below.
    Shared session state (page, context, stats, selector-failure counter)
    is initialised here so platform modules don't duplicate it.

    Args:
        config: A BotConfig with log path, caps, headless flag, etc.
    """

    # Subclasses MUST set this to their platform name (e.g. "naukri").
    PLATFORM_NAME: str = ""

    def __init__(self, config: BotConfig) -> None:
        self.log_path: Path = config.log_path
        self.headless: bool = config.headless
        self.dry_run: bool = config.dry_run
        self.daily_cap: int = config.daily_cap
        self.standard_answers: dict[str, str] = dict(config.standard_answers)

        # Session state — shared across all platforms
        self._stats: PlatformStats = PlatformStats()
        self._page: Page | None = None
        self._context = None
        self._playwright = None
        self._selector_failures: int = 0
        self._session_start: float = 0.0

    # ── Abstract primitives (subclass implements) ─────────────────────

    @abstractmethod
    async def login(self) -> bool:
        """Log into the platform.

        Returns:
            True on success, False on failure.
        """
        raise NotImplementedError

    @abstractmethod
    async def search(self, keyword: str, filters: dict) -> AsyncIterator:
        """Yield Job objects from search results for a keyword.

        Should paginate up to 5 pages with delays between pages.

        Args:
            keyword: Search query string.
            filters: Dict of filters (locations, experience range, etc.).

        Yields:
            Job objects from search results.
        """
        raise NotImplementedError

    @abstractmethod
    async def open_application_form(self, job: object) -> object:
        """Navigate to the job's apply form and describe its fields.

        Args:
            job: A Job object representing the listing.

        Returns:
            A FormDescriptor describing the form's fields.
        """
        raise NotImplementedError

    @abstractmethod
    async def fill_and_submit(self, form: object, answers: dict) -> object:
        """Fill the application form and click submit.

        Only called for Green-tier applications (standard fields only).

        Args:
            form: A FormDescriptor from open_application_form().
            answers: Dict of standard field values (name, email, etc.).

        Returns:
            An ApplyResult with the submission outcome.
        """
        raise NotImplementedError

    @abstractmethod
    async def logout(self) -> None:
        """Log out of the platform gracefully."""
        raise NotImplementedError

    # ── Shared helpers ────────────────────────────────────────────────

    async def ensure_logged_in(self, auth_selector: str | None = None) -> bool:
        """Check for an active session; if not found, call login().

        Args:
            auth_selector: Optional CSS selector that is only present when
                the user is logged in (e.g. a profile avatar).  If provided
                and found on the current page, login() is skipped.

        Returns:
            True if the session is active (either pre-existing or freshly
            logged in), False if login() failed.
        """
        if self._page is not None and auth_selector:
            try:
                await self._page.wait_for_selector(auth_selector, timeout=3_000)
                logger.info("[%s] Already logged in (found %s)", self.PLATFORM_NAME, auth_selector)
                return True
            except PlaywrightTimeout:
                pass  # not logged in — fall through to login()

        return await self.login()

    async def _safe_click(self, selector: str, timeout: int = 10_000) -> bool:
        """Click an element with retry and selector-failure tracking.

        Uses human_click from core.browser for realistic mouse movement.
        On timeout: waits ``_SELECTOR_RETRY_DELAY`` seconds and retries once.
        After 5 cumulative selector failures in a session, raises RuntimeError
        so the platform run stops (guidelines.md §3.4).

        Args:
            selector: CSS selector of the element to click.
            timeout: Milliseconds to wait for the element (per attempt).

        Returns:
            True if the click succeeded, False if both attempts timed out.

        Raises:
            RuntimeError: If cumulative selector failures reach 5.
        """
        if self._page is None:
            return False

        try:
            await human_click(self._page, selector)
            return True
        except (PlaywrightTimeout, Exception):
            self._selector_failures += 1
            logger.debug(
                "[%s] _safe_click failed on '%s' (attempt 1, failures=%d)",
                self.PLATFORM_NAME, selector, self._selector_failures,
            )

        # Retry once after a short pause
        await asyncio.sleep(_SELECTOR_RETRY_DELAY)
        try:
            await human_click(self._page, selector)
            return True
        except (PlaywrightTimeout, Exception):
            self._selector_failures += 1
            logger.warning(
                "[%s] _safe_click failed on '%s' (attempt 2, failures=%d)",
                self.PLATFORM_NAME, selector, self._selector_failures,
            )

        if self._selector_failures >= 5:
            raise RuntimeError(
                f"selector_failure_limit: {self._selector_failures} failures in session"
            )
        return False

    async def _handle_bot_detection(self) -> None:
        """Check the current page for bot challenges and raise if found.

        Takes a debug screenshot to data/debug/ before raising so the user
        can see what the platform showed.

        Raises:
            BotDetectionError: If is_bot_challenged() returns True.
        """
        if self._page is None:
            return

        if not await is_bot_challenged(self._page):
            return

        logger.warning("[%s] Bot detection challenge found on page", self.PLATFORM_NAME)

        # Save a debug screenshot — best-effort, don't let a screenshot
        # failure mask the real problem.
        try:
            debug_dir = Path("data/debug")
            debug_dir.mkdir(parents=True, exist_ok=True)
            path = debug_dir / f"{self.PLATFORM_NAME}_bot.png"
            await self._page.screenshot(path=str(path))
            logger.info("Debug screenshot saved to %s", path)
        except Exception as exc:
            logger.debug("Could not save debug screenshot: %s", exc)

        raise BotDetectionError("bot challenge detected", self.PLATFORM_NAME)

    async def score_and_apply(
        self,
        job: Job,
        fit_score: float,
        tier: str,
        answers: dict | None = None,
    ) -> dict:
        """Apply to a queued job using human-reviewed answers for custom questions.

        Called by the review-queue handler after Varun has confirmed and
        optionally edited the suggested answers for any unrecognised fields.
        Not abstract — delegates to the existing open_application_form /
        fill_and_submit primitives that each platform already implements.

        Args:
            job: Job object (URL stored in job.posted_date per platform convention).
            fit_score: Pre-computed fit score from the queue entry.
            tier: Pre-classified tier (T1/T2/T3) from the queue entry.
            answers: {question_text: answer} for custom questions, or None for
                auto behaviour (identical to the normal Green apply path).

        Returns:
            {"status": str, "notes": str} from fill_and_submit.
        """
        merged = {
            **self.standard_answers,
            "_custom": {k.lower(): v for k, v in (answers or {}).items()},
        }
        form = await self.open_application_form(job)
        return await self.fill_and_submit(form, merged)
