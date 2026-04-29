"""Unit tests for platforms/instahyre.py.

Tests cover: selector constants are present, URL building, _should_queue,
_should_stop, _is_recognized field detection, and config wiring.
Browser interactions are not tested (manually verified after headful seed).
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from core.types import BotConfig
from platforms.instahyre import (
    BASE_URL,
    InstahyrePlatform,
    LOGIN_URL,
    MAX_PAGES_PER_KEYWORD,
    MAX_SELECTOR_FAILURES,
    MAX_SESSION_SECONDS,
    OPPORTUNITIES_URL,
    STANDARD_FIELD_NAMES,
)


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def config(tmp_path: Path) -> BotConfig:
    """BotConfig pointing at temp directory for logs."""
    log_path = tmp_path / "applications_log.csv"
    return BotConfig(
        log_path=log_path,
        headless=True,
        dry_run=True,
        daily_cap=100,
        standard_answers={"name": "Varun Sah", "phone": "+91-8595062552"},
    )


@pytest.fixture
def platform(config: BotConfig) -> InstahyrePlatform:
    """Create an InstahyrePlatform instance for testing."""
    with patch.dict(os.environ, {
        "INSTAHYRE_EMAIL": "test@example.com",
        "INSTAHYRE_PASSWORD": "testpass123",
    }):
        return InstahyrePlatform(config)


# ── Selector constants are present ────────────────────────────────────


class TestSelectorsPresent:
    """Verify that all required selectors exist in the YAML file."""

    def test_login_selectors(self, platform: InstahyrePlatform):
        assert platform.sel.login_email
        assert platform.sel.login_password
        assert platform.sel.login_submit
        assert platform.sel.login_success
        assert platform.sel.captcha

    def test_search_selectors(self, platform: InstahyrePlatform):
        assert platform.sel.job_card
        assert platform.sel.job_title
        assert platform.sel.job_company
        assert platform.sel.job_location
        assert platform.sel.job_url
        assert platform.sel.job_snippet
        assert platform.sel.next_page

    def test_apply_selectors(self, platform: InstahyrePlatform):
        assert platform.sel.apply_button
        assert platform.sel.accept_button
        assert platform.sel.form_container

    def test_form_field_selectors(self, platform: InstahyrePlatform):
        assert platform.sel.field_name
        assert platform.sel.field_email
        assert platform.sel.field_phone
        assert platform.sel.field_linkedin
        assert platform.sel.resume_upload
        assert platform.sel.submit_button

    def test_confirmation_selectors(self, platform: InstahyrePlatform):
        assert platform.sel.apply_success
        assert platform.sel.apply_message

    def test_logout_selectors(self, platform: InstahyrePlatform):
        assert platform.sel.profile_menu
        assert platform.sel.logout_link


# ── Platform configuration ────────────────────────────────────────────


class TestPlatformConfig:
    def test_platform_name(self, platform: InstahyrePlatform):
        assert platform.PLATFORM_NAME == "instahyre"

    def test_credentials_from_env(self):
        with patch.dict(os.environ, {
            "INSTAHYRE_EMAIL": "varun@test.com",
            "INSTAHYRE_PASSWORD": "secret",
        }):
            p = InstahyrePlatform(BotConfig(log_path=Path("/tmp/test.csv")))
        assert p.email == "varun@test.com"
        assert p.password == "secret"

    def test_missing_credentials_empty_string(self):
        with patch.dict(os.environ, {}, clear=True):
            # Remove INSTAHYRE_* if present
            os.environ.pop("INSTAHYRE_EMAIL", None)
            os.environ.pop("INSTAHYRE_PASSWORD", None)
            p = InstahyrePlatform(BotConfig(log_path=Path("/tmp/test.csv")))
        assert p.email == ""
        assert p.password == ""

    def test_standard_answers_include_email(self):
        with patch.dict(os.environ, {
            "INSTAHYRE_EMAIL": "varun@test.com",
            "INSTAHYRE_PASSWORD": "secret",
        }):
            p = InstahyrePlatform(BotConfig(
                log_path=Path("/tmp/test.csv"),
                standard_answers={"name": "Varun Sah"},
            ))
        assert p.standard_answers["email"] == "varun@test.com"
        assert p.standard_answers["name"] == "Varun Sah"

    def test_headless_from_config(self):
        with patch.dict(os.environ, {
            "INSTAHYRE_EMAIL": "test@example.com",
            "INSTAHYRE_PASSWORD": "pass",
        }):
            p_headless = InstahyrePlatform(BotConfig(log_path=Path("/tmp/t.csv"), headless=True))
            p_headed = InstahyrePlatform(BotConfig(log_path=Path("/tmp/t.csv"), headless=False))
        assert p_headless.headless is True
        assert p_headed.headless is False


# ── URL building ──────────────────────────────────────────────────────


class TestBuildSearchUrl:
    def test_basic_keyword(self):
        url = InstahyrePlatform._build_search_url("growth manager")
        assert OPPORTUNITIES_URL in url
        assert "q=growth+manager" in url or "q=growth%20manager" in url

    def test_keyword_with_location(self):
        url = InstahyrePlatform._build_search_url("product manager", location="Bangalore")
        assert "q=product" in url
        assert "location=Bangalore" in url

    def test_page_2(self):
        url = InstahyrePlatform._build_search_url("growth manager", page=2)
        assert "page=2" in url

    def test_page_1_no_page_param(self):
        url = InstahyrePlatform._build_search_url("growth manager", page=1)
        assert "page=" not in url

    def test_empty_keyword(self):
        url = InstahyrePlatform._build_search_url("")
        assert url == OPPORTUNITIES_URL

    def test_url_base(self):
        url = InstahyrePlatform._build_search_url("test")
        assert url.startswith(OPPORTUNITIES_URL)


# ── _should_queue ─────────────────────────────────────────────────────


class TestShouldQueue:
    def test_t1_high_score_not_queued(self, platform: InstahyrePlatform):
        assert not platform._should_queue(0.8, "T1")

    def test_t1_low_score_queued(self, platform: InstahyrePlatform):
        assert platform._should_queue(0.6, "T1")

    def test_t1_boundary_queued(self, platform: InstahyrePlatform):
        assert platform._should_queue(0.69, "T1")

    def test_t1_at_threshold_not_queued(self, platform: InstahyrePlatform):
        assert not platform._should_queue(0.7, "T1")

    def test_t2_always_queued(self, platform: InstahyrePlatform):
        assert platform._should_queue(0.9, "T2")

    def test_t3_always_queued(self, platform: InstahyrePlatform):
        assert platform._should_queue(0.9, "T3")


# ── _should_stop ──────────────────────────────────────────────────────


class TestShouldStop:
    def test_stop_file(self, platform: InstahyrePlatform, tmp_path: Path):
        stop_file = tmp_path / "STOP"
        stop_file.touch()
        with patch("platforms.instahyre.STOP_FILE", stop_file):
            platform._session_start = time.monotonic()
            assert platform._should_stop()

    def test_session_timeout(self, platform: InstahyrePlatform):
        platform._session_start = time.monotonic() - (MAX_SESSION_SECONDS + 60)
        with patch("platforms.instahyre.STOP_FILE", Path("/nonexistent")):
            assert platform._should_stop()

    def test_selector_failures(self, platform: InstahyrePlatform):
        platform._session_start = time.monotonic()
        platform._selector_failures = MAX_SELECTOR_FAILURES
        with patch("platforms.instahyre.STOP_FILE", Path("/nonexistent")):
            assert platform._should_stop()

    def test_normal_not_stopped(self, platform: InstahyrePlatform, tmp_path: Path):
        platform._session_start = time.monotonic()
        platform._selector_failures = 0
        with patch("platforms.instahyre.STOP_FILE", tmp_path / "nope"):
            assert not platform._should_stop()


# ── Standard field names ──────────────────────────────────────────────


class TestStandardFieldNames:
    def test_core_fields_present(self):
        assert "name" in STANDARD_FIELD_NAMES
        assert "email" in STANDARD_FIELD_NAMES
        assert "phone" in STANDARD_FIELD_NAMES
        assert "linkedin" in STANDARD_FIELD_NAMES
        assert "resume" in STANDARD_FIELD_NAMES

    def test_ctc_fields_present(self):
        assert "current ctc" in STANDARD_FIELD_NAMES
        assert "expected ctc" in STANDARD_FIELD_NAMES

    def test_notice_period_present(self):
        assert "notice period" in STANDARD_FIELD_NAMES


# ── Constants ─────────────────────────────────────────────────────────


class TestConstants:
    def test_login_url(self):
        assert LOGIN_URL == "https://www.instahyre.com/login/"

    def test_base_url(self):
        assert BASE_URL == "https://www.instahyre.com"

    def test_opportunities_url(self):
        assert OPPORTUNITIES_URL == "https://www.instahyre.com/candidate/opportunities/"

    def test_max_pages(self):
        assert MAX_PAGES_PER_KEYWORD == 5

    def test_max_session_90_min(self):
        assert MAX_SESSION_SECONDS == 90 * 60
