"""Tests for outreach/lib/gmail_pool.py.

Covers: pick_inbox selection logic, NoInboxAvailableError paths,
load_from_env parsing, and cap enforcement.

Note: InboxPool has no record_send method — it is stateless.  Send counts
are read from tracker.csv via tracker.count_sent_today_by_inbox().  Tests
mock those functions to control the counts pick_inbox sees.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from outreach.lib.gmail_pool import InboxConfig, InboxPool, NoInboxAvailableError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_inbox(address: str = "a@gmail.com", daily_cap: int = 10) -> InboxConfig:
    return InboxConfig(address=address, oauth_token_path=Path("/tokens/a.json"), daily_cap=daily_cap)


def _make_pool(inboxes: list[InboxConfig], global_cap: int = 100) -> InboxPool:
    return InboxPool(inboxes=inboxes, global_cap=global_cap)


# ---------------------------------------------------------------------------
# TestPickInbox
# ---------------------------------------------------------------------------

class TestPickInbox:
    """pick_inbox selects the lowest-count inbox that is under cap."""

    def test_returns_lowest_count_inbox(self, tmp_path):
        inbox_a = _make_inbox(address="a@gmail.com", daily_cap=10)
        inbox_b = _make_inbox(address="b@gmail.com", daily_cap=10)
        pool = _make_pool([inbox_a, inbox_b])

        counts = {inbox_a.address: 7, inbox_b.address: 2}

        with (
            patch("outreach.lib.tracker.count_sent_today", return_value=0),
            patch(
                "outreach.lib.tracker.count_sent_today_by_inbox",
                side_effect=lambda addr, path: counts[addr],
            ),
        ):
            result = pool.pick_inbox(tmp_path / "tracker.csv")

        assert result.address == inbox_b.address

    def test_raises_when_all_per_inbox_caps_met(self, tmp_path):
        inbox_a = _make_inbox(address="a@gmail.com", daily_cap=5)
        inbox_b = _make_inbox(address="b@gmail.com", daily_cap=5)
        pool = _make_pool([inbox_a, inbox_b])

        with (
            patch("outreach.lib.tracker.count_sent_today", return_value=0),
            patch("outreach.lib.tracker.count_sent_today_by_inbox", return_value=5),
        ):
            with pytest.raises(NoInboxAvailableError, match="per-inbox"):
                pool.pick_inbox(tmp_path / "tracker.csv")

    def test_raises_when_global_cap_reached(self, tmp_path):
        inbox = _make_inbox(daily_cap=25)
        pool = _make_pool([inbox], global_cap=25)

        with (
            patch("outreach.lib.tracker.count_sent_today", return_value=25) as mock_global,
            patch("outreach.lib.tracker.count_sent_today_by_inbox") as mock_by_inbox,
        ):
            with pytest.raises(NoInboxAvailableError, match="Global"):
                pool.pick_inbox(tmp_path / "tracker.csv")

        # Global cap short-circuits before iterating inboxes
        mock_by_inbox.assert_not_called()

    def test_global_cap_enforced_even_if_per_inbox_allows(self, tmp_path):
        inbox_a = _make_inbox(address="a@gmail.com", daily_cap=25)
        inbox_b = _make_inbox(address="b@gmail.com", daily_cap=25)
        pool = _make_pool([inbox_a, inbox_b], global_cap=10)

        with (
            patch("outreach.lib.tracker.count_sent_today", return_value=10),
            patch("outreach.lib.tracker.count_sent_today_by_inbox", return_value=0),
        ):
            # Per-inbox caps would allow sending, but global cap is exhausted
            with pytest.raises(NoInboxAvailableError):
                pool.pick_inbox(tmp_path / "tracker.csv")


# ---------------------------------------------------------------------------
# TestLoadFromEnv
# ---------------------------------------------------------------------------

class TestLoadFromEnv:
    """load_from_env parses .env.outreach correctly."""

    def test_loads_single_inbox(self, tmp_path):
        env_file = tmp_path / ".env.outreach"
        env_file.write_text(
            "OUTREACH_INBOX_1_ADDRESS=a@gmail.com\n"
            "OUTREACH_INBOX_1_TOKEN_PATH=/tokens/a.json\n"
            "OUTREACH_DAILY_CAP_GLOBAL=20\n"
            "OUTREACH_DAILY_CAP_PER_INBOX=10\n"
        )

        pool = InboxPool.load_from_env(env_file)

        assert len(pool.inboxes) == 1
        assert pool.inboxes[0].address == "a@gmail.com"
        assert pool.global_cap == 20
        assert pool.inboxes[0].daily_cap == 10

    def test_ignores_empty_slots(self, tmp_path):
        env_file = tmp_path / ".env.outreach"
        env_file.write_text(
            "OUTREACH_INBOX_1_ADDRESS=a@gmail.com\n"
            "OUTREACH_INBOX_1_TOKEN_PATH=/t/a.json\n"
            # slot 2: absent
            "OUTREACH_INBOX_3_ADDRESS=c@gmail.com\n"
            "OUTREACH_INBOX_3_TOKEN_PATH=/t/c.json\n"
            # slot 4: absent
            "OUTREACH_INBOX_5_ADDRESS=e@gmail.com\n"
            "OUTREACH_INBOX_5_TOKEN_PATH=/t/e.json\n"
        )

        pool = InboxPool.load_from_env(env_file)

        assert len(pool.inboxes) == 3
        assert pool.inboxes[0].address == "a@gmail.com"
        assert pool.inboxes[1].address == "c@gmail.com"
        assert pool.inboxes[2].address == "e@gmail.com"

    def test_defaults_when_caps_absent(self, tmp_path):
        env_file = tmp_path / ".env.outreach"
        env_file.write_text(
            "OUTREACH_INBOX_1_ADDRESS=a@gmail.com\n"
            "OUTREACH_INBOX_1_TOKEN_PATH=/t/a.json\n"
        )

        pool = InboxPool.load_from_env(env_file)

        assert pool.global_cap == 25
        assert pool.inboxes[0].daily_cap == 25
