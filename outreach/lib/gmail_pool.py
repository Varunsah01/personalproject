"""Gmail inbox pool for outreach send rotation.

Loads inbox configs from .env.outreach, picks the lowest-count inbox
under its per-inbox cap, enforces the global daily cap from
GUARDRAILS.md §1.7 and guidelines.md §3.8.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

from outreach.lib import tracker

logger = logging.getLogger(__name__)

_DEFAULT_GLOBAL_CAP = 25
_DEFAULT_PER_INBOX_CAP = 25


class NoInboxAvailableError(RuntimeError):
    """All inboxes are at their daily send cap."""


@dataclass
class InboxConfig:
    """Configuration for a single Gmail sending inbox.

    Attributes:
        address: Gmail address (e.g. "varun.outreach1@gmail.com").
        oauth_token_path: Path to the saved OAuth token JSON file.
        daily_cap: Max sends/day for this inbox.
    """

    address: str
    oauth_token_path: Path
    daily_cap: int = _DEFAULT_PER_INBOX_CAP


class InboxPool:
    """Rotates across configured Gmail inboxes, picking the least-used one.

    Attributes:
        inboxes: List of InboxConfig instances.
        global_cap: Max sends/day across all inboxes combined.
    """

    def __init__(
        self,
        inboxes: list[InboxConfig],
        global_cap: int = _DEFAULT_GLOBAL_CAP,
    ) -> None:
        self.inboxes = inboxes
        self.global_cap = global_cap

    @classmethod
    def load_from_env(
        cls,
        env_path: Path | str = Path(".env.outreach"),
    ) -> InboxPool:
        """Build an InboxPool from .env.outreach variables.

        Reads OUTREACH_INBOX_{1..5}_ADDRESS and _TOKEN_PATH, plus optional
        OUTREACH_DAILY_CAP_GLOBAL and OUTREACH_DAILY_CAP_PER_INBOX.

        Args:
            env_path: Path to the .env.outreach file.

        Returns:
            Configured InboxPool instance.
        """
        env = dotenv_values(str(env_path))
        global_cap = int(env.get("OUTREACH_DAILY_CAP_GLOBAL", _DEFAULT_GLOBAL_CAP))
        per_inbox_cap = int(
            env.get("OUTREACH_DAILY_CAP_PER_INBOX", _DEFAULT_PER_INBOX_CAP)
        )

        inboxes: list[InboxConfig] = []
        for i in range(1, 6):
            address = env.get(f"OUTREACH_INBOX_{i}_ADDRESS", "").strip()
            token_path = env.get(f"OUTREACH_INBOX_{i}_TOKEN_PATH", "").strip()
            if address and token_path:
                inboxes.append(
                    InboxConfig(
                        address=address,
                        oauth_token_path=Path(token_path),
                        daily_cap=per_inbox_cap,
                    )
                )

        if not inboxes:
            logger.warning("No inboxes configured in %s", env_path)

        return cls(inboxes=inboxes, global_cap=global_cap)

    def pick_inbox(
        self,
        tracker_path: Path | str = Path("outreach/data/tracker.csv"),
    ) -> InboxConfig:
        """Select the inbox with the lowest send count today, under cap.

        Args:
            tracker_path: Path to tracker.csv for count lookups.

        Returns:
            The InboxConfig to use for the next send.

        Raises:
            NoInboxAvailableError: If all inboxes are at their daily cap.
        """
        # Global cap check first
        global_count = tracker.count_sent_today(tracker_path)
        if global_count >= self.global_cap:
            raise NoInboxAvailableError(
                f"Global daily cap reached: {global_count}/{self.global_cap}"
            )

        # Find the inbox with the lowest count that's still under its cap
        best: InboxConfig | None = None
        best_count = float("inf")

        for inbox in self.inboxes:
            count = tracker.count_sent_today_by_inbox(inbox.address, tracker_path)
            if count < inbox.daily_cap and count < best_count:
                best = inbox
                best_count = count

        if best is None:
            raise NoInboxAvailableError("All inboxes at per-inbox daily cap")

        return best
