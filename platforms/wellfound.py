"""Wellfound (AngelList) platform integration.

Build step 7. Startups — best fit for Varun's profile.
Daily cap: 30 (configurable in .env as DAILY_CAP_WELLFOUND).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from core.types import ApplyResult, FormDescriptor, Job
from platforms.base import BasePlatform


class WellfoundPlatform(BasePlatform):
    """Wellfound (AngelList) integration."""

    PLATFORM_NAME = "wellfound"

    async def login(self) -> bool:
        raise NotImplementedError("Wellfound login not yet implemented — build step 7")

    async def search(self, keyword: str, filters: dict) -> AsyncIterator[Job]:
        raise NotImplementedError("Wellfound search not yet implemented — build step 7")
        yield  # noqa: RET503

    async def open_application_form(self, job: Job) -> FormDescriptor:
        raise NotImplementedError("Wellfound form detection not yet implemented — build step 7")

    async def fill_and_submit(self, form: FormDescriptor, answers: dict) -> ApplyResult:
        raise NotImplementedError("Wellfound submit not yet implemented — build step 7")

    async def logout(self) -> None:
        raise NotImplementedError("Wellfound logout not yet implemented — build step 7")
