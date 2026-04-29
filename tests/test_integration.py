"""Integration smoke tests for NaukriPlatform.run() orchestration.

Mocks the Playwright browser — no real browser or network needed.
Verifies that _process_job() and run() correctly wire together
login/search/logout, deduplication, cap enforcement, and dry-run behavior.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from core.logger import LogEntry, log_application, read_log
from core.scorer import Job
from core.types import BotConfig
from platforms.naukri import NaukriPlatform


# ── Helpers ────────────────────────────────────────────────────────────


def _high_fit_job(url: str = "https://naukri.com/job/1") -> Job:
    """T1 job that scores >= 0.7 — qualifies for the green auto-apply path."""
    return Job(
        title="Founding Member — Growth",
        company="RocketPay",
        location="Bangalore",
        experience_required="2-4 years",
        jd_text=(
            "Series A B2B SaaS company building GTM tooling. "
            "Looking for a founding member to own growth from 0 to 1."
        ),
        posted_date=url,  # NaukriPlatform stashes job URL in posted_date
    )


def _low_fit_job(url: str = "https://naukri.com/job/2") -> Job:
    """Hard-skip job (tier=skip, backend engineer) — never gets applied."""
    return Job(
        title="Backend Engineer",
        company="BigCorp",
        location="Bangalore",
        experience_required="3-5 years",
        jd_text="Senior backend developer needed for microservices.",
        posted_date=url,
    )


def _make_platform(
    tmp_path: Path,
    *,
    dry_run: bool = False,
    daily_cap: int = 5,
) -> NaukriPlatform:
    """NaukriPlatform with a tmp log path and stub credentials."""
    config = BotConfig(
        log_path=tmp_path / "log.csv",
        headless=True,
        dry_run=dry_run,
        daily_cap=daily_cap,
        standard_answers={},
    )
    p = NaukriPlatform(config)
    p.email = "test@example.com"
    p.password = "test-password"
    return p


def _stub_login_logout(platform: NaukriPlatform) -> None:
    """Replace login() and logout() with silent no-ops."""

    async def _login() -> bool:
        return True

    async def _logout() -> None:
        pass

    platform.login = _login
    platform.logout = _logout


def _search_yielding(*jobs: Job):
    """Async generator function that yields the given jobs once."""

    async def _gen(keyword: str, filters: dict):
        for job in jobs:
            yield job

    return _gen


def _empty_search():
    """Async generator function that yields nothing."""

    async def _gen(keyword: str, filters: dict):
        return
        yield  # pragma: no cover — marks the function as an async generator

    return _gen


# ── Test 1: call order ─────────────────────────────────────────────────


class TestRunCallOrder:
    async def test_login_then_search_then_logout(self, tmp_path: Path) -> None:
        """run() must call login(), then search() per keyword, then logout()."""
        platform = _make_platform(tmp_path)
        call_order: list[str] = []

        async def mock_login() -> bool:
            call_order.append("login")
            return True

        async def mock_logout() -> None:
            call_order.append("logout")

        async def mock_search(keyword: str, filters: dict):
            call_order.append(f"search:{keyword}")
            return
            yield  # pragma: no cover

        platform.login = mock_login
        platform.logout = mock_logout
        platform.search = mock_search

        await platform.run(["growth manager", "founding member"], {})

        assert call_order[0] == "login"
        assert "search:growth manager" in call_order
        assert "search:founding member" in call_order
        # logout lives in finally — must always be the last call
        assert call_order[-1] == "logout"

    async def test_search_not_called_when_login_fails(self, tmp_path: Path) -> None:
        """If login() returns False, run() returns early without searching."""
        platform = _make_platform(tmp_path)
        call_order: list[str] = []

        async def mock_login() -> bool:
            call_order.append("login")
            return False  # failed

        async def mock_search(keyword: str, filters: dict):
            call_order.append("search")
            return
            yield  # pragma: no cover

        platform.login = mock_login
        # logout is not reached when login fails (early return before try/finally)
        platform.logout = AsyncMock()
        platform.search = mock_search

        await platform.run(["growth manager"], {})

        assert call_order == ["login"]


# ── Test 2: dry run ────────────────────────────────────────────────────


class TestDryRun:
    async def test_above_threshold_logged_skipped_dry_run(self, tmp_path: Path) -> None:
        """High-fit T1 job (score >= 0.7) with dry_run=True: logged as
        status='skipped' with notes 'dry run: would apply'.
        open_application_form() must never be called."""
        platform = _make_platform(tmp_path, dry_run=True)
        _stub_login_logout(platform)
        platform.search = _search_yielding(_high_fit_job())
        platform.open_application_form = AsyncMock(
            side_effect=AssertionError("open_application_form must not be called in dry-run")
        )

        await platform.run(["founding member"], {})

        entries = read_log(tmp_path / "log.csv")
        recorded = [e for e in entries if e.company_name == "RocketPay"]
        assert len(recorded) == 1
        assert recorded[0].status == "skipped"
        assert "dry run" in recorded[0].notes
        platform.open_application_form.assert_not_called()


# ── Test 3: below threshold ────────────────────────────────────────────


class TestBelowThreshold:
    async def test_hard_skip_tier_logged_skipped(self, tmp_path: Path) -> None:
        """Backend engineer (tier=skip) is logged as status='skipped'.
        open_application_form() must never be called."""
        platform = _make_platform(tmp_path)
        _stub_login_logout(platform)
        platform.search = _search_yielding(_low_fit_job())
        platform.open_application_form = AsyncMock(
            side_effect=AssertionError("open_application_form must not be called for skip-tier jobs")
        )

        await platform.run(["backend engineer"], {})

        entries = read_log(tmp_path / "log.csv")
        recorded = [e for e in entries if e.company_name == "BigCorp"]
        assert len(recorded) == 1
        assert recorded[0].status == "skipped"
        platform.open_application_form.assert_not_called()


# ── Test 4: deduplication ──────────────────────────────────────────────


class TestDeduplicate:
    async def test_duplicate_url_skipped_before_form(self, tmp_path: Path) -> None:
        """A job URL already in the log is caught by is_duplicate() at step 1.
        Status is logged as 'skipped' with notes='duplicate'.
        open_application_form() must never be called."""
        log_path = tmp_path / "log.csv"
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Seed the log with the same URL that will appear in search results
        prior = LogEntry(
            date_applied=today,
            time_applied="09:00",
            platform="naukri",
            company_name="RocketPay",
            role_title="Founding Member — Growth",
            experience_required="2-4 years",
            location="Bangalore",
            job_url="https://naukri.com/job/1",
            fit_score=0.85,
            status="applied",
        )
        log_application(prior, log_path)

        platform = _make_platform(tmp_path)
        _stub_login_logout(platform)
        platform.search = _search_yielding(_high_fit_job(url="https://naukri.com/job/1"))
        platform.open_application_form = AsyncMock(
            side_effect=AssertionError("open_application_form must not be called for duplicates")
        )

        await platform.run(["founding member"], {})

        entries = read_log(log_path)
        dup_entries = [e for e in entries if e.notes == "duplicate"]
        assert len(dup_entries) == 1
        assert dup_entries[0].status == "skipped"
        platform.open_application_form.assert_not_called()

    async def test_utm_variant_url_is_also_duplicate(self, tmp_path: Path) -> None:
        """Same URL with tracking params is normalised and caught as duplicate."""
        log_path = tmp_path / "log.csv"
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        prior = LogEntry(
            date_applied=today,
            time_applied="10:00",
            platform="naukri",
            company_name="RocketPay",
            role_title="Founding Member — Growth",
            experience_required="2-4 years",
            location="Bangalore",
            job_url="https://naukri.com/job/1",
            fit_score=0.85,
            status="applied",
        )
        log_application(prior, log_path)

        platform = _make_platform(tmp_path)
        _stub_login_logout(platform)
        # Same job, different UTM params — should still be a duplicate
        platform.search = _search_yielding(
            _high_fit_job(url="https://naukri.com/job/1?utm_source=email&utm_campaign=daily")
        )
        platform.open_application_form = AsyncMock(
            side_effect=AssertionError("UTM variant should be caught as duplicate")
        )

        await platform.run(["founding member"], {})

        entries = read_log(log_path)
        dup_entries = [e for e in entries if e.notes == "duplicate"]
        assert len(dup_entries) == 1
        platform.open_application_form.assert_not_called()


# ── Test 5: daily cap ──────────────────────────────────────────────────


class TestDailyCap:
    async def test_cap_reached_stops_applying(self, tmp_path: Path) -> None:
        """When today's applied count == daily_cap, the next job is logged as
        status='skipped' with 'cap reached' in notes.
        open_application_form() must never be called."""
        log_path = tmp_path / "log.csv"
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        cap = 2

        # Fill the cap with prior applications today
        for i in range(cap):
            entry = LogEntry(
                date_applied=today,
                time_applied=f"08:0{i}",
                platform="naukri",
                company_name=f"PriorCo{i}",
                role_title="Growth Manager",
                experience_required="2-4 years",
                location="Bangalore",
                job_url=f"https://naukri.com/job/prior-{i}",
                fit_score=0.80,
                status="applied",
            )
            log_application(entry, log_path)

        platform = _make_platform(tmp_path, daily_cap=cap)
        _stub_login_logout(platform)
        platform.search = _search_yielding(_high_fit_job(url="https://naukri.com/job/new"))
        platform.open_application_form = AsyncMock(
            side_effect=AssertionError("open_application_form must not be called when cap is reached")
        )

        await platform.run(["founding member"], {})

        entries = read_log(log_path)
        cap_skipped = [e for e in entries if "cap reached" in e.notes]
        assert len(cap_skipped) == 1
        assert cap_skipped[0].status == "skipped"
        platform.open_application_form.assert_not_called()

    async def test_cap_not_exceeded_allows_applying(self, tmp_path: Path) -> None:
        """If today's count is one below the cap, the next job reaches _attempt_apply().
        We mock _attempt_apply() to confirm it was called."""
        log_path = tmp_path / "log.csv"
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        cap = 2

        # Only cap-1 prior applications — still room for one more
        for i in range(cap - 1):
            entry = LogEntry(
                date_applied=today,
                time_applied=f"08:0{i}",
                platform="naukri",
                company_name=f"PriorCo{i}",
                role_title="Growth Manager",
                experience_required="2-4 years",
                location="Bangalore",
                job_url=f"https://naukri.com/job/prior-{i}",
                fit_score=0.80,
                status="applied",
            )
            log_application(entry, log_path)

        platform = _make_platform(tmp_path, daily_cap=cap)
        _stub_login_logout(platform)
        platform.search = _search_yielding(_high_fit_job(url="https://naukri.com/job/new"))

        # _attempt_apply → open_application_form should be reached this time
        attempt_called = False

        async def mock_attempt_apply(job: Job, fit_score: float, tier: str) -> None:
            nonlocal attempt_called
            attempt_called = True
            # Simulate a direct apply success without touching the browser
            platform._record(job, fit_score, "applied", "mock apply")

        platform._attempt_apply = mock_attempt_apply

        await platform.run(["founding member"], {})

        assert attempt_called, "_attempt_apply should have been called"
        entries = read_log(log_path)
        applied = [e for e in entries if e.status == "applied" and e.company_name == "RocketPay"]
        assert len(applied) == 1
