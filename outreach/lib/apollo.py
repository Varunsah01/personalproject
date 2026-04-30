"""Apollo.io free-tier email lookup.

Single-function REST client for the People Enrichment API.
Rate-limited (600 req/hour per Apollo docs), credit-tracked
to ``outreach/data/apollo_usage.csv``.

Usage::

    from outreach.lib.apollo import lookup_email

    result = lookup_email("Priya", "Mehta", "razorpay.com")
    # result: {"email": "priya@razorpay.com", "email_status": "verified", ...}
    # or None if not found / error / quota exhausted
"""

from __future__ import annotations

import csv
import fcntl
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

_API_URL = "https://api.apollo.io/api/v1/people/match"

# Free tier: 50 credits/month.  We leave a 2-credit buffer.
_MONTHLY_CREDIT_CAP = 50
_MONTHLY_CREDIT_WARN = 5

# Rate limit: 600 req/hour → 1 req per 6s is safe.  We enforce a
# minimum 2s gap between calls (burst-safe within the 600/hr budget).
_MIN_INTERVAL_SECS = 2.0

_USAGE_PATH = Path("outreach/data/apollo_usage.csv")
_USAGE_COLUMNS = ["date", "credits_used", "cumulative_month"]

_last_call_ts: float = 0.0


# ---------------------------------------------------------------------------
# Credit tracking
# ---------------------------------------------------------------------------

@dataclass
class _UsageRow:
    date: str
    credits_used: int
    cumulative_month: int


def _ensure_usage_file(path: Path = _USAGE_PATH) -> None:
    """Create the usage CSV with a header if it doesn't exist."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(_USAGE_COLUMNS)


def credits_used_this_month(path: Path = _USAGE_PATH) -> int:
    """Return the cumulative credits used in the current calendar month."""
    _ensure_usage_file(path)
    now = datetime.now(timezone.utc)
    month_prefix = now.strftime("%Y-%m")

    with open(path, newline="") as f:
        fcntl.flock(f, fcntl.LOCK_SH)
        reader = csv.DictReader(f)
        best = 0
        for row in reader:
            if row["date"].startswith(month_prefix):
                try:
                    best = max(best, int(row["cumulative_month"]))
                except (ValueError, KeyError):
                    pass
        fcntl.flock(f, fcntl.LOCK_UN)
    return best


def _record_usage(credits: int, path: Path = _USAGE_PATH) -> int:
    """Append a usage row and return the new cumulative total."""
    _ensure_usage_file(path)
    cumulative = credits_used_this_month(path) + credits
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    with open(path, "a", newline="") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        writer = csv.writer(f)
        writer.writerow([today, credits, cumulative])
        fcntl.flock(f, fcntl.LOCK_UN)

    return cumulative


# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------

def _get_api_key() -> str | None:
    """Read APOLLO_API_KEY from env (loaded from .env.outreach by caller)."""
    key = os.environ.get("APOLLO_API_KEY", "").strip()
    return key or None


def _rate_limit() -> None:
    """Enforce minimum interval between Apollo API calls."""
    global _last_call_ts
    elapsed = time.monotonic() - _last_call_ts
    if elapsed < _MIN_INTERVAL_SECS:
        time.sleep(_MIN_INTERVAL_SECS - elapsed)
    _last_call_ts = time.monotonic()


def lookup_email(
    first_name: str,
    last_name: str,
    domain: str,
    *,
    usage_path: Path = _USAGE_PATH,
) -> dict[str, Any] | None:
    """Look up a person's email via Apollo People Enrichment.

    Args:
        first_name: Person's first name.
        last_name: Person's last name.
        domain: Company domain (e.g. ``razorpay.com``).
        usage_path: Path to the credit usage CSV (for testing).

    Returns:
        A dict with ``email``, ``email_status``, ``name``, ``title``,
        ``linkedin_url`` on success.  ``None`` if not found, quota
        exhausted, API key missing, or API error.

    The ``email_status`` field maps to confidence:
    - ``"verified"`` → high
    - ``"guessed"`` / ``"likely"`` → medium
    - anything else → skip (not usable)
    """
    api_key = _get_api_key()
    if not api_key:
        logger.warning("APOLLO_API_KEY not set — skipping Apollo lookup")
        return None

    # Check monthly quota
    used = credits_used_this_month(usage_path)
    if used >= _MONTHLY_CREDIT_CAP:
        logger.warning(
            "Apollo monthly credit cap reached (%d/%d) — skipping",
            used, _MONTHLY_CREDIT_CAP,
        )
        return None

    if used >= _MONTHLY_CREDIT_CAP - _MONTHLY_CREDIT_WARN:
        logger.info(
            "Apollo credits low: %d/%d used this month",
            used, _MONTHLY_CREDIT_CAP,
        )

    _rate_limit()

    try:
        resp = requests.post(
            _API_URL,
            json={
                "first_name": first_name,
                "last_name": last_name,
                "domain": domain,
                "reveal_personal_emails": False,
            },
            headers={
                "Content-Type": "application/json",
                "Cache-Control": "no-cache",
                "X-Api-Key": api_key,
            },
            timeout=15,
        )
    except requests.RequestException as exc:
        logger.error("Apollo API request failed: %s", exc)
        return None

    # Record credit usage regardless of result (Apollo charges on call)
    cumulative = _record_usage(1, usage_path)
    logger.info("Apollo credit used (cumulative this month: %d/%d)", cumulative, _MONTHLY_CREDIT_CAP)

    if resp.status_code == 401:
        logger.error("Apollo API key invalid (401)")
        return None
    if resp.status_code == 429:
        logger.warning("Apollo rate limit hit (429) — back off")
        return None
    if resp.status_code != 200:
        logger.error("Apollo API error: %d %s", resp.status_code, resp.text[:200])
        return None

    data = resp.json()
    person = data.get("person")
    if not person:
        logger.debug("Apollo: no person match for %s %s @ %s", first_name, last_name, domain)
        return None

    email = person.get("email", "")
    email_status = person.get("email_status", "")

    if not email:
        logger.debug("Apollo: matched person but no email for %s %s", first_name, last_name)
        return None

    return {
        "email": email,
        "email_status": email_status,
        "name": person.get("name", ""),
        "title": person.get("title", ""),
        "linkedin_url": person.get("linkedin_url", ""),
    }
