"""Tests for outreach/lib/apollo.py.

Mocks the Apollo API to verify credit tracking, rate limiting, and
error handling without hitting real services.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from outreach.lib.apollo import (
    lookup_email,
    credits_used_this_month,
    _record_usage,
    _ensure_usage_file,
    _MONTHLY_CREDIT_CAP,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(status_code: int = 200, json_data: dict | None = None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = json.dumps(json_data or {})
    return resp


def _apollo_person(email: str = "priya@razorpay.com", status: str = "verified"):
    return {
        "person": {
            "email": email,
            "email_status": status,
            "name": "Priya Mehta",
            "title": "Head of Growth",
            "linkedin_url": "https://linkedin.com/in/priya",
        }
    }


# ---------------------------------------------------------------------------
# Credit tracking
# ---------------------------------------------------------------------------

class TestCreditTracking:

    def test_empty_file_returns_zero(self, tmp_path):
        usage = tmp_path / "usage.csv"
        assert credits_used_this_month(usage) == 0

    def test_record_and_read(self, tmp_path):
        usage = tmp_path / "usage.csv"
        _record_usage(1, usage)
        assert credits_used_this_month(usage) == 1
        _record_usage(1, usage)
        assert credits_used_this_month(usage) == 2

    def test_creates_file_with_header(self, tmp_path):
        usage = tmp_path / "usage.csv"
        _ensure_usage_file(usage)
        assert usage.exists()
        with open(usage) as f:
            header = f.readline().strip()
        assert header == "date,credits_used,cumulative_month"

    def test_cumulative_tracks_correctly(self, tmp_path):
        usage = tmp_path / "usage.csv"
        cumulative = _record_usage(3, usage)
        assert cumulative == 3
        cumulative = _record_usage(2, usage)
        assert cumulative == 5


# ---------------------------------------------------------------------------
# lookup_email
# ---------------------------------------------------------------------------

class TestLookupEmail:

    @patch("outreach.lib.apollo.requests.post")
    @patch.dict("os.environ", {"APOLLO_API_KEY": "test-key"})
    def test_verified_email_returns_result(self, mock_post, tmp_path):
        usage = tmp_path / "usage.csv"
        mock_post.return_value = _mock_response(200, _apollo_person())

        result = lookup_email("Priya", "Mehta", "razorpay.com", usage_path=usage)

        assert result is not None
        assert result["email"] == "priya@razorpay.com"
        assert result["email_status"] == "verified"
        assert credits_used_this_month(usage) == 1

    @patch("outreach.lib.apollo.requests.post")
    @patch.dict("os.environ", {"APOLLO_API_KEY": "test-key"})
    def test_guessed_email_returns_result(self, mock_post, tmp_path):
        usage = tmp_path / "usage.csv"
        mock_post.return_value = _mock_response(200, _apollo_person(status="guessed"))

        result = lookup_email("Priya", "Mehta", "razorpay.com", usage_path=usage)

        assert result is not None
        assert result["email_status"] == "guessed"

    @patch("outreach.lib.apollo.requests.post")
    @patch.dict("os.environ", {"APOLLO_API_KEY": "test-key"})
    def test_no_person_returns_none(self, mock_post, tmp_path):
        usage = tmp_path / "usage.csv"
        mock_post.return_value = _mock_response(200, {"person": None})

        result = lookup_email("Nobody", "Here", "example.com", usage_path=usage)

        assert result is None
        # Credit still consumed
        assert credits_used_this_month(usage) == 1

    @patch("outreach.lib.apollo.requests.post")
    @patch.dict("os.environ", {"APOLLO_API_KEY": "test-key"})
    def test_no_email_field_returns_none(self, mock_post, tmp_path):
        usage = tmp_path / "usage.csv"
        mock_post.return_value = _mock_response(200, {
            "person": {"name": "Priya", "email": "", "email_status": ""}
        })

        result = lookup_email("Priya", "Mehta", "razorpay.com", usage_path=usage)

        assert result is None

    @patch.dict("os.environ", {"APOLLO_API_KEY": ""})
    def test_missing_api_key_returns_none(self, tmp_path):
        usage = tmp_path / "usage.csv"
        result = lookup_email("Priya", "Mehta", "razorpay.com", usage_path=usage)

        assert result is None
        assert credits_used_this_month(usage) == 0

    @patch("outreach.lib.apollo.requests.post")
    @patch.dict("os.environ", {"APOLLO_API_KEY": "test-key"})
    def test_quota_exhausted_returns_none(self, mock_post, tmp_path):
        usage = tmp_path / "usage.csv"
        # Pre-fill usage to cap
        for _ in range(_MONTHLY_CREDIT_CAP):
            _record_usage(1, usage)

        result = lookup_email("Priya", "Mehta", "razorpay.com", usage_path=usage)

        assert result is None
        mock_post.assert_not_called()

    @patch("outreach.lib.apollo.requests.post")
    @patch.dict("os.environ", {"APOLLO_API_KEY": "test-key"})
    def test_401_returns_none(self, mock_post, tmp_path):
        usage = tmp_path / "usage.csv"
        mock_post.return_value = _mock_response(401, {"error": "unauthorized"})

        result = lookup_email("Priya", "Mehta", "razorpay.com", usage_path=usage)

        assert result is None

    @patch("outreach.lib.apollo.requests.post")
    @patch.dict("os.environ", {"APOLLO_API_KEY": "test-key"})
    def test_429_returns_none(self, mock_post, tmp_path):
        usage = tmp_path / "usage.csv"
        mock_post.return_value = _mock_response(429, {})

        result = lookup_email("Priya", "Mehta", "razorpay.com", usage_path=usage)

        assert result is None

    @patch("outreach.lib.apollo.requests.post")
    @patch.dict("os.environ", {"APOLLO_API_KEY": "test-key"})
    def test_request_exception_returns_none(self, mock_post, tmp_path):
        import requests as req
        usage = tmp_path / "usage.csv"
        mock_post.side_effect = req.ConnectionError("connection error")

        result = lookup_email("Priya", "Mehta", "razorpay.com", usage_path=usage)

        assert result is None
        # No credit consumed on network error
        assert credits_used_this_month(usage) == 0
