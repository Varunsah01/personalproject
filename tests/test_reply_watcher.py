"""Tests for outreach/lib/reply_watcher.py.

Mocks the Gmail API to verify reply detection and tracker updates without
hitting real Google services.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from outreach.lib.tracker import Row, read_all, upsert, update_status, mark_sent
from outreach.lib.gmail_pool import InboxConfig, InboxPool
from outreach.lib.reply_watcher import tick, _find_replies


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_row(**kw) -> Row:
    defaults = dict(
        company="Acme",
        person_name="Jane",
        email="hiring@acme.com",
        subject="Founding operator",
        body_path="",
        person_country="India",
        status="sent",
        assigned_inbox="out@gmail.com",
        sent_at_utc="2026-04-28T10:00:00Z",
    )
    defaults.update(kw)
    return Row(**defaults)


def _make_inbox(address: str = "out@gmail.com") -> InboxConfig:
    return InboxConfig(
        address=address,
        oauth_token_path=Path("/tokens/out.json"),
        daily_cap=25,
    )


def _mock_service_with_reply(reply_email: str):
    """Gmail service that returns a 2-message thread (outbound + reply)."""
    svc = MagicMock()

    # messages().list() returns one message
    svc.users.return_value.messages.return_value.list.return_value.execute.return_value = {
        "messages": [{"id": "msg-001", "threadId": "thread-001"}],
    }

    # threads().get() returns 2 messages: our outbound + their reply
    svc.users.return_value.threads.return_value.get.return_value.execute.return_value = {
        "messages": [
            {
                "id": "msg-001",
                "payload": {
                    "headers": [
                        {"name": "From", "value": "out@gmail.com"},
                        {"name": "To", "value": reply_email},
                    ],
                },
            },
            {
                "id": "msg-002",
                "payload": {
                    "headers": [
                        {"name": "From", "value": reply_email},
                        {"name": "To", "value": "out@gmail.com"},
                    ],
                },
            },
        ],
    }

    return svc


def _mock_service_no_reply():
    """Gmail service that returns a 1-message thread (no reply)."""
    svc = MagicMock()

    svc.users.return_value.messages.return_value.list.return_value.execute.return_value = {
        "messages": [{"id": "msg-001", "threadId": "thread-001"}],
    }

    svc.users.return_value.threads.return_value.get.return_value.execute.return_value = {
        "messages": [
            {
                "id": "msg-001",
                "payload": {
                    "headers": [
                        {"name": "From", "value": "out@gmail.com"},
                        {"name": "To", "value": "hiring@acme.com"},
                    ],
                },
            },
        ],
    }

    return svc


def _mock_service_no_results():
    """Gmail service that returns no matching messages."""
    svc = MagicMock()
    svc.users.return_value.messages.return_value.list.return_value.execute.return_value = {
        "messages": [],
    }
    return svc


def _mock_pool(address: str = "out@gmail.com") -> InboxPool:
    return InboxPool(
        inboxes=[_make_inbox(address)],
        global_cap=25,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def seeded_tracker(tmp_path) -> tuple[Path, list[str]]:
    """Create a tracker with 3 sent rows. Returns (csv_path, [id1, id2, id3])."""
    csv_path = tmp_path / "tracker.csv"

    # Insert rows at research_done (the initial valid state for upsert)
    rows = [
        Row(company="Alpha", person_name="Alice", email="alice@alpha.com",
            subject="Founding operator", assigned_inbox="out@gmail.com",
            status="research_done"),
        Row(company="Beta", person_name="Bob", email="bob@beta.com",
            subject="Growth lead", assigned_inbox="out@gmail.com",
            status="research_done"),
        Row(company="Gamma", person_name="Carol", email="carol@gamma.com",
            subject="Head of product", assigned_inbox="out@gmail.com",
            status="research_done"),
    ]
    for r in rows:
        upsert(r, csv_path)

    ids = [r.id for r in read_all(csv_path)]

    # Walk each row through the state machine to 'sent'
    for rid in ids:
        update_status(rid, "people_found", csv_path)
        update_status(rid, "contact_found", csv_path)
        update_status(rid, "drafted", csv_path)
        update_status(rid, "queued", csv_path)
        mark_sent(rid, "out@gmail.com", csv_path)

    return csv_path, ids


# ---------------------------------------------------------------------------
# Tests: _find_replies
# ---------------------------------------------------------------------------

class TestFindReplies:
    """Unit tests for the Gmail reply-matching logic."""

    def test_detects_reply_from_recipient(self):
        svc = _mock_service_with_reply("hiring@acme.com")
        rows = [_make_row(email="hiring@acme.com")]

        replied = _find_replies(svc, rows)

        assert len(replied) == 1
        assert replied[0].email == "hiring@acme.com"

    def test_no_reply_returns_empty(self):
        svc = _mock_service_no_reply()
        rows = [_make_row(email="hiring@acme.com")]

        replied = _find_replies(svc, rows)

        assert replied == []

    def test_no_matching_messages_returns_empty(self):
        svc = _mock_service_no_results()
        rows = [_make_row(email="hiring@acme.com")]

        replied = _find_replies(svc, rows)

        assert replied == []

    def test_skips_rows_without_email(self):
        svc = _mock_service_with_reply("")
        rows = [_make_row(email="")]

        replied = _find_replies(svc, rows)

        assert replied == []

    def test_skips_rows_without_subject(self):
        svc = _mock_service_with_reply("hiring@acme.com")
        rows = [_make_row(subject="")]

        replied = _find_replies(svc, rows)

        assert replied == []


# ---------------------------------------------------------------------------
# Tests: tick() integration
# ---------------------------------------------------------------------------

class TestTickIntegration:
    """End-to-end tick() tests with mocked Gmail and real tracker CSV."""

    def test_reply_advances_row_to_replied(self, seeded_tracker, tmp_path):
        csv_path, ids = seeded_tracker
        stop_path = tmp_path / "STOP"
        env_path = tmp_path / ".env.outreach"
        env_path.write_text("OUTREACH_INBOX_1_ADDRESS=out@gmail.com\n"
                            "OUTREACH_INBOX_1_TOKEN_PATH=/tokens/out.json\n")

        # Only alice@alpha.com gets a reply
        def mock_find_replies(service, rows):
            return [r for r in rows if r.email == "alice@alpha.com"]

        with (
            patch("outreach.lib.reply_watcher._build_service", return_value=MagicMock()),
            patch("outreach.lib.reply_watcher._find_replies", side_effect=mock_find_replies),
            patch("outreach.lib.reply_watcher.InboxPool.load_from_env",
                  return_value=_mock_pool()),
        ):
            tick(tracker_path=csv_path, env_path=env_path, stop_path=stop_path)

        # Read back and check
        all_rows = {r.id: r for r in read_all(csv_path)}

        # Alice's row should be replied
        assert all_rows[ids[0]].status == "replied"
        assert all_rows[ids[0]].replied == "true"
        assert "replied_at_utc=" in all_rows[ids[0]].notes

        # Bob and Carol stay at sent
        assert all_rows[ids[1]].status == "sent"
        assert all_rows[ids[2]].status == "sent"

    def test_dry_run_does_not_modify_tracker(self, seeded_tracker, tmp_path):
        csv_path, ids = seeded_tracker
        stop_path = tmp_path / "STOP"
        env_path = tmp_path / ".env.outreach"
        env_path.write_text("OUTREACH_INBOX_1_ADDRESS=out@gmail.com\n"
                            "OUTREACH_INBOX_1_TOKEN_PATH=/tokens/out.json\n")

        def mock_find_replies(service, rows):
            return rows  # all replied

        with (
            patch("outreach.lib.reply_watcher._build_service", return_value=MagicMock()),
            patch("outreach.lib.reply_watcher._find_replies", side_effect=mock_find_replies),
            patch("outreach.lib.reply_watcher.InboxPool.load_from_env",
                  return_value=_mock_pool()),
        ):
            tick(dry_run=True, tracker_path=csv_path, env_path=env_path, stop_path=stop_path)

        # All rows should remain at sent
        for row in read_all(csv_path):
            assert row.status == "sent"
            assert row.replied != "true"

    def test_stop_file_halts_immediately(self, seeded_tracker, tmp_path):
        csv_path, ids = seeded_tracker
        stop_path = tmp_path / "STOP"
        stop_path.write_text("")

        with patch("outreach.lib.reply_watcher._build_service") as mock_build:
            tick(tracker_path=csv_path, stop_path=stop_path)

        # Should never even try to connect
        mock_build.assert_not_called()

    def test_no_unreplied_rows_exits_early(self, tmp_path):
        csv_path = tmp_path / "tracker.csv"
        # Create a row at research_done, walk to sent, then set replied='true'
        row = Row(company="Done", person_name="Dan", email="dan@done.com",
                  subject="Test", assigned_inbox="out@gmail.com",
                  status="research_done")
        upsert(row, csv_path)
        rid = read_all(csv_path)[0].id
        update_status(rid, "people_found", csv_path)
        update_status(rid, "contact_found", csv_path)
        update_status(rid, "drafted", csv_path)
        update_status(rid, "queued", csv_path)
        mark_sent(rid, "out@gmail.com", csv_path)
        # Manually set replied field
        from outreach.lib.reply_watcher import _set_replied_flag
        _set_replied_flag(rid, csv_path)

        stop_path = tmp_path / "STOP"

        with patch("outreach.lib.reply_watcher._build_service") as mock_build:
            tick(tracker_path=csv_path, stop_path=stop_path)

        mock_build.assert_not_called()
