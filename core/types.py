"""Data types shared across all modules.

Defines the contracts between platforms, scorer, logger, and the orchestration
layer in BasePlatform. No external dependencies — stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


@dataclass
class Job:
    """A single job listing scraped from a platform."""

    platform: str
    company_name: str
    role_title: str
    experience_required: str
    location: str
    job_url: str
    jd_snippet: str  # first 500 chars of JD, used for hard-skip regex
    raw_data: dict = field(default_factory=dict)  # platform-specific, opaque to base


@dataclass
class FormField:
    """A single field in an application form."""

    name: str
    field_type: str  # text, select, file, checkbox, radio, textarea
    max_length: int | None = None
    is_recognized: bool = True  # False triggers Yellow demotion
    current_value: str = ""  # pre-filled value, if any


@dataclass
class FormDescriptor:
    """Describes an application form's fields before filling.

    Returned by BasePlatform.open_application_form(). The orchestration layer
    inspects this to decide whether to apply (Green) or queue (Yellow).
    """

    fields: list[FormField] = field(default_factory=list)

    @property
    def has_unrecognized_fields(self) -> bool:
        """Any field the bot doesn't know how to fill."""
        return any(not f.is_recognized for f in self.fields)

    @property
    def has_long_text_fields(self) -> bool:
        """Any text/textarea field requiring > 100 chars (custom essay)."""
        return any(
            f.field_type in ("text", "textarea")
            and f.max_length is not None
            and f.max_length > 100
            for f in self.fields
        )


class ApplyStatus(Enum):
    """Outcome of a single form submission attempt."""

    APPLIED = "applied"
    APPLIED_UNCONFIRMED = "applied_unconfirmed"
    ERROR = "error"


@dataclass
class ApplyResult:
    """Returned by BasePlatform.fill_and_submit()."""

    status: ApplyStatus
    notes: str = ""
    confirmation_url: str | None = None


class ProcessStatus(Enum):
    """Outcome of the full per-job orchestration flow."""

    APPLIED = "applied"
    APPLIED_UNCONFIRMED = "applied_unconfirmed"
    QUEUED = "queued"
    SKIPPED = "skipped"
    ERROR = "error"


@dataclass
class ProcessResult:
    """Returned by BasePlatform.process_job()."""

    status: ProcessStatus
    notes: str = ""
    fit_score: float | None = None
    tier: str | None = None  # T1, T2, T3, or None (hard-skip / untiered)
