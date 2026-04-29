"""Candidate pool and merge-and-rank allocation for global daily cap.

Separates job discovery (all platforms) from application (ranked top-N).
The global cap limits total applies across all platforms; per-platform
soft ceilings prevent rate-limit issues on any single site.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.logger import count_today
from core.scorer import Job


@dataclass
class Candidate:
    """A scored job discovered during the discovery phase.

    Attributes:
        platform: Platform name, e.g. "naukri".
        job: The Job object (URL stored in job.posted_date per convention).
        fit_score: Pre-computed score from score_job().
        tier: Classification from classify_tier() — "T1", "T2", or "T3".
    """

    platform: str
    job: Job
    fit_score: float
    tier: str


def rank_and_allocate(
    candidates: list[Candidate],
    global_cap: int,
    ceilings: dict[str, int],
    already_applied: dict[str, int] | None = None,
) -> tuple[list[Candidate], list[Candidate]]:
    """Sort candidates by fit_score and allocate up to global_cap.

    Respects per-platform soft ceilings so no single platform dominates.
    Candidates that don't make the cut are returned as overflow.

    Args:
        candidates: All candidates discovered across platforms.
        global_cap: Maximum total applications for the day.
        ceilings: Per-platform soft ceiling, e.g. {"naukri": 100}.
        already_applied: How many applications each platform already has
            today (from previous runs). Counted toward both ceiling and
            global cap.

    Returns:
        (allocated, overflow) — allocated are the candidates to apply to,
        in descending fit_score order. overflow are the rest.
    """
    if not candidates:
        return [], []

    prior = dict(already_applied) if already_applied else {}
    platform_counts: dict[str, int] = {}
    for name in ceilings:
        platform_counts[name] = prior.get(name, 0)

    total_allocated = sum(platform_counts.values())

    # Stable sort — ties keep discovery order
    ranked = sorted(candidates, key=lambda c: c.fit_score, reverse=True)

    allocated: list[Candidate] = []
    overflow: list[Candidate] = []

    for candidate in ranked:
        if total_allocated >= global_cap:
            overflow.append(candidate)
            continue

        plat = candidate.platform
        ceiling = ceilings.get(plat, 100)
        current = platform_counts.get(plat, 0)

        if current >= ceiling:
            overflow.append(candidate)
            continue

        allocated.append(candidate)
        platform_counts[plat] = current + 1
        total_allocated += 1

    return allocated, overflow


def count_today_all(platforms: list[str], log_path: Path) -> dict[str, int]:
    """Count today's successful applications per platform.

    Wraps core.logger.count_today() for each platform name.

    Args:
        platforms: List of platform names to check.
        log_path: Path to applications_log.csv.

    Returns:
        Dict mapping platform name to today's apply count.
    """
    return {name: count_today(name, log_path) for name in platforms}
