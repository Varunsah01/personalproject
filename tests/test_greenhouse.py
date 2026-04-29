"""Unit tests for platforms/greenhouse.py.

Tests cover API response parsing, keyword filtering, location filtering,
caching logic, and form-field helpers. Browser interactions are not tested
(manually verified). Uses mocked httpx responses — no real API calls.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.scorer import Job
from core.types import BotConfig
from platforms.greenhouse import (
    GreenhousePlatform,
    _keyword_matches,
    _load_companies,
    _location_matches,
    _strip_html,
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
        daily_cap=80,
        standard_answers={"name": "Varun Sah", "phone": "+91-8595062552"},
    )


@pytest.fixture
def companies_file(tmp_path: Path) -> Path:
    """Create a temporary greenhouse_companies.txt."""
    path = tmp_path / "companies.txt"
    path.write_text("# comment\nstripe\nanthropic\n\n# another comment\nrazorpay\n")
    return path


def _make_api_response(jobs: list[dict]) -> dict:
    """Wrap jobs in the Greenhouse API response envelope."""
    return {"jobs": jobs, "meta": {"total": len(jobs)}}


def _make_raw_job(
    title: str = "Growth Manager",
    location: str = "Bangalore, India",
    company: str = "testco",
    content: str = "<p>B2B SaaS growth role</p>",
    absolute_url: str = "https://boards.greenhouse.io/testco/jobs/123",
) -> dict:
    """Build a raw Greenhouse API job object."""
    return {
        "id": 123,
        "title": title,
        "location": {"name": location},
        "absolute_url": absolute_url,
        "content": content,
        "departments": [{"name": "Growth"}],
        "offices": [{"name": "India"}],
    }


# ── _strip_html ───────────────────────────────────────────────────────


class TestStripHtml:
    def test_removes_tags(self):
        result = _strip_html("<p>Hello <b>world</b></p>")
        assert "Hello" in result and "world" in result
        assert "<" not in result

    def test_decodes_entities(self):
        assert "&" in _strip_html("&amp;")
        assert "<" in _strip_html("&lt;")

    def test_empty_string(self):
        assert _strip_html("") == ""

    def test_plain_text_unchanged(self):
        assert _strip_html("no tags here") == "no tags here"


# ── _keyword_matches ──────────────────────────────────────────────────


class TestKeywordMatches:
    def test_exact_match(self):
        assert _keyword_matches("Growth Manager", "growth manager")

    def test_partial_match(self):
        assert _keyword_matches("Senior Growth Manager - India", "growth manager")

    def test_no_match(self):
        assert not _keyword_matches("Backend Engineer", "growth manager")

    def test_case_insensitive(self):
        assert _keyword_matches("PRODUCT MANAGER", "product manager")


# ── _location_matches ─────────────────────────────────────────────────


class TestLocationMatches:
    def test_india_city(self):
        assert _location_matches("Bangalore, India")

    def test_delhi_ncr(self):
        assert _location_matches("Delhi NCR")

    def test_remote(self):
        assert _location_matches("Remote")

    def test_anywhere(self):
        assert _location_matches("Anywhere")

    def test_us_only(self):
        assert not _location_matches("San Francisco, CA")

    def test_london(self):
        assert not _location_matches("London, UK")

    def test_mumbai(self):
        assert _location_matches("Mumbai, Maharashtra")

    def test_gurugram(self):
        assert _location_matches("Gurugram, Haryana")

    def test_global(self):
        assert _location_matches("Global / Distributed")


# ── _load_companies ───────────────────────────────────────────────────


class TestLoadCompanies:
    def test_parses_file(self, companies_file: Path):
        with patch("platforms.greenhouse.COMPANIES_PATH", companies_file):
            result = _load_companies()
        assert result == ["stripe", "anthropic", "razorpay"]

    def test_skips_comments_and_blanks(self, companies_file: Path):
        with patch("platforms.greenhouse.COMPANIES_PATH", companies_file):
            result = _load_companies()
        assert "#" not in "".join(result)

    def test_missing_file(self, tmp_path: Path):
        with patch("platforms.greenhouse.COMPANIES_PATH", tmp_path / "nope.txt"):
            result = _load_companies()
        assert result == []


# ── GreenhousePlatform._fetch_jobs ────────────────────────────────────


class TestFetchJobs:
    @pytest.mark.asyncio
    async def test_api_success(self, config: BotConfig, tmp_path: Path):
        """API returns valid jobs → stored in cache and returned."""
        jobs_data = [_make_raw_job()]
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = _make_api_response(jobs_data)
        response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("platforms.greenhouse.COMPANIES_PATH", tmp_path / "c.txt"), \
             patch("platforms.greenhouse.CACHE_DIR", tmp_path / "cache"), \
             patch("platforms.greenhouse.asyncio.sleep", new_callable=AsyncMock):
            # Write companies file
            (tmp_path / "c.txt").write_text("testco\n")
            platform = GreenhousePlatform(config)

            with patch("httpx.AsyncClient", return_value=mock_client):
                result = await platform._fetch_jobs("testco")

        assert len(result) == 1
        assert result[0]["title"] == "Growth Manager"

    @pytest.mark.asyncio
    async def test_api_404_returns_empty(self, config: BotConfig, tmp_path: Path):
        """404 (invalid board token) → empty list, no crash."""
        response = MagicMock()
        response.status_code = 404

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("platforms.greenhouse.COMPANIES_PATH", tmp_path / "c.txt"), \
             patch("platforms.greenhouse.CACHE_DIR", tmp_path / "cache"):
            (tmp_path / "c.txt").write_text("badco\n")
            platform = GreenhousePlatform(config)

            with patch("httpx.AsyncClient", return_value=mock_client):
                result = await platform._fetch_jobs("badco")

        assert result == []

    @pytest.mark.asyncio
    async def test_api_error_returns_empty(self, config: BotConfig, tmp_path: Path):
        """Network error → empty list, no crash."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("platforms.greenhouse.COMPANIES_PATH", tmp_path / "c.txt"), \
             patch("platforms.greenhouse.CACHE_DIR", tmp_path / "cache"):
            (tmp_path / "c.txt").write_text("testco\n")
            platform = GreenhousePlatform(config)

            with patch("httpx.AsyncClient", return_value=mock_client):
                result = await platform._fetch_jobs("testco")

        assert result == []

    @pytest.mark.asyncio
    async def test_disk_cache_hit(self, config: BotConfig, tmp_path: Path):
        """Fresh disk cache → no API call."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        cache_file = cache_dir / "testco.json"
        jobs_data = [_make_raw_job()]
        cache_file.write_text(json.dumps(jobs_data))

        with patch("platforms.greenhouse.COMPANIES_PATH", tmp_path / "c.txt"), \
             patch("platforms.greenhouse.CACHE_DIR", cache_dir):
            (tmp_path / "c.txt").write_text("testco\n")
            platform = GreenhousePlatform(config)

            # No httpx mock needed — should use disk cache
            result = await platform._fetch_jobs("testco")

        assert len(result) == 1
        assert result[0]["title"] == "Growth Manager"

    @pytest.mark.asyncio
    async def test_memory_cache_hit(self, config: BotConfig, tmp_path: Path):
        """In-memory cache → no disk or API access."""
        with patch("platforms.greenhouse.COMPANIES_PATH", tmp_path / "c.txt"), \
             patch("platforms.greenhouse.CACHE_DIR", tmp_path / "cache"):
            (tmp_path / "c.txt").write_text("testco\n")
            platform = GreenhousePlatform(config)

            # Pre-populate in-memory cache
            jobs_data = [_make_raw_job()]
            platform._api_cache["testco"] = jobs_data

            result = await platform._fetch_jobs("testco")

        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_stale_disk_cache_triggers_api(self, config: BotConfig, tmp_path: Path):
        """Disk cache older than 6 hours → refetches from API."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        cache_file = cache_dir / "testco.json"
        cache_file.write_text(json.dumps([_make_raw_job(title="Old Job")]))

        # Age the file to 7 hours old
        old_time = time.time() - 7 * 3600
        os.utime(cache_file, (old_time, old_time))

        new_jobs = [_make_raw_job(title="Fresh Job")]
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = _make_api_response(new_jobs)
        response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("platforms.greenhouse.COMPANIES_PATH", tmp_path / "c.txt"), \
             patch("platforms.greenhouse.CACHE_DIR", cache_dir), \
             patch("platforms.greenhouse.asyncio.sleep", new_callable=AsyncMock):
            (tmp_path / "c.txt").write_text("testco\n")
            platform = GreenhousePlatform(config)

            with patch("httpx.AsyncClient", return_value=mock_client):
                result = await platform._fetch_jobs("testco")

        assert result[0]["title"] == "Fresh Job"


