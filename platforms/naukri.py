"""Naukri.com platform integration.

Build step 4. Highest volume Indian board, easiest selectors.
Daily cap: 75 (configurable in .env as DAILY_CAP_NAUKRI).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from core.types import ApplyResult, FormDescriptor, Job
from platforms.base import BasePlatform


class NaukriPlatform(BasePlatform):
    """Naukri.com integration."""

    PLATFORM_NAME = "naukri"

    # Selectors — all CSS selectors live here as class constants.
    # Populated when building the platform (step 4), not now.

    async def login(self) -> bool:
        raise NotImplementedError("Naukri login not yet implemented — build step 4")

    async def search(self, keyword: str, filters: dict) -> AsyncIterator[Job]:
        raise NotImplementedError("Naukri search not yet implemented — build step 4")
        yield  # make this a generator  # noqa: RET503

    async def open_application_form(self, job: Job) -> FormDescriptor:
        raise NotImplementedError("Naukri form detection not yet implemented — build step 4")

    async def fill_and_submit(self, form: FormDescriptor, answers: dict) -> ApplyResult:
        raise NotImplementedError("Naukri submit not yet implemented — build step 4")

    async def logout(self) -> None:
        raise NotImplementedError("Naukri logout not yet implemented — build step 4")
