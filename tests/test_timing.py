"""Tests for outreach/lib/timing.py.

Covers: same-day morning slot, next-day rollover, weekend skip to Monday,
and unknown-country UTC fallback.
"""

from __future__ import annotations

import random
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from outreach.lib.timing import country_to_tz, next_send_window_utc


# Fixed RNG so tests are deterministic
_RNG = random.Random(42)


class TestCountryToTz:
    """country_to_tz should resolve known countries and fall back to UTC."""

    def test_india_lowercase(self):
        assert country_to_tz("india") == "Asia/Kolkata"

    def test_code_case_insensitive(self):
        assert country_to_tz("IN") == "Asia/Kolkata"

    def test_unknown_country_returns_utc(self):
        assert country_to_tz("Narnia") == "UTC"


class TestNextSendWindowUtc:
    """next_send_window_utc must respect windows, weekends, and timezone."""

    def test_9am_ist_monday_gets_same_day_morning(self):
        """9:00 IST Monday → should land in the 10:00-11:00 IST window same day."""
        # 2026-04-27 is a Monday
        # 09:00 IST = 03:30 UTC
        now_utc = datetime(2026, 4, 27, 3, 30, tzinfo=timezone.utc)
        result = next_send_window_utc("India", now_utc, _rng=_RNG)

        # Convert result to IST and check it's in the 10:00-11:00 window
        ist = ZoneInfo("Asia/Kolkata")
        result_local = result.astimezone(ist)
        assert result_local.date() == now_utc.astimezone(ist).date()
        assert 10 <= result_local.hour < 11

    def test_11pm_ist_wednesday_gets_next_day_morning(self):
        """23:00 IST Wednesday → both windows passed, should get Thursday 10am."""
        # 2026-04-29 is a Wednesday
        # 23:00 IST = 17:30 UTC
        now_utc = datetime(2026, 4, 29, 17, 30, tzinfo=timezone.utc)
        result = next_send_window_utc("India", now_utc, _rng=_RNG)

        ist = ZoneInfo("Asia/Kolkata")
        result_local = result.astimezone(ist)
        # Should be Thursday
        assert result_local.weekday() == 3  # Thursday
        assert 10 <= result_local.hour < 11

    def test_saturday_skips_to_monday(self):
        """Saturday 10:00 IST → should skip to Monday 10:00-11:00."""
        # 2026-05-02 is a Saturday
        # 10:00 IST = 04:30 UTC
        now_utc = datetime(2026, 5, 2, 4, 30, tzinfo=timezone.utc)
        result = next_send_window_utc("India", now_utc, _rng=_RNG)

        ist = ZoneInfo("Asia/Kolkata")
        result_local = result.astimezone(ist)
        # Should be Monday 2026-05-04
        assert result_local.weekday() == 0  # Monday
        assert result_local.day == 4
        assert 10 <= result_local.hour < 11

    def test_unknown_country_uses_utc(self):
        """Unknown country → UTC timezone, still picks a valid window."""
        # 2026-04-27 is a Monday, 08:00 UTC
        now_utc = datetime(2026, 4, 27, 8, 0, tzinfo=timezone.utc)
        result = next_send_window_utc("Narnia", now_utc, _rng=_RNG)

        # In UTC, 10:00-11:00 is the next window
        result_utc = result.astimezone(timezone.utc)
        assert result_utc.date() == now_utc.date()
        assert 10 <= result_utc.hour < 11