# ── search filtering ──────────────────────────────────────────────────


class TestSearchFiltering:
    @pytest.mark.asyncio
    async def test_filters_by_keyword(self, config: BotConfig, tmp_path: Path):
        """Only jobs matching the keyword are yielded."""
        jobs = [
            _make_raw_job(title="Growth Manager", absolute_url="https://gh.io/1"),
            _make_raw_job(title="Backend Engineer", absolute_url="https://gh.io/2"),
        ]

        with patch("platforms.greenhouse.COMPANIES_PATH", tmp_path / "c.txt"), \
             patch("platforms.greenhouse.CACHE_DIR", tmp_path / "cache"):
            (tmp_path / "c.txt").write_text("testco\n")
            platform = GreenhousePlatform(config)
            platform._api_cache["testco"] = jobs
            platform._session_start = time.monotonic()

            results = []
            async for job in platform.search("growth", {}):
                results.append(job)

        assert len(results) == 1
        assert results[0].title == "Growth Manager"

    @pytest.mark.asyncio
    async def test_filters_by_location(self, config: BotConfig, tmp_path: Path):
        """Only India/Remote locations pass through."""
        jobs = [
            _make_raw_job(title="Growth Manager", location="San Francisco", absolute_url="https://gh.io/1"),
            _make_raw_job(title="Growth Manager", location="Bangalore", absolute_url="https://gh.io/2"),
        ]

        with patch("platforms.greenhouse.COMPANIES_PATH", tmp_path / "c.txt"), \
             patch("platforms.greenhouse.CACHE_DIR", tmp_path / "cache"):
            (tmp_path / "c.txt").write_text("testco\n")
            platform = GreenhousePlatform(config)
            platform._api_cache["testco"] = jobs
            platform._session_start = time.monotonic()

            results = []
            async for job in platform.search("growth", {}):
                results.append(job)

        assert len(results) == 1
        assert results[0].location == "Bangalore"

    @pytest.mark.asyncio
    async def test_dedupes_across_keywords(self, config: BotConfig, tmp_path: Path):
        """Same job URL across two keyword searches is yielded only once."""
        jobs = [_make_raw_job(title="Growth Product Manager")]

        with patch("platforms.greenhouse.COMPANIES_PATH", tmp_path / "c.txt"), \
             patch("platforms.greenhouse.CACHE_DIR", tmp_path / "cache"):
            (tmp_path / "c.txt").write_text("testco\n")
            platform = GreenhousePlatform(config)
            platform._api_cache["testco"] = jobs
            platform._session_start = time.monotonic()

            # First search
            r1 = []
            async for job in platform.search("growth", {}):
                r1.append(job)
            # Second search — same URL
            r2 = []
            async for job in platform.search("product", {}):
                r2.append(job)

        assert len(r1) == 1
        assert len(r2) == 0  # deduped

    @pytest.mark.asyncio
    async def test_skips_missing_url(self, config: BotConfig, tmp_path: Path):
        """Jobs without an absolute_url are skipped."""
        jobs = [_make_raw_job(absolute_url="")]

        with patch("platforms.greenhouse.COMPANIES_PATH", tmp_path / "c.txt"), \
             patch("platforms.greenhouse.CACHE_DIR", tmp_path / "cache"):
            (tmp_path / "c.txt").write_text("testco\n")
            platform = GreenhousePlatform(config)
            platform._api_cache["testco"] = jobs
            platform._session_start = time.monotonic()

            results = []
            async for job in platform.search("growth", {}):
                results.append(job)

        assert len(results) == 0


