"""Push notifications via ntfy.sh free tier.

Single function for mobile alerts.  Posts to ``https://ntfy.sh/{TOPIC}``
where TOPIC is read from the ``NTFY_TOPIC`` env var.  If unset, logs a
warning and returns False — never crashes the caller.

Usage::

    from outreach.lib.push import notify

    notify("drafts ready", "5 drafts ready for review", priority="high")
"""

from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger(__name__)

_NTFY_BASE = "https://ntfy.sh"


def notify(
    title: str,
    body: str,
    *,
    priority: str = "default",
    tags: str = "",
) -> bool:
    """Send a push notification via ntfy.sh.

    Args:
        title: Notification title (shown bold on mobile).
        body: Notification body text.
        priority: One of ``min``, ``low``, ``default``, ``high``, ``urgent``.
        tags: Comma-separated ntfy tag names (mapped to emoji on mobile).

    Returns:
        ``True`` if sent successfully, ``False`` if skipped or failed.
    """
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        logger.warning("NTFY_TOPIC not set — push notification skipped")
        return False

    headers: dict[str, str] = {
        "Title": title,
        "Priority": priority,
    }
    if tags:
        headers["Tags"] = tags

    try:
        resp = requests.post(
            f"{_NTFY_BASE}/{topic}",
            data=body.encode("utf-8"),
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        logger.info("Push sent: %s", title)
        return True
    except requests.RequestException as exc:
        logger.error("Push notification failed: %s", exc)
        return False
