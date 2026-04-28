"""LinkedIn Easy Apply platform integration.

Build step 5. Best quality, but stricter rate limits.
Daily cap: 40 (configurable in .env as DAILY_CAP_LINKEDIN).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from core.types import ApplyResult, FormDescriptor, Job
from platforms.base import BasePlatform


class LinkedInPlatform(BasePlatform):
    """LinkedIn Easy Apply integration."""

    PLATFORM_NAME = "linkedin"

    async def login(self) -> bool:
        raise NotImplementedError("LinkedIn login not yet implemented — build step 5")

    async def search(self, keyword: str, filters: dict) -> AsyncIterator[Job]:
        raise NotImplementedError("LinkedIn search not yet implemented — build step 5")
        yield  # noqa: RET503

    async def open_application_form(self, job: Job) -> FormDescriptor:
        raise NotImplementedError("LinkedIn form detection not yet implemented — build step 5")

    async def fill_and_submit(self, form: FormDescriptor, answers: dict) -> ApplyResult:
        raise NotImplementedError("LinkedIn submit not yet implemented — build step 5")

    async def logout(self) -> None:
        raise NotImplementedError("LinkedIn logout not yet implemented — build step 5")
