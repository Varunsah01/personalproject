"""Unit tests for core/logger.py."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.logger import (
    COLUMNS,
    LogEntry,
    count_today,
    init_log,
    is_duplicate,
    log_application,
    normalize_url,
    read_log,
)


@pytest.fixture
def log_path(tmp_path: Path) -> Path:
    """Provide a fresh log file path inside tmp_path."""
    return tmp_path / "applications_log.csv"


class TestInitLog:
    def test_creates_file_with_header(self, log_path: Path) -> None:
        """init_log creates the CSV with the correct header row."""
        assert not log_path.exists()
        init_log(log_path)
        assert log_path.exists()

        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 1
        header = lines[0].split(",")
        assert header == COLUMNS

    def test_idempotent(self, log_path: Path) -> None:
        """Calling init_log twice doesn't duplicate the header."""
        init_log(log_path)
        init_log(log_path)

        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 1

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        """init_log creates parent directories if they don't exist."""
        nested = tmp_path / "a" / "b" / "log.csv"
        init_log(nested)
        assert nested.exists()


class TestNormalizeUrl:
    def test_strips_utm_params(self) -> None:
        url = "https://naukri.com/job/123?utm_source=google&utm_medium=cpc&title=pm"
        result = normalize_url(url)
        assert "utm_source" not in result
        assert "utm_medium" not in result
        assert "title=pm" in result

    def test_strips_ref_param(self) -> None:
        url = "https://linkedin.com/jobs/view/456?ref=homepage"
        result = normalize_url(url)
        assert "ref=" not in result

    def test_strips_source_param(self) -> None:
        url = "https://example.com/job?source=email&id=789"
        result = normalize_url(url)
        assert "source=" not in result
        assert "id=789" in result

    def test_strips_fragment(self) -> None:
        url = "https://wellfound.com/job/99#apply-section"
        result = normalize_url(url)
        assert "#" not in result
        assert "apply-section" not in result

    def test_strips_fbclid_and_gclid(self) -> None:
        url = "https://example.com/job?fbclid=abc&gclid=def&real=1"
        result = normalize_url(url)
        assert "fbclid" not in result
        assert "gclid" not in result
        assert "real=1" in result

    def test_preserves_path_and_meaningful_params(self) -> None:
        url = "https://naukri.com/job/senior-pm-123?company=razorpay"
        result = normalize_url(url)
        assert "senior-pm-123" in result
        assert "company=razorpay" in result


class TestIsDuplicate:
    def test_no_file_returns_false(self, log_path: Path) -> None:
        """Non-existent log means no duplicates."""
        assert not is_duplicate("naukri", "https://example.com/job/1", log_path)

    def test_exact_match(self, log_path: Path) -> None:
        """Exact URL match is caught."""
        entry = LogEntry(
            date_applied="2026-04-28",
            time_applied="10:00",
            platform="naukri",
            company_name="Acme",
            role_title="PM",
            experience_required="2-4 years",
            location="Bangalore",
            job_url="https://naukri.com/job/123",
            fit_score=0.8,
            status="applied",
        )
        log_application(entry, log_path)

        assert is_duplicate("naukri", "https://naukri.com/job/123", log_path)

    def test_normalises_utm_params(self, log_path: Path) -> None:
        """Same job with vs without UTM params is caught as duplicate."""
        entry = LogEntry(
            date_applied="2026-04-28",
            time_applied="10:00",
            platform="linkedin",
            company_name="Startup",
            role_title="Growth Manager",
            experience_required="1-3 years",
            location="Delhi",
            job_url="https://linkedin.com/jobs/view/456",
            fit_score=0.7,
            status="applied",
        )
        log_application(entry, log_path)

        # Same job but with tracking params
        assert is_duplicate(
            "linkedin",
            "https://linkedin.com/jobs/view/456?utm_source=email&utm_campaign=daily",
            log_path,
        )

    def test_normalises_fragment(self, log_path: Path) -> None:
        """Same job with fragment hash is caught as duplicate."""
        entry = LogEntry(
            date_applied="2026-04-28",
            time_applied="11:00",
            platform="wellfound",
            company_name="Finco",
            role_title="BDM",
            experience_required="2-5 years",
            location="Mumbai",
            job_url="https://wellfound.com/job/789",
            fit_score=0.65,
            status="applied",
        )
        log_application(entry, log_path)

        assert is_duplicate(
            "wellfound",
            "https://wellfound.com/job/789#apply-now",
            log_path,
        )

    def test_different_platform_not_duplicate(self, log_path: Path) -> None:
        """Same URL on different platform is not a duplicate."""
        entry = LogEntry(
            date_applied="2026-04-28",
            time_applied="12:00",
            platform="naukri",
            company_name="Corp",
            role_title="PM",
            experience_required="3 years",
            location="Remote",
            job_url="https://example.com/job/100",
            fit_score=0.6,
            status="applied",
        )
        log_application(entry, log_path)

        assert not is_duplicate("linkedin", "https://example.com/job/100", log_path)

    def test_case_insensitive_platform(self, log_path: Path) -> None:
        """Platform comparison is case-insensitive."""
        entry = LogEntry(
            date_applied="2026-04-28",
            time_applied="13:00",
            platform="Naukri",
            company_name="X",
            role_title="Y",
            experience_required="",
            location="",
            job_url="https://naukri.com/job/200",
            fit_score=0.5,
            status="applied",
        )
        log_application(entry, log_path)

        assert is_duplicate("naukri", "https://naukri.com/job/200", log_path)
        assert is_duplicate("NAUKRI", "https://naukri.com/job/200", log_path)


