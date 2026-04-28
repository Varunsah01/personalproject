"""Daily email digest sender.

Build step 9 — not yet implemented. See guidelines.md §3.7 for the
digest email spec (contents, schedule, recipients).
"""

from __future__ import annotations


async def send_daily_digest(summary: dict, smtp_config: dict) -> None:
    """Send the 7 PM digest email to Varun.

    Args:
        summary: Dict with daily stats (applied, skipped, errors, top jobs).
        smtp_config: Dict with SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, DIGEST_TO.

    Raises:
        NotImplementedError: Always — this is a stub until build step 9.
    """
    raise NotImplementedError("Notifier not yet implemented — build step 9")
