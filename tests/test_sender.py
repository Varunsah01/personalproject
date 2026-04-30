"""Tests for outreach/lib/sender.py.

Covers: _is_generic_alias, send_one (happy/sad paths), tick (STOP file,
daily cap, scheduling, dry-run), and the structured HttpError handler
introduced in Part C (_is_hard_bounce, _is_auth_error, suppression on
hard bounce).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from googleapiclient.errors import HttpError

from outreach.lib.gmail_pool import InboxConfig
from outreach.lib.sender import (
    _GLOBAL_DAILY_CAP,
    _is_generic_alias,
    send_one,
    send_one_followup,
    tick,
)
from outreach.lib.tracker import Row


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_row(**kw) -> Row:
    defaults = dict(
        id="uuid-0001",
        company="Acme",
        person_name="Jane",
        email="hiring@acme.com",
        person_linkedin="",
        subject="Founding operator",
        body_path="",
        person_country="India",
        send_at_utc="",
        status="queued",
    )
    defaults.update(kw)
    return Row(**defaults)


def _make_inbox(address: str = "out@gmail.com") -> InboxConfig:
    return InboxConfig(address=address, oauth_token_path=Path("/tokens/out.json"), daily_cap=25)


def _http_error(status: int, body: bytes) -> HttpError:
    resp = MagicMock()
    resp.status = status
    resp.reason = "Error"
    return HttpError(resp, body)


def _mock_service():
    """Return a fully-wired mock Gmail service."""
    svc = MagicMock()
    svc.users.return_value.messages.return_value.send.return_value.execute.return_value = {
        "id": "msg-123"
    }
    return svc


# ---------------------------------------------------------------------------
# TestIsGenericAlias
# ---------------------------------------------------------------------------

class TestIsGenericAlias:
    """_is_generic_alias must block all banned prefixes, case-insensitively."""

    @pytest.mark.parametrize("email", [
        "info@company.com",
        "hello@co.io",
        "careers@startup.com",
        "support@saas.io",
        "contact@firm.co",
    ])
    def test_banned_prefixes_blocked(self, email):
        assert _is_generic_alias(email) is True

    @pytest.mark.parametrize("email", [
        "john@acme.com",
        "priya@startup.io",
        "founder@co.com",
    ])
    def test_normal_email_allowed(self, email):
        assert _is_generic_alias(email) is False

    @pytest.mark.parametrize("email", [
        "INFO@COMPANY.COM",
        "Careers@Corp.com",
        "HELLO@EXAMPLE.IO",
    ])
    def test_case_insensitive(self, email):
        assert _is_generic_alias(email) is True


# ---------------------------------------------------------------------------
# TestSendOne
# ---------------------------------------------------------------------------

class TestSendOne:
    """send_one: pre-flight guards and happy-path success."""

    def test_generic_alias_returns_none_no_api_call(self):
        row = _make_row(email="info@company.com")
        inbox = _make_inbox()

        with patch("outreach.lib.sender._load_credentials") as mock_creds:
            result = send_one(row, inbox)

        assert result is None
        mock_creds.assert_not_called()

    def test_blank_email_returns_none(self):
        row = _make_row(email="   ")
        inbox = _make_inbox()

        with patch("outreach.lib.sender._load_credentials") as mock_creds:
            result = send_one(row, inbox)

        assert result is None
        mock_creds.assert_not_called()

    def test_missing_draft_returns_none(self, tmp_path):
        row = _make_row(body_path=str(tmp_path / "missing.md"))
        inbox = _make_inbox()

        with patch("outreach.lib.sender._load_credentials") as mock_creds:
            result = send_one(row, inbox)

        assert result is None
        mock_creds.assert_not_called()

    def test_success_returns_message_id_and_archives(self, tmp_path):
        draft = tmp_path / "draft.md"
        draft.write_text("Hello, I'm reaching out about your seed raise.", encoding="utf-8")
        row = _make_row(body_path=str(draft))
        inbox = _make_inbox()

        with (
            patch("outreach.lib.sender._load_credentials", return_value=MagicMock()),
            patch("googleapiclient.discovery.build", return_value=_mock_service()),
            patch("outreach.lib.sender._archive_eml") as mock_archive,
        ):
            result = send_one(row, inbox)

        assert isinstance(result, str)
        assert result.startswith("<") and result.endswith(">")
        mock_archive.assert_called_once_with(
            row.id, row.subject, inbox.address, row.email,
            "Hello, I'm reaching out about your seed raise.",
        )

    def test_api_exception_returns_none(self, tmp_path):
        draft = tmp_path / "draft.md"
        draft.write_text("body", encoding="utf-8")
        row = _make_row(body_path=str(draft))
        inbox = _make_inbox()

        svc = _mock_service()
        svc.users.return_value.messages.return_value.send.return_value.execute.side_effect = (
            Exception("network error")
        )

        with (
            patch("outreach.lib.sender._load_credentials", return_value=MagicMock()),
            patch("googleapiclient.discovery.build", return_value=svc),
            patch("outreach.lib.sender._archive_eml") as mock_archive,
        ):
            result = send_one(row, inbox)

        assert result is None
        mock_archive.assert_not_called()


# ---------------------------------------------------------------------------
# TestSendOneErrorHandling (Part C branches)
# ---------------------------------------------------------------------------

class TestSendOneErrorHandling:
    """send_one: structured HttpError handling — bounce, auth, transient."""

    def _run(self, tmp_path, row, inbox, http_exc):
        """Helper: write a draft, configure the API to raise http_exc, call send_one."""
        draft = tmp_path / "draft.md"
        draft.write_text("body", encoding="utf-8")
        row.body_path = str(draft)

        svc = _mock_service()
        svc.users.return_value.messages.return_value.send.return_value.execute.side_effect = (
            http_exc
        )

        with (
            patch("outreach.lib.sender._load_credentials", return_value=MagicMock()),
            patch("googleapiclient.discovery.build", return_value=svc),
            patch("outreach.lib.sender._archive_eml"),
            patch("outreach.lib.tracker.add_to_suppression") as mock_suppress,
        ):
            result = send_one(row, inbox)

        return result, mock_suppress

    def test_hard_bounce_suppresses_and_returns_none(self, tmp_path):
        row = _make_row(email="ghost@example.com")
        inbox = _make_inbox()
        exc = _http_error(400, b'{"error":{"message":"550 5.1.1 user not found"}}')

        result, mock_suppress = self._run(tmp_path, row, inbox, exc)

        assert result is None
        mock_suppress.assert_called_once()
        call_kwargs = mock_suppress.call_args.kwargs
        assert call_kwargs["email"] == "ghost@example.com"
        assert call_kwargs["reason"].startswith("hard_bounce")

    def test_400_without_bounce_signal_no_suppression(self, tmp_path):
        row = _make_row()
        inbox = _make_inbox()
        exc = _http_error(400, b'{"error":{"message":"Message too large"}}')

        result, mock_suppress = self._run(tmp_path, row, inbox, exc)

        assert result is None
        mock_suppress.assert_not_called()

    def test_auth_error_401_no_suppression(self, tmp_path):
        row = _make_row()
        inbox = _make_inbox()
        exc = _http_error(401, b'{"error":"invalid_grant"}')

        result, mock_suppress = self._run(tmp_path, row, inbox, exc)

        assert result is None
        mock_suppress.assert_not_called()

    def test_transient_429_no_suppression(self, tmp_path):
        row = _make_row()
        inbox = _make_inbox()
        exc = _http_error(429, b'{"error":"rateLimitExceeded"}')

        result, mock_suppress = self._run(tmp_path, row, inbox, exc)

        assert result is None
        mock_suppress.assert_not_called()

    def test_transient_503_no_suppression(self, tmp_path):
        row = _make_row()
        inbox = _make_inbox()
        exc = _http_error(503, b'{"error":"backendError"}')

        result, mock_suppress = self._run(tmp_path, row, inbox, exc)

        assert result is None
        mock_suppress.assert_not_called()


# ---------------------------------------------------------------------------
# TestTick
# ---------------------------------------------------------------------------

# Common patch targets used across tick tests
_PATCH_GLOBAL = "outreach.lib.tracker.count_sent_today"
_PATCH_BY_STATUS = "outreach.lib.tracker.read_by_status"
_PATCH_UPSERT = "outreach.lib.tracker.upsert"
_PATCH_MARK_SENT = "outreach.lib.tracker.mark_sent"
_PATCH_COOLDOWN = "outreach.lib.tracker.is_in_cooldown"
_PATCH_SUPPRESSED = "outreach.lib.tracker.is_suppressed"
_PATCH_POOL = "outreach.lib.gmail_pool.InboxPool.load_from_env"
_PATCH_SEND_ONE = "outreach.lib.sender.send_one"
_PATCH_NEXT_WINDOW = "outreach.lib.sender.next_send_window_utc"


def _mock_pool():
    pool = MagicMock()
    pool.pick_inbox.return_value = _make_inbox()
    return pool


class TestTick:
    """tick(): STOP file, global cap, scheduling, and dry-run."""

    def test_stop_file_exits_immediately(self, tmp_path):
        stop_path = tmp_path / "STOP"
        stop_path.write_text("")

        with (
            patch(_PATCH_GLOBAL) as mock_global,
            patch(_PATCH_POOL) as mock_pool_cls,
        ):
            tick(
                stop_path=stop_path,
                tracker_path=tmp_path / "tracker.csv",
                env_path=tmp_path / ".env",
            )

        mock_global.assert_not_called()
        mock_pool_cls.assert_not_called()

    def test_global_cap_exits_immediately(self, tmp_path):
        row = _make_row(send_at_utc=(datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat())

        with (
            patch(_PATCH_GLOBAL, return_value=_GLOBAL_DAILY_CAP),
            patch(_PATCH_BY_STATUS, return_value=[row]),
            patch(_PATCH_POOL) as mock_pool_cls,
            patch(_PATCH_MARK_SENT) as mock_mark,
        ):
            tick(
                stop_path=tmp_path / "STOP",
                tracker_path=tmp_path / "tracker.csv",
                env_path=tmp_path / ".env",
            )

        mock_pool_cls.assert_not_called()
        mock_mark.assert_not_called()

    def test_schedules_send_at_utc_no_send(self, tmp_path):
        row = _make_row(send_at_utc="", person_country="India")
        future_time = datetime.now(timezone.utc) + timedelta(hours=2)

        with (
            patch(_PATCH_GLOBAL, return_value=0),
            patch(_PATCH_BY_STATUS, return_value=[row]),
            patch(_PATCH_NEXT_WINDOW, return_value=future_time),
            patch(_PATCH_UPSERT) as mock_upsert,
            patch(_PATCH_SEND_ONE) as mock_send,
            patch(_PATCH_MARK_SENT),
            patch(_PATCH_COOLDOWN, return_value=False),
            patch(_PATCH_SUPPRESSED, return_value=False),
            patch(_PATCH_POOL, return_value=_mock_pool()),
        ):
            tick(
                stop_path=tmp_path / "STOP",
                tracker_path=tmp_path / "tracker.csv",
                env_path=tmp_path / ".env",
            )

        # send_at_utc was written back
        mock_upsert.assert_called_once()
        # No send because the scheduled time is in the future
        mock_send.assert_not_called()

    def test_dry_run_never_calls_send_one(self, tmp_path):
        # Row with send_at_utc 5 min ago — inside the 30-min window
        send_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        row = _make_row(send_at_utc=send_at)

        with (
            patch(_PATCH_GLOBAL, return_value=0),
            patch(_PATCH_BY_STATUS, return_value=[row]),
            patch(_PATCH_SEND_ONE) as mock_send,
            patch(_PATCH_MARK_SENT) as mock_mark,
            patch(_PATCH_UPSERT),
            patch(_PATCH_COOLDOWN, return_value=False),
            patch(_PATCH_SUPPRESSED, return_value=False),
            patch(_PATCH_POOL, return_value=_mock_pool()),
        ):
            tick(
                dry_run=True,
                stop_path=tmp_path / "STOP",
                tracker_path=tmp_path / "tracker.csv",
                env_path=tmp_path / ".env",
            )

        mock_send.assert_not_called()
        mock_mark.assert_not_called()


# ---------------------------------------------------------------------------
# TestSendOneFollowup
# ---------------------------------------------------------------------------

class TestSendOneFollowup:
    """send_one_followup: In-Reply-To threading and subject prefix."""

    def test_success_sets_in_reply_to_and_returns_msg_id(self, tmp_path):
        draft = tmp_path / "followup.md"
        draft.write_text("Wanted to bring this back to the top of your inbox.", encoding="utf-8")
        row = _make_row(
            body_path=str(draft),
            message_id="<original-123@gmail.com>",
            status="follow_up_queued",
        )
        inbox = _make_inbox()

        sent_raw = {}

        def capture_send(userId, body):
            sent_raw.update(body)
            mock_result = MagicMock()
            mock_result.execute.return_value = {"id": "msg-456"}
            return mock_result

        svc = MagicMock()
        svc.users.return_value.messages.return_value.send.side_effect = capture_send

        with (
            patch("outreach.lib.sender._load_credentials", return_value=MagicMock()),
            patch("googleapiclient.discovery.build", return_value=svc),
            patch("outreach.lib.sender._archive_eml"),
        ):
            result = send_one_followup(row, inbox)

        assert isinstance(result, str)
        assert result.startswith("<") and result.endswith(">")

        # Decode the sent message to verify headers
        import base64
        from email import message_from_bytes

        raw_bytes = base64.urlsafe_b64decode(sent_raw["raw"])
        msg = message_from_bytes(raw_bytes)
        assert msg["In-Reply-To"] == "<original-123@gmail.com>"
        assert msg["References"] == "<original-123@gmail.com>"
        assert msg["Subject"] == "Re: Founding operator"

    def test_no_in_reply_to_when_message_id_empty(self, tmp_path):
        draft = tmp_path / "followup.md"
        draft.write_text("Following up.", encoding="utf-8")
        row = _make_row(
            body_path=str(draft),
            message_id="",
            status="follow_up_queued",
        )
        inbox = _make_inbox()

        sent_raw = {}

        def capture_send(userId, body):
            sent_raw.update(body)
            mock_result = MagicMock()
            mock_result.execute.return_value = {"id": "msg-789"}
            return mock_result

        svc = MagicMock()
        svc.users.return_value.messages.return_value.send.side_effect = capture_send

        with (
            patch("outreach.lib.sender._load_credentials", return_value=MagicMock()),
            patch("googleapiclient.discovery.build", return_value=svc),
            patch("outreach.lib.sender._archive_eml"),
        ):
            result = send_one_followup(row, inbox)

        assert isinstance(result, str)

        import base64
        from email import message_from_bytes

        raw_bytes = base64.urlsafe_b64decode(sent_raw["raw"])
        msg = message_from_bytes(raw_bytes)
        assert msg["In-Reply-To"] is None
        assert msg["References"] is None

    def test_generic_alias_returns_none(self):
        row = _make_row(email="info@company.com", status="follow_up_queued")
        inbox = _make_inbox()

        result = send_one_followup(row, inbox)

        assert result is None

    def test_archives_with_followup_suffix(self, tmp_path):
        draft = tmp_path / "followup.md"
        draft.write_text("bump", encoding="utf-8")
        row = _make_row(
            body_path=str(draft),
            message_id="<orig@gmail.com>",
            status="follow_up_queued",
        )
        inbox = _make_inbox()

        with (
            patch("outreach.lib.sender._load_credentials", return_value=MagicMock()),
            patch("googleapiclient.discovery.build", return_value=_mock_service()),
            patch("outreach.lib.sender._archive_eml") as mock_archive,
        ):
            send_one_followup(row, inbox)

        mock_archive.assert_called_once()
        call_args = mock_archive.call_args
        assert call_args[0][0] == "uuid-0001_followup"