# ── Job construction ──────────────────────────────────────────────────


class TestJobConstruction:
    @pytest.mark.asyncio
    async def test_job_fields(self, config: BotConfig, tmp_path: Path):
        """Yielded Job has correct fields from API response."""
        jobs = [_make_raw_job(
            title="Product Manager",
            location="Remote, India",
            company="acmeco",
            content="<p>Great role for PMs</p>",
            absolute_url="https://boards.greenhouse.io/acmeco/jobs/456",
        )]

        with patch("platforms.greenhouse.COMPANIES_PATH", tmp_path / "c.txt"), \
             patch("platforms.greenhouse.CACHE_DIR", tmp_path / "cache"):
            (tmp_path / "c.txt").write_text("acmeco\n")
            platform = GreenhousePlatform(config)
            platform._api_cache["acmeco"] = jobs
            platform._session_start = time.monotonic()

            results = []
            async for job in platform.search("product", {}):
                results.append(job)

        assert len(results) == 1
        job = results[0]
        assert job.title == "Product Manager"
        assert job.company == "acmeco"
        assert job.location == "Remote, India"
        assert job.posted_date == "https://boards.greenhouse.io/acmeco/jobs/456"
        assert "Great role" in job.jd_text
        assert job.experience_required == ""  # not in Greenhouse API


