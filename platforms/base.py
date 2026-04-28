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
