"""Job scoring engine.

Scores jobs against Varun's profile using the rubric from CLAUDE.md §6.
Hard-skip patterns from profile.md §15. Tier classification from CLAUDE.md §3.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Job:
    """A single job listing scraped from a platform."""

    title: str
    company: str
    location: str
    experience_required: str
    jd_text: str               # full JD text (hard-skip scans first 500 chars)
    posted_date: str = ""      # ISO date, e.g. "2026-04-20"


# ── Tier keyword lists (CLAUDE.md §3) ─────────────────────────────────

_TIER_1_KEYWORDS: list[str] = [
    "growth manager",
    "head of growth",
    "product manager",
    "associate product manager",
    "associate pm",
    "strategy and operations",
    "strategy & operations",
    "business development manager",
    "founding team",
    "founding member",
    "early employee",
]

_TIER_2_KEYWORDS: list[str] = [
    "partnerships manager",
    "gtm lead",
    "go-to-market",
    "go to market",
    "revenue operations",
    "vc analyst",
    "vc associate",
    "strategy consultant",
    "strategy analyst",
]

_TIER_3_KEYWORDS: list[str] = [
    "marketing manager",
    "account executive",
    "chief of staff",
    "program manager",
]

# ── Tier thresholds (guidelines.md §4.2) ──────────────────────────────

TIER_THRESHOLDS: dict[str, float] = {
    "T1": 0.5,
    "T2": 0.6,
    "T3": 0.75,
}

# ── Hard-skip patterns (profile.md §15, CLAUDE.md §3) ─────────────────
# Each tuple: (compiled regex, human-readable reason)

_HARD_SKIP_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # 5+ years as a hard floor — catches "5+ years", "7 years minimum",
    # "minimum 5 years", "at least 6 years", etc.
    # Must NOT catch "3-5 years" or "2-5 years" (those are fine).
    (re.compile(
        r"""
        (?:                              # "minimum/at least N years" where N >= 5
            (?:minimum|at\s+least)\s+
            [5-9]\d*\s*\+?\s*(?:years?|yrs?)
        )
        |
        (?:                              # "N+ years" where N >= 5, standalone
            (?<!\d[-–])                  # negative lookbehind: not preceded by digit-dash
            [5-9]\d*\s*\+\s*(?:years?|yrs?)
        )
        """,
        re.IGNORECASE | re.VERBOSE,
    ), "5+ years required"),

    # Field sales for non-tech
    (re.compile(
        r"\b(?:insurance\s+(?:agent|advisor|sales|executive)"
        r"|real\s+estate\s+(?:agent|sales|broker)"
        r"|direct\s+sell(?:ing|er))\b",
        re.IGNORECASE,
    ), "field sales non-tech"),

    # Bond / lock-in
    (re.compile(
        r"\b(?:bond\s+(?:of|period)|lock[- ]?in\s+(?:period|clause)|service\s+bond)\b",
        re.IGNORECASE,
    ), "bond/lock-in clause"),

    # MLM / commission-only
    (re.compile(
        r"\b(?:mlm|multi[- ]?level\s+marketing|be\s+your\s+own\s+boss"
        r"|commission[- ]?only)\b",
        re.IGNORECASE,
    ), "MLM/commission-only role"),

    # Pure backend/data engineering
    (re.compile(
        r"\b(?:backend\s+(?:engineer|developer)|data\s+engineer(?:ing)?)\b",
        re.IGNORECASE,
    ), "pure backend/data engineering"),

    # Pure design (no product remit)
    (re.compile(
        r"\b(?:ui\s+design|ux\s+design|graphic\s+design)(?:er)?\b",
        re.IGNORECASE,
    ), "pure design role"),

    # Data entry / manual reporting dominant
    (re.compile(
        r"\bdata\s+entry\b",
        re.IGNORECASE,
    ), "data entry role"),
]

# ── Location preferences ──────────────────────────────────────────────

_PREFERRED_LOCATIONS: list[str] = [
    "delhi", "delhi ncr", "ncr", "gurgaon", "gurugram", "noida",
    "bangalore", "bengaluru", "mumbai",
    "remote", "pan india", "work from home", "wfh",
]

_INDIA_INDICATORS: list[str] = [
    "india", "delhi", "bangalore", "bengaluru", "mumbai", "gurgaon",
    "gurugram", "noida", "hyderabad", "pune", "chennai", "kolkata",
    "ncr", "remote", "pan india",
]

# ── Sector preferences (CLAUDE.md §5) ─────────────────────────────────

_PREFERRED_SECTORS: list[str] = [
    "b2b saas", "saas", "fintech", "edtech", "sales-tech", "salestech",
    "gtm tooling", "ai", "genai", "gen ai", "artificial intelligence",
    "marketplace", "crm",
]

# ── Company stage keywords ────────────────────────────────────────────

_STAGE_SCORES: dict[str, float] = {
    "seed": 1.0,
    "pre-seed": 1.0,
    "series a": 1.0,
    "series b": 1.0,
    "series c": 1.0,
    "early stage": 1.0,
    "early-stage": 1.0,
    "startup": 0.9,
    "growth stage": 0.7,
    "growth-stage": 0.7,
    "series d": 0.5,
    "series e": 0.4,
    "mnc": 0.2,
    "fortune 500": 0.2,
    "large enterprise": 0.2,
    "fmcg": 0.2,
    "conglomerate": 0.2,
}


# ── Public API ────────────────────────────────────────────────────────

def classify_tier(title: str) -> str:
    """Classify a role title into T1, T2, T3, or skip.

    Uses case-insensitive substring matching. Returns the highest-priority
    tier that matches (T1 > T2 > T3). Returns "skip" if no tier matches.

    Args:
        title: The job title string.

    Returns:
        One of "T1", "T2", "T3", "skip".
    """
    title_lower = title.lower()
    for keyword in _TIER_1_KEYWORDS:
        if keyword in title_lower:
            return "T1"
    for keyword in _TIER_2_KEYWORDS:
        if keyword in title_lower:
            return "T2"
    for keyword in _TIER_3_KEYWORDS:
        if keyword in title_lower:
            return "T3"
    return "skip"


def _check_hard_skip(job: Job) -> str | None:
    """Check if a job matches any deal-breaker pattern.

    Scans title + first 500 chars of jd_text.

    Returns:
        Reason string if the job should be skipped, None otherwise.
    """
    text = f"{job.title} {job.jd_text[:500]}".lower()

    for pattern, reason in _HARD_SKIP_PATTERNS:
        if pattern.search(text):
            return reason

    # Outside India check (profile.md §15)
    if job.location:
        loc_lower = job.location.lower()
        if not any(ind in loc_lower for ind in _INDIA_INDICATORS):
            return "outside India"

    return None


def title_match(title: str) -> float:
    """Score how well the title matches target roles. 0.0–1.0."""
    title_lower = title.lower()
    for keyword in _TIER_1_KEYWORDS:
        if keyword in title_lower:
            return 1.0
    for keyword in _TIER_2_KEYWORDS:
        if keyword in title_lower:
            return 0.7
    for keyword in _TIER_3_KEYWORDS:
        if keyword in title_lower:
            return 0.4
    # Partial signals — related words that aren't exact tier matches
    partials = ["growth", "product", "strategy", "founding", "gtm", "operations"]
    for signal in partials:
        if signal in title_lower:
            return 0.3
    return 0.0


def stage_match(jd_text: str, company: str) -> float:
    """Score how well the company stage matches preference (seed-to-C). 0.0–1.0."""
    text = f"{company} {jd_text}".lower()
    best = 0.0
    for keyword, value in _STAGE_SCORES.items():
        if keyword in text:
            best = max(best, value)
    # Default 0.5 if no stage signal — assume mid-stage
    return best if best > 0 else 0.5


def sector_match(jd_text: str) -> float:
    """Score how well the company/role sector matches preferences. 0.0–1.0."""
    text = jd_text.lower()
    matches = sum(1 for sector in _PREFERRED_SECTORS if sector in text)
    if matches >= 3:
        return 1.0
    if matches == 2:
        return 0.8
    if matches == 1:
        return 0.5
    return 0.1


def location_match(location: str) -> float:
    """Score how well the job location matches preferred locations. 0.0–1.0."""
    loc_lower = location.lower()
    for pref in _PREFERRED_LOCATIONS:
        if pref in loc_lower:
            return 1.0
    return 0.0


def experience_match(experience_required: str) -> float:
    """Score how well the experience requirement matches ~3 years. 0.0–1.0.

    Parses strings like "2-4 years", "3+ years", "5 years".
    """
    if not experience_required:
        return 0.5  # unknown, assume neutral

    # Range like "2-4" or "3-5"
    range_hit = re.search(r"(\d+)\s*[-–]\s*(\d+)", experience_required)
    if range_hit:
        low, high = int(range_hit.group(1)), int(range_hit.group(2))
        if low <= 3 <= high:
            return 1.0
        if low <= 5 and high >= 1:
            return 0.7
        return 0.2

    # Single number like "3+ years" or "5 years"
    single_hit = re.search(r"(\d+)\+?\s*(?:years?|yrs?)", experience_required, re.IGNORECASE)
    if single_hit:
        num = int(single_hit.group(1))
        if num <= 3:
            return 1.0
        if num <= 5:
            return 0.7
        return 0.2

    return 0.5


def score_job(job: Job) -> float:
    """Compute fit score 0.0–1.0 using the rubric from CLAUDE.md §6.

    score = 0.40 * title_match
          + 0.25 * stage_match
          + 0.15 * sector_match
          + 0.10 * location_match
          + 0.10 * experience_match

    Args:
        job: A Job dataclass instance.

    Returns:
        Float score between 0.0 and 1.0.
    """
    score = (
        0.40 * title_match(job.title)
        + 0.25 * stage_match(job.jd_text, job.company)
        + 0.15 * sector_match(job.jd_text)
        + 0.10 * location_match(job.location)
        + 0.10 * experience_match(job.experience_required)
    )
    return round(score, 4)


def should_apply(job: Job, score: float, tier: str) -> tuple[bool, str]:
    """Decide whether to apply, implementing tier thresholds and hard-skips.

    Checks hard-skip patterns first (profile.md §15), then tier thresholds
    (guidelines.md §4.2).

    Args:
        job: A Job dataclass instance.
        score: Pre-computed fit score from score_job().
        tier: Result of classify_tier() — "T1", "T2", "T3", or "skip".

    Returns:
        (apply, reason) — True + "" if yes, False + reason string if no.
    """
    # Hard-skip check first
    skip_reason = _check_hard_skip(job)
    if skip_reason:
        return False, f"hard skip: {skip_reason}"

    # No matching tier
    if tier == "skip":
        return False, "no matching tier"

    # Threshold check
    threshold = TIER_THRESHOLDS.get(tier)
    if threshold is None:
        return False, f"unknown tier: {tier}"
    if score < threshold:
        return False, f"below threshold: {score:.2f} < {threshold}"

    return True, ""
