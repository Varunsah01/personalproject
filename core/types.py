"""Shared data types for job-bot.

BotConfig carries the configuration that every platform needs.
PlatformStats tracks per-run apply/skip/error/queue counts.
Job and LogEntry live in scorer.py and logger.py respectively —
kept separate because they predate this file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class BotConfig:
    """Configuration shared by every platform.

    Constructed once per run in apply.py and passed to each platform's
    ``__init__``.  Platform-specific values (credentials, CTC env vars)
    are added by the subclass after calling ``super().__init__(config)``.

    Attributes:
        log_path: Path to applications_log.csv.
        headless: Whether to run the browser in headless mode.
        dry_run: Score and log but never click Apply.
        daily_cap: Maximum applications per day for this platform.
        standard_answers: Dict of standard form-field answers (name, phone,
            location, etc.).  The base set is built in apply.py; each
            platform subclass extends it with email / CTC from .env.
    """

    log_path: Path
    headless: bool = True
    dry_run: bool = False
    daily_cap: int = 75
    standard_answers: dict[str, str] = field(default_factory=dict)


@dataclass
class PlatformStats:
    """Mutable counters for one platform run.

    Attributes:
        applied: Jobs successfully submitted (includes applied_unconfirmed).
        skipped: Jobs intentionally not applied (duplicate, below threshold, etc.).
        errored: Jobs that hit an error during the apply flow.
        queued: Jobs sent to the Yellow review queue.
    """

    applied: int = 0
    skipped: int = 0
    errored: int = 0
    queued: int = 0

    def increment(self, status: str) -> None:
        """Increment the counter matching *status*.

        Maps CSV status values to the correct field:
        - ``"applied"`` / ``"applied_unconfirmed"`` -> applied
        - ``"skipped"`` -> skipped
        - ``"error"`` -> errored
        - ``"queued"`` -> queued
        """
        if status in ("applied", "applied_unconfirmed"):
            self.applied += 1
        elif status == "skipped":
            self.skipped += 1
        elif status == "error":
            self.errored += 1
        elif status == "queued":
            self.queued += 1

    def reset(self) -> None:
        """Zero all counters (called at the start of each run)."""
        self.applied = 0
        self.skipped = 0
        self.errored = 0
        self.queued = 0

    def as_dict(self) -> dict[str, int]:
        """Return a plain dict for callers that expect one (apply.py)."""
        return {
            "applied": self.applied,
            "skipped": self.skipped,
            "errored": self.errored,
            "queued": self.queued,
        }
