"""Daily email digest sender.

Build step 9. See guidelines.md §3.7 for the digest email spec
(contents, schedule, recipients).
"""

from __future__ import annotations


async def send_daily_digest(summary: dict, smtp_config: dict) -> None:
    """Send the 7 PM digest email.

    Args:
        summary: Dict with daily stats (applied, skipped, errors, top jobs).
        smtp_config: Dict with SMTP_HOST, SMTP_PORT, SMTP_USER,
                     SMTP_PASSWORD, DIGEST_TO.
    """
    raise NotImplementedError
