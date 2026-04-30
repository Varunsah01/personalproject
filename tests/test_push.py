"""Tests for outreach/lib/push.py.

Mocks requests.post to verify ntfy.sh integration without hitting
real services.
"""

from __future__ import annotations

from unittest.mock import patch, MagicMock

from outreach.lib.push import notify


def _mock_response(status_code: int = 200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        import requests
        resp.raise_for_status.side_effect = requests.HTTPError(
            response=resp
        )
    return resp


class TestNotify:

    @patch("outreach.lib.push.requests.post")
    def test_sends_when_topic_set(self, mock_post, monkeypatch):
        monkeypatch.setenv("NTFY_TOPIC", "test-topic")
        mock_post.return_value = _mock_response(200)

        result = notify("title", "body")

        assert result is True
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert "test-topic" in call_args[0][0]
        assert call_args[1]["data"] == b"body"

    @patch("outreach.lib.push.requests.post")
    def test_noop_when_topic_unset(self, mock_post, monkeypatch):
        monkeypatch.delenv("NTFY_TOPIC", raising=False)

        result = notify("title", "body")

        assert result is False
        mock_post.assert_not_called()

    @patch("outreach.lib.push.requests.post")
    def test_returns_false_on_network_error(self, mock_post, monkeypatch):
        import requests as req
        monkeypatch.setenv("NTFY_TOPIC", "test-topic")
        mock_post.side_effect = req.ConnectionError("connection refused")

        result = notify("title", "body")

        assert result is False

    @patch("outreach.lib.push.requests.post")
    def test_priority_and_tags_headers(self, mock_post, monkeypatch):
        monkeypatch.setenv("NTFY_TOPIC", "test-topic")
        mock_post.return_value = _mock_response(200)

        notify("title", "body", priority="high", tags="incoming_envelope")

        headers = mock_post.call_args[1]["headers"]
        assert headers["Priority"] == "high"
        assert headers["Tags"] == "incoming_envelope"
        assert headers["Title"] == "title"

    @patch("outreach.lib.push.requests.post")
    def test_empty_tags_not_in_headers(self, mock_post, monkeypatch):
        monkeypatch.setenv("NTFY_TOPIC", "test-topic")
        mock_post.return_value = _mock_response(200)

        notify("title", "body", tags="")

        headers = mock_post.call_args[1]["headers"]
        assert "Tags" not in headers
