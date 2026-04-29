"""Unit tests for core.orchestrator — merge-and-rank allocation."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.orchestrator import Candidate, count_today_all, rank_and_allocate
from core.scorer import Job


def _make_candidate(
    platform: str = "naukri",
    score: float = 0.7,
    tier: str = "T1",
    title: str = "Growth Manager",
) -> Candidate:
    """Helper to build a Candidate with sensible defaults."""
    job = Job(
        title=title,
        company="TestCo",
        location="Delhi NCR",
        experience_required="2-4 years",
        jd_text="B2B SaaS growth role at early-stage startup.",
        posted_date="https://example.com/job/123",
    )
    return Candidate(platform=platform, job=job, fit_score=score, tier=tier)


CEILINGS = {"naukri": 100, "linkedin": 100, "wellfound": 100, "cutshort": 100}


class TestRankAndAllocateBasic:
    """Core allocation behaviour."""

    def test_empty_pool_returns_empty(self) -> None:
        allocated, overflow = rank_and_allocate([], global_cap=300, ceilings=CEILINGS)
        assert allocated == []
        assert overflow == []

    def test_sorts_by_score_descending(self) -> None:
        candidates = [
            _make_candidate(score=0.5),
            _make_candidate(score=0.9),
            _make_candidate(score=0.7),
        ]
        allocated, _ = rank_and_allocate(candidates, global_cap=300, ceilings=CEILINGS)
        scores = [c.fit_score for c in allocated]
        assert scores == [0.9, 0.7, 0.5]

    def test_global_cap_limits_total(self) -> None:
        candidates = [_make_candidate(score=0.5 + i * 0.01) for i in range(10)]
        allocated, overflow = rank_and_allocate(
            candidates, global_cap=5, ceilings=CEILINGS
        )
        assert len(allocated) == 5
        assert len(overflow) == 5

    def test_all_allocated_when_under_cap(self) -> None:
        candidates = [_make_candidate() for _ in range(3)]
        allocated, overflow = rank_and_allocate(
            candidates, global_cap=300, ceilings=CEILINGS
        )
        assert len(allocated) == 3
        assert len(overflow) == 0


class TestPlatformCeilings:
    """Per-platform soft ceiling enforcement."""

    def test_platform_ceiling_respected(self) -> None:
        candidates = [_make_candidate(platform="naukri", score=0.8) for _ in range(6)]
        ceilings = {"naukri": 3, "linkedin": 100, "wellfound": 100, "cutshort": 100}
        allocated, overflow = rank_and_allocate(
            candidates, global_cap=300, ceilings=ceilings
        )
        assert len(allocated) == 3
        assert len(overflow) == 3

    def test_ceiling_skip_continues_other_platforms(self) -> None:
        candidates = [
            _make_candidate(platform="naukri", score=0.9),
            _make_candidate(platform="naukri", score=0.85),
            _make_candidate(platform="naukri", score=0.8),
            _make_candidate(platform="linkedin", score=0.75),
            _make_candidate(platform="linkedin", score=0.7),
        ]
        ceilings = {"naukri": 2, "linkedin": 100, "wellfound": 100, "cutshort": 100}
        allocated, overflow = rank_and_allocate(
            candidates, global_cap=300, ceilings=ceilings
        )
        # naukri: 2 (0.9, 0.85), linkedin: 2 (0.75, 0.7)
        assert len(allocated) == 4
        assert len(overflow) == 1
        assert overflow[0].platform == "naukri"
        assert overflow[0].fit_score == 0.8

    def test_all_platforms_at_ceiling_stops(self) -> None:
        candidates = [
            _make_candidate(platform="naukri", score=0.9),
            _make_candidate(platform="naukri", score=0.8),
            _make_candidate(platform="linkedin", score=0.7),
            _make_candidate(platform="linkedin", score=0.6),
        ]
        ceilings = {"naukri": 1, "linkedin": 1, "wellfound": 100, "cutshort": 100}
        allocated, overflow = rank_and_allocate(
            candidates, global_cap=300, ceilings=ceilings
        )
        assert len(allocated) == 2
        assert len(overflow) == 2


class TestAlreadyApplied:
    """Prior applies from earlier runs today."""

    def test_already_applied_reduces_ceiling(self) -> None:
        candidates = [_make_candidate(platform="naukri", score=0.8) for _ in range(5)]
        ceilings = {"naukri": 5, "linkedin": 100, "wellfound": 100, "cutshort": 100}
        allocated, overflow = rank_and_allocate(
            candidates, global_cap=300, ceilings=ceilings, already_applied={"naukri": 3}
        )
        # ceiling=5, already=3 → only 2 more allowed
        assert len(allocated) == 2
        assert len(overflow) == 3

    def test_already_applied_reduces_global_cap(self) -> None:
        candidates = [
            _make_candidate(platform="naukri", score=0.9),
            _make_candidate(platform="linkedin", score=0.8),
            _make_candidate(platform="wellfound", score=0.7),
        ]
        allocated, overflow = rank_and_allocate(
            candidates,
            global_cap=10,
            ceilings=CEILINGS,
            already_applied={"naukri": 4, "linkedin": 4},
        )
        # global cap=10, already used 8 → only 2 more
        assert len(allocated) == 2
        assert len(overflow) == 1


class TestTiesAndOrder:
    """Tie-breaking and ordering guarantees."""

    def test_ties_preserve_discovery_order(self) -> None:
        c1 = _make_candidate(platform="naukri", score=0.8)
        c1.job = Job("A", "CompA", "", "", "", "")
        c2 = _make_candidate(platform="linkedin", score=0.8)
        c2.job = Job("B", "CompB", "", "", "", "")
        c3 = _make_candidate(platform="wellfound", score=0.8)
        c3.job = Job("C", "CompC", "", "", "", "")

        allocated, _ = rank_and_allocate(
            [c1, c2, c3], global_cap=300, ceilings=CEILINGS
        )
        # Python's sorted() is stable — same score keeps insertion order
        companies = [c.job.company for c in allocated]
        assert companies == ["CompA", "CompB", "CompC"]

    def test_highest_score_allocated_first(self) -> None:
        candidates = [
            _make_candidate(platform="naukri", score=0.5),
            _make_candidate(platform="linkedin", score=0.95),
            _make_candidate(platform="wellfound", score=0.7),
        ]
        allocated, _ = rank_and_allocate(
            candidates, global_cap=300, ceilings=CEILINGS
        )
        assert allocated[0].fit_score == 0.95
        assert allocated[-1].fit_score == 0.5


class TestOverflow:
    """Overflow list correctness."""

    def test_overflow_contains_remainder(self) -> None:
        candidates = [
            _make_candidate(platform="naukri", score=0.9),
            _make_candidate(platform="naukri", score=0.6),
            _make_candidate(platform="linkedin", score=0.8),
        ]
        allocated, overflow = rank_and_allocate(
            candidates, global_cap=2, ceilings=CEILINGS
        )
        assert len(allocated) == 2
        assert len(overflow) == 1
        assert overflow[0].fit_score == 0.6

    def test_global_cap_zero_all_overflow(self) -> None:
        candidates = [_make_candidate() for _ in range(3)]
        allocated, overflow = rank_and_allocate(
            candidates, global_cap=0, ceilings=CEILINGS
        )
        assert len(allocated) == 0
        assert len(overflow) == 3


class TestCountTodayAll:
    """count_today_all() helper."""

    def test_aggregates_platforms(self, tmp_path: Path) -> None:
        log_path = tmp_path / "log.csv"
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        with open(log_path, "w", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "date_applied", "time_applied", "platform", "company_name",
                    "role_title", "experience_required", "location", "job_url",
                    "fit_score", "status", "notes",
                ],
            )
            writer.writeheader()
            for i, plat in enumerate(["naukri", "naukri", "linkedin"]):
                writer.writerow({
                    "date_applied": today,
                    "time_applied": "10:00",
                    "platform": plat,
                    "company_name": f"Co{i}",
                    "role_title": "PM",
                    "experience_required": "",
                    "location": "",
                    "job_url": f"https://example.com/{i}",
                    "fit_score": "0.8",
                    "status": "applied",
                    "notes": "",
                })

        result = count_today_all(["naukri", "linkedin", "wellfound"], log_path)
        assert result == {"naukri": 2, "linkedin": 1, "wellfound": 0}
