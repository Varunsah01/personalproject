"""BasePlatform — abstract base class for all job platform integrations.

Subclasses implement platform-specific primitives (login, search, etc.).
Orchestration logic (dedupe, score, threshold, cap, tier-route, apply, delay)
will live here in process_job() and run() — added in later build steps.

Design: the per-application flow from guidelines.md §3.2 is identical across
platforms. Only the mechanics of interacting with each site's HTML differ.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from core.scorer import Job


class BasePlatform(ABC):
    """Base class for all job platform integrations.

    Subclasses implement the five platform-specific primitives below.
    Orchestration methods (process_job, run) are added in later build steps.
    """

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
            **getattr(self, "standard_answers", {}),
            "_custom": {k.lower(): v for k, v in (answers or {}).items()},
        }
        form = await self.open_application_form(job)
        return await self.fill_and_submit(form, merged)