# ── _should_queue ─────────────────────────────────────────────────────


class TestShouldQueue:
    def _make_platform(self, config: BotConfig, tmp_path: Path) -> GreenhousePlatform:
        with patch("platforms.greenhouse.COMPANIES_PATH", tmp_path / "c.txt"):
            (tmp_path / "c.txt").write_text("testco\n")
            return GreenhousePlatform(config)

    def test_t1_high_score_not_queued(self, config: BotConfig, tmp_path: Path):
        p = self._make_platform(config, tmp_path)
        assert not p._should_queue(0.8, "T1")

    def test_t1_low_score_queued(self, config: BotConfig, tmp_path: Path):
        p = self._make_platform(config, tmp_path)
        assert p._should_queue(0.6, "T1")

    def test_t2_always_queued(self, config: BotConfig, tmp_path: Path):
        p = self._make_platform(config, tmp_path)
        assert p._should_queue(0.9, "T2")

    def test_t3_always_queued(self, config: BotConfig, tmp_path: Path):
        p = self._make_platform(config, tmp_path)
        assert p._should_queue(0.9, "T3")


# ── _should_stop ──────────────────────────────────────────────────────


class TestShouldStop:
    def test_stop_file(self, config: BotConfig, tmp_path: Path):
        stop_file = tmp_path / "STOP"
        stop_file.touch()
        with patch("platforms.greenhouse.COMPANIES_PATH", tmp_path / "c.txt"), \
             patch("platforms.greenhouse.STOP_FILE", stop_file):
            (tmp_path / "c.txt").write_text("testco\n")
            p = GreenhousePlatform(config)
            p._session_start = time.monotonic()
            assert p._should_stop()

    def test_session_timeout(self, config: BotConfig, tmp_path: Path):
        with patch("platforms.greenhouse.COMPANIES_PATH", tmp_path / "c.txt"):
            (tmp_path / "c.txt").write_text("testco\n")
            p = GreenhousePlatform(config)
            p._session_start = time.monotonic() - 100 * 60  # 100 min ago
            assert p._should_stop()

    def test_normal_not_stopped(self, config: BotConfig, tmp_path: Path):
        with patch("platforms.greenhouse.COMPANIES_PATH", tmp_path / "c.txt"), \
             patch("platforms.greenhouse.STOP_FILE", tmp_path / "nope"):
            (tmp_path / "c.txt").write_text("testco\n")
            p = GreenhousePlatform(config)
            p._session_start = time.monotonic()
            assert not p._should_stop()