class TestLogApplication:
    def test_appends_correctly(self, log_path: Path) -> None:
        """log_application appends a row with all fields."""
        init_log(log_path)
        entry = LogEntry(
            date_applied="2026-04-28",
            time_applied="14:32",
            platform="naukri",
            company_name="Razorpay",
            role_title="Senior PM — Payments",
            experience_required="3-5 years",
            location="Bangalore",
            job_url="https://naukri.com/job/razorpay-pm",
            fit_score=0.78,
            status="applied",
            notes="",
        )
        log_application(entry, log_path)

        entries = read_log(log_path)
        assert len(entries) == 1
        assert entries[0].platform == "naukri"
        assert entries[0].company_name == "Razorpay"
        assert entries[0].fit_score == 0.78
        assert entries[0].status == "applied"

    def test_multiple_appends(self, log_path: Path) -> None:
        """Multiple calls append multiple rows."""
        for i in range(3):
            entry = LogEntry(
                date_applied="2026-04-28",
                time_applied=f"10:0{i}",
                platform="naukri",
                company_name=f"Company{i}",
                role_title="PM",
                experience_required="",
                location="Delhi",
                job_url=f"https://naukri.com/job/{i}",
                fit_score=0.5 + i * 0.1,
                status="applied",
            )
            log_application(entry, log_path)

        entries = read_log(log_path)
        assert len(entries) == 3

    def test_creates_file_if_missing(self, log_path: Path) -> None:
        """log_application creates the CSV if it doesn't exist."""
        assert not log_path.exists()
        entry = LogEntry(
            date_applied="2026-04-28",
            time_applied="09:00",
            platform="cutshort",
            company_name="Test",
            role_title="Test Role",
            experience_required="",
            location="Remote",
            job_url="https://cutshort.io/job/1",
            fit_score=0.55,
            status="skipped",
            notes="below threshold",
        )
        log_application(entry, log_path)

        assert log_path.exists()
        entries = read_log(log_path)
        assert len(entries) == 1
        assert entries[0].notes == "below threshold"


class TestCountToday:
    def test_counts_applied_today(self, log_path: Path) -> None:
        """count_today returns the count of applied/applied_unconfirmed for today."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        for i in range(5):
            entry = LogEntry(
                date_applied=today,
                time_applied=f"10:0{i}",
                platform="naukri",
                company_name=f"Co{i}",
                role_title="PM",
                experience_required="",
                location="Delhi",
                job_url=f"https://naukri.com/job/{i}",
                fit_score=0.7,
                status="applied",
            )
            log_application(entry, log_path)

        assert count_today("naukri", log_path) == 5

    def test_filters_by_platform(self, log_path: Path) -> None:
        """count_today only counts the specified platform."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        for platform in ["naukri", "naukri", "linkedin"]:
            entry = LogEntry(
                date_applied=today,
                time_applied="10:00",
                platform=platform,
                company_name="Co",
                role_title="PM",
                experience_required="",
                location="Delhi",
                job_url=f"https://example.com/{platform}/{id(platform)}",
                fit_score=0.7,
                status="applied",
            )
            log_application(entry, log_path)

        assert count_today("naukri", log_path) == 2
        assert count_today("linkedin", log_path) == 1

    def test_ignores_skipped_and_error(self, log_path: Path) -> None:
        """count_today doesn't count skipped or errored entries."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        for status in ["applied", "skipped", "error", "applied_unconfirmed"]:
            entry = LogEntry(
                date_applied=today,
                time_applied="10:00",
                platform="naukri",
                company_name="Co",
                role_title="PM",
                experience_required="",
                location="Delhi",
                job_url=f"https://naukri.com/job/{status}",
                fit_score=0.7,
                status=status,
            )
            log_application(entry, log_path)

        # Only "applied" and "applied_unconfirmed" count
        assert count_today("naukri", log_path) == 2

    def test_ignores_other_dates(self, log_path: Path) -> None:
        """count_today only counts today's date, not yesterday."""
        entry = LogEntry(
            date_applied="2020-01-01",
            time_applied="10:00",
            platform="naukri",
            company_name="Old",
            role_title="PM",
            experience_required="",
            location="Delhi",
            job_url="https://naukri.com/job/old",
            fit_score=0.7,
            status="applied",
        )
        log_application(entry, log_path)

        assert count_today("naukri", log_path) == 0

    def test_nonexistent_file_returns_zero(self, log_path: Path) -> None:
        """count_today returns 0 if the log file doesn't exist."""
        assert count_today("naukri", log_path) == 0
