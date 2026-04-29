"""Timezone-aware send-window scheduling for outreach.

Determines the next valid send time in a recipient's local timezone,
respecting the 10:00-11:00 / 14:00-15:00 windows and weekend exclusions
from GUARDRAILS.md §1.7 and guidelines.md §3.8.
"""

from __future__ import annotations

import random
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

# Top 30 countries by outreach likelihood → canonical IANA timezone.
# For countries spanning multiple zones, we pick the business-capital zone.
COUNTRY_TZ: dict[str, str] = {
    "india": "Asia/Kolkata",
    "in": "Asia/Kolkata",
    "united states": "America/New_York",
    "us": "America/New_York",
    "usa": "America/New_York",
    "united kingdom": "Europe/London",
    "uk": "Europe/London",
    "gb": "Europe/London",
    "germany": "Europe/Berlin",
    "de": "Europe/Berlin",
    "france": "Europe/Paris",
    "fr": "Europe/Paris",
    "canada": "America/Toronto",
    "ca": "America/Toronto",
    "australia": "Australia/Sydney",
    "au": "Australia/Sydney",
    "singapore": "Asia/Singapore",
    "sg": "Asia/Singapore",
    "japan": "Asia/Tokyo",
    "jp": "Asia/Tokyo",
    "south korea": "Asia/Seoul",
    "kr": "Asia/Seoul",
    "china": "Asia/Shanghai",
    "cn": "Asia/Shanghai",
    "netherlands": "Europe/Amsterdam",
    "nl": "Europe/Amsterdam",
    "sweden": "Europe/Stockholm",
    "se": "Europe/Stockholm",
    "switzerland": "Europe/Zurich",
    "ch": "Europe/Zurich",
    "israel": "Asia/Jerusalem",
    "il": "Asia/Jerusalem",
    "uae": "Asia/Dubai",
    "ae": "Asia/Dubai",
    "brazil": "America/Sao_Paulo",
    "br": "America/Sao_Paulo",
    "mexico": "America/Mexico_City",
    "mx": "America/Mexico_City",
    "ireland": "Europe/Dublin",
    "ie": "Europe/Dublin",
    "spain": "Europe/Madrid",
    "es": "Europe/Madrid",
    "italy": "Europe/Rome",
    "it": "Europe/Rome",
    "poland": "Europe/Warsaw",
    "pl": "Europe/Warsaw",
    "indonesia": "Asia/Jakarta",
    "id": "Asia/Jakarta",
    "thailand": "Asia/Bangkok",
    "th": "Asia/Bangkok",
    "vietnam": "Asia/Ho_Chi_Minh",
    "vn": "Asia/Ho_Chi_Minh",
    "south africa": "Africa/Johannesburg",
    "za": "Africa/Johannesburg",
    "nigeria": "Africa/Lagos",
    "ng": "Africa/Lagos",
    "kenya": "Africa/Nairobi",
    "ke": "Africa/Nairobi",
    "new zealand": "Pacific/Auckland",
    "nz": "Pacific/Auckland",
    "philippines": "Asia/Manila",
    "ph": "Asia/Manila",
    "malaysia": "Asia/Kuala_Lumpur",
    "my": "Asia/Kuala_Lumpur",
}

# The two allowed local-time send windows (GUARDRAILS §1.7)
_WINDOW_MORNING = (time(10, 0), time(11, 0))
_WINDOW_AFTERNOON = (time(14, 0), time(15, 0))


def country_to_tz(country: str) -> str:
    """Map a country name or code to an IANA timezone string.

    Args:
        country: Country name (e.g. "India") or ISO code (e.g. "IN").
            Case-insensitive.

    Returns:
        IANA timezone string, or "UTC" if no mapping exists.
    """
    return COUNTRY_TZ.get(country.strip().lower(), "UTC")


def next_send_window_utc(
    country: str,
    now_utc: datetime,
    *,
    _rng: random.Random | None = None,
) -> datetime:
    """Compute the next valid send time in UTC for a recipient's country.

    Picks the earliest available window (10:00-11:00 or 14:00-15:00 local),
    skipping Saturday and Sunday in the recipient's timezone. Returns a
    random minute within the chosen window to avoid robotic timing.

    Args:
        country: Recipient's country name or code.
        now_utc: Current time in UTC (must be timezone-aware).
        _rng: Optional Random instance for deterministic testing.

    Returns:
        A timezone-aware UTC datetime within the next valid send window.
    """
    rng = _rng or random.Random()
    tz = ZoneInfo(country_to_tz(country))
    now_local = now_utc.astimezone(tz)

    # Try today and the next 7 days (enough to skip a full weekend)
    for day_offset in range(8):
        candidate_date = (now_local + timedelta(days=day_offset)).date()
        candidate_dt = datetime.combine(candidate_date, time(0, 0), tzinfo=tz)

        # Skip weekends (GUARDRAILS §1.7)
        if candidate_dt.weekday() in (5, 6):  # Sat, Sun
            continue

        for window_start, window_end in (_WINDOW_MORNING, _WINDOW_AFTERNOON):
            slot_start = datetime.combine(candidate_date, window_start, tzinfo=tz)
            slot_end = datetime.combine(candidate_date, window_end, tzinfo=tz)

            # Skip windows that are already fully in the past
            if slot_end <= now_local:
                continue

            # Pick a random minute within the window
            # If we're partway through the window, start from now
            effective_start = max(slot_start, now_local)
            remaining_seconds = int((slot_end - effective_start).total_seconds())
            if remaining_seconds <= 0:
                continue

            offset_seconds = rng.randint(0, remaining_seconds)
            send_local = effective_start + timedelta(seconds=offset_seconds)
            return send_local.astimezone(timezone.utc)

    # Should never reach here (8 days covers any weekend), but be safe
    return now_utc + timedelta(hours=1)
