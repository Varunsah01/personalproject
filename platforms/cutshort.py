"""Cutshort platform integration.

Build step 8. India tech roles, moderate volume.
Daily cap: 25 (configurable in .env as DAILY_CAP_CUTSHORT).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from core.types import ApplyResult, FormDescriptor, Job
from platforms.base import BasePlatform


class CutshortPlatform(BasePlatform):
    """Cutshort integration."""

    PLATFORM_NAME = "cutshort"

    async def login(self) -> bool:
        raise NotImplementedError("Cutshort login not yet implemented — build step 8")

    async def search(self, keyword: str, filters: dict) -> AsyncIterator[Job]:
        raise NotImplementedError("Cutshort search not yet implemented — build step 8")
        yield  # noqa: RET503

    async def open_application_form(self, job: Job) -> FormDescriptor:
        raise NotImplementedError("Cutshort form detection not yet implemented — build step 8")

    async def fill_and_submit(self, form: FormDescriptor, answers: dict) -> ApplyResult:
        raise NotImplementedError("Cutshort submit not yet implemented — build step 8")

    async def logout(self) -> None:
        raise NotImplementedError("Cutshort logout not yet implemented — build step 8")
